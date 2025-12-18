#!/usr/bin/env python3
"""
Device Registry für ELDAT Plugin
Verwaltet die Persistierung von EW-Sendern, EW-Empfängern, EWneo-Sensoren und EWneo-Aktoren
Basierend auf der EEPROM-Struktur aus dem Firmware-Projekt
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, asdict
from enum import IntEnum

try:
    import aiofiles
except ImportError:
    aiofiles = None

_LOGGER = logging.getLogger(__name__)

# Device Type Constants (basierend auf EEPROM deviceType)
class DeviceType(IntEnum):
    """Device types matching EEPROM deviceType field"""
    UNKNOWN = 0x00
    EW_SENDER = 0x01          # EW-Sender (Transmitter)
    EW_EMPFAENGER = 0x02      # EW-Empfänger (Receiver)
    EWNEO_SENSOR = 0x03       # EWneo-Sensor
    EWNEO_AKTOR = 0x04        # EWneo-Aktor
    RX11_GATEWAY = 0xFF       # RX11 Gateway/Transceiver

# Status Register Flags (legacy compatibility)
class StatusFlags(IntEnum):
    """Status register bitflags for backward compatibility"""
    NOT_INITIALIZED = 0xFF
    VALID = 0x11
    VALID_EMPTY = 0x10
    INVALID = 0x22
    IN_PROGRESS = 0x01
    BLACKLISTED = 0x80        # Custom flag für entfernte Geräte
    LEARNING_MODE = 0x40      # Custom flag für Lernmodus
    BATTERY_LOW = 0x20        # Custom flag für niedrige Batterie

@dataclass
class EepromInformationEntry:
    """Entspricht eeprom_information_entry_t aus der Firmware"""
    device_id: int           # uint16_t deviceId (0-65535)
    device_type: int         # uint8_t deviceType (DeviceType enum)
    index: int               # uint8_t index (0-255, für RX11 serial mapping)
    status_register: int     # uint8_t statusRegister (StatusFlags)

@dataclass
class EepromSerialEntry:
    """Entspricht eeprom_serial_entry_t aus der Firmware"""
    gateway_serial: str      # uint8_t gatewaySerial[16] as hex string
    receiver_transmitter: str # uint8_t receiverTransmitter[16] as hex string

@dataclass
class EepromEntry:
    """Entspricht eeprom_entry_t aus der Firmware"""
    information: EepromInformationEntry
    serial_number: EepromSerialEntry
    
    # Zusätzliche Home Assistant spezifische Felder
    unique_id: str                    # Home Assistant unique ID
    name: str                        # Benutzerfreundlicher Name
    area: Optional[str] = None       # Home Assistant Area
    created_at: Optional[str] = None # Erstellungszeitpunkt
    last_seen: Optional[str] = None  # Letzter Kontakt
    battery_level: Optional[int] = None # Batterielevel (0-100)
    signal_strength: Optional[int] = None # Signalstärke

class DeviceRegistry:
    """
    Device Registry für ELDAT Plugin
    Verwaltet die Persistierung und Serialnummer-Mapping
    """
    
    def __init__(self, config_dir: str):
        self.config_dir = Path(config_dir)
        self.registry_file = self.config_dir / "eldat_device_registry.json"
        self.backup_file = self.config_dir / "eldat_device_registry_backup.json"
        
        # In-Memory Speicher
        self._devices: Dict[int, EepromEntry] = {}  # device_id -> EepromEntry
        self._serial_to_device: Dict[str, int] = {}  # serial -> device_id
        self._index_to_serial: Dict[int, str] = {}   # RX11 index -> serial
        self._ew_receiver_mapping: Dict[int, Dict[str, str]] = {}  # index -> {"serial": "...", "device_serial": "..."}
        self._blacklisted_serials: Set[str] = set()  # Entfernte Geräte
        
        # Registry state
        self._loaded = False
        self._lock = asyncio.Lock()
    
    async def load_registry(self) -> bool:
        """Lädt die Geräte-Registry aus der JSON-Datei mit asynchronen I/O"""
        async with self._lock:
            try:
                if not self.registry_file.exists():
                    _LOGGER.info("🗂️ Device registry file does not exist, starting with empty registry")
                    await self._create_empty_registry()
                    return True
                
                _LOGGER.info(f"📖 Loading device registry from {self.registry_file}")
                
                # Use aiofiles if available, fallback to run_in_executor
                if aiofiles:
                    async with aiofiles.open(self.registry_file, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        data = json.loads(content)
                else:
                    # Fallback to run_in_executor for synchronous file operations
                    import concurrent.futures
                    loop = asyncio.get_event_loop()
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        def read_file():
                            with open(self.registry_file, 'r', encoding='utf-8') as f:
                                return json.load(f)
                        data = await loop.run_in_executor(executor, read_file)
                
                # Validate file structure
                if not isinstance(data, dict) or 'devices' not in data:
                    _LOGGER.error("❌ Invalid registry file structure")
                    return await self._load_backup()
                
                # Clear current state
                self._devices.clear()
                self._serial_to_device.clear()
                self._index_to_serial.clear()
                self._ew_receiver_mapping.clear()
                self._blacklisted_serials.clear()
                
                # Load devices
                devices_data = data.get('devices', {})
                blacklisted = data.get('blacklisted_serials', [])
                ew_receiver_mappings = data.get('ew_receiver_mappings', {})
                
                # Load EW-Receiver mappings first
                for index_str, mapping in ew_receiver_mappings.items():
                    try:
                        index = int(index_str)
                        self._ew_receiver_mapping[index] = mapping
                        _LOGGER.debug("📝 Loaded EW-Receiver mapping: Index %d → %s", 
                                    index, mapping.get('ew_serial', 'unknown')[-8:])
                    except Exception as e:
                        _LOGGER.warning("⚠️ Failed to load EW-Receiver mapping %s: %s", index_str, e)
                
                for device_id_str, device_data in devices_data.items():
                    try:
                        device_id = int(device_id_str)
                        entry = self._dict_to_eeprom_entry(device_data)
                        
                        self._devices[device_id] = entry
                        
                        # Build serial mappings
                        rt_serial = entry.serial_number.receiver_transmitter
                        if rt_serial and rt_serial != "00" * 16:
                            self._serial_to_device[rt_serial] = device_id
                            
                            # Build index mapping für RX11
                            index = entry.information.index
                            if index < 256:  # Valid index range
                                self._index_to_serial[index] = rt_serial
                        
                    except Exception as e:
                        _LOGGER.warning(f"⚠️ Failed to load device {device_id_str}: {e}")
                        continue
                
                # Load blacklisted serials
                self._blacklisted_serials.update(blacklisted)
                
                self._loaded = True
                _LOGGER.info(f"✅ Registry loaded: {len(self._devices)} devices, {len(self._blacklisted_serials)} blacklisted")
                
                # Log summary by device type
                type_counts = {}
                for entry in self._devices.values():
                    device_type = entry.information.device_type
                    type_name = self._get_device_type_name(device_type)
                    type_counts[type_name] = type_counts.get(type_name, 0) + 1
                
                for type_name, count in type_counts.items():
                    _LOGGER.info(f"   📱 {type_name}: {count} devices")
                
                return True
                
            except Exception as e:
                _LOGGER.error(f"❌ Failed to load device registry: {e}")
                return await self._load_backup()
    
    async def _load_backup(self) -> bool:
        """Lädt die Backup-Registry mit asynchronen I/O"""
        try:
            if not self.backup_file.exists():
                _LOGGER.warning("⚠️ No backup registry found, creating empty registry")
                await self._create_empty_registry()
                return True
            
            _LOGGER.info(f"🔄 Loading backup registry from {self.backup_file}")
            
            # Copy backup to main file asynchronously
            if aiofiles:
                async with aiofiles.open(self.backup_file, 'rb') as src:
                    content = await src.read()
                async with aiofiles.open(self.registry_file, 'wb') as dst:
                    await dst.write(content)
            else:
                # Fallback to run_in_executor
                import concurrent.futures
                import shutil
                loop = asyncio.get_event_loop()
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    await loop.run_in_executor(executor, shutil.copy2, self.backup_file, self.registry_file)
            
            # Load from main file
            return await self.load_registry()
            
        except Exception as e:
            _LOGGER.error(f"❌ Failed to load backup registry: {e}")
            await self._create_empty_registry()
            return False
    
    async def _create_empty_registry(self):
        """Erstellt eine leere Registry"""
        self._devices.clear()
        self._serial_to_device.clear()
        self._index_to_serial.clear()
        self._ew_receiver_mapping.clear()
        self._blacklisted_serials.clear()
        self._loaded = True
        await self.save_registry()
    
    async def save_registry(self, create_backup: bool = True) -> bool:
        """Speichert die Registry in die JSON-Datei mit asynchronen I/O"""
        async with self._lock:
            try:
                # Create backup if requested
                if create_backup and self.registry_file.exists():
                    if aiofiles:
                        async with aiofiles.open(self.registry_file, 'rb') as src:
                            content = await src.read()
                        async with aiofiles.open(self.backup_file, 'wb') as dst:
                            await dst.write(content)
                    else:
                        # Fallback to run_in_executor
                        import concurrent.futures
                        import shutil
                        loop = asyncio.get_event_loop()
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            await loop.run_in_executor(executor, shutil.copy2, self.registry_file, self.backup_file)
                
                # Prepare data structure
                registry_data = {
                    "version": "1.0",
                    "created_at": datetime.now().isoformat(),
                    "devices": {},
                    "blacklisted_serials": list(self._blacklisted_serials),
                    "ew_receiver_mappings": self._ew_receiver_mapping,  # Store EW-Receiver mappings
                    "statistics": {
                        "total_devices": len(self._devices),
                        "device_types": {},
                        "ew_receiver_mappings": len(self._ew_receiver_mapping)
                    }
                }
                
                # Add devices
                for device_id, entry in self._devices.items():
                    registry_data["devices"][str(device_id)] = self._eeprom_entry_to_dict(entry)
                
                # Add statistics
                type_counts = {}
                for entry in self._devices.values():
                    device_type = entry.information.device_type
                    type_name = self._get_device_type_name(device_type)
                    type_counts[type_name] = type_counts.get(type_name, 0) + 1
                
                registry_data["statistics"]["device_types"] = type_counts
                
                # Ensure directory exists
                self.config_dir.mkdir(parents=True, exist_ok=True)
                
                # Write to file asynchronously to avoid blocking the event loop
                json_content = json.dumps(registry_data, indent=2, ensure_ascii=False)
                
                if aiofiles:
                    async with aiofiles.open(self.registry_file, 'w', encoding='utf-8') as f:
                        await f.write(json_content)
                else:
                    # Fallback to run_in_executor for synchronous file operations
                    import concurrent.futures
                    loop = asyncio.get_event_loop()
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        def write_file():
                            with open(self.registry_file, 'w', encoding='utf-8') as f:
                                f.write(json_content)
                        await loop.run_in_executor(executor, write_file)
                
                _LOGGER.info(f"💾 Device registry saved asynchronously: {len(self._devices)} devices")
                return True
                
            except Exception as e:
                _LOGGER.error(f"❌ Failed to save device registry: {e}")
                return False
    
    async def add_device(self, entry: EepromEntry) -> bool:
        """Fügt ein Gerät zur Registry hinzu"""
        async with self._lock:
            try:
                device_id = entry.information.device_id
                rt_serial = entry.serial_number.receiver_transmitter
                
                # Check if device already exists
                if device_id in self._devices:
                    _LOGGER.warning(f"⚠️ Device {device_id} already exists, updating...")
                
                # Check for serial conflicts
                if rt_serial in self._serial_to_device and self._serial_to_device[rt_serial] != device_id:
                    existing_id = self._serial_to_device[rt_serial]
                    _LOGGER.warning(f"⚠️ Serial {rt_serial} already assigned to device {existing_id}")
                    return False
                
                # Add timestamp
                if not entry.created_at:
                    entry.created_at = datetime.now().isoformat()
                
                entry.last_seen = datetime.now().isoformat()
                
                # Store device
                self._devices[device_id] = entry
                
                # Update serial mapping
                if rt_serial and rt_serial != "00" * 16:
                    self._serial_to_device[rt_serial] = device_id
                    
                    # Update index mapping
                    index = entry.information.index
                    if index < 256:
                        self._index_to_serial[index] = rt_serial
                
                # Remove from blacklist if present
                self._blacklisted_serials.discard(rt_serial)
                
                _LOGGER.info(f"➕ Added device: {entry.name} (ID: {device_id}, Type: {self._get_device_type_name(entry.information.device_type)})")
                
                # Save to file
                await self.save_registry()
                return True
                
            except Exception as e:
                _LOGGER.error(f"❌ Failed to add device: {e}")
                return False
    
    async def remove_device(self, device_id: int, blacklist: bool = True) -> bool:
        """Entfernt ein Gerät aus der Registry"""
        async with self._lock:
            try:
                if device_id not in self._devices:
                    _LOGGER.warning(f"⚠️ Device {device_id} not found in registry")
                    return False
                
                entry = self._devices[device_id]
                rt_serial = entry.serial_number.receiver_transmitter
                
                # Remove from mappings
                if rt_serial in self._serial_to_device:
                    del self._serial_to_device[rt_serial]
                
                index = entry.information.index
                if index in self._index_to_serial:
                    del self._index_to_serial[index]
                
                # Add to blacklist if requested
                if blacklist and rt_serial and rt_serial != "00" * 16:
                    self._blacklisted_serials.add(rt_serial)
                
                # Remove device
                del self._devices[device_id]
                
                _LOGGER.info(f"➖ Removed device: {entry.name} (ID: {device_id})")
                if blacklist:
                    _LOGGER.info(f"🚫 Serial {rt_serial} added to blacklist")
                
                # Save to file
                await self.save_registry()
                return True
                
            except Exception as e:
                _LOGGER.error(f"❌ Failed to remove device: {e}")
                return False
    
    async def get_device(self, device_id: int) -> Optional[EepromEntry]:
        """Holt ein Gerät anhand der device_id"""
        return self._devices.get(device_id)
    
    async def get_device_by_serial(self, serial: str) -> Optional[EepromEntry]:
        """Holt ein Gerät anhand der Seriennummer"""
        device_id = self._serial_to_device.get(serial)
        if device_id:
            return self._devices.get(device_id)
        return None
    
    async def get_all_devices(self) -> Dict[int, EepromEntry]:
        """Holt alle Geräte"""
        return self._devices.copy()
    
    async def get_devices_by_type(self, device_type: DeviceType) -> Dict[int, EepromEntry]:
        """Holt alle Geräte eines bestimmten Typs"""
        return {
            device_id: entry for device_id, entry in self._devices.items()
            if entry.information.device_type == device_type
        }
    
    def store_ew_receiver_mapping(self, index: int, ew_serial: str, device_serial: str) -> None:
        """Store EW-Receiver mapping with enhanced persistence for RX11-based devices."""
        self._ew_receiver_mapping[index] = {
            "ew_serial": ew_serial,
            "device_serial": device_serial,
            "created_at": datetime.now().isoformat(),
            "rx11_based": True,  # Mark as RX11-based device
            "persistent": True,  # Ensure persistent storage
        }
        _LOGGER.info("📝 Persistently stored EW-Receiver mapping: Index %d → EW Serial %s → Device %s", 
                    index, ew_serial[-8:], device_serial[-8:])
    
    def get_ew_receiver_mapping(self, index: int) -> Optional[Dict[str, str]]:
        """Get EW-Receiver mapping for given index."""
        return self._ew_receiver_mapping.get(index)
    
    def get_device_by_ew_receiver_serial(self, ew_serial: str) -> Optional[int]:
        """Get device ID by EW-Receiver serial with enhanced lookup."""
        for index, mapping in self._ew_receiver_mapping.items():
            if mapping.get("ew_serial") == ew_serial:
                device_serial = mapping.get("device_serial")
                device_id = self._serial_to_device.get(device_serial)
                if device_id:
                    _LOGGER.debug("🔍 Found device ID %d for EW-Receiver serial %s", device_id, ew_serial[-8:])
                    return device_id
        return None
    
    def remove_ew_receiver_mapping(self, index: int) -> bool:
        """Remove EW-Receiver mapping for given index with enhanced cleanup."""
        if index in self._ew_receiver_mapping:
            mapping = self._ew_receiver_mapping.pop(index)
            _LOGGER.info("🗑️ Removed EW-Receiver mapping: Index %d (was: %s → %s)", 
                        index, mapping.get("ew_serial", "unknown")[-8:], 
                        mapping.get("device_serial", "unknown")[-8:])
            return True
        return False
    
    def get_all_ew_receiver_mappings(self) -> Dict[int, Dict[str, str]]:
        """Get all EW-Receiver mappings with RX11-based device information."""
        return self._ew_receiver_mapping.copy()
    
    def restore_ew_receiver_mappings(self, mappings: Dict[int, Dict[str, str]]) -> int:
        """Restore EW-Receiver mappings from backup/storage with validation."""
        restored_count = 0
        try:
            for index, mapping in mappings.items():
                if isinstance(mapping, dict) and "ew_serial" in mapping and "device_serial" in mapping:
                    # Enhance mapping with RX11-based flags if missing
                    enhanced_mapping = mapping.copy()
                    enhanced_mapping.setdefault("rx11_based", True)
                    enhanced_mapping.setdefault("persistent", True)
                    enhanced_mapping.setdefault("restored_at", datetime.now().isoformat())
                    
                    self._ew_receiver_mapping[index] = enhanced_mapping
                    restored_count += 1
                    _LOGGER.info("🔄 Restored EW-Receiver mapping: Index %d → %s → %s", 
                               index, mapping.get("ew_serial", "unknown")[-8:], 
                               mapping.get("device_serial", "unknown")[-8:])
            
            _LOGGER.info("✅ Restored %d EW-Receiver mappings for RX11-based devices", restored_count)
            return restored_count
            
        except Exception as e:
            _LOGGER.error("❌ Error restoring EW-Receiver mappings: %s", e)
            return 0
    
    async def is_blacklisted(self, serial: str) -> bool:
        """Prüft, ob eine Seriennummer auf der Blacklist steht"""
        return serial in self._blacklisted_serials
    
    async def get_serial_for_index(self, index: int) -> Optional[str]:
        """Holt die Seriennummer für einen RX11-Index (für EwGetFdSerial mapping)"""
        return self._index_to_serial.get(index)
    
    async def get_index_for_serial(self, serial: str) -> Optional[int]:
        """Holt den RX11-Index für eine Seriennummer"""
        device_id = self._serial_to_device.get(serial)
        if device_id and device_id in self._devices:
            return self._devices[device_id].information.index
        return None
    
    async def update_device_status(self, device_id: int, **kwargs) -> bool:
        """Aktualisiert den Status eines Geräts"""
        async with self._lock:
            if device_id not in self._devices:
                return False
            
            entry = self._devices[device_id]
            
            # Update fields
            if 'last_seen' in kwargs:
                entry.last_seen = kwargs['last_seen']
            elif 'last_seen' not in kwargs:
                entry.last_seen = datetime.now().isoformat()
            
            if 'battery_level' in kwargs:
                entry.battery_level = kwargs['battery_level']
            
            if 'signal_strength' in kwargs:
                entry.signal_strength = kwargs['signal_strength']
            
            if 'status_register' in kwargs:
                entry.information.status_register = kwargs['status_register']
            
            if 'area' in kwargs:
                entry.area = kwargs['area']
            
            return True
    
    def get_next_device_id(self) -> int:
        """Generiert die nächste verfügbare Device ID"""
        if not self._devices:
            return 1
        return max(self._devices.keys()) + 1
    
    def get_next_index(self) -> int:
        """Generiert den nächsten verfügbaren RX11-Index"""
        used_indices = {entry.information.index for entry in self._devices.values()}
        for i in range(256):  # RX11 supports 0-255
            if i not in used_indices:
                return i
        raise ValueError("No free RX11 indices available")
    
    def _get_device_type_name(self, device_type: int) -> str:
        """Gibt den Namen eines Device Types zurück"""
        type_names = {
            DeviceType.EW_SENDER: "EW-Sender",
            DeviceType.EW_EMPFAENGER: "EW-Empfänger", 
            DeviceType.EWNEO_SENSOR: "EWneo-Sensor",
            DeviceType.EWNEO_AKTOR: "EWneo-Aktor",
            DeviceType.RX11_GATEWAY: "RX11 Gateway"
        }
        return type_names.get(device_type, f"Unknown ({device_type})")
    
    def _eeprom_entry_to_dict(self, entry: EepromEntry) -> Dict[str, Any]:
        """Konvertiert EepromEntry zu Dictionary"""
        return {
            "information": {
                "device_id": entry.information.device_id,
                "device_type": entry.information.device_type,
                "index": entry.information.index,
                "status_register": entry.information.status_register
            },
            "serial_number": {
                "gateway_serial": entry.serial_number.gateway_serial,
                "receiver_transmitter": entry.serial_number.receiver_transmitter
            },
            "unique_id": entry.unique_id,
            "name": entry.name,
            "area": entry.area,
            "created_at": entry.created_at,
            "last_seen": entry.last_seen,
            "battery_level": entry.battery_level,
            "signal_strength": entry.signal_strength
        }
    
    def _dict_to_eeprom_entry(self, data: Dict[str, Any]) -> EepromEntry:
        """Konvertiert Dictionary zu EepromEntry"""
        info_data = data["information"]
        serial_data = data["serial_number"]
        
        information = EepromInformationEntry(
            device_id=info_data["device_id"],
            device_type=info_data["device_type"],
            index=info_data["index"],
            status_register=info_data["status_register"]
        )
        
        serial_number = EepromSerialEntry(
            gateway_serial=serial_data["gateway_serial"],
            receiver_transmitter=serial_data["receiver_transmitter"]
        )
        
        return EepromEntry(
            information=information,
            serial_number=serial_number,
            unique_id=data["unique_id"],
            name=data["name"],
            area=data.get("area"),
            created_at=data.get("created_at"),
            last_seen=data.get("last_seen"),
            battery_level=data.get("battery_level"),
            signal_strength=data.get("signal_strength")
        )

# Utility functions for creating entries
def create_ew_sender_entry(
    device_id: int,
    serial: str,
    name: str,
    rx11_index: int,
    gateway_serial: str = "00" * 16,
    area: str = None
) -> EepromEntry:
    """Erstellt einen EW-Sender Entry"""
    
    information = EepromInformationEntry(
        device_id=device_id,
        device_type=DeviceType.EW_SENDER,
        index=rx11_index,
        status_register=StatusFlags.VALID
    )
    
    serial_number = EepromSerialEntry(
        gateway_serial=gateway_serial,
        receiver_transmitter=serial
    )
    
    return EepromEntry(
        information=information,
        serial_number=serial_number,
        unique_id=f"eldat_ew_sender_{serial}",
        name=name,
        area=area,
        created_at=datetime.now().isoformat()
    )

def create_ew_empfaenger_entry(
    device_id: int,
    serial: str,
    name: str,
    rx11_index: int,
    gateway_serial: str = "00" * 16,
    area: str = None
) -> EepromEntry:
    """Erstellt einen EW-Empfänger Entry"""
    
    information = EepromInformationEntry(
        device_id=device_id,
        device_type=DeviceType.EW_EMPFAENGER,
        index=rx11_index,
        status_register=StatusFlags.VALID
    )
    
    serial_number = EepromSerialEntry(
        gateway_serial=gateway_serial,
        receiver_transmitter=serial
    )
    
    return EepromEntry(
        information=information,
        serial_number=serial_number,
        unique_id=f"eldat_ew_receiver_{serial}",
        name=name,
        area=area,
        created_at=datetime.now().isoformat()
    )