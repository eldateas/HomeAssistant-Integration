"""Konstanten für die EASYWAVE Integration."""
from __future__ import annotations

import datetime
from typing import Final

# Domain und Integration Info
DOMAIN: Final = "easywave"
INTEGRATION_NAME: Final = "Home Assistant Integration for EASYWAVE devices"

# Documentation URL Base
DOCS_URL_BASE: Final = "https://github.com/eldateas/HomeAssistant-Integration/tree/prod/custom_components/easywave/docs"

# ═══════════════════════════════════════════════════════════════════════════
# USB Device Registry — SINGLE SOURCE OF TRUTH
# ═══════════════════════════════════════════════════════════════════════════
# Add a new USB stick here and it will automatically be:
#   • discovered by the config flow
#   • matched at startup & reconnect
#   • shown with the correct manufacturer / product name
#   • listed in manifest.json (must be added there manually once)
#
# Key:   (VID, PID)  — both as int
# Value: {"manufacturer": "…", "product": "…"}
# ═══════════════════════════════════════════════════════════════════════════
USB_DEVICE_NAMES: Final = {
    (0x155A, 0x1014): {
        "manufacturer": "ELDAT EaS GmbH",
        "product": "RX11 USB Transceiver",
    },
    # (0x155A, 0x1015): {
    #     "manufacturer": "ELDAT EaS GmbH",
    #     "product": "RX21 USB Transceiver",
    # },
}

# Derived set of all supported (VID, PID) tuples — used for device scanning.
SUPPORTED_USB_IDS: Final = frozenset(USB_DEVICE_NAMES.keys())

# Legacy aliases — kept for existing imports but now derived from the table.
EASYWAVE_VID: Final = 0x155A
EASYWAVE_PIDS: Final = sorted({pid for _, pid in SUPPORTED_USB_IDS})


def is_supported_usb_device(vid: int | None, pid: int | None) -> bool:
    """Return True when the VID/PID pair belongs to a supported stick."""
    return (vid, pid) in SUPPORTED_USB_IDS


def usb_device_name(vid: int | None, pid: int | None) -> tuple[str, str]:
    """Return (manufacturer, product) for a VID/PID pair.

    Falls back to generic strings when the combination is unknown.
    """
    entry = USB_DEVICE_NAMES.get((vid, pid))
    if entry:
        return entry["manufacturer"], entry["product"]
    return "ELDAT EaS GmbH", "Unknown Easywave Device"

# Config Entry Keys
CONF_DEVICE_PATH: Final = "device_path"  # Legacy: optional fallback
CONF_DEVICE_NAME: Final = "device_name"
CONF_USB_VID: Final = "usb_vid"  # Primary: USB Vendor ID
CONF_USB_PID: Final = "usb_pid"  # Primary: USB Product ID
CONF_USB_SERIAL_NUMBER: Final = "usb_serial_number"  # Primary: USB Serial Number
CONF_USB_MANUFACTURER: Final = "usb_manufacturer"  # Display: Manufacturer name
CONF_USB_PRODUCT: Final = "usb_product"  # Display: Product name
CONF_FW_VERSION: Final = "fw_version"  # Display: Firmware version
CONF_HW_VERSION: Final = "hw_version"  # Display: Hardware version
# CONF_AUTO_DISCOVERY removed - auto-discovery feature disabled
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_TRANSCEIVER_TYPE: Final = "transceiver_type"

# Default Values
DEFAULT_SCAN_INTERVAL: Final = 5
DEFAULT_DEVICE_NAME: Final = "RX11 USB Transceiver"

# USB und Device Settings
USB_DEVICE_PATH: Final = "device_path"
DEVICE_SCAN_INTERVAL: Final = datetime.timedelta(seconds=5)
SERIAL_NUMBER_LENGTH: Final = 16

# Device Types (basierend auf RxModule.h)
DEVICE_TYPES: Final = {
    # EWB (EasyWave Bidi) Types
    0x01: "ewneo_bidi_transmitter",      # EWB_DT_BIDI_TR
    0x03: "ewneo_switch",                # EWB_DT_SWITCH
    0x04: "ewneo_dimmer",                # EWB_DT_DIMMER
    0x05: "ewneo_motor",                 # EWB_DT_MOTOR
    0x06: "ewneo_dual_switch",           # EWB_DT_DUAL_SWITCH
    0x07: "ewneo_quad_switch",           # EWB_DT_QUAD_SWITCH
    0x08: "ewneo_dual_motor",            # EWB_DT_DUAL_MOTOR
    0x09: "ewneo_quad_motor",            # EWB_DT_QUAD_MOTOR
    0x0A: "ewb_part_switch",           # EWB_DT_PART_SWITCH
    0x0B: "ewb_part_motor",            # EWB_DT_PART_MOTOR
    # EW (EasyWave) Types
    0x10: "ew_receiver",               # EW_RECEIVER
    0x11: "ew_transmitter",            # EW_TRANSMITTER
    0x12: "ew_transmitter_part",       # EW_TRANSMITTER_PART
    0x13: "ew_sensor",                 # EW_SENSOR
    0x14: "ew_sensor_part",            # EW_SENSOR_PART
    # SEC (Secwave) Types
    0x21: "sec_receiver",              # SEC_RECEIVER
    0x22: "sec_transmitter",           # SEC_TRANSMITTER
    # Virtual/Unknown
    0x00: "unknown",                   # EMPTY_TYPE
}

# Central mapping for device_type_code to device_type string (for EWneo devices)
DEVICE_TYPE_CODE_MAP: Final = {
    0x03: "ewneo_switch",
    0x04: "ewneo_dimmer",
    0x05: "ewneo_motor",
    0x06: "ewneo_dual_switch",
    0x07: "ewneo_quad_switch",
    0x08: "ewneo_dual_motor",
    0x09: "ewneo_quad_motor",
}

# Central mapping for device_type_code to translation key for model names
# Use translate() from translations.py to get localized model names
EWNEO_MODEL_TRANSLATION_KEYS: Final = {
    0x03: "ewneo.switch",
    0x04: "ewneo.dimmer",
    0x05: "ewneo.motor",
    0x06: "ewneo.dual_switch",
    0x07: "ewneo.quad_switch",
    0x08: "ewneo.dual_motor",
    0x09: "ewneo.quad_motor",
}

# NEO Subtypes für Telegram Mapping
NEO_TELEGRAM_TYPE_MAPPING: Final = {
    0x30: "neo_switch",
    0x31: "neo_dimmer", 
    0x32: "neo_motor",
    0x33: "neo_sensor_temperature",
    0x34: "neo_sensor_humidity",
    0x35: "neo_sensor_motion",
    0x36: "neo_sensor_door"
}

# Info Types (basierend auf RxModule.h)
TM_IT_EASW_RELEASE: Final = 0x00     # Easywave transmitter button release
TM_IT_EASW_PUSH: Final = 0x01        # Easywave transmitter button push and hold
TM_IT_SENSOR_DATA: Final = 0x02       # Sensor data message
TM_IT_EWBIDI_STATE: Final = 0x03      # Easywave Bidi receiver state change
TM_IT_EWBIDI_ABORT: Final = 0x40      # Easywave Bidi aborted learn/removal
TM_IT_EWBIDI_ADD_TR: Final = 0x41     # Easywave Bidi learned transmitter
TM_IT_EWBIDI_RMV_TR: Final = 0x42     # Easywave Bidi removed transmitter
TM_IT_EWBIDI_LN_T: Final = 0xF0       # Easywave Bidi learn ack for transmitter
TM_IT_EWBIDI_CHG_T: Final = 0xF1      # Easywave Bidi receiver change state
TM_IT_EWBIDI_QUR_T: Final = 0xF2      # Easywave Bidi receiver query state

# Button Definitions (basierend auf RxModule.h)
TM_BUTTON_MASK: Final = 3
TM_BUTTON_A: Final = 0
TM_BUTTON_B: Final = 1
TM_BUTTON_C: Final = 2
TM_BUTTON_D: Final = 3

# Button Functions
TM_BUTTON_FUNC_MASK: Final = 0xFC
TM_BUTTON_DEFAULT: Final = 0x00
TM_BUTTON_LRN_DEL: Final = 0x04
TM_BUTTON_LRN_ADD: Final = 0x08
TM_BUTTON_LRN_RESET: Final = 0x0C
TM_BUTTON_LRN_TIMER: Final = 0x10
TM_BUTTON_HOLD: Final = 0x14
TM_BUTTON_RELEASE: Final = 0x18
TM_BUTTON_LOWBAT: Final = 0x80

# Error Codes (basierend auf RxModule.h)
ERR_SUCCESS: Final = 0x00
ERR_CANCELED: Final = 0x01
ERR_OUT_OF_QUEUE: Final = 0x02
ERR_INVALID_REQUEST: Final = 0x03
ERR_SIZE_MISMATCH: Final = 0x04
ERR_INVALID_PARAMETER: Final = 0x05
ERR_INCOMPLETE_FW: Final = 0x06
ERR_RF_TIMEOUT: Final = 0x07
ERR_INVALID_SERIAL: Final = 0x08
ERR_SUPERSEDED: Final = 0x09
ERR_INCOMPAT_FW: Final = 0x0A
ERR_SERIAL_FILTER: Final = 0x0B
ERR_FILTER_OUT_OF_MEM: Final = 0x0C
ERR_INVALID_SEC_REPLY: Final = 0x0D
ERR_TOO_LATE: Final = 0x0E
ERR_FAILSTATE: Final = 0xFF

# Services
SERVICE_ADD_DEVICE: Final = "add_device"
SERVICE_REMOVE_DEVICE: Final = "remove_device"
SERVICE_REMOVE_DEVICE_BY_ID: Final = "remove_device_by_id"
SERVICE_REMOVE_ALL_DEVICES: Final = "remove_all_devices"
SERVICE_LIST_DEVICES: Final = "list_devices"
SERVICE_LIST_REMOVABLE_DEVICES: Final = "list_removable_devices"
# Removed: SERVICE_CLEAR_BLACKLIST - use whitelist-based approach instead
SERVICE_SCAN_DEVICES: Final = "scan_devices"
SERVICE_LEARN_DEVICE: Final = "learn_device"
SERVICE_SEND_COMMAND: Final = "send_command"
SERVICE_CONNECT_USB: Final = "connect_usb"
SERVICE_EXPORT_DEVICES: Final = "export_devices"
SERVICE_IMPORT_DEVICES: Final = "import_devices"
SERVICE_BACKUP_DEVICES: Final = "backup_devices"

# Configuration File
DEVICES_CONFIG_FILE: Final = "easywave_devices.json"
DEVICES_BACKUP_FILE: Final = "easywave_devices_backup.json"
CONFIG_VERSION: Final = "1.0"

# Entity Categories
ENTITY_CATEGORY_CONFIG: Final = "config"
ENTITY_CATEGORY_DIAGNOSTIC: Final = "diagnostic"

# Attributes
ATTR_DEVICE_TYPE: Final = "device_type"
ATTR_DEVICE_ID: Final = "device_id"
ATTR_SERIAL_NUMBER: Final = "serial_number"
ATTR_DEVICE_NAME: Final = "device_name"
ATTR_BUTTON: Final = "button"
ATTR_COMMAND: Final = "command"
ATTR_TIMEOUT: Final = "timeout"
ATTR_FORCE: Final = "force"
# Removed: ATTR_BLACKLIST - use whitelist-based approach instead
ATTR_INFO_TYPE: Final = "info_type"
ATTR_CHANNELS: Final = "channels"
ATTR_BATTERY_LEVEL: Final = "battery_level"
ATTR_SIGNAL_STRENGTH: Final = "signal_strength"
ATTR_BACKUP_NAME: Final = "backup_name"
ATTR_INCLUDE_CONFIG: Final = "include_config"
ATTR_DEVICE_FILTER: Final = "device_filter"

# Default Timeouts
DEFAULT_LEARNING_TIMEOUT: Final = 30
DEFAULT_COMMAND_TIMEOUT: Final = 5
DEFAULT_RESPONSE_TIMEOUT: Final = 2
LEARNING_TIMEOUT: Final = 180  # Timeout für EWneo Receiver Learning (3 Minuten)

# Device Names und Labels
DEVICE_NAME_PREFIXES: Final = {
    "ew_transmitter": "Easywave Sender",
    "ew_receiver": "Easywave Receiver",
    "ew_temperature_sensor": "EWneo-Sensoren", 
    "ew_humidity_sensor": "EWneo-Sensoren",
    "ew_sensor": "EWneo-Sensoren",
    "ewneo_transceiver": "EW NEO Transceiver",
    "ewneo_switch": "EWneo-Switch",
    "ewneo_dimmer": "EWneo-Dimmer", 
    "ewneo_motor": "EWneo-Motor",
    "ewneo_sensor": "EWneo-Sensoren",
    "ewneo_bidi_transmitter": "Easywave Sender",
    "ewneo_switch": "EWneo-Switch",
    "ewneo_dimmer": "EWneo-Dimmer",
    "ewneo_motor": "EWneo-Motor",
    "ewneo_dual_switch": "EWneo-DualSwitch",
    "ewneo_quad_switch": "EWneo-QuadSwitch",
    "ewneo_dual_motor": "EWneo-DualMotor",
    "ewneo_quad_motor": "EWneo-QuadMotor"
}

# Button Labels (translation keys - actual labels come from translations.py)
BUTTON_LABELS_KEYS: Final = {
    TM_BUTTON_A: "button.a",
    TM_BUTTON_B: "button.b", 
    TM_BUTTON_C: "button.c",
    TM_BUTTON_D: "button.d"
}

# Legacy BUTTON_LABELS for backwards compatibility (German defaults)
# New code should use translations.get_button_label() instead
BUTTON_LABELS: Final = {
    TM_BUTTON_A: "Taste A",
    TM_BUTTON_B: "Taste B", 
    TM_BUTTON_C: "Taste C",
    TM_BUTTON_D: "Taste D"
}

# Device Icons
DEVICE_ICONS: Final = {
    "ew_transmitter": "mdi:radio-handheld",
    "ew_receiver": "mdi:radio",
    "ew_temperature_sensor": "mdi:thermometer",
    "ew_humidity_sensor": "mdi:water-percent",
    "ew_sensor": "mdi:motion-sensor",
    "ewneo_transceiver": "mdi:radio-tower",
    "ewneo_switch": "mdi:light-switch",
    "ewneo_dimmer": "mdi:brightness-6",
    "ewneo_motor": "mdi:motor",
    "ewneo_sensor": "mdi:sensor",
    "ewneo_bidi_transmitter": "mdi:radio-handheld",
    "ewneo_switch": "mdi:light-switch",
    "ewneo_dimmer": "mdi:brightness-6",
    "ewneo_motor": "mdi:motor",
    "ewneo_dual_switch": "mdi:light-switch",
    "ewneo_quad_switch": "mdi:light-switch",
    "ewneo_dual_motor": "mdi:motor",
    "ewneo_quad_motor": "mdi:motor"
}

# Platform Specific Settings
SCAN_INTERVAL_SENSORS: Final = datetime.timedelta(seconds=60)
SCAN_INTERVAL_SWITCHES: Final = datetime.timedelta(seconds=30)

# States
STATE_UNKNOWN: Final = "unknown"
STATE_UNAVAILABLE: Final = "unavailable"
STATE_ON: Final = "on"
STATE_OFF: Final = "off"

# Units
UNIT_CELSIUS: Final = "°C"
UNIT_FAHRENHEIT: Final = "°F"
UNIT_PERCENT: Final = "%"
UNIT_DBM: Final = "dBm"

# Coordinator Events
EVENT_DEVICE_ADDED: Final = "easywave_device_added"
EVENT_DEVICE_REMOVED: Final = "easywave_device_removed"
EVENT_DEVICE_UPDATED: Final = "easywave_device_updated"
EVENT_DEVICE_STATE_UPDATE: Final = "easywave_device_state_update"
EVENT_TELEGRAM_RECEIVED: Final = "easywave_telegram_received"
EVENT_SENSOR_UPDATE: Final = "easywave_sensor_update"
EVENT_SENSOR_ADDED: Final = "easywave_sensor_added"
EVENT_FORCE_CREATE: Final = "easywave_force_create"

# Gateway Connection Events
EVENT_GATEWAY_CONNECTED: Final = "easywave_gateway_connected"
EVENT_GATEWAY_DISCONNECTED: Final = "easywave_gateway_disconnected"
EVENT_GATEWAY_STATUS_CHANGED: Final = "easywave_gateway_status_changed"

# Button Events (basierend auf RX11 EWB_RCV Grundfunktionen)
# Nur diese drei Events werden gefeuert:
# 1. easywave_button_press - Taste gedrückt (vom RX11)
# 2. easywave_button_release - Taste losgelassen (vom RX11)
# 3. easywave_button_hold - Taste gedrückt halten (emuliert, > 1 Sekunde)