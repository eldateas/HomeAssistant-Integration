"""ELDAT integration initialization with transceiver modularity."""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

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


async def _compile_rx11_library(hass: HomeAssistant) -> bool:
    """Compile the RX11 C library if it doesn't exist."""
    integration_dir = Path(__file__).parent
    rx11_dir = integration_dir / "transceivers" / "rx11"
    library_path = rx11_dir / "RxModule.so"
    source_path = rx11_dir / "RxModule.c"
    
    # Check if library already exists
    if library_path.exists():
        _LOGGER.debug("RX11 C library already exists at %s", library_path)
        return True
    
    # Check if source files exist
    if not source_path.exists():
        _LOGGER.warning("RX11 source files not found, library compilation skipped")
        return False
    
    _LOGGER.info("Compiling RX11 C library...")
    
    try:
        # Run compilation in executor to avoid blocking
        def _compile():
            compile_script = integration_dir / "compile_library.sh"
            if not compile_script.exists():
                _LOGGER.error("Compilation script not found at %s", compile_script)
                return False
            
            result = subprocess.run(
                ["bash", str(compile_script)],
                cwd=str(integration_dir),
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                _LOGGER.info("✅ RX11 C library compiled successfully")
                return True
            else:
                _LOGGER.error("❌ RX11 library compilation failed: %s", result.stderr)
                return False
        
        return await hass.async_add_executor_job(_compile)
        
    except subprocess.TimeoutExpired:
        _LOGGER.error("RX11 library compilation timed out")
        return False
    except Exception as e:
        _LOGGER.error("Failed to compile RX11 library: %s", e)
        return False

# Platforms to set up
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.LIGHT,
    Platform.COVER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ELDAT from a config entry with transceiver modularity."""
    _LOGGER.info("Setting up ELDAT integration for entry %s", entry.entry_id)
    
    # Get configuration data
    transceiver_type_str = entry.data.get(CONF_TRANSCEIVER_TYPE)
    device_path = entry.data.get(CONF_DEVICE_PATH)
    device_name = entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)
    
    # Compile RX11 library if needed and RX11 is being used
    if transceiver_type_str == TransceiverType.RX11.value:
        await _compile_rx11_library(hass)
    
    if not transceiver_type_str:
        _LOGGER.error("No transceiver type specified in config entry")
        raise ConfigEntryNotReady("Transceiver type missing")
    
    if not device_path:
        _LOGGER.error("No device path specified in config entry")
        raise ConfigEntryNotReady("Device path missing")
    
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
    
    # Store coordinator in hass data
    hass.data.setdefault(DOMAIN, {})
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
    """Remove a config entry with proper cleanup."""
    _LOGGER.info("🗑️ Removing ELDAT config entry...")
    
    # Additional cleanup if needed
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator:
        await coordinator.async_shutdown()
    
    _LOGGER.info("✅ ELDAT config entry removed successfully")