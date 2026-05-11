from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from flask import Flask, abort, redirect, render_template_string, request, send_file, url_for

from src.backtest.historical_underlying_backtest import (
    HistoricalUnderlyingBacktestRequest,
    run_historical_underlying_backtest,
)
from src.backtest.news_underlying_backtest import (
    NewsBacktestRequest,
    run_news_underlying_backtest,
)
from src.data_manager.underlying_history_reader import get_active_underlyings
from src.services.historical_prediction import (
    HistoricalPredictionRequest,
    HistoricalPredictionService,
)
from src.services.prediction_service import PredictionService


PROJECT_ROOT = Path(__file__).resolve().parent
app = Flask(__name__)


def load_watched_underlyings() -> list[str]:
    symbols = get_active_underlyings(instrument_type=None)
    return sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})


@app.get("/")
def index():
    selected = request.args.get("underlying", "")
    context = {
        "today": date.today().isoformat(),
        "selected": selected,
        "underlyings": [],
        "load_error": "",
        "result": None,
        "csv_rows": [],
        "csv_columns": [],
        "csv_download_url": "",
        "active_path": request.args.get("path", "technical"),
    }
    try:
        context["underlyings"] = load_watched_underlyings()
        if not selected and context["underlyings"]:
            context["selected"] = context["underlyings"][0]
    except Exception as exc:
        context["load_error"] = str(exc)
    return render_template_string(PAGE_TEMPLATE, **context)


@app.post("/technical/predict")
def technical_predict():
    underlying = request.form.get("underlying", "").strip().upper()
    if not underlying:
        return redirect(url_for("index", path="technical"))

    result: dict[str, Any]
    csv_path: Path | None = None
    try:
        service = PredictionService.from_project_root(PROJECT_ROOT)
        result = service.run_reference_date_predictions(
            instrument=underlying,
            reference_date=date.today(),
        )
        output_file = result.get("output_file")
        csv_path = PROJECT_ROOT / "output" / str(output_file) if output_file else None
        status = "success"
    except Exception as exc:
        result = {"error": str(exc)}
        status = "error"

    return render_result(
        result=result,
        csv_path=csv_path,
        selected=underlying,
        active_path="technical",
        banner=("Prediction generated." if status == "success" else "Prediction failed."),
        status=status,
    )


@app.post("/technical/backtest")
def technical_backtest():
    underlying = request.form.get("underlying", "").strip().upper()
    if not underlying:
        return redirect(url_for("index", path="technical"))

    result: dict[str, Any]
    csv_path: Path | None = None
    try:
        historical_service = HistoricalPredictionService.from_project_root(PROJECT_ROOT)
        prediction_result = historical_service.run(
            HistoricalPredictionRequest(
                underlying=underlying,
                lookback_days=60,
            )
        )
        backtest_result = run_historical_underlying_backtest(
            HistoricalUnderlyingBacktestRequest(underlying=underlying)
        )
        result = {
            "prediction_run": prediction_result,
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


@app.post("/news/backtest")
def news_backtest():
    published_date_raw = request.form.get("published_date", date.today().isoformat())
    force = request.form.get("force") == "on"
    try:
        published_date = date.fromisoformat(published_date_raw)
    except ValueError:
        published_date = date.today()

    csv_path: Path | None = None
    try:
        result = run_news_underlying_backtest(
            NewsBacktestRequest(
                signal_journal_file="trade_signal_journal.csv",
                output_dir=PROJECT_ROOT / "output",
                published_date=published_date,
                force=force,
            )
        )
        output_file = result.get("output_file")
        csv_path = Path(str(output_file)) if output_file else None
        status = "success"
    except Exception as exc:
        result = {"error": str(exc)}
        status = "error"

    return render_result(
        result=result,
        csv_path=csv_path,
        selected="",
        active_path="news",
        banner=("News signal backtest completed." if status == "success" else "News signal backtest failed."),
        status=status,
        published_date=published_date.isoformat(),
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
        "underlyings": [],
        "load_error": "",
        "result": json_safe(result),
        "headline": extract_headline(result),
        "csv_rows": [],
        "csv_columns": [],
        "csv_download_url": "",
        "active_path": active_path,
        "banner": banner,
        "status": status,
        "published_date": published_date or date.today().isoformat(),
    }
    try:
        context["underlyings"] = load_watched_underlyings()
    except Exception as exc:
        context["load_error"] = str(exc)

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
{% macro result_panel(headline, csv_rows, csv_columns, result, csv_download_url) -%}
  {% if headline %}
    <div class="metric-grid">
      {% for key, value in headline.items() %}
        <div class="metric"><span>{{ key.replace('_', ' ') }}</span><strong>{{ value }}</strong></div>
      {% endfor %}
    </div>
  {% endif %}
  {% if csv_rows %}
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
  {% elif result %}
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
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Stockie26</h1>
      <p class="subtitle">Technical predictions and news-signal backtests against watched instruments.</p>
    </div>
    <div class="subtitle">Today: {{ today }}</div>
  </header>

  <main>
    <nav class="tabs">
      <a class="tab {{ 'active' if active_path == 'technical' else '' }}" href="{{ url_for('index', path='technical', underlying=selected) }}">Technical Analysis</a>
      <a class="tab {{ 'active' if active_path == 'news' else '' }}" href="{{ url_for('index', path='news') }}">News Signal Backtest</a>
    </nav>

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
            <h2 class="section-title">Technical Analysis</h2>
            <p class="hint">Select a watched stock or index. Predict uses latest available history up to yesterday's trading data; Backtest regenerates the last 60 days and evaluates the results.</p>
            <div class="field">
              <label for="underlying">Watched stock / index</label>
              <select id="underlying" name="underlying" {% if not underlyings %}disabled{% endif %}>
                {% for item in underlyings %}
                  <option value="{{ item }}" {% if item == selected %}selected{% endif %}>{{ item }}</option>
                {% endfor %}
              </select>
            </div>
            <div class="actions">
              <button type="submit" formaction="{{ url_for('technical_predict') }}" {% if not underlyings %}disabled{% endif %}>Predict</button>
              <button type="submit" formaction="{{ url_for('technical_backtest') }}" class="secondary" {% if not underlyings %}disabled{% endif %}>Backtest</button>
            </div>
          </form>
          <div class="result-pane">
            {{ result_panel(headline, csv_rows, csv_columns, result, csv_download_url) }}
          </div>
        </div>
      </div>

      <div class="path {{ 'active' if active_path == 'news' else '' }}">
        <div class="grid">
          <form method="post" action="{{ url_for('news_backtest') }}">
            <h2 class="section-title">News Signal Backtest</h2>
            <p class="hint">News prediction is disabled for now. Backtest reads existing rows from output/trade_signal_journal.csv for the selected published date.</p>
            <div class="field">
              <label for="article">News article</label>
              <textarea id="article" name="article" placeholder="Paste article text here for the future Predict flow."></textarea>
            </div>
            <div class="field">
              <label for="published_date">Published date</label>
              <input id="published_date" name="published_date" type="date" value="{{ published_date or today }}">
            </div>
            <label class="check">
              <input type="checkbox" name="force">
              Force rerun already-backtested rows
            </label>
            <div class="actions" style="margin-top:16px;">
              <button type="button" disabled>Predict</button>
              <button type="submit" class="secondary">Backtest</button>
            </div>
          </form>
          <div class="result-pane">
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
