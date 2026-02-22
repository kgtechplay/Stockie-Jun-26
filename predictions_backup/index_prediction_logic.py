"""Compatibility wrapper for legacy imports.

The strategy implementation now lives at:
    src.prediction.technical.strategies

This module re-exports all strategy symbols to keep existing scripts/imports stable.
"""

from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.prediction.technical.strategies import *  # noqa: F401,F403
