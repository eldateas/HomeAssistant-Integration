"""Additional tests for config_flow deeper sub-flows."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywave.const import DOMAIN

from .conftest import (
    MOCK_CONFIG_DATA,
    create_mock_coordinator,
)


# ─────── Transmitter 1-button sub-flow ────────────────────────────────

async def test_transmitter_1button_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test transmitter 1-button selection shows grouping options."""
    mock_config_entry.add_to_hass(hass)
    coordinator = create_mock_coordinator(hass, mock_config_entry, connected=True)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.MENU

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "device_transmitter_config"},
    )
    assert result2["type"] == FlowResultType.MENU

    # Select 1-button → should go to grouping
    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {"next_step_id": "device_transmitter_1button"},
    )
    assert result3["type"] == FlowResultType.MENU
    assert result3["step_id"] == "device_transmitter_grouping"
    assert "device_transmitter_grouping_single" in result3["menu_options"]
    assert "device_transmitter_grouping_group" in result3["menu_options"]


async def test_transmitter_2button_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test transmitter 2-button selection shows usage options."""
    mock_config_entry.add_to_hass(hass)
    coordinator = create_mock_coordinator(hass, mock_config_entry, connected=True)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "device_transmitter_config"},
    )
    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {"next_step_id": "device_transmitter_2button"},
    )
    assert result3["type"] == FlowResultType.MENU
    assert result3["step_id"] == "device_transmitter_2button_usage"


async def test_transmitter_3button_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test transmitter 3-button selection goes to description step."""
    mock_config_entry.add_to_hass(hass)
    coordinator = create_mock_coordinator(hass, mock_config_entry, connected=True)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "device_transmitter_config"},
    )
    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {"next_step_id": "device_transmitter_3button"},
    )
    # Should show description/confirmation form
    assert result3["type"] in (FlowResultType.FORM, FlowResultType.MENU)


# ─────── Device cancel ────────────────────────────────────────────────

async def test_device_cancel_returns_to_menu(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test cancel returns to type select menu."""
    mock_config_entry.add_to_hass(hass)
    coordinator = create_mock_coordinator(hass, mock_config_entry, connected=True)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    # Navigate to transmitter config, then back to type select
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "device_transmitter_config"},
    )
    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {"next_step_id": "device_type_select"},
    )
    assert result3["type"] == FlowResultType.MENU
    assert result3["step_id"] == "device_type_select"


# ─────── Receiver sub-flow ────────────────────────────────────────────

async def test_receiver_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test receiver device selection shows receiver kind options."""
    mock_config_entry.add_to_hass(hass)
    coordinator = create_mock_coordinator(hass, mock_config_entry, connected=True)
    # Receiver flow needs async methods on coordinator
    coordinator.get_next_free_ew_receiver_index = AsyncMock(return_value=0)
    coordinator.transceiver.rx11_ew_receiver_get_serial_by_index = AsyncMock(
        return_value="AABB0001"
    )
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "device_receiver"},
    )
    # Should show receiver kind selection menu
    assert result2["type"] == FlowResultType.MENU
    assert result2["step_id"] == "device_receiver_type"


# ─────── Sensor sub-flow ─────────────────────────────────────────────

async def test_sensor_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test sensor device selection flow."""
    mock_config_entry.add_to_hass(hass)
    coordinator = create_mock_coordinator(hass, mock_config_entry, connected=True)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "device_sensor"},
    )
    # Should show some form or learning step
    assert result2["type"] in (FlowResultType.FORM, FlowResultType.MENU, FlowResultType.SHOW_PROGRESS)


# ─────── EWneo receiver sub-flow ─────────────────────────────────────

async def test_ewneo_receiver_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test EWneo receiver device selection flow."""
    mock_config_entry.add_to_hass(hass)
    coordinator = create_mock_coordinator(hass, mock_config_entry, connected=True)
    # EWneo flow needs several async methods
    mock_wrapper = MagicMock()
    mock_wrapper.is_connected.return_value = True
    coordinator.transceiver._rx11_wrapper = mock_wrapper
    coordinator.get_next_free_ewb_index = AsyncMock(return_value=0)
    coordinator.is_ewb_index_used = MagicMock(return_value=True)
    coordinator.transceiver.rx11_ewb_get_serial_by_index = AsyncMock(
        return_value="CCDD0001EEFF0002"
    )
    coordinator.transceiver.rx11_ewb_add_filter = AsyncMock(return_value=True)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "device_ewneo_receiver"},
    )
    # Should show programming mode instructions menu
    assert result2["type"] in (FlowResultType.FORM, FlowResultType.MENU)


# ─────── No transceiver configured ───────────────────────────────────

async def test_device_step_no_entry(
    hass: HomeAssistant,
) -> None:
    """Test flow aborts when no transceiver is detected and no entry exists."""
    # Don't add any entry to hass — auto-detection will find no USB devices

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    # No USB devices → abort with "no_devices"
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_devices"
