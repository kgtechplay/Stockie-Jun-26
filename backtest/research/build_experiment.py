"""Restructure the NIFTY direction-prediction experiment capture.

Produces, under output/backtest/NIFTY/experiment/:

  base.csv / base.txt
      Feature-only dataset (regime + strategy_* + final_raw_signal removed),
      enriched with India VIX signals, plus actual_trade_label derived from a
      0.5% next-day intraday move from next_open.

  strategy_<Name>.csv / .txt   (one set per strategy family)
      base columns + that family's signal column(s), scored vs actual_trade_label
      for precision / recall / F1.

  comparison.txt
      Side-by-side comparison and observations across every strategy variant.

Read-only w.r.t. the DB except for SELECTing India VIX from MacroFactorDaily.
Inputs are point-in-time as of trade_date; next_* columns are realized D+1
outcomes used only for grading.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import textwrap

import numpy as np
import pandas as pd

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client

EXPERIMENT_DIR = project_root / "output" / "backtest" / "NIFTY" / "experiment"
BASE_CSV = EXPERIMENT_DIR / "base.csv"
THRESHOLD = 0.005  # 0.5% next-day intraday move (touch) from next_open

CALL, PUT, FLAT = "CALL", "PUT", "NO_POSITION"

# columns dropped when forming the feature-only base
_DROP_EXACT = {"final_raw_signal", "selected_regime", "hindsight_regime",
               "expected_regime_lag2", "actual_trade_label"}
_VIX_COLS = ["vix_close", "vix_chg_1d", "vix_chg_pct"]


# ───────────────────────── data assembly ─────────────────────────

def _load_vix() -> pd.DataFrame:
    settings = get_settings()
    db = get_database_client(settings)
    db.connect()
    try:
        with db.conn.cursor() as cur:
            cur.execute(
                'SELECT factor_date, india_vix FROM "MacroFactorDaily" '
                "WHERE india_vix IS NOT NULL ORDER BY factor_date"
            )
            rows = cur.fetchall()
    finally:
        db.close()
    vix = pd.DataFrame(rows, columns=["trade_date", "vix_close"])
    vix["trade_date"] = pd.to_datetime(vix["trade_date"]).dt.strftime("%Y-%m-%d")
    vix["vix_close"] = vix["vix_close"].astype(float)
    vix["vix_chg_1d"] = vix["vix_close"].diff()
    vix["vix_chg_pct"] = vix["vix_close"].pct_change()
    return vix


def build_base() -> pd.DataFrame:
    """Read the current base.csv, strip regime/strategy/label columns, join VIX,
    and (re)derive actual_trade_label from the 0.5% intraday rule.

    Idempotent: safe to re-run on an already-restructured base.csv because all
    feature + next_* columns are retained.
    """
    df = pd.read_csv(BASE_CSV)
    df = df[[c for c in df.columns
             if c not in _DROP_EXACT
             and not c.startswith("strategy_")
             and c not in _VIX_COLS]]

    df = df.merge(_load_vix(), on="trade_date", how="left")

    o, h, lo = df["next_open"], df["next_high"], df["next_low"]
    # Touch-based: did the next-day intraday move reach the threshold from open?
    call_ok = (h - o) / o >= THRESHOLD
    put_ok = (o - lo) / o >= THRESHOLD
    label = np.select(
        [call_ok & ~put_ok, put_ok & ~call_ok, call_ok & put_ok],
        [CALL, PUT, "BOTH"],
        default=FLAT,
    )
    df["actual_trade_label"] = label
    return df


def _call_ok(df: pd.DataFrame) -> pd.Series:
    return df["actual_trade_label"].isin([CALL, "BOTH"])


def _put_ok(df: pd.DataFrame) -> pd.Series:
    return df["actual_trade_label"].isin([PUT, "BOTH"])


# ───────────────────────── strategy definitions ─────────────────────────
# Each entry: signal_column_name -> callable(df) -> Series of CALL/PUT/NO_POSITION

def _sig(mask: pd.Series, side: str) -> pd.Series:
    return pd.Series(np.where(mask.fillna(False), side, FLAT), index=mask.index)


def oversold_bounce_call(df: pd.DataFrame) -> dict[str, pd.Series]:
    rsi, room = df["rsi14"], df["resistance_distance_10d"]
    rp10, vix = df["range_position_10d"], df["vix_close"]
    return {
        "strategy_OversoldBounceCall_HighPrecision_signal":
            _sig((rp10 <= 0.20) & (vix >= 13), CALL),
        "strategy_OversoldBounceCall_MoreTrades_signal":
            _sig((rsi <= 42) & (room >= 0.025) & (vix >= 13), CALL),
    }


def down_momentum_put(df: pd.DataFrame) -> dict[str, pd.Series]:
    s20, vol, dvix, vix = df["ma20_slope"], df["volume_day"], df["vix_chg_1d"], df["vix_close"]
    return {
        "strategy_DownMomentumPut_HighPrecision_signal":
            _sig((s20 <= -0.003) & (vol >= 90000) & (dvix > 0), PUT),
        "strategy_DownMomentumPut_MoreTrades_signal":
            _sig((s20 <= -0.003) & (vol >= 90000) & (vix >= 13), PUT),
    }


def momentum_directional(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Merged best-balanced CALL + PUT into one two-sided directional signal.

    CALL fires on >=2 oversold-reversion votes (max 4); PUT fires on >=3
    down-momentum votes (max 5). When both sides fire on the same day the
    conflict is resolved by normalised vote strength (votes / max_votes): the
    side that is more strongly confirmed wins. This vote-margin tie-break is
    far better than dropping conflicts, because oversold and down-momentum
    conditions overlap heavily on falling days.
    """
    rsi, ret5, room, rp10 = df["rsi14"], df["ret_5d"], df["resistance_distance_10d"], df["range_position_10d"]
    s20, s10, vol, bbw, ret10 = df["ma20_slope"], df["ma10d_slope"], df["volume_day"], df["bb_width"], df["ret_10d"]

    call_votes = ((rsi <= 42).astype(int) + (ret5 < -0.012).astype(int)
                  + (room >= 0.025).astype(int) + (rp10 <= 0.25).astype(int))
    put_votes = (((s20 <= -0.003) | (s10 <= -0.004)).astype(int)
                 + (ret10 <= -0.005).astype(int) + (vol >= 88000).astype(int)
                 + (bbw >= 0.055).astype(int) + (rp10 <= 0.40).astype(int))

    call_fire = call_votes >= 2
    put_fire = put_votes >= 3
    call_strength = call_votes / 4.0
    put_strength = put_votes / 5.0
    conflict_pick = np.where(put_strength >= call_strength, PUT, CALL)
    sig = np.where(call_fire & ~put_fire, CALL,
          np.where(put_fire & ~call_fire, PUT,
          np.where(call_fire & put_fire, conflict_pick, FLAT)))
    return {"strategy_MomentumDirectional_signal": pd.Series(sig, index=df.index)}


# ───────────────────────── existing / legacy strategies ─────────────────────────
# Faithful vectorised reproductions of the strategies previously stored as the
# dropped strategy_* columns (logic from
# src/technical_analysis/prediction/strategies.py and
# backtest/test_underlying_prediction.py). Regime gating is removed because the
# regime columns are excluded from base.csv by design.

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

    return {
        "strategy_MAAlignmentRoom_signal": base,
        "strategy_MAAlignmentRoom_PutGuarded_signal": put_guarded,
        "strategy_MAAlignmentRoom_ReboundCall_signal": rebound_call,
        "strategy_MaTrend_001_signal": pd.Series(matrend, index=df.index),
    }


def mean_reversion(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Merged mean-reversion family: Bollinger + RSI_6040 (both ungated)."""
    close, upper, lower = df["close_1515"], df["bb_upper"], df["bb_lower"]
    boll = np.where(close < lower, CALL, np.where(close > upper, PUT, FLAT))

    rsi = df["rsi14"]
    rsi_mr = np.where(rsi <= 40.0, CALL, np.where(rsi >= 60.0, PUT, FLAT))

    return {
        "strategy_BollingerMeanReversion_signal": pd.Series(boll, index=df.index),
        "strategy_RsiMeanReversion_6040_signal": pd.Series(rsi_mr, index=df.index),
    }


def range_breakout(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Merged two-sided 20-day range breakout (replaces the regime-gated
    trendUpRangeBreakout / trendDownRangeBreakout). CALL on a close above the
    prior-20-day high, PUT on a close below the prior-20-day low."""
    close = df["close_1515"].astype(float)
    prior_high = df["high_day"].astype(float).shift(1).rolling(20).max()
    prior_low = df["low_day"].astype(float).shift(1).rolling(20).min()
    sig = np.where(close > prior_high, CALL,
          np.where(close < prior_low, PUT, FLAT))
    return {"strategy_RangeBreakout_signal": pd.Series(sig, index=df.index)}


STRATEGY_FAMILIES = {
    "OversoldBounceCall": oversold_bounce_call,
    "DownMomentumPut": down_momentum_put,
    "MomentumDirectional": momentum_directional,
    "MAAlignmentRoom": ma_alignment_room,
    "MeanReversion": mean_reversion,
    "RangeBreakout": range_breakout,
}


# Human-readable definitions, keyed by metric name
# (signal column without the strategy_ prefix and _signal suffix).
STRATEGY_DEFINITIONS: dict[str, str] = {
    "OversoldBounceCall_HighPrecision":
        "CALL when range_position_10d <= 0.20 (close near the 10-day low) AND "
        "vix_close >= 13. Oversold mean-reversion bounce, gated away from "
        "dead low-volatility days.",
    "OversoldBounceCall_MoreTrades":
        "CALL when rsi14 <= 42 AND resistance_distance_10d >= 2.5% (oversold with "
        "headroom to resistance) AND vix_close >= 13. Looser entry than the "
        "HighPrecision variant, so it fires more often.",
    "DownMomentumPut_HighPrecision":
        "PUT when ma20_slope <= -0.003 (falling 20-day MA) AND volume_day >= 90,000 "
        "AND vix_chg_1d > 0 (India VIX rising). Downside momentum continuation "
        "confirmed by rising fear.",
    "DownMomentumPut_MoreTrades":
        "PUT when ma20_slope <= -0.003 AND volume_day >= 90,000 AND vix_close >= 13. "
        "Same momentum core but a VIX level gate (instead of rising-VIX) to trade more.",
    "MomentumDirectional":
        "Two-sided. CALL on >=2 of {rsi14<=42, ret_5d<-1.2%, "
        "resistance_distance_10d>=2.5%, range_position_10d<=0.25}. PUT on >=3 of "
        "{ma20_slope<=-0.003 or ma10d_slope<=-0.004, ret_10d<=-0.5%, "
        "volume_day>=88k, bb_width>=0.055, range_position_10d<=0.40}. When both "
        "sides fire, the side with higher normalised vote strength wins.",
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
    "BollingerMeanReversion":
        "CALL when close < lower Bollinger band (20-day mean - 2 sigma); "
        "PUT when close > upper band (mean + 2 sigma).",
    "RsiMeanReversion_6040":
        "CALL when rsi14 <= 40; PUT when rsi14 >= 60; else NO_POSITION.",
    "RangeBreakout":
        "Two-sided 20-day breakout. CALL when close > the prior-20-day high; "
        "PUT when close < the prior-20-day low. Merges the old TREND_UP/TREND_DOWN "
        "regime-gated breakouts into one ungated signal.",
}

# Features profiled when characterising precision / recall misses.
_PROFILE_FEATURES = [
    "rsi14", "ma10d_slope", "ma20_slope", "ret_5d", "ret_10d", "bb_width",
    "volatility_10d", "trend_efficiency_10d", "range_position_10d",
    "resistance_distance_10d", "support_distance_10d", "volume_day", "atr14",
    "vix_close", "vix_chg_1d",
]


# ───────────────────────── metrics ─────────────────────────

@dataclass
class Metrics:
    name: str
    n_call: int
    n_put: int
    precision: float
    recall: float
    f1: float
    call_precision: float
    put_precision: float
    coverage: float


def score_signal(df: pd.DataFrame, signal: pd.Series, name: str) -> Metrics:
    call_ok, put_ok = _call_ok(df), _put_ok(df)
    fired_call = signal == CALL
    fired_put = signal == PUT

    correct_call = int((fired_call & call_ok).sum())
    correct_put = int((fired_put & put_ok).sum())
    n_call, n_put = int(fired_call.sum()), int(fired_put.sum())
    n_fired = n_call + n_put
    correct = correct_call + correct_put

    # opportunities: CALL-eligible days for CALL signals, PUT-eligible for PUT,
    # union for two-sided strategies.
    opp = 0
    if n_call:
        opp += int(call_ok.sum())
    if n_put:
        opp += int(put_ok.sum())
    if n_call and n_put:  # two-sided: opportunity is any day a move happened
        opp = int((call_ok | put_ok).sum())

    precision = correct / n_fired if n_fired else float("nan")
    recall = correct / opp if opp else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if n_fired and opp and (precision + recall) > 0 else float("nan"))
    return Metrics(
        name=name, n_call=n_call, n_put=n_put,
        precision=precision, recall=recall, f1=f1,
        call_precision=correct_call / n_call if n_call else float("nan"),
        put_precision=correct_put / n_put if n_put else float("nan"),
        coverage=n_fired / len(df),
    )


# ───────────────────────── file writers ─────────────────────────

def _fmt(x: float) -> str:
    return "  n/a" if x != x else f"{x:.3f}"


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


# ───────────────────────── final daily prediction (cascade) ─────────────────────────

PRECISION_FLOOR = 0.70   # a variant only votes a side if that side's precision exceeds this
MIN_FIRES = 5            # ...and it fired that side at least this many times (noise guard)
TRAIN_FRAC = 0.60        # chronological train fraction for the out-of-sample readout
WF_WINDOW = 120          # trailing-day lookback for walk-forward eligibility
WF_MIN_FIRES = 4         # lighter fires guard inside the shorter walk-forward window


def gather_signals(df: pd.DataFrame) -> dict[str, pd.Series]:
    """All strategy variants flattened to {variant_name: signal Series}."""
    signals: dict[str, pd.Series] = {}
    for fn in STRATEGY_FAMILIES.values():
        for col, sig in fn(df).items():
            name = col.replace("strategy_", "").replace("_signal", "")
            signals[name] = sig
    return signals


def _side_precisions(elig_df: pd.DataFrame, signals: dict[str, pd.Series]):
    """Per-variant (call_precision, n_call, put_precision, n_put) measured on elig_df."""
    call_ok, put_ok = _call_ok(elig_df), _put_ok(elig_df)
    out = {}
    for name, sig in signals.items():
        s = sig.loc[elig_df.index]
        fc, fp = s == CALL, s == PUT
        nc, npp = int(fc.sum()), int(fp.sum())
        cp = int((fc & call_ok).sum()) / nc if nc else float("nan")
        pp = int((fp & put_ok).sum()) / npp if npp else float("nan")
        out[name] = (cp, nc, pp, npp)
    return out


def build_cascade(df: pd.DataFrame, signals: dict[str, pd.Series], elig_df: pd.DataFrame):
    """One prediction per day. A variant may cast a CALL vote only if its CALL
    precision (measured on elig_df) > PRECISION_FLOOR with >= MIN_FIRES fires;
    same for PUT. Each day we take the highest-precision eligible CALL vote vs the
    highest-precision eligible PUT vote, and the higher precision wins; ties or no
    eligible vote -> NO_POSITION.

    Returns (prediction Series over df.index, call_eligibility dict, put_eligibility dict).
    """
    prec = _side_precisions(elig_df, signals)
    call_elig = {n: cp for n, (cp, nc, pp, npp) in prec.items()
                 if nc >= MIN_FIRES and cp > PRECISION_FLOOR}
    put_elig = {n: pp for n, (cp, nc, pp, npp) in prec.items()
                if npp >= MIN_FIRES and pp > PRECISION_FLOOR}

    pred = pd.Series(FLAT, index=df.index)
    for idx in df.index:
        best_call = max((p for n, p in call_elig.items() if signals[n].loc[idx] == CALL),
                        default=None)
        best_put = max((p for n, p in put_elig.items() if signals[n].loc[idx] == PUT),
                       default=None)
        if best_call is not None and (best_put is None or best_call > best_put):
            pred.loc[idx] = CALL
        elif best_put is not None and (best_call is None or best_put > best_call):
            pred.loc[idx] = PUT
    return pred, call_elig, put_elig


def walk_forward(df: pd.DataFrame, signals: dict[str, pd.Series], window: int = WF_WINDOW):
    """Rolling out-of-sample prediction. For each day i (after a warm-up of
    `window` days), eligibility is fit ONLY on the trailing `window` days
    [i-window, i) and used to predict day i. Nothing from day i onward leaks in.

    Returns (prediction Series aligned to df.index, n_call_voter_days,
    n_put_voter_days) where the voter-day counts are how many predicted days had
    at least one eligible CALL / PUT voter available.
    """
    call_ok_all, put_ok_all = _call_ok(df), _put_ok(df)
    pred = pd.Series(FLAT, index=df.index)
    call_voter_days = put_voter_days = 0

    for pos in range(window, len(df)):
        win = df.iloc[pos - window:pos]
        cok, pok = call_ok_all.loc[win.index], put_ok_all.loc[win.index]
        idx = df.index[pos]

        call_elig, put_elig = {}, {}
        for name, sig in signals.items():
            w = sig.loc[win.index]
            fc, fp = w == CALL, w == PUT
            nc, npp = int(fc.sum()), int(fp.sum())
            if nc >= WF_MIN_FIRES:
                cp = int((fc & cok).sum()) / nc
                if cp > PRECISION_FLOOR:
                    call_elig[name] = cp
            if npp >= WF_MIN_FIRES:
                pp = int((fp & pok).sum()) / npp
                if pp > PRECISION_FLOOR:
                    put_elig[name] = pp

        if call_elig:
            call_voter_days += 1
        if put_elig:
            put_voter_days += 1

        best_call = max((p for n, p in call_elig.items() if signals[n].loc[idx] == CALL),
                        default=None)
        best_put = max((p for n, p in put_elig.items() if signals[n].loc[idx] == PUT),
                       default=None)
        if best_call is not None and (best_put is None or best_call > best_put):
            pred.loc[idx] = CALL
        elif best_put is not None and (best_call is None or best_put > best_call):
            pred.loc[idx] = PUT

    return pred, call_voter_days, put_voter_days


def score_final(df_sub: pd.DataFrame, pred: pd.Series) -> dict:
    """Grade a final one-per-day prediction against actual_trade_label."""
    call_ok, put_ok = _call_ok(df_sub), _put_ok(df_sub)
    label = df_sub["actual_trade_label"]
    move = call_ok | put_ok
    fc, fp, ff = pred == CALL, pred == PUT, pred == FLAT
    fired = fc | fp
    correct = (fc & call_ok) | (fp & put_ok)
    n_fired = int(fired.sum())
    n_move = int(move.sum())
    # wrong-way = took a side but the OPPOSITE exclusive move happened (the costly error)
    wrong_way = int(((fc & (label == PUT)) | (fp & (label == CALL))).sum())
    correct_flat = int((ff & (label == FLAT)).sum())
    put_base = float(put_ok.mean())
    dir_prec = int(correct.sum()) / n_fired if n_fired else float("nan")
    return {
        "n": len(df_sub),
        "n_call": int(fc.sum()), "n_put": int(fp.sum()), "n_flat": int(ff.sum()),
        "dir_precision": dir_prec,
        "dir_recall": int(correct.sum()) / n_move if n_move else float("nan"),
        "wrong_way_rate": wrong_way / n_fired if n_fired else float("nan"),
        "overall_accuracy": (int(correct.sum()) + correct_flat) / len(df_sub),
        "put_base": put_base,
        "lift": dir_prec / put_base if put_base else float("nan"),
    }


def _confusion_lines(df_sub: pd.DataFrame, pred: pd.Series) -> list[str]:
    label = df_sub["actual_trade_label"]
    acts = [CALL, PUT, "BOTH", FLAT]
    disp = {CALL: "CALL", PUT: "PUT", "BOTH": "BOTH", FLAT: "NONE"}
    lines = [f"    {'pred \\ actual':<16}" + "".join(f"{disp[a]:>7}" for a in acts)]
    for p in [CALL, PUT, FLAT]:
        row = pred == p
        cells = "".join(f"{int((row & (label == a)).sum()):>7}" for a in acts)
        lines.append(f"    {disp[p]:<16}{cells}")
    return lines


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


def cascade_report(df: pd.DataFrame, signals: dict[str, pd.Series]):
    """Build the base.txt cascade section and the per-row final_prediction
    (in-sample eligibility). Also runs a 60/40 chronological out-of-sample readout."""
    pred_full, call_elig, put_elig = build_cascade(df, signals, df)

    lines = [
        "",
        "Final daily prediction — precision cascade",
        "------------------------------------------",
        "One prediction per day, picked as follows:",
        f"  - A variant may cast a CALL vote only if its CALL precision > "
        f"{PRECISION_FLOOR:.0%} with >= {MIN_FIRES} fires; likewise for PUT.",
        "  - Each day, the highest-precision eligible CALL vote competes with the",
        "    highest-precision eligible PUT vote; the higher precision wins.",
        "  - No eligible vote (or an exact tie) -> NO_POSITION.",
        "  - Precisions are measured on the eligibility window noted in each block.",
        "",
        "  Eligible CALL voters (variant: in-sample CALL precision):",
    ]
    if call_elig:
        for n, p in sorted(call_elig.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"    {n:<42}{p:.3f}")
    else:
        lines.append("    (none cleared the precision floor)")
    lines.append("  Eligible PUT voters (variant: in-sample PUT precision):")
    if put_elig:
        for n, p in sorted(put_elig.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"    {n:<42}{p:.3f}")
    else:
        lines.append("    (none cleared the precision floor)")
    lines.append("")

    # In-sample scorecard (eligibility + evaluation both on full data)
    lines.append("In-sample (eligibility + evaluation on full data; optimistic)")
    lines.append("-" * 60)
    lines += _final_metric_block("Full-period cascade:", score_final(df, pred_full))
    lines.append("  Confusion matrix:")
    lines += _confusion_lines(df, pred_full)
    lines.append("")

    # Out-of-sample 60/40 chronological split
    cut = int(len(df) * TRAIN_FRAC)
    train, test = df.iloc[:cut], df.iloc[cut:]
    pred_oos, ce_t, pe_t = build_cascade(df, signals, train)  # eligibility from train only
    lines.append(f"Out-of-sample 60/40 time split (eligibility from first {cut} days,")
    lines.append(f"evaluated on the held-out last {len(test)} days)")
    lines.append("-" * 60)
    lines.append(f"  Train-period eligible CALL voters: "
                 f"{', '.join(sorted(ce_t)) or '(none)'}")
    lines.append(f"  Train-period eligible PUT voters : "
                 f"{', '.join(sorted(pe_t)) or '(none)'}")
    lines += _final_metric_block("Train slice (in-sample):",
                                 score_final(train, pred_oos.loc[train.index]))
    lines += _final_metric_block("Test slice (out-of-sample, the honest number):",
                                 score_final(test, pred_oos.loc[test.index]))
    lines.append("  Test confusion matrix:")
    lines += _confusion_lines(test, pred_oos.loc[test.index])
    lines.append("")

    # Rolling walk-forward: trailing-window eligibility, predict the next day.
    wf_pred, wf_call_days, wf_put_days = walk_forward(df, signals)
    wf_eval = df.iloc[WF_WINDOW:]
    lines.append(f"Walk-forward (rolling {WF_WINDOW}-day trailing eligibility, "
                 f">={WF_MIN_FIRES} fires)")
    lines.append("-" * 60)
    lines.append("Each predicted day uses only the prior {0} days to decide which "
                 "variants".format(WF_WINDOW))
    lines.append("are eligible, then predicts that single day. No future data leaks in.")
    lines.append(f"  days with >=1 eligible CALL voter: {wf_call_days} of {len(wf_eval)}")
    lines.append(f"  days with >=1 eligible PUT voter : {wf_put_days} of {len(wf_eval)}")
    lines += _final_metric_block("Walk-forward (out-of-sample, the most realistic number):",
                                 score_final(wf_eval, wf_pred.loc[wf_eval.index]))
    lines.append("  Walk-forward confusion matrix:")
    lines += _confusion_lines(wf_eval, wf_pred.loc[wf_eval.index])
    lines.append("")
    lines.append("Caveat: precision floors are fit on the same year they are graded on,")
    lines.append("so the in-sample block is optimistic. The 60/40 split freezes eligibility")
    lines.append("on the first half, which can starve a time-concentrated edge (e.g. the PUT")
    lines.append("momentum leg). The walk-forward re-fits eligibility on a trailing window,")
    lines.append("so a side activates once its recent precision earns it — the most honest")
    lines.append("estimate here, though still thin on a single year of data.")
    lines.append("")

    return lines, pred_full


def write_base(df: pd.DataFrame, cascade_lines: list[str]) -> None:
    EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(BASE_CSV, index=False)

    bc = _call_ok(df).mean()
    bp = _put_ok(df).mean()
    label_counts = df["actual_trade_label"].value_counts().to_dict()
    txt = f"""NIFTY direction-prediction experiment — BASE dataset
=====================================================
rows: {len(df)}   date range: {df['trade_date'].min()} .. {df['trade_date'].max()}

THRESHOLD = {THRESHOLD:.3%}  (next-day intraday move from next_open)

actual_trade_label
------------------
For each trade_date, the realised next-day intraday move from next_open is checked:
  CALL  : (next_high - next_open) / next_open >= {THRESHOLD:.3%}   (and PUT side did not)
  PUT   : (next_open - next_low ) / next_open >= {THRESHOLD:.3%}   (and CALL side did not)
  BOTH  : both the +{THRESHOLD:.1%} and -{THRESHOLD:.1%} moves were touched intraday
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
        "\nExcluded from this file by design: *_regime columns, strategy_* signal "
        "columns,\nand final_raw_signal. The final_prediction column in base.csv is the "
        "cascade\noutput described above. Strategy signals live in the per-strategy CSVs.\n"
    )
    full = txt + "\n".join(cascade_lines) + footer
    (EXPERIMENT_DIR / "base.txt").write_text(full, encoding="utf-8")


def write_strategy(df: pd.DataFrame, family: str, signals: dict[str, pd.Series]) -> list[Metrics]:
    out = df.drop(columns=["final_prediction"], errors="ignore").copy()
    metrics: list[Metrics] = []
    ordered_names: list[str] = []
    for col, sig in signals.items():
        out[col] = sig
        metric_name = col.replace("strategy_", "").replace("_signal", "")
        ordered_names.append(metric_name)
        metrics.append(score_signal(df, sig, metric_name))

    csv_path = EXPERIMENT_DIR / f"strategy_{family}.csv"
    out.to_csv(csv_path, index=False)

    bc, bp = _call_ok(df).mean(), _put_ok(df).mean()
    lines = [
        f"Strategy family: {family}",
        "=" * (17 + len(family)),
        f"rows: {len(df)}   THRESHOLD = {THRESHOLD:.3%}",
        f"base CALL precision (random): {bc:.3f}   base PUT precision (random): {bp:.3f}",
        "",
        "Definitions",
        "-----------",
    ]
    for nm in ordered_names:
        definition = STRATEGY_DEFINITIONS.get(nm, "(definition not recorded)")
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
        lines += miss_patterns(df, sig, metric_name)

    lines += ["Notes",
              "-----",
              "precision = correct fires / total fires.",
              "recall    = correct fires / days the threshold move actually occurred.",
              "f1        = harmonic mean of precision and recall.",
              "Add tweaked variants as new strategy_<family>_<variant>_signal columns to",
              "this same CSV to compare against the existing variants here.", ""]
    (EXPERIMENT_DIR / f"strategy_{family}.txt").write_text("\n".join(lines), encoding="utf-8")
    return metrics


def write_comparison(df: pd.DataFrame, all_metrics: list[Metrics]) -> None:
    bc, bp = _call_ok(df).mean(), _put_ok(df).mean()
    lines = [
        "NIFTY direction-prediction — strategy comparison",
        "================================================",
        f"rows: {len(df)}   THRESHOLD = {THRESHOLD:.3%}",
        f"base CALL precision (random): {bc:.3f}   base PUT precision (random): {bp:.3f}",
        "",
        f"{'strategy':<42}{'side':>6}{'n':>6}{'prec':>8}{'recall':>8}{'f1':>8}{'cov':>7}",
        "-" * 91,
    ]
    for m in all_metrics:
        side = "BOTH" if (m.n_call and m.n_put) else ("CALL" if m.n_call else "PUT")
        lines.append(
            f"{m.name:<42}{side:>6}{m.n_call + m.n_put:>6}"
            f"{_fmt(m.precision):>8}{_fmt(m.recall):>8}{_fmt(m.f1):>8}{m.coverage:>7.3f}"
        )
    lines += [
        "",
        "Observations",
        "------------",
        "1. CALL edge is mean-reversion: NIFTY tends to bounce >=0.5% the day after",
        "   an oversold/low-in-range close. OversoldBounceCall_HighPrecision is the",
        "   sharpest CALL (fewest trades, highest precision); _MoreTrades trades more",
        "   often at slightly lower precision.",
        "2. PUT edge is momentum continuation: down-trending, high-volume days tend to",
        "   extend >=0.5% lower next day. DownMomentumPut_HighPrecision adds 'VIX rising'",
        "   for the highest precision but few trades; _MoreTrades swaps that for a VIX",
        "   level gate to fire more often.",
        "3. India VIX is a confirmation/gate, not a standalone trigger. A VIX level gate",
        "   (>=13) removes dead low-volatility days where 0.5% rarely prints; 'VIX rising'",
        "   specifically sharpens PUT precision.",
        "4. MomentumDirectional merges the best-balanced CALL and PUT into one",
        "   two-sided signal. NOTE the two sides use OPPOSITE logic (CALL =",
        "   mean-reversion, PUT = momentum-continuation) and overlap on ~60 of the",
        "   falling/oversold days, so conflicts are common. They are resolved by",
        "   normalised vote strength (the more strongly confirmed side wins), which",
        "   gives the best precision/recall balance; dropping conflicts instead",
        "   collapses the signal. It trades per-trade precision for the highest recall.",
        "5. Precision and recall trade off: sniper variants sit at ~7-15% coverage with",
        "   high precision; the balanced directional sits near ~30-40% coverage. Choose",
        "   by desired trade frequency vs hit-rate.",
        "",
        "Legacy strategies (reproduced from the previously-dropped strategy_* columns)",
        "----------------------------------------------------------------------------",
        "6. MAAlignmentRoom family (MAAlignmentRoom, _PutGuarded, _ReboundCall) plus",
        "   MaTrend_001 are MA-alignment / MA-trend rules. They are ungated here (the",
        "   regime columns are excluded from base.csv).",
        "7. MeanReversion merges BollingerMeanReversion and RsiMeanReversion_6040 — both",
        "   are mean-reversion rules, so they share one family file.",
        "8. RangeBreakout merges trendUpRangeBreakout and trendDownRangeBreakout into one",
        "   two-sided 20-day breakout (CALL above prior-20d high, PUT below prior-20d low).",
        "   Originally these were the same breakout gated on TREND_UP vs TREND_DOWN; with",
        "   regime excluded they collapse to a single signal. In this ~95% RANGE sample,",
        "   breakouts are rare and tend to be false (mean-reverting tape), so expect low",
        "   precision relative to the mean-reversion CALL and momentum PUT rules.",
        "",
        "Caveat: single year of data (~95% RANGE regime). The CALL mean-reversion bet",
        "weakens in a sustained downtrend; re-validate as more data accrues.",
        "",
    ]
    (EXPERIMENT_DIR / "comparison.txt").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    df = build_base()
    signals = gather_signals(df)
    cascade_lines, final_pred = cascade_report(df, signals)
    df["final_prediction"] = final_pred
    write_base(df, cascade_lines)
    print(f"wrote base.csv ({len(df)} rows) + base.txt")

    all_metrics: list[Metrics] = []
    for family, fn in STRATEGY_FAMILIES.items():
        fam_signals = fn(df)
        metrics = write_strategy(df, family, fam_signals)
        all_metrics.extend(metrics)
        print(f"wrote strategy_{family}.csv + .txt ({len(fam_signals)} variant(s))")

    write_comparison(df, all_metrics)
    print("wrote comparison.txt")
    print("\nSummary:")
    for m in all_metrics:
        print(f"  {m.name:<42} prec={_fmt(m.precision)} recall={_fmt(m.recall)} f1={_fmt(m.f1)}")


if __name__ == "__main__":
    main()
