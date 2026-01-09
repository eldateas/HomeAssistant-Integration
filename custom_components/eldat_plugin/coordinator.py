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
from .device_manager import DeviceManager, ManagedDevice, DeviceAvailability, DeviceType
from .device_migration import migrate_to_device_manager
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
        # Use new DeviceManager from DeviceConfigManager
        self.device_manager = self.device_config_manager.device_manager
        
        # Central registered device management - only these devices will be managed
        self._registered_devices: Dict[str, Dict[str, Any]] = {}
        self._registered_devices_file = f"{hass.config.config_dir}/eldat_plugin/registered_devices.json"
        
        # EWB Index Tracking - Persistent storage of used EWB indices
        self._used_ewb_indices: Dict[int, Dict[str, Any]] = {}  # {index: {gateway_serial, device_serial, device_name, created_at}}
        self._ewb_indices_file = f"{hass.config.config_dir}/eldat_plugin/used_ewb_indices.json"
        self._next_free_ewb_index: Optional[int] = 0  # Cache for next known free index
        
        # EW-Receiver Index Tracking - Persistent storage of used EW receiver indices
        self._used_ew_receiver_indices: Dict[int, Dict[str, Any]] = {}  # {index: {receiver_serial, device_serial, device_name, created_at}}
        self._ew_receiver_indices_file = f"{hass.config.config_dir}/eldat_plugin/used_ew_receiver_indices.json"
        self._next_free_ew_receiver_index: Optional[int] = 0  # Cache for next known free index
        
        # Device registration will happen during async_setup
        
        # Global entity tracking to prevent duplicates across all platforms
        self.created_entity_unique_ids: Set[str] = set()
        
        # Device backup system for restoration
        self._device_backup: Dict[str, Dict[str, Any]] = {}
        self._device_whitelist: Set[str] = set()  # Only whitelisted devices are loaded
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
        """Load used EWB indices from registered_devices.json (with migration from old file)."""
        import json
        import aiofiles
        import os
        try:
            # Try loading from registered_devices.json first
            if os.path.exists(self._registered_devices_file):
                async with aiofiles.open(self._registered_devices_file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                    
                    # Load from integrated structure
                    if 'used_ewb_indices' in data:
                        # Convert string keys back to integers
                        self._used_ewb_indices = {int(k): v for k, v in data.get('used_ewb_indices', {}).items()}
                        self._next_free_ewb_index = self._find_next_free_ewb_index() if self._used_ewb_indices else 0
                        _LOGGER.info("📋 Loaded %d used EWB indices from registered_devices.json (next free: %d)", 
                                   len(self._used_ewb_indices), self._next_free_ewb_index)
                        return
            
            # Migration: Load from old separate file if it exists
            if os.path.exists(self._ewb_indices_file):
                async with aiofiles.open(self._ewb_indices_file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    old_data = json.loads(content)
                    self._used_ewb_indices = {int(k): v for k, v in old_data.get('used_indices', {}).items()}
                    self._next_free_ewb_index = self._find_next_free_ewb_index() if self._used_ewb_indices else 0
                    _LOGGER.info("🔄 Migrated %d EWB indices from old file (will be saved to registered_devices.json)", 
                               len(self._used_ewb_indices))
                    return
            
            # No data found
            _LOGGER.info("📋 No EWB indices found, starting with empty tracking")
            self._used_ewb_indices = {}
            self._next_free_ewb_index = 0
        except Exception as e:
            _LOGGER.error("❌ Failed to load used EWB indices: %s", e)
            self._used_ewb_indices = {}
            self._next_free_ewb_index = 0
    
    async def _save_used_ewb_indices(self) -> None:
        """Save used EWB indices to registered_devices.json."""
        # EWB indices are now saved as part of registered_devices.json
        # This method triggers a save of the complete file
        await self._save_registered_devices()
        _LOGGER.debug("💾 Saved %d used EWB indices to registered_devices.json", len(self._used_ewb_indices))
    
    async def _load_used_ew_receiver_indices(self) -> None:
        """Load used EW-Receiver indices from registered_devices.json (with migration)."""
        import json
        import aiofiles
        import os
        try:
            # Try loading from registered_devices.json first
            if os.path.exists(self._registered_devices_file):
                async with aiofiles.open(self._registered_devices_file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                    
                    # Load from integrated structure
                    if 'used_ew_receiver_indices' in data:
                        # Convert string keys back to integers
                        self._used_ew_receiver_indices = {int(k): v for k, v in data.get('used_ew_receiver_indices', {}).items()}
                        self._next_free_ew_receiver_index = self._find_next_free_ew_receiver_index() if self._used_ew_receiver_indices else 0
                        _LOGGER.info("📋 Loaded %d used EW-Receiver indices from registered_devices.json (next free: %d)", 
                                   len(self._used_ew_receiver_indices), self._next_free_ew_receiver_index)
                        return
            
            # Migration: Load from old separate file if it exists
            if os.path.exists(self._ew_receiver_indices_file):
                async with aiofiles.open(self._ew_receiver_indices_file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    old_data = json.loads(content)
                    self._used_ew_receiver_indices = {int(k): v for k, v in old_data.get('used_indices', {}).items()}
                    self._next_free_ew_receiver_index = self._find_next_free_ew_receiver_index() if self._used_ew_receiver_indices else 0
                    _LOGGER.info("🔄 Migrated %d EW-Receiver indices from old file (will be saved to registered_devices.json)", 
                               len(self._used_ew_receiver_indices))
                    return
            
            # No data found
            _LOGGER.info("📋 No EW-Receiver indices found, starting with empty tracking")
            self._used_ew_receiver_indices = {}
            self._next_free_ew_receiver_index = 0
        except Exception as e:
            _LOGGER.error("❌ Failed to load used EW-Receiver indices: %s", e)
            self._used_ew_receiver_indices = {}
            self._next_free_ew_receiver_index = 0
    
    async def _save_used_ew_receiver_indices(self) -> None:
        """Save used EW-Receiver indices to registered_devices.json."""
        # EW-Receiver indices are now saved as part of registered_devices.json
        # This method triggers a save of the complete file
        await self._save_registered_devices()
        _LOGGER.debug("💾 Saved %d used EW-Receiver indices to registered_devices.json", len(self._used_ew_receiver_indices))

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
                    loaded_devices = data.get('devices', {})
                    
                    # WICHTIG: Bereinige ALLE geladenen Geräte von alten Metadaten
                    for device_info in loaded_devices.values():
                        self._clean_device_info(device_info)
                    
                    self._registered_devices = loaded_devices
                    
                    # Rebuild used_ewb_indices from device data to fix inconsistencies
                    # This ensures all EWneo devices are properly tracked
                    indices_from_devices = {}
                    indices_changed = False
                    for serial, device_info in loaded_devices.items():
                        if device_info.get('neo_device') and 'ewneo_index' in device_info:
                            index = device_info['ewneo_index']
                            gateway_serial = device_info.get('gateway_serial')
                            device_name = device_info.get('name', f"EWneo ({serial[-6:]})")
                            
                            # Check for duplicate indices
                            if index in indices_from_devices:
                                _LOGGER.error("❌ DUPLICATE INDEX DETECTED: Index %d used by both %s and %s!", 
                                            index, indices_from_devices[index]['device_serial'][-8:], serial[-8:])
                                # Assign new index to this device
                                new_index = self._find_first_unused_index(set(indices_from_devices.keys()))
                                _LOGGER.info("✅ Reassigning device %s from index %d to %d", serial[-8:], index, new_index)
                                device_info['ewneo_index'] = new_index
                                index = new_index
                                indices_changed = True
                            
                            indices_from_devices[index] = {
                                'gateway_serial': gateway_serial,
                                'device_serial': serial,
                                'device_name': device_name,
                                'created_at': device_info.get('registered_at', datetime.now().isoformat())
                            }
                    
                    # Merge with loaded indices (prefer device data as source of truth)
                    # But also preserve any temporary reservations from the loaded data
                    loaded_indices = {int(k): v for k, v in data.get('used_ewb_indices', {}).items()}
                    
                    # Start with device data (source of truth for actual devices)
                    if indices_from_devices:
                        _LOGGER.info("🔄 Rebuilt %d EWB indices from device data", len(indices_from_devices))
                        self._used_ewb_indices = indices_from_devices
                        
                        # Add back any reservations that don't conflict
                        for idx, info in loaded_indices.items():
                            if info.get('reserved') and idx not in self._used_ewb_indices:
                                self._used_ewb_indices[idx] = info
                                _LOGGER.debug("Preserved reservation for index %d", idx)
                        
                        self._next_free_ewb_index = self._find_next_free_ewb_index()
                        # Save changes if we had to reassign indices
                        if indices_changed:
                            await self._save_registered_devices()
                            _LOGGER.info("💾 Saved corrected indices to storage")
                    else:
                        # No devices found, use loaded indices (including reservations)
                        self._used_ewb_indices = loaded_indices
                        self._next_free_ewb_index = self._find_next_free_ewb_index()
                    
                    _LOGGER.info("📋 Loaded %d registered devices from storage", len(self._registered_devices))
            else:
                _LOGGER.info("📋 No registered devices file found, starting with empty list")
        except Exception as e:
            _LOGGER.error("❌ Failed to load registered devices: %s", e)
            self._registered_devices = {}
    
    def _clean_device_info(self, device_info: Dict[str, Any]) -> None:
        """Remove problematic metadata from device_info that should not be persisted.
        
        This prevents issues with old config_entry_ids and other stale data.
        """
        # Remove old config_entry_id - wird automatisch beim Device-Registry-Eintrag gesetzt
        if 'config_entry_id' in device_info:
            old_id = device_info.pop('config_entry_id')
            _LOGGER.debug("Cleaned old config_entry_id %s from device info", old_id[-8:] if old_id else "None")
        
        # Entferne auch andere problematische Felder, die nicht persistiert werden sollten
        problematic_fields = ['via_device', 'config_subentry_id']
        for field in problematic_fields:
            if field in device_info:
                device_info.pop(field)
                _LOGGER.debug("Cleaned field %s from device info", field)
    
    def _find_first_unused_index(self, used_indices: set) -> int:
        """Find the first unused index starting from 0."""
        for i in range(256):
            if i not in used_indices:
                return i
        raise ValueError("No free indices available (all 0-255 used)")
    
    async def _cleanup_old_index_files(self) -> None:
        """Remove old separate index files after successful migration to consolidated structure."""
        import os
        try:
            files_to_remove = [self._ewb_indices_file, self._ew_receiver_indices_file]
            removed_count = 0
            
            for file_path in files_to_remove:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        removed_count += 1
                        _LOGGER.info("🧹 Removed old index file: %s", os.path.basename(file_path))
                    except Exception as e:
                        _LOGGER.warning("⚠️ Could not remove old index file %s: %s", file_path, e)
            
            if removed_count > 0:
                _LOGGER.info("✅ Cleanup complete: Removed %d old index files (now using consolidated registered_devices.json)", removed_count)
        except Exception as e:
            _LOGGER.error("❌ Error during index file cleanup: %s", e)
    
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
                'version': '2.0',  # Version erhöht wegen neuer Struktur mit integrierten Indices
                'last_updated': datetime.now().isoformat(),
                'config_entry_id': self.config_entry.entry_id,
                'device_count': len(serializable_devices),
                'devices': serializable_devices,
                # Integrierte Index-Tracking-Daten (ersetzt separate Dateien)
                'used_ewb_indices': {str(k): v for k, v in self._used_ewb_indices.items()},
                'next_free_ewb_index': self._next_free_ewb_index,
                'used_ew_receiver_indices': {str(k): v for k, v in self._used_ew_receiver_indices.items()},
                'next_free_ew_receiver_index': self._next_free_ew_receiver_index
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
    
    def get_device_instance(self, serial_number: str):
        """Get the device instance for a serial number.
        
        Returns the actual device class instance (e.g., EWneoSensor) if available.
        """
        try:
            if self.transceiver and hasattr(self.transceiver, 'get_device'):
                device = self.transceiver.get_device(serial_number)
                if device:
                    _LOGGER.debug("✅ Found device instance for %s: %s", 
                                 serial_number[-6:], type(device).__name__)
                else:
                    # Reduce noise - only log at debug level, device will be created on-demand later
                    _LOGGER.debug("⚠️ No device instance found for %s in transceiver._device_instances", 
                                  serial_number[-6:])
                return device
            else:
                _LOGGER.debug("⚠️ Transceiver does not have get_device method")
        except Exception as e:
            _LOGGER.debug("Could not get device instance for %s: %s", serial_number[-6:], e)
        return None
    
    async def register_device_permanently(self, serial_number: str, device_info: Dict[str, Any]) -> bool:
        """Register a device for permanent management."""
        try:
            # Add metadata
            device_info['registered_at'] = datetime.now().isoformat()
            # NICHT speichern: config_entry_id wird beim Entity-Setup automatisch gesetzt
            # device_info['config_entry_id'] = self.config_entry.entry_id
            
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
        
        # Check if this was a reservation that we're now confirming
        was_reserved = self._used_ewb_indices.get(index, {}).get('reserved', False)
        
        self._used_ewb_indices[index] = {
            'gateway_serial': gateway_serial,
            'device_serial': device_serial, 
            'device_name': device_name or f"EWneo-Device ({device_serial[-6:]})",
            'created_at': datetime.now().isoformat()
            # Remove 'reserved' flag
        }
        
        # Update next free index cache
        if index == self._next_free_ewb_index:
            self._next_free_ewb_index = self._find_next_free_ewb_index()
        
        if was_reserved:
            _LOGGER.info("✅ Confirmed reservation for EWB index %d (device: %s, gateway: %s)", 
                        index, device_serial[-8:], gateway_serial[-8:])
        else:
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
    
    async def get_next_free_ewb_index(self) -> int:
        """Get the next free EWB index from persistent tracking (transaction-safe).
        
        Immediately reserves the index to prevent race conditions when multiple
        devices are added simultaneously.
        """
        # Find next free index based on our persistent tracking
        index = self._find_next_free_ewb_index()
        
        # IMMEDIATELY reserve it with a placeholder to prevent race conditions
        # This will be updated with real device info when mark_ewb_index_used() is called
        self._used_ewb_indices[index] = {
            'gateway_serial': None,
            'device_serial': 'RESERVED',
            'device_name': f'Reserved (Index {index})',
            'created_at': datetime.now().isoformat(),
            'reserved': True  # Mark as temporary reservation
        }
        
        # Update next free index cache
        self._next_free_ewb_index = self._find_next_free_ewb_index()
        
        _LOGGER.info("🔒 Reserved EWB index %d (next free: %d)", index, self._next_free_ewb_index)
        
        # Save immediately to prevent concurrent allocations
        await self._save_used_ewb_indices()
        
        # Schedule cleanup of this reservation after 5 minutes if not confirmed
        asyncio.create_task(self._cleanup_stale_reservation(index))
        
        return index
    
    async def _cleanup_stale_reservation(self, index: int) -> None:
        """Clean up a reservation if it hasn't been confirmed after 5 minutes."""
        await asyncio.sleep(300)  # Wait 5 minutes
        
        # Check if still a reservation (not confirmed with real device data)
        if index in self._used_ewb_indices and self._used_ewb_indices[index].get('reserved'):
            _LOGGER.warning("⚠️ Cleaning up stale reservation for index %d", index)
            self.mark_ewb_index_free(index)
    
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
    
    # ============================================================
    # EW-Receiver Index Management (unidirectional receivers)
    # ============================================================
    
    def mark_ew_receiver_index_used(self, index: int, receiver_serial: str, device_serial: str, device_name: str = None) -> None:
        """Mark an EW-Receiver index as used by a specific device."""
        from datetime import datetime
        
        self._used_ew_receiver_indices[index] = {
            'receiver_serial': receiver_serial,  # Gateway serial from RX11
            'device_serial': device_serial,      # Device's own serial
            'device_name': device_name or f"EW-Receiver ({device_serial[-6:]})",
            'created_at': datetime.now().isoformat()
        }
        
        # Update next free index cache
        if index == self._next_free_ew_receiver_index:
            self._next_free_ew_receiver_index = self._find_next_free_ew_receiver_index()
            
        _LOGGER.info("📍 Marked EW-Receiver index %d as used (device: %s, receiver: %s)", 
                    index, device_serial[-8:], receiver_serial[-8:])
        
        # Save to persistent storage
        asyncio.create_task(self._save_used_ew_receiver_indices())
        
        # Update transceiver wrapper tracking if available
        if hasattr(self.transceiver, '_rx11_wrapper') and self.transceiver._rx11_wrapper:
            wrapper = self.transceiver._rx11_wrapper
            wrapper.mark_receiver_used(index, receiver_serial)
    
    def mark_ew_receiver_index_free(self, index: int) -> None:
        """Mark an EW-Receiver index as free/available for new devices."""
        if index in self._used_ew_receiver_indices:
            device_info = self._used_ew_receiver_indices.pop(index)
            _LOGGER.info("♻️ Marked EW-Receiver index %d as free (was: %s)", index, device_info.get('device_name', 'Unknown'))
            
            # Update next free index cache if this becomes the new earliest free
            if index < self._next_free_ew_receiver_index:
                self._next_free_ew_receiver_index = index
                
            # Save to persistent storage
            asyncio.create_task(self._save_used_ew_receiver_indices())
            
            # Update transceiver wrapper tracking if available
            if hasattr(self.transceiver, '_rx11_wrapper') and self.transceiver._rx11_wrapper:
                wrapper = self.transceiver._rx11_wrapper
                wrapper.mark_receiver_available(index)
    
    async def get_next_free_ew_receiver_index(self) -> int:
        """Get the next free EW-Receiver index from persistent tracking (transaction-safe).
        
        Uses DeviceManager.allocate_rx11_index() for thread-safe allocation
        with lock protection to prevent race conditions.
        """
        # Use DeviceManager transaction-safe allocation
        try:
            index = self.device_manager.allocate_rx11_index()
            if index is not None:
                self._next_free_ew_receiver_index = index
                return index
            else:
                # Fallback if all indices are used
                _LOGGER.warning("⚠️ DeviceManager allocation failed (all indices used), using fallback")
                return self._find_next_free_ew_receiver_index()
        except Exception as e:
            # Fallback to old method if DeviceManager fails
            _LOGGER.warning("⚠️ DeviceManager allocation failed: %s, using fallback", e)
            return self._find_next_free_ew_receiver_index()
    
    def _find_next_free_ew_receiver_index(self) -> int:
        """Find the next EW-Receiver index available for assignment."""
        used_indices = set(self._used_ew_receiver_indices.keys())
        for index in range(255):  # EW supports indices 0-254
            if index not in used_indices:
                return index
        raise ValueError("No free EW-Receiver indices available (all 0-254 in use)")
    
    def is_ew_receiver_index_used(self, index: int) -> bool:
        """Check if an EW-Receiver index is already used."""
        return index in self._used_ew_receiver_indices
        
    def get_used_ew_receiver_indices(self) -> Dict[int, Dict[str, Any]]:
        """Get all used EW-Receiver indices with their device information."""
        return self._used_ew_receiver_indices.copy()
    
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
    
    async def _sync_ew_receiver_indices_with_wrapper(self) -> None:
        """Synchronize EW-Receiver index tracking between coordinator and transceiver wrapper."""
        if not hasattr(self.transceiver, '_rx11_wrapper') or not self.transceiver._rx11_wrapper:
            return
            
        wrapper = self.transceiver._rx11_wrapper
        
        # Clear wrapper tracking and rebuild from coordinator's persistent data
        wrapper._used_receivers.clear()
        
        for index, device_info in self._used_ew_receiver_indices.items():
            receiver_serial = device_info.get('receiver_serial')
            
            if receiver_serial:
                # Re-read actual serial from RX11 to ensure we have the correct one
                _LOGGER.warning("🔍 Syncing index %s: stored serial=%s", index, receiver_serial)
                actual_serial = await wrapper.rx11_ew_receiver_get_serial_by_index(int(index))
                
                if actual_serial and actual_serial != receiver_serial:
                    _LOGGER.warning("⚠️ Serial mismatch for index %s! Stored: %s, Actual: %s", 
                                  index, receiver_serial[-8:], actual_serial[-8:])
                    # Use the actual serial from RX11
                    receiver_serial = actual_serial
                    # Update stored value
                    device_info['receiver_serial'] = actual_serial
                    self._used_ew_receiver_indices[int(index)] = device_info
                    await self._save_used_ew_receiver_indices()
                elif actual_serial:
                    _LOGGER.warning("✅ Serial match for index %s: %s", index, actual_serial[-8:])
                
                wrapper.mark_receiver_used(int(index), receiver_serial)
                
        _LOGGER.info("🔄 Synchronized %d EW-Receiver indices from coordinator to wrapper", len(self._used_ew_receiver_indices))

    async def unregister_device_permanently(self, serial_number: str) -> bool:
        """Permanently remove device from all systems including DeviceManager."""
        try:
            # Get device info before removal for cleanup
            device_info = self._registered_devices.get(serial_number) or self.devices.get(serial_number)
            if not device_info:
                _LOGGER.warning("⚠️ Device %s not found in registered list", serial_number[-8:])
                # Try to clean up orphaned entities anyway
                await self._remove_from_ha_registry(serial_number, {})
                
                # Still try to remove from DeviceManager
                if self.device_manager.is_whitelisted(serial_number):
                    self.device_manager.remove_device(serial_number)
                    await self.device_manager.save()
                    _LOGGER.info("✅ Removed orphaned device from DeviceManager")
                
                return False
            
            device_name = device_info.get('name', serial_number[-8:])
            _LOGGER.info("🗑️ Starting permanent removal of device: %s (%s)", device_name, serial_number[-8:])
            
            # Step 1: Remove from DeviceManager (whitelist - single source of truth)
            _LOGGER.info("  Step 1/7: Removing from DeviceManager (whitelist)...")
            if self.device_manager.is_whitelisted(serial_number):
                self.device_manager.remove_device(serial_number)
                await self.device_manager.save()
                _LOGGER.info("  ✅ Removed from DeviceManager whitelist")
            else:
                _LOGGER.info("  ℹ️ Device not in DeviceManager whitelist")
            
            # Step 2: Remove from Home Assistant registries (entities and device)
            _LOGGER.info("  Step 2/7: Removing from HA registries...")
            await self._remove_from_ha_registry(serial_number, device_info)
            
            # Step 3: Remove from registered devices
            _LOGGER.info("  Step 3/7: Removing from registered devices...")
            if serial_number in self._registered_devices:
                del self._registered_devices[serial_number]
            
            # Step 4: Remove from legacy dicts
            _LOGGER.info("  Step 4/7: Removing from legacy dicts...")
            if serial_number in self.devices:
                del self.devices[serial_number]
            self._known_devices.discard(serial_number)
            
            # Step 5: Remove from whitelist (legacy compatibility)
            _LOGGER.info("  Step 5/7: Removing from legacy whitelist...")
            await self._remove_from_whitelist(serial_number)
            
            # Remove from fired events tracking (allow re-registration if needed)
            self._devices_with_fired_events.discard(serial_number)
            
            # Step 6: Perform device-specific cleanup (RX11, etc.)
            _LOGGER.info("  Step 6/7: Device-specific cleanup...")
            device_type = device_info.get("type", "unknown")
            _LOGGER.info("  Device type for cleanup: %s", device_type)
            if device_type == "ew_receiver":
                await self._cleanup_rx11_device(serial_number, device_info)
            elif device_type == "ewneo_receiver":
                await self._cleanup_ewneo_device(serial_number, device_info)
            else:
                _LOGGER.info("  No specific cleanup needed for device type: %s", device_type)
            
            # Step 7: Save changes to all storage systems
            _LOGGER.info("  Step 7/7: Saving configuration...")
            await self._save_registered_devices()
            await self._save_device_configuration()
            
            _LOGGER.info("✅ Device %s permanently unregistered from all systems (7/7 steps complete)", device_name)
            return True
            
        except Exception as e:
            _LOGGER.error("❌ Failed to unregister device %s: %s", serial_number[-8:], e, exc_info=True)
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
        removed_devices = []  # Track devices not in whitelist
        
        for serial_number, device_info in self._registered_devices.items():
            try:
                # CRITICAL: Only restore devices that are in DeviceManager whitelist
                if not self.device_manager.is_whitelisted(serial_number):
                    removed_devices.append(serial_number)
                    _LOGGER.warning("⚠️ Skipping device %s - not in DeviceManager whitelist (was removed)", 
                                  serial_number[-8:])
                    continue
                
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
        
        # Clean up removed devices from _registered_devices
        if removed_devices:
            for serial in removed_devices:
                del self._registered_devices[serial]
            await self._save_registered_devices()
            _LOGGER.info("🧹 Cleaned up %d devices not in whitelist from registered_devices", len(removed_devices))
        
        _LOGGER.info("✅ Restored %d/%d registered devices - ignoring all others", 
                   restored_count, len(self._registered_devices))
        
        # Events will be fired by fire_pending_device_events() after platform setup
        # No need to store pending events - fire_pending_device_events uses self.devices directly
        # and tracks already-fired events to prevent duplicates

    async def fire_pending_device_events(self) -> None:
        """Fire device events after platforms are set up (only once per device).
        
        NOTE: This should NOT fire events for devices loaded at startup, as those
        are already handled by the platform setup code (e.g., binary_sensor.py lines 94-110).
        This should only fire events for devices added AFTER startup (marked with registered_permanently flag).
        """
        # DO NOT fire events for restored devices - they're already created by platform setup
        # Only fire for devices that have the registered_permanently flag (newly added after startup)
        devices_to_fire = [
            (serial, info) for serial, info in self.devices.items()
            if serial not in self._devices_with_fired_events
            and info.get("registered_permanently", False)  # Only newly registered devices
        ]
        
        if not devices_to_fire:
            _LOGGER.debug("No pending devices to fire events for")
            return
            
        _LOGGER.info("🔥 Firing events for %d newly registered devices after platform setup", len(devices_to_fire))
        
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
            
            # Also fire platform-specific events if device has configured entities
            entities = device_info.get("entities", [])
            platforms = device_info.get("platforms", [])
            
            # Fire platform-specific events for each platform with configured entities
            for platform in platforms:
                platform_entities = [e for e in entities if e.get("type") == platform]
                if platform_entities:
                    event_name = f"eldat_plugin_{platform}_device_added"
                    _LOGGER.info("🔥 [fire_pending] Firing %s for %s (%d entities)", 
                               event_name, serial_number[-8:], len(platform_entities))
                    self.hass.bus.async_fire(
                        event_name,
                        {
                            "serial_number": serial_number,
                            "device_info": device_info,
                            "entities": platform_entities,
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
            
            # Initialize EW-Receiver index tracking
            await self._load_used_ew_receiver_indices()
            
            # Save to consolidate all data into single file after migration
            await self._save_registered_devices()
            
            # Cleanup old separate index files after successful consolidation
            await self._cleanup_old_index_files()
            
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
            
            # Synchronize EW-Receiver index tracking with transceiver wrapper
            await self._sync_ew_receiver_indices_with_wrapper()
            
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
            serial_number = telegram_data.get("serial_number")
            _LOGGER.warning("📥 COORDINATOR: Handling telegram from %s, type=%s", 
                          serial_number[-8:] if serial_number else "unknown",
                          telegram_data.get("type"))
            
            if not serial_number:
                _LOGGER.warning("Received telegram without serial number")
                return

            # Check if this telegram is from a registered EWneo device
            is_ewneo_device = self._is_ewneo_device_telegram(serial_number)
            
            # Fire button events IMMEDIATELY for EW-Transmitters, but NOT for EWneo devices
            if (telegram_data.get("type") == "ew_transmitter" or telegram_data.get("info_type") in [0, 1]) and not is_ewneo_device:
                _LOGGER.warning("🔵 Firing button events for EW-Transmitter telegram")
                await self._fire_button_events(serial_number, telegram_data)
            elif is_ewneo_device:
                _LOGGER.warning("🔄 Processing EWneo device telegram %s as state update", serial_number[-8:])
            
            # Check if device is registered for management
            is_registered = self.is_device_registered(serial_number)
            _LOGGER.warning("🔍 Device %s registered: %s", serial_number[-8:], is_registered)
            
            if not is_registered:
                # Only process learn telegrams for potential new device registration
                is_learn = telegram_data.get("is_learn_telegram", False) or self.is_setup_mode_active
                if not is_learn:
                    _LOGGER.debug("📡 Ignoring telegram from unregistered device %s (not in setup mode)", 
                                serial_number[-8:])
                    return
                _LOGGER.warning("🎓 Processing telegram for potential registration of device %s", 
                           serial_number[-8:])
            
            # Update device info if known device - MUST use FULL serial (32 chars)
            if serial_number in self.devices:
                _LOGGER.debug("Device found in coordinator for serial %s...%s", serial_number[:8], serial_number[-8:])
                self.devices[serial_number]["last_seen"] = time.time()
                self.devices[serial_number]["last_telegram"] = telegram_data
                
                # CRITICAL: For EWneo-Sensoren, directly update temperature/humidity/battery in devices dict
                device_type = self.devices[serial_number].get("type")
                if device_type in ["ewneo_sensor", "ew_sensor"]:
                    # Extract measurements from telegram
                    if "temperature" in telegram_data:
                        self.devices[serial_number]["temperature"] = telegram_data["temperature"]
                        _LOGGER.debug("Temperature updated: %.1f°C", telegram_data["temperature"])
                    
                    if "humidity" in telegram_data:
                        self.devices[serial_number]["humidity"] = telegram_data["humidity"]
                        _LOGGER.debug("Humidity updated: %.1f%%", telegram_data["humidity"])
                    
                    if "battery_level" in telegram_data:
                        self.devices[serial_number]["battery_level"] = telegram_data["battery_level"]
                        _LOGGER.debug("Battery updated: %d%%", telegram_data["battery_level"])
                    
                    # IMMEDIATELY fire sensor update events with FULL serial
                    await self._fire_specific_entity_events(serial_number, telegram_data)
            else:
                _LOGGER.warning("Device NOT found in coordinator.devices! Serial: %s...%s", 
                              serial_number[:8], serial_number[-8:])
            
            # ALWAYS fire telegram event with FULL serial (even if not in devices dict)
            self.hass.bus.async_fire(
                EVENT_TELEGRAM_RECEIVED,
                {
                    "serial_number": serial_number,
                    "telegram_data": telegram_data,
                }
            )
            
            # Fire device updated event if device exists in registry
            if serial_number in self.devices:
                _LOGGER.warning("📢 Firing EVENT_DEVICE_UPDATED with full serial: %s", serial_number[:8]+"..."+serial_number[-8:])
                self.hass.bus.async_fire(
                    EVENT_DEVICE_UPDATED,
                    {
                        "serial_number": serial_number,
                        "device_info": self.devices[serial_number],
                        "telegram_data": telegram_data,
                    }
                )
                
                # Process EWneo device state updates
                if is_ewneo_device:
                    await self._process_ewneo_state_update(serial_number, telegram_data)
                
                # Notify listeners directly for faster updates (instead of full refresh)
                _LOGGER.debug("🔔 Notifying coordinator listeners...")
                self.async_set_updated_data(self.devices)
                _LOGGER.warning("✅ Telegram handling complete")
            else:
                # Unknown device - ignore (no auto-discovery)
                device_type = telegram_data.get("type", "unknown")
                _LOGGER.debug("Received telegram from unknown device %s (type: %s) - ignoring", 
                            serial_number[-6:], device_type)
                    
        except Exception as e:
            _LOGGER.error("❌ Error handling telegram: %s", e, exc_info=True)

    async def _register_device_with_transceiver(self, serial_number: str, device_info: Dict[str, Any], telegram_data: Dict[str, Any]) -> None:
        """Register device with transceiver using structured device classes."""
        try:
            # Create device instance using device factory first
            if hasattr(self.transceiver, 'device_factory'):
                # Prepare kwargs for EWneo devices
                create_kwargs = {}
                if device_info.get("neo_device"):
                    create_kwargs['gateway_serial'] = device_info.get('gateway_serial')
                    create_kwargs['transceiver'] = self.transceiver
                
                device_instance = self.transceiver.device_factory.create_device(
                    serial_number=serial_number,
                    device_info=device_info,
                    telegram_data=telegram_data,
                    **create_kwargs
                )
                
                if device_instance:
                    # Store device instance in transceiver
                    if not hasattr(self.transceiver, '_device_instances'):
                        self.transceiver._device_instances = {}
                    self.transceiver._device_instances[serial_number] = device_instance
                    
                    # Initialize EWneo devices (queries state)
                    if device_info.get("neo_device") and hasattr(device_instance, 'async_initialize'):
                        _LOGGER.info("🆕 Initializing new EWneo device: %s", serial_number[-6:])
                        await device_instance.async_initialize(is_restoration=False)
                    
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
            
            # Handle EWneo-Sensoren measurement events (both EW and EWneo-Sensoren)
            elif device_type in ["ew_sensor", "ewneo_sensor"] or info_type == 2:
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
            
            # Extract state bytes - can be directly in telegram_data or in raw_data
            state_bytes = []
            
            # First try: Direct state_bytes from telegram_data (new format)
            if "state_bytes" in telegram_data:
                state_bytes = telegram_data["state_bytes"]
                _LOGGER.debug("🔍 EWneo device %s: Using direct state_bytes: %s", 
                            serial_number[-8:], [f"0x{b:02X}" for b in state_bytes])
            else:
                # Fallback: Extract from raw_data.info_data (old format)
                raw_data = telegram_data.get("raw_data", {})
                info_data_hex = raw_data.get("info_data", "")
                
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
                    _LOGGER.warning("⚠️ No state_bytes or info_data found in telegram for EWneo device %s", serial_number[-8:])
                    return
            
            device_type_code = target_device_info.get("device_type_code", 0)
            device_type_name = target_device_info.get("device_type_name", "ewneo_switch")
            
            _LOGGER.debug("🔍 EWneo device %s: device_type_code=%s, device_type_name=%s", 
                         target_device_serial[-8:], device_type_code, device_type_name)
            
            if state_bytes:
                # Extract query_mode from telegram if available
                query_mode = telegram_data.get("mode")
                
                # Parse the state using our existing parser
                parsed_state = self._parse_ewneo_state(device_type_code, state_bytes, device_type_name, target_device_serial, mode=query_mode)
                
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
                            "query_mode": query_mode,  # Include query mode for channel filtering
                            "timestamp": telegram_data.get("timestamp"),
                        }
                    )
                    
                    _LOGGER.debug("Fired EWneo state update event for device %s (from %s, mode=%s)", 
                                target_device_serial[-8:], serial_number[-8:], query_mode)
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
            from .entity_specs import create_entity_specs_for_device
            
            # Create entity specifications for this device
            entity_specs = create_entity_specs_for_device(serial_number, device_info)
            
            # Flatten entity specs into a single list for the event
            all_entities = []
            for platform, entities in entity_specs.items():
                all_entities.extend(entities)
            
            # Store entities in device_info if not already there
            if "entities" not in device_info or not device_info["entities"]:
                device_info["entities"] = all_entities
                _LOGGER.debug("Stored %d entities in device_info for %s", len(all_entities), serial_number[-8:])
            
            # Get platforms from entity specs
            platforms = {platform for platform, entities in entity_specs.items() if entities}
            
            _LOGGER.info("🔥 [register_device] Firing EVENT_DEVICE_ADDED for %s with %d entities", 
                        serial_number[-8:], len(all_entities))
            
            self.hass.bus.async_fire(
                EVENT_DEVICE_ADDED,
                {
                    "serial_number": serial_number,
                    "device_info": device_info,
                    "device_type": device_info.get("type"),
                    "device_name": device_info.get("name"),
                    "entities": all_entities,  # Add flattened entities to event data
                    "platforms": platforms,  # Add detected platforms
                }
            )
            
            # Mark event as fired to prevent duplicates
            self._devices_with_fired_events.add(serial_number)
            
            _LOGGER.info("🚀 [register_device] Device added event fired for %s (platforms: %s, entities: %d)", 
                        serial_number[-8:], platforms, len(all_entities))
            
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
        """Remove device from registered list and clean up entities.
        
        Args:
            serial_number: Device serial number to remove
            force_remove: Force removal even if device was auto-discovered
            add_to_blacklist: Legacy parameter (ignored - DeviceManager handles this)
            
        Returns:
            True if successful, False otherwise
        """
        # Note: add_to_blacklist is ignored because DeviceManager whitelist is the single source of truth
        # Devices not in whitelist are automatically not loaded
        return await self.unregister_device_permanently(serial_number)
    async def _cleanup_rx11_device(self, serial_number: str, device_info: Dict[str, Any]) -> None:
        """Clean up RX11-based device resources - delegates to transceiver."""
        if hasattr(self.transceiver, 'cleanup_rx11_device'):
            await self.transceiver.cleanup_rx11_device(
                serial_number=serial_number,
                device_info=device_info,
                device_registry=self.device_manager,
                index_free_callback=self.mark_ew_receiver_index_free
            )
        else:
            _LOGGER.warning("Transceiver does not support cleanup_rx11_device, using legacy cleanup")
            # Fallback to basic cleanup
            try:
                ew_receiver_index = device_info.get("ew_receiver_index") or device_info.get("rx11_index")
                if ew_receiver_index is not None:
                    self.mark_ew_receiver_index_free(ew_receiver_index)
                    _LOGGER.info("♻️ Freed EW-Receiver index %d", ew_receiver_index)
            except Exception as e:
                _LOGGER.error("Error in legacy RX11 cleanup: %s", e)

    async def _cleanup_ewneo_device(self, serial_number: str, device_info: Dict[str, Any]) -> None:
        """Clean up EWneo-based device resources including EWB index mappings."""
        try:
            # Clean up EWB index mapping if this was an EWneo device
            ewneo_index = device_info.get("ewneo_index")
            gateway_serial = device_info.get("gateway_serial")
            receiver_serial = serial_number  # The device's own serial is the receiver serial
            
            _LOGGER.info("🔍 EWneo cleanup debug - ewneo_index: %s, gateway_serial: %s, receiver_serial: %s",
                        ewneo_index, gateway_serial[-8:] if gateway_serial else None, receiver_serial[-8:])
            
            # Try to remove the device via EWB protocol first
            # We need gateway_serial to send the remove command
            if gateway_serial and receiver_serial:
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
            else:
                _LOGGER.warning("⚠️ Cannot remove EWneo device via EWB protocol - missing gateway_serial (%s) or receiver_serial (%s)",
                              "present" if gateway_serial else "missing",
                              "present" if receiver_serial else "missing")
                    
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
                
                # Mark as already fired since entities will be created by platform setup
                # This prevents fire_pending_device_events from firing duplicate events
                self._devices_with_fired_events.add(serial_number)
                
                # Register with transceiver
                await self.transceiver.register_device(serial_number, device_info)
                
                # Create and store device instance using device factory
                _LOGGER.info("🔧 Attempting to create device instance for %s (type=%s)", 
                           serial_number[-6:], device_info.get("device_type"))
                
                if hasattr(self.transceiver, 'device_factory'):
                    try:
                        # Prepare kwargs for EWneo devices
                        create_kwargs = {}
                        if device_info.get("neo_device"):
                            create_kwargs['gateway_serial'] = device_info.get('gateway_serial')
                            create_kwargs['transceiver'] = self.transceiver
                        
                        device_instance = self.transceiver.device_factory.create_device(
                            serial_number=serial_number,
                            device_info=device_info,
                            telegram_data={},  # No telegram data during restoration
                            **create_kwargs
                        )
                        
                        if device_instance:
                            # Store device instance in transceiver
                            if not hasattr(self.transceiver, '_device_instances'):
                                self.transceiver._device_instances = {}
                            self.transceiver._device_instances[serial_number] = device_instance
                            
                            # Initialize EWneo devices (queries state)
                            if device_info.get("neo_device") and hasattr(device_instance, 'async_initialize'):
                                _LOGGER.info("🔄 Initializing EWneo device: %s", serial_number[-6:])
                                await device_instance.async_initialize(is_restoration=True)
                            
                            _LOGGER.info("✅ Created and stored device instance for %s: %s", 
                                        serial_number[-6:], type(device_instance).__name__)
                        else:
                            _LOGGER.warning("⚠️ Device factory returned None for %s", serial_number[-6:])
                    except Exception as e:
                        _LOGGER.error("❌ Error creating device instance for %s: %s", 
                                     serial_number[-6:], e, exc_info=True)
                else:
                    _LOGGER.warning("⚠️ Transceiver has no device_factory attribute")
                
                # No events fired here - entities are created by platform setup code
            
            # Log summary
            if skipped_count > 0:
                _LOGGER.info("✅ Loaded %d/%d devices (skipped %d non-whitelisted)", 
                           loaded_count, len(saved_devices), skipped_count)
            else:
                _LOGGER.info("✅ Loaded %d devices successfully", loaded_count)
            
            # Recalculate next free indices after all devices are restored
            # This ensures the indices are correct even if devices were restored with indices
            if self._used_ewb_indices:
                self._next_free_ewb_index = self._find_next_free_ewb_index()
                _LOGGER.info("📍 Recalculated next free EWB index after device restore: %d (used: %d indices)", 
                           self._next_free_ewb_index, len(self._used_ewb_indices))
            
            if self._used_ew_receiver_indices:
                self._next_free_ew_receiver_index = self._find_next_free_ew_receiver_index()
                _LOGGER.info("📍 Recalculated next free EW-Receiver index after device restore: %d (used: %d indices)", 
                           self._next_free_ew_receiver_index, len(self._used_ew_receiver_indices))
            
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
        """Restore RX11-based device - delegates to transceiver."""
        if hasattr(self.transceiver, 'restore_rx11_device'):
            await self.transceiver.restore_rx11_device(
                serial_number=serial_number,
                device_info=device_info,
                device_registry=self.device_manager,
                index_used_callback=self.mark_ew_receiver_index_used,
                whitelist_callback=self._add_to_whitelist_memory
            )
            
            # Additional EWneo handling (if needed)
            ewneo_index = device_info.get("ewneo_index")
            gateway_serial = device_info.get("gateway_serial")
            
            if ewneo_index is not None and gateway_serial:
                device_name = device_info.get("name", f"EWneo Device ({serial_number[-6:]})")
                self.mark_ewb_index_used(ewneo_index, gateway_serial, serial_number, device_name)
                _LOGGER.info("🔄 Restored EWneo EWB index: %d, Gateway: %s, Device: %s", 
                           ewneo_index, gateway_serial[-8:], serial_number[-8:])
        else:
            _LOGGER.warning("Transceiver does not support restore_rx11_device")

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
        """Get all devices (both legacy and registered) with cleaned metadata."""
        all_devices = {}
        
        # Merge devices and registered_devices
        for serial, device_info in {**self.devices, **self._registered_devices}.items():
            # Clean device_info before returning
            cleaned_info = device_info.copy()
            self._clean_device_info(cleaned_info)
            all_devices[serial] = cleaned_info
            
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
            
        return await self.unregister_device(serial_number, force_remove=force)

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
    
    # Blacklist functionality removed - only whitelist-based approach is used
    
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

            # Use RX11 transceiver's device creation helper (maintains architectural boundaries)
            if hasattr(self.transceiver, 'create_rx11_ew_device_info'):
                device_info = await self.transceiver.create_rx11_ew_device_info(
                    serial_number=serial_number,
                    device_type=device_type,
                    device_name=device_name,
                    channels=channels,
                    detected_via=detected_via,
                    info_type=info_type,
                    timestamp=timestamp,
                    device_registry=self.device_manager,
                    ew_receiver_allocator=self._allocate_ew_receiver_with_persistence
                )
            else:
                _LOGGER.error("Transceiver does not support create_rx11_ew_device_info")
                return False
            
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

    # DEPRECATED: Moved to transceivers/rx11/transceiver.py for proper architectural layering
    # Use self.transceiver.create_rx11_ew_device_info() instead
    # async def _create_rx11_ew_device_info(...)

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
            
            # Device will be added to whitelist during registration
            
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
            
            # Remove device
            success = await self.async_remove_device(serial_number, force=True)
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
    # UNIFIED CLEANUP SYSTEM
    # =============================================================================
    
    async def async_cleanup_devices(
        self,
        mode: str = "orphaned",
        dry_run: bool = False,
        max_age_hours: int = 168
    ) -> Dict[str, Any]:
        """Unified device cleanup with multiple modes.
        
        Args:
            mode: Cleanup mode - 'orphaned', 'ghost', 'missing', or 'all'
            dry_run: If True, only report what would be cleaned without removing
            max_age_hours: For ghost mode, devices not seen for this many hours
            
        Returns:
            Dictionary with cleanup results
        """
        try:
            _LOGGER.info("🧹 Starting device cleanup (mode=%s, dry_run=%s)", mode, dry_run)
            
            if mode == "orphaned":
                return await self._cleanup_orphaned_mode(dry_run)
            elif mode == "ghost":
                return await self._cleanup_ghost_mode(dry_run, max_age_hours)
            elif mode == "missing":
                return await self._cleanup_missing_mode(dry_run)
            elif mode == "all":
                # Run all cleanup modes sequentially
                results = {}
                results["orphaned"] = await self._cleanup_orphaned_mode(dry_run)
                results["ghost"] = await self._cleanup_ghost_mode(dry_run, max_age_hours)
                results["missing"] = await self._cleanup_missing_mode(dry_run)
                return results
            else:
                raise ValueError(f"Unknown cleanup mode: {mode}")
                
        except Exception as e:
            _LOGGER.error("❌ Error during cleanup: %s", e)
            return {"error": str(e), "mode": mode}
    
    async def _cleanup_orphaned_mode(self, dry_run: bool = False) -> Dict[str, Any]:
        """Clean up orphaned entities (entities without corresponding devices)."""
        try:
            _LOGGER.info("🔍 Checking for orphaned entities...")
            
            entity_registry = er.async_get(self.hass)
            device_registry = dr.async_get(self.hass)
            
            eldat_entities = []
            orphaned_entities = []
            
            for entity in entity_registry.entities.values():
                if entity.platform == "eldat_plugin":
                    eldat_entities.append(entity)
                    
                    # Extract serial from unique_id
                    serial_in_unique_id = self._extract_serial_from_unique_id(entity.unique_id)
                    
                    if serial_in_unique_id and serial_in_unique_id not in self.devices:
                        orphaned_entities.append((entity, serial_in_unique_id))
            
            # Clean up orphaned entities
            cleaned_serials = set()
            if not dry_run:
                for entity, serial_number in orphaned_entities:
                    try:
                        _LOGGER.debug("Removing orphaned entity: %s (device: %s)", 
                                    entity.entity_id, serial_number)
                        entity_registry.async_remove(entity.entity_id)
                        cleaned_serials.add(serial_number)
                        if entity.unique_id:
                            self.created_entity_unique_ids.discard(entity.unique_id)
                    except Exception as e:
                        _LOGGER.warning("Failed to remove orphaned entity %s: %s", 
                                      entity.entity_id, e)
            
            # Also clean up orphaned devices
            orphaned_devices = []
            for device in device_registry.devices.values():
                if any(identifier[0] == DOMAIN for identifier in device.identifiers):
                    for domain, serial in device.identifiers:
                        if domain == DOMAIN and serial not in self.devices:
                            orphaned_devices.append((device, serial))
                            break
            
            if not dry_run:
                for device_entry, serial_number in orphaned_devices:
                    try:
                        _LOGGER.debug("Removing orphaned device: %s (serial: %s)", 
                                    device_entry.id, serial_number)
                        device_registry.async_remove_device(device_entry.id)
                        cleaned_serials.add(serial_number)
                    except Exception as e:
                        _LOGGER.warning("Failed to remove orphaned device %s: %s", 
                                      device_entry.id, e)
            
            result = {
                "mode": "orphaned",
                "dry_run": dry_run,
                "total_eldat_entities": len(eldat_entities),
                "orphaned_entities_found": len(orphaned_entities),
                "orphaned_devices_found": len(orphaned_devices),
                "cleaned_entities": 0 if dry_run else len([e for e, _ in orphaned_entities]),
                "cleaned_devices": 0 if dry_run else len([d for d, _ in orphaned_devices]),
                "affected_serials": list(cleaned_serials)
            }
            
            _LOGGER.info("✅ Orphaned cleanup: %d entities, %d devices %s", 
                        len(orphaned_entities), len(orphaned_devices),
                        "would be cleaned" if dry_run else "cleaned")
            
            return result
            
        except Exception as e:
            _LOGGER.error("❌ Error during orphaned cleanup: %s", e)
            return {"error": str(e), "mode": "orphaned"}
    
    async def _cleanup_ghost_mode(self, dry_run: bool = False, max_age_hours: int = 168) -> Dict[str, Any]:
        """Clean up ghost devices (devices not seen for a long time)."""
        try:
            _LOGGER.info("🔍 Checking for ghost devices (max_age=%d hours)...", max_age_hours)
            
            current_devices = await self.device_registry.get_all_devices()
            ghost_devices = []
            current_time = time.time()
            
            for device_id, device_entry in current_devices.items():
                serial_number = device_entry.serial_number.receiver_transmitter
                last_seen = device_entry.last_seen
                
                # Calculate hours since last seen
                hours_since_seen = self._calculate_hours_since_seen(last_seen, current_time)
                
                if hours_since_seen > max_age_hours:
                    device_data = {
                        "name": device_entry.name,
                        "device_type": DeviceType(device_entry.information.device_type).name,
                        "hours_since_seen": hours_since_seen,
                        "last_seen": last_seen
                    }
                    ghost_devices.append((serial_number, device_data))
            
            # Remove ghost devices if not dry run
            removed_count = 0
            if not dry_run:
                for serial_number, device_data in ghost_devices:
                    try:
                        await self.async_remove_device(serial_number)
                        removed_count += 1
                    except Exception as e:
                        _LOGGER.warning("Failed to remove ghost device %s: %s", 
                                      serial_number, e)
            
            result = {
                "mode": "ghost",
                "dry_run": dry_run,
                "max_age_hours": max_age_hours,
                "ghost_devices_found": len(ghost_devices),
                "ghost_devices_removed": removed_count,
                "devices": [
                    {
                        "serial": serial[-8:],
                        "name": data["name"],
                        "type": data["device_type"],
                        "hours_inactive": data["hours_since_seen"]
                    }
                    for serial, data in ghost_devices
                ]
            }
            
            _LOGGER.info("✅ Ghost cleanup: %d devices %s", 
                        len(ghost_devices),
                        "would be removed" if dry_run else "removed")
            
            return result
            
        except Exception as e:
            _LOGGER.error("❌ Error during ghost cleanup: %s", e)
            return {"error": str(e), "mode": "ghost"}
    
    async def _cleanup_missing_mode(self, dry_run: bool = False) -> Dict[str, Any]:
        """Repair/cleanup devices in storage but missing from Home Assistant."""
        try:
            _LOGGER.info("🔍 Checking for missing devices...")
            
            missing_devices = []
            repaired_count = 0
            
            for serial_number, device_info in self._registered_devices.items():
                ha_device_id = device_info.get("homeassistant_device_id")
                ha_entities = device_info.get("homeassistant_entities", [])
                
                if not ha_device_id or not ha_entities:
                    missing_devices.append((serial_number, device_info))
            
            # Repair missing devices if not dry run
            if not dry_run:
                for serial_number, device_info in missing_devices:
                    try:
                        _LOGGER.info("🔧 Repairing missing device: %s", 
                                   device_info.get("name", serial_number[-8:]))
                        
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
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        _LOGGER.warning("Failed to repair missing device %s: %s", 
                                      serial_number, e)
            
            result = {
                "mode": "missing",
                "dry_run": dry_run,
                "missing_devices_found": len(missing_devices),
                "devices_repaired": repaired_count,
                "devices": [
                    {
                        "serial": serial[-8:],
                        "name": info.get("name", "Unknown"),
                        "type": info.get("type", "unknown")
                    }
                    for serial, info in missing_devices
                ]
            }
            
            _LOGGER.info("✅ Missing device cleanup: %d devices %s", 
                        len(missing_devices),
                        "would be repaired" if dry_run else "repaired")
            
            return result
            
        except Exception as e:
            _LOGGER.error("❌ Error during missing device cleanup: %s", e)
            return {"error": str(e), "mode": "missing"}
    
    def _extract_serial_from_unique_id(self, unique_id: str) -> Optional[str]:
        """Extract serial number from entity unique_id."""
        if not unique_id or "_" not in unique_id:
            return None
        
        # Try pattern: serial_entitytype
        parts = unique_id.split("_")
        for part in parts:
            if len(part) == 32 and all(c in "0123456789ABCDEFabcdef" for c in part):
                return part.upper()
        
        return None
    
    def _calculate_hours_since_seen(self, last_seen: Optional[str], current_time: float) -> float:
        """Calculate hours since device was last seen."""
        if not last_seen:
            return 999.0  # Very old if no data
        
        try:
            from datetime import datetime
            last_seen_dt = datetime.fromisoformat(last_seen.replace('Z', '+00:00'))
            last_seen_timestamp = last_seen_dt.timestamp()
            return (current_time - last_seen_timestamp) / 3600
        except (ValueError, AttributeError):
            return 999.0
    
    # =============================================================================
    # LEGACY CLEANUP METHODS (Deprecated - use async_cleanup_devices instead)
    # =============================================================================
    
    async def repair_orphaned_devices(self) -> int:
        """DEPRECATED: Use async_cleanup_devices(mode='missing') instead."""
        _LOGGER.warning("repair_orphaned_devices is deprecated, use async_cleanup_devices(mode='missing')")
        result = await self._cleanup_missing_mode(dry_run=False)
        return result.get("devices_repaired", 0)
    
    async def _cleanup_orphaned_entities(self, serial_number: str) -> bool:
        """DEPRECATED: Internal method, use async_cleanup_devices instead."""
        _LOGGER.warning("_cleanup_orphaned_entities is deprecated")
        # Keep for backward compatibility but log warning
        try:
            _LOGGER.info("🧹 Cleaning up orphaned entities for device: %s", serial_number)
            
            device_registry = dr.async_get(self.hass)
            device_entry = device_registry.async_get_device(
                identifiers={(DOMAIN, serial_number)}
            )
            if device_entry:
                _LOGGER.debug("Removing orphaned device from device registry: %s", device_entry.id)
                device_registry.async_remove_device(device_entry.id)
            
            entity_registry = er.async_get(self.hass)
            entities_to_remove = []
            
            for entity in entity_registry.entities.values():
                if (entity.platform == "eldat_plugin" and 
                    (entity.device_id == device_entry.id if device_entry else
                     serial_number in entity.unique_id)):
                    entities_to_remove.append(entity.entity_id)
            
            for entity_id in entities_to_remove:
                _LOGGER.debug("Removing orphaned entity: %s", entity_id)
                entity = entity_registry.async_get(entity_id)
                if entity and entity.unique_id:
                    self.created_entity_unique_ids.discard(entity.unique_id)
                entity_registry.async_remove(entity_id)
            
            _LOGGER.info("✅ Cleaned up %d orphaned entities for device %s", 
                        len(entities_to_remove), serial_number)
            return True
        except Exception as e:
            _LOGGER.error("Failed to cleanup orphaned entities: %s", e)
            return False
    
    async def async_cleanup_all_orphaned_entities(self) -> Dict[str, Any]:
        """DEPRECATED: Use async_cleanup_devices(mode='orphaned') instead."""
        _LOGGER.warning("async_cleanup_all_orphaned_entities is deprecated, use async_cleanup_devices(mode='orphaned')")
        return await self._cleanup_orphaned_mode(dry_run=False)
    
    async def async_cleanup_ghost_devices(self) -> None:
        """DEPRECATED: Use async_cleanup_devices(mode='ghost') instead."""
        _LOGGER.warning("async_cleanup_ghost_devices is deprecated, use async_cleanup_devices(mode='ghost')")
        await self._cleanup_ghost_mode(dry_run=False)

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
            state_value = int.from_bytes(state_bytes, byteorder='big')  # EWneo uses big-endian
            
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

    def _parse_ewneo_state(self, device_type_code: int, state_bytes: list, device_type_name: str, device_serial: str = None, mode: int = None) -> Optional[dict]:
        """Parse EWneo receiver state based on device type.
        
        Args:
            device_type_code: Device type code (0x05=Single, 0x08=Dual, 0x09=Quad motor, etc.)
            state_bytes: 4-byte state data
            device_type_name: Device type name string
            device_serial: Device serial number (optional, for logging)
            mode: EWB query/response mode (0=all motors, 2/10/18/26=individual motor)
        
        NOTE: EWneo devices send state responses in LITTLE-ENDIAN byte order (confirmed by device implementations),
        but we parse them here as BIG-ENDIAN to match the command format we send.
        This works because we're receiving the raw bytes and the device layer handles the conversion.
        """
        try:
            # EWneo uses BIG-ENDIAN byte order for state parsing (to match command format)
            state_value = int.from_bytes(state_bytes, byteorder='big')
            
            if device_type_code == 0x03:  # EWB_DT_SWITCH (Single Switch)
                # Switch state format (from EWB specification):
                # Bits 31-27: Counter (0-31) incremented on switch-on
                # Bits 26-24: State reason (1=off, 2=on, 5=on due to logic)
                # Bits 23-0:  Reserved
                
                # Extract state reason from bits 26-24
                state_reason = (state_value >> 24) & 0x07
                counter = (state_value >> 27) & 0x1F
                
                # State is ON if reason is 2 (on) or 5 (on due to logic)
                is_on = state_reason in [2, 5]
                
                device_id = device_serial[-8:] if device_serial else "Unknown"
                _LOGGER.debug("🔍 EWneo Switch %s: State=0x%08X, Reason=%d, Counter=%d, IsOn=%s", 
                             device_id, state_value, state_reason, counter, is_on)
                
                return {
                    'type': 'switch',
                    'state': is_on,
                    'on': is_on,
                    'counter': counter,
                    'reason': state_reason
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
                
            elif device_type_code == 0x05:  # EWB_DT_MOTOR (Single Motor)
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
                
            elif device_type_code in [0x06, 0x07]:  # EWB_DT_DUAL_SWITCH (0x06), EWB_DT_QUAD_SWITCH (0x07)
                # Multi-switch state format: All channels packed in ONE 32-bit word
                # Dual Switch (0x06):
                #   Bits 31-27: Counter for switch #1
                #   Bits 26-24: State for switch #1 (1=off, 2=on, 3=timer, 5=logic)
                #   Bits 23-19: Counter for switch #2
                #   Bits 18-16: State for switch #2 (1=off, 2=on, 3=timer, 5=logic)
                #   Bits 15-0: Reserved
                # Quad Switch (0x07): Similar but with 4 channels
                
                # Parse as big-endian to match command format
                state_value = int.from_bytes(state_bytes, byteorder='big')
                
                channels = 2 if device_type_code == 0x06 else 4
                channel_states = {}
                
                # Extract state for each channel from the packed 32-bit word
                if device_type_code == 0x06:  # Dual switch
                    # Channel 1: bits 31-24
                    ch1_counter = (state_value >> 27) & 0x1F
                    ch1_reason = (state_value >> 24) & 0x07
                    ch1_is_on = ch1_reason in [2, 3, 5]
                    
                    # Channel 2: bits 23-16
                    ch2_counter = (state_value >> 19) & 0x1F
                    ch2_reason = (state_value >> 16) & 0x07
                    ch2_is_on = ch2_reason in [2, 3, 5]
                    
                    channel_states['channel_1'] = {
                        'state': ch1_is_on,
                        'on': ch1_is_on,
                        'counter': ch1_counter,
                        'reason': ch1_reason
                    }
                    channel_states['channel_2'] = {
                        'state': ch2_is_on,
                        'on': ch2_is_on,
                        'counter': ch2_counter,
                        'reason': ch2_reason
                    }
                    
                    device_id = device_serial[-8:] if device_serial else "Unknown"
                    _LOGGER.debug("🔍 EWneo Dual Switch %s: State=0x%08X, CH1=%s (reason=%d, cnt=%d), CH2=%s (reason=%d, cnt=%d)", 
                                 device_id, state_value, 
                                 "ON" if ch1_is_on else "OFF", ch1_reason, ch1_counter,
                                 "ON" if ch2_is_on else "OFF", ch2_reason, ch2_counter)
                    
                else:  # Quad switch (0x07)
                    # Similar pattern for 4 channels
                    # Channel 1: bits 31-24
                    # Channel 2: bits 23-16
                    # Channel 3: bits 15-8
                    # Channel 4: bits 7-0
                    for i in range(4):
                        shift = (3 - i) * 8  # 24, 16, 8, 0
                        counter = (state_value >> (shift + 3)) & 0x1F
                        reason = (state_value >> shift) & 0x07
                        is_on = reason in [2, 3, 5]
                        
                        channel_states[f'channel_{i+1}'] = {
                            'state': is_on,
                            'on': is_on,
                            'counter': counter,
                            'reason': reason
                        }
                
                return {
                    'type': 'switch',  # Return 'switch' type for compatibility
                    'channels': channels,
                    **channel_states  # Unpack channel states at top level for easy access
                }
                
            elif device_type_code in [0x08, 0x09]:  # EWB_DT_DUAL_MOTOR (0x08), EWB_DT_QUAD_MOTOR (0x09)
                # Dual/Quad motor state parsing according to EWB specification
                # 
                # Mode 0: All motors in one 32-bit word (multi_cover)
                # Mode 2/10/18/26: Individual motor detail (cover)
                
                # CRITICAL: Check mode parameter to determine format
                # Mode 0 = Summary (all motors), Mode 2/10/18/26 = Individual motor
                if mode == 0 or mode is None:
                    # Mode 0 or unknown: Parse as multi-channel summary (all motors in 32-bit word)
                    # Skip to multi_cover parsing below
                    pass
                elif mode in [2, 10, 18, 26]:
                    # Mode 2/10/18/26: Individual motor response in full detail
                    # Parse using the same format as single motor (0x05)
                    motor_status_code = (state_value >> 25) & 0x7F
                    current_position_raw = (state_value >> 17) & 0x7F  # Bits 23-17
                    recent_tilt = bool(state_value & (1 << 16))
                    target_position_raw = (state_value >> 9) & 0x7F
                    auto_tilt = bool(state_value & (1 << 8))
                    runtime_measured = bool(state_value & (1 << 7))
                    tilt_measured = bool(state_value & (1 << 6))
                    terrace_function = bool(state_value & (1 << 2))
                    stored_position = state_value & 0x03
                    
                    # Interpret motor status code
                    motor_status_map = {
                        126: "stopped",
                        119: "stopped_terrace",
                        120: "opening_runtime",
                        121: "closing_runtime",
                        122: "opening_120s",
                        123: "closing_120s",
                        124: "opening_position",
                        125: "closing_position",
                        117: "calibrating_runtime",
                        118: "calibrating_tilt"
                    }
                    
                    motor_status = motor_status_map.get(motor_status_code, "unknown")
                    
                    # Convert positions
                    current_position = None
                    target_position = None
                    position_available = False
                    
                    if runtime_measured:
                        if current_position_raw <= 100:
                            current_position = 100 - current_position_raw
                            position_available = True
                        if target_position_raw <= 100:
                            target_position = 100 - target_position_raw
                        
                        if motor_status_code in [117, 118, 122, 123]:
                            current_position = None
                            target_position = None
                            position_available = False
                    
                    # Determine movement state
                    is_opening = motor_status_code in [120, 122, 124]
                    is_closing = motor_status_code in [121, 123, 125]
                    is_stopped = motor_status_code in [126, 119] or (0 <= motor_status_code <= 100)
                    is_calibrating = motor_status_code in [117, 118]
                    
                    device_id = device_serial[-8:] if device_serial else "Unknown"
                    _LOGGER.debug("🔍 EWneo Multi-Motor (individual mode) %s: State=0x%08X, Status=%s, Pos=%s, Runtime=%s",
                                 device_id, state_value, motor_status, current_position, runtime_measured)
                    
                    # Return in multi_cover format but with single channel
                    # The Cover entity will extract the correct channel based on which mode was used
                    return {
                        'type': 'cover',  # Single motor format for individual channel response
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
                
                # Otherwise, parse as Mode 0 (Summary) - all motors packed together
                # CORRECT FORMAT per EWB specification:
                # Dual Motor:
                #   Bits 31-25 (7 bits): Motor #1 status/position (0-100 or 117-127)
                #   Bit 24: Motor #1 recent tilt
                #   Bits 23-17 (7 bits): Motor #2 status/position
                #   Bit 16: Motor #2 recent tilt
                #   Bits 15-0: Reserved
                # Quad Motor:
                #   Bits 31-25: Motor #1 status, Bit 24: Motor #1 tilt
                #   Bits 23-17: Motor #2 status, Bit 16: Motor #2 tilt
                #   Bits 15-9: Motor #3 status, Bit 8: Motor #3 tilt
                #   Bits 7-1: Motor #4 status, Bit 0: Motor #4 tilt
                
                channels = 2 if device_type_code == 0x08 else 4
                channel_states = {}
                
                # Bit positions for each motor: [status_shift, tilt_bit]
                motor_bit_positions = [
                    (25, 24),  # Motor #1: bits 31-25, bit 24
                    (17, 16),  # Motor #2: bits 23-17, bit 16
                    (9, 8),    # Motor #3: bits 15-9, bit 8
                    (1, 0),    # Motor #4: bits 7-1, bit 0
                ]
                
                for i in range(channels):
                    status_shift, tilt_bit = motor_bit_positions[i]
                    
                    # Extract 7 bits for motor status/position
                    motor_status_code = (state_value >> status_shift) & 0x7F
                    
                    # Extract tilt bit
                    recent_tilt = bool(state_value & (1 << tilt_bit))
                    
                    # Interpret motor status code
                    motor_status_map = {
                        127: "unchanged",            # Host wants motor to remain at state
                        126: "stopped",              # Motor has stopped at unknown position
                        119: "stopped_tilted",       # Motor stopped, tilted to horizontal
                        120: "opening_runtime",      # Opening for runtime duration
                        121: "closing_runtime",      # Closing for runtime duration
                        122: "opening_120s",         # Opening for 120 seconds
                        123: "closing_120s",         # Closing for 120 seconds
                        124: "opening_position",     # Moving to open position
                        125: "closing_position",     # Moving to close position
                        117: "calibrating_runtime",  # Runtime measurement in progress
                        118: "calibrating_tilt"      # Tilt measurement in progress
                    }
                    
                    motor_status = motor_status_map.get(motor_status_code, "unknown")
                    
                    # Position is valid only if 0-100 (runtime measurement done)
                    position = None
                    runtime_measured = False
                    
                    if 0 <= motor_status_code <= 100:
                        # Position available: 0=open, 100=closed (convert to HA format)
                        position = 100 - motor_status_code  # Convert to HA: 0=closed, 100=open
                        runtime_measured = True
                    elif motor_status_code in [120, 121, 124, 125]:
                        # During movement with runtime measurement
                        runtime_measured = True
                    
                    # Determine movement state
                    is_opening = motor_status_code in [120, 122, 124]
                    is_closing = motor_status_code in [121, 123, 125]
                    is_stopped = motor_status_code in [126, 119] or (0 <= motor_status_code <= 100)
                    is_calibrating = motor_status_code in [117, 118]
                    
                    channel_states[f'channel_{i+1}'] = {
                        'motor_status': motor_status,
                        'motor_status_code': motor_status_code,
                        'position': position,
                        'position_available': position is not None,
                        'runtime_measured': runtime_measured,
                        'recent_tilt': recent_tilt,
                        'is_opening': is_opening,
                        'is_closing': is_closing,
                        'is_stopped': is_stopped,
                        'is_calibrating': is_calibrating,
                        'supports_position': runtime_measured
                    }
                
                device_id = device_serial[-8:] if device_serial else "Unknown"
                _LOGGER.debug("🔍 EWneo %s Motor %s: State=0x%08X, Motors=%s", 
                             "Dual" if channels == 2 else "Quad", device_id, state_value,
                             {f"M{i+1}": f"status={channel_states[f'channel_{i+1}']['motor_status']}, pos={channel_states[f'channel_{i+1}']['position']}" 
                              for i in range(channels)})
                
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