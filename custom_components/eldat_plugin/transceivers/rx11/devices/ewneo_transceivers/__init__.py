"""EWneo transceiver device classes."""

from .base import EWneoBaseDevice
from .transceiver import RX11EWneoTransceiver, create_rx11_ewneo_transceiver
from .switch import RX11EWneoSwitch, create_rx11_ewneo_switch
from .dimmer import RX11EWneoDimmer, create_rx11_ewneo_dimmer
from .motor import RX11EWneoMotor, create_rx11_ewneo_motor
from .multi_channel_switch import (
    RX11EWneoMultiChannelSwitch,
    create_rx11_ewneo_dual_switch,
    create_rx11_ewneo_quad_switch,
)
from .multi_channel_motor import (
    RX11EWneoMultiChannelMotor,
    create_rx11_ewneo_dual_motor,
    create_rx11_ewneo_quad_motor,
)

# Backward compatibility aliases
RX11EWneoDualSwitch = RX11EWneoMultiChannelSwitch
RX11EWneoQuadSwitch = RX11EWneoMultiChannelSwitch
RX11EWneoDualMotor = RX11EWneoMultiChannelMotor
RX11EWneoQuadMotor = RX11EWneoMultiChannelMotor

__all__ = [
    # Base class
    "EWneoBaseDevice",
    
    # Single-channel devices
    "RX11EWneoTransceiver",
    "RX11EWneoSwitch", 
    "RX11EWneoDimmer",
    "RX11EWneoMotor",
    
    # Multi-channel devices
    "RX11EWneoMultiChannelSwitch",
    "RX11EWneoMultiChannelMotor",
    
    # Backward compatibility aliases
    "RX11EWneoDualSwitch",
    "RX11EWneoQuadSwitch",
    "RX11EWneoDualMotor", 
    "RX11EWneoQuadMotor",
    
    # Factory functions
    "create_rx11_ewneo_transceiver",
    "create_rx11_ewneo_switch",
    "create_rx11_ewneo_dual_switch",
    "create_rx11_ewneo_quad_switch",
    "create_rx11_ewneo_dimmer",
    "create_rx11_ewneo_motor",
    "create_rx11_ewneo_dual_motor",
    "create_rx11_ewneo_quad_motor",
]