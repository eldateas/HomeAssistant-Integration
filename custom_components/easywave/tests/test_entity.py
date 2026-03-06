"""Tests for the EASYWAVE entity base class and platform setups."""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywave.const import DOMAIN
from custom_components.easywave.entity import EasywaveEntity

from .conftest import (
    MOCK_SERIAL_NUMBER,
    MOCK_DEVICE_PATH,
    MOCK_CONFIG_DATA,
    create_mock_coordinator,
    create_mock_transceiver,
    MOCK_DEVICE_INFO_EW_RECEIVER,
    MOCK_DEVICE_INFO_EWNEO_SWITCH,
    MOCK_DEVICE_INFO_EW_TRANSMITTER,
)


# ─────── Helper ──────────────────────────────────────────────────────

def _create_entity(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    serial_number: str = "AABBCCDDEEFF0011",
    device_info: Dict[str, Any] | None = None,
    connected: bool = True,
) -> EasywaveEntity:
    """Create a minimal EasywaveEntity for testing."""
    coordinator = create_mock_coordinator(hass, config_entry, connected=connected)
    coordinator._registered_devices = {}
    coordinator.devices = {}
    info = device_info or {
        "type": "ew_receiver",
        "name": "Test Device",
        "receiver_kind": "switch",
    }
    entity = EasywaveEntity(coordinator, serial_number, info)
    entity.hass = hass
    return entity


# ─────── EasywaveEntity base class ──────────────────────────────────

class TestEasywaveEntityBase:
    """Tests for the EasywaveEntity base class."""

    async def test_init_sets_serial_number(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test entity initialization stores serial number."""
        entity = _create_entity(hass, mock_config_entry, serial_number="AA11BB22CC33DD44")
        assert entity._serial_number == "AA11BB22CC33DD44"
        assert entity.serial_number == "AA11BB22CC33DD44"

    async def test_init_cleans_device_info(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test that problematic fields are stripped from device_info."""
        info = {
            "type": "ew_receiver",
            "name": "Test",
            "config_entry_id": "should_be_removed",
            "via_device": "should_be_removed",
            "config_subentry_id": "should_be_removed",
        }
        entity = _create_entity(hass, mock_config_entry, device_info=info)
        assert "config_entry_id" not in entity._device_info
        assert "via_device" not in entity._device_info
        assert "config_subentry_id" not in entity._device_info
        assert entity._device_info["type"] == "ew_receiver"

    async def test_has_entity_name(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test that has_entity_name is True."""
        entity = _create_entity(hass, mock_config_entry)
        assert entity._attr_has_entity_name is True

    async def test_device_type_property(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test device_type property returns correct type."""
        entity = _create_entity(
            hass,
            mock_config_entry,
            device_info={"type": "ewneo_switch", "name": "Neo Switch"},
        )
        assert entity.device_type == "ewneo_switch"

    async def test_device_type_defaults_to_unknown(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test device_type returns 'unknown' when type is missing."""
        entity = _create_entity(
            hass, mock_config_entry, device_info={"name": "No type"}
        )
        assert entity.device_type == "unknown"

    async def test_extra_state_attributes(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test extra_state_attributes includes serial_number."""
        entity = _create_entity(hass, mock_config_entry, serial_number="SERIAL123")
        attrs = entity.extra_state_attributes
        assert "serial_number" in attrs
        assert attrs["serial_number"] == "SERIAL123"

    async def test_extra_state_attributes_ewneo_index(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test egneo_index is included in extra_state_attributes."""
        info = {"type": "ewneo_switch", "name": "Neo", "ewneo_index": 3}
        entity = _create_entity(hass, mock_config_entry, device_info=info)
        # Set up coordinator to return device_info with ewneo_index
        entity.coordinator._registered_devices = {entity._serial_number: info}
        attrs = entity.extra_state_attributes
        assert attrs.get("ewneo_index") == 3

    async def test_available_when_connected(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test entity is available when RX11 is connected."""
        entity = _create_entity(hass, mock_config_entry, connected=True)
        assert entity.available is True

    async def test_unavailable_when_disconnected(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test entity is unavailable when RX11 is disconnected."""
        entity = _create_entity(hass, mock_config_entry, connected=False)
        assert entity.available is False

    async def test_device_info_returns_device_info(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test device_info property returns DeviceInfo."""
        mock_config_entry.add_to_hass(hass)
        entity = _create_entity(hass, mock_config_entry)
        info = entity.device_info
        assert info is not None
        # Identifiers should contain DOMAIN
        identifiers = info.get("identifiers", set())
        assert len(identifiers) > 0

    async def test_gateway_identifier(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test _get_gateway_identifier returns expected tuple."""
        mock_config_entry.add_to_hass(hass)
        entity = _create_entity(hass, mock_config_entry)
        gw = entity._get_gateway_identifier()
        assert gw is not None
        assert gw[0] == DOMAIN
        assert mock_config_entry.entry_id in gw[1]


# ─────── Platform entity setup tests ────────────────────────────────

class TestPlatformSetup:
    """Tests for platform async_setup_entry functions."""

    async def test_switch_platform_setup(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test switch platform setup."""
        mock_config_entry.add_to_hass(hass)
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

        add_entities = MagicMock()

        from custom_components.easywave.switch import async_setup_entry

        await async_setup_entry(hass, mock_config_entry, add_entities)
        # Should register platform handler with coordinator
        coordinator.register_platform_handler.assert_called()

    async def test_light_platform_setup(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test light platform setup."""
        mock_config_entry.add_to_hass(hass)
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

        add_entities = MagicMock()

        from custom_components.easywave.light import async_setup_entry

        await async_setup_entry(hass, mock_config_entry, add_entities)
        coordinator.register_platform_handler.assert_called()

    async def test_cover_platform_setup(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test cover platform setup."""
        mock_config_entry.add_to_hass(hass)
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

        add_entities = MagicMock()

        from custom_components.easywave.cover import async_setup_entry

        await async_setup_entry(hass, mock_config_entry, add_entities)
        coordinator.register_platform_handler.assert_called()

    async def test_sensor_platform_setup(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test sensor platform setup."""
        mock_config_entry.add_to_hass(hass)
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

        add_entities = MagicMock()

        from custom_components.easywave.sensor import async_setup_entry

        await async_setup_entry(hass, mock_config_entry, add_entities)
        # Sensor platform uses event listeners rather than register_platform_handler
        # Verify add_entities was called (for the gateway sensor)
        add_entities.assert_called()

    async def test_button_platform_setup(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test button platform setup."""
        mock_config_entry.add_to_hass(hass)
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

        add_entities = MagicMock()

        from custom_components.easywave.button import async_setup_entry

        await async_setup_entry(hass, mock_config_entry, add_entities)
        coordinator.register_platform_handler.assert_called()

    async def test_binary_sensor_platform_setup(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test binary_sensor platform setup."""
        mock_config_entry.add_to_hass(hass)
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

        add_entities = MagicMock()

        from custom_components.easywave.binary_sensor import async_setup_entry

        # binary_sensor uses event listeners, not register_platform_handler
        await async_setup_entry(hass, mock_config_entry, add_entities)

    async def test_select_platform_setup(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test select platform setup."""
        mock_config_entry.add_to_hass(hass)
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

        add_entities = MagicMock()

        from custom_components.easywave.select import async_setup_entry

        # select platform uses event listeners, not register_platform_handler
        await async_setup_entry(hass, mock_config_entry, add_entities)
