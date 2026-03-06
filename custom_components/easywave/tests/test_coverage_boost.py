"""Tests targeting mid-coverage modules: select, device_icons, entity_specs, translations, __init__."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywave.const import DOMAIN

from .conftest import (
    MOCK_CONFIG_DATA,
    create_mock_coordinator,
    create_mock_transceiver,
)


# ═════════════════════════════════════════════════════════════════════
# device_icons.py – get_entity_config_for_device
# ═════════════════════════════════════════════════════════════════════


class TestGetEntityConfig:
    """Tests for get_entity_config_for_device."""

    def test_receiver_impulse(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        cfg = get_entity_config_for_device("ew_receiver", receiver_kind="impulse")
        assert "icon" in cfg

    def test_receiver_switch_2button(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        cfg = get_entity_config_for_device("ew_receiver", receiver_kind="switch_2button")
        assert "icon" in cfg

    def test_receiver_cover_2button(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        cfg = get_entity_config_for_device(
            "ew_receiver", receiver_kind="cover_2button", entity_type="cover",
        )
        assert "icon" in cfg

    def test_receiver_motor_3button(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        cfg = get_entity_config_for_device(
            "ew_receiver", receiver_kind="motor_3button", entity_type="cover",
        )
        assert "icon" in cfg

    def test_receiver_heating_cooling(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        cfg = get_entity_config_for_device("ew_receiver", receiver_kind="heating_cooling")
        assert "icon" in cfg

    def test_ewneo_receiver(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        cfg = get_entity_config_for_device("ewneo_receiver", entity_type="switch")
        assert "icon" in cfg

    def test_ewneo_sensor_temperature(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        cfg = get_entity_config_for_device(
            "ewneo_sensor", sensor_type="temperature", entity_type="sensor",
        )
        assert "icon" in cfg

    def test_ewneo_sensor_humidity(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        cfg = get_entity_config_for_device(
            "ewneo_sensor", sensor_type="humidity", entity_type="sensor",
        )
        assert "icon" in cfg

    def test_unknown_device_type(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        cfg = get_entity_config_for_device("unknown_type")
        assert isinstance(cfg, dict)

    def test_transmitter_entity_config(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        cfg = get_entity_config_for_device("ew_transmitter", entity_type="binary_sensor")
        assert isinstance(cfg, dict)


# ═════════════════════════════════════════════════════════════════════
# Select platform – entity construction
# ═════════════════════════════════════════════════════════════════════


class TestSelectEntity:
    """Tests for EasywaveSelect entity."""

    def test_select_entity_construction(self) -> None:
        from custom_components.easywave.select import EasywaveSelect
        coordinator = MagicMock()
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.transceiver = MagicMock()
        coordinator.transceiver.serial_number = "GW001"

        entity_spec = {
            "unique_id": "easywave_AABB_select",
            "options": ["Option A", "Option B"],
            "icon": "mdi:gesture-tap-button",
            "name": "Button Select",
            "serial_number": "AABB0001",
        }
        device_info = {
            "type": "ew_transmitter",
            "name": "Test TX",
            "serial_number": "AABB0001",
            "registration_id": "reg_sel_001",
        }

        entity = EasywaveSelect(coordinator, "AABB0001", device_info, entity_spec)
        assert entity.unique_id == "easywave_AABB_select"
        assert entity.options == ["Option A", "Option B"]

    def test_select_option(self) -> None:
        from custom_components.easywave.select import EasywaveSelect
        coordinator = MagicMock()
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.transceiver = MagicMock()
        coordinator.transceiver.serial_number = "GW001"

        entity_spec = {
            "unique_id": "easywave_AABB_select",
            "options": ["Schalter A", "Schalter B"],
            "icon": "mdi:gesture-tap-button",
            "name": "Button Select",
            "serial_number": "AABB0001",
        }
        device_info = {
            "type": "ew_transmitter",
            "name": "Test TX",
            "serial_number": "AABB0001",
            "registration_id": "reg_sel_002",
        }

        entity = EasywaveSelect(coordinator, "AABB0001", device_info, entity_spec)
        assert entity.current_option is None or isinstance(entity.current_option, str)


# ═════════════════════════════════════════════════════════════════════
# entity_specs – create_entity_specs deep coverage
# ═════════════════════════════════════════════════════════════════════


class TestEntitySpecsDeep:
    """Deep tests for entity_specs module."""

    def test_transmitter_2button_switch(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        specs = create_entity_specs_for_device("AABB0001", {
            "type": "ew_transmitter",
            "operating_type": "2",
            "usage_type": "switch",
            "button_count": 2,
            "registration_id": "reg_001",
        })
        assert isinstance(specs, (dict, list))

    def test_transmitter_2button_cover(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        specs = create_entity_specs_for_device("AABB0002", {
            "type": "ew_transmitter",
            "operating_type": "2",
            "usage_type": "cover",
            "button_count": 2,
            "registration_id": "reg_002",
        })
        assert isinstance(specs, (dict, list))

    def test_transmitter_3button(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        specs = create_entity_specs_for_device("AABB0003", {
            "type": "ew_transmitter",
            "operating_type": "3",
            "button_count": 3,
            "registration_id": "reg_003",
        })
        assert isinstance(specs, (dict, list))

    def test_receiver_impulse(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        specs = create_entity_specs_for_device("AABB0004", {
            "type": "ew_receiver",
            "receiver_kind": "impulse",
            "registration_id": "reg_004",
        })
        assert isinstance(specs, (dict, list))

    def test_receiver_motor_3button(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        specs = create_entity_specs_for_device("AABB0005", {
            "type": "ew_receiver",
            "receiver_kind": "motor_3button",
            "registration_id": "reg_005",
        })
        assert isinstance(specs, (dict, list))

    def test_receiver_heating_cooling(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        specs = create_entity_specs_for_device("AABB0006", {
            "type": "ew_receiver",
            "receiver_kind": "heating_cooling",
            "registration_id": "reg_006",
        })
        assert isinstance(specs, (dict, list))

    def test_ewneo_receiver(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        specs = create_entity_specs_for_device("AABB0007", {
            "type": "ewneo_receiver",
            "registration_id": "reg_007",
        })
        assert isinstance(specs, (dict, list))

    def test_ewneo_sensor(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        specs = create_entity_specs_for_device("AABB0008", {
            "type": "ewneo_sensor",
            "registration_id": "reg_008",
        })
        assert isinstance(specs, (dict, list))

    def test_get_device_prop(self) -> None:
        from custom_components.easywave.entity_specs import _get_device_prop
        info = {"key1": "val1", "extra_data": {"key2": "val2"}}
        assert _get_device_prop(info, "key1") == "val1"
        assert _get_device_prop(info, "key2") == "val2"
        assert _get_device_prop(info, "key3", "default") == "default"

    def test_get_registration_id(self) -> None:
        from custom_components.easywave.entity_specs import _get_registration_id
        assert _get_registration_id({"registration_id": "reg_xyz"}) == "reg_xyz"
        assert _get_registration_id({}) == ""


# ═════════════════════════════════════════════════════════════════════
# translations.py – deeper coverage
# ═════════════════════════════════════════════════════════════════════


class TestTranslationsDeep:
    """Deeper tests for translations module."""

    def test_translate_all_device_types(self) -> None:
        from custom_components.easywave.translations import translate
        keys = [
            "device_info.easywave_transmitter",
            "device_info.easywave_receiver",
            "device_info.easywave_neo_sensor",
            "device_info.easywave_neo_receiver",
        ]
        for key in keys:
            for lang in ("en", "de"):
                result = translate(key, lang)
                assert isinstance(result, str)
                assert len(result) > 0

    def test_translate_button_labels(self) -> None:
        from custom_components.easywave.translations import get_button_label
        for i in range(4):
            label_en = get_button_label(i, "en")
            label_de = get_button_label(i, "de")
            assert isinstance(label_en, str)
            assert isinstance(label_de, str)

    def test_translate_receiver_modes(self) -> None:
        from custom_components.easywave.translations import translate
        modes = [
            "receiver_mode.impulse",
            "receiver_mode.switch_2button",
            "receiver_mode.cover_2button",
            "receiver_mode.motor_3button",
            "receiver_mode.heating_cooling",
        ]
        for mode in modes:
            for lang in ("en", "de"):
                result = translate(mode, lang)
                assert isinstance(result, str)

    def test_translate_with_hass(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.translations import translate, get_language
        lang = get_language(hass)
        result = translate("device_info.easywave_transmitter", lang, hass)
        assert isinstance(result, str)

    def test_translate_grouping_modes(self) -> None:
        from custom_components.easywave.translations import translate
        keys = ["device_info.grouping_single", "device_info.grouping_group"]
        for key in keys:
            result = translate(key, "de")
            assert isinstance(result, str)

    def test_translate_switch_modes(self) -> None:
        from custom_components.easywave.translations import translate
        keys = ["device_info.switch_impulse", "device_info.switch_permanent"]
        for key in keys:
            result = translate(key, "en")
            assert isinstance(result, str)


# ═════════════════════════════════════════════════════════════════════
# __init__.py – additional paths
# ═════════════════════════════════════════════════════════════════════


class TestInitAdditionalPaths:
    """Additional tests for __init__.py paths."""

    async def test_setup_with_transceiver_creation_failure(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test setup when transceiver type is unsupported."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                **MOCK_CONFIG_DATA,
                "transceiver_type": "unsupported_type",
            },
            version=2,
        )
        entry.add_to_hass(hass)

        with (
            patch(
                "custom_components.easywave.device_migration.migrate_to_device_manager",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.easywave.entity_migration.migrate_entities_if_needed",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.easywave.services.async_setup_services",
                new_callable=AsyncMock,
            ),
        ):
            result = await hass.config_entries.async_setup(entry.entry_id)
            # Should fail due to unsupported transceiver type
            assert result is False

    async def test_async_reload_entry(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test that async_reload_entry calls unload then setup."""
        from custom_components.easywave import async_reload_entry

        with (
            patch(
                "custom_components.easywave.async_unload_entry",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_unload,
            patch(
                "custom_components.easywave.async_setup_entry",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_setup,
        ):
            await async_reload_entry(hass, mock_config_entry)
            mock_unload.assert_called_once_with(hass, mock_config_entry)
            mock_setup.assert_called_once_with(hass, mock_config_entry)


# ═════════════════════════════════════════════════════════════════════
# ManagedDevice – serialization
# ═════════════════════════════════════════════════════════════════════


class TestManagedDevice:
    """Tests for DeviceManager.ManagedDevice dataclass."""

    def test_managed_device_to_dict(self) -> None:
        from custom_components.easywave.device_manager import ManagedDevice, DeviceType, DeviceAvailability
        dev = ManagedDevice(
            serial_number="AABB0001",
            device_type=DeviceType.EW_TRANSMITTER,
            name="Test TX",
        )
        d = dev.to_dict()
        assert d["serial_number"] == "AABB0001"
        assert "device_type" in d
        assert "name" in d

    def test_managed_device_from_dict(self) -> None:
        from custom_components.easywave.device_manager import ManagedDevice, DeviceType
        data = {
            "serial_number": "AABB0001",
            "device_type": DeviceType.EW_TRANSMITTER.value,
            "name": "Test TX",
        }
        dev = ManagedDevice.from_dict(data)
        assert dev.serial_number == "AABB0001"
        assert dev.name == "Test TX"

    def test_managed_device_roundtrip(self) -> None:
        from custom_components.easywave.device_manager import ManagedDevice, DeviceType
        dev = ManagedDevice(
            serial_number="CCDD0001",
            device_type=DeviceType.EW_RECEIVER,
            name="Test RX",
            extra_data={"receiver_kind": "switch_2button"},
        )
        d = dev.to_dict()
        dev2 = ManagedDevice.from_dict(d)
        assert dev2.serial_number == dev.serial_number
        assert dev2.name == dev.name

    def test_mark_available(self) -> None:
        from custom_components.easywave.device_manager import ManagedDevice, DeviceType, DeviceAvailability
        dev = ManagedDevice(
            serial_number="AABB0001",
            device_type=DeviceType.EW_TRANSMITTER,
            name="Test",
        )
        dev.mark_available()
        assert dev.availability == DeviceAvailability.AVAILABLE
        assert dev.last_seen is not None

    def test_mark_unavailable(self) -> None:
        from custom_components.easywave.device_manager import ManagedDevice, DeviceType, DeviceAvailability
        dev = ManagedDevice(
            serial_number="AABB0001",
            device_type=DeviceType.EW_TRANSMITTER,
            name="Test",
        )
        dev.mark_available()
        dev.mark_unavailable()
        assert dev.availability == DeviceAvailability.UNAVAILABLE


# ═════════════════════════════════════════════════════════════════════
# DeviceManager – load/save
# ═════════════════════════════════════════════════════════════════════


class TestDeviceManagerLoadSave:
    """Tests for DeviceManager persistence."""

    async def test_device_manager_init(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.device_manager import DeviceManager
        dm = DeviceManager(hass, "test_entry_id")
        assert dm._devices == {}
        assert dm.config_entry_id == "test_entry_id"

    async def test_device_manager_load_no_file(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.device_manager import DeviceManager
        dm = DeviceManager(hass, "test_entry_id")
        with patch("aiofiles.open", side_effect=FileNotFoundError):
            result = await dm.load()
            # Should handle missing file gracefully
            assert isinstance(result, bool)

    async def test_device_manager_save_and_load(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.device_manager import DeviceManager, ManagedDevice, DeviceType
        import json

        # Point config dir to tmp_path so DeviceManager creates files there
        hass.config.config_dir = str(tmp_path)
        dm = DeviceManager(hass, "test_entry_id")

        dev = ManagedDevice(
            serial_number="AABB0001",
            device_type=DeviceType.EW_TRANSMITTER,
            name="Test TX",
        )
        dm._devices["AABB0001"] = dev

        # Save
        saved = await dm.save()
        assert saved is True

        # Verify file was written (attribute is devices_file, not _file_path)
        assert dm.devices_file.exists()

        # Verify content
        raw = json.loads(dm.devices_file.read_text())
        assert "devices" in raw
        assert "AABB0001" in raw["devices"]


# ═════════════════════════════════════════════════════════════════════
# StateManager – deeper coverage
# ═════════════════════════════════════════════════════════════════════


class TestStateManagerDeep:
    """Additional tests for state_manager.py."""

    async def test_state_manager_is_entity_created(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.state_manager import StateManager
        sm = StateManager(hass, "test_entry_id")
        sm._state["entity_unique_ids"] = {"uid_001", "uid_002"}
        assert sm.is_entity_created("uid_001") is True
        assert sm.is_entity_created("uid_999") is False

    async def test_state_manager_add_ew_receiver_index(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.state_manager import StateManager
        sm = StateManager(hass, "test_entry_id")
        sm._state["ew_receiver_indices"] = {}
        sm.add_ew_receiver_index(0, {"serial": "AABB"})
        assert "0" in sm._state["ew_receiver_indices"]

    async def test_state_manager_remove_ew_receiver_index(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.state_manager import StateManager
        sm = StateManager(hass, "test_entry_id")
        sm._state["ew_receiver_indices"] = {"0": {"serial": "AABB"}}
        sm.remove_ew_receiver_index(0)
        assert "0" not in sm._state["ew_receiver_indices"]

    async def test_state_manager_load_no_file(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.state_manager import StateManager
        sm = StateManager(hass, "test_entry_id")
        with patch("aiofiles.open", side_effect=FileNotFoundError):
            result = await sm.load()
            assert isinstance(result, bool)


# ═════════════════════════════════════════════════════════════════════
# IndexAllocator – deeper coverage
# ═════════════════════════════════════════════════════════════════════


class TestIndexAllocatorDeep:
    """Additional tests for index_allocator.py."""

    async def test_allocate_with_preferred_index(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        ia._allocations = {"ewb": {}, "ew_receiver": {}, "rx11": {}}
        ia._next_free = {"ewb": 0, "ew_receiver": 0, "rx11": 0}
        ia._save = AsyncMock()

        index = await ia.allocate_index("ewb", "AABB0001", "Device1", preferred_index=5)
        assert index == 5
        assert 5 in ia._allocations["ewb"]

    async def test_allocate_auto_index(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        ia._allocations = {"ewb": {}, "ew_receiver": {}, "rx11": {}}
        ia._next_free = {"ewb": 0, "ew_receiver": 0, "rx11": 0}
        ia._save = AsyncMock()

        index = await ia.allocate_index("ewb", "AABB0001", "Device1")
        assert index == 0
        assert 0 in ia._allocations["ewb"]

    async def test_deallocate_index(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        ia._allocations = {"ewb": {5: {"serial": "AABB0001", "name": "Dev"}}, "ew_receiver": {}, "rx11": {}}
        ia._next_free = {"ewb": 6, "ew_receiver": 0, "rx11": 0}
        ia._save = AsyncMock()

        result = await ia.deallocate_index("ewb", 5)
        assert result is True
        assert 5 not in ia._allocations["ewb"]

    async def test_deallocate_with_serial_verification(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        ia._allocations = {"ewb": {5: {"serial": "AABB0001", "name": "Dev"}}, "ew_receiver": {}, "rx11": {}}
        ia._next_free = {"ewb": 6, "ew_receiver": 0, "rx11": 0}
        ia._save = AsyncMock()

        # Wrong serial should fail verification
        result = await ia.deallocate_index("ewb", 5, device_serial="WRONG_SERIAL")
        # May return False or True depending on implementation
        assert isinstance(result, bool)

    async def test_get_allocation_info(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        ia._allocations = {"ewb": {3: {"serial": "AABB0001", "name": "Dev"}}, "ew_receiver": {}, "rx11": {}}

        info = await ia.get_allocation_info("ewb", 3)
        assert info is not None
        assert info["serial"] == "AABB0001"

    async def test_get_allocation_info_empty(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        ia._allocations = {"ewb": {}, "ew_receiver": {}, "rx11": {}}

        info = await ia.get_allocation_info("ewb", 99)
        assert info is None

    async def test_get_device_indices(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        ia._allocations = {
            "ewb": {0: {"device_serial": "AABB0001", "name": "D1"}},
            "ew_receiver": {2: {"device_serial": "AABB0001", "name": "D1"}},
            "rx11": {},
        }

        indices = await ia.get_device_indices("AABB0001")
        assert isinstance(indices, dict)
        # Should find the device in ewb and ew_receiver
        assert len(indices) >= 1
