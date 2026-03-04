from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any

from src.prediction.contracts import EventItem


def _next_weekday(d: datetime, weekday: int) -> datetime:
    days_ahead = (weekday - d.weekday()) % 7
    return d + timedelta(days=days_ahead)


def _business_days_between(start: datetime, end: datetime) -> int:
    if end.date() <= start.date():
        return 0

    count = 0
    cur = start.date()
    while cur < end.date():
        cur = cur + timedelta(days=1)
        if cur.weekday() < 5:
            count += 1
    return count


class EventCalendarAgent:
    """Deterministic event calendar agent (MVP)."""

    def get_upcoming_events(self, instrument: str, as_of: datetime) -> list[EventItem]:
        events: list[EventItem] = []

        expiry_dt = _next_weekday(as_of, 3)  # Thursday weekly expiry approximation
        days_to_expiry = _business_days_between(as_of, expiry_dt)

        if days_to_expiry <= 3:
            risk_level = "HIGH" if days_to_expiry <= 1 else "MEDIUM"
            events.append(
                EventItem(
                    name=f"Weekly expiry window ({instrument})",
                    event_type="EXPIRY",
                    start_time=expiry_dt.replace(hour=9, minute=15, second=0, microsecond=0),
                    end_time=expiry_dt.replace(hour=15, minute=30, second=0, microsecond=0),
                    risk_level=risk_level,
                    expected_volatility=0.7 if risk_level == "HIGH" else 0.45,
                    notes=f"{days_to_expiry} trading day(s) to Thursday expiry",
                )
            )

        events.extend(self._load_manual_events())
        return events

    def _load_manual_events(self) -> list[EventItem]:
        raw = os.getenv("MANUAL_EVENTS_JSON", "").strip()
        if not raw:
            return []

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []

        if not isinstance(parsed, list):
            return []

        out: list[EventItem] = []
        for row in parsed:
            if not isinstance(row, dict):
                continue
            out.append(self._event_from_dict(row))
        return out

    @staticmethod
    def _event_from_dict(data: dict[str, Any]) -> EventItem:
        return EventItem(
            name=str(data.get("name", "Manual event")),
            event_type=str(data.get("event_type", "UNKNOWN")),
            start_time=_parse_dt(data.get("start_time")),
            end_time=_parse_dt(data.get("end_time")),
            risk_level=str(data.get("risk_level", "LOW")),
            expected_volatility=_parse_float(data.get("expected_volatility")),
            notes=str(data.get("notes")) if data.get("notes") is not None else None,
        )


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

