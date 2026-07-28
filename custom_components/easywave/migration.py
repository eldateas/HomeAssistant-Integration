"""One-shot migration from HACS JSON device files to bucket subentries."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_ACTUATOR_SERIAL,
    CONF_BUTTON_COUNT,
    CONF_CHANNELS,
    CONF_COVER_MODE,
    CONF_DETECTED_BUTTON_TYPE,
    CONF_DEVICE_TITLE,
    CONF_DEVICE_TYPE_CODE,
    CONF_ENTRY_TYPE,
    CONF_EWNEO_INDEX,
    CONF_GATEWAY_SERIAL,
    CONF_GROUPING_MODE,
    CONF_JSON_MIGRATION_DONE,
    CONF_OPERATING_MODE,
    CONF_OPERATING_TYPE,
    CONF_RECEIVER_KIND,
    CONF_RECEIVER_SERIAL,
    CONF_RX11_INDEX,
    CONF_SENSOR_CAPABILITIES,
    CONF_SENSOR_SERIAL,
    CONF_SWITCH_MODE,
    CONF_TRANSMITTER_SERIAL,
    CONF_USAGE_TYPE,
    CONF_RUNTIME_MEASURED,
    DEVICE_TYPE_CODE_MOTOR_TYPES,
    DEVICE_TYPE_CODE_TO_CHANNELS,
    DOMAIN,
    ENTRY_TYPE_NEO_ACTUATOR,
    ENTRY_TYPE_NEO_SENSOR,
    ENTRY_TYPE_RECEIVER,
    ENTRY_TYPE_TO_SUBENTRY_TYPE,
    ENTRY_TYPE_TRANSMITTER,
    HACS_ACTUATOR_TYPE_TO_CODE,
    RECEIVER_KIND_IMPULSE,
    TRANSMITTER_GROUPING_GROUP,
    TRANSMITTER_SWITCH_IMPULSE,
    bucket_subentry_unique_id,
    device_id_for_neo_actuator,
    device_id_for_neo_sensor,
    device_id_for_receiver,
    device_id_for_transmitter,
    normalize_serial_hex,
)
from .devices import (
    async_bucket_subentry_title,
    get_devices,
    iter_subentries_of_type,
)

_LOGGER = logging.getLogger(__name__)

_REGISTERED_FILE = "registered_devices.json"
_MANAGED_FILE = "managed_devices.json"


def _easywave_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.config_dir) / DOMAIN


def _load_json_devices(path: Path) -> dict[str, dict[str, Any]]:
    """Load a devices dict from a HACS JSON file."""
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        _LOGGER.warning("Could not read %s: %s", path, err)
        return {}

    if isinstance(raw, dict) and isinstance(raw.get("devices"), dict):
        return {
            str(key): value
            for key, value in raw["devices"].items()
            if isinstance(value, dict)
        }
    if isinstance(raw, dict):
        # managed_devices.json is a flat serial → device map
        return {
            str(key): value
            for key, value in raw.items()
            if isinstance(value, dict) and ("serial_number" in value or "device_type" in value)
        }
    return {}


def _merge_device_maps(
    *maps: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Merge device maps; later maps win on serial collision."""
    combined: dict[str, dict[str, Any]] = {}
    for device_map in maps:
        for serial, info in device_map.items():
            combined[str(serial)] = _flatten_device_info(info)
    return combined


def _load_legacy_device_maps(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Load 0.6.x JSON devices from live paths, then archived migrated copies."""
    base = _easywave_dir(hass)
    registered = _load_json_devices(base / _REGISTERED_FILE)
    managed = _load_json_devices(base / _MANAGED_FILE)
    combined = _merge_device_maps(managed, registered)
    if combined:
        return combined

    # Recovery: setup may have failed before migration archived files, or the
    # user restored an old backup into migrated/. Prefer newest archive names.
    migrated = base / "migrated"
    if not migrated.is_dir():
        return {}
    archived_registered = sorted(
        migrated.glob(f"{_REGISTERED_FILE}*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    archived_managed = sorted(
        migrated.glob(f"{_MANAGED_FILE}*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    recovered_registered = (
        _load_json_devices(archived_registered[0]) if archived_registered else {}
    )
    recovered_managed = (
        _load_json_devices(archived_managed[0]) if archived_managed else {}
    )
    recovered = _merge_device_maps(recovered_managed, recovered_registered)
    if recovered:
        _LOGGER.warning(
            "Loaded %d Easywave devices from archived JSON under %s",
            len(recovered),
            migrated,
        )
    return recovered


def _device_type(info: dict[str, Any]) -> str:
    return str(info.get("device_type") or info.get("type") or "").lower()


def _title(info: dict[str, Any], fallback: str) -> str:
    return str(info.get("name") or info.get("title") or fallback)


def _flatten_device_info(info: dict[str, Any]) -> dict[str, Any]:
    """Flatten managed_devices ``extra_data`` into top-level fields.

    HACS 0.6.10 stores operating_type / sensor lists / indices extras inside
    ``extra_data`` on ManagedDevice while ``registered_devices.json`` keeps
    them flat. Migration accepts both shapes.
    """
    flat = dict(info)
    extra = flat.pop("extra_data", None)
    if isinstance(extra, dict):
        for key, value in extra.items():
            flat.setdefault(key, value)
    return flat


def _sensor_capability_bits(info: dict[str, Any]) -> int:
    """Build CORE bitmask from HACS 0.6.10 sensor fields.

    In 0.6.10 ``sensor_capabilities`` is typically a *list of names*
    (temperature/humidity/battery), not the CORE integer bitmask. Also
    consult ``available_sensors`` / ``sensor_types`` / ``measurement_types``.
    """
    raw = info.get("sensor_capabilities")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)

    names: set[str] = set()
    if isinstance(raw, list):
        names.update(str(item).lower() for item in raw)
    for key in ("available_sensors", "sensor_types", "measurement_types"):
        items = info.get(key) or []
        if isinstance(items, list):
            names.update(str(item).lower() for item in items)

    capabilities = 0
    if "battery" in names or info.get("has_battery"):
        capabilities |= 1 << 0
    if "temperature" in names or "temp" in names:
        capabilities |= 1 << 4
    if "humidity" in names or "hum" in names:
        capabilities |= 1 << 5
    if "wind" in names or "wind_speed" in names:
        capabilities |= 1 << 6
    if "rain" in names:
        capabilities |= 1 << 7
    return capabilities


def _map_transmitter(serial: str, info: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    serial_hex = normalize_serial_hex(info.get("serial_number") or serial)
    device_id = device_id_for_transmitter(serial_hex)
    data = {
        CONF_ENTRY_TYPE: ENTRY_TYPE_TRANSMITTER,
        CONF_TRANSMITTER_SERIAL: serial_hex,
        CONF_OPERATING_TYPE: str(info.get("operating_type") or "1"),
        CONF_BUTTON_COUNT: int(info.get("button_count") or info.get("channels") or 1),
        CONF_GROUPING_MODE: str(
            info.get("grouping_mode") or TRANSMITTER_GROUPING_GROUP
        ),
        CONF_SWITCH_MODE: str(info.get("switch_mode") or TRANSMITTER_SWITCH_IMPULSE),
    }
    if usage := info.get("usage_type"):
        data[CONF_USAGE_TYPE] = str(usage)
    if "cover_mode" in info:
        data[CONF_COVER_MODE] = bool(info["cover_mode"])
    if detected := info.get("detected_button_type"):
        data[CONF_DETECTED_BUTTON_TYPE] = str(detected)
    return device_id, data


def _map_neo_sensor(serial: str, info: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    serial_hex = normalize_serial_hex(info.get("serial_number") or serial)
    device_id = device_id_for_neo_sensor(serial_hex)
    data = {
        CONF_ENTRY_TYPE: ENTRY_TYPE_NEO_SENSOR,
        CONF_SENSOR_SERIAL: serial_hex,
        CONF_SENSOR_CAPABILITIES: _sensor_capability_bits(info),
    }
    return device_id, data


def _map_receiver(serial: str, info: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    serial_hex = normalize_serial_hex(info.get("serial_number") or serial)
    rx11_index = info.get("rx11_index")
    if rx11_index is None and isinstance(info.get("indices"), dict):
        rx11_index = info["indices"].get("rx11") or info["indices"].get("ew_receiver")
    if rx11_index is None:
        _LOGGER.warning("Skipping receiver %s: missing rx11_index", serial_hex[-8:])
        return None
    device_id = device_id_for_receiver(serial_hex)
    data = {
        CONF_ENTRY_TYPE: ENTRY_TYPE_RECEIVER,
        CONF_RECEIVER_SERIAL: serial_hex,
        CONF_RX11_INDEX: int(rx11_index),
        CONF_RECEIVER_KIND: str(info.get("receiver_kind") or RECEIVER_KIND_IMPULSE),
        CONF_OPERATING_MODE: str(info.get("operating_mode") or "1"),
    }
    return device_id, data


def _map_neo_actuator(
    serial: str, info: dict[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    serial_hex = normalize_serial_hex(info.get("serial_number") or serial)
    dtype = _device_type(info)
    type_code = info.get("device_type_code")
    if type_code is None:
        type_code = HACS_ACTUATOR_TYPE_TO_CODE.get(dtype)
    if type_code is None:
        _LOGGER.warning("Skipping unknown neo actuator type %s", dtype)
        return None
    type_code = int(type_code)

    ewneo_index = info.get("ewneo_index")
    if ewneo_index is None and isinstance(info.get("indices"), dict):
        ewneo_index = info["indices"].get("ewb")
    if ewneo_index is None:
        _LOGGER.warning("Skipping neo actuator %s: missing ewneo_index", serial_hex[-8:])
        return None

    gateway_serial = info.get("gateway_serial") or ""
    if gateway_serial:
        gateway_serial = normalize_serial_hex(str(gateway_serial))

    channels = info.get("channels") or DEVICE_TYPE_CODE_TO_CHANNELS.get(type_code, 1)
    device_id = device_id_for_neo_actuator(serial_hex, type_code)
    data = {
        CONF_ENTRY_TYPE: ENTRY_TYPE_NEO_ACTUATOR,
        CONF_ACTUATOR_SERIAL: serial_hex,
        CONF_EWNEO_INDEX: int(ewneo_index),
        CONF_GATEWAY_SERIAL: gateway_serial,
        CONF_DEVICE_TYPE_CODE: type_code,
        CONF_CHANNELS: int(channels),
    }
    if type_code in DEVICE_TYPE_CODE_MOTOR_TYPES:
        initial = info.get("initial_state") if isinstance(info.get("initial_state"), dict) else {}
        runtime = info.get(CONF_RUNTIME_MEASURED)
        if runtime is None:
            runtime = initial.get("runtime_measured")
        if runtime is None:
            runtime = initial.get("supports_position")
        if runtime is not None:
            data[CONF_RUNTIME_MEASURED] = bool(runtime)
    return device_id, data


def _convert_device(
    serial: str, info: dict[str, Any]
) -> tuple[str, str, dict[str, Any]] | None:
    """Return (entry_type, device_id, data) or None if unsupported."""
    dtype = _device_type(info)
    if dtype in {"ew_transmitter", "transmitter"}:
        device_id, data = _map_transmitter(serial, info)
        return ENTRY_TYPE_TRANSMITTER, device_id, data
    if dtype in {"ewneo_sensor", "ew_sensor", "neo_sensor"}:
        device_id, data = _map_neo_sensor(serial, info)
        return ENTRY_TYPE_NEO_SENSOR, device_id, data
    if dtype in {"ew_receiver", "receiver"}:
        mapped = _map_receiver(serial, info)
        if mapped is None:
            return None
        device_id, data = mapped
        return ENTRY_TYPE_RECEIVER, device_id, data
    if dtype in HACS_ACTUATOR_TYPE_TO_CODE or info.get("neo_device"):
        mapped = _map_neo_actuator(serial, info)
        if mapped is None:
            return None
        device_id, data = mapped
        return ENTRY_TYPE_NEO_ACTUATOR, device_id, data
    _LOGGER.info("Skipping unsupported device type during migration: %s", dtype)
    return None


def _archive_json_files(hass: HomeAssistant) -> None:
    """Move legacy JSON files into easywave/migrated/."""
    base = _easywave_dir(hass)
    migrated = base / "migrated"
    migrated.mkdir(parents=True, exist_ok=True)
    for name in (
        _REGISTERED_FILE,
        f"{_REGISTERED_FILE}.bak",
        _MANAGED_FILE,
        "managed_devices_backup.json",
        f"{_MANAGED_FILE}.bak",
        "easywave_devices.json",
        "used_ewb_indices.json",
        "used_ew_receiver_indices.json",
    ):
        src = base / name
        if src.is_file():
            dest = migrated / name
            if dest.exists():
                dest = migrated / f"{name}.{src.stat().st_mtime_ns}"
            shutil.move(str(src), str(dest))
            _LOGGER.info("Archived %s → %s", src.name, dest)


async def async_migrate_json_devices(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, int]:
    """Migrate HACS JSON devices into bucket subentries once.

    Returns a count dict of migrated device types. Automations/triggers are
    intentionally not migrated — users recreate them against the new entities.

    After the first run, ``CONF_JSON_MIGRATION_DONE`` is set so archived files
    under ``config/easywave/migrated/`` cannot recreate devices when the user
    deletes the integration or all bucket subentries.
    """
    counts = {
        ENTRY_TYPE_TRANSMITTER: 0,
        ENTRY_TYPE_NEO_SENSOR: 0,
        ENTRY_TYPE_RECEIVER: 0,
        ENTRY_TYPE_NEO_ACTUATOR: 0,
        "skipped": 0,
    }

    def _mark_migration_done() -> None:
        if entry.data.get(CONF_JSON_MIGRATION_DONE):
            return
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_JSON_MIGRATION_DONE: True},
        )

    if entry.data.get(CONF_JSON_MIGRATION_DONE):
        _LOGGER.debug("JSON migration already completed; skipping")
        return counts

    if get_devices(entry):
        _LOGGER.debug("Subentries already contain devices; skipping JSON migration")
        _mark_migration_done()
        return counts

    combined = await hass.async_add_executor_job(_load_legacy_device_maps, hass)
    if not combined:
        _LOGGER.debug(
            "No legacy Easywave JSON devices found under %s (or migrated/)",
            _easywave_dir(hass),
        )
        _mark_migration_done()
        return counts

    buckets: dict[str, dict[str, dict[str, Any]]] = {
        ENTRY_TYPE_TRANSMITTER: {},
        ENTRY_TYPE_NEO_SENSOR: {},
        ENTRY_TYPE_RECEIVER: {},
        ENTRY_TYPE_NEO_ACTUATOR: {},
    }

    for serial, info in combined.items():
        try:
            converted = _convert_device(serial, info)
        except Exception:  # noqa: BLE001 — one bad record must not abort migration
            _LOGGER.exception(
                "Failed to convert device %s during JSON migration", serial[-8:]
            )
            counts["skipped"] += 1
            continue
        if converted is None:
            counts["skipped"] += 1
            continue
        entry_type, device_id, data = converted
        title = _title(info, device_id)
        buckets[entry_type][device_id] = {CONF_DEVICE_TITLE: title, **data}
        counts[entry_type] += 1

    for entry_type, devices in buckets.items():
        if not devices:
            continue
        bucket_type = ENTRY_TYPE_TO_SUBENTRY_TYPE[entry_type]
        bucket_unique_id = bucket_subentry_unique_id(entry.entry_id, bucket_type)
        existing = None
        for subentry in iter_subentries_of_type(entry, bucket_type):
            if subentry.unique_id == bucket_unique_id:
                existing = subentry
                break
        if existing is None:
            hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType({CONF_DEVICES: devices}),
                    subentry_type=bucket_type,
                    title=await async_bucket_subentry_title(hass, bucket_type),
                    unique_id=bucket_unique_id,
                ),
            )
        else:
            merged = dict(existing.data.get(CONF_DEVICES) or {})
            merged.update(devices)
            hass.config_entries.async_update_subentry(
                entry,
                existing,
                data={CONF_DEVICES: merged},
            )

    total = sum(counts[key] for key in counts if key != "skipped")
    if total:
        await hass.async_add_executor_job(_archive_json_files, hass)
        _LOGGER.warning(
            "Migrated %d Easywave devices from JSON to subentries "
            "(transmitters=%d, sensors=%d, receivers=%d, actuators=%d, skipped=%d). "
            "Please recreate automations and device triggers.",
            total,
            counts[ENTRY_TYPE_TRANSMITTER],
            counts[ENTRY_TYPE_NEO_SENSOR],
            counts[ENTRY_TYPE_RECEIVER],
            counts[ENTRY_TYPE_NEO_ACTUATOR],
            counts["skipped"],
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"devices_migrated_{entry.entry_id}",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="devices_migrated",
            translation_placeholders={
                "count": str(total),
            },
        )

    _mark_migration_done()
    return counts
