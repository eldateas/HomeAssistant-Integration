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
from homeassistant.exceptions import ConfigEntryNotReady
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
            # Check if this is an ELDAT device (VID: 0x155A, PID: 0x1006 or 0x1014)
            if port.vid == 0x155A and port.pid in [0x1006, 0x1014]:
                _LOGGER.info("✅ Found ELDAT device at %s (VID:0x%04X PID:0x%04X)", 
                           port.device, port.vid, port.pid)
                return port.device
        
        # No ELDAT device found
        _LOGGER.error("❌ No ELDAT USB device found (VID:0x155A PID:0x1006/0x1014)")
        
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
        
        # Setup coordinator
        if not await coordinator.async_setup():
            raise ConfigEntryNotReady("Failed to setup coordinator")
            
        _LOGGER.info("Coordinator setup completed")
        
    except Exception as e:
        _LOGGER.error("Failed to setup coordinator: %s", e)
        raise ConfigEntryNotReady(f"Coordinator setup failed: {e}")
    
    # Store coordinator in hass data (DOMAIN dict already initialized at top of function)
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    # Step 3: Restore ONLY registered devices BEFORE setting up platforms
    _LOGGER.debug("Restoring only registered devices...")
    await coordinator.restore_registered_devices_only()
    _LOGGER.info("✅ Restored %d registered devices", len(coordinator.get_all_registered_devices()))
    
    # Give a moment for device restoration to complete
    import asyncio
    await asyncio.sleep(0.5)
    
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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a config entry with proper cleanup - removes ALL devices."""
    _LOGGER.info("🗑️ Removing ELDAT config entry and all associated devices...")
    
    # Get coordinator for shutdown
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator:
        await coordinator.async_shutdown()
    
    # Remove all devices associated with this config entry from device registry
    device_registry = dr.async_get(hass)
    devices_to_remove = []
    
    for device in device_registry.devices.values():
        # Check if device belongs to this config entry
        for config_entry_id in device.config_entries:
            if config_entry_id == entry.entry_id:
                devices_to_remove.append(device.id)
                break
    
    # Remove each device
    for device_id in devices_to_remove:
        device_registry.async_remove_device(device_id)
        _LOGGER.info("🗑️ Removed device: %s", device_id)
    
    _LOGGER.info("✅ ELDAT config entry removed - %d devices deleted", len(devices_to_remove))
    
    # Optional: Clean up stored data files
    import os
    import shutil
    data_dir = f"{hass.config.config_dir}/eldat_plugin"
    if os.path.exists(data_dir):
        try:
            # Remove only device-related files, keep the directory
            files_to_remove = [
                "registered_devices.json",
                "managed_devices.json", 
                "managed_devices_backup.json",
            ]
            for filename in files_to_remove:
                filepath = os.path.join(data_dir, filename)
                if os.path.exists(filepath):
                    os.remove(filepath)
                    _LOGGER.info("🗑️ Removed data file: %s", filename)
        except Exception as e:
            _LOGGER.warning("⚠️ Could not clean up data files: %s", e)