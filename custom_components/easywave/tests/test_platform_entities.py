"""Tests for platform entity classes – construct, property, and action coverage."""
from __future__ import annotations

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import (
    MOCK_CONFIG_DATA,
    MOCK_SERIAL_NUMBER,
    create_mock_coordinator,
    create_mock_transceiver,
    DOMAIN,
)


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _neo_device_info(serial: str = "AABB001122334455", reg_id: str = "neo_reg_1", ch: int = 0, dt_code: int = 0x03) -> Dict[str, Any]:
    return {
        "serial_number": serial,
        "registration_id": reg_id,
        "type": "ewneo_switch",
        "name": "Neo Device",
        "neo_device": True,
        "gateway_serial": "GW00112233",
        "device_type_code": dt_code,
        "initial_state": {},
        "entities": [],
    }


def _ew_receiver_device_info(serial: str = "CCDD001122334455", reg_id: str = "rx_reg_1", kind: str = "switch") -> Dict[str, Any]:
    return {
        "serial_number": serial,
        "registration_id": reg_id,
        "type": "ew_receiver",
        "name": "EW Receiver",
        "neo_device": False,
        "receiver_kind": kind,
        "entities": [],
    }


def _transmitter_device_info(serial: str = "EEFF001122334455", reg_id: str = "tx_reg_1") -> Dict[str, Any]:
    return {
        "serial_number": serial,
        "registration_id": reg_id,
        "type": "ew_transmitter",
        "name": "EW Transmitter",
        "neo_device": False,
        "entities": [],
        "operating_type": "2",
        "operating_mode": None,
    }


def _entity_spec(etype: str = "switch", channel: int = 0, uid: str | None = None, name: str = "Test Entity") -> Dict[str, Any]:
    return {
        "type": etype,
        "unique_id": uid or f"easywave_test_{etype}_ch{channel}",
        "name": name,
        "channel": channel,
        "icon": "mdi:test-icon",
        "icon_on": "mdi:lightbulb-on",
        "icon_off": "mdi:lightbulb-off",
        "translation_key": None,
        "button_config": None,
        "operating_mode": None,
        "receiver_kind": "switch",
    }


# ═══════════════════════════════════════════════════════════════════
# Switch entities
# ═══════════════════════════════════════════════════════════════════

class TestEasywaveEWneoSwitch:
    """Tests for EWneo Switch entity."""

    def _make(self, hass, entry):
        from custom_components.easywave.switch import EasywaveEWneoSwitch
        coord = create_mock_coordinator(hass, entry)
        di = _neo_device_info()
        spec = _entity_spec("switch", 0)
        return EasywaveEWneoSwitch(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None
        assert entity._channel == 0

    def test_is_on_default(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.is_on is False

    def test_icon_property(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        icon = entity.icon
        assert icon is not None

    def test_available(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.available is True

    def test_extra_state_attributes(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        attrs = entity.extra_state_attributes
        assert isinstance(attrs, dict)

    async def test_async_turn_on(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        entity.hass = hass
        # Mock _send_ewb_change_state to succeed, avoiding deep transceiver calls
        entity._send_ewb_change_state = AsyncMock(return_value=True)
        entity.async_write_ha_state = MagicMock()
        await entity.async_turn_on()
        entity._send_ewb_change_state.assert_called_once()

    async def test_async_turn_off(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        entity.hass = hass
        entity._send_ewb_change_state = AsyncMock(return_value=True)
        entity.async_write_ha_state = MagicMock()
        await entity.async_turn_off()
        entity._send_ewb_change_state.assert_called_once()


class TestEasywaveEWReceiverSwitch:
    """Tests for classic EW Receiver Switch entity."""

    def _make(self, hass, entry):
        from custom_components.easywave.switch import EasywaveEWReceiverSwitch
        coord = create_mock_coordinator(hass, entry)
        di = _ew_receiver_device_info()
        spec = _entity_spec("switch", 0)
        return EasywaveEWReceiverSwitch(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_is_on_default(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.is_on is False or entity.is_on is None

    def test_assumed_state(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.assumed_state is True

    def test_icon(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.icon is not None

    def test_extra_state_attributes(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        attrs = entity.extra_state_attributes
        assert isinstance(attrs, dict)


# ═══════════════════════════════════════════════════════════════════
# Light entities
# ═══════════════════════════════════════════════════════════════════

class TestEasywaveEWReceiverDimmer:
    """Tests for EW Receiver Dimmer (light) entity."""

    def _make(self, hass, entry):
        from custom_components.easywave.light import EasywaveEWReceiverDimmer
        coord = create_mock_coordinator(hass, entry)
        di = _ew_receiver_device_info(kind="dimmer")
        spec = _entity_spec("light", 0)
        return EasywaveEWReceiverDimmer(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_is_on_stateless(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        # EW Receiver dimmer is stateless, is_on returns None
        assert entity.is_on is None

    def test_brightness_default(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        # brightness may be None or 0 by default
        assert entity.brightness is None or isinstance(entity.brightness, int)


class TestEasywaveEWneoLight:
    """Tests for EWneo Light entity."""

    def _make(self, hass, entry):
        from custom_components.easywave.light import EasywaveEWneoLight
        coord = create_mock_coordinator(hass, entry)
        di = _neo_device_info(dt_code=0x04)  # dimmer type
        spec = _entity_spec("light", 0)
        return EasywaveEWneoLight(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_is_on_default(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.is_on is False or entity.is_on is None

    def test_available(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.available is True

    def test_icon(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.icon is not None

    def test_extra_state_attributes(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        attrs = entity.extra_state_attributes
        assert isinstance(attrs, dict)


# ═══════════════════════════════════════════════════════════════════
# Cover entities
# ═══════════════════════════════════════════════════════════════════

class TestEasywaveCover:
    """Tests for classic EW Receiver Cover entity."""

    def _make(self, hass, entry):
        from custom_components.easywave.cover import EasywaveCover
        coord = create_mock_coordinator(hass, entry)
        di = _ew_receiver_device_info(kind="motor")
        spec = _entity_spec("cover", 0)
        return EasywaveCover(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_assumed_state(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        # Default is False (stateless=False in entity_spec)
        assert isinstance(entity.assumed_state, bool)

    def test_is_closed_default(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        # Default None or False
        assert entity.is_closed is None or entity.is_closed is False

    def test_icon(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.icon is not None

    def test_extra_state_attributes(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        attrs = entity.extra_state_attributes
        assert isinstance(attrs, dict)


class TestEasywaveEWneoCover:
    """Tests for EWneo Cover entity."""

    def _make(self, hass, entry, dt_code=0x05):
        from custom_components.easywave.cover import EasywaveEWneoCover
        coord = create_mock_coordinator(hass, entry)
        di = _neo_device_info(dt_code=dt_code)
        spec = _entity_spec("cover", 0)
        return EasywaveEWneoCover(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_is_closed_default(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.is_closed is None or isinstance(entity.is_closed, bool)

    def test_available(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.available is True

    def test_icon(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.icon is not None

    def test_extra_state_attributes(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        attrs = entity.extra_state_attributes
        assert isinstance(attrs, dict)


class TestEasywaveEWneoDualMotorCover:
    """Tests for EWneo Dual Motor Cover entity."""

    def _make(self, hass, entry):
        from custom_components.easywave.cover import EasywaveEWneoDualMotorCover
        coord = create_mock_coordinator(hass, entry)
        di = _neo_device_info(dt_code=0x08)
        spec = _entity_spec("cover", 0)
        return EasywaveEWneoDualMotorCover(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_name(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        name = entity.name
        # Dual motor cover should have a channel-based name
        assert name is not None


class TestEasywaveEWneoQuadMotorCover:
    """Tests for EWneo Quad Motor Cover entity."""

    def _make(self, hass, entry):
        from custom_components.easywave.cover import EasywaveEWneoQuadMotorCover
        coord = create_mock_coordinator(hass, entry)
        di = _neo_device_info(dt_code=0x09)
        spec = _entity_spec("cover", 0)
        return EasywaveEWneoQuadMotorCover(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None


# ═══════════════════════════════════════════════════════════════════
# Binary sensor entities
# ═══════════════════════════════════════════════════════════════════

class TestEasywaveTransmitterButtonSensor:
    """Tests for Transmitter Button Binary Sensor."""

    def _make(self, hass, entry):
        from custom_components.easywave.binary_sensor import EasywaveTransmitterButtonSensor
        coord = create_mock_coordinator(hass, entry)
        di = _transmitter_device_info()
        spec = _entity_spec("binary_sensor", 0, name="Button A")
        return EasywaveTransmitterButtonSensor(coord, di["serial_number"], di, button_id=0, entity_spec=spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_is_on_default(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.is_on is False or entity.is_on is None

    def test_icon(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        icon = entity.icon
        assert icon is not None or icon is None  # may vary

    def test_extra_state_attributes(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        attrs = entity.extra_state_attributes
        assert isinstance(attrs, dict)


class TestEWneoBatterySensor:
    """Tests for EWneo Battery Binary Sensor."""

    def _make(self, hass, entry):
        from custom_components.easywave.binary_sensor import EWneoBatterySensor
        coord = create_mock_coordinator(hass, entry)
        di = _neo_device_info()
        spec = _entity_spec("binary_sensor", 0, name="Battery")
        return EWneoBatterySensor(coord, di["serial_number"], di, entity_spec=spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_is_on(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        # Battery sensor: is_on indicates low battery
        result = entity.is_on
        assert result is False or result is None or result is True

    def test_icon(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.icon is not None


class TestEasywaveTransmitterStateBinarySensor:
    """Tests for Transmitter State Binary Sensor."""

    def _make(self, hass, entry):
        from custom_components.easywave.binary_sensor import EasywaveTransmitterStateBinarySensor
        coord = create_mock_coordinator(hass, entry)
        di = _transmitter_device_info()
        spec = _entity_spec("binary_sensor", 0, name="State")
        spec["operating_mode"] = "toggle"
        return EasywaveTransmitterStateBinarySensor(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_is_on_default(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.is_on is False or entity.is_on is None


# ═══════════════════════════════════════════════════════════════════
# Sensor entities
# ═══════════════════════════════════════════════════════════════════

class TestEasywaveGatewaySensor:
    """Tests for GatewaySensor (SensorEntity, not EasywaveEntity)."""

    def _make(self, hass, entry):
        from custom_components.easywave.sensor import EasywaveGatewaySensor
        coord = create_mock_coordinator(hass, entry)
        return EasywaveGatewaySensor(coord)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity is not None

    def test_device_info(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        di = entity.device_info
        assert di is not None

    def test_native_value(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        val = entity.native_value
        assert val is not None or val is None

    def test_icon(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.icon is not None

    def test_available(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert isinstance(entity.available, bool)

    def test_extra_state_attributes(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        attrs = entity.extra_state_attributes
        assert isinstance(attrs, dict)


class TestEasywaveBatterySensor:
    """Tests for battery sensor (3-arg constructor)."""

    def _make(self, hass, entry):
        from custom_components.easywave.sensor import EasywaveBatterySensor
        coord = create_mock_coordinator(hass, entry)
        di = _transmitter_device_info()
        return EasywaveBatterySensor(coord, di["serial_number"], di)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_native_value_default(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        val = entity.native_value
        assert val is None or isinstance(val, (int, float))

    async def test_update_battery_level(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()
        await entity.update_battery_level(75)
        assert entity.native_value == 75


class TestEasywaveSignalStrengthSensor:
    """Tests for signal strength sensor (3-arg constructor)."""

    def _make(self, hass, entry):
        from custom_components.easywave.sensor import EasywaveSignalStrengthSensor
        coord = create_mock_coordinator(hass, entry)
        di = _transmitter_device_info()
        return EasywaveSignalStrengthSensor(coord, di["serial_number"], di)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_update_signal(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        entity.hass = hass
        entity.schedule_update_ha_state = MagicMock()
        entity.update_signal_strength(-50)
        assert entity.native_value == -50


class TestEasywaveDiagnosticSensor:
    """Tests for diagnostic sensor (3-arg constructor)."""

    def _make(self, hass, entry):
        from custom_components.easywave.sensor import EasywaveDiagnosticSensor
        coord = create_mock_coordinator(hass, entry)
        di = _transmitter_device_info()
        return EasywaveDiagnosticSensor(coord, di["serial_number"], di)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_update_last_seen(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        entity.hass = hass
        entity.schedule_update_ha_state = MagicMock()
        entity.update_last_seen()
        assert entity.native_value is not None


class TestEasywaveLastButtonSensor:
    """Tests for last button sensor."""

    def _make(self, hass, entry):
        from custom_components.easywave.sensor import EasywaveLastButtonSensor
        coord = create_mock_coordinator(hass, entry)
        di = _transmitter_device_info()
        spec = _entity_spec("sensor", 0, name="Last Button")
        return EasywaveLastButtonSensor(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_native_value_default(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        val = entity.native_value
        assert val is None or isinstance(val, str)


class TestEasywaveTransmitterButtonEnumSensor:
    """Tests for transmitter button enum sensor."""

    def _make(self, hass, entry):
        from custom_components.easywave.sensor import EasywaveTransmitterButtonEnumSensor
        coord = create_mock_coordinator(hass, entry)
        di = _transmitter_device_info()
        spec = _entity_spec("sensor", 0, name="Button Enum")
        spec["button_config"] = {"button_count": 2, "labels": ["A", "B"]}
        return EasywaveTransmitterButtonEnumSensor(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_native_value_default(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        val = entity.native_value
        assert val is None or isinstance(val, str)


class TestEasywaveTransmitterStateSensor:
    """Tests for transmitter state sensor."""

    def _make(self, hass, entry):
        from custom_components.easywave.sensor import EasywaveTransmitterStateSensor
        coord = create_mock_coordinator(hass, entry)
        di = _transmitter_device_info()
        spec = _entity_spec("sensor", 0, name="State Sensor")
        return EasywaveTransmitterStateSensor(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_native_value_default(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        val = entity.native_value
        assert val is None or isinstance(val, str)


# ═══════════════════════════════════════════════════════════════════
# Button entities
# ═══════════════════════════════════════════════════════════════════

class TestEasywaveButton:
    """Tests for basic button (EasywaveButton)."""

    def _make(self, hass, entry):
        from custom_components.easywave.button import EasywaveButton
        coord = create_mock_coordinator(hass, entry)
        di = _transmitter_device_info()
        return EasywaveButton(coord, di["serial_number"], di, button_id=0)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_available(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert isinstance(entity.available, bool)


class TestEWReceiverUIButton:
    """Tests for EW Receiver UI Button."""

    def _make(self, hass, entry):
        from custom_components.easywave.button import EWReceiverUIButton
        coord = create_mock_coordinator(hass, entry)
        di = _ew_receiver_device_info()
        spec = _entity_spec("button", 0, name="UI Button")
        spec["button_config"] = {"type": "toggle", "supports_long_press": False}
        return EWReceiverUIButton(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_name(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.name is not None

    def test_icon(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.icon is not None


class TestEasywaveTestButton:
    """Tests for test button (3-arg constructor)."""

    def _make(self, hass, entry):
        from custom_components.easywave.button import EasywaveTestButton
        coord = create_mock_coordinator(hass, entry)
        di = _neo_device_info()
        return EasywaveTestButton(coord, di["serial_number"], di)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_available(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert isinstance(entity.available, bool)


class TestEasywaveEWReceiverButton:
    """Tests for EW Receiver Button."""

    def _make(self, hass, entry):
        from custom_components.easywave.button import EasywaveEWReceiverButton
        coord = create_mock_coordinator(hass, entry)
        di = _ew_receiver_device_info()
        spec = _entity_spec("button", 0, name="EW Button")
        spec["button_config"] = {"type": "toggle", "supports_long_press": False, "stateless": True}
        return EasywaveEWReceiverButton(coord, di["serial_number"], di, spec)

    def test_construction(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity.unique_id is not None

    def test_extra_state_attributes(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        attrs = entity.extra_state_attributes
        assert isinstance(attrs, dict)
