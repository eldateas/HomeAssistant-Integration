"""Tests for EASYWAVE services module."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant, ServiceCall

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywave.const import DOMAIN
from custom_components.easywave.services import (
    SERVICE_RESET_ENTITY_REGISTRY,
    SERVICE_RELOAD_SENSORS,
    SERVICE_FIX_TRANSCEIVER,
    SERVICE_SAVE_DEVICES_TO_REGISTRY,
    SERVICE_UPDATE_TRANSLATIONS,
    _get_coordinator,
    async_setup_services,
    async_unload_services,
    async_update_device_translations,
    auto_detect_transceiver_index,
    fix_transceiver_device,
)

from .conftest import (
    MOCK_CONFIG_DATA,
    MOCK_SERIAL_NUMBER,
    create_mock_coordinator,
)


# ═══════════════════════════════════════════════════════
# _get_coordinator
# ═══════════════════════════════════════════════════════


class TestGetCoordinator:
    """Tests for _get_coordinator helper."""

    def test_returns_none_when_no_domain(self, hass: HomeAssistant) -> None:
        assert _get_coordinator(hass) is None

    def test_returns_none_when_empty(self, hass: HomeAssistant) -> None:
        hass.data[DOMAIN] = {}
        assert _get_coordinator(hass) is None

    def test_returns_coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        from custom_components.easywave.coordinator import EasywaveCoordinator

        coordinator = create_mock_coordinator(hass, mock_config_entry)
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
        # _get_coordinator checks isinstance(data, EasywaveCoordinator)
        # MagicMock won't pass, so we mock the spec
        coordinator_with_spec = MagicMock(spec=EasywaveCoordinator)
        hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator_with_spec}
        result = _get_coordinator(hass)
        assert result is coordinator_with_spec

    def test_returns_none_when_no_matching_type(self, hass: HomeAssistant) -> None:
        hass.data[DOMAIN] = {"entry_1": "not_a_coordinator"}
        assert _get_coordinator(hass) is None


# ═══════════════════════════════════════════════════════
# async_setup_services / async_unload_services
# ═══════════════════════════════════════════════════════


class TestServiceSetup:
    """Tests for service registration and unregistration."""

    async def test_register_services(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coordinator

        await async_setup_services(hass, mock_config_entry)

        # Verify all services exist
        assert hass.services.has_service(DOMAIN, SERVICE_RESET_ENTITY_REGISTRY)
        assert hass.services.has_service(DOMAIN, SERVICE_RELOAD_SENSORS)
        assert hass.services.has_service(DOMAIN, SERVICE_FIX_TRANSCEIVER)
        assert hass.services.has_service(DOMAIN, SERVICE_SAVE_DEVICES_TO_REGISTRY)
        assert hass.services.has_service(DOMAIN, "refresh_entity_specs")
        assert hass.services.has_service(DOMAIN, SERVICE_UPDATE_TRANSLATIONS)

    async def test_unload_services(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coordinator

        await async_setup_services(hass, mock_config_entry)
        await async_unload_services(hass)

        assert not hass.services.has_service(DOMAIN, SERVICE_RESET_ENTITY_REGISTRY)
        assert not hass.services.has_service(DOMAIN, SERVICE_RELOAD_SENSORS)
        assert not hass.services.has_service(DOMAIN, SERVICE_FIX_TRANSCEIVER)
        assert not hass.services.has_service(DOMAIN, SERVICE_SAVE_DEVICES_TO_REGISTRY)
        assert not hass.services.has_service(DOMAIN, "refresh_entity_specs")
        assert not hass.services.has_service(DOMAIN, SERVICE_UPDATE_TRANSLATIONS)


# ═══════════════════════════════════════════════════════
# Service handlers
# ═══════════════════════════════════════════════════════


class TestServiceHandlers:
    """Tests for individual service handler behaviours."""

    async def _setup(self, hass, mock_config_entry):
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coordinator
        await async_setup_services(hass, mock_config_entry)
        return coordinator

    async def test_handle_reload_sensors(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        coordinator = await self._setup(hass, mock_config_entry)
        coordinator._load_device_configuration = AsyncMock()

        await hass.services.async_call(DOMAIN, SERVICE_RELOAD_SENSORS, {})
        await hass.async_block_till_done()

        coordinator._load_device_configuration.assert_called_once_with(fire_events=True)

    async def test_handle_reset_entity_registry(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        coordinator = await self._setup(hass, mock_config_entry)

        with patch(
            "custom_components.easywave.services.get_entity_registry"
        ) as mock_get_reg:
            mock_registry = MagicMock()
            mock_get_reg.return_value = mock_registry

            # Patch config entry reload to avoid actually reloading
            with patch.object(
                hass.config_entries, "async_reload", new=AsyncMock()
            ):
                await hass.services.async_call(
                    DOMAIN, SERVICE_RESET_ENTITY_REGISTRY, {}
                )
                await hass.async_block_till_done()

            mock_registry.clear.assert_called_once()

    async def test_handle_fix_transceiver_no_serial(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """fix_transceiver without serial_number logs error, does nothing."""
        await self._setup(hass, mock_config_entry)

        # Should not raise
        await hass.services.async_call(DOMAIN, SERVICE_FIX_TRANSCEIVER, {})
        await hass.async_block_till_done()

    async def test_handle_fix_transceiver_with_serial(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        coordinator = await self._setup(hass, mock_config_entry)

        with patch(
            "custom_components.easywave.services.fix_transceiver_device",
            new=AsyncMock(return_value=False),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_FIX_TRANSCEIVER,
                {"serial_number": "AABB0001"},
            )
            await hass.async_block_till_done()


# ═══════════════════════════════════════════════════════
# auto_detect_transceiver_index
# ═══════════════════════════════════════════════════════


class TestAutoDetectTransceiverIndex:
    """Tests for auto_detect_transceiver_index helper."""

    async def test_no_wrapper(self) -> None:
        coordinator = MagicMock()
        coordinator.gateway_wrapper = None
        result = await auto_detect_transceiver_index(coordinator, "AABB0001")
        assert result is None

    async def test_finds_matching_serial(self) -> None:
        wrapper = MagicMock()
        wrapper._ensure_receiver_cached = AsyncMock(
            side_effect=lambda idx: "XXXXXXXXAABB0001" if idx == 2 else None
        )
        coordinator = MagicMock()
        coordinator.gateway_wrapper = wrapper

        result = await auto_detect_transceiver_index(coordinator, "AABB0001")
        assert result == 2

    async def test_no_match(self) -> None:
        wrapper = MagicMock()
        wrapper._ensure_receiver_cached = AsyncMock(return_value=None)
        coordinator = MagicMock()
        coordinator.gateway_wrapper = wrapper

        result = await auto_detect_transceiver_index(coordinator, "AABB0001")
        assert result is None

    async def test_exception_handling(self) -> None:
        coordinator = MagicMock()
        coordinator.gateway_wrapper = MagicMock()
        coordinator.gateway_wrapper._ensure_receiver_cached = AsyncMock(
            side_effect=RuntimeError("fail")
        )
        # Should not raise
        result = await auto_detect_transceiver_index(coordinator, "AABB0001")
        assert result is None


# ═══════════════════════════════════════════════════════
# fix_transceiver_device
# ═══════════════════════════════════════════════════════


class TestFixTransceiverDevice:
    """Tests for fix_transceiver_device helper."""

    async def test_device_not_found(self) -> None:
        coordinator = MagicMock()
        coordinator.device_registry = MagicMock()
        coordinator.device_registry.get_device_by_serial = AsyncMock(return_value=None)

        result = await fix_transceiver_device(coordinator, "AABB0001")
        assert result is False

    async def test_device_found_index_detected(self) -> None:
        coordinator = MagicMock()
        coordinator.device_registry = MagicMock()
        coordinator.device_registry.get_device_by_serial = AsyncMock(
            return_value={"serial_number": "AABB0001"}
        )
        coordinator.device_registry.update_device = MagicMock()

        with patch(
            "custom_components.easywave.services.auto_detect_transceiver_index",
            new=AsyncMock(return_value=3),
        ):
            result = await fix_transceiver_device(coordinator, "AABB0001")

        assert result is True
        coordinator.device_registry.update_device.assert_called_once()

    async def test_device_found_no_index(self) -> None:
        coordinator = MagicMock()
        coordinator.device_registry = MagicMock()
        coordinator.device_registry.get_device_by_serial = AsyncMock(
            return_value={"serial_number": "AABB0001"}
        )

        with patch(
            "custom_components.easywave.services.auto_detect_transceiver_index",
            new=AsyncMock(return_value=None),
        ):
            result = await fix_transceiver_device(coordinator, "AABB0001")

        assert result is False

    async def test_exception_handling(self) -> None:
        coordinator = MagicMock()
        coordinator.device_registry = MagicMock()
        coordinator.device_registry.get_device_by_serial = AsyncMock(
            side_effect=RuntimeError("fail")
        )
        result = await fix_transceiver_device(coordinator, "AABB0001")
        assert result is False


# ═══════════════════════════════════════════════════════
# async_update_device_translations
# ═══════════════════════════════════════════════════════


class TestAsyncUpdateDeviceTranslations:
    """Tests for async_update_device_translations."""

    async def test_no_devices(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        mock_config_entry.add_to_hass(hass)

        with patch(
            "custom_components.easywave.services.get_language",
            return_value="en",
        ):
            result = await async_update_device_translations(hass, coordinator)

        assert result["updated"] == 0
        assert result["errors"] == 0
        assert result["language"] == "en"

    async def test_returns_dict(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        coordinator = create_mock_coordinator(hass, mock_config_entry)
        mock_config_entry.add_to_hass(hass)

        with patch(
            "custom_components.easywave.services.get_language",
            return_value="de",
        ):
            result = await async_update_device_translations(hass, coordinator)

        assert isinstance(result, dict)
        assert "updated" in result
        assert "errors" in result
        assert "language" in result


# ═══════════════════════════════════════════════════════
# Service constants
# ═══════════════════════════════════════════════════════


class TestServiceConstants:
    """Tests for service constant definitions."""

    def test_constants_defined(self) -> None:
        assert SERVICE_RESET_ENTITY_REGISTRY == "reset_entity_registry"
        assert SERVICE_RELOAD_SENSORS == "reload_sensors"
        assert SERVICE_FIX_TRANSCEIVER == "fix_transceiver"
        assert SERVICE_SAVE_DEVICES_TO_REGISTRY == "save_devices_to_registry"
        assert SERVICE_UPDATE_TRANSLATIONS == "update_translations"
