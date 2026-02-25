"""Consolidated State Manager - Speichert alle persistierten Daten in einer Datei.

Dieses Modul konsolidiert alle persistierten Daten:
- Entity-unique IDs
- Registrierte Geräte
- EWB-Index-Tracking
- EWneo-Receiver-Index-Tracking

in einer einzelnen JSON-Datei für einfaches Backup.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Set, Dict, Any, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class StateManager:
    """Verwaltet alle persistierten Integrations-Daten in einer Datei.
    
    Diese konsolidierte Lösung:
    - Speichert eine einzige JSON-Datei (~/.homeassistant/easywave_state_<entry_id>.json)
    - Wird automatisch vom HA-Standard-Backup erfasst
    - Enthält alle Indexe, Device-Listen und Entity-Informationen
    - Ermöglicht leichte Migration zwischen Installationen
    """
    
    def __init__(self, hass: HomeAssistant, config_entry_id: str):
        """Initialisiert StateManager.
        
        Args:
            hass: Home Assistant instance
            config_entry_id: Config entry ID
        """
        self.hass = hass
        self.config_entry_id = config_entry_id
        self._lock = asyncio.Lock()
        
        # Single consolidated state file in .homeassistant/ (auto-backed up by HA)
        backup_dir = Path(hass.config.config_dir)
        self.state_file = backup_dir / f"easywave_state_{config_entry_id}.json"
        
        # In-memory state cache
        self._state: Dict[str, Any] = {
            "entity_unique_ids": set(),
            "entity_metadata": {},
            "registered_devices": {},
            "ewb_indices": {},
            "ew_receiver_indices": {},
        }
    
    async def load(self) -> bool:
        """Lädt alle State-Daten aus Datei.
        
        Returns:
            True wenn erfolgreich, False bei Fehler
        """
        async with self._lock:
            try:
                if not self.state_file.exists():
                    _LOGGER.info("📄 Keine State-Datei gefunden, starte mit leer")
                    return True
                
                _LOGGER.info("📖 Lade State aus %s", self.state_file)
                
                data = await self._read_json_file(self.state_file)
                
                # Load entity tracking
                entity_uids = set(data.get("entity_unique_ids", []))
                self._state["entity_unique_ids"] = entity_uids
                self._state["entity_metadata"] = data.get("entity_metadata", {})
                
                # Load device data
                self._state["registered_devices"] = data.get("registered_devices", {})
                self._state["ewb_indices"] = data.get("ewb_indices", {})
                self._state["ew_receiver_indices"] = data.get("ew_receiver_indices", {})
                
                loaded_count = len(entity_uids)
                _LOGGER.info("✅ Geladen: %d Entities, %d Devices, %d EWB-Indizes", 
                            loaded_count,
                            len(self._state["registered_devices"]),
                            len(self._state["ewb_indices"]))
                
                return True
                
            except Exception as e:
                _LOGGER.error("❌ Fehler beim Laden State: %s", e)
                return False
    
    async def save(self) -> bool:
        """Speichert alle State-Daten in Datei.
        
        Returns:
            True wenn erfolgreich, False bei Fehler
        """
        async with self._lock:
            try:
                data = {
                    "version": "2.0",
                    "created_at": datetime.now().isoformat(),
                    "config_entry_id": self.config_entry_id,
                    "entity_count": len(self._state["entity_unique_ids"]),
                    "device_count": len(self._state["registered_devices"]),
                    "entity_unique_ids": sorted(list(self._state["entity_unique_ids"])),
                    "entity_metadata": self._state["entity_metadata"],
                    "registered_devices": self._state["registered_devices"],
                    "ewb_indices": self._state["ewb_indices"],
                    "ew_receiver_indices": self._state["ew_receiver_indices"],
                }
                
                await self._write_json_file(self.state_file, data)
                
                _LOGGER.info("💾 Gespeichert: %d Entities, %d Devices in %s",
                            data["entity_count"],
                            data["device_count"],
                            self.state_file)
                
                return True
                
            except Exception as e:
                _LOGGER.error("❌ Fehler beim Speichern State: %s", e)
                return False
    
    # Entity persistence methods
    
    def is_entity_created(self, unique_id: str) -> bool:
        """Prüft, ob Entity bereits erstellt wurde."""
        return unique_id in self._state["entity_unique_ids"]
    
    def mark_entity_created(self, unique_id: str, entity_name: Optional[str] = None) -> None:
        """Markiert Entity als erstellt."""
        if unique_id not in self._state["entity_unique_ids"]:
            self._state["entity_unique_ids"].add(unique_id)
            self._state["entity_metadata"][unique_id] = {
                "name": entity_name,
                "created_at": datetime.now().isoformat()
            }
            _LOGGER.debug("📍 Entity markiert: %s", unique_id[-24:])
    
    def unmark_entity_deleted(self, unique_id: str) -> None:
        """Entfernt Entity aus Verfolgung (ist gelöscht)."""
        if unique_id in self._state["entity_unique_ids"]:
            self._state["entity_unique_ids"].discard(unique_id)
            self._state["entity_metadata"].pop(unique_id, None)
            _LOGGER.debug("🗑️ Entity entfernt: %s", unique_id[-24:])
    
    def get_all_created_entities(self) -> Set[str]:
        """Gibt alle erstellten Entities zurück."""
        return self._state["entity_unique_ids"].copy()
    
    # Device registration methods
    
    def get_registered_devices(self) -> Dict[str, Dict[str, Any]]:
        """Gibt alle registrierten Geräte zurück."""
        return self._state["registered_devices"].copy()
    
    def set_registered_devices(self, devices: Dict[str, Dict[str, Any]]) -> None:
        """Setzt registrierte Geräte."""
        self._state["registered_devices"] = devices.copy()
    
    def add_registered_device(self, serial: str, device_info: Dict[str, Any]) -> None:
        """Fügt registriertes Gerät hinzu."""
        self._state["registered_devices"][serial] = device_info
    
    def remove_registered_device(self, serial: str) -> None:
        """Entfernt registriertes Gerät."""
        self._state["registered_devices"].pop(serial, None)
    
    # EWB index tracking methods
    
    def get_ewb_indices(self) -> Dict[int, Dict[str, Any]]:
        """Gibt alle EWB-Indizes zurück."""
        return self._state["ewb_indices"].copy()
    
    def set_ewb_indices(self, indices: Dict[int, Dict[str, Any]]) -> None:
        """Setzt EWB-Indizes."""
        self._state["ewb_indices"] = {str(k): v for k, v in indices.items()}
    
    def add_ewb_index(self, index: int, data: Dict[str, Any]) -> None:
        """Registriert EWB-Index."""
        self._state["ewb_indices"][str(index)] = data
    
    def remove_ewb_index(self, index: int) -> None:
        """Entfernt EWB-Index."""
        self._state["ewb_indices"].pop(str(index), None)
    
    # EWneo receiver index tracking methods
    
    def get_ew_receiver_indices(self) -> Dict[int, Dict[str, Any]]:
        """Gibt alle EWneo-Receiver-Indizes zurück."""
        return self._state["ew_receiver_indices"].copy()
    
    def set_ew_receiver_indices(self, indices: Dict[int, Dict[str, Any]]) -> None:
        """Setzt EWneo-Receiver-Indizes."""
        self._state["ew_receiver_indices"] = {str(k): v for k, v in indices.items()}
    
    def add_ew_receiver_index(self, index: int, data: Dict[str, Any]) -> None:
        """Registriert EWneo-Receiver-Index."""
        self._state["ew_receiver_indices"][str(index)] = data
    
    def remove_ew_receiver_index(self, index: int) -> None:
        """Entfernt EWneo-Receiver-Index."""
        self._state["ew_receiver_indices"].pop(str(index), None)
    
    # Cleanup methods
    
    async def cleanup_old_files(self) -> int:
        """Entfernt alte separate State-Dateien (Migration).
        
        Returns:
            Anzahl der gelöschten Dateien
        """
        old_files = [
            Path(self.hass.config.config_dir) / "easywave" / f"entity_persistence_{self.config_entry_id}.json",
            Path(self.hass.config.config_dir) / "easywave" / f"entity_persistence_{self.config_entry_id}_backup.json",
            Path(self.hass.config.config_dir) / "eldat" / "registered_devices.json",
            Path(self.hass.config.config_dir) / "eldat" / "used_ewb_indices.json",
            Path(self.hass.config.config_dir) / "eldat" / "used_ew_receiver_indices.json",
        ]
        
        deleted_count = 0
        for file_path in old_files:
            try:
                if file_path.exists():
                    file_path.unlink()
                    deleted_count += 1
                    _LOGGER.debug("🗑️ Gelöscht alte Datei: %s", file_path)
            except Exception as e:
                _LOGGER.warning("⚠️ Konnte alte Datei nicht löschen %s: %s", file_path, e)
        
        if deleted_count > 0:
            _LOGGER.info("🧹 Bereinigt %d alte State-Dateien", deleted_count)
        
        return deleted_count
    
    async def validate_state(self) -> Dict[str, Any]:
        """Validiert State gegen HA Registry.
        
        Returns:
            Validierungsbericht
        """
        report = {
            "total_entities_tracked": len(self._state["entity_unique_ids"]),
            "total_devices_tracked": len(self._state["registered_devices"]),
            "total_ewb_indices": len(self._state["ewb_indices"]),
            "total_ew_receiver_indices": len(self._state["ew_receiver_indices"]),
            "validation_errors": [],
        }
        
        try:
            entity_registry = er.async_get(self.hass)
            
            # Prüfe auf verwaiste Entity-IDs
            for unique_id in self._state["entity_unique_ids"]:
                found = False
                for entity in entity_registry.entities.values():
                    if entity.unique_id == unique_id:
                        found = True
                        break
                
                if not found:
                    report["validation_errors"].append(f"Entity nicht in HA Registry: {unique_id[-20:]}")
        
        except Exception as e:
            _LOGGER.error("❌ Validierungsfehler: %s", e)
            report["validation_errors"].append(f"Validierung fehlgeschlagen: {str(e)}")
        
        return report
    
    # Helper methods
    
    async def _read_json_file(self, file_path: Path) -> Dict[str, Any]:
        """Liest JSON-Datei asynchron."""
        def _read():
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return await asyncio.get_event_loop().run_in_executor(None, _read)
    
    async def _write_json_file(self, file_path: Path, data: Dict[str, Any]) -> None:
        """Schreibt JSON-Datei asynchron."""
        def _write():
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        await asyncio.get_event_loop().run_in_executor(None, _write)
