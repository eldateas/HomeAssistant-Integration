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
        # Cleanup: remove callback and disable learning
        try:
            coordinator.transceiver.set_telegram_callback(None)
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


def determine_device_entities(learned_device: dict) -> dict:
    """DEPRECATED: Use entity_specs.create_entity_specs_for_device() instead.
    
    This function is kept for backward compatibility but delegates to
    the centralized entity specification system.
    
    Returns a dict with keys: entities (list), platforms (set),
    device_class, category.
    """
    import logging
    _LOGGER = logging.getLogger(__name__)
    _LOGGER.warning("determine_device_entities() is deprecated, use create_entity_specs_for_device()")
    
    from .entity_specs import create_entity_specs_for_device
    
    # Convert learned_device format to device_info format
    serial_number = learned_device.get("serial_number", "unknown")
    device_info = {
        "type": learned_device.get("type", "unknown"),
        "name": learned_device.get("name", ""),
        "serial_number": serial_number,
        **learned_device  # Include all other fields
    }
    
    # Get entity specs from centralized function
    entity_specs = create_entity_specs_for_device(serial_number, device_info)
    
    # Convert entity_specs format (dict of lists) to old format (single list + platforms)
    all_entities = []
    all_platforms = set()
    
    for platform, entities in entity_specs.items():
        if entities:
            all_platforms.add(platform)
            all_entities.extend(entities)
    
    # Determine device_class and category from device type
    device_type = learned_device.get("type", "unknown")
    device_class = "unknown"
    category = "config"
    
    if device_type == "ew_transmitter":
        device_class = "remote_control"
        category = "remote"
    elif device_type == "ew_sensor":
        device_class = "sensor"
        category = "sensor"
    elif device_type == "ew_receiver":
        receiver_kind = learned_device.get("receiver_kind", "switch")
        if receiver_kind == "motor":
            device_class = "blind"
            category = "cover"
        elif receiver_kind == "dimmer":
            device_class = "light"
            category = "light"
        else:
            device_class = "switch"
            category = "switch"
    
    return {
        "entities": all_entities,
        "platforms": all_platforms,
        "device_class": device_class,
        "category": category,
    }

