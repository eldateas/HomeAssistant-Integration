"""ELDAT services for debugging and maintenance."""
from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import async_get_platforms

from .const import DOMAIN
from .coordinator import EldatCoordinator
from .entity_registry import get_entity_registry

_LOGGER = logging.getLogger(__name__)

SERVICE_RESET_ENTITY_REGISTRY = "reset_entity_registry"
SERVICE_RELOAD_SENSORS = "reload_sensors"
SERVICE_FIX_TRANSCEIVER = "fix_transceiver"
SERVICE_CLEANUP_GHOST_DEVICES = "cleanup_ghost_devices"
SERVICE_CLEANUP_ORPHANED_ENTITIES = "cleanup_orphaned_entities"
SERVICE_REPAIR_ORPHANED_DEVICES = "repair_orphaned_devices"


# ============================================================
# Helper functions (formerly in services_helper.py)
# ============================================================

async def auto_detect_transceiver_index(coordinator, serial_number: str) -> Optional[int]:
    """Auto-detect the RX11 index for a transceiver by scanning all indices."""
    try:
        wrapper = coordinator.gateway_wrapper
        if not wrapper:
            _LOGGER.error("❌ No gateway wrapper available")
            return None
        
        # Try to find the device in RX11 indices 0-9
        for index in range(10):
            try:
                # Try to get receiver serial from this index
                cached_serial = await wrapper._ensure_receiver_cached(index)
                if cached_serial and cached_serial[-8:].upper() == serial_number[-8:].upper():
                    _LOGGER.info("✅ Found transceiver %s at RX11 index %d", serial_number[-8:], index)
                    return index
            except Exception as e:
                _LOGGER.debug("Index %d check failed: %s", index, e)
                continue
        
        _LOGGER.warning("⚠️ Could not auto-detect RX11 index for %s", serial_number[-8:])
        return None
        
    except Exception as e:
        _LOGGER.error("❌ Error during auto-detection: %s", e)
        return None


async def fix_transceiver_device(coordinator, serial_number: str) -> bool:
    """Fix a transceiver device by updating its configuration and detecting index."""
    try:
        device_registry = coordinator.device_registry
        device_data = await device_registry.get_device_by_serial(serial_number)
        
        if not device_data:
            _LOGGER.error("❌ Device %s not found in registry", serial_number[-8:])
            return False
        
        # Auto-detect RX11 index
        rx11_index = await auto_detect_transceiver_index(coordinator, serial_number)
        if rx11_index is not None:
            # Update device configuration
            updates = {
                "device_type": "ew_transceiver",
                "rx11_index": rx11_index,
                "supports_sensors": True,
                "supports_buttons": True,
                "bidirectional": True,
                "discovered": True,
                "detected_via": "service_fix",
                "sensor_types": ["temperature", "humidity", "battery"],
                "measurement_types": ["temperature", "humidity"],
                "available_sensors": ["temperature", "humidity", "battery"]
            }
            
            # Update device
            device_registry.update_device(serial_number, updates)
            
            _LOGGER.info("✅ Fixed transceiver %s with RX11 index %d", serial_number[-8:], rx11_index)
            return True
        
        return False
        
    except Exception as e:
        _LOGGER.error("❌ Error fixing transceiver device: %s", e)
        return False


# ============================================================
# Service handlers
# ============================================================

async def async_setup_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up ELDAT services."""
    
    async def handle_reset_entity_registry(call: ServiceCall) -> None:
        """Handle reset entity registry service call."""
        try:
            # Clear the session entity tracking
            entity_registry = get_entity_registry()
            entity_registry.clear()
            
            _LOGGER.warning("🔄 Session entity tracking cleared via service call")
            
            # Optionally reload the integration
            force_reload = call.data.get("force_reload", True)
            if force_reload:
                # Get the coordinator
                coordinator: EldatCoordinator = hass.data[DOMAIN][entry.entry_id]
                
                # Reload all platforms
                await hass.config_entries.async_reload(entry.entry_id)
                _LOGGER.info("✅ ELDAT integration reloaded after registry reset")
                
        except Exception as e:
            _LOGGER.error("❌ Failed to reset entity registry: %s", e)
    
    async def handle_reload_sensors(call: ServiceCall) -> None:
        """Handle reload sensors service call."""
        try:
            coordinator: EldatCoordinator = hass.data[DOMAIN][entry.entry_id]
            
            # Force reload device configuration and fire events
            await coordinator._load_device_configuration(fire_events=True)
            _LOGGER.info("✅ Sensors reloaded via service call")
            
        except Exception as e:
            _LOGGER.error("❌ Failed to reload sensors: %s", e)
    
    async def handle_fix_transceiver(call: ServiceCall) -> None:
        """Handle fix transceiver service call."""
        try:
            coordinator: EldatCoordinator = hass.data[DOMAIN][entry.entry_id]
            serial_number = call.data.get("serial_number")
            
            if not serial_number:
                _LOGGER.error("❌ Serial number required for fix_transceiver service")
                return
            
            # Try to fix the transceiver
            success = await fix_transceiver_device(coordinator, serial_number)
            if success:
                # Reload the integration to apply changes
                await hass.config_entries.async_reload(entry.entry_id)
                _LOGGER.info("✅ Transceiver %s fixed and integration reloaded", serial_number[-8:])
            else:
                _LOGGER.error("❌ Failed to fix transceiver %s", serial_number[-8:])
                
        except Exception as e:
            _LOGGER.error("❌ Failed to fix transceiver: %s", e)
    
    async def handle_cleanup_ghost_devices(call: ServiceCall) -> None:
        """Handle cleanup ghost devices service call."""
        try:
            # Get the coordinator
            coordinator: EldatCoordinator = hass.data[DOMAIN][entry.entry_id]
            
            # Run ghost device cleanup
            await coordinator.async_cleanup_ghost_devices()
            _LOGGER.info("✅ Ghost device cleanup completed via service call")
                
        except Exception as e:
            _LOGGER.error("❌ Failed to cleanup ghost devices: %s", e)
    
    async def handle_cleanup_orphaned_entities(call: ServiceCall) -> None:
        """Handle cleanup orphaned entities service call."""
        try:
            # Get the coordinator
            coordinator: EldatCoordinator = hass.data[DOMAIN][entry.entry_id]
            
            # Run orphaned entity cleanup
            result = await coordinator.async_cleanup_all_orphaned_entities()
            
            if "error" in result:
                _LOGGER.error("❌ Orphaned entity cleanup failed: %s", result["error"])
            else:
                _LOGGER.info("✅ Orphaned entity cleanup completed: cleaned %d entities, %d devices", 
                           result["cleaned_entities"], result["cleaned_devices"])
                
                # Fire event with results
                hass.bus.async_fire("eldat_orphaned_cleanup_completed", result)
                
        except Exception as e:
            _LOGGER.error("❌ Failed to cleanup orphaned entities: %s", e)
    
    async def handle_repair_orphaned_devices(call: ServiceCall) -> None:
        """Handle repair orphaned devices service call."""
        try:
            # Get the coordinator
            coordinator: EldatCoordinator = hass.data[DOMAIN][entry.entry_id]
            
            # Run orphaned device repair
            repaired_count = await coordinator.repair_orphaned_devices()
            _LOGGER.info("✅ Orphaned device repair completed - %d devices repaired", repaired_count)
                
        except Exception as e:
            _LOGGER.error("❌ Failed to repair orphaned devices: %s", e)
    
    # Register services
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_ENTITY_REGISTRY,
        handle_reset_entity_registry,
        schema=None,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_RELOAD_SENSORS,
        handle_reload_sensors,
        schema=None,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_FIX_TRANSCEIVER,
        handle_fix_transceiver,
        schema=None,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEANUP_GHOST_DEVICES,
        handle_cleanup_ghost_devices,
        schema=None,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEANUP_ORPHANED_ENTITIES,
        handle_cleanup_orphaned_entities,
        schema=None,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_REPAIR_ORPHANED_DEVICES,
        handle_repair_orphaned_devices,
        schema=None,
    )
    
    _LOGGER.info("✅ Registered ELDAT services: %s, %s, %s, %s, %s, %s", 
                SERVICE_RESET_ENTITY_REGISTRY, SERVICE_RELOAD_SENSORS, SERVICE_FIX_TRANSCEIVER, 
                SERVICE_CLEANUP_GHOST_DEVICES, SERVICE_CLEANUP_ORPHANED_ENTITIES, SERVICE_REPAIR_ORPHANED_DEVICES)


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload ELDAT services."""
    hass.services.async_remove(DOMAIN, SERVICE_RESET_ENTITY_REGISTRY)
    hass.services.async_remove(DOMAIN, SERVICE_RELOAD_SENSORS)
    hass.services.async_remove(DOMAIN, SERVICE_FIX_TRANSCEIVER)
    hass.services.async_remove(DOMAIN, SERVICE_CLEANUP_GHOST_DEVICES)
    hass.services.async_remove(DOMAIN, SERVICE_CLEANUP_ORPHANED_ENTITIES)
    hass.services.async_remove(DOMAIN, SERVICE_REPAIR_ORPHANED_DEVICES)
    _LOGGER.info("🗑️ Unloaded ELDAT services")