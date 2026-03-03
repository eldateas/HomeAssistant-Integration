"""EASYWAVE integration initialization with transceiver modularity."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import (
    DOMAIN,
    CONF_DEVICE_PATH,
    CONF_DEVICE_NAME,
    CONF_USB_VID,
    CONF_USB_PID,
    CONF_USB_SERIAL_NUMBER,
    CONF_USB_MANUFACTURER,
    CONF_USB_PRODUCT,
    CONF_FW_VERSION,
    CONF_HW_VERSION,
    CONF_TRANSCEIVER_TYPE,
    DEVICE_SCAN_INTERVAL,
    DEFAULT_DEVICE_NAME,
    EVENT_DEVICE_ADDED,
    usb_device_name,
)
from .transceivers import TransceiverFactory, TransceiverType
from .coordinator import EasywaveCoordinator
from .translations import translate, get_language

_LOGGER = logging.getLogger(__name__)


async def _find_usb_device_path(hass: HomeAssistant, entry: ConfigEntry) -> tuple[str, dict[str, Any]]:
    """Find the actual USB device path using USB identification.
    
    Uses VID/PID/SerialNumber to identify the device, allowing USB port changes
    without reconfiguration. Falls back to configured path if available.
    
    Args:
        hass: Home Assistant instance
        entry: Config entry with USB identification data
        
    Returns:
        Tuple of (device_path, device_info) where device_info contains VID, PID, SN, manufacturer, product
    """
    import os
    import serial.tools.list_ports
    
    entry_data = entry.data
    
    # Extract USB identification from config entry
    vid = entry_data.get(CONF_USB_VID)
    pid = entry_data.get(CONF_USB_PID)
    serial_number = entry_data.get(CONF_USB_SERIAL_NUMBER, "unknown")
    configured_path = entry_data.get(CONF_DEVICE_PATH)
    
    # If we have USB identification, use it to find the device
    if vid is not None and pid is not None:
        _LOGGER.info("🔍 Searching for RX11 device: VID=0x%04X, PID=0x%04X, SN=%s", vid, pid, serial_number)
        
        try:
            # Run blocking I/O operation in executor to avoid blocking event loop
            def _list_ports():
                return list(serial.tools.list_ports.comports())
            
            ports = await hass.async_add_executor_job(_list_ports)
            
            # Look for EASYWAVE USB device by VID/PID
            for port in ports:
                if port.vid == vid and port.pid == pid:
                    # If we have a serial number, check if it matches
                    if serial_number != "unknown" and port.serial_number:
                        if port.serial_number == serial_number:
                            _LOGGER.info("✅ Found RX11 at %s (VID:0x%04X PID:0x%04X SN:%s)", 
                                       port.device, port.vid, port.pid, port.serial_number)
                            mfr, prod = usb_device_name(port.vid, port.pid)
                            device_info = {
                                "device": port.device,
                                "vid": port.vid,
                                "pid": port.pid,
                                "serial_number": port.serial_number,
                                "manufacturer": mfr,
                                "product": prod,
                                "location": port.location,
                            }
                            return port.device, device_info
                    else:
                        # No serial number to check, use first matching VID/PID
                        _LOGGER.info("✅ Found RX11 at %s (VID:0x%04X PID:0x%04X)", 
                                   port.device, port.vid, port.pid)
                        mfr, prod = usb_device_name(port.vid, port.pid)
                        device_info = {
                            "device": port.device,
                            "vid": port.vid,
                            "pid": port.pid,
                            "serial_number": port.serial_number or "unknown",
                            "manufacturer": mfr,
                            "product": prod,
                            "location": port.location,
                        }
                        return port.device, device_info
            
            # USB scan didn't find device — only warn if there's no configured path fallback
            if configured_path and os.path.exists(configured_path):
                _LOGGER.debug("USB scan didn't find RX11 by VID/PID/SN, using configured path fallback: %s", configured_path)
            else:
                _LOGGER.debug("USB scan: no device with VID:0x%04X PID:0x%04X SN:%s", vid, pid, serial_number)
        
        except Exception as e:
            _LOGGER.error("❌ Error searching for USB device: %s", e)
    
    # Fallback: use configured path if it exists
    if configured_path and os.path.exists(configured_path):
        _LOGGER.info("✅ Using configured device path: %s", configured_path)
        mfr, prod = usb_device_name(vid, pid)
        device_info = {
            "device": configured_path,
            "vid": vid,
            "pid": pid,
            "serial_number": serial_number,
            "manufacturer": entry_data.get(CONF_USB_MANUFACTURER) or mfr,
            "product": entry_data.get(CONF_USB_PRODUCT) or prod,
        }
        return configured_path, device_info
    
    # No device found — caller handles the offline-mode warning
    _LOGGER.debug("USB scan: no RX11 device found and configured path not available")
    device_info = {
        "device": configured_path or "unknown",
        "vid": vid,
        "pid": pid,
        "serial_number": serial_number,
    }
    return configured_path or "unknown", device_info


# Platforms to set up
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.LIGHT,
    Platform.COVER,
    Platform.SELECT,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EASYWAVE from a config entry with transceiver modularity.
    
    IMPORTANT: This function does NOT raise ConfigEntryNotReady when the USB
    device is not found.  Instead, the integration starts in "offline mode":
    - All registered devices are loaded from registered_devices.json
    - Entity objects are created but show as "unavailable"
    - The coordinator's update loop reconnects automatically when the USB
      device appears
    
    This prevents the scenario where entities disappear completely during
    HA startup when the USB device is temporarily not detected.
    """
    _LOGGER.info("Setting up EASYWAVE integration for entry %s", entry.entry_id)
    
    # Guard against double setup
    hass.data.setdefault(DOMAIN, {})
    if entry.entry_id in hass.data[DOMAIN]:
        _LOGGER.warning("⚠️ Entry %s already set up, skipping", entry.entry_id)
        return True
    
    # Get configuration data
    transceiver_type_str = entry.data.get(CONF_TRANSCEIVER_TYPE)
    device_name = entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)
    
    if not transceiver_type_str:
        _LOGGER.error("No transceiver type specified in config entry")
        raise ConfigEntryNotReady("Transceiver type missing")
    
    # Find USB device using VID/PID/SerialNumber identification
    # This handles USB port changes automatically
    actual_device_path, device_info = await _find_usb_device_path(hass, entry)
    
    # If USB device is not found, proceed in offline mode instead of raising ConfigEntryNotReady.
    # The coordinator will create entities from registered_devices.json (showing as "unavailable")
    # and automatically reconnect when the USB device appears.
    usb_offline = (actual_device_path == "unknown")
    if usb_offline:
        _LOGGER.warning("⚠️ RX11 USB Transceiver nicht gefunden — Offline-Modus. "
                       "Entitäten werden erstellt, sind aber nicht verfügbar bis das Gerät verbunden wird.")
        # Use None as device_path — transceiver.connect() will search by VID/PID
        actual_device_path = None
    else:
        # Update config entry with latest device information if path changed
        stored_path = entry.data.get(CONF_DEVICE_PATH)
        if actual_device_path != stored_path:
            _LOGGER.info("🔄 USB device port changed: %s → %s", stored_path, actual_device_path)
            # Update config entry with new path (persistent across restarts)
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    CONF_DEVICE_PATH: actual_device_path,
                    CONF_USB_SERIAL_NUMBER: device_info.get("serial_number", "unknown"),
                }
            )
    
    try:
        transceiver_type = TransceiverType(transceiver_type_str)
    except ValueError:
        _LOGGER.error("Invalid transceiver type: %s", transceiver_type_str)
        raise ConfigEntryNotReady(f"Invalid transceiver type: {transceiver_type_str}")
    
    _LOGGER.info("Setting up %s transceiver at %s (SN:%s, offline=%s)", 
                transceiver_type.value, actual_device_path or "(none)", 
                device_info.get("serial_number", "unknown"), usb_offline)
    
    # Step 1: Create and setup transceiver
    try:
        transceiver = TransceiverFactory.create_transceiver(transceiver_type, actual_device_path)
        _LOGGER.info("Created %s transceiver instance", transceiver_type.value)
        
        # Set USB device identity from config entry / scan results
        usb_serial = device_info.get("serial_number") or entry.data.get(CONF_USB_SERIAL_NUMBER, "unknown")
        usb_vid = device_info.get("vid") or entry.data.get(CONF_USB_VID)
        usb_pid = device_info.get("pid") or entry.data.get(CONF_USB_PID)
        if hasattr(transceiver, 'update_usb_identity'):
            transceiver.update_usb_identity(
                serial_number=usb_serial,
                vid=usb_vid,
                pid=usb_pid,
            )
            _LOGGER.debug("Set USB identity: VID=0x%04X PID=0x%04X SN=%s",
                         usb_vid or 0, usb_pid or 0, usb_serial)
        elif hasattr(transceiver, 'set_usb_serial_number'):
            transceiver.set_usb_serial_number(usb_serial)
            _LOGGER.debug("Set USB Serial Number: %s", usb_serial)
        
    except Exception as e:
        _LOGGER.error("Failed to create transceiver: %s", e)
        raise ConfigEntryNotReady(f"Failed to create {transceiver_type.value} transceiver: {e}")
    
    # Step 2: Create coordinator
    try:
        coordinator = EasywaveCoordinator(
            hass=hass,
            transceiver=transceiver,
            config_entry=entry,
            update_interval=DEVICE_SCAN_INTERVAL,
        )
        
        # Migrate old configuration files to DeviceManager (one-time)
        _LOGGER.debug("Checking for legacy device configuration files...")
        from .device_migration import migrate_to_device_manager
        await migrate_to_device_manager(hass, entry.entry_id)
        
        # NOTE: Entity migration moved AFTER restore_registered_devices_only (Step 3.1)
        # to ensure _registered_devices is loaded as authoritative source before
        # any cleanup runs. Previously this ran here and could delete entities
        # for devices that exist in registered_devices.json but not managed_devices.json.
        
        # Setup coordinator — loads device data and optionally connects to USB.
        # Returns True even when USB is offline (data was loaded successfully).
        # Only returns False when data loading itself fails.
        if not await coordinator.async_setup():
            raise ConfigEntryNotReady("Failed to load device data")
            
        _LOGGER.info("Coordinator setup completed (transceiver_connected=%s)", 
                     coordinator.transceiver.is_connected if coordinator.transceiver else False)
        
    except ConfigEntryNotReady:
        raise
    except Exception as e:
        _LOGGER.warning("⚠️ Setup incomplete: %s - integration will retry", e)
        raise ConfigEntryNotReady(f"Setup incomplete: {e}")
    
    # Store coordinator in hass data (DOMAIN dict already initialized at top of function)
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    # Step 2.5: Purge activity log for RX11 gateway sensor to start fresh
    gateway_entity_id = f"sensor.rx11_usb_transceiver_{entry.entry_id[:8]}_gateway_status"
    # Also try common naming patterns
    gateway_entity_ids = [
        gateway_entity_id,
        f"sensor.rx11_usb_transceiver_verbindungsstatus",
        f"sensor.rx11_usb_transceiver_connection_status",
        f"sensor.easywave_gateway_verbindungsstatus",
        f"sensor.easywave_gateway_connection_status",
    ]
    try:
        await hass.services.async_call(
            "recorder",
            "purge_entities",
            {
                "entity_id": gateway_entity_ids,
                "keep_days": 0,
            },
            blocking=False,
        )
        _LOGGER.debug("🧹 Purged activity log for gateway sensor")
    except Exception as e:
        _LOGGER.debug("Could not purge gateway sensor history (this is normal on first setup): %s", e)
    
    # Step 3: Restore registered devices and sync DeviceManager BEFORE setting up platforms
    _LOGGER.debug("Restoring registered devices...")
    await coordinator.restore_registered_devices_only()
    _LOGGER.info("✅ Restored %d registered devices", len(coordinator.get_all_registered_devices()))
    
    # Step 3.1: Entity migration - NOW safe because _registered_devices is loaded
    # Uses BOTH registered_devices AND DeviceManager as valid_serials to prevent data loss
    try:
        from .entity_migration import migrate_entities_if_needed
        # Combine both sources so no device is considered "orphaned" by mistake
        all_known_serials = set(coordinator.get_all_registered_devices().keys())
        all_known_serials.update(coordinator.device_manager.get_all_devices().keys())
        
        # Build a combined device dict for migration checks
        # Convert ManagedDevice objects to dicts for migration function
        combined_devices = {}
        for serial, device in coordinator.device_manager.get_all_devices().items():
            combined_devices[serial] = device.to_dict() if hasattr(device, 'to_dict') else device
        combined_devices.update(coordinator.get_all_registered_devices())
        
        migration_report = await migrate_entities_if_needed(hass, entry.entry_id, combined_devices)
        
        # Log v0.6.4 migration results
        v064_devices = migration_report.get("v064_devices_migrated", 0)
        v064_entities = migration_report.get("v064_entities_migrated", 0)
        if v064_devices > 0 or v064_entities > 0:
            _LOGGER.info("🔄 v0.6.4 migration: %d devices, %d entities migrated to new identifier format",
                        v064_devices, v064_entities)

        if migration_report.get("migrated_devices", 0) > 0:
            _LOGGER.info("🔄 Entity migration completed: %d devices, %d entities updated",
                        migration_report["migrated_devices"],
                        len(migration_report.get("recreated_entities", [])))
        
        duplicates = migration_report.get("duplicate_entities_removed", 0)
        legacy_battery = migration_report.get("legacy_battery_sensors_removed", 0)
        if duplicates > 0 or legacy_battery > 0:
            _LOGGER.warning("🧹 Entity cleanup: %d duplicates, %d legacy battery sensors removed",
                        duplicates, legacy_battery)
    except Exception as e:
        _LOGGER.warning("⚠️ Entity migration check failed (non-fatal): %s", e)
    
    # Step 3.5: Clear EWB filter BEFORE platforms setup to prevent ERR_FILTER_OUT_OF_MEM
    if coordinator.transceiver and coordinator.transceiver.is_connected:
        if hasattr(coordinator.transceiver, 'rx11_ewb_clear_filter'):
            try:
                clear_result = await coordinator.transceiver.rx11_ewb_clear_filter()
                if clear_result:
                    _LOGGER.info("✅ EWB-Filter erfolgreich geleert vor Platform-Setup")
                else:
                    _LOGGER.warning("⚠️ EWB-Filter konnte nicht geleert werden")
            except Exception as e:
                _LOGGER.error("❌ Exception beim Leeren des EWB-Filters: %s", e)
    
    # Step 4: Setup platforms
    _LOGGER.debug("Setting up platforms...")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.info("✅ Platforms setup complete")
    
    # Step 4.1: Setup central event dispatcher
    # All platforms have now registered their handlers via coordinator.register_platform_handler()
    # Now setup the dispatcher that will coordinate events to all platforms
    _LOGGER.debug("Setting up central event dispatcher...")
    dispatcher_cleanup = coordinator.setup_event_dispatchers()
    entry.async_on_unload(dispatcher_cleanup)
    _LOGGER.info("✅ Central event dispatcher setup complete")
    
    # Step 4.5: Setup registry listener for device enable/disable
    # HA natively handles: device disabled → entities get disabled_by=DEVICE
    # HA natively handles: device re-enabled → entities get disabled_by=None
    # BUT: HA does NOT recreate entity Python objects — we must trigger platform re-setup.
    # IMPORTANT: We only reload PLATFORMS (not the full config entry) so the
    # coordinator and transceiver connection (RX11) stay alive.
    device_registry = dr.async_get(hass)
    _reload_timer = None  # Debounce handle for platform reload
    
    # Track disabled devices to detect genuine re-enable transitions.
    # Without this, ANY device_registry_updated event for an active device
    # (disabled_by=None) would trigger a reload — including during startup
    # when entity registration causes device updates. We only want to reload
    # when a device transitions from disabled → enabled.
    _disabled_device_ids: set[str] = set()
    for dev_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if dev_entry.disabled_by is not None:
            _disabled_device_ids.add(dev_entry.id)
    _LOGGER.debug("Tracking %d currently disabled devices for re-enable detection", len(_disabled_device_ids))
    
    @callback
    def _on_device_registry_update(event):
        """Handle device registry updates (device re-enable).
        
        When user re-enables a device, HA automatically clears disabled_by on entities.
        However, entity Python objects only get created during platform setup.
        We schedule a debounced PLATFORM reload (not full config reload) so the
        transceiver connection stays alive — no RX11 reconnect needed.
        
        IMPORTANT: We track disabled_device_ids to distinguish a genuine re-enable
        (disabled → enabled) from any other device update. Without this, normal
        device registry updates during startup would trigger spurious reloads.
        """
        nonlocal _reload_timer
        try:
            action = event.data.get("action")
            device_id = event.data.get("device_id")
            
            if not device_id or action != "update":
                return
            
            device_entry = device_registry.async_get(device_id)
            if not device_entry:
                return
            
            # Only react to our integration's devices
            if entry.entry_id not in (device_entry.config_entries or []):
                return
            
            # Track disable/enable transitions
            if device_entry.disabled_by is not None:
                # Device was just disabled — remember it
                _disabled_device_ids.add(device_id)
                _LOGGER.debug("Device %s disabled (disabled_by=%s) — tracking for future re-enable", 
                            device_id[-8:], device_entry.disabled_by)
                return
            
            # Device is enabled. Only react if it was PREVIOUSLY disabled (genuine re-enable).
            if device_id not in _disabled_device_ids:
                # Device was already enabled — this is a normal update (name change, entity count, etc.)
                return
            
            # Genuine re-enable: remove from tracking and schedule platform reload
            _disabled_device_ids.discard(device_id)
            
            _LOGGER.info("🔄 Device %s re-enabled — scheduling platform reload to restore entities "
                        "(transceiver connection stays alive)", device_id[-8:])
            
            # Cancel any pending reload (debounce)
            if _reload_timer is not None:
                _reload_timer()
                _reload_timer = None
            
            # Schedule platform reload with 1s delay:
            # - HA needs time to re-enable entities (clear disabled_by) before reload
            # - Debounce in case multiple devices are re-enabled at once
            async def _delayed_platform_reload(_now=None):
                nonlocal _reload_timer
                _reload_timer = None
                try:
                    _LOGGER.info("🔄 Reloading platforms to restore entities (keeping transceiver connection)")
                    
                    # 1) Unload all platforms (removes entity objects + event listeners)
                    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
                    
                    # 2) Clear stale coordinator state from the previous platform lifecycle.
                    #    _platform_handlers held closures over the OLD async_add_entities —
                    #    they become invalid after unload.  _dispatched_devices would prevent
                    #    the central dispatcher from processing restored devices.
                    coordinator._platform_handlers.clear()
                    coordinator._dispatched_devices.clear()
                    
                    # 3) Reset session-level entity tracking so platforms don't think
                    #    entities were "already created this session".
                    from .entity_registry import get_entity_registry
                    get_entity_registry().clear()
                    
                    # 4) Re-setup all platforms — each one registers fresh handlers
                    #    and restores entities from coordinator.get_all_devices().
                    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
                    _LOGGER.info("✅ Platform reload complete — transceiver connection maintained")
                except Exception as exc:
                    _LOGGER.error("❌ Platform reload failed: %s", exc, exc_info=True)
            
            _reload_timer = async_call_later(hass, 1, _delayed_platform_reload)
            
        except Exception as e:
            _LOGGER.error("Error handling device registry update: %s", e)
    
    remove_device_listener = hass.bus.async_listen("device_registry_updated", _on_device_registry_update)
    entry.async_on_unload(remove_device_listener)
    _LOGGER.debug("✅ Registry listener installed for device re-enable sync")
    
    # Step 5: Fire device events AFTER platforms are ready
    _LOGGER.debug("Firing device events after platform setup...")
    await coordinator.fire_pending_device_events()
    _LOGGER.info("✅ Device events fired")
    
    # Note: Device events are now fired after platform setup for proper entity creation
    
    # Step 6: Setup debug services
    try:
        from .services import async_setup_services
        await async_setup_services(hass, entry)
        _LOGGER.info("✅ Debug services setup complete")
    except Exception as e:
        _LOGGER.error("Failed to setup services: %s", e)
        # Continue without services
    
    # Step 7: Initial data fetch
    try:
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.info("✅ Initial data refresh completed")
        
        # Step 8: Restore gateway filters after initial connection
        if coordinator.transceiver.is_connected:
            await coordinator._restore_gateway_filters()
        
    except Exception as e:
        if coordinator.transceiver and not coordinator.transceiver.is_connected:
            # Expected in offline mode — don't fail setup, entities are already created
            _LOGGER.debug("Initial data refresh skipped (offline): %s", e)
        else:
            _LOGGER.error("Failed to perform initial data refresh: %s", e)
            await coordinator.async_shutdown()
            raise ConfigEntryNotReady(f"Initial data refresh failed: {e}")
    
    _LOGGER.info("EASYWAVE integration setup complete for %s (%s)", 
                device_name, transceiver_type.value)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up all associated devices."""
    _LOGGER.debug("Unloading EASYWAVE integration")
    
    # Unload platforms first
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    # Unload services
    try:
        from .services import async_unload_services
        await async_unload_services(hass)
    except Exception as e:
        _LOGGER.error("Failed to unload services: %s", e)
    
    # Get coordinator and clean up
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        try:
            # Clean up orphaned devices from HA Device Registry
            # This should ONLY happen on unload, not on startup
            _LOGGER.info("🧹 Cleaning up orphaned devices during integration unload...")
            await coordinator._cleanup_orphaned_ha_devices(manual_call=True)
            
            # Shutdown coordinator
            await coordinator.async_shutdown()
            _LOGGER.info("✅ Coordinator shutdown completed")
        except Exception as e:
            _LOGGER.error("❌ Error during coordinator shutdown/cleanup: %s", e)
    
    # Remove from hass data
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    
    _LOGGER.info("EASYWAVE integration unloaded successfully")
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    _LOGGER.debug("Reloading EASYWAVE integration")
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old config entries to new format."""
    version = config_entry.version
    
    _LOGGER.debug("Migrating EASYWAVE config entry from version %s", version)
    
    if version == 1:
        # Migrate to new structure (version 2)
        new_data = {**config_entry.data}
        new_options = {**config_entry.options}
        
        # Add transceiver type if missing (assume RX11 for old entries)
        if CONF_TRANSCEIVER_TYPE not in new_data:
            new_data[CONF_TRANSCEIVER_TYPE] = TransceiverType.RX11.value
            _LOGGER.info("Added transceiver type RX11 during migration")
        
        # Add default scan interval if missing
        if "scan_interval" not in new_data:
            new_data["scan_interval"] = 30

        # Use async_update_entry to change version and data
        hass.config_entries.async_update_entry(
            config_entry, 
            data=new_data, 
            options=new_options,
            version=2
        )
        
        _LOGGER.info("Migrated EASYWAVE config entry to version 2")
    
    return True


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the EASYWAVE component."""
    # YAML configuration not supported - use UI
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Remove a device from the integration via the UI menu.
    
    This is called when a user clicks "Delete" on a device in the integration view.
    Returns True if removal was successful, False otherwise.
    """
    _LOGGER.info("🗑️ User requested removal of device: %s (%s)", 
                device_entry.name, device_entry.id)
    
    # Get coordinator
    coordinator = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    if not coordinator:
        _LOGGER.error("❌ Coordinator not found for config entry %s", config_entry.entry_id)
        return False
    
    # Extract device identifier from device identifiers
    # The identifier may be a registration_id (UUID) or serial_number (legacy)
    identifier_value = None
    for identifier in device_entry.identifiers:
        if identifier[0] == DOMAIN:
            identifier_value = identifier[1]
            break
    
    if not identifier_value:
        _LOGGER.warning("⚠️ Could not find identifier for device %s", device_entry.id)
        # Allow deletion anyway - this might be an orphaned device
        return True
    
    # ── RX11 gateway protection ──────────────────────────────────
    # Must be checked BEFORE serial resolution because the gateway
    # identifier ("{entry_id}_gateway") is not in _registered_devices
    # and get_serial_by_ha_identifier() would return None.
    is_rx11_gateway = (
        identifier_value.endswith("_gateway") or
        "gateway" in identifier_value.lower() or
        identifier_value == config_entry.entry_id
    )
    
    if is_rx11_gateway:
        _LOGGER.info("ℹ️ User tried to delete RX11 transceiver - this is not allowed via UI")
        raise HomeAssistantError(
            translate("error.cannot_delete_rx11", hass=hass)
        )
    
    # Resolve UUID-based identifier back to serial_number
    serial_number = coordinator.get_serial_by_ha_identifier(identifier_value)
    if not serial_number:
        _LOGGER.warning("⚠️ Could not resolve identifier %s to serial_number", identifier_value[-8:])
        # Allow deletion anyway — might be an orphaned device
        return True
    
    try:
        # Use coordinator's removal method for proper cleanup
        success = await coordinator.async_remove_device(serial_number, force=True)
        
        if success:
            _LOGGER.info("✅ Device %s removed successfully via UI", serial_number[-8:] if len(serial_number) > 8 else serial_number)
            
            # Fire event for UI notifications
            hass.bus.async_fire("easywave_device_removed", {
                "serial_number": serial_number,
                "device_name": device_entry.name,
                "removal_method": "ui_menu"
            })
        else:
            _LOGGER.warning("⚠️ Coordinator removal returned False for %s, allowing device registry cleanup anyway", serial_number[-8:] if len(serial_number) > 8 else serial_number)
        
        # Always return True to allow Home Assistant to clean up the device registry
        return True
        
    except Exception as e:
        _LOGGER.error("❌ Error removing device %s: %s", serial_number, e, exc_info=True)
        # Return True anyway to allow cleanup of orphaned devices
        return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a config entry with proper cleanup - removes ALL devices and persistent data.
    
    Stellt sicher, dass beim Löschen der Integration ALLE Dateien und Daten gelöscht werden,
    damit bei einer Neu-Installation ein sauberer Start erfolgt.
    """
    import homeassistant.helpers.entity_registry as er
    
    _LOGGER.info("🗑️ Removing EASYWAVE config entry and all associated data...")
    
    # Step 1: Reset global entity registry (prevents old names from persisting)
    try:
        from .entity_registry import reset_entity_registry
        reset_entity_registry()
        _LOGGER.info("✅ Global entity registry reset")
    except Exception as e:
        _LOGGER.warning("⚠️ Could not reset entity registry: %s", e)
    
    # Step 2: Get coordinator for shutdown and index reset
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator:
        # Entity persistence is automatically saved on shutdown by coordinator.async_shutdown()
        # No need to clear as persistence manager handles it
        coordinator._devices_with_fired_events.clear()
        coordinator._known_devices.clear()
        coordinator.devices.clear()
        coordinator._registered_devices.clear()
        _LOGGER.info("✅ Coordinator tracking data cleared")
        
        # Reset all indices before shutdown
        try:
            coordinator.reset_all_ew_receiver_indices()
            _LOGGER.info("✅ Easywave Receiver indices reset")
        except Exception as e:
            _LOGGER.warning("⚠️ Could not reset EW receiver indices: %s", e)
        
        try:
            coordinator.reset_all_ewb_indices()
            _LOGGER.info("✅ EWB indices reset")
        except Exception as e:
            _LOGGER.warning("⚠️ Could not reset EWB indices: %s", e)
        
        await coordinator.async_shutdown()
    
    # Step 3: Remove ALL entities for this integration from entity registry
    entity_registry = er.async_get(hass)
    entities_to_remove = []
    
    for entity in list(entity_registry.entities.values()):
        if entity.platform == DOMAIN:
            entities_to_remove.append(entity.entity_id)
    
    for entity_id in entities_to_remove:
        try:
            entity_registry.async_remove(entity_id)
            _LOGGER.debug("🗑️ Removed entity: %s", entity_id)
        except Exception as e:
            _LOGGER.warning("⚠️ Could not remove entity %s: %s", entity_id, e)
    
    _LOGGER.info("✅ Removed %d entities from entity registry", len(entities_to_remove))
    
    # Step 4: Remove all devices associated with this config entry from device registry
    device_registry = dr.async_get(hass)
    devices_to_remove = []
    
    for device in list(device_registry.devices.values()):
        # Check if device belongs to this config entry
        if entry.entry_id in device.config_entries:
            devices_to_remove.append(device.id)
    
    # Remove each device
    for device_id in devices_to_remove:
        try:
            device_registry.async_remove_device(device_id)
            _LOGGER.debug("🗑️ Removed device: %s", device_id)
        except Exception as e:
            _LOGGER.warning("⚠️ Could not remove device %s: %s", device_id, e)
    
    _LOGGER.info("✅ Removed %d devices from device registry", len(devices_to_remove))
    
    # Step 5: Clean up ALL persistent data files from BOTH directories (COMPREHENSIVE)
    import os
    
    # Umfassendere Liste: Alle möglichen Dateien die durch die Integration erstellt werden
    # Diese Liste wird erweitert, um auch neue oder zukünftige Dateien zu erfassen
    files_to_remove = [
        # Aktuelle koordinator-Dateien
        "registered_devices.json",
        "registered_devices.json.bak",
        # DeviceManager (whitelist) Dateien
        "managed_devices.json", 
        "managed_devices_backup.json",
        "managed_devices.json.bak",
        # Index-Tracking Dateien
        "used_ewb_indices.json",
        "used_ew_receiver_indices.json",
        "ewb_indices.json",
        "ew_receiver_indices.json",
        # Legacy migration files (wenn vorhanden)
        "device_whitelist.json",
        "device_whitelist.json.migrated_backup",
        "device_whitelist.json.bak",
        "easywave_devices.json",
        "easywave_devices.json.migrated_backup",
        "easywave_devices.json.bak",
        "easywave_device_registry.json",
        "easywave_device_registry.json.migrated_backup",
        "easywave_device_registry.json.bak",
        # State Manager Dateien (Entity Persistence)
        f"easywave_entity_persistence_{entry.entry_id}.json",
        f"entity_persistence_{entry.entry_id}.json",
        # Device Backup Dateien
        f"device_backup_{entry.entry_id}.json",
        "device_backup.json",
        # Device Lifecycle Dateien (wenn separat gespeichert)
        f"device_lifecycle_{entry.entry_id}.json",
        "device_lifecycle.json",
        # Transceiver Konfiguration
        f"transceiver_config_{entry.entry_id}.json",
        "transceiver_config.json",
        # Learning Modus / Setup Dateien
        "learning_mode.json",
        f"learning_{entry.entry_id}.json",
        # Cache Dateien
        ".cache",
        f".cache_{entry.entry_id}",
        # Log Dateien
        f"easywave_{entry.entry_id}.log",
        f"easywave_{entry.entry_id}.log",
    ]
    
    # Clean up BOTH directories: "easywave" (coordinator files) und "easywave" (DeviceManager files)
    # Existiert möglicherweise AUCH eine .local/share/homeassistant/custom_components/easywave/ Datei?
    data_directories = [
        Path(hass.config.config_dir) / "easywave",
        Path(hass.config.config_dir) / DOMAIN,  # "easywave"
    ]
    
    total_removed = 0
    total_dir_removed = 0
    
    # Helper function to do glob operations in executor (non-blocking)
    def _glob_files(dir_path: Path) -> list:
        """Get all .json and .json.* files from directory (sync operation for executor)."""
        try:
            pattern_files = list(dir_path.glob("*.json")) + list(dir_path.glob("*.json.*"))
            return pattern_files
        except Exception:
            return []
    
    for data_dir in data_directories:
        if data_dir.exists():
            try:
                removed_count = 0
                for filename in files_to_remove:
                    filepath = data_dir / filename
                    if filepath.exists():
                        try:
                            os.remove(filepath)
                            removed_count += 1
                            _LOGGER.debug("🗑️ Removed: %s/%s", data_dir.name, filename)
                        except Exception as e:
                            _LOGGER.debug("Could not remove %s: %s", filename, e)
                
                # Entferne AUCH alle .json und .bak Dateien (fallback für Dateien die wir vergessen haben)
                # Use executor to avoid blocking the event loop
                pattern_files = await hass.async_add_executor_job(_glob_files, data_dir)
                for filepath in pattern_files:
                    # Überspringe nur die Datei, wenn sie zu einer anderen Config Entry gehört
                    filename = filepath.name
                    other_entry = False
                    for other_id in hass.config_entries.async_entries(DOMAIN):
                        if other_id.entry_id != entry.entry_id and other_id.entry_id in filename:
                            other_entry = True
                            break
                    
                    if not other_entry:
                        try:
                            os.remove(filepath)
                            removed_count += 1
                            _LOGGER.debug("🗑️ Removed glob: %s/%s", data_dir.name, filename)
                        except Exception:
                            pass
                
                total_removed += removed_count
                
                # Versuche Empty Directory zu Entfernen (mit Executor um Event Loop nicht zu blockieren)
                def _check_and_remove_dir(dir_path):
                    try:
                        if dir_path.exists() and not any(dir_path.iterdir()):
                            dir_path.rmdir()
                            return True
                    except Exception:
                        pass
                    return False
                
                if await hass.async_add_executor_job(_check_and_remove_dir, data_dir):
                    _LOGGER.info("🗑️ Removed empty directory: %s", data_dir.name)
                    total_dir_removed += 1
                    
            except Exception as e:
                _LOGGER.warning("⚠️ Could not clean up data files in %s: %s", data_dir.name, e)
    
    # Zusätzlich: Versuche, Konfigurationsdateien im Config-Root zu löschen (falls dort gespeichert)
    root_config_dir = Path(hass.config.config_dir)
    root_files_to_check = [
        f"easywave_{entry.entry_id}.json",
        f".easywave_{entry.entry_id}.json",
        f"easywave_{entry.entry_id}.json",
    ]
    
    for filename in root_files_to_check:
        filepath = root_config_dir / filename
        if filepath.exists():
            try:
                os.remove(filepath)
                total_removed += 1
                _LOGGER.debug("🗑️ Removed root config: %s", filename)
            except Exception as e:
                _LOGGER.debug("Could not remove root %s: %s", filename, e)
    
    _LOGGER.info("✅ EASYWAVE config entry removed - %d devices, %d entities, %d files deleted, %d directories removed", 
                len(devices_to_remove), len(entities_to_remove), total_removed, total_dir_removed)