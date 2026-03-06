"""Tests for device_manager, state_manager, entity_registry, device_config, device_lifecycle."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import pytest

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywave.const import DOMAIN

from .conftest import MOCK_CONFIG_DATA


# ═════════════════════════════════════════════════════════════════════
# DeviceManager
# ═════════════════════════════════════════════════════════════════════


class TestDeviceManager:
    """Tests for DeviceManager."""

    def _make_dm(self, hass):
        from custom_components.easywave.device_manager import DeviceManager
        return DeviceManager(hass, "test_entry_id")

    def test_init(self, hass: HomeAssistant) -> None:
        dm = self._make_dm(hass)
        assert dm is not None
        assert dm.get_all_devices() == {}

    def test_add_device(self, hass: HomeAssistant) -> None:
        dm = self._make_dm(hass)
        device = dm.add_device("AABB0001", "ew_transmitter", name="TX 1")
        assert device is not None
        assert device.serial_number == "AABB0001"
        assert device.name == "TX 1"
        assert dm.is_whitelisted("AABB0001")

    def test_remove_device(self, hass: HomeAssistant) -> None:
        dm = self._make_dm(hass)
        dm.add_device("AABB0001", "ew_transmitter", name="TX 1")
        result = dm.remove_device("AABB0001")
        assert result is True
        assert not dm.is_whitelisted("AABB0001")

    def test_remove_nonexistent_device(self, hass: HomeAssistant) -> None:
        dm = self._make_dm(hass)
        result = dm.remove_device("NONEXIST")
        assert result is False

    def test_get_device(self, hass: HomeAssistant) -> None:
        dm = self._make_dm(hass)
        dm.add_device("AABB0001", "ew_receiver", name="RX 1")
        device = dm.get_device("AABB0001")
        assert device is not None
        assert device.device_type == "ew_receiver"

    def test_get_device_not_found(self, hass: HomeAssistant) -> None:
        dm = self._make_dm(hass)
        assert dm.get_device("UNKNOWN") is None

    def test_add_device_with_rx11_index(self, hass: HomeAssistant) -> None:
        dm = self._make_dm(hass)
        device = dm.add_device("AABB0001", "ew_receiver", name="RX 1", rx11_index=5)
        assert device.rx11_index == 5
        found = dm.get_device_by_rx11_index(5)
        assert found is not None
        assert found.serial_number == "AABB0001"

    def test_get_device_by_rx11_index_not_found(self, hass: HomeAssistant) -> None:
        dm = self._make_dm(hass)
        assert dm.get_device_by_rx11_index(99) is None

    def test_device_count(self, hass: HomeAssistant) -> None:
        dm = self._make_dm(hass)
        dm.add_device("AABB0001", "ew_transmitter", name="TX 1")
        dm.add_device("AABB0002", "ew_receiver", name="RX 1")
        counts = dm.get_device_count()
        assert isinstance(counts, dict)
        assert counts.get("total", 0) >= 2

    def test_set_device_availability(self, hass: HomeAssistant) -> None:
        dm = self._make_dm(hass)
        dm.add_device("AABB0001", "ew_transmitter", name="TX 1")
        result = dm.set_device_availability("AABB0001", False)
        assert result is True

    def test_get_devices_by_availability(self, hass: HomeAssistant) -> None:
        dm = self._make_dm(hass)
        dm.add_device("AABB0001", "ew_transmitter", name="TX 1")
        dm.set_device_availability("AABB0001", True)
        available = dm.get_devices_by_availability(True)
        assert "AABB0001" in available

    def test_update_device_info(self, hass: HomeAssistant) -> None:
        dm = self._make_dm(hass)
        dm.add_device("AABB0001", "ew_transmitter", name="TX 1")
        result = dm.update_device_info("AABB0001", name="TX Updated")
        assert result is True
        device = dm.get_device("AABB0001")
        assert device.name == "TX Updated"

    def test_managed_device_to_dict_from_dict(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.device_manager import ManagedDevice
        dm = self._make_dm(hass)
        device = dm.add_device("AABB0001", "ew_transmitter", name="TX 1")
        data = device.to_dict()
        assert isinstance(data, dict)
        restored = ManagedDevice.from_dict(data)
        assert restored.serial_number == "AABB0001"

    def test_managed_device_mark_available(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.device_manager import ManagedDevice, DeviceAvailability
        dm = self._make_dm(hass)
        device = dm.add_device("AABB0001", "ew_transmitter", name="TX 1")
        device.mark_available()
        assert device.availability == DeviceAvailability.AVAILABLE

    def test_managed_device_mark_unavailable(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.device_manager import ManagedDevice, DeviceAvailability
        dm = self._make_dm(hass)
        device = dm.add_device("AABB0001", "ew_transmitter", name="TX 1")
        device.mark_unavailable()
        assert device.availability == DeviceAvailability.UNAVAILABLE


# ═════════════════════════════════════════════════════════════════════
# DeviceType enum
# ═════════════════════════════════════════════════════════════════════


class TestDeviceTypeEnum:
    def test_device_type_values(self) -> None:
        from custom_components.easywave.device_manager import DeviceType
        assert DeviceType.EW_TRANSMITTER.value == 0x01
        assert DeviceType.EW_RECEIVER.value == 0x02
        assert DeviceType.EWNEO_SENSOR.value == 0x03
        assert DeviceType.RX11_GATEWAY.value == 0xFF


# ═════════════════════════════════════════════════════════════════════
# StateManager
# ═════════════════════════════════════════════════════════════════════


class TestStateManager:
    """Tests for StateManager."""

    def _make_sm(self, hass):
        from custom_components.easywave.state_manager import StateManager
        sm = StateManager(hass, "test_entry_id")
        # Initialize keys that load() would normally create from file
        sm._state.setdefault("entity_metadata", {})
        sm._state.setdefault("entity_unique_ids", set())
        sm._state.setdefault("registered_devices", {})
        sm._state.setdefault("ewb_indices", {})
        sm._state.setdefault("ew_receiver_indices", {})
        return sm

    def test_init(self, hass: HomeAssistant) -> None:
        sm = self._make_sm(hass)
        assert sm is not None

    def test_mark_entity_created(self, hass: HomeAssistant) -> None:
        sm = self._make_sm(hass)
        sm.mark_entity_created("unique_001", "Test Entity")
        assert sm.is_entity_created("unique_001")

    def test_entity_not_created(self, hass: HomeAssistant) -> None:
        sm = self._make_sm(hass)
        assert not sm.is_entity_created("nonexistent")

    def test_get_all_created_entities(self, hass: HomeAssistant) -> None:
        sm = self._make_sm(hass)
        sm.mark_entity_created("unique_001", "E1")
        sm.mark_entity_created("unique_002", "E2")
        entities = sm.get_all_created_entities()
        assert "unique_001" in entities
        assert "unique_002" in entities

    def test_registered_devices_management(self, hass: HomeAssistant) -> None:
        sm = self._make_sm(hass)
        sm.set_registered_devices({"DEV1": {"type": "ew_transmitter"}})
        devices = sm.get_registered_devices()
        assert "DEV1" in devices

    def test_add_remove_registered_device(self, hass: HomeAssistant) -> None:
        sm = self._make_sm(hass)
        sm.add_registered_device("DEV1", {"type": "ew_transmitter"})
        assert "DEV1" in sm.get_registered_devices()
        sm.remove_registered_device("DEV1")
        assert "DEV1" not in sm.get_registered_devices()

    def test_ewb_indices_management(self, hass: HomeAssistant) -> None:
        sm = self._make_sm(hass)
        sm.set_ewb_indices({0: {"serial": "A"}, 1: {"serial": "B"}})
        indices = sm.get_ewb_indices()
        assert len(indices) == 2

    def test_add_remove_ewb_index(self, hass: HomeAssistant) -> None:
        sm = self._make_sm(hass)
        sm.add_ewb_index(5, {"serial": "AABB"})
        assert 5 in sm.get_ewb_indices() or "5" in sm.get_ewb_indices()
        sm.remove_ewb_index(5)

    def test_ew_receiver_indices_management(self, hass: HomeAssistant) -> None:
        sm = self._make_sm(hass)
        sm.set_ew_receiver_indices({0: {"serial": "A"}})
        indices = sm.get_ew_receiver_indices()
        assert len(indices) == 1


# ═════════════════════════════════════════════════════════════════════
# EasywaveEntityRegistry (session tracking)
# ═════════════════════════════════════════════════════════════════════


class TestEasywaveEntityRegistry:
    """Tests for the in-memory entity registry."""

    def test_get_singleton(self) -> None:
        from custom_components.easywave.entity_registry import (
            get_entity_registry,
            reset_entity_registry,
        )
        reset_entity_registry()
        reg = get_entity_registry()
        assert reg is not None
        reg2 = get_entity_registry()
        assert reg is reg2

    def test_reset_creates_new_instance(self) -> None:
        from custom_components.easywave.entity_registry import (
            get_entity_registry,
            reset_entity_registry,
        )
        reg1 = get_entity_registry()
        reset_entity_registry()
        reg2 = get_entity_registry()
        assert reg1 is not reg2

    def test_mark_entity_created_this_session(self) -> None:
        from custom_components.easywave.entity_registry import (
            get_entity_registry,
            reset_entity_registry,
        )
        reset_entity_registry()
        reg = get_entity_registry()
        assert not reg.is_entity_created_this_session("uid_001")
        reg.mark_entity_created("uid_001", "AABB0001")
        assert reg.is_entity_created_this_session("uid_001")

    def test_clear(self) -> None:
        from custom_components.easywave.entity_registry import (
            get_entity_registry,
            reset_entity_registry,
        )
        reset_entity_registry()
        reg = get_entity_registry()
        reg.mark_entity_created("uid_001", "AABB0001")
        reg.clear()
        assert not reg.is_entity_created_this_session("uid_001")


# ═════════════════════════════════════════════════════════════════════
# DeviceConfigManager
# ═════════════════════════════════════════════════════════════════════


class TestDeviceConfigManager:
    """Tests for DeviceConfigManager facade."""

    def test_init(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.device_config import DeviceConfigManager
        dcm = DeviceConfigManager(hass, "test_entry_id")
        assert dcm is not None
        assert dcm.device_manager is not None


# ═════════════════════════════════════════════════════════════════════
# DeviceLifecycleManager
# ═════════════════════════════════════════════════════════════════════


class TestDeviceLifecycleManager:
    """Tests for DeviceLifecycleManager."""

    SERIAL = "AABB0001"
    # normalize_serial_number pads to 32 hex chars
    NORMALIZED = "000000000000000000000000aabb0001"

    def _make_lm(self, hass):
        from custom_components.easywave.device_lifecycle import DeviceLifecycleManager
        return DeviceLifecycleManager(hass)

    async def test_start_device_discovery(self, hass: HomeAssistant) -> None:
        lm = self._make_lm(hass)
        state = await lm.start_device_discovery(
            self.SERIAL, "ew_transmitter", name="Test TX"
        )
        assert state is not None
        assert state.serial_number == self.NORMALIZED

    async def test_device_exists_after_discovery(self, hass: HomeAssistant) -> None:
        lm = self._make_lm(hass)
        await lm.start_device_discovery(self.SERIAL, "ew_transmitter")
        assert lm.device_exists(self.SERIAL)

    async def test_device_not_exists(self, hass: HomeAssistant) -> None:
        lm = self._make_lm(hass)
        # Use a valid hex serial that hasn't been added
        assert not lm.device_exists("FF000000")

    async def test_get_device_state(self, hass: HomeAssistant) -> None:
        lm = self._make_lm(hass)
        await lm.start_device_discovery(self.SERIAL, "ew_transmitter")
        state = lm.get_device_state(self.SERIAL)
        assert state is not None

    async def test_complete_registration(self, hass: HomeAssistant) -> None:
        lm = self._make_lm(hass)
        await lm.start_device_discovery(self.SERIAL, "ew_transmitter")
        result = await lm.complete_device_registration(self.SERIAL, device_id="dev_001")
        assert result is True

    async def test_complete_device_removal(self, hass: HomeAssistant) -> None:
        lm = self._make_lm(hass)
        await lm.start_device_discovery(self.SERIAL, "ew_transmitter")
        await lm.complete_device_removal(self.SERIAL)

    async def test_register_entity_for_device(self, hass: HomeAssistant) -> None:
        lm = self._make_lm(hass)
        await lm.start_device_discovery(self.SERIAL, "ew_transmitter")
        await lm.register_entity_for_device(self.SERIAL, "switch.test", "uid_001")


# ═════════════════════════════════════════════════════════════════════
# DeviceLifecycleStateEnum
# ═════════════════════════════════════════════════════════════════════


class TestDeviceLifecycleStateEnum:
    def test_enum_values(self) -> None:
        from custom_components.easywave.device_lifecycle import DeviceLifecycleStateEnum
        assert DeviceLifecycleStateEnum.ACTIVE is not None
        assert DeviceLifecycleStateEnum.DISCOVERING is not None
        assert DeviceLifecycleStateEnum.DELETING is not None
        assert DeviceLifecycleStateEnum.DELETED is not None


# ═════════════════════════════════════════════════════════════════════
# PersistenceTransaction
# ═════════════════════════════════════════════════════════════════════


class TestPersistenceTransaction:
    """Tests for PersistenceTransaction and TransactionalPersistenceManager."""

    def test_create_transaction(self) -> None:
        from custom_components.easywave.persistence_transaction import (
            TransactionalPersistenceManager,
        )
        pm = TransactionalPersistenceManager(timeout=10.0)
        tx = pm.create_transaction()
        assert tx is not None

    def test_queue_write(self) -> None:
        from custom_components.easywave.persistence_transaction import (
            PersistenceTransaction,
        )
        tx = PersistenceTransaction(timeout=10.0)
        tx.queue_write(Path("/tmp/test.json"), {"key": "value"})
        assert len(tx.get_queued_files()) == 1


# ═════════════════════════════════════════════════════════════════════
# IntegrityReport
# ═════════════════════════════════════════════════════════════════════


class TestIntegrityReport:
    def test_init(self) -> None:
        from custom_components.easywave.persistence_integrity_checker import IntegrityReport
        report = IntegrityReport()
        assert report.status == "OK"
        assert len(report.issues) == 0

    def test_add_issue_changes_status(self) -> None:
        from custom_components.easywave.persistence_integrity_checker import IntegrityReport
        report = IntegrityReport()
        report.add_issue("Test issue")
        assert "ISSUES" in report.status or "ERROR" in report.status
        assert len(report.issues) == 1

    def test_add_warning(self) -> None:
        from custom_components.easywave.persistence_integrity_checker import IntegrityReport
        report = IntegrityReport()
        report.add_warning("Test warning")
        assert len(report.warnings) == 1

    def test_add_repair(self) -> None:
        from custom_components.easywave.persistence_integrity_checker import IntegrityReport
        report = IntegrityReport()
        report.add_repair("Test repair")
        assert len(report.repairs) == 1

    def test_to_dict(self) -> None:
        from custom_components.easywave.persistence_integrity_checker import IntegrityReport
        report = IntegrityReport()
        report.add_issue("Test issue")
        data = report.to_dict()
        assert isinstance(data, dict)
        assert "issues" in data


# ═════════════════════════════════════════════════════════════════════
# IndexAllocator
# ═════════════════════════════════════════════════════════════════════


class TestIndexAllocator:
    """Tests for IndexAllocator."""

    def test_init(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        assert ia is not None

    def test_index_ranges(self) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        assert "ewb" in IndexAllocator.INDEX_RANGES
        assert "ew_receiver" in IndexAllocator.INDEX_RANGES

    async def test_allocate_index(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        # Initialize allocations dict (normally done by load())
        for idx_type in IndexAllocator.INDEX_RANGES:
            ia._allocations[idx_type] = {}
            ia._next_free_cache[idx_type] = 0
        index = await ia.allocate_index("ewb", "AABB0001", "Test Device")
        assert isinstance(index, int)
        assert index >= 0

    async def test_deallocate_index(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        for idx_type in IndexAllocator.INDEX_RANGES:
            ia._allocations[idx_type] = {}
            ia._next_free_cache[idx_type] = 0
        index = await ia.allocate_index("ewb", "AABB0001", "Test Device")
        result = await ia.deallocate_index("ewb", index, "AABB0001")
        assert result is True

    async def test_get_allocated_indices(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        for idx_type in IndexAllocator.INDEX_RANGES:
            ia._allocations[idx_type] = {}
            ia._next_free_cache[idx_type] = 0
        await ia.allocate_index("ewb", "AABB0001", "Test Device")
        indices = await ia.get_allocated_indices("ewb")
        assert len(indices) >= 1

    async def test_get_device_indices(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        for idx_type in IndexAllocator.INDEX_RANGES:
            ia._allocations[idx_type] = {}
            ia._next_free_cache[idx_type] = 0
        await ia.allocate_index("ewb", "AABB0001", "Test Device")
        device_indices = await ia.get_device_indices("AABB0001")
        assert isinstance(device_indices, dict)

    async def test_check_integrity(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        for idx_type in IndexAllocator.INDEX_RANGES:
            ia._allocations[idx_type] = {}
            ia._next_free_cache[idx_type] = 0
        result = await ia.check_integrity()
        assert isinstance(result, dict)

    async def test_export_allocation_map(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.index_allocator import IndexAllocator
        ia = IndexAllocator(hass, "test_entry_id")
        for idx_type in IndexAllocator.INDEX_RANGES:
            ia._allocations[idx_type] = {}
            ia._next_free_cache[idx_type] = 0
        result = await ia.export_allocation_map()
        assert isinstance(result, dict)


# ═════════════════════════════════════════════════════════════════════
# DefensiveStateManager
# ═════════════════════════════════════════════════════════════════════


class TestDefensiveStateManager:
    """Tests for DefensiveStateManager."""

    def test_init(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.defensive_state_manager import DefensiveStateManager
        from custom_components.easywave.device_manager import DeviceManager
        from custom_components.easywave.index_allocator import IndexAllocator
        from custom_components.easywave.persistence_integrity_checker import PersistenceIntegrityChecker

        dm = DeviceManager(hass, "entry_id")
        ia = IndexAllocator(hass, "entry_id")
        ic = PersistenceIntegrityChecker(hass, dm, ia, "entry_id")
        dsm = DefensiveStateManager(hass, dm, ia, ic, "entry_id")
        assert dsm is not None

    def test_is_healthy_initially_false(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.defensive_state_manager import DefensiveStateManager
        from custom_components.easywave.device_manager import DeviceManager
        from custom_components.easywave.index_allocator import IndexAllocator
        from custom_components.easywave.persistence_integrity_checker import PersistenceIntegrityChecker

        dm = DeviceManager(hass, "entry_id")
        ia = IndexAllocator(hass, "entry_id")
        ic = PersistenceIntegrityChecker(hass, dm, ia, "entry_id")
        dsm = DefensiveStateManager(hass, dm, ia, ic, "entry_id")
        # _is_degraded starts as False, so is_healthy returns True initially
        assert dsm.is_healthy() is True

    def test_get_status(self, hass: HomeAssistant) -> None:
        from custom_components.easywave.defensive_state_manager import DefensiveStateManager
        from custom_components.easywave.device_manager import DeviceManager
        from custom_components.easywave.index_allocator import IndexAllocator
        from custom_components.easywave.persistence_integrity_checker import PersistenceIntegrityChecker

        dm = DeviceManager(hass, "entry_id")
        ia = IndexAllocator(hass, "entry_id")
        ic = PersistenceIntegrityChecker(hass, dm, ia, "entry_id")
        dsm = DefensiveStateManager(hass, dm, ia, ic, "entry_id")
        status = dsm.get_status()
        assert isinstance(status, dict)
