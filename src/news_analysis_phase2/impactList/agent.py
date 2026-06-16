from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.news_analysis_phase2.azure_agent_client import AzureAgentClient
from src.news_analysis_phase2.dailyNews.output_schema import DailyNewsOutput
from src.news_analysis_phase2.impactList.output_schema import ImpactedSector, ImpactedStock, ImpactListOutput


@dataclass
class ImpactListAgent:
    """impactList agent backed by Azure when configured."""

    client: AzureAgentClient = field(default_factory=AzureAgentClient)
    agent_dir: Path = Path(__file__).resolve().parent

    def run(
        self,
        daily_news_output: DailyNewsOutput,
        as_of: datetime | None = None,
    ) -> ImpactListOutput:
        timestamp = as_of or daily_news_output.as_of
        if self.client.is_configured:
            payload = {
                "reference_date": daily_news_output.reference_date.isoformat(),
                "as_of": timestamp.isoformat(),
                "daily_news_output": daily_news_output,
            }
            response = self.client.run_json(
                agent_name="impactList",
                agent_definition_path=self.agent_dir / "agent_definition.md",
                config_path=self.agent_dir / "config.yaml",
                output_schema_path=self.agent_dir / "output_schema.py",
                payload=payload,
            )
            return _impact_list_from_dict(response, daily_news_output=daily_news_output, as_of=timestamp)

        sectors: list[ImpactedSector] = []
        for rank, commodity in enumerate(daily_news_output.commodities, start=1):
            sectors.append(
                ImpactedSector(
                    rank=rank,
                    sector="UNMAPPED",
                    commodity=commodity.commodity,
                    commodity_direction=commodity.expected_price_direction,
                    impact_direction="uncertain",
                    directness=commodity.impact_type,
                    sensitivity="low",
                    timeline_bucket=commodity.timeline,
                    sector_confidence=commodity.confidence,
                    rationale=commodity.reasoning,
                    source_event_id=daily_news_output.event_id,
                    source_headlines=[daily_news_output.article.title],
                    stocks=[],
                )
            )

        return ImpactListOutput(
            reference_date=daily_news_output.reference_date,
            as_of=timestamp,
            event_id=daily_news_output.event_id,
            impacted_sectors=sectors,
        )


def _impact_list_from_dict(
    data: dict[str, Any],
    daily_news_output: DailyNewsOutput,
    as_of: datetime,
) -> ImpactListOutput:
    sectors: list[ImpactedSector] = []
    for idx, item in enumerate(data.get("impacted_sectors", []), start=1):
        if not isinstance(item, dict):
            continue
        stocks = [
            ImpactedStock(
                company_name=str(stock.get("company_name") or ""),
                ticker=stock.get("ticker"),
                exchange=stock.get("exchange") or "NSE",
                expected_stock_direction=str(stock.get("expected_stock_direction") or "uncertain"),
                company_confidence=float(stock.get("company_confidence") or 0.0),
                reasoning=str(stock.get("reasoning") or ""),
            )
            for stock in item.get("stocks", [])
            if isinstance(stock, dict)
        ]
        sectors.append(
            ImpactedSector(
                rank=int(item.get("rank") or idx),
                sector=str(item.get("sector") or "UNMAPPED"),
                commodity=str(item.get("commodity") or ""),
                commodity_direction=str(item.get("commodity_direction") or "uncertain"),
                impact_direction=str(item.get("impact_direction") or "uncertain"),
                directness=str(item.get("directness") or "indirect"),
                sensitivity=str(item.get("sensitivity") or "low"),
                timeline_bucket=str(item.get("timeline_bucket") or "uncertain"),
                sector_confidence=float(item.get("sector_confidence") or 0.0),
                rationale=str(item.get("rationale") or ""),
                source_event_id=str(item.get("source_event_id") or daily_news_output.event_id),
                source_headlines=list(item.get("source_headlines") or []),
                stocks=stocks,
            )
        )
    return ImpactListOutput(
        reference_date=daily_news_output.reference_date,
        as_of=as_of,
        event_id=str(data.get("event_id") or daily_news_output.event_id),
        impacted_sectors=sectors,
    )
