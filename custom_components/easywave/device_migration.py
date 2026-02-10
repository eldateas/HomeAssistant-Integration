"""Migration utilities for transitioning to unified DeviceManager.

This module provides helpers to migrate from the old system
(DeviceWhitelist, DeviceRegistry, DeviceStorage) to the new
unified DeviceManager system.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Any, Set

from homeassistant.core import HomeAssistant

from .device_manager import DeviceManager, ManagedDevice, DeviceAvailability
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def migrate_to_device_manager(
    hass: HomeAssistant,
    config_entry_id: str
) -> DeviceManager:
    """Migrate from old device management system to new DeviceManager.
    
    This function:
    1. Creates a new DeviceManager instance
    2. Migrates devices from old whitelist/registry/storage files
    3. Consolidates all device information
    4. Saves the new unified format
    
    Args:
        hass: Home Assistant instance
        config_entry_id: Config entry ID
        
    Returns:
        Initialized DeviceManager with migrated devices
    """
    _LOGGER.info("🔄 Starting migration to unified DeviceManager")
    
    # Create new DeviceManager
    device_manager = DeviceManager(hass, config_entry_id)
    
    # Attempt to load existing data
    await device_manager.load()
    
    # If already has devices, no migration needed
    if len(device_manager.get_all_devices()) > 0:
        _LOGGER.info("✅ DeviceManager already initialized with %d devices",
                    len(device_manager.get_all_devices()))
        return device_manager
    
    _LOGGER.info("📦 No devices in DeviceManager, attempting migration from old systems")
    
    config_dir = Path(hass.config.config_dir) / DOMAIN
    migrated_count = 0
    
    # Migrate from device_whitelist.json
    migrated_count += await _migrate_from_whitelist(device_manager, config_dir)
    
    # Migrate from eldat_device_registry.json
    migrated_count += await _migrate_from_registry(device_manager, config_dir)
    
    # Migrate from eldat_devices.json
    migrated_count += await _migrate_from_storage(device_manager, config_dir)
    
    # Save migrated data
    if migrated_count > 0:
        _LOGGER.info("💾 Saving %d migrated devices", migrated_count)
        await device_manager.save()
        _LOGGER.info("✅ Migration completed successfully: %d devices migrated", migrated_count)
        
        # Clean up old files after successful migration
        await _cleanup_old_files(config_dir)
    else:
        _LOGGER.info("ℹ️ No devices found to migrate")
    
    return device_manager


async def _cleanup_old_files(config_dir: Path) -> None:
    """Clean up old configuration files after successful migration.
    
    Args:
        config_dir: Configuration directory path
    """
    import os
    
    old_files = [
        "device_whitelist.json",
        "eldat_device_registry.json",
        "eldat_devices.json",
        "used_ewb_indices.json",  # Now integrated in registered_devices.json
        "used_ew_receiver_indices.json",  # Now integrated in registered_devices.json
    ]
    
    for filename in old_files:
        file_path = config_dir / filename
        if file_path.exists():
            try:
                # Create backup first
                backup_path = config_dir / f"{filename}.migrated_backup"
                if not backup_path.exists():
                    os.rename(str(file_path), str(backup_path))
                    _LOGGER.info("📦 Archived old file: %s -> %s", filename, backup_path.name)
                else:
                    # Backup already exists, just delete the original
                    os.remove(str(file_path))
                    _LOGGER.info("🗑️ Removed old file: %s (backup already exists)", filename)
            except Exception as e:
                _LOGGER.warning("⚠️ Could not clean up old file %s: %s", filename, e)


async def _migrate_from_whitelist(
    device_manager: DeviceManager,
    config_dir: Path
) -> int:
    """Migrate devices from device_whitelist.json.
    
    Args:
        device_manager: DeviceManager instance
        config_dir: Configuration directory path
        
    Returns:
        Number of devices migrated
    """
    whitelist_file = config_dir / "device_whitelist.json"
    
    if not whitelist_file.exists():
        _LOGGER.debug("No device_whitelist.json found")
        return 0
    
    try:
        _LOGGER.info("📋 Migrating from device_whitelist.json")
        
        with open(whitelist_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        devices_data = data.get('devices', {})
        count = 0
        
        for serial, device_info in devices_data.items():
            # Extract device information
            device_type = device_info.get('device_type', 'unknown')
            name = device_info.get('name', f"{device_type}_{serial[-8:]}")
            rx11_index = device_info.get('rx11_index')
            area = device_info.get('area')
            
            # Add to new manager
            device = device_manager.add_device(
                serial_number=serial,
                device_type=device_type,
                name=name,
                rx11_index=rx11_index,
                area=area
            )
            
            # Copy additional metadata
            if 'added_at' in device_info:
                device.created_at = device_info['added_at']
            if 'last_updated' in device_info:
                device.updated_at = device_info['last_updated']
            
            count += 1
            _LOGGER.debug("✅ Migrated from whitelist: %s", serial[-8:])
        
        _LOGGER.info("✅ Migrated %d devices from whitelist", count)
        
        # Rename old file to indicate it was migrated
        if count > 0:
            migrated_file = config_dir / "device_whitelist.json.migrated"
            whitelist_file.rename(migrated_file)
            _LOGGER.info("📁 Renamed whitelist file to .migrated")
        
        return count
        
    except Exception as e:
        _LOGGER.error("❌ Failed to migrate from whitelist: %s", e)
        return 0


async def _migrate_from_registry(
    device_manager: DeviceManager,
    config_dir: Path
) -> int:
    """Migrate devices from eldat_device_registry.json.
    
    Args:
        device_manager: DeviceManager instance
        config_dir: Configuration directory path
        
    Returns:
        Number of devices migrated
    """
    registry_file = config_dir / "eldat_device_registry.json"
    
    if not registry_file.exists():
        _LOGGER.debug("No eldat_device_registry.json found")
        return 0
    
    try:
        _LOGGER.info("📋 Migrating from eldat_device_registry.json")
        
        with open(registry_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        devices_data = data.get('devices', {})
        count = 0
        
        for device_id_str, device_entry in devices_data.items():
            # Extract serial number from entry
            serial_info = device_entry.get('serial_number', {})
            receiver_transmitter = serial_info.get('receiver_transmitter', '')
            
            if not receiver_transmitter:
                _LOGGER.warning("⚠️ Device %s has no serial number, skipping", device_id_str)
                continue
            
            serial = receiver_transmitter
            
            # Skip if already exists
            if device_manager.is_whitelisted(serial):
                _LOGGER.debug("⏭️ Device %s already migrated, skipping", serial[-8:])
                continue
            
            # Extract device information
            info = device_entry.get('information', {})
            device_type_code = info.get('device_type', 0)
            rx11_index = info.get('index')
            
            # Map device type code to string
            device_type_map = {
                1: 'ew_transmitter',
                2: 'ew_receiver',
                3: 'ewneo_sensor',
                4: 'ewneo_aktor',
                5: 'ew_sensor',
                6: 'ewb_transceiver',
                7: 'sec_transmitter',
                8: 'sec_receiver',
            }
            device_type = device_type_map.get(device_type_code, 'unknown')
            
            name = device_entry.get('name', f"{device_type}_{serial[-8:]}")
            area = device_entry.get('area')
            
            # Add to new manager
            device = device_manager.add_device(
                serial_number=serial,
                device_type=device_type,
                name=name,
                rx11_index=rx11_index,
                area=area
            )
            
            # Copy additional metadata
            if 'created_at' in device_entry:
                device.created_at = device_entry['created_at']
            if 'last_seen' in device_entry:
                device.last_seen = device_entry['last_seen']
            if 'battery_level' in device_entry:
                device.battery_level = device_entry['battery_level']
            if 'signal_strength' in device_entry:
                device.signal_strength = device_entry['signal_strength']
            
            count += 1
            _LOGGER.debug("✅ Migrated from registry: %s", serial[-8:])
        
        _LOGGER.info("✅ Migrated %d devices from registry", count)
        
        # Rename old file to indicate it was migrated
        if count > 0:
            migrated_file = config_dir / "eldat_device_registry.json.migrated"
            registry_file.rename(migrated_file)
            _LOGGER.info("📁 Renamed registry file to .migrated")
        
        return count
        
    except Exception as e:
        _LOGGER.error("❌ Failed to migrate from registry: %s", e)
        return 0


async def _migrate_from_storage(
    device_manager: DeviceManager,
    config_dir: Path
) -> int:
    """Migrate devices from eldat_devices.json.
    
    Args:
        device_manager: DeviceManager instance
        config_dir: Configuration directory path
        
    Returns:
        Number of devices migrated
    """
    storage_file = config_dir / "eldat_devices.json"
    
    if not storage_file.exists():
        _LOGGER.debug("No eldat_devices.json found")
        return 0
    
    try:
        _LOGGER.info("📋 Migrating from eldat_devices.json")
        
        with open(storage_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        devices_data = data.get('devices', {})
        count = 0
        
        for serial, device_info in devices_data.items():
            # Skip if already exists
            if device_manager.is_whitelisted(serial):
                _LOGGER.debug("⏭️ Device %s already migrated, skipping", serial[-8:])
                continue
            
            # Extract device information
            device_type = device_info.get('device_type', 'unknown')
            name = device_info.get('name', f"{device_type}_{serial[-8:]}")
            rx11_index = device_info.get('rx11_index')
            area = device_info.get('area')
            
            # Add to new manager
            device = device_manager.add_device(
                serial_number=serial,
                device_type=device_type,
                name=name,
                rx11_index=rx11_index,
                area=area
            )
            
            # Copy additional metadata
            if 'created_at' in device_info:
                device.created_at = device_info['created_at']
            if 'last_updated' in device_info:
                device.updated_at = device_info['last_updated']
            if 'battery_level' in device_info:
                device.battery_level = device_info['battery_level']
            
            # Handle availability
            if 'available' in device_info:
                if device_info['available']:
                    device.mark_available()
                else:
                    device.mark_unavailable()
            
            count += 1
            _LOGGER.debug("✅ Migrated from storage: %s", serial[-8:])
        
        _LOGGER.info("✅ Migrated %d devices from storage", count)
        
        # Rename old file to indicate it was migrated
        if count > 0:
            migrated_file = config_dir / "eldat_devices.json.migrated"
            storage_file.rename(migrated_file)
            _LOGGER.info("📁 Renamed storage file to .migrated")
        
        return count
        
    except Exception as e:
        _LOGGER.error("❌ Failed to migrate from storage: %s", e)
        return 0


def create_compatibility_wrapper(device_manager: DeviceManager):
    """Create compatibility wrappers for old API.
    
    This allows existing code to continue working while using the new
    DeviceManager underneath.
    
    Args:
        device_manager: DeviceManager instance
        
    Returns:
        Object with compatibility methods
    """
    class CompatibilityWrapper:
        """Wrapper to maintain backward compatibility."""
        
        def __init__(self, manager: DeviceManager):
            self.manager = manager
        
        # DeviceWhitelist compatibility
        async def save_device_whitelist(self, whitelist: Dict[str, Dict[str, Any]]) -> bool:
            """Compatibility: save_device_whitelist."""
            for serial, info in whitelist.items():
                self.manager.add_device(
                    serial_number=serial,
                    device_type=info.get('device_type', 'unknown'),
                    name=info.get('name'),
                    rx11_index=info.get('rx11_index'),
                    area=info.get('area')
                )
            return await self.manager.save()
        
        async def load_device_whitelist(self) -> Dict[str, Dict[str, Any]]:
            """Compatibility: load_device_whitelist."""
            await self.manager.load()
            result = {}
            for serial, device in self.manager.get_all_devices().items():
                result[serial] = {
                    'serial_number': device.serial_number,
                    'device_type': device.device_type,
                    'name': device.name,
                    'rx11_index': device.rx11_index,
                    'area': device.area,
                    'added_at': device.created_at,
                    'last_updated': device.updated_at,
                }
            return result
        
        async def is_device_whitelisted(self, serial_number: str) -> bool:
            """Compatibility: is_device_whitelisted."""
            return self.manager.is_whitelisted(serial_number)
        
        # DeviceStorage compatibility
        async def load_devices(self) -> Dict[str, Any]:
            """Compatibility: load_devices - returns flat dict with all device info including extra_data."""
            await self.manager.load()
            result = {}
            for serial, device in self.manager.get_all_devices().items():
                # Build device info with all attributes
                device_info = {
                    'serial_number': device.serial_number,
                    'device_type': device.device_type,
                    'name': device.name,
                    'available': device.availability == DeviceAvailability.AVAILABLE,
                    'rx11_index': device.rx11_index,
                    'area': device.area,
                }
                # Add extra_data fields (gateway_serial, ewneo_index, entities, platforms, etc.)
                if hasattr(device, 'extra_data') and device.extra_data:
                    device_info.update(device.extra_data)
                result[serial] = device_info
            return result
        
        async def save_device_config(self, devices: Dict[str, Dict[str, Any]], force: bool = False) -> bool:
            """Compatibility: save_device_config - stores all fields in extra_data."""
            # Define core fields that map to ManagedDevice attributes
            core_fields = {'serial_number', 'device_type', 'name', 'rx11_index', 'area'}
            
            for serial, info in devices.items():
                # Collect extra fields (everything not in core_fields)
                # Convert sets to lists for JSON serialization
                extra_data = {}
                for k, v in info.items():
                    if k not in core_fields:
                        if isinstance(v, set):
                            extra_data[k] = list(v)
                        else:
                            extra_data[k] = v
                
                if self.manager.is_whitelisted(serial):
                    # Update existing device with core fields
                    core_data = {k: v for k, v in info.items() if k in core_fields and k != 'serial_number'}
                    self.manager.update_device_info(serial, **core_data)
                    # Store extra fields in extra_data
                    if extra_data:
                        self.manager.update_device_info(serial, extra_data=extra_data)
                else:
                    # Extract core fields for device creation
                    device_type = info.get('device_type', 'unknown')
                    name = info.get('name')
                    rx11_index = info.get('rx11_index')
                    area = info.get('area')
                    
                    # Add device with core parameters and extra_data
                    self.manager.add_device(
                        serial_number=serial,
                        device_type=device_type,
                        name=name,
                        rx11_index=rx11_index,
                        area=area,
                        extra_data=extra_data if extra_data else None
                    )
            
            return await self.manager.save()
    
    return CompatibilityWrapper(device_manager)
