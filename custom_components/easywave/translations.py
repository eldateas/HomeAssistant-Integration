"""Translations helper for ELDAT integration.

Provides utility functions for language detection and state checking.
All translations are loaded from translations/*.json files at module import time.
Supports any number of languages - just add a new .json file.

IMPORTANT: All file I/O happens at module import time (before the event loop starts)
to avoid blocking calls inside Home Assistant's async event loop.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Final, TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Default/fallback language
DEFAULT_LANGUAGE: Final = "en"

# Get translations directory path
_TRANSLATIONS_DIR: Final = Path(__file__).parent / "translations"


def _scan_available_languages() -> set[str]:
    """Scan for available language codes from .json files in translations directory.
    
    Called once at module import time.
    """
    available = set()
    
    if _TRANSLATIONS_DIR.exists():
        for json_file in _TRANSLATIONS_DIR.glob("*.json"):
            lang_code = json_file.stem
            # Skip files that don't look like language codes (2-3 letter codes)
            if 2 <= len(lang_code) <= 3 and lang_code.isalpha():
                available.add(lang_code)
    
    if not available:
        _LOGGER.warning("No translation files found in %s", _TRANSLATIONS_DIR)
        available = {DEFAULT_LANGUAGE}
    
    return available


def _load_all_translations() -> dict[str, dict[str, Any]]:
    """Load all translation files at once.
    
    Called once at module import time to avoid blocking I/O in the event loop.
    """
    translations = {}
    
    if not _TRANSLATIONS_DIR.exists():
        _LOGGER.warning("Translations directory not found: %s", _TRANSLATIONS_DIR)
        return translations
    
    for json_file in _TRANSLATIONS_DIR.glob("*.json"):
        lang_code = json_file.stem
        # Skip files that don't look like language codes
        if not (2 <= len(lang_code) <= 3 and lang_code.isalpha()):
            continue
        
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                translations[lang_code] = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            _LOGGER.error("Failed to load translations from %s: %s", json_file, e)
    
    return translations


def _build_state_values(translations: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    """Build state values sets from all loaded translations.
    
    Returns a dict mapping state type to set of all translated values:
    {
        "on": {"Ein", "On", "Aan", ...},
        "off": {"Aus", "Off", "Uit", ...},
        ...
    }
    """
    state_values: dict[str, set[str]] = {
        "on": set(),
        "off": set(),
        "up": set(),
        "down": set(),
        "stop": set(),
        "open": set(),
        "closed": set(),
    }
    
    for lang_data in translations.values():
        state_section = lang_data.get("common", {}).get("state", {})
        for state_key in state_values:
            value = state_section.get(state_key)
            if value:
                state_values[state_key].add(value)
                # Also add lowercase version for case-insensitive matching
                state_values[state_key].add(value.lower())
    
    # Always include the English keys themselves (lowercase)
    for key in state_values:
        state_values[key].add(key)
    
    return state_values


# ============================================================================
# Load everything at module import time (before event loop starts)
# ============================================================================

# Load all translations synchronously at import time
TRANSLATIONS: Final[dict[str, dict[str, Any]]] = _load_all_translations()

# Get available languages from loaded translations
AVAILABLE_LANGUAGES: Final[set[str]] = set(TRANSLATIONS.keys()) or {DEFAULT_LANGUAGE}

# Build state values cache from all languages
STATE_VALUES: Final[dict[str, set[str]]] = _build_state_values(TRANSLATIONS)


# ============================================================================
# Public API - no file I/O, only uses pre-loaded data
# ============================================================================

def _get_nested_value(data: dict, key_path: str) -> str | None:
    """Get a nested value from a dictionary using dot notation.
    
    Example: _get_nested_value(data, "common.state.on") returns data["common"]["state"]["on"]
    """
    keys = key_path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current if isinstance(current, str) else None


def get_language(hass: "HomeAssistant | None" = None, user_id: str | None = None) -> str:
    """Get the current user's language from Home Assistant.
    
    This function tries to get the language in the following order:
    1. User-specific language from frontend storage (async loaded data)
    2. System language from hass.config.language
    3. DEFAULT_LANGUAGE as fallback
    
    Returns the language code if a translation file exists for it,
    otherwise falls back to DEFAULT_LANGUAGE.
    """
    if hass is not None:
        try:
            # Try to get user-specific language from frontend storage
            # The data is stored via HassKey "frontend_storage" containing UserStore objects
            # Each UserStore has a .data dict with "language" -> {"language": "en", ...}
            
            # Access the frontend storage dict
            frontend_storage = None
            for key, value in hass.data.items():
                # Look for the frontend_storage HassKey
                key_str = str(key) if hasattr(key, '__str__') else ""
                if 'frontend_storage' in key_str:
                    frontend_storage = value
                    _LOGGER.debug("Found frontend_storage with key: %s, type: %s", key_str, type(value))
                    break
            
            if frontend_storage:
                _LOGGER.debug("Frontend storage contains %d entries", len(frontend_storage))
                # frontend_storage is dict[str, Future[UserStore]]
                # We need to iterate through resolved futures
                for uid, future_or_store in frontend_storage.items():
                    _LOGGER.debug("Processing user %s, type: %s", uid, type(future_or_store))
                    try:
                        # If it's a resolved future, get the result
                        if hasattr(future_or_store, 'done') and future_or_store.done():
                            store = future_or_store.result()
                            _LOGGER.debug("Got store from future for user %s", uid)
                        elif hasattr(future_or_store, 'data'):
                            # It's already a UserStore
                            store = future_or_store
                            _LOGGER.debug("Direct store for user %s", uid)
                        else:
                            _LOGGER.debug("Skipping user %s - not a store or resolved future", uid)
                            continue
                        
                        # Get language from store data
                        if hasattr(store, 'data') and store.data:
                            _LOGGER.debug("Store data for user %s: %s", uid, store.data)
                            lang_data = store.data.get("language", {})
                            if isinstance(lang_data, dict):
                                user_lang = lang_data.get("language")
                            else:
                                user_lang = lang_data
                            
                            if user_lang:
                                base_lang = user_lang.split("-")[0].split("_")[0].lower()
                                _LOGGER.debug("Found user language: %s -> base: %s", user_lang, base_lang)
                                if base_lang in AVAILABLE_LANGUAGES:
                                    _LOGGER.info("Using user language: %s", base_lang)
                                    return base_lang
                    except Exception as e:
                        _LOGGER.debug("Error processing user %s: %s", uid, e)
                        continue
            else:
                _LOGGER.debug("No frontend_storage found in hass.data. Keys: %s", [str(k) for k in list(hass.data.keys())[:20]])
            
            # Fallback to system language
            lang = hass.config.language
            _LOGGER.debug("Falling back to system language: %s", lang)
            if lang:
                # Extract base language code (e.g., "de-DE" -> "de")
                base_lang = lang.split("-")[0].split("_")[0].lower()
                
                # Check if we have translations for this language
                if base_lang in AVAILABLE_LANGUAGES:
                    return base_lang
                
                # Try full code if base didn't match
                if lang.lower() in AVAILABLE_LANGUAGES:
                    return lang.lower()
        except (AttributeError, KeyError) as e:
            _LOGGER.debug("Error getting language: %s", e)
            pass
    
    _LOGGER.debug("Using default language: %s", DEFAULT_LANGUAGE)
    return DEFAULT_LANGUAGE


def translate(key: str, language: str | None = None, hass: "HomeAssistant | None" = None, **kwargs) -> str:
    """Get translated string for the given key.
    
    Args:
        key: The translation key (e.g., "state.on", "button.a", "error.rx11_not_connected")
        language: Language code, auto-detected if not provided
        hass: Home Assistant instance for language detection
        **kwargs: Format arguments for the string (e.g., channel=1)
    
    Returns:
        Translated string, or the key if not found
    """
    if language is None:
        language = get_language(hass)
    
    # Get translations for requested language (already loaded)
    translations = TRANSLATIONS.get(language, {})
    
    # Look up in common section first (e.g., "state.on" -> "common.state.on")
    result = _get_nested_value(translations, f"common.{key}")
    
    # If not found, try as direct path
    if result is None:
        result = _get_nested_value(translations, key)
    
    # Fallback to default language if still not found
    if result is None and language != DEFAULT_LANGUAGE:
        default_translations = TRANSLATIONS.get(DEFAULT_LANGUAGE, {})
        result = _get_nested_value(default_translations, f"common.{key}")
        if result is None:
            result = _get_nested_value(default_translations, key)
    
    # Return key if nothing found
    if result is None:
        return key
    
    # Format with kwargs if provided
    if kwargs:
        try:
            result = result.format(**kwargs)
        except (KeyError, ValueError):
            pass
    
    return result


# ============================================================================
# EWneo device type mapping
# ============================================================================

EWNEO_DEVICE_TYPE_KEYS: Final = {
    0x03: "ewneo.switch",
    0x04: "ewneo.dimmer",
    0x05: "ewneo.motor",
    0x06: "ewneo.dual_switch",
    0x07: "ewneo.quad_switch",
    0x08: "ewneo.dual_motor",
    0x09: "ewneo.quad_motor",
}


def get_ewneo_device_name(device_type_code: int, language: str | None = None, hass: "HomeAssistant | None" = None) -> str:
    """Get translated EWneo device type name."""
    key = EWNEO_DEVICE_TYPE_KEYS.get(device_type_code, "device.device_prefix")
    return translate(key, language, hass)


# ============================================================================
# Button label helpers
# ============================================================================

BUTTON_INDEX_KEYS: Final = {
    0: "button.a",
    1: "button.b",
    2: "button.c",
    3: "button.d",
}


def get_button_label(button_index: int, language: str | None = None, hass: "HomeAssistant | None" = None) -> str:
    """Get translated button label for the given button index (0-3)."""
    key = BUTTON_INDEX_KEYS.get(button_index, f"button.{button_index}")
    label = translate(key, language, hass)
    if label == key:
        return translate("button.a", language, hass).replace("A", chr(65 + button_index))
    return label


# ============================================================================
# State option helpers (for entity_specs)
# ============================================================================

def get_state_options_keys(operating_type: str, usage_type: str = "switch") -> list[str]:
    """Get list of untranslated state option keys for HA translation.
    
    These keys will be translated by Home Assistant using translations/*.json
    under entity.sensor.<translation_key>.state.<key>
    """
    if operating_type == "2":
        return ["on", "off"] if usage_type == "switch" else ["up", "down"]
    elif operating_type == "3":
        return ["up", "down", "stop"]
    elif operating_type == "1":
        return ["a", "b", "c", "d"]
    return []


def get_button_map_keys(operating_type: str, usage_type: str = "switch") -> dict:
    """Get button index to untranslated state key mapping."""
    if operating_type == "2":
        if usage_type == "switch":
            return {0: "on", 1: "off", 2: "on", 3: "off", "A": "on", "B": "off", "C": "on", "D": "off"}
        else:
            return {0: "up", 1: "down", 2: "up", 3: "down", "A": "up", "B": "down", "C": "up", "D": "down"}
    elif operating_type == "3":
        return {0: "up", 1: "down", 2: "stop", 3: "stop", "A": "up", "B": "down", "C": "stop", "D": "stop"}
    return {}


# ============================================================================
# Language-independent state checking functions
# Uses pre-loaded STATE_VALUES - no file I/O
# ============================================================================

def get_all_on_values() -> set[str]:
    """Get all 'on' state values from all languages."""
    return STATE_VALUES.get("on", {"on"})


def get_all_off_values() -> set[str]:
    """Get all 'off' state values from all languages."""
    return STATE_VALUES.get("off", {"off"})


def get_all_up_values() -> set[str]:
    """Get all 'up' state values from all languages."""
    return STATE_VALUES.get("up", {"up"})


def get_all_down_values() -> set[str]:
    """Get all 'down' state values from all languages."""
    return STATE_VALUES.get("down", {"down"})


def get_all_stop_values() -> set[str]:
    """Get all 'stop' state values from all languages."""
    return STATE_VALUES.get("stop", {"stop"})


def is_on_state(value: str | None) -> bool:
    """Check if value represents an 'on' state in any language."""
    return value is not None and value in STATE_VALUES.get("on", set())


def is_off_state(value: str | None) -> bool:
    """Check if value represents an 'off' state in any language."""
    return value is not None and value in STATE_VALUES.get("off", set())


def is_up_state(value: str | None) -> bool:
    """Check if value represents an 'up' state in any language."""
    return value is not None and value in STATE_VALUES.get("up", set())


def is_down_state(value: str | None) -> bool:
    """Check if value represents a 'down' state in any language."""
    return value is not None and value in STATE_VALUES.get("down", set())


def is_stop_state(value: str | None) -> bool:
    """Check if value represents a 'stop' state in any language."""
    return value is not None and value in STATE_VALUES.get("stop", set())


def is_active_state(value: str | None) -> bool:
    """Check if value represents any 'active' state in any language."""
    if value is None:
        return False
    return value in STATE_VALUES.get("on", set()) or value in STATE_VALUES.get("up", set())


def is_inactive_state(value: str | None) -> bool:
    """Check if value represents any 'inactive' state in any language."""
    if value is None:
        return False
    return value in STATE_VALUES.get("off", set()) or value in STATE_VALUES.get("down", set())


def has_switch_states(options: set[str] | list[str]) -> bool:
    """Check if options contain switch states (on/off in any language)."""
    options_set = set(options) if not isinstance(options, set) else options
    on_values = STATE_VALUES.get("on", set())
    off_values = STATE_VALUES.get("off", set())
    return bool(on_values & options_set) and bool(off_values & options_set)


def has_cover_states(options: set[str] | list[str]) -> bool:
    """Check if options contain cover states (up/down in any language)."""
    options_set = set(options) if not isinstance(options, set) else options
    up_values = STATE_VALUES.get("up", set())
    down_values = STATE_VALUES.get("down", set())
    return bool(up_values & options_set) and bool(down_values & options_set)


# ============================================================================
# Convenience functions for common translations
# ============================================================================

def t_state(language: str | None = None, hass: "HomeAssistant | None" = None) -> str:
    """Get translated 'State' entity name."""
    return translate("entity.state", language, hass)


def t_battery_level(language: str | None = None, hass: "HomeAssistant | None" = None) -> str:
    """Get translated 'Battery Level' entity name."""
    return translate("entity.battery_level", language, hass)


def t_receiver(language: str | None = None, hass: "HomeAssistant | None" = None) -> str:
    """Get translated 'Receiver' string."""
    return translate("device.receiver", language, hass)


def t_transmitter(language: str | None = None, hass: "HomeAssistant | None" = None) -> str:
    """Get translated 'Transmitter' / 'Sender' string."""
    return translate("device.transmitter", language, hass)


def t_sensor_device(language: str | None = None, hass: "HomeAssistant | None" = None) -> str:
    """Get translated 'Sensor' string."""
    return translate("device.sensor", language, hass)
