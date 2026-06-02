from __future__ import annotations

from .schema import OptionBias, OptionStrategyType

HIGH_IV_RANK = 70
VERY_HIGH_IV_RANK = 85


def choose_option_strategy_type(
    option_bias: OptionBias,
    atm_iv_rank_90d: float | None,
    atm_iv_percentile_90d: float | None,
    expected_move_pct: float | None,
    expected_holding_days: int,
    min_days_to_expiry_available: int | None,
) -> OptionStrategyType:
    _ = atm_iv_percentile_90d, expected_move_pct
    if option_bias == "NEUTRAL":
        return "NO_TRADE"
    if min_days_to_expiry_available is not None and min_days_to_expiry_available <= 1 and expected_holding_days >= 2:
        return "NO_TRADE"

    high_iv = atm_iv_rank_90d is not None and atm_iv_rank_90d >= HIGH_IV_RANK
    very_high_iv = atm_iv_rank_90d is not None and atm_iv_rank_90d >= VERY_HIGH_IV_RANK

    if option_bias == "BULLISH_STRONG":
        return "BULL_CALL_SPREAD" if high_iv or very_high_iv else "LONG_CALL"
    if option_bias == "BULLISH_MODERATE":
        return "BULL_CALL_SPREAD"
    if option_bias == "BEARISH_STRONG":
        return "BEAR_PUT_SPREAD" if high_iv or very_high_iv else "LONG_PUT"
    if option_bias == "BEARISH_MODERATE":
        return "BEAR_PUT_SPREAD"
    return "NO_TRADE"
