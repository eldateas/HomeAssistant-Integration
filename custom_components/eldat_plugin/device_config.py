"""Device configuration management for ELDAT integration."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path
import re

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    DEVICES_CONFIG_FILE,
    DEVICES_BACKUP_FILE,
    CONFIG_VERSION,
    DEVICE_TYPES,
    SERIAL_NUMBER_LENGTH,
)

_LOGGER = logging.getLogger(__name__)


class DeviceConfigManager:
    """Manages device configuration persistence and backup with enhanced serial number handling."""

    def __init__(self, hass: HomeAssistant, config_entry_id: str) -> None:
        """Initialize device config manager."""
        self.hass = hass
        self.config_entry_id = config_entry_id
        # Store in main config directory for better persistence
        self.config_dir = Path(hass.config.config_dir) / DOMAIN
        self.config_file = self.config_dir / "eldat_devices.json"
        self.backup_dir = self.config_dir / "backups"
        self.removed_devices_file = self.config_dir / "removed_devices.json"  # Legacy blacklist (deprecated)
        self.whitelist_file = self.config_dir / "device_whitelist.json"  # NEW: Whitelist of registered devices
        
        # Ensure directories exist
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Device type detection patterns based on serial number
        self._serial_type_patterns = {
            # 16-byte (32 hex chars) serial numbers - all EasyWave devices
            32: ["ew_transmitter", "ew_sensor", "ewb_transceiver", "ewneo_transceiver", "ew_receiver"],
            # 8-byte (16 hex chars) serial numbers - SecWave devices only
            16: ["sec_transmitter", "sec_receiver"],
        }
        
    def validate_serial_number(self, serial_number: str) -> bool:
        """Validate serial number format.
        
        - 16 hex chars (8 bytes): SecWave devices
        - 32 hex chars (16 bytes): EasyWave devices (EW-Transmitter, EW-Sensor, EWB, EWneo)
        - MANUAL_XXXXXXXXXX: Manually added devices
        """
        if not serial_number:
            return False
        # Allow both 16 and 32 character hex strings, plus MANUAL devices
        return bool(re.match(r'^[0-9A-Fa-f]{16}$|^[0-9A-Fa-f]{32}$|^MANUAL_\d+$', serial_number))
    
    def detect_device_type_from_serial(self, serial_number: str, telegram_type: Optional[int] = None) -> str:
        """Detect likely device type based on serial number length and telegram type.
        
        Serial length mapping:
        - 32 hex (16 bytes): EasyWave devices (ew_transmitter, ew_sensor, ewb, ewneo)
        - 16 hex (8 bytes): SecWave devices (sec_transmitter, sec_receiver)
        """
        serial_len = len(serial_number)
        
        # Use serial length as primary indicator
        if serial_len == 32:  # 16-byte serial (all EasyWave devices)
            if telegram_type == 0x81:
                return "ew_sensor"
            elif telegram_type and 0x80 <= telegram_type <= 0x8F:
                return "ewneo_transceiver"
            elif telegram_type and 0x10 <= telegram_type <= 0x13:
                return "ew_transmitter"
            else:
                return "ewb_transceiver"
        elif serial_len == 16:  # 8-byte serial (SecWave devices only)
            if telegram_type and 0x20 <= telegram_type <= 0x2F:
                return "sec_transmitter"
            else:
                return "sec_receiver"
        
        return "unknown"
    
    def assign_rx11_index(self, serial_number: str, device_type: str, existing_devices: Dict[str, Dict[str, Any]]) -> Optional[int]:
        """Auto-assign next available RX11 index for devices that need it."""
        # Only certain device types use RX11 indices
        rx11_device_types = ["ew_receiver", "sec_receiver", "ewneo_receiver", "ewneo_switch", "ewneo_dimmer", "ewneo_motor"]
        
        if device_type not in rx11_device_types:
            return None
        
        # Find next available index (0-255)
        used_indices = set()
        for device_info in existing_devices.values():
            idx = device_info.get("rx11_index")
            if idx is not None:
                used_indices.add(idx)
        
        # Find first available index
        for i in range(256):
            if i not in used_indices:
                _LOGGER.debug("Assigned RX11 index %d to device %s", i, serial_number)
                return i
        
        _LOGGER.warning("No available RX11 indices for device %s", serial_number)
        return None

    async def load_devices(self) -> Dict[str, Any]:
        """Load devices from configuration file asynchronously."""
        try:
            if self.config_file.exists():
                # Use asyncio to run the file operation in a thread pool
                def _load_file():
                    with open(self.config_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                
                config = await asyncio.get_event_loop().run_in_executor(None, _load_file)
                _LOGGER.info(f"Loaded configuration with {len(config.get('devices', {}))} devices")
                return config.get('devices', {})
            else:
                _LOGGER.info("No existing configuration file found, returning empty devices")
                return {}
        except Exception as e:
            _LOGGER.error(f"Failed to load device configuration: {e}")
            return {}

    async def save_device_config(self, devices: Dict[str, Dict[str, Any]], force: bool = False) -> bool:
        """Save device configuration to persistent JSON file only if changed.
        
        Args:
            devices: Current device dictionary
            force: If True, always save regardless of changes
            
        Returns:
            True if saved, False if no changes or error
        """
        try:
            config_data = {
                "version": CONFIG_VERSION,
                "last_updated": datetime.now().isoformat(),
                "config_entry_id": self.config_entry_id,
                "device_count": len(devices),
                "devices": {}
            }
            
            device_registry = dr.async_get(self.hass)
            
            for serial_number, device_info in devices.items():
                # Validate serial number
                if not self.validate_serial_number(serial_number):
                    _LOGGER.warning("Invalid serial number format: %s", serial_number)
                    continue
                
                # Get Home Assistant device reference
                ha_device = device_registry.async_get_device({(DOMAIN, serial_number)})
                
                # Determine RX11 index if applicable
                rx11_index = device_info.get("rx11_index")
                if rx11_index is None:
                    device_type = device_info.get("type", "unknown")
                    rx11_index = self.assign_rx11_index(serial_number, device_type, devices)
                
                # Enhanced device configuration with all metadata
                device_config = {
                    # Core identification
                    "serial_number": serial_number,
                    "serial_length": len(serial_number),
                    "device_type": device_info.get("type", "unknown"),
                    "device_type_code": device_info.get("device_type_code", 0x00),
                    "name": device_info.get("name", f"ELDAT Device {serial_number[-6:]}"),
                    
                    # Device properties
                    "rx11_index": rx11_index,
                    "channels": device_info.get("channels", 1),
                    "info_type": device_info.get("info_type"),
                    "telegram_type": device_info.get("telegram_type"),
                    
                    # Capabilities
                    "button_count": device_info.get("button_count"),
                    "supports_buttons": device_info.get("supports_buttons", device_info.get("type") == "ew_receiver"),  # EW-Receiver brauchen Button-Support
                    "supports_sensors": device_info.get("supports_sensors", False),
                    "supports_feedback": device_info.get("supports_feedback", False),
                    "bidirectional": device_info.get("bidirectional", False),
                    "neo_device": device_info.get("neo_device", False),
                    "sensor_types": device_info.get("sensor_types", []),
                    "measurement_types": device_info.get("measurement_types", []),
                    "available_sensors": device_info.get("available_sensors", []),  # CRITICAL FIX: Save available_sensors
                    
                    # Discovery metadata
                    "added_manually": device_info.get("added_manually", False),
                    "discovered": device_info.get("discovered", False),
                    "detected_via": device_info.get("detected_via", "unknown"),
                    "first_seen": self._format_timestamp(device_info.get("first_seen", device_info.get("timestamp"))),
                    "last_seen": self._format_timestamp(device_info.get("last_seen")),
                    
                    # Status
                    "battery_level": device_info.get("battery_level"),
                    "signal_strength": device_info.get("signal_strength"),
                    
                    # Last sensor values (for persistence after restart)
                    "last_temperature": device_info.get("last_temperature"),
                    "last_humidity": device_info.get("last_humidity"),
                    
                    # Entity configuration (CRITICAL FOR PRESERVING CONFIGURED ENTITIES)
                    "entities": device_info.get("entities", []),  # Configured entity specifications
                    "platforms": list(device_info.get("platforms", set())),  # Supported platforms
                    "receiver_kind": device_info.get("receiver_kind"),  # EW-Receiver specific
                    "operating_mode": device_info.get("operating_mode"),  # EW-Receiver specific
                    
                    # Home Assistant integration
                    "homeassistant_device_id": ha_device.id if ha_device else None,
                    "homeassistant_entities": self._get_device_entities(ha_device) if ha_device else [],
                }
                
                config_data["devices"][serial_number] = device_config
            
            # Check if data has actually changed (unless forced)
            if not force and self.config_file.exists():
                existing_data = await self._read_json_file(self.config_file)
                if existing_data and self._compare_device_data(existing_data.get("devices", {}), config_data["devices"]):
                    _LOGGER.debug("Device configuration unchanged, skipping save")
                    return False
            
            # Create backup before saving
            if self.config_file.exists():
                await self._create_backup()
            
            # Write config file atomically
            await self._write_json_file(self.config_file, config_data)
            
            _LOGGER.info("✅ Device configuration saved to %s: %d devices", 
                        self.config_file, len(devices))
            return True
            
        except Exception as e:
            _LOGGER.error("Error saving device configuration: %s", e)
            return False
    
    def _compare_device_data(self, old_devices: Dict, new_devices: Dict) -> bool:
        """Compare device data to detect changes, ignoring timestamps.
        
        Returns:
            True if data is the same (no changes), False if changed
        """
        if len(old_devices) != len(new_devices):
            return False
        
        for serial_number, new_device in new_devices.items():
            if serial_number not in old_devices:
                return False
            
            old_device = old_devices[serial_number]
            
            # Compare all fields except timestamps and last_updated
            for key, new_value in new_device.items():
                if key in ("last_seen", "first_seen", "last_updated"):
                    continue
                
                old_value = old_device.get(key)
                if new_value != old_value:
                    return False
        
        return True
    
    def _format_timestamp(self, timestamp: Any) -> Optional[str]:
        """Format timestamp to ISO format string."""
        if timestamp is None:
            return None
        if isinstance(timestamp, datetime):
            return timestamp.isoformat()
        if isinstance(timestamp, str):
            return timestamp
        return str(timestamp)

    async def load_device_config(self) -> Dict[str, Dict[str, Any]]:
        """Load device configuration from persistent JSON file with validation."""
        try:
            if not self.config_file.exists():
                _LOGGER.info("📁 No existing device configuration found at %s, starting fresh", 
                           self.config_file)
                return {}
            
            config_data = await self._read_json_file(self.config_file)
            
            if not config_data or "devices" not in config_data:
                _LOGGER.warning("Invalid device config file format")
                return {}
            
            # Validate version and migrate if needed
            version = config_data.get("version", "0.0")
            if version != CONFIG_VERSION:
                _LOGGER.warning("Device config version mismatch: %s vs %s - attempting migration", 
                              version, CONFIG_VERSION)
                config_data = await self._migrate_config(config_data, version)
            
            devices = {}
            invalid_devices = []
            
            for serial_number, device_config in config_data["devices"].items():
                # Validate serial number
                if not self.validate_serial_number(serial_number):
                    _LOGGER.warning("Invalid serial number in config: %s", serial_number)
                    invalid_devices.append(serial_number)
                    continue
                
                # Restore device info with all metadata
                device_info = {
                    # Core identification
                    "serial_number": serial_number,
                    "type": device_config.get("device_type", "unknown"),  # Important: map device_type to type for platform recognition
                    "device_type": device_config.get("device_type", "unknown"),  # Keep original name too
                    "device_type_code": device_config.get("device_type_code", 0x00),
                    "name": device_config.get("name", f"ELDAT Device {serial_number[-6:]}"),
                    
                    # Device properties
                    "rx11_index": device_config.get("rx11_index"),
                    "channels": device_config.get("channels", 1),
                    "info_type": device_config.get("info_type"),
                    "telegram_type": device_config.get("telegram_type"),
                    
                    # Capabilities
                    "button_count": device_config.get("button_count"),
                    "supports_buttons": device_config.get("supports_buttons", device_config.get("device_type") == "ew_receiver"),  # EW-Receiver brauchen Button-Support
                    "supports_sensors": device_config.get("supports_sensors", False),
                    "supports_feedback": device_config.get("supports_feedback", False),
                    "bidirectional": device_config.get("bidirectional", False),
                    "neo_device": device_config.get("neo_device", False),
                    "sensor_types": device_config.get("sensor_types", []),
                    "measurement_types": device_config.get("measurement_types", []),
                    "available_sensors": device_config.get("available_sensors", []),  # CRITICAL FIX: Restore available_sensors
                    
                    # Discovery metadata
                    "added_manually": device_config.get("added_manually", False),
                    "discovered": device_config.get("discovered", False),
                    "detected_via": device_config.get("detected_via", "config_file"),
                    
                    # Status
                    "battery_level": device_config.get("battery_level"),
                    "signal_strength": device_config.get("signal_strength"),
                    
                    # Last sensor values (restored after restart)
                    "last_temperature": device_config.get("last_temperature"),
                    "last_humidity": device_config.get("last_humidity"),
                    
                    # Entity configuration (CRITICAL FOR PRESERVING CONFIGURED ENTITIES)
                    "entities": device_config.get("entities", []),  # Configured entity specifications
                    "platforms": set(device_config.get("platforms", [])),  # Supported platforms
                    "receiver_kind": device_config.get("receiver_kind"),  # EW-Receiver specific
                    "operating_mode": device_config.get("operating_mode"),  # EW-Receiver specific
                    
                    # Home Assistant integration
                    "homeassistant_device_id": device_config.get("homeassistant_device_id"),
                    "homeassistant_entities": device_config.get("homeassistant_entities", []),
                    
                    # Mark as restored
                    "restored_from_config": True,
                }
                
                # Parse timestamps
                for ts_field in ["first_seen", "last_seen", "timestamp"]:
                    timestamp_str = device_config.get(ts_field)
                    if timestamp_str:
                        try:
                            device_info[ts_field] = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                        except (ValueError, AttributeError):
                            if ts_field == "first_seen":
                                device_info[ts_field] = datetime.now()
                
                # Ensure timestamp exists
                if "timestamp" not in device_info or not device_info["timestamp"]:
                    device_info["timestamp"] = device_info.get("first_seen", datetime.now())
                
                devices[serial_number] = device_info
            
            if invalid_devices:
                _LOGGER.warning("Skipped %d devices with invalid serial numbers: %s", 
                              len(invalid_devices), invalid_devices[:5])
            
            _LOGGER.info("✅ Device configuration loaded from %s: %d valid devices", 
                        self.config_file, len(devices))
            return devices
            
        except Exception as e:
            _LOGGER.error("Error loading device configuration: %s", e)
            return {}
    
    async def _migrate_config(self, config_data: Dict[str, Any], from_version: str) -> Dict[str, Any]:
        """Migrate configuration from older version to current version."""
        try:
            _LOGGER.info("Migrating device configuration from version %s to %s", 
                        from_version, CONFIG_VERSION)
            
            # Add migration logic here as needed
            # For now, just update the version
            config_data["version"] = CONFIG_VERSION
            config_data["migrated_from"] = from_version
            config_data["migration_date"] = datetime.now().isoformat()
            
            return config_data
            
        except Exception as e:
            _LOGGER.error("Error during config migration: %s", e)
            return config_data

    async def export_device_config(self, export_path: str) -> bool:
        """Export device configuration to specified path."""
        try:
            if not self.config_file.exists():
                _LOGGER.error("No device configuration to export")
                return False
            
            config_data = await self._read_json_file(self.config_file)
            if not config_data:
                return False
            
            # Add export metadata
            config_data["exported_at"] = datetime.now().isoformat()
            config_data["exported_by"] = "ELDAT Integration"
            
            export_file = Path(export_path)
            await self._write_json_file(export_file, config_data)
            
            _LOGGER.info("Device configuration exported to: %s", export_path)
            return True
            
        except Exception as e:
            _LOGGER.error("Error exporting device configuration: %s", e)
            return False

    async def import_device_config(self, import_path: str, merge: bool = True) -> Dict[str, Any]:
        """Import device configuration from specified path."""
        try:
            import_file = Path(import_path)
            if not import_file.exists():
                raise FileNotFoundError(f"Import file not found: {import_path}")
            
            imported_data = await self._read_json_file(import_file)
            if not imported_data or "devices" not in imported_data:
                raise ValueError("Invalid import file format")
            
            # Create backup before import
            if self.config_file.exists():
                await self._create_backup()
            
            if merge:
                # Merge with existing config
                existing_data = {}
                if self.config_file.exists():
                    existing_data = await self._read_json_file(self.config_file)
                
                if existing_data and "devices" in existing_data:
                    # Merge device lists
                    imported_data["devices"].update(existing_data["devices"])
            
            # Update metadata
            imported_data["version"] = CONFIG_VERSION
            imported_data["imported_at"] = datetime.now().isoformat()
            imported_data["config_entry_id"] = self.config_entry_id
            
            await self._write_json_file(self.config_file, imported_data)
            
            devices = imported_data["devices"]
            result = {
                "success": True,
                "devices_imported": len(devices),
                "devices": devices,
                "import_source": import_path,
                "merge_mode": merge,
            }
            
            _LOGGER.info("Device configuration imported: %d devices from %s", len(devices), import_path)
            return result
            
        except Exception as e:
            _LOGGER.error("Error importing device configuration: %s", e)
            return {
                "success": False,
                "error": str(e),
                "devices_imported": 0,
            }

    async def clear_device_config(self) -> bool:
        """Clear all device configuration (create backup first)."""
        try:
            # Create backup before clearing
            if self.config_file.exists():
                await self._create_backup()
                _LOGGER.info("Backup created before clearing device configuration")
            
            # Delete config file
            if self.config_file.exists():
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.config_file.unlink)
                _LOGGER.info("Device configuration file deleted: %s", self.config_file)
            
            return True
            
        except Exception as e:
            _LOGGER.error("Error clearing device configuration: %s", e)
            return False

    async def create_manual_backup(self) -> str:
        """Create a manual backup and return the backup path."""
        try:
            if not self.config_file.exists():
                raise FileNotFoundError("No device configuration to backup")
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.backup_dir / f"eldat_devices_backup_{timestamp}.json"
            
            config_data = await self._read_json_file(self.config_file)
            config_data["backup_created"] = datetime.now().isoformat()
            config_data["backup_type"] = "manual"
            
            await self._write_json_file(backup_path, config_data)
            
            _LOGGER.info("📦 Manual device backup created: %s", backup_path)
            return str(backup_path)
            
        except Exception as e:
            _LOGGER.error("Error creating manual backup: %s", e)
            raise

    async def save_removed_devices_list(self, removed_devices: List[str]) -> bool:
        """Save list of manually removed devices."""
        try:
            removed_data = {
                "version": "1.0",
                "last_updated": datetime.now().isoformat(),
                "removed_devices": removed_devices,
                "count": len(removed_devices)
            }
            
            await self._write_json_file(self.removed_devices_file, removed_data)
            _LOGGER.info("💾 Saved %d removed devices to blacklist", len(removed_devices))
            return True
            
        except Exception as e:
            _LOGGER.error("Error saving removed devices list: %s", e)
            return False

    async def load_removed_devices_list(self) -> List[str]:
        """Load list of manually removed devices."""
        try:
            if not self.removed_devices_file.exists():
                return []
            
            removed_data = await self._read_json_file(self.removed_devices_file)
            removed_devices = removed_data.get("removed_devices", [])
            
            _LOGGER.info("📝 Loaded %d removed devices from blacklist", len(removed_devices))
            return removed_devices
            
        except Exception as e:
            _LOGGER.error("Error loading removed devices list: %s", e)
            return []

    def is_device_removed(self, serial_number: str, removed_devices: List[str]) -> bool:
        """Check if a device is in the removed devices blacklist (DEPRECATED - use whitelist instead)."""
        return serial_number in removed_devices

    # ============================================================
    # NEW: Whitelist-based Device Management
    # ============================================================
    
    async def save_device_whitelist(self, whitelist: Dict[str, Dict[str, Any]]) -> bool:
        """Save whitelist of registered devices with their RX11 indices.
        
        Whitelist format:
        {
            "serial_number": {
                "serial_number": str,
                "rx11_index": int or None,
                "device_type": str,
                "name": str,
                "added_date": str (ISO format),
                "last_seen": str (ISO format),
                "source": str ("GetFdSerial", "telegram", "manual")
            }
        }
        """
        try:
            whitelist_data = {
                "version": "1.0",
                "last_updated": datetime.now().isoformat(),
                "description": "Whitelist of registered ELDAT devices. Only these devices will be restored on restart.",
                "device_count": len(whitelist),
                "devices": whitelist
            }
            
            await self._write_json_file(self.whitelist_file, whitelist_data)
            _LOGGER.info("💾 Saved whitelist with %d registered devices", len(whitelist))
            return True
            
        except Exception as e:
            _LOGGER.error("❌ Error saving device whitelist: %s", e)
            return False

    async def load_device_whitelist(self) -> Dict[str, Dict[str, Any]]:
        """Load whitelist of registered devices.
        
        Returns:
            Dict mapping serial_number to device info
        """
        try:
            if not self.whitelist_file.exists():
                _LOGGER.info("ℹ️  No whitelist file found, creating empty whitelist")
                return {}
            
            whitelist_data = await self._read_json_file(self.whitelist_file)
            devices = whitelist_data.get("devices", {})
            
            _LOGGER.info("✅ Loaded whitelist with %d registered devices", len(devices))
            return devices
            
        except Exception as e:
            _LOGGER.error("❌ Error loading device whitelist: %s", e)
            return {}

    async def add_device_to_whitelist(
        self,
        serial_number: str,
        rx11_index: Optional[int] = None,
        device_type: str = "unknown",
        name: Optional[str] = None,
        source: str = "unknown"
    ) -> bool:
        """Add a single device to the whitelist.
        
        Args:
            serial_number: Device serial number
            rx11_index: RX11 index if applicable (for receivers)
            device_type: Type of device (ew_receiver, ew_transmitter, etc.)
            name: Human-readable device name
            source: How device was discovered ("GetFdSerial", "telegram", "manual")
        """
        try:
            # Load current whitelist
            whitelist = await self.load_device_whitelist()
            
            # Check if device already exists
            if serial_number in whitelist:
                # Update last_seen
                whitelist[serial_number]["last_seen"] = datetime.now().isoformat()
                if rx11_index is not None:
                    whitelist[serial_number]["rx11_index"] = rx11_index
                if name:
                    whitelist[serial_number]["name"] = name
                _LOGGER.debug("📝 Updated existing whitelist entry for %s", serial_number[-8:])
            else:
                # Add new entry
                whitelist[serial_number] = {
                    "serial_number": serial_number,
                    "rx11_index": rx11_index,
                    "device_type": device_type,
                    "name": name or f"Device {serial_number[-6:]}",
                    "added_date": datetime.now().isoformat(),
                    "last_seen": datetime.now().isoformat(),
                    "source": source
                }
                _LOGGER.info("✅ Added device %s to whitelist (index: %s, type: %s, source: %s)",
                           serial_number[-8:], rx11_index, device_type, source)
            
            # Save updated whitelist
            return await self.save_device_whitelist(whitelist)
            
        except Exception as e:
            _LOGGER.error("❌ Error adding device to whitelist: %s", e)
            return False

    async def remove_device_from_whitelist(self, serial_number: str) -> bool:
        """Remove a device from the whitelist (UI delete button)."""
        try:
            whitelist = await self.load_device_whitelist()
            
            if serial_number in whitelist:
                device_info = whitelist.pop(serial_number)
                await self.save_device_whitelist(whitelist)
                _LOGGER.info("🗑️  Removed device %s from whitelist (was at index %s)",
                           serial_number[-8:], device_info.get("rx11_index"))
                return True
            else:
                _LOGGER.warning("⚠️  Device %s not in whitelist", serial_number[-8:])
                return False
                
        except Exception as e:
            _LOGGER.error("❌ Error removing device from whitelist: %s", e)
            return False

    async def is_device_whitelisted(self, serial_number: str) -> bool:
        """Check if a device is in the whitelist."""
        whitelist = await self.load_device_whitelist()
        return serial_number in whitelist

    async def get_whitelisted_devices_by_index(self) -> Dict[int, str]:
        """Get mapping of RX11 index to serial number for all whitelisted devices.
        
        Returns:
            Dict mapping rx11_index to serial_number
        """
        whitelist = await self.load_device_whitelist()
        index_map = {}
        
        for serial, device_info in whitelist.items():
            rx11_index = device_info.get("rx11_index")
            if rx11_index is not None:
                index_map[rx11_index] = serial
        
        return index_map

    async def migrate_from_blacklist_to_whitelist(self) -> bool:
        """Migrate from old blacklist system to new whitelist system.
        
        This should be called once during upgrade to convert existing devices
        to whitelist format.
        """
        try:
            # Load all currently configured devices
            devices = await self.load_devices()
            
            # Load blacklist
            removed_devices = await self.load_removed_devices_list()
            
            # Create whitelist from devices not in blacklist
            whitelist = {}
            for serial, device_info in devices.items():
                if serial not in removed_devices:
                    whitelist[serial] = {
                        "serial_number": serial,
                        "rx11_index": device_info.get("rx11_index"),
                        "device_type": device_info.get("type", "unknown"),
                        "name": device_info.get("name", f"Device {serial[-6:]}"),
                        "added_date": datetime.now().isoformat(),
                        "last_seen": datetime.now().isoformat(),
                        "source": "migration"
                    }
            
            # Save whitelist
            success = await self.save_device_whitelist(whitelist)
            if success:
                _LOGGER.info("✅ Migrated %d devices to whitelist system", len(whitelist))
            
            return success
            
        except Exception as e:
            _LOGGER.error("❌ Error migrating to whitelist: %s", e)
            return False

    async def list_backups(self) -> List[Dict[str, Any]]:
        """List available backup files."""
        try:
            backups = []
            pattern = "eldat_devices_backup_*.json"
            
            for backup_file in self.backup_dir.glob(pattern):
                try:
                    backup_data = await self._read_json_file(backup_file)
                    backup_info = {
                        "file_path": str(backup_file),
                        "file_name": backup_file.name,
                        "created": backup_data.get("backup_created", "Unknown"),
                        "device_count": len(backup_data.get("devices", {})),
                        "version": backup_data.get("version", "Unknown"),
                        "size": backup_file.stat().st_size,
                        "type": backup_data.get("backup_type", "unknown"),
                    }
                    backups.append(backup_info)
                except Exception as e:
                    _LOGGER.warning("Error reading backup file %s: %s", backup_file, e)
            
            # Sort by creation time (newest first)
            backups.sort(key=lambda x: x["created"], reverse=True)
            
            # Cleanup old backups (keep last 10)
            if len(backups) > 10:
                for old_backup in backups[10:]:
                    try:
                        Path(old_backup["file_path"]).unlink()
                        _LOGGER.debug("Deleted old backup: %s", old_backup["file_name"])
                    except Exception:
                        pass
                backups = backups[:10]
            
            return backups
            
        except Exception as e:
            _LOGGER.error("Error listing backups: %s", e)
            return []
    
    async def remove_device(self, serial_number: str) -> bool:
        """Remove a device from configuration."""
        try:
            if not self.config_file.exists():
                _LOGGER.warning("No config file to remove device from")
                return False
            
            config_data = await self._read_json_file(self.config_file)
            
            if serial_number in config_data.get("devices", {}):
                # Create backup before removal
                await self._create_backup()
                
                device_name = config_data["devices"][serial_number].get("name", serial_number)
                del config_data["devices"][serial_number]
                config_data["device_count"] = len(config_data["devices"])
                config_data["last_updated"] = datetime.now().isoformat()
                
                await self._write_json_file(self.config_file, config_data)
                
                _LOGGER.info("🗑️  Device removed from configuration: %s (%s)", device_name, serial_number)
                return True
            else:
                _LOGGER.warning("Device not found in configuration: %s", serial_number)
                return False
                
        except Exception as e:
            _LOGGER.error("Error removing device from configuration: %s", e)
            return False
    
    async def update_device(self, serial_number: str, updates: Dict[str, Any]) -> bool:
        """Update specific fields of a device configuration."""
        try:
            if not self.config_file.exists():
                _LOGGER.warning("No config file to update")
                return False
            
            config_data = await self._read_json_file(self.config_file)
            
            if serial_number not in config_data.get("devices", {}):
                _LOGGER.warning("Device not found in configuration: %s", serial_number)
                return False
            
            # Update device fields
            device = config_data["devices"][serial_number]
            device.update(updates)
            device["last_updated"] = datetime.now().isoformat()
            
            config_data["last_updated"] = datetime.now().isoformat()
            
            await self._write_json_file(self.config_file, config_data)
            
            _LOGGER.info("✏️  Device configuration updated: %s", serial_number)
            return True
            
        except Exception as e:
            _LOGGER.error("Error updating device configuration: %s", e)
            return False

    def _get_device_entities(self, ha_device: dr.DeviceEntry) -> List[Dict[str, str]]:
        """Get list of Home Assistant entities for this device."""
        try:
            from homeassistant.helpers import entity_registry as er
            
            entity_registry = er.async_get(self.hass)
            entities = er.async_entries_for_device(entity_registry, ha_device.id)
            
            entity_list = []
            for entity in entities:
                entity_info = {
                    "entity_id": entity.entity_id,
                    "platform": entity.platform,
                    "device_class": entity.device_class,
                    "name": entity.name,
                }
                entity_list.append(entity_info)
            
            return entity_list
            
        except Exception as e:
            _LOGGER.warning("Error getting device entities: %s", e)
            return []

    async def _create_backup(self) -> None:
        """Create automatic backup before making changes."""
        try:
            if self.config_file.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = self.backup_dir / f"eldat_devices_auto_{timestamp}.json"
                
                config_data = await self._read_json_file(self.config_file)
                config_data["backup_created"] = datetime.now().isoformat()
                config_data["backup_type"] = "automatic"
                
                await self._write_json_file(backup_path, config_data)
                _LOGGER.debug("Automatic backup created: %s", backup_path.name)
                
        except Exception as e:
            _LOGGER.warning("Error creating automatic backup: %s", e)

    async def _read_json_file(self, file_path: Path) -> Dict[str, Any]:
        """Read JSON file asynchronously."""
        loop = asyncio.get_event_loop()
        
        def _read_file():
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        return await loop.run_in_executor(None, _read_file)

    async def _write_json_file(self, file_path: Path, data: Dict[str, Any]) -> None:
        """Write JSON file asynchronously."""
        loop = asyncio.get_event_loop()
        
        def _write_file():
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        
        await loop.run_in_executor(None, _write_file)