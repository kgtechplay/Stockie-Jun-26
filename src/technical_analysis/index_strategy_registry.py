from __future__ import annotations

from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Callable

from .index_prediction_common import DEFAULT_LOOKBACK_DAYS, detect_regime

IndexPredictionFunction = Callable[[object], str]


def _load_index_modules() -> list[ModuleType]:
    this_dir = Path(__file__).parent
    modules: list[ModuleType] = []
    for path in sorted(this_dir.glob("index_prediction_*.py")):
        if path.name in {"index_prediction_common.py", "index_registry.py"}:
            continue
        mod_name = f"{__package__}.{path.stem}"
        modules.append(import_module(mod_name))
    return modules


def load_index_prediction_strategies() -> dict[str, IndexPredictionFunction]:
    registry: dict[str, IndexPredictionFunction] = {}
    for module in _load_index_modules():
        strategy_name = getattr(module, "STRATEGY_NAME", None)
        predict_fn = getattr(module, "predict", None)
        if isinstance(strategy_name, str) and callable(predict_fn):
            registry[strategy_name] = predict_fn
    return registry


PREDICTION_STRATEGIES = load_index_prediction_strategies()
