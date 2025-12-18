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
    """Determine which entities should be created for a learned device.

    This extracts the large switch/case logic from the config flow into a
    reusable helper. Returns a dict with keys: entities (list), platforms (set),
    device_class, category.
    """
    device_type = learned_device.get("type", "unknown")
    entity_info: dict = {"entities": [], "platforms": set()}

    if device_type == "ew_transmitter":
        # EW-Transmitter creates binary sensor entities for button press detection
        # Battery sensors are handled separately via sensor platform
        entity_info.update({
            "entities": [],
            "platforms": {"binary_sensor", "sensor"},  # Both binary_sensor (buttons) and sensor (battery)
            "device_class": "remote_control",
            "category": "remote",
        })

    elif device_type == "ew_sensor":
        entities = []
        if learned_device.get("is_learn_telegram"):
            available_sensors = learned_device.get("available_sensors", [])
            if "temperature" in available_sensors:
                entities.append({
                    "type": "sensor",
                    "sensor_type": "temperature",
                    "name": "Temperature",
                    "unique_id": f"{learned_device.get('serial_number')}_temperature",
                    "device_class": "temperature",
                    "unit": "°C",
                    "icon": "mdi:thermometer",
                })
            if "humidity" in available_sensors:
                entities.append({
                    "type": "sensor",
                    "sensor_type": "humidity",
                    "name": "Humidity",
                    "unique_id": f"{learned_device.get('serial_number')}_humidity",
                    "device_class": "humidity",
                    "unit": "%",
                    "icon": "mdi:water-percent",
                })
            if "rain" in available_sensors:
                entities.append({
                    "type": "sensor",
                    "sensor_type": "rain",
                    "name": "Rain",
                    "unique_id": f"{learned_device.get('serial_number')}_rain",
                    "device_class": "precipitation",
                    "unit": "mm",
                    "icon": "mdi:weather-rainy",
                })
            if "wind" in available_sensors:
                entities.append({
                    "type": "sensor",
                    "sensor_type": "wind",
                    "name": "Wind Speed",
                    "unique_id": f"{learned_device.get('serial_number')}_wind",
                    "device_class": "wind_speed",
                    "unit": "m/s",
                    "icon": "mdi:weather-windy",
                })
        if learned_device.get("has_battery"):
            entities.append({
                "type": "sensor",
                "sensor_type": "battery",
                "name": "Battery",
                "unique_id": f"{learned_device.get('serial_number')}_battery",
                "device_class": "battery",
                "unit": "%",
                "icon": "mdi:battery",
                "current_value": battery_percentage_from_level(learned_device.get("battery_level")),
            })
        entity_info.update({
            "entities": entities,
            "platforms": {"sensor"},
            "device_class": "sensor",
            "category": "sensor",
        })

    elif device_type == "ew_receiver":
        entities = []
        channel_count = learned_device.get("channels", 1)
        for i in range(channel_count):
            entities.append({
                "type": "switch",
                "channel": i,
                "name": f"Channel {i+1}",
                "unique_id": f"{learned_device.get('serial_number')}_ch{i}",
                "device_class": "switch",
                "icon": "mdi:light-switch",
            })
        entity_info.update({
            "entities": entities,
            "platforms": {"switch"},
            "device_class": "switch",
            "category": "switch",
            # CRITICAL: EW-Receiver müssen Button-Support haben für Control-Entities
            "supports_buttons": True,
            "supports_feedback": False,  # Explizit false für EW-Receiver
            "supports_sensors": False,   # Explizit false für EW-Receiver
        })

    elif device_type in ["ewneo_transceiver", "ewneo_bidi_transmitter"]:
        entities = []
        button_count = int(learned_device.get("button_count", learned_device.get("channels", 2)))
        for i in range(button_count):
            button_letter = chr(ord("A") + i)
            entities.append({
                "type": "button",
                "channel": i,
                "name": f"Button {button_letter}",
                "unique_id": f"{learned_device.get('serial_number')}_btn{i}",
                "device_class": "button",
                "icon": "mdi:gesture-tap-button",
            })
        entity_info.update({
            "entities": entities,
            "platforms": {"button"},
            "device_class": "remote_control",
            "category": "remote",
        })

    elif device_type == "ewneo_receiver":
        entities = []
        gateway_device_type = learned_device.get("device_type", "EWB_DT_SWITCH")
        platforms = set()
        if gateway_device_type == "EWB_DT_SWITCH":
            entities.append({
                "type": "switch",
                "channel": 0,
                "name": "Switch",
                "unique_id": f"{learned_device.get('serial_number')}_switch",
                "device_class": "switch",
                "icon": "mdi:toggle-switch",
            })
            platforms = {"switch"}
        elif gateway_device_type == "EWB_DT_DUAL_SWITCH":
            for i in range(2):
                entities.append({
                    "type": "switch",
                    "channel": i,
                    "name": f"Switch {i+1}",
                    "unique_id": f"{learned_device.get('serial_number')}_switch_{i}",
                    "device_class": "switch",
                    "icon": "mdi:toggle-switch",
                })
            platforms = {"switch"}
        elif gateway_device_type == "EWB_DT_QUAD_SWITCH":
            for i in range(4):
                entities.append({
                    "type": "switch",
                    "channel": i,
                    "name": f"Switch {i+1}",
                    "unique_id": f"{learned_device.get('serial_number')}_switch_{i}",
                    "device_class": "switch",
                    "icon": "mdi:toggle-switch",
                })
            platforms = {"switch"}
        elif gateway_device_type == "EWB_DT_DIMMER":
            entities.append({
                "type": "light",
                "channel": 0,
                "name": "Dimmer",
                "unique_id": f"{learned_device.get('serial_number')}_dimmer",
                "device_class": "light",
                "icon": "mdi:brightness-6",
                "supports_brightness": True,
            })
            platforms = {"light"}
        elif gateway_device_type == "EWB_DT_MOTOR":
            entities.append({
                "type": "cover",
                "channel": 0,
                "name": "Cover",
                "unique_id": f"{learned_device.get('serial_number')}_cover",
                "device_class": "blind",
                "icon": "mdi:window-shutter",
            })
            platforms = {"cover"}
        elif gateway_device_type == "EWB_DT_DUAL_MOTOR":
            for i in range(2):
                entities.append({
                    "type": "cover",
                    "channel": i,
                    "name": f"Cover {i+1}",
                    "unique_id": f"{learned_device.get('serial_number')}_cover_{i}",
                    "device_class": "blind",
                    "icon": "mdi:window-shutter",
                })
            platforms = {"cover"}
        elif gateway_device_type == "EWB_DT_QUAD_MOTOR":
            for i in range(4):
                entities.append({
                    "type": "cover",
                    "channel": i,
                    "name": f"Cover {i+1}",
                    "unique_id": f"{learned_device.get('serial_number')}_cover_{i}",
                    "device_class": "blind",
                    "icon": "mdi:window-shutter",
                })
            platforms = {"cover"}
        elif gateway_device_type == "EWB_DT_BIDI_TR":
            entities.append({
                "type": "switch",
                "channel": 0,
                "name": "Transceiver",
                "unique_id": f"{learned_device.get('serial_number')}_transceiver",
                "device_class": "switch",
                "icon": "mdi:transit-connection-variant",
            })
            platforms = {"switch"}
        else:
            entities.append({
                "type": "switch",
                "channel": 0,
                "name": "Unknown Device",
                "unique_id": f"{learned_device.get('serial_number')}_unknown",
                "device_class": "switch",
                "icon": "mdi:help-circle",
            })
            platforms = {"switch"}

        entity_info.update({
            "entities": entities,
            "platforms": platforms,
            "device_class": ("outlet" if str(gateway_device_type).endswith("_SWITCH") else
                              "light" if str(gateway_device_type).endswith("_DIMMER") else
                              "blind" if str(gateway_device_type).endswith("_MOTOR") else "switch"),
            "category": "config",
        })

    return entity_info
