"""Tests for RX11 transceiver, wrapper, rx_module, and device registry."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from homeassistant.core import HomeAssistant


# ═══════════════════════════════════════════════════════════════════
# RX11 Transceiver — construction & properties
# ═══════════════════════════════════════════════════════════════════


class TestRX11Transceiver:
    """Test RX11Transceiver class."""

    def _make(self, device_path="/dev/ttyUSB0"):
        from custom_components.easywave.transceivers.rx11.transceiver import RX11Transceiver
        with patch(
            "custom_components.easywave.transceivers.rx11.transceiver.RX11Wrapper"
        ) as mock_wrapper_cls:
            mock_wrapper_cls.return_value = MagicMock()
            t = RX11Transceiver(device_path)
        return t

    def test_construction(self):
        t = self._make()
        assert t is not None
        assert t.device_path == "/dev/ttyUSB0"

    def test_transceiver_type(self):
        from custom_components.easywave.transceivers.base import TransceiverType
        t = self._make()
        assert t.transceiver_type == TransceiverType.RX11

    def test_capabilities(self):
        t = self._make()
        caps = t.capabilities
        assert caps.supports_learning is True
        assert caps.supports_bidirectional is True
        assert caps.max_devices == 255

    def test_wrapper_property(self):
        t = self._make()
        assert t.wrapper is not None

    def test_set_usb_serial_number(self):
        t = self._make()
        t.set_usb_serial_number("ABC123")
        t._rx11_wrapper.set_usb_serial_number.assert_called_once_with("ABC123")

    def test_get_usb_serial_number(self):
        t = self._make()
        t._rx11_wrapper.get_usb_serial_number.return_value = "XYZ789"
        assert t.get_usb_serial_number() == "XYZ789"

    def test_get_device_info(self):
        t = self._make()
        t._rx11_wrapper.get_device_info.return_value = {"extra": "data"}
        info = t.get_device_info()
        assert isinstance(info, dict)
        assert info["device_path"] == "/dev/ttyUSB0"
        assert info["extra"] == "data"

    def test_update_usb_identity(self):
        t = self._make()
        t.update_usb_identity(serial_number="TEST", vid=0x155A, pid=0x1014)
        t._rx11_wrapper.update_usb_identity.assert_called_once()

    def test_get_connection_health_stats(self):
        t = self._make()
        t._rx11_wrapper.get_connection_stats.return_value = {"errors": 0}
        stats = t.get_connection_health_stats()
        assert isinstance(stats, dict)

    def test_construction_without_path(self):
        from custom_components.easywave.transceivers.rx11.transceiver import RX11Transceiver
        t = RX11Transceiver(None)
        assert t._rx11_wrapper is None
        assert t.wrapper is None

    def test_get_usb_serial_number_no_wrapper(self):
        from custom_components.easywave.transceivers.rx11.transceiver import RX11Transceiver
        t = RX11Transceiver(None)
        assert t.get_usb_serial_number() is None


# ═══════════════════════════════════════════════════════════════════
# find_rx11_devices and validate_rx11_device
# ═══════════════════════════════════════════════════════════════════


class TestRX11Discovery:
    """Test find_rx11_devices and validate_rx11_device."""

    def test_find_no_devices(self):
        from custom_components.easywave.transceivers.rx11.transceiver import find_rx11_devices
        with patch("serial.tools.list_ports.comports", return_value=[]):
            devices = find_rx11_devices()
        assert devices == []

    def test_find_with_supported_device(self):
        from custom_components.easywave.transceivers.rx11.transceiver import find_rx11_devices
        port = MagicMock()
        port.device = "/dev/ttyUSB0"
        port.vid = 0x155A
        port.pid = 0x1014
        port.serial_number = "ABC"
        port.location = "1-1"
        port.hwid = "USB VID:PID=155A:1014"
        port.manufacturer = "EASYWAVE"
        port.product = "RX11"
        with patch("serial.tools.list_ports.comports", return_value=[port]):
            devices = find_rx11_devices()
        assert len(devices) == 1
        assert devices[0]["vid"] == 0x155A

    def test_find_exception(self):
        from custom_components.easywave.transceivers.rx11.transceiver import find_rx11_devices
        with patch("serial.tools.list_ports.comports", side_effect=OSError("fail")):
            devices = find_rx11_devices()
        assert devices == []

    def test_validate_valid_device(self):
        from custom_components.easywave.transceivers.rx11.transceiver import validate_rx11_device
        port = MagicMock()
        port.device = "/dev/ttyUSB0"
        port.vid = 0x155A
        port.pid = 0x1014
        with patch("serial.tools.list_ports.comports", return_value=[port]):
            result = validate_rx11_device("/dev/ttyUSB0")
        assert result is True

    def test_validate_not_found(self):
        from custom_components.easywave.transceivers.rx11.transceiver import validate_rx11_device
        with patch("serial.tools.list_ports.comports", return_value=[]):
            result = validate_rx11_device("/dev/ttyUSB99")
        assert result is False

    def test_validate_exception(self):
        from custom_components.easywave.transceivers.rx11.transceiver import validate_rx11_device
        with patch("serial.tools.list_ports.comports", side_effect=OSError("fail")):
            result = validate_rx11_device("/dev/ttyUSB0")
        assert result is False


# ═══════════════════════════════════════════════════════════════════
# RX11Wrapper
# ═══════════════════════════════════════════════════════════════════


class TestRX11Wrapper:
    """Test RX11Wrapper basic construction and properties."""

    def _make(self, device_path="/dev/ttyUSB0"):
        from custom_components.easywave.transceivers.rx11.wrapper import RX11Wrapper
        with patch(
            "custom_components.easywave.transceivers.rx11.wrapper.RxModule"
        ) as mock_module:
            mock_module.return_value = MagicMock()
            wrapper = RX11Wrapper(device_path)
        return wrapper

    def test_construction(self):
        w = self._make()
        assert w is not None
        assert w.device_path == "/dev/ttyUSB0"
        assert w._connected is False

    def test_usb_serial_number(self):
        w = self._make()
        assert w._usb_serial_number is None
        w.set_usb_serial_number("TEST_SN")
        assert w.get_usb_serial_number() == "TEST_SN"

    def test_usb_identity(self):
        w = self._make()
        w.update_usb_identity(serial_number="SN", vid=0x155A, pid=0x1014)
        assert w._usb_serial_number == "SN"
        assert w._usb_vid == 0x155A
        assert w._usb_pid == 0x1014

    def test_device_info(self):
        w = self._make()
        info = w.get_device_info()
        assert isinstance(info, dict)

    def test_serial_cache_empty(self):
        w = self._make()
        assert len(w._serial_cache) == 0


# ═══════════════════════════════════════════════════════════════════
# RxModule — protocol constants and enums
# ═══════════════════════════════════════════════════════════════════


class TestRxModuleConstants:
    """Test RxModule protocol constants and enums."""

    def test_protocol_constants(self):
        from custom_components.easywave.transceivers.rx11.rx_module import (
            PREFIX, SOP, EOP, STUFFING_MIN, STUFFING_MAX,
        )
        assert PREFIX == 0x80
        assert SOP == 0x81
        assert EOP == 0x82
        assert STUFFING_MIN == 0x80
        assert STUFFING_MAX == 0x82

    def test_function_codes(self):
        from custom_components.easywave.transceivers.rx11.rx_module import FunctionCode
        assert FunctionCode.EW_RCV_BUTTON == 0x01
        assert FunctionCode.EW_SEND_CMD == 0x02
        assert FunctionCode.EWB_CHANGE_STATE == 0x09
        assert FunctionCode.MA_QUERY_HW_VER == 0xC0
        assert FunctionCode.MA_QUERY_FW_VER == 0xC1

    def test_error_codes(self):
        from custom_components.easywave.transceivers.rx11.rx_module import ErrorCode
        assert ErrorCode.SUCCESS == 0x00
        assert ErrorCode.ERR_RF_TIMEOUT == 0x07

    def test_device_type_enum(self):
        from custom_components.easywave.transceivers.rx11.rx_module import DeviceType
        assert DeviceType is not None

    def test_button_constants(self):
        from custom_components.easywave.transceivers.rx11.rx_module import (
            TM_BUTTON_A, TM_BUTTON_B, TM_BUTTON_C, TM_BUTTON_D,
        )
        assert TM_BUTTON_A is not None
        assert TM_BUTTON_B is not None
        assert TM_BUTTON_C is not None
        assert TM_BUTTON_D is not None


# ═══════════════════════════════════════════════════════════════════
# Device Registry
# ═══════════════════════════════════════════════════════════════════


class TestDeviceRegistry:
    """Test RX11DeviceFactory/registry."""

    def test_factory_construction(self):
        from custom_components.easywave.transceivers.rx11.devices.registry import RX11DeviceFactory
        factory = RX11DeviceFactory()
        assert factory is not None

    def test_factory_is_importable(self):
        from custom_components.easywave.transceivers.rx11.devices.registry import RX11DeviceFactory
        factory = RX11DeviceFactory()
        assert factory is not None

    def test_handler_registry(self):
        from custom_components.easywave.transceivers.rx11.devices.registry import RX11DeviceHandlerRegistry
        assert RX11DeviceHandlerRegistry is not None


# ═══════════════════════════════════════════════════════════════════
# EW Device Classes
# ═══════════════════════════════════════════════════════════════════


class TestEWDeviceClasses:
    """Test EW receiver and transmitter device classes."""

    def test_ew_switch_receiver(self):
        from custom_components.easywave.transceivers.rx11.devices.ew_receivers.switch import RX11SwitchReceiver
        assert RX11SwitchReceiver is not None

    def test_ew_motor_receiver(self):
        from custom_components.easywave.transceivers.rx11.devices.ew_receivers.motor import RX11MotorReceiver
        assert RX11MotorReceiver is not None

    def test_ew_climate_receiver(self):
        from custom_components.easywave.transceivers.rx11.devices.ew_receivers.climate import RX11ClimateReceiver
        assert RX11ClimateReceiver is not None

    def test_button_transmitter(self):
        from custom_components.easywave.transceivers.rx11.devices.ew_transmitters.button_transmitter import (
            RX11ButtonTransmitter,
        )
        assert RX11ButtonTransmitter is not None

    def test_ewneo_sensor(self):
        from custom_components.easywave.transceivers.rx11.devices.ewneo_sensors.ewneo_sensor import EWneoSensor
        assert EWneoSensor is not None


class TestEWneoTransceivers:
    """Test EWneo transceiver classes."""

    def test_ewneo_base(self):
        from custom_components.easywave.transceivers.rx11.devices.ewneo_transceivers.base import EWneoBaseDevice
        assert EWneoBaseDevice is not None

    def test_ewneo_dimmer(self):
        from custom_components.easywave.transceivers.rx11.devices.ewneo_transceivers.dimmer import RX11EWneoDimmer
        assert RX11EWneoDimmer is not None

    def test_ewneo_unified_switch(self):
        from custom_components.easywave.transceivers.rx11.devices.ewneo_transceivers.unified_switch import (
            RX11EWneoSwitch,
        )
        assert RX11EWneoSwitch is not None

    def test_ewneo_unified_motor(self):
        from custom_components.easywave.transceivers.rx11.devices.ewneo_transceivers.unified_motor import (
            RX11EWneoMotor,
        )
        assert RX11EWneoMotor is not None

    def test_ewneo_transceiver(self):
        from custom_components.easywave.transceivers.rx11.devices.ewneo_transceivers.transceiver import (
            RX11EWneoTransceiver,
        )
        assert RX11EWneoTransceiver is not None
