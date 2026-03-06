"""Shared test fixtures for the EASYWAVE integration tests."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant import loader
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

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

# ─────────────────────────────────────────────────────────────────────
# Enable custom integrations in the HA test harness
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(hass: HomeAssistant):
    """Enable custom integrations defined in the test dir."""
    hass.data.pop(loader.DATA_CUSTOM_COMPONENTS, None)


# ─────────────────────────────────────────────────────────────────────
# Test data constants
# ─────────────────────────────────────────────────────────────────────

MOCK_VID = 0x155A
MOCK_PID = 0x1014
MOCK_SERIAL_NUMBER = "1234567890ABCDEF"
MOCK_DEVICE_PATH = "/dev/ttyUSB0"
MOCK_DEVICE_NAME = "RX11 USB Transceiver"
MOCK_MANUFACTURER = "ELDAT"
MOCK_PRODUCT = "RX11 USB Transceiver"

MOCK_CONFIG_DATA = {
    CONF_TRANSCEIVER_TYPE: "rx11",
    CONF_USB_VID: MOCK_VID,
    CONF_USB_PID: MOCK_PID,
    CONF_USB_SERIAL_NUMBER: MOCK_SERIAL_NUMBER,
    CONF_USB_MANUFACTURER: MOCK_MANUFACTURER,
    CONF_USB_PRODUCT: MOCK_PRODUCT,
    CONF_DEVICE_PATH: MOCK_DEVICE_PATH,
    CONF_DEVICE_NAME: MOCK_DEVICE_NAME,
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
}

MOCK_DEVICE_INFO_EW_RECEIVER = {
    "serial_number": "AABBCCDDEEFF0011",
    "type": "ew_receiver",
    "name": "Test Receiver",
    "receiver_kind": "switch",
    "neo_device": False,
    "entities": [
        {
            "type": "switch",
            "unique_id": "easywave_AABBCCDDEEFF0011_switch",
            "name": "Test Switch",
        }
    ],
}

MOCK_DEVICE_INFO_EWNEO_SWITCH = {
    "serial_number": "NEOSW00112233",
    "type": "ewneo_switch",
    "name": "Neo Switch",
    "neo_device": True,
    "device_type_code": 0x03,
    "ewneo_index": 0,
    "entities": [
        {
            "type": "switch",
            "unique_id": "easywave_NEOSW00112233_switch_ch0",
            "name": "Neo Switch CH1",
            "channel": 0,
        }
    ],
}

MOCK_DEVICE_INFO_EW_TRANSMITTER = {
    "serial_number": "TXMT00112233AABB",
    "type": "ew_transmitter",
    "name": "Test Transmitter",
    "neo_device": False,
    "entities": [
        {
            "type": "button",
            "unique_id": "easywave_TXMT00112233AABB_button_A",
            "name": "Button A",
            "button_id": "A",
        }
    ],
}


# ─────────────────────────────────────────────────────────────────────
# Mock USB port
# ─────────────────────────────────────────────────────────────────────

class MockUSBPort:
    """Mock a serial.tools.list_ports port info object."""

    def __init__(
        self,
        device: str = MOCK_DEVICE_PATH,
        vid: int = MOCK_VID,
        pid: int = MOCK_PID,
        serial_number: str = MOCK_SERIAL_NUMBER,
        manufacturer: str = MOCK_MANUFACTURER,
        product: str = MOCK_PRODUCT,
        location: str = "1-1",
        hwid: str = "USB VID:PID=155A:1014",
    ):
        self.device = device
        self.vid = vid
        self.pid = pid
        self.serial_number = serial_number
        self.manufacturer = manufacturer
        self.product = product
        self.location = location
        self.hwid = hwid


# ─────────────────────────────────────────────────────────────────────
# Mock Transceiver
# ─────────────────────────────────────────────────────────────────────

def create_mock_transceiver(connected: bool = True) -> MagicMock:
    """Create a mock RX11 transceiver."""
    from custom_components.easywave.transceivers import TransceiverType

    transceiver = MagicMock()
    transceiver.is_connected = connected
    transceiver.device_path = MOCK_DEVICE_PATH
    transceiver.transceiver_type = TransceiverType.RX11

    # Async methods
    transceiver.connect = AsyncMock(return_value=True)
    transceiver.disconnect = AsyncMock()
    transceiver.set_learning_mode = AsyncMock()
    transceiver.set_telegram_callback = MagicMock()
    transceiver.rx11_system_set_learning_mode = AsyncMock()
    transceiver.rx11_ewb_clear_filter = AsyncMock(return_value=True)
    transceiver.get_firmware_version = AsyncMock(return_value="1.0.0")
    transceiver.get_hardware_version = AsyncMock(return_value="1.0")
    transceiver.async_setup = AsyncMock(return_value=True)

    # USB identity
    transceiver.update_usb_identity = MagicMock()
    transceiver.set_usb_serial_number = MagicMock()

    # Properties
    transceiver.serial_number = MOCK_SERIAL_NUMBER

    return transceiver


# ─────────────────────────────────────────────────────────────────────
# Mock Coordinator
# ─────────────────────────────────────────────────────────────────────

def create_mock_coordinator(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    connected: bool = True,
    devices: dict | None = None,
) -> MagicMock:
    """Create a mock EasywaveCoordinator."""
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.config_entry = config_entry
    coordinator.transceiver = create_mock_transceiver(connected)
    coordinator.last_update_success = connected
    coordinator.devices = devices or {}
    coordinator._registered_devices = devices or {}
    coordinator._config_flow_learning = False
    coordinator._devices_with_fired_events = set()
    coordinator._known_devices = set()
    coordinator._platform_handlers = {}

    # Async methods
    coordinator.async_setup = AsyncMock(return_value=True)
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_shutdown = AsyncMock()
    coordinator.restore_registered_devices_only = AsyncMock()
    coordinator.fire_pending_device_events = AsyncMock()
    coordinator._restore_gateway_filters = AsyncMock()
    coordinator._cleanup_orphaned_ha_devices = AsyncMock()
    coordinator._purge_entity_history = AsyncMock()

    # Sync methods
    coordinator.get_all_devices = MagicMock(return_value=devices or {})
    coordinator.get_all_registered_devices = MagicMock(return_value=devices or {})
    coordinator.register_platform_handler = MagicMock()
    coordinator.is_entity_created = MagicMock(return_value=False)
    coordinator.mark_entity_created = MagicMock()
    coordinator.setup_event_dispatchers = MagicMock(return_value=lambda: None)
    coordinator.get_serial_by_ha_identifier = MagicMock(return_value=None)
    coordinator.async_remove_device = AsyncMock(return_value=True)
    coordinator.reset_all_ew_receiver_indices = MagicMock()
    coordinator.reset_all_ewb_indices = MagicMock()

    # Device manager
    coordinator.device_manager = MagicMock()
    coordinator.device_manager.get_all_devices = MagicMock(return_value={})

    # State manager
    coordinator.state_manager = MagicMock()

    return coordinator


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Create a mock config entry for EASYWAVE."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Easywave Gateway",
        data=MOCK_CONFIG_DATA.copy(),
        unique_id=f"rx11_{MOCK_SERIAL_NUMBER}",
        version=2,
    )


@pytest.fixture
def mock_usb_port() -> MockUSBPort:
    """Create a mock USB port."""
    return MockUSBPort()


@pytest.fixture
def mock_find_rx11_devices():
    """Patch find_rx11_devices to return a mock device."""
    mock_device = {
        "device": MOCK_DEVICE_PATH,
        "name": MOCK_PRODUCT,
        "manufacturer": MOCK_MANUFACTURER,
        "serial_number": MOCK_SERIAL_NUMBER,
        "vid": MOCK_VID,
        "pid": MOCK_PID,
        "location": "1-1",
        "hwid": "USB VID:PID=155A:1014",
    }
    with patch(
        "custom_components.easywave.config_flow.find_rx11_devices",
        return_value=[mock_device],
    ) as mock_find:
        yield mock_find


@pytest.fixture
def mock_no_rx11_devices():
    """Patch find_rx11_devices to return no devices."""
    with patch(
        "custom_components.easywave.config_flow.find_rx11_devices",
        return_value=[],
    ) as mock_find:
        yield mock_find


@pytest.fixture
def mock_transceiver():
    """Create a mock transceiver."""
    return create_mock_transceiver()


@pytest.fixture
def mock_setup_entry():
    """Prevent actual setup of the integration during config flow tests."""
    with patch(
        "custom_components.easywave.async_setup_entry",
        return_value=True,
    ) as mock_setup:
        yield mock_setup


@pytest.fixture
def mock_async_setup():
    """Prevent actual async_setup."""
    with patch(
        "custom_components.easywave.async_setup",
        return_value=True,
    ) as mock_setup:
        yield mock_setup


@pytest.fixture
def mock_coordinator(hass: HomeAssistant, mock_config_entry: MockConfigEntry):
    """Create a mock coordinator fixture."""
    return create_mock_coordinator(hass, mock_config_entry)
