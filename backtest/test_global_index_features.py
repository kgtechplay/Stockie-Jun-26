from datetime import date

import pandas as pd

from src.technical_analysis.cascade.global_index_features import build_global_index_features


def test_build_global_index_features_lags_us_and_europe():
    rows = []
    for index_code in ["SP500", "DAX", "NIKKEI225"]:
        rows.append({"index_code": index_code, "trade_date": date(2026, 6, 23), "close_price": 100.0})
        rows.append({"index_code": index_code, "trade_date": date(2026, 6, 24), "close_price": 101.0})
        rows.append({"index_code": index_code, "trade_date": date(2026, 6, 25), "close_price": 102.0})

    features = build_global_index_features(pd.DataFrame(rows))
    row = features[features["trade_date"] == pd.Timestamp("2026-06-25")].iloc[0]

    assert round(row["global_ret_SP500"], 6) == round(0.01, 6)
    assert round(row["global_ret_DAX"], 6) == round(0.01, 6)
    assert round(row["global_ret_NIKKEI225"], 6) == round((102.0 - 101.0) / 101.0, 6)


def test_build_global_index_features_adds_risk_tone_columns():
    rows = []
    for index_code in ["NIKKEI225", "HANG_SENG", "SHANGHAI", "KOSPI", "ASX200"]:
        rows.append({"index_code": index_code, "trade_date": date(2026, 6, 24), "close_price": 100.0})
        rows.append({"index_code": index_code, "trade_date": date(2026, 6, 25), "close_price": 101.0})

    features = build_global_index_features(pd.DataFrame(rows))
    latest = features[features["trade_date"] == pd.Timestamp("2026-06-25")].iloc[0]

    assert latest["global_positive_count"] == 5
    assert latest["global_breadth"] == 1.0
    assert latest["global_risk_on"] == 1
    assert latest["global_risk_off"] == 0