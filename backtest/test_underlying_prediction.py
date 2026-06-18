"""
NIFTY underlying prediction — unit tests + prediction CSV generator.

Unittest mode (pytest):
    pytest backtest/test_underlying_prediction.py

Script mode — build output/backtest/NIFTY_prediction.csv from SignalFeatureDaily:
    python backtest/test_underlying_prediction.py
    python backtest/test_underlying_prediction.py --underlying NIFTY --start 2026-04-01 --end 2026-06-17
    python backtest/test_underlying_prediction.py --output output/backtest/NIFTY_prediction.csv

CSV produced (one row per SignalFeatureDaily date):
    date, underlying, close, MA/RSI/ATR/BB/return features, regime,
    per-strategy signals (CALL/PUT/NO_POSITION — only strategies active for that regime),
    raw_signal, direction, strength_score, confidence,
    option_bias, is_option_eligible, primary_strategy, setup_type,
    expected_move_pct/abs/holding_days, score components.

Regime-to-strategy routing (strategies outside the active set are marked NO_POSITION):
    TREND_UP   → MaTrend_001, trendUpRangeBreakout, BollingerMeanReversion
    TREND_DOWN → MaTrend_001, trendDownRangeBreakout, BollingerMeanReversion
    RANGE      → rangeBollingerMeanReversion, rangeRsiMeanReversion_6535
    CHOPPY     → (none — veto, always NO_POSITION)
    UNKNOWN    → (none — insufficient data)
"""

from __future__ import annotations

import argparse
import sys
import unittest
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

load_dotenv(_repo_root / ".env")

# ── shared prediction imports ─────────────────────────────────────────────────
from src.technical_analysis.prediction.aggregator import build_strategy_signal, build_strategy_signals
from src.technical_analysis.prediction.expected_move import estimate_expected_move
from src.technical_analysis.prediction.schema import (
    RegimeSnapshot,
    StrategySignal,
    UnderlyingFeatureSnapshot,
)
from src.technical_analysis.prediction.scoring import score_underlying_view
from src.technical_analysis.prediction.view import build_underlying_view

# ── pipeline-only imports ─────────────────────────────────────────────────────
from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client
from src.technical_analysis.prediction.highsight_regime import compute_hindsight_regime
from src.technical_analysis.prediction.regime import detect_regime as _detect_regime
from src.technical_analysis.prediction.snapshot import build_regime_snapshot
from src.technical_analysis.prediction.strategies import (
    signal_bollinger_mean_reversion,
    signal_ma_trend,
    signal_range_breakout,
    signal_rsi_mean_reversion,
)
from src.technical_analysis.prediction.underlying_registry import (
    DEFAULT_LOOKBACK_DAYS,
    load_underlying_prediction_strategies,
)

# ─────────────────────────────────────────────────────────────────────────────
# Unit tests  (pytest picks these up automatically)
# ─────────────────────────────────────────────────────────────────────────────

def _bullish_features() -> UnderlyingFeatureSnapshot:
    return UnderlyingFeatureSnapshot(
        symbol="TEST", trade_date="2026-05-15",
        close=110, volume=150000,
        ma10=108, ma20=105, ma50=100, ma90=95,
        ma20_slope=0.02, ma50_slope=0.015,
        rsi14=58, atr14=3,
        bb_upper=112, bb_middle=105, bb_lower=98, bb_width=0.12,
        ret_5d=0.03, ret_20d=0.08, ret_60d=0.18,
        volatility_20d=0.018, volume_avg_20d=100000,
        volume_ratio=None, trend_efficiency=0.55, range_position=0.75,
        relative_strength_vs_sector=0.03, relative_strength_vs_benchmark=0.04,
    )


def _bearish_features() -> UnderlyingFeatureSnapshot:
    return UnderlyingFeatureSnapshot(
        symbol="TEST", trade_date="2026-05-15",
        close=90, volume=150000,
        ma10=92, ma20=95, ma50=100, ma90=105,
        ma20_slope=-0.02, ma50_slope=-0.015,
        rsi14=42, atr14=3,
        bb_upper=102, bb_middle=95, bb_lower=88, bb_width=0.12,
        ret_5d=-0.03, ret_20d=-0.08, ret_60d=-0.18,
        volatility_20d=0.018, volume_avg_20d=100000,
        volume_ratio=None, trend_efficiency=0.55, range_position=0.25,
        relative_strength_vs_sector=-0.03, relative_strength_vs_benchmark=-0.04,
    )


def _regime(stock: str, sector: str | None = None, benchmark: str | None = None) -> RegimeSnapshot:
    return RegimeSnapshot(
        stock_regime=stock,  # type: ignore[arg-type]
        sector_regime=sector,  # type: ignore[arg-type]
        benchmark_regime=benchmark,  # type: ignore[arg-type]
        stock_regime_confidence=None, sector_regime_confidence=None,
        benchmark_regime_confidence=None, regime_reasons=[],
    )


class UnderlyingPredictionViewTests(unittest.TestCase):
    def test_bullish_trend_scores_high(self) -> None:
        score = score_underlying_view(
            _bullish_features(), _regime("TREND_UP", "TREND_UP", "TREND_UP"),
            "BULLISH", "TREND_UP_PULLBACK_LONG",
        )
        self.assertGreaterEqual(score.final_score, 80)

    def test_bearish_trend_scores_high(self) -> None:
        score = score_underlying_view(
            _bearish_features(), _regime("TREND_DOWN", "TREND_DOWN", "TREND_DOWN"),
            "BEARISH", "TREND_DOWN_RALLY_SHORT",
        )
        self.assertGreaterEqual(score.final_score, 80)

    def test_choppy_regime_blocks_option_eligibility(self) -> None:
        features = _bullish_features()
        reg = _regime("CHOPPY", "TREND_UP", "TREND_UP")
        signals = [build_strategy_signal("MaTrend_001", "CALL", features, reg)]
        view = build_underlying_view("TEST", "2026-05-15", features, reg, signals)
        self.assertFalse(view.is_option_eligible)
        self.assertEqual(view.option_bias, "NEUTRAL")

    def test_expected_move_uses_breakout_multiplier(self) -> None:
        expected_pct, expected_abs, holding_days = estimate_expected_move(
            _bullish_features(), "TREND_UP", "TREND_UP_BREAKOUT_LONG",
        )
        self.assertAlmostEqual(expected_abs or 0, 3.75)
        self.assertAlmostEqual(expected_pct or 0, 3.75 / 110)
        self.assertEqual(holding_days, 3)

    def test_underlying_view_maps_high_score_to_strong_bias(self) -> None:
        features = _bullish_features()
        reg = _regime("TREND_UP", "TREND_UP", "TREND_UP")
        signals = build_strategy_signals(
            {"MaTrend_001": "CALL", "trendUpRangeBreakout": "CALL"}, features, reg
        )
        view = build_underlying_view("TEST", "2026-05-15", features, reg, signals)
        self.assertEqual(view.raw_signal, "CALL")
        self.assertEqual(view.option_bias, "BULLISH_STRONG")
        self.assertTrue(view.is_option_eligible)

    def test_conflicting_strategies_return_no_position(self) -> None:
        features = _bullish_features()
        reg = _regime("TREND_UP", "TREND_UP", "TREND_UP")
        signals = [
            StrategySignal(
                strategy_name="bull", raw_signal="CALL", direction="BULLISH",
                setup_type="TREND_UP_PULLBACK_LONG", score=80, confidence="HIGH",
                expected_holding_days=3, expected_move_pct=0.02, expected_move_abs=2,
                stop_loss_pct=None, target_pct=0.02, reward_risk=None, reasons=[], warnings=[],
            ),
            StrategySignal(
                strategy_name="bear", raw_signal="PUT", direction="BEARISH",
                setup_type="TREND_DOWN_RALLY_SHORT", score=80, confidence="HIGH",
                expected_holding_days=3, expected_move_pct=0.02, expected_move_abs=2,
                stop_loss_pct=None, target_pct=0.02, reward_risk=None, reasons=[], warnings=[],
            ),
        ]
        view = build_underlying_view("TEST", "2026-05-15", features, reg, signals)
        self.assertEqual(view.raw_signal, "NO_POSITION")
        self.assertFalse(view.is_option_eligible)


# ─────────────────────────────────────────────────────────────────────────────
# Prediction CSV pipeline
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_OUTPUT = Path("output") / "backtest" / "NIFTY" / "prediction" / "NIFTY_prediction.csv"
DEFAULT_RESEARCH_OUTPUT = Path("output") / "backtest" / "NIFTY" / "prediction" / "NIFTY_prediction_research.csv"
DEFAULT_REGIME_COMPARISON = Path("output") / "backtest" / "NIFTY" / "regime" / "NIFTY_regime_experiment_comparison.csv"
_EXTRA_OHLCV_DAYS = 120  # rolling window lookback before start_date


def _call_only(fn: Any) -> Any:
    """Return CALL from fn; anything else becomes NO_POSITION (for direction-constrained setups)."""
    def _inner(w: Any) -> str:
        r = fn(w)
        return r if r == "CALL" else "NO_POSITION"
    return _inner


def _put_only(fn: Any) -> Any:
    def _inner(w: Any) -> str:
        r = fn(w)
        return r if r == "PUT" else "NO_POSITION"
    return _inner


# Per-regime callable map — bypasses internal detect_regime() inside range-gated wrappers
# so the DB-computed regime (from SignalFeatureDaily) is always authoritative.
# RANGE RSI thresholds relaxed to 60/40 (were 65/35) to fire in moderate RANGE conditions.
_REGIME_DIRECT_CALLS: dict[str, dict[str, Any]] = {
    "TREND_UP": {
        "MaTrend_001":           signal_ma_trend,
        "trendUpRangeBreakout":  _call_only(signal_range_breakout),
        "BollingerMeanReversion": signal_bollinger_mean_reversion,
    },
    "TREND_DOWN": {
        "MaTrend_001":            signal_ma_trend,
        "trendDownRangeBreakout": _put_only(signal_range_breakout),
        "BollingerMeanReversion": signal_bollinger_mean_reversion,
    },
    "RANGE": {
        "rangeBollingerMeanReversion":  signal_bollinger_mean_reversion,
        "rangeRsiMeanReversion_6535":   partial(signal_rsi_mean_reversion, overbought=60.0, oversold=40.0),
    },
    "CHOPPY":  {},
    "UNKNOWN": {},
}

_RESEARCH_DIRECT_CALLS: dict[str, Any] = {
    "BollingerMeanReversion": signal_bollinger_mean_reversion,
    "MaTrend_001": signal_ma_trend,
    "RsiMeanReversion_6535": partial(
        signal_rsi_mean_reversion,
        rsi_period=14,
        overbought=65.0,
        oversold=35.0,
    ),
    "rangeBollingerMeanReversion": signal_bollinger_mean_reversion,
    "rangeRsiMeanReversion_6535": partial(
        signal_rsi_mean_reversion,
        rsi_period=14,
        overbought=60.0,
        oversold=40.0,
    ),
    "trendDownRangeBreakout": _put_only(signal_range_breakout),
    "trendUpRangeBreakout": _call_only(signal_range_breakout),
    "choppy": lambda _window: "NO_POSITION",
    "unknown": lambda _window: "NO_POSITION",
}


_SIGNIFICANCE_PCT = 0.5   # |intraday_chg_pct| >= this = "significant move" for recall


def _generate_prediction_summary(
    df: pd.DataFrame,
    output_path: Path,
    significance_pct: float = _SIGNIFICANCE_PCT,
) -> None:
    """Print and write accuracy / recall for the prediction backtest."""

    def _is_strategy_col(col: str, s: pd.Series) -> bool:
        if col == "raw_signal":
            return False
        vals = set(s.dropna().unique())
        return bool(vals) and vals.issubset({"CALL", "PUT", "NO_POSITION"})

    strategy_cols = [c for c in df.columns if _is_strategy_col(c, df[c])]
    w = df.copy()
    has_chg = "next_day_close_chg_pct" in w.columns
    n_rows = len(w)

    lines: list[str] = []

    def _acc(signal_col: str) -> str:
        if not has_chg:
            return "    Accuracy: n/a (no close-chg column)"
        chg = w["next_day_close_chg_pct"]
        valid = chg.notna()
        calls = (w[signal_col] == "CALL") & valid
        puts  = (w[signal_col] == "PUT") & valid
        total = int(calls.sum() + puts.sum())
        if total == 0:
            return "    Accuracy: no signals"
        correct = int(((w[signal_col] == "CALL") & valid & (chg > 0)).sum()
                    + ((w[signal_col] == "PUT") & valid & (chg < 0)).sum())
        wrong = total - correct
        pct = 100.0 * correct / total
        call_ok = int(((w[signal_col] == "CALL") & valid & (chg > 0)).sum())
        put_ok  = int(((w[signal_col] == "PUT")  & valid & (chg < 0)).sum())
        nc = int(calls.sum()); np_ = int(puts.sum())
        return (f"    Accuracy : {correct}/{total} = {pct:.0f}%  "
                f"(CALL {call_ok}/{nc} correct, PUT {put_ok}/{np_} correct, {wrong} wrong)")

    def _rec(signal_col: str) -> str:
        if not has_chg:
            return "    Recall   : n/a (no close-chg column)"
        chg = w["next_day_close_chg_pct"]
        active = w[signal_col].notna()
        sig_moves = active & chg.notna() & (chg.abs() >= significance_pct)
        total_sig = int(sig_moves.sum())
        if total_sig == 0:
            return f"    Recall   : no significant moves (>={significance_pct}%) in active window"
        caught = int((sig_moves & w[signal_col].isin({"CALL", "PUT"})).sum())
        missed = int((sig_moves & (w[signal_col] == "NO_POSITION")).sum())
        pct = 100.0 * caught / total_sig
        return (f"    Recall   : caught {caught}/{total_sig} sig moves = {pct:.0f}%  "
                f"(missed {missed} where strategy said NO_POSITION)")

    total_sig_chg = 0
    if has_chg:
        total_sig_chg = int((w["next_day_close_chg_pct"].notna()
                              & (w["next_day_close_chg_pct"].abs() >= significance_pct)).sum())

    lines.append(f"\n{'='*62}")
    lines.append("  NIFTY Prediction Summary")
    lines.append(f"{'='*62}")
    lines.append(f"  Rows: {n_rows}   Significance threshold: |close chg| >= {significance_pct}%")

    lines.append("\n--- OVERALL (aggregate raw_signal) ---")
    n_call = int((w["raw_signal"] == "CALL").sum())
    n_put  = int((w["raw_signal"] == "PUT").sum())
    n_nop  = int((w["raw_signal"] == "NO_POSITION").sum())
    lines.append(f"  Signals : CALL {n_call}  PUT {n_put}  NO_POSITION {n_nop}")
    if has_chg:
        lines.append(f"  Sig moves next day: {total_sig_chg}/{n_rows}")
    lines.append(_acc("raw_signal"))
    lines.append(_rec("raw_signal"))

    if {"regime", "hindsight_regime", "regime_match"}.issubset(w.columns):
        comparable = w[w["hindsight_regime"].notna() & (w["hindsight_regime"] != "UNKNOWN")]
        total_regime = len(comparable)
        correct_regime = int((comparable["regime_match"] == True).sum())
        regime_pct = 100.0 * correct_regime / total_regime if total_regime else 0.0
        lines.append("\n--- REGIME DETECTION ---")
        lines.append(f"  Comparable hindsight rows: {total_regime}/{n_rows}")
        if total_regime:
            lines.append(f"  Accuracy : {correct_regime}/{total_regime} = {regime_pct:.0f}%")
            mismatch = comparable[comparable["regime_match"] != True]
            if not mismatch.empty:
                pairs = mismatch.groupby(["regime", "hindsight_regime"]).size().sort_values(ascending=False)
                lines.append("  Top mismatches:")
                for (detected, hindsight), count in pairs.head(5).items():
                    lines.append(f"    {detected} vs {hindsight}: {int(count)}")

    lines.append(f"\n--- PER STRATEGY ---")
    for col in strategy_cols:
        n_active = int(w[col].notna().sum())
        n_sig_s  = int(w[col].isin({"CALL", "PUT"}).sum())
        n_nop_s  = int((w[col] == "NO_POSITION").sum())
        lines.append(f"\n  [{col}]  active {n_active}/{n_rows}  "
                     f"signals {n_sig_s}  NO_POSITION {n_nop_s}")
        lines.append(_acc(col))
        lines.append(_rec(col))

    lines.append("")
    text = "\n".join(lines)
    print(text)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    print(f"Summary written to {output_path}")


def _fetch_ohlcv(conn, underlying: str, lookback_start: date, end_date: date) -> pd.DataFrame:
    sql = """
        SELECT trade_date, open_price, high_price, low_price, close_price, volume
        FROM "UnderlyingSnapshot"
        WHERE underlying = %s AND trade_date >= %s AND trade_date <= %s
        ORDER BY trade_date
    """
    df = pd.read_sql_query(sql, conn, params=(underlying.upper(), lookback_start, end_date))
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.sort_values("trade_date").reset_index(drop=True)


def _fetch_signal_features(conn, underlying: str, start_date: date, end_date: date) -> pd.DataFrame:
    sql = """
        SELECT *
        FROM "SignalFeatureDaily"
        WHERE symbol = %s AND signal_date >= %s AND signal_date <= %s
          AND feature_version = 'v1'
        ORDER BY signal_date
    """
    df = pd.read_sql_query(sql, conn, params=(underlying.upper(), start_date, end_date))
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    return df


def _f(val: Any) -> float | None:
    try:
        if val is None or pd.isna(val):
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _sf_row_to_feature_snapshot(row: dict[str, Any], symbol: str) -> UnderlyingFeatureSnapshot:
    return UnderlyingFeatureSnapshot(
        symbol=symbol.upper(),
        trade_date=str(row["signal_date"])[:10],
        close=_f(row.get("close_1515")),
        volume=_f(row.get("volume_day")),
        ma10=_f(row.get("ma10")), ma20=_f(row.get("ma20")),
        ma50=_f(row.get("ma50")), ma90=_f(row.get("ma90")),
        ma20_slope=_f(row.get("ma20_slope")), ma50_slope=_f(row.get("ma50_slope")),
        rsi14=_f(row.get("rsi14")), atr14=_f(row.get("atr14")),
        bb_upper=_f(row.get("bb_upper")), bb_middle=_f(row.get("bb_middle")),
        bb_lower=_f(row.get("bb_lower")), bb_width=_f(row.get("bb_width")),
        ret_5d=_f(row.get("ret_5d")), ret_20d=_f(row.get("ret_20d")),
        ret_60d=_f(row.get("ret_60d")),
        volatility_20d=_f(row.get("volatility_20d")),
        volume_avg_20d=_f(row.get("volume_20d")),
        volume_ratio=None,
        trend_efficiency=_f(row.get("trend_efficiency_60d")),
        range_position=_f(row.get("range_position_20d")),
        relative_strength_vs_sector=_f(row.get("relative_strength_vs_sector")),
        relative_strength_vs_benchmark=None,
    )


def _load_current_regime_overrides(path: Path | None, underlying: str) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    df = pd.read_csv(path)
    required = {"date", "current_regime"}
    if not required.issubset(df.columns):
        return {}
    if "underlying" in df.columns:
        df = df[df["underlying"].astype(str).str.upper() == underlying.upper()]

    overrides: dict[str, str] = {}
    for _, row in df.iterrows():
        regime = str(row.get("current_regime") or "").upper()
        if regime in {"TREND_UP", "TREND_DOWN", "RANGE", "CHOPPY", "UNKNOWN"}:
            overrides[str(row["date"])[:10]] = regime
    return overrides


def _ensure_regime_comparison(
    path: Path | None,
    underlying: str,
    start_date: date,
    end_date: date,
) -> Path | None:
    if path is None or path.exists():
        return path

    print(f"Regime comparison missing at {path}; running regime experiments first...")
    from backtest.test_regime import run_regime_experiments

    result = run_regime_experiments(
        underlying=underlying.upper(),
        start_date=start_date,
        end_date=end_date,
        output_dir=path.parent,
    )
    generated = Path(str(result.get("comparison_path") or path))
    return generated if generated.exists() else path


def generate_prediction_csv(
    underlying: str = "NIFTY",
    start_date: date | None = None,
    end_date: date | None = None,
    output_path: Path = DEFAULT_OUTPUT,
    research_output_path: Path | None = DEFAULT_RESEARCH_OUTPUT,
    regime_comparison_path: Path | None = DEFAULT_REGIME_COMPARISON,
) -> dict[str, Any]:
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = date(end_date.year - 1, end_date.month, end_date.day)

    strategies = load_underlying_prediction_strategies()
    strategy_names = sorted(strategies.keys())
    lookback_start = start_date - timedelta(days=_EXTRA_OHLCV_DAYS)
    regime_comparison_path = _ensure_regime_comparison(
        regime_comparison_path,
        underlying,
        start_date,
        end_date,
    )

    print(f"Fetching data for {underlying} {start_date} to {end_date} ...")
    settings = get_settings()
    db = get_database_client(settings)
    db.connect()
    try:
        ohlcv_df = _fetch_ohlcv(db.conn, underlying, lookback_start, end_date)
        sf_df = _fetch_signal_features(db.conn, underlying, start_date, end_date)
    finally:
        db.close()

    if sf_df.empty:
        print(f"No SignalFeatureDaily rows for {underlying} {start_date}–{end_date}")
        return {"rows": 0, "path": str(output_path)}

    regime_overrides = _load_current_regime_overrides(regime_comparison_path, underlying)
    if regime_overrides:
        print(f"Loaded {len(regime_overrides)} current-regime overrides from {regime_comparison_path}")

    # Index OHLCV by date for fast rolling window slicing
    ohlcv_df = ohlcv_df.copy()
    ohlcv_df["_idx"] = range(len(ohlcv_df))
    date_to_ohlcv_idx: dict[pd.Timestamp, int] = {
        row["trade_date"]: int(row["_idx"]) for _, row in ohlcv_df.iterrows()
    }

    # Build next-day close/open lookups for the backtest columns
    _sorted_ohlcv = ohlcv_df.sort_values("trade_date").reset_index(drop=True)

    def _as_date(v: Any) -> date:
        return v.date() if hasattr(v, "date") else v

    _ohlcv_by_date: dict[date, dict[str, Any]] = {
        _as_date(r["trade_date"]): r.to_dict()
        for _, r in _sorted_ohlcv.iterrows()
    }
    _close_by_date: dict[date, float] = {
        d: float(r["close_price"])
        for d, r in _ohlcv_by_date.items()
    }
    _sorted_dates = sorted(_close_by_date.keys())
    _next_date: dict[date, date] = {
        _sorted_dates[i]: _sorted_dates[i + 1]
        for i in range(len(_sorted_dates) - 1)
    }
    _next_day_close: dict[date, float] = {
        _sorted_dates[i]: _close_by_date[_sorted_dates[i + 1]]
        for i in range(len(_sorted_dates) - 1)
    }

    output_rows: list[dict[str, Any]] = []
    research_rows: list[dict[str, Any]] = []

    for _, sf_row in sf_df.iterrows():
        signal_ts = pd.to_datetime(sf_row["signal_date"])
        signal_date_str = str(signal_ts)[:10]
        db_regime = str(sf_row.get("regime") or "UNKNOWN").upper()
        regime = regime_overrides.get(signal_date_str, db_regime)

        # Build rolling window DataFrame for raw strategy functions
        idx = date_to_ohlcv_idx.get(signal_ts)
        if idx is not None:
            window = ohlcv_df.iloc[max(0, idx - DEFAULT_LOOKBACK_DAYS + 1): idx + 1].copy()
        else:
            window = pd.DataFrame()

        # If DB stored UNKNOWN (insufficient history at the time), re-detect from the window
        # using the updated (relaxed) thresholds — avoids both UNKNOWN and stale DB values.
        if regime == "UNKNOWN" and not window.empty:
            regime = _detect_regime(window).upper()

        # Call the raw signal functions directly (bypasses internal detect_regime wrappers).
        # Inactive strategies → None (blank in CSV, not NO_POSITION, to avoid confusion).
        direct_fns = _REGIME_DIRECT_CALLS.get(regime, {})
        predictions: dict[str, str | None] = {}
        for name in strategy_names:
            fn = direct_fns.get(name)
            if fn is None or window.empty:
                predictions[name] = None
            else:
                try:
                    predictions[name] = fn(window)
                except Exception:
                    predictions[name] = None

        research_predictions: dict[str, str] = {}
        for name in strategy_names:
            strategy = _RESEARCH_DIRECT_CALLS.get(name) or strategies.get(name)
            if strategy is None or window.empty:
                research_predictions[name] = "NO_POSITION"
                continue
            try:
                raw = str(strategy(window)).strip().upper()
                research_predictions[name] = raw if raw in {"CALL", "PUT", "NO_POSITION"} else "NO_POSITION"
            except Exception:
                research_predictions[name] = "NO_POSITION"

        # Build feature snapshot from pre-computed SignalFeatureDaily values
        features = _sf_row_to_feature_snapshot(sf_row.to_dict(), underlying)

        # Build full view (regime → signals → scoring → view)
        regime_snap = build_regime_snapshot(regime)
        strategy_signals = build_strategy_signals(predictions, features, regime_snap)
        research_strategy_signals = build_strategy_signals(research_predictions, features, regime_snap)
        research_strategy_signals_by_name = {signal.strategy_name: signal for signal in research_strategy_signals}
        view = build_underlying_view(
            underlying, signal_date_str, features, regime_snap, strategy_signals
        )

        row: dict[str, Any] = {
            "date": signal_date_str,
            "underlying": underlying.upper(),
            # raw features
            "close": sf_row.get("close_1515"),
            "ma10": sf_row.get("ma10"), "ma20": sf_row.get("ma20"),
            "ma50": sf_row.get("ma50"), "ma90": sf_row.get("ma90"),
            "rsi14": sf_row.get("rsi14"), "atr14": sf_row.get("atr14"),
            "bb_upper": sf_row.get("bb_upper"), "bb_lower": sf_row.get("bb_lower"),
            "bb_width": sf_row.get("bb_width"),
            "ret_5d": sf_row.get("ret_5d"), "ret_10d": sf_row.get("ret_10d"),
            "ret_20d": sf_row.get("ret_20d"),
            "ret_60d": sf_row.get("ret_60d"),
            "volatility_10d": sf_row.get("volatility_10d"),
            "volatility_20d": sf_row.get("volatility_20d"),
            "volume_10d": sf_row.get("volume_10d"),
            "volume_20d": sf_row.get("volume_20d"),
            "regime": regime,
            "regime_source": "regime_experiment_current" if signal_date_str in regime_overrides else "SignalFeatureDaily",
            "db_regime": db_regime,
        }
        hindsight = compute_hindsight_regime(ohlcv_df, idx)
        row.update(hindsight)
        row["regime_match"] = (
            regime == hindsight["hindsight_regime"]
            if hindsight["hindsight_regime"] != "UNKNOWN"
            else None
        )
        # per-strategy raw signals (None = strategy not active for this regime)
        for name in strategy_names:
            row[name] = predictions.get(name)
        # full view fields
        # Omitted columns (structurally always 0/None for NIFTY):
        #   stock_regime            — identical to `regime`
        #   sector/benchmark scores — no sector/benchmark regime in pipeline
        #   relative_strength_score — sector/benchmark RS both None; ret_20d already a raw feature
        signal_date_obj = date.fromisoformat(signal_date_str)
        current_close_val = _close_by_date.get(signal_date_obj)
        next_date_val = _next_date.get(signal_date_obj)
        next_ohlcv = _ohlcv_by_date.get(next_date_val) if next_date_val else None
        next_close_val = _next_day_close.get(signal_date_obj)
        next_day_close_chg_pct = (
            round((next_close_val - current_close_val) / current_close_val * 100, 2)
            if current_close_val and next_close_val else None
        )

        row.update({
            "raw_signal": view.raw_signal,
            "direction": view.direction,
            "strength_score": round(view.strength_score, 2),
            "confidence": view.confidence,
            "option_bias": view.option_bias,
            "is_option_eligible": view.is_option_eligible,
            # primary_strategy / setup_type / expected_move_* omitted:
            # not meaningful for NIFTY RANGE signals (always generic estimates)
            "stock_technical_score": round(view.stock_technical_score, 2),
            "volume_confirmation_score": round(view.volume_confirmation_score, 2),
            "risk_quality_score": round(view.risk_quality_score, 2),
            "regime_quality_score": round(view.regime_quality_score, 2),
            "next_day_close_chg_pct": next_day_close_chg_pct,
        })
        output_rows.append(row)

        if research_output_path is not None:
            research_row = _build_research_row(
                base_row=row,
                sf_row=sf_row.to_dict(),
                signal_date=signal_date_obj,
                current_ohlcv=_ohlcv_by_date.get(signal_date_obj),
                next_date=next_date_val,
                next_ohlcv=next_ohlcv,
                research_predictions=research_predictions,
                strategy_names=strategy_names,
                strategy_signals_by_name=research_strategy_signals_by_name,
                view=view,
            )
            research_rows.append(research_row)

    out_df = pd.DataFrame(output_rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(output_path, index=False)
    print(f"Wrote {len(out_df)} rows to {output_path}")

    summary_path = output_path.with_name(output_path.stem + "_summary.txt")
    _generate_prediction_summary(out_df, summary_path)

    research_path: str | None = None
    if research_output_path is not None:
        research_df = pd.DataFrame(research_rows)
        research_output_path.parent.mkdir(parents=True, exist_ok=True)
        research_df.to_csv(research_output_path, index=False)
        research_path = str(research_output_path)
        print(f"Wrote {len(research_df)} research rows to {research_output_path}")

    return {"rows": len(out_df), "path": str(output_path), "research_path": research_path}


def _build_research_row(
    base_row: dict[str, Any],
    sf_row: dict[str, Any],
    signal_date: date,
    current_ohlcv: dict[str, Any] | None,
    next_date: date | None,
    next_ohlcv: dict[str, Any] | None,
    research_predictions: dict[str, str],
    strategy_names: list[str],
    strategy_signals_by_name: dict[str, StrategySignal],
    view: Any,
) -> dict[str, Any]:
    current_close = _f((current_ohlcv or {}).get("close_price")) or _f(sf_row.get("close_1515"))
    next_open = _f((next_ohlcv or {}).get("open_price"))
    next_high = _f((next_ohlcv or {}).get("high_price"))
    next_low = _f((next_ohlcv or {}).get("low_price"))
    next_close = _f((next_ohlcv or {}).get("close_price"))
    next_return_pct = (
        round((next_close - current_close) / current_close * 100, 4)
        if current_close and next_close is not None else None
    )

    research: dict[str, Any] = {
        "trade_date": signal_date.isoformat(),
        "next_trade_date": next_date.isoformat() if next_date else None,
        "open_915": sf_row.get("open_915"),
        "high_day": sf_row.get("high_day"),
        "low_day": sf_row.get("low_day"),
        "close_1515": sf_row.get("close_1515"),
        "volume_day": sf_row.get("volume_day"),
        "ma10": sf_row.get("ma10"),
        "ma20": sf_row.get("ma20"),
        "ma50": sf_row.get("ma50"),
        "ma90": sf_row.get("ma90"),
        "ma5d_slope": sf_row.get("ma5d_slope"),
        "ma10d_slope": sf_row.get("ma10d_slope"),
        "ma20_slope": sf_row.get("ma20_slope"),
        "ma50_slope": sf_row.get("ma50_slope"),
        "rsi14": sf_row.get("rsi14"),
        "atr14": sf_row.get("atr14"),
        "bb_upper": sf_row.get("bb_upper"),
        "bb_middle": sf_row.get("bb_middle"),
        "bb_lower": sf_row.get("bb_lower"),
        "bb_width": sf_row.get("bb_width"),
        "ret_5d": sf_row.get("ret_5d"),
        "ret_10d": sf_row.get("ret_10d"),
        "ret_20d": sf_row.get("ret_20d"),
        "ret_60d": sf_row.get("ret_60d"),
        "volatility_10d": sf_row.get("volatility_10d"),
        "volatility_20d": sf_row.get("volatility_20d"),
        "volume_10d": sf_row.get("volume_10d"),
        "volume_20d": sf_row.get("volume_20d"),
        "trend_efficiency_5d": sf_row.get("trend_efficiency_5d"),
        "trend_efficiency_10d": sf_row.get("trend_efficiency_10d"),
        "trend_efficiency_20d": sf_row.get("trend_efficiency_20d"),
        "recent_high_5d": sf_row.get("recent_high_5d"),
        "recent_low_5d": sf_row.get("recent_low_5d"),
        "recent_high_10d": sf_row.get("recent_high_10d"),
        "recent_low_10d": sf_row.get("recent_low_10d"),
        "recent_high_20d": sf_row.get("recent_high_20d"),
        "recent_low_20d": sf_row.get("recent_low_20d"),
        "range_position_5d": sf_row.get("range_position_5d"),
        "range_position_10d": sf_row.get("range_position_10d"),
        "selected_regime": base_row.get("regime"),
        "hindsight_regime": base_row.get("hindsight_regime"),
    }

    for name in strategy_names:
        if name == "unknown":
            continue
        research[f"strategy_{name}_signal"] = research_predictions.get(name, "NO_POSITION")

    research.update({
        "final_raw_signal": view.raw_signal,
        "next_open": next_open,
        "next_high": next_high,
        "next_low": next_low,
        "next_close": next_close,
        "next_return_pct": next_return_pct,
        "actual_trade_label": _actual_trade_label(next_return_pct),
    })
    return research


def _actual_trade_label(next_return_pct: float | None) -> str | None:
    if next_return_pct is None:
        return None
    if next_return_pct >= _SIGNIFICANCE_PCT:
        return "CALL"
    if next_return_pct <= -_SIGNIFICANCE_PCT:
        return "PUT"
    return "NO_POSITION"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate NIFTY prediction CSV from SignalFeatureDaily.")
    parser.add_argument("--underlying", default="NIFTY", help="Underlying symbol. Default: NIFTY")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD. Default: 1 year before end.")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD. Default: today.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV path. Default: {DEFAULT_OUTPUT}")
    parser.add_argument(
        "--research-output",
        default=str(DEFAULT_RESEARCH_OUTPUT),
        help=f"Flat research CSV path. Default: {DEFAULT_RESEARCH_OUTPUT}. Use empty string to skip.",
    )
    parser.add_argument(
        "--regime-comparison",
        default=str(DEFAULT_REGIME_COMPARISON),
        help=(
            "Regime experiment comparison CSV. Uses current_regime when present; "
            f"default: {DEFAULT_REGIME_COMPARISON}"
        ),
    )
    args = parser.parse_args()

    result = generate_prediction_csv(
        underlying=args.underlying.upper(),
        start_date=date.fromisoformat(args.start) if args.start else None,
        end_date=date.fromisoformat(args.end) if args.end else None,
        output_path=Path(args.output),
        research_output_path=Path(args.research_output) if args.research_output else None,
        regime_comparison_path=Path(args.regime_comparison) if args.regime_comparison else None,
    )
    print(result)


if __name__ == "__main__":
    main()
