"""Helpers to create transmitter entities matching HACS 0.6.x UX."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EasywaveConfigEntry
from .const import (
    CONF_BUTTON_COUNT,
    CONF_DETECTED_BUTTON_TYPE,
    CONF_GROUPING_MODE,
    CONF_OPERATING_TYPE,
    CONF_SWITCH_MODE,
    CONF_USAGE_TYPE,
    TRANSMITTER_GROUPING_GROUP,
    TRANSMITTER_GROUPING_SINGLE,
    TRANSMITTER_SWITCH_IMPULSE,
)
from .entity import EasywaveDeviceEntry


def create_transmitter_entities(
    entry: EasywaveConfigEntry,
    device: EasywaveDeviceEntry,
) -> dict[str, list[Any]]:
    """Return platform → entity list for a transmitter (HACS-style presentation)."""
    # Lazy imports avoid circular platform imports at module load.
    from .binary_sensor import EasywaveTransmitterCoverStateBinarySensor
    from .sensor import (
        EasywaveTransmitterBatterySensor,
        EasywaveTransmitterButtonEnumSensor,
        EasywaveTransmitterLastButtonSensor,
        EasywaveTransmitterStateSensor,
    )

    op = str(device.data.get(CONF_OPERATING_TYPE, "1"))
    grouping = str(device.data.get(CONF_GROUPING_MODE, TRANSMITTER_GROUPING_GROUP))
    usage = str(device.data.get(CONF_USAGE_TYPE, "switch"))
    button_count = int(device.data.get(CONF_BUTTON_COUNT, 4))
    switch_mode = str(device.data.get(CONF_SWITCH_MODE, TRANSMITTER_SWITCH_IMPULSE))
    detected = device.data.get(CONF_DETECTED_BUTTON_TYPE)

    result: dict[str, list[Any]] = {
        "sensor": [],
        "binary_sensor": [],
    }

    # CORE-aligned enum sensor (unique_id …_battery_warning).
    result["sensor"].append(EasywaveTransmitterBatterySensor(entry, device))

    if op == "1":
        if grouping == TRANSMITTER_GROUPING_SINGLE:
            indices = list(range(min(button_count, 4)))
            if button_count == 1 and isinstance(detected, str):
                letter = detected.strip().upper()
                if letter in {"A", "B", "C", "D"}:
                    indices = [{"A": 0, "B": 1, "C": 2, "D": 3}[letter]]
            for index in indices:
                result["sensor"].append(
                    EasywaveTransmitterButtonEnumSensor(
                        entry, device, index, switch_mode
                    )
                )
        else:
            result["sensor"].append(
                EasywaveTransmitterLastButtonSensor(entry, device)
            )
        return result

    if op == "2":
        if usage == "cover":
            if button_count >= 4:
                result["binary_sensor"].append(
                    EasywaveTransmitterCoverStateBinarySensor(
                        entry, device, pair="ab"
                    )
                )
                result["binary_sensor"].append(
                    EasywaveTransmitterCoverStateBinarySensor(
                        entry, device, pair="cd"
                    )
                )
            else:
                result["binary_sensor"].append(
                    EasywaveTransmitterCoverStateBinarySensor(
                        entry, device, pair="ab"
                    )
                )
        else:
            # switch usage → On/Off state sensor(s)
            if button_count >= 4:
                result["sensor"].append(
                    EasywaveTransmitterStateSensor(
                        entry, device, mode="switch", pair="ab"
                    )
                )
                result["sensor"].append(
                    EasywaveTransmitterStateSensor(
                        entry, device, mode="switch", pair="cd"
                    )
                )
            else:
                result["sensor"].append(
                    EasywaveTransmitterStateSensor(
                        entry, device, mode="switch", pair="ab"
                    )
                )
        return result

    if op == "3":
        result["sensor"].append(
            EasywaveTransmitterStateSensor(entry, device, mode="cover3", pair=None)
        )
        return result

    # Fallback
    result["sensor"].append(EasywaveTransmitterLastButtonSensor(entry, device))
    return result


async def async_setup_transmitter_entities(
    hass: HomeAssistant,
    entry: EasywaveConfigEntry,
    device: EasywaveDeviceEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    platform: str,
) -> None:
    """Add transmitter entities for one platform."""
    created = create_transmitter_entities(entry, device)
    entities = created.get(platform, [])
    if entities:
        async_add_entities(entities, config_subentry_id=device.subentry_id)
