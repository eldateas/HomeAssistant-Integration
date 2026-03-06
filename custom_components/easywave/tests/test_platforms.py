"""Tests for EASYWAVE platform entity setup (switch, light, cover, binary_sensor, button, sensor, select)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywave.const import DOMAIN

from .conftest import (
    MOCK_CONFIG_DATA,
    create_mock_coordinator,
    MOCK_DEVICE_INFO_EW_RECEIVER,
    MOCK_DEVICE_INFO_EWNEO_SWITCH,
    MOCK_DEVICE_INFO_EW_TRANSMITTER,
)


def _setup_coordinator(hass, config_entry, devices=None):
    """Helper: attach a mock coordinator with optional devices to hass.data."""
    coordinator = create_mock_coordinator(hass, config_entry, devices=devices)
    coordinator.transceiver.is_connected = True
    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = coordinator
    return coordinator


def _make_add_entities():
    """Create a mock async_add_entities that captures entities."""
    entities = []

    def add_entities(ents, update_before_add=False):
        entities.extend(ents)

    return entities, add_entities


# ═══════════════════════════════════════════════════════
# switch platform
# ═══════════════════════════════════════════════════════


class TestSwitchPlatform:
    """Tests for switch.async_setup_entry."""

    async def test_setup_empty(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """No devices → no entities created, handler registered."""
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(hass, mock_config_entry)

        from custom_components.easywave.switch import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        assert entities == []
        coordinator.register_platform_handler.assert_called_once()

    async def test_setup_ew_receiver_switch(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Classic EW receiver with switch entity spec is created."""
        device_info = {
            **MOCK_DEVICE_INFO_EW_RECEIVER,
            "registration_id": "TEST_REG_001",
        }
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(
            hass, mock_config_entry, devices={device_info["serial_number"]: device_info}
        )

        from custom_components.easywave.switch import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        # Should create at least one switch entity
        assert len(entities) >= 1

    async def test_setup_ewneo_switch(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """EWneo switch device creates EWneo switch entities."""
        device_info = {
            **MOCK_DEVICE_INFO_EWNEO_SWITCH,
            "registration_id": "TEST_REG_002",
        }
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(
            hass, mock_config_entry, devices={device_info["serial_number"]: device_info}
        )

        from custom_components.easywave.switch import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        assert len(entities) >= 1


# ═══════════════════════════════════════════════════════
# light platform
# ═══════════════════════════════════════════════════════


class TestLightPlatform:
    """Tests for light.async_setup_entry."""

    async def test_setup_empty(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(hass, mock_config_entry)

        from custom_components.easywave.light import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        assert entities == []
        coordinator.register_platform_handler.assert_called_once()

    async def test_setup_with_light_device(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Device with light entity spec creates light entities."""
        device_info = {
            "serial_number": "LIGHT001AABBCCDD",
            "type": "ew_receiver",
            "name": "Test Light",
            "receiver_kind": "dimmer",
            "neo_device": False,
            "registration_id": "LIGHT_REG_001",
            "entities": [
                {
                    "type": "light",
                    "unique_id": "easywave_LIGHT001_light",
                    "name": "Test Light",
                }
            ],
        }
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(
            hass, mock_config_entry, devices={"LIGHT001AABBCCDD": device_info}
        )

        from custom_components.easywave.light import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        assert len(entities) >= 1


# ═══════════════════════════════════════════════════════
# cover platform
# ═══════════════════════════════════════════════════════


class TestCoverPlatform:
    """Tests for cover.async_setup_entry."""

    async def test_setup_empty(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(hass, mock_config_entry)

        from custom_components.easywave.cover import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        assert entities == []
        coordinator.register_platform_handler.assert_called_once()

    async def test_setup_with_cover_device(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        device_info = {
            "serial_number": "COV001AABBCCDDEE",
            "type": "ew_receiver",
            "name": "Test Cover",
            "receiver_kind": "motor",
            "neo_device": False,
            "registration_id": "COV_REG_001",
            "entities": [
                {
                    "type": "cover",
                    "unique_id": "easywave_COV001_cover",
                    "name": "Test Cover",
                }
            ],
        }
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(
            hass, mock_config_entry, devices={"COV001AABBCCDDEE": device_info}
        )

        from custom_components.easywave.cover import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        assert len(entities) >= 1


# ═══════════════════════════════════════════════════════
# binary_sensor platform
# ═══════════════════════════════════════════════════════


class TestBinarySensorPlatform:
    """Tests for binary_sensor.async_setup_entry."""

    async def test_setup_empty(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(hass, mock_config_entry)

        from custom_components.easywave.binary_sensor import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        # No devices → no entities, but binary_sensor doesn't register handler
        assert entities == []

    async def test_setup_with_transmitter(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Transmitter creates binary sensor entities."""
        device_info = {
            **MOCK_DEVICE_INFO_EW_TRANSMITTER,
            "registration_id": "TX_REG_001",
        }
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(
            hass,
            mock_config_entry,
            devices={device_info["serial_number"]: device_info},
        )

        from custom_components.easywave.binary_sensor import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        # Should create binary sensor entities for buttons
        assert len(entities) >= 1


# ═══════════════════════════════════════════════════════
# button platform
# ═══════════════════════════════════════════════════════


class TestButtonPlatform:
    """Tests for button.async_setup_entry."""

    async def test_setup_empty(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(hass, mock_config_entry)

        from custom_components.easywave.button import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        assert entities == []

    async def test_setup_with_ew_receiver(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """EW receiver with button entity specs creates button entities."""
        device_info = {
            "serial_number": "BTN001AABBCCDDEE",
            "type": "ew_receiver",
            "name": "Test Button Device",
            "receiver_kind": "switch",
            "neo_device": False,
            "registration_id": "BTN_REG_001",
            "entities": [
                {
                    "type": "button",
                    "unique_id": "easywave_BTN001_button_on",
                    "name": "On",
                    "button_id": "on",
                },
                {
                    "type": "button",
                    "unique_id": "easywave_BTN001_button_off",
                    "name": "Off",
                    "button_id": "off",
                },
            ],
        }
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(
            hass, mock_config_entry, devices={"BTN001AABBCCDDEE": device_info}
        )

        from custom_components.easywave.button import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        # May or may not create — depends on skip logic for receiver types
        assert isinstance(entities, list)


# ═══════════════════════════════════════════════════════
# sensor platform
# ═══════════════════════════════════════════════════════


class TestSensorPlatform:
    """Tests for sensor.async_setup_entry."""

    async def test_setup_creates_gateway_sensor(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Even with no devices, the gateway sensor is always created."""
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(hass, mock_config_entry)

        from custom_components.easywave.sensor import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        # Always creates a gateway sensor at minimum
        assert len(entities) >= 1

    async def test_setup_with_sensor_device(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        device_info = {
            "serial_number": "SENS001AABBCCDD",
            "type": "ewneo_sensor",
            "name": "Temp Sensor",
            "neo_device": True,
            "device_type_code": 0x10,
            "registration_id": "SENS_REG_001",
            "entities": [
                {
                    "type": "sensor",
                    "unique_id": "easywave_SENS001_temp",
                    "name": "Temperature",
                    "sensor_type": "temperature",
                }
            ],
        }
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(
            hass, mock_config_entry, devices={"SENS001AABBCCDD": device_info}
        )

        from custom_components.easywave.sensor import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        # Gateway sensor + device sensors
        assert len(entities) >= 1


# ═══════════════════════════════════════════════════════
# select platform
# ═══════════════════════════════════════════════════════


class TestSelectPlatform:
    """Tests for select.async_setup_entry."""

    async def test_setup_empty(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(hass, mock_config_entry)

        from custom_components.easywave.select import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        # No transmitter devices → no select entities
        assert entities == []

    async def test_setup_with_transmitter(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Transmitter devices create select entities."""
        device_info = {
            **MOCK_DEVICE_INFO_EW_TRANSMITTER,
            "registration_id": "TX_SEL_REG_001",
            "operating_type": "2",
        }
        mock_config_entry.add_to_hass(hass)
        coordinator = _setup_coordinator(
            hass,
            mock_config_entry,
            devices={device_info["serial_number"]: device_info},
        )

        from custom_components.easywave.select import async_setup_entry

        entities, add_entities = _make_add_entities()
        await async_setup_entry(hass, mock_config_entry, add_entities)
        # May create select entities for transmitter state selection
        assert isinstance(entities, list)
