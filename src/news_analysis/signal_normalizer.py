from __future__ import annotations

import csv
import hashlib
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import yaml
except ImportError:  # pragma: no cover - fallback keeps imports alive before deps install.
    yaml = None

from src.news_analysis.reviewList.output_schema import ApprovedTradeSignal, ReviewListOutput


DEFAULT_CONFIG: dict[str, Any] = {
    "trade_signal_generation": {
        "minimum_confidence_for_signal": 0.50,
        "minimum_trade_score_for_approved": 0.60,
        "minimum_trade_score_for_monitor_only": 0.40,
        "excluded_signal_directions": ["mixed", "uncertain"],
        "excluded_timeline_buckets": ["uncertain"],
    },
    "score_weights": {
        "directness": {"direct": 1.0, "indirect": 0.65, "second_order": 0.35},
        "sensitivity": {"very_high": 1.0, "high": 0.8, "medium": 0.5, "low": 0.25},
        "timeline": {
            "same_day": 1.0,
            "1_3_days": 0.85,
            "1_4_weeks": 0.55,
            "1_6_months": 0.30,
            "6_months_plus": 0.15,
            "uncertain": 0.0,
        },
    },
    "suggested_max_holding_days": {
        "same_day": 1,
        "1_3_days": 3,
        "1_4_weeks": 20,
        "1_6_months": 90,
        "6_months_plus": 120,
    },
    "entry_rules": {
        "market_timezone": "Asia/Kolkata",
        "market_open_time": "09:15",
        "market_close_time": "15:30",
        "intraday_entry_delay_minutes": 15,
    },
}


@dataclass
class FinalizedTradeSignal:
    signal_id: str
    news_event_id: str
    published_at: datetime
    processed_at: datetime
    commodity: str
    commodity_direction: str
    commodity_confidence: float
    sector: str
    sub_sector: str | None
    company_name: str
    ticker: str
    exchange: str | None
    expected_stock_direction: str
    directness: str
    sensitivity: str
    timeline_bucket: str
    sector_confidence: float
    company_confidence: float
    reviewer_confidence: float
    final_trade_score: float
    entry_allowed_from: datetime
    suggested_max_holding_days: int
    signal_status: str
    impact_channel: str
    reasoning: str
    risks_to_thesis: list[str]
    invalidation_triggers: list[str]

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        for key in ["published_at", "processed_at", "entry_allowed_from"]:
            row[key] = row[key].isoformat() if row[key] else ""
        row["risks_to_thesis"] = " | ".join(self.risks_to_thesis)
        row["invalidation_triggers"] = " | ".join(self.invalidation_triggers)
        return row


def normalize_review_signals(
    review_output: ReviewListOutput,
    config_path: Path | None = None,
) -> list[FinalizedTradeSignal]:
    config = load_signal_config(config_path)
    finalized: list[FinalizedTradeSignal] = []
    for raw_signal in review_output.approved_trade_signals:
        normalized = normalize_signal(raw_signal, config)
        if normalized is not None:
            finalized.append(normalized)
    return finalized


def load_signal_config(config_path: Path | None = None) -> dict[str, Any]:
    if not config_path or not config_path.exists() or yaml is None:
        return DEFAULT_CONFIG

    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}

    config = DEFAULT_CONFIG.copy()
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key] = {**config[key], **value}
        else:
            config[key] = value
    return config


def normalize_signal(raw: ApprovedTradeSignal, config: dict[str, Any]) -> FinalizedTradeSignal | None:
    ticker = (raw.stock.ticker or "").strip().upper()
    if not ticker:
        return None

    direction = _clean_token(raw.expected_stock_direction)
    commodity_direction = _clean_token(raw.commodity_direction)
    timeline = _clean_token(raw.timeline_bucket)
    directness = _clean_token(raw.directness)
    sensitivity = _clean_token(raw.sensitivity)

    trade_config = config["trade_signal_generation"]
    if direction not in {"up", "down"}:
        return None
    if commodity_direction in set(trade_config.get("excluded_signal_directions", [])):
        return None
    if timeline in set(trade_config.get("excluded_timeline_buckets", [])):
        return None
    if min(raw.commodity_confidence, raw.sector_confidence, raw.company_confidence, raw.reviewer_confidence) < float(
        trade_config.get("minimum_confidence_for_signal", 0.0)
    ):
        return None

    final_score = calculate_final_trade_score(raw, directness, sensitivity, timeline, config)
    approved_threshold = float(trade_config["minimum_trade_score_for_approved"])
    monitor_threshold = float(trade_config["minimum_trade_score_for_monitor_only"])
    if final_score >= approved_threshold:
        status = "approved"
    elif final_score >= monitor_threshold:
        status = "monitor_only"
    else:
        return None

    holding_days = int(config["suggested_max_holding_days"].get(timeline) or 1)
    processed_at = ensure_market_timezone(raw.processed_at, config)
    return FinalizedTradeSignal(
        signal_id=raw.signal_id or generate_signal_id(raw, ticker),
        news_event_id=raw.news_event_id,
        published_at=ensure_market_timezone(raw.published_at, config),
        processed_at=processed_at,
        commodity=raw.commodity,
        commodity_direction=commodity_direction,
        commodity_confidence=float(raw.commodity_confidence),
        sector=raw.sector,
        sub_sector=raw.sub_sector,
        company_name=raw.stock.company_name,
        ticker=ticker,
        exchange=raw.stock.exchange,
        expected_stock_direction=direction,
        directness=directness,
        sensitivity=sensitivity,
        timeline_bucket=timeline,
        sector_confidence=float(raw.sector_confidence),
        company_confidence=float(raw.company_confidence),
        reviewer_confidence=float(raw.reviewer_confidence),
        final_trade_score=final_score,
        entry_allowed_from=calculate_entry_allowed_from(processed_at, config),
        suggested_max_holding_days=holding_days,
        signal_status=status,
        impact_channel=raw.impact_channel,
        reasoning=raw.reasoning,
        risks_to_thesis=raw.risks_to_thesis,
        invalidation_triggers=raw.invalidation_triggers,
    )


def calculate_final_trade_score(
    raw: ApprovedTradeSignal,
    directness: str,
    sensitivity: str,
    timeline: str,
    config: dict[str, Any],
) -> float:
    weights = config["score_weights"]
    score = (
        float(raw.commodity_confidence)
        * float(raw.sector_confidence)
        * float(raw.company_confidence)
        * float(raw.reviewer_confidence)
        * float(weights["directness"].get(directness, 0.0))
        * float(weights["sensitivity"].get(sensitivity, 0.0))
        * float(weights["timeline"].get(timeline, 0.0))
    )
    return round(score, 4)


def calculate_entry_allowed_from(processed_at: datetime, config: dict[str, Any]) -> datetime:
    rules = config["entry_rules"]
    market_open = _parse_time(rules["market_open_time"])
    market_close = _parse_time(rules["market_close_time"])
    delay = int(rules.get("intraday_entry_delay_minutes", 15))

    current = processed_at
    if not is_trading_day(current.date()):
        return datetime.combine(next_trading_day(current.date()), market_open, tzinfo=current.tzinfo)

    open_dt = datetime.combine(current.date(), market_open, tzinfo=current.tzinfo)
    close_dt = datetime.combine(current.date(), market_close, tzinfo=current.tzinfo)
    if current < open_dt:
        return open_dt
    if current <= close_dt:
        delayed_entry = round_up_to_next_5m(current + timedelta(minutes=delay))
        if delayed_entry <= close_dt:
            return delayed_entry
        return datetime.combine(next_trading_day(current.date()), market_open, tzinfo=current.tzinfo)
    return datetime.combine(next_trading_day(current.date()), market_open, tzinfo=current.tzinfo)


def generate_signal_id(raw: ApprovedTradeSignal, ticker: str) -> str:
    published_at = raw.published_at.isoformat()
    published_date = raw.published_at.date().strftime("%Y%m%d")
    direction = _clean_token(raw.expected_stock_direction)
    base = "|".join(
        [
            raw.news_event_id,
            published_at,
            ticker,
            direction,
        ]
    )
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:6].upper()
    parts = [
        "SIG",
        _safe_id(raw.news_event_id),
        published_date,
        _safe_id(ticker),
        _safe_id(direction),
        digest,
    ]
    return "_".join(part for part in parts if part)


def persist_signal_journal(
    signals: list[FinalizedTradeSignal],
    output_dir: Path,
    journal_filename: str = "trade_signal_journal.csv",
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    journal_path = output_dir / journal_filename
    existing_ids = _existing_signal_ids(journal_path)
    new_rows = [signal.to_row() for signal in signals if signal.signal_id not in existing_ids]
    fieldnames = list(FinalizedTradeSignal.__dataclass_fields__.keys())

    if new_rows:
        with journal_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            if journal_path.stat().st_size == 0:
                writer.writeheader()
            writer.writerows(new_rows)

    return {
        "path": str(journal_path),
        "signals_received": len(signals),
        "signals_inserted": len(new_rows),
        "signals_skipped_as_duplicates": len(signals) - len(new_rows),
    }


def ensure_market_timezone(value: datetime, config: dict[str, Any]) -> datetime:
    tz = ZoneInfo(config["entry_rules"].get("market_timezone", "Asia/Kolkata"))
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def is_trading_day(value: date) -> bool:
    return value.weekday() < 5


def next_trading_day(value: date) -> date:
    candidate = value + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def round_up_to_next_5m(value: datetime) -> datetime:
    rounded = value.replace(second=0, microsecond=0)
    remainder = rounded.minute % 5
    if remainder:
        rounded += timedelta(minutes=5 - remainder)
    return rounded


def _existing_signal_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["signal_id"] for row in csv.DictReader(handle) if row.get("signal_id")}


def _parse_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _clean_token(value: str) -> str:
    return str(value or "").strip().lower()


def _safe_id(value: str) -> str:
    return "".join(ch for ch in str(value).upper() if ch.isalnum())[:40]
