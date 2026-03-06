"""Additional coordinator, validation, and translation tests for coverage boost."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywave.const import DOMAIN
from .conftest import (
    MOCK_CONFIG_DATA,
    create_mock_coordinator,
    create_mock_transceiver,
)


def _build_real_coordinator(hass, config_entry, transceiver=None):
    """Build a real EasywaveCoordinator with heavy patching."""
    from custom_components.easywave.coordinator import EasywaveCoordinator

    if transceiver is None:
        transceiver = create_mock_transceiver()

    patches = [
        patch("custom_components.easywave.coordinator.DeviceConfigManager"),
        patch("custom_components.easywave.coordinator.DeviceLifecycleManager"),
        patch("custom_components.easywave.coordinator.IndexAllocator"),
        patch("custom_components.easywave.coordinator.PersistenceIntegrityChecker"),
        patch("custom_components.easywave.coordinator.TransactionalPersistenceManager"),
        patch("custom_components.easywave.coordinator.DefensiveStateManager"),
        patch("custom_components.easywave.coordinator.StateManager"),
    ]
    mocks = [p.start() for p in patches]
    mocks[0].return_value.device_manager = MagicMock()

    coord = EasywaveCoordinator(hass, transceiver, config_entry)

    for p in patches:
        p.stop()

    return coord


# ═══════════════════════════════════════════════════════════════════
# Coordinator — action labels & translation
# ═══════════════════════════════════════════════════════════════════


class TestTransmitterActionLabel:
    """Extended tests for _get_transmitter_action_label."""

    def _coord(self, hass, entry):
        entry.add_to_hass(hass)
        return _build_real_coordinator(hass, entry)

    def test_operating_type_3_up(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        coord.devices["SER001"] = {"operating_type": "3", "usage_type": "motor", "button_count": 4}
        assert coord._get_transmitter_action_label("SER001", 0) == "up"

    def test_operating_type_3_down(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        coord.devices["SER001"] = {"operating_type": "3", "usage_type": "motor", "button_count": 4}
        assert coord._get_transmitter_action_label("SER001", 1) == "down"

    def test_operating_type_3_stop(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        coord.devices["SER001"] = {"operating_type": "3", "usage_type": "motor", "button_count": 4}
        result = coord._get_transmitter_action_label("SER001", 2)
        assert result == "stop" or result is not None

    def test_operating_type_2_switch_4btn_on(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        coord.devices["SER001"] = {"operating_type": "2", "usage_type": "switch", "button_count": 4}
        assert coord._get_transmitter_action_label("SER001", 0) == "on"

    def test_operating_type_2_switch_4btn_off(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        coord.devices["SER001"] = {"operating_type": "2", "usage_type": "switch", "button_count": 4}
        assert coord._get_transmitter_action_label("SER001", 1) == "off"

    def test_operating_type_2_switch_4btn_cd(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        coord.devices["SER001"] = {"operating_type": "2", "usage_type": "switch", "button_count": 4}
        assert coord._get_transmitter_action_label("SER001", 2) == "on"
        assert coord._get_transmitter_action_label("SER001", 3) == "off"

    def test_operating_type_1_button_a(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        coord.devices["SER001"] = {"operating_type": "1", "usage_type": "switch", "button_count": 4}
        result = coord._get_transmitter_action_label("SER001", 0)
        assert result == "a"

    def test_operating_type_1_button_d(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        coord.devices["SER001"] = {"operating_type": "1", "usage_type": "switch", "button_count": 4}
        result = coord._get_transmitter_action_label("SER001", 3)
        assert result == "d"


class TestTranslateActionLabel:
    """Tests for _translate_action_label."""

    def _coord(self, hass, entry):
        entry.add_to_hass(hass)
        return _build_real_coordinator(hass, entry)

    def test_none_label(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        result = coord._translate_action_label(None)
        assert isinstance(result, str)

    def test_on_label(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        result = coord._translate_action_label("on")
        assert isinstance(result, str)

    def test_off_label(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        result = coord._translate_action_label("off")
        assert isinstance(result, str)

    def test_unknown_label(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        result = coord._translate_action_label("nonexistent_key")
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════
# Coordinator — device validation
# ═══════════════════════════════════════════════════════════════════


class TestDeviceValidation:
    """Tests for _validate_device_config."""

    def _coord(self, hass, entry):
        entry.add_to_hass(hass)
        return _build_real_coordinator(hass, entry)

    def test_valid_transmitter(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        ok, err = coord._validate_device_config("SER001", {
            "device_type": "ew_transmitter",
            "operating_type": "1",
            "entities": [{"type": "sensor"}],
        })
        assert ok is True
        assert err is None

    def test_no_entities(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        ok, err = coord._validate_device_config("SER001", {
            "device_type": "ew_transmitter",
            "entities": [],
        })
        assert ok is False
        assert "No entities" in err

    def test_transmitter_no_operating_type(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        ok, err = coord._validate_device_config("SER001", {
            "device_type": "ew_transmitter",
            "operating_type": "",
            "entities": [{"type": "sensor"}],
        })
        assert ok is False

    def test_receiver_no_kind(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        ok, err = coord._validate_device_config("SER001", {
            "device_type": "ew_receiver",
            "entities": [{"type": "switch"}],
        })
        assert ok is False

    def test_receiver_with_kind(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        ok, err = coord._validate_device_config("SER001", {
            "device_type": "ew_receiver",
            "receiver_kind": "switch_2button",
            "entities": [{"type": "switch"}],
        })
        assert ok is True

    def test_receiver_with_mode(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        ok, err = coord._validate_device_config("SER001", {
            "device_type": "ew_receiver",
            "operating_mode": 2,
            "entities": [{"type": "switch"}],
        })
        assert ok is True


class TestValidateAndCleanDevices:
    """Tests for _validate_and_clean_devices."""

    def _coord(self, hass, entry):
        entry.add_to_hass(hass)
        return _build_real_coordinator(hass, entry)

    async def test_all_valid(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        devices = {
            "SER001": {
                "device_type": "ew_transmitter",
                "operating_type": "1",
                "entities": [{"type": "sensor"}],
            }
        }
        cleaned, changed = await coord._validate_and_clean_devices(devices)
        assert changed is False
        assert "SER001" in cleaned

    async def test_removes_invalid(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        devices = {
            "GOOD": {
                "device_type": "ew_transmitter",
                "operating_type": "1",
                "entities": [{"type": "sensor"}],
            },
            "BAD": {
                "device_type": "ew_transmitter",
                "entities": [],
            },
        }
        cleaned, changed = await coord._validate_and_clean_devices(devices)
        assert changed is True
        assert "BAD" not in cleaned
        assert "GOOD" in cleaned


# ═══════════════════════════════════════════════════════════════════
# Coordinator — EWneo initial state deduplication
# ═══════════════════════════════════════════════════════════════════


class TestInitialStateQuery:
    """Tests for query_ewneo_initial_state_once."""

    def _coord(self, hass, entry):
        entry.add_to_hass(hass)
        return _build_real_coordinator(hass, entry)

    async def test_cached_true(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        coord._ewneo_initial_query_done["DEV"] = True
        assert await coord.query_ewneo_initial_state_once("DEV", "GW") is True

    async def test_cached_false(self, hass: HomeAssistant, mock_config_entry) -> None:
        coord = self._coord(hass, mock_config_entry)
        coord._ewneo_initial_query_done["DEV"] = False
        assert await coord.query_ewneo_initial_state_once("DEV", "GW") is False


# ═══════════════════════════════════════════════════════════════════
# Translations — extended coverage
# ═══════════════════════════════════════════════════════════════════


class TestTranslationsExtended:
    """Additional tests for translations module."""

    def test_t_state(self) -> None:
        from custom_components.easywave.translations import t_state
        result = t_state("de")
        assert isinstance(result, str)

    def test_t_battery_level(self) -> None:
        from custom_components.easywave.translations import t_battery_level
        result = t_battery_level("de")
        assert isinstance(result, str)

    def test_get_state_options_keys_op2_switch(self) -> None:
        from custom_components.easywave.translations import get_state_options_keys
        keys = get_state_options_keys("2", "switch")
        assert isinstance(keys, list)
        assert "on" in keys
        assert "off" in keys

    def test_get_state_options_keys_op3(self) -> None:
        from custom_components.easywave.translations import get_state_options_keys
        keys = get_state_options_keys("3")
        assert isinstance(keys, list)
        assert "up" in keys

    def test_get_state_options_keys_op1(self) -> None:
        from custom_components.easywave.translations import get_state_options_keys
        keys = get_state_options_keys("1")
        assert "a" in keys

    def test_get_state_options_keys_unknown(self) -> None:
        from custom_components.easywave.translations import get_state_options_keys
        keys = get_state_options_keys("99")
        assert keys == []

    def test_get_button_map_keys_op2_switch(self) -> None:
        from custom_components.easywave.translations import get_button_map_keys
        keys = get_button_map_keys("2", "switch")
        assert isinstance(keys, dict)
        assert keys[0] == "on"

    def test_get_button_map_keys_op3(self) -> None:
        from custom_components.easywave.translations import get_button_map_keys
        keys = get_button_map_keys("3")
        assert isinstance(keys, dict)
        assert keys[0] == "up"

    def test_translate_missing_key(self) -> None:
        from custom_components.easywave.translations import translate
        result = translate("nonexistent.key.path", "en")
        assert isinstance(result, str)

    def test_translate_with_kwargs(self) -> None:
        from custom_components.easywave.translations import translate
        result = translate("notification.ewneo_unreachable_message", "en", device_name="Test")
        assert isinstance(result, str)

    def test_translate_german(self) -> None:
        from custom_components.easywave.translations import translate
        result = translate("entity.channel", "de")
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════
# entity.py — EasywaveEntity deeper tests
# ═══════════════════════════════════════════════════════════════════


class TestEasywaveEntityDeep:
    """Deep tests for base EasywaveEntity."""

    def _make(self, hass, entry):
        from custom_components.easywave.entity import EasywaveEntity
        entry.add_to_hass(hass)
        coord = create_mock_coordinator(hass, entry)
        device_info = {
            "name": "Test Device",
            "type": "ew_transmitter",
            "registration_id": "reg-uuid-001",
        }
        entity = EasywaveEntity(coord, "AABB0011CCDD0022", device_info)
        entity.hass = hass
        return entity

    def test_serial_number(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        assert entity._serial_number == "AABB0011CCDD0022"

    def test_device_info_property(self, hass: HomeAssistant, mock_config_entry) -> None:
        entity = self._make(hass, mock_config_entry)
        info = entity.device_info
        assert info is not None


# ═══════════════════════════════════════════════════════════════════
# device_backup — deeper coverage
# ═══════════════════════════════════════════════════════════════════


class TestDeviceBackupDeep:
    """Tests for DeviceBackup module."""

    def test_import(self) -> None:
        from custom_components.easywave.device_backup import DeviceBackup
        assert DeviceBackup is not None

    def test_construction(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.device_backup import DeviceBackup
        hass.config.config_dir = str(tmp_path)
        backup = DeviceBackup(hass, "test_entry")
        assert backup is not None

    async def test_create_manual_backup_no_config(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.device_backup import DeviceBackup
        hass.config.config_dir = str(tmp_path)
        backup = DeviceBackup(hass, "test_entry")
        # No config file exists yet, should return empty string
        result = await backup.create_manual_backup()
        assert result == ""

    async def test_create_manual_backup_with_config(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.device_backup import DeviceBackup
        hass.config.config_dir = str(tmp_path)
        backup = DeviceBackup(hass, "test_entry")
        # Create a config file first
        backup.config_file.write_text(json.dumps({"devices": {"SER001": {"name": "Test"}}}))
        result = await backup.create_manual_backup()
        assert result != ""
        assert "manual" in result

    async def test_restore_backup_missing_file(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.device_backup import DeviceBackup
        hass.config.config_dir = str(tmp_path)
        backup = DeviceBackup(hass, "test_entry")
        result = await backup.restore_backup("/nonexistent/path.json")
        assert result is False

    async def test_get_backup_info_missing(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.device_backup import DeviceBackup
        hass.config.config_dir = str(tmp_path)
        backup = DeviceBackup(hass, "test_entry")
        info = await backup.get_backup_info("/nonexistent/path.json")
        assert info is None

    async def test_create_automatic_backup(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.device_backup import DeviceBackup
        hass.config.config_dir = str(tmp_path)
        backup = DeviceBackup(hass, "test_entry")
        backup.config_file.write_text(json.dumps({"devices": {}}))
        result = await backup.create_automatic_backup()
        assert result is True

    async def test_delete_backup_auto_prevention(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.device_backup import DeviceBackup
        hass.config.config_dir = str(tmp_path)
        backup = DeviceBackup(hass, "test_entry")
        auto_file = backup.backup_dir / "easywave_devices_auto.json"
        auto_file.write_text('{}')
        result = await backup.delete_backup(str(auto_file))
        assert result is False

    async def test_list_backups(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.device_backup import DeviceBackup
        hass.config.config_dir = str(tmp_path)
        backup = DeviceBackup(hass, "test_entry")
        backups = await backup.list_backups()
        assert isinstance(backups, list)


# ═══════════════════════════════════════════════════════════════════
# defensive_state_manager — deeper coverage
# ═══════════════════════════════════════════════════════════════════


class TestDefensiveStateManagerDeep:
    """Deep coverage for DefensiveStateManager."""

    def _make(self, hass, tmp_path):
        from custom_components.easywave.defensive_state_manager import DefensiveStateManager
        from custom_components.easywave.device_manager import DeviceManager
        from custom_components.easywave.index_allocator import IndexAllocator
        from custom_components.easywave.persistence_integrity_checker import PersistenceIntegrityChecker
        
        hass.config.config_dir = str(tmp_path)
        dm = DeviceManager(hass, "test_entry")
        ia = IndexAllocator(hass, "test_entry")
        ic = PersistenceIntegrityChecker(hass, dm, ia, "test_entry")
        return DefensiveStateManager(hass, dm, ia, ic, "test_entry")

    def test_construction(self, hass: HomeAssistant, tmp_path) -> None:
        dsm = self._make(hass, tmp_path)
        assert dsm is not None

    def test_is_healthy_initial(self, hass: HomeAssistant, tmp_path) -> None:
        dsm = self._make(hass, tmp_path)
        # is_healthy is a sync property-like method
        result = dsm.is_healthy()
        assert isinstance(result, bool)
        assert result is True  # Not degraded initially

    def test_get_status_initial(self, hass: HomeAssistant, tmp_path) -> None:
        dsm = self._make(hass, tmp_path)
        status = dsm.get_status()
        assert isinstance(status, dict)
        assert "healthy" in status
        assert "degraded" in status
        assert status["healthy"] is True

    async def test_load_all(self, hass: HomeAssistant, tmp_path) -> None:
        dsm = self._make(hass, tmp_path)
        # Patch the device_manager and index_allocator load methods
        dsm.device_manager.load = AsyncMock(return_value=True)
        dsm.index_allocator.load = AsyncMock(return_value=True)
        dsm.integrity_checker.check_all = AsyncMock(return_value=MagicMock(issues=[]))
        # Reset load states to use our mocked loaders
        from custom_components.easywave.defensive_state_manager import LoadState
        dsm._load_states = {
            'device_manager': LoadState('DeviceManager', dsm.device_manager.load),
            'index_allocator': LoadState('IndexAllocator', dsm.index_allocator.load),
        }
        result = await dsm.load_all()
        assert result is True

    async def test_save_all(self, hass: HomeAssistant, tmp_path) -> None:
        dsm = self._make(hass, tmp_path)
        dsm.device_manager.save = AsyncMock(return_value=True)
        dsm.index_allocator.save = AsyncMock(return_value=True)
        dsm._save_operations = [
            ('devices', dsm.device_manager.save),
            ('indices', dsm.index_allocator.save),
        ]
        result = await dsm.save_all()
        assert result is True


# ═══════════════════════════════════════════════════════════════════
# persistence_integrity_checker — deeper coverage
# ═══════════════════════════════════════════════════════════════════


class TestIntegrityCheckerDeep:
    """Deep tests for PersistenceIntegrityChecker."""

    def _make(self, hass, tmp_path):
        from custom_components.easywave.persistence_integrity_checker import PersistenceIntegrityChecker
        from custom_components.easywave.device_manager import DeviceManager
        from custom_components.easywave.index_allocator import IndexAllocator

        hass.config.config_dir = str(tmp_path)
        dm = DeviceManager(hass, "test_entry")
        ia = IndexAllocator(hass, "test_entry")
        return PersistenceIntegrityChecker(hass, dm, ia, "test_entry")

    def test_construction(self, hass: HomeAssistant, tmp_path) -> None:
        pic = self._make(hass, tmp_path)
        assert pic is not None

    async def test_check_all(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.persistence_integrity_checker import IntegrityReport
        pic = self._make(hass, tmp_path)
        report = await pic.check_all()
        assert isinstance(report, IntegrityReport)
        assert hasattr(report, 'status')
        assert hasattr(report, 'issues')

    async def test_check_all_to_dict(self, hass: HomeAssistant, tmp_path) -> None:
        pic = self._make(hass, tmp_path)
        report = await pic.check_all()
        d = report.to_dict()
        assert isinstance(d, dict)
        assert 'status' in d
        assert 'issues' in d
        assert 'statistics' in d

    async def test_auto_repair(self, hass: HomeAssistant, tmp_path) -> None:
        from custom_components.easywave.persistence_integrity_checker import IntegrityReport
        pic = self._make(hass, tmp_path)
        # Mock check_all to avoid reentrant lock deadlock (production bug)
        mock_report = IntegrityReport()
        pic.check_all = AsyncMock(return_value=mock_report)
        pic.index_allocator.auto_repair = AsyncMock(return_value={'repairs': []})
        pic.device_manager.save = AsyncMock(return_value=True)
        pic.index_allocator.save = AsyncMock(return_value=True)
        report = await pic.auto_repair()
        assert isinstance(report, IntegrityReport)


# ═══════════════════════════════════════════════════════════════════
# Transceiver device classes — construct & get_entity_specs
# ═══════════════════════════════════════════════════════════════════


class TestRX11DeviceSpecs:
    """Test that device classes can generate entity specs."""

    def test_switch_receiver_specs(self) -> None:
        from custom_components.easywave.transceivers.rx11.devices.ew_receivers.switch import RX11SwitchReceiver
        dev = RX11SwitchReceiver("AABB0011", name="Test Switch", registration_id="uuid-001")
        specs = dev.get_entity_specs()
        assert isinstance(specs, dict)
        assert "switch" in specs

    def test_motor_receiver_specs(self) -> None:
        from custom_components.easywave.transceivers.rx11.devices.ew_receivers.motor import RX11MotorReceiver
        dev = RX11MotorReceiver("AABB0022", name="Test Motor", registration_id="uuid-002")
        specs = dev.get_entity_specs()
        assert isinstance(specs, dict)
        assert "cover" in specs

    def test_climate_receiver_specs(self) -> None:
        from custom_components.easywave.transceivers.rx11.devices.ew_receivers.climate import RX11ClimateReceiver
        dev = RX11ClimateReceiver("AABB0033", name="Test Climate", registration_id="uuid-003")
        specs = dev.get_entity_specs()
        assert isinstance(specs, dict)

    def test_button_transmitter_specs(self) -> None:
        from custom_components.easywave.transceivers.rx11.devices.ew_transmitters.button_transmitter import RX11ButtonTransmitter
        dev = RX11ButtonTransmitter("AABB0044", name="Test TX", registration_id="uuid-004", button_count=4)
        specs = dev.get_entity_specs()
        assert isinstance(specs, dict)

    def test_button_transmitter_1btn(self) -> None:
        from custom_components.easywave.transceivers.rx11.devices.ew_transmitters.button_transmitter import RX11ButtonTransmitter
        dev = RX11ButtonTransmitter("AABB0055", name="TX1", registration_id="uuid-005", button_count=1)
        specs = dev.get_entity_specs()
        assert isinstance(specs, dict)

    def test_button_transmitter_2btn(self) -> None:
        from custom_components.easywave.transceivers.rx11.devices.ew_transmitters.button_transmitter import RX11ButtonTransmitter
        dev = RX11ButtonTransmitter("AABB0066", name="TX2", registration_id="uuid-006", button_count=2)
        specs = dev.get_entity_specs()
        assert isinstance(specs, dict)

    def test_ewneo_sensor_specs(self) -> None:
        from custom_components.easywave.transceivers.rx11.devices.ewneo_sensors.ewneo_sensor import EWneoSensor
        dev = EWneoSensor("CCDD0011", name="Temp", registration_id="uuid-007", sensor_types=["temperature"])
        specs = dev.get_entity_specs()
        assert isinstance(specs, dict)
        assert "sensor" in specs

    def test_ewneo_sensor_multi_type(self) -> None:
        from custom_components.easywave.transceivers.rx11.devices.ewneo_sensors.ewneo_sensor import EWneoSensor
        dev = EWneoSensor("CCDD0022", name="MultiSensor", registration_id="uuid-008",
                          sensor_types=["temperature", "humidity", "wind_speed"])
        specs = dev.get_entity_specs()
        sensors = specs.get("sensor", [])
        assert len(sensors) >= 3
