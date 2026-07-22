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


def test_convert_neo_sensor_hacs_list_capabilities_field() -> None:
    """0.6.10 stores sensor_capabilities as a name list, not a bitmask."""
    serial = "1" * 32
    result = migration._convert_device(
        serial,
        {
            "device_type": "ewneo_sensor",
            "name": "Climate",
            "sensor_capabilities": ["temperature", "humidity", "battery"],
        },
    )
    assert result is not None
    capabilities = int(result[2]["sensor_capabilities"])
    assert capabilities & 1  # battery
    assert (capabilities >> 4) & 1
    assert (capabilities >> 5) & 1


def test_convert_neo_sensor_empty_capabilities_falls_back() -> None:
    """Empty list capabilities must not crash; use available_sensors."""
    serial = "2" * 32
    result = migration._convert_device(
        serial,
        {
            "device_type": "ewneo_sensor",
            "sensor_capabilities": [],
            "available_sensors": ["temperature"],
        },
    )
    assert result is not None
    capabilities = int(result[2]["sensor_capabilities"])
    assert (capabilities >> 4) & 1
    assert not ((capabilities >> 5) & 1)


def test_flatten_managed_extra_data() -> None:
    """managed_devices.json nests TX fields under extra_data."""
    flat = migration._flatten_device_info(
        {
            "device_type": "ew_transmitter",
            "name": "Remote",
            "serial_number": "a" * 32,
            "extra_data": {
                "operating_type": "2",
                "button_count": 4,
                "grouping_mode": "group",
                "usage_type": "cover",
            },
        }
    )
    assert flat["operating_type"] == "2"
    assert flat["usage_type"] == "cover"
    assert "extra_data" not in flat


def test_convert_transmitter_from_flattened_managed() -> None:
    """Transmitter fields from managed extra_data survive flatten+convert."""
    serial = "3" * 32
    info = migration._flatten_device_info(
        {
            "device_type": "ew_transmitter",
            "serial_number": serial,
            "name": "Cover Remote",
            "extra_data": {
                "operating_type": "2",
                "button_count": 2,
                "usage_type": "cover",
                "switch_mode": "impulse",
            },
        }
    )
    result = migration._convert_device(serial, info)
    assert result is not None
    _entry_type, _device_id, data = result
    assert data["operating_type"] == "2"
    assert data["usage_type"] == "cover"
    assert data["button_count"] == 2


def test_convert_neo_actuator_via_indices_ewb() -> None:
    """Managed devices may only expose ewneo index under indices.ewb."""
    serial = "4" * 32
    result = migration._convert_device(
        serial,
        {
            "device_type": "ewneo_motor",
            "name": "Blind",
            "neo_device": True,
            "indices": {"ewb": 5},
            "gateway_serial": "d" * 32,
            "channels": 1,
        },
    )
    assert result is not None
    entry_type, device_id, data = result
    assert entry_type == const.ENTRY_TYPE_NEO_ACTUATOR
    assert device_id == const.device_id_for_neo_actuator(
        serial, const.DEVICE_TYPE_CODE_MOTOR
    )
    assert data["ewneo_index"] == 5


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


def test_load_managed_devices_v3_envelope(tmp_path: Path) -> None:
    """managed_devices.json v3.0 envelope is parsed like registered."""
    path = tmp_path / "managed_devices.json"
    path.write_text(
        json.dumps(
            {
                "version": "3.0",
                "devices": {
                    "b" * 32: {
                        "device_type": "ew_receiver",
                        "name": "Plug",
                        "rx11_index": 2,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    devices_map = migration._load_json_devices(path)
    assert ("b" * 32) in devices_map


def test_skip_receiver_without_index() -> None:
    """Receivers without index are skipped."""
    assert (
        migration._convert_device(
            "f" * 32,
            {"device_type": "ew_receiver", "name": "Broken"},
        )
        is None
    )


def test_realistic_0610_device_set_converts() -> None:
    """Representative 0.6.10 registered_devices payload converts without error."""
    devices = {
        "a" * 32: {
            "device_type": "ew_transmitter",
            "type": "ew_transmitter",
            "name": "Wandschalter",
            "serial_number": "a" * 32,
            "operating_type": "1",
            "button_count": 4,
            "grouping_mode": "group",
            "switch_mode": "impulse",
            "registration_id": "deadbeef" * 4,
            "entities": [{"unique_id": "deadbeef" * 4 + "_battery"}],
        },
        "b" * 32: {
            "device_type": "ewneo_sensor",
            "type": "ewneo_sensor",
            "name": "Klima",
            "serial_number": "b" * 32,
            "sensor_capabilities": ["temperature", "humidity", "battery"],
            "available_sensors": ["temperature", "humidity", "battery"],
            "sensor_types": ["temperature", "humidity"],
            "has_battery": True,
        },
        "c" * 32: {
            "device_type": "ew_receiver",
            "type": "ew_receiver",
            "name": "Steckdose",
            "serial_number": "c" * 32,
            "rx11_index": 3,
            "receiver_kind": "impulse",
            "operating_mode": "1",
        },
        "d" * 32: {
            "device_type": "ewneo_dual_motor",
            "type": "ewneo_dual_motor",
            "name": "Jalousie",
            "serial_number": "d" * 32,
            "neo_device": True,
            "ewneo_index": 7,
            "gateway_serial": "e" * 32,
            "device_type_code": const.DEVICE_TYPE_CODE_DUAL_MOTOR,
            "channels": 2,
            "initial_state": {"runtime_measured": True},
        },
    }
    converted = []
    for serial, info in devices.items():
        result = migration._convert_device(serial, info)
        assert result is not None, f"failed for {info['device_type']}"
        converted.append(result)

    types = {entry_type for entry_type, _, _ in converted}
    assert types == {
        const.ENTRY_TYPE_TRANSMITTER,
        const.ENTRY_TYPE_NEO_SENSOR,
        const.ENTRY_TYPE_RECEIVER,
        const.ENTRY_TYPE_NEO_ACTUATOR,
    }
    sensor_caps = next(
        data["sensor_capabilities"]
        for entry_type, _, data in converted
        if entry_type == const.ENTRY_TYPE_NEO_SENSOR
    )
    assert sensor_caps & 1
    assert (sensor_caps >> 4) & 1
    assert (sensor_caps >> 5) & 1
    motor = next(
        data
        for entry_type, _, data in converted
        if entry_type == const.ENTRY_TYPE_NEO_ACTUATOR
    )
    assert motor["runtime_measured"] is True
    assert motor["ewneo_index"] == 7
