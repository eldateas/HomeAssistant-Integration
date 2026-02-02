"""Helper utilities for Eldat integration config flow.

Contains learning helper and device entity determination utilities extracted from config_flow.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

_LOGGER = logging.getLogger(__name__)


async def run_learning(coordinator, match_fn: Callable[[dict], Optional[dict]], timeout: int = 180) -> Optional[Dict[str, Any]]:
    """Run a learning session on the given coordinator.

    - coordinator: the RX11 coordinator object that exposes transceiver and hass
    - match_fn: a function that receives a raw device dict and returns a processed
      device dict when a match is found, or None to continue listening.
    - timeout: seconds to wait for a matching telegram

    This helper registers a temporary telegram callback on the transceiver,
    enables learning mode, waits for a match or timeout, then cleans up.
    """
    if not coordinator or not getattr(coordinator, "transceiver", None):
        _LOGGER.debug("run_learning: no coordinator or transceiver available")
        return False, None, "No coordinator or transceiver available"

    event = asyncio.Event()
    result: dict = {"device": None}
    
    # Store the original callback to restore it later
    original_callback = getattr(coordinator.transceiver, '_telegram_callback', None)
    _LOGGER.debug("run_learning: storing original callback: %s", original_callback)

    async def _callback(dev: dict, info_dict: dict = None, raw_data: str = None) -> None:
        try:
            processed = match_fn(dev)
            if processed:
                result["device"] = processed
                # Wake the waiting coroutine
                coordinator.hass.loop.call_soon_threadsafe(event.set)
        except Exception as exc:  # pragma: no cover - defensive logging
            _LOGGER.exception("Error in match_fn during learning: %s", exc)

    # Register callback and enable learning
    coordinator.transceiver.set_telegram_callback(_callback)
    await coordinator.transceiver.set_learning_mode(True, timeout)
    coordinator._config_flow_learning = True
    _LOGGER.debug("Learning mode enabled for coordinator (timeout=%s)", timeout)

    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        _LOGGER.debug("Learning timed out after %s seconds", timeout)
    finally:
        # Cleanup: restore original callback and disable learning
        try:
            # Restore the original callback instead of setting to None
            if original_callback:
                coordinator.transceiver.set_telegram_callback(original_callback)
                _LOGGER.debug("run_learning: restored original callback")
            else:
                # If there was no original callback, restore the coordinator's _handle_telegram
                if hasattr(coordinator, '_handle_telegram'):
                    coordinator.transceiver.set_telegram_callback(coordinator._handle_telegram)
                    _LOGGER.debug("run_learning: restored coordinator._handle_telegram callback")
            await coordinator.transceiver.set_learning_mode(False)
            coordinator._config_flow_learning = False
        except Exception:  # pragma: no cover - defensive cleanup
            _LOGGER.exception("Error cleaning up learning state")

    return result.get("device")


def battery_percentage_from_level(battery_level: int | None) -> int:
    """Convert the battery level (0-7) into a percentage (0-100%).

    ELDAT devices report battery level as 0-7:
    - 0 = weak/low battery (~10%)
    - 7 = full battery (100%)
    - 1-6 = intermediate levels
    
    Returns an int percentage 0..100. If battery_level is None, returns 0.
    """
    if battery_level is None:
        return 0
    try:
        level = int(battery_level)
    except Exception:
        return 0
    
    # Clamp to valid range
    if level < 0:
        return 0
    if level > 7:
        return 100

    # Map 0-7 to percentage
    # 0 → 10%, 1 → 24%, 2 → 38%, 3 → 52%, 4 → 66%, 5 → 80%, 6 → 94%, 7 → 100%
    if level == 7:
        return 100
    if level == 0:
        return 10
    
    # Linear interpolation between 10% and 100%
    return int(10 + (90 * level / 7.0))


# Obsolete Funktion entfernt - verwende stattdessen:
# from .entity_specs import create_entity_specs_for_device


def build_model_description(device_type: str, device_info: dict) -> str:
    """Build model description with device type and operating mode.
    
    This function creates a human-readable model description for Home Assistant's
    device info panel. It should be used consistently in both config_flow and entity
    to ensure the device info is correct immediately after creation.
    
    Args:
        device_type: The device type (e.g., 'ew_transmitter', 'ewneo_sensor')
        device_info: The device information dictionary
        
    Returns:
        A human-readable model description
    """
    from .const import EWNEO_MODEL_MAP
    
    # Map device types to German descriptions
    type_mapping = {
        # Easywave devices
        "ew_transmitter": "Easywave Sender",
        "ew_receiver": "Easywave Empfänger",
        
        # Easywave Neo sensors
        "ewneo_sensor": "Easywave neo Sensor",
        "ew_sensor": "Easywave neo Sensor",
        
        # Legacy/compatibility
        "ew_transceiver": "Easywave neo Sensor",
    }
    
    # Get base description
    base_description = type_mapping.get(device_type, device_type.replace("_", " ").title())
    
    # Build detailed description with operating type and button/channel info
    details = []
    
    # Add operating type info for transmitters
    operating_type = device_info.get("operating_type")
    if operating_type and device_type == "ew_transmitter":
        if operating_type == "1":
            details.append("1-Tast-Bedienung")
        elif operating_type == "2":
            details.append("2-Tast-Bedienung")
        elif operating_type == "3":
            details.append("3-Tast-Bedienung")
    
    # Add switch mode for 1-Tast-Bedienung (Impuls/Dauer)
    switch_mode = device_info.get("switch_mode")
    if switch_mode and device_type == "ew_transmitter" and operating_type == "1":
        if switch_mode == "impulse":
            details.append("Impuls")
        elif switch_mode == "permanent":
            details.append("Dauer")
    
    # Add button count for 1-Tast-Bedienung
    button_count = device_info.get("button_count")
    if button_count and device_type == "ew_transmitter" and operating_type == "1":
        details.append(f"{button_count} Tasten")
    
    # Add button count for 2-Tast and 3-Tast-Bedienung
    if button_count and device_type == "ew_transmitter" and operating_type in ["2", "3"]:
        details.append(f"{button_count} Tasten")
    
    # Add receiver_kind info for receivers
    receiver_kind = device_info.get("receiver_kind")
    if receiver_kind and device_type == "ew_receiver":
        if receiver_kind == "impulse":
            details.append("Impuls")
        elif receiver_kind == "switch_2button":
            details.append("EIN/AUS")
        elif receiver_kind == "cover_2button":
            details.append("AUF/ZU")
        elif receiver_kind == "motor_3button":
            details.append("AUF/STOPP/ZU")
        elif receiver_kind == "heating_cooling":
            details.append("Heizung")
        elif receiver_kind == "universal_4button":
            details.append("Universal")
    
    # Add channel info for receivers (legacy support)
    # Exclude EWneo devices as they have their own channel descriptions
    channels = device_info.get("channels")
    if channels and "receiver" in device_type and not receiver_kind and not device_type.startswith("ewneo_"):
        if channels == 1:
            details.append("1-Kanal")
        elif channels == 2:
            details.append("2-Kanal")
        elif channels == 4:
            details.append("4-Kanal")
        else:
            details.append(f"{channels}-Kanal")
    
    # For EWneo devices, return the specific model name directly based on device_type_code
    if device_type.startswith("ewneo_") and device_type != "ewneo_sensor":
        device_type_code = device_info.get("device_type_code", 0)
        model = EWNEO_MODEL_MAP.get(device_type_code)
        if model:
            return model
        # Fallback for unknown EWneo device types
        return "Easywave neo Empfänger"
    
    # Combine base description with details
    if details:
        return f"{base_description}, {', '.join(details)}"
    
    return base_description
