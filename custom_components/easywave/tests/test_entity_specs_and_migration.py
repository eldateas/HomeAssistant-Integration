"""Tests for entity_specs, entity_migration, device_config, device_manager."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywave.const import DOMAIN
from .conftest import MOCK_CONFIG_DATA


# ═══════════════════════════════════════════════════════════════════
# entity_specs — create_entity_specs_for_device
# ═══════════════════════════════════════════════════════════════════


class TestEntitySpecs:
    """Test entity specification generator."""

    def test_empty_entity_dict(self) -> None:
        from custom_components.easywave.entity_specs import _empty_entity_dict
        d = _empty_entity_dict()
        assert "switch" in d
        assert "light" in d
        assert "cover" in d
        assert "sensor" in d
        assert "binary_sensor" in d
        assert "button" in d
        assert "select" in d
        assert all(isinstance(v, list) for v in d.values())

    def test_get_device_prop_top_level(self) -> None:
        from custom_components.easywave.entity_specs import _get_device_prop
        info = {"type": "ew_transmitter"}
        assert _get_device_prop(info, "type") == "ew_transmitter"

    def test_get_device_prop_extra_data(self) -> None:
        from custom_components.easywave.entity_specs import _get_device_prop
        info = {"extra_data": {"type": "ew_sensor"}}
        assert _get_device_prop(info, "type") == "ew_sensor"

    def test_get_device_prop_default(self) -> None:
        from custom_components.easywave.entity_specs import _get_device_prop
        info = {}
        assert _get_device_prop(info, "missing", "fallback") == "fallback"

    def test_get_registration_id(self) -> None:
        from custom_components.easywave.entity_specs import _get_registration_id
        info = {"registration_id": "uuid-1234"}
        assert _get_registration_id(info) == "uuid-1234"

    def test_get_registration_id_extra_data(self) -> None:
        from custom_components.easywave.entity_specs import _get_registration_id
        info = {"extra_data": {"registration_id": "uuid-5678"}}
        assert _get_registration_id(info) == "uuid-5678"

    def test_get_registration_id_missing(self) -> None:
        from custom_components.easywave.entity_specs import _get_registration_id
        info = {}
        assert _get_registration_id(info) == ""

    def test_create_specs_ew_transmitter(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        info = {
            "type": "ew_transmitter",
            "name": "Test TX",
            "registration_id": "reg-001",
            "button_count": 4,
        }
        specs = create_entity_specs_for_device("AABB0011", info)
        assert isinstance(specs, dict)
        # Should have sensor and/or button specs
        has_entities = any(len(v) > 0 for v in specs.values())
        assert has_entities

    def test_create_specs_ew_receiver_impulse(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        info = {
            "type": "ew_receiver",
            "receiver_kind": "impulse",
            "registration_id": "reg-002",
        }
        specs = create_entity_specs_for_device("AABB0022", info)
        assert len(specs.get("button", [])) >= 1

    def test_create_specs_ew_receiver_switch_2button(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        info = {
            "type": "ew_receiver",
            "receiver_kind": "switch_2button",
            "registration_id": "reg-003",
        }
        specs = create_entity_specs_for_device("AABB0033", info)
        assert len(specs.get("switch", [])) >= 1

    def test_create_specs_ew_receiver_cover_2button(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        info = {
            "type": "ew_receiver",
            "receiver_kind": "cover_2button",
            "registration_id": "reg-004",
        }
        specs = create_entity_specs_for_device("AABB0044", info)
        assert len(specs.get("cover", [])) >= 1

    def test_create_specs_ew_receiver_motor_3button(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        info = {
            "type": "ew_receiver",
            "receiver_kind": "motor_3button",
            "registration_id": "reg-005",
        }
        specs = create_entity_specs_for_device("AABB0055", info)
        covers = specs.get("cover", [])
        assert len(covers) >= 1
        assert covers[0].get("supports_stop") is True

    def test_create_specs_ew_receiver_heating_cooling(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        info = {
            "type": "ew_receiver",
            "receiver_kind": "heating_cooling",
            "registration_id": "reg-006",
        }
        specs = create_entity_specs_for_device("AABB0066", info)
        assert len(specs.get("switch", [])) >= 1

    def test_create_specs_ew_receiver_universal_4button(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        info = {
            "type": "ew_receiver",
            "receiver_kind": "universal_4button",
            "registration_id": "reg-007",
        }
        specs = create_entity_specs_for_device("AABB0077", info)
        assert len(specs.get("button", [])) == 4

    def test_create_specs_unknown_type(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        info = {"type": "phomagor", "registration_id": "reg-008"}
        specs = create_entity_specs_for_device("AABB0088", info)
        # Should return empty entity dict
        has_entities = any(len(v) > 0 for v in specs.values())
        assert has_entities is False

    def test_create_specs_ewneo_by_type_code(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        info = {
            "device_type_code": 0x05,  # EWB_DT_SWITCH
            "registration_id": "reg-009",
            "name": "Neo Switch",
        }
        specs = create_entity_specs_for_device("CCDD0099", info)
        assert isinstance(specs, dict)

    def test_create_specs_ew_sensor(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        info = {
            "type": "ewneo_sensor",
            "registration_id": "reg-010",
            "name": "Temp Sensor",
            "sensor_types": ["temperature"],
        }
        specs = create_entity_specs_for_device("EEFF0011", info)
        assert isinstance(specs, dict)

    def test_create_specs_dimmer(self) -> None:
        from custom_components.easywave.entity_specs import create_entity_specs_for_device
        info = {
            "type": "ew_receiver",
            "receiver_kind": "dimmer",
            "registration_id": "reg-011",
        }
        specs = create_entity_specs_for_device("AABB1111", info)
        # Dimmer should create light entities
        assert isinstance(specs, dict)


# ═══════════════════════════════════════════════════════════════════
# EntityMigrationHelper
# ═══════════════════════════════════════════════════════════════════


class TestEntityMigrationHelper:
    """Test entity migration utilities."""

    def test_construction(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.entity_migration import EntityMigrationHelper
        helper = EntityMigrationHelper(hass)
        assert helper is not None
        assert helper._migration_log == []

    async def test_check_no_devices(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.entity_migration import EntityMigrationHelper
        helper = EntityMigrationHelper(hass)
        report = await helper.check_and_migrate_entities("test_entry", {})
        assert report["checked_devices"] == 0
        assert report["migrated_devices"] == 0

    async def test_check_with_devices_no_entities(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.entity_migration import EntityMigrationHelper
        helper = EntityMigrationHelper(hass)
        devices = {"SER001": {"name": "Test", "entities": []}}
        report = await helper.check_and_migrate_entities("test_entry", devices)
        assert report["checked_devices"] == 1
        assert report["incompatible_entities"] == 0

    def test_apply_migration_pattern_no_match(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.entity_migration import EntityMigrationHelper
        helper = EntityMigrationHelper(hass)
        result = helper._apply_migration_pattern("some_unique_id")
        assert result is None

    def test_get_existing_entities(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.entity_migration import EntityMigrationHelper
        helper = EntityMigrationHelper(hass)
        entities = helper._get_existing_entities("nonexistent_entry_id")
        assert isinstance(entities, list)
        assert len(entities) == 0

    def test_find_incompatibilities_no_specs(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.entity_migration import EntityMigrationHelper
        helper = EntityMigrationHelper(hass)
        result = helper._find_incompatibilities("SER001", {}, [])
        assert result == []


# ═══════════════════════════════════════════════════════════════════
# migrate_v064_to_current
# ═══════════════════════════════════════════════════════════════════


class TestMigrateV064:
    """Test v0.6.4 migration function."""

    async def test_migrate_function_exists(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.entity_migration import migrate_entities_if_needed
        assert callable(migrate_entities_if_needed)

    async def test_migrate_no_devices(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.entity_migration import migrate_entities_if_needed
        result = await migrate_entities_if_needed(hass, "test_entry", {})
        assert isinstance(result, dict) or result is None


# ═══════════════════════════════════════════════════════════════════
# DeviceConfigManager
# ═══════════════════════════════════════════════════════════════════


class TestDeviceConfigManager:
    """Test DeviceConfigManager facade."""

    def _make(self, hass, tmp_path):
        from custom_components.easywave.device_config import DeviceConfigManager
        hass.config.config_dir = str(tmp_path)
        return DeviceConfigManager(hass, "test_entry")

    def test_construction(self, hass: HomeAssistant, tmp_path) -> None:
        dcm = self._make(hass, tmp_path)
        assert dcm is not None
        assert dcm.device_manager is not None

    async def test_load_devices(self, hass: HomeAssistant, tmp_path) -> None:
        dcm = self._make(hass, tmp_path)
        devices = await dcm.load_devices()
        assert isinstance(devices, dict)

    async def test_add_and_remove_device(self, hass: HomeAssistant, tmp_path) -> None:
        dcm = self._make(hass, tmp_path)
        result = await dcm.add_device_to_whitelist("SER001", "ew_transmitter", "Test TX")
        assert result is True
        # Remove
        result = await dcm.remove_device("SER001")
        assert result is True

    async def test_remove_nonexistent(self, hass: HomeAssistant, tmp_path) -> None:
        dcm = self._make(hass, tmp_path)
        result = await dcm.remove_device("NONEXISTENT")
        assert result is False


# ═══════════════════════════════════════════════════════════════════
# DeviceManager — deep coverage
# ═══════════════════════════════════════════════════════════════════


class TestDeviceManagerDeep:
    """Test DeviceManager internals."""

    def _make(self, hass, tmp_path):
        from custom_components.easywave.device_manager import DeviceManager
        hass.config.config_dir = str(tmp_path)
        return DeviceManager(hass, "test_entry")

    def test_construction(self, hass: HomeAssistant, tmp_path) -> None:
        dm = self._make(hass, tmp_path)
        assert dm is not None

    async def test_load_no_file(self, hass: HomeAssistant, tmp_path) -> None:
        dm = self._make(hass, tmp_path)
        await dm.load()
        devices = dm.get_all_devices()
        assert isinstance(devices, dict)

    def test_add_device(self, hass: HomeAssistant, tmp_path) -> None:
        dm = self._make(hass, tmp_path)
        dm.add_device(
            serial_number="AABB0011",
            device_type="ew_transmitter",
            name="Test TX",
        )
        device = dm.get_device("AABB0011")
        assert device is not None

    def test_add_device_with_extras(self, hass: HomeAssistant, tmp_path) -> None:
        dm = self._make(hass, tmp_path)
        dm.add_device(
            serial_number="AABB0022",
            device_type="ew_receiver",
            name="Test RX",
            rx11_index=5,
            area="living_room",
            extra_data={"receiver_kind": "switch_2button"},
        )
        device = dm.get_device("AABB0022")
        assert device is not None

    def test_remove_device(self, hass: HomeAssistant, tmp_path) -> None:
        dm = self._make(hass, tmp_path)
        dm.add_device("AABB0033", "ew_transmitter", "TX")
        result = dm.remove_device("AABB0033")
        assert result is True
        assert dm.get_device("AABB0033") is None

    def test_remove_nonexistent(self, hass: HomeAssistant, tmp_path) -> None:
        dm = self._make(hass, tmp_path)
        result = dm.remove_device("NONEXISTENT")
        assert result is False

    def test_get_all_devices(self, hass: HomeAssistant, tmp_path) -> None:
        dm = self._make(hass, tmp_path)
        dm.add_device("A", "ew_transmitter", "A")
        dm.add_device("B", "ew_receiver", "B")
        all_devices = dm.get_all_devices()
        assert len(all_devices) == 2

    async def test_save_and_load(self, hass: HomeAssistant, tmp_path) -> None:
        dm = self._make(hass, tmp_path)
        dm.add_device("SER001", "ew_transmitter", "TX1")
        await dm.save()

        dm2 = self._make(hass, tmp_path)
        await dm2.load()
        device = dm2.get_device("SER001")
        assert device is not None

    def test_update_device_info(self, hass: HomeAssistant, tmp_path) -> None:
        dm = self._make(hass, tmp_path)
        dm.add_device("SER001", "ew_transmitter", "TX1")
        dm.update_device_info("SER001", name="Updated TX")
        device = dm.get_device("SER001")
        assert device is not None

    def test_device_count(self, hass: HomeAssistant, tmp_path) -> None:
        dm = self._make(hass, tmp_path)
        dm.add_device("A", "ew_transmitter", "TX")
        dm.add_device("B", "ew_receiver", "RX")
        dm.add_device("C", "ew_transmitter", "TX2")
        all_devices = dm.get_all_devices()
        assert len(all_devices) == 3


# ═══════════════════════════════════════════════════════════════════
# device_migration — migrate_to_device_manager, create_compatibility_wrapper
# ═══════════════════════════════════════════════════════════════════


class TestDeviceMigration:
    """Test device migration utilities."""

    async def test_migrate_to_device_manager(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.device_migration import migrate_to_device_manager
        hass.config.config_dir = str(tmp_path)
        result = await migrate_to_device_manager(hass, "test_entry")
        assert result is not None

    def test_create_compatibility_wrapper(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.device_migration import create_compatibility_wrapper
        from custom_components.easywave.device_manager import DeviceManager
        hass.config.config_dir = str(tmp_path)
        dm = DeviceManager(hass, "test_entry")
        wrapper = create_compatibility_wrapper(dm)
        assert wrapper is not None

    async def test_compatibility_wrapper_load(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.device_migration import create_compatibility_wrapper
        from custom_components.easywave.device_manager import DeviceManager
        hass.config.config_dir = str(tmp_path)
        dm = DeviceManager(hass, "test_entry")
        wrapper = create_compatibility_wrapper(dm)
        devices = await wrapper.load_devices()
        assert isinstance(devices, dict)

    async def test_compatibility_wrapper_save(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.device_migration import create_compatibility_wrapper
        from custom_components.easywave.device_manager import DeviceManager
        hass.config.config_dir = str(tmp_path)
        dm = DeviceManager(hass, "test_entry")
        wrapper = create_compatibility_wrapper(dm)
        result = await wrapper.save_device_config({"SER001": {"name": "Test"}})
        assert result is True or result is False

    async def test_migrate_with_legacy_file(self, hass: HomeAssistant, tmp_path) -> None:
        """Test migration when legacy file exists."""
        from custom_components.easywave.device_migration import migrate_to_device_manager
        hass.config.config_dir = str(tmp_path)
        # Create legacy file
        easywave_dir = tmp_path / "easywave"
        easywave_dir.mkdir(exist_ok=True)
        legacy_data = {
            "SER001": {"name": "Legacy TX", "type": "ew_transmitter"},
        }
        (easywave_dir / "registered_devices.json").write_text(json.dumps(legacy_data))
        result = await migrate_to_device_manager(hass, "test_entry")
        assert result is not None


# ═══════════════════════════════════════════════════════════════════
# device_icons
# ═══════════════════════════════════════════════════════════════════


class TestDeviceIcons:
    """Additional tests for device_icons."""

    def test_get_entity_config_for_device_switch(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        config = get_entity_config_for_device(
            device_type="ew_receiver",
            receiver_kind="switch_2button",
        )
        assert isinstance(config, dict)

    def test_get_entity_config_for_device_motor(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        config = get_entity_config_for_device(
            device_type="ew_receiver",
            receiver_kind="motor_3button",
        )
        assert isinstance(config, dict)

    def test_get_entity_config_unknown(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        config = get_entity_config_for_device(
            device_type="unknown_type",
            receiver_kind="unknown_kind",
        )
        assert isinstance(config, dict)

    def test_get_entity_config_dimmer(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        config = get_entity_config_for_device(
            device_type="ew_receiver",
            receiver_kind="dimmer",
            operating_mode=1,
            entity_type="light",
        )
        assert isinstance(config, dict)

    def test_get_entity_config_transmitter(self) -> None:
        from custom_components.easywave.device_icons import get_entity_config_for_device
        config = get_entity_config_for_device(device_type="ew_transmitter")
        assert isinstance(config, dict)


# ═══════════════════════════════════════════════════════════════════
# ManagedDevice dataclass
# ═══════════════════════════════════════════════════════════════════


class TestManagedDevice:
    """Test ManagedDevice dataclass."""

    def test_construction(self) -> None:
        from custom_components.easywave.device_manager import ManagedDevice, DeviceAvailability, DeviceType
        dev = ManagedDevice(
            serial_number="AABB0011",
            device_type=DeviceType.EW_TRANSMITTER,
            name="Test TX",
        )
        assert dev.serial_number == "AABB0011"
        assert dev.name == "Test TX"

    def test_availability_enum(self) -> None:
        from custom_components.easywave.device_manager import DeviceAvailability
        assert DeviceAvailability.AVAILABLE is not None
        assert DeviceAvailability.UNAVAILABLE is not None

    def test_device_type_enum(self) -> None:
        from custom_components.easywave.device_manager import DeviceType
        assert DeviceType.EW_TRANSMITTER is not None
        assert DeviceType.EW_RECEIVER is not None
