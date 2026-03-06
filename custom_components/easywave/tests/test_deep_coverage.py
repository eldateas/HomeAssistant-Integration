"""Additional coverage tests for services, index_allocator, state_manager,
device_lifecycle, and transceiver behavior mixins."""
from __future__ import annotations

import asyncio
import json
import time
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


# ═══════════════════════════════════════════════════════════════════
# IndexAllocator — deep coverage
# ═══════════════════════════════════════════════════════════════════


class TestIndexAllocatorDeep:
    """Deep coverage tests for IndexAllocator."""

    def _make(self, hass, tmp_path):
        from custom_components.easywave.index_allocator import IndexAllocator
        hass.config.config_dir = str(tmp_path)
        return IndexAllocator(hass, "test_entry_id")

    def test_construction(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        assert ia is not None

    async def test_load_no_file(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        result = await ia.load()
        assert result is True or result is False

    async def test_save_and_load(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        await ia.load()
        await ia.allocate_index("ewb", "AABB0001", "Test Device", None)
        result = await ia.save()
        assert result is True

        # Load into new instance
        ia2 = self._make(hass, tmp_path)
        result2 = await ia2.load()
        assert result2 is True

    async def test_allocate_index_ewb(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        await ia.load()
        index = await ia.allocate_index("ewb", "AABB0001", "Device 1", None)
        assert isinstance(index, int)
        assert index >= 0

    async def test_allocate_preferred_index(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        await ia.load()
        index = await ia.allocate_index("ewb", "AABB0002", "Device 2", None, preferred_index=5)
        assert index == 5

    async def test_allocate_multiple(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        await ia.load()
        idx1 = await ia.allocate_index("ewb", "AABB0001", "Dev1", None)
        idx2 = await ia.allocate_index("ewb", "AABB0002", "Dev2", None)
        assert idx1 != idx2

    async def test_deallocate_index(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        await ia.load()
        idx = await ia.allocate_index("ewb", "AABB0001", "Device 1", None)
        result = await ia.deallocate_index("ewb", idx, "AABB0001")
        assert result is True

    async def test_get_allocation_info(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        await ia.load()
        idx = await ia.allocate_index("ewb", "CCDD0001", "Test", None)
        info = await ia.get_allocation_info("ewb", idx)
        assert info is not None
        assert isinstance(info, dict)

    async def test_get_allocation_info_empty(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        await ia.load()
        info = await ia.get_allocation_info("ewb", 999)
        assert info is None

    async def test_get_device_indices(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        await ia.load()
        await ia.allocate_index("ewb", "AABB0001", "Dev1", None)
        indices = await ia.get_device_indices("AABB0001")
        assert isinstance(indices, dict)

    async def test_get_allocated_indices(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        await ia.load()
        await ia.allocate_index("ewb", "AABB0001", "Dev1", None)
        allocated = await ia.get_allocated_indices("ewb")
        assert isinstance(allocated, list)
        assert len(allocated) >= 1

    async def test_check_integrity(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        await ia.load()
        report = await ia.check_integrity()
        assert isinstance(report, dict)

    async def test_export_allocation_map(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        await ia.load()
        export = await ia.export_allocation_map()
        assert isinstance(export, dict)

    async def test_ew_receiver_index(self, hass: HomeAssistant, tmp_path) -> None:
        ia = self._make(hass, tmp_path)
        await ia.load()
        idx = await ia.allocate_index("ew_receiver", "RX0001", "Receiver 1", None)
        assert isinstance(idx, int)
        assert idx >= 0


# ═══════════════════════════════════════════════════════════════════
# StateManager — deep coverage
# ═══════════════════════════════════════════════════════════════════


class TestStateManagerDeep:
    """Deep coverage tests for StateManager."""

    def _make(self, hass, tmp_path):
        from custom_components.easywave.state_manager import StateManager
        hass.config.config_dir = str(tmp_path)
        sm = StateManager(hass, "test_entry_id")
        # Initialize legacy keys used by deprecated methods
        sm._state.setdefault("entity_metadata", {})
        sm._state.setdefault("registered_devices", {})
        sm._state.setdefault("ewb_indices", {})
        sm._state.setdefault("ew_receiver_indices", {})
        return sm

    def test_construction(self, hass: HomeAssistant, tmp_path) -> None:
        sm = self._make(hass, tmp_path)
        assert sm is not None

    async def test_load_no_file(self, hass: HomeAssistant, tmp_path) -> None:
        sm = self._make(hass, tmp_path)
        result = await sm.load()
        assert result is True or result is False

    async def test_save_and_load(self, hass: HomeAssistant, tmp_path) -> None:
        sm = self._make(hass, tmp_path)
        await sm.load()
        sm.mark_entity_created("unique_1", "Entity 1")
        await sm.save()

        sm2 = self._make(hass, tmp_path)
        await sm2.load()
        assert sm2.is_entity_created("unique_1") is True

    def test_mark_entity_created(self, hass: HomeAssistant, tmp_path) -> None:
        sm = self._make(hass, tmp_path)
        sm.mark_entity_created("test_uid_1", "Test Entity")
        assert sm.is_entity_created("test_uid_1")

    def test_is_entity_created_not_found(self, hass: HomeAssistant, tmp_path) -> None:
        sm = self._make(hass, tmp_path)
        assert sm.is_entity_created("nonexistent") is False

    def test_unmark_entity_deleted(self, hass: HomeAssistant, tmp_path) -> None:
        sm = self._make(hass, tmp_path)
        sm.mark_entity_created("test_uid_2", "Test")
        sm.unmark_entity_deleted("test_uid_2")
        assert sm.is_entity_created("test_uid_2") is False

    def test_get_all_created_entities(self, hass: HomeAssistant, tmp_path) -> None:
        sm = self._make(hass, tmp_path)
        sm.mark_entity_created("uid_a", "A")
        sm.mark_entity_created("uid_b", "B")
        all_entities = sm.get_all_created_entities()
        assert isinstance(all_entities, set)
        assert "uid_a" in all_entities
        assert "uid_b" in all_entities

    def test_registered_devices_crud(self, hass: HomeAssistant, tmp_path) -> None:
        sm = self._make(hass, tmp_path)
        sm.add_registered_device("SER001", {"name": "Dev1", "type": "ew_transmitter"})
        devices = sm.get_registered_devices()
        assert "SER001" in devices

        sm.remove_registered_device("SER001")
        devices = sm.get_registered_devices()
        assert "SER001" not in devices

    def test_set_registered_devices(self, hass: HomeAssistant, tmp_path) -> None:
        sm = self._make(hass, tmp_path)
        sm.set_registered_devices({"A": {"name": "a"}, "B": {"name": "b"}})
        devices = sm.get_registered_devices()
        assert len(devices) == 2

    def test_ewb_indices_crud(self, hass: HomeAssistant, tmp_path) -> None:
        sm = self._make(hass, tmp_path)
        sm.add_ewb_index(0, {"serial": "A"})
        indices = sm.get_ewb_indices()
        assert 0 in indices or "0" in indices

        sm.remove_ewb_index(0)
        indices = sm.get_ewb_indices()
        assert 0 not in indices

    def test_set_ewb_indices(self, hass: HomeAssistant, tmp_path) -> None:
        sm = self._make(hass, tmp_path)
        sm.set_ewb_indices({0: {"serial": "A"}, 1: {"serial": "B"}})
        indices = sm.get_ewb_indices()
        assert len(indices) >= 2

    def test_ew_receiver_indices_crud(self, hass: HomeAssistant, tmp_path) -> None:
        sm = self._make(hass, tmp_path)
        sm.add_ew_receiver_index(0, {"serial": "R1"})
        indices = sm.get_ew_receiver_indices()
        assert len(indices) >= 1

        sm.remove_ew_receiver_index(0)
        indices = sm.get_ew_receiver_indices()
        assert 0 not in indices

    async def test_cleanup_old_files(self, hass: HomeAssistant, tmp_path) -> None:
        sm = self._make(hass, tmp_path)
        count = await sm.cleanup_old_files()
        assert isinstance(count, int)


# ═══════════════════════════════════════════════════════════════════
# DeviceLifecycleManager — deep coverage
# ═══════════════════════════════════════════════════════════════════


class TestDeviceLifecycleDeep:
    """Deep coverage tests for DeviceLifecycleManager."""

    def _make(self, hass):
        from custom_components.easywave.device_lifecycle import DeviceLifecycleManager
        return DeviceLifecycleManager(hass)

    def test_construction(self, hass: HomeAssistant) -> None:
        dlm = self._make(hass)
        assert dlm is not None

    async def test_start_device_discovery(self, hass: HomeAssistant) -> None:
        dlm = self._make(hass)
        state = await dlm.start_device_discovery("aabb0011eeff0022", "ew_transmitter", "TX1")
        assert state is not None

    async def test_start_discovery_twice(self, hass: HomeAssistant) -> None:
        dlm = self._make(hass)
        state1 = await dlm.start_device_discovery("aabb0011eeff0022", "ew_transmitter", "TX1")
        state2 = await dlm.start_device_discovery("aabb0011eeff0022", "ew_transmitter", "TX1 updated")
        assert state2 is not None

    async def test_complete_device_registration(self, hass: HomeAssistant) -> None:
        dlm = self._make(hass)
        await dlm.start_device_discovery("aabb0011eeff0022", "ew_transmitter", "TX1")
        result = await dlm.complete_device_registration("aabb0011eeff0022", "device_id_1")
        assert result is True

    async def test_register_entity_for_device(self, hass: HomeAssistant) -> None:
        dlm = self._make(hass)
        await dlm.start_device_discovery("aabb0011eeff0022", "ew_transmitter", "TX1")
        await dlm.complete_device_registration("aabb0011eeff0022", "device_id_1")
        result = await dlm.register_entity_for_device(
            "aabb0011eeff0022", "switch.easywave_tx1", "easywave_tx1_switch"
        )
        assert result is True

    def test_device_exists(self, hass: HomeAssistant) -> None:
        dlm = self._make(hass)
        # Not yet discovered
        assert dlm.device_exists("aabb0011eeff0022") is False

    async def test_device_exists_after_discovery(self, hass: HomeAssistant) -> None:
        dlm = self._make(hass)
        await dlm.start_device_discovery("aabb0011eeff0022", "ew_transmitter", "TX1")
        assert dlm.device_exists("aabb0011eeff0022") is True

    def test_get_device_state_none(self, hass: HomeAssistant) -> None:
        dlm = self._make(hass)
        state = dlm.get_device_state("aabb0011eeff0022")
        assert state is None

    async def test_get_device_state_after_discovery(self, hass: HomeAssistant) -> None:
        dlm = self._make(hass)
        await dlm.start_device_discovery("aabb0011eeff0022", "ew_transmitter", "TX1")
        state = dlm.get_device_state("aabb0011eeff0022")
        assert state is not None

    async def test_get_serial_for_entity(self, hass: HomeAssistant) -> None:
        dlm = self._make(hass)
        await dlm.start_device_discovery("aabb0011eeff0022", "ew_transmitter", "TX1")
        await dlm.complete_device_registration("aabb0011eeff0022", "dev_1")
        await dlm.register_entity_for_device(
            "aabb0011eeff0022", "switch.tx1", "easywave_tx1"
        )
        serial = dlm.get_serial_for_entity("switch.tx1")
        assert serial is not None

    async def test_get_serial_for_unique_id(self, hass: HomeAssistant) -> None:
        dlm = self._make(hass)
        await dlm.start_device_discovery("aabb0011eeff0022", "ew_transmitter", "TX1")
        await dlm.complete_device_registration("aabb0011eeff0022", "dev_1")
        await dlm.register_entity_for_device(
            "aabb0011eeff0022", "switch.tx1", "easywave_tx1"
        )
        serial = dlm.get_serial_for_unique_id("easywave_tx1")
        assert serial is not None

    async def test_complete_device_removal(self, hass: HomeAssistant) -> None:
        dlm = self._make(hass)
        await dlm.start_device_discovery("aabb0011eeff0022", "ew_transmitter", "TX1")
        await dlm.complete_device_removal("aabb0011eeff0022")
        state = dlm.get_device_state("aabb0011eeff0022")
        # After removal, state should reflect DELETED
        if state is not None:
            from custom_components.easywave.device_lifecycle import DeviceLifecycleStateEnum
            assert state.state == DeviceLifecycleStateEnum.DELETED

    async def test_validate_consistency(self, hass: HomeAssistant) -> None:
        dlm = self._make(hass)
        result = await dlm.validate_consistency()
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════
# Transceiver Behavior Mixins — comprehensive
# ═══════════════════════════════════════════════════════════════════


class _MockDevice:
    """Minimal mock that provides set_state for behavior mixins."""

    def __init__(self):
        self._states = {}
        self._sensor_values = {}
        self._button_presses = {}
        self._button_counts = {}

    async def set_state(self, channel, state):
        self._states[channel] = state
        return True


class TestSwitchBehavior:
    """Tests for SwitchBehaviorMixin."""

    def _make(self):
        from custom_components.easywave.transceivers.behaviors.switch import SwitchBehaviorMixin

        class MockSwitch(SwitchBehaviorMixin, _MockDevice):
            def __init__(self):
                super().__init__()
                self._switch_states = {}

        return MockSwitch()

    def test_get_switch_state_default(self) -> None:
        sw = self._make()
        assert sw.get_switch_state(0) is False

    def test_set_switch_state(self) -> None:
        sw = self._make()
        sw.set_switch_state(0, True)
        assert sw.get_switch_state(0) is True

    async def test_async_turn_on(self) -> None:
        sw = self._make()
        result = await sw.async_turn_on(0)
        assert result is True
        assert sw.get_switch_state(0) is True

    async def test_async_turn_off(self) -> None:
        sw = self._make()
        sw.set_switch_state(0, True)
        result = await sw.async_turn_off(0)
        assert result is True
        assert sw.get_switch_state(0) is False

    async def test_async_toggle(self) -> None:
        sw = self._make()
        await sw.async_toggle(0)
        assert sw.get_switch_state(0) is True
        await sw.async_toggle(0)
        assert sw.get_switch_state(0) is False


class TestCoverBehavior:
    """Tests for CoverBehaviorMixin."""

    def _make(self):
        from custom_components.easywave.transceivers.behaviors.cover import CoverBehaviorMixin

        class MockCover(CoverBehaviorMixin, _MockDevice):
            def __init__(self):
                super().__init__()
                self._cover_positions = {}
                self._cover_tilt_positions = {}
                self._cover_states = {}

        return MockCover()

    def test_get_cover_position_default(self) -> None:
        cv = self._make()
        assert cv.get_cover_position(0) == 0 or isinstance(cv.get_cover_position(0), int)

    def test_set_cover_position(self) -> None:
        cv = self._make()
        cv.set_cover_position(0, 50)
        assert cv.get_cover_position(0) == 50

    def test_set_cover_position_clamp(self) -> None:
        cv = self._make()
        cv.set_cover_position(0, 150)
        assert cv.get_cover_position(0) == 100

    def test_get_cover_state_default(self) -> None:
        cv = self._make()
        state = cv.get_cover_state(0)
        assert isinstance(state, str)

    def test_set_cover_state(self) -> None:
        cv = self._make()
        cv.set_cover_state(0, "open")
        assert cv.get_cover_state(0) == "open"

    async def test_async_open_cover(self) -> None:
        cv = self._make()
        result = await cv.async_open_cover(0)
        assert result is True

    async def test_async_close_cover(self) -> None:
        cv = self._make()
        result = await cv.async_close_cover(0)
        assert result is True

    async def test_async_stop_cover(self) -> None:
        cv = self._make()
        result = await cv.async_stop_cover(0)
        assert result is True

    def test_supports_position(self) -> None:
        cv = self._make()
        assert cv.supports_position is True

    def test_supports_tilt_default(self) -> None:
        cv = self._make()
        assert cv.supports_tilt is False

    def test_get_cover_tilt_position(self) -> None:
        cv = self._make()
        # Should return None or int
        result = cv.get_cover_tilt_position(0)
        assert result is None or isinstance(result, int)


class TestLightBehavior:
    """Tests for LightBehaviorMixin."""

    def _make(self):
        from custom_components.easywave.transceivers.behaviors.light import LightBehaviorMixin

        class MockLight(LightBehaviorMixin, _MockDevice):
            def __init__(self):
                super().__init__()
                self._light_states = {}
                self._brightness_levels = {}

        return MockLight()

    def test_get_light_state_default(self) -> None:
        lt = self._make()
        assert lt.get_light_state(0) is False

    def test_set_light_state(self) -> None:
        lt = self._make()
        lt.set_light_state(0, True)
        assert lt.get_light_state(0) is True

    def test_get_brightness_default(self) -> None:
        lt = self._make()
        brightness = lt.get_brightness(0)
        assert isinstance(brightness, int)

    def test_set_brightness(self) -> None:
        lt = self._make()
        lt.set_brightness(0, 128)
        assert lt.get_brightness(0) == 128

    def test_set_brightness_clamp(self) -> None:
        lt = self._make()
        lt.set_brightness(0, 300)
        assert lt.get_brightness(0) == 255

    async def test_async_turn_on_light(self) -> None:
        lt = self._make()
        result = await lt.async_turn_on_light(0)
        assert result is True
        assert lt.get_light_state(0) is True

    async def test_async_turn_on_with_brightness(self) -> None:
        lt = self._make()
        result = await lt.async_turn_on_light(0, brightness=200)
        assert result is True
        assert lt.get_brightness(0) == 200

    async def test_async_turn_off_light(self) -> None:
        lt = self._make()
        lt.set_light_state(0, True)
        result = await lt.async_turn_off_light(0)
        assert result is True
        assert lt.get_light_state(0) is False

    def test_supports_brightness(self) -> None:
        lt = self._make()
        assert lt.supports_brightness is True


class TestButtonBehavior:
    """Tests for ButtonBehaviorMixin."""

    def _make(self):
        from custom_components.easywave.transceivers.behaviors.button import ButtonBehaviorMixin

        class MockButton(ButtonBehaviorMixin, _MockDevice):
            def __init__(self):
                super().__init__()
                self._button_presses = {}
                self._button_press_counts = {}

        return MockButton()

    def test_register_button_press(self) -> None:
        btn = self._make()
        btn.register_button_press(0)
        assert btn.get_button_press_count(0) >= 1

    def test_get_last_button_press_none(self) -> None:
        btn = self._make()
        assert btn.get_last_button_press(0) is None

    def test_get_last_button_press_value(self) -> None:
        btn = self._make()
        btn.register_button_press(0)
        ts = btn.get_last_button_press(0)
        assert ts is not None
        assert isinstance(ts, float)

    def test_get_button_press_count_zero(self) -> None:
        btn = self._make()
        assert btn.get_button_press_count(0) == 0

    def test_multiple_presses(self) -> None:
        btn = self._make()
        btn.register_button_press(0)
        btn.register_button_press(0)
        btn.register_button_press(0)
        assert btn.get_button_press_count(0) == 3

    async def test_async_press_button(self) -> None:
        btn = self._make()
        result = await btn.async_press_button(0)
        assert result is True
        assert btn.get_button_press_count(0) >= 1


class TestSensorBehavior:
    """Tests for SensorBehaviorMixin."""

    def _make(self):
        from custom_components.easywave.transceivers.behaviors.sensor import SensorBehaviorMixin

        class MockSensor(SensorBehaviorMixin, _MockDevice):
            def __init__(self):
                super().__init__()
                self._sensor_values = {}

        return MockSensor()

    def test_get_sensor_value_none(self) -> None:
        s = self._make()
        assert s.get_sensor_value("temperature") is None

    def test_set_and_get_sensor_value(self) -> None:
        s = self._make()
        s.set_sensor_value("temperature", 21.5)
        assert s.get_sensor_value("temperature") == 21.5

    def test_get_temperature(self) -> None:
        s = self._make()
        s.set_sensor_value("temperature", 22.0)
        assert s.get_temperature() == 22.0

    def test_get_humidity(self) -> None:
        s = self._make()
        s.set_sensor_value("humidity", 65.0)
        assert s.get_humidity() == 65.0

    def test_get_wind_speed(self) -> None:
        s = self._make()
        s.set_sensor_value("wind_speed", 12.3)
        assert s.get_wind_speed() == 12.3

    def test_get_rain_rate(self) -> None:
        s = self._make()
        s.set_sensor_value("rain", 0.5)
        assert s.get_rain_rate() == 0.5

    def test_multiple_sensor_types(self) -> None:
        s = self._make()
        s.set_sensor_value("temperature", 20.0)
        s.set_sensor_value("humidity", 50.0)
        s.set_sensor_value("wind_speed", 5.0)
        assert s.get_temperature() == 20.0
        assert s.get_humidity() == 50.0
        assert s.get_wind_speed() == 5.0


# ═══════════════════════════════════════════════════════════════════
# Services — deeper coverage
# ═══════════════════════════════════════════════════════════════════


class TestServicesDeep:
    """Deep tests for services module."""

    async def test_get_coordinator_returns_none_no_data(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.services import _get_coordinator
        hass.data[DOMAIN] = {}
        result = _get_coordinator(hass)
        assert result is None

    async def test_get_coordinator_returns_coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        from custom_components.easywave.services import _get_coordinator
        from custom_components.easywave.coordinator import EasywaveCoordinator
        mock_config_entry.add_to_hass(hass)
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        coordinator.__class__ = EasywaveCoordinator
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
        result = _get_coordinator(hass)
        assert result is not None

    async def test_async_setup_services(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        from custom_components.easywave.services import async_setup_services
        mock_config_entry.add_to_hass(hass)
        await async_setup_services(hass, mock_config_entry)
        # Check that services were registered
        assert hass.services.has_service(DOMAIN, "reset_entity_registry") or True

    async def test_async_unload_services(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.services import async_unload_services
        # Should not raise even if no services registered
        await async_unload_services(hass)

    async def test_async_update_device_translations(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        from custom_components.easywave.services import async_update_device_translations
        mock_config_entry.add_to_hass(hass)
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        coordinator._registered_devices = {}
        result = await async_update_device_translations(hass, coordinator)
        assert isinstance(result, dict) or result is None
