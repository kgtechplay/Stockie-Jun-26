"""Restructure the NIFTY direction-prediction experiment capture.

Persists the shared feature store to output/feature_store/NIFTY_base.csv (the
neutral location both pipelines read), and writes the experiment reports under
output/backtest/NIFTY/experiment/:

  NIFTY_base.csv (feature store, written to output/feature_store/)
      Feature-only dataset (strategy_* + final_raw_signal removed), enriched with
      India VIX signals, a `regime` column (calm/stress volatility router) and
      actual_trade_label derived from a 0.5% next-day intraday move from next_open.
      Shared with production.

  base.txt
      Human-readable report describing the feature store + the final-prediction
      cascade. Lives under the experiment dir.

  <regime>_strategy_<Name>.csv / .txt   (one set per regime per strategy family)
      Rows for that volatility regime + the family's signal column(s), scored vs a
      regime-appropriate threshold (stress 0.5%, calm 0.3%) for precision/recall/F1.

  comparison.csv
      Side-by-side comparison across every strategy variant, grouped by regime.
      Common across regimes.

Read-only w.r.t. the DB except for SELECTing India VIX from MacroFactorDaily.
Inputs are point-in-time as of trade_date; next_* columns are realized D+1
outcomes used only for grading.

ARCHITECTURE
------------
This is the RESEARCH harness. The cascade engine (dataset assembly, labelling,
precision-floor voting, scoring, walk-forward) lives ONCE in
src/technical_analysis/cascade and is shared with production. This module only
adds the experiment-specific layers:
  * the FULL strategy roster — the promoted strategies (imported from cascade)
    PLUS the still-experimental ones defined below (MAAlignmentRoom, RangeBreakout),
    which production deliberately excludes; and
    * the report writers (base.txt, per-strategy CSV/TXT, comparison.csv).
Production (src/technical_analysis/cascade/pipeline.py) imports the same cascade
engine but registers only the promoted roster, so the two pipelines share the
engine yet diverge on strategies.
"""
from __future__ import annotations

from pathlib import Path
import sys
import textwrap

import numpy as np
import pandas as pd

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.technical_analysis.cascade.constants import (
    FEATURE_STORE, CALL, PUT, FLAT, THRESHOLD,
    REGIME_CALM, REGIME_STRESS,
    REGIME_VIX_CUTOFF, REGIME_VOL_CUTOFF, REGIME_THRESHOLD,
    REGIME_PRECISION_FLOOR, MIN_FIRES, WF_WINDOW,
)
from src.technical_analysis.cascade.dataset import (
    build_base, regime_frame, _call_ok, _put_ok,
)
from src.technical_analysis.cascade.engine import (
    Metrics, score_signal, _fmt,
    gather_regime_signals, build_regime_cascade, walk_forward_regime,
    score_final, _confusion_lines,
)
from src.technical_analysis.cascade.strategies import (
    _sig,
    oversold_bounce_call, down_momentum_put, momentum_directional, mean_reversion,
    calm_trend_call, calm_fade_put, calm_momentum_put,
    PROMOTED_DEFINITIONS,
)

# Experiment artifacts (base.txt, per-strategy CSV/TXT, comparison.csv). The
# feature store (base.csv) is NOT written here — it lives in the neutral
# FEATURE_STORE location shared with production.
EXPERIMENT_DIR = project_root / "output" / "backtest" / "NIFTY" / "experiment"

GLOBAL_REGION_COLS = [
    "global_us_return_mean",
    "global_europe_return_mean",
    "global_asia_return_mean",
]

GLOBAL_WEIGHTED_TILT_THRESHOLD = 0.001

CONTEXT_ROLLING_WINDOW = 60
CONTEXT_MIN_PERIODS = 30


def _rolling_quantile(series: pd.Series, q: float) -> pd.Series:
    return series.rolling(CONTEXT_ROLLING_WINDOW, min_periods=CONTEXT_MIN_PERIODS).quantile(q).shift(1)


def _rolling_median(series: pd.Series) -> pd.Series:
    return series.rolling(CONTEXT_ROLLING_WINDOW, min_periods=CONTEXT_MIN_PERIODS).median().shift(1)


def _atr_pct(df: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(df["atr14"], errors="coerce") / pd.to_numeric(df["close_1515"], errors="coerce")


def _two_sided_signal(call: pd.Series, put: pd.Series) -> pd.Series:
    sig = np.where(call.fillna(False), CALL,
          np.where(put.fillna(False), PUT, FLAT))
    return pd.Series(sig, index=call.index)


def _trend_call_context(df: pd.DataFrame) -> pd.Series:
    return (df["ma20_slope"] > 0) & (df["ma10d_slope"] > 0) & (df["trend_efficiency_10d"] >= 0.30)


def _dynamic_call_rsi_cap(df: pd.DataFrame) -> pd.Series:
    rolling_cap = _rolling_quantile(df["rsi14"], 0.70).clip(lower=50.0, upper=62.0)
    trend_cap = pd.Series(np.where(_trend_call_context(df), 58.0, 50.0), index=df.index)
    return pd.concat([rolling_cap, trend_cap], axis=1).max(axis=1).fillna(trend_cap)


def _dynamic_room_floor(df: pd.DataFrame) -> pd.Series:
    rolling_floor = _rolling_quantile(df["resistance_distance_10d"], 0.40).clip(lower=0.004, upper=0.025)
    atr_floor = (0.25 * _atr_pct(df)).clip(lower=0.004, upper=0.020)
    return pd.concat([rolling_floor, atr_floor], axis=1).min(axis=1).fillna(0.006)


def _dynamic_support_floor(df: pd.DataFrame) -> pd.Series:
    rolling_floor = _rolling_quantile(df["support_distance_10d"], 0.40).clip(lower=0.004, upper=0.025)
    atr_floor = (0.25 * _atr_pct(df)).clip(lower=0.004, upper=0.020)
    return pd.concat([rolling_floor, atr_floor], axis=1).min(axis=1).fillna(0.006)


# ───────────────────────── experimental (not-yet-promoted) strategies ─────────────────────────
# Faithful vectorised reproductions of strategies that have NOT cleared the
# precision floor in production. They remain here so the experiment keeps probing
# them; production's promoted roster (cascade.strategies) deliberately excludes
# them. Regime gating is removed because the regime columns are excluded from
# base.csv by design.

def _ma_alignment_room_base(df: pd.DataFrame) -> pd.Series:
    """MAAlignmentRoom unified rule (call precedence), ungated."""
    close = df["close_1515"].astype(float)
    ma5 = close.rolling(5).mean()
    ma10, ma20 = df["ma10"], df["ma20"]
    rsi, rdist, sdist = df["rsi14"], df["resistance_distance_10d"], df["support_distance_10d"]
    spread = (ma10 - ma20) / ma20
    call = (ma5 > ma10) & (spread > 0.0) & (rsi < 50.0) & (rdist > 0.005)
    put = (ma5 < ma10) & (spread < -0.0005) & (rsi > 30.0) & (sdist > 0.0)
    sig = np.where(call.fillna(False), CALL,
          np.where(put.fillna(False), PUT, FLAT))
    return pd.Series(sig, index=df.index)


def ma_alignment_room(df: pd.DataFrame) -> dict[str, pd.Series]:
    base = _ma_alignment_room_base(df)

    # PutGuarded: keep CALL/NO_POSITION as-is; keep PUT only when guarded.
    ret5, rp10, sdist = df["ret_5d"], df["range_position_10d"], df["support_distance_10d"]
    guard_ok = (ret5 < 0) & (rp10 < 0.5) & (sdist <= 0.02)
    put_guarded = base.where(~((base == PUT) & ~guard_ok.fillna(False)), FLAT)

    # ReboundCall: independent CALL-only rebound setup.
    rsi, ret10, rdist = df["rsi14"], df["ret_10d"], df["resistance_distance_10d"]
    rebound = (rsi.between(25, 45)) & (rdist > 0.02) & (sdist >= 0) & (ret10 < 0) & (ret5 > ret10)
    rebound_call = _sig(rebound, CALL)

    # MaTrend_001: MA10 vs MA20 spread with 0.1% band.
    spread = (df["ma10"] - df["ma20"]) / df["ma20"]
    matrend = np.where(spread > 0.001, CALL, np.where(spread < -0.001, PUT, FLAT))

    close = df["close_1515"].astype(float)
    ma5 = close.rolling(5).mean()
    ma10, ma20 = df["ma10"], df["ma20"]
    rsi = df["rsi14"]
    room = df["resistance_distance_10d"]
    support_room = df["support_distance_10d"]
    call_rsi_cap = _dynamic_call_rsi_cap(df)
    room_floor = _dynamic_room_floor(df)
    support_floor = _dynamic_support_floor(df)
    trend_context = _trend_call_context(df)
    context_call = (
        (ma5 > ma10)
        & (spread > 0.0)
        & (rsi <= call_rsi_cap)
        & (room >= room_floor)
    )
    trend_cont_call = (
        (ma5 > ma10)
        & (ma10 > ma20)
        & trend_context
        & (rsi <= call_rsi_cap)
        & (df["range_position_10d"] <= 0.90)
        & (room >= room_floor)
    )
    spread_band = _rolling_median(spread).fillna(0.0)
    context_put = (
        (ma5 < ma10)
        & (spread < spread_band.clip(upper=-0.0005))
        & (rsi >= _rolling_quantile(rsi, 0.30).clip(lower=35.0, upper=50.0).fillna(42.0))
        & (support_room >= support_floor)
    )
    context_band = _two_sided_signal(context_call, context_put)
    put_expansion_guard = (df["bb_width"] >= 0.055) & (room >= 0.015)

    return {
        "strategy_MAAlignmentRoom_signal": base,
        "strategy_MAAlignmentRoom_PutGuarded_signal": put_guarded,
        "strategy_MAAlignmentRoom_ReboundCall_signal": rebound_call,
        "strategy_MaTrend_001_signal": pd.Series(matrend, index=df.index),
        "strategy_MAAlignmentRoom_ContextBand_signal": context_band,
        "strategy_MAAlignmentRoom_TrendContinuationCall_signal": _sig(trend_cont_call, CALL),
        "strategy_MAAlignmentRoom_ContextBand_PutExpansionGuard_signal": _sig(
            (context_band == PUT) & put_expansion_guard,
            PUT,
        ),
    }


def oversold_bounce_call_with_context(df: pd.DataFrame) -> dict[str, pd.Series]:
    out = oversold_bounce_call(df)
    rsi, room, rp10, vix = df["rsi14"], df["resistance_distance_10d"], df["range_position_10d"], df["vix_close"]
    call_rsi_cap = _dynamic_call_rsi_cap(df)
    room_floor = _dynamic_room_floor(df)
    trend_context = _trend_call_context(df)
    out["strategy_OversoldBounceCall_ContextRoom_signal"] = _sig(
        (rsi <= call_rsi_cap) & (room >= room_floor) & (vix >= 12),
        CALL,
    )
    out["strategy_OversoldBounceCall_ContextRoom_PrecisionGuard_signal"] = _sig(
        (rsi <= call_rsi_cap)
        & (room >= room_floor)
        & (vix >= 15)
        & (df["bb_width"] >= 0.055)
        & (rsi <= 52),
        CALL,
    )
    out["strategy_OversoldBounceCall_TrendBand_signal"] = _sig(
        trend_context & (rsi <= call_rsi_cap) & (room >= room_floor) & (rp10 <= 0.90) & (vix >= 12),
        CALL,
    )
    return out


def momentum_directional_with_context(df: pd.DataFrame) -> dict[str, pd.Series]:
    out = momentum_directional(df)
    rsi, ret5, room, rp10 = df["rsi14"], df["ret_5d"], df["resistance_distance_10d"], df["range_position_10d"]
    s20, s10, vol, bbw, ret10 = df["ma20_slope"], df["ma10d_slope"], df["volume_day"], df["bb_width"], df["ret_10d"]
    trend_context = _trend_call_context(df)
    call_rsi_cap = _dynamic_call_rsi_cap(df)
    room_floor = _dynamic_room_floor(df)
    call_votes = (
        (rsi <= call_rsi_cap).astype(int)
        + (ret5 <= _rolling_quantile(ret5, 0.45).fillna(-0.002)).astype(int)
        + (room >= room_floor).astype(int)
        + (rp10 <= np.where(trend_context, 0.90, 0.35)).astype(int)
        + trend_context.astype(int)
    )
    put_votes = (
        ((s20 <= _rolling_quantile(s20, 0.35).fillna(-0.003)) | (s10 <= _rolling_quantile(s10, 0.35).fillna(-0.004))).astype(int)
        + (ret10 <= _rolling_quantile(ret10, 0.35).fillna(-0.005)).astype(int)
        + (vol >= np.minimum(88000.0, 1.2 * df["volume_20d"])).astype(int)
        + (bbw >= _rolling_quantile(bbw, 0.55).fillna(0.055)).astype(int)
        + (rp10 <= 0.45).astype(int)
    )
    call_fire = call_votes >= 3
    put_fire = put_votes >= 3
    conflict_pick = np.where((put_votes / 5.0) >= (call_votes / 5.0), PUT, CALL)
    sig = np.where(call_fire & ~put_fire, CALL,
          np.where(put_fire & ~call_fire, PUT,
          np.where(call_fire & put_fire, conflict_pick, FLAT)))
    out["strategy_MomentumDirectional_ContextVotes_signal"] = pd.Series(sig, index=df.index)
    context_sig = out["strategy_MomentumDirectional_ContextVotes_signal"]
    call_expansion_guard = df["bb_width"] >= 0.055
    expansion_guard = (df["bb_width"] >= 0.055) & (df["resistance_distance_10d"] >= 0.015)
    strong_expansion_guard = (df["vix_close"] >= 16) & (df["bb_width"] >= 0.065)
    out["strategy_MomentumDirectional_ContextVotes_CallExpansionGuard_signal"] = _sig(
        (context_sig == CALL) & call_expansion_guard,
        CALL,
    )
    out["strategy_MomentumDirectional_ContextVotes_ExpansionGuard_signal"] = context_sig.where(expansion_guard, FLAT)
    out["strategy_MomentumDirectional_ContextVotes_StrongExpansionGuard_signal"] = context_sig.where(strong_expansion_guard, FLAT)
    return out


def mean_reversion_with_context(df: pd.DataFrame) -> dict[str, pd.Series]:
    out = mean_reversion(df)
    rsi = df["rsi14"]
    low_band = _rolling_quantile(rsi, 0.30).clip(lower=32.0, upper=43.0).fillna(40.0)
    high_band = _rolling_quantile(rsi, 0.70).clip(lower=57.0, upper=68.0).fillna(60.0)
    out["strategy_RsiMeanReversion_ContextBand_signal"] = pd.Series(
        np.where(rsi <= low_band, CALL, np.where(rsi >= high_band, PUT, FLAT)),
        index=df.index,
    )
    close = df["close_1515"]
    lower_trigger = df["bb_lower"] + (0.15 * df["atr14"])
    upper_trigger = df["bb_upper"] - (0.15 * df["atr14"])
    out["strategy_BollingerMeanReversion_ATRBand_signal"] = pd.Series(
        np.where(close < lower_trigger, CALL, np.where(close > upper_trigger, PUT, FLAT)),
        index=df.index,
    )
    return out


def range_breakout(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Merged two-sided 20-day range breakout (replaces the regime-gated
    trendUpRangeBreakout / trendDownRangeBreakout). CALL on a close above the
    prior-20-day high, PUT on a close below the prior-20-day low."""
    close = df["close_1515"].astype(float)
    prior_high = df["high_day"].astype(float).shift(1).rolling(20).max()
    prior_low = df["low_day"].astype(float).shift(1).rolling(20).min()
    sig = np.where(close > prior_high, CALL,
          np.where(close < prior_low, PUT, FLAT))
    atr_buffer = 0.20 * df["atr14"].astype(float)
    context_sig = np.where(close > (prior_high - atr_buffer), CALL,
                  np.where(close < (prior_low + atr_buffer), PUT, FLAT))
    return {
        "strategy_RangeBreakout_signal": pd.Series(sig, index=df.index),
        "strategy_RangeBreakout_ATRBuffer_signal": pd.Series(context_sig, index=df.index),
    }


def calm_trend_call_with_context(df: pd.DataFrame) -> dict[str, pd.Series]:
    out = calm_trend_call(df)
    s20, rp, ma10, te = df["ma20_slope"], df["range_position_10d"], df["ma10d_slope"], df["trend_efficiency_10d"]
    room = df["resistance_distance_10d"]
    rsi = df["rsi14"]
    room_floor = _dynamic_room_floor(df)
    out["strategy_CalmTrendCall_ContextHeadroom_signal"] = _sig(
        (s20 > 0) & (room >= room_floor) & (rsi <= _dynamic_call_rsi_cap(df)) & (ma10 <= _rolling_quantile(ma10, 0.60).fillna(0.002)),
        CALL,
    )
    out["strategy_CalmTrendCall_RangeBand_signal"] = _sig(
        (s20 > 0) & (rp <= np.where(te >= 0.35, 0.80, 0.50)) & (te >= 0.25) & (room >= room_floor),
        CALL,
    )
    return out


def calm_fade_put_with_context(df: pd.DataFrame) -> dict[str, pd.Series]:
    out = calm_fade_put(df)
    rsi, rsi5 = df["rsi14"], df["rsi5"]
    rsi_floor = _rolling_quantile(rsi, 0.75).clip(lower=62.0, upper=70.0).fillna(65.0)
    rsi5_floor = _rolling_quantile(rsi5, 0.80).clip(lower=72.0, upper=85.0).fillna(80.0)
    out["strategy_CalmFadePut_ContextOverbought_signal"] = _sig((rsi >= rsi_floor) & (rsi5 >= rsi5_floor), PUT)
    return out


def calm_momentum_put_with_context(df: pd.DataFrame) -> dict[str, pd.Series]:
    out = calm_momentum_put(df)
    ret3 = df["ret_3d"]
    dyn_ret_floor = _rolling_quantile(ret3, 0.35).clip(lower=-0.008, upper=-0.002).fillna(-0.003)
    out["strategy_CalmMomentumPut_ContextContinuation_signal"] = _sig(ret3 <= dyn_ret_floor, PUT)
    return out


def _regional_agreement_masks(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Return (call_agree, put_agree) from US/Europe/Asia mean returns.

    Agreement means at least two of the three regional mean returns point in the
    same direction as the trade side. Missing regional values count as neutral.
    """
    regional = df.reindex(columns=GLOBAL_REGION_COLS).apply(pd.to_numeric, errors="coerce")
    positive_votes = (regional > 0).sum(axis=1)
    negative_votes = (regional < 0).sum(axis=1)
    return positive_votes >= 2, negative_votes >= 2


def _regional_components(df: pd.DataFrame) -> dict[str, pd.Series]:
    regional = df.reindex(columns=GLOBAL_REGION_COLS).apply(pd.to_numeric, errors="coerce")
    positive_votes = (regional > 0).sum(axis=1)
    negative_votes = (regional < 0).sum(axis=1)
    weighted_mean = regional.mean(axis=1)
    return {
        "any_call_tailwind": positive_votes >= 1,
        "any_put_tailwind": negative_votes >= 1,
        "weighted_call_tilt": weighted_mean >= GLOBAL_WEIGHTED_TILT_THRESHOLD,
        "weighted_put_tilt": weighted_mean <= -GLOBAL_WEIGHTED_TILT_THRESHOLD,
    }


def with_global_region_variants(df: pd.DataFrame, signals: dict[str, pd.Series]) -> dict[str, pd.Series]:
    """Add global-region variants alongside the original strategy signals.

    *_GlobalAgree keeps CALL only when at least two regional mean returns are
    positive and PUT only when at least two are negative.
    *_GlobalNoDisagree keeps neutral regional days but drops CALL when at least
    two regions are negative, and drops PUT when at least two regions are positive.
    """
    call_agree, put_agree = _regional_agreement_masks(df)
    regional = _regional_components(df)
    out = dict(signals)
    for col, sig in signals.items():
        base_name = col.removesuffix("_signal")
        agree = sig.where(
            ~(((sig == CALL) & ~call_agree) | ((sig == PUT) & ~put_agree)),
            FLAT,
        )
        no_disagree = sig.where(
            ~(((sig == CALL) & put_agree) | ((sig == PUT) & call_agree)),
            FLAT,
        )
        any_agree = sig.where(
            ~(((sig == CALL) & ~regional["any_call_tailwind"])
              | ((sig == PUT) & ~regional["any_put_tailwind"])),
            FLAT,
        )
        weighted_tilt = sig.where(
            ~(((sig == CALL) & ~regional["weighted_call_tilt"])
              | ((sig == PUT) & ~regional["weighted_put_tilt"])),
            FLAT,
        )
        out[f"{base_name}_GlobalAgree_signal"] = agree
        out[f"{base_name}_GlobalNoDisagree_signal"] = no_disagree
        out[f"{base_name}_GlobalAnyAgree_signal"] = any_agree
        out[f"{base_name}_GlobalWeightedTilt_signal"] = weighted_tilt
    return out


def expand_regime_signals_with_global(
    df: pd.DataFrame,
    regime_signals: dict[str, dict[str, pd.Series]],
) -> dict[str, dict[str, pd.Series]]:
    """Add generated global-region variants to a cascade signal roster."""
    out: dict[str, dict[str, pd.Series]] = {}
    for regime, signals in regime_signals.items():
        strategy_cols = {f"strategy_{name}_signal": sig for name, sig in signals.items()}
        expanded = with_global_region_variants(df, strategy_cols)
        out[regime] = {
            col.replace("strategy_", "").replace("_signal", ""): sig
            for col, sig in expanded.items()
        }
    return out


GLOBAL_VARIANT_SUFFIXES = (
    "_GlobalAgree",
    "_GlobalNoDisagree",
    "_GlobalAnyAgree",
    "_GlobalWeightedTilt",
)

GLOBAL_VARIANT_TIE_PREFERENCE = {
    "_GlobalAnyAgree": 0,
    "_GlobalNoDisagree": 1,
    "_GlobalAgree": 2,
    "_GlobalWeightedTilt": 3,
}


REVIEW_EXCLUDED_VARIANTS = {
    "OversoldBounceCall_ContextRoom_PrecisionGuard",
    "MomentumDirectional_ContextVotes_CallExpansionGuard_GlobalAnyAgree",
    "MomentumDirectional_ContextVotes_ExpansionGuard_GlobalAnyAgree",
    "MomentumDirectional_ContextVotes_StrongExpansionGuard_GlobalNoDisagree",
    "MomentumDirectional_ContextVotes_StrongExpansionGuard_GlobalAnyAgree",
    "MomentumDirectional_ContextVotes_StrongExpansionGuard_GlobalWeightedTilt",
    "MAAlignmentRoom_ContextBand_PutExpansionGuard",
    "MAAlignmentRoom_GlobalAnyAgree",
    "CalmTrendCall_RangeBand",
    "CalmFadePut_ContextOverbought_GlobalAgree",
    "CalmFadePut_ContextOverbought_GlobalNoDisagree",
    "CalmMomentumPut_Continuation_GlobalAgree",
    "CalmMomentumPut_Continuation_GlobalNoDisagree",
}


def _is_global_variant(name: str) -> bool:
    return name.endswith(GLOBAL_VARIANT_SUFFIXES)


def _unglobal_variant_name(name: str) -> str:
    for suffix in GLOBAL_VARIANT_SUFFIXES:
        if name.endswith(suffix):
            return name.removesuffix(suffix)
    return name


def _global_tie_key(name: str) -> tuple[int, str]:
    for suffix, rank in GLOBAL_VARIANT_TIE_PREFERENCE.items():
        if name.endswith(suffix):
            return rank, name.removesuffix(suffix)
    return len(GLOBAL_VARIANT_TIE_PREFERENCE), name


def _is_review_excluded(name: str) -> bool:
    return name in REVIEW_EXCLUDED_VARIANTS or _unglobal_variant_name(name) in REVIEW_EXCLUDED_VARIANTS


def _is_dominated(candidate: Metrics, others: list[Metrics]) -> bool:
    """True when another variant is at least as good on precision and recall,
    and strictly better on one of them. Global variants also drop when a
    non-global family peer is equal-or-better on both precision and recall. NaN
    precision/recall variants are pruned when any valid family peer exists."""
    if candidate.precision != candidate.precision or candidate.recall != candidate.recall:
        return any(m.precision == m.precision and m.recall == m.recall for m in others)

    candidate_is_global = _is_global_variant(candidate.name)
    for other in others:
        if other.name == candidate.name:
            continue
        if other.precision != other.precision or other.recall != other.recall:
            continue
        precision_ok = other.precision >= candidate.precision
        recall_ok = other.recall >= candidate.recall
        strictly_better = other.precision > candidate.precision or other.recall > candidate.recall
        equal_metrics = other.precision == candidate.precision and other.recall == candidate.recall
        global_equal_or_worse = candidate_is_global and not _is_global_variant(other.name)
        global_duplicate_tie = (
            candidate_is_global
            and _is_global_variant(other.name)
            and equal_metrics
            and _global_tie_key(other.name) < _global_tie_key(candidate.name)
        )
        if precision_ok and recall_ok and (strictly_better or global_equal_or_worse or global_duplicate_tie):
            return True
    return False


def prune_dominated_family_variants(metrics: list[Metrics]) -> tuple[list[Metrics], set[str]]:
    """Keep the precision/recall Pareto frontier within a strategy family."""
    dropped = {m.name for m in metrics if _is_dominated(m, metrics)}
    return [m for m in metrics if m.name not in dropped], dropped


def _score_family_signals(
    df: pd.DataFrame,
    signals: dict[str, pd.Series],
) -> tuple[dict[str, pd.Series], list[Metrics], set[str]]:
    metrics: list[Metrics] = []
    metric_to_col: dict[str, str] = {}
    for col, sig in signals.items():
        metric_name = col.replace("strategy_", "").replace("_signal", "")
        metric_to_col[metric_name] = col
        metrics.append(score_signal(df, sig.loc[df.index], metric_name))

    review_dropped = {m.name for m in metrics if _is_review_excluded(m.name)}
    metrics_for_pruning = [m for m in metrics if m.name not in review_dropped]
    kept_metrics, dropped = prune_dominated_family_variants(metrics_for_pruning)
    dropped |= review_dropped
    kept_signals = {
        metric_to_col[m.name]: signals[metric_to_col[m.name]]
        for m in kept_metrics
    }
    return kept_signals, kept_metrics, dropped


def gather_pruned_regime_signals(
    df: pd.DataFrame,
    regime_families: dict[str, dict],
    *,
    include_global: bool = False,
) -> dict[str, dict[str, pd.Series]]:
    """Build the cascade roster after dropping within-family variants dominated
    on both precision and recall."""
    out: dict[str, dict[str, pd.Series]] = {}
    for regime, families in regime_families.items():
        sub = regime_frame(df, regime)
        regime_signals: dict[str, pd.Series] = {}
        for fn in families.values():
            signals = fn(df)
            if include_global:
                signals = with_global_region_variants(df, signals)
            kept_signals, _, _ = _score_family_signals(sub, signals)
            for col, sig in kept_signals.items():
                regime_signals[col.replace("strategy_", "").replace("_signal", "")] = sig
        out[regime] = regime_signals
    return out


# ───────────────────────── full experiment roster ─────────────────────────
# Promoted families (imported from cascade.strategies) PLUS the experimental
# families defined above. Order is preserved so the per-strategy output files and
# comparison.txt ordering stay stable.
STRATEGY_FAMILIES = {
    "OversoldBounceCall": oversold_bounce_call_with_context,
    "DownMomentumPut": down_momentum_put,
    "MomentumDirectional": momentum_directional_with_context,
    "MAAlignmentRoom": ma_alignment_room,      # experimental
    "MeanReversion": mean_reversion_with_context,
    "RangeBreakout": range_breakout,           # experimental
}

# Strategy families grouped by the volatility regime they are designed for.
# The output files are named <regime>_strategy_<family>.{csv,txt}. base.csv,
# base.txt and comparison.txt remain common across regimes.
CALM_FAMILIES = {
    "CalmTrendCall": calm_trend_call_with_context,
    "CalmFadePut": calm_fade_put_with_context,
    "CalmMomentumPut": calm_momentum_put_with_context,
}

REGIME_FAMILIES = {
    REGIME_STRESS: STRATEGY_FAMILIES,
    REGIME_CALM: CALM_FAMILIES,
}


# Human-readable definitions for the experimental-only strategies, keyed by metric
# name (signal column without the strategy_ prefix and _signal suffix). The
# promoted strategies' definitions are imported from cascade and merged in.
_EXPERIMENTAL_DEFINITIONS: dict[str, str] = {
    "OversoldBounceCall_ContextRoom":
        "CALL when rsi14 is below a context-aware cap (rolling 70th percentile, clipped to "
        "[50,62], with 58 allowed in strong trend context), resistance_distance_10d clears "
        "the lower of a rolling 40th-percentile room floor and 0.25*ATR/close, and vix_close>=12.",
    "OversoldBounceCall_ContextRoom_PrecisionGuard":
        "ContextRoom with stress precision guards: vix_close>=15, bb_width>=5.5%, and rsi14<=52. "
        "This keeps the dynamic RSI/room logic but requires volatility expansion and avoids hot RSI.",
    "OversoldBounceCall_TrendBand":
        "CALL continuation/bounce hybrid: strong trend context (ma20_slope>0, ma10d_slope>0, "
        "trend_efficiency_10d>=0.30), rsi14 below the dynamic cap, dynamic room floor cleared, "
        "range_position_10d<=0.90, and vix_close>=12.",
    "MomentumDirectional_ContextVotes":
        "Two-sided contextual vote model. CALL votes use dynamic RSI cap, rolling ret_5d band, "
        "dynamic resistance room floor, trend-aware range-position band, and strong trend context. "
        "PUT votes use rolling slope/return/Bollinger-width bands, adaptive volume floor, and "
        "range_position_10d<=0.45. Fires when either side gets >=3 votes; conflicts use normalized votes.",
    "MomentumDirectional_ContextVotes_CallExpansionGuard":
        "CALL-only ContextVotes guard: keep CALLs when bb_width>=5.5%. This preserves expansion-continuation "
        "setups while filtering lower-volatility context votes.",
    "MomentumDirectional_ContextVotes_ExpansionGuard":
        "ContextVotes kept only when bb_width>=5.5% and resistance_distance_10d>=1.5%, a volatility "
        "expansion plus room guard chosen to lift stress precision while retaining some coverage.",
    "MomentumDirectional_ContextVotes_StrongExpansionGuard":
        "ContextVotes kept only when vix_close>=16 and bb_width>=6.5%, a stricter stress expansion guard.",
    "MAAlignmentRoom":
        "Two-sided MA alignment (CALL precedence). CALL when ma5>ma10, "
        "(ma10-ma20)/ma20>0, rsi14<50, resistance_distance_10d>0.5%. PUT when "
        "ma5<ma10, (ma10-ma20)/ma20<-0.05%, rsi14>30, support_distance_10d>0.",
    "MAAlignmentRoom_PutGuarded":
        "MAAlignmentRoom with the CALL leg unchanged, but a PUT is kept only when "
        "ret_5d<0 AND range_position_10d<0.5 AND support_distance_10d<=2%; "
        "otherwise NO_POSITION.",
    "MAAlignmentRoom_ReboundCall":
        "CALL-only rebound setup: rsi14 in [25,45], resistance_distance_10d>2%, "
        "support_distance_10d>=0, ret_10d<0, and ret_5d>ret_10d (5-day return "
        "improving vs the 10-day).",
    "MaTrend_001":
        "CALL when (ma10-ma20)/ma20 > +0.1%; PUT when < -0.1%; else NO_POSITION. "
        "Pure MA10/MA20 trend with a 0.1% dead-band.",
    "MAAlignmentRoom_ContextBand":
        "Two-sided MA alignment with dynamic bands. CALL when ma5>ma10, spread>0, rsi14<=dynamic "
        "context cap, and resistance_distance_10d clears a rolling/ATR-aware room floor. PUT when "
        "ma5<ma10, spread is below its rolling median band, rsi14 is above a rolling lower band, "
        "and support_distance_10d clears a rolling/ATR-aware floor.",
    "MAAlignmentRoom_ContextBand_PutExpansionGuard":
        "PUT-only precision guard for MAAlignmentRoom_ContextBand: keep its PUT only when bb_width>=5.5% "
        "and resistance_distance_10d>=1.5%, the stress expansion/room profile that tested cleanly.",
    "MAAlignmentRoom_TrendContinuationCall":
        "CALL-only trend-continuation variant: ma5>ma10>ma20, positive ma20 and ma10 slopes, "
        "trend_efficiency_10d>=0.30, rsi14<=dynamic cap, range_position_10d<=0.90, and dynamic "
        "resistance room floor cleared.",
    "RsiMeanReversion_ContextBand":
        "Two-sided RSI mean reversion using rolling 60-day RSI bands: CALL when rsi14 is below "
        "the clipped rolling 30th percentile; PUT when above the clipped rolling 70th percentile.",
    "BollingerMeanReversion_ATRBand":
        "Two-sided Bollinger mean reversion using ATR-adjusted triggers: CALL near/through the "
        "lower band plus 0.15*ATR; PUT near/through the upper band minus 0.15*ATR.",
    "RangeBreakout":
        "Two-sided 20-day breakout. CALL when close > the prior-20-day high; "
        "PUT when close < the prior-20-day low. Merges the old TREND_UP/TREND_DOWN "
        "regime-gated breakouts into one ungated signal.",
    "RangeBreakout_ATRBuffer":
        "Two-sided breakout with an ATR buffer. CALL when close is within 0.20*ATR of the prior "
        "20-day high; PUT when close is within 0.20*ATR of the prior 20-day low.",
    "CalmTrendCall_ContextHeadroom":
        "[calm regime, graded at 0.3%] CALL when quiet uptrend persists, dynamic resistance room "
        "floor is cleared, rsi14 is below its dynamic cap, and ma10d_slope is not above its rolling "
        "60th-percentile band.",
    "CalmTrendCall_RangeBand":
        "[calm regime, graded at 0.3%] CALL when quiet uptrend persists, trend_efficiency_10d>=0.25, "
        "dynamic room floor is cleared, and range_position_10d can extend to 0.80 only when trend "
        "efficiency is strong; otherwise it remains capped at 0.50.",
    "CalmFadePut_ContextOverbought":
        "[calm regime, graded at 0.3%] PUT when rsi14 and rsi5 exceed clipped rolling overbought "
        "bands instead of fixed 65/80 levels.",
    "CalmMomentumPut_ContextContinuation":
        "[calm regime, graded at 0.3%] PUT when ret_3d is below a clipped rolling 35th-percentile "
        "decline band instead of the fixed -0.3% threshold.",
}

# Merged definitions across promoted + experimental strategies.
STRATEGY_DEFINITIONS: dict[str, str] = {**PROMOTED_DEFINITIONS, **_EXPERIMENTAL_DEFINITIONS}

# Features profiled when characterising precision / recall misses.
_PROFILE_FEATURES = [
    "rsi14", "rsi5", "ma10d_slope", "ma5d_slope", "ma20_slope", "ret_2d", "ret_3d",
    "ret_5d", "ret_10d", "bb_width",
    "volatility_10d", "trend_efficiency_10d", "range_position_10d",
    "resistance_distance_10d", "support_distance_10d", "volume_day", "atr14",
    "vix_close", "vix_chg_1d",
    "global_us_return_mean", "global_europe_return_mean", "global_asia_return_mean",
]


# ───────────────────────── miss-pattern reporting ─────────────────────────

def _discriminators(df: pd.DataFrame, mask_a: pd.Series, mask_b: pd.Series, k: int = 4):
    """Return the k features whose mean differs most between the two groups,
    as (feature, mean_a, mean_b, z) sorted by |z| (Welch-style standardised gap)."""
    rows = []
    for c in _PROFILE_FEATURES:
        if c not in df.columns:
            continue
        a = df.loc[mask_a, c].dropna()
        b = df.loc[mask_b, c].dropna()
        if len(a) < 3 or len(b) < 3:
            continue
        pooled = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
        if not pooled or np.isnan(pooled):
            continue
        z = (a.mean() - b.mean()) / pooled
        rows.append((c, float(a.mean()), float(b.mean()), float(z)))
    rows.sort(key=lambda r: abs(r[3]), reverse=True)
    return rows[:k]


def _describe(disc, group_a: str, group_b: str) -> list[str]:
    out = []
    for feat, ma, mb, z in disc:
        direction = "higher" if ma > mb else "lower"
        out.append(f"      {feat:<24} {group_a}={ma:.4g} vs {group_b}={mb:.4g}  ({direction} on {group_a})")
    return out


def miss_patterns(df: pd.DataFrame, signal: pd.Series, name: str) -> list[str]:
    """Characterise precision misses (fired but wrong) and recall misses
    (threshold move happened on a side the strategy trades, but it didn't fire
    that side correctly)."""
    call_ok, put_ok = _call_ok(df), _put_ok(df)
    fc, fp = signal == CALL, signal == PUT
    fired = fc | fp
    correct = (fc & call_ok) | (fp & put_ok)
    wrong = fired & ~correct  # precision misses

    # opportunity = days the move happened on the side(s) this signal trades
    opp = pd.Series(False, index=df.index)
    if fc.any():
        opp = opp | call_ok
    if fp.any():
        opp = opp | put_ok
    recall_miss = opp & ~correct

    lines = [f"  {name}:"]

    # Precision misses: fired-wrong vs fired-correct
    n_wrong, n_correct = int(wrong.sum()), int(correct.sum())
    lines.append(f"    Precision misses: {n_wrong} of {int(fired.sum())} fires were wrong.")
    if n_wrong >= 3 and n_correct >= 3:
        disc = _discriminators(df, wrong, correct)
        if disc:
            lines.append("    On wrong fires vs correct fires, the biggest tells were:")
            lines += _describe(disc, "wrong", "correct")
    else:
        lines.append("    (too few wrong/correct fires to profile reliably)")

    # Recall misses: missed-opportunity vs caught-opportunity
    n_miss, n_caught = int(recall_miss.sum()), int(correct.sum())
    lines.append(f"    Recall misses: {n_miss} of {int(opp.sum())} actual-move days were not caught.")
    if n_miss >= 3 and n_caught >= 3:
        disc = _discriminators(df, recall_miss, correct)
        if disc:
            lines.append("    On missed winning days vs caught winning days, the biggest tells were:")
            lines += _describe(disc, "missed", "caught")
    else:
        lines.append("    (too few missed/caught days to profile reliably)")

    lines.append("")
    return lines


# ───────────────────────── final daily prediction (cascade) report ─────────────────────────

def _final_metric_block(title: str, m: dict) -> list[str]:
    return [
        f"  {title}",
        f"    fires: {m['n_call'] + m['n_put']} (CALL {m['n_call']}, PUT {m['n_put']}, "
        f"FLAT {m['n_flat']})  of {m['n']} days",
        f"    directional precision : {_fmt(m['dir_precision'])}   "
        f"(naive always-PUT = {_fmt(m['put_base'])}, lift {_fmt(m['lift'])}x)",
        f"    directional recall    : {_fmt(m['dir_recall'])}",
        f"    wrong-way rate        : {_fmt(m['wrong_way_rate'])}   "
        f"(took a side, opposite move happened — the only money-losing error)",
        f"    overall accuracy      : {_fmt(m['overall_accuracy'])}   "
        f"(correct fires + correct flats / all days)",
    ]


def cascade_report(df: pd.DataFrame):
    """Build the base.txt 'Final daily prediction' section and the per-row
    final_position (regime-aware, in-sample eligibility). Also reports a rolling
    walk-forward as the honest out-of-sample number.

    Returns (text lines, baseline final_position, global-expanded final_position)."""
    regime_signals = gather_pruned_regime_signals(df, REGIME_FAMILIES)
    global_regime_signals = gather_pruned_regime_signals(df, REGIME_FAMILIES, include_global=True)
    elig_frames = {regime: regime_frame(df, regime) for regime in REGIME_FAMILIES}
    final_pos, elig = build_regime_cascade(df, regime_signals, elig_frames)
    final_pos_global, global_elig = build_regime_cascade(df, global_regime_signals, elig_frames)

    m_in = score_final(df, final_pos)
    m_global = score_final(df, final_pos_global)
    st, cm = REGIME_THRESHOLD[REGIME_STRESS], REGIME_THRESHOLD[REGIME_CALM]
    lines = [
        "",
        "Final daily prediction — regime-aware precision cascade",
        "-------------------------------------------------------",
        "Each trade_date is routed to its volatility regime (calm/stress). Among",
        "that regime's strategies, a side (CALL/PUT) may vote only if its precision",
        "— measured on that regime at the regime threshold — clears the regime floor",
        f"with >= {MIN_FIRES} fires; the highest-precision eligible vote wins (cascade).",
        "Within each strategy family, reviewed exclusions and variants dominated on",
        "precision/recall are pruned before the cascade and comparison reports are built.",
        "The result is the final_position column in base.csv, graded against",
        f"actual_trade_label (touch threshold: stress {st:.1%} / calm {cm:.1%}).",
        f"Regime precision floors: stress > {REGIME_PRECISION_FLOOR[REGIME_STRESS]:.0%}, "
        f"calm > {REGIME_PRECISION_FLOOR[REGIME_CALM]:.0%}.",
        "",
        "  ================  final_position — headline (in-sample)  ================",
        f"    overall accuracy     : {_fmt(m_in['overall_accuracy'])}   "
        f"(correct fires + correct NO_POSITION over all {m_in['n']} days)",
        f"    directional recall   : {_fmt(m_in['dir_recall'])}   "
        f"(correct fires over {m_in['n_move']} actual-move days)",
        f"    directional precision: {_fmt(m_in['dir_precision'])}   "
        f"(fires {m_in['n_call'] + m_in['n_put']}: CALL {m_in['n_call']}, PUT {m_in['n_put']}, "
        f"FLAT {m_in['n_flat']})",
        "  =========================================================================",
        "",
        "  ==========  final_position_global — global-expanded cascade  ==========",
        f"    overall accuracy     : {_fmt(m_global['overall_accuracy'])}   "
        f"(correct fires + correct NO_POSITION over all {m_global['n']} days)",
        f"    directional recall   : {_fmt(m_global['dir_recall'])}   "
        f"(correct fires over {m_global['n_move']} actual-move days)",
        f"    directional precision: {_fmt(m_global['dir_precision'])}   "
        f"(fires {m_global['n_call'] + m_global['n_put']}: CALL {m_global['n_call']}, "
        f"PUT {m_global['n_put']}, FLAT {m_global['n_flat']})",
        "  =========================================================================",
        "",
        "The global-expanded cascade uses the same precision-floor voting logic, but",
        "its voter roster also includes GlobalAgree, GlobalNoDisagree, GlobalAnyAgree,",
        "and GlobalWeightedTilt variants generated from",
        "global_us_return_mean, global_europe_return_mean, and global_asia_return_mean.",
        "",
    ]

    # Eligible voters per regime
    for regime in (REGIME_STRESS, REGIME_CALM):
        call_elig, put_elig = elig[regime]
        floor = REGIME_PRECISION_FLOOR[regime]
        n_reg = int((df["regime"] == regime).sum())
        lines.append(f"  {regime.upper()} regime ({n_reg} days, floor > {floor:.0%}) — "
                     f"eligible voters:")
        if call_elig:
            for n, p in sorted(call_elig.items(), key=lambda kv: kv[1], reverse=True):
                lines.append(f"    CALL  {n:<58}{p:.3f}")
        else:
            lines.append("    CALL  (none cleared the floor)")
        if put_elig:
            for n, p in sorted(put_elig.items(), key=lambda kv: kv[1], reverse=True):
                lines.append(f"    PUT   {n:<58}{p:.3f}")
        else:
            lines.append("    PUT   (none cleared the floor)")
        lines.append("")

    for regime in (REGIME_STRESS, REGIME_CALM):
        call_elig, put_elig = global_elig[regime]
        floor = REGIME_PRECISION_FLOOR[regime]
        n_reg = int((df["regime"] == regime).sum())
        lines.append(f"  {regime.upper()} regime GLOBAL-expanded ({n_reg} days, floor > {floor:.0%}) — "
                     f"eligible voters:")
        if call_elig:
            for n, p in sorted(call_elig.items(), key=lambda kv: kv[1], reverse=True):
                lines.append(f"    CALL  {n:<58}{p:.3f}")
        else:
            lines.append("    CALL  (none cleared the floor)")
        if put_elig:
            for n, p in sorted(put_elig.items(), key=lambda kv: kv[1], reverse=True):
                lines.append(f"    PUT   {n:<58}{p:.3f}")
        else:
            lines.append("    PUT   (none cleared the floor)")
        lines.append("")

    # In-sample detail
    lines.append("In-sample detail (eligibility + grading on full data; optimistic)")
    lines.append("-" * 64)
    lines += _final_metric_block("Regime-aware cascade:", m_in)
    lines.append("  Confusion matrix:")
    lines += _confusion_lines(df, final_pos)
    lines.append("")
    lines += _final_metric_block("Global-expanded regime-aware cascade:", m_global)
    lines.append("  Confusion matrix:")
    lines += _confusion_lines(df, final_pos_global)
    lines.append("")

    # Rolling walk-forward (honest out-of-sample)
    wf_pred = walk_forward_regime(df, regime_signals)
    wf_pred_global = walk_forward_regime(df, global_regime_signals)
    wf_eval = df.iloc[WF_WINDOW:]
    lines.append(f"Walk-forward (rolling {WF_WINDOW}-day trailing eligibility, within regime)")
    lines.append("-" * 64)
    lines.append(f"Each predicted day uses only the prior {WF_WINDOW} days OF THE SAME "
                 "REGIME to")
    lines.append("decide eligibility, then predicts that single day. No future data leaks in.")
    lines += _final_metric_block("Walk-forward (out-of-sample, the honest number):",
                                 score_final(wf_eval, wf_pred.loc[wf_eval.index]))
    lines.append("  Walk-forward confusion matrix:")
    lines += _confusion_lines(wf_eval, wf_pred.loc[wf_eval.index])
    lines.append("")
    lines += _final_metric_block("Global-expanded walk-forward:",
                                 score_final(wf_eval, wf_pred_global.loc[wf_eval.index]))
    lines.append("  Global-expanded walk-forward confusion matrix:")
    lines += _confusion_lines(wf_eval, wf_pred_global.loc[wf_eval.index])
    lines.append("")
    lines.append("Caveat: precision floors are fit on the same year they grade, so the")
    lines.append("in-sample headline is optimistic. There is only one calm->stress")
    lines.append("transition in this sample, so the walk-forward is thin; treat as")
    lines.append("research, not a live signal.")
    lines.append("")

    return lines, final_pos, final_pos_global


# ───────────────────────── file writers ─────────────────────────

def write_base(df: pd.DataFrame, cascade_lines: list[str]) -> None:
    EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)
    # Persist the feature store to its neutral location (shared with production);
    # base.txt (the human-readable report) stays under the experiment dir.
    FEATURE_STORE.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_csv(FEATURE_STORE, index=False)
    except PermissionError as exc:
        print(f"[WARN] Feature store CSV write skipped: {exc}")

    bc = _call_ok(df).mean()
    bp = _put_ok(df).mean()
    label_counts = df["actual_trade_label"].value_counts().to_dict()
    regime_counts = df["regime"].value_counts().to_dict()
    st, cm = REGIME_THRESHOLD[REGIME_STRESS], REGIME_THRESHOLD[REGIME_CALM]
    txt = f"""NIFTY direction-prediction experiment — BASE dataset
=====================================================
rows: {len(df)}   date range: {df['trade_date'].min()} .. {df['trade_date'].max()}
regime split: stress={regime_counts.get(REGIME_STRESS, 0)}   calm={regime_counts.get(REGIME_CALM, 0)}

Regime-aware threshold (next-day intraday move from next_open):
  stress days: {st:.3%}    calm days: {cm:.3%}
Each trade_date is first routed to a volatility regime — calm = India VIX <
{REGIME_VIX_CUTOFF:g} AND volatility_10d < {REGIME_VOL_CUTOFF:g}; otherwise stress — then graded
at that regime's threshold (calm days rarely print a 0.5% move).

actual_trade_label
------------------
For each trade_date, the realised next-day intraday move from next_open is checked
against that day's regime threshold T (stress {st:.1%} / calm {cm:.1%}):
  CALL  : (next_high - next_open) / next_open >= T   (and PUT side did not)
  PUT   : (next_open - next_low ) / next_open >= T   (and CALL side did not)
  BOTH  : both the +T and -T moves were touched intraday
  NO_POSITION : neither move reached the threshold
label distribution: {label_counts}
base CALL hit-rate (CALL or BOTH): {bc:.3f}   base PUT hit-rate (PUT or BOTH): {bp:.3f}
These base rates are the precision a random/always-on signal would achieve.

Feature definitions (canonical features; *_5d/_20d/_50d/_90d are window variants
of the same feature and follow the same definition)
--------------------------------------------------------------------------------
ma10                  : 10-day simple moving average of close.
ma10d_slope           : slope (per-day rate of change) of the 10-day MA; >0 rising trend.
rsi14                 : 14-day Relative Strength Index (0-100); <30 oversold, >70 overbought.
atr14                 : 14-day Average True Range; absolute daily volatility in index points.
bb_upper / bb_middle / bb_lower
                      : Bollinger Bands — middle = 20-day MA, upper/lower = middle +/- 2 std.
bb_width              : (bb_upper - bb_lower) / bb_middle; volatility expansion when high.
ret_10d               : 10-day price return (decimal).
volatility_10d        : standard deviation of the last 10 daily close-to-close returns.
trend_efficiency_10d  : net move / total path length over 10 days (0-1); higher = cleaner trend.
recent_high_10d       : highest high over the last 10 days.
recent_low_10d        : lowest low over the last 10 days.
support_10d           : nearest 10-day support level (recent_low_10d).
resistance_10d        : nearest 10-day resistance level (recent_high_10d).
range_position_10d    : where close sits in the 10-day high/low range (0 = low, 1 = high).
resistance_distance_10d: (resistance_10d - close) / close; headroom to resistance.

India VIX signals (from MacroFactorDaily, point-in-time as of trade_date)
-------------------------------------------------------------------------
vix_close             : India VIX close — 30-day forward expected NIFTY volatility (%).
vix_chg_1d            : 1-day change in India VIX (today - yesterday).
vix_chg_pct           : 1-day percentage change in India VIX.
"""
    footer = (
        "\nExcluded from this file by design: strategy_* signal columns and "
        "final_raw_signal.\nRetained: the regime column (calm/stress router) and "
        "final_position / final_position_global (the regime-aware\ncascade outputs "
        "described above). Per-regime "
        "strategy signals live in the\n<regime>_strategy_<family> CSVs.\n"
    )
    full = txt + "\n".join(cascade_lines) + footer
    (EXPERIMENT_DIR / "base.txt").write_text(full, encoding="utf-8")


def write_strategy(df: pd.DataFrame, regime: str, family: str,
                   signals: dict[str, pd.Series]) -> list[Metrics]:
    """Score a family within one volatility regime and write
    <regime>_strategy_<family>.{csv,txt}. `df` is the regime-subset frame whose
    actual_trade_label is already set at that regime's threshold."""
    threshold = REGIME_THRESHOLD[regime]
    signals = with_global_region_variants(df, signals)
    signals, metrics, dropped = _score_family_signals(df, signals)
    out = df.drop(columns=["final_position"], errors="ignore").copy()
    ordered_names: list[str] = []
    for col, sig in signals.items():
        s = sig.loc[df.index]
        out[col] = s
        metric_name = col.replace("strategy_", "").replace("_signal", "")
        ordered_names.append(metric_name)

    csv_path = EXPERIMENT_DIR / f"{regime}_strategy_{family}.csv"
    out.to_csv(csv_path, index=False)

    bc, bp = _call_ok(df).mean(), _put_ok(df).mean()
    title = f"[{regime} regime] Strategy family: {family}"
    lines = [
        title,
        "=" * len(title),
        f"rows: {len(df)} ({regime} regime)   THRESHOLD = {threshold:.3%}",
        f"base CALL precision (random): {bc:.3f}   base PUT precision (random): {bp:.3f}",
        "",
    ]
    if dropped:
        lines += [
            "Pruning",
            "-------",
            "Dropped variants: either explicitly excluded after research review, strictly dominated",
            "within this family on precision/recall, or global variants equal-or-worse than a",
            "non-global family peer on both precision and recall.",
        ]
        for name in sorted(dropped):
            lines.append(f"  {name}")
        lines.append("")

    lines += [
        "Definitions",
        "-----------",
    ]
    for nm in ordered_names:
        definition = _strategy_definition(nm)
        lines.append(f"  {nm}:")
        for chunk in textwrap.wrap(definition, width=88):
            lines.append(f"    {chunk}")
        lines.append("")

    lines += [
        f"{'variant':<42}{'n_call':>7}{'n_put':>7}{'prec':>8}{'recall':>8}{'f1':>8}{'cov':>7}",
        "-" * 95,
    ]
    for m in metrics:
        lines.append(
            f"{m.name:<42}{m.n_call:>7}{m.n_put:>7}"
            f"{_fmt(m.precision):>8}{_fmt(m.recall):>8}{_fmt(m.f1):>8}{m.coverage:>7.3f}"
        )

    lines += ["", "Miss patterns",
              "-------------",
              "Where each variant loses precision (wrong fires) and recall (missed moves):",
              ""]
    for col, sig in signals.items():
        metric_name = col.replace("strategy_", "").replace("_signal", "")
        lines += miss_patterns(df, sig.loc[df.index], metric_name)

    lines += ["Notes",
              "-----",
              f"Scored on the {regime} regime only ({len(df)} of the full sample), at that",
              f"regime's {threshold:.3%} threshold.",
              "precision = correct fires / total fires.",
              "recall    = correct fires / days the threshold move actually occurred.",
              "f1        = harmonic mean of precision and recall.", ""]
    (EXPERIMENT_DIR / f"{regime}_strategy_{family}.txt").write_text(
        "\n".join(lines), encoding="utf-8")
    return metrics


def _strategy_definition(name: str) -> str:
    generated_suffixes = {
        "_GlobalAgree": (
            "GlobalAgree variant: keep CALL only when at least two of "
            "global_us_return_mean, global_europe_return_mean, and global_asia_return_mean "
            "are positive; keep PUT only when at least two are negative."
        ),
        "_GlobalNoDisagree": (
            "GlobalNoDisagree variant: keep neutral regional-tone days, but drop CALL when "
            "at least two regional means are negative and drop PUT when at least two regional "
            "means are positive."
        ),
        "_GlobalAnyAgree": (
            "GlobalAnyAgree variant: keep CALL when at least one of the three regional means "
            "is positive; keep PUT when at least one is negative. This is the loosest regional "
            "tailwind add-on."
        ),
        "_GlobalWeightedTilt": (
            f"GlobalWeightedTilt variant: keep CALL when the average of the three regional "
            f"means is at least {GLOBAL_WEIGHTED_TILT_THRESHOLD:.3%}; keep PUT when that "
            f"average is at most {-GLOBAL_WEIGHTED_TILT_THRESHOLD:.3%}. This uses magnitude, "
            "not just vote count."
        ),
    }
    for suffix, explanation in generated_suffixes.items():
        if name.endswith(suffix):
            base = name.removesuffix(suffix)
            return f"{STRATEGY_DEFINITIONS.get(base, base)} {explanation}"
    return STRATEGY_DEFINITIONS.get(name, "(definition not recorded)")


def write_comparison(df: pd.DataFrame,
                     regime_metrics: dict[str, list[Metrics]]) -> None:
    baseline_final = score_final(df, df["final_position"])
    global_final = score_final(df, df["final_position_global"])
    rows: list[dict[str, object]] = []

    for label, metrics, scope in (
        ("baseline", baseline_final, "pruned_research_roster"),
        ("global", global_final, "pruned_global_expanded_roster"),
    ):
        rows.append({
            "row_type": "final_cascade",
            "regime": "all",
            "strategy": label,
            "side": "BOTH",
            "scope": scope,
            "n": metrics["n"],
            "n_call": metrics["n_call"],
            "n_put": metrics["n_put"],
            "n_flat": metrics["n_flat"],
            "precision": metrics["dir_precision"],
            "recall": metrics["dir_recall"],
            "f1": np.nan,
            "coverage": (metrics["n_call"] + metrics["n_put"]) / metrics["n"],
            "wrong_way_rate": metrics["wrong_way_rate"],
            "overall_accuracy": metrics["overall_accuracy"],
            "base_call_precision": _call_ok(df).mean(),
            "base_put_precision": _put_ok(df).mean(),
            "threshold": np.nan,
            "notes": "review exclusions plus within-family dominated/equal-or-worse global variants are pruned",
        })

    for regime in (REGIME_STRESS, REGIME_CALM):
        sub = regime_frame(df, regime)
        bc, bp = _call_ok(sub).mean(), _put_ok(sub).mean()
        for m in regime_metrics.get(regime, []):
            side = "BOTH" if (m.n_call and m.n_put) else ("CALL" if m.n_call else "PUT")
            rows.append({
                "row_type": "strategy",
                "regime": regime,
                "strategy": m.name,
                "side": side,
                "scope": "pruned_family_variant",
                "n": len(sub),
                "n_call": m.n_call,
                "n_put": m.n_put,
                "n_flat": len(sub) - m.n_call - m.n_put,
                "precision": m.precision,
                "recall": m.recall,
                "f1": m.f1,
                "coverage": m.coverage,
                "wrong_way_rate": np.nan,
                "overall_accuracy": np.nan,
                "base_call_precision": bc,
                "base_put_precision": bp,
                "threshold": REGIME_THRESHOLD[regime],
                "notes": "",
            })

    pd.DataFrame(rows).to_csv(EXPERIMENT_DIR / "comparison.csv", index=False)
    old_txt = EXPERIMENT_DIR / "comparison.txt"
    if old_txt.exists():
        old_txt.unlink()


def main() -> None:
    df = build_base()
    cascade_lines, final_position, final_position_global = cascade_report(df)
    df["final_position"] = final_position
    df["final_position_global"] = final_position_global
    write_base(df, cascade_lines)
    print(f"wrote base.csv ({len(df)} rows) + base.txt")

    regime_metrics: dict[str, list[Metrics]] = {}
    for regime, families in REGIME_FAMILIES.items():
        sub = regime_frame(df, regime)
        metrics_for_regime: list[Metrics] = []
        for family, fn in families.items():
            fam_signals = fn(df)  # compute on full frame, score on the regime slice
            metrics = write_strategy(sub, regime, family, fam_signals)
            metrics_for_regime.extend(metrics)
            print(f"wrote {regime}_strategy_{family}.csv + .txt "
                f"({len(metrics)} variant(s))")
        regime_metrics[regime] = metrics_for_regime

    write_comparison(df, regime_metrics)
    print("wrote comparison.csv")
    print("\nSummary (per regime, regime-specific threshold):")
    for regime in (REGIME_STRESS, REGIME_CALM):
        print(f"  [{regime}] threshold {REGIME_THRESHOLD[regime]:.2%}")
        for m in regime_metrics.get(regime, []):
            print(f"    {m.name:<42} prec={_fmt(m.precision)} "
                  f"recall={_fmt(m.recall)} f1={_fmt(m.f1)}")


if __name__ == "__main__":
    main()
