"""Entity specification generator for ELDAT devices.

DEPRECATED: Diese Datei wird schrittweise durch get_entity_specs() 
in den Device-Klassen (transceivers/rx11/devices/) ersetzt.

Neue Device-Klassen sollten EntitySpecsMixin verwenden und ihre 
Entity-Spezifikationen selbst generieren.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional

_LOGGER = logging.getLogger(__name__)


def create_entity_specs_for_device(serial_number: str, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Create entity specifications for a device based on its type and configuration.
    
    LEGACY FUNCTION: Versucht zuerst, die Device-Klasse zu verwenden.
    Falls nicht verfügbar, fällt auf alte Logik zurück.
    
    Returns a dictionary with entity platform names as keys and lists of entity specs as values.
    """
    # Try to get specs from device class if available
    device_class_specs = _try_get_specs_from_device_class(serial_number, device_info)
    if device_class_specs:
        return device_class_specs
    
    # Legacy fallback
    device_type = device_info.get("type", "unknown")
    
    if device_type == "ew_receiver":
        return _create_ew_receiver_entities_legacy(serial_number, device_info)
    elif device_type == "ew_transmitter":
        return _create_ew_transmitter_entities_legacy(serial_number, device_info)
    elif device_type == "ew_sensor":
        return _create_ew_sensor_entities_legacy(serial_number, device_info)
    elif device_type == "ewneo_receiver" or device_info.get("neo_device"):
        return _create_ewneo_entities_legacy(serial_number, device_info)
    elif device_type in ("sec_receiver", "sec_transmitter"):
        return _create_sec_entities_legacy(serial_number, device_info)
    
    return _empty_entity_dict()


def _try_get_specs_from_device_class(serial_number: str, device_info: Dict[str, Any]) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """Try to get entity specs from the device class using transceivers registry."""
    try:
        from .transceivers.rx11.devices.registry import get_device_class_for_info
        
        device_class = get_device_class_for_info(device_info)
        if device_class and hasattr(device_class, 'get_entity_specs'):
            # Instantiate device temporarily to get specs
            device_instance = device_class(serial_number, name=device_info.get("name"))
            specs = device_instance.get_entity_specs()
            _LOGGER.debug("✅ Using entity specs from device class for %s", serial_number[-6:])
            return specs
    except Exception as e:
        _LOGGER.debug("Could not get specs from device class: %s", e)
    
    return None


def _empty_entity_dict() -> Dict[str, List[Dict[str, Any]]]:
    """Return empty entity dictionary."""
    return {
        "switch": [],
        "light": [],
        "cover": [],
        "sensor": [],
        "binary_sensor": [],
        "button": []
    }


# ============================================================
# Legacy entity creation functions
# Diese werden schrittweise durch Device-Klassen ersetzt
# ============================================================

def _create_ew_receiver_entities_legacy(serial_number: str, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Create entities for EW-Receiver devices (LEGACY)."""
    from .device_icons import get_entity_config_for_device
    
    entities = _empty_entity_dict()
    receiver_kind = device_info.get("receiver_kind", "switch")
    operating_mode = device_info.get("operating_mode", 1)
    
    # Get device configuration
    device_config = get_entity_config_for_device(
        device_type="ew_receiver",
        receiver_kind=receiver_kind,
        operating_mode=operating_mode
    )
    
    # Create appropriate entities based on receiver_kind
    if receiver_kind == "motor":
        # Motor devices get cover entities
        entities["cover"].append({
            "type": "cover",
            "name": device_info.get("name", f"Motor {serial_number[-6:]}"),
            "unique_id": f"{serial_number}_cover",
            "device_class": "blind",
            "icon": "mdi:blinds"
        })
    elif receiver_kind == "switch":
        # Switch devices get switch entities
        entities["switch"].append({
            "type": "switch",
            "name": device_info.get("name", f"Switch {serial_number[-6:]}"),
            "unique_id": f"{serial_number}_switch",
            "device_class": "switch",
            "icon": "mdi:light-switch"
        })
    elif receiver_kind == "heating_cooling":
        # Heating/cooling devices get switch entities with 4h repetition
        entities["switch"].append({
            "type": "switch",
            "name": device_info.get("name", f"Heizung {serial_number[-6:]}"),
            "unique_id": f"{serial_number}_heating_cooling_switch",
            "device_class": "switch",
            "icon": "mdi:thermostat",
            "operating_mode": operating_mode,
            "receiver_kind": "heating_cooling",
            "button_config": {
                "toggle": 0 if operating_mode == 1 else 0,
                "on": 0 if operating_mode == 2 else 0,
                "off": 1 if operating_mode == 2 else 0
            },
            "supports_4h_repetition": True,
            "entity_category": None,
            "persistent_state": True
        })
    
    return entities


def _create_ew_transmitter_entities_legacy(serial_number: str, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Create entities for EW-Transmitter devices (LEGACY)."""
    entities = _empty_entity_dict()
    button_count = device_info.get("button_count", 2)
    
    # Create binary sensor for each button
    button_labels = ["A", "B", "C", "D"]
    for i in range(button_count):
        entities["binary_sensor"].append({
            "type": "binary_sensor",
            "name": f"{device_info.get('name', 'Transmitter')} Button {button_labels[i]}",
            "unique_id": f"{serial_number}_button_{i}",
            "device_class": "motion",
            "icon": "mdi:gesture-tap-button"
        })
    
    return entities


def _create_ew_sensor_entities_legacy(serial_number: str, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Create entities for EW-Sensor devices (LEGACY)."""
    entities = _empty_entity_dict()
    
    # Temperature sensor
    if device_info.get("has_temperature", True):
        entities["sensor"].append({
            "type": "sensor",
            "name": f"{device_info.get('name', 'Sensor')} Temperature",
            "unique_id": f"{serial_number}_temperature",
            "device_class": "temperature",
            "unit_of_measurement": "°C",
            "icon": "mdi:thermometer"
        })
    
    # Humidity sensor
    if device_info.get("has_humidity", False):
        entities["sensor"].append({
            "type": "sensor",
            "name": f"{device_info.get('name', 'Sensor')} Humidity",
            "unique_id": f"{serial_number}_humidity",
            "device_class": "humidity",
            "unit_of_measurement": "%",
            "icon": "mdi:water-percent"
        })
    
    # Battery sensor
    entities["sensor"].append({
        "type": "sensor",
        "name": f"{device_info.get('name', 'Sensor')} Battery",
        "unique_id": f"{serial_number}_battery",
        "device_class": "battery",
        "unit_of_measurement": "%",
        "icon": "mdi:battery"
    })
    
    return entities


def _create_ewneo_entities_legacy(serial_number: str, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Create entities for EWneo devices (LEGACY)."""
    entities = _empty_entity_dict()
    
    device_type_code = device_info.get("device_type_code", 0)
    
    # EWneo Switch (single/dual/quad)
    if device_type_code in [0x01, 0x02, 0x03]:
        channel_count = {0x01: 1, 0x02: 2, 0x03: 4}.get(device_type_code, 1)
        for ch in range(channel_count):
            entities["switch"].append({
                "type": "switch",
                "name": f"{device_info.get('name', 'EWneo Switch')} CH{ch+1}",
                "unique_id": f"{serial_number}_switch_ch{ch}",
                "channel": ch,
                "device_class": "switch",
                "icon": "mdi:light-switch"
            })
    
    # EWneo Dimmer
    elif device_type_code == 0x04:
        entities["light"].append({
            "type": "light",
            "name": device_info.get('name', 'EWneo Dimmer'),
            "unique_id": f"{serial_number}_light",
            "icon": "mdi:lightbulb-outline"
        })
    
    # EWneo Motor (single/dual/quad)
    elif device_type_code in [0x07, 0x08, 0x09]:
        channel_count = {0x07: 1, 0x08: 2, 0x09: 4}.get(device_type_code, 1)
        for ch in range(channel_count):
            entities["cover"].append({
                "type": "cover",
                "name": f"{device_info.get('name', 'EWneo Motor')} CH{ch+1}",
                "unique_id": f"{serial_number}_cover_ch{ch}",
                "channel": ch,
                "device_class": "blind",
                "icon": "mdi:blinds"
            })
    
    return entities


def _create_sec_entities_legacy(serial_number: str, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Create entities for SecWave devices (LEGACY)."""
    # SecWave support is minimal for now
    return _empty_entity_dict()
