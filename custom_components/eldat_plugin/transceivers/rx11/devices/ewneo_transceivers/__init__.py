"""EWneo transceiver device classes.

This module provides device classes for EWneo (EasyWave Neo) bidirectional devices.
Unified classes handle all channel counts (1, 2, 4), replacing separate
Single/Dual/Quad classes.

Structure:
- EWneoBaseDevice: Base class with common EWB state parsing
- RX11EWneoSwitch: Unified switch (1/2/4 channels)
- RX11EWneoDimmer: Single-channel dimmer
- RX11EWneoMotor: Unified motor (1/2/4 channels)
- RX11EWneoTransceiver: Generic transceiver

Legacy class names are provided as aliases for backward compatibility.
"""

from .base import EWneoBaseDevice
from .transceiver import RX11EWneoTransceiver, create_rx11_ewneo_transceiver
from .dimmer import RX11EWneoDimmer, create_rx11_ewneo_dimmer

# Import unified switch class (replaces separate single/multi-channel classes)
from .unified_switch import (
    RX11EWneoSwitch,
    create_rx11_ewneo_switch,
    create_rx11_ewneo_dual_switch,
    create_rx11_ewneo_quad_switch,
    # Backward compatibility aliases
    RX11EWneoMultiChannelSwitch,
    RX11EWneoDualSwitch,
    RX11EWneoQuadSwitch,
)

# Import unified motor class (replaces separate single/multi-channel classes)
from .unified_motor import (
    RX11EWneoMotor,
    create_rx11_ewneo_motor,
    create_rx11_ewneo_dual_motor,
    create_rx11_ewneo_quad_motor,
    # Backward compatibility aliases
    RX11EWneoMultiChannelMotor,
    RX11EWneoDualMotor,
    RX11EWneoQuadMotor,
)

__all__ = [
    # Base class
    "EWneoBaseDevice",
    
    # Unified switch class (handles 1/2/4 channels)
    "RX11EWneoSwitch",
    
    # Dimmer (single-channel only)
    "RX11EWneoDimmer",
    
    # Unified motor class (handles 1/2/4 channels)
    "RX11EWneoMotor",
    
    # Generic transceiver
    "RX11EWneoTransceiver",
    
    # Backward compatibility aliases - Switch
    "RX11EWneoMultiChannelSwitch",
    "RX11EWneoDualSwitch",
    "RX11EWneoQuadSwitch",
    
    # Backward compatibility aliases - Motor
    "RX11EWneoMultiChannelMotor",
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