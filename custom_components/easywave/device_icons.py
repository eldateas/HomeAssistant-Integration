"""Device-specific icons and tooltips for ELDAT integration.

DEPRECATED: Diese Datei wird schrittweise durch Device-Klassen ersetzt.
Neue Device-Klassen in transceivers/rx11/devices/ sollten Icons und
Tooltips direkt definieren.
"""
from __future__ import annotations

from typing import Dict, Any, Optional

# Minimal icon lookup for legacy code
DEVICE_ICONS = {
    "ew_receiver": {
        "switch": "mdi:light-switch",
        "motor": "mdi:window-shutter",
        "heating_cooling": "mdi:thermostat",
    },
    "ew_transmitter": "mdi:gesture-tap-button",
    "ewneo_sensor": {
        "temperature": "mdi:thermometer",
        "humidity": "mdi:water-percent",
        "wind": "mdi:weather-windy",
        "rain": "mdi:weather-rainy",
    },
    "ewneo_receiver": {
        "switch": "mdi:light-switch",
        "dimmer": "mdi:lightbulb-outline",
        "motor": "mdi:window-shutter",
    }
}


def get_entity_config_for_device(
    device_type: str,
    receiver_kind: Optional[str] = None,
    operating_mode: int = 1,
    sensor_type: Optional[str] = None,
    entity_type: str = "switch"
) -> Dict[str, Any]:
    """Get entity configuration (icon, device_class, tooltips) for a specific device.
    
    LEGACY FUNCTION: Verwendet für Rückwärtskompatibilität.
    Neue Geräte sollten Icons direkt in Device-Klassen definieren.
    
    Args:
        device_type: The device type (e.g., "ew_receiver", "ewneo_dimmer")
        receiver_kind: For ew_receiver, the receiver kind (switch/motor/heating_cooling)
        operating_mode: The operating mode (1=toggle, 2=on_off, 3=three_button)
        sensor_type: For sensors, the sensor type (temperature/humidity)
        entity_type: The entity platform type (switch/light/cover/sensor)
        
    Returns:
        Dictionary with icon, device_class, and action descriptions
    """
    config = {
        "icon": "mdi:help-circle",
        "device_class": None,
        "actions": {}
    }
    
    # Get icon from lookup
    if device_type in DEVICE_ICONS:
        icons = DEVICE_ICONS[device_type]
        if isinstance(icons, dict):
            if receiver_kind and receiver_kind in icons:
                config["icon"] = icons[receiver_kind]
            elif sensor_type and sensor_type in icons:
                config["icon"] = icons[sensor_type]
        else:
            config["icon"] = icons
    
    # Set device_class based on receiver_kind or entity_type
    if receiver_kind == "motor" or entity_type == "cover":
        config["device_class"] = "blind"
    elif sensor_type == "temperature":
        config["device_class"] = "temperature"
    elif sensor_type == "humidity":
        config["device_class"] = "humidity"
    
    # Add simple action descriptions
    if receiver_kind == "motor":
        config["actions"] = {
            "open_action": "Hochfahren",
            "close_action": "Herunterfahren",
            "stop_action": "Stoppen"
        }
    elif receiver_kind == "switch":
        config["actions"] = {
            "turn_on_action": "Einschalten",
            "turn_off_action": "Ausschalten"
        }
    elif receiver_kind == "heating_cooling":
        config["actions"] = {
            "turn_on_action": "Einschalten (mit 4h-Wiederholung)",
            "turn_off_action": "Ausschalten"
        }
    
    return config


# Obsolete Funktionen entfernt - Icons werden jetzt direkt in Device-Klassen definiert
# Nur get_entity_config_for_device() wird noch für Legacy-Support benötigt
