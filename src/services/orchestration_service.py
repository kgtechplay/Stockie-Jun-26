from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.news_analysis.dailyNews.agent import DailyNewsAgent
from src.news_analysis.impactList.agent import ImpactListAgent
from src.news_analysis.reviewList.agent import ReviewListAgent
from src.news_analysis.signal_normalizer import (
    FinalizedTradeSignal,
    normalize_review_signals,
    persist_signal_journal,
)
from src.backtest.news_underlying_backtest import NewsBacktestRequest, run_news_underlying_backtest
from src.services.backfill_service import BackfillRequest, BackfillService
from src.services.sector_expansion_service import SectorExpansionService


@dataclass
class OrchestrationService:
    """
    Coordinates the news-to-signal pipeline.

    Flow:
      1. dailyNews extracts commodity impacts.
      2. impactList maps commodity impacts into sectors/stocks.
      3. reviewList emits raw approved_trade_signals.
      4. signal_normalizer validates, scores, timestamps, and IDs signals.
      5. finalized signals are persisted to the trade signal journal.
      6. sector_expansion_service ensures relevant sector stocks are watched.
      7. backfill_service fills required data for new signal symbols.
      8. news_underlying_backtest evaluates approved signals when data exists.
    """

    daily_news_agent: DailyNewsAgent
    impact_list_agent: ImpactListAgent
    review_list_agent: ReviewListAgent
    sector_expansion_service: SectorExpansionService
    backfill_service: BackfillService
    output_dir: Path
    review_config_path: Path
    backfill_days: int = 90

    @classmethod
    def default(cls) -> "OrchestrationService":
        project_root = Path(__file__).resolve().parents[2]
        return cls(
            daily_news_agent=DailyNewsAgent(),
            impact_list_agent=ImpactListAgent(),
            review_list_agent=ReviewListAgent(),
            sector_expansion_service=SectorExpansionService(),
            backfill_service=BackfillService(),
            output_dir=project_root / "output",
            review_config_path=project_root / "src" / "news_analysis" / "reviewList" / "config.yaml",
        )

    def run(
        self,
        reference_date: date | None = None,
        as_of: datetime | None = None,
        run_backtest: bool = True,
    ) -> dict[str, Any]:
        ref_date = reference_date or datetime.now().date()
        processed_at = as_of or datetime.now()

        news_output = self.daily_news_agent.run(reference_date=ref_date, as_of=processed_at)
        impact_output = self.impact_list_agent.run(news_output, as_of=processed_at)
        review_output = self.review_list_agent.run(impact_output, as_of=processed_at)

        finalized_signals = normalize_review_signals(
            review_output=review_output,
            config_path=self.review_config_path,
        )
        journal_result = persist_signal_journal(finalized_signals, self.output_dir)

        sector_expansion_result = self._expand_sectors_from_signals(finalized_signals)
        backfill_result = self._run_backfill_for_signals(
            signals=finalized_signals,
            reference_date=review_output.reference_date,
        )
        backtest_result = self._run_news_backtest(journal_result, run_backtest=run_backtest)

        return {
            "reference_date": review_output.reference_date.isoformat(),
            "processed_at": processed_at.isoformat(),
            "dailyNews": news_output,
            "impactList": impact_output,
            "reviewList": review_output,
            "signals": {
                "finalized_count": len(finalized_signals),
                "approved_count": len([s for s in finalized_signals if s.signal_status == "approved"]),
                "monitor_only_count": len([s for s in finalized_signals if s.signal_status == "monitor_only"]),
                "tickers": sorted({s.ticker for s in finalized_signals}),
            },
            "signalJournal": journal_result,
            "sectorExpansion": sector_expansion_result,
            "backfill": backfill_result,
            "backtest": backtest_result,
        }

    def _expand_sectors_from_signals(self, signals: list[FinalizedTradeSignal]) -> dict[str, Any]:
        sectors = sorted({signal.sector for signal in signals if signal.sector})
        if not sectors:
            return {
                "triggered": False,
                "reason": "No finalized signal sectors to expand.",
                "sectors": [],
                "symbols": [],
                "new_symbols": [],
            }
        return self.sector_expansion_service.expand_sectors(sectors)

    def _run_backfill_for_signals(
        self,
        signals: list[FinalizedTradeSignal],
        reference_date: date,
    ) -> dict[str, Any]:
        symbols = sorted({signal.ticker for signal in signals if signal.ticker})
        if not symbols:
            return {
                "triggered": False,
                "reason": "No finalized signal tickers to backfill.",
                "underlyings": [],
            }

        start_date = reference_date - timedelta(days=self.backfill_days)
        end_date = reference_date - timedelta(days=1)
        if start_date > end_date:
            return {
                "triggered": False,
                "reason": "Invalid backfill window.",
                "underlyings": symbols,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            }

        try:
            result = self.backfill_service.run_backfill(
                BackfillRequest(
                    start_date=start_date,
                    end_date=end_date,
                    underlyings=symbols,
                )
            )
            return {"triggered": True, **result}
        except Exception as exc:
            return {
                "triggered": False,
                "reason": str(exc),
                "underlyings": symbols,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            }

    def _run_news_backtest(self, journal_result: dict[str, Any], run_backtest: bool) -> dict[str, Any]:
        if not run_backtest:
            return {"triggered": False, "reason": "Backtest disabled for this run."}

        journal_path = journal_result.get("path")
        if not journal_path:
            return {"triggered": False, "reason": "Signal journal path was not returned."}

        try:
            result = run_news_underlying_backtest(
                NewsBacktestRequest(
                    signal_journal_file=journal_path,
                    output_dir=self.output_dir,
                )
            )
            return {"triggered": True, **result}
        except Exception as exc:
            return {
                "triggered": False,
                "reason": str(exc),
                "signal_journal_file": journal_path,
            }
