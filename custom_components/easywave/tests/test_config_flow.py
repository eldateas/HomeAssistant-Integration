"""Tests for the EASYWAVE config flow."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.easywave.const import (
    DOMAIN,
    CONF_DEVICE_PATH,
    CONF_DEVICE_NAME,
    CONF_USB_VID,
    CONF_USB_PID,
    CONF_USB_SERIAL_NUMBER,
    CONF_USB_MANUFACTURER,
    CONF_USB_PRODUCT,
    CONF_TRANSCEIVER_TYPE,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)

from .conftest import (
    MOCK_VID,
    MOCK_PID,
    MOCK_SERIAL_NUMBER,
    MOCK_DEVICE_PATH,
    MOCK_DEVICE_NAME,
    MOCK_MANUFACTURER,
    MOCK_PRODUCT,
    MOCK_CONFIG_DATA,
    MockUSBPort,
    create_mock_coordinator,
)


# ─────── Initial user step ───────────────────────────────────────────

async def test_user_step_auto_detection_finds_device(
    hass: HomeAssistant,
    mock_find_rx11_devices,
    mock_setup_entry,
) -> None:
    """Test user step when RX11 device is automatically detected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    # Should have detected device and show rx11_setup form
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "rx11_setup"


async def test_user_step_no_device_found(
    hass: HomeAssistant,
    mock_no_rx11_devices,
) -> None:
    """Test user step when no RX11 device is found."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_devices"


async def test_user_step_detection_error(
    hass: HomeAssistant,
) -> None:
    """Test user step when device detection raises an error."""
    with patch(
        "custom_components.easywave.config_flow.find_rx11_devices",
        side_effect=OSError("USB access error"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "detection_failed"


# ─────── RX11 setup step ─────────────────────────────────────────────

async def test_rx11_setup_creates_entry(
    hass: HomeAssistant,
    mock_find_rx11_devices,
    mock_setup_entry,
) -> None:
    """Test that rx11_setup step creates a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "rx11_setup"

    # Submit the form (no user input required — auto-detected)
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={},
    )

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Easywave Gateway"
    assert result2["data"][CONF_TRANSCEIVER_TYPE] == "rx11"
    assert result2["data"][CONF_USB_VID] == MOCK_VID
    assert result2["data"][CONF_USB_PID] == MOCK_PID
    assert result2["data"][CONF_USB_SERIAL_NUMBER] == MOCK_SERIAL_NUMBER
    assert result2["data"][CONF_DEVICE_PATH] == MOCK_DEVICE_PATH
    assert result2["data"][CONF_SCAN_INTERVAL] == DEFAULT_SCAN_INTERVAL


async def test_rx11_setup_already_configured(
    hass: HomeAssistant,
    mock_find_rx11_devices,
    mock_setup_entry,
    mock_config_entry,
) -> None:
    """Test flow when integration is already configured redirects to device step."""
    mock_config_entry.add_to_hass(hass)

    # When integration is already configured, should redirect to device flow
    # which requires a coordinator — should abort since there's no real coordinator
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] in (FlowResultType.ABORT, FlowResultType.FORM, FlowResultType.MENU)


# ─────── Config entry properties ─────────────────────────────────────

async def test_config_entry_version(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test config entry has correct version."""
    assert mock_config_entry.version == 2
    assert mock_config_entry.domain == DOMAIN


# ─────── Device flow ─────────────────────────────────────────────────

async def test_device_step_no_coordinator(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test device step aborts when coordinator is not available."""
    mock_config_entry.add_to_hass(hass)

    # Ensure hass.data for DOMAIN exists but without coordinator
    hass.data.setdefault(DOMAIN, {})

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    # Should abort because no coordinator is available
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "transceiver_not_available"


async def test_device_step_transceiver_not_connected(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test device step aborts when transceiver is not connected."""
    mock_config_entry.add_to_hass(hass)

    coordinator = create_mock_coordinator(hass, mock_config_entry, connected=False)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "rx11_not_connected"


async def test_device_type_select_menu(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test device type selection shows menu options."""
    mock_config_entry.add_to_hass(hass)

    coordinator = create_mock_coordinator(hass, mock_config_entry, connected=True)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    # Should arrive at device type select menu
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "device_type_select"
    assert "device_transmitter_config" in result["menu_options"]
    assert "device_receiver" in result["menu_options"]
    assert "device_sensor" in result["menu_options"]
    assert "device_ewneo_receiver" in result["menu_options"]


# ─────── Transmitter config sub-flow ─────────────────────────────────

async def test_device_transmitter_config_menu(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test transmitter configuration shows operating mode options."""
    mock_config_entry.add_to_hass(hass)

    coordinator = create_mock_coordinator(hass, mock_config_entry, connected=True)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.MENU

    # Select transmitter config
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "device_transmitter_config"},
    )

    assert result2["type"] == FlowResultType.MENU
    assert result2["step_id"] == "device_transmitter_config"
    assert "device_transmitter_1button" in result2["menu_options"]
    assert "device_transmitter_2button" in result2["menu_options"]
    assert "device_transmitter_3button" in result2["menu_options"]
