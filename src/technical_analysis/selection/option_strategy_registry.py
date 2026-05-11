from __future__ import annotations

from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Callable, Optional, Dict

import pandas as pd

OptionSelectionFunction = Callable[[pd.DataFrame, str, pd.Timestamp], Optional[Dict]]


def _load_option_modules() -> list[ModuleType]:
    this_dir = Path(__file__).parent
    modules: list[ModuleType] = []
    for path in sorted(this_dir.glob("option_selection_*.py")):
        if path.name in {"option_selection_common.py", "option_registry.py"}:
            continue
        mod_name = f"{__package__}.{path.stem}"
        modules.append(import_module(mod_name))
    return modules


def load_option_selection_strategies() -> dict[str, OptionSelectionFunction]:
    registry: dict[str, OptionSelectionFunction] = {}
    for module in _load_option_modules():
        strategy_name = getattr(module, "STRATEGY_NAME", None)
        select_fn = getattr(module, "select", None)
        if isinstance(strategy_name, str) and callable(select_fn):
            registry[strategy_name] = select_fn
    return registry


SELECTION_STRATEGIES = load_option_selection_strategies()

