"""Device helpers for Easywave hub config entries."""

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import translation

from .const import (
    BUCKET_SUBENTRY_TITLES,
    CONF_DEVICE_TITLE,
    DEVICE_SUBENTRY_TYPES,
    DOMAIN,
    bucket_subentry_unique_id,
)
from .entity import EasywaveDeviceEntry

if TYPE_CHECKING:
    from . import EasywaveConfigEntry


def iter_subentries_of_type(
    entry: ConfigEntry, subentry_type: str
) -> Iterator[ConfigSubentry]:
    """Yield subentries of ``subentry_type`` (HA 2026.3+ compatible).

    ``ConfigEntry.get_subentries_of_type`` was added after 2026.3; older cores
    still expose ``entry.subentries`` and must be filtered manually.
    """
    getter = getattr(entry, "get_subentries_of_type", None)
    if callable(getter):
        yield from getter(subentry_type)
        return
    for subentry in entry.subentries.values():
        if subentry.subentry_type == subentry_type:
            yield subentry


def iter_device_buckets(entry: EasywaveConfigEntry) -> Iterator[ConfigSubentry]:
    """Yield device bucket subentries for a gateway config entry."""
    for subentry_type in DEVICE_SUBENTRY_TYPES:
        yield from iter_subentries_of_type(entry, subentry_type)


def _iter_devices_in_bucket(
    subentry: ConfigSubentry,
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Yield device id, title and data stored in a bucket subentry."""
    devices = subentry.data.get(CONF_DEVICES)
    if not isinstance(devices, dict):
        return
    for device_id, device_data in devices.items():
        if not isinstance(device_data, dict):
            continue
        data = dict(device_data)
        title = str(data.pop(CONF_DEVICE_TITLE, device_id))
        yield device_id, title, data


def get_devices(entry: EasywaveConfigEntry) -> list[EasywaveDeviceEntry]:
    """Return configured child devices for a gateway config entry."""
    devices: list[EasywaveDeviceEntry] = []
    for subentry in iter_device_buckets(entry):
        if subentry.unique_id is None:
            continue
        for device_id, title, data in _iter_devices_in_bucket(subentry):
            devices.append(
                EasywaveDeviceEntry(
                    device_id=device_id,
                    title=title,
                    data=data,
                    subentry_id=subentry.subentry_id,
                )
            )
    return devices


def get_device_data(
    entry: EasywaveConfigEntry, device_id: str
) -> dict[str, Any] | None:
    """Return stored data for a child device identifier."""
    for subentry in iter_device_buckets(entry):
        for stored_id, _title, data in _iter_devices_in_bucket(subentry):
            if stored_id == device_id:
                return data
    return None


async def async_bucket_subentry_title(
    hass: HomeAssistant, subentry_type: str
) -> str:
    """Return the localized title for a device-type bucket subentry.

    Uses ``initiate_flow.user`` (not ``entry_type``) so the UI can keep an
    empty ``entry_type`` and avoid a duplicated subtitle under the title.
    """
    translations = await translation.async_get_translations(
        hass, hass.config.language, "config_subentries", integrations=[DOMAIN]
    )
    key = (
        f"component.{DOMAIN}.config_subentries.{subentry_type}.initiate_flow.user"
    )
    return translations.get(key) or BUCKET_SUBENTRY_TITLES.get(
        subentry_type, subentry_type
    )


async def async_sync_bucket_subentry_titles(
    hass: HomeAssistant, entry: EasywaveConfigEntry
) -> None:
    """Update stored bucket titles to match the current HA language."""
    for subentry_type in DEVICE_SUBENTRY_TYPES:
        title = await async_bucket_subentry_title(hass, subentry_type)
        bucket_unique_id = bucket_subentry_unique_id(entry.entry_id, subentry_type)
        for subentry in iter_subentries_of_type(entry, subentry_type):
            if subentry.unique_id != bucket_unique_id:
                continue
            if subentry.title == title:
                continue
            hass.config_entries.async_update_subentry(entry, subentry, title=title)
