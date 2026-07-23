"""The Easywave integration."""

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICES, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)

from .const import (
    CONF_DEVICE_PATH,
    CONF_USB_PID,
    CONF_USB_SERIAL_NUMBER,
    DOMAIN,
    EasywaveGatewayFeature as EasywaveGatewayFeature,
    EasywaveTransmitterFeature as EasywaveTransmitterFeature,
    get_frequency_for_pid,
    is_country_allowed_for_frequency,
)
from .coordinator import EasywaveCoordinator
from .devices import async_sync_bucket_subentry_titles, iter_device_buckets
from .gateway_device import update_gateway_device
from .migration import async_migrate_json_devices
from .transceiver import RX11Transceiver

_LOGGER = logging.getLogger(__name__)


@dataclass
class EasywaveRuntimeData:
    """Runtime data for the Easywave integration."""

    coordinator: EasywaveCoordinator


type EasywaveConfigEntry = ConfigEntry[EasywaveRuntimeData]

_PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
]


def _normalize_hub_unique_id(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rewrite legacy HACS 0.6 unique_id ``rx11_*`` to CORE ``easywave_*``."""
    unique_id = entry.unique_id
    if not unique_id or not unique_id.startswith("rx11_"):
        return
    serial = unique_id.removeprefix("rx11_")
    if not serial or serial == "unknown":
        serial = str(entry.data.get(CONF_USB_SERIAL_NUMBER) or "").strip() or "unknown"
    new_unique_id = f"easywave_{serial}"
    if new_unique_id == unique_id:
        return
    _LOGGER.info(
        "Normalizing Easywave hub unique_id %s → %s", unique_id, new_unique_id
    )
    hass.config_entries.async_update_entry(entry, unique_id=new_unique_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry schema to the current VERSION.

    HACS 0.6.x already used VERSION 2. This handler covers older VERSION 1
    entries and keeps the stored major version aligned so setup can proceed.
    """
    if entry.version > 2:
        _LOGGER.error(
            "Easywave config entry %s has unsupported version %s",
            entry.title,
            entry.version,
        )
        return False

    if entry.version < 2:
        data = dict(entry.data)
        hass.config_entries.async_update_entry(entry, data=data, version=2)
        _LOGGER.info("Migrated Easywave config entry '%s' to version 2", entry.title)

    _normalize_hub_unique_id(hass, entry)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EasywaveConfigEntry) -> bool:
    """Set up the Easywave gateway config entry."""
    _normalize_hub_unique_id(hass, entry)
    await async_migrate_json_devices(hass, entry)
    await async_sync_bucket_subentry_titles(hass, entry)

    usb_pid = entry.data.get(CONF_USB_PID)
    frequency = get_frequency_for_pid(usb_pid)
    country_code = hass.config.country

    if frequency and not is_country_allowed_for_frequency(frequency, country_code):
        _LOGGER.warning(
            "This hardware operates on %s, which is not permitted in "
            "your configured region (%s). Integration disabled for regulatory compliance",
            frequency,
            country_code or "unknown",
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"frequency_not_permitted_{entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="frequency_not_permitted",
            translation_placeholders={
                "frequency": frequency,
                "country": country_code or "unknown",
            },
        )
        return False

    ir.async_delete_issue(hass, DOMAIN, f"frequency_not_permitted_{entry.entry_id}")

    transceiver = RX11Transceiver(hass, entry.data.get(CONF_DEVICE_PATH))
    coordinator = EasywaveCoordinator(hass, transceiver, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = EasywaveRuntimeData(coordinator=coordinator)

    update_gateway_device(hass, entry, transceiver)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: EasywaveConfigEntry) -> None:
    """Reload the entry when device subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EasywaveConfigEntry) -> bool:
    """Unload the gateway config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)
    if unload_ok:
        await entry.runtime_data.coordinator.async_shutdown()
    return unload_ok


def _device_identifier(device: dr.DeviceEntry) -> str | None:
    """Return the Easywave identifier stored on a device registry entry."""
    for domain, identifier in device.identifiers:
        if domain == DOMAIN:
            return identifier
    return None


async def _async_purge_device_history(
    hass: HomeAssistant, device_entry: dr.DeviceEntry
) -> None:
    """Remove recorder/logbook history for all entities of a device.

    Matches the 0.6.10 delete UX so a later re-learn of the same serial does not
    revive old activity. Storage schema stays CORE-compatible; this only calls
    the recorder service.
    """
    entity_registry = er.async_get(hass)
    entity_ids = [
        entry.entity_id
        for entry in er.async_entries_for_device(entity_registry, device_entry.id)
    ]
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
        _LOGGER.debug(
            "Purged history for %d entities of device %s",
            len(entity_ids),
            device_entry.id,
        )
    except Exception as err:  # noqa: BLE001 - best-effort; delete must continue
        _LOGGER.warning(
            "Could not purge history for device %s: %s",
            device_entry.name or device_entry.id,
            err,
        )


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: EasywaveConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Handle removal of a device via the three-dot menu."""
    if (DOMAIN, config_entry.entry_id) in device_entry.identifiers:
        return False

    easywave_id = _device_identifier(device_entry)
    if easywave_id is None:
        return False

    for subentry in iter_device_buckets(config_entry):
        devices = subentry.data.get(CONF_DEVICES)
        if not isinstance(devices, dict) or easywave_id not in devices:
            continue

        await _async_purge_device_history(hass, device_entry)

        updated_devices = dict(devices)
        del updated_devices[easywave_id]
        if updated_devices:
            hass.config_entries.async_update_subentry(
                config_entry,
                subentry,
                data={CONF_DEVICES: updated_devices},
            )
        else:
            hass.config_entries.async_remove_subentry(
                config_entry, subentry.subentry_id
            )
        return True

    return False
