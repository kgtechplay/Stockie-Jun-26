from __future__ import annotations

import unittest

import pandas as pd

from backtest.vectorbt_research.strategy_grid import (
    CALL,
    PUT,
    ma_spread_variant,
    rsi_reversion_variant,
)


class VectorBTStrategyGridTests(unittest.TestCase):
    def test_ma_spread_variant_emits_call_and_put(self) -> None:
        df = pd.DataFrame({
            "ma10": [101.0, 99.0, 100.0],
            "ma20": [100.0, 100.0, 100.0],
            "rsi14": [55.0, 45.0, 50.0],
        })
        variant = ma_spread_variant("test", 0.005, 60, 40)

        signal = variant.signal_fn(df)

        self.assertEqual(signal.iloc[0], CALL)
        self.assertEqual(signal.iloc[1], PUT)

    def test_rsi_reversion_variant_emits_edges(self) -> None:
        df = pd.DataFrame({"rsi14": [39.0, 50.0, 61.0]})
        variant = rsi_reversion_variant("rsi", 40, 60)

        signal = variant.signal_fn(df)

        self.assertEqual(signal.iloc[0], CALL)
        self.assertEqual(signal.iloc[2], PUT)


if __name__ == "__main__":
    unittest.main()
