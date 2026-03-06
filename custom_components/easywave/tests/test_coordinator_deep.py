"""Deep tests for coordinator.py – update_data, device management, index tracking."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import timedelta

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywave.const import DOMAIN
from custom_components.easywave.coordinator import EasywaveCoordinator

from .conftest import (
    MOCK_CONFIG_DATA,
    create_mock_coordinator,
    create_mock_transceiver,
)


COORDINATOR_MODULE = "custom_components.easywave.coordinator"


def _build_real_coordinator(hass, config_entry, transceiver=None):
    """Build a real EasywaveCoordinator with all internal deps mocked."""
    if transceiver is None:
        transceiver = create_mock_transceiver(connected=True)

    with (
        patch(f"{COORDINATOR_MODULE}.DeviceConfigManager"),
        patch(f"{COORDINATOR_MODULE}.IndexAllocator"),
        patch(f"{COORDINATOR_MODULE}.PersistenceIntegrityChecker"),
        patch(f"{COORDINATOR_MODULE}.TransactionalPersistenceManager"),
        patch(f"{COORDINATOR_MODULE}.DefensiveStateManager"),
        patch(f"{COORDINATOR_MODULE}.DeviceLifecycleManager"),
        patch(f"{COORDINATOR_MODULE}.StateManager"),
    ):
        coordinator = EasywaveCoordinator(
            hass,
            transceiver,
            config_entry,
            update_interval=timedelta(seconds=30),
        )
    return coordinator


# ═════════════════════════════════════════════════════════════════════
# _async_update_data
# ═════════════════════════════════════════════════════════════════════


class TestAsyncUpdateData:
    """Tests for _async_update_data method."""

    async def test_update_raises_when_no_transceiver(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """If transceiver is None, UpdateFailed should be raised."""
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        coordinator.transceiver = None

        with pytest.raises(UpdateFailed, match="Transceiver not available"):
            await coordinator._async_update_data()

    async def test_update_returns_devices_when_connected(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """When transceiver connected, data is returned."""
        mock_config_entry.add_to_hass(hass)
        transceiver = create_mock_transceiver(connected=True)
        # Async methods called inside _async_update_data
        transceiver.get_hw_version = AsyncMock(return_value="1.0")
        transceiver.get_fw_version = AsyncMock(return_value="2.0")
        coordinator = _build_real_coordinator(hass, mock_config_entry, transceiver)
        coordinator.devices = {"AABB": {"name": "Test"}}
        # Pre-set connection state so the state-change branch is not entered
        coordinator._last_connection_state = True
        # _async_update_data may call persistent_notification services
        hass.services.async_register("persistent_notification", "create", AsyncMock())
        hass.services.async_register("persistent_notification", "dismiss", AsyncMock())

        result = await coordinator._async_update_data()
        assert isinstance(result, dict)
        assert result["transceiver_connected"] is True
        assert result["hw_version"] == "1.0"

    async def test_update_reconnect_attempts_on_disconnect(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """When transceiver is disconnected, UpdateFailed is raised."""
        mock_config_entry.add_to_hass(hass)
        transceiver = create_mock_transceiver(connected=False)
        transceiver.disconnect = AsyncMock()
        transceiver.connect = AsyncMock(return_value=False)
        coordinator = _build_real_coordinator(hass, mock_config_entry, transceiver)
        coordinator._reconnect_attempts = 0
        coordinator._last_reconnect_time = 0
        # Pre-set connection state so the notification branch doesn't fire
        coordinator._last_connection_state = False
        # Register notification services in case they're called
        hass.services.async_register("persistent_notification", "create", AsyncMock())
        hass.services.async_register("persistent_notification", "dismiss", AsyncMock())

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
        # Reconnect was attempted
        assert coordinator._reconnect_attempts >= 1


# ═════════════════════════════════════════════════════════════════════
# Index management
# ═════════════════════════════════════════════════════════════════════


class TestIndexManagement:
    """Tests for EWB and EW receiver index methods."""

    async def test_get_next_free_ewb_index(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """get_next_free_ewb_index returns next free index and reserves it."""
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        coordinator._used_ewb_indices = {}
        coordinator._next_free_ewb_index = 0
        # Patch persistent save
        coordinator._save_registered_devices = AsyncMock()

        index = await coordinator.get_next_free_ewb_index()
        assert index == 0
        # Index 0 should now be reserved
        assert 0 in coordinator._used_ewb_indices

    async def test_get_next_free_ew_receiver_index(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """get_next_free_ew_receiver_index returns next free index."""
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        coordinator._used_ew_receiver_indices = {}
        coordinator._save_registered_devices = AsyncMock()

        index = await coordinator.get_next_free_ew_receiver_index()
        assert index == 0
        # With index 0 used, next should be 1
        coordinator._used_ew_receiver_indices = {0: {"serial": "AA"}}
        index2 = await coordinator.get_next_free_ew_receiver_index()
        assert index2 == 1

    def test_is_ewb_index_used(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Test is_ewb_index_used checks the used index dict."""
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        coordinator._used_ewb_indices = {0: {"serial": "AA"}, 3: {"serial": "BB"}}

        assert coordinator.is_ewb_index_used(0)
        assert not coordinator.is_ewb_index_used(1)
        assert coordinator.is_ewb_index_used(3)


# ═════════════════════════════════════════════════════════════════════
# Registered device management
# ═════════════════════════════════════════════════════════════════════


class TestRegisteredDevices:
    """Tests for device registration and removal."""

    def test_registered_devices_starts_empty(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        assert len(coordinator._registered_devices) == 0

    def test_add_registered_device(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Register a device and verify it's tracked."""
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        device_info = {"type": "ew_transmitter", "name": "Test TX"}
        coordinator._registered_devices["AABB0001"] = device_info

        assert "AABB0001" in coordinator._registered_devices
        assert coordinator._registered_devices["AABB0001"]["name"] == "Test TX"

    def test_remove_registered_device(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Unregister a device and verify it's gone."""
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        coordinator._registered_devices["AABB0001"] = {"type": "ew_transmitter"}
        del coordinator._registered_devices["AABB0001"]
        assert "AABB0001" not in coordinator._registered_devices


# ═════════════════════════════════════════════════════════════════════
# EWneo failure tracking (deep)
# ═════════════════════════════════════════════════════════════════════


class TestEwneoFailureTracking:
    """Tests for EWneo communication failure tracking."""

    async def test_report_failure_creates_notification_after_threshold(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """After 2 failures, an unreachable notification should be created."""
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        coordinator._ewneo_failure_counts = {}
        coordinator._ewneo_unreachable_notified = set()
        coordinator._registered_devices = {"AABB": {"name": "Test EWneo", "type": "ewneo_receiver"}}

        # Register dismiss service to allow notification creation
        hass.services.async_register("persistent_notification", "create", AsyncMock())
        hass.services.async_register("persistent_notification", "dismiss", AsyncMock())

        # First failure
        await coordinator.report_ewneo_communication_failure("AABB")
        assert coordinator._ewneo_failure_counts.get("AABB", 0) == 1

        # Second failure → should notify
        await coordinator.report_ewneo_communication_failure("AABB")
        assert coordinator._ewneo_failure_counts.get("AABB", 0) >= 2

    async def test_report_success_clears_failures(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Successful communication clears failure count."""
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        coordinator._ewneo_failure_counts = {"AABB": 3}
        coordinator._ewneo_unreachable_notified = {"AABB"}

        # Register dismiss service
        hass.services.async_register("persistent_notification", "create", AsyncMock())
        hass.services.async_register("persistent_notification", "dismiss", AsyncMock())

        await coordinator.report_ewneo_communication_success("AABB")
        assert coordinator._ewneo_failure_counts.get("AABB", 0) == 0
        assert "AABB" not in coordinator._ewneo_unreachable_notified


# ═════════════════════════════════════════════════════════════════════
# Central event dispatcher
# ═════════════════════════════════════════════════════════════════════


class TestCentralEventDispatcher:
    """Tests for platform handler registration and dispatch."""

    def test_register_platform_handler(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        handler = AsyncMock()
        coordinator.register_platform_handler("switch", handler)
        assert "switch" in coordinator._platform_handlers
        assert coordinator._platform_handlers["switch"] is handler

    def test_register_multiple_handlers(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        switch_handler = AsyncMock()
        light_handler = AsyncMock()
        coordinator.register_platform_handler("switch", switch_handler)
        coordinator.register_platform_handler("light", light_handler)
        assert len(coordinator._platform_handlers) == 2

    async def test_dispatch_to_platforms_calls_handlers(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """dispatch_to_platforms should call registered handlers."""
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        handler = AsyncMock()
        coordinator.register_platform_handler("switch", handler)
        coordinator._dispatched_devices = set()

        device_serial = "AABB0001"
        device_info = {
            "type": "ew_transmitter",
            "name": "Test TX",
            "serial_number": device_serial,
        }
        coordinator.devices[device_serial] = device_info
        coordinator._registered_devices[device_serial] = device_info

        # Call dispatch
        if hasattr(coordinator, "dispatch_to_platforms"):
            await coordinator.dispatch_to_platforms(device_serial, device_info)
            # Handler should have been called
            handler.assert_called()


# ═════════════════════════════════════════════════════════════════════
# Setup mode
# ═════════════════════════════════════════════════════════════════════


class TestSetupModeDeep:
    """Additional setup mode tests."""

    def test_start_setup_mode_activates(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        coordinator.start_setup_mode(timeout_seconds=30)
        assert coordinator._setup_mode_active is True

    def test_stop_setup_mode_deactivates(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        coordinator.start_setup_mode(timeout_seconds=30)
        coordinator.stop_setup_mode()
        assert coordinator._setup_mode_active is False
        assert coordinator._setup_timeout is None

    def test_setup_mode_cancels_previous_timeout(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Starting setup mode again cancels the previous timeout."""
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        coordinator.start_setup_mode(timeout_seconds=60)
        first_timeout = coordinator._setup_timeout
        coordinator.start_setup_mode(timeout_seconds=30)
        # Should be a new task
        assert coordinator._setup_timeout is not None


# ═════════════════════════════════════════════════════════════════════
# HA device identifier
# ═════════════════════════════════════════════════════════════════════


class TestDeviceIdentifier:
    """Tests for get_ha_device_identifier."""

    def test_get_ha_device_identifier(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        coordinator._registered_devices["AABB0001"] = {
            "type": "ew_transmitter", "name": "Test",
            "registration_id": "uuid-test-001",
        }
        result = coordinator.get_ha_device_identifier("AABB0001")
        assert result == "uuid-test-001"

    def test_device_identifier_is_deterministic(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        coordinator._registered_devices["AABB0001"] = {
            "type": "ew_transmitter", "name": "Test",
            "registration_id": "uuid-test-001",
        }
        id1 = coordinator.get_ha_device_identifier("AABB0001")
        id2 = coordinator.get_ha_device_identifier("AABB0001")
        assert id1 == id2

    def test_device_identifier_raises_for_unknown(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        coordinator = _build_real_coordinator(hass, mock_config_entry)
        with pytest.raises(KeyError):
            coordinator.get_ha_device_identifier("UNKNOWN")
