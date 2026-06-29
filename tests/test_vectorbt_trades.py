from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from backtest.vectorbt_trades.runner import build_signal_matrices_from_fills, run_vectorbt_or_fallback


class StockieVectorBTAdapterTest(unittest.TestCase):
    def test_build_signal_matrices_from_actual_fills(self) -> None:
        fills = pd.DataFrame([{
            "trade_id": "2026-06-24_1",
            "entry_time": "2026-06-25 09:15:00",
            "entry_price": 100.0,
            "exit_time": "2026-06-25 10:00:00",
            "exit_price": 106.0,
        }])

        price, entries, exits = build_signal_matrices_from_fills(fills)

        self.assertTrue(entries.loc[pd.Timestamp("2026-06-25 09:15:00"), "2026-06-24_1"])
        self.assertTrue(exits.loc[pd.Timestamp("2026-06-25 10:00:00"), "2026-06-24_1"])
        self.assertEqual(price.loc[pd.Timestamp("2026-06-25 09:15:00"), "2026-06-24_1"], 100.0)
        self.assertEqual(price.loc[pd.Timestamp("2026-06-25 10:00:00"), "2026-06-24_1"], 106.0)

    def test_fallback_replay_returns_trade_metrics(self) -> None:
        idx = pd.to_datetime(["2026-06-25 09:15:00", "2026-06-25 15:15:00"])
        price = pd.DataFrame({"trade": [100.0, 110.0]}, index=idx)
        entries = pd.DataFrame({"trade": [True, False]}, index=idx)
        exits = pd.DataFrame({"trade": [False, True]}, index=idx)

        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "vectorbt":
                raise ImportError("vectorbt intentionally disabled for fallback test")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            trades, metrics, used_vectorbt = run_vectorbt_or_fallback(
                price=price,
                entries=entries,
                exits=exits,
                initial_cash=100_000,
                fees=0.0,
                slippage=0.0,
                closed_trades=pd.DataFrame([{
                    "trade_id": "trade",
                    "entry_price": 100.0,
                    "exit_price": 110.0,
                    "lot_size": 1,
                }]),
            )

        self.assertIn("trades", metrics)
        self.assertEqual(metrics["trades"], 1)
        self.assertFalse(used_vectorbt)
        self.assertEqual(float(trades.iloc[0]["pnl_per_unit"]), 10.0)


if __name__ == "__main__":
    unittest.main()

