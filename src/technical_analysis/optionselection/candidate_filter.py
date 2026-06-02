from __future__ import annotations

from .schema import OptionContract, OptionFeatures

PREFERRED_LONG_MIN_DTE = 5
PREFERRED_LONG_MAX_DTE = 21
PREFERRED_SPREAD_MIN_DTE = 7
PREFERRED_SPREAD_MAX_DTE = 30
MAX_LONG_OPTION_SPREAD_PCT = 0.05
MIN_LIQUIDITY_SCORE = 60
MIN_SELL_LEG_LIQUIDITY_SCORE = 50
LONG_OPTION_MIN_DELTA = 0.35
LONG_OPTION_MAX_DELTA = 0.65
SPREAD_BUY_LEG_MIN_DELTA = 0.45
SPREAD_BUY_LEG_MAX_DELTA = 0.65
SPREAD_SELL_LEG_MIN_DELTA = 0.20
SPREAD_SELL_LEG_MAX_DELTA = 0.40
MAX_THETA_BURN_PCT_PER_DAY_HARD = 0.12


def filter_long_call_candidates(
    contracts: list[OptionContract],
    features: dict[str, OptionFeatures],
) -> list[OptionContract]:
    return [
        contract
        for contract in contracts
        if contract.option_type == "CE" and _long_candidate_ok(contract, features, signed_delta=True)
    ]


def filter_long_put_candidates(
    contracts: list[OptionContract],
    features: dict[str, OptionFeatures],
) -> list[OptionContract]:
    return [
        contract
        for contract in contracts
        if contract.option_type == "PE" and _long_candidate_ok(contract, features, signed_delta=False)
    ]


def filter_spread_buy_leg_candidates(
    contracts: list[OptionContract],
    features: dict[str, OptionFeatures],
    option_type: str,
) -> list[OptionContract]:
    output: list[OptionContract] = []
    for contract in contracts:
        f = features.get(contract.tradingsymbol)
        delta = abs(contract.delta) if contract.delta is not None else None
        if (
            contract.option_type == option_type
            and f is not None
            and f.is_tradeable
            and f.liquidity_score >= MIN_LIQUIDITY_SCORE
            and PREFERRED_SPREAD_MIN_DTE <= f.days_to_expiry <= PREFERRED_SPREAD_MAX_DTE
            and delta is not None
            and SPREAD_BUY_LEG_MIN_DELTA <= delta <= SPREAD_BUY_LEG_MAX_DELTA
            and not f.is_iv_outlier
        ):
            output.append(contract)
    return output


def filter_spread_sell_leg_candidates(
    contracts: list[OptionContract],
    features: dict[str, OptionFeatures],
    option_type: str,
    buy_leg: OptionContract,
) -> list[OptionContract]:
    output: list[OptionContract] = []
    for contract in contracts:
        f = features.get(contract.tradingsymbol)
        delta = abs(contract.delta) if contract.delta is not None else None
        valid_strike = contract.strike > buy_leg.strike if option_type == "CE" else contract.strike < buy_leg.strike
        if (
            contract.option_type == option_type
            and contract.expiry == buy_leg.expiry
            and valid_strike
            and f is not None
            and f.is_tradeable
            and f.liquidity_score >= MIN_SELL_LEG_LIQUIDITY_SCORE
            and delta is not None
            and SPREAD_SELL_LEG_MIN_DELTA <= delta <= SPREAD_SELL_LEG_MAX_DELTA
        ):
            output.append(contract)
    return output


def _long_candidate_ok(
    contract: OptionContract,
    features: dict[str, OptionFeatures],
    signed_delta: bool,
) -> bool:
    f = features.get(contract.tradingsymbol)
    if f is None or not f.is_tradeable:
        return False
    delta = contract.delta if signed_delta else abs(contract.delta) if contract.delta is not None else None
    return (
        delta is not None
        and LONG_OPTION_MIN_DELTA <= delta <= LONG_OPTION_MAX_DELTA
        and PREFERRED_LONG_MIN_DTE <= f.days_to_expiry <= PREFERRED_LONG_MAX_DTE
        and (f.spread_pct is None or f.spread_pct <= MAX_LONG_OPTION_SPREAD_PCT)
        and f.liquidity_score >= MIN_LIQUIDITY_SCORE
        and (f.theta_burn_pct_per_day is None or f.theta_burn_pct_per_day <= MAX_THETA_BURN_PCT_PER_DAY_HARD)
        and not f.is_iv_outlier
        and contract.last_price > 0
    )
