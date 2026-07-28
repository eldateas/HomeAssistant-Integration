"""Cleanup helpers for Easywave devices, history, and legacy files."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN
from .devices import get_devices

_LOGGER = logging.getLogger(__name__)

# hass.data[DOMAIN][entry_id] → cached entity_ids per Easywave device identifier
_DATA_ENTITY_CACHE = "entity_ids_by_device"


def _easywave_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.config_dir) / DOMAIN


def _domain_data(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.setdefault(DOMAIN, {})


def _entry_cache(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    return _domain_data(hass).setdefault(entry_id, {})


def _device_identifier(device: dr.DeviceEntry) -> str | None:
    """Return the Easywave identifier stored on a device registry entry."""
    for domain, identifier in device.identifiers:
        if domain == DOMAIN:
            return identifier
    return None


async def async_purge_entity_history(
    hass: HomeAssistant, entity_ids: list[str]
) -> None:
    """Best-effort purge of recorder/logbook history for entity IDs."""
    if not entity_ids:
        return
    if not hass.services.has_service("recorder", "purge_entities"):
        _LOGGER.debug("Recorder purge_entities unavailable; skipping history purge")
        return
    try:
        await hass.services.async_call(
            "recorder",
            "purge_entities",
            {
                "entity_id": entity_ids,
                "keep_days": 0,
            },
            blocking=True,
        )
    except Exception as err:  # noqa: BLE001 - cleanup must not block delete
        _LOGGER.warning("Could not purge history for %s: %s", entity_ids, err)


async def async_purge_device_history(
    hass: HomeAssistant, device_entry: dr.DeviceEntry
) -> None:
    """Purge recorder history for all entities currently linked to a device."""
    entity_registry = er.async_get(hass)
    entity_ids = [
        entry.entity_id
        for entry in er.async_entries_for_device(entity_registry, device_entry.id)
    ]
    await async_purge_entity_history(hass, entity_ids)


@callback
def async_snapshot_entry_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Cache entity IDs per Easywave device id for later purge-on-remove."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    by_device: dict[str, list[str]] = {}

    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if not entity_entry.device_id:
            continue
        device = device_registry.async_get(entity_entry.device_id)
        if device is None:
            continue
        easywave_id = _device_identifier(device)
        if easywave_id is None:
            continue
        by_device.setdefault(easywave_id, []).append(entity_entry.entity_id)

    _entry_cache(hass, entry.entry_id)[_DATA_ENTITY_CACHE] = by_device


def _forget_deleted_device(
    device_registry: dr.DeviceRegistry, device: dr.DeviceEntry
) -> None:
    """Remove an active device and drop its deleted-devices tombstone."""
    device_id = device.id
    identifiers = set(device.identifiers)
    if device_id in device_registry.devices:
        device_registry.async_remove_device(device_id)
    # Drop tombstone so a later re-learn does not restore the old device card.
    for deleted in list(
        device_registry.deleted_devices.get_entries(identifiers, set())
    ):
        device_registry.deleted_devices.pop(deleted.id, None)
    if device_id in device_registry.deleted_devices:
        device_registry.deleted_devices.pop(device_id, None)
    device_registry.async_schedule_save()


async def async_purge_and_remove_device(
    hass: HomeAssistant, device_entry: dr.DeviceEntry
) -> None:
    """Purge history, remove the device, and forget registry tombstones."""
    await async_purge_device_history(hass, device_entry)
    _forget_deleted_device(dr.async_get(hass), device_entry)


async def async_cleanup_devices_not_in_buckets(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Purge/remove child devices that are no longer stored in bucket subentries.

    Called when a bucket subentry is removed or emptied so devices (and their
    history) do not linger or get revived from recorder/registry state.
    """
    remaining = {device.device_id for device in get_devices(entry)}
    remaining.add(entry.entry_id)  # gateway identifier

    cache: dict[str, list[str]] = dict(
        _entry_cache(hass, entry.entry_id).get(_DATA_ENTITY_CACHE) or {}
    )
    device_registry = dr.async_get(hass)

    # Purge via cache first — entities may already be gone after subentry delete.
    for easywave_id, entity_ids in list(cache.items()):
        if easywave_id in remaining:
            continue
        await async_purge_entity_history(hass, entity_ids)
        cache.pop(easywave_id, None)

    for device in list(
        dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    ):
        easywave_id = _device_identifier(device)
        if easywave_id is None or easywave_id in remaining:
            continue
        # History may already be purged via cache; purge again if entities remain.
        await async_purge_and_remove_device(hass, device)
        cache.pop(easywave_id, None)

    _entry_cache(hass, entry.entry_id)[_DATA_ENTITY_CACHE] = cache


async def async_cleanup_all_entry_devices(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Purge history and remove every device belonging to a config entry."""
    cache: dict[str, list[str]] = dict(
        _entry_cache(hass, entry.entry_id).get(_DATA_ENTITY_CACHE) or {}
    )
    for entity_ids in cache.values():
        await async_purge_entity_history(hass, entity_ids)

    device_registry = dr.async_get(hass)
    for device in list(
        dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    ):
        await async_purge_and_remove_device(hass, device)

    _domain_data(hass).pop(entry.entry_id, None)


async def async_remove_legacy_files(hass: HomeAssistant) -> None:
    """Delete ``config/easywave`` (live JSON + migrated archives)."""
    path = _easywave_dir(hass)
    if not path.exists():
        return

    def _rmtree() -> None:
        shutil.rmtree(path, ignore_errors=True)

    await hass.async_add_executor_job(_rmtree)
    _LOGGER.info("Removed Easywave legacy data directory %s", path)
