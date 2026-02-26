"""Entity specification generator for ELDAT devices.

DEPRECATED: Diese Datei wird schrittweise durch get_entity_specs() 
in den Device-Klassen (transceivers/rx11/devices/) ersetzt.

Neue Device-Klassen sollten EntitySpecsMixin verwenden und ihre 
Entity-Spezifikationen selbst generieren.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Dict, Any, List, Optional


from .translations import (
    translate,
    get_language,
    get_state_options_keys,
    get_button_map_keys,
    t_state,
    t_battery_level,
    t_receiver,
)
from .helpers_unique_id import make_unique_id

_LOGGER = logging.getLogger(__name__)


def _get_device_prop(device_info: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Get a property from device_info with extra_data fallback.
    
    Properties may be at the top level (from registered_devices.json) or 
    inside extra_data (from managed_devices.json / DeviceManager). This helper
    ensures the value is found regardless of storage format.
    """
    value = device_info.get(key)
    if value is not None:
        return value
    extra_data = device_info.get("extra_data", {})
    if extra_data:
        value = extra_data.get(key)
        if value is not None:
            return value
    return default


def _get_registration_id(device_info: Dict[str, Any]) -> str:
    """Get a short registration ID from device_info.
    
    This ID changes when a device is re-learned (new registration_id),
    but stays the same across restarts (loaded from persistent storage).
    
    IMPORTANT: Only uses explicit 'registration_id' field, NOT 'registered_at'.
    This ensures backwards compatibility - existing devices without 
    registration_id continue to use their original unique_ids.
    
    Returns:
        A 6-character hex string based on registration_id,
        or empty string if not available (backwards compatibility).
    """
    # Only use explicit registration_id field - NOT registered_at!
    # This ensures existing devices keep their original unique_ids
    # Check both top-level and extra_data (managed_devices.json stores it in extra_data)
    registration_id = device_info.get("registration_id", "")
    if not registration_id:
        extra_data = device_info.get("extra_data", {})
        registration_id = extra_data.get("registration_id", "")
    
    if not registration_id:
        return ""
    
    # Create a short hash from the registration_id
    hash_input = str(registration_id).encode('utf-8')
    hash_hex = hashlib.md5(hash_input).hexdigest()[:6]
    return hash_hex  # Return WITHOUT leading underscore - make_unique_id() will add it


def create_entity_specs_for_device(serial_number: str, device_info: Dict[str, Any], coordinator=None) -> Dict[str, List[Dict[str, Any]]]:
    """Create entity specifications for a device based on its type and configuration.
    
    LEGACY FUNCTION: Versucht zuerst, die Device-Klasse zu verwenden.
    Falls nicht verfügbar, fällt auf alte Logik zurück.
    
    Args:
        coordinator: Optional coordinator instance to store device instances
    
    Returns a dictionary with entity platform names as keys and lists of entity specs as values.
    """
    # Get extra_data for nested properties (managed_devices.json stores type in extra_data)
    extra_data = device_info.get("extra_data", {})
    
    # Try to get specs from device class if available
    device_class_specs = _try_get_specs_from_device_class(serial_number, device_info, coordinator)
    if device_class_specs:
        _LOGGER.info("✅ Using device class specs for %s", serial_number)
        return device_class_specs
    
    # Check if this is an EWneo device based on device_type_code (EWB devices)
    # This is the primary indicator for EWneo/EWB devices
    device_type_code = device_info.get("device_type_code") or extra_data.get("device_type_code", 0)
    if device_type_code:
        if device_type_code in [0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B]:
            # EWneo/EWB device types (switches, dimmers, motors, transceivers)
            _LOGGER.debug("Creating EWneo entities for device %s with type_code 0x%02X", 
                         serial_number, device_type_code)
            return _create_ewneo_entities_legacy(serial_number, device_info)
    
    # Legacy fallback based on type string
    # Use _get_device_prop for consistent extra_data fallback
    device_type = _get_device_prop(device_info, "type", "unknown")
    
    _LOGGER.info("🔍 create_entity_specs_for_device: serial=%s, type=%s, receiver_kind=%s", 
                serial_number, device_type, _get_device_prop(device_info, "receiver_kind"))
    
    if device_type == "ew_receiver":
        result = _create_ew_receiver_entities_legacy(serial_number, device_info)
        _LOGGER.info("📦 Easywave Receiver entity specs: %d platforms, cover=%d", 
                    len([p for p, e in result.items() if e]), len(result.get("cover", [])))
        return result
    elif device_type == "ew_transmitter":
        return _create_ew_transmitter_entities_legacy(serial_number, device_info)
    elif device_type in ["ew_sensor", "ewneo_sensor"]:
        return _create_ew_sensor_entities_legacy(serial_number, device_info)
    elif device_type == "ewneo_receiver" or device_info.get("neo_device") or extra_data.get("neo_device"):
        return _create_ewneo_entities_legacy(serial_number, device_info)
    elif device_type in ("sec_receiver", "sec_transmitter"):
        return _create_sec_entities_legacy(serial_number, device_info)
    
    _LOGGER.warning("⚠️ No entity specs found for device_type=%s", device_type)
    return _empty_entity_dict()


def _try_get_specs_from_device_class(serial_number: str, device_info: Dict[str, Any], coordinator=None) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """Try to get entity specs from the device class using transceivers registry.
    
    Args:
        coordinator: Optional coordinator to store device instances
    """
    device_type = device_info.get("type") or device_info.get("device_type")

    try:
        from .transceivers.rx11.devices.registry import get_device_class_for_info
        
        device_class = get_device_class_for_info(device_info)
        if device_class and hasattr(device_class, 'get_entity_specs'):
            # Prepare kwargs for device instantiation
            init_kwargs = {"name": device_info.get("name")}
            
            # Get extra_data for nested properties
            extra_data = device_info.get("extra_data", {})
            
            # Pass registration_id for unique entity IDs on re-learning
            # Check both top-level and extra_data (managed_devices.json stores it in extra_data)
            registration_id = device_info.get("registration_id") or extra_data.get("registration_id")
            
            # BUGFIX: If no registration_id exists, generate a DETERMINISTIC one for ALL device types
            # This ensures unique_id stability across restarts and re-learning
            if not registration_id:
                import hashlib
                # Use deterministic ID based on device_type + serial_number
                registration_id = hashlib.md5(f"{device_type}_{serial_number}".encode('utf-8')).hexdigest()
                device_info["registration_id"] = registration_id
                # Also store in extra_data if it exists
                if "extra_data" in device_info and isinstance(device_info["extra_data"], dict):
                    device_info["extra_data"]["registration_id"] = registration_id
                _LOGGER.info("🔧 Generated deterministic registration_id for %s %s: %s", 
                            device_type, serial_number[-8:], registration_id[:6])
            
            if registration_id:
                init_kwargs["registration_id"] = registration_id
            
            # For EWneoSensor, pass sensor_types
            if device_type in ["ewneo_sensor", "ew_sensor"]:
                # Check both top-level and extra_data
                sensor_types = device_info.get("sensor_types") or extra_data.get("sensor_types", [])
                # If no sensor_types stored, use default which includes temperature and humidity
                # Most EWneo sensors support both, so better to include both by default
                if not sensor_types:
                    sensor_types = ["temperature", "humidity"]
                    _LOGGER.debug("🔍 _try_get_specs_from_device_class: No sensor_types in device_info, using default %s", sensor_types)
                init_kwargs["sensor_types"] = sensor_types
                _LOGGER.debug("🔍 _try_get_specs_from_device_class: Creating EWneoSensor with sensor_types=%s, registration_id=%s", 
                             sensor_types, registration_id)
            
            # For EW Transmitter, pass button_count and detected_button_type (for 1-button A/B/C/D)
            if device_type == "ew_transmitter":
                button_count = device_info.get("button_count", 4)
                detected_button_type = device_info.get("detected_button_type") or extra_data.get("detected_button_type")
                init_kwargs["button_count"] = button_count
                if detected_button_type:
                    init_kwargs["button_type"] = detected_button_type
                _LOGGER.debug("Creating Transmitter with button_count=%s, button_type=%s", 
                             button_count, detected_button_type or "None")
            
            # Instantiate device temporarily to get specs
            device_instance = device_class(serial_number, **init_kwargs)
            specs = device_instance.get_entity_specs()
            
            # CRITICAL FIX: Store the device instance in the coordinator's transceiver
            # so that entities can find it later
            if coordinator and hasattr(coordinator, 'transceiver'):
                if not hasattr(coordinator.transceiver, '_device_instances'):
                    coordinator.transceiver._device_instances = {}
                coordinator.transceiver._device_instances[serial_number] = device_instance
                _LOGGER.info("💾 Stored device instance for %s in transceiver: %s", 
                           serial_number[-8:], type(device_instance).__name__)
            
            _LOGGER.debug("Device class returned specs: %s", {k: len(v) for k, v in specs.items() if v})
            
            # For Easywave Transmitter: use legacy specs which now include battery_warning
            # The Device class specs are used for telegram processing, but legacy specs 
            # are used for entity creation to ensure all Transmitter configurations are supported
            if device_type == "ew_transmitter":
                legacy_specs = _create_ew_transmitter_entities_legacy(serial_number, device_info)
                _LOGGER.debug("✅ Using legacy entity specs for Easywave Transmitter %s (includes battery_warning)", serial_number)
                return legacy_specs
            
            _LOGGER.debug("✅ Using entity specs from device class for %s", serial_number)
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
        "button": [],
        "select": []
    }


# ============================================================
# Entity creation functions
# ============================================================

def _create_ew_receiver_entities_legacy(serial_number: str, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Create entities for Easywave Receiver devices.
    
    Supported receiver_kind values:
    - impulse: 1 zustandsloser Toggle-Button
    - switch_2button: 1 zustandslose Switch-Entität (Ein/Aus)
    - cover_2button: 1 zustandslose Cover-Entität (Auf/Zu ohne Stopp)
    - motor_3button: 1 zustandslose Cover-Entität (Auf/Stopp/Zu)
    - heating_cooling: 1 Switch mit Zustand und 4h Wiederholung
    - universal_4button: 4 einzelne zustandslose Buttons
    """
    from .device_icons import get_entity_config_for_device
    
    entities = _empty_entity_dict()
    # CRITICAL: Check extra_data fallback — DeviceManager stores receiver_kind in extra_data
    receiver_kind = _get_device_prop(device_info, "receiver_kind", "impulse")
    operating_mode = _get_device_prop(device_info, "operating_mode", 1)
    # Get registration ID for unique entity creation on re-learning
    reg_id = _get_registration_id(device_info)
    # Get translated fallback name
    lang = get_language()
    fallback_name = f"Easywave {t_receiver(lang)} {serial_number}"
    base_name = device_info.get("name") or fallback_name
    
    # Get device configuration
    device_config = get_entity_config_for_device(
        device_type="ew_receiver",
        receiver_kind=receiver_kind,
        operating_mode=operating_mode
    )
    
    # make_unique_id is now imported at top-level
    # Create entities based on receiver_kind
    if receiver_kind == "impulse":
        # Impuls (1-Tast): Toggle button - stateless
        entities["button"].append({
            "type": "button",
            "name": "",
            "unique_id": make_unique_id(serial_number, "toggle", None, reg_id),
            "button_code": 0,  # TM_BUTTON_A
            "action": "toggle",
            "icon": "mdi:gesture-tap-button",
            "operating_mode": 1,
            "receiver_kind": "impulse"
        })
    
    elif receiver_kind == "switch_2button":
        # EIN/AUS (2-Tast): Zustandslose Switch-Entität
        entities["switch"].append({
            "type": "switch",
            "name": "",
            "unique_id": make_unique_id(serial_number, "switch", None, reg_id),
            "device_class": "switch",
            "icon": "mdi:light-switch",
            "icon_on": "mdi:light-switch",
            "icon_off": "mdi:light-switch-off",
            "operating_mode": 2,
            "receiver_kind": "switch_2button",
            "button_config": {
                "on": 0,   # TM_BUTTON_A for ON
                "off": 1   # TM_BUTTON_B for OFF
            },
            "stateless": True,
            "assumed_state": True,  # Zeigt immer Ein/Aus Buttons statt Toggle
            "repeat_on_toggle": True,
            "entity_category": None,
            "persistent_state": False
        })
    
    elif receiver_kind == "cover_2button":
        # AUF/ZU (2-Tast): Zustandslose Cover-Entität ohne Stop
        entities["cover"].append({
            "type": "cover",
            "name": "",
            "unique_id": make_unique_id(serial_number, "cover", None, reg_id),
            "device_class": "shade",
            "icon": "mdi:window-shutter",
            "icon_open": "mdi:window-shutter-open",
            "icon_closed": "mdi:window-shutter",
            "icon_unknown": "mdi:window-shutter-alert",
            "operating_mode": 2,
            "receiver_kind": "cover_2button",
            "button_config": {
                "open": 0,   # TM_BUTTON_A for UP
                "close": 1   # TM_BUTTON_B for DOWN
            },
            "supports_stop": False,
            "stateless": True,
            "assumed_state": True,  # Zeigt immer Auf/Zu Buttons statt Toggle
            "repeat_on_command": True,
            "entity_category": None,
            "persistent_state": False
        })
    
    elif receiver_kind == "motor_3button":
        # AUF/STOPP/ZU (3-Tast): Zustandslose Cover-Entität mit Stop
        entities["cover"].append({
            "type": "cover",
            "name": "",
            "unique_id": make_unique_id(serial_number, "motor", None, reg_id),
            "device_class": "shade",
            "icon": "mdi:window-shutter",
            "icon_open": "mdi:window-shutter-open",
            "icon_closed": "mdi:window-shutter",
            "icon_unknown": "mdi:window-shutter-alert",
            "icon_stopped": "mdi:stop-circle-outline",
            "operating_mode": 3,
            "receiver_kind": "motor_3button",
            "button_config": {
                "open": 0,   # TM_BUTTON_A for UP
                "close": 1,  # TM_BUTTON_B for DOWN
                "stop": 2    # TM_BUTTON_C for STOP
            },
            "supports_stop": True,
            "stateless": True,
            "assumed_state": True,  # Zeigt immer Auf/Stopp/Zu Buttons statt Toggle
            "repeat_on_command": True,
            "entity_category": None,
            "persistent_state": False
        })
    
    elif receiver_kind == "heating_cooling":
        # EIN/AUS (Heizung): Switch with 4h auto-repeat
        entities["switch"].append({
            "type": "switch",
            "name": "",
            "unique_id": make_unique_id(serial_number, "heating_cooling_switch", None, reg_id),
            "device_class": "switch",
            "icon": "mdi:radiator",
            "icon_on": "mdi:radiator",
            "icon_off": "mdi:radiator-off",
            "operating_mode": 2,
            "receiver_kind": "heating_cooling",
            "button_config": {
                "on": 0,   # TM_BUTTON_A for ON
                "off": 1   # TM_BUTTON_B for OFF
            },
            "supports_4h_repetition": True,
            "entity_category": None,
            "persistent_state": True
        })
    
    elif receiver_kind == "universal_4button":
        # UNIVERSAL (4-Tast): 4 individual buttons
        # Use translation_key for HA's translation system
        button_keys = ["a", "b", "c", "d"]
        button_translation_keys = ["state_a", "state_b", "state_c", "state_d"]
        button_icons = [
            "mdi:alpha-a-circle",
            "mdi:alpha-b-circle",
            "mdi:alpha-c-circle",
            "mdi:alpha-d-circle"
        ]
        for i in range(4):
            entities["button"].append({
                "type": "button",
                "translation_key": button_translation_keys[i],  # Uses translations/*.json
                "unique_id": make_unique_id(serial_number, f"button_{button_keys[i]}", None, reg_id),
                "button_code": i,
                "action": f"button_{button_keys[i]}",
                "icon": button_icons[i],
                "operating_mode": 4,
                "receiver_kind": "universal_4button"
            })
    
    else:
        # Unknown receiver_kind - create fallback toggle button
        _LOGGER.warning("Unknown receiver_kind '%s' for Easywave Receiver %s, creating toggle button", 
                       receiver_kind, serial_number)
        entities["button"].append({
            "type": "button",
            "name": "",
            "unique_id": make_unique_id(serial_number, "toggle", None, reg_id),
            "button_code": 0,
            "action": "toggle",
            "icon": "mdi:gesture-tap-button",
            "operating_mode": 1,
            "receiver_kind": "impulse"
        })
    
    return entities


def _create_ew_transmitter_entities_legacy(serial_number: str, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Create entities for Easywave Transmitter devices (LEGACY).
    
    Supports different operating modes:
    - 1-Tast-Bedienung: Individual button entities
    - 2-Tast-Bedienung: 
      - EIN/AUS (switch): 2 buttons = 1 switch, 4 buttons = 2 switches
      - AUF/ZU (cover): 2 buttons = 1 cover, 4 buttons = 2 covers
    - 3-Tast-Bedienung: Cover with Auf/Zu/Stopp (A→Auf, B→Zu, C/D→Stopp)
    """
    entities = _empty_entity_dict()
    
    # Use _get_device_prop for extra_data fallback (DeviceManager stores in extra_data)
    operating_type = _get_device_prop(device_info, "operating_type", "1")
    button_count = _get_device_prop(device_info, "button_count", 4)
    grouping_mode = _get_device_prop(device_info, "grouping_mode", "single")
    usage_type = _get_device_prop(device_info, "usage_type", "switch")  # "switch" or "cover"
    switch_mode = _get_device_prop(device_info, "switch_mode", "impulse")
    base_name = _get_device_prop(device_info, 'name', 'Transmitter')
    detected_button_type = _get_device_prop(device_info, "detected_button_type")  # For 1-button A/B/C/D types
    # Get registration ID for unique entity creation on re-learning
    reg_id = _get_registration_id(device_info)
    
    _LOGGER.debug("Easywave Transmitter entity specs: serial=%s, operating_type=%s, button_count=%s, detected_button_type=%s",
                serial_number[-8:], operating_type, button_count, detected_button_type)
    
    if operating_type == "1":
        # 1-Tast-Bedienung
        button_labels = ["A", "B", "C", "D"]
        
        if grouping_mode == "single":
            # Einzeln schalten - Enum-Sensor pro Taste (keine automatischen Entity-Trigger)
            # Bei "impulse": Status wird bei Loslassen zurückgesetzt
            # Bei "permanent": Status bleibt erhalten (toggle)
            button_translation_keys = ["button_a", "button_b", "button_c", "button_d"]
            
            # For 1-button transmitters with auto-detected type: only create entity for the detected button
            if button_count == 1 and detected_button_type and detected_button_type.upper() in button_labels:
                detected_index = button_labels.index(detected_button_type.upper())
                _LOGGER.info("🎯 1-Button Transmitter: Creating entity for detected button %s", 
                              detected_button_type)
                # Create only ONE entity for the detected button
                entities["sensor"].append({
                    "type": "sensor",
                    "sensor_type": "transmitter_button",
                    "translation_key": button_translation_keys[detected_index],  # Uses translations/*.json
                    "unique_id": make_unique_id(serial_number, "button", detected_index, reg_id),
                    "button": button_labels[detected_index],
                    "button_index": detected_index,
                    "switch_mode": switch_mode,  # "impulse" oder "permanent"
                    "icon": "mdi:gesture-tap-button",
                    "device_class": "enum",
                    "options": ["pressed", "released"],  # Translated via translations/*.json
                })
            else:
                # Standard: create entities for all buttons up to button_count
                for i in range(button_count):
                    # Enum-Sensor mit "pressed"/"released" Zuständen (übersetzt zu "Betätigt"/"Nicht betätigt")
                    # Wir verwenden NICHT "on"/"off", da HA diese automatisch zu "eingeschaltet"/"ausgeschaltet" übersetzt
                    entities["sensor"].append({
                        "type": "sensor",
                        "sensor_type": "transmitter_button",
                        "translation_key": button_translation_keys[i],  # Uses translations/*.json
                        "unique_id": make_unique_id(serial_number, "button", i, reg_id),
                        "button": button_labels[i],
                        "button_index": i,
                        "switch_mode": switch_mode,  # "impulse" oder "permanent"
                        "icon": "mdi:gesture-tap-button",
                        "device_class": "enum",
                        "options": ["pressed", "released"],  # Translated via translations/*.json
                    })
        else: # "group" or any other grouping mode
            # Als Gruppe schalten - immer Sensor mit letztem Button
            # Bei "impulse": Status wird zurückgesetzt
            # Bei "permanent": Status bleibt erhalten
            
            # For 1-button transmitters with auto-detected type: only show detected button + released
            if button_count == 1 and detected_button_type and detected_button_type.upper() in button_labels:
                detected_button_lower = detected_button_type.lower()
                options = [detected_button_lower]
                _LOGGER.info("🎯 1-Button Transmitter (group mode): Creating 'Zustand' sensor with options for button %s", 
                              detected_button_type)
                if switch_mode == "impulse":
                    options = options + ["released"]  # "released" = "Nicht betätigt" via translations/*.json
                entities["sensor"].append({
                    "type": "sensor",
                    "translation_key": "last_button",  # Uses translations/*.json (= "Zustand")
                    "unique_id": make_unique_id(serial_number, "last_button", None, reg_id),
                    "switch_mode": switch_mode,  # "impulse" oder "permanent"
                    "icon": "mdi:radiobox-marked",
                    "device_class": "enum",
                    "options": options,
                })
            else:
                # Standard: create "last_button" sensor for all buttons
                # Use lowercase keys for proper HA translation lookup
                button_option_keys = ["a", "b", "c", "d"]
                options = button_option_keys[:button_count]
                _LOGGER.debug("Creating grouped 'Zustand' sensor: button_count=%d, options=%s",
                            button_count, options)
                if switch_mode == "impulse":
                    options = options + ["released"]  # "released" = "Nicht betätigt" via translations/*.json
                entities["sensor"].append({
                    "type": "sensor",
                    "translation_key": "last_button",  # Uses translations/*.json
                    "unique_id": make_unique_id(serial_number, "last_button", None, reg_id),
                    "switch_mode": switch_mode,  # "impulse" oder "permanent"
                    "icon": "mdi:radiobox-marked",
                    "device_class": "enum",
                    "options": options,
                })
    
    elif operating_type == "2":
        # 2-Tast-Bedienung -> create state sensors (An/Aus) or switches (Auf/Zu)
        is_switch_mode = usage_type == "switch" or switch_mode == "switch"
        if is_switch_mode:
            state_options = get_state_options_keys("2", "switch")  # ["on", "off"] - translated by HA
            icon = "mdi:light-switch"
            if button_count == 2 or grouping_mode == "single":
                # 2 buttons → 1 state sensor (A→state[0], B→state[1])
                entities["sensor"].append({
                    "type": "sensor",
                    "sensor_type": "transmitter_state",
                    "translation_key": "transmitter_state",  # Uses translations/*.json
                    "unique_id": make_unique_id(serial_number, "state", None, reg_id),
                    "state_key": "transmitter_state_1",
                    "channel": 0,
                    "device_class": "enum",
                    "options": state_options,
                    "button_map": get_button_map_keys("2", "switch"),
                    "icon": icon,
                    "operating_type": operating_type,
                    "usage_type": usage_type,
                })
            else:
                # 4 buttons → 2 state sensors (A/B, C/D) - "Zustand A/B" und "Zustand C/D"
                entities["sensor"].append({
                    "type": "sensor",
                    "sensor_type": "transmitter_state",
                    "translation_key": "state_ab",  # Uses translations/*.json
                    "unique_id": make_unique_id(serial_number, "state", 0, reg_id),
                    "state_key": "transmitter_state_1",
                    "channel": 0,
                    "device_class": "enum",
                    "options": state_options,
                    "button_map": {
                        0: "on",
                        1: "off",
                    },
                    "icon": icon,
                    "operating_type": operating_type,
                    "usage_type": usage_type,
                })
                entities["sensor"].append({
                    "type": "sensor",
                    "sensor_type": "transmitter_state",
                    "translation_key": "state_cd",  # Uses translations/*.json
                    "unique_id": make_unique_id(serial_number, "state", 1, reg_id),
                    "state_key": "transmitter_state_2",
                    "channel": 1,
                    "device_class": "enum",
                    "options": state_options,
                    "button_map": {
                        2: "on",
                        3: "off",
                        "C": "on",
                        "D": "off",
                    },
                    "icon": icon,
                    "operating_type": operating_type,
                    "usage_type": usage_type,
                })
        else:
            # Auf/Zu -> switch entity with persistent state
            state_options = get_state_options_keys("2", "cover")  # ["up", "down"] - translated by HA
            icon = "mdi:window-shutter"
            if button_count == 2 or grouping_mode == "single":
                # 2 Tasten
                entities["binary_sensor"].append({
                    "type": "binary_sensor",
                    "sensor_type": "transmitter_state",
                    "translation_key": "transmitter_state_cover",  # Uses translations/*.json
                    "unique_id": make_unique_id(serial_number, "state", None, reg_id),
                    "state_key": "transmitter_state_1",
                    "channel": 0,
                    "device_class": "opening",  # Shows 'Geöffnet'/'Geschlossen' in logbook
                    "options": state_options,
                    "button_map": get_button_map_keys("2", "cover"),
                    "icon": icon,
                    "operating_type": operating_type,
                    "usage_type": usage_type,
                    "on_label": state_options[0],
                    "off_label": state_options[1],
                })
            else:
                # 4 Tasten - "Zustand A/B" und "Zustand C/D"
                entities["binary_sensor"].append({
                    "type": "binary_sensor",
                    "sensor_type": "transmitter_state",
                    "translation_key": "state_ab",  # Uses translations/*.json
                    "unique_id": make_unique_id(serial_number, "state", 0, reg_id),
                    "state_key": "transmitter_state_1",
                    "channel": 0,
                    "device_class": "opening",  # Shows 'Geöffnet'/'Geschlossen' in logbook
                    "options": state_options,
                    "button_map": {
                        0: "up",
                        1: "down",
                        "A": "up",
                        "B": "down",
                    },
                    "icon": icon,
                    "operating_type": operating_type,
                    "usage_type": usage_type,
                    "on_label": state_options[0],
                    "off_label": state_options[1],
                })
                entities["binary_sensor"].append({
                    "type": "binary_sensor",
                    "sensor_type": "transmitter_state",
                    "translation_key": "state_cd",  # Uses translations/*.json
                    "unique_id": make_unique_id(serial_number, "state", 1, reg_id),
                    "state_key": "transmitter_state_2",
                    "channel": 1,
                    "device_class": "opening",  # Shows 'Geöffnet'/'Geschlossen' in logbook
                    "options": state_options,
                    "button_map": {
                        2: "up",
                        3: "down",
                        "C": "up",
                        "D": "down",
                    },
                    "icon": icon,
                    "operating_type": operating_type,
                    "usage_type": usage_type,
                    "on_label": state_options[0],
                    "off_label": state_options[1],
                })
    
    elif operating_type == "3":
        # 3-Tast-Bedienung: state sensor with Auf/Zu/Stopp
        # A=Auf, B=Zu, C/D=Stopp (beide Tasten triggern denselben Zustand)
        state_options = get_state_options_keys("3")  # ["up", "down", "stop"] - translated by HA
        button_map = get_button_map_keys("3")
        _LOGGER.debug("Creating 3-button sensor for %s with options: %s", serial_number[-8:], state_options)
        entities["sensor"].append({
            "type": "sensor",
            "sensor_type": "transmitter_state",
            "translation_key": "transmitter_state",  # Uses translations/*.json
            "unique_id": make_unique_id(serial_number, "state", None, reg_id),
            "channel": 0,
            "device_class": "enum",
            "options": state_options,
            "button_map": button_map,
            "icon": "mdi:window-shutter",
            "operating_type": operating_type,
            "usage_type": "cover",
        })
        _LOGGER.debug("3-button sensor spec created for %s", serial_number[-8:])
    
    else:
        # Fallback: Create binary sensor for each button
        _LOGGER.warning("⚠️ Unknown operating_type '%s' for %s - using fallback binary sensors",
                       operating_type, serial_number[-8:])
        button_labels = ["A", "B", "C", "D"]
        button_translation_keys = ["button_a", "button_b", "button_c", "button_d"]
        for i in range(button_count):
            entities["binary_sensor"].append({
                "type": "binary_sensor",
                "translation_key": button_translation_keys[i],  # Uses translations/*.json
                "unique_id": make_unique_id(serial_number, f"button", i, reg_id),
                "button_index": i,
                "button_label": button_labels[i],
                "device_class": "button",
                "icon": "mdi:gesture-tap-button"
            })
    
    # Battery warning binary sensor - always add for transmitters
    # Transmitters report battery status via telegram info_type 0
    entities["binary_sensor"].append({
        "type": "binary_sensor",
        "sensor_type": "battery_warning",
        "translation_key": "battery_warning",  # HA looks up entity.binary_sensor.battery_warning.name
        "unique_id": make_unique_id(serial_number, "battery_warning", None, reg_id),
        "device_class": "battery",
        "icon": "mdi:battery",
        "has_entity_name": True
    })
    
    _LOGGER.info("✅ Easywave Transmitter entities created: binary_sensor=%d, sensor=%d, switch=%d, cover=%d",
                len(entities.get("binary_sensor", [])), 
                len(entities.get("sensor", [])),
                len(entities.get("switch", [])),
                len(entities.get("cover", [])))
    
    return entities


def _create_ew_sensor_entities_legacy(serial_number: str, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Create entities for EW-Sensor devices (LEGACY).
    
    For EWneo-Sensoren, tries to use the new universal sensor class specs.
    """
    import hashlib
    entities = _empty_entity_dict()
    # Get registration ID for unique entity creation on re-learning
    # Check both top-level and extra_data (managed_devices.json stores it in extra_data)
    extra_data = device_info.get("extra_data", {})
    reg_id = _get_registration_id(device_info)
    if not reg_id:
        reg_id = _get_registration_id(extra_data)
    registration_id = device_info.get("registration_id") or extra_data.get("registration_id")
    
    # BUGFIX: If no registration_id exists, generate one based on serial_number
    # This ensures unique_id stability - the same sensor always gets the same unique_ids
    # even if registration_id wasn't set at entity creation time
    if not registration_id and device_info.get("type") == "ewneo_sensor":
        # Generate deterministic registration_id from serial number
        registration_id = hashlib.md5(f"neo_sensor_{serial_number}".encode('utf-8')).hexdigest()
        # Store it so future calls use the same value
        device_info["registration_id"] = registration_id
        # Also store in extra_data if it exists
        if "extra_data" in device_info and isinstance(device_info["extra_data"], dict):
            device_info["extra_data"]["registration_id"] = registration_id
        # Now re-calculate reg_id from the new registration_id using same logic as _get_registration_id()
        reg_id = hashlib.md5(str(registration_id).encode('utf-8')).hexdigest()[:6]
        _LOGGER.info("🔧 Generated deterministic registration_id for neo_sensor %s: %s", 
                    serial_number[-8:], registration_id[:6])
    
    # Check if this is an EWneo-Sensoren (new format)
    device_type = device_info.get("type") or extra_data.get("type", "unknown")
    _LOGGER.debug("Creating EWneo sensor specs: device_type=%s, registration_id=%s", device_type, registration_id)
    
    if device_type == "ewneo_sensor":
        # Try to create entity specs using the new EWneoSensor class
        try:
            from .transceivers.rx11.devices.ewneo_sensors import EWneoSensor, NEO_TYPE_SENSOR_MAP
            
            # Determine sensor types from device_info (check both top-level and extra_data)
            sensor_types = device_info.get('sensor_types') or extra_data.get('sensor_types', [])
            _LOGGER.debug("sensor_types from device_info: %s", sensor_types)
            
            # If no sensor_types, try to convert from neo_types
            if not sensor_types:
                neo_types = device_info.get('neo_types') or extra_data.get('neo_types', [])
                if neo_types:
                    sensor_types = []
                    for neo_type in neo_types:
                        sensor_type = NEO_TYPE_SENSOR_MAP.get(neo_type)
                        if sensor_type and sensor_type not in sensor_types:
                            sensor_types.append(sensor_type)
            
            # If still no sensor_types, check for single neo_type
            if not sensor_types:
                neo_type = device_info.get('neo_type') or extra_data.get('neo_type')
                if neo_type:
                    sensor_type = NEO_TYPE_SENSOR_MAP.get(neo_type)
                    if sensor_type:
                        sensor_types = [sensor_type]
            
            # Default to temperature if nothing found
            if not sensor_types:
                sensor_types = ['temperature']
                _LOGGER.warning("⚠️ No sensor types found for %s, defaulting to temperature", serial_number)
            
            _LOGGER.debug("Creating EWneoSensor with sensor_types: %s, registration_id: %s", sensor_types, registration_id)
            
            # Create temporary device instance to get entity specs
            # Pass registration_id for unique_id suffix
            init_kwargs = {
                "name": device_info.get('name', f'EWneo-Sensoren {serial_number}'),
                "sensor_types": sensor_types
            }
            if registration_id:
                init_kwargs["registration_id"] = registration_id
                
            device_instance = EWneoSensor(serial_number, **init_kwargs)
            
            # Get specs from device
            specs = device_instance.get_entity_specs()
            _LOGGER.debug("EWneoSensor.get_entity_specs() returned: %s", 
                          {k: len(v) for k, v in specs.items() if v})
            for platform, entities in specs.items():
                if entities:
                    for entity in entities:
                        _LOGGER.debug("  %s: sensor_type=%s, name=%s", 
                                      platform, entity.get("sensor_type"), entity.get("name"))
            return specs
            
        except Exception as e:
            _LOGGER.warning("Could not create EWneo-Sensoren specs from class: %s, using legacy fallback", e)
    
    # Legacy fallback for old EW sensors
    base_name = device_info.get('name', 'Sensor')
    
    # Temperature sensor - use translation_key for HA translation
    # For Neo sensors, use DETERMINISTIC unique_id without registration_id suffix
    # This ensures entities remain stable across re-learning
    if device_info.get("has_temperature", True):
        entities["sensor"].append({
            "type": "sensor",
            "sensor_type": "temperature",
            "translation_key": "temperature",  # HA looks up entity.sensor.temperature.name
            "unique_id": f"{serial_number}_temperature",
            "device_class": "temperature",
            "unit_of_measurement": "°C",
            "icon": "mdi:thermometer",
            "has_entity_name": True
        })
    
    # Humidity sensor - use translation_key for HA translation
    # Default to True to include humidity for most sensors
    if device_info.get("has_humidity", True):
        entities["sensor"].append({
            "type": "sensor",
            "sensor_type": "humidity",
            "translation_key": "humidity",  # HA looks up entity.sensor.humidity.name
            "unique_id": f"{serial_number}_humidity",
            "device_class": "humidity",
            "unit_of_measurement": "%",
            "icon": "mdi:water-percent",
            "has_entity_name": True
        })
    
    # Battery warning binary sensor (NOT percentage sensor)
    # EWneo sensors use battery_warning (low/ok) not percentage
    entities["binary_sensor"].append({
        "type": "binary_sensor",
        "sensor_type": "battery_warning",
        "translation_key": "battery_warning",  # HA looks up entity.binary_sensor.battery_warning.name
        "unique_id": f"{serial_number}_battery_warning",
        "device_class": "battery",
        "icon": "mdi:battery",
        "has_entity_name": True
    })
    
    return entities


def _create_ewneo_entities_legacy(serial_number: str, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Create entities for EWneo devices (LEGACY)."""
    entities = _empty_entity_dict()
    
    device_type_code = device_info.get("device_type_code", 0)
    # Get registration ID for unique entity creation on re-learning
    reg_id = _get_registration_id(device_info)
    
    # EWneo Switch (single/dual/quad)
    # 0x03 = Single Switch, 0x06 = Dual Switch, 0x07 = Quad Switch
    if device_type_code in [0x03, 0x06, 0x07]:
        channel_count = {0x03: 1, 0x06: 2, 0x07: 4}.get(device_type_code, 1)
        
        for ch in range(channel_count):
            # Für Single-Switch (0x03): Kein Entity-Name (None) - nur Device-Name wird angezeigt
            # Für Dual/Quad (0x06, 0x07): Mit Kanal-Nummer via translation_key
            if device_type_code == 0x03:
                # Single switch - no entity name, device name only
                entities["switch"].append({
                    "type": "switch",
                    "name": None,
                    "unique_id": make_unique_id(serial_number, "switch", None, reg_id),
                    "channel": ch,
                    "device_class": "switch",
                    "icon": "mdi:light-switch",
                    "icon_on": "mdi:light-switch",
                    "icon_off": "mdi:light-switch-off"
                })
            else:
                # Dual/Quad switch - with channel number via translation_key
                entities["switch"].append({
                    "type": "switch",
                    "translation_key": f"channel_{ch+1}",  # Uses translations/*.json
                    "unique_id": make_unique_id(serial_number, "switch", ch, reg_id),
                    "channel": ch,
                    "device_class": "switch",
                    "icon": "mdi:light-switch",
                    "icon_on": "mdi:light-switch",
                    "icon_off": "mdi:light-switch-off"
                })
    
    # EWneo Dimmer
    elif device_type_code == 0x04:
        entities["light"].append({
            "type": "light",
            "name": None,  # Use device name only
            "unique_id": make_unique_id(serial_number, "light", None, reg_id),
            "icon": "mdi:lightbulb-outline",
            "icon_on": "mdi:lightbulb-on",
            "icon_off": "mdi:lightbulb-outline"
        })
    
    # EWneo Motor (single/dual/quad)
    # 0x05 = Single Motor, 0x08 = Dual Motor, 0x09 = Quad Motor
    elif device_type_code in [0x05, 0x08, 0x09]:
        channel_count = {0x05: 1, 0x08: 2, 0x09: 4}.get(device_type_code, 1)
        
        for ch in range(channel_count):
            # Für Single-Motor (0x05): Kein Entity-Name (None) - nur Device-Name wird angezeigt
            # Für Dual/Quad (0x08, 0x09): Mit Kanal-Nummer via translation_key
            if device_type_code == 0x05:
                # Single motor - no entity name, device name only
                entities["cover"].append({
                    "type": "cover",
                    "name": None,
                    "unique_id": make_unique_id(serial_number, "cover", None, reg_id),
                    "channel": ch,
                    "device_class": "blind",
                    "icon": "mdi:window-shutter",
                    "icon_open": "mdi:window-shutter-open",
                    "icon_closed": "mdi:window-shutter"
                })
            else:
                # Dual/Quad motor - with channel number via translation_key
                entities["cover"].append({
                    "type": "cover",
                    "translation_key": f"channel_{ch+1}",  # Uses translations/*.json
                    "unique_id": make_unique_id(serial_number, "cover", ch, reg_id),
                    "channel": ch,
                    "device_class": "blind",
                    "icon": "mdi:window-shutter",
                    "icon_open": "mdi:window-shutter-open",
                    "icon_closed": "mdi:window-shutter"
                })
    
    return entities


def _create_sec_entities_legacy(serial_number: str, device_info: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Create entities for SecWave devices (LEGACY)."""
    # SecWave support is minimal for now
    return _empty_entity_dict()
