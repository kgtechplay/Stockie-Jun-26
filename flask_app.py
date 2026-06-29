from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, redirect, render_template_string, request, url_for

from src.common.config import get_settings

load_dotenv(Path(".env"))

NIFTY_SYMBOL = "NIFTY"
MODEL_VERSION = "cascade_v1"
BASE_OUTPUT_DIR = Path("output") / "backtest" / NIFTY_SYMBOL
RESEARCH_OUTPUT_DIR = BASE_OUTPUT_DIR / "vectorbt_research"
PRODUCTION_OUTPUT_DIR = BASE_OUTPUT_DIR / "production"
TRADES_OUTPUT_DIR = BASE_OUTPUT_DIR / "vectorbt"

app = Flask(__name__)


@dataclass(frozen=True)
class PageTable:
    title: str
    path: Path | None
    html: str
    rows: int
    empty_message: str = "No rows available yet."


@app.get("/")
def index():
    return redirect(url_for("research"))


@app.get("/health")
def health():
    return {"status": "ok", "app": "stockie26-flask-ui"}


@app.route("/research", methods=["GET", "POST"])
def research():
    message, error = "", ""
    if request.method == "POST":
        try:
            from backtest.vectorbt_research.strategy_grid import DEFAULT_VARIANTS, run_strategy_grid

            variant_filter = request.form.get("variants", "").strip()
            variants = None
            if variant_filter:
                filters = [item.strip().lower() for item in variant_filter.split(",") if item.strip()]
                variants = [v for v in DEFAULT_VARIANTS if any(f in v.name.lower() for f in filters)]
                if not variants:
                    raise ValueError(f"No strategy variants matched: {variant_filter}")

            paths = run_strategy_grid(
                start=parse_date(request.form.get("start")),
                end=parse_date(request.form.get("end")),
                target_pct=parse_float(request.form.get("target_pct"), 0.03),
                stop_loss_pct=parse_optional_float(request.form.get("stop_loss_pct")),
                output_dir=RESEARCH_OUTPUT_DIR,
                variants=variants,
            )
            message = "Research strategy grid completed. Outputs refreshed: " + ", ".join(paths.keys())
        except Exception as exc:
            error = f"Research run failed: {exc}"

    leaderboard = csv_table(
        "Strategy Leaderboard",
        RESEARCH_OUTPUT_DIR / "strategy_grid_leaderboard.csv",
        limit=200,
    )
    definitions = csv_table(
        "Strategy Definitions",
        RESEARCH_OUTPUT_DIR / "strategy_grid_definitions.csv",
        limit=200,
    )
    trades = csv_table(
        "Research Trades",
        RESEARCH_OUTPUT_DIR / "strategy_grid_trades.csv",
        limit=200,
    )
    summary = read_text(RESEARCH_OUTPUT_DIR / "strategy_grid_summary.txt")

    return render_dashboard(
        active="research",
        message=message,
        error=error,
        title="Research",
        subtitle="Run VectorBT across research strategy variants and compare PnL, win rate, and generated option trades.",
        controls=RESEARCH_CONTROLS,
        tables=[leaderboard, definitions, trades],
        summary=summary,
        summary_title="Research Summary",
    )


@app.route("/production", methods=["GET", "POST"])
def production():
    message, error = "", ""
    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            if action == "prediction":
                from backtest.production.pipeline_backtest_prediction import (
                    HistoricalUnderlyingBacktestRequest,
                    run_historical_underlying_backtest,
                )

                result = run_historical_underlying_backtest(
                    HistoricalUnderlyingBacktestRequest(
                        underlying=NIFTY_SYMBOL,
                        prediction_file=request.form.get("prediction_file") or None,
                        prediction_dir=PRODUCTION_OUTPUT_DIR,
                    )
                )
                message = f"Production prediction refreshed: {result}"
            elif action == "option_selection":
                from backtest.production.pipeline_backtest_optionselection import generate_option_selection_csv

                result = generate_option_selection_csv(
                    input_path=PRODUCTION_OUTPUT_DIR / "NIFTY_prediction.csv",
                    output_path=PRODUCTION_OUTPUT_DIR / "NIFTY_optionSelection.csv",
                    underlying=NIFTY_SYMBOL,
                    prediction_source=request.form.get("prediction_source", "csv"),
                )
                message = f"Production option selection refreshed: {result}"
            elif action == "pnl":
                result = run_production_pnl(
                    start=parse_date(request.form.get("start")),
                    end=parse_date(request.form.get("end")),
                )
                message = f"Production PnL refreshed: {result}"
            else:
                raise ValueError("Unknown production action.")
        except Exception as exc:
            error = f"Production run failed: {exc}"

    db_rows, db_error = load_june_signal_rows()
    if db_error and not error:
        error = db_error

    db_table = PageTable(
        title="Daily Prediction And Option Selection From DB",
        path=None,
        html=df_to_html(pd.DataFrame(db_rows)),
        rows=len(db_rows),
        empty_message="No DB rows available. Run the production pipeline or check Supabase settings.",
    )
    prediction = csv_table("Prediction CSV", PRODUCTION_OUTPUT_DIR / "NIFTY_prediction.csv", limit=150)
    signals = csv_table("Production Signals", PRODUCTION_OUTPUT_DIR / "production_signals.csv", limit=150)
    option_selection = csv_table("Option Selection CSV", PRODUCTION_OUTPUT_DIR / "NIFTY_optionSelection.csv", limit=150)
    pnl = csv_table("Production PnL Trades", PRODUCTION_OUTPUT_DIR / "production_pnl_trades.csv", limit=150)
    summary = read_text(PRODUCTION_OUTPUT_DIR / "production_pnl_summary.txt")

    return render_dashboard(
        active="production",
        message=message,
        error=error,
        title="Stockie Prediction",
        subtitle="Run production prediction, option selection, and PnL backtests; review predicted vs actual labels and option movement.",
        controls=PRODUCTION_CONTROLS,
        tables=[db_table, prediction, signals, option_selection, pnl],
        summary=summary,
        summary_title="Production PnL Summary",
    )


@app.route("/trades", methods=["GET", "POST"])
def trades():
    message, error = "", ""
    trade_date = parse_date(request.values.get("trade_date")) or date.today()
    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            if action == "prepare":
                from src.execution.paper import prepare_paper_signals

                inserted = prepare_paper_signals(trade_date=trade_date, symbol=NIFTY_SYMBOL, model_version=MODEL_VERSION)
                message = f"Prepared paper execution signals for {trade_date}: inserted {inserted} new row(s)."
            elif action == "vectorbt_replay":
                from backtest.vectorbt_trades.schemas import StockieVectorBTRequest
                from backtest.vectorbt_trades.service import run_stockie_vectorbt_backtest

                result = run_stockie_vectorbt_backtest(
                    StockieVectorBTRequest(
                        underlying=NIFTY_SYMBOL,
                        model_version=MODEL_VERSION,
                        mode="paper",
                        start_date=parse_date(request.form.get("start")),
                        end_date=parse_date(request.form.get("end")),
                        output_dir=TRADES_OUTPUT_DIR,
                    )
                )
                message = (
                    f"Paper trade VectorBT replay completed with "
                    f"{len(result.trade_plans)} loaded trade(s), {len(result.trades)} closed replay trade(s)."
                )
            else:
                raise ValueError("Unknown trade action.")
        except Exception as exc:
            error = f"Trade action failed: {exc}"

    paper_rows, paper_error = load_paper_trade_rows(trade_date)
    if paper_error and not error:
        error = paper_error

    paper = PageTable(
        title=f"Paper Trades For {trade_date}",
        path=None,
        html=df_to_html(pd.DataFrame(paper_rows)),
        rows=len(paper_rows),
        empty_message="No paper trade rows found for this date.",
    )
    executed = csv_table("Executed Paper Trades", TRADES_OUTPUT_DIR / "paper_executed_trades.csv", limit=200)
    closed = csv_table("Closed Paper Trades", TRADES_OUTPUT_DIR / "paper_closed_trades.csv", limit=200)
    open_trades = csv_table("Open Paper Trades", TRADES_OUTPUT_DIR / "paper_open_trades.csv", limit=200)
    vectorbt_trades = csv_table("VectorBT Trade Replay", TRADES_OUTPUT_DIR / "vectorbt_trades.csv", limit=200)
    summary = read_text(TRADES_OUTPUT_DIR / "vectorbt_summary.txt")

    return render_dashboard(
        active="trades",
        message=message,
        error=error,
        title="Trades",
        subtitle="Prepare paper signals, review live/paper fills, and replay executed trades through VectorBT.",
        controls=TRADES_CONTROLS,
        tables=[paper, executed, closed, open_trades, vectorbt_trades],
        summary=summary,
        summary_title="Paper Trade Replay Summary",
        trade_date=trade_date.isoformat(),
    )


def render_dashboard(
    active: str,
    title: str,
    subtitle: str,
    controls: str,
    tables: list[PageTable],
    summary: str,
    summary_title: str,
    message: str = "",
    error: str = "",
    trade_date: str = "",
) -> str:
    controls = controls.replace("{{ trade_date }}", trade_date)
    return render_template_string(
        PAGE_TEMPLATE,
        active=active,
        title=title,
        subtitle=subtitle,
        controls=controls,
        tables=tables,
        summary=summary,
        summary_title=summary_title,
        message=message,
        error=error,
        today=date.today().isoformat(),
        trade_date=trade_date,
    )


def csv_table(title: str, path: Path, limit: int = 100) -> PageTable:
    if not path.exists():
        return PageTable(title=title, path=path, html="", rows=0)
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        return PageTable(title=title, path=path, html="", rows=0, empty_message=f"Could not read CSV: {exc}")
    return PageTable(title=title, path=path, html=df_to_html(df.head(limit)), rows=len(df))


def df_to_html(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    display = df.copy()
    for col in display.columns:
        if "date" in col.lower() or "time" in col.lower():
            display[col] = display[col].astype(str)
    return display.to_html(index=False, classes="data-table", border=0, escape=True)


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(errors="replace")


def load_paper_trade_rows(trade_date: date) -> tuple[list[dict[str, Any]], str]:
    settings = get_settings()
    if not settings.supabase_conn_str:
        return [], "SUPABASE_CONN_STR is missing. Paper trades can still be viewed from existing CSV outputs."
    try:
        from src.data_manager.db.client_factory import get_database_client

        db = get_database_client(settings)
        db.connect()
        try:
            return db.list_paper_trade_results(
                trade_date=trade_date,
                statuses=("PLANNED", "OPEN", "CLOSED", "FAILED"),
                symbol=NIFTY_SYMBOL,
                model_version=MODEL_VERSION,
            ), ""
        finally:
            db.close()
    except Exception as exc:
        return [], f"Could not load paper trades from Supabase: {exc}"


def load_june_signal_rows() -> tuple[list[dict[str, Any]], str]:
    settings = get_settings()
    if not settings.supabase_conn_str:
        return [], "SUPABASE_CONN_STR is missing. Production DB rows can still be viewed from existing CSV outputs."
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        with psycopg2.connect(settings.supabase_conn_str) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(JUNE_SIGNAL_SQL, {"symbol": NIFTY_SYMBOL, "model_version": MODEL_VERSION})
                rows = cur.fetchall()
    except Exception as exc:
        return [], f"Could not load June signal rows from Supabase: {exc}"

    return [format_signal_row(dict(row)) for row in rows], ""


def run_production_pnl(start: date | None = None, end: date | None = None) -> dict[str, Any]:
    from backtest.production import pipeline_backtest_pnl as pnl

    signals = pnl._load_production_signals(NIFTY_SYMBOL, MODEL_VERSION, start, end)
    if signals.empty:
        return {"signals": 0, "trades": 0, "summary": "No production signals found."}

    snapshots = pnl._load_snapshot_prices(signals)
    snap_ids = set(snapshots["trade_id"]) if not snapshots.empty else set()
    no_snapshot = signals[~signals["trade_id"].isin(snap_ids)].copy()
    trades = pnl._simulate_exits(signals, snapshots)
    metrics = pnl._compute_metrics(trades)
    paths = pnl._write_outputs(
        PRODUCTION_OUTPUT_DIR,
        signals,
        no_snapshot,
        trades,
        metrics,
        NIFTY_SYMBOL,
        MODEL_VERSION,
        start,
        end,
    )
    return {
        "signals": len(signals),
        "signals_without_snapshots": len(no_snapshot),
        "trades": len(trades),
        "summary": str(paths["summary"]),
    }


JUNE_SIGNAL_SQL = """
WITH june_predictions AS (
    SELECT *
    FROM "NiftyPrediction"
    WHERE symbol = %(symbol)s
      AND model_version = %(model_version)s
      AND EXTRACT(MONTH FROM trade_date) = 6
), option_rows AS (
    SELECT *
    FROM "NiftyOptionSelection"
    WHERE symbol = %(symbol)s
      AND model_version = %(model_version)s
      AND EXTRACT(MONTH FROM trade_date) = 6
), selected AS (
    SELECT
        p.trade_date,
        p.next_trade_date,
        p.final_prediction,
        p.direction,
        p.actual_trade_label,
        p.regime,
        p.primary_strategy AS prediction_strategy,
        p.strength_score,
        p.confidence_level,
        o.selected_strategy,
        o.primary_buy_symbol,
        o.primary_buy_token,
        o.primary_buy_strike,
        o.primary_buy_expiry,
        o.primary_buy_option_type,
        o.primary_buy_entry_price,
        o.target_1_price,
        o.target_2_price,
        o.stop_loss_price,
        o.no_trade_reason
    FROM june_predictions p
    LEFT JOIN option_rows o
      ON o.symbol = p.symbol
     AND o.trade_date = p.trade_date
     AND o.model_version = p.model_version
)
SELECT
    s.*,
    stats.first_snapshot_time,
    stats.last_snapshot_time,
    stats.max_option_price,
    stats.min_option_price,
    stats.latest_option_price,
    stats.snapshot_count,
    CASE
        WHEN s.stop_loss_price IS NOT NULL AND stats.min_option_price <= s.stop_loss_price THEN s.stop_loss_price
        WHEN s.target_2_price IS NOT NULL AND stats.max_option_price >= s.target_2_price THEN s.target_2_price
        WHEN s.target_1_price IS NOT NULL AND stats.max_option_price >= s.target_1_price THEN s.target_1_price
        ELSE stats.latest_option_price
    END AS pnl_exit_price,
    CASE
        WHEN s.primary_buy_entry_price IS NULL OR stats.latest_option_price IS NULL THEN NULL
        ELSE ROUND(((
            CASE
                WHEN s.stop_loss_price IS NOT NULL AND stats.min_option_price <= s.stop_loss_price THEN s.stop_loss_price
                WHEN s.target_2_price IS NOT NULL AND stats.max_option_price >= s.target_2_price THEN s.target_2_price
                WHEN s.target_1_price IS NOT NULL AND stats.max_option_price >= s.target_1_price THEN s.target_1_price
                ELSE stats.latest_option_price
            END - s.primary_buy_entry_price
        ) / NULLIF(s.primary_buy_entry_price, 0) * 100)::numeric, 2)
    END AS latest_pnl_pct,
    CASE
        WHEN s.primary_buy_entry_price IS NULL OR stats.latest_option_price IS NULL THEN NULL
        ELSE ROUND((
            CASE
                WHEN s.stop_loss_price IS NOT NULL AND stats.min_option_price <= s.stop_loss_price THEN s.stop_loss_price
                WHEN s.target_2_price IS NOT NULL AND stats.max_option_price >= s.target_2_price THEN s.target_2_price
                WHEN s.target_1_price IS NOT NULL AND stats.max_option_price >= s.target_1_price THEN s.target_1_price
                ELSE stats.latest_option_price
            END - s.primary_buy_entry_price
        )::numeric, 2)
    END AS latest_pnl_points,
    CASE
        WHEN s.stop_loss_price IS NOT NULL AND stats.min_option_price <= s.stop_loss_price THEN 'STOP_LOSS_HIT'
        WHEN s.target_2_price IS NOT NULL AND stats.max_option_price >= s.target_2_price THEN 'TARGET_2_HIT'
        WHEN s.target_1_price IS NOT NULL AND stats.max_option_price >= s.target_1_price THEN 'TARGET_1_HIT'
        WHEN s.primary_buy_symbol IS NULL THEN COALESCE(s.no_trade_reason, 'NO_OPTION_SELECTED')
        WHEN COALESCE(stats.snapshot_count, 0) = 0 THEN 'NO_NEXT_DAY_SNAPSHOT'
        ELSE 'OPEN_OR_NOT_HIT'
    END AS pnl_status
FROM selected s
LEFT JOIN LATERAL (
    SELECT
        MIN(os.snapshot_time) AS first_snapshot_time,
        MAX(os.snapshot_time) AS last_snapshot_time,
        MAX(os.last_price) AS max_option_price,
        MIN(os.last_price) AS min_option_price,
        (ARRAY_AGG(os.last_price ORDER BY os.snapshot_time DESC))[1] AS latest_option_price,
        COUNT(*) AS snapshot_count
    FROM "OptionSnapshot" os
    JOIN "OptionInstrument" oi ON oi.id = os.option_instrument_id
    WHERE oi.instrument_token = s.primary_buy_token
      AND os.trade_date = s.next_trade_date
      AND os.last_price IS NOT NULL
) stats ON true
ORDER BY s.trade_date;
"""


def format_signal_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal_date": fmt_date(row.get("trade_date")),
        "trade_date": fmt_date(row.get("next_trade_date")),
        "predicted": row.get("direction") or row.get("final_prediction") or "",
        "actual_label": row.get("actual_trade_label") or "Pending",
        "regime": row.get("regime") or "",
        "prediction_strategy": row.get("prediction_strategy") or "",
        "strength": fmt_number(row.get("strength_score")),
        "confidence": fmt_pct(row.get("confidence_level")),
        "option_selection": row.get("selected_strategy") or row.get("no_trade_reason") or "No selection",
        "option_symbol": row.get("primary_buy_symbol") or "",
        "option_type": row.get("primary_buy_option_type") or "",
        "strike": fmt_number(row.get("primary_buy_strike")),
        "entry": fmt_money(row.get("primary_buy_entry_price")),
        "target_1": fmt_money(row.get("target_1_price")),
        "target_2": fmt_money(row.get("target_2_price")),
        "stop_loss": fmt_money(row.get("stop_loss_price")),
        "latest_option_price": fmt_money(row.get("latest_option_price")),
        "max_option_price": fmt_money(row.get("max_option_price")),
        "min_option_price": fmt_money(row.get("min_option_price")),
        "pnl_pct": fmt_pct(row.get("latest_pnl_pct")),
        "pnl_points": fmt_money(row.get("latest_pnl_points")),
        "pnl_status": row.get("pnl_status") or "",
        "snapshots": int(row.get("snapshot_count") or 0),
        "last_snapshot": fmt_datetime(row.get("last_snapshot_time")),
    }


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def parse_optional_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def fmt_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")


def fmt_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value or "")


def fmt_number(value: Any) -> str:
    number = as_float(value)
    if number is None:
        return ""
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}"


def fmt_money(value: Any) -> str:
    number = as_float(value)
    return "" if number is None else f"{number:.2f}"


def fmt_pct(value: Any) -> str:
    number = as_float(value)
    return "" if number is None else f"{number:.2f}%"


def as_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


RESEARCH_CONTROLS = """
<form method="post" class="control-grid">
  <label>Start date <input name="start" type="date"></label>
  <label>End date <input name="end" type="date"></label>
  <label>Target pct <input name="target_pct" value="0.03"></label>
  <label>Stop loss pct <input name="stop_loss_pct" placeholder="optional"></label>
  <label class="wide">Variant filter <input name="variants" placeholder="e.g. Momentum,Rsi,MAAlignment"></label>
  <button type="submit">Run Research Grid</button>
</form>
"""

PRODUCTION_CONTROLS = """
<form method="post" class="button-row">
  <label>Prediction file <input name="prediction_file" placeholder="NIFTY_prediction.csv"></label>
  <label>Start <input name="start" type="date"></label>
  <label>End <input name="end" type="date"></label>
  <button name="action" value="prediction">Run Prediction</button>
  <button name="action" value="option_selection">Run Option Selection</button>
  <button name="action" value="pnl">Run PnL Backtest</button>
  <label>Prediction source
    <select name="prediction_source">
      <option value="csv">CSV</option>
      <option value="db">DB</option>
    </select>
  </label>
</form>
"""

TRADES_CONTROLS = """
<form method="post" class="control-grid">
  <label>Paper trade date <input name="trade_date" type="date" value="{{ trade_date }}"></label>
  <label>Replay start <input name="start" type="date"></label>
  <label>Replay end <input name="end" type="date"></label>
  <button name="action" value="prepare">Prepare Paper Signals</button>
  <button name="action" value="vectorbt_replay">Replay Paper Trades</button>
</form>
"""

PAGE_TEMPLATE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stockie26 Dashboard</title>
  <style>
    :root {
      --bg: #f6f8fb;
      --panel: #ffffff;
      --text: #1f2937;
      --muted: #667085;
      --line: #d9e0ea;
      --accent: #186d5d;
      --accent-soft: #e7f2ef;
      --danger: #b42318;
      --shadow: 0 12px 30px rgba(20, 30, 45, 0.07);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
    }
    header {
      padding: 20px 30px 0;
      background: #fff;
      border-bottom: 1px solid var(--line);
    }
    .topbar { display: flex; justify-content: space-between; gap: 18px; align-items: end; }
    h1 { margin: 0; font-size: 28px; letter-spacing: 0; }
    .subtitle { margin: 6px 0 0; color: var(--muted); font-size: 14px; }
    nav { display: flex; gap: 8px; margin-top: 18px; }
    nav a {
      padding: 12px 16px;
      color: #475467;
      text-decoration: none;
      border: 1px solid transparent;
      border-bottom: 0;
      border-radius: 8px 8px 0 0;
      font-weight: 700;
      font-size: 14px;
    }
    nav a.active { background: var(--bg); color: var(--accent); border-color: var(--line); }
    main { padding: 22px 30px 34px; }
    .surface {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .hero {
      display: flex;
      justify-content: space-between;
      gap: 22px;
      align-items: start;
      padding: 20px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
    }
    h2 { margin: 0; font-size: 22px; letter-spacing: 0; }
    .hero p { margin: 6px 0 0; color: var(--muted); max-width: 760px; }
    .controls {
      padding: 16px 20px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }
    .control-grid, .button-row {
      display: flex;
      align-items: end;
      gap: 12px;
      flex-wrap: wrap;
    }
    label {
      display: grid;
      gap: 6px;
      color: #344054;
      font-size: 13px;
      font-weight: 700;
      min-width: 145px;
    }
    label.wide { min-width: 280px; flex: 1; }
    input, select {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 10px 11px;
      font: inherit;
      background: #fff;
      color: var(--text);
    }
    button {
      border: 0;
      border-radius: 7px;
      padding: 11px 14px;
      background: var(--accent);
      color: #fff;
      font-weight: 800;
      cursor: pointer;
    }
    button:hover { filter: brightness(0.95); }
    .notice {
      margin: 16px 20px 0;
      padding: 12px 14px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: var(--accent-soft);
      color: #164f44;
      font-size: 14px;
    }
    .notice.error { background: #fff5f5; color: var(--danger); border-color: rgba(180, 35, 24, 0.25); }
    .summary {
      margin: 16px 20px 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    .summary h3, .table-card h3 {
      margin: 0;
      padding: 12px 14px;
      background: #f8fafc;
      border-bottom: 1px solid var(--line);
      font-size: 15px;
    }
    pre {
      margin: 0;
      padding: 14px;
      white-space: pre-wrap;
      font: 13px/1.55 Consolas, Monaco, "Courier New", monospace;
      max-height: 300px;
      overflow: auto;
    }
    .table-card {
      margin: 16px 20px 20px;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    .path {
      padding: 9px 14px;
      color: var(--muted);
      background: #fff;
      border-bottom: 1px solid #edf0f5;
      font-size: 12px;
    }
    .table-wrap {
      overflow: auto;
      max-height: 520px;
      background: #fff;
    }
    table.data-table {
      border-collapse: collapse;
      width: max-content;
      min-width: 100%;
      font-size: 13px;
    }
    .data-table th, .data-table td {
      padding: 9px 11px;
      border-bottom: 1px solid #edf0f5;
      text-align: left;
      white-space: nowrap;
    }
    .data-table th {
      position: sticky;
      top: 0;
      background: #f8fafc;
      color: #475569;
      z-index: 1;
      font-size: 12px;
      text-transform: uppercase;
    }
    .empty {
      padding: 26px 14px;
      color: var(--muted);
      background: #fff;
    }
    @media (max-width: 900px) {
      header { padding: 18px 16px 0; }
      main { padding: 16px; }
      .topbar, .hero { flex-direction: column; align-items: stretch; }
      nav { overflow-x: auto; }
      .control-grid, .button-row { align-items: stretch; flex-direction: column; }
      label, label.wide { min-width: 0; width: 100%; }
      button { width: 100%; }
    }
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div>
        <h1>Stockie26 Dashboard</h1>
        <p class="subtitle">Research backtests, production prediction review, and paper trade P&amp;L.</p>
      </div>
      <div class="subtitle">Today: {{ today }}</div>
    </div>
    <nav>
      <a class="{{ 'active' if active == 'research' else '' }}" href="/research">Research</a>
      <a class="{{ 'active' if active == 'production' else '' }}" href="/production">Stockie Prediction</a>
      <a class="{{ 'active' if active == 'trades' else '' }}" href="/trades">Trades</a>
    </nav>
  </header>
  <main>
    <section class="surface">
      <div class="hero">
        <div>
          <h2>{{ title }}</h2>
          <p>{{ subtitle }}</p>
        </div>
      </div>
      <div class="controls">{{ controls | safe }}</div>
      {% if message %}<div class="notice">{{ message }}</div>{% endif %}
      {% if error %}<div class="notice error">{{ error }}</div>{% endif %}
      {% if summary %}
        <section class="summary">
          <h3>{{ summary_title }}</h3>
          <pre>{{ summary }}</pre>
        </section>
      {% endif %}
      {% for table in tables %}
        <section class="table-card">
          <h3>{{ table.title }}{% if table.rows %} <span class="subtitle">({{ table.rows }} rows)</span>{% endif %}</h3>
          {% if table.path %}<div class="path">{{ table.path }}</div>{% endif %}
          {% if table.html %}
            <div class="table-wrap">{{ table.html | safe }}</div>
          {% else %}
            <div class="empty">{{ table.empty_message }}</div>
          {% endif %}
        </section>
      {% endfor %}
    </section>
  </main>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
