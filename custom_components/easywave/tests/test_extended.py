"""Tests for translations, device_trigger, and async_remove_entry."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywave.const import (
    DOMAIN,
    DEVICE_TYPES,
    DEVICE_TYPE_CODE_MAP,
    BUTTON_LABELS,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_SCAN_INTERVAL,
    EVENT_DEVICE_ADDED,
    EVENT_DEVICE_REMOVED,
    EVENT_DEVICE_UPDATED,
    EVENT_DEVICE_STATE_UPDATE,
    EVENT_TELEGRAM_RECEIVED,
    EVENT_SENSOR_ADDED,
    SUPPORTED_USB_IDS,
    USB_DEVICE_NAMES,
)
from custom_components.easywave.translations import (
    translate,
    get_language,
    get_button_label,
    DEFAULT_LANGUAGE,
    t_receiver,
    t_transmitter,
    t_sensor_device,
)

from .conftest import (
    MOCK_CONFIG_DATA,
    create_mock_coordinator,
    create_mock_transceiver,
)


# ═════════════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════════════

class TestConstantsExtended:
    """Extended tests for const.py."""

    def test_device_types_not_empty(self) -> None:
        assert len(DEVICE_TYPES) > 0

    def test_device_type_code_map_not_empty(self) -> None:
        assert len(DEVICE_TYPE_CODE_MAP) > 0

    def test_button_labels_defined(self) -> None:
        assert isinstance(BUTTON_LABELS, dict)

    def test_default_scan_interval(self) -> None:
        assert DEFAULT_SCAN_INTERVAL > 0

    def test_device_scan_interval(self) -> None:
        assert DEVICE_SCAN_INTERVAL is not None

    def test_event_constants_defined(self) -> None:
        assert isinstance(EVENT_DEVICE_ADDED, str)
        assert isinstance(EVENT_DEVICE_REMOVED, str)
        assert isinstance(EVENT_DEVICE_UPDATED, str)
        assert isinstance(EVENT_DEVICE_STATE_UPDATE, str)
        assert isinstance(EVENT_TELEGRAM_RECEIVED, str)
        assert isinstance(EVENT_SENSOR_ADDED, str)

    def test_supported_usb_ids_structure(self) -> None:
        for usb_id in SUPPORTED_USB_IDS:
            assert isinstance(usb_id, (tuple, list, dict))
            if isinstance(usb_id, (tuple, list)):
                assert len(usb_id) >= 2
            else:
                assert "vid" in usb_id
                assert "pid" in usb_id

    def test_usb_device_names_not_empty(self) -> None:
        assert len(USB_DEVICE_NAMES) > 0


# ═════════════════════════════════════════════════════════════════════
# Translations
# ═════════════════════════════════════════════════════════════════════

class TestTranslations:
    """Tests for translations.py."""

    def test_default_language(self) -> None:
        assert DEFAULT_LANGUAGE in ("en", "de")

    def test_translate_known_key_en(self) -> None:
        result = translate("device_info.easywave_transmitter", "en")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_translate_known_key_de(self) -> None:
        result = translate("device_info.easywave_transmitter", "de")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_translate_unknown_key_returns_key(self) -> None:
        result = translate("nonexistent.key", "en")
        # Should return the key itself or a reasonable fallback
        assert isinstance(result, str)

    def test_get_language_returns_string(self, hass: HomeAssistant) -> None:
        lang = get_language(hass)
        assert isinstance(lang, str)
        assert len(lang) >= 2

    def test_get_button_label(self) -> None:
        label = get_button_label(0, "en")
        assert isinstance(label, str)

    def test_t_receiver(self) -> None:
        result_en = t_receiver("en")
        result_de = t_receiver("de")
        assert isinstance(result_en, str)
        assert isinstance(result_de, str)

    def test_t_transmitter(self) -> None:
        result_en = t_transmitter("en")
        result_de = t_transmitter("de")
        assert isinstance(result_en, str)
        assert isinstance(result_de, str)

    def test_t_sensor_device(self) -> None:
        result_en = t_sensor_device("en")
        result_de = t_sensor_device("de")
        assert isinstance(result_en, str)
        assert isinstance(result_de, str)

    def test_translate_with_kwargs(self) -> None:
        # Test translation with format parameters
        result = translate("device_info.buttons", "en", count=4)
        assert isinstance(result, str)

    def test_translate_receiver_kinds(self) -> None:
        kinds = [
            "device_info.receiver_impulse",
            "device_info.receiver_switch_2button",
            "device_info.receiver_cover_2button",
            "device_info.receiver_motor_3button",
        ]
        for key in kinds:
            result = translate(key, "en")
            assert isinstance(result, str)
            assert len(result) > 0

    def test_translate_operating_types(self) -> None:
        for i in range(1, 4):
            result = translate(f"device_info.operating_type_{i}", "en")
            assert isinstance(result, str)
            assert len(result) > 0


# ═════════════════════════════════════════════════════════════════════
# async_remove_entry
# ═════════════════════════════════════════════════════════════════════

class TestAsyncRemoveEntry:
    """Tests for async_remove_entry (full cleanup)."""

    async def test_remove_entry_without_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test remove_entry when no coordinator exists."""
        from custom_components.easywave import async_remove_entry

        mock_config_entry.add_to_hass(hass)
        hass.data.setdefault(DOMAIN, {})

        # Should not raise even without coordinator
        await async_remove_entry(hass, mock_config_entry)

    async def test_remove_entry_with_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test remove_entry clears coordinator data."""
        from custom_components.easywave import async_remove_entry

        mock_config_entry.add_to_hass(hass)
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        coordinator._devices_with_fired_events = set(["dev1"])
        coordinator._known_devices = set(["dev1"])
        coordinator.devices = {"dev1": {}}
        coordinator._registered_devices = {"dev1": {}}
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

        with patch(
            "custom_components.easywave.entity_registry.reset_entity_registry",
        ):
            await async_remove_entry(hass, mock_config_entry)

        assert len(coordinator.devices) == 0
        assert len(coordinator._registered_devices) == 0


# ═════════════════════════════════════════════════════════════════════
# Device icons
# ═════════════════════════════════════════════════════════════════════

class TestDeviceIcons:
    """Tests for device_icons.py."""

    def test_device_icons_import(self) -> None:
        from custom_components.easywave.const import DEVICE_ICONS
        assert isinstance(DEVICE_ICONS, dict)
        assert len(DEVICE_ICONS) > 0

    def test_known_device_types_have_icons(self) -> None:
        from custom_components.easywave.const import DEVICE_ICONS
        for device_type in ["ew_transmitter", "ew_receiver"]:
            if device_type in DEVICE_ICONS:
                assert DEVICE_ICONS[device_type].startswith("mdi:")


# ═════════════════════════════════════════════════════════════════════
# Entity specs
# ═════════════════════════════════════════════════════════════════════

class TestEntitySpecs:
    """Tests for entity_specs.py."""

    def test_create_entity_specs_import(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        assert callable(create_entity_specs_for_device)

    def test_create_entity_specs_ew_transmitter(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        device_info = {
            "type": "ew_transmitter",
            "operating_type": "1",
            "button_count": 1,
            "grouping_mode": "single",
            "switch_mode": "impulse",
            "registration_id": "reg_test_001",
        }
        specs = create_entity_specs_for_device("AABBCCDD", device_info)
        assert isinstance(specs, (list, dict))

    def test_create_entity_specs_ew_receiver(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        device_info = {
            "type": "ew_receiver",
            "receiver_kind": "switch_2button",
            "registration_id": "reg_test_002",
        }
        specs = create_entity_specs_for_device("AABBCCDD", device_info)
        assert isinstance(specs, (list, dict))

    def test_create_entity_specs_unknown_type(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        device_info = {
            "type": "unknown_type",
        }
        specs = create_entity_specs_for_device("AABBCCDD", device_info)
        assert isinstance(specs, (list, dict))


# ═════════════════════════════════════════════════════════════════════
# Transceiver types
# ═════════════════════════════════════════════════════════════════════

class TestTransceiverTypes:
    """Tests for transceiver type enums."""

    def test_transceiver_type_rx11(self) -> None:
        from custom_components.easywave.transceivers import TransceiverType
        assert TransceiverType.RX11.value == "rx11"

    def test_transceiver_type_from_string(self) -> None:
        from custom_components.easywave.transceivers import TransceiverType
        assert TransceiverType("rx11") == TransceiverType.RX11

    def test_transceiver_type_invalid(self) -> None:
        from custom_components.easywave.transceivers import TransceiverType
        with pytest.raises(ValueError):
            TransceiverType("nonexistent")


# ═════════════════════════════════════════════════════════════════════
# Build model description edge cases
# ═════════════════════════════════════════════════════════════════════

class TestBuildModelDescriptionEdgeCases:
    """Edge-case tests for build_model_description."""

    def test_transmitter_operating_type_1_with_details(self) -> None:
        from custom_components.easywave.helpers import build_model_description
        desc = build_model_description("ew_transmitter", {
            "operating_type": "1",
            "grouping_mode": "single",
            "switch_mode": "impulse",
            "button_count": 2,
        }, "en")
        assert isinstance(desc, str)
        assert len(desc) > 0

    def test_transmitter_operating_type_2(self) -> None:
        from custom_components.easywave.helpers import build_model_description
        desc = build_model_description("ew_transmitter", {
            "operating_type": "2",
            "button_count": 4,
        }, "en")
        assert isinstance(desc, str)

    def test_transmitter_operating_type_3(self) -> None:
        from custom_components.easywave.helpers import build_model_description
        desc = build_model_description("ew_transmitter", {
            "operating_type": "3",
        }, "en")
        assert isinstance(desc, str)

    def test_receiver_with_kind(self) -> None:
        from custom_components.easywave.helpers import build_model_description
        kinds = ["impulse", "switch_2button", "cover_2button", "motor_3button", 
                 "heating_cooling", "universal_4button"]
        for kind in kinds:
            desc = build_model_description("ew_receiver", {"receiver_kind": kind}, "en")
            assert isinstance(desc, str)
            assert len(desc) > 0

    def test_receiver_with_channels(self) -> None:
        from custom_components.easywave.helpers import build_model_description
        desc = build_model_description("ew_receiver", {"channels": 4}, "en")
        assert isinstance(desc, str)

    def test_ewneo_sensor(self) -> None:
        from custom_components.easywave.helpers import build_model_description
        desc = build_model_description("ewneo_sensor", {}, "en")
        assert isinstance(desc, str)

    def test_grouping_group_mode(self) -> None:
        from custom_components.easywave.helpers import build_model_description
        desc = build_model_description("ew_transmitter", {
            "operating_type": "1",
            "grouping_mode": "group",
            "switch_mode": "permanent",
            "button_count": 1,
        }, "de")
        assert isinstance(desc, str)
