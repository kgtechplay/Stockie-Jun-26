# src/trend_service.py
"""
Service to fetch historical trend data for option instruments.
Returns data formatted for timeline graphs.
"""
import logging
from datetime import datetime, timedelta, date
from typing import List, Dict, Any, Optional

from src.core.config import Settings
from src.data.db_client import AzureSqlClient

logger = logging.getLogger(__name__)


def fetch_option_trend_data(
    option_instrument_id: int,
    days: int = 30,
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """
    Fetch historical trend data for a specific option instrument.
    
    Returns data for the last N days including:
    - Underlying prices (per day)
    - Option prices (per day)
    - IV and Greeks (delta, gamma, theta, vega)
    
    Args:
        option_instrument_id: Database ID of the option instrument
        days: Number of days of history to fetch (default: 30)
        settings: Settings object (if None, will be loaded)
    
    Returns:
        Dictionary with trend data formatted for charts:
        {
            "option_instrument_id": int,
            "tradingsymbol": str,
            "strike": float,
            "expiry": str,
            "instrument_type": str,
            "data_points": [
                {
                    "date": "YYYY-MM-DD",
                    "timestamp": "ISO datetime string",
                    "underlying_price": float | null,
                    "option_price": float | null,
                    "implied_volatility": float | null,
                    "delta": float | null,
                    "gamma": float | null,
                    "theta": float | null,
                    "vega": float | null,
                },
                ...
            ]
        }
    """
    if settings is None:
        from src.core.config import get_settings
        settings = get_settings()
    
    db = AzureSqlClient(settings)
    db.connect()
    
    try:
        # Calculate date range
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)
        
        logger.info(f"Fetching trend data for option_instrument_id={option_instrument_id} from {from_date.date()} to {to_date.date()}")
        
        # Fetch historical data for this specific option instrument
        option_data_list = db.fetch_option_data(
            option_instrument_ids=[option_instrument_id],
            from_time=from_date,
            to_time=to_date,
        )
        
        logger.info(f"Fetched {len(option_data_list)} data points")
        
        # Get option instrument details
        option_info = db.get_option_instrument_by_id(option_instrument_id)
        if not option_info:
            logger.warning(f"Option instrument {option_instrument_id} not found")
            return {
                "option_instrument_id": option_instrument_id,
                "tradingsymbol": "Unknown",
                "strike": 0.0,
                "expiry": "",
                "instrument_type": "",
                "data_points": [],
                "error": "Option instrument not found",
            }
        
        # Format data points for charting
        data_points = []
        for data in option_data_list:
            snapshot_date = data.snapshot_time
            data_points.append({
                "date": snapshot_date.date().isoformat(),
                "timestamp": snapshot_date.isoformat(),
                "underlying_price": data.underlying_price,
                "option_price": data.last_price,
                "implied_volatility": data.implied_volatility,
                "delta": data.delta,
                "gamma": data.gamma,
                "theta": data.theta,
                "vega": data.vega,
            })
        
        # Sort by date
        data_points.sort(key=lambda x: x["date"])
        
        expiry = option_info.get("expiry")
        expiry_str = ""
        if expiry:
            if isinstance(expiry, date):
                expiry_str = expiry.isoformat()
            elif isinstance(expiry, datetime):
                expiry_str = expiry.date().isoformat()
            elif isinstance(expiry, str):
                expiry_str = expiry
        
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



