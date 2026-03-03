"""Enumerations for EASYWAVE transceivers and devices."""
from __future__ import annotations

from enum import Enum


class TransceiverType(Enum):
    """Supported transceiver types following structured naming.
    
    Each transceiver type supports different device types:
    - RX11: EW receivers, transmitters, EWB sensors
    - RX21: Future advanced transceiver
    - Gateway: Network-based transceiver
    """
    RX11 = "rx11"
    # Future transceivers can be added here
    # RX21 = "rx21"
    # Gateway = "gateway"


class DeviceType(Enum):
    """Grundlegende Device-Typen basierend auf EASYWAVE Spezifikation."""
    # EasyWave (EW) Geräte - ohne Sensoren (nur EWneo hat Sensoren)
    EW_RECEIVER = "ew_receiver"
    EW_TRANSMITTER = "ew_transmitter"
    EWNEO_SENSOR = "ewneo_sensor"
    EWNEO_RECEIVER = "ewneo_receiver"
    
    # EasyWave Neo (EWB) Geräte
    EWNEO_TRANSCEIVER = "ewneo_transceiver"
    EWNEO_BIDI_TRANSMITTER = "ewneo_bidi_transmitter"
    EWNEO_SWITCH = "ewneo_switch"
    EWNEO_DUAL_SWITCH = "ewneo_dual_switch"
    EWNEO_QUAD_SWITCH = "ewneo_quad_switch"
    EWNEO_DIMMER = "ewneo_dimmer"
    EWNEO_MOTOR = "ewneo_motor"
    EWNEO_DUAL_MOTOR = "ewneo_dual_motor"
    EWNEO_QUAD_MOTOR = "ewneo_quad_motor"
    
    UNKNOWN = "unknown"


class DeviceSubtype(Enum):
    """Device-Subtypen für verschiedene Funktionalitäten."""
    # Receiver Subtypen
    MOTOR = "motor"
    SWITCH = "switch"
    DIMMER = "dimmer"
    HEATING_COOLING = "heating_cooling"
    TRANSCEIVER = "transceiver"
    
    # Multi-channel variants
    DUAL_SWITCH = "dual_switch"
    QUAD_SWITCH = "quad_switch"
    DUAL_MOTOR = "dual_motor"
    QUAD_MOTOR = "quad_motor"
    
    # Sensor Subtypen - nur für EWneo
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    WIND_SPEED = "wind_speed"
    RAIN = "rain"
    
    # Transmitter Subtypen - nur typisierte 1-Button Varianten (A/B/C/D, kein generischer SINGLE_BUTTON)
    SINGLE_BUTTON_A = "single_button_a"  # 1-Button Type A
    SINGLE_BUTTON_B = "single_button_b"  # 1-Button Type B
    SINGLE_BUTTON_C = "single_button_c"  # 1-Button Type C
    SINGLE_BUTTON_D = "single_button_d"  # 1-Button Type D
    DUAL_BUTTON = "dual_button"
    TRIPLE_BUTTON = "triple_button"
    QUAD_BUTTON = "quad_button"
    
    UNKNOWN = "unknown"


class OperatingMode(Enum):
    """Betriebsarten für Geräte."""
    # Transmitter Modi
    ONE_BUTTON = "1_button"
    TWO_BUTTON = "2_button"
    THREE_BUTTON = "3_button"
    FOUR_BUTTON = "4_button"
    PUSH_BUTTON = "push_button"  # General push button mode
    
    # Receiver Modi
    SINGLE_CHANNEL = "single_channel"
    DUAL_CHANNEL = "dual_channel"
    QUAD_CHANNEL = "quad_channel"
    BIDIRECTIONAL = "bidirectional"  # For EWneo transceivers
    
    # Sensor Modi
    CONTINUOUS = "continuous"
    PERIODIC = "periodic"
    EVENT_TRIGGERED = "event_triggered"
    
    UNKNOWN = "unknown"
