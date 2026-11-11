"""Run hassfest inside the official image with the HACS feature-enum stub.

The stub is registered in ``sys.modules`` and is not mounted under
``homeassistant/components/easywave``. A directory there makes hassfest warn
that the custom integration domain collides with Core.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _register_stub() -> None:
    """Expose this repo's feature enums as ``homeassistant.components.easywave``."""
    stub = Path("/github/workspace/scripts/hassfest_easywave_stub/__init__.py")
    if not stub.is_file():
        stub = Path(__file__).resolve().parent / "hassfest_easywave_stub" / "__init__.py"
    import homeassistant.components as components

    name = "homeassistant.components.easywave"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, stub)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load hassfest Easywave stub from {stub}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    setattr(components, "easywave", module)


def main() -> int:
    """Register the stub, then run the image's hassfest entry point."""
    _register_stub()
    try:
        from hassfest.__main__ import main as hassfest_main
    except ImportError:
        from script.hassfest.__main__ import main as hassfest_main
    return int(hassfest_main())


if __name__ == "__main__":
    raise SystemExit(main())
