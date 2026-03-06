"""Tests for the EASYWAVE coordinator."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywave.const import (
    DOMAIN,
    CONF_DEVICE_PATH,
    CONF_DEVICE_NAME,
    CONF_TRANSCEIVER_TYPE,
    DEVICE_SCAN_INTERVAL,
)
from custom_components.easywave.coordinator import EasywaveCoordinator

from .conftest import (
    MOCK_SERIAL_NUMBER,
    MOCK_DEVICE_PATH,
    MOCK_CONFIG_DATA,
    create_mock_transceiver,
    create_mock_coordinator,
)


# ─────── Coordinator initialisation ─────────────────────────────────

class TestCoordinatorInit:
    """Tests for coordinator construction and initialisation."""

    async def test_basic_attributes(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test that basic coordinator attributes are set correctly."""
        coord = create_mock_coordinator(hass, mock_config_entry)
        assert coord.hass is hass
        assert coord.config_entry is mock_config_entry

    async def test_device_dict_initially_empty(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test devices dict is empty on creation."""
        coord = create_mock_coordinator(hass, mock_config_entry)
        assert coord.devices == {}

    async def test_setup_mode_starts_inactive(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test setup mode is inactive initially."""
        coord = create_mock_coordinator(hass, mock_config_entry)
        assert coord._config_flow_learning is False


# ─────── EWneo failure tracking ──────────────────────────────────────

class TestEWneoFailureTracking:
    """Tests for EWneo failure tracking."""

    async def test_failure_counts_start_empty(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Failure counts are empty initially."""
        mock_config_entry.add_to_hass(hass)
        transceiver = create_mock_transceiver()

        with (
            patch(
                "custom_components.easywave.coordinator.DeviceConfigManager",
            ),
            patch(
                "custom_components.easywave.coordinator.IndexAllocator",
            ),
            patch(
                "custom_components.easywave.coordinator.PersistenceIntegrityChecker",
            ),
            patch(
                "custom_components.easywave.coordinator.TransactionalPersistenceManager",
            ),
            patch(
                "custom_components.easywave.coordinator.DefensiveStateManager",
            ),
            patch(
                "custom_components.easywave.coordinator.DeviceLifecycleManager",
            ),
            patch(
                "custom_components.easywave.coordinator.StateManager",
            ),
        ):
            coord = EasywaveCoordinator(
                hass,
                transceiver,
                mock_config_entry,
                update_interval=timedelta(seconds=30),
            )

        assert coord._ewneo_failure_counts == {}
        assert len(coord._ewneo_unreachable_notified) == 0

    async def test_report_failure_increments(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """report_ewneo_communication_failure increments counter."""
        mock_config_entry.add_to_hass(hass)
        transceiver = create_mock_transceiver()

        with (
            patch(
                "custom_components.easywave.coordinator.DeviceConfigManager",
            ),
            patch(
                "custom_components.easywave.coordinator.IndexAllocator",
            ),
            patch(
                "custom_components.easywave.coordinator.PersistenceIntegrityChecker",
            ),
            patch(
                "custom_components.easywave.coordinator.TransactionalPersistenceManager",
            ),
            patch(
                "custom_components.easywave.coordinator.DefensiveStateManager",
            ),
            patch(
                "custom_components.easywave.coordinator.DeviceLifecycleManager",
            ),
            patch(
                "custom_components.easywave.coordinator.StateManager",
            ),
        ):
            coord = EasywaveCoordinator(
                hass,
                transceiver,
                mock_config_entry,
                update_interval=timedelta(seconds=30),
            )

        # Patch the async helper to avoid scheduling real tasks
        coord._get_device_friendly_name = AsyncMock(return_value="Test Device")

        result1 = await coord.report_ewneo_communication_failure("SERIAL001")
        assert coord._ewneo_failure_counts["SERIAL001"] == 1
        assert result1 is False

        result2 = await coord.report_ewneo_communication_failure("SERIAL001")
        assert coord._ewneo_failure_counts["SERIAL001"] == 2
        assert result2 is True


    async def test_clear_failure_resets_count(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """report_ewneo_communication_success resets the counter."""
        mock_config_entry.add_to_hass(hass)
        transceiver = create_mock_transceiver()

        with (
            patch(
                "custom_components.easywave.coordinator.DeviceConfigManager",
            ),
            patch(
                "custom_components.easywave.coordinator.IndexAllocator",
            ),
            patch(
                "custom_components.easywave.coordinator.PersistenceIntegrityChecker",
            ),
            patch(
                "custom_components.easywave.coordinator.TransactionalPersistenceManager",
            ),
            patch(
                "custom_components.easywave.coordinator.DefensiveStateManager",
            ),
            patch(
                "custom_components.easywave.coordinator.DeviceLifecycleManager",
            ),
            patch(
                "custom_components.easywave.coordinator.StateManager",
            ),
        ):
            coord = EasywaveCoordinator(
                hass,
                transceiver,
                mock_config_entry,
                update_interval=timedelta(seconds=30),
            )

        coord._ewneo_failure_counts["SERIAL001"] = 5
        coord._ewneo_unreachable_notified.add("SERIAL001")
        
        # Register persistent_notification service so the coordinator can call it
        hass.services.async_register(
            "persistent_notification", "dismiss", AsyncMock()
        )
        await coord.report_ewneo_communication_success("SERIAL001")
        
        assert coord._ewneo_failure_counts.get("SERIAL001", 0) == 0
        assert "SERIAL001" not in coord._ewneo_unreachable_notified


# ─────── Setup mode ──────────────────────────────────────────────────

class TestSetupMode:
    """Tests for setup mode."""

    async def test_start_stop_setup_mode(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test start and stop setup mode."""
        mock_config_entry.add_to_hass(hass)
        transceiver = create_mock_transceiver()

        with (
            patch(
                "custom_components.easywave.coordinator.DeviceConfigManager",
            ),
            patch(
                "custom_components.easywave.coordinator.IndexAllocator",
            ),
            patch(
                "custom_components.easywave.coordinator.PersistenceIntegrityChecker",
            ),
            patch(
                "custom_components.easywave.coordinator.TransactionalPersistenceManager",
            ),
            patch(
                "custom_components.easywave.coordinator.DefensiveStateManager",
            ),
            patch(
                "custom_components.easywave.coordinator.DeviceLifecycleManager",
            ),
            patch(
                "custom_components.easywave.coordinator.StateManager",
            ),
        ):
            coord = EasywaveCoordinator(
                hass,
                transceiver,
                mock_config_entry,
                update_interval=timedelta(seconds=30),
            )

        assert coord._setup_mode_active is False

        coord.start_setup_mode(timeout_seconds=5)
        assert coord._setup_mode_active is True

        coord.stop_setup_mode()
        assert coord._setup_mode_active is False


# ─────── Platform handler registration ───────────────────────────────

class TestPlatformHandlers:
    """Tests for platform handler registration."""

    async def test_register_handler(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test registering a platform handler."""
        mock_config_entry.add_to_hass(hass)
        transceiver = create_mock_transceiver()

        with (
            patch(
                "custom_components.easywave.coordinator.DeviceConfigManager",
            ),
            patch(
                "custom_components.easywave.coordinator.IndexAllocator",
            ),
            patch(
                "custom_components.easywave.coordinator.PersistenceIntegrityChecker",
            ),
            patch(
                "custom_components.easywave.coordinator.TransactionalPersistenceManager",
            ),
            patch(
                "custom_components.easywave.coordinator.DefensiveStateManager",
            ),
            patch(
                "custom_components.easywave.coordinator.DeviceLifecycleManager",
            ),
            patch(
                "custom_components.easywave.coordinator.StateManager",
            ),
        ):
            coord = EasywaveCoordinator(
                hass,
                transceiver,
                mock_config_entry,
                update_interval=timedelta(seconds=30),
            )

        handler = AsyncMock()
        coord.register_platform_handler("switch", handler)
        assert "switch" in coord._platform_handlers
        assert coord._platform_handlers["switch"] is handler
