# src/services/options_service.py
import logging
from datetime import datetime, timedelta, date
from typing import Tuple, List, Dict, Any, Optional

from src.common.config import Settings, get_settings
from src.data_manager.db.client_factory import get_database_client
from src.data_manager.kite_client import KiteClient
from src.data_manager.kite_option_snapshot_builder import (
    filter_options_for_underlyings,
    build_option_data_snapshot,
)

logger = logging.getLogger(__name__)


def process_underlying_once(tradingsymbol: str, settings: Settings) -> Tuple[int, int]:
    """
    End-to-end pipeline for a single underlying:

    1) Fetch NFO instruments from Kite
    2) Filter options for underlying
    3) Upsert OptionInstrument
    4) Fetch quotes + IV + Greeks
    5) Insert snapshots (OptionSnapshot + OptionSnapshotCalc)

    Returns: (option_contract_count, inserted_snapshot_count)
    """
    kite_client = KiteClient(settings)
    kite_client.authenticate()

    db = get_database_client(settings)
    db.connect()

    # 1) fetch all NFO instruments
    logger.info("Fetching NFO instruments from Kite...")
    instruments_nfo = kite_client.fetch_instruments_nfo()
    logger.info(f"Fetched {len(instruments_nfo)} NFO instruments")

    # 2) filter for this underlying
    logger.info(f"Filtering options for {tradingsymbol}...")
    option_contracts = filter_options_for_underlyings(
        instruments_dump=instruments_nfo,
        underlyings=[tradingsymbol],
    )
    logger.info(f"Found {len(option_contracts)} option contracts for {tradingsymbol}")

    if not option_contracts:
        db.close()
        logger.warning(f"No option contracts found for {tradingsymbol}")
        return 0, 0

    # 3) upsert contracts
    logger.info("Upserting option contracts to database...")
    db.upsert_option_instruments(option_contracts)
    logger.info("Option contracts upserted")

    # 4) map instrument_token -> OptionInstrument.id
    logger.info("Mapping instrument tokens to database IDs...")
    token_to_id = db.get_option_instrument_ids_by_token(
        o.instrument_token for o in option_contracts
    )
    logger.info(f"Mapped {len(token_to_id)} tokens")

    # 5) build snapshots (OptionData in memory)
    logger.info(f"Fetching quotes and calculating IV/Greeks for {len(option_contracts)} contracts...")
    logger.info("This may take a while for large underlyings like NIFTY50...")
    option_data_rows = build_option_data_snapshot(
        kite_client=kite_client,
        option_instruments=option_contracts,
        risk_free_rate=0.07,
    )
    logger.info(f"Built {len(option_data_rows)} option data snapshots")

    # map token -> DB PK
    mapped_rows = []
    for d in option_data_rows:
        token = d.option_instrument_id
        db_id = token_to_id.get(token)
        if db_id is None:
            continue
        d.option_instrument_id = db_id
        mapped_rows.append(d)

    if mapped_rows:
        db.bulk_insert_option_data(mapped_rows)

    db.close()
    return len(option_contracts), len(mapped_rows)


def fetch_option_trend_data(
    option_instrument_id: int,
    days: int = 30,
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """
    Fetch historical trend data for a specific option instrument.
    Returns OHLCV + IV/Greeks for the last N days, formatted for charting.
    """
    if settings is None:
        settings = get_settings()

    db = get_database_client(settings)
    db.connect()
    try:
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)

        option_data_list = db.fetch_option_data(
            option_instrument_ids=[option_instrument_id],
            from_time=from_date,
            to_time=to_date,
        )

        option_info = db.get_option_instrument_by_id(option_instrument_id)
        if not option_info:
            return {
                "option_instrument_id": option_instrument_id,
                "tradingsymbol": "Unknown",
                "strike": 0.0,
                "expiry": "",
                "instrument_type": "",
                "data_points": [],
                "error": "Option instrument not found",
            }

        data_points = sorted(
            [
                {
                    "date": d.snapshot_time.date().isoformat(),
                    "timestamp": d.snapshot_time.isoformat(),
                    "underlying_price": d.underlying_price,
                    "option_price": d.last_price,
                    "implied_volatility": d.implied_volatility,
                    "delta": d.delta,
                    "gamma": d.gamma,
                    "theta": d.theta,
                    "vega": d.vega,
                }
                for d in option_data_list
            ],
            key=lambda x: x["date"],
        )

        expiry = option_info.get("expiry")
        if isinstance(expiry, datetime):
            expiry_str = expiry.date().isoformat()
        elif isinstance(expiry, date):
            expiry_str = expiry.isoformat()
        else:
            expiry_str = str(expiry) if expiry else ""

        return {
            "option_instrument_id": option_instrument_id,
            "tradingsymbol": option_info.get("tradingsymbol", ""),
            "strike": option_info.get("strike", 0.0),
            "expiry": expiry_str,
            "instrument_type": option_info.get("instrument_type", ""),
            "data_points": data_points,
        }
    finally:
        db.close()
