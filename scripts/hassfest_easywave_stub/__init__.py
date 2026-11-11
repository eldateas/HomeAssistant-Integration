"""Expose Easywave feature enums as ``homeassistant.components.easywave``.

Hassfest resolves ``supported_features`` in ``triggers.yaml`` via
``importlib.import_module("homeassistant.components.<domain>")``. Easywave is
not in Core yet (HACS). CI registers this module in ``sys.modules``
instead of placing it under ``homeassistant/components``.

Enums are loaded from ``custom_components/easywave/const.py`` without importing
the integration package (which would execute ``__init__.py``).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_CUSTOM_CONST_CANDIDATES = (
    Path("/github/workspace/custom_components/easywave/const.py"),
    Path(__file__).resolve().parents[2] / "custom_components" / "easywave" / "const.py",
)


def _load_const() -> ModuleType:
    """Load const.py by file path so package ``__init__`` is not executed."""
    const_path = next((path for path in _CUSTOM_CONST_CANDIDATES if path.is_file()), None)
    if const_path is None:
        raise ModuleNotFoundError(
            "Could not find custom_components/easywave/const.py for hassfest stub"
        )
    spec = importlib.util.spec_from_file_location("_easywave_const_hassfest", const_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load Easywave const from {const_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_const = _load_const()
EasywaveGatewayFeature = _const.EasywaveGatewayFeature
EasywaveTransmitterFeature = _const.EasywaveTransmitterFeature

__all__ = [
    "EasywaveGatewayFeature",
    "EasywaveTransmitterFeature",
]
