"""Constants for the Easywave integration."""

from datetime import timedelta
from enum import IntFlag
from typing import Final

DOMAIN: Final = "easywave"

# Home Assistant requires integrations to verify that RF hardware is permitted
# in the user's configured country. The RX11 USB Transceiver operates on
# 868 MHz (EU ISM band), which is only allowed in CEPT member countries.
FREQUENCY_868MHZ: Final = "868 MHz"

# Single source of truth for supported USB sticks.
# Adding a new device here is sufficient — config flow and discovery pick it up
# automatically. Also update the `usb` list in manifest.json.
#
# Key:   (VID, PID) as int
# Value: {"manufacturer": str, "product": str, "frequency": str}
USB_DEVICE_NAMES: Final[dict[tuple[int, int], dict[str, str]]] = {
    (0x155A, 0x1014): {
        "manufacturer": "ELDAT",
        "product": "RX11 USB Transceiver",
        "frequency": FREQUENCY_868MHZ,
    },
}

SUPPORTED_USB_IDS: Final = frozenset(USB_DEVICE_NAMES.keys())

# Periodic polling interval for USB device reconnection attempts
DEVICE_SCAN_INTERVAL: Final = timedelta(seconds=30)


CONF_DEVICE_PATH: Final = "device_path"
CONF_USB_VID: Final = "usb_vid"
CONF_USB_PID: Final = "usb_pid"
CONF_USB_SERIAL_NUMBER: Final = "usb_serial_number"
CONF_USB_MANUFACTURER: Final = "usb_manufacturer"
CONF_USB_PRODUCT: Final = "usb_product"

ALLOWED_COUNTRIES_868MHZ: Final = frozenset(
    {
        # EU Member States (CEPT)
        "AT",
        "BE",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "HU",
        "IE",
        "IT",
        "LV",
        "LT",
        "LU",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SK",
        "SI",
        "ES",
        "SE",
        # CEPT Members (non-EU)
        "CH",
        "NO",
        "IS",
        "LI",
        # UK (post-Brexit)
        "GB",
        "UK",
    }
)

FREQUENCY_ALLOWED_COUNTRIES: Final = {
    FREQUENCY_868MHZ: ALLOWED_COUNTRIES_868MHZ,
}


def is_country_allowed_for_frequency(frequency: str, country_code: str | None) -> bool:
    """Check whether a country is permitted to operate on the given frequency."""
    if country_code is None:
        return True

    allowed = FREQUENCY_ALLOWED_COUNTRIES.get(frequency)
    if allowed is None:
        return True

    return country_code.upper() in allowed


def get_frequency_for_pid(pid: int | None) -> str | None:
    """Get frequency band for a supported USB device PID."""
    if pid is None:
        return None
    for (_vid, device_pid), device_info in USB_DEVICE_NAMES.items():
        if device_pid == pid:
            return device_info["frequency"]
    return None


# Event fired for gateway/battery/button state changes (usable in automations).
EVENT_EASYWAVE: Final = f"{DOMAIN}_event"

# Device trigger event types
EVENT_TYPE_BUTTON_PRESS: Final = "button_press"
EVENT_TYPE_BUTTON_RELEASE: Final = "button_release"
EVENT_TYPE_BATTERY_LOW: Final = "battery_low"
EVENT_TYPE_BATTERY_NORMAL: Final = "battery_normal"
EVENT_TYPE_GATEWAY_CONNECTED: Final = "gateway_connected"
EVENT_TYPE_GATEWAY_DISCONNECTED: Final = "gateway_disconnected"

CONF_ENTRY_TYPE: Final = "entry_type"
CONF_DEVICE_TITLE: Final = "title"

SUBENTRY_TYPE_EASYWAVE_TRANSMITTER: Final = "easywave_transmitter"
SUBENTRY_TYPE_EASYWAVE_NEO_SENSOR: Final = "easywave_neo_sensor"
SUBENTRY_TYPE_EASYWAVE_RECEIVER: Final = "easywave_receiver"
SUBENTRY_TYPE_EASYWAVE_NEO_ACTUATOR: Final = "easywave_neo_actuator"

DEVICE_SUBENTRY_TYPES: Final = (
    SUBENTRY_TYPE_EASYWAVE_TRANSMITTER,
    SUBENTRY_TYPE_EASYWAVE_NEO_SENSOR,
    SUBENTRY_TYPE_EASYWAVE_RECEIVER,
    SUBENTRY_TYPE_EASYWAVE_NEO_ACTUATOR,
)

ENTRY_TYPE_TRANSMITTER: Final = "transmitter"
ENTRY_TYPE_NEO_SENSOR: Final = "neo_sensor"
ENTRY_TYPE_RECEIVER: Final = "receiver"
ENTRY_TYPE_NEO_ACTUATOR: Final = "neo_actuator"

ENTRY_TYPE_TO_SUBENTRY_TYPE: Final = {
    ENTRY_TYPE_TRANSMITTER: SUBENTRY_TYPE_EASYWAVE_TRANSMITTER,
    ENTRY_TYPE_NEO_SENSOR: SUBENTRY_TYPE_EASYWAVE_NEO_SENSOR,
    ENTRY_TYPE_RECEIVER: SUBENTRY_TYPE_EASYWAVE_RECEIVER,
    ENTRY_TYPE_NEO_ACTUATOR: SUBENTRY_TYPE_EASYWAVE_NEO_ACTUATOR,
}

BUCKET_SUBENTRY_TITLES: Final = {
    SUBENTRY_TYPE_EASYWAVE_TRANSMITTER: "Easywave transmitter",
    SUBENTRY_TYPE_EASYWAVE_NEO_SENSOR: "Easywave neo sensor",
    SUBENTRY_TYPE_EASYWAVE_RECEIVER: "Easywave receiver",
    SUBENTRY_TYPE_EASYWAVE_NEO_ACTUATOR: "Easywave neo receiver",
}


def bucket_subentry_unique_id(config_entry_id: str, subentry_type: str) -> str:
    """Return the fixed unique id for a device-type bucket subentry."""
    return f"{config_entry_id}_{subentry_type}"


CONF_TRANSMITTER_SERIAL: Final = "transmitter_serial"

CONF_SENSOR_SERIAL: Final = "sensor_serial"
CONF_SENSOR_CAPABILITIES: Final = "sensor_capabilities"

CONF_OPERATING_TYPE: Final = "operating_type"
CONF_BUTTON_COUNT: Final = "button_count"
CONF_GROUPING_MODE: Final = "grouping_mode"
CONF_SWITCH_MODE: Final = "switch_mode"
CONF_USAGE_TYPE: Final = "usage_type"
CONF_COVER_MODE: Final = "cover_mode"
CONF_DETECTED_BUTTON_TYPE: Final = "detected_button_type"

CONF_RECEIVER_SERIAL: Final = "receiver_serial"
CONF_RX11_INDEX: Final = "rx11_index"
CONF_RECEIVER_KIND: Final = "receiver_kind"
CONF_OPERATING_MODE: Final = "operating_mode"

CONF_ACTUATOR_SERIAL: Final = "actuator_serial"
CONF_EWNEO_INDEX: Final = "ewneo_index"
CONF_GATEWAY_SERIAL: Final = "gateway_serial"
CONF_DEVICE_TYPE_CODE: Final = "device_type_code"
CONF_CHANNELS: Final = "channels"

# Grouping modes for transmitters
TRANSMITTER_GROUPING_GROUP: Final = "group"
TRANSMITTER_GROUPING_SINGLE: Final = "single"
TRANSMITTER_GROUPING_DUAL: Final = "dual"
TRANSMITTER_GROUPING_COVER: Final = "cover"

# Switch modes for transmitters
TRANSMITTER_SWITCH_IMPULSE: Final = "impulse"
TRANSMITTER_SWITCH_PERMANENT: Final = "permanent"
TRANSMITTER_SWITCH_SWITCH: Final = "switch"
TRANSMITTER_SWITCH_COVER: Final = "cover"

# Receiver kinds (EW basic receivers driven from HA)
RECEIVER_KIND_IMPULSE: Final = "impulse"
RECEIVER_KIND_SWITCH_2BUTTON: Final = "switch_2button"
RECEIVER_KIND_COVER_2BUTTON: Final = "cover_2button"
RECEIVER_KIND_MOTOR_3BUTTON: Final = "motor_3button"
RECEIVER_KIND_HEATING_COOLING: Final = "heating_cooling"
RECEIVER_KIND_UNIVERSAL_4BUTTON: Final = "universal_4button"

RECEIVER_KINDS: Final = (
    RECEIVER_KIND_IMPULSE,
    RECEIVER_KIND_SWITCH_2BUTTON,
    RECEIVER_KIND_COVER_2BUTTON,
    RECEIVER_KIND_MOTOR_3BUTTON,
    RECEIVER_KIND_HEATING_COOLING,
    RECEIVER_KIND_UNIVERSAL_4BUTTON,
)

# EWneo device type codes (match easywave_home_control.DeviceType)
DEVICE_TYPE_CODE_SWITCH: Final = 0x03
DEVICE_TYPE_CODE_DIMMER: Final = 0x04
DEVICE_TYPE_CODE_MOTOR: Final = 0x05
DEVICE_TYPE_CODE_DUAL_SWITCH: Final = 0x06
DEVICE_TYPE_CODE_QUAD_SWITCH: Final = 0x07
DEVICE_TYPE_CODE_DUAL_MOTOR: Final = 0x08
DEVICE_TYPE_CODE_QUAD_MOTOR: Final = 0x09

DEVICE_TYPE_CODE_TO_PREFIX: Final = {
    DEVICE_TYPE_CODE_SWITCH: "ewneo_switch",
    DEVICE_TYPE_CODE_DIMMER: "ewneo_dimmer",
    DEVICE_TYPE_CODE_MOTOR: "ewneo_motor",
    DEVICE_TYPE_CODE_DUAL_SWITCH: "ewneo_switch",
    DEVICE_TYPE_CODE_QUAD_SWITCH: "ewneo_switch",
    DEVICE_TYPE_CODE_DUAL_MOTOR: "ewneo_motor",
    DEVICE_TYPE_CODE_QUAD_MOTOR: "ewneo_motor",
}

DEVICE_TYPE_CODE_TO_CHANNELS: Final = {
    DEVICE_TYPE_CODE_SWITCH: 1,
    DEVICE_TYPE_CODE_DIMMER: 1,
    DEVICE_TYPE_CODE_MOTOR: 1,
    DEVICE_TYPE_CODE_DUAL_SWITCH: 2,
    DEVICE_TYPE_CODE_QUAD_SWITCH: 4,
    DEVICE_TYPE_CODE_DUAL_MOTOR: 2,
    DEVICE_TYPE_CODE_QUAD_MOTOR: 4,
}

# HACS legacy device_type strings → type codes (migration)
HACS_ACTUATOR_TYPE_TO_CODE: Final = {
    "ewneo_switch": DEVICE_TYPE_CODE_SWITCH,
    "ewneo_dimmer": DEVICE_TYPE_CODE_DIMMER,
    "ewneo_motor": DEVICE_TYPE_CODE_MOTOR,
    "ewneo_dual_switch": DEVICE_TYPE_CODE_DUAL_SWITCH,
    "ewneo_quad_switch": DEVICE_TYPE_CODE_QUAD_SWITCH,
    "ewneo_dual_motor": DEVICE_TYPE_CODE_DUAL_MOTOR,
    "ewneo_quad_motor": DEVICE_TYPE_CODE_QUAD_MOTOR,
}

BUTTON_A: Final = 0
BUTTON_B: Final = 1
BUTTON_C: Final = 2
BUTTON_D: Final = 3

BUTTON_LETTERS: Final = ("a", "b", "c", "d")


class EasywaveTransmitterFeature(IntFlag):
    """Feature flags for transmitter last-button sensor trigger filtering."""

    BUTTON_A = 1
    BUTTON_B = 2
    BUTTON_C = 4
    BUTTON_D = 8
    BUTTON_RELEASE = 16


_BUTTON_FEATURE_BY_INDEX: Final = (
    EasywaveTransmitterFeature.BUTTON_A,
    EasywaveTransmitterFeature.BUTTON_B,
    EasywaveTransmitterFeature.BUTTON_C,
    EasywaveTransmitterFeature.BUTTON_D,
)


def transmitter_trigger_features(button_count: int, switch_mode: str) -> int:
    """Return supported trigger feature flags for a group-mode transmitter."""
    features = EasywaveTransmitterFeature(0)
    for index in range(min(button_count, 4)):
        features |= _BUTTON_FEATURE_BY_INDEX[index]
    if switch_mode == TRANSMITTER_SWITCH_IMPULSE:
        features |= EasywaveTransmitterFeature.BUTTON_RELEASE
    return features.value


class EasywaveGatewayFeature(IntFlag):
    """Feature flag for the RX11 gateway status sensor trigger filtering."""

    GATEWAY_STATUS = 32


LEARNING_TIMEOUT: Final = 30  # seconds
EWB_LEARNING_TIMEOUT: Final = 180  # seconds (EWneo join)


def normalize_serial_hex(serial: str | bytes) -> str:
    """Normalize a serial number to lowercase hex without separators."""
    if isinstance(serial, bytes):
        return serial.hex().lower()
    cleaned = (
        str(serial)
        .strip()
        .lower()
        .replace(":", "")
        .replace("-", "")
        .replace(" ", "")
    )
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    return cleaned


def device_id_for_transmitter(serial_hex: str) -> str:
    """Return CORE-compatible transmitter device id."""
    return f"transmitter_{normalize_serial_hex(serial_hex)}"


def device_id_for_neo_sensor(serial_hex: str) -> str:
    """Return CORE-compatible neo sensor device id."""
    return f"neo_sensor_{normalize_serial_hex(serial_hex)}"


def device_id_for_receiver(serial_hex: str) -> str:
    """Return CORE-compatible receiver device id."""
    return f"receiver_{normalize_serial_hex(serial_hex)}"


def device_id_for_neo_actuator(serial_hex: str, device_type_code: int) -> str:
    """Return CORE-compatible neo actuator device id."""
    prefix = DEVICE_TYPE_CODE_TO_PREFIX.get(device_type_code, "ewneo_actuator")
    return f"{prefix}_{normalize_serial_hex(serial_hex)}"
