# src/options_service.py
import logging
from typing import Tuple, List

from src.core.config import Settings
from src.data.db_client import AzureSqlClient
from src.integrations.kite_client import KiteClient
from src.fetchers.option_fetcher import (
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

    db = AzureSqlClient(settings)
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


