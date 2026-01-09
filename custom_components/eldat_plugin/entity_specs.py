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
    
    # Check if this is an EWneo device based on device_type_code (EWB devices)
    # This is the primary indicator for EWneo/EWB devices
    if "device_type_code" in device_info:
        device_type_code = device_info.get("device_type_code", 0)
        if device_type_code in [0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B]:
            # EWneo/EWB device types (switches, dimmers, motors, transceivers)
            _LOGGER.debug("Creating EWneo entities for device %s with type_code 0x%02X", 
                         serial_number[-6:], device_type_code)
            return _create_ewneo_entities_legacy(serial_number, device_info)
    
    # Legacy fallback based on type string
    device_type = device_info.get("type", "unknown")
    
    if device_type == "ew_receiver":
        return _create_ew_receiver_entities_legacy(serial_number, device_info)
    elif device_type == "ew_transmitter":
        return _create_ew_transmitter_entities_legacy(serial_number, device_info)
    elif device_type in ["ew_sensor", "ewneo_sensor"]:
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
        # Motor devices get button entities based on operating mode
        # operating_mode: 1 = Eintast (Toggle), 2 = Zweitast (Up/Down), 3 = Dreitast (Up/Stop/Down)
        base_name = device_info.get("name", f"Motor {serial_number[-6:]}")
        
        if operating_mode == 1:
            # Eintastbedienung: Toggle button
            entities["button"].append({
                "type": "button",
                "name": f"{base_name} A - Toggle",
                "unique_id": f"{serial_number}_toggle",
                "button_code": 0,  # TM_BUTTON_A
                "action": "toggle",
                "icon": "mdi:gesture-tap-button",
                "operating_mode": operating_mode,
                "receiver_kind": "motor"
            })
        elif operating_mode == 2:
            # Zweitastbedienung: Up + Down buttons
            entities["button"].append({
                "type": "button",
                "name": f"{base_name} A - Auf",
                "unique_id": f"{serial_number}_up",
                "button_code": 0,  # TM_BUTTON_A
                "action": "up",
                "icon": "mdi:arrow-up-circle",
                "operating_mode": operating_mode,
                "receiver_kind": "motor"
            })
            entities["button"].append({
                "type": "button",
                "name": f"{base_name} B - Ab",
                "unique_id": f"{serial_number}_down",
                "button_code": 1,  # TM_BUTTON_B
                "action": "down",
                "icon": "mdi:arrow-down-circle",
                "operating_mode": operating_mode,
                "receiver_kind": "motor"
            })
        elif operating_mode == 3:
            # Dreitastbedienung: A - Auf, B - Ab, C - Stopp
            entities["button"].append({
                "type": "button",
                "name": f"{base_name} A - Auf",
                "unique_id": f"{serial_number}_up",
                "button_code": 0,  # TM_BUTTON_A
                "action": "up",
                "icon": "mdi:arrow-up-circle",
                "operating_mode": operating_mode,
                "receiver_kind": "motor"
            })
            entities["button"].append({
                "type": "button",
                "name": f"{base_name} B - Ab",
                "unique_id": f"{serial_number}_down",
                "button_code": 1,  # TM_BUTTON_B
                "action": "down",
                "icon": "mdi:arrow-down-circle",
                "operating_mode": operating_mode,
                "receiver_kind": "motor"
            })
            entities["button"].append({
                "type": "button",
                "name": f"{base_name} C - Stopp",
                "unique_id": f"{serial_number}_stop",
                "button_code": 2,  # TM_BUTTON_C
                "action": "stop",
                "icon": "mdi:stop-circle-outline",
                "operating_mode": operating_mode,
                "receiver_kind": "motor"
            })
    elif receiver_kind == "switch":
        # Switch devices get button entities for stateless operation
        # operating_mode: 1 = Eintast (Toggle), 2 = Zweitast (Ein/Aus)
        base_name = device_info.get("name", f"Switch {serial_number[-6:]}")
        
        if operating_mode == 1:
            # Eintastbedienung: Toggle button
            entities["button"].append({
                "type": "button",
                "name": f"{base_name} A - Toggle",
                "unique_id": f"{serial_number}_toggle",
                "button_code": 0,  # TM_BUTTON_A
                "action": "toggle",
                "icon": "mdi:gesture-tap-button",
                "operating_mode": operating_mode,
                "receiver_kind": "switch"
            })
        else:
            # Zweitastbedienung (default): Ein + Aus buttons
            entities["button"].append({
                "type": "button",
                "name": f"{base_name} A - Ein",
                "unique_id": f"{serial_number}_on",
                "button_code": 0,  # TM_BUTTON_A
                "action": "on",
                "icon": "mdi:lightbulb-on",
                "operating_mode": operating_mode,
                "receiver_kind": "switch"
            })
            entities["button"].append({
                "type": "button",
                "name": f"{base_name} B - Aus",
                "unique_id": f"{serial_number}_off",
                "button_code": 1,  # TM_BUTTON_B
                "action": "off",
                "icon": "mdi:lightbulb-off",
                "operating_mode": operating_mode,
                "receiver_kind": "switch"
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
    """Create entities for EW-Sensor devices (LEGACY).
    
    For EWneo-Sensoren, tries to use the new universal sensor class specs.
    """
    entities = _empty_entity_dict()
    
    # Check if this is an EWneo-Sensoren (new format)
    device_type = device_info.get("type", "unknown")
    if device_type == "ewneo_sensor":
        # Try to create entity specs using the new EWneoSensor class
        try:
            from .transceivers.rx11.devices.ewneo_sensors import EWneoSensor, NEO_TYPE_SENSOR_MAP
            
            # Determine sensor types from device_info
            sensor_types = device_info.get('sensor_types', [])
            
            # If no sensor_types, try to convert from neo_types
            if not sensor_types:
                neo_types = device_info.get('neo_types', [])
                if neo_types:
                    sensor_types = []
                    for neo_type in neo_types:
                        sensor_type = NEO_TYPE_SENSOR_MAP.get(neo_type)
                        if sensor_type and sensor_type not in sensor_types:
                            sensor_types.append(sensor_type)
            
            # If still no sensor_types, check for single neo_type
            if not sensor_types:
                neo_type = device_info.get('neo_type')
                if neo_type:
                    sensor_type = NEO_TYPE_SENSOR_MAP.get(neo_type)
                    if sensor_type:
                        sensor_types = [sensor_type]
            
            # Default to temperature if nothing found
            if not sensor_types:
                sensor_types = ['temperature']
                _LOGGER.debug("No sensor types found for %s, defaulting to temperature", serial_number[-6:])
            
            # Create temporary device instance to get entity specs
            device_instance = EWneoSensor(
                serial_number,
                name=device_info.get('name', f'EWneo-Sensoren {serial_number[-6:]}'),
                sensor_types=sensor_types
            )
            
            # Get specs from device
            specs = device_instance.get_entity_specs()
            _LOGGER.info("✅ Created entity specs for EWneo-Sensoren %s with types: %s", 
                        serial_number[-6:], sensor_types)
            return specs
            
        except Exception as e:
            _LOGGER.warning("Could not create EWneo-Sensoren specs from class: %s, using legacy fallback", e)
    
    # Legacy fallback for old EW sensors
    base_name = device_info.get('name', 'Sensor')
    
    # Temperature sensor
    if device_info.get("has_temperature", True):
        entities["sensor"].append({
            "type": "sensor",
            "sensor_type": "temperature",
            "name": f"{base_name} Temperature",
            "unique_id": f"{serial_number}_temperature",
            "device_class": "temperature",
            "unit_of_measurement": "°C",
            "icon": "mdi:thermometer"
        })
    
    # Humidity sensor
    if device_info.get("has_humidity", False):
        entities["sensor"].append({
            "type": "sensor",
            "sensor_type": "humidity",
            "name": f"{base_name} Humidity",
            "unique_id": f"{serial_number}_humidity",
            "device_class": "humidity",
            "unit_of_measurement": "%",
            "icon": "mdi:water-percent"
        })
    
    # Battery sensor
    entities["sensor"].append({
        "type": "sensor",
        "sensor_type": "battery",
        "name": f"{base_name} Battery",
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
    # 0x03 = Single Switch, 0x06 = Dual Switch, 0x07 = Quad Switch
    if device_type_code in [0x03, 0x06, 0x07]:
        channel_count = {0x03: 1, 0x06: 2, 0x07: 4}.get(device_type_code, 1)
        base_name = device_info.get('name', 'EWneo Switch')
        
        for ch in range(channel_count):
            # Für Single-Switch (0x03): Kein Kanal-Suffix
            # Für Dual/Quad (0x06, 0x07): Mit Kanal-Suffix
            if device_type_code == 0x03:
                # Single switch - no channel suffix
                switch_name = base_name
                unique_id = f"{serial_number}_switch"
            else:
                # Dual/Quad switch - with channel suffix
                switch_name = f"{base_name} CH{ch+1}"
                unique_id = f"{serial_number}_switch_ch{ch}"
                
            entities["switch"].append({
                "type": "switch",
                "name": switch_name,
                "unique_id": unique_id,
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
    # 0x05 = Single Motor, 0x08 = Dual Motor, 0x09 = Quad Motor
    elif device_type_code in [0x05, 0x08, 0x09]:
        channel_count = {0x05: 1, 0x08: 2, 0x09: 4}.get(device_type_code, 1)
        base_name = device_info.get('name', 'EWneo Motor')
        
        for ch in range(channel_count):
            # Für Single-Motor (0x05): Kein Kanal-Suffix
            # Für Dual/Quad (0x08, 0x09): Mit Kanal-Suffix
            if device_type_code == 0x05:
                # Single motor - no channel suffix
                cover_name = base_name
                unique_id = f"{serial_number}_cover"
            else:
                # Dual/Quad motor - with channel suffix
                cover_name = f"{base_name} CH{ch+1}"
                unique_id = f"{serial_number}_cover_ch{ch}"
                
            entities["cover"].append({
                "type": "cover",
                "name": cover_name,
                "unique_id": unique_id,
                "channel": ch,
                "device_class": "blind",
                "icon": "mdi:blinds"
            })
    
    return entities


def _create_sec_entities_legacy(serial_number: str, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Create entities for SecWave devices (LEGACY)."""
    # SecWave support is minimal for now
    return _empty_entity_dict()
