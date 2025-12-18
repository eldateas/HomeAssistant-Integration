"""RX11 device implementations with structured entity classes.

This module contains organized device implementations:
- sensors/: Individual sensor classes (temperature, humidity, motion, etc.)
- receivers/: Individual receiver classes (switch, dimmer, motor, climate)
- transmitters/: Individual transmitter classes (single, dual, quad button)

The new structure provides:
- Better separation of concerns
- Simpler individual classes
- Easier maintenance and extension
- Clear device type hierarchy
"""

# Import the registry for device creation
from .registry import RX11DeviceFactory, rx11_device_factory, rx11_device_registry, RX11DeviceHandlerRegistry

# Import individual device classes
try:
    from .ewneo_sensors import (
        RX11TemperatureSensor,
        RX11HumiditySensor,
        RX11CombinedSensor,
    )

    from .ew_receivers import (
        RX11EWSwitchReceiver,
        RX11EWMotorReceiver,
        RX11EWClimateReceiver,
    )

    from .ew_transmitters import (
        RX11EWSingleButtonTransmitter,
        RX11EWDualButtonTransmitter,
        RX11EWTrippleButtonTransmitter,
        RX11EWQuadButtonTransmitter,
    )

except ImportError as e:
    import logging
    _LOGGER = logging.getLogger(__name__)
    _LOGGER.warning(f"Could not import device classes: {e}")
    # Provide fallback classes to prevent import errors
    class RX11TemperatureSensor: pass
    class RX11HumiditySensor: pass
    class RX11CombinedSensor: pass
    class RX11EWSwitchReceiver: pass
    class RX11EWMotorReceiver: pass
    class RX11EWClimateReceiver: pass
    class RX11EWSingleButtonTransmitter: pass
    class RX11EWDualButtonTransmitter: pass
    class RX11EWTrippleButtonTransmitter: pass
    class RX11EWQuadButtonTransmitter: pass

# Primary interface - factory and registry
__all__ = [
    # Main factory and registry (preferred interface)
    "RX11DeviceFactory",
    "rx11_device_factory",
    "rx11_device_registry",
    "RX11DeviceHandlerRegistry",  # Legacy compatibility
    
    # Individual sensor classes
    "RX11TemperatureSensor",
    "RX11HumiditySensor",
    "RX11CombinedSensor",
    
    # Individual receiver classes
    "RX11EWSwitchReceiver",
    "RX11EWMotorReceiver",
    "RX11EWClimateReceiver",
    
    # Individual transmitter classes
    "RX11EWSingleButtonTransmitter",
    "RX11EWDualButtonTransmitter",
    "RX11EWTrippleButtonTransmitter",
    "RX11EWQuadButtonTransmitter",
]
