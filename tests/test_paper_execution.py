from __future__ import annotations

import unittest
from datetime import datetime, time
from zoneinfo import ZoneInfo

from src.execution.paper import resolve_exit_reason


class PaperExecutionTests(unittest.TestCase):
    def test_target_2_takes_priority_after_stop(self) -> None:
        trade = {"target_1_price": 110.0, "target_2_price": 120.0, "stop_loss_price": 90.0}
        now = datetime(2026, 6, 29, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertEqual(resolve_exit_reason(trade, 121.0, now, time(15, 15)), "TARGET_2_HIT")

    def test_stop_loss_exit(self) -> None:
        trade = {"target_1_price": 110.0, "target_2_price": 120.0, "stop_loss_price": 90.0}
        now = datetime(2026, 6, 29, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertEqual(resolve_exit_reason(trade, 89.0, now, time(15, 15)), "STOP_LOSS_HIT")

    def test_time_exit(self) -> None:
        trade = {"target_1_price": 110.0, "target_2_price": 120.0, "stop_loss_price": 90.0}
        now = datetime(2026, 6, 29, 15, 16, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertEqual(resolve_exit_reason(trade, 100.0, now, time(15, 15)), "TIME_EXIT")

    def test_time_exit_can_be_disabled(self) -> None:
        trade = {"target_1_price": 110.0, "target_2_price": 120.0, "stop_loss_price": 90.0}
        now = datetime(2026, 6, 29, 15, 16, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertIsNone(resolve_exit_reason(trade, 100.0, now, None))


if __name__ == "__main__":
    unittest.main()
