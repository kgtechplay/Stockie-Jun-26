from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.news_analysis.azure_agent_client import AzureAgentClient, AzureAgentError
from src.news_analysis.dailyNews.output_schema import DailyNewsOutput, NewsArticle
from src.news_analysis.dailyNews.output_schema import CommodityImpact


@dataclass
class DailyNewsAgent:
    """dailyNews agent backed by Azure when configured."""

    client: AzureAgentClient = field(default_factory=AzureAgentClient)
    agent_dir: Path = Path(__file__).resolve().parent

    def run(
        self,
        reference_date: date,
        as_of: datetime | None = None,
        article: NewsArticle | None = None,
    ) -> DailyNewsOutput:
        timestamp = as_of or datetime.now()
        news_article = article or NewsArticle(
            title=f"Daily news scan for {reference_date.isoformat()}",
            source="placeholder",
            published_at=timestamp,
        )
        if self.client.is_configured:
            try:
                payload = {
                    "reference_date": reference_date.isoformat(),
                    "as_of": timestamp.isoformat(),
                    "article": news_article,
                }
                response = self.client.run_json(
                    agent_name="dailyNews",
                    agent_definition_path=self.agent_dir / "agent_definition.md",
                    config_path=self.agent_dir / "config.yaml",
                    output_schema_path=self.agent_dir / "output_schema.py",
                    payload=payload,
                )
                return _daily_news_from_dict(response, reference_date=reference_date, as_of=timestamp)
            except AzureAgentError:
                raise

        return DailyNewsOutput(
            event_id=f"EVT_{reference_date.strftime('%Y%m%d')}_001",
            reference_date=reference_date,
            as_of=timestamp,
            article=news_article,
            event_summary="Placeholder output. Connect live news scraping or LLM extraction here.",
            event_type="other",
            commodities=[],
            overall_confidence=0.0,
            requires_human_review=True,
        )


def _daily_news_from_dict(data: dict[str, Any], reference_date: date, as_of: datetime) -> DailyNewsOutput:
    article_data = data.get("article") or {}
    article = NewsArticle(
        title=str(article_data.get("title") or ""),
        source=str(article_data.get("source") or ""),
        published_at=_parse_datetime(article_data.get("published_at"), as_of),
        region=article_data.get("region"),
        url=article_data.get("url"),
    )
    commodities = [
        CommodityImpact(
            commodity=str(item.get("commodity") or ""),
            commodity_group=str(item.get("commodity_group") or "other"),
            impact_type=str(item.get("impact_type") or "indirect"),
            expected_price_direction=str(item.get("expected_price_direction") or "uncertain"),
            impact_mechanism=list(item.get("impact_mechanism") or []),
            timeline=str(item.get("timeline") or "uncertain"),
            reasoning=str(item.get("reasoning") or ""),
            evidence_from_article=list(item.get("evidence_from_article") or []),
            alternate_view=item.get("alternate_view"),
            confidence=float(item.get("confidence") or 0.0),
        )
        for item in data.get("commodities", [])
        if isinstance(item, dict)
    ]
    return DailyNewsOutput(
        event_id=str(data.get("event_id") or f"EVT_{reference_date.strftime('%Y%m%d')}_001"),
        reference_date=reference_date,
        as_of=as_of,
        article=article,
        event_summary=str(data.get("event_summary") or ""),
        event_type=str(data.get("event_type") or "other"),
        commodities=commodities,
        overall_confidence=float(data.get("overall_confidence") or 0.0),
        requires_human_review=bool(data.get("requires_human_review", True)),
    )


def _parse_datetime(value: Any, default: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return default
    return default
