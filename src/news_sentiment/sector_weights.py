from __future__ import annotations

from datetime import datetime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from src.news_sentiment.config import (
    NIFTY50_CONSTITUENT_WEIGHTS_STORE,
    NIFTY50_SECTOR_DEFINITIONS,
    NIFTY50_SECTOR_WEIGHTS,
    NIFTY50_SECTOR_WEIGHTS_STORE,
)

IST = ZoneInfo("Asia/Kolkata")
NSE_HOME = "https://www.nseindia.com/"
NSE_NIFTY50_CONSTITUENTS_URL = "https://nseindia.com/content/indices/ind_nifty50list.csv"

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": NSE_HOME,
}

def fetch_nifty50_constituent_weights(timeout_seconds: int = 20) -> pd.DataFrame:
    session = requests.Session()
    session.get(NSE_HOME, headers=NSE_HEADERS, timeout=timeout_seconds)
    response = session.get(NSE_NIFTY50_CONSTITUENTS_URL, headers=NSE_HEADERS, timeout=timeout_seconds)
    response.raise_for_status()

    df = pd.read_csv(StringIO(response.text))
    required = {"Company Name", "Industry", "Symbol", "Weightage"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            "NSE NIFTY50 CSV did not include expected column(s): "
            + ", ".join(sorted(missing))
        )

    out = df.copy()
    out["Weightage"] = pd.to_numeric(out["Weightage"], errors="coerce")
    out = out.dropna(subset=["Weightage"])
    if out["Weightage"].max() > 1.0:
        out["weight"] = out["Weightage"] / 100.0
    else:
        out["weight"] = out["Weightage"]
    out["sector_key"] = out["Industry"].map(map_nse_industry_to_sector)
    out["fetched_at"] = datetime.now(IST).isoformat()
    return out


def refresh_nifty50_sector_weights(timeout_seconds: int = 20) -> pd.DataFrame:
    constituents = fetch_nifty50_constituent_weights(timeout_seconds=timeout_seconds)
    NIFTY50_CONSTITUENT_WEIGHTS_STORE.parent.mkdir(parents=True, exist_ok=True)
    constituents.to_csv(NIFTY50_CONSTITUENT_WEIGHTS_STORE, index=False)

    labels = {definition.key: definition.label for definition in NIFTY50_SECTOR_DEFINITIONS}
    grouped = (
        constituents.groupby("sector_key", as_index=False)
        .agg(weight=("weight", "sum"), constituent_count=("Symbol", "count"))
        .sort_values("weight", ascending=False)
        .reset_index(drop=True)
    )
    grouped["label"] = grouped["sector_key"].map(labels).fillna(grouped["sector_key"])
    grouped["weight_pct"] = grouped["weight"] * 100.0
    grouped["source"] = NSE_NIFTY50_CONSTITUENTS_URL
    grouped["fetched_at"] = datetime.now(IST).isoformat()
    grouped = grouped[
        ["sector_key", "label", "weight", "weight_pct", "constituent_count", "source", "fetched_at"]
    ]
    grouped.to_csv(NIFTY50_SECTOR_WEIGHTS_STORE, index=False)
    return grouped


def load_nifty50_sector_weights(path=NIFTY50_SECTOR_WEIGHTS_STORE) -> dict[str, float]:
    if path.exists():
        df = pd.read_csv(path)
        if {"sector_key", "weight"}.issubset(df.columns):
            weights = {
                str(row["sector_key"]): float(row["weight"])
                for _, row in df.iterrows()
                if pd.notna(row.get("sector_key")) and pd.notna(row.get("weight"))
            }
            if weights:
                weights["broad_market"] = 1.0
                return weights
    return dict(NIFTY50_SECTOR_WEIGHTS)


def build_sector_weights_from_component_csv(
    component_csv: Path,
    output_path: Path = NIFTY50_SECTOR_WEIGHTS_STORE,
) -> pd.DataFrame:
    df = pd.read_csv(component_csv)
    required = {"symbol", "market_cap", "predicted_sector"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            "Component CSV did not include expected column(s): "
            + ", ".join(sorted(missing))
        )

    out = df.copy()
    out["market_cap_value"] = out["market_cap"].map(_parse_market_cap)
    out = out.dropna(subset=["market_cap_value", "predicted_sector"])
    total_market_cap = float(out["market_cap_value"].sum())
    if total_market_cap <= 0:
        raise ValueError("Component CSV did not include positive market-cap values.")

    labels = {definition.key: definition.label for definition in NIFTY50_SECTOR_DEFINITIONS}
    grouped = (
        out.groupby("predicted_sector", as_index=False)
        .agg(weight_value=("market_cap_value", "sum"), constituent_count=("symbol", "count"))
        .rename(columns={"predicted_sector": "sector_key"})
        .sort_values("weight_value", ascending=False)
        .reset_index(drop=True)
    )
    grouped["weight"] = grouped["weight_value"] / total_market_cap
    grouped["label"] = grouped["sector_key"].map(labels).fillna(grouped["sector_key"])
    grouped["weight_pct"] = grouped["weight"] * 100.0
    grouped["source"] = str(component_csv)
    grouped["fetched_at"] = datetime.now(IST).isoformat()
    grouped = grouped[
        ["sector_key", "label", "weight", "weight_pct", "constituent_count", "source", "fetched_at"]
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    grouped.to_csv(output_path, index=False)
    return grouped


def _parse_market_cap(value: object) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    multiplier = 1.0
    suffix = text[-1].upper()
    if suffix == "T":
        multiplier = 1_000_000_000_000.0
        text = text[:-1]
    elif suffix == "B":
        multiplier = 1_000_000_000.0
        text = text[:-1]
    elif suffix == "M":
        multiplier = 1_000_000.0
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def map_nse_industry_to_sector(industry: str) -> str:
    text = str(industry or "").lower()
    if any(term in text for term in ["financial", "bank", "finance", "insurance"]):
        return "financial_services"
    if any(term in text for term in ["information technology", "software", "technology"]):
        return "information_technology"
    if any(term in text for term in ["oil", "gas", "petroleum", "energy"]):
        return "oil_gas"
    if any(term in text for term in ["fast moving consumer", "fmcg", "consumer goods"]):
        return "fmcg"
    if any(term in text for term in ["automobile", "auto", "vehicle"]):
        return "automobile"
    if any(term in text for term in ["healthcare", "pharma", "pharmaceutical", "hospital"]):
        return "healthcare"
    if any(term in text for term in ["metal", "mining", "steel", "aluminium", "aluminum"]):
        return "metals"
    if any(term in text for term in ["consumer durables", "durables"]):
        return "consumer_durables"
    if "telecom" in text:
        return "telecom"
    if any(term in text for term in ["construction", "infrastructure", "cement", "capital goods"]):
        return "construction"
    if any(term in text for term in ["power", "utilities", "electricity"]):
        return "power"
    if any(term in text for term in ["services", "logistics", "transport", "aviation", "ports"]):
        return "services"
    if any(term in text for term in ["realty", "real estate"]):
        return "realty"
    return "broad_market"