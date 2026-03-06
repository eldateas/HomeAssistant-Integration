"""Tests for EASYWAVE device_trigger module."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywave.const import DOMAIN
from custom_components.easywave.device_trigger import (
    CONF_SUBTYPE,
    TRIGGER_TYPE_BUTTON_PRESS,
    TRIGGER_TYPE_BUTTON_RELEASE,
    TRIGGER_TYPE_CHANNEL_ON,
    TRIGGER_TYPE_CHANNEL_OFF,
    TRIGGER_TYPE_GATEWAY_CONNECTED,
    TRIGGER_TYPE_GATEWAY_DISCONNECTED,
    TRIGGER_SCHEMA,
    TRANSMITTER_TRIGGER_SCHEMA,
    GATEWAY_TRIGGER_SCHEMA,
    _SENSOR_TYPE_IDENTIFIERS,
    _get_device_info_for_serial,
    _is_sensor_device,
    _get_transmitter_trigger_map,
    async_get_triggers,
    async_attach_trigger,
    async_get_trigger_capabilities,
)

from .conftest import create_mock_coordinator, MOCK_CONFIG_DATA


# ═══════════════════════════════════════════════════════
# Constants & Schemas
# ═══════════════════════════════════════════════════════


class TestTriggerConstants:
    """Tests for trigger type constants."""

    def test_trigger_types(self) -> None:
        assert TRIGGER_TYPE_BUTTON_PRESS == "button_press"
        assert TRIGGER_TYPE_BUTTON_RELEASE == "button_release"
        assert TRIGGER_TYPE_CHANNEL_ON == "channel_on"
        assert TRIGGER_TYPE_CHANNEL_OFF == "channel_off"
        assert TRIGGER_TYPE_GATEWAY_CONNECTED == "gateway_connected"
        assert TRIGGER_TYPE_GATEWAY_DISCONNECTED == "gateway_disconnected"

    def test_conf_subtype(self) -> None:
        assert CONF_SUBTYPE == "subtype"

    def test_sensor_type_identifiers(self) -> None:
        assert "temperature" in _SENSOR_TYPE_IDENTIFIERS
        assert "humidity" in _SENSOR_TYPE_IDENTIFIERS
        assert "wind_speed" in _SENSOR_TYPE_IDENTIFIERS
        assert "rain" in _SENSOR_TYPE_IDENTIFIERS

    def test_transmitter_trigger_schema_valid(self) -> None:
        """Transmitter trigger schema accepts valid button_press config."""
        import voluptuous as vol
        config = {
            "platform": "device",
            "domain": DOMAIN,
            "device_id": "abc123",
            "type": "button_press",
            "subtype": "A",
        }
        result = TRANSMITTER_TRIGGER_SCHEMA(config)
        assert result[CONF_TYPE] == "button_press"

    def test_gateway_trigger_schema_valid(self) -> None:
        """Gateway trigger schema accepts valid gateway_connected config."""
        config = {
            "platform": "device",
            "domain": DOMAIN,
            "device_id": "abc123",
            "type": "gateway_connected",
        }
        result = GATEWAY_TRIGGER_SCHEMA(config)
        assert result[CONF_TYPE] == "gateway_connected"


# ═══════════════════════════════════════════════════════
# _get_device_info_for_serial
# ═══════════════════════════════════════════════════════


class TestGetDeviceInfoForSerial:
    """Tests for _get_device_info_for_serial helper."""

    def test_no_domain_data(self, hass: HomeAssistant) -> None:
        result = _get_device_info_for_serial(hass, "AABB0001")
        assert result is None

    def test_empty_domain_data(self, hass: HomeAssistant) -> None:
        hass.data[DOMAIN] = {}
        result = _get_device_info_for_serial(hass, "AABB0001")
        assert result is None

    def test_exact_match(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        coordinator = create_mock_coordinator(
            hass,
            mock_config_entry,
            devices={"AABB0001": {"type": "ew_transmitter", "name": "TX"}},
        )
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

        result = _get_device_info_for_serial(hass, "AABB0001")
        assert result is not None
        assert result["type"] == "ew_transmitter"

    def test_last8_fallback(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        coordinator = create_mock_coordinator(
            hass,
            mock_config_entry,
            devices={"0000000000AABB0001": {"type": "ew_transmitter"}},
        )
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

        result = _get_device_info_for_serial(hass, "XXAABB0001")
        assert result is not None

    def test_no_match(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        coordinator = create_mock_coordinator(
            hass,
            mock_config_entry,
            devices={"AABB0001": {"type": "ew_transmitter"}},
        )
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

        result = _get_device_info_for_serial(hass, "FF000000")
        assert result is None

    def test_dict_entry_data(self, hass: HomeAssistant) -> None:
        """Test when entry data is a dict with 'coordinator' key."""
        coordinator = MagicMock()
        coordinator.devices = {"AABB0001": {"type": "ew_transmitter"}}
        hass.data[DOMAIN] = {"entry_1": {"coordinator": coordinator}}

        result = _get_device_info_for_serial(hass, "AABB0001")
        assert result is not None

    def test_entry_data_without_coordinator(self, hass: HomeAssistant) -> None:
        """Test when entry data is a dict without 'coordinator' or devices attr."""
        hass.data[DOMAIN] = {"entry_1": {"something_else": True}}
        result = _get_device_info_for_serial(hass, "AABB0001")
        assert result is None


# ═══════════════════════════════════════════════════════
# _is_sensor_device
# ═══════════════════════════════════════════════════════


class TestIsSensorDevice:
    """Tests for _is_sensor_device helper."""

    def test_none_device_info_no_entities(self, hass: HomeAssistant) -> None:
        """No device info and no matching entities → not a sensor."""
        result = _is_sensor_device(None, "dev_123", hass)
        assert result is False

    def test_ew_sensor_type(self, hass: HomeAssistant) -> None:
        """device_info with type=ew_sensor → is sensor."""
        result = _is_sensor_device({"type": "ew_sensor"}, "dev_123", hass)
        assert result is True

    def test_ewneo_sensor_type(self, hass: HomeAssistant) -> None:
        """device_info with type=ewneo_sensor → is sensor."""
        result = _is_sensor_device({"type": "ewneo_sensor"}, "dev_123", hass)
        assert result is True

    def test_device_type_field(self, hass: HomeAssistant) -> None:
        """device_info with device_type=ew_sensor → is sensor."""
        result = _is_sensor_device({"device_type": "ew_sensor"}, "dev_123", hass)
        assert result is True

    def test_extra_data_type(self, hass: HomeAssistant) -> None:
        """device_info with extra_data.type=ewneo_sensor → is sensor."""
        result = _is_sensor_device(
            {"type": "", "extra_data": {"type": "ewneo_sensor"}}, "dev_123", hass
        )
        assert result is True

    def test_transmitter_type_not_sensor(self, hass: HomeAssistant) -> None:
        """device_info with type=ew_transmitter → not a sensor."""
        result = _is_sensor_device({"type": "ew_transmitter"}, "dev_123", hass)
        assert result is False

    def test_entity_registry_temperature_match(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Entity with _temperature in unique_id → sensor device."""
        mock_config_entry.add_to_hass(hass)
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "SENSOR001")},
        )
        ent_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            "reg001_temperature",
            config_entry=mock_config_entry,
            device_id=device.id,
        )

        result = _is_sensor_device(None, device.id, hass)
        assert result is True

    def test_entity_registry_humidity_match(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Entity with _humidity in unique_id → sensor device."""
        mock_config_entry.add_to_hass(hass)
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "SENSOR002")},
        )
        ent_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            "reg002_humidity",
            config_entry=mock_config_entry,
            device_id=device.id,
        )

        result = _is_sensor_device(None, device.id, hass)
        assert result is True

    def test_entity_registry_no_sensor_match(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Entity with unrelated unique_id → not a sensor."""
        mock_config_entry.add_to_hass(hass)
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TX999")},
        )
        ent_reg.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            "reg999_button_ch0",
            config_entry=mock_config_entry,
            device_id=device.id,
        )

        result = _is_sensor_device(None, device.id, hass)
        assert result is False


# ═══════════════════════════════════════════════════════
# _get_transmitter_trigger_map
# ═══════════════════════════════════════════════════════


class TestGetTransmitterTriggerMap:
    """Tests for _get_transmitter_trigger_map helper."""

    def test_no_device_info_returns_abcd(self, hass: HomeAssistant) -> None:
        result = _get_transmitter_trigger_map(hass, None, "AABB0001")
        assert len(result) == 4
        assert result[0] == ("A", "A")
        assert result[3] == ("D", "D")

    def test_with_device_info_returns_list(self, hass: HomeAssistant) -> None:
        device_info = {
            "type": "ew_transmitter",
            "serial_number": "AABB0001",
            "operating_type": "2",
            "usage_type": "switch",
            "registration_id": "test_reg_001",
            "entities": [
                {"type": "binary_sensor", "name": "Button A", "button_id": "A"},
                {"type": "binary_sensor", "name": "Button B", "button_id": "B"},
            ],
        }
        result = _get_transmitter_trigger_map(hass, device_info, "AABB0001")
        assert isinstance(result, list)

    def test_with_1btn_transmitter(self, hass: HomeAssistant) -> None:
        """1-button transmitter with entity specs."""
        device_info = {
            "type": "ew_transmitter",
            "serial_number": "AABB0001",
            "operating_type": "1",
            "usage_type": "switch",
            "button_count": 1,
            "switch_mode": "impulse",
            "registration_id": "test_reg_002",
        }
        result = _get_transmitter_trigger_map(hass, device_info, "AABB0001")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_with_3btn_cover(self, hass: HomeAssistant) -> None:
        """3-Tast cover device."""
        device_info = {
            "type": "ew_transmitter",
            "serial_number": "AABB0001",
            "operating_type": "3",
            "usage_type": "cover",
            "button_count": 4,
            "registration_id": "test_reg_003",
        }
        result = _get_transmitter_trigger_map(hass, device_info, "AABB0001")
        assert isinstance(result, list)

    def test_fallback_to_button_count(self, hass: HomeAssistant) -> None:
        """Device with no matching specs falls back to button_count labels."""
        device_info = {
            "type": "ew_transmitter",
            "serial_number": "AABB0001",
            "operating_type": "1",
            "usage_type": "switch",
            "button_count": 2,
            "switch_mode": "impulse",
            "registration_id": "test_reg_004",
        }
        result = _get_transmitter_trigger_map(hass, device_info, "AABB0001")
        assert isinstance(result, list)


# ═══════════════════════════════════════════════════════
# async_get_triggers
# ═══════════════════════════════════════════════════════


class TestAsyncGetTriggers:
    """Tests for async_get_triggers."""

    async def test_device_not_found(self, hass: HomeAssistant) -> None:
        """Unknown device_id should return empty list."""
        triggers = await async_get_triggers(hass, "unknown_device_id")
        assert triggers == []

    async def test_non_easywave_device(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Device without EASYWAVE identifier returns empty."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={("other_domain", "some_id")},
        )
        triggers = await async_get_triggers(hass, device.id)
        assert triggers == []

    async def test_gateway_device(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Gateway devices get connected/disconnected triggers."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "gateway_serial_gateway")},
        )

        triggers = await async_get_triggers(hass, device.id)
        assert len(triggers) == 2
        trigger_types = {t[CONF_TYPE] for t in triggers}
        assert TRIGGER_TYPE_GATEWAY_CONNECTED in trigger_types
        assert TRIGGER_TYPE_GATEWAY_DISCONNECTED in trigger_types
        # Check subtypes
        subtypes = {t.get(CONF_SUBTYPE) for t in triggers}
        assert "connected" in subtypes
        assert "disconnected" in subtypes

    async def test_sensor_device_returns_empty(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """EWneo sensor devices should NOT get triggers."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "SENSOR001")},
        )

        coordinator = create_mock_coordinator(
            hass,
            mock_config_entry,
            devices={
                "SENSOR001": {
                    "type": "ewneo_sensor",
                    "serial_number": "SENSOR001",
                    "name": "Temp Sensor",
                }
            },
        )
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

        triggers = await async_get_triggers(hass, device.id)
        assert triggers == []

    async def test_transmitter_2tast_switch(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """2-Tast switch transmitter gets on/off button_press triggers."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TX00112233")},
        )

        coordinator = create_mock_coordinator(
            hass,
            mock_config_entry,
            devices={
                "TX00112233": {
                    "type": "ew_transmitter",
                    "serial_number": "TX00112233",
                    "operating_type": "2",
                    "usage_type": "switch",
                    "name": "TX1",
                }
            },
        )
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

        triggers = await async_get_triggers(hass, device.id)
        assert len(triggers) == 2
        subtypes = {t.get(CONF_SUBTYPE) for t in triggers}
        assert "on" in subtypes
        assert "off" in subtypes
        # All should be button_press type
        assert all(t[CONF_TYPE] == TRIGGER_TYPE_BUTTON_PRESS for t in triggers)

    async def test_transmitter_2tast_cover(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """2-Tast cover transmitter gets up/down button_press triggers."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TX00COVER1")},
        )

        coordinator = create_mock_coordinator(
            hass,
            mock_config_entry,
            devices={
                "TX00COVER1": {
                    "type": "ew_transmitter",
                    "serial_number": "TX00COVER1",
                    "operating_type": "2",
                    "usage_type": "cover",
                    "name": "Cover TX",
                }
            },
        )
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

        triggers = await async_get_triggers(hass, device.id)
        assert len(triggers) == 2
        subtypes = {t.get(CONF_SUBTYPE) for t in triggers}
        assert "up" in subtypes
        assert "down" in subtypes

    async def test_transmitter_3tast_cover(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """3-Tast cover transmitter gets up/down/stop triggers."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TX3TAST01")},
        )

        coordinator = create_mock_coordinator(
            hass,
            mock_config_entry,
            devices={
                "TX3TAST01": {
                    "type": "ew_transmitter",
                    "serial_number": "TX3TAST01",
                    "operating_type": "3",
                    "usage_type": "cover",
                    "name": "3-Tast Cover",
                }
            },
        )
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

        triggers = await async_get_triggers(hass, device.id)
        assert len(triggers) == 3
        subtypes = {t.get(CONF_SUBTYPE) for t in triggers}
        assert "up" in subtypes
        assert "down" in subtypes
        assert "stop" in subtypes

    async def test_transmitter_1tast_single_4btn(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """1-Tast single mode 4-button gets channel_on/off per button."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TX1SINGLE4")},
        )

        coordinator = create_mock_coordinator(
            hass,
            mock_config_entry,
            devices={
                "TX1SINGLE4": {
                    "type": "ew_transmitter",
                    "serial_number": "TX1SINGLE4",
                    "operating_type": "1",
                    "usage_type": "switch",
                    "grouping_mode": "single",
                    "button_count": 4,
                    "switch_mode": "impulse",
                    "name": "4-Button Single",
                }
            },
        )
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

        triggers = await async_get_triggers(hass, device.id)
        # 4 buttons × 2 (channel_on + channel_off) = 8
        assert len(triggers) == 8
        types = {t[CONF_TYPE] for t in triggers}
        assert TRIGGER_TYPE_CHANNEL_ON in types
        assert TRIGGER_TYPE_CHANNEL_OFF in types
        subtypes = {t.get(CONF_SUBTYPE) for t in triggers}
        assert "A" in subtypes
        assert "D" in subtypes

    async def test_transmitter_1tast_group_impulse(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """1-Tast group impulse mode gets button_press per button + released."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TX1GROUP4")},
        )

        coordinator = create_mock_coordinator(
            hass,
            mock_config_entry,
            devices={
                "TX1GROUP4": {
                    "type": "ew_transmitter",
                    "serial_number": "TX1GROUP4",
                    "operating_type": "1",
                    "usage_type": "switch",
                    "grouping_mode": "group",
                    "button_count": 4,
                    "switch_mode": "impulse",
                    "name": "4-Button Group",
                }
            },
        )
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

        triggers = await async_get_triggers(hass, device.id)
        # 4 button_press + 1 button_release (released)
        assert len(triggers) == 5
        types = {t[CONF_TYPE] for t in triggers}
        assert TRIGGER_TYPE_BUTTON_PRESS in types
        assert TRIGGER_TYPE_BUTTON_RELEASE in types
        # Check released subtype
        release_triggers = [t for t in triggers if t[CONF_TYPE] == TRIGGER_TYPE_BUTTON_RELEASE]
        assert len(release_triggers) == 1
        assert release_triggers[0][CONF_SUBTYPE] == "released"

    async def test_transmitter_1tast_group_permanent(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """1-Tast group permanent mode: button_press per button, NO released."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TX1GPERM4")},
        )

        coordinator = create_mock_coordinator(
            hass,
            mock_config_entry,
            devices={
                "TX1GPERM4": {
                    "type": "ew_transmitter",
                    "serial_number": "TX1GPERM4",
                    "operating_type": "1",
                    "usage_type": "switch",
                    "grouping_mode": "group",
                    "button_count": 4,
                    "switch_mode": "permanent",
                    "name": "4-Button Group Permanent",
                }
            },
        )
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

        triggers = await async_get_triggers(hass, device.id)
        # 4 button_press only, no release
        assert len(triggers) == 4
        types = {t[CONF_TYPE] for t in triggers}
        assert TRIGGER_TYPE_BUTTON_PRESS in types
        assert TRIGGER_TYPE_BUTTON_RELEASE not in types

    async def test_transmitter_1btn_detected_button(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """1-button TX with detected_button_type limits to that button only."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TX1BTN_C")},
        )

        coordinator = create_mock_coordinator(
            hass,
            mock_config_entry,
            devices={
                "TX1BTN_C": {
                    "type": "ew_transmitter",
                    "serial_number": "TX1BTN_C",
                    "operating_type": "1",
                    "usage_type": "switch",
                    "grouping_mode": "single",
                    "button_count": 1,
                    "switch_mode": "impulse",
                    "detected_button_type": "C",
                    "name": "1-Button C",
                }
            },
        )
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

        triggers = await async_get_triggers(hass, device.id)
        # Only button C: channel_on + channel_off = 2
        assert len(triggers) == 2
        subtypes = {t.get(CONF_SUBTYPE) for t in triggers}
        assert "C" in subtypes

    async def test_transmitter_no_device_info_fallback(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Transmitter without device_info falls back to operating_type=1."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TX_UNKNOWN")},
        )
        # No coordinator data set → _get_device_info_for_serial returns None
        triggers = await async_get_triggers(hass, device.id)
        # Falls back to operating_type=1 single mode, 4 buttons → 8 triggers
        assert len(triggers) == 8

    async def test_extra_data_fields(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Device info from extra_data is used when top-level lacks operating_type."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TX_EXTRA")},
        )

        coordinator = create_mock_coordinator(
            hass,
            mock_config_entry,
            devices={
                "TX_EXTRA": {
                    "type": "ew_transmitter",
                    "serial_number": "TX_EXTRA",
                    "name": "TX Extra",
                    "extra_data": {
                        "operating_type": "3",
                        "usage_type": "cover",
                    },
                }
            },
        )
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

        triggers = await async_get_triggers(hass, device.id)
        assert len(triggers) == 3
        subtypes = {t.get(CONF_SUBTYPE) for t in triggers}
        assert "stop" in subtypes


# ═══════════════════════════════════════════════════════
# async_attach_trigger
# ═══════════════════════════════════════════════════════


class TestAsyncAttachTrigger:
    """Tests for async_attach_trigger."""

    async def test_attach_button_press(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Attaching a button_press trigger returns a callback."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "AABB0001")},
        )

        config = {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device.id,
            CONF_TYPE: TRIGGER_TYPE_BUTTON_PRESS,
            CONF_SUBTYPE: "on",
        }

        action = AsyncMock()
        trigger_info = MagicMock()
        trigger_info.data = {}

        result = await async_attach_trigger(hass, config, action, trigger_info)
        assert callable(result)

    async def test_attach_gateway_connected(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Attaching a gateway connected trigger returns a callback."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "gw_serial_gateway")},
        )

        config = {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device.id,
            CONF_TYPE: TRIGGER_TYPE_GATEWAY_CONNECTED,
        }

        action = AsyncMock()
        trigger_info = MagicMock()
        trigger_info.data = {}

        result = await async_attach_trigger(hass, config, action, trigger_info)
        assert callable(result)

    async def test_attach_gateway_disconnected(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Attaching a gateway disconnected trigger returns a callback."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "gw_serial_gateway")},
        )

        config = {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device.id,
            CONF_TYPE: TRIGGER_TYPE_GATEWAY_DISCONNECTED,
        }

        action = AsyncMock()
        trigger_info = MagicMock()
        trigger_info.data = {}

        result = await async_attach_trigger(hass, config, action, trigger_info)
        assert callable(result)

    async def test_attach_state_subtypes(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Subtypes on/off/up/down/stop use action_label matching."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TXSTATE01")},
        )

        for subtype in ["on", "off", "up", "down", "stop"]:
            config = {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device.id,
                CONF_TYPE: TRIGGER_TYPE_BUTTON_PRESS,
                CONF_SUBTYPE: subtype,
            }
            action = AsyncMock()
            trigger_info = MagicMock()
            trigger_info.data = {}
            result = await async_attach_trigger(hass, config, action, trigger_info)
            assert callable(result), f"Failed for subtype {subtype}"

    async def test_attach_group_letter_subtypes(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Lowercase a/b/c/d subtypes use action_label matching."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TXGRP01")},
        )

        for subtype in ["a", "b", "c", "d"]:
            config = {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device.id,
                CONF_TYPE: TRIGGER_TYPE_BUTTON_PRESS,
                CONF_SUBTYPE: subtype,
            }
            action = AsyncMock()
            trigger_info = MagicMock()
            trigger_info.data = {}
            result = await async_attach_trigger(hass, config, action, trigger_info)
            assert callable(result)

    async def test_attach_released_subtype(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """'released' subtype matches by is_release."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TXREL01")},
        )

        config = {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device.id,
            CONF_TYPE: TRIGGER_TYPE_BUTTON_RELEASE,
            CONF_SUBTYPE: "released",
        }

        action = AsyncMock()
        trigger_info = MagicMock()
        trigger_info.data = {}
        result = await async_attach_trigger(hass, config, action, trigger_info)
        assert callable(result)

    async def test_attach_uppercase_button_subtypes(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Uppercase A/B/C/D subtypes match by button index."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TXSINGLE1")},
        )

        for subtype in ["A", "B", "C", "D"]:
            config = {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device.id,
                CONF_TYPE: TRIGGER_TYPE_CHANNEL_ON,
                CONF_SUBTYPE: subtype,
            }
            action = AsyncMock()
            trigger_info = MagicMock()
            trigger_info.data = {}
            result = await async_attach_trigger(hass, config, action, trigger_info)
            assert callable(result)

    async def test_attach_device_not_found(
        self, hass: HomeAssistant
    ) -> None:
        """Missing device returns a noop lambda."""
        config = {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: "nonexistent_device",
            CONF_TYPE: TRIGGER_TYPE_BUTTON_PRESS,
            CONF_SUBTYPE: "A",
        }
        action = AsyncMock()
        trigger_info = MagicMock()
        trigger_info.data = {}
        result = await async_attach_trigger(hass, config, action, trigger_info)
        assert callable(result)
        # Noop lambda should not raise
        result()

    async def test_attach_device_no_serial(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Device without EASYWAVE serial returns noop lambda."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={("other_domain", "other_id")},
        )

        config = {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device.id,
            CONF_TYPE: TRIGGER_TYPE_BUTTON_PRESS,
            CONF_SUBTYPE: "A",
        }
        action = AsyncMock()
        trigger_info = MagicMock()
        trigger_info.data = {}
        result = await async_attach_trigger(hass, config, action, trigger_info)
        assert callable(result)
        result()

    async def test_attach_custom_subtype_fallback(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Unknown subtype falls back to action_label matching."""
        mock_config_entry.add_to_hass(hass)
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "TXCUSTOM1")},
        )

        config = {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device.id,
            CONF_TYPE: TRIGGER_TYPE_BUTTON_PRESS,
            CONF_SUBTYPE: "custom_label",
        }
        action = AsyncMock()
        trigger_info = MagicMock()
        trigger_info.data = {}
        result = await async_attach_trigger(hass, config, action, trigger_info)
        assert callable(result)


# ═══════════════════════════════════════════════════════
# async_get_trigger_capabilities
# ═══════════════════════════════════════════════════════


class TestAsyncGetTriggerCapabilities:
    """Tests for async_get_trigger_capabilities."""

    async def test_returns_empty_dict(self, hass: HomeAssistant) -> None:
        config = {
            CONF_TYPE: TRIGGER_TYPE_BUTTON_PRESS,
            CONF_SUBTYPE: "A",
        }
        result = await async_get_trigger_capabilities(hass, config)
        assert result == {}

    async def test_gateway_trigger(self, hass: HomeAssistant) -> None:
        config = {
            CONF_TYPE: TRIGGER_TYPE_GATEWAY_CONNECTED,
        }
        result = await async_get_trigger_capabilities(hass, config)
        assert result == {}

    async def test_channel_on_trigger(self, hass: HomeAssistant) -> None:
        config = {
            CONF_TYPE: TRIGGER_TYPE_CHANNEL_ON,
            CONF_SUBTYPE: "B",
        }
        result = await async_get_trigger_capabilities(hass, config)
        assert isinstance(result, dict)
