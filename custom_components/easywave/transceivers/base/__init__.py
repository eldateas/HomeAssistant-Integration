"""Base classes for EASYWAVE transceivers and devices.

Structured Naming Convention:
- transceiver_<type>_<device_type>_<operation>: Structured operation naming
  Example: rx11_ew_receiver_button_start_continuous
  
Hierarchy: transceiver -> type -> device_type -> button_type
- transceiver: RX11, RX21, Gateway
- type: EW (EasyWave), EWneo, EWB (EasyWave Bidirectional) 
- device_type: receiver, transmitter, sensor
- button_type: A(0), B(1), C(2), D(3)

This provides a clear, hierarchical naming structure for all operations
across different EASYWAVE device types and transceivers.
"""

# Export enums
from .enums import (
    DeviceSubtype,
    DeviceType,
    OperatingMode,
    TransceiverType,
)

# Export data classes
from .device_info import (
    DeviceInfo,
    TransceiverCapabilities,
)

# Export base classes
from .device import BaseDevice
from .receiver import BaseReceiver
from .sensor import BaseSensor
from .transceiver import BaseDeviceHandler, BaseTransceiver
from .transmitter import BaseTransmitter

# LEGACY: Also re-export behaviors from here for backward compatibility
# New code should import from ..behaviors instead
try:
    from ..behaviors import (
        ButtonBehaviorMixin,
        CoverBehaviorMixin,
        EntitySpecsMixin,
        LightBehaviorMixin,
        SensorBehaviorMixin,
        SwitchBehaviorMixin,
    )
    _BEHAVIORS_AVAILABLE = True
except ImportError:
    # Behaviors not yet imported, will be available later
    _BEHAVIORS_AVAILABLE = False

__all__ = [
    # Enums
    "TransceiverType",
    "DeviceType",
    "DeviceSubtype",
    "OperatingMode",
    # Data classes
    "DeviceInfo",
    "TransceiverCapabilities",
    # Base classes
    "BaseTransceiver",
    "BaseDeviceHandler",
    "BaseDevice",
    "BaseReceiver",
    "BaseTransmitter",
    "BaseSensor",
]

# Add behaviors to __all__ if available
if _BEHAVIORS_AVAILABLE:
    __all__.extend([
        "CoverBehaviorMixin",
        "SwitchBehaviorMixin",
        "LightBehaviorMixin",
        "SensorBehaviorMixin",
        "ButtonBehaviorMixin",
        "EntitySpecsMixin",
    ])

