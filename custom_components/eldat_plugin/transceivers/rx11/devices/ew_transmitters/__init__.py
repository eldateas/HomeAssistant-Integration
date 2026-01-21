"""EW transmitter implementations for RX11 transceiver.

This module provides button transmitter classes for EW (EasyWave) devices.
The unified RX11ButtonTransmitter class handles all button counts (1-4),
replacing the separate Single/Dual/Triple/Quad button classes.

Legacy class names are provided as aliases for backward compatibility.
"""
from .button_transmitter import (
    # Unified class
    RX11ButtonTransmitter,
    # Factory functions
    create_rx11_button_transmitter,
    create_rx11_single_button_transmitter,
    create_rx11_dual_button_transmitter,
    create_rx11_triple_button_transmitter,
    create_rx11_quad_button_transmitter,
)

# Backward compatibility aliases with EW prefix
RX11EWSingleButtonTransmitter = RX11ButtonTransmitter
RX11EWDualButtonTransmitter = RX11ButtonTransmitter
RX11EWTripleButtonTransmitter = RX11ButtonTransmitter  # Fixed typo: Tripple -> Triple
RX11EWTrippleButtonTransmitter = RX11ButtonTransmitter  # Keep old typo for compatibility
RX11EWQuadButtonTransmitter = RX11ButtonTransmitter

# Also keep original class names for compatibility
RX11SingleButtonTransmitter = RX11ButtonTransmitter
RX11DualButtonTransmitter = RX11ButtonTransmitter
RX11TripleButtonTransmitter = RX11ButtonTransmitter
RX11QuadButtonTransmitter = RX11ButtonTransmitter

__all__ = [
    # Unified class (preferred)
    "RX11ButtonTransmitter",
    
    # Factory functions
    "create_rx11_button_transmitter",
    "create_rx11_single_button_transmitter",
    "create_rx11_dual_button_transmitter",
    "create_rx11_triple_button_transmitter",
    "create_rx11_quad_button_transmitter",
    
    # Backward compatibility - EW prefixed
    "RX11EWSingleButtonTransmitter",
    "RX11EWDualButtonTransmitter",
    "RX11EWTripleButtonTransmitter",
    "RX11EWTrippleButtonTransmitter",  # Legacy typo
    "RX11EWQuadButtonTransmitter",
    
    # Backward compatibility - original names
    "RX11SingleButtonTransmitter",
    "RX11DualButtonTransmitter",
    "RX11TripleButtonTransmitter",
    "RX11QuadButtonTransmitter",
]