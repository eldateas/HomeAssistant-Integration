"""Tests for Easywave migration mapping and ID helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "custom_components" / "easywave"


def _load(name: str, path: Path):
    """Load a module by file path without importing package __init__."""
    # Ensure package name exists for relative imports inside migration
    if "custom_components" not in sys.modules:
        pkg = type(sys)("custom_components")
        pkg.__path__ = [str(ROOT / "custom_components")]  # type: ignore[attr-defined]
        sys.modules["custom_components"] = pkg
    if "custom_components.easywave" not in sys.modules:
        ew = type(sys)("custom_components.easywave")
        ew.__path__ = [str(COMPONENT)]  # type: ignore[attr-defined]
        sys.modules["custom_components.easywave"] = ew

    mod_name = f"custom_components.easywave.{name}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


const = _load("const", COMPONENT / "const.py")
# devices is imported by migration; stub a minimal devices module first
devices = type(sys)("custom_components.easywave.devices")

def get_devices(_entry):  # noqa: ANN001
    return []

async def async_bucket_subentry_title(_hass, subentry_type: str) -> str:  # noqa: ANN001
    return const.BUCKET_SUBENTRY_TITLES[subentry_type]

devices.get_devices = get_devices  # type: ignore[attr-defined]
devices.async_bucket_subentry_title = async_bucket_subentry_title  # type: ignore[attr-defined]
sys.modules["custom_components.easywave.devices"] = devices
migration = _load("migration", COMPONENT / "migration.py")


def test_normalize_serial_hex() -> None:
    """Serial normalization strips separators and lowercases."""
    assert const.normalize_serial_hex("AA:BB-CC") == "aabbcc"
    assert const.normalize_serial_hex(b"\x01\x02") == "0102"
    assert const.normalize_serial_hex("0xabcd") == "abcd"


def test_device_ids_are_serial_stable() -> None:
    """Device IDs match CORE-compatible prefixes."""
    serial = "aabbccddeeff00112233445566778899"
    assert const.device_id_for_transmitter(serial) == f"transmitter_{serial}"
    assert const.device_id_for_neo_sensor(serial) == f"neo_sensor_{serial}"
    assert const.device_id_for_receiver(serial) == f"receiver_{serial}"
    assert (
        const.device_id_for_neo_actuator(serial, const.DEVICE_TYPE_CODE_SWITCH)
        == f"ewneo_switch_{serial}"
    )


def test_convert_transmitter() -> None:
    """Transmitter JSON maps into transmitter bucket fields."""
    serial = "a" * 32
    result = migration._convert_device(
        serial,
        {
            "device_type": "ew_transmitter",
            "name": "Remote",
            "operating_type": "1",
            "button_count": 2,
            "grouping_mode": "group",
            "switch_mode": "impulse",
        },
    )
    assert result is not None
    entry_type, device_id, data = result
    assert entry_type == const.ENTRY_TYPE_TRANSMITTER
    assert device_id == const.device_id_for_transmitter(serial)
    assert data[const.CONF_ENTRY_TYPE] == const.ENTRY_TYPE_TRANSMITTER
    assert data[const.CONF_TRANSMITTER_SERIAL] == serial
    assert data["button_count"] == 2


def test_convert_receiver() -> None:
    """Receiver JSON maps rx11_index and kind."""
    serial = "b" * 32
    result = migration._convert_device(
        serial,
        {
            "device_type": "ew_receiver",
            "name": "Plug",
            "rx11_index": 7,
            "receiver_kind": const.RECEIVER_KIND_SWITCH_2BUTTON,
            "operating_mode": "2",
        },
    )
    assert result is not None
    entry_type, device_id, data = result
    assert entry_type == const.ENTRY_TYPE_RECEIVER
    assert device_id == const.device_id_for_receiver(serial)
    assert data[const.CONF_RX11_INDEX] == 7
    assert data[const.CONF_RECEIVER_KIND] == const.RECEIVER_KIND_SWITCH_2BUTTON


def test_convert_neo_actuator() -> None:
    """Neo actuator JSON maps type code and index."""
    serial = "c" * 32
    result = migration._convert_device(
        serial,
        {
            "device_type": "ewneo_switch",
            "name": "Neo Switch",
            "ewneo_index": 3,
            "gateway_serial": "d" * 32,
            "neo_device": True,
        },
    )
    assert result is not None
    entry_type, device_id, data = result
    assert entry_type == const.ENTRY_TYPE_NEO_ACTUATOR
    assert device_id == const.device_id_for_neo_actuator(
        serial, const.DEVICE_TYPE_CODE_SWITCH
    )
    assert data["device_type_code"] == const.DEVICE_TYPE_CODE_SWITCH
    assert data["ewneo_index"] == 3


def test_convert_neo_sensor_capabilities_from_list() -> None:
    """Neo sensor capabilities can be inferred from available_sensors."""
    serial = "e" * 32
    result = migration._convert_device(
        serial,
        {
            "device_type": "ewneo_sensor",
            "name": "Climate",
            "available_sensors": ["temperature", "humidity"],
        },
    )
    assert result is not None
    entry_type, _device_id, data = result
    assert entry_type == const.ENTRY_TYPE_NEO_SENSOR
    capabilities = int(data["sensor_capabilities"])
    assert (capabilities >> 4) & 1
    assert (capabilities >> 5) & 1


def test_load_registered_devices_envelope(tmp_path: Path) -> None:
    """registered_devices.json envelope is parsed."""
    path = tmp_path / "registered_devices.json"
    path.write_text(
        json.dumps(
            {
                "version": "2.0",
                "devices": {
                    "a" * 32: {"device_type": "ew_transmitter", "name": "TX"},
                },
            }
        ),
        encoding="utf-8",
    )
    devices_map = migration._load_json_devices(path)
    assert len(devices_map) == 1
    assert ("a" * 32) in devices_map


def test_skip_receiver_without_index() -> None:
    """Receivers without index are skipped."""
    assert (
        migration._convert_device(
            "f" * 32,
            {"device_type": "ew_receiver", "name": "Broken"},
        )
        is None
    )
