# src/stock_fetcher.py
from typing import Iterable, List

from src.common.models import StockInstrument


def extract_stock_instruments(
    instruments_dump: Iterable[dict],
) -> List[StockInstrument]:
    """
    Filter instruments from NSE/BSE to include:
    - Stocks: segment in ("NSE", "BSE") and instrument_type == "EQ"
    - Indices: segment ends with "INDICES" and exchange in ("NSE", "BSE")
    Maps to StockInstrument dataclass.
    """
    results: List[StockInstrument] = []

    for inst in instruments_dump:
        segment = inst.get("segment", "")
        exchange = inst.get("exchange", "")
        instrument_type = inst.get("instrument_type", "")

        # Check if it's a stock
        is_stock = (segment in ("NSE", "BSE")) and (instrument_type == "EQ")

        # Check if it's an index
        is_index = segment.endswith("INDICES") and (exchange in ("NSE", "BSE"))

        # Include if it's either a stock or an index
        if not (is_stock or is_index):
            continue

        stock = StockInstrument(
            exchange=exchange,
            tradingsymbol=inst.get("tradingsymbol", ""),
            name=inst.get("name"),
            instrument_token=int(inst.get("instrument_token", 0)),
            segment=segment,
            tick_size=float(inst.get("tick_size", 0.0))
            if inst.get("tick_size") is not None
            else None,
            lot_size=int(inst.get("lot_size", 0))
            if inst.get("lot_size") is not None
            else None,
        )
        results.append(stock)

    return results
