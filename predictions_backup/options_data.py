"""Compatibility wrapper for legacy imports.

Options data provider logic moved to:
    src.prediction.providers.options_data_provider
"""

from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.prediction.providers.options_data_provider import *  # noqa: F401,F403
