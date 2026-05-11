from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.news_analysis.azure_agent_client import AzureAgentClient
from src.news_analysis.impactList.output_schema import ImpactListOutput
from src.news_analysis.reviewList.output_schema import (
    ApprovedTradeSignal,
    ReviewedSectorImpact,
    ReviewListOutput,
    SignalStock,
)


@dataclass
class ReviewListAgent:
    """reviewList agent backed by Azure when configured."""

    client: AzureAgentClient = field(default_factory=AzureAgentClient)
    agent_dir: Path = Path(__file__).resolve().parent

    def run(
        self,
        impact_list_output: ImpactListOutput,
        as_of: datetime | None = None,
    ) -> ReviewListOutput:
        timestamp = as_of or impact_list_output.as_of
        if self.client.is_configured:
            payload = {
                "reference_date": impact_list_output.reference_date.isoformat(),
                "as_of": timestamp.isoformat(),
                "impact_list_output": impact_list_output,
                "processed_at": timestamp.isoformat(),
            }
            response = self.client.run_json(
                agent_name="reviewList",
                agent_definition_path=self.agent_dir / "agent_definition.md",
                config_path=self.agent_dir / "config.yaml",
                output_schema_path=self.agent_dir / "output_schema.py",
                payload=payload,
            )
            return _review_list_from_dict(response, impact_list_output=impact_list_output, as_of=timestamp)

        return ReviewListOutput(
            reference_date=impact_list_output.reference_date,
            as_of=timestamp,
            event_id=impact_list_output.event_id,
            review_summary="Placeholder output. Connect LLM review to produce approved_trade_signals.",
            overall_quality_score=0.0,
            reviewed_sector_impacts=[],
            approved_trade_signals=[],
            final_recommendation="reject_and_rerun",
        )


def _review_list_from_dict(
    data: dict[str, Any],
    impact_list_output: ImpactListOutput,
    as_of: datetime,
) -> ReviewListOutput:
    reviewed = [
        ReviewedSectorImpact(
            sector=str(item.get("sector") or ""),
            review_action=str(item.get("review_action") or "remove"),
            final_score=float(item.get("final_score") or 0.0),
            review_notes=item.get("review_notes"),
            checks=list(item.get("checks") or []),
        )
        for item in data.get("reviewed_sector_impacts", [])
        if isinstance(item, dict)
    ]
    signals = [
        _approved_signal_from_dict(item, default_event_id=impact_list_output.event_id, default_processed_at=as_of)
        for item in data.get("approved_trade_signals", [])
        if isinstance(item, dict)
    ]
    return ReviewListOutput(
        reference_date=impact_list_output.reference_date,
        as_of=as_of,
        event_id=str(data.get("event_id") or impact_list_output.event_id),
        review_summary=str(data.get("review_summary") or ""),
        overall_quality_score=float(data.get("overall_quality_score") or 0.0),
        reviewed_sector_impacts=reviewed,
        missing_sector_impacts=list(data.get("missing_sector_impacts") or []),
        removed_or_flagged_items=list(data.get("removed_or_flagged_items") or []),
        approved_trade_signals=signals,
        final_recommendation=str(data.get("final_recommendation") or "reject_and_rerun"),
    )


def _approved_signal_from_dict(
    item: dict[str, Any],
    default_event_id: str,
    default_processed_at: datetime,
) -> ApprovedTradeSignal:
    stock_data = item.get("stock") or {}
    return ApprovedTradeSignal(
        signal_id=item.get("signal_id"),
        news_event_id=str(item.get("news_event_id") or default_event_id),
        published_at=_parse_datetime(item.get("published_at"), default_processed_at),
        processed_at=_parse_datetime(item.get("processed_at"), default_processed_at),
        commodity=str(item.get("commodity") or ""),
        commodity_direction=str(item.get("commodity_direction") or "uncertain"),
        commodity_confidence=float(item.get("commodity_confidence") or 0.0),
        sector=str(item.get("sector") or ""),
        sub_sector=item.get("sub_sector"),
        stock=SignalStock(
            company_name=str(stock_data.get("company_name") or ""),
            ticker=stock_data.get("ticker"),
            exchange=stock_data.get("exchange") or "NSE",
        ),
        expected_stock_direction=str(item.get("expected_stock_direction") or "uncertain"),
        directness=str(item.get("directness") or "indirect"),
        sensitivity=str(item.get("sensitivity") or "low"),
        timeline_bucket=str(item.get("timeline_bucket") or "uncertain"),
        sector_confidence=float(item.get("sector_confidence") or 0.0),
        company_confidence=float(item.get("company_confidence") or 0.0),
        reviewer_confidence=float(item.get("reviewer_confidence") or 0.0),
        final_trade_score=item.get("final_trade_score"),
        entry_allowed_from=_parse_optional_datetime(item.get("entry_allowed_from")),
        suggested_max_holding_days=item.get("suggested_max_holding_days"),
        signal_status=str(item.get("signal_status") or "monitor_only"),
        impact_channel=str(item.get("impact_channel") or "other"),
        reasoning=str(item.get("reasoning") or ""),
        risks_to_thesis=list(item.get("risks_to_thesis") or []),
        invalidation_triggers=list(item.get("invalidation_triggers") or []),
    )


def _parse_datetime(value: Any, default: datetime) -> datetime:
    parsed = _parse_optional_datetime(value)
    return parsed or default


def _parse_optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
