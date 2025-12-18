"""Coordinator for ELDAT integration with transceiver modularity."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import aiofiles

from homeassistant.core import HomeAssistant, Event, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    DOMAIN,
    DEVICE_SCAN_INTERVAL,
    EVENT_DEVICE_ADDED,
    EVENT_DEVICE_REMOVED,
    EVENT_DEVICE_UPDATED,
    EVENT_DEVICE_STATE_UPDATE,
    EVENT_TELEGRAM_RECEIVED,
    EVENT_SENSOR_ADDED,
    EVENT_FORCE_CREATE,
    CONF_TRANSCEIVER_TYPE,
)
from .transceivers import TransceiverFactory, TransceiverType, BaseTransceiver
from .device_config import DeviceConfigManager
from .device_registry import DeviceRegistry, EepromEntry, DeviceType, StatusFlags
from .entity_specs import create_entity_specs_for_device

_LOGGER = logging.getLogger(__name__)


class EldatCoordinator(DataUpdateCoordinator):
    """Data coordinator for ELDAT with transceiver modularity."""

    def __init__(
        self,
        hass: HomeAssistant,
        transceiver: BaseTransceiver,
        config_entry,
        update_interval: timedelta = DEVICE_SCAN_INTERVAL,
    ):
        """Initialize the coordinator."""
        self.transceiver = transceiver
        self.config_entry = config_entry
        self.devices: Dict[str, Dict[str, Any]] = {}
        self._known_devices: Set[str] = set()
        self._config_flow_learning = False
        self._setup_mode_active = False
        self._setup_timeout = None
        self.device_config_manager = DeviceConfigManager(hass, config_entry.entry_id)
        self.device_registry = DeviceRegistry(hass.config.config_dir)
        
        # Central registered device management - only these devices will be managed
        self._registered_devices: Dict[str, Dict[str, Any]] = {}
        self._registered_devices_file = f"{hass.config.config_dir}/eldat_plugin/registered_devices.json"
        
        # EWB Index Tracking - Persistent storage of used EWB indices
        self._used_ewb_indices: Dict[int, Dict[str, Any]] = {}  # {index: {gateway_serial, device_serial, device_name, created_at}}
        self._ewb_indices_file = f"{hass.config.config_dir}/eldat_plugin/used_ewb_indices.json"
        self._next_free_ewb_index: Optional[int] = 0  # Cache for next known free index
        
        # Device registration will happen during async_setup
        
        # Global entity tracking to prevent duplicates across all platforms
        self.created_entity_unique_ids: Set[str] = set()
        
        # Device backup system for restoration
        self._device_backup: Dict[str, Dict[str, Any]] = {}
        self._device_blacklist: Set[str] = set()  # DEPRECATED - kept for migration
        self._device_whitelist: Set[str] = set()  # NEW: Only whitelisted devices are loaded
        self._whitelist_mode: bool = True  # ENABLED - Whitelist-basierte Geräteverwaltung
        self._whitelist_dirty: bool = False  # Track if whitelist needs saving
        self._setup_complete: bool = False  # Track if setup is complete
        
        # Track devices that already fired events (prevent duplicates)
        self._devices_with_fired_events: Set[str] = set()
        
        # Telegram listener
        self._telegram_listener_remove = None
        self._shutdown_event = asyncio.Event()

        # Initialize coordinator state
        self.last_update_success = False

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )

    def start_setup_mode(self, timeout_seconds: int = 60):
        """Start device setup mode to allow registration of new devices."""
        self._setup_mode_active = True
        
        # Cancel previous timeout if exists
        if self._setup_timeout:
            self._setup_timeout.cancel()
        
        # Set timeout to automatically stop setup mode
        async def stop_setup():
            await asyncio.sleep(timeout_seconds)
            self.stop_setup_mode()
        
        self._setup_timeout = self.hass.async_create_task(stop_setup())
        _LOGGER.info("Device setup mode activated for %d seconds", timeout_seconds)
    
    def stop_setup_mode(self):
        """Stop device setup mode."""
        self._setup_mode_active = False
        if self._setup_timeout:
            self._setup_timeout.cancel()
            self._setup_timeout = None
        _LOGGER.info("Device setup mode deactivated")
    
    async def _load_used_ewb_indices(self) -> None:
        """Load used EWB indices from persistent storage."""
        import json
        import aiofiles
        import os
        try:
            if os.path.exists(self._ewb_indices_file):
                async with aiofiles.open(self._ewb_indices_file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                    # Convert string keys back to integers
                    self._used_ewb_indices = {int(k): v for k, v in data.get('used_indices', {}).items()}
                    self._next_free_ewb_index = data.get('next_free_index', 0)
                    _LOGGER.info("📋 Loaded %d used EWB indices from storage", len(self._used_ewb_indices))
            else:
                _LOGGER.info("📋 No EWB indices file found, starting with empty tracking")
                self._used_ewb_indices = {}
                self._next_free_ewb_index = 0
        except Exception as e:
            _LOGGER.error("❌ Failed to load used EWB indices: %s", e)
            self._used_ewb_indices = {}
            self._next_free_ewb_index = 0
    
    async def _save_used_ewb_indices(self) -> None:
        """Save used EWB indices to persistent storage."""
        import json
        import aiofiles
        import os
        try:
            os.makedirs(os.path.dirname(self._ewb_indices_file), exist_ok=True)
            
            data = {
                'used_indices': {str(k): v for k, v in self._used_ewb_indices.items()},
                'next_free_index': self._next_free_ewb_index,
                'last_updated': datetime.now().isoformat()
            }
            
            async with aiofiles.open(self._ewb_indices_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))
                
            _LOGGER.debug("💾 Saved %d used EWB indices to storage", len(self._used_ewb_indices))
        except Exception as e:
            _LOGGER.error("❌ Failed to save used EWB indices: %s", e)

    async def _load_registered_devices(self) -> None:
        """Load registered devices from persistent storage."""
        import json
        import aiofiles
        import os
        try:
            if os.path.exists(self._registered_devices_file):
                async with aiofiles.open(self._registered_devices_file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                    self._registered_devices = data.get('devices', {})
                    _LOGGER.info("📋 Loaded %d registered devices from storage", len(self._registered_devices))
            else:
                _LOGGER.info("📋 No registered devices file found, starting with empty list")
        except Exception as e:
            _LOGGER.error("❌ Failed to load registered devices: %s", e)
            self._registered_devices = {}
    
    async def _save_registered_devices(self) -> None:
        """Save registered devices to persistent storage."""
        import json
        import aiofiles
        import os
        try:
            os.makedirs(os.path.dirname(self._registered_devices_file), exist_ok=True)
            
            # Deep copy devices and convert sets to lists for JSON serialization
            serializable_devices = {}
            for serial, device_info in self._registered_devices.items():
                device_copy = device_info.copy()
                
                # Convert any sets to lists for JSON serialization
                if 'platforms' in device_copy and isinstance(device_copy['platforms'], set):
                    device_copy['platforms'] = list(device_copy['platforms'])
                
                # Recursively convert any nested sets in entities
                if 'entities' in device_copy:
                    for entity in device_copy['entities']:
                        if isinstance(entity, dict):
                            for key, value in entity.items():
                                if isinstance(value, set):
                                    entity[key] = list(value)
                
                serializable_devices[serial] = device_copy
            
            data = {
                'version': '1.0',
                'last_updated': datetime.now().isoformat(),
                'config_entry_id': self.config_entry.entry_id,
                'device_count': len(serializable_devices),
                'devices': serializable_devices
            }
            
            content = json.dumps(data, indent=2, ensure_ascii=False)
            async with aiofiles.open(self._registered_devices_file, 'w', encoding='utf-8') as f:
                await f.write(content)
                
            _LOGGER.info("💾 Saved %d registered devices to storage", len(self._registered_devices))
        except Exception as e:
            _LOGGER.error("❌ Failed to save registered devices: %s", e)
    
    async def _migrate_existing_devices_to_registered_list(self) -> None:
        """Migrate existing devices from eldat_devices.json to registered list."""
        import json
        import aiofiles
        import os
        try:
            # Check if legacy device file exists
            legacy_file = f"{os.path.dirname(self._registered_devices_file)}/eldat_devices.json"
            if not os.path.exists(legacy_file):
                return
                
            _LOGGER.info("🔄 Migrating existing devices to registered list...")
            
            async with aiofiles.open(legacy_file, 'r', encoding='utf-8') as f:
                content = await f.read()
                legacy_data = json.loads(content)
                
            migrated_count = 0
            legacy_devices = legacy_data.get('devices', {})
            
            for serial_number, device_info in legacy_devices.items():
                # Only migrate if not already in registered list
                if serial_number not in self._registered_devices:
                    # Add migration metadata
                    device_info['migrated_from_legacy'] = True
                    device_info['migrated_at'] = datetime.now().isoformat()
                    
                    self._registered_devices[serial_number] = device_info
                    migrated_count += 1
                    
            if migrated_count > 0:
                _LOGGER.info("✅ Migrated %d devices from legacy storage to registered list", migrated_count)
                # Save the migrated registered list
                await self._save_registered_devices()
            else:
                _LOGGER.debug("No devices to migrate from legacy storage")
                
        except Exception as e:
            _LOGGER.error("❌ Failed to migrate legacy devices: %s", e)
    
    def is_device_registered(self, serial_number: str) -> bool:
        """Check if device is in registered devices list."""
        return serial_number in self._registered_devices
    
    def get_registered_device(self, serial_number: str) -> Optional[Dict[str, Any]]:
        """Get registered device info."""
        return self._registered_devices.get(serial_number)
    
    def get_all_registered_devices(self) -> Dict[str, Dict[str, Any]]:
        """Get all registered devices."""
        return self._registered_devices.copy()
    
    async def register_device_permanently(self, serial_number: str, device_info: Dict[str, Any]) -> bool:
        """Register a device for permanent management."""
        try:
            # Add metadata
            device_info['registered_at'] = datetime.now().isoformat()
            device_info['config_entry_id'] = self.config_entry.entry_id
            
            # Store in registered devices list
            self._registered_devices[serial_number] = device_info
            
            # Also add to legacy devices dict for platform compatibility
            self.devices[serial_number] = device_info
            self._known_devices.add(serial_number)
            
            # Add to whitelist (memory-only during setup, saved after)
            self._add_to_whitelist_memory(
                serial_number=serial_number,
                rx11_index=device_info.get("rx11_index"),
                device_type=device_info.get("device_type", device_info.get("type", "unknown")),
                source="register_permanent"
            )
            
            # Save to persistent storage
            await self._save_registered_devices()

            # Determine platforms from entities
            entities = device_info.get("entities", [])
            platforms = set()
            entity_specs_by_platform = {}
            
            for entity in entities:
                entity_type = entity.get("type")
                if entity_type:
                    platforms.add(entity_type)
                    if entity_type not in entity_specs_by_platform:
                        entity_specs_by_platform[entity_type] = []
                    entity_specs_by_platform[entity_type].append(entity)

            # Update device_info with platforms
            device_info["platforms"] = list(platforms)
            
            _LOGGER.debug("Determined platforms for device %s: %s", serial_number[-6:], list(platforms))

            # Fire event to trigger entity creation in Home Assistant
            await asyncio.sleep(0.1)  # Small delay to ensure readiness
            self.hass.bus.async_fire(
                EVENT_DEVICE_ADDED,
                {
                    "serial_number": serial_number,
                    "device_info": device_info,
                    "device_type": device_info.get("type"),
                    "device_name": device_info.get("name"),
                    "entities": device_info.get("entities", []),
                    "platforms": platforms,
                    "registered_permanently": True,  # Flag to indicate this is a new permanent registration
                    "force_create": True  # Force creation even if entity registry thinks it exists
                }
            )
            
            # Fire platform-specific events
            for platform, platform_entities in entity_specs_by_platform.items():
                self.hass.bus.async_fire(
                    f"{EVENT_DEVICE_ADDED}_{platform}",
                    {
                        "serial_number": serial_number,
                        "device_info": device_info,
                        "entities": platform_entities,
                        "platforms": {platform},
                        "force_create": True
                    }
                )
                _LOGGER.debug("Fired platform-specific event for %s: %s", platform, serial_number[-6:])
                await asyncio.sleep(0.05)
            
            _LOGGER.info("✅ Device %s permanently registered and event fired for entity creation", serial_number[-8:])
            return True
        except Exception as e:
            _LOGGER.error("❌ Failed to register device %s: %s", serial_number[-8:], e)
            return False
    
    # =============================================================================
    # EWB INDEX MANAGEMENT
    # =============================================================================
    
    def mark_ewb_index_used(self, index: int, gateway_serial: str, device_serial: str, device_name: str = None) -> None:
        """Mark an EWB index as used by a specific EWneo device.
        
        Note: An index can have a gateway serial but still be available for new EWneo devices.
        Only after a device is successfully joined via EwJoinDevice should it be marked as 'used'.
        """
        from datetime import datetime
        
        self._used_ewb_indices[index] = {
            'gateway_serial': gateway_serial,
            'device_serial': device_serial, 
            'device_name': device_name or f"EWneo-Device ({device_serial[-6:]})",
            'created_at': datetime.now().isoformat()
        }
        
        # Update next free index cache
        if index == self._next_free_ewb_index:
            self._next_free_ewb_index = self._find_next_free_ewb_index()
            
        _LOGGER.info("📍 Marked EWB index %d as used (device: %s, gateway: %s)", 
                    index, device_serial[-8:], gateway_serial[-8:])
        
        # Save to persistent storage
        asyncio.create_task(self._save_used_ewb_indices())
        
        # Update transceiver wrapper tracking if available
        if hasattr(self.transceiver, '_rx11_wrapper') and self.transceiver._rx11_wrapper:
            wrapper = self.transceiver._rx11_wrapper
            wrapper.mark_ewb_index_used(index, gateway_serial, device_serial)
    
    def mark_ewb_index_free(self, index: int) -> None:
        """Mark an EWB index as free/available for new EWneo devices.
        
        This removes the EWneo device association but the gateway serial remains.
        """
        if index in self._used_ewb_indices:
            device_info = self._used_ewb_indices.pop(index)
            _LOGGER.info("♻️ Marked EWB index %d as free (was: %s)", index, device_info.get('device_name', 'Unknown'))
            
            # Update next free index cache if this becomes the new earliest free
            if index < self._next_free_ewb_index:
                self._next_free_ewb_index = index
                
            # Save to persistent storage
            asyncio.create_task(self._save_used_ewb_indices())
            
            # Update transceiver wrapper tracking if available
            if hasattr(self.transceiver, '_rx11_wrapper') and self.transceiver._rx11_wrapper:
                wrapper = self.transceiver._rx11_wrapper
                wrapper.mark_ewb_index_free(index)
    
    def get_next_free_ewb_index(self) -> int:
        """Get the next free EWB index from persistent tracking."""
        # Use cached value if available and valid
        if self._next_free_ewb_index is not None and self._next_free_ewb_index not in self._used_ewb_indices:
            return self._next_free_ewb_index
            
        # Find next free index
        self._next_free_ewb_index = self._find_next_free_ewb_index()
        return self._next_free_ewb_index
    
    def _find_next_free_ewb_index(self) -> int:
        """Find the next EWB index available for EWneo device assignment.
        
        An index is 'free' if no EWneo device is assigned to it yet,
        regardless of whether it has a gateway serial or not.
        """
        used_indices = set(self._used_ewb_indices.keys())
        for index in range(256):  # EWB supports indices 0-255
            if index not in used_indices:
                return index
        raise ValueError("No free EWB indices available (all 0-255 in use)")
    
    def is_ewb_index_used(self, index: int) -> bool:
        """Check if an EWB index is already used by an EWneo device.
        
        Returns True if an EWneo device is assigned to this index,
        False if the index is available for new EWneo device assignment.
        """
        return index in self._used_ewb_indices
        
    def get_used_ewb_indices(self) -> Dict[int, Dict[str, Any]]:
        """Get all used EWB indices with their device information."""
        return self._used_ewb_indices.copy()
        
    def get_ewb_device_by_index(self, index: int) -> Optional[Dict[str, Any]]:
        """Get device information by EWB index."""
        return self._used_ewb_indices.get(index)
    
    async def _sync_ewb_indices_with_wrapper(self) -> None:
        """Synchronize EWB index tracking between coordinator and transceiver wrapper."""
        if not hasattr(self.transceiver, '_rx11_wrapper') or not self.transceiver._rx11_wrapper:
            return
            
        wrapper = self.transceiver._rx11_wrapper
        
        # Clear wrapper tracking and rebuild from coordinator's persistent data
        wrapper._used_ewb_indices.clear()
        wrapper._ewb_device_serials.clear()
        
        for index, device_info in self._used_ewb_indices.items():
            gateway_serial = device_info.get('gateway_serial')
            device_serial = device_info.get('device_serial')
            
            if gateway_serial and device_serial:
                wrapper.mark_ewb_index_used(index, gateway_serial, device_serial)
                
        _LOGGER.info("🔄 Synchronized %d EWB indices from coordinator to wrapper", len(self._used_ewb_indices))

    async def unregister_device_permanently(self, serial_number: str) -> bool:
        """Permanently remove device from registered list and clean up all HA entities."""
        try:
            # Get device info before removal for cleanup
            device_info = self._registered_devices.get(serial_number)
            if not device_info:
                _LOGGER.warning("⚠️ Device %s not found in registered list", serial_number[-8:])
                # Try to clean up orphaned entities anyway
                await self._remove_from_ha_registry(serial_number, {})
                return False
            
            device_name = device_info.get('name', serial_number[-8:])
            _LOGGER.info("🗑️  Starting permanent removal of device: %s (%s)", device_name, serial_number[-8:])
            
            # First, remove from Home Assistant registries (entities and device)
            _LOGGER.info("  Step 1/6: Removing from HA registries...")
            await self._remove_from_ha_registry(serial_number, device_info)
            
            # Remove from registered devices
            _LOGGER.info("  Step 2/6: Removing from registered devices...")
            del self._registered_devices[serial_number]
            
            # Also remove from legacy dicts
            _LOGGER.info("  Step 3/6: Removing from legacy dicts...")
            if serial_number in self.devices:
                del self.devices[serial_number]
            self._known_devices.discard(serial_number)
            
            # Remove from whitelist (prevents restoration on restart)
            _LOGGER.info("  Step 4/6: Removing from whitelist...")
            await self._remove_from_whitelist(serial_number)
            
            # Remove from fired events tracking (allow re-registration if needed)
            self._devices_with_fired_events.discard(serial_number)
            
            # Perform device-specific cleanup (RX11, etc.)
            _LOGGER.info("  Step 5/6: Device-specific cleanup...")
            device_type = device_info.get("type", "unknown")
            if device_type == "ew_receiver":
                await self._cleanup_rx11_device(serial_number, device_info)
            elif device_type == "ewneo_receiver":
                await self._cleanup_ewneo_device(serial_number, device_info)
            
            # Save changes to registered devices list
            _LOGGER.info("  Step 6/6: Saving configuration...")
            await self._save_registered_devices()
            
            # Also remove from device config (eldat_devices.json)
            await self._save_device_configuration()
            
            _LOGGER.info("✅ Device %s permanently unregistered and cleaned up (all 6 steps complete)", device_name)
            return True
            
        except Exception as e:
            _LOGGER.error("❌ Failed to unregister device %s: %s", serial_number[-8:], e)
            return False
    
    async def restore_registered_devices_only(self) -> None:
        """Restore ONLY registered devices at startup - ignore others."""
        _LOGGER.info("🔄 Restoring %d registered devices from persistent storage", 
                   len(self._registered_devices))
        
        # Clear any existing devices first
        self.devices.clear()
        self._known_devices.clear()
        
        restored_count = 0
        orphaned_devices = []  # Track devices without HA entities
        
        for serial_number, device_info in self._registered_devices.items():
            try:
                # Normalize device type fields (handle both 'type' and 'device_type')
                device_type = device_info.get('type') or device_info.get('device_type')
                if device_type and 'type' not in device_info:
                    device_info['type'] = device_type
                
                # Validate device info
                if not device_info.get('name') or not device_info.get('type'):
                    _LOGGER.warning("⚠️ Skipping invalid registered device %s", serial_number[-8:])
                    continue
                
                # Restore to legacy devices dict for platform compatibility
                self.devices[serial_number] = device_info
                self._known_devices.add(serial_number)
                
                # Check if device has Home Assistant entities
                ha_device_id = device_info.get("homeassistant_device_id")
                ha_entities = device_info.get("homeassistant_entities", [])
                
                if not ha_device_id or not ha_entities:
                    orphaned_devices.append(serial_number)
                    _LOGGER.warning("⚠️ Device %s is orphaned (no HA entities) - will repair", serial_number[-8:])
                
                restored_count += 1
                _LOGGER.debug("✅ Restored registered device: %s (%s)", 
                            device_info.get('name'), device_info.get('type'))
                
            except Exception as e:
                _LOGGER.error("❌ Failed to restore device %s: %s", serial_number[-8:], e)
        
        _LOGGER.info("✅ Restored %d/%d registered devices - ignoring all others", 
                   restored_count, len(self._registered_devices))
        
        # Events will be fired by fire_pending_device_events() after platform setup
        # No need to store pending events - fire_pending_device_events uses self.devices directly
        # and tracks already-fired events to prevent duplicates

    async def fire_pending_device_events(self) -> None:
        """Fire device events after platforms are set up (only once per device)."""
        # Use all loaded devices from self.devices instead of pending list
        # This ensures we don't miss any devices and avoids duplicates
        devices_to_fire = [
            (serial, info) for serial, info in self.devices.items()
            if serial not in self._devices_with_fired_events
        ]
        
        if not devices_to_fire:
            _LOGGER.debug("No devices to fire events for (all already fired)")
            return
            
        _LOGGER.info("🔥 Firing events for %d devices after platform setup", len(devices_to_fire))
        
        # Fire events for all loaded devices that haven't fired yet
        for serial_number, device_info in devices_to_fire:
            _LOGGER.info("🔥 [fire_pending] Firing EVENT_DEVICE_ADDED for %s", serial_number[-8:])
            
            self.hass.bus.async_fire(
                EVENT_DEVICE_ADDED,
                {
                    "serial_number": serial_number,
                    "device_info": device_info,
                    "restored_from_registered_list": True,
                    "force_create": True
                }
            )
            # Mark as fired to prevent duplicates
            self._devices_with_fired_events.add(serial_number)
            
            # Small delay to prevent overwhelming the system
            await asyncio.sleep(0.05)
        
        # Check for missing switch entities on heating_cooling devices
        await self._check_and_repair_missing_switch_entities()
        
        _LOGGER.info("✅ All device events fired after platform setup")

    async def _check_and_repair_missing_switch_entities(self) -> None:
        """Check for heating_cooling devices that are missing switch entities and repair them."""
        _LOGGER.debug("🔍 Checking for missing switch entities on heating_cooling devices...")
        
        for serial_number, device_info in self._registered_devices.items():
            receiver_kind = device_info.get("receiver_kind")
            if receiver_kind == "heating_cooling":
                platforms = device_info.get("platforms", [])
                if "switch" not in platforms:
                    _LOGGER.info("🔧 Repairing missing switch entity for heating_cooling device %s", serial_number[-6:])
                    
                    # Update device info to include switch platform
                    device_info["platforms"] = platforms + ["switch"]
                    await self._save_registered_devices()
                    
                    # Fire switch-specific event
                    from .entity_specs import create_entity_specs_for_device
                    entity_specs = create_entity_specs_for_device(serial_number, device_info)
                    switch_entities = entity_specs.get("switch", [])
                    
                    if switch_entities:
                        # Fire both switch-specific and force-create events
                        self.hass.bus.async_fire(
                            f"{EVENT_DEVICE_ADDED}_switch",
                            {
                                "serial_number": serial_number,
                                "device_info": device_info,
                                "entities": switch_entities,
                                "platforms": {"switch"},
                                "missing_entity_repair": True,
                                "force_create": True
                            }
                        )
                        
                        # Also fire force create event as backup
                        self.hass.bus.async_fire(
                            EVENT_FORCE_CREATE,
                            {
                                "serial_number": serial_number,
                                "device_info": device_info,
                                "entity_type": "switch",
                                "force_repair": True
                            }
                        )
                        
                        _LOGGER.info("✅ Fired switch creation events for device %s", serial_number[-6:])
                        await asyncio.sleep(0.1)
    
    async def repair_orphaned_devices(self) -> int:
        """Manually repair devices that exist in storage but have no Home Assistant entities."""
        repaired_count = 0
        
        for serial_number, device_info in self._registered_devices.items():
            ha_device_id = device_info.get("homeassistant_device_id")
            ha_entities = device_info.get("homeassistant_entities", [])
            
            if not ha_device_id or not ha_entities:
                _LOGGER.info("🔧 Repairing orphaned device: %s", device_info.get("name", serial_number[-8:]))
                
                self.hass.bus.async_fire(
                    EVENT_DEVICE_ADDED,
                    {
                        "serial_number": serial_number,
                        "device_info": device_info,
                        "orphaned_device_repair": True,
                        "force_create": True
                    }
                )
                repaired_count += 1
                await asyncio.sleep(0.1)  # Small delay between repairs
        
        _LOGGER.info("✅ Repaired %d orphaned devices", repaired_count)
        return repaired_count
    
    async def update_device_ha_info(self, serial_number: str, device_id: str = None, entity_ids: list = None):
        """Update Home Assistant device and entity information for a registered device."""
        if serial_number not in self._registered_devices:
            return
        
        device_info = self._registered_devices[serial_number]
        updated = False
        
        if device_id and device_info.get("homeassistant_device_id") != device_id:
            device_info["homeassistant_device_id"] = device_id
            updated = True
        
        if entity_ids is not None:
            # Update entity list, preserving existing ones and adding new ones
            existing_entities = device_info.get("homeassistant_entities", [])
            existing_entity_ids = {e.get("entity_id") for e in existing_entities if e.get("entity_id")}
            
            for entity_id in entity_ids:
                if entity_id not in existing_entity_ids:
                    existing_entities.append({
                        "entity_id": entity_id,
                        "platform": "eldat_plugin",
                        "device_class": None,
                        "name": None
                    })
            
            device_info["homeassistant_entities"] = existing_entities
            updated = True
        
        if updated:
            # Also update the legacy devices dict
            self.devices[serial_number] = device_info
            await self._save_registered_devices()
            _LOGGER.debug("✅ Updated HA info for device %s", serial_number[-8:])

    @property
    def is_setup_mode_active(self) -> bool:
        """Check if setup mode is currently active."""
        return self._setup_mode_active

    @property
    def transceiver_type(self) -> TransceiverType:
        """Get the transceiver type."""
        return self.transceiver.transceiver_type

    async def async_setup(self) -> bool:
        """Set up the coordinator."""
        try:
            # Initialize registered device management
            await self._load_registered_devices()
            await self._migrate_existing_devices_to_registered_list()
            
            # Initialize EWB index tracking
            await self._load_used_ewb_indices()
            
            # Setup and connect transceiver with timeouts
            if not await asyncio.wait_for(self.transceiver.async_setup(self.hass), timeout=10.0):
                _LOGGER.error("Failed to setup transceiver")
                return False
                
            if not await asyncio.wait_for(self.transceiver.connect(), timeout=15.0):
                _LOGGER.error("Failed to connect transceiver")
                return False
            
            # Set coordinator reference in transceiver for EWB index tracking
            if hasattr(self.transceiver, 'set_coordinator_reference'):
                self.transceiver.set_coordinator_reference(self)
            
            # Synchronize EWB index tracking with transceiver wrapper
            await self._sync_ewb_indices_with_wrapper()
            
            # Set telegram callback
            self.transceiver.set_telegram_callback(self._handle_telegram)
            
            # Load device configuration from storage
            await self._load_device_configuration(fire_events=False)
            
            # EWB telegram monitoring is handled by the wrapper's continuous loop
            # No need for separate coordinator EWB monitoring
            _LOGGER.info("📡 EWB telegram monitoring handled by wrapper continuous loop")
            
            # Mark coordinator as successfully updated
            self.last_update_success = True
            self._setup_complete = True
            
            # Save whitelist if devices were added during setup
            if self._whitelist_dirty:
                _LOGGER.info("💾 Saving whitelist with %d devices", len(self._device_whitelist))
                asyncio.create_task(self._save_whitelist_async())
            
            _LOGGER.info("Modern coordinator setup completed for %s transceiver", 
                        self.transceiver_type.value)
            return True
            
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout during coordinator setup")
            self.last_update_success = False
            return False
        except Exception as e:
            _LOGGER.error("Error setting up modern coordinator: %s", e)
            self.last_update_success = False
            return False

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        _LOGGER.debug("🛑 Shutting down modern coordinator...")
        
        # Set shutdown event
        self._shutdown_event.set()
        
        # Cancel EWB monitoring task
        if hasattr(self, '_ewb_monitoring_task') and self._ewb_monitoring_task:
            self._ewb_monitoring_task.cancel()
            try:
                await self._ewb_monitoring_task
            except asyncio.CancelledError:
                pass
            _LOGGER.info("✅ EWB monitoring task cancelled")
        
        # Remove telegram listener
        if self._telegram_listener_remove:
            self._telegram_listener_remove()
            self._telegram_listener_remove = None
        
        # Disconnect transceiver
        if self.transceiver:
            try:
                await self.transceiver.disconnect()
                _LOGGER.info("✅ Transceiver disconnected")
            except Exception as e:
                _LOGGER.error("❌ Error disconnecting transceiver: %s", e)
        
        _LOGGER.info("✅ Modern coordinator shutdown completed")

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from the transceiver."""
        if not self.transceiver:
            raise UpdateFailed("Transceiver not available")
            
        # Check if transceiver connection changed
        is_connected = self.transceiver.is_connected
        if hasattr(self, '_last_connection_state') and self._last_connection_state != is_connected:
            if is_connected:
                _LOGGER.info("🔌 USB transmitter connected - entities are now available")
            else:
                _LOGGER.warning("⚠️  USB transmitter disconnected - entities are unavailable")
            
            # Trigger entity state update for all entities
            self.async_update_listeners()
        
        self._last_connection_state = is_connected
            
        try:
            # Only get versions once every 30 updates or on first update
            if not hasattr(self, '_update_count'):
                self._update_count = 0
                self._cached_hw_version = None
                self._cached_fw_version = None
                
            self._update_count += 1
            
            # Get versions only occasionally to reduce hardware queries
            if self._update_count == 1 or self._update_count % 30 == 0:
                try:
                    self._cached_hw_version = await self.transceiver.get_hw_version()
                    self._cached_fw_version = await self.transceiver.get_fw_version()
                    if self._update_count == 1:
                        _LOGGER.debug("✅ Versions cached: HW=%s, FW=%s", self._cached_hw_version, self._cached_fw_version)
                except Exception as e:
                    _LOGGER.debug("Could not get transceiver versions: %s", e)
                    
            # Run ghost device cleanup every 120 updates (approximately every 2 hours)
            if self._update_count % 120 == 0:
                try:
                    await self.async_cleanup_ghost_devices()
                except Exception as e:
                    _LOGGER.error("Error during periodic ghost device cleanup: %s", e)
            
            return {
                "hw_version": self._cached_hw_version,
                "fw_version": self._cached_fw_version,
                "device_count": len(self.devices),
                "transceiver_type": self.transceiver_type.value,
                "last_update": datetime.now().isoformat(),
                "transceiver_connected": is_connected,
            }
        except Exception as e:
            raise UpdateFailed(f"Error fetching data from transceiver: {e}") from e

    async def _handle_telegram(self, telegram_data: Dict[str, Any]) -> None:
        """Handle incoming telegram from transceiver - only process registered devices."""
        try:
            _LOGGER.info("📥 Handling telegram: %s", {k: v for k, v in telegram_data.items() if k != 'raw_data'})
            
            serial_number = telegram_data.get("serial_number")
            if not serial_number:
                _LOGGER.warning("Received telegram without serial number")
                return

            # Check if this telegram is from a registered EWneo device
            is_ewneo_device = self._is_ewneo_device_telegram(serial_number)
            
            # Fire button events IMMEDIATELY for EW-Transmitters, but NOT for EWneo devices
            # This ensures Binary Sensors work even if device registration has issues
            _LOGGER.info("🔍 Checking telegram for button events: type=%s, info_type=%s, is_ewneo=%s", 
                        telegram_data.get("type"), telegram_data.get("info_type"), is_ewneo_device)
            
            if (telegram_data.get("type") == "ew_transmitter" or telegram_data.get("info_type") in [0, 1]) and not is_ewneo_device:
                _LOGGER.info("🔵 Firing button events for EW-Transmitter telegram (serial: %s)", 
                           serial_number[-8:])
                await self._fire_button_events(serial_number, telegram_data)
            elif is_ewneo_device:
                _LOGGER.info("🔄 Processing EWneo device telegram %s as state update", 
                           serial_number[-8:])
            
            # Debug: Check registration status
            is_registered = self.is_device_registered(serial_number)
            _LOGGER.info("🔍 Telegram from %s - Registered: %s (checking exact serial: %s)", 
                         serial_number[-8:], is_registered, serial_number)
            _LOGGER.debug("🔍 Registered devices: %s", 
                         list(self._registered_devices.keys()))
            
            # Check if device is registered for management
            if not is_registered:
                # Only process learn telegrams for potential new device registration
                is_learn = telegram_data.get("is_learn_telegram", False) or self.is_setup_mode_active
                if not is_learn:
                    _LOGGER.debug("📡 Ignoring telegram from unregistered device %s (not in setup mode)", 
                                serial_number[-8:])
                    return
                _LOGGER.info("🎓 Processing telegram for potential registration of device %s", 
                           serial_number[-8:])
            
            _LOGGER.debug("Processing telegram for registered device %s (type: %s)", 
                        serial_number[-8:], telegram_data.get("type", "unknown"))
            
            # Update device info if known device
            if serial_number in self.devices:
                self.devices[serial_number]["last_seen"] = time.time()
                self.devices[serial_number]["last_telegram"] = telegram_data
                
                # Process through device instance first if available
                device_instance = None
                if (hasattr(self.transceiver, '_device_instances') and 
                    serial_number in self.transceiver._device_instances):
                    device_instance = self.transceiver._device_instances[serial_number]
                    
                    # Let device instance process the telegram and update its state
                    if hasattr(device_instance, 'process_telegram'):
                        processed_data = device_instance.process_telegram(telegram_data)
                        # If device returns processed data, use it
                        if processed_data:
                            telegram_data.update(processed_data)
                    
                    # Get current state from device instance
                    if hasattr(device_instance, 'get_state'):
                        device_state = device_instance.get_state()
                        self.devices[serial_number].update(device_state)
                        _LOGGER.debug("Updated device %s state from instance: %s", 
                                    serial_number[-6:], list(device_state.keys()))
                
                # Update device-specific data from telegram (fallback or additional data)
                for key in ["temperature", "humidity", "battery_level", "battery_status", 
                           "button", "function", "is_push", "is_release", "is_low_battery"]:
                    if key in telegram_data:
                        old_value = self.devices[serial_number].get(key)
                        new_value = telegram_data[key]
                        self.devices[serial_number][key] = new_value
                        if old_value != new_value:
                            _LOGGER.debug("Device %s: %s updated from %s to %s", 
                                        serial_number[-6:], key, old_value, new_value)
                
                # Fire telegram event
                _LOGGER.debug("Firing EVENT_TELEGRAM_RECEIVED for device %s", serial_number[-6:])
                self.hass.bus.async_fire(
                    EVENT_TELEGRAM_RECEIVED,
                    {
                        "serial_number": serial_number,
                        "device_type": self.devices[serial_number].get("type"),
                        "telegram_data": telegram_data,
                    }
                )
                
                # Fire device updated event
                _LOGGER.debug("Firing EVENT_DEVICE_UPDATED for device %s", serial_number[-6:])
                self.hass.bus.async_fire(
                    EVENT_DEVICE_UPDATED,
                    {
                        "serial_number": serial_number,
                        "device_info": self.devices[serial_number],
                        "telegram_data": telegram_data,
                    }
                )
                
                # Check if ew_transmitter needs to be upgraded to ew_transceiver
                await self._check_transmitter_upgrade(serial_number, telegram_data)
                
                # Fire specific entity events based on device type and telegram data
                await self._fire_specific_entity_events(serial_number, telegram_data)
                
                # Process EWneo device state updates
                if is_ewneo_device:
                    await self._process_ewneo_state_update(serial_number, telegram_data)
                
                # Request refresh to update entities
                await self.async_request_refresh()
            else:
                # Unknown device - ignore (no auto-discovery)
                device_type = telegram_data.get("type", "unknown")
                _LOGGER.debug("Received telegram from unknown device %s (type: %s) - ignoring (auto-discovery disabled)", 
                            serial_number[-6:], device_type)
                    
        except Exception as e:
            _LOGGER.error("Error handling telegram: %s", e)

    async def _register_device_with_transceiver(self, serial_number: str, device_info: Dict[str, Any], telegram_data: Dict[str, Any]) -> None:
        """Register device with transceiver using structured device classes."""
        try:
            # Create device instance using device factory first
            if hasattr(self.transceiver, 'device_factory'):
                device_instance = self.transceiver.device_factory.create_device(
                    serial_number=serial_number,
                    device_info=device_info,
                    telegram_data=telegram_data
                )
                
                if device_instance:
                    # Store device instance in transceiver
                    if not hasattr(self.transceiver, '_device_instances'):
                        self.transceiver._device_instances = {}
                    self.transceiver._device_instances[serial_number] = device_instance
                    
                    _LOGGER.debug("Created and registered device instance for %s: %s", 
                                serial_number[-6:], type(device_instance).__name__)
                else:
                    _LOGGER.warning("Device factory failed to create instance for %s", serial_number[-6:])
            
            # Register with basic transceiver for backwards compatibility
            await self.transceiver.register_device(serial_number, device_info)
                
        except Exception as e:
            _LOGGER.error("Error registering device %s with transceiver: %s", serial_number[-6:], e)

    async def _check_transmitter_upgrade(self, serial_number: str, telegram_data: Dict[str, Any]) -> None:
        """Check if an ew_transmitter should be upgraded to ew_transceiver when it sends sensor data."""
        try:
            device = self.devices.get(serial_number)
            if not device:
                return
                
            device_type = device.get("type")
            
            # Only upgrade ew_transmitter devices
            if device_type != "ew_transmitter":
                return
                
            # Check if telegram contains sensor data (temperature, humidity, battery)
            has_sensor_data = any(key in telegram_data for key in ["temperature", "humidity", "battery_level"])
            
            if has_sensor_data:
                _LOGGER.warning("🔄 EW-Transmitter %s is sending sensor data, upgrading to EW-Transceiver", 
                              serial_number[-8:])
                
                # Update device configuration
                device_updates = {
                    "type": "ew_transceiver",
                    "device_type": "ew_transceiver", 
                    "supports_sensors": True,
                    "supports_buttons": True,
                    "bidirectional": True,
                    "sensor_types": ["temperature", "humidity", "battery"],
                    "measurement_types": ["temperature", "humidity"],
                    "available_sensors": ["temperature", "humidity", "battery"],
                    "detected_via": "auto_upgrade_from_telegram",
                    "discovered": True
                }
                
                # Update sensor values if present
                if "temperature" in telegram_data:
                    device_updates["last_temperature"] = telegram_data["temperature"]
                if "humidity" in telegram_data:
                    device_updates["last_humidity"] = telegram_data["humidity"]
                if "battery_level" in telegram_data:
                    device_updates["battery_level"] = telegram_data["battery_level"]
                
                # Update device in memory
                device.update(device_updates)
                
                # Device registry update is handled by the coordinator's device_registry instance
                # No direct device_registry.update_device call needed as it's managed by coordinator
                
                # Add missing sensor entities
                await self._add_transceiver_sensor_entities(serial_number, device)
                
                _LOGGER.info("✅ Successfully upgraded %s from EW-Transmitter to EW-Transceiver", serial_number[-8:])
                
        except Exception as e:
            _LOGGER.error("❌ Error upgrading transmitter %s: %s", serial_number[-8:], e)

    async def _add_transceiver_sensor_entities(self, serial_number: str, device_info: Dict[str, Any]) -> None:
        """Add sensor entities for an upgraded transceiver."""
        try:
            # Define the sensor entities that should be added
            new_entities = [
                {
                    "type": "sensor",
                    "sensor_type": "temperature", 
                    "name": "Temperature",
                    "unique_id": f"{serial_number}_temperature",
                    "device_class": "temperature",
                    "unit_of_measurement": "°C",
                    "icon": "mdi:thermometer",
                    "state_class": "measurement",
                    "current_value": device_info.get("last_temperature")
                },
                {
                    "type": "sensor",
                    "sensor_type": "humidity",
                    "name": "Humidity", 
                    "unique_id": f"{serial_number}_humidity",
                    "device_class": "humidity",
                    "unit_of_measurement": "%",
                    "icon": "mdi:water-percent",
                    "state_class": "measurement",
                    "current_value": device_info.get("last_humidity")
                },
                {
                    "type": "sensor",
                    "sensor_type": "battery",
                    "name": "Battery",
                    "unique_id": f"{serial_number}_battery",
                    "device_class": "battery",
                    "unit_of_measurement": "%", 
                    "icon": "mdi:battery",
                    "state_class": "measurement",
                    "current_value": device_info.get("battery_level")
                }
            ]
            
            # Add entities to device
            if "entities" not in device_info:
                device_info["entities"] = []
                
            # Add new entities if they don't already exist
            existing_unique_ids = {entity.get("unique_id") for entity in device_info["entities"]}
            for new_entity in new_entities:
                if new_entity["unique_id"] not in existing_unique_ids:
                    device_info["entities"].append(new_entity)
                    _LOGGER.info("📊 Added %s sensor entity for upgraded transceiver %s", 
                               new_entity["sensor_type"], serial_number[-8:])
            
            # Fire sensor added event to trigger entity creation
            self.hass.bus.async_fire(
                EVENT_SENSOR_ADDED,
                {
                    "serial_number": serial_number,
                    "device_info": device_info,
                    "entities": new_entities,
                    "source": "auto_upgrade"
                }
            )
            
        except Exception as e:
            _LOGGER.error("❌ Error adding sensor entities for transceiver %s: %s", serial_number[-8:], e)

    async def _fire_button_events(self, serial_number: str, telegram_data: Dict[str, Any]) -> None:
        """Fire button events for EW-Transmitter devices immediately, regardless of registration."""
        try:
            button = telegram_data.get("button")
            # Use 'function' field from telegram, fallback to 'action' or 'is_release' detection
            action = telegram_data.get("function") or telegram_data.get("action")
            
            # If no explicit action, determine from is_push/is_release flags
            if not action:
                if telegram_data.get("is_release"):
                    action = "release"
                elif telegram_data.get("is_push"):
                    action = "press"
                else:
                    action = "press"  # Default fallback
            
            if button is None:
                _LOGGER.warning("Button data missing from EW-Transmitter telegram")
                return
                
            _LOGGER.info("🔍 Button event details: button=%s, action=%s, function=%s, is_push=%s, is_release=%s", 
                         button, action, telegram_data.get("function"), 
                         telegram_data.get("is_push"), telegram_data.get("is_release"))
                
            # Determine event type based on action
            if action in ["press", "push", "on", "1", 1]:
                # Check for low battery condition
                is_low_battery = telegram_data.get("is_low_battery", False)
                if is_low_battery:
                    event_type = "eldat_plugin_button_low_battery"
                    is_push = True
                    is_release = False
                    _LOGGER.warning("🔋 Low battery detected for device %s button %s", serial_number[-8:], button)
                else:
                    event_type = "eldat_plugin_button_press"
                    is_push = True
                    is_release = False
            elif action in ["release", "off", "0", 0]:
                event_type = "eldat_plugin_button_release"
                is_push = False
                is_release = True
            else:
                event_type = "eldat_plugin_button_press"  # Default to press
                is_push = True
                is_release = False
            
            event_data = {
                "device_id": serial_number,  # Use serial_number directly for device automation
                "serial_number": serial_number,
                "button": button,
                "action": action,
                "is_push": is_push,
                "is_release": is_release,
                "is_low_battery": telegram_data.get("is_low_battery", False),
                "battery_level": telegram_data.get("battery_level", 100),
                "battery_status": telegram_data.get("battery_status", "good"),
                "telegram": telegram_data
            }
            
            _LOGGER.info("🚨 Firing %s for device %s button %s (action=%s)", 
                         event_type, serial_number[-8:], button, action)
                         
            # Fire original event for backward compatibility
            self.hass.bus.async_fire(event_type, event_data)
            
            # Fire device automation compatible events
            button_name = telegram_data.get("button_name", f"button_{button}")
            
            # Fire device trigger compatible events
            if action in ["press", "push", "on", "1", 1]:
                # Check for low battery condition
                if telegram_data.get("is_low_battery", False):
                    # Battery low event
                    self.hass.bus.async_fire("eldat_button_battery_low", {
                        "device_id": serial_number,
                        "subtype": button_name,
                        "button": button,
                        "button_name": button_name,
                        "battery_level": telegram_data.get("battery_level", 0),
                        "device_name": self.devices.get(serial_number, {}).get("name", "Unknown")
                    })
                    _LOGGER.warning("🪫 Battery low event fired for %s button %s (level: %s%%)", 
                                  serial_number[-8:], button_name, telegram_data.get("battery_level", 0))
                    
                # Button press start event
                self.hass.bus.async_fire("eldat_button_press_start", {
                    "device_id": serial_number,
                    "subtype": button_name,
                    "button": button,
                    "button_name": button_name,
                    "is_low_battery": telegram_data.get("is_low_battery", False),
                    "device_name": self.devices.get(serial_number, {}).get("name", "Unknown")
                })
                
                # Short press event (for immediate action)
                self.hass.bus.async_fire("eldat_button_short_press", {
                    "device_id": serial_number,
                    "subtype": button_name,
                    "button": button,
                    "button_name": button_name,
                    "device_name": self.devices.get(serial_number, {}).get("name", "Unknown")
                })
                
            elif action in ["release", "off", "0", 0]:
                # Button press end event
                self.hass.bus.async_fire("eldat_button_press_end", {
                    "device_id": serial_number,
                    "subtype": button_name,
                    "button": button,
                    "button_name": button_name,
                    "device_name": self.devices.get(serial_number, {}).get("name", "Unknown")
                })
            
        except Exception as e:
            _LOGGER.error("Error firing button events for %s: %s", serial_number[-8:], e)

    async def _fire_specific_entity_events(self, serial_number: str, telegram_data: Dict[str, Any]) -> None:
        """Fire specific events that entities are listening for."""
        try:
            device = self.devices.get(serial_number)
            if not device:
                return
                
            device_type = device.get("type")
            info_type = telegram_data.get("info_type")
            
            # Handle EW-Transmitter button press/release events
            if device_type == "ew_transmitter" or info_type == 1:
                button = telegram_data.get("button")
                function = telegram_data.get("function")
                is_push = telegram_data.get("is_push", False)
                is_release = telegram_data.get("is_release", False)
                is_low_battery = telegram_data.get("is_low_battery", False)
                button_name = telegram_data.get("button_name", f"Button {button}")
                
                if button is not None:
                    # Determine event type based on new parsing format
                    if is_push:
                        if is_low_battery:
                            event_type = f"{DOMAIN}_button_low_battery"
                        else:
                            event_type = f"{DOMAIN}_button_press"
                    elif is_release:
                        event_type = f"{DOMAIN}_button_release"
                    else:
                        # Fallback to old logic for compatibility
                        if function == 1:  # Press
                            event_type = f"{DOMAIN}_button_press"
                        elif function == 0:  # Release  
                            event_type = f"{DOMAIN}_button_release"
                        else:
                            event_type = f"{DOMAIN}_button_press"  # Default to press
                    
                    # Fire button event
                    self.hass.bus.async_fire(
                        event_type,
                        {
                            "device_id": serial_number,
                            "serial_number": serial_number,
                            "button": button,
                            "button_name": button_name,
                            "function": function,
                            "is_push": is_push,
                            "is_release": is_release,
                            "is_low_battery": is_low_battery,
                            "battery_level": telegram_data.get("battery_level", 100),
                            "battery_status": telegram_data.get("battery_status", "good"),
                            "additional_info": telegram_data.get("additional_info"),
                            "raw_data": telegram_data.get("raw_data"),
                            "timestamp": telegram_data.get("timestamp"),
                        }
                    )
                    
                    _LOGGER.debug("Fired %s event for device %s button %s (%s)", 
                                event_type, serial_number[-6:], button, button_name)
            
            # Handle EW-Sensor measurement events
            elif device_type == "ew_sensor" or info_type == 2:
                # Extract sensor measurements
                temperature = telegram_data.get("temperature")
                humidity = telegram_data.get("humidity")
                battery_level = telegram_data.get("battery_level")
                battery_status = telegram_data.get("battery_status", "unknown")
                
                # Fire individual sensor events for each measurement type
                if temperature is not None:
                    temp_event_data = {
                        "serial_number": serial_number,
                        "device_id": serial_number,
                        "measurement_type": "temperature",
                        "value": temperature,
                        "timestamp": telegram_data.get("timestamp"),
                        "battery_level": battery_level,
                        "battery_status": battery_status,
                        "raw_data": telegram_data.get("raw_data"),
                    }
                    self.hass.bus.async_fire(f"{DOMAIN}_sensor_update", temp_event_data)
                    _LOGGER.info("Fired temperature event for device %s: %.1f°C", 
                               serial_number[-6:], temperature)
                
                if humidity is not None:
                    hum_event_data = {
                        "serial_number": serial_number,
                        "device_id": serial_number,
                        "measurement_type": "humidity",
                        "value": humidity,
                        "timestamp": telegram_data.get("timestamp"),
                        "battery_level": battery_level,
                        "battery_status": battery_status,
                        "raw_data": telegram_data.get("raw_data"),
                    }
                    self.hass.bus.async_fire(f"{DOMAIN}_sensor_update", hum_event_data)
                    _LOGGER.info("Fired humidity event for device %s: %.1f%%", 
                               serial_number[-6:], humidity)
                
                # Also fire a combined sensor update event (legacy support)
                combined_event_data = {
                    "serial_number": serial_number,
                    "device_id": serial_number,
                    "timestamp": telegram_data.get("timestamp"),
                    "battery_level": battery_level,
                    "battery_status": battery_status,
                    "raw_data": telegram_data.get("raw_data"),
                    "temperature": temperature,
                    "humidity": humidity,
                }
                self.hass.bus.async_fire(f"{DOMAIN}_sensor_update", combined_event_data)
                
                _LOGGER.debug("Fired combined sensor_update event for device %s with temp=%s, hum=%s",
                            serial_number[-6:], temperature, humidity)
                            
        except Exception as e:
            _LOGGER.error("Error firing specific entity events: %s", e)

    def _is_ewneo_device_telegram(self, serial_number: str) -> bool:
        """Check if a telegram is from a registered EWneo device using stored gateway+receiver pairs."""
        try:
            # Check if this serial number matches any registered EWneo receiver
            for device_serial, device_info in self._registered_devices.items():
                if device_info.get("neo_device", False):
                    # For EWneo devices, check both gateway and receiver serials
                    gateway_serial = device_info.get("gateway_serial")
                    receiver_serial = device_info.get("serial_number", device_serial)
                    
                    # Telegram could be from either gateway or receiver
                    if serial_number == gateway_serial or serial_number == receiver_serial:
                        _LOGGER.debug("📡 EWneo telegram from %s (device: %s, gateway: %s)", 
                                    serial_number[-8:], device_serial[-8:], 
                                    gateway_serial[-8:] if gateway_serial else "None")
                        return True
                        
            return False
            
        except Exception as e:
            _LOGGER.error("Error checking EWneo device telegram: %s", e)
            return False

    async def _process_ewneo_state_update(self, serial_number: str, telegram_data: Dict[str, Any]) -> None:
        """Process state updates for EWneo devices and notify entities."""
        try:
            _LOGGER.debug("🔍 Processing EWneo state update for %s with telegram: %s", 
                         serial_number[-8:], telegram_data)
            
            # Find the EWneo device that matches this telegram
            target_device_info = None
            target_device_serial = None
            
            for device_serial, device_info in self._registered_devices.items():
                if device_info.get("neo_device", False):
                    gateway_serial = device_info.get("gateway_serial")
                    receiver_serial = device_info.get("serial_number", device_serial)
                    
                    # Check if telegram is from this EWneo device
                    if serial_number == gateway_serial or serial_number == receiver_serial:
                        target_device_info = device_info
                        target_device_serial = device_serial
                        break
                        
            if not target_device_info:
                _LOGGER.debug("No matching EWneo device found for telegram from %s", serial_number[-8:])
                return
                
            _LOGGER.info("🔄 Processing EWneo state update for device %s (telegram from %s)", 
                        target_device_serial[-8:], serial_number[-8:])
            
            # Extract state bytes from telegram's raw_data.info_data (EWB telegram format)
            raw_data = telegram_data.get("raw_data", {})
            info_data_hex = raw_data.get("info_data", "")
            
            state_bytes = []
            if info_data_hex:
                try:
                    # Convert hex string to bytes and take first 4 bytes (EWB state data)
                    info_data_bytes = bytes.fromhex(info_data_hex)
                    state_bytes = list(info_data_bytes[:4])  # EWB state is 4 bytes
                    _LOGGER.debug("🔍 EWneo device %s: Extracted state_bytes: %s from info_data: %s", 
                                serial_number[-8:], [f"0x{b:02X}" for b in state_bytes], info_data_hex)
                except ValueError as e:
                    _LOGGER.warning("⚠️ Could not parse info_data hex for EWneo device %s: %s (hex: %s)", 
                                  serial_number[-8:], e, info_data_hex)
                    return
            else:
                _LOGGER.warning("⚠️ No info_data found in telegram for EWneo device %s", serial_number[-8:])
                return
            
            device_type_code = target_device_info.get("device_type_code", 0)
            device_type_name = target_device_info.get("device_type_name", "ewneo_switch")
            
            _LOGGER.debug("🔍 EWneo device %s: device_type_code=%s, device_type_name=%s", 
                         target_device_serial[-8:], device_type_code, device_type_name)
            
            if state_bytes:
                # Parse the state using our existing parser
                parsed_state = self._parse_ewneo_state(device_type_code, state_bytes, device_type_name, target_device_serial)
                
                if parsed_state:
                    _LOGGER.info("🎯 EWneo device %s: Parsed state update: %s", 
                               target_device_serial[-8:], parsed_state)
                    
                    # Fire an event that EWneo entities can listen to using the device serial (not telegram serial)
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_ewneo_state_update",
                        {
                            "serial_number": target_device_serial,  # Use device serial, not telegram serial
                            "device_id": target_device_serial,
                            "parsed_state": parsed_state,
                            "state_bytes": state_bytes,
                            "telegram_data": telegram_data,
                            "telegram_from": serial_number,  # Track which serial sent the telegram
                            "timestamp": telegram_data.get("timestamp"),
                        }
                    )
                    
                    _LOGGER.debug("Fired EWneo state update event for device %s (from %s)", 
                                target_device_serial[-8:], serial_number[-8:])
                else:
                    _LOGGER.warning("⚠️ Could not parse EWneo state for device %s (state_bytes: %s)", 
                                  target_device_serial[-8:], [f"0x{b:02X}" for b in state_bytes])
            else:
                _LOGGER.debug("No state_bytes extracted from telegram for EWneo device %s", target_device_serial[-8:])
                
        except Exception as e:
            _LOGGER.error("Error processing EWneo state update for %s: %s", serial_number[-8:], e)

    # Auto-discovery methods removed - devices must be manually added

    async def register_device(self, serial_number: str, device_info: Dict[str, Any]) -> None:
        """Register a new device."""
        # Check for ghost device prevention (except for manually added devices)
        if not device_info.get("added_manually", False):
            if not await self.async_prevent_ghost_device_creation(serial_number, device_info):
                _LOGGER.warning("Ghost device prevention blocked registration of %s", serial_number)
                return
        
        # Store in devices dict
        self.devices[serial_number] = device_info
        self._known_devices.add(serial_number)
        
        # Add to whitelist (memory-only during setup, saved after)
        self._add_to_whitelist_memory(
            serial_number=serial_number,
            rx11_index=device_info.get("rx11_index"),
            device_type=device_info.get("device_type", device_info.get("type", "unknown")),
            source="telegram" if not device_info.get("added_manually") else "manual"
        )
        
        # Only register with transceiver if it's connected
        if self.transceiver and self.transceiver.is_connected:
            await self.transceiver.register_device(serial_number, device_info)
        else:
            _LOGGER.warning("Device %s registered but transceiver not connected - device will be unavailable", serial_number)
        
        # Save configuration
        await self._save_device_configuration()
        
        # Only fire events if setup is complete and platforms are loaded
        # Otherwise, events will be fired by fire_pending_device_events()
        if not self._setup_complete:
            _LOGGER.debug("Setup not complete, device %s will be fired later", serial_number[-8:])
            return
        
        # Check if event was already fired for this device (prevent duplicates)
        if serial_number in self._devices_with_fired_events:
            _LOGGER.debug("Event already fired for %s, skipping duplicate", serial_number[-8:])
            return
        
        # Fire device added event with slight delay to ensure Home Assistant is ready
        async def fire_device_added_event():
            await asyncio.sleep(0.1)  # Small delay to ensure readiness
            
            # Import here to avoid circular import
            from .helpers import get_entity_specs_for_device
            
            # Get entity information for proper platform detection
            entity_info = get_entity_specs_for_device(device_info)
            
            _LOGGER.info("🔥 [register_device] Firing EVENT_DEVICE_ADDED for %s", serial_number[-8:])
            
            self.hass.bus.async_fire(
                EVENT_DEVICE_ADDED,
                {
                    "serial_number": serial_number,
                    "device_info": device_info,
                    "device_type": device_info.get("type"),
                    "device_name": device_info.get("name"),
                    "entity_info": entity_info,  # Add entity_info with platforms
                    "entities": device_info.get("entities", []),  # Add entities to event data
                }
            )
            
            # Mark event as fired to prevent duplicates
            self._devices_with_fired_events.add(serial_number)
            
            _LOGGER.info("🚀 [register_device] Device added event fired for %s (platforms: %s)", 
                        serial_number[-8:], entity_info.get("platforms", set()))
            
            # For heating/cooling receivers, also fire a switch-specific event
            receiver_kind = device_info.get("receiver_kind")
            if (device_info.get("type") == "ew_receiver" and 
                receiver_kind in ["heating", "cooling", "heating_cooling"]):
                
                # Create a switch entity spec for the heating/cooling switch
                from .entity_specs import create_entity_specs_for_device
                entity_specs = create_entity_specs_for_device(serial_number, device_info)
                switch_entities = entity_specs.get("switch", [])
                
                if switch_entities:
                    # Fire switch-specific event
                    self.hass.bus.async_fire(
                        f"{EVENT_DEVICE_ADDED}_switch",
                        {
                            "serial_number": serial_number,
                            "device_info": device_info,
                            "entities": switch_entities,
                            "platforms": {"switch"},
                            "force_create": True,
                            "heating_cooling_device": True
                        }
                    )
                    _LOGGER.info("🌡️ Fired switch-specific event for heating/cooling device %s", serial_number[-6:])
                else:
                    _LOGGER.warning("⚠️ No switch entities generated for heating/cooling device %s", serial_number[-6:])
            
        # Schedule the event firing
        self.hass.async_create_task(fire_device_added_event())
        
        _LOGGER.info("Device registered: %s (%s)", 
                    device_info.get("name"), serial_number)

    async def unregister_device(self, serial_number: str, force_remove: bool = False, add_to_blacklist: bool = True) -> bool:
        """Remove device from registered list and clean up entities."""
        # Use permanent unregistration method
        return await self.unregister_device_permanently(serial_number)
    async def _cleanup_rx11_device(self, serial_number: str, device_info: Dict[str, Any]) -> None:
        """Clean up RX11-based device resources including EW-Receiver mappings."""
        try:
            device_type = device_info.get("type", "unknown")
            
            # Clean up EW-Receiver mapping if this was an EW-Receiver device
            ew_receiver_index = device_info.get("ew_receiver_index")
            ew_receiver_serial = device_info.get("ew_receiver_serial")
            
            if ew_receiver_index is not None and ew_receiver_serial:
                # Remove EW-Receiver mapping from device registry
                if self.device_registry.remove_ew_receiver_mapping(ew_receiver_index):
                    _LOGGER.info("🗑️ Removed EW-Receiver mapping: Index %d, Serial %s", 
                               ew_receiver_index, ew_receiver_serial[-8:])
                
                # Free up receiver in transceiver for reuse
                if hasattr(self.transceiver, '_rx11_wrapper'):
                    self.transceiver._rx11_wrapper.mark_receiver_free(ew_receiver_index)
                    _LOGGER.info("♻️ Freed EW-Receiver index %d for reuse", ew_receiver_index)
            
            # Save device registry changes
            await self.device_registry.save_registry()
            
            _LOGGER.info("🧹 RX11 device cleanup completed for %s (%s)", serial_number[-8:], device_type)
            
        except Exception as e:
            _LOGGER.error("Error cleaning up RX11 device %s: %s", serial_number, e)

    async def _cleanup_ewneo_device(self, serial_number: str, device_info: Dict[str, Any]) -> None:
        """Clean up EWneo-based device resources including EWB index mappings."""
        try:
            # Clean up EWB index mapping if this was an EWneo device
            ewneo_index = device_info.get("ewneo_index")
            gateway_serial = device_info.get("gateway_serial")
            receiver_serial = serial_number  # The device's own serial is the receiver serial
            
            # Try to remove the device via EWB protocol first
            if ewneo_index is not None and gateway_serial and receiver_serial:
                _LOGGER.info("🗑️ Attempting to remove EWneo device via EwbRemoveDevice - Gateway: %s, Receiver: %s", 
                           gateway_serial[-8:], receiver_serial[-8:])
                
                try:
                    success = await self.transceiver.rx11_ewb_remove_device(gateway_serial, receiver_serial)
                    if success:
                        _LOGGER.info("✅ EWneo device successfully removed via EwbRemoveDevice")
                    else:
                        _LOGGER.warning("⚠️ EwbRemoveDevice failed, continuing with cleanup")
                except Exception as e:
                    _LOGGER.warning("⚠️ EwbRemoveDevice error: %s, continuing with cleanup", e)
                    
            if ewneo_index is not None:
                # Free up EWB index for reuse
                self.mark_ewb_index_free(ewneo_index)
                _LOGGER.info("♻️ Freed EWB index %d for reuse (was: %s)", ewneo_index, serial_number[-8:])
            
            _LOGGER.info("🧹 EWneo device cleanup completed for %s (EWB index: %s)", 
                        serial_number[-8:], ewneo_index)
            
        except Exception as e:
            _LOGGER.error("Error cleaning up EWneo device %s: %s", serial_number, e)

    async def _save_device_configuration(self) -> None:
        """Save device configuration to persistent storage."""
        try:
            await self.device_config_manager.save_device_config(self.devices)
        except Exception as e:
            _LOGGER.error("Error saving device configuration: %s", e)

    async def _load_device_configuration(self, fire_events: bool = True) -> None:
        """Load device configuration with whitelist filtering."""
        try:
            # Load whitelist
            whitelist_dict = await self.device_config_manager.load_device_whitelist()
            self._device_whitelist = set(whitelist_dict.keys())
            
            if self._device_whitelist:
                _LOGGER.info("🔐 Whitelist loaded: %d registered devices", len(self._device_whitelist))
            else:
                _LOGGER.info("📝 Whitelist is empty - new devices will be added automatically")
            
            # Load all saved devices
            saved_devices = await self.device_config_manager.load_devices()
            _LOGGER.info("🔄 Loading device configuration - found %d saved devices", len(saved_devices))
            
            # Filter by whitelist
            loaded_count = 0
            skipped_count = 0
            
            for serial_number, device_info in saved_devices.items():
                # Check whitelist (skip if not whitelisted and whitelist is not empty)
                if self._whitelist_mode and self._device_whitelist and serial_number not in self._device_whitelist:
                    _LOGGER.debug("⏭️  Skipping non-whitelisted device %s", serial_number[-8:])
                    skipped_count += 1
                    continue
                
                _LOGGER.info("✅ Loading device %s (type=%s)", 
                           serial_number[-6:], device_info.get("device_type", "unknown"))
                
                # Enhanced RX11-based device restoration
                await self._restore_rx11_device(serial_number, device_info)
                
                # Auto-correct device type if unknown but has indicators
                original_type = device_info.get("device_type", "unknown")
                if original_type == "unknown":
                    device_info = self._auto_correct_device_type(serial_number, device_info)
                
                # Ensure both type fields are set consistently
                device_info["type"] = device_info.get("device_type", "unknown")
                
                # Regenerate entity specs for consistent naming
                device_info = await self._regenerate_entity_specs(serial_number, device_info)
                
                self.devices[serial_number] = device_info
                self._known_devices.add(serial_number)
                loaded_count += 1
                
                # Register with transceiver
                await self.transceiver.register_device(serial_number, device_info)
                
                # Events will be fired by fire_pending_device_events() after platform setup
            
            # Log summary
            if skipped_count > 0:
                _LOGGER.info("✅ Loaded %d/%d devices (skipped %d non-whitelisted)", 
                           loaded_count, len(saved_devices), skipped_count)
            else:
                _LOGGER.info("✅ Loaded %d devices successfully", loaded_count)
            
            # Fire event to signal that all devices are loaded and available
            self.hass.bus.async_fire(
                "eldat_devices_loaded",
                {
                    "device_count": loaded_count,
                    "devices": list(self.devices.keys())
                }
            )
            _LOGGER.info("📡 Fired device loaded event - %d devices available", loaded_count)
            
            # Force entity refresh after loading all devices
            if saved_devices:
                _LOGGER.info("Triggering entity refresh for %d restored devices", len(saved_devices))
                await self.async_refresh()
                
        except Exception as e:
            _LOGGER.error("Error loading device configuration: %s", e)

    async def _restore_rx11_device(self, serial_number: str, device_info: Dict[str, Any]) -> None:
        """Restore RX11-based device with persistent EW-Receiver serial handling."""
        try:
            device_type = device_info.get("type", device_info.get("device_type", "unknown"))
            
            # Restore EW-Receiver serial mapping if present
            ew_receiver_serial = device_info.get("ew_receiver_serial")
            ew_receiver_index = device_info.get("ew_receiver_index")
            
            if ew_receiver_serial and ew_receiver_index is not None:
                # Restore EW-Receiver mapping in device registry
                self.device_registry.store_ew_receiver_mapping(
                    ew_receiver_index, ew_receiver_serial, serial_number
                )
                
                # Mark receiver as used in transceiver if available
                if hasattr(self.transceiver, '_rx11_wrapper'):
                    self.transceiver._rx11_wrapper.mark_receiver_used(ew_receiver_index, ew_receiver_serial)
                
                # Add to whitelist (memory-only during setup)
                self._add_to_whitelist_memory(
                    serial_number=serial_number,
                    rx11_index=ew_receiver_index,
                    device_type=device_type,
                    source="GetFdSerial"
                )
                
                _LOGGER.info("🔄 Restored EW-Receiver mapping: Index %d, Serial %s → Device %s", 
                           ew_receiver_index, ew_receiver_serial[-8:], serial_number[-8:])
            
            # Ensure RX11-based devices have proper sensor configuration
            if device_type in ["ew_sensor", "ew_transceiver"] and not device_info.get("available_sensors"):
                device_info.update({
                    "available_sensors": ["temperature", "humidity"],
                    "measurement_types": ["temperature", "humidity"],
                    "sensor_types": ["temperature", "humidity"],
                    "supports_sensors": True,
                    "rx11_based": True,
                })
                _LOGGER.info("🎯 Enhanced RX11 sensor configuration for device %s", serial_number[-8:])
                
        except Exception as e:
            _LOGGER.error("Error restoring RX11 device %s: %s", serial_number, e)

    def _auto_correct_device_type(self, serial_number: str, device_info: Dict[str, Any]) -> Dict[str, Any]:
        """Auto-correct device type based on available indicators."""
        try:
            measurement_types = device_info.get("measurement_types", [])
            available_sensors = device_info.get("available_sensors", [])
            entities = device_info.get("entities", [])
            
            # Check for sensor indicators
            if (measurement_types or available_sensors or 
                any("temperature" in str(e) or "humidity" in str(e) for e in entities)):
                device_info["device_type"] = "ew_sensor"
                device_info["type"] = "ew_sensor"  # Also set legacy field
                _LOGGER.info("Auto-corrected device type for %s: unknown → ew_sensor", serial_number[-6:])
            
            # Check for receiver indicators
            elif entities and any(e.get("type") == "button" for e in entities):
                device_info["device_type"] = "ew_receiver"
                device_info["type"] = "ew_receiver"
                _LOGGER.info("Auto-corrected device type for %s: unknown → ew_receiver", serial_number[-6:])
                
            return device_info
            
        except Exception as e:
            _LOGGER.error("Error auto-correcting device type for %s: %s", serial_number, e)
            return device_info

    async def _regenerate_entity_specs(self, serial_number: str, device_info: Dict[str, Any]) -> Dict[str, Any]:
        """Regenerate entity specs with latest format and naming."""
        try:
            _LOGGER.debug("About to regenerate entity specs for %s (type=%s, receiver_kind=%s, operating_mode=%s)", 
                        serial_number[-6:], 
                        device_info.get("type"),
                        device_info.get("receiver_kind"),
                        device_info.get("operating_mode"))
                        
            entity_specs = create_entity_specs_for_device(serial_number, device_info)
            _LOGGER.debug("Entity specs result: %s platforms, entity_specs=%s", 
                         len(entity_specs) if entity_specs else 0, bool(entity_specs))
                         
            if entity_specs:
                # Merge all entity types into a single entities list
                all_entities = []
                for platform, entities in entity_specs.items():
                    all_entities.extend(entities)
                _LOGGER.debug("Merged entities: %d total", len(all_entities))
                
                if all_entities:
                    # REPLACE old entity specs completely with newly generated ones
                    device_info["entities"] = all_entities
                    _LOGGER.info("✅ Regenerated %d entity specs for device %s with updated naming", 
                               len(all_entities), serial_number[-6:])
                else:
                    _LOGGER.warning("⚠️  Entity specs generation returned empty list for device %s", serial_number[-6:])
            else:
                _LOGGER.warning("⚠️  Entity specs generation returned None for device %s", serial_number[-6:])
                
            return device_info
            
        except Exception as e:
            _LOGGER.error("Error regenerating entity specs for %s: %s", serial_number, e)
            return device_info
            
        except Exception as e:
            _LOGGER.error("Error loading device configuration: %s", e)

    async def send_command(self, serial_number: str, command, **kwargs) -> bool:
        """Send command to a device via transceiver."""
        if serial_number not in self.devices and serial_number not in self._registered_devices:
            _LOGGER.error("Device not found: %s", serial_number)
            return False
            
        try:
            # Convert bytes to appropriate format if needed for the transceiver
            if isinstance(command, bytes):
                # Use transceiver-specific command sending for bytes
                if hasattr(self.transceiver, 'send_command_to_device'):
                    result = await self.transceiver.send_command_to_device(serial_number, command)
                else:
                    _LOGGER.error("Transceiver does not support send_command_to_device for bytes")
                    return False
            else:
                # String command - use send_command_to_receiver
                if hasattr(self.transceiver, 'send_command_to_receiver'):
                    result = await self.transceiver.send_command_to_receiver(serial_number, command)
                else:
                    _LOGGER.error("Transceiver does not support send_command_to_receiver for strings")
                    return False
            
            if result:
                _LOGGER.debug("Command sent successfully to %s: %s", serial_number, command)
            else:
                _LOGGER.warning("Failed to send command to %s: %s", serial_number, command)
                
            return result
            
        except Exception as e:
            _LOGGER.error("Error sending command to %s: %s", serial_number, e)
            return False

    async def set_learning_mode(self, enabled: bool, timeout: int = 180) -> bool:
        """Set learning mode on the transceiver."""
        try:
            result = await self.transceiver.set_learning_mode(enabled, timeout)
            
            if result:
                _LOGGER.info("Learning mode %s", "enabled" if enabled else "disabled")
            else:
                _LOGGER.warning("Failed to %s learning mode", "enable" if enabled else "disable")
                
            return result
            
        except Exception as e:
            _LOGGER.error("Error setting learning mode: %s", e)
            return False

    async def is_learning_mode(self) -> bool:
        """Check if learning mode is active."""
        try:
            return await self.transceiver.is_learning_mode()
        except Exception as e:
            _LOGGER.error("Error checking learning mode: %s", e)
            return False

    def get_all_devices(self) -> Dict[str, Dict[str, Any]]:
        """Get all devices (both legacy and registered)."""
        all_devices = self.devices.copy()
        all_devices.update(self._registered_devices)
        return all_devices

    def get_device(self, serial_number: str) -> Optional[Dict[str, Any]]:
        """Get device info by serial number."""
        return self.devices.get(serial_number)

    def get_device_state(self, serial_number: str) -> Optional[Dict[str, Any]]:
        """Get persistent state for a device."""
        # First try registered devices
        if serial_number in self._registered_devices:
            device = self._registered_devices[serial_number]
            persistent_state = device.get("persistent_state", {})
            if persistent_state:
                return persistent_state
        
        # Fallback to legacy devices
        device = self.devices.get(serial_number)
        if device:
            return device.get("persistent_state", {})
        return None

    def set_device_state(self, serial_number: str, state_data: Dict[str, Any]) -> None:
        """Set persistent state for a device."""
        # Update both legacy devices and registered devices
        if serial_number in self.devices:
            if "persistent_state" not in self.devices[serial_number]:
                self.devices[serial_number]["persistent_state"] = {}
            self.devices[serial_number]["persistent_state"].update(state_data)
        
        # Also update registered devices
        if serial_number in self._registered_devices:
            if "persistent_state" not in self._registered_devices[serial_number]:
                self._registered_devices[serial_number]["persistent_state"] = {}
            self._registered_devices[serial_number]["persistent_state"].update(state_data)
            
            # Trigger save of registered devices to persist the state
            self.hass.async_create_task(self._save_registered_devices())
            
        _LOGGER.debug("Updated persistent state for device %s: %s", 
                     serial_number[-6:], state_data)

    async def async_remove_device_manually(self, serial_number: str, force: bool = False) -> bool:
        """Remove device manually with safety checks."""
        if not serial_number or len(serial_number) != 16:
            _LOGGER.error("Invalid serial number for device removal: %s", serial_number)
            return False
            
        device_info = self.devices.get(serial_number)
        if not device_info:
            _LOGGER.warning("Device %s not found for manual removal", serial_number)
            return False
            
        # Check if device was added manually (unless force is used)
        if not force and not device_info.get("added_manually", False):
            _LOGGER.warning("Device %s was auto-discovered. Use force=True to remove anyway.", serial_number)
            return False
            
        return await self.unregister_device(serial_number, force_remove=force, add_to_blacklist=True)

    # Enhanced alias with additional options
    async def async_remove_device(self, serial_number: str, force: bool = False, blacklist: bool = True) -> bool:
        """Remove device with full control options."""
        return await self.unregister_device(serial_number, force_remove=force, add_to_blacklist=blacklist)

    async def _remove_from_ha_registry(self, serial_number: str, device_info: Dict[str, Any]) -> bool:
        """Remove device from Home Assistant device and entity registries."""
        try:
            import homeassistant.helpers.device_registry as dr
            import homeassistant.helpers.entity_registry as er
            
            entity_registry = er.async_get(self.hass)
            device_registry = dr.async_get(self.hass)
            
            # Get device entry before removing
            device_entry = device_registry.async_get_device(
                identifiers={(DOMAIN, serial_number)}
            )
            
            # Find all entities for this device BEFORE removing the device
            entities_to_remove = []
            
            # Method 1: Find by device_id if device exists
            if device_entry:
                _LOGGER.debug("  Found device entry with ID: %s", device_entry.id)
                entities = er.async_entries_for_device(entity_registry, device_entry.id)
                for entity_entry in entities:
                    entities_to_remove.append(entity_entry.entity_id)
                    _LOGGER.debug("    Found entity by device_id: %s (unique_id: %s)", 
                                entity_entry.entity_id, entity_entry.unique_id)
                        
            # Method 2: Find by unique_id pattern (fallback and additional cleanup)
            # This catches entities that might have been orphaned
            for entity_entry in list(entity_registry.entities.values()):
                if entity_entry.platform == "eldat_plugin" and entity_entry.unique_id:
                    # Check if serial is in unique_id (format: {SERIAL}_{sensor_type})
                    if serial_number.upper() in entity_entry.unique_id.upper():
                        if entity_entry.entity_id not in entities_to_remove:
                            entities_to_remove.append(entity_entry.entity_id)
                            _LOGGER.debug("    Found entity by unique_id pattern: %s", entity_entry.entity_id)
            
            # Remove entities first
            _LOGGER.info("🗑️  Removing %d entities for device %s", len(entities_to_remove), serial_number[-8:])
            for entity_id in entities_to_remove:
                try:
                    # Get unique_id BEFORE removing the entity
                    unique_id = None
                    if entity_id in entity_registry.entities:
                        entity_entry = entity_registry.entities[entity_id]
                        if entity_entry:
                            unique_id = entity_entry.unique_id
                    
                    _LOGGER.debug("  Removing entity: %s (unique_id: %s)", entity_id, unique_id)
                    entity_registry.async_remove(entity_id)
                    
                    # Remove from our tracking
                    if unique_id:
                        self.created_entity_unique_ids.discard(unique_id)
                        _LOGGER.debug("    Removed unique_id from tracking: %s", unique_id)
                except Exception as e:
                    _LOGGER.warning("  Failed to remove entity %s: %s", entity_id, e)
            
            # Small delay to ensure registry updates are processed
            await asyncio.sleep(0.3)
                
            # Remove device after entities are removed
            if device_entry:
                _LOGGER.info("🗑️  Removing device from registry: %s", serial_number[-8:])
                device_registry.async_remove_device(device_entry.id)
                
            _LOGGER.info("✅ Removed %d entities and device %s from Home Assistant registries", 
                        len(entities_to_remove), serial_number[-8:])
            return True
            
        except Exception as e:
            _LOGGER.error("Error removing device from HA registry: %s", e)
            return False
    
    async def _cleanup_orphaned_entities(self, serial_number: str) -> bool:
        """Clean up orphaned entities when device no longer exists in coordinator."""
        try:
            _LOGGER.info("🧹 Cleaning up orphaned entities for device: %s", serial_number)
            
            # Remove from device registry if it exists
            device_registry = dr.async_get(self.hass)
            device_entry = device_registry.async_get_device(
                identifiers={(DOMAIN, serial_number)}
            )
            if device_entry:
                _LOGGER.debug("Removing orphaned device from device registry: %s", device_entry.id)
                device_registry.async_remove_device(device_entry.id)
            
            # Remove orphaned entities from entity registry
            entity_registry = er.async_get(self.hass)
            entities_to_remove = []
            
            for entity in entity_registry.entities.values():
                if (entity.platform == "eldat_plugin" and 
                    (entity.device_id == device_entry.id if device_entry else
                     serial_number in entity.unique_id)):
                    entities_to_remove.append(entity.entity_id)
            
            for entity_id in entities_to_remove:
                _LOGGER.debug("Removing orphaned entity: %s", entity_id)
                # Remove from global tracking
                entity = entity_registry.async_get(entity_id)
                if entity and entity.unique_id:
                    self.created_entity_unique_ids.discard(entity.unique_id)
                # Remove from registry
                entity_registry.async_remove(entity_id)
            
            _LOGGER.info("✅ Cleaned up %d orphaned entities for device %s", 
                        len(entities_to_remove), serial_number)
            return len(entities_to_remove) > 0  # Return True if we cleaned up anything
            
        except Exception as e:
            _LOGGER.error("Error cleaning up orphaned entities for %s: %s", serial_number, e)
            return False
    
    async def _add_to_device_blacklist(self, serial_number: str) -> None:
        """Add device to blacklist (DEPRECATED - use _remove_from_whitelist instead)."""
        try:
            if self.device_config_manager:
                # NEW: Remove from whitelist instead
                await self._remove_from_whitelist(serial_number)
                
                # Legacy blacklist support (kept for backwards compatibility)
                removed_devices = await self.device_config_manager.load_removed_devices_list()
                if serial_number not in removed_devices:
                    removed_devices.append(serial_number)
                    await self.device_config_manager.save_removed_devices_list(removed_devices)
                    _LOGGER.debug("🚫 Device %s added to legacy blacklist", serial_number)
                    
        except Exception as e:
            _LOGGER.error("Error adding device to blacklist: %s", e)
    
    async def _remove_from_whitelist(self, serial_number: str) -> None:
        """Remove device from whitelist (UI delete button)."""
        try:
            if self.device_config_manager:
                success = await self.device_config_manager.remove_device_from_whitelist(serial_number)
                if success and serial_number in self._device_whitelist:
                    self._device_whitelist.remove(serial_number)
                    _LOGGER.info("🗑️  Removed device %s from whitelist", serial_number[-8:])
        except Exception as e:
            _LOGGER.error("Error removing device from whitelist: %s", e)
    
    def _add_to_whitelist_memory(
        self, 
        serial_number: str, 
        rx11_index: Optional[int] = None,
        device_type: str = "unknown",
        source: str = "unknown"
    ) -> None:
        """Add device to whitelist (in-memory only, no I/O)."""
        if serial_number not in self._device_whitelist:
            self._device_whitelist.add(serial_number)
            self._whitelist_dirty = True
            _LOGGER.debug("📝 Added device %s to whitelist memory (source: %s)", serial_number[-8:], source)
    
    async def _add_to_whitelist(
        self, 
        serial_number: str, 
        rx11_index: Optional[int] = None,
        device_type: str = "unknown",
        source: str = "unknown"
    ) -> None:
        """Add device to whitelist for persistence (with I/O)."""
        try:
            if self.device_config_manager:
                device_info = self.devices.get(serial_number, {})
                device_name = device_info.get("name", f"Device {serial_number[-6:]}")
                
                # Add to memory first
                self._add_to_whitelist_memory(serial_number, rx11_index, device_type, source)
                
                # Only write to disk if setup is complete (avoid blocking during startup)
                if self._setup_complete:
                    success = await self.device_config_manager.add_device_to_whitelist(
                        serial_number=serial_number,
                        rx11_index=rx11_index,
                        device_type=device_type,
                        name=device_name,
                        source=source
                    )
                    
                    if success:
                        _LOGGER.debug("✅ Saved device %s to whitelist (source: %s)", serial_number[-8:], source)
                    
        except Exception as e:
            _LOGGER.error("Error adding device to whitelist: %s", e)
    
    async def _save_whitelist_async(self) -> None:
        """Save in-memory whitelist to disk (background task)."""
        try:
            if not self._whitelist_dirty:
                return
                
            await asyncio.sleep(1)  # Small delay to batch updates
            
            _LOGGER.info("💾 Saving whitelist with %d devices to disk", len(self._device_whitelist))
            
            whitelist_dict = {}
            for serial_number in self._device_whitelist:
                device_info = self.devices.get(serial_number, {})
                whitelist_dict[serial_number] = {
                    "serial_number": serial_number,
                    "rx11_index": device_info.get("rx11_index") or device_info.get("ew_receiver_index"),
                    "device_type": device_info.get("device_type", device_info.get("type", "unknown")),
                    "name": device_info.get("name", f"Device {serial_number[-6:]}"),
                    "added_date": datetime.now().isoformat(),
                    "last_seen": datetime.now().isoformat(),
                    "source": "setup"
                }
            
            await self.device_config_manager.save_device_whitelist(whitelist_dict)
            self._whitelist_dirty = False
            
            _LOGGER.info("✅ Whitelist saved successfully")
            
        except Exception as e:
            _LOGGER.error("❌ Error saving whitelist: %s", e)
    
    async def _populate_initial_whitelist(self) -> None:
        """Populate initial whitelist from currently loaded devices (background task)."""
        try:
            await asyncio.sleep(2)  # Wait for setup to complete
            
            _LOGGER.info("🔄 Populating initial whitelist from %d devices", len(self.devices))
            
            for serial_number, device_info in self.devices.items():
                await self._add_to_whitelist(
                    serial_number=serial_number,
                    rx11_index=device_info.get("rx11_index") or device_info.get("ew_receiver_index"),
                    device_type=device_info.get("device_type", device_info.get("type", "unknown")),
                    source="initial_setup"
                )
            
            # Enable whitelist mode for next restart
            self._whitelist_mode = True
            
            _LOGGER.info("✅ Initial whitelist created with %d devices - will be active on next restart", 
                        len(self._device_whitelist))
            
        except Exception as e:
            _LOGGER.error("❌ Error populating initial whitelist: %s", e)
    
    async def async_cleanup_all_orphaned_entities(self) -> Dict[str, Any]:
        """Clean up all orphaned entities from removed devices."""
        try:
            _LOGGER.info("🧹 Starting cleanup of all orphaned entities...")
            
            # Get all ELDAT entities from entity registry
            entity_registry = er.async_get(self.hass)
            device_registry = dr.async_get(self.hass)
            
            eldat_entities = []
            orphaned_entities = []
            
            for entity in entity_registry.entities.values():
                if entity.platform == "eldat_plugin":
                    eldat_entities.append(entity)
                    
                    # Check if the entity's device still exists in coordinator
                    serial_in_unique_id = None
                    
                    # Try to extract serial from unique_id pattern (device_serial_entitytype)
                    if "_" in entity.unique_id:
                        potential_serial = entity.unique_id.split("_")[0]
                        if len(potential_serial) == 32:  # Standard ELDAT serial length
                            serial_in_unique_id = potential_serial
                    
                    # Also check if the full unique_id contains any known removed serials
                    if not serial_in_unique_id:
                        # Try other patterns like serial_remove, etc.
                        parts = entity.unique_id.split("_")
                        for part in parts:
                            if len(part) == 32 and all(c in "0123456789ABCDEF" for c in part):
                                serial_in_unique_id = part
                                break
                    
                    if serial_in_unique_id and serial_in_unique_id not in self.devices:
                        orphaned_entities.append((entity, serial_in_unique_id))
            
            # Clean up orphaned entities
            cleaned_serials = set()
            for entity, serial_number in orphaned_entities:
                try:
                    _LOGGER.debug("Removing orphaned entity: %s (device: %s)", entity.entity_id, serial_number)
                    entity_registry.async_remove(entity.entity_id)
                    cleaned_serials.add(serial_number)
                    # Remove from global tracking
                    if entity.unique_id:
                        self.created_entity_unique_ids.discard(entity.unique_id)
                except Exception as e:
                    _LOGGER.warning("Failed to remove orphaned entity %s: %s", entity.entity_id, e)
            
            # Also clean up orphaned devices from device registry
            orphaned_devices = []
            for device in device_registry.devices.values():
                if any(identifier[0] == DOMAIN for identifier in device.identifiers):
                    # Extract serial from device identifiers
                    for domain, serial in device.identifiers:
                        if domain == DOMAIN and serial not in self.devices:
                            orphaned_devices.append((device, serial))
                            break
            
            for device_entry, serial_number in orphaned_devices:
                try:
                    _LOGGER.debug("Removing orphaned device: %s (serial: %s)", device_entry.id, serial_number)
                    device_registry.async_remove_device(device_entry.id)
                    cleaned_serials.add(serial_number)
                except Exception as e:
                    _LOGGER.warning("Failed to remove orphaned device %s: %s", device_entry.id, e)
            
            result = {
                "total_eldat_entities": len(eldat_entities),
                "orphaned_entities_found": len(orphaned_entities),
                "orphaned_devices_found": len(orphaned_devices),
                "cleaned_entities": len([e for e, _ in orphaned_entities]),
                "cleaned_devices": len([d for d, _ in orphaned_devices]),
                "affected_serials": list(cleaned_serials)
            }
            
            _LOGGER.info("✅ Orphaned entity cleanup completed: %d entities, %d devices cleaned for %d device serials", 
                        result["cleaned_entities"], result["cleaned_devices"], len(cleaned_serials))
            
            return result
            
        except Exception as e:
            _LOGGER.error("Error during orphaned entity cleanup: %s", e)
            return {"error": str(e)}

    async def list_removable_devices(self) -> Dict[str, Dict[str, Any]]:
        """List all devices that can be safely removed."""
        removable_devices = {}
        
        for serial_number, device_info in self.devices.items():
            # Include devices that were added manually or can be force-removed
            removable_info = {
                "name": device_info.get("name", serial_number),
                "type": device_info.get("type", "unknown"),
                "added_manually": device_info.get("added_manually", False),
                "can_force_remove": True,
                "last_seen": device_info.get("last_seen"),
                "entities_count": len(device_info.get("entities", []))
            }
            removable_devices[serial_number] = removable_info
            
        return removable_devices

    async def async_add_device_manually(
        self,
        serial_number: str,
        device_type: str,
        device_name: str = None,
        channels: int = 1,
        detected_via: str = "manual",
        info_type: int = None,
        timestamp: float = None,
    ) -> bool:
        """Simplified RX11-based device creation with persistent EW-Receiver serial storage."""
        try:
            # Check if device already exists
            if serial_number in self.devices:
                _LOGGER.warning("Device %s already exists", serial_number)
                return False

            # Simplified RX11-based device creation
            device_info = await self._create_rx11_ew_device_info(
                serial_number, device_type, device_name, channels, detected_via, info_type, timestamp
            )
            
            if not device_info:
                _LOGGER.error("Failed to create device info for %s", serial_number)
                return False

            # Check for ghost device prevention
            if not await self.async_prevent_ghost_device_creation(serial_number, device_info):
                _LOGGER.warning("Ghost device prevention blocked creation of %s", serial_number)
                return False

            # Register the device
            await self.register_device(serial_number, device_info)
            
            _LOGGER.info("✅ Simplified RX11 device creation completed: %s (%s)", 
                        device_info.get("name"), device_type)
            
            return True
            
        except Exception as e:
            _LOGGER.error("Error in simplified RX11 device creation: %s", e)
            return False

    async def _create_rx11_ew_device_info(
        self, 
        serial_number: str, 
        device_type: str, 
        device_name: str = None,
        channels: int = 1,
        detected_via: str = "manual",
        info_type: int = None,
        timestamp: float = None
    ) -> Dict[str, Any] | None:
        """Create simplified device info for RX11-based devices with persistent serial storage."""
        try:
            # Base device info
            device_info = {
                "serial_number": serial_number,
                "type": device_type,
                "name": device_name or f"ELDAT {device_type} {serial_number[-6:]}",
                "channels": channels,
                "detected_via": detected_via,
                "added_manually": True,
                "timestamp": timestamp or time.time(),
                "last_seen": datetime.now().isoformat(),
                "persistent_storage": True,  # Mark for enhanced persistence
            }
            
            # Special handling for EW-Receiver devices with persistent serial storage
            if device_type == "EW_Receiver" or device_type.lower() == "ew_receiver":
                ew_receiver_data = await self._allocate_ew_receiver_with_persistence(serial_number)
                if ew_receiver_data:
                    device_info.update(ew_receiver_data)
                    _LOGGER.info("📝 EW-Receiver serial %s permanently stored for device %s", 
                               ew_receiver_data.get("ew_receiver_serial", "unknown")[-8:], serial_number[-8:])
                else:
                    _LOGGER.error("Failed to allocate EW-Receiver for device %s", serial_number)
                    return None
            
            # For sensor devices, ensure they have sensor capabilities
            elif device_type in ["ew_sensor", "ew_transceiver"]:
                device_info.update({
                    "available_sensors": ["temperature", "humidity"],
                    "measurement_types": ["temperature", "humidity"],
                    "sensor_types": ["temperature", "humidity"],
                    "supports_sensors": True,
                })
            
            if info_type is not None:
                device_info["info_type"] = info_type
                
            return device_info
            
        except Exception as e:
            _LOGGER.error("Error creating RX11 device info: %s", e)
            return None

    async def _allocate_ew_receiver_with_persistence(self, device_serial: str) -> Dict[str, Any] | None:
        """Allocate EW-Receiver with persistent serial number storage."""
        try:
            # Get next available receiver with index and serial
            if hasattr(self.transceiver, 'get_next_available_receiver'):
                receiver_info = await self.transceiver.get_next_available_receiver()
                if receiver_info:
                    ew_receiver_index, ew_receiver_serial = receiver_info
                    
                    # Store mapping persistently in device registry
                    self.device_registry.store_ew_receiver_mapping(
                        ew_receiver_index, ew_receiver_serial, device_serial
                    )
                    
                    # Mark receiver as used to prevent double allocation
                    if hasattr(self.transceiver, '_rx11_wrapper'):
                        self.transceiver._rx11_wrapper.mark_receiver_used(ew_receiver_index, ew_receiver_serial)
                    
                    _LOGGER.info("🎯 Allocated and stored EW-Receiver: Index %d, Serial %s → Device %s", 
                               ew_receiver_index, ew_receiver_serial[-8:], device_serial[-8:])
                    
                    return {
                        "ew_receiver_index": ew_receiver_index,
                        "ew_receiver_serial": ew_receiver_serial,
                        "supports_continuous": True,
                        "rx11_based": True,
                    }
                else:
                    _LOGGER.error("No available EW-Receiver index found")
                    return None
            else:
                _LOGGER.error("Transceiver does not support EW-Receiver allocation")
                return None
                
        except Exception as e:
            _LOGGER.error("Error allocating EW-Receiver with persistence: %s", e)
            return None
            return False

    async def async_restore_device(self, serial_number: str, device_config: Dict[str, Any]) -> bool:
        """Restore a previously removed device from backup configuration."""
        try:
            # Add back to whitelist
            await self._add_to_whitelist(
                serial_number=serial_number,
                rx11_index=device_config.get("rx11_index"),
                device_type=device_config.get("device_type", "unknown"),
                source="restore"
            )
            
            # Legacy: Remove from blacklist if present
            if hasattr(self, '_device_blacklist') and serial_number in self._device_blacklist:
                self._device_blacklist.remove(serial_number)
                _LOGGER.debug("Removed %s from legacy blacklist for restoration", serial_number)
            
            # Restore device configuration
            self.devices[serial_number] = device_config.copy()
            
            # Fire device added event
            device_name = device_config.get("name", serial_number[-8:])
            self.hass.bus.async_fire(
                EVENT_DEVICE_ADDED,
                {
                    "serial_number": serial_number,
                    "device_info": device_config,
                    "device_type": device_config.get("type"),
                    "device_name": device_name,
                    "restored": True
                }
            )
            
            # Update data to trigger entity recreation
            await self.async_update_data()
            
            _LOGGER.info("✅ Device %s (%s) successfully restored", device_name, serial_number[-8:])
            return True
            
        except Exception as e:
            _LOGGER.error("Error restoring device %s: %s", serial_number, e)
            return False
    
    async def get_backup_devices(self) -> List[Dict[str, Any]]:
        """Get list of backup/removed devices that can be restored."""
        backup_devices = []
        
        try:
            # Check if we have a backup storage mechanism
            if hasattr(self, '_device_backup'):
                for serial_number, backup_info in self._device_backup.items():
                    backup_devices.append({
                        "serial_number": serial_number,
                        "name": backup_info.get("name", serial_number[-8:]),
                        "type": backup_info.get("type", "unknown"),
                        "device_config": backup_info,
                        "removed_at": backup_info.get("removed_at"),
                        "can_restore": True
                    })
                    
        except Exception as e:
            _LOGGER.error("Error getting backup devices: %s", e)
            
        return backup_devices
    
    async def async_recreate_device(self, serial_number: str) -> bool:
        """Recreate a device by removing and re-adding it."""
        try:
            # Get current device info
            device_info = self.devices.get(serial_number)
            if not device_info:
                _LOGGER.error("Cannot recreate device %s: not found", serial_number)
                return False
            
            device_name = device_info.get("name", serial_number[-8:])
            
            # Backup device configuration
            device_backup = device_info.copy()
            
            # Remove device (without blacklisting)
            success = await self.async_remove_device(serial_number, force=True, blacklist=False)
            if not success:
                _LOGGER.error("Failed to remove device %s for recreation", serial_number)
                return False
            
            # Wait a moment for cleanup
            await asyncio.sleep(0.5)
            
            # Restore device
            success = await self.async_restore_device(serial_number, device_backup)
            if success:
                _LOGGER.info("✅ Device %s (%s) successfully recreated", device_name, serial_number[-8:])
            else:
                _LOGGER.error("Failed to restore device %s after recreation", serial_number)
                
            return success
            
        except Exception as e:
            _LOGGER.error("Error recreating device %s: %s", serial_number, e)
            return False

    @callback
    def async_add_listener(self, update_callback, context=None) -> callable:
        """Add listener for data updates."""
        if context is not None:
            # New Home Assistant version with context support
            return super().async_add_listener(update_callback, context)
        else:
            # Older Home Assistant version without context
            return super().async_add_listener(update_callback)

    async def async_cleanup_ghost_devices(self) -> None:
        """Detect and remove ghost devices that are no longer needed."""
        try:
            _LOGGER.info("🧹 Starting ghost device cleanup...")
            
            # Get current devices from device registry
            current_devices = await self.device_registry.get_all_devices()
            
            # Get Home Assistant device and entity registries  
            ha_device_registry = dr.async_get(self.hass)
            ha_entity_registry = er.async_get(self.hass)
            
            ghost_devices = []
            current_time = time.time()
            
            for device_id, device_entry in current_devices.items():
                # Extract data from EepromEntry structure
                serial_number = device_entry.serial_number.receiver_transmitter
                device_data = {
                    "name": device_entry.name,
                    "device_type": DeviceType(device_entry.information.device_type).name,
                    "last_seen": device_entry.last_seen,
                    "created_at": device_entry.created_at,
                    "unique_id": device_entry.unique_id,
                    "area": device_entry.area
                }
                
                # Skip if device has been seen recently (within 24 hours)
                last_seen = device_entry.last_seen
                if last_seen and isinstance(last_seen, str):
                    try:
                        # Convert ISO format to timestamp
                        from datetime import datetime
                        last_seen_dt = datetime.fromisoformat(last_seen.replace('Z', '+00:00'))
                        last_seen_timestamp = last_seen_dt.timestamp()
                        hours_since_seen = (current_time - last_seen_timestamp) / 3600
                        if hours_since_seen < 24:
                            continue
                    except (ValueError, AttributeError):
                        # Invalid datetime format, treat as old device
                        hours_since_seen = 999
                else:
                    hours_since_seen = 999  # No last_seen data
                
                # Check if device type suggests it might be temporary
                device_type = DeviceType(device_entry.information.device_type)
                if device_type == DeviceType.EW_SENDER:
                    # EW-Transmitters are often used temporarily and become ghost devices
                    ghost_devices.append((serial_number, device_data, "Transmitter device not seen recently"))
                elif device_entry.created_at:
                    # Auto-discovered devices that haven't been seen in a while
                    if last_seen is None or hours_since_seen > 168:  # 1 week
                        ghost_devices.append((serial_number, device_data, "Auto-discovered device inactive for >1 week"))
            
            # Report found ghost devices
            if ghost_devices:
                _LOGGER.warning("👻 Found %d potential ghost devices:", len(ghost_devices))
                for serial, data, reason in ghost_devices:
                    device_name = data.get("name", "Unknown")
                    _LOGGER.warning("  - %s (%s): %s", device_name, serial[-8:], reason)
                
                # For now, just log them. Auto-removal could be added as a configuration option
                _LOGGER.info("💡 To manually remove these devices, use the remove button in Home Assistant")
                
            else:
                _LOGGER.info("✅ No ghost devices detected")
                
        except Exception as e:
            _LOGGER.error("Error during ghost device cleanup: %s", e)

    async def async_prevent_ghost_device_creation(self, serial_number: str, device_data: Dict[str, Any]) -> bool:
        """Prevent creation of devices that are likely to become ghost devices."""
        try:
            device_type = device_data.get("device_type", "").lower()
            
            # Check for EW-Transmitters without proper setup
            if "transmitter" in device_type:
                # Only allow transmitter creation in setup mode or if manually added
                if not self._setup_mode_active and not device_data.get("added_manually", False):
                    _LOGGER.warning("🚫 Preventing ghost device creation: EW-Transmitter %s detected outside setup mode", 
                                  serial_number[-8:])
                    _LOGGER.info("💡 Enable setup mode to register EW-Transmitter devices")
                    return False
                    
                # Additional validation for transmitters
                if not device_data.get("button_count") and not device_data.get("supports_buttons"):
                    _LOGGER.warning("🚫 Preventing ghost device: Transmitter %s has no usable buttons", 
                                  serial_number[-8:])
                    return False
            
            # Check for devices with no useful entities
            available_sensors = device_data.get("available_sensors", [])
            measurement_types = device_data.get("measurement_types", [])
            supports_buttons = device_data.get("supports_buttons", False)
            
            if not available_sensors and not measurement_types and not supports_buttons:
                _LOGGER.warning("🚫 Preventing ghost device: %s has no useful sensors or buttons", 
                              serial_number[-8:])
                return False
            
            return True
            
        except Exception as e:
            _LOGGER.error("Error in ghost device prevention for %s: %s", serial_number, e)
            return False

    # =============================================================================
    # EWB (EASYWAVE BIDI) MONITORING FOR STATE UPDATES
    # =============================================================================
    
    async def _start_ewb_monitoring(self) -> None:
        """Start EWB telegram monitoring for state updates."""
        if not hasattr(self, '_ewb_monitoring_task') or self._ewb_monitoring_task is None:
            self._ewb_monitoring_task = asyncio.create_task(self._ewb_monitoring_loop())
            _LOGGER.info("🚀 Started EWB monitoring task")

    async def _ewb_monitoring_loop(self) -> None:
        """Continuous loop to monitor EWB telegrams for state updates."""
        _LOGGER.info("🔄 Starting EWB monitoring loop")
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while not self._shutdown_event.is_set():
            try:
                if not hasattr(self.transceiver, 'rx11_ewb_receive_telegram'):
                    _LOGGER.debug("Transceiver does not support EWB receive")
                    await asyncio.sleep(5.0)
                    continue
                    
                # Receive EWB telegram
                telegram = await self.transceiver.rx11_ewb_receive_telegram()
                
                if telegram:
                    consecutive_errors = 0  # Reset error counter on success
                    await self._process_ewb_telegram(telegram)
                else:
                    # Short pause before next attempt
                    await asyncio.sleep(0.1)
                    
            except asyncio.CancelledError:
                _LOGGER.info("🛑 EWB monitoring loop cancelled")
                break
                
            except Exception as e:
                consecutive_errors += 1
                _LOGGER.error("❌ Error in EWB monitoring loop (%d/%d): %s", 
                             consecutive_errors, max_consecutive_errors, e)
                
                if consecutive_errors >= max_consecutive_errors:
                    _LOGGER.error("❌ Too many consecutive errors in EWB monitoring - stopping")
                    break
                
                # Exponential backoff on errors
                await asyncio.sleep(min(2.0 ** consecutive_errors, 30.0))
        
        _LOGGER.info("🏁 EWB monitoring loop ended")

    async def _process_ewb_telegram(self, telegram: dict) -> None:
        """Process received EWB telegram for state updates."""
        try:
            info_type = telegram.get('info_type')
            serial_number = telegram.get('serial_number')
            raw_data = telegram.get('raw_info_data', [])
            
            if info_type == 3:  # State update from EWneo receiver
                await self._process_ewneo_raw_state_update(serial_number, raw_data)
            else:
                # Für alle anderen info_types: Konvertiere zu normalem Telegramm-Format 
                # und leite an _handle_telegram weiter für normale Verarbeitung
                _LOGGER.debug("📡 Converting EWB telegram (info_type %d) to normal telegram format", info_type)
                
                # Extrahiere Rohdaten für normale Telegram-Verarbeitung
                if len(serial_number) >= 32:  # 16 bytes = 32 hex chars
                    device_id_bytes = bytes.fromhex(serial_number)
                    info_data_bytes = bytes(raw_data) if raw_data else b'\x00' * 8
                    
                    # Rufe den normalen Telegram-Callback auf
                    if hasattr(self.transceiver, '_telegram_callback') and self.transceiver._telegram_callback:
                        try:
                            self.transceiver._telegram_callback(info_type, device_id_bytes, info_data_bytes)
                        except Exception as cb_error:
                            _LOGGER.error("❌ Error calling telegram callback: %s", cb_error)
                    else:
                        _LOGGER.debug("📡 No telegram callback available for info_type %d", info_type)
                else:
                    _LOGGER.warning("⚠️ Invalid serial number length for EWB telegram: %s", serial_number)
                
        except Exception as e:
            _LOGGER.error("❌ Error processing EWB telegram: %s", e)

    async def _process_ewneo_raw_state_update(self, serial_number: str, raw_data: list) -> None:
        """Process EWneo receiver state update from raw EWB telegram data."""
        try:
            if len(raw_data) < 4:
                _LOGGER.warning("⚠️ Invalid state data length for %s: %d bytes", serial_number[-8:], len(raw_data))
                return
                
            # Extract 4-byte state
            state_bytes = raw_data[:4]
            state_value = int.from_bytes(state_bytes, byteorder='little')  # Assuming little endian
            
            _LOGGER.debug("📊 State update for %s: %s (0x%08X)", 
                         serial_number[-8:], [f'0x{b:02X}' for b in state_bytes], state_value)
            
            # Get device info to determine type
            device_info = self.device_registry.get_device(serial_number)
            if not device_info:
                _LOGGER.warning("⚠️ Device %s not found in registry for state update", serial_number[-8:])
                return
                
            device_type_code = device_info.get('device_type_code', 0)
            device_type_name = device_info.get('device_type_name', 'unknown')
            
            # Process state based on device type
            parsed_state = self._parse_ewneo_state(device_type_code, state_bytes, device_type_name, serial_number)
            
            if parsed_state:
                # Update device data with new state
                device_info['last_state_update'] = time.time()
                device_info['current_state'] = parsed_state
                device_info['raw_state'] = state_bytes
                
                # Fire state update event for entities
                self.hass.bus.async_fire(
                    f"{EVENT_DEVICE_STATE_UPDATE}",
                    {
                        "serial_number": serial_number,
                        "device_type_code": device_type_code,
                        "device_type_name": device_type_name,
                        "state": parsed_state,
                        "raw_state": state_bytes,
                        "timestamp": time.time()
                    }
                )
                
                _LOGGER.info("📊 State update processed for %s (%s): %s", 
                           serial_number[-8:], device_type_name, parsed_state)
            else:
                _LOGGER.warning("⚠️ Could not parse state for %s (type 0x%02X)", 
                               serial_number[-8:], device_type_code)
                
        except Exception as e:
            _LOGGER.error("❌ Error processing EWneo state update for %s: %s", serial_number[-8:], e)

    def _parse_ewneo_state(self, device_type_code: int, state_bytes: list, device_type_name: str, device_serial: str = None) -> Optional[dict]:
        """Parse EWneo receiver state based on device type."""
        try:
            state_value = int.from_bytes(state_bytes, byteorder='little')
            
            if device_type_code == 0x03:  # EWB_DT_SWITCH
                # Switch state: Bit 0 = on/off
                return {
                    'type': 'switch',
                    'state': bool(state_value & 0x01),
                    'on': bool(state_value & 0x01)
                }
                
            elif device_type_code == 0x04:  # EWB_DT_DIMMER
                # Dimmer state: Bit 0 = on/off, Byte 2 = brightness level (0-255)
                on_state = bool(state_value & 0x01)
                brightness = state_bytes[2] if len(state_bytes) > 2 else 0  # Direct byte access
                return {
                    'type': 'light',
                    'state': on_state,
                    'on': on_state,
                    'brightness': brightness,
                    'brightness_pct': round(brightness / 255 * 100, 1)
                }
                
            elif device_type_code == 0x05:  # EWB_DT_MOTOR
                # Motor state: Complex parsing according to EWB specification Mode 0
                if len(state_bytes) >= 4:
                    # Handle both 4-byte (EWB_CHANGE_STATE) and 5-byte (EWB_RCV) responses
                    if len(state_bytes) == 4:
                        # 4-byte response from EWB_CHANGE_STATE: direct state word
                        mode = 0  # Mode is implicit (Mode 0 for motor responses)
                        state_word = int.from_bytes(state_bytes, byteorder='big')
                    else:
                        # 5-byte response from EWB_RCV: 1 byte mode + 4 bytes state
                        mode = state_bytes[0]
                        state_word = int.from_bytes(state_bytes[1:5], byteorder='big')
                    
                    # Extract motor status code (bits 31-25)
                    motor_status_code = (state_word >> 25) & 0x7F
                    
                    # Bit 24: Reserved
                    
                    # Current position (bits 23-17, 0=open, 100=closed)
                    current_position_raw = (state_word >> 17) & 0x7F
                    
                    # Bit 16: Recent tilt to horizontal
                    recent_tilt = bool(state_word & (1 << 16))
                    
                    # Target position (bits 15-9, 0=open, 100=closed)
                    target_position_raw = (state_word >> 9) & 0x7F
                    
                    # Bit 8: Auto-tilt after positioning
                    auto_tilt = bool(state_word & (1 << 8))
                    
                    # Configuration flags
                    runtime_measured = bool(state_word & (1 << 7))  # Bit 7
                    tilt_measured = bool(state_word & (1 << 6))     # Bit 6
                    # Bits 5-3: Reserved
                    terrace_function = bool(state_word & (1 << 2))  # Bit 2
                    stored_position = state_word & 0x03             # Bits 1-0
                    
                    # Enhanced runtime measurement detection logging
                    bit7_value = (state_word >> 7) & 1
                    device_id = device_serial[-8:] if device_serial else "Unknown"
                    _LOGGER.debug("🔍 EWneo state parse: Device %s, State=0x%08X, Bit7=%d, Runtime=%s, Pos=%s", 
                                 device_id, state_word, bit7_value, runtime_measured, 
                                 current_position_raw if runtime_measured else "N/A")
                    
                    # Interpret motor status code
                    motor_status_map = {
                        126: "stopped",              # Motor has stopped
                        119: "stopped_terrace",      # Motor stopped, terrace function active
                        120: "opening_runtime",      # Moving open for runtime duration
                        121: "closing_runtime",      # Moving close for runtime duration
                        122: "opening_120s",         # Moving open for 120 seconds
                        123: "closing_120s",         # Moving close for 120 seconds
                        124: "opening_position",     # Moving open to specific position
                        125: "closing_position",     # Moving close to specific position
                        117: "calibrating_runtime",  # Runtime measurement in progress
                        118: "calibrating_tilt"      # Tilt measurement in progress
                    }
                    
                    motor_status = motor_status_map.get(motor_status_code, "unknown")
                    
                    # Convert positions to Home Assistant format (0=closed, 100=open)
                    # Only valid if runtime measurement is done AND position is not 126 (unknown)
                    current_position = None
                    target_position = None
                    position_available = False
                    
                    if runtime_measured:
                        # Runtime measurement done - positions are valid if not 126
                        if current_position_raw <= 100:
                            current_position = 100 - current_position_raw  # Convert to HA format
                            position_available = True
                            
                        if target_position_raw <= 100:
                            target_position = 100 - target_position_raw    # Convert to HA format
                            
                        # Special cases where position is 126 (unknown)
                        if motor_status_code in [117, 118]:  # During calibration
                            current_position = None
                            target_position = None
                            position_available = False
                        elif motor_status_code in [122, 123]:  # 120s movement without positioning
                            current_position = None
                            target_position = None
                            position_available = False
                    else:
                        # No runtime measurement - positions are always unknown
                        current_position = None
                        target_position = None
                        position_available = False
                    
                    # Determine movement state
                    is_opening = motor_status_code in [120, 122, 124]  # Opening movements
                    is_closing = motor_status_code in [121, 123, 125]  # Closing movements
                    is_stopped = motor_status_code in [126, 119]       # Stopped states
                    is_calibrating = motor_status_code in [117, 118]   # Calibration states
                    
                    return {
                        'type': 'cover',
                        'motor_status': motor_status,
                        'motor_status_code': motor_status_code,
                        'position': current_position,
                        'target_position': target_position,
                        'position_available': position_available,
                        'runtime_measured': runtime_measured,
                        'tilt_measured': tilt_measured,
                        'terrace_function': terrace_function,
                        'recent_tilt': recent_tilt,
                        'auto_tilt': auto_tilt,
                        'stored_position': stored_position,
                        'is_opening': is_opening,
                        'is_closing': is_closing,
                        'is_stopped': is_stopped,
                        'is_calibrating': is_calibrating,
                        'supports_position': runtime_measured,
                        'supports_tilt': tilt_measured
                    }
                else:
                    # Fallback for short state data (should not happen with proper EWB)
                    _LOGGER.warning("EWB Motor: Received incomplete state data (%d bytes, expected 4 or 5)", len(state_bytes))
                    position_state = state_value & 0x03
                    return {
                        'type': 'cover',
                        'motor_status': "opening" if position_state == 1 else "closing" if position_state == 2 else "stopped",
                        'position': None,
                        'position_available': False,
                        'runtime_measured': False,
                        'is_opening': position_state == 1,
                        'is_closing': position_state == 2,
                        'is_stopped': position_state == 0
                    }
                
            elif device_type_code in [0x06, 0x07]:  # EWB_DT_DUAL_SWITCH, EWB_DT_QUAD_SWITCH
                # Multi-switch: Each bit represents a channel
                channels = 2 if device_type_code == 0x06 else 4
                channel_states = {}
                for i in range(channels):
                    channel_states[f'channel_{i+1}'] = bool(state_value & (1 << i))
                return {
                    'type': 'multi_switch',
                    'channels': channels,
                    'channel_states': channel_states
                }
                
            elif device_type_code in [0x08, 0x09]:  # EWB_DT_DUAL_MOTOR, EWB_DT_QUAD_MOTOR
                # Multi-motor: 2 bits per channel for position state
                channels = 2 if device_type_code == 0x08 else 4
                channel_states = {}
                for i in range(channels):
                    channel_state = (state_value >> (i * 2)) & 0x03
                    channel_states[f'channel_{i+1}'] = {
                        'position_state': channel_state,
                        'is_opening': channel_state == 1,
                        'is_closing': channel_state == 2,
                        'is_stopped': channel_state == 0
                    }
                return {
                    'type': 'multi_cover',
                    'channels': channels,
                    'channel_states': channel_states
                }
                
            else:
                _LOGGER.warning("⚠️ Unknown EWneo device type for state parsing: 0x%02X", device_type_code)
                return {
                    'type': 'unknown',
                    'raw_state': state_value,
                    'device_type_code': device_type_code
                }
                
        except Exception as e:
            _LOGGER.error("❌ Error parsing EWneo state: %s", e)
            return None

    async def _query_ewneo_state(self, gateway_serial: str, device_serial: str) -> bool:
        """Query initial state of EWneo device using EwbQueryState.
        
        Returns True if command was sent successfully, False otherwise.
        State will be received via EWB monitoring and processed automatically.
        """
        return await self._query_ewneo_state_with_mode(gateway_serial, device_serial, mode=0)
    
    async def _query_ewneo_state_with_mode(self, gateway_serial: str, device_serial: str, mode: int = 0) -> bool:
        """Query state of EWneo device with specific mode using EwbQueryState.
        
        Args:
            gateway_serial: Gateway device serial number
            device_serial: Target device serial number  
            mode: Query mode (0=summary, 2=motor1 full, 10=motor2 full, etc.)
        
        Returns True if command was sent successfully, False otherwise.
        State will be received via EWB monitoring and processed automatically.
        """
        try:
            if not hasattr(self.transceiver, 'wrapper') or not self.transceiver.wrapper:
                _LOGGER.error("❌ No transceiver wrapper available for EwbQueryState")
                return False
            
            _LOGGER.debug("🔍 Querying EWneo device state: Gateway %s, Device %s, Mode %d", 
                         gateway_serial[-8:], device_serial[-8:], mode)
            
            # Call RX11 wrapper method (async)
            result = await self.transceiver.wrapper.rx11_ewb_query_state(gateway_serial, device_serial, mode=mode)
            
            if result:
                _LOGGER.debug("✅ EwbQueryState command sent successfully (mode %d)", mode)
                return True
            else:
                _LOGGER.warning("⚠️ EwbQueryState command failed (mode %d)", mode)
                return False
                
        except Exception as e:
            _LOGGER.error("❌ Error sending EwbQueryState command (mode %d): %s", mode, e)
            return False