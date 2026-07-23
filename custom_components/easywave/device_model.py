"""Localized device model strings for the Home Assistant device info panel.

Matches the 0.6.10 ``build_model_description`` wording (DE/EN/FR).
Serial numbers are intentionally not included in DeviceInfo.
"""

from __future__ import annotations

from typing import Any, Final

from .const import (
    CONF_BUTTON_COUNT,
    CONF_DEVICE_TYPE_CODE,
    CONF_GROUPING_MODE,
    CONF_OPERATING_TYPE,
    CONF_RECEIVER_KIND,
    CONF_SWITCH_MODE,
    DEVICE_TYPE_CODE_DIMMER,
    DEVICE_TYPE_CODE_DUAL_MOTOR,
    DEVICE_TYPE_CODE_DUAL_SWITCH,
    DEVICE_TYPE_CODE_MOTOR,
    DEVICE_TYPE_CODE_QUAD_MOTOR,
    DEVICE_TYPE_CODE_QUAD_SWITCH,
    DEVICE_TYPE_CODE_SWITCH,
    RECEIVER_KIND_COVER_2BUTTON,
    RECEIVER_KIND_HEATING_COOLING,
    RECEIVER_KIND_IMPULSE,
    RECEIVER_KIND_MOTOR_3BUTTON,
    RECEIVER_KIND_SWITCH_2BUTTON,
    RECEIVER_KIND_UNIVERSAL_4BUTTON,
    TRANSMITTER_GROUPING_GROUP,
    TRANSMITTER_GROUPING_SINGLE,
    TRANSMITTER_SWITCH_IMPULSE,
    TRANSMITTER_SWITCH_PERMANENT,
)

_DEVICE_INFO: Final[dict[str, dict[str, str]]] = {
    "en": {
        "easywave_transmitter": "Easywave Transmitter",
        "easywave_receiver": "Easywave Receiver",
        "easywave_neo_sensor": "Easywave neo Sensor",
        "operating_type_1": "1-Button Operation",
        "operating_type_2": "2-Button Operation",
        "operating_type_3": "3-Button Operation",
        "grouping_single": "Individual",
        "grouping_group": "Group",
        "switch_impulse": "Impulse",
        "switch_permanent": "Permanent",
        "buttons": "{count} Buttons",
        "buttons_3_or_4": "3 or 4 Buttons",
        "receiver_impulse": "Impulse",
        "receiver_switch_2button": "ON/OFF",
        "receiver_cover_2button": "UP/DOWN",
        "receiver_motor_3button": "UP/STOP/DOWN",
        "receiver_heating_cooling": "Heating",
        "receiver_universal_4button": "Universal",
        "ewneo_switch": "Easywave neo Switch",
        "ewneo_dimmer": "Easywave neo Dimmer",
        "ewneo_motor": "Easywave neo Motor",
        "ewneo_dual_switch": "Easywave neo 2-Channel Switch",
        "ewneo_quad_switch": "Easywave neo 4-Channel Switch",
        "ewneo_dual_motor": "Easywave neo 2-Channel Motor",
        "ewneo_quad_motor": "Easywave neo 4-Channel Motor",
        "ewneo_fallback": "Easywave neo Receiver",
    },
    "de": {
        "easywave_transmitter": "Easywave Sender",
        "easywave_receiver": "Easywave Empfänger",
        "easywave_neo_sensor": "Easywave neo Sensor",
        "operating_type_1": "1-Tast-Bedienung",
        "operating_type_2": "2-Tast-Bedienung",
        "operating_type_3": "3-Tast-Bedienung",
        "grouping_single": "Einzeln",
        "grouping_group": "Gruppe",
        "switch_impulse": "Impuls",
        "switch_permanent": "Dauer",
        "buttons": "{count} Tasten",
        "buttons_3_or_4": "3 oder 4 Tasten",
        "receiver_impulse": "Impuls",
        "receiver_switch_2button": "EIN/AUS",
        "receiver_cover_2button": "AUF/ZU",
        "receiver_motor_3button": "AUF/STOPP/ZU",
        "receiver_heating_cooling": "Heizung",
        "receiver_universal_4button": "Universal",
        "ewneo_switch": "Easywave neo Schalter",
        "ewneo_dimmer": "Easywave neo Dimmer",
        "ewneo_motor": "Easywave neo Motor",
        "ewneo_dual_switch": "Easywave neo 2-Kanal Schalter",
        "ewneo_quad_switch": "Easywave neo 4-Kanal Schalter",
        "ewneo_dual_motor": "Easywave neo 2-Kanal Motor",
        "ewneo_quad_motor": "Easywave neo 4-Kanal Motor",
        "ewneo_fallback": "Easywave neo Empfänger",
    },
    "fr": {
        "easywave_transmitter": "Émetteur Easywave",
        "easywave_receiver": "Récepteur Easywave",
        "easywave_neo_sensor": "Capteur Easywave neo",
        "operating_type_1": "Fonctionnement 1 bouton",
        "operating_type_2": "Fonctionnement 2 boutons",
        "operating_type_3": "Fonctionnement 3 boutons",
        "grouping_single": "Individuel",
        "grouping_group": "Groupe",
        "switch_impulse": "Impulsion",
        "switch_permanent": "Permanent",
        "buttons": "{count} boutons",
        "buttons_3_or_4": "3 ou 4 boutons",
        "receiver_impulse": "Impulsion",
        "receiver_switch_2button": "ON/OFF",
        "receiver_cover_2button": "HAUT/BAS",
        "receiver_motor_3button": "HAUT/STOP/BAS",
        "receiver_heating_cooling": "Chauffage",
        "receiver_universal_4button": "Universel",
        "ewneo_switch": "Interrupteur Easywave neo",
        "ewneo_dimmer": "Variateur Easywave neo",
        "ewneo_motor": "Moteur Easywave neo",
        "ewneo_dual_switch": "Interrupteur Easywave neo 2 canaux",
        "ewneo_quad_switch": "Interrupteur Easywave neo 4 canaux",
        "ewneo_dual_motor": "Moteur Easywave neo 2 canaux",
        "ewneo_quad_motor": "Moteur Easywave neo 4 canaux",
        "ewneo_fallback": "Récepteur Easywave neo",
    },
}

_RECEIVER_KIND_KEYS: Final[dict[str, str]] = {
    RECEIVER_KIND_IMPULSE: "receiver_impulse",
    RECEIVER_KIND_SWITCH_2BUTTON: "receiver_switch_2button",
    RECEIVER_KIND_COVER_2BUTTON: "receiver_cover_2button",
    RECEIVER_KIND_MOTOR_3BUTTON: "receiver_motor_3button",
    RECEIVER_KIND_HEATING_COOLING: "receiver_heating_cooling",
    RECEIVER_KIND_UNIVERSAL_4BUTTON: "receiver_universal_4button",
}

_EWNEO_MODEL_KEYS: Final[dict[int, str]] = {
    DEVICE_TYPE_CODE_SWITCH: "ewneo_switch",
    DEVICE_TYPE_CODE_DIMMER: "ewneo_dimmer",
    DEVICE_TYPE_CODE_MOTOR: "ewneo_motor",
    DEVICE_TYPE_CODE_DUAL_SWITCH: "ewneo_dual_switch",
    DEVICE_TYPE_CODE_QUAD_SWITCH: "ewneo_quad_switch",
    DEVICE_TYPE_CODE_DUAL_MOTOR: "ewneo_dual_motor",
    DEVICE_TYPE_CODE_QUAD_MOTOR: "ewneo_quad_motor",
}


def _normalize_language(language: str | None) -> str:
    """Map Home Assistant language codes to supported device-info locales."""
    lang = (language or "en").lower()
    if lang.startswith("de"):
        return "de"
    if lang.startswith("fr"):
        return "fr"
    return "en"


def _t(key: str, language: str | None, **kwargs: Any) -> str:
    """Return a localized device-info string."""
    locale = _normalize_language(language)
    template = _DEVICE_INFO[locale].get(key) or _DEVICE_INFO["en"].get(key, key)
    if kwargs:
        return template.format(**kwargs)
    return template


def transmitter_model(data: dict[str, Any], language: str | None = None) -> str:
    """Return the 0.6.10-style model string for a transmitter."""
    base = _t("easywave_transmitter", language)
    op = str(data.get(CONF_OPERATING_TYPE, "1"))
    details: list[str] = []

    if op == "1":
        details.append(_t("operating_type_1", language))
        grouping = data.get(CONF_GROUPING_MODE)
        if grouping == TRANSMITTER_GROUPING_SINGLE:
            details.append(_t("grouping_single", language))
        elif grouping == TRANSMITTER_GROUPING_GROUP:
            details.append(_t("grouping_group", language))
        mode = data.get(CONF_SWITCH_MODE)
        if mode == TRANSMITTER_SWITCH_IMPULSE:
            details.append(_t("switch_impulse", language))
        elif mode == TRANSMITTER_SWITCH_PERMANENT:
            details.append(_t("switch_permanent", language))
        count = data.get(CONF_BUTTON_COUNT)
        if count:
            details.append(_t("buttons", language, count=count))
    elif op == "2":
        details.append(_t("operating_type_2", language))
        count = data.get(CONF_BUTTON_COUNT)
        if count:
            details.append(_t("buttons", language, count=count))
    elif op == "3":
        details.append(_t("operating_type_3", language))
        details.append(_t("buttons_3_or_4", language))

    if details:
        return f"{base}, {', '.join(details)}"
    return base


def neo_sensor_model(language: str | None = None) -> str:
    """Return the 0.6.10-style model string for a neo sensor."""
    return _t("easywave_neo_sensor", language)


def receiver_model(data: dict[str, Any], language: str | None = None) -> str:
    """Return the 0.6.10-style model string for an EW receiver."""
    base = _t("easywave_receiver", language)
    kind = str(data.get(CONF_RECEIVER_KIND, RECEIVER_KIND_IMPULSE))
    kind_key = _RECEIVER_KIND_KEYS.get(kind)
    if kind_key:
        return f"{base}, {_t(kind_key, language)}"
    return base


def actuator_model(data: dict[str, Any], language: str | None = None) -> str:
    """Return the 0.6.10-style model string for an EWneo actuator."""
    code = int(data.get(CONF_DEVICE_TYPE_CODE, 0))
    key = _EWNEO_MODEL_KEYS.get(code, "ewneo_fallback")
    return _t(key, language)
