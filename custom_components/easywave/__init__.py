"""ELDAT integration initialization with transceiver modularity."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    CONF_DEVICE_PATH,
    CONF_DEVICE_NAME,
    CONF_TRANSCEIVER_TYPE,
    DEVICE_SCAN_INTERVAL,
    DEFAULT_DEVICE_NAME,
    EVENT_DEVICE_ADDED,
)
from .transceivers import TransceiverFactory, TransceiverType
from .coordinator import EldatCoordinator
from .translations import translate, get_language

_LOGGER = logging.getLogger(__name__)


async def _find_usb_device_path(hass: HomeAssistant, configured_path: str) -> str:
    """Find the actual USB device path, handling port changes.
    
    If the configured path doesn't exist, searches for ELDAT device by VID/PID.
    This allows the integration to continue working when USB port changes.
    
    Args:
        hass: Home Assistant instance
        configured_path: The originally configured device path
        
    Returns:
        The actual device path (may be different from configured_path)
    """
    import os
    
    # If configured path exists, use it
    if os.path.exists(configured_path):
        return configured_path
    
    _LOGGER.warning("⚠️ Configured USB path %s not found, searching for ELDAT device...", configured_path)
    
    # Search for ELDAT device by VID/PID
    try:
        import serial.tools.list_ports
        
        # Run blocking I/O operation in executor to avoid blocking event loop
        def _list_ports():
            return list(serial.tools.list_ports.comports())
        
        ports = await hass.async_add_executor_job(_list_ports)
        
        # Look for ELDAT USB devices
        for port in ports:
            # Check if this is an ELDAT device (VID: 0x155A, PID: 0x1014)
            if port.vid == 0x155A and port.pid == 0x1014:
                _LOGGER.info("✅ Found ELDAT device at %s (VID:0x%04X PID:0x%04X)", 
                           port.device, port.vid, port.pid)
                return port.device
        
        # No ELDAT device found
        _LOGGER.warning("⚠️ No alternative ELDAT USB device found - waiting for device to be connected")
        
    except ImportError:
        _LOGGER.warning("⚠️ pyserial not available for USB device detection")
    except Exception as e:
        _LOGGER.error("❌ Error searching for USB device: %s", e)
    
    # Fallback: return configured path (will fail later if still doesn't exist)
    return configured_path


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
    """Set up ELDAT from a config entry with transceiver modularity."""
    _LOGGER.info("Setting up ELDAT integration for entry %s", entry.entry_id)
    
    # Guard against double setup
    hass.data.setdefault(DOMAIN, {})
    if entry.entry_id in hass.data[DOMAIN]:
        _LOGGER.warning("⚠️ Entry %s already set up, skipping", entry.entry_id)
        return True
    
    # Get configuration data
    transceiver_type_str = entry.data.get(CONF_TRANSCEIVER_TYPE)
    device_path = entry.data.get(CONF_DEVICE_PATH)
    device_name = entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)
    
    if not transceiver_type_str:
        _LOGGER.error("No transceiver type specified in config entry")
        raise ConfigEntryNotReady("Transceiver type missing")
    
    if not device_path:
        _LOGGER.error("No device path specified in config entry")
        raise ConfigEntryNotReady("Device path missing")
    
    # Auto-detect USB port if configured path doesn't exist
    # This allows the device to work even if USB port changes (e.g., USB0 -> USB1)
    actual_device_path = await _find_usb_device_path(hass, device_path)
    if actual_device_path != device_path:
        _LOGGER.info("🔄 USB port changed: %s → %s", device_path, actual_device_path)
        # Update config entry with new path (persistent across restarts)
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_DEVICE_PATH: actual_device_path}
        )
        device_path = actual_device_path
    
    try:
        transceiver_type = TransceiverType(transceiver_type_str)
    except ValueError:
        _LOGGER.error("Invalid transceiver type: %s", transceiver_type_str)
        raise ConfigEntryNotReady(f"Invalid transceiver type: {transceiver_type_str}")
    
    _LOGGER.info("Setting up %s transceiver at %s", transceiver_type.value, device_path)
    
    # Step 1: Create and setup transceiver
    try:
        transceiver = TransceiverFactory.create_transceiver(transceiver_type, device_path)
        _LOGGER.info("Created %s transceiver instance", transceiver_type.value)
    except Exception as e:
        _LOGGER.error("Failed to create transceiver: %s", e)
        raise ConfigEntryNotReady(f"Failed to create {transceiver_type.value} transceiver: {e}")
    
    # Step 2: Create coordinator
    try:
        coordinator = EldatCoordinator(
            hass=hass,
            transceiver=transceiver,
            config_entry=entry,
            update_interval=DEVICE_SCAN_INTERVAL,
        )
        
        # Migrate old configuration files to DeviceManager (one-time)
        _LOGGER.debug("Checking for legacy device configuration files...")
        from .device_migration import migrate_to_device_manager
        await migrate_to_device_manager(hass, entry.entry_id)
        
        # Check for entity incompatibilities and migrate if needed (fallback protection)
        _LOGGER.debug("Checking for entity compatibility issues...")
        from .entity_migration import migrate_entities_if_needed
        managed_devices = coordinator.device_manager.get_all_devices()
        migration_report = await migrate_entities_if_needed(hass, entry.entry_id, managed_devices)
        
        if migration_report.get("migrated_devices", 0) > 0:
            _LOGGER.info("🔄 Entity migration completed: %d devices, %d entities updated",
                        migration_report["migrated_devices"],
                        len(migration_report.get("recreated_entities", [])))
        
        # Log cleanup results
        duplicates = migration_report.get("duplicate_entities_removed", 0)
        legacy_battery = migration_report.get("legacy_battery_sensors_removed", 0)
        orphaned = migration_report.get("orphaned_entities_removed", 0)
        if duplicates > 0 or legacy_battery > 0 or orphaned > 0:
            _LOGGER.info("🧹 Entity cleanup: %d duplicates, %d legacy battery sensors, %d orphaned entities removed",
                        duplicates, legacy_battery, orphaned)
        
        # Setup coordinator
        if not await coordinator.async_setup():
            raise ConfigEntryNotReady("Waiting for ELDAT device connection")
            
        _LOGGER.info("Coordinator setup completed")
        
    except ConfigEntryNotReady:
        raise
    except Exception as e:
        _LOGGER.warning("⚠️ Setup incomplete: %s - integration will retry", e)
        raise ConfigEntryNotReady(f"Setup incomplete: {e}")
    
    # Store coordinator in hass data (DOMAIN dict already initialized at top of function)
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    # Step 3: Restore ONLY registered devices BEFORE setting up platforms
    _LOGGER.debug("Restoring only registered devices...")
    await coordinator.restore_registered_devices_only()
    _LOGGER.info("✅ Restored %d registered devices", len(coordinator.get_all_registered_devices()))
    
    # Give a moment for device restoration to complete
    import asyncio
    await asyncio.sleep(0.5)
    
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
        _LOGGER.error("Failed to perform initial data refresh: %s", e)
        await coordinator.async_shutdown()
        raise ConfigEntryNotReady(f"Initial data refresh failed: {e}")
    
    _LOGGER.info("ELDAT integration setup complete for %s (%s)", 
                device_name, transceiver_type.value)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading ELDAT integration")
    
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
            await coordinator.async_shutdown()
            _LOGGER.info("✅ Coordinator shutdown completed")
        except Exception as e:
            _LOGGER.error("❌ Error during coordinator shutdown: %s", e)
    
    # Remove from hass data
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    
    _LOGGER.info("ELDAT integration unloaded successfully")
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    _LOGGER.debug("Reloading ELDAT integration")
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old config entries to new format."""
    version = config_entry.version
    
    _LOGGER.debug("Migrating ELDAT config entry from version %s", version)
    
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
        
        _LOGGER.info("Migrated ELDAT config entry to version 2")
    
    return True


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the ELDAT component."""
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
    
    # Extract serial number from device identifiers
    serial_number = None
    for identifier in device_entry.identifiers:
        if identifier[0] == DOMAIN:
            # Identifier format is (DOMAIN, serial_number)
            serial_number = identifier[1]
            break
    
    if not serial_number:
        _LOGGER.warning("⚠️ Could not find serial number for device %s", device_entry.id)
        # Allow deletion anyway - this might be an orphaned device
        return True
    
    # Don't allow deletion of the main RX11 transceiver device
    # The RX11 gateway identifier is "{config_entry.entry_id}_gateway"
    is_rx11_gateway = (
        serial_number.endswith("_gateway") or
        "gateway" in serial_number.lower() or
        serial_number == config_entry.entry_id or
        "RX11" in (device_entry.name or "").upper()
    )
    
    if is_rx11_gateway:
        # Check if there are other devices besides the RX11
        device_registry = dr.async_get(hass)
        other_devices = []
        
        for device in device_registry.devices.values():
            # Check if device belongs to this config entry
            if config_entry.entry_id not in device.config_entries:
                continue
            # Skip the RX11 gateway itself
            if device.id == device_entry.id:
                continue
            # This is another device
            other_devices.append(device)
        
        if other_devices:
            # There are other devices - show warning message
            device_names = [d.name or translate("device.unknown", hass=hass) for d in other_devices[:5]]
            device_list = ", ".join(device_names)
            if len(other_devices) > 5:
                device_list += f" und {len(other_devices) - 5} weitere"
            
            _LOGGER.info("ℹ️ User tried to delete RX11 transceiver with %d other devices present", len(other_devices))
            raise HomeAssistantError(
                translate("error.cannot_delete_rx11_with_devices", hass=hass)
            )
        else:
            # No other devices - remove the entire integration
            _LOGGER.info("🗑️ No other devices present - removing entire ELDAT integration")
            
            # Schedule the config entry removal (can't do it synchronously here)
            async def remove_integration():
                await asyncio.sleep(0.5)  # Small delay to let the current operation complete
                await hass.config_entries.async_remove(config_entry.entry_id)
                _LOGGER.info("✅ ELDAT integration removed successfully")
            
            hass.async_create_task(remove_integration())
            
            # Return True to allow the device removal to proceed
            return True
    
    try:
        # Use coordinator's removal method for proper cleanup
        success = await coordinator.async_remove_device(serial_number, force=True)
        
        if success:
            _LOGGER.info("✅ Device %s removed successfully via UI", serial_number[-8:] if len(serial_number) > 8 else serial_number)
            
            # Fire event for UI notifications
            hass.bus.async_fire("eldat_device_removed", {
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
    """Remove a config entry with proper cleanup - removes ALL devices and persistent data."""
    import homeassistant.helpers.entity_registry as er
    
    _LOGGER.info("🗑️ Removing ELDAT config entry and all associated data...")
    
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
        # Clear all tracking sets to prevent stale data
        coordinator.created_entity_unique_ids.clear()
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
    
    # Step 5: Clean up ALL persistent data files from BOTH directories
    import os
    
    # Files that may exist in each directory
    files_to_remove = [
        "registered_devices.json",
        "managed_devices.json", 
        "managed_devices_backup.json",
        "used_ewb_indices.json",
        "used_ew_receiver_indices.json",
        # Legacy migration files (if present)
        "device_whitelist.json",
        "device_whitelist.json.migrated_backup",
        "eldat_devices.json",
        "eldat_devices.json.migrated_backup",
        "eldat_device_registry.json",
        "eldat_device_registry.json.migrated_backup",
    ]
    
    # Clean up BOTH directories: "eldat" (coordinator files) and "easywave" (DeviceManager files)
    data_directories = [
        Path(hass.config.config_dir) / "eldat",
        Path(hass.config.config_dir) / DOMAIN,  # "easywave"
    ]
    
    total_removed = 0
    for data_dir in data_directories:
        if data_dir.exists():
            try:
                removed_count = 0
                for filename in files_to_remove:
                    filepath = data_dir / filename
                    if filepath.exists():
                        os.remove(filepath)
                        removed_count += 1
                        _LOGGER.info("🗑️ Removed: %s/%s", data_dir.name, filename)
                
                total_removed += removed_count
                
                # Remove empty directory (use executor to avoid blocking)
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
                    
            except Exception as e:
                _LOGGER.warning("⚠️ Could not clean up data files in %s: %s", data_dir.name, e)
    
    _LOGGER.info("✅ ELDAT config entry removed - %d devices, %d entities, %d files deleted", 
                len(devices_to_remove), len(entities_to_remove), total_removed)