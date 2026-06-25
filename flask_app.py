from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
from flask import Flask, render_template_string, request

from src.common.config import get_settings

NIFTY_SYMBOL = "NIFTY"
NIFTY_DISPLAY = "NIFTY 50"
MODEL_VERSION = "cascade_v1"

app = Flask(__name__)


@app.get("/")
def index():
    stocks, load_error = load_stock_options()
    selected = request.args.get("symbol", NIFTY_SYMBOL).strip().upper() or NIFTY_SYMBOL
    if selected not in {stock["symbol"] for stock in stocks}:
        selected = NIFTY_SYMBOL

    rows, table_error = load_june_signal_rows(selected)
    banner = "" if selected == NIFTY_SYMBOL else "Production prediction and option-selection rows are currently available for NIFTY 50 only."

    context = {
        "today": date.today().isoformat(),
        "stocks": stocks,
        "selected": selected,
        "selected_label": display_symbol(selected),
        "rows": rows,
        "load_error": table_error or load_error,
        "banner": banner,
        "summary": summarize_rows(rows),
    }
    return render_template_string(PAGE_TEMPLATE, **context)


def load_stock_options() -> tuple[list[dict[str, str]], str]:
    fallback = [{"symbol": NIFTY_SYMBOL, "label": NIFTY_DISPLAY}]
    settings = get_settings()
    if not settings.supabase_conn_str:
        return fallback, "SUPABASE_CONN_STR is not configured, so only the NIFTY 50 option is shown."

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        with psycopg2.connect(settings.supabase_conn_str) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT tradingsymbol, COALESCE(NULLIF(name, ''), tradingsymbol) AS label
                    FROM "WatchedInstrument"
                    WHERE is_active = true
                    ORDER BY CASE WHEN tradingsymbol = 'NIFTY' THEN 0 ELSE 1 END, tradingsymbol
                    """
                )
                rows = cur.fetchall()
    except Exception as exc:
        return fallback, f"Could not load stock list from Supabase: {exc}"

    options = [
        {"symbol": str(row["tradingsymbol"]), "label": display_symbol(str(row["tradingsymbol"]), str(row["label"]))}
        for row in rows
        if row.get("tradingsymbol")
    ]
    if not any(option["symbol"] == NIFTY_SYMBOL for option in options):
        options.insert(0, fallback[0])
    return options or fallback, ""


def load_june_signal_rows(symbol: str) -> tuple[list[dict[str, Any]], str]:
    if symbol != NIFTY_SYMBOL:
        return [], ""

    settings = get_settings()
    if not settings.supabase_conn_str:
        return [], "SUPABASE_CONN_STR is missing. Set it to load June prediction, option-selection, and P&L rows."

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        with psycopg2.connect(settings.supabase_conn_str) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(JUNE_SIGNAL_SQL, {"symbol": symbol, "model_version": MODEL_VERSION})
                db_rows = cur.fetchall()
    except Exception as exc:
        return [], f"Could not load June signal rows: {exc}"

    return [format_signal_row(dict(row)) for row in db_rows], ""


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
        o.stop_loss_enabled,
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
    stats.latest_option_price,
    stats.snapshot_count,
  CASE
    WHEN s.target_2_price IS NOT NULL AND stats.max_option_price >= s.target_2_price THEN s.target_2_price
    WHEN s.target_1_price IS NOT NULL AND stats.max_option_price >= s.target_1_price THEN s.target_1_price
    ELSE stats.latest_option_price
  END AS pnl_exit_price,
    CASE
    WHEN s.primary_buy_entry_price IS NULL OR stats.latest_option_price IS NULL THEN NULL
    ELSE ROUND(((
      CASE
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
        WHEN s.target_2_price IS NOT NULL AND stats.max_option_price >= s.target_2_price THEN s.target_2_price
        WHEN s.target_1_price IS NOT NULL AND stats.max_option_price >= s.target_1_price THEN s.target_1_price
        ELSE stats.latest_option_price
      END - s.primary_buy_entry_price
    )::numeric, 2)
    END AS latest_pnl_points,
    CASE
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
    entry = as_float(row.get("primary_buy_entry_price"))
    latest = as_float(row.get("latest_option_price"))
    max_price = as_float(row.get("max_option_price"))
    target_1 = as_float(row.get("target_1_price"))
    target_2 = as_float(row.get("target_2_price"))

    return {
        "trade_date": fmt_date(row.get("trade_date")),
        "next_trade_date": fmt_date(row.get("next_trade_date")),
        "predicted_direction": row.get("direction") or row.get("final_prediction") or "",
        "actual_trade_label": row.get("actual_trade_label") or "Pending",
        "regime": row.get("regime") or "",
        "prediction_strategy": row.get("prediction_strategy") or "",
        "strength_score": fmt_number(row.get("strength_score")),
        "confidence_level": fmt_pct(row.get("confidence_level")),
        "option_selection": row.get("selected_strategy") or row.get("no_trade_reason") or "No selection",
        "option_symbol": row.get("primary_buy_symbol") or "",
        "option_type": row.get("primary_buy_option_type") or "",
        "strike": fmt_number(row.get("primary_buy_strike")),
        "expiry": fmt_date(row.get("primary_buy_expiry")),
        "entry_price": fmt_money(entry),
        "target_1": fmt_money(target_1),
        "target_2": fmt_money(target_2),
        "latest_price": fmt_money(latest),
        "max_price": fmt_money(max_price),
        "pnl_pct": fmt_pct(row.get("latest_pnl_pct")),
        "pnl_points": fmt_money(row.get("latest_pnl_points")),
        "pnl_status": row.get("pnl_status") or "",
        "snapshot_count": int(row.get("snapshot_count") or 0),
        "last_snapshot_time": fmt_datetime(row.get("last_snapshot_time")),
    }


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"June rows": 0, "Option selections": 0, "Target hits": 0, "Pending labels": 0}
    return {
        "June rows": len(rows),
        "Option selections": sum(1 for row in rows if row["option_symbol"]),
        "Target hits": sum(1 for row in rows if "TARGET" in row["pnl_status"]),
        "Pending labels": sum(1 for row in rows if row["actual_trade_label"] == "Pending"),
    }


def display_symbol(symbol: str, label: str | None = None) -> str:
    if symbol == NIFTY_SYMBOL:
        return NIFTY_DISPLAY
    if label and label != symbol:
        return f"{label} ({symbol})"
    return symbol


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


PAGE_TEMPLATE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stockie26</title>
  <style>
    :root {
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #1f7a68;
      --accent-dark: #155a4d;
      --error: #b42318;
      --warn-bg: #fff8eb;
      --shadow: 0 14px 34px rgba(32, 42, 57, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: linear-gradient(180deg, rgba(31, 122, 104, 0.12), rgba(247, 248, 250, 0) 260px), var(--bg);
    }
    header {
      padding: 22px 32px 14px;
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: end;
      border-bottom: 1px solid rgba(217, 222, 231, 0.8);
      background: rgba(255, 255, 255, 0.5);
      backdrop-filter: blur(10px);
    }
    h1 { margin: 0; font-size: 30px; line-height: 1.1; letter-spacing: 0; }
    .subtitle { margin: 7px 0 0; color: var(--muted); font-size: 14px; }
    main { padding: 20px 32px 32px; }
    .surface {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      box-shadow: var(--shadow);
      overflow: hidden;
      min-height: calc(100vh - 150px);
    }
    .toolbar {
      padding: 18px 20px;
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: end;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
    }
    .title h2 { margin: 0; font-size: 22px; letter-spacing: 0; }
    .title p { margin: 5px 0 0; color: var(--muted); font-size: 13px; }
    form { min-width: 260px; }
    label { display: block; color: #344054; font-weight: 700; font-size: 13px; margin-bottom: 7px; }
    select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 11px 12px;
      font: inherit;
      color: var(--text);
      background: #fff;
      outline: none;
    }
    select:focus { border-color: rgba(31, 122, 104, 0.55); box-shadow: 0 0 0 3px rgba(31, 122, 104, 0.12); }
    .notice {
      margin: 16px 20px 0;
      padding: 13px 15px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #f8fafc;
      color: #344054;
      font-size: 14px;
    }
    .notice.error { border-color: rgba(180, 35, 24, 0.25); background: #fff5f5; color: var(--error); }
    .notice.warn { background: var(--warn-bg); }
    .metric-grid {
      padding: 16px 20px 0;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px;
    }
    .metric { border: 1px solid var(--line); border-radius: 8px; padding: 14px; background: #fff; }
    .metric span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .metric strong { font-size: 24px; }
    .table-wrap {
      margin: 16px 20px 20px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      max-height: calc(100vh - 310px);
      min-height: 480px;
      background: #fff;
    }
    table { border-collapse: collapse; width: max-content; min-width: 100%; font-size: 13px; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #edf0f5; text-align: left; white-space: nowrap; }
    th { position: sticky; top: 0; z-index: 1; background: #f8fafc; color: #475569; font-size: 12px; text-transform: uppercase; }
    tbody tr:hover { background: #fbfcfe; }
    .empty-state {
      margin: 16px 20px 20px;
      min-height: 340px;
      display: grid;
      place-items: center;
      border: 1px dashed #cbd5e1;
      border-radius: 10px;
      background: #fbfcfe;
      color: #344054;
      text-align: center;
      padding: 32px;
    }
    .empty-state strong { display: block; font-size: 21px; margin-bottom: 8px; }
    .empty-state span { color: var(--muted); font-size: 14px; }
    @media (max-width: 900px) {
      header { padding: 18px 16px 12px; align-items: start; flex-direction: column; }
      main { padding: 16px; }
      .toolbar { align-items: stretch; flex-direction: column; }
      form { min-width: 0; }
      .table-wrap { min-height: 320px; max-height: 560px; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Stockie26</h1>
      <p class="subtitle">Prediction accuracy, option selection, and June P&amp;L by signal date.</p>
    </div>
    <div class="subtitle">Today: {{ today }}</div>
  </header>

  <main>
    <section class="surface">
      <div class="toolbar">
        <div class="title">
          <h2>{{ selected_label }} June Signal Review</h2>
          <p>Predicted direction, realized trade label, selected option, target plan, and option P&amp;L.</p>
        </div>
        <form method="get" action="/">
          <label for="symbol">Stock</label>
          <select id="symbol" name="symbol" onchange="this.form.submit()">
            {% for stock in stocks %}
              <option value="{{ stock.symbol }}" {{ 'selected' if stock.symbol == selected else '' }}>{{ stock.label }}</option>
            {% endfor %}
          </select>
        </form>
      </div>

      {% if banner %}<div class="notice warn">{{ banner }}</div>{% endif %}
      {% if load_error %}<div class="notice error">{{ load_error }}</div>{% endif %}

      <div class="metric-grid">
        {% for key, value in summary.items() %}
          <div class="metric"><span>{{ key }}</span><strong>{{ value }}</strong></div>
        {% endfor %}
      </div>

      {% if rows %}
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Signal date</th>
                <th>Trade date</th>
                <th>Predicted</th>
                <th>Actual label</th>
                <th>Regime</th>
                <th>Prediction strategy</th>
                <th>Strength</th>
                <th>Confidence</th>
                <th>Option selection</th>
                <th>Option symbol</th>
                <th>Type</th>
                <th>Strike</th>
                <th>Expiry</th>
                <th>Entry</th>
                <th>Target 1</th>
                <th>Target 2</th>
                <th>Latest</th>
                <th>Max</th>
                <th>P&amp;L %</th>
                <th>P&amp;L points</th>
                <th>Status</th>
                <th>Snapshots</th>
                <th>Last snapshot</th>
              </tr>
            </thead>
            <tbody>
              {% for row in rows %}
                <tr>
                  <td>{{ row.trade_date }}</td>
                  <td>{{ row.next_trade_date }}</td>
                  <td>{{ row.predicted_direction }}</td>
                  <td>{{ row.actual_trade_label }}</td>
                  <td>{{ row.regime }}</td>
                  <td>{{ row.prediction_strategy }}</td>
                  <td>{{ row.strength_score }}</td>
                  <td>{{ row.confidence_level }}</td>
                  <td>{{ row.option_selection }}</td>
                  <td>{{ row.option_symbol }}</td>
                  <td>{{ row.option_type }}</td>
                  <td>{{ row.strike }}</td>
                  <td>{{ row.expiry }}</td>
                  <td>{{ row.entry_price }}</td>
                  <td>{{ row.target_1 }}</td>
                  <td>{{ row.target_2 }}</td>
                  <td>{{ row.latest_price }}</td>
                  <td>{{ row.max_price }}</td>
                  <td>{{ row.pnl_pct }}</td>
                  <td>{{ row.pnl_points }}</td>
                  <td>{{ row.pnl_status }}</td>
                  <td>{{ row.snapshot_count }}</td>
                  <td>{{ row.last_snapshot_time }}</td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      {% else %}
        <div class="empty-state">
          <div>
            <strong>No June rows found</strong>
            <span>Run the daily prediction and option-selection pipeline for NIFTY 50, then refresh this page.</span>
          </div>
        </div>
      {% endif %}
    </section>
  </main>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
