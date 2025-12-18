"""EW receiver implementations for RX11 transceiver.

WICHTIG: EW Receiver haben NUR diese Typen:
- Switch (Ein/Aus)
- Motor (Rolladen/Jalousie)
- Climate (Heizung/Kühlung)

Dimmer gibt es NUR bei EWneo-Geräten, nicht bei klassischen EW Receivern!
"""
from .switch import RX11SwitchReceiver as RX11EWSwitchReceiver
from .motor import RX11MotorReceiver as RX11EWMotorReceiver
from .climate import RX11ClimateReceiver as RX11EWClimateReceiver

__all__ = [
    "RX11EWSwitchReceiver",
    "RX11EWMotorReceiver",
    "RX11EWClimateReceiver",
]