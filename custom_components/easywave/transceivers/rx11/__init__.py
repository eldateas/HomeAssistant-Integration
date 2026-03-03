"""RX11 USB Transceiver implementation for EASYWAVE integration.

This package uses a Python implementation for the EASYWAVE RX11 transceiver.
"""

from .transceiver import RX11Transceiver
from .wrapper import RX11Wrapper
from .rx_module import RxModule, ErrorCode, InfoType, DeviceType
from .devices import (
    rx11_device_factory,
    rx11_device_registry,
    RX11DeviceHandlerRegistry,
)

# Convenience functions
from .transceiver import find_rx11_devices, validate_rx11_device

__all__ = [
    "RX11Transceiver",
    "RX11Wrapper",
    "RxModule",
    "ErrorCode",
    "InfoType",
    "DeviceType",
    "rx11_device_factory",
    "rx11_device_registry",
    "RX11DeviceHandlerRegistry",
    "find_rx11_devices",
    "validate_rx11_device",
]