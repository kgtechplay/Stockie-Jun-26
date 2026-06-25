"""
NIFTY production prediction pipeline — regime-aware precision cascade.

This is the PRODUCTION counterpart to the research harness in
backtest/research/build_experiment.py. The cascade engine (dataset assembly,
labelling, precision-floor voting, scoring, walk-forward) is shared; production
registers ONLY the promoted strategy roster and captures the single final
prediction per day.

Pipeline:
  1. build_base() reads the shared feature store (output/feature_store/
     NIFTY_base.csv), appends any newly-resolved day from the DB, and labels every
     resolved day (actual_trade_label).
  2. Any current day whose next-day outcome does not exist yet is also loaded so
     the cascade can still PREDICT it (it just cannot be graded — handy for the
     daily pre-market run).
  3. The regime-aware precision cascade (eligibility fit on resolved history only)
     produces one final_prediction per day.
  4. output/backtest/NIFTY/production/NIFTY_prediction.csv keeps the historical
     prices, volume, India VIX, regime, the final_prediction and the
     actual_trade_label (the technical feature columns are dropped — they live in
     the shared feature store).
  5. NIFTY_prediction_summary.txt captures precision / recall / accuracy of the
     final prediction (in-sample headline + an honest walk-forward number).

Run directly (the daily job, scripts/daily_NIFTY/daily_nifty_prediction.py, is the
production entrypoint that also persists to the DB):
    python -m src.technical_analysis.cascade.pipeline
    python src/technical_analysis/cascade/pipeline.py --output <path>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

load_dotenv(_repo_root / ".env")

# ── shared cascade engine (single source of truth, shared with the experiment) ─
# Production registers ONLY the promoted strategy roster; the research harness
# (backtest/research/build_experiment.py) registers the full roster on the same
# engine, so the two pipelines share the engine yet diverge on strategies.
from src.technical_analysis.cascade.constants import (
    _VIX_COLS, _BASE_STR_COLS, WF_WINDOW,
)
from src.technical_analysis.cascade.dataset import (
    build_base, regime_frame, classify_regime, load_vix,
)
from src.technical_analysis.cascade.engine import (
    _fmt, score_final, _confusion_lines,
    gather_regime_signals, build_regime_cascade, walk_forward_regime,
)
from src.technical_analysis.cascade.option_signal_mapper import enrich_option_signal_columns
from src.technical_analysis.cascade.strategies import PROMOTED_REGIME_FAMILIES

# ── pipeline-only imports ─────────────────────────────────────────────────────
from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client


DEFAULT_OUTPUT = Path("output") / "backtest" / "NIFTY" / "production" / "NIFTY_prediction.csv"

# Columns kept in the production CSV: the raw market data (prices, volume, India
# VIX), the volatility regime, the cascade's final_prediction and the realised
# actual_trade_label. Every technical feature column from the feature store is
# dropped — those belong to research, not to the production prediction record.
_PRODUCTION_COLS = [
    "trade_date", "next_trade_date",
    "open_915", "high_day", "low_day", "close_1515",
    "volume_day",
    "vix_close", "vix_chg_1d", "vix_chg_pct",
    "regime",
    "next_open", "next_high", "next_low", "next_close", "next_return_pct",
    "final_prediction",
    "direction",
    "volatility_regime", "stock_regime",
    "primary_strategy", "strategy_precision", "signal_style",
    "strength_score", "strength_label", "confidence_level",
    "expected_move_pct", "is_option_eligible", "option_bias", "conflict_flag",
    "actual_trade_label",
]


def _load_unresolved_rows(resolved: pd.DataFrame) -> pd.DataFrame:
    """Pull SignalFeatureDaily NIFTY rows NEWER than the last resolved date — the
    current day(s) whose next-day outcome does not exist yet — shaped into the base
    schema so the cascade can still PREDICT them. next_* and actual_trade_label stay
    blank (pending). Returns an empty frame on no-new-rows or DB failure."""
    max_date = str(resolved["trade_date"].max())
    try:
        settings = get_settings()
        db = get_database_client(settings)
        db.connect()
        try:
            with db.conn.cursor() as cur:
                cur.execute(
                    'SELECT * FROM "SignalFeatureDaily" '
                    "WHERE symbol = %s AND signal_date > %s ORDER BY signal_date",
                    ("NIFTY", max_date),
                )
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 - never let a DB hiccup break the run
        print(f"[WARN] unresolved-day load skipped: {exc}")
        return resolved.iloc[0:0].copy()

    sf = pd.DataFrame(rows, columns=cols)
    if sf.empty:
        return resolved.iloc[0:0].copy()

    sf = sf.rename(columns={"signal_date": "trade_date"})
    sf["trade_date"] = pd.to_datetime(sf["trade_date"]).dt.strftime("%Y-%m-%d")
    sf = sf.sort_values("trade_date").reset_index(drop=True)
    sf = sf[sf["trade_date"] > max_date]
    if sf.empty:
        return resolved.iloc[0:0].copy()

    # support/resistance levels + distances from the 10-day extremes (mirror
    # cascade.dataset); next-day outcome columns are unknown for these rows.
    sf["support_10d"] = sf["recent_low_10d"]
    sf["resistance_10d"] = sf["recent_high_10d"]
    sf["support_distance_10d"] = (sf["close_1515"] - sf["support_10d"]) / sf["close_1515"]
    sf["resistance_distance_10d"] = (sf["resistance_10d"] - sf["close_1515"]) / sf["close_1515"]

    # Shape to the base schema (minus the VIX columns, which we merge fresh).
    keep = [c for c in resolved.columns if c not in _VIX_COLS]
    out = sf.reindex(columns=keep)
    for col in out.columns:
        if col not in _BASE_STR_COLS:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.merge(load_vix(), on="trade_date", how="left")
    out["regime"] = classify_regime(out)
    out["actual_trade_label"] = np.nan
    out = out.reindex(columns=resolved.columns)
    return out.reset_index(drop=True)


def _final_block(title: str, m: dict) -> list[str]:
    return [
        title,
        "-" * 64,
        f"  fires            : {m['n_call'] + m['n_put']} "
        f"(CALL {m['n_call']}, PUT {m['n_put']}, FLAT {m['n_flat']}) of {m['n']} days",
        f"  precision        : {_fmt(m['dir_precision'])}   "
        f"(naive always-PUT {_fmt(m['put_base'])}, lift {_fmt(m['lift'])}x)",
        f"  recall           : {_fmt(m['dir_recall'])}   "
        f"(correct fires / {m['n_move']} actual-move days)",
        f"  wrong-way rate   : {_fmt(m['wrong_way_rate'])}   "
        f"(took a side, opposite move happened)",
        f"  overall accuracy : {_fmt(m['overall_accuracy'])}   "
        f"(correct fires + correct NO_POSITION / all days)",
        "",
    ]


def _write_prediction_summary(
    df_res: pd.DataFrame,
    pred_res: pd.Series,
    pending: pd.DataFrame,
    summary_path: Path,
) -> None:
    """Write precision / recall / accuracy of the final prediction. Graded on the
    resolved history (in-sample headline + walk-forward out-of-sample)."""
    m_in = score_final(df_res, pred_res)
    lines = [
        "=" * 64,
        "NIFTY final prediction — summary (regime-aware precision cascade)",
        "=" * 64,
        f"graded rows: {m_in['n']}   "
        f"date range: {df_res['trade_date'].min()} .. {df_res['trade_date'].max()}",
        "",
        "The final prediction is the cascade output: each day is routed",
        "to its volatility regime (calm/stress); among that regime's strategies the",
        "highest-precision eligible CALL/PUT vote wins, else NO_POSITION. The cascade",
        "engine is shared with the research harness; production registers only the",
        "promoted strategy roster (src/technical_analysis/cascade/strategies.py).",
        "",
    ]

    if not pending.empty:
        lines.append("Pending prediction(s) — predicted but not yet gradeable "
                     "(no next-day outcome):")
        for _, r in pending.iterrows():
            lines.append(f"    {r['trade_date']}  regime={r['regime']:<6}  "
                         f"prediction={r['final_prediction']}")
        lines.append("")

    lines += _final_block("In-sample (eligibility fit + graded on the same history; optimistic)",
                          m_in)
    lines.append("  Confusion matrix:")
    lines += _confusion_lines(df_res, pred_res)
    lines.append("")

    # Walk-forward (honest out-of-sample) — eligibility fit only on trailing days.
    rs_res = gather_regime_signals(df_res, PROMOTED_REGIME_FAMILIES)
    wf_pred = walk_forward_regime(df_res, rs_res)
    wf_eval = df_res.iloc[WF_WINDOW:]
    if len(wf_eval):
        wf = score_final(wf_eval, wf_pred.loc[wf_eval.index])
        lines += _final_block(
            f"Walk-forward (rolling {WF_WINDOW}-day eligibility, out-of-sample — "
            "the honest number)",
            wf,
        )
        lines.append("  Walk-forward confusion matrix:")
        lines += _confusion_lines(wf_eval, wf_pred.loc[wf_eval.index])
        lines.append("")

    lines.append("Caveat: in-sample eligibility is fit on the same history it grades, so")
    lines.append("the in-sample headline is optimistic; the walk-forward number is the")
    lines.append("honest read. Research, not a live trading signal.")
    lines.append("")

    text = "\n".join(lines)
    print(text)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(text, encoding="utf-8")
    print(f"Summary written to {summary_path}")


def generate_prediction_csv(
    underlying: str = "NIFTY",
    output_path: Path = DEFAULT_OUTPUT,
    summary_path: Path | None = None,
    **_legacy_kwargs: Any,
) -> dict[str, Any]:
    """Run the regime-aware precision cascade over the full NIFTY history (plus any
    current unresolved day) and write the production prediction CSV + summary.

    The final prediction uses the shared cascade engine
    (src/technical_analysis/cascade) with the PROMOTED strategy roster — the same
    engine the research harness drives with the full roster. Extra legacy keyword
    arguments are accepted and ignored for backward compatibility with older
    callers."""
    if underlying.upper() != "NIFTY":
        raise ValueError("The cascade production pipeline currently supports NIFTY only.")

    output_path = Path(output_path)
    if summary_path is None:
        summary_path = output_path.with_name(output_path.stem + "_summary.txt")
    else:
        summary_path = Path(summary_path)

    # 1) resolved history: prices/volume/VIX/base features + regime + label.
    resolved = build_base().reset_index(drop=True)

    # 2) current unresolved day(s) — predicted but not yet gradeable.
    unresolved = _load_unresolved_rows(resolved)
    if not unresolved.empty:
        print(f"  loaded {len(unresolved)} unresolved day(s) to predict "
              f"(no outcome yet): {', '.join(unresolved['trade_date'])}")

    full = (pd.concat([resolved, unresolved], ignore_index=True)
            if not unresolved.empty else resolved.copy())
    n_res = len(resolved)
    resolved_full = full.iloc[:n_res]

    # 3) cascade: eligibility fit on resolved rows only; predict every row.
    regime_signals = gather_regime_signals(full, PROMOTED_REGIME_FAMILIES)
    elig_frames = {r: regime_frame(resolved_full, r) for r in PROMOTED_REGIME_FAMILIES}
    final_pos, elig = build_regime_cascade(full, regime_signals, elig_frames)
    full = full.copy()
    full["final_prediction"] = final_pos
    full = enrich_option_signal_columns(full, final_pos, regime_signals, elig)

    # 4) production CSV — market data + regime + final prediction + actual label.
    out_df = full.reindex(columns=_PRODUCTION_COLS).copy()
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        out_df.to_csv(output_path, index=False)
        print(f"Wrote {len(out_df)} prediction rows to {output_path}")
    except Exception as exc:  # noqa: BLE001 - Render DB persistence must not depend on local files.
        print(f"[WARN] Prediction CSV write skipped: {type(exc).__name__}: {exc}")

    # 5) summary — precision / recall graded on the resolved history.
    pending = out_df.iloc[n_res:]
    try:
        _write_prediction_summary(resolved_full, final_pos.iloc[:n_res], pending, summary_path)
    except Exception as exc:  # noqa: BLE001 - summary output is local-dashboard convenience.
        print(f"[WARN] Prediction summary write skipped: {type(exc).__name__}: {exc}")

    return {
        "rows": len(out_df),
        "path": str(output_path),
        "summary_path": str(summary_path),
        "graded_rows": n_res,
        "pending_predicted": int(len(unresolved)),
        "frame": out_df,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the NIFTY production final-prediction CSV via the "
                    "regime-aware precision cascade.")
    parser.add_argument("--underlying", default="NIFTY", help="Underlying symbol. Default: NIFTY")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help=f"Output CSV path. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--summary", default=None,
                        help="Summary txt path. Default: <output>_summary.txt")
    args = parser.parse_args()

    result = generate_prediction_csv(
        underlying=args.underlying.upper(),
        output_path=Path(args.output),
        summary_path=Path(args.summary) if args.summary else None,
    )
    print(result)


if __name__ == "__main__":
    main()
