"""Compatibility wrapper for legacy CLI/import path.

Index backtest logic moved to:
    src.backtest.index_backtest
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.backtest.index_backtest import *  # noqa: F401,F403


if __name__ == "__main__":
    runpy.run_module("src.backtest.index_backtest", run_name="__main__")
