"""Move an enum battery sensor onto the binary sensor that replaces it.

The entity domain cannot change, so ``sensor.<object_id>`` becomes
``binary_sensor.<object_id>``. History is retargeted before the new entity
writes its first state. A later start only removes a leftover enum sensor.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

BATTERY_UNIQUE_SUFFIX = "_battery_warning"
_SEED_KEY = "battery_seed"

_CONFIG_FILES = (
    "automations.yaml",
    "scripts.yaml",
    "scenes.yaml",
    "configuration.yaml",
    "ui-lovelace.yaml",
    "groups.yaml",
)


def binary_entity_id_for_sensor(sensor_entity_id: str) -> str | None:
    """Return the binary-sensor entity id that replaces an enum battery sensor."""
    domain, separator, object_id = sensor_entity_id.partition(".")
    if separator != "." or domain != "sensor" or not object_id:
        return None
    return f"binary_sensor.{object_id}"


def enum_state_is_low(state: str | None) -> bool | None:
    """Map an enum battery state onto the binary sensor (on = low)."""
    if state == "low":
        return True
    if state == "ok":
        return False
    return None


def consume_battery_seed(hass: Any, device_id: str) -> bool | None:
    """Return and clear the migrated low/ok value for one device."""
    bucket = hass.data.get(DOMAIN, {}).get(_SEED_KEY)
    if not isinstance(bucket, dict):
        return None
    value = bucket.pop(f"{device_id}{BATTERY_UNIQUE_SUFFIX}", None)
    return value if isinstance(value, bool) else None


def _remember_seed(hass: Any, unique_id: str, is_low: bool | None) -> None:
    if is_low is None:
        return
    hass.data.setdefault(DOMAIN, {}).setdefault(_SEED_KEY, {})[unique_id] = is_low


def _enum_is_low(hass: Any, entity_id: str) -> bool | None:
    from homeassistant.helpers import restore_state

    stored = restore_state.async_get(hass).last_states.get(entity_id)
    if stored is not None:
        return enum_state_is_low(stored.state.state)
    state = hass.states.get(entity_id)
    if state is None:
        return None
    return enum_state_is_low(state.state)


def _copy_restore_state(
    hass: Any,
    source_entity_id: str,
    target_entity_id: str,
    *,
    is_low: bool | None,
) -> None:
    """Point startup restore at the binary sensor before it is added."""
    from homeassistant.core import State
    from homeassistant.helpers import restore_state
    from homeassistant.helpers.restore_state import StoredState
    from homeassistant.util import dt as dt_util

    if source_entity_id == target_entity_id:
        return
    data = restore_state.async_get(hass)
    current = data.last_states.get(target_entity_id)
    if current is not None and current.state.state in ("on", "off"):
        return
    source = data.last_states.get(source_entity_id)
    if source is not None and source.state.state in ("on", "off"):
        data.last_states[target_entity_id] = StoredState(
            State(target_entity_id, source.state.state, source.state.attributes),
            source.extra_data,
            source.last_seen,
        )
        return
    if is_low is None:
        return
    data.last_states[target_entity_id] = StoredState(
        State(target_entity_id, "on" if is_low else "off"),
        None,
        dt_util.utcnow(),
    )


def _customization_updates(source: Any, target: Any) -> dict[str, Any]:
    """Copy user-facing registry fields that the new entity does not have yet."""
    updates: dict[str, Any] = {}
    if target.name is None and source.name is not None:
        updates["name"] = source.name
    if target.icon is None and source.icon is not None:
        updates["icon"] = source.icon
    if target.area_id is None and source.area_id is not None:
        updates["area_id"] = source.area_id
    if source.labels:
        merged = set(target.labels) | set(source.labels)
        if merged != set(target.labels):
            updates["labels"] = merged
    if source.disabled_by is not None and target.disabled_by is None:
        updates["disabled_by"] = source.disabled_by
    if source.hidden_by is not None and target.hidden_by is None:
        updates["hidden_by"] = source.hidden_by
    return updates


def _rewrite_config_files(config_dir: Path, mapping: dict[str, str]) -> None:
    paths = [config_dir / name for name in _CONFIG_FILES]
    storage = config_dir / ".storage"
    if storage.is_dir():
        paths.extend(path for path in storage.glob("lovelace*") if path.is_file())
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        updated = text
        for old_entity_id, new_entity_id in mapping.items():
            updated = updated.replace(old_entity_id, new_entity_id)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            _LOGGER.info("Updated battery entity ids in %s", path.name)


async def async_migrate_enum_battery_entities(hass: Any, entry: Any) -> None:
    """Retire enum battery sensors and keep one binary sensor per device."""
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.recorder import DATA_INSTANCE

    registry = er.async_get(hass)
    mapping: dict[str, str] = {}
    sensors = [
        sensor
        for sensor in er.async_entries_for_config_entry(registry, entry.entry_id)
        if sensor.domain == "sensor"
        and sensor.platform == DOMAIN
        and sensor.unique_id
        and sensor.unique_id.endswith(BATTERY_UNIQUE_SUFFIX)
    ]

    for sensor in sensors:
        desired = binary_entity_id_for_sensor(sensor.entity_id)
        if desired is None or sensor.unique_id is None:
            continue

        is_low = _enum_is_low(hass, sensor.entity_id)
        _remember_seed(hass, sensor.unique_id, is_low)

        binary_id = registry.async_get_entity_id(
            "binary_sensor", DOMAIN, sensor.unique_id
        )
        if binary_id is None:
            # A removed binary sensor with this unique id would come back under
            # its old entity id (for example ``…_batteriestatus``).
            registry.deleted_entities.pop(
                ("binary_sensor", DOMAIN, sensor.unique_id), None
            )
            created = registry.async_get_or_create(
                "binary_sensor",
                DOMAIN,
                sensor.unique_id,
                suggested_object_id=sensor.entity_id.split(".", 1)[1],
                config_entry=entry,
                config_subentry_id=sensor.config_subentry_id,
                device_id=sensor.device_id,
                has_entity_name=True,
                original_device_class="battery",
                translation_key="battery_warning",
            )
            binary_id = created.entity_id

        if binary_id != desired and registry.async_get(desired) is None:
            registry.async_update_entity(binary_id, new_entity_id=desired)
            _copy_restore_state(hass, binary_id, desired, is_low=None)
            binary_id = desired

        recorder = hass.data.get(DATA_INSTANCE)
        if recorder is not None:
            recorder.async_update_states_metadata(sensor.entity_id, binary_id)

        binary = registry.async_get(binary_id)
        if binary is not None:
            updates = _customization_updates(sensor, binary)
            if updates:
                registry.async_update_entity(binary_id, **updates)

        _copy_restore_state(hass, sensor.entity_id, binary_id, is_low=is_low)
        registry.async_remove(sensor.entity_id)
        mapping[sensor.entity_id] = binary_id
        _LOGGER.info(
            "Moved Easywave battery %s to %s", sensor.entity_id, binary_id
        )

    if mapping:
        await hass.async_add_executor_job(
            _rewrite_config_files, Path(hass.config.config_dir), mapping
        )
