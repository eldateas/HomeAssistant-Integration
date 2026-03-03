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
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    EVENT_GATEWAY_CONNECTED,
    EVENT_GATEWAY_DISCONNECTED,
    EVENT_GATEWAY_STATUS_CHANGED,
)

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


def _get_device_info_for_serial(hass: HomeAssistant, serial_number: str) -> dict[str, Any] | None:
    """Find device info in coordinator by serial number."""
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
            # First try exact match
            if dev_serial == serial_number:
                return dev_info
            # Fallback: match by last 8 characters
            if len(dev_serial) >= 8 and len(serial_number) >= 8:
                if dev_serial[-8:] == serial_number[-8:]:
                    return dev_info

    _LOGGER.warning("⚠️ No device info found for serial %s", serial_number)
    return None


def _get_transmitter_trigger_map(
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

    # State label translation map for 2-button and 3-button modes
    state_label_map = {
        "on": "Ein",
        "off": "Aus",
        "up": "Auf",
        "down": "Zu",
        "stop": "Stopp",
        "released": "Nicht betätigt",
    }

    # Prefer transmitter state sensors (2/3-button modes)
    for spec in specs.get("sensor", []):
        if spec.get("sensor_type") == "transmitter_state":
            button_map = spec.get("button_map", {})
            for button_index, label in button_map.items():
                button_name = ["A", "B", "C", "D"][button_index] if button_index in [0, 1, 2, 3] else str(button_index)
                # Translate state keys to readable labels
                translated_label = state_label_map.get(label, label)
                # Avoid duplicates: only add if label hasn't been seen yet
                if translated_label not in seen_labels:
                    trigger_map.append((translated_label, button_name))
                    seen_labels.add(translated_label)
        elif spec.get("sensor_type") == "transmitter_press_state":
            button_index = spec.get("button_index")
            if button_index is None:
                continue
            label = spec.get("button") or BUTTON_LABELS.get(button_index, f"Taste {button_index + 1}")
            button_name = ["A", "B", "C", "D"][button_index] if button_index in [0, 1, 2, 3] else str(button_index)
            trigger_map.append((label, button_name))
        elif spec.get("device_class") == "enum" and spec.get("options"):
            # Map option keys to readable labels for last_button sensors (group mode)
            option_label_map = {
                "a": BUTTON_LABELS.get(0, "Taste A"),
                "b": BUTTON_LABELS.get(1, "Taste B"),
                "c": BUTTON_LABELS.get(2, "Taste C"),
                "d": BUTTON_LABELS.get(3, "Taste D"),
                "released": "Nicht betätigt",
                "off": "Aus",
            }
            for option in spec.get("options", []):
                label = option_label_map.get(option, option)
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
            label = spec.get("button") or BUTTON_LABELS.get(button_index, f"Taste {button_index + 1}")
        else:
            label = spec.get("button_label") or spec.get("button") or BUTTON_LABELS.get(button_index, f"Taste {button_index + 1}")
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
    
    device_info = _get_device_info_for_serial(hass, serial_number)
    if not device_info:
        _LOGGER.warning("No device info found for serial %s, cannot generate triggers", serial_number)
        return []
    
    trigger_map = _get_transmitter_trigger_map(device_info, serial_number)
    _LOGGER.debug("Generated trigger map for %s: %s", serial_number, trigger_map)
    
    # Determine which event types are relevant based on operating_type and usage_type
    operating_type = device_info.get("operating_type", "1")
    usage_type = device_info.get("usage_type", "permanent")
    switch_mode = device_info.get("switch_mode", "impulse")
    grouping_mode = device_info.get("grouping_mode", "single")

    # Trigger-Generierung basierend auf Betriebsmodus:
    # - 3-Tast: Zustandswechsel zu "AUF", "STOPP", "ZU"
    # - 2-Tast: Zustandswechsel zu "EIN"/"AUS" bzw. "AUF"/"ZU"
    # - 1-Tast Gruppe: Zustand = Taste XY
    # - 1-Tast Einzel: "betätigt"/"losgelassen" (Press/Release per Button)
    
    if operating_type == "2" or operating_type == "3":
        # 2-Tast und 3-Tast: Trigger für Zustandswechsel
        # trigger_map enthält bereits die Zustände (Ein/Aus, Auf/Zu, Auf/Zu/Stopp)
        for subtype_label, _button_name in trigger_map:
            triggers.append(
                {
                    CONF_PLATFORM: "device",
                    CONF_DOMAIN: DOMAIN,
                    CONF_DEVICE_ID: device_id,
                    CONF_TYPE: TRIGGER_TYPE_BUTTON_PRESS,
                    CONF_SUBTYPE: subtype_label,
                }
            )
    
    elif operating_type == "1":
        # 1-Tast-Bedienung: Abhängig von Modus
        if grouping_mode == "single":
            # Einzelmodus: Trigger pro Taste (betätigt/losgelassen)
            button_labels = ["A", "B", "C", "D"]
            button_count = device_info.get("button_count", 4)
            for i in range(button_count):
                button_label = button_labels[i]
                # betätigt Trigger
                triggers.append(
                    {
                        CONF_PLATFORM: "device",
                        CONF_DOMAIN: DOMAIN,
                        CONF_DEVICE_ID: device_id,
                        CONF_TYPE: TRIGGER_TYPE_CHANNEL_ON,
                        CONF_SUBTYPE: button_label,
                    }
                )
                # losgelassen Trigger (immer, für Impuls und Dauer)
                triggers.append(
                    {
                        CONF_PLATFORM: "device",
                        CONF_DOMAIN: DOMAIN,
                        CONF_DEVICE_ID: device_id,
                        CONF_TYPE: TRIGGER_TYPE_CHANNEL_OFF,
                        CONF_SUBTYPE: button_label,
                    }
                )
        
        elif grouping_mode == "group":
            # Gruppenmodus: Zustand = Taste XY (trigger_map enthält Tastenlabels)
            for subtype_label, _button_name in trigger_map:
                triggers.append(
                    {
                        CONF_PLATFORM: "device",
                        CONF_DOMAIN: DOMAIN,
                        CONF_DEVICE_ID: device_id,
                        CONF_TYPE: TRIGGER_TYPE_BUTTON_PRESS,
                        CONF_SUBTYPE: subtype_label,
                    }
                )
    
    _LOGGER.debug("Created %d triggers for device %s", len(triggers), device_id)
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
    device_info = _get_device_info_for_serial(hass, serial_number)
    trigger_map = dict(_get_transmitter_trigger_map(device_info, serial_number))
    button_name = trigger_map.get(subtype_label, subtype_label)
    
    # Map trigger types to event types (RX11 grundfunktionen)
    event_type_map = {
        TRIGGER_TYPE_BUTTON_PRESS: "easywave_button_press",        # Taste gedrückt / Zustandswechsel
        TRIGGER_TYPE_BUTTON_RELEASE: "easywave_button_release",    # Taste losgelassen
        TRIGGER_TYPE_CHANNEL_ON: "easywave_button_press",          # Channel ON = press
        TRIGGER_TYPE_CHANNEL_OFF: "easywave_button_release",       # Channel OFF = release
    }
    
    event_type = event_type_map.get(trigger_type, "easywave_button_press")
    
    # Reverse translation map: translated label -> raw event value
    label_to_event_value = {
        "Ein": "on",
        "Aus": "off",
        "Auf": "up",
        "Zu": "down",
        "Stopp": "stop",
        "Nicht betätigt": "released",
        "Taste A": "a",
        "Taste B": "b",
        "Taste C": "c",
        "Taste D": "d",
    }
    
    event_match_key = "button_name"
    event_match_value = button_name
    
    # For translated state labels (2/3-button and group mode), match by action_label with raw value
    if subtype_label in label_to_event_value:
        event_match_key = "action_label"
        event_match_value = label_to_event_value[subtype_label]
    elif subtype_label != button_name and button_name in ["A", "B", "C", "D"]:
        # For group mode triggers with translated labels (e.g., "Taste A")
        event_match_key = "action_label"
        event_match_value = button_name.lower()  # "A" -> "a"
    elif subtype_label != button_name:
        event_match_key = "action_label"
        event_match_value = subtype_label

    # For 1-button press/release and channel on/off triggers, match by button index
    if trigger_type in (
        TRIGGER_TYPE_BUTTON_PRESS,
        TRIGGER_TYPE_BUTTON_RELEASE,
        TRIGGER_TYPE_CHANNEL_ON,
        TRIGGER_TYPE_CHANNEL_OFF,
    ):
        if subtype_label in ["A", "B", "C", "D"]:
            event_match_key = "button"
            event_match_value = ["A", "B", "C", "D"].index(subtype_label)

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
