"""Pytest configuration for Easywave custom component tests."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# Make `custom_components.easywave` importable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Lightweight stubs so unit tests run without a full Home Assistant install.
if "homeassistant" not in sys.modules:
    ha = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    const = types.ModuleType("homeassistant.const")
    core = types.ModuleType("homeassistant.core")
    helpers = types.ModuleType("homeassistant.helpers")
    issue_registry = types.ModuleType("homeassistant.helpers.issue_registry")

    class ConfigEntry:  # noqa: D101
        pass

    class ConfigSubentry:  # noqa: D101
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.__dict__.update(kwargs)

    const.CONF_DEVICES = "devices"
    core.HomeAssistant = MagicMock
    issue_registry.IssueSeverity = MagicMock()
    issue_registry.async_create_issue = MagicMock()

    config_entries.ConfigEntry = ConfigEntry
    config_entries.ConfigSubentry = ConfigSubentry

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.const"] = const
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.issue_registry"] = issue_registry
