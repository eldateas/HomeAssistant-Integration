"""Device Trigger Support für ELDAT Integration."""

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.helpers import device_registry as dr
import voluptuous as vol

from .const import DOMAIN

# Device Trigger Typen für EW-Receiver
TRIGGER_TYPES = [
    "button_press_start",      # Taste gedrückt (Start)
    "button_press_end",        # Taste losgelassen (Ende) 
    "button_short_press",      # Kurzes Drücken (< 1.5s) - einzelner Befehl
    "button_long_press_start", # Langes Drücken Start (≥ 1.5s) - Continuous Sending beginnt
    "button_long_press_end",   # Langes Drücken Ende - Continuous Sending stoppt
    "button_hold",            # Taste gehalten (kontinuierlich)
]

# Button Subtypen basierend auf Receiver-Typ und Modus
BUTTON_SUBTYPES = {
    "switch": {
        1: ["toggle"],
        2: ["ein", "aus"]
    },
    "dimmer": {
        1: ["toggle"], 
        2: ["heller", "dunkler"]
    },
    "motor": {
        1: ["toggle"],
        2: ["auf", "ab"],
        3: ["auf", "ab", "stop"]
    }
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend({
    vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
    vol.Required("subtype"): str,
})


async def async_get_triggers(hass, device_id):
    """Gibt verfügbare Device Triggers für ein ELDAT-Gerät zurück."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    
    if not device or DOMAIN not in device.identifiers:
        return []
    
    # Extrahiere Device-Informationen
    device_identifier = None
    for identifier in device.identifiers:
        if identifier[0] == DOMAIN:
            device_identifier = identifier[1]
            break
    
    if not device_identifier:
        return []
    
    # Ermittle Gerätetyp und Modus aus den Entitäten
    entity_registry = hass.helpers.entity_registry.async_get(hass)
    entities = entity_registry.entities
    
    device_entities = [
        entity for entity in entities.values()
        if entity.device_id == device_id and entity.domain == "button"
    ]
    
    triggers = []
    
    # Analysiere Buttons um Receiver-Typ zu ermitteln
    receiver_info = _analyze_device_entities(device_entities)
    
    for receiver_type, modes in receiver_info.items():
        for mode, buttons in modes.items():
            for button in buttons:
                # Erstelle Triggers für jeden Button und Trigger-Typ
                for trigger_type in TRIGGER_TYPES:
                    # Skip irrelevante Kombinationen
                    if _is_trigger_relevant(trigger_type, button, receiver_type):
                        triggers.append({
                            CONF_PLATFORM: "device",
                            CONF_DEVICE_ID: device_id,
                            CONF_DOMAIN: DOMAIN,
                            CONF_TYPE: trigger_type,
                            "subtype": button,
                            "name": f"{_get_button_display_name(button, receiver_type)} ({_get_trigger_display_name(trigger_type)})"
                        })
    
    return triggers


async def async_attach_trigger(hass, config, action, automation_info):
    """Bindet einen Device Trigger an eine Automation."""
    device_id = config[CONF_DEVICE_ID]
    trigger_type = config[CONF_TYPE]
    subtype = config["subtype"]
    
    # Get device serial number from device registry
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    
    device_serial_number = None
    if device:
        for identifier in device.identifiers:
            if identifier[0] == DOMAIN:
                device_serial_number = identifier[1]
                break
    
    if not device_serial_number:
        raise ValueError(f"Could not find serial number for device {device_id}")
    
    # Event-basierter Trigger für ELDAT Events
    event_type = f"eldat_{trigger_type}"
    
    from homeassistant.helpers import event
    
    async def _handle_event(event_data):
        """Handle the event and check if it matches our criteria."""
        # Match by serial number (which is used as device_id in events)
        if (event_data.data.get("device_id") == device_serial_number and 
            event_data.data.get("subtype") == subtype):
            # Execute the automation action
            await action({
                "trigger": {
                    "platform": "device",
                    "device_id": device_id, 
                    "type": trigger_type,
                    "subtype": subtype,
                    "event": event_data
                }
            })
    
    # Track the event
    return hass.bus.async_listen(event_type, _handle_event)


def _analyze_device_entities(entities):
    """Analysiert Device-Entitäten um Receiver-Typ und Modi zu ermitteln."""
    receiver_info = {}
    
    for entity in entities:
        # Parse Entity ID für Receiver-Informationen
        entity_id = entity.entity_id
        if "_btn_" in entity_id:
            # Extrahiere Button-Aktion aus Entity ID
            button_action = entity_id.split("_btn_")[-1]
            
            # Ermittle Receiver-Typ basierend auf Button-Aktion
            receiver_type = _get_receiver_type_from_action(button_action)
            mode = _get_mode_from_action(button_action, receiver_type)
            
            if receiver_type not in receiver_info:
                receiver_info[receiver_type] = {}
            if mode not in receiver_info[receiver_type]:
                receiver_info[receiver_type][mode] = []
            
            receiver_info[receiver_type][mode].append(button_action)
    
    return receiver_info


def _get_receiver_type_from_action(action):
    """Ermittelt Receiver-Typ basierend auf Button-Aktion."""
    if action in ["auf", "ab", "stop"]:
        return "motor"
    elif action in ["heller", "dunkler"]:
        return "dimmer"
    elif action in ["ein", "aus", "toggle"]:
        return "switch"
    else:
        return "unknown"


def _get_mode_from_action(action, receiver_type):
    """Ermittelt Modus basierend auf Aktion und Receiver-Typ."""
    if receiver_type == "motor":
        if action == "stop":
            return 3  # 3-Tasten Modus
        else:
            return 2  # 2-Tasten Modus (könnte auch 1 sein)
    elif receiver_type in ["dimmer", "switch"]:
        if action == "toggle":
            return 1  # 1-Tasten Modus
        else:
            return 2  # 2-Tasten Modus
    return 1


def _is_trigger_relevant(trigger_type, button, receiver_type):
    """Prüft ob ein Trigger-Typ für einen Button relevant ist."""
    # Stop-Buttons brauchen keine Hold/Long Press Triggers
    if button == "stop" and trigger_type in ["button_long_press", "button_hold"]:
        return False
    
    # Toggle-Buttons brauchen meist nur Short Press
    if button == "toggle" and trigger_type in ["button_press_start", "button_press_end", "button_hold"]:
        return False
    
    # Movement/Dimming Buttons profitieren von allen Trigger-Typen
    if button in ["auf", "ab", "heller", "dunkler"]:
        return True
    
    # Ein/Aus Buttons hauptsächlich Short/Long Press
    if button in ["ein", "aus"] and trigger_type not in ["button_hold"]:
        return True
    
    return True


def _get_button_display_name(button, receiver_type):
    """Gibt Display-Namen für Button zurück."""
    names = {
        "ein": "Ein",
        "aus": "Aus", 
        "toggle": "Toggle",
        "heller": "Heller",
        "dunkler": "Dunkler",
        "auf": "Auf",
        "ab": "Ab", 
        "stop": "Stop"
    }
    return names.get(button, button.capitalize())


def _get_trigger_display_name(trigger_type):
    """Gibt Display-Namen für Trigger-Typ zurück."""
    names = {
        "button_press_start": "Taste gedrückt",
        "button_press_end": "Taste losgelassen",
        "button_short_press": "kurz drücken",
        "button_long_press": "lang drücken", 
        "button_hold": "gehalten"
    }
    return names.get(trigger_type, trigger_type)