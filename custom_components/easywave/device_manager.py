"""Unified Device Manager for EASYWAVE integration.

This module provides a centralized, simplified device management system
where the whitelist is the single source of truth. All devices must be
in the whitelist, and devices not found are automatically marked as unavailable.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Set
from pathlib import Path
from dataclasses import dataclass, asdict
from enum import IntEnum

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class DeviceType(IntEnum):
    """Device types for EASYWAVE devices."""
    UNKNOWN = 0x00
    EW_TRANSMITTER = 0x01      # Easywave Transmitter (Transmitter)
    EW_RECEIVER = 0x02         # Easywave Receiver (Receiver)
    EWNEO_SENSOR = 0x03        # EWneo-Sensoren
    EWNEO_AKTOR = 0x04         # EWneo-Aktor
    EW_SENSOR = 0x05           # EWneo-Sensoren (Battery-powered sensors)
    EWB_TRANSCEIVER = 0x06     # EWB Transceiver
    SEC_TRANSMITTER = 0x07     # SecWave Transmitter
    SEC_RECEIVER = 0x08        # SecWave Receiver
    RX11_GATEWAY = 0xFF        # RX11 Gateway/Transceiver


class DeviceAvailability(IntEnum):
    """Device availability states."""
    AVAILABLE = 1       # Device is present and responding
    UNAVAILABLE = 2     # Device was removed or not responding
    UNKNOWN = 0         # Initial state or status unclear


@dataclass
class ManagedDevice:
    """Represents a managed device in the EASYWAVE system.
    
    This is the single source of truth for all device information.
    All devices must be registered in the whitelist to be managed.
    """
    # Core identification
    serial_number: str                    # Unique serial number (primary key)
    device_type: str                      # Device type as string
    name: str                             # Human-readable name
    
    # State management
    availability: DeviceAvailability = DeviceAvailability.UNKNOWN
    last_seen: Optional[str] = None       # ISO timestamp of last contact
    
    # Unified index management (consolidation from separate index files)
    indices: Optional[Dict[str, int]] = None  # {'ewb': 5, 'ew_receiver': 3, 'rx11': 42, ...}
    index_allocation_date: Optional[str] = None  # ISO timestamp when index was allocated
    allocation_gateway: Optional[str] = None  # Gateway serial that allocated the index
    
    # Legacy attribute (kept for backwards compatibility, now in indices dict)
    rx11_index: Optional[int] = None      # RX11 index for receivers (0-255) - DEPRECATED use indices['rx11']
    
    # Device attributes
    area: Optional[str] = None            # Home Assistant area
    battery_level: Optional[int] = None   # Battery level (0-100)
    signal_strength: Optional[int] = None # Signal strength (RSSI)
    
    # Metadata
    created_at: Optional[str] = None      # ISO timestamp when added
    updated_at: Optional[str] = None      # ISO timestamp of last update
    config_entry_id: Optional[str] = None # Home Assistant config entry ID
    
    # Additional data
    extra_data: Optional[Dict[str, Any]] = None  # Flexible additional data
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = asdict(self)
        # Convert enum to value
        result['availability'] = self.availability.value
        
        # Ensure rx11_index is synced with indices dict for backwards compatibility
        if self.indices and 'rx11' in self.indices:
            result['rx11_index'] = self.indices['rx11']
        
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ManagedDevice:
        """Create instance from dictionary with backwards compatibility."""
        # Handle availability enum
        if 'availability' in data:
            if isinstance(data['availability'], int):
                data['availability'] = DeviceAvailability(data['availability'])
        
        # Backwards compatibility: migrate rx11_index to indices dict if needed
        if 'rx11_index' in data and data['rx11_index'] is not None:
            if 'indices' not in data or data['indices'] is None:
                data['indices'] = {}
            if not isinstance(data['indices'], dict):
                data['indices'] = {}
            # Don't override if already in indices
            if 'rx11' not in data['indices']:
                data['indices']['rx11'] = data['rx11_index']
        
        return cls(**data)
    
    def mark_available(self) -> None:
        """Mark device as available and update last_seen."""
        self.availability = DeviceAvailability.AVAILABLE
        self.last_seen = datetime.now().isoformat()
    
    def mark_unavailable(self) -> None:
        """Mark device as unavailable."""
        self.availability = DeviceAvailability.UNAVAILABLE


class DeviceManager:
    """Unified device manager with whitelist as single source of truth.
    
    Key principles:
    1. All devices MUST be in the whitelist to be managed
    2. Devices not in whitelist are automatically marked as unavailable
    3. Serial number is the unique identifier (primary key)
    4. Thread-safe with async locks
    5. Persistent storage in JSON format
    """
    
    def __init__(self, hass: HomeAssistant, config_entry_id: str) -> None:
        """Initialize the unified device manager."""
        self.hass = hass
        self.config_entry_id = config_entry_id
        
        # File paths
        self.config_dir = Path(hass.config.config_dir) / DOMAIN
        self.devices_file = self.config_dir / "managed_devices.json"
        self.backup_file = self.config_dir / "managed_devices_backup.json"
        
        # In-memory device storage (serial_number -> ManagedDevice)
        self._devices: Dict[str, ManagedDevice] = {}
        # Backup of previous state (for defensive loading - never lose data)
        self._devices_backup: Dict[str, ManagedDevice] = {}
        
        # Secondary indices for quick lookups
        self._rx11_index_to_serial: Dict[int, str] = {}  # RX11 index -> serial
        self._rx11_index_to_serial_backup: Dict[int, str] = {}  # Backup
        
        # Thread safety
        self._lock = asyncio.Lock()
        
        # Ensure directory exists
        self.config_dir.mkdir(parents=True, exist_ok=True)
    
    async def load(self) -> bool:
        """Load all devices from persistent storage (defensive - preserves state on errors).
        
        Returns:
            True if loaded successfully, False otherwise (but preserves previous state)
        """
        async with self._lock:
            try:
                # STEP 1: Backup current state (for defensive recovery)
                self._devices_backup = dict(self._devices)
                self._rx11_index_to_serial_backup = dict(self._rx11_index_to_serial)
                
                if not self.devices_file.exists():
                    _LOGGER.info("📂 No device file found, starting with empty device list")
                    return True
                
                _LOGGER.info("📖 Loading managed devices from %s", self.devices_file)
                
                # STEP 2: Read file
                data = await self._read_json_file(self.devices_file)
                
                # Validate structure
                if not isinstance(data, dict) or 'devices' not in data:
                    _LOGGER.error("❌ Invalid device file structure")
                    return await self._load_backup()
                
                # Check version and migrate if needed
                file_version = data.get('version', '1.0')
                if file_version not in ('2.0', '3.0'):
                    _LOGGER.info("🔄 Migrating device data from version %s to 3.0", file_version)
                    data = await self._migrate_version(data, file_version)
                
                # STEP 3: Load into fresh state (not modifying _devices yet!)
                temp_devices: Dict[str, ManagedDevice] = {}
                temp_rx11_map: Dict[int, str] = {}
                
                # Load devices
                devices_data = data.get('devices', {})
                for serial, device_dict in devices_data.items():
                    try:
                        device = ManagedDevice.from_dict(device_dict)
                        temp_devices[serial] = device
                        
                        # Update secondary indices
                        rx11_idx = None
                        if device.indices and 'rx11' in device.indices:
                            rx11_idx = device.indices['rx11']
                        elif device.rx11_index is not None:
                            rx11_idx = device.rx11_index
                        
                        if rx11_idx is not None:
                            temp_rx11_map[rx11_idx] = serial
                        
                        _LOGGER.debug("✅ Loaded device: %s (%s)", 
                                     device.name, serial[-8:])
                    except Exception as e:
                        _LOGGER.error("❌ Failed to load device %s: %s", serial[-8:], e)
                
                # STEP 4: Only now replace state if all succeeded
                self._devices = temp_devices
                self._rx11_index_to_serial = temp_rx11_map
                
                _LOGGER.info("✅ Loaded %d managed devices", len(self._devices))
                return True
                
            except Exception as e:
                _LOGGER.error("❌ Failed to load devices: %s", e)
                
                # RECOVERY: Restore previous state from backup
                if self._devices_backup:
                    _LOGGER.warning("🔄 Restoring previous device state from backup")
                    self._devices = self._devices_backup
                    self._rx11_index_to_serial = self._rx11_index_to_serial_backup
                    return False
                else:
                    # No backup available, stay in current state
                    _LOGGER.warning("⚠️ No backup available, keeping current state")
                    return False
    
    async def _load_backup(self) -> bool:
        """Load from backup file if main file is corrupted."""
        try:
            if not self.backup_file.exists():
                _LOGGER.warning("⚠️ No backup file available")
                return False
            
            _LOGGER.info("🔄 Attempting to load from backup")
            data = await self._read_json_file(self.backup_file)
            
            # Restore from backup
            devices_data = data.get('devices', {})
            for serial, device_dict in devices_data.items():
                device = ManagedDevice.from_dict(device_dict)
                self._devices[serial] = device
                
                # Update rx11 index mappings from both old and new format
                rx11_idx = None
                if device.indices and 'rx11' in device.indices:
                    rx11_idx = device.indices['rx11']
                elif device.rx11_index is not None:
                    rx11_idx = device.rx11_index
                    
                if rx11_idx is not None:
                    self._rx11_index_to_serial[rx11_idx] = serial
            
            _LOGGER.info("✅ Restored %d devices from backup", len(self._devices))
            return True
            
        except Exception as e:
            _LOGGER.error("❌ Failed to load backup: %s", e)
            return False
    
    async def save(self) -> bool:
        """Save all devices to persistent storage.
        
        Returns:
            True if saved successfully, False otherwise
        """
        async with self._lock:
            try:
                # Create backup of current file
                if self.devices_file.exists():
                    await self._copy_file(self.devices_file, self.backup_file)
                
                # Build index allocation summary
                index_allocations = {}
                for device in self._devices.values():
                    if device.indices:
                        for index_type, index_value in device.indices.items():
                            if index_type not in index_allocations:
                                index_allocations[index_type] = []
                            if index_value is not None:
                                index_allocations[index_type].append(index_value)
                
                # Prepare data structure (v3.0 with unified indices)
                data = {
                    "version": "3.0",
                    "created_at": datetime.now().isoformat(),
                    "config_entry_id": self.config_entry_id,
                    "device_count": len(self._devices),
                    "index_allocations": index_allocations,  # New in v3.0: allocation summary
                    "devices": {
                        serial: device.to_dict()
                        for serial, device in self._devices.items()
                    }
                }
                
                # Write to file
                await self._write_json_file(self.devices_file, data)
                
                _LOGGER.info("💾 Saved %d devices to persistent storage", len(self._devices))
                return True
                
            except Exception as e:
                _LOGGER.error("❌ Failed to save devices: %s", e)
                return False
    
    def add_device(
        self,
        serial_number: str,
        device_type: str,
        name: Optional[str] = None,
        rx11_index: Optional[int] = None,
        indices: Optional[Dict[str, int]] = None,
        **kwargs
    ) -> ManagedDevice:
        """Add a new device to the whitelist.
        
        If device already exists, updates its information.
        This is the ONLY way to add devices - all devices must be whitelisted.
        
        Args:
            serial_number: Unique serial number
            device_type: Device type string
            name: Human-readable name (auto-generated if None)
            rx11_index: Optional RX11 index for receivers (DEPRECATED - use indices instead)
            indices: Optional dict of index allocations {'ewb': 5, 'ew_receiver': 3, 'rx11': 42}
            **kwargs: Additional device attributes
            
        Returns:
            The created or updated ManagedDevice
        """
        # Normalize indices: merge rx11_index into indices dict
        if indices is None:
            indices = {}
        if isinstance(indices, dict):
            indices = dict(indices)  # Make a copy
        
        # Backwards compatibility: rx11_index parameter overrides indices['rx11']
        if rx11_index is not None:
            indices['rx11'] = rx11_index
        
        # Check if device already exists
        if serial_number in self._devices:
            _LOGGER.info("🔄 Updating existing device: %s", serial_number[-8:])
            device = self._devices[serial_number]
            device.device_type = device_type
            if name:
                device.name = name
            
            # Update indices
            if indices:
                device.indices = indices
                device.index_allocation_date = datetime.now().isoformat()
                
                # Update rx11 mapping
                if 'rx11' in indices and indices['rx11'] is not None:
                    # Remove old mapping
                    if device.rx11_index is not None and device.rx11_index in self._rx11_index_to_serial:
                        del self._rx11_index_to_serial[device.rx11_index]
                    # Add new mapping
                    self._rx11_index_to_serial[indices['rx11']] = serial_number
            
            device.updated_at = datetime.now().isoformat()
        else:
            # Create new device
            _LOGGER.info("➕ Adding new device to whitelist: %s", serial_number[-8:])
            device = ManagedDevice(
                serial_number=serial_number,
                device_type=device_type,
                name=name or f"{device_type}_{serial_number[-8:]}",
                rx11_index=indices.get('rx11') if indices else None,
                indices=indices if indices else None,
                created_at=datetime.now().isoformat(),
                config_entry_id=self.config_entry_id,
                **kwargs
            )
            self._devices[serial_number] = device
            
            # Update rx11 index mapping
            if indices and 'rx11' in indices and indices['rx11'] is not None:
                self._rx11_index_to_serial[indices['rx11']] = serial_number
        
        # Mark as available by default when added
        device.mark_available()
        
        return device
    
    def remove_device(self, serial_number: str) -> bool:
        """Remove a device from the whitelist.
        
        Note: This is a simple whitelist removal.  For complete cleanup including
        entity registry and device registry cleanup, use coordinator.async_remove_device().
        
        Args:
            serial_number: Serial number of device to remove
            
        Returns:
            True if device was removed, False if not found
        """
        if serial_number not in self._devices:
            _LOGGER.debug("Device %s not in whitelist (already removed or never added)", serial_number[-8:])
            return False
        
        device = self._devices[serial_number]
        
        # Clean up index mapping
        if device.rx11_index is not None and device.rx11_index in self._rx11_index_to_serial:
            del self._rx11_index_to_serial[device.rx11_index]
        
        # Remove device
        del self._devices[serial_number]
        
        _LOGGER.info("🗑️ Removed device from whitelist: %s", serial_number[-8:])
        return True
    
    def get_device(self, serial_number: str) -> Optional[ManagedDevice]:
        """Get device by serial number.
        
        Args:
            serial_number: Device serial number
            
        Returns:
            ManagedDevice if found, None otherwise
        """
        return self._devices.get(serial_number)
    
    def get_device_by_rx11_index(self, rx11_index: int) -> Optional[ManagedDevice]:
        """Get device by RX11 index.
        
        Args:
            rx11_index: RX11 index (0-255)
            
        Returns:
            ManagedDevice if found, None otherwise
        """
        serial = self._rx11_index_to_serial.get(rx11_index)
        if serial:
            return self._devices.get(serial)
        return None
    
    def is_whitelisted(self, serial_number: str) -> bool:
        """Check if a device is in the whitelist.
        
        Args:
            serial_number: Device serial number
            
        Returns:
            True if device is whitelisted, False otherwise
        """
        return serial_number in self._devices
    
    def set_device_availability(self, serial_number: str, available: bool) -> bool:
        """Setzt die Verfügbarkeit eines Geräts.
        
        Args:
            serial_number: Seriennummer des Geräts
            available: True = verfügbar, False = nicht verfügbar
            
        Returns:
            True wenn gesetzt, False wenn Gerät nicht im Whitelist
        """
        device = self._devices.get(serial_number)
        if not device:
            if available:
                _LOGGER.warning("⚠️ Kann Verfügbarkeit nicht setzen: Gerät %s nicht im Whitelist", 
                              serial_number[-8:])
            else:
                _LOGGER.debug("🔍 Gerät %s not in whitelist, already unavailable", 
                             serial_number[-8:])
            return False
        
        if available:
            device.mark_available()
            _LOGGER.debug("✅ Gerät %s verfügbar", serial_number[-8:])
        else:
            device.mark_unavailable()
            _LOGGER.debug("❌ Gerät %s nicht verfügbar", serial_number[-8:])
        
        return True
    
    def get_devices_by_availability(self, available: Optional[bool] = None) -> Dict[str, ManagedDevice]:
        """Holt Geräte nach Verfügbarkeitsstatus.
        
        Args:
            available: True=verfügbar, False=nicht verfügbar, None=alle
            
        Returns:
            Dictionary mit gefilterten Geräten
        """
        if available is None:
            return self._devices.copy()
        
        target_state = DeviceAvailability.AVAILABLE if available else DeviceAvailability.UNAVAILABLE
        return {
            serial: device
            for serial, device in self._devices.items()
            if device.availability == target_state
        }
    
    def get_all_devices(self) -> Dict[str, ManagedDevice]:
        """Holt alle verwalteten Geräte.
        
        Returns:
            Dictionary mit allen Geräten
        """
        return self._devices.copy()
    
    def update_device_info(self, serial_number: str, **kwargs) -> bool:
        """Update device information.
        
        Args:
            serial_number: Device serial number
            **kwargs: Fields to update
            
        Returns:
            True if updated, False if device not found
        """
        device = self._devices.get(serial_number)
        if not device:
            _LOGGER.warning("⚠️ Cannot update: device %s not in whitelist", 
                          serial_number[-8:])
            return False
        
        # Update allowed fields
        for key, value in kwargs.items():
            if hasattr(device, key):
                setattr(device, key, value)
        
        device.updated_at = datetime.now().isoformat()
        _LOGGER.debug("📝 Updated device %s", serial_number[-8:])
        return True
    
    def sync_with_discovered_devices(self, discovered_serials: Set[str]) -> None:
        """Synchronize device availability based on discovered devices.
        
        - Devices in whitelist AND discovered -> mark available
        - Devices in whitelist but NOT discovered -> mark unavailable
        - Devices discovered but NOT in whitelist -> ignored (not managed)
        
        Args:
            discovered_serials: Set of serial numbers that were discovered
        """
        whitelisted_serials = set(self._devices.keys())
        
        # Mark whitelisted devices that were found as available
        available_count = 0
        for serial in whitelisted_serials & discovered_serials:
            self.set_device_availability(serial, True)
            available_count += 1
        
        # Mark whitelisted devices that were NOT found as unavailable
        unavailable_count = 0
        for serial in whitelisted_serials - discovered_serials:
            self.set_device_availability(serial, False)
            unavailable_count += 1
        
        # Log devices that were discovered but not whitelisted
        not_whitelisted = discovered_serials - whitelisted_serials
        if not_whitelisted:
            _LOGGER.info("🔍 Found %d devices not in whitelist (will be ignored): %s",
                        len(not_whitelisted),
                        [s[-8:] for s in list(not_whitelisted)[:5]])
        
        _LOGGER.info("🔄 Sync complete: %d available, %d unavailable, %d not whitelisted",
                    available_count, unavailable_count, len(not_whitelisted))
    
    def allocate_rx11_index(self) -> Optional[int]:
        """Allocate next available RX11 index (0-255).
        
        Returns:
            Next available index or None if all allocated
        """
        used_indices = set(self._rx11_index_to_serial.keys())
        for idx in range(256):
            if idx not in used_indices:
                return idx
        _LOGGER.error("❌ No free RX11 indices available (all 256 used)")
        return None
    
    def get_device_count(self) -> Dict[str, int]:
        """Get device count statistics.
        
        Returns:
            Dictionary with counts by availability
        """
        total = len(self._devices)
        available = sum(1 for d in self._devices.values() 
                       if d.availability == DeviceAvailability.AVAILABLE)
        unavailable = sum(1 for d in self._devices.values() 
                         if d.availability == DeviceAvailability.UNAVAILABLE)
        unknown = sum(1 for d in self._devices.values() 
                     if d.availability == DeviceAvailability.UNKNOWN)
        
        return {
            "total": total,
            "available": available,
            "unavailable": unavailable,
            "unknown": unknown
        }
    
    # File I/O helpers
    
    async def _read_json_file(self, file_path: Path) -> Dict[str, Any]:
        """Read JSON file asynchronously."""
        def _read():
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return await asyncio.get_event_loop().run_in_executor(None, _read)
    
    async def _write_json_file(self, file_path: Path, data: Dict[str, Any]) -> None:
        """Write JSON file asynchronously."""
        def _write():
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        await asyncio.get_event_loop().run_in_executor(None, _write)
    
    async def _copy_file(self, src: Path, dst: Path) -> None:
        """Copy file asynchronously."""
        def _copy():
            import shutil
            shutil.copy2(src, dst)
        await asyncio.get_event_loop().run_in_executor(None, _copy)
    
    async def _migrate_version(self, data: Dict[str, Any], from_version: str) -> Dict[str, Any]:
        """Migrate data from older version to current version.
        
        This ensures backward compatibility when loading old device files.
        
        Args:
            data: Device data in old format
            from_version: Version string of the old format
            
        Returns:
            Migrated data in current format (version 2.0)
        """
        _LOGGER.info("🔄 Starting migration from version %s to 2.0", from_version)
        
        # Version 1.0 -> 2.0 migration
        if from_version == '1.0':
            # Add device_count if missing
            if 'device_count' not in data:
                data['device_count'] = len(data.get('devices', {}))
            
            # Ensure all devices have required fields
            devices = data.get('devices', {})
            for serial, device in devices.items():
                # Add availability if missing (default to UNKNOWN)
                if 'availability' not in device:
                    device['availability'] = DeviceAvailability.UNKNOWN.value
                
                # Ensure extra_data exists
                if 'extra_data' not in device:
                    device['extra_data'] = None
            
            # Update version
            data['version'] = '2.0'
            
            _LOGGER.info("✅ Successfully migrated from version 1.0 to 2.0")
        
        # Future migrations can be added here
        # elif from_version == '2.0' and target_version == '3.0':
        #     # Migration logic for 2.0 -> 3.0
        #     pass
        
        return data
    
    async def add_device_from_fallback(
        self,
        serial_number: str,
        fallback_config: Dict[str, Any]
    ) -> bool:
        """Add a device from fallback configuration (created from old entities).
        
        This is used when entity migration detects incompatible unique_ids
        and needs to recreate the device.
        
        Args:
            serial_number: Device serial number
            fallback_config: Configuration extracted from old entities
            
        Returns:
            True if device was added successfully
        """
        try:
            device_type = fallback_config.get("type", "unknown")
            device_name = fallback_config.get("name", f"Device {serial_number[-8:]}")
            
            _LOGGER.info("📦 Adding device from fallback config: %s (type=%s)",
                        device_name, device_type)
            
            # Create device configuration
            device_config = {
                "serial_number": serial_number,
                "name": device_name,
                "type": device_type,
                "recreated_from_fallback": True,
                "fallback_timestamp": fallback_config.get("recreated_at"),
            }
            
            # Copy relevant fields from fallback config
            for field in ["area", "button_count", "channels", "sensor_types"]:
                if field in fallback_config:
                    device_config[field] = fallback_config[field]
            
            # Add to DeviceManager
            success = await self.add_device(
                serial_number=serial_number,
                device_type=device_type,
                name=device_name,
                configuration=device_config
            )
            
            if success:
                _LOGGER.info("✅ Device %s added from fallback configuration",
                           serial_number[-8:])
            else:
                _LOGGER.warning("⚠️ Failed to add device %s from fallback",
                              serial_number[-8:])
            
            return success
            
        except Exception as e:
            _LOGGER.error("❌ Error adding device from fallback: %s", e, exc_info=True)
            return False
