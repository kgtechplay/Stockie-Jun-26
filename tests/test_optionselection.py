from __future__ import annotations

import unittest
from unittest.mock import patch

from src.technical_analysis.optionselection.candidate_filter import (
    filter_long_call_candidates,
    filter_long_put_candidates,
)
from src.technical_analysis.optionselection.option_features import (
    compute_option_features_for_chain,
    compute_spread_pct,
    compute_theta_burn_pct_per_day,
)
from src.technical_analysis.optionselection.option_selector import select_option_strategy
from src.technical_analysis.optionselection.risk import calculate_strategy_risk
from src.technical_analysis.optionselection.schema import OptionContract
from src.technical_analysis.optionselection.scoring import score_option_candidate
from src.technical_analysis.optionselection.strategy_builder import build_strategy_candidates
from src.technical_analysis.optionselection.strategy_rules import choose_option_strategy_type
from src.technical_analysis.optionselection.underlying_view_strength import derive_option_bias
from src.technical_analysis.prediction.schema import UnderlyingView


def view(
    raw_signal: str = "CALL",
    direction: str = "BULLISH",
    score: float = 85,
    stock_regime: str = "TREND_UP",
) -> UnderlyingView:
    return UnderlyingView(
        symbol="NIFTY",
        trade_date="2026-05-15",
        raw_signal=raw_signal,  # type: ignore[arg-type]
        direction=direction,  # type: ignore[arg-type]
        stock_regime=stock_regime,  # type: ignore[arg-type]
        sector_regime="TREND_UP",
        benchmark_regime="TREND_UP",
        primary_strategy="MaTrend_001",
        setup_type="TREND_UP_PULLBACK_LONG",
        strength_score=score,
        confidence="HIGH" if score >= 80 else "MEDIUM",
        expected_move_pct=0.012,
        expected_move_abs=120,
        expected_holding_days=3,
        atr14=100,
        volatility_20d=0.02,
        volume_ratio=1.2,
        relative_strength_vs_sector=0.02,
        relative_strength_vs_benchmark=0.02,
        stock_technical_score=25,
        sector_confirmation_score=15,
        benchmark_confirmation_score=10,
        relative_strength_score=15,
        volume_confirmation_score=7,
        risk_quality_score=10,
        regime_quality_score=15,
        strategy_signals=[],
        reasons=[],
        warnings=[],
        is_option_eligible=True,
        option_bias="BULLISH_STRONG" if score >= 80 else "BULLISH_MODERATE",
    )


def contract(
    symbol: str,
    strike: float,
    option_type: str,
    delta: float,
    expiry: str = "2026-05-29",
    bid: float = 99,
    ask: float = 101,
    last_price: float = 100,
    theta: float = -4,
    iv: float = 0.22,
) -> OptionContract:
    return OptionContract(
        instrument_token=1,
        tradingsymbol=symbol,
        underlying="NIFTY",
        expiry=expiry,
        strike=strike,
        option_type=option_type,  # type: ignore[arg-type]
        last_price=last_price,
        bid=bid,
        ask=ask,
        volume=1000,
        open_interest=5000,
        iv=iv,
        delta=delta,
        gamma=0.001,
        theta=theta,
        vega=5,
    )


class OptionSelectionTests(unittest.TestCase):
    def test_feature_calculations(self) -> None:
        self.assertAlmostEqual(compute_spread_pct(99, 101) or 0, 0.02)
        self.assertAlmostEqual(compute_theta_burn_pct_per_day(-4, 100) or 0, 0.04)

    def test_bias_and_strategy_rules(self) -> None:
        self.assertEqual(derive_option_bias(view()), "BULLISH_STRONG")
        self.assertEqual(derive_option_bias(view(score=60)), "NEUTRAL")
        self.assertEqual(derive_option_bias(view(stock_regime="CHOPPY")), "NEUTRAL")
        self.assertEqual(
            choose_option_strategy_type("BULLISH_STRONG", 40, None, 0.01, 3, 10),
            "LONG_CALL",
        )
        self.assertEqual(
            choose_option_strategy_type("BULLISH_STRONG", 75, None, 0.01, 3, 10),
            "BULL_CALL_SPREAD",
        )
        self.assertEqual(
            choose_option_strategy_type("BEARISH_MODERATE", 40, None, 0.01, 3, 10),
            "BEAR_PUT_SPREAD",
        )

    def test_long_candidate_filters(self) -> None:
        contracts = [
            contract("NIFTY26MAY10000CE", 10000, "CE", 0.52),
            contract("NIFTY26MAY10000PE", 10000, "PE", -0.52),
            contract("NIFTY26MAY10500CE", 10500, "CE", 0.10),
        ]
        features = compute_option_features_for_chain(contracts, 10000, "2026-05-15", [0.15, 0.20, 0.25])
        self.assertEqual(len(filter_long_call_candidates(contracts, features)), 1)
        self.assertEqual(len(filter_long_put_candidates(contracts, features)), 1)

    def test_strategy_build_risk_and_score(self) -> None:
        contracts = [
            contract("NIFTY26MAY10000CE", 10000, "CE", 0.52),
            contract("NIFTY26MAY10200CE", 10200, "CE", 0.30, bid=39, ask=41, last_price=40),
        ]
        features = compute_option_features_for_chain(contracts, 10000, "2026-05-15", [0.15, 0.20, 0.25])
        candidates = build_strategy_candidates("BULL_CALL_SPREAD", contracts, features, view(), 10000)
        self.assertEqual(len(candidates), 1)
        risked = calculate_strategy_risk(candidates[0])
        self.assertGreater(risked.total_delta or 0, 0)
        scored = score_option_candidate(risked, view(), features)
        self.assertGreaterEqual(scored.score, 65)

    def test_selector_no_trade_for_weak_underlying(self) -> None:
        result = select_option_strategy(object(), view(score=60), 10000)
        self.assertEqual(result.selected_strategy.strategy_type, "NO_TRADE")
        self.assertEqual(result.no_trade_reason, "Underlying signal score below threshold")

    def test_selector_selects_long_call(self) -> None:
        contracts = [
            contract("NIFTY26MAY10000CE", 10000, "CE", 0.52),
            contract("NIFTY26MAY10000PE", 10000, "PE", -0.52),
        ]
        with patch(
            "src.technical_analysis.optionselection.option_selector.load_option_chain_with_calcs",
            return_value=contracts,
        ):
            result = select_option_strategy(object(), view(), 10000, atm_iv_history_90d=[0.10, 0.20, 0.40])
        self.assertEqual(result.selected_strategy.strategy_type, "LONG_CALL")
        self.assertIsNone(result.no_trade_reason)


if __name__ == "__main__":
    unittest.main()
