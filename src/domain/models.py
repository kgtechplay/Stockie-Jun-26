from dataclasses import dataclass
from datetime import date, datetime

@dataclass
class StockInstrument:
    exchange: str
    tradingsymbol: str
    name: str | None
    instrument_token: int
    segment: str | None
    tick_size: float | None
    lot_size: int | None

@dataclass
class OptionInstrument:
    fetch_date: date
    underlying: str
    exchange: str
    tradingsymbol: str
    instrument_token: int
    name: str | None
    strike: float
    expiry: date
    instrument_type: str
    lot_size: int
    tick_size: float | None
    segment: str | None


@dataclass
class OptionSnapshot:
    """
    One raw snapshot from Kite for a given option instrument.

    Maps 1:1 to dbo.OptionSnapshot in SQL:
      - id is DB identity PK (can be None before insert)
    """
    id: int | None            # DB PK; set after insert
    option_instrument_id: int # FK -> OptionInstrument (your DB id)
    snapshot_time: datetime

    # raw data from Kite
    underlying_price: float | None
    last_price: float | None
    bid_price: float | None
    bid_qty: int | None
    ask_price: float | None
    ask_qty: int | None
    volume: int | None
    open_interest: int | None


# -------------------------
# NEW: calculated table
# -------------------------

@dataclass
class OptionSnapshotCalc:
    """
    Calculated analytics (IV + Greeks) for a given snapshot.

    Maps 1:1 to dbo.OptionSnapshotCalc:
      - option_snapshot_id FK -> OptionSnapshot.id
    """
    option_snapshot_id: int   # FK -> OptionSnapshot.id
    implied_volatility: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None


# -------------------------------------------------
# OPTIONAL: read model combining both via a JOIN
# -------------------------------------------------

@dataclass
class OptionData:
    """
    Convenience view used when READING:
    result of joining OptionSnapshot + OptionSnapshotCalc.
    (Not a separate table.)
    """
    option_instrument_id: int
    snapshot_time: datetime

    underlying_price: float | None
    last_price: float | None
    bid_price: float | None
    bid_qty: int | None
    ask_price: float | None
    ask_qty: int | None
    volume: int | None
    open_interest: int | None

    implied_volatility: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
