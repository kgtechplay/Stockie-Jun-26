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
        volume_ratio=1.5, trend_efficiency=0.55, range_position=0.75,
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
        volume_ratio=1.5, trend_efficiency=0.55, range_position=0.25,
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

DEFAULT_OUTPUT = Path("output") / "backtest" / "NIFTY_prediction.csv"
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
        volume_avg_20d=None,
        volume_ratio=_f(row.get("volume_ratio")),
        trend_efficiency=_f(row.get("trend_efficiency_60d")),
        range_position=_f(row.get("range_position_20d")),
        relative_strength_vs_sector=_f(row.get("relative_strength_vs_sector")),
        relative_strength_vs_benchmark=None,
    )


def generate_prediction_csv(
    underlying: str = "NIFTY",
    start_date: date | None = None,
    end_date: date | None = None,
    output_path: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = date(end_date.year - 1, end_date.month, end_date.day)

    strategies = load_underlying_prediction_strategies()
    strategy_names = sorted(strategies.keys())
    lookback_start = start_date - timedelta(days=_EXTRA_OHLCV_DAYS)

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

    _close_by_date: dict[date, float] = {
        _as_date(r["trade_date"]): float(r["close_price"])
        for _, r in _sorted_ohlcv.iterrows()
    }
    _sorted_dates = sorted(_close_by_date.keys())
    _next_day_close: dict[date, float] = {
        _sorted_dates[i]: _close_by_date[_sorted_dates[i + 1]]
        for i in range(len(_sorted_dates) - 1)
    }

    output_rows: list[dict[str, Any]] = []

    for _, sf_row in sf_df.iterrows():
        signal_ts = pd.to_datetime(sf_row["signal_date"])
        signal_date_str = str(signal_ts)[:10]
        regime = str(sf_row.get("regime") or "UNKNOWN").upper()

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

        # Build feature snapshot from pre-computed SignalFeatureDaily values
        features = _sf_row_to_feature_snapshot(sf_row.to_dict(), underlying)

        # Build full view (regime → signals → scoring → view)
        regime_snap = build_regime_snapshot(regime)
        strategy_signals = build_strategy_signals(predictions, features, regime_snap)
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
            "ret_5d": sf_row.get("ret_5d"), "ret_20d": sf_row.get("ret_20d"),
            "ret_60d": sf_row.get("ret_60d"),
            "volatility_20d": sf_row.get("volatility_20d"),
            "volume_ratio": sf_row.get("volume_ratio"),
            "regime": regime,
        }
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

    out_df = pd.DataFrame(output_rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(output_path, index=False)
    print(f"Wrote {len(out_df)} rows to {output_path}")

    summary_path = output_path.with_name(output_path.stem + "_summary.txt")
    _generate_prediction_summary(out_df, summary_path)

    return {"rows": len(out_df), "path": str(output_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate NIFTY prediction CSV from SignalFeatureDaily.")
    parser.add_argument("--underlying", default="NIFTY", help="Underlying symbol. Default: NIFTY")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD. Default: 1 year before end.")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD. Default: today.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV path. Default: {DEFAULT_OUTPUT}")
    args = parser.parse_args()

    result = generate_prediction_csv(
        underlying=args.underlying.upper(),
        start_date=date.fromisoformat(args.start) if args.start else None,
        end_date=date.fromisoformat(args.end) if args.end else None,
        output_path=Path(args.output),
    )
    print(result)


if __name__ == "__main__":
    main()
