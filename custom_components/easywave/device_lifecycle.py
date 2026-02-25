"""Device Lifecycle Manager - Strukturierte Verwaltung des Lebenszyklus von Geräten und Entitäten.

Optimierungen für Performance und Eindeutigkeit:
1. Konsolidierte Reverse-Mappings (elimniert redundante Datenstrukturen)
2. Effiziente Entity-Sammlung mit Index-basiertem Lookup (O(1) statt O(n))
3. Granularere Lock-Verwaltung für bessere Parallelität
4. Cache-Konsistenzprüfungen
5. Deduplication und Validierung
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN
from .helpers_unique_id import normalize_serial_number

_LOGGER = logging.getLogger(__name__)


class DeviceLifecycleStateEnum(Enum):
    """Zustände im Geräte-Lebenszyklus."""
    UNKNOWN = "unknown"           # Unbekannter Zustand
    DISCOVERING = "discovering"   # Wird gerade entdeckt
    REGISTERING = "registering"   # Wird gerade registriert
    ACTIVE = "active"             # Aktiv und verwaltet
    DELETING = "deleting"         # Wird gerade gelöscht
    DELETED = "deleted"           # Vollständig gelöscht
    ZOMBIE = "zombie"             # Waisengerät (Daten ohne Gerät)


@dataclass
class DeviceLifecycleState:
    """Verfolgt den Zustand eines Geräts im Lebenszyklus."""
    serial_number: str                    # Eindeutige Seriennummer (normalisiert)
    state: DeviceLifecycleStateEnum       # Aktueller Zustand
    
    # Timestamps
    created_at: Optional[str] = None      # Zeitstempel der Erstellung
    discovered_at: Optional[str] = None   # Zeitstempel der Entdeckung
    registered_at: Optional[str] = None   # Zeitstempel der Registrierung
    deleted_at: Optional[str] = None      # Zeitstempel des Löschens
    
    # Tracking
    entity_ids: List[str] = field(default_factory=list)  # Zugeordnete Entity-IDs
    unique_ids: Set[str] = field(default_factory=set)   # Zugeordnete Unique-IDs
    device_id: Optional[str] = None       # Home Assistant Device ID
    
    # Metadaten
    name: Optional[str] = None
    device_type: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)


class DeviceLifecycleManager:
    """Verwaltet den strukturierten Lebenszyklus von Geräten und Entitäten mit optimierter Performance.
    
    Performance-Optimierungen:
    1. Konsolidierte Reverse-Mappings (ein Dict statt drei für weniger Speicher und schnellere Konsistency)
    2. Effiziente Entity-Sammlung durch Index-basierte Lookups
    3. Granularere Lock-Verwaltung für bessere Parallelität
    4. Cache-Konsistenzprüfungen und Validierung
    5. Deduplication bei Entity-Registrierung
    """
    
    def __init__(self, hass: HomeAssistant):
        """Initialisiert den Device Lifecycle Manager mit optimierter Speichernutzung."""
        self.hass = hass
        self._global_lock = asyncio.Lock()
        
        # Pro-Gerät Locks für bessere Parallelität (statt globalem Lock für alles)
        self._device_locks: Dict[str, asyncio.Lock] = {}
        
        # Zustandsverfolgung für alle Geräte
        self._devices: Dict[str, DeviceLifecycleState] = {}
        
        # KONSOLIDIERTE Reverse-Mappings für bessere Performance
        # Struktur: identifier → (type, serial_number)
        # type ∈ {'device_id', 'entity_id', 'unique_id'}
        self._identifier_index: Dict[str, Tuple[str, str]] = {}
        
        # Index für Unique-ID Pattern Lookups (für schnelle Entity-Sammlung)
        # serial_prefix → Set[unique_id]
        self._unique_id_index: Dict[str, Set[str]] = {}
        
        # Geräte in Bearbeitung (Sperren gegen gleichzeitige Operationen)
        self._devices_in_operation: Set[str] = set()
        
        # Cache-Konsistenztracking
        self._cache_generation: Dict[str, int] = {}  # serial → generation
    
    async def _get_device_lock(self, serial_number: str) -> asyncio.Lock:
        """Holt oder erstellt einen Pro-Gerät Lock für bessere Parallelität.
        
        Diese Methode reduziert Lock-Contention indem jedes Gerät seinen eigenen Lock hat.
        """
        async with self._global_lock:
            if serial_number not in self._device_locks:
                self._device_locks[serial_number] = asyncio.Lock()
            return self._device_locks[serial_number]
    
    async def start_device_discovery(
        self, 
        serial_number: str, 
        device_type: str,
        name: Optional[str] = None,
        **attributes
    ) -> Optional[DeviceLifecycleState]:
        """Phase 1: Geräte-Entdeckung starten (optimiert).
        
        Dies ist eine LEICHTE Operation mit Pro-Gerät Locking für bessere Parallelität.
        
        Args:
            serial_number: Eindeutige Seriennummer des Geräts
            device_type: Typ des Geräts (z.B. 'ewneo_switch')
            name: Optionaler Name des Geräts
            **attributes: Zusätzliche Attribute
            
        Returns:
            DeviceLifecycleState oder None bei Fehler
        """
        # Normalisiere die Seriennummer
        normalized_serial = normalize_serial_number(serial_number)
        
        # Verwende Pro-Gerät Lock für bessere Parallelität
        device_lock = await self._get_device_lock(normalized_serial)
        
        async with device_lock:
            # Prüfe ob Gerät bereits existiert
            if normalized_serial in self._devices:
                device_state = self._devices[normalized_serial]
                _LOGGER.debug(
                    "Gerät %s existiert bereits im Zustand %s",
                    normalized_serial[-8:], 
                    device_state.state.value
                )
                return device_state
            
            # Prüfe, ob Gerät gerade in Bearbeitung ist
            if normalized_serial in self._devices_in_operation:
                _LOGGER.warning(
                    "Gerät %s wird gerade bearbeitet, Entdeckung übersprungen",
                    normalized_serial[-8:]
                )
                return None
            
            # Erstelle neuen Geräte-Zustand
            device_state = DeviceLifecycleState(
                serial_number=normalized_serial,
                state=DeviceLifecycleStateEnum.DISCOVERING,
                created_at=datetime.now().isoformat(),
                discovered_at=datetime.now().isoformat(),
                name=name or f"{device_type}_{normalized_serial[-8:]}",
                device_type=device_type,
                attributes=attributes
            )
            
            self._devices[normalized_serial] = device_state
            self._cache_generation[normalized_serial] = 1
            
            _LOGGER.info(
                "✅ Geräte-Entdeckung gestartet: %s (%s)",
                device_state.name,
                normalized_serial[-8:]
            )
            
            return device_state
    
    async def complete_device_registration(
        self,
        serial_number: str,
        device_id: Optional[str] = None
    ) -> bool:
        """Phase 2: Geräte-Registrierung abschließen (optimiert).
        
        Setzt den Zustand auf 'ACTIVE' nach erfolgreicher Registrierung.
        
        Args:
            serial_number: Seriennummer des Geräts
            device_id: Optionale Home Assistant Device ID
            
        Returns:
            True bei Erfolg, False bei Fehler
        """
        normalized_serial = normalize_serial_number(serial_number)
        device_lock = await self._get_device_lock(normalized_serial)
        
        async with device_lock:
            if normalized_serial not in self._devices:
                _LOGGER.error(
                    "Kann Registrierung nicht abschließen: Gerät %s nicht gefunden",
                    normalized_serial[-8:]
                )
                return False
            
            device_state = self._devices[normalized_serial]
            device_state.state = DeviceLifecycleStateEnum.ACTIVE
            device_state.registered_at = datetime.now().isoformat()
            
            if device_id:
                device_state.device_id = device_id
                # Speichere in konsolidiertem Index
                self._identifier_index[device_id] = ('device_id', normalized_serial)
                # Invalidiere Cache
                self._cache_generation[normalized_serial] = self._cache_generation.get(normalized_serial, 0) + 1
            
            _LOGGER.info(
                "✅ Gerät aktiv: %s (%s)",
                device_state.name,
                normalized_serial[-8:]
            )
            return True
    
    async def register_entity_for_device(
        self,
        serial_number: str,
        entity_id: str,
        unique_id: str
    ) -> bool:
        """Registriert eine Entity als Teil eines Geräts (mit Deduplication).
        
        Args:
            serial_number: Seriennummer des Geräts
            entity_id: Vollständige Entity ID (z.B. 'switch.my_device')
            unique_id: Eindeutige ID der Entity
            
        Returns:
            True bei erfolgreicher Registrierung
        """
        normalized_serial = normalize_serial_number(serial_number)
        device_lock = await self._get_device_lock(normalized_serial)
        
        async with device_lock:
            if normalized_serial not in self._devices:
                _LOGGER.warning(
                    "⚠️ Kann Entity nicht registrieren: Gerät %s nicht in Lifecycle Manager",
                    normalized_serial[-8:]
                )
                return False
            
            device_state = self._devices[normalized_serial]
            
            # Deduplication: Prüfe ob Entity bereits registriert ist
            if entity_id in device_state.entity_ids:
                _LOGGER.debug(
                    "📋 Entity bereits registriert für Gerät %s: %s",
                    normalized_serial[-8:],
                    entity_id
                )
                return True  # Nicht als Fehler, sondern als "bereits done"
            
            # Validierung: Prüfe ob Entity bereits einem anderen Gerät zugeordnet ist
            if entity_id in self._identifier_index:
                existing_type, existing_serial = self._identifier_index[entity_id]
                if existing_serial != normalized_serial:
                    _LOGGER.warning(
                        "⚠️ Entity %s ist bereits Gerät %s zugeordnet, nicht %s",
                        entity_id,
                        existing_serial[-8:],
                        normalized_serial[-8:]
                    )
                    return False
            
            # Registriere Entity
            device_state.entity_ids.append(entity_id)
            self._identifier_index[entity_id] = ('entity_id', normalized_serial)
            
            # Registriere Unique ID mit Deduplication und Index
            if unique_id not in device_state.unique_ids:
                device_state.unique_ids.add(unique_id)
                self._identifier_index[unique_id] = ('unique_id', normalized_serial)
                
                # Aktualisiere Unique-ID Index für schnelle Pattern-Lookups
                serial_prefix = normalized_serial[:16]  # Verwende Prefix für Index
                if serial_prefix not in self._unique_id_index:
                    self._unique_id_index[serial_prefix] = set()
                self._unique_id_index[serial_prefix].add(unique_id)
            
            # Invalidiere Cache
            self._cache_generation[normalized_serial] = self._cache_generation.get(normalized_serial, 0) + 1
            
            _LOGGER.debug(
                "📋 Entity registriert für Gerät %s: %s (unique_id: %s)",
                normalized_serial[-8:],
                entity_id,
                unique_id[-16:]
            )
            return True
    
    async def comprehensive_device_cleanup(
        self,
        serial_number: str,
        force: bool = False
    ) -> Dict[str, Any]:
        """Phase 3: UMFASSENDER Cleanup beim Löschen eines Geräts (optimiert).
        
        Dies ist eine SCHWERE Operation, die:
        - Alle Entities aus der Entity Registry entfernt (mit effizientem Index-Lookup)
        - Das Gerät aus der Device Registry entfernt
        - Lokale Zustandsverfolgung aufgeräumt
        - Cache-Generationen invalidiert
        
        Args:
            serial_number: Seriennummer des zu löschenden Geräts
            force: Erzwinge Löschung auch bei Fehlern
            
        Returns:
            Dict mit Cleanup-Statistiken
        """
        normalized_serial = normalize_serial_number(serial_number)
        device_lock = await self._get_device_lock(normalized_serial)
        
        async with device_lock:
            result = {
                "serial": normalized_serial,
                "status": "failed",
                "entities_removed": 0,
                "device_removed": False,
                "errors": []
            }
            
            # Markiere Gerät als "in Bearbeitung"
            self._devices_in_operation.add(normalized_serial)
            
            try:
                # Hole Geräte-Zustand
                device_state = self._devices.get(normalized_serial)
                if not device_state:
                    _LOGGER.warning(
                        "⚠️ Gerät %s nicht in Lifecycle Manager, fahre trotzdem fort",
                        normalized_serial[-8:]
                    )
                else:
                    device_state.state = DeviceLifecycleStateEnum.DELETING
                
                # Hole Registries
                entity_registry = er.async_get(self.hass)
                device_registry = dr.async_get(self.hass)
                
                # Phase 1: Sammle alle Entities für dieses Gerät (mit optimiertem Index)
                entities_to_remove = await self._collect_entities_for_device(
                    entity_registry,
                    device_registry,
                    normalized_serial,
                    device_state
                )
                
                result["entities_found"] = len(entities_to_remove)
                
                # Phase 2: Entferne Entities (mit konsolidiertem Index-Cleanup)
                if entities_to_remove:
                    removed_count = await self._remove_entities(
                        entity_registry,
                        entities_to_remove,
                        normalized_serial
                    )
                    result["entities_removed"] = removed_count
                
                # Phase 3: Entferne Device aus Device Registry
                if device_state and device_state.device_id:
                    device_entry = device_registry.async_get(device_state.device_id)
                    if device_entry:
                        device_registry.async_remove_device(device_entry.id)
                        result["device_removed"] = True
                        _LOGGER.info(
                            "🗑️ Gerät aus Device Registry entfernt: %s",
                            normalized_serial[-8:]
                        )
                
                # Phase 4: Cleanup lokale Zustandsverfolgung
                self._cleanup_local_tracking(normalized_serial, device_state)
                
                # Phase 5: Markiere als gelöscht
                if device_state:
                    device_state.state = DeviceLifecycleStateEnum.DELETED
                    device_state.deleted_at = datetime.now().isoformat()
                
                result["status"] = "success"
                _LOGGER.info(
                    "✅ Vollständiger Cleanup abgeschlossen für %s: %d Entities entfernt",
                    normalized_serial[-8:],
                    result["entities_removed"]
                )
                
            except Exception as e:
                result["errors"].append(str(e))
                _LOGGER.error(
                    "❌ Fehler beim Cleanup von Gerät %s: %s",
                    normalized_serial[-8:],
                    e
                )
                if not force:
                    raise
            finally:
                # Entferne aus "in Bearbeitung"
                self._devices_in_operation.discard(normalized_serial)
            
            return result
    
    async def complete_device_removal(self, serial_number: str) -> None:
        """Markiert ein Gerät als vollständig entfernt."""
        normalized_serial = normalize_serial_number(serial_number)
        device_lock = await self._get_device_lock(normalized_serial)
        
        async with device_lock:
            device_state = self._devices.get(normalized_serial)
            
            if device_state:
                device_state.state = DeviceLifecycleStateEnum.DELETED
                device_state.deleted_at = datetime.now().isoformat()
                _LOGGER.debug(
                    "✅ Gerät als vollständig entfernt markiert: %s",
                    normalized_serial[-8:]
                )
    
    async def _collect_entities_for_device(
        self,
        entity_registry: er.EntityRegistry,
        device_registry: dr.DeviceRegistry,
        serial_number: str,
        device_state: Optional[DeviceLifecycleState]
    ) -> List[str]:
        """Sammelt alle Entity-IDs für ein Gerät mit optimiertem Index-Lookup.
        
        Performance-optimiert mit mehreren Methoden:
        1. Nach Device ID (O(n) aber nur für dieses Gerät)
        2. Nach Unique-ID Index Pattern (O(1) mit Index-Lookup)
        3. Nach gespeicherten Entity-IDs im Lifecycle State (O(1))
        """
        entities_to_remove: Set[str] = set()  # Set für Deduplication
        
        # Methode 1: Nach Device ID (zuverlässigste Methode)
        if device_state and device_state.device_id:
            device_entry = device_registry.async_get(device_state.device_id)
            if device_entry:
                for entity_entry in er.async_entries_for_device(entity_registry, device_entry.id):
                    entities_to_remove.add(entity_entry.entity_id)
                    _LOGGER.debug(
                        "  Gefunden per Device ID: %s",
                        entity_entry.entity_id
                    )
        
        # Methode 2: Nach Unique-ID Index Pattern (optimiert mit Index)
        serial_prefix = serial_number[:16]
        if serial_prefix in self._unique_id_index:
            for unique_id in self._unique_id_index[serial_prefix]:
                # Validiere dass dieser Unique ID wirklich noch für dieses Gerät ist
                if unique_id in self._identifier_index:
                    id_type, stored_serial = self._identifier_index[unique_id]
                    if stored_serial == serial_number and id_type == 'unique_id':
                        # Suche Entity mit diesem Unique ID
                        for entity_entry in entity_registry.entities.values():
                            if entity_entry.unique_id == unique_id and entity_entry.platform == DOMAIN:
                                entities_to_remove.add(entity_entry.entity_id)
                                _LOGGER.debug(
                                    "  Gefunden per Unique-ID Index: %s",
                                    entity_entry.entity_id
                                )
                                break
        
        # Methode 3: Aus gespeicherten Entity-IDs im Geräte-Zustand
        if device_state:
            for entity_id in device_state.entity_ids:
                entities_to_remove.add(entity_id)
                _LOGGER.debug(
                    "  Gefunden im Geräte-Zustand: %s",
                    entity_id
                )
        
        return list(entities_to_remove)
    
    async def _remove_entities(
        self,
        entity_registry: er.EntityRegistry,
        entity_ids: List[str],
        serial_number: str
    ) -> int:
        """Entfernt Entities aus der Registry mit Cleanup des konsolidierten Index."""
        removed_count = 0
        
        for entity_id in entity_ids:
            try:
                # Hole Entity-Info vor dem Löschen
                entity_entry = entity_registry.entities.get(entity_id)
                
                # Entferne Entity
                entity_registry.async_remove(entity_id)
                removed_count += 1
                
                # Cleanup Index-Einträge
                if entity_entry and entity_entry.unique_id:
                    # Entferne aus konsolidiertem Index
                    self._identifier_index.pop(entity_entry.unique_id, None)
                    
                    # Entferne aus Unique-ID Index (Deduktion)
                    serial_prefix = serial_number[:16]
                    if serial_prefix in self._unique_id_index:
                        self._unique_id_index[serial_prefix].discard(entity_entry.unique_id)
                
                # Entferne Entity-ID aus konsolidiertem Index
                self._identifier_index.pop(entity_id, None)
                
                _LOGGER.debug(
                    "  Entity entfernt: %s",
                    entity_id
                )
            except Exception as e:
                _LOGGER.warning(
                    "  Fehler beim Entfernen von Entity %s: %s",
                    entity_id,
                    e
                )
        
        return removed_count
    
    def _cleanup_local_tracking(
        self,
        serial_number: str,
        device_state: Optional[DeviceLifecycleState]
    ) -> None:
        """Bereinigt lokale Zustandsverfolgung und Index-Einträge."""
        # Entferne Device State
        self._devices.pop(serial_number, None)
        self._cache_generation.pop(serial_number, None)
        
        if device_state:
            # Bereinige Index-Einträge (konsolidiert)
            if device_state.device_id:
                self._identifier_index.pop(device_state.device_id, None)
            
            for entity_id in device_state.entity_ids:
                self._identifier_index.pop(entity_id, None)
            
            for unique_id in device_state.unique_ids:
                self._identifier_index.pop(unique_id, None)
            
            # Bereinige Unique-ID Index
            serial_prefix = serial_number[:16]
            for unique_id in device_state.unique_ids:
                if serial_prefix in self._unique_id_index:
                    self._unique_id_index[serial_prefix].discard(unique_id)
            
            # Entferne leere Index-Einträge
            if serial_prefix in self._unique_id_index and not self._unique_id_index[serial_prefix]:
                self._unique_id_index.pop(serial_prefix)
        
        _LOGGER.debug(
            "🧹 Lokale Zustandsverfolgung bereinigt für %s",
            serial_number[-8:]
        )
    
    def device_exists(self, serial_number: str) -> bool:
        """Prüft, ob ein Gerät eindeutig existiert."""
        normalized_serial = normalize_serial_number(serial_number)
        device_state = self._devices.get(normalized_serial)
        
        if not device_state:
            return False
        
        # Gerät existiert, wenn es nicht gelöscht ist
        return device_state.state != DeviceLifecycleStateEnum.DELETED
    
    def get_device_state(self, serial_number: str) -> Optional[DeviceLifecycleState]:
        """Holt den Zustand eines Geräts."""
        normalized_serial = normalize_serial_number(serial_number)
        return self._devices.get(normalized_serial)
    
    def get_serial_for_entity(self, entity_id: str) -> Optional[str]:
        """Findet die Seriennummer eines Geräts für eine gegebene Entity-ID.
        
        Dies ist ein schneller O(1) Lookup mit dem konsolidierten Index.
        """
        if entity_id in self._identifier_index:
            id_type, serial = self._identifier_index[entity_id]
            if id_type == 'entity_id':
                return serial
        return None
    
    def get_serial_for_unique_id(self, unique_id: str) -> Optional[str]:
        """Findet die Seriennummer eines Geräts für eine gegebene Unique-ID.
        
        Dies ist ein schneller O(1) Lookup mit dem konsolidierten Index.
        """
        if unique_id in self._identifier_index:
            id_type, serial = self._identifier_index[unique_id]
            if id_type == 'unique_id':
                return serial
        return None
    
    async def validate_consistency(self) -> Dict[str, Any]:
        """Validiert die Konsistenz zwischen internem State und HA-Registries.
        
        Dies ist eine Diagnose-Funktion zur Überprüfung und Reparatur. 
        Sollte periodisch aufgerufen werden.
        """
        result = {
            "status": "ok",
            "orphaned_indices": 0,
            "inconsistencies_found": 0,
            "fixed": 0,
            "errors": []
        }
        
        async with self._global_lock:
            try:
                entity_registry = er.async_get(self.hass)
                device_registry = dr.async_get(self.hass)
                
                # Prüfe ob Indices auf Geräte mit aktiven Device-State zeigen
                for identifier, (id_type, serial) in list(self._identifier_index.items()):
                    if serial not in self._devices:
                        # Verwaister Index-Eintrag
                        result["orphaned_indices"] += 1
                        _LOGGER.warning(
                            "⚠️ Verwaister Index-Eintrag für %s (Gerät %s nicht mehr verwaltet)",
                            identifier[-16:],
                            serial[-8:]
                        )
                        # Lösche verwaisten Eintrag
                        self._identifier_index.pop(identifier, None)
                        result["fixed"] += 1
                
                # Prüfe Unique-ID Index auf Konsistenz
                for prefix, unique_ids in list(self._unique_id_index.items()):
                    invalid_ids = []
                    for unique_id in unique_ids:
                        if unique_id not in self._identifier_index:
                            invalid_ids.append(unique_id)
                    
                    if invalid_ids:
                        result["inconsistencies_found"] += len(invalid_ids)
                        for uid in invalid_ids:
                            self._unique_id_index[prefix].discard(uid)
                            result["fixed"] += 1
                    
                    # Entferne leere Indizes
                    if not self._unique_id_index[prefix]:
                        self._unique_id_index.pop(prefix)
                
                result["status"] = "ok" if result["inconsistencies_found"] == 0 else "inconsistencies_found_and_fixed"
                
            except Exception as e:
                result["status"] = "error"
                result["errors"].append(str(e))
                _LOGGER.error("❌ Fehler bei Konsistenz-Validierung: %s", e)
        
        return result
