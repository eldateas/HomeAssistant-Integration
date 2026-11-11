"""Run hassfest inside the official image with the HACS feature-enum stub.

The image keeps Home Assistant at ``/usr/src/homeassistant`` and does not
install it as a site package. The official entrypoint changes into that
directory before importing it. This wrapper does the same, then registers
the feature-enum stub in ``sys.modules`` so it is not a directory under
``homeassistant/components``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CORE = Path("/usr/src/homeassistant")
_WORKSPACE = Path("/github/workspace")


def _integration_paths() -> list[str]:
    """Return custom integration directories the way the image entrypoint does."""
    workspace = _WORKSPACE if _WORKSPACE.is_dir() else Path.cwd()
    paths: list[str] = []
    custom = workspace / "custom_components"
    if custom.is_dir():
        paths.extend(
            str(manifest.parent)
            for manifest in sorted(custom.glob("*/manifest.json"))
        )
    paths.extend(str(manifest.parent) for manifest in sorted(workspace.glob("manifest.json")))
    if not paths:
        raise SystemExit("Error: No integrations found!")
    return paths


def _register_stub() -> None:
    """Expose this repo's feature enums as ``homeassistant.components.easywave``."""
    if _CORE.is_dir():
        sys.path.insert(0, str(_CORE))
    stub = _WORKSPACE / "scripts" / "hassfest_easywave_stub" / "__init__.py"
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
    """Register the stub, then validate the custom integrations."""
    _register_stub()
    core = _CORE if _CORE.is_dir() else Path.cwd()
    sys.argv = ["hassfest", "--action", "validate", "--core-path", str(core)]
    for path in _integration_paths():
        sys.argv.extend(("--integration-path", path))
    from script.hassfest.__main__ import main as hassfest_main

    return int(hassfest_main())


if __name__ == "__main__":
    raise SystemExit(main())
