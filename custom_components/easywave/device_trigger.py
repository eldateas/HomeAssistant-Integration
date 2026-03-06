"""Device triggers for EASYWAVE integration.

This module provides device triggers for Easywave Transmitter button presses,
allowing users to create automations based on button events directly
in the Home Assistant UI.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    EVENT_GATEWAY_CONNECTED,
    EVENT_GATEWAY_DISCONNECTED,
    EVENT_GATEWAY_STATUS_CHANGED,
)
from .translations import translate, get_button_label, get_language

_LOGGER = logging.getLogger(__name__)

# Trigger types for Easywave Transmitter buttons (basierend auf RX11 EWB_RCV)
# Nur die grundlegenden Events vom RX11:
TRIGGER_TYPE_BUTTON_PRESS = "button_press"        # Taste gedrückt / Zustandswechsel (vom RX11)
TRIGGER_TYPE_BUTTON_RELEASE = "button_release"    # Taste losgelassen (vom RX11)
TRIGGER_TYPE_CHANNEL_ON = "channel_on"            # Kanal eingeschaltet (1-Tast Dauer)
TRIGGER_TYPE_CHANNEL_OFF = "channel_off"          # Kanal ausgeschaltet (1-Tast Dauer)

# Gateway connection status triggers
TRIGGER_TYPE_GATEWAY_CONNECTED = "gateway_connected"
TRIGGER_TYPE_GATEWAY_DISCONNECTED = "gateway_disconnected"

# Schema for transmitter button triggers
CONF_SUBTYPE = "subtype"

TRANSMITTER_TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(
            [
                TRIGGER_TYPE_BUTTON_PRESS,
                TRIGGER_TYPE_BUTTON_RELEASE,
                TRIGGER_TYPE_CHANNEL_ON,
                TRIGGER_TYPE_CHANNEL_OFF,
            ]
        ),
        vol.Required(CONF_SUBTYPE): str,
    }
)

# Schema for gateway triggers (no subtype needed)
GATEWAY_TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(
            [
                TRIGGER_TYPE_GATEWAY_CONNECTED,
                TRIGGER_TYPE_GATEWAY_DISCONNECTED,
            ]
        ),
        vol.Optional(CONF_SUBTYPE): str,
    }
)

# Combined schema for validation
TRIGGER_SCHEMA = vol.Any(TRANSMITTER_TRIGGER_SCHEMA, GATEWAY_TRIGGER_SCHEMA)

# Sensor type identifiers used to detect EWneo/EW sensor devices
_SENSOR_TYPE_IDENTIFIERS = ["temperature", "humidity", "wind_speed", "rain"]

# Receiver device types — these should NOT get button triggers.
# Receivers use standard HA entity state triggers (switch on/off, cover open/close, etc.).
_RECEIVER_DEVICE_TYPES = {
    "ew_receiver",
    "ewneo_switch",
    "ewneo_dimmer",
    "ewneo_motor",
    "ewneo_dual_switch",
    "ewneo_quad_switch",
    "ewneo_dual_motor",
    "ewneo_quad_motor",
}


def _get_device_info_for_serial(hass: HomeAssistant, serial_number: str) -> dict[str, Any] | None:
    """Find device info in coordinator by serial number or registration_id.

    The HA device registry identifier may be a registration_id (UUID) for
    newer devices or the hardware serial number for legacy devices.  This
    helper tries both lookup strategies.
    """
    domain_data = hass.data.get(DOMAIN, {})

    for entry_id, entry_data in domain_data.items():
        coordinator = None
        if isinstance(entry_data, dict):
            coordinator = entry_data.get("coordinator")
        elif hasattr(entry_data, "devices"):
            coordinator = entry_data

        if not coordinator:
            continue

        for dev_serial, dev_info in coordinator.devices.items():
            # 1) Exact serial match
            if dev_serial == serial_number:
                return dev_info
            # 2) Match by registration_id (UUID-based identifier)
            reg_id = dev_info.get("registration_id")
            if not reg_id:
                extra = dev_info.get("extra_data", {})
                reg_id = extra.get("registration_id") if extra else None
            if reg_id and reg_id == serial_number:
                return dev_info
            # 3) Fallback: match by last 8 characters
            if len(dev_serial) >= 8 and len(serial_number) >= 8:
                if dev_serial[-8:] == serial_number[-8:]:
                    return dev_info

    _LOGGER.debug("No device info found for identifier %s", serial_number)
    return None


def _is_sensor_device(
    device_info: dict[str, Any] | None,
    device_id: str,
    hass: HomeAssistant,
) -> bool:
    """Check if the device is an EWneo/EW sensor (measurement device, not a transmitter).
    
    EWneo sensors report measurement values (temperature, humidity, wind, rain)
    and should NOT get button press/release triggers.
    """
    if device_info:
        device_type = device_info.get("type") or device_info.get("device_type", "")
        extra_data = device_info.get("extra_data", {})
        device_type = device_type or extra_data.get("type", "")
        if device_type in ("ew_sensor", "ewneo_sensor"):
            return True
    
    # Fallback: check entity registry for sensor-type entities (temperature, humidity, etc.)
    entity_registry = er.async_get(hass)
    device_entities = [
        entry for entry in entity_registry.entities.values()
        if entry.device_id == device_id
    ]
    
    for entity in device_entities:
        uid = entity.unique_id or ""
        # EWneo sensor unique_ids contain sensor type names
        for sensor_type in _SENSOR_TYPE_IDENTIFIERS:
            if f"_{sensor_type}" in uid:
                return True
    
    return False


def _is_receiver_device(
    device_info: dict[str, Any] | None,
    device_id: str,
    hass: HomeAssistant,
) -> bool:
    """Check if the device is an Easywave Receiver or Easywave neo Receiver.

    Receivers are controlled actuators (switches, dimmers, motors, covers).
    They should NOT get button press/release triggers because they do not
    have physical buttons.  State changes are already exposed through
    standard Home Assistant entity triggers (state change, numeric state, etc.).
    """
    if device_info:
        device_type = device_info.get("type") or device_info.get("device_type", "")
        extra_data = device_info.get("extra_data", {})
        device_type = device_type or extra_data.get("type", "")
        if device_type in _RECEIVER_DEVICE_TYPES:
            return True

        # EWneo devices identified by device_type_code (0x03-0x0B)
        device_type_code = device_info.get("device_type_code")
        if device_type_code is None:
            device_type_code = extra_data.get("device_type_code", 0)
        if device_type_code and 0x03 <= device_type_code <= 0x0B:
            return True

        # neo_device flag from DeviceManager
        if device_info.get("neo_device") or extra_data.get("neo_device"):
            return True

        # receiver_kind is only set on ew_receiver / neo receivers
        if device_info.get("receiver_kind") or extra_data.get("receiver_kind"):
            return True

    # Fallback: check entity registry for receiver-type platforms (switch, cover, light)
    # that are NOT transmitter-state entities
    entity_registry = er.async_get(hass)
    device_entities = [
        entry for entry in entity_registry.entities.values()
        if entry.device_id == device_id
    ]

    has_actuator_entity = False
    has_transmitter_entity = False
    for entity in device_entities:
        uid = entity.unique_id or ""
        # Transmitter entities use channel-based unique_id patterns:
        #   _button_ch<N>, _state, _state_ch<N>, _press_state_ch<N>, _last_button
        # Note: _button_ch is specific to transmitters — receivers use _button_a/b/c/d or _toggle
        if any(k in uid for k in ("_button_ch", "_state", "_press", "_last_button")):
            has_transmitter_entity = True
            break
        # Actuator platforms (switch, cover, light, button) indicate a receiver
        if entity.domain in ("switch", "cover", "light", "button"):
            has_actuator_entity = True

    if has_actuator_entity and not has_transmitter_entity:
        return True

    return False


def _get_transmitter_trigger_map(
    hass: HomeAssistant,
    device_info: dict[str, Any] | None,
    serial_number: str,
) -> list[tuple[str, str]]:
    """Return (subtype_label, button_name) pairs for transmitter triggers."""
    if not device_info:
        return [(label, label) for label in ["A", "B", "C", "D"]]

    from .entity_specs import create_entity_specs_for_device
    from .const import BUTTON_LABELS

    specs = create_entity_specs_for_device(serial_number, device_info)

    trigger_map: list[tuple[str, str]] = []
    seen_labels = set()  # Track labels to avoid duplicates

    # State label translation map for 2-button and 3-button modes — use translate()
    def _translate_state(key: str) -> str:
        result = translate(f"state_translated.{key}", hass=hass)
        return result if result != f"state_translated.{key}" else key

    # Prefer transmitter state sensors (2/3-button modes)
    for spec in specs.get("sensor", []):
        if spec.get("sensor_type") == "transmitter_state":
            button_map = spec.get("button_map", {})
            for button_index, label in button_map.items():
                button_name = ["A", "B", "C", "D"][button_index] if button_index in [0, 1, 2, 3] else str(button_index)
                # Translate state keys to readable labels
                translated_label = _translate_state(label)
                # Avoid duplicates: only add if label hasn't been seen yet
                if translated_label not in seen_labels:
                    trigger_map.append((translated_label, button_name))
                    seen_labels.add(translated_label)
        elif spec.get("sensor_type") == "transmitter_press_state":
            button_index = spec.get("button_index")
            if button_index is None:
                continue
            label = spec.get("button") or get_button_label(button_index, hass=hass)
            button_name = ["A", "B", "C", "D"][button_index] if button_index in [0, 1, 2, 3] else str(button_index)
            trigger_map.append((label, button_name))
        elif spec.get("device_class") == "enum" and spec.get("options"):
            # Map option keys to readable labels for last_button sensors (group mode)
            for option in spec.get("options", []):
                if option in ("a", "b", "c", "d"):
                    label = get_button_label(ord(option) - ord('a'), hass=hass)
                else:
                    label = _translate_state(option)
                if (label, option.upper()) not in trigger_map:
                    trigger_map.append((label, option.upper()))

    # Also support transmitter state switches (cover Auf/Zu)
    for spec in specs.get("switch", []):
        if spec.get("switch_type") == "transmitter_state":
            button_map = spec.get("button_map", {})
            for button_index, label in button_map.items():
                button_name = ["A", "B", "C", "D"][button_index] if button_index in [0, 1, 2, 3] else str(button_index)
                if (label, button_name) not in trigger_map:
                    trigger_map.append((label, button_name))

    if trigger_map:
        return trigger_map

    # Fallback to binary sensor labels (1-button modes)
    for spec in specs.get("binary_sensor", []):
        button_index = spec.get("button_index")
        if button_index is None:
            continue
        if device_info.get("operating_type") == "1" and device_info.get("switch_mode") == "permanent":
            label = spec.get("button") or get_button_label(button_index, hass=hass)
        else:
            label = spec.get("button_label") or spec.get("button") or get_button_label(button_index, hass=hass)
        button_name = ["A", "B", "C", "D"][button_index] if button_index in [0, 1, 2, 3] else str(button_index)
        trigger_map.append((label, button_name))

    if trigger_map:
        return trigger_map

    button_count = device_info.get("button_count", 4)
    labels = ["A", "B", "C", "D"][:button_count]
    return [(label, label) for label in labels]


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """Return a list of triggers for Easywave Transmitter devices."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    
    if device is None:
        return []
    
    triggers = []
    
    # Check if this is an EASYWAVE device
    if not any(identifier[0] == DOMAIN for identifier in device.identifiers):
        return []
    
    # Get the serial number from identifiers
    serial_number = None
    for identifier in device.identifiers:
        if identifier[0] == DOMAIN:
            serial_number = identifier[1]
            break
    
    if not serial_number:
        return []
    
    # Gateway device gets connection status triggers
    if serial_number.endswith("_gateway"):
        return [
            {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device_id,
                CONF_TYPE: TRIGGER_TYPE_GATEWAY_CONNECTED,
                CONF_SUBTYPE: "connected",
            },
            {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device_id,
                CONF_TYPE: TRIGGER_TYPE_GATEWAY_DISCONNECTED,
                CONF_SUBTYPE: "disconnected",
            },
        ]
    
    # Try to get device info from coordinator
    device_info = _get_device_info_for_serial(hass, serial_number)
    
    # EWneo Sensor devices use standard HA entity triggers (state change,
    # numeric state above/below threshold, etc.) — no custom device triggers needed.
    if _is_sensor_device(device_info, device_id, hass):
        return []

    # Receiver devices (Easywave Receiver, Easywave neo Receiver) should NOT
    # get button triggers.  They are actuators without physical buttons.
    # Users can use standard HA entity state triggers for state changes.
    if _is_receiver_device(device_info, device_id, hass):
        _LOGGER.debug(
            "Device %s is a receiver — skipping button triggers",
            serial_number[-8:],
        )
        return []
    
    # Determine operating mode from device_info or entity inspection
    operating_type = None
    usage_type = "switch"
    grouping_mode = "single"
    button_count = 4
    switch_mode = "impulse"
    detected_button_type = None  # For 1-button transmitters: which button (A/B/C/D) was learned
    
    if device_info:
        extra_data = device_info.get("extra_data", {})
        operating_type = device_info.get("operating_type") or extra_data.get("operating_type")
        usage_type = device_info.get("usage_type") or extra_data.get("usage_type", "switch")
        grouping_mode = device_info.get("grouping_mode") or extra_data.get("grouping_mode", "single")
        # Use explicit None check — button_count could be 0 or other falsy int
        bc = device_info.get("button_count")
        if bc is None:
            bc = extra_data.get("button_count")
        if bc is not None:
            button_count = int(bc)
        switch_mode = device_info.get("switch_mode") or extra_data.get("switch_mode", "impulse")
        detected_button_type = device_info.get("detected_button_type") or extra_data.get("detected_button_type")
    
    # If no operating_type from device_info, infer from registered entities
    if not operating_type:
        entity_registry = er.async_get(hass)
        device_entities = [
            entry for entry in entity_registry.entities.values()
            if entry.device_id == device_id and entry.domain in ("sensor", "binary_sensor")
        ]
        
        for entity in device_entities:
            uid = entity.unique_id or ""
            # Skip battery-related entities
            if "battery" in uid:
                continue
            
            # State sensor/binary_sensor → 2-Tast or 3-Tast
            # unique_id pattern: <reg_id>_state or <reg_id>_state_ch<N>
            if "_state" in uid and "_button" not in uid and "_press" not in uid and "_last_" not in uid:
                # Binary sensor with "opening" device_class → 2-Tast Rollladen
                if entity.domain == "binary_sensor":
                    dev_cls = getattr(entity, "original_device_class", None)
                    if dev_cls is not None and "opening" in str(dev_cls):
                        operating_type = "2"
                        usage_type = "cover"
                    else:
                        operating_type = "2"
                        usage_type = "switch"
                    break
                
                # Enum sensor — check options to distinguish 2-Tast vs 3-Tast
                state = hass.states.get(entity.entity_id)
                if state:
                    options = state.attributes.get("options", [])
                    if "stop" in options:
                        operating_type = "3"
                        usage_type = "cover"
                    elif "up" in options or "down" in options:
                        operating_type = "2"
                        usage_type = "cover"
                    elif "on" in options or "off" in options:
                        operating_type = "2"
                        usage_type = "switch"
                    else:
                        operating_type = "2"
                else:
                    # State not yet available — default to 2-Tast switch
                    operating_type = "2"
                break
            
            # Per-button sensor → 1-Tast Einzeln
            # unique_id pattern: <reg_id>_button_ch<N>
            elif "_button_" in uid and "_last_" not in uid:
                operating_type = "1"
                grouping_mode = "single"
                # Count how many per-button entities actually exist for this device
                button_entities = [
                    e for e in device_entities
                    if "_button_" in (e.unique_id or "") and "_last_" not in (e.unique_id or "")
                    and "battery" not in (e.unique_id or "")
                ]
                if button_entities:
                    button_count = len(button_entities)
                break
            
            # Last-button sensor → 1-Tast Gruppe
            # unique_id pattern: <reg_id>_last_button
            elif "_last_button" in uid:
                operating_type = "1"
                grouping_mode = "group"
                state = hass.states.get(entity.entity_id)
                if state:
                    options = state.attributes.get("options", [])
                    letter_options = [o for o in options if o in ("a", "b", "c", "d")]
                    if letter_options:
                        button_count = len(letter_options)
                    if "released" in options:
                        switch_mode = "impulse"
                    else:
                        switch_mode = "permanent"
                break
    
    # Final fallback: default to 1-button single mode
    if not operating_type:
        operating_type = "1"
    
    _LOGGER.debug(
        "Trigger generation for %s: operating_type=%s, usage_type=%s, grouping_mode=%s, "
        "switch_mode=%s, button_count=%s, detected_button_type=%s, device_info=%s",
        serial_number[-8:], operating_type, usage_type, grouping_mode, switch_mode,
        button_count, detected_button_type,
        "found" if device_info else "NOT FOUND"
    )
    
    # --- Determine which button indices are actually present ---
    # For 1-button transmitters with detected_button_type, only that specific button exists.
    # For multi-button transmitters, buttons 0..button_count-1 exist.
    all_labels = ["A", "B", "C", "D"]
    
    if button_count == 1 and detected_button_type and detected_button_type.upper() in all_labels:
        # 1-button transmitter: only the learned button
        active_indices = [all_labels.index(detected_button_type.upper())]
    else:
        # Multi-button: buttons 0..button_count-1
        active_indices = list(range(min(button_count, 4)))
    
    # --- Trigger-Generierung basierend auf Betriebsmodus ---
    
    if operating_type == "2":
        # 2-Tast-Bedienung: Zustandswechsel Ein/Aus oder Auf/Zu
        if usage_type == "cover":
            state_keys = ["up", "down"]
        else:
            state_keys = ["on", "off"]
        
        for state_key in state_keys:
            triggers.append(
                {
                    CONF_PLATFORM: "device",
                    CONF_DOMAIN: DOMAIN,
                    CONF_DEVICE_ID: device_id,
                    CONF_TYPE: TRIGGER_TYPE_BUTTON_PRESS,
                    CONF_SUBTYPE: state_key,
                }
            )
    
    elif operating_type == "3":
        # 3-Tast-Bedienung: Zustandswechsel Auf/Zu/Stopp
        for state_key in ["up", "down", "stop"]:
            triggers.append(
                {
                    CONF_PLATFORM: "device",
                    CONF_DOMAIN: DOMAIN,
                    CONF_DEVICE_ID: device_id,
                    CONF_TYPE: TRIGGER_TYPE_BUTTON_PRESS,
                    CONF_SUBTYPE: state_key,
                }
            )
    
    elif operating_type == "1":
        if grouping_mode == "group":
            # Gruppenmodus: Trigger pro vorhandener Taste
            for i in active_indices:
                triggers.append(
                    {
                        CONF_PLATFORM: "device",
                        CONF_DOMAIN: DOMAIN,
                        CONF_DEVICE_ID: device_id,
                        CONF_TYPE: TRIGGER_TYPE_BUTTON_PRESS,
                        CONF_SUBTYPE: all_labels[i].lower(),
                    }
                )
            # "Nicht betätigt" / released trigger (only for impulse mode)
            if switch_mode == "impulse":
                triggers.append(
                    {
                        CONF_PLATFORM: "device",
                        CONF_DOMAIN: DOMAIN,
                        CONF_DEVICE_ID: device_id,
                        CONF_TYPE: TRIGGER_TYPE_BUTTON_RELEASE,
                        CONF_SUBTYPE: "released",
                    }
                )
        else:
            # Einzelmodus: "Taste X betätigt" / "Taste X nicht betätigt" pro vorhandener Taste
            for i in active_indices:
                triggers.append(
                    {
                        CONF_PLATFORM: "device",
                        CONF_DOMAIN: DOMAIN,
                        CONF_DEVICE_ID: device_id,
                        CONF_TYPE: TRIGGER_TYPE_CHANNEL_ON,
                        CONF_SUBTYPE: all_labels[i],
                    }
                )
                triggers.append(
                    {
                        CONF_PLATFORM: "device",
                        CONF_DOMAIN: DOMAIN,
                        CONF_DEVICE_ID: device_id,
                        CONF_TYPE: TRIGGER_TYPE_CHANNEL_OFF,
                        CONF_SUBTYPE: all_labels[i],
                    }
                )
    
    _LOGGER.info("Created %d triggers for device %s: %s", len(triggers), serial_number[-8:],
                [(t[CONF_TYPE], t[CONF_SUBTYPE]) for t in triggers])
    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger to listen for button events."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(config[CONF_DEVICE_ID])
    
    if device is None:
        _LOGGER.error("Device not found: %s", config[CONF_DEVICE_ID])
        return lambda: None
    
    # Get the serial number from identifiers
    serial_number = None
    for identifier in device.identifiers:
        if identifier[0] == DOMAIN:
            serial_number = identifier[1]
            break
    
    if not serial_number:
        _LOGGER.error("No serial number found for device: %s", config[CONF_DEVICE_ID])
        return lambda: None
    
    trigger_type = config[CONF_TYPE]
    subtype_label = config.get(CONF_SUBTYPE, "")
    
    # Handle gateway connection triggers
    if trigger_type in (TRIGGER_TYPE_GATEWAY_CONNECTED, TRIGGER_TYPE_GATEWAY_DISCONNECTED):
        event_type = EVENT_GATEWAY_CONNECTED if trigger_type == TRIGGER_TYPE_GATEWAY_CONNECTED else EVENT_GATEWAY_DISCONNECTED
        
        event_config = event_trigger.TRIGGER_SCHEMA(
            {
                event_trigger.CONF_PLATFORM: "event",
                event_trigger.CONF_EVENT_TYPE: event_type,
            }
        )
        
        _LOGGER.info(
            "Attaching gateway trigger: type=%s, event=%s",
            trigger_type, event_type
        )
        
        return await event_trigger.async_attach_trigger(
            hass, event_config, action, trigger_info
        )
    
    # Handle transmitter button triggers
    
    # Map trigger types to event types (RX11 grundfunktionen)
    event_type_map = {
        TRIGGER_TYPE_BUTTON_PRESS: "easywave_button_press",        # Taste gedrückt / Zustandswechsel
        TRIGGER_TYPE_BUTTON_RELEASE: "easywave_button_release",    # Taste losgelassen
        TRIGGER_TYPE_CHANNEL_ON: "easywave_button_press",          # Channel ON = press
        TRIGGER_TYPE_CHANNEL_OFF: "easywave_button_release",       # Channel OFF = release
    }
    
    event_type = event_type_map.get(trigger_type, "easywave_button_press")
    
    # Determine event matching based on subtype
    # Subtypes are raw keys: "on", "off", "up", "down", "stop", "released", "a"-"d", "A"-"D"
    
    if subtype_label in ("on", "off", "up", "down", "stop"):
        # State trigger: match by action_label (raw key from coordinator)
        event_match_key = "action_label"
        event_match_value = subtype_label
    elif subtype_label in ("a", "b", "c", "d"):
        # Group mode button trigger: match by action_label
        event_match_key = "action_label"
        event_match_value = subtype_label
    elif subtype_label == "released":
        # Released trigger: match release event for any button
        event_match_key = "is_release"
        event_match_value = True
    elif subtype_label in ("A", "B", "C", "D"):
        # Single mode button trigger: match by button index
        event_match_key = "button"
        event_match_value = ["A", "B", "C", "D"].index(subtype_label)
    else:
        event_match_key = "action_label"
        event_match_value = subtype_label

    # Create event config that matches events from coordinator
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: event_type,
            event_trigger.CONF_EVENT_DATA: {
                event_match_key: event_match_value,
            },
        }
    )
    
    _LOGGER.info(
        "Attaching trigger for device %s: type=%s, button=%s, event=%s",
        serial_number[-8:], trigger_type, subtype_label, event_type
    )
    
    # We need to filter events by serial number in the action
    # Since event_trigger doesn't support partial matching, we wrap the action
    original_action = action
    
    async def filtered_action(run_variables: dict[str, Any], context=None) -> None:
        """Filter action to only trigger for matching device."""
        trigger_data = run_variables.get("trigger", {})
        event_data = trigger_data.get("event", {}).data if hasattr(trigger_data.get("event", {}), "data") else {}
        
        # Get device_id from event
        event_device_id = event_data.get("device_id", "")
        
        # Check if this event is for our device (compare last 8 chars)
        if serial_number[-8:] == event_device_id[-8:] if len(event_device_id) >= 8 else serial_number.endswith(event_device_id):
            await original_action(run_variables, context)
        else:
            _LOGGER.debug(
                "Ignoring event for device %s (expected %s)",
                event_device_id[-8:] if event_device_id else "unknown",
                serial_number[-8:]
            )
    
    return await event_trigger.async_attach_trigger(
        hass, event_config, filtered_action, trigger_info
    )


async def async_get_trigger_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """Return trigger capabilities (no additional options needed)."""
    return {}
