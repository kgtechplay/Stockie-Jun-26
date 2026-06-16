from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from flask import Flask, abort, render_template_string, request, send_file, url_for

from backtest.legacy.historical_underlying_backtest import (
    HistoricalUnderlyingBacktestRequest,
    run_historical_underlying_backtest,
)
PROJECT_ROOT = Path(__file__).resolve().parent
NIFTY_SYMBOL = "NIFTY"
app = Flask(__name__)


@app.get("/")
def index():
    dashboard = load_nifty_dashboard()
    context = {
        "today": date.today().isoformat(),
        "selected": NIFTY_SYMBOL,
        "underlyings": [NIFTY_SYMBOL],
        "load_error": "",
        "result": None,
        "csv_rows": dashboard["preview_rows"],
        "csv_columns": dashboard["preview_columns"],
        "csv_download_url": dashboard["download_url"],
        "active_path": "technical",
        "dashboard": dashboard,
    }
    return render_template_string(PAGE_TEMPLATE, **context)


@app.post("/technical/predict")
def technical_predict():
    underlying = NIFTY_SYMBOL

    dashboard = load_nifty_dashboard()
    result: dict[str, Any] = {
        "underlying": underlying,
        "dashboard_file": dashboard["path"],
        "rows": dashboard["row_count"],
        "latest": dashboard["latest"],
        "summary": dashboard["summary"],
    }
    csv_path = Path(dashboard["path"]) if dashboard["has_file"] else None
    status = "success"

    return render_result(
        result=result,
        csv_path=csv_path,
        selected=underlying,
        active_path="technical",
        banner="NIFTY dashboard refreshed.",
        status=status,
    )


@app.post("/technical/backtest")
def technical_backtest():
    underlying = NIFTY_SYMBOL

    result: dict[str, Any]
    csv_path: Path | None = None
    try:
        backtest_result = run_historical_underlying_backtest(
            HistoricalUnderlyingBacktestRequest(underlying=underlying)
        )
        result = {
            "backtest": backtest_result,
        }
        output_file = backtest_result.get("prediction_file")
        csv_path = Path(str(output_file)) if output_file else None
        status = "success"
    except Exception as exc:
        result = {"error": str(exc)}
        status = "error"

    return render_result(
        result=result,
        csv_path=csv_path,
        selected=underlying,
        active_path="technical",
        banner=("Historical prediction and backtest completed." if status == "success" else "Backtest failed."),
        status=status,
    )


def render_result(
    result: dict[str, Any],
    csv_path: Path | None,
    selected: str,
    active_path: str,
    banner: str,
    status: str,
    published_date: str | None = None,
):
    context = {
        "today": date.today().isoformat(),
        "selected": selected,
        "underlyings": [NIFTY_SYMBOL],
        "load_error": "",
        "result": json_safe(result),
        "headline": extract_headline(result),
        "csv_rows": [],
        "csv_columns": [],
        "csv_download_url": "",
        "active_path": "technical",
        "banner": banner,
        "status": status,
        "published_date": published_date or date.today().isoformat(),
        "dashboard": load_nifty_dashboard(),
    }

    if csv_path:
        rows, columns = read_csv_preview(csv_path)
        context["csv_rows"] = rows
        context["csv_columns"] = columns
        context["csv_download_url"] = build_download_url(csv_path)
    return render_template_string(PAGE_TEMPLATE, **context)


@app.get("/download/csv")
def download_csv():
    requested = request.args.get("path", "")
    path = resolve_output_csv_path(requested)
    if path is None or not path.exists() or path.suffix.lower() != ".csv":
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="text/csv")


def read_csv_preview(path: Path, limit: int = 50) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], []
    df = pd.read_csv(path).head(limit)
    return df.fillna("").to_dict(orient="records"), list(df.columns)


def load_nifty_dashboard() -> dict[str, Any]:
    path = PROJECT_ROOT / "output" / "backtest" / f"{NIFTY_SYMBOL}_prediction.csv"
    dashboard: dict[str, Any] = {
        "has_file": path.exists(),
        "path": str(path),
        "download_url": "",
        "row_count": 0,
        "latest": {},
        "summary": {},
        "trend_rows": [],
        "preview_rows": [],
        "preview_columns": [],
    }
    if not path.exists():
        return dashboard

    df = pd.read_csv(path)
    if df.empty:
        return dashboard

    dashboard["row_count"] = int(len(df))
    dashboard["download_url"] = build_download_url(path)
    preview_rows, preview_columns = read_csv_preview(path)
    dashboard["preview_rows"] = preview_rows
    dashboard["preview_columns"] = preview_columns

    date_col = "date" if "date" in df.columns else None
    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.sort_values(date_col)

    latest = df.iloc[-1].to_dict()
    latest_fields = [
        "date",
        "aggregate_decision",
        "underlying_raw_signal",
        "underlying_direction",
        "underlying_strength_score",
        "underlying_confidence",
        "detected_regime",
        "actual_move",
        "aggregate_decision_result",
    ]
    dashboard["latest"] = _pick_fields(latest, latest_fields)

    result_cols = [c for c in df.columns if c.endswith("_result")]
    correct = int((df[result_cols] == "CORRECT").sum().sum()) if result_cols else 0
    wrong = int((df[result_cols] == "WRONG").sum().sum()) if result_cols else 0
    total = correct + wrong
    dashboard["summary"] = {
        "rows": int(len(df)),
        "result_columns": len(result_cols),
        "correct": correct,
        "wrong": wrong,
        "accuracy_pct": round((correct / total) * 100, 2) if total else None,
    }

    trend_fields = [
        "date",
        "aggregate_decision",
        "underlying_raw_signal",
        "underlying_direction",
        "underlying_strength_score",
        "detected_regime",
        "actual_move",
        "aggregate_decision_result",
    ]
    dashboard["trend_rows"] = [
        _pick_fields(row, trend_fields)
        for row in df.tail(10).fillna("").to_dict(orient="records")
    ]
    return dashboard


def _pick_fields(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in fields:
        if field in row:
            value = row[field]
            if hasattr(value, "date"):
                value = value.date().isoformat()
            out[field] = json_safe(value)
    return out


def build_download_url(path: Path) -> str:
    resolved = resolve_output_csv_path(str(path))
    if resolved is None or not resolved.exists():
        return ""
    relative = resolved.relative_to(PROJECT_ROOT)
    return url_for("download_csv", path=relative.as_posix())


def resolve_output_csv_path(value: str) -> Path | None:
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        resolved = candidate.resolve()
        output_root = (PROJECT_ROOT / "output").resolve()
        if resolved == output_root or output_root not in resolved.parents:
            return None
        return resolved
    except OSError:
        return None


def extract_headline(result: dict[str, Any]) -> dict[str, Any]:
    backtest = result.get("backtest") if isinstance(result.get("backtest"), dict) else result
    summary = backtest.get("summary", {}) if isinstance(backtest, dict) else {}
    keys = [
        "days_backtested",
        "actionable_predictions",
        "approved_signals_backtested",
        "profit_hits",
        "stop_hits",
        "accuracy_pct",
        "profit_hit_rate_pct",
        "recall_pct",
    ]
    return {key: summary[key] for key in keys if key in summary}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


PAGE_TEMPLATE = r"""
{% macro dashboard_panel(dashboard) -%}
  <div class="dashboard">
    <div class="dashboard-head">
      <div>
        <strong>NIFTY Data And Trends</strong>
        <span>
          {% if dashboard.has_file %}
            Source: output/backtest/NIFTY_prediction.csv
          {% else %}
            No NIFTY historical output found yet.
          {% endif %}
        </span>
      </div>
      {% if dashboard.download_url %}
        <a class="download-button" href="{{ dashboard.download_url }}">Download NIFTY CSV</a>
      {% endif %}
    </div>
    {% if dashboard.has_file %}
      <div class="metric-grid">
        {% for key, value in dashboard.summary.items() %}
          <div class="metric"><span>{{ key.replace('_', ' ') }}</span><strong>{{ value if value is not none else 'N/A' }}</strong></div>
        {% endfor %}
      </div>
      {% if dashboard.latest %}
        <div class="compact-panel">
          <strong>Latest Signal</strong>
          <div class="kv-grid">
            {% for key, value in dashboard.latest.items() %}
              <span>{{ key.replace('_', ' ') }}</span><b>{{ value }}</b>
            {% endfor %}
          </div>
        </div>
      {% endif %}
      {% if dashboard.trend_rows %}
        <div class="compact-panel">
          <strong>Recent Trend Rows</strong>
          <div class="table-wrap compact">
            <table>
              <thead>
                <tr>{% for column in dashboard.trend_rows[0].keys() %}<th>{{ column.replace('_', ' ') }}</th>{% endfor %}</tr>
              </thead>
              <tbody>
                {% for row in dashboard.trend_rows %}
                  <tr>{% for value in row.values() %}<td>{{ value }}</td>{% endfor %}</tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
        </div>
      {% endif %}
    {% else %}
      <div class="notice">Run the NIFTY prediction or backtest to generate the dashboard CSV.</div>
    {% endif %}
  </div>
{%- endmacro %}

{% macro result_panel(headline, csv_rows, csv_columns, result, csv_download_url) -%}
  {% set error_message = result.get('error') if result and result.get('error') else '' %}
  {% if error_message %}
    <div class="notice error">
      <strong>Run failed.</strong><br>
      {{ error_message }}
    </div>
  {% endif %}
  {% if headline %}
    <div class="metric-grid">
      {% for key, value in headline.items() %}
        <div class="metric"><span>{{ key.replace('_', ' ') }}</span><strong>{{ value }}</strong></div>
      {% endfor %}
    </div>
  {% endif %}
  {% if csv_rows and not error_message %}
    <div class="result-toolbar">
      <div>
        <strong>CSV Preview</strong>
        <span>Showing first 50 rows.</span>
      </div>
      {% if csv_download_url %}
        <a class="download-button" href="{{ csv_download_url }}">Download CSV</a>
      {% endif %}
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>{% for column in csv_columns %}<th>{{ column }}</th>{% endfor %}</tr>
        </thead>
        <tbody>
          {% for row in csv_rows %}
            <tr>{% for column in csv_columns %}<td>{{ row[column] }}</td>{% endfor %}</tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% elif result and not error_message %}
    <div class="notice">No CSV preview available for this run.</div>
  {% else %}
    <div class="empty-state">
      <div>
        <strong>Ready when you are</strong>
        <span>Run Predict or Backtest to fill this workspace with strategy output, metrics, and CSV rows.</span>
      </div>
    </div>
  {% endif %}
  {% if result %}
    <details>
      <summary>Raw result dictionary</summary>
      <pre>{{ result | tojson(indent=2) }}</pre>
    </details>
  {% endif %}
{%- endmacro %}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stockie26</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1e252f;
      --muted: #697386;
      --line: #d9dee7;
      --accent: #1f7a68;
      --accent-dark: #155a4d;
      --warn: #a96012;
      --error: #b42318;
      --shadow: 0 16px 40px rgba(32, 42, 57, 0.09);
    }
    * { box-sizing: border-box; }
    html { min-height: 100%; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background:
        linear-gradient(180deg, rgba(31, 122, 104, 0.13), rgba(246, 247, 249, 0) 280px),
        var(--bg);
    }
    header {
      width: 100%;
      padding: 22px 32px 14px;
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: end;
      border-bottom: 1px solid rgba(217, 222, 231, 0.75);
      background: rgba(255, 255, 255, 0.42);
      backdrop-filter: blur(10px);
    }
    h1 { margin: 0; font-size: 32px; letter-spacing: 0; line-height: 1.05; }
    .subtitle { margin: 7px 0 0; color: var(--muted); font-size: 14px; }
    main { width: 100%; padding: 20px 32px 32px; }
    .tabs { display: flex; gap: 10px; margin: 0 0 16px; }
    .tab {
      padding: 11px 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--muted);
      text-decoration: none;
      background: rgba(255, 255, 255, 0.65);
      font-weight: 650;
      font-size: 14px;
    }
    .tab.active { color: var(--text); background: var(--panel); box-shadow: 0 8px 24px rgba(32, 42, 57, 0.08); }
    .surface {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      box-shadow: var(--shadow);
      overflow: hidden;
      min-height: calc(100vh - 160px);
    }
    .path { display: none; padding: 22px; }
    .path.active { display: block; }
    .grid {
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr);
      gap: 22px;
      align-items: start;
    }
    form {
      position: sticky;
      top: 20px;
      align-self: start;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
      background: #fbfcfe;
    }
    .result-pane {
      min-width: 0;
      min-height: calc(100vh - 220px);
    }
    .section-title { margin: 0 0 8px; font-size: 22px; }
    .hint { color: var(--muted); margin: 0 0 24px; font-size: 14px; line-height: 1.6; }
    label { display: block; color: #334155; font-weight: 700; font-size: 13px; margin-bottom: 8px; }
    select, input[type="date"], textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 13px 12px;
      font: inherit;
      color: var(--text);
      background: #fff;
      outline: none;
    }
    select:focus, input[type="date"]:focus, textarea:focus {
      border-color: rgba(31, 122, 104, 0.55);
      box-shadow: 0 0 0 3px rgba(31, 122, 104, 0.12);
    }
    textarea { min-height: 220px; resize: vertical; }
    .field { margin-bottom: 18px; }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; }
    button {
      border: 0;
      border-radius: 7px;
      padding: 12px 17px;
      font: inherit;
      font-weight: 750;
      cursor: pointer;
      background: var(--accent);
      color: white;
    }
    button:hover { background: var(--accent-dark); }
    button.secondary { background: #eef2f6; color: #263241; }
    button:disabled { background: #cbd5e1; cursor: not-allowed; }
    .notice {
      padding: 14px 16px;
      border-radius: 8px;
      margin-bottom: 16px;
      border: 1px solid var(--line);
      background: #f8fafc;
      color: #334155;
    }
    .notice.error { border-color: rgba(180, 35, 24, 0.25); background: #fff5f5; color: var(--error); }
    .notice.success { border-color: rgba(31, 122, 104, 0.28); background: #f1fbf8; color: #145b4f; }
    .metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 16px; }
    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      background: #fbfcfe;
    }
    .metric span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .metric strong { font-size: 26px; }
    .result-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 12px;
    }
    .result-toolbar strong { display: block; font-size: 15px; }
    .result-toolbar span { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
    .download-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
      padding: 0 14px;
      border-radius: 7px;
      background: var(--accent);
      color: #fff;
      text-decoration: none;
      font-weight: 750;
      white-space: nowrap;
    }
    .download-button:hover { background: var(--accent-dark); }
    .table-wrap {
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      max-height: calc(100vh - 300px);
      min-height: 520px;
      background: #fff;
    }
    table { border-collapse: collapse; width: max-content; min-width: 100%; font-size: 13px; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #edf0f5; text-align: left; white-space: nowrap; }
    th { position: sticky; top: 0; background: #f8fafc; z-index: 1; color: #475569; }
    details { margin-top: 16px; }
    pre {
      overflow: auto;
      padding: 14px;
      border-radius: 8px;
      background: #111827;
      color: #e5e7eb;
      font-size: 12px;
      line-height: 1.5;
      max-height: 360px;
    }
    .check { display: flex; align-items: center; gap: 8px; color: #334155; font-size: 14px; }
    .readonly-symbol {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 13px 12px;
      background: #fff;
      font-weight: 800;
      letter-spacing: 0;
    }
    .dashboard {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 18px;
      background: #fbfcfe;
    }
    .dashboard-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 14px;
    }
    .dashboard-head strong,
    .compact-panel strong { display: block; font-size: 15px; margin-bottom: 3px; }
    .dashboard-head span { color: var(--muted); font-size: 12px; }
    .compact-panel {
      border-top: 1px solid #edf0f5;
      padding-top: 14px;
      margin-top: 14px;
    }
    .kv-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 8px 16px;
      font-size: 13px;
    }
    .kv-grid span { color: var(--muted); }
    .kv-grid b { color: var(--text); font-weight: 750; }
    .table-wrap.compact {
      max-height: 260px;
      min-height: 0;
      margin-top: 10px;
    }
    .empty-state {
      min-height: calc(100vh - 220px);
      display: grid;
      place-items: center;
      border: 1px dashed #cbd5e1;
      border-radius: 10px;
      background:
        linear-gradient(135deg, rgba(31, 122, 104, 0.08), rgba(255, 255, 255, 0.6)),
        #fbfcfe;
      color: #334155;
      text-align: center;
      padding: 32px;
    }
    .empty-state strong { display: block; font-size: 22px; margin-bottom: 8px; }
    .empty-state span { color: var(--muted); font-size: 14px; }
    @media (max-width: 900px) {
      header { padding: 18px 16px 12px; }
      main { padding: 16px; }
      header { align-items: start; flex-direction: column; }
      .grid { grid-template-columns: 1fr; }
      form { position: static; }
      .surface { min-height: auto; }
      .result-pane { min-height: auto; }
      .empty-state { min-height: 260px; }
      .table-wrap { max-height: 520px; min-height: 260px; }
      .result-toolbar { align-items: stretch; flex-direction: column; }
      .download-button { width: 100%; }
      .tabs { flex-wrap: wrap; }
      .dashboard-head { align-items: stretch; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Stockie26</h1>
      <p class="subtitle">NIFTY technical data, trend signals, and backtesting results.</p>
    </div>
    <div class="subtitle">Today: {{ today }}</div>
  </header>

  <main>
    {% if banner %}
      <div class="notice {{ status }}">{{ banner }}</div>
    {% endif %}
    {% if load_error %}
      <div class="notice error">Could not load watched instruments: {{ load_error }}</div>
    {% endif %}

    <section class="surface">
      <div class="path {{ 'active' if active_path == 'technical' else '' }}">
        <div class="grid">
          <form method="post">
            <h2 class="section-title">NIFTY Technical Analysis</h2>
            <p class="hint">This app is intentionally NIFTY-only. Predict runs the latest signal view; Backtest regenerates the legacy CSV backtest for NIFTY only.</p>
            <div class="field">
              <label>Underlying</label>
              <div class="readonly-symbol">NIFTY</div>
            </div>
            <div class="actions">
              <button type="submit" formaction="{{ url_for('technical_predict') }}">Predict</button>
              <button type="submit" formaction="{{ url_for('technical_backtest') }}" class="secondary">Backtest</button>
            </div>
          </form>
          <div class="result-pane">
            {{ dashboard_panel(dashboard) }}
            {{ result_panel(headline, csv_rows, csv_columns, result, csv_download_url) }}
          </div>
        </div>
      </div>
    </section>
  </main>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
