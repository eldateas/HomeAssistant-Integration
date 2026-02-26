"""Entity migration utilities for handling unique_id changes.

This module provides a fallback mechanism to recreate devices with new unique_ids
when breaking changes occur, preserving device names and configurations.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional, Set
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


# Map of old unique_id patterns to new patterns
# Add entries here when unique_id formats change
UNIQUE_ID_MIGRATIONS: Dict[str, str] = {
    # Example: "{serial}_old_button_{id}" -> "{serial}_btn{id}"
    # Add actual migrations as they occur
}


class EntityMigrationHelper:
    """Helper to migrate entities when unique_id formats change."""
    
    def __init__(self, hass: HomeAssistant):
        """Initialize migration helper."""
        self.hass = hass
        self.entity_registry = er.async_get(hass)
        self._migration_log = []
    
    async def check_and_migrate_entities(
        self,
        config_entry_id: str,
        expected_devices: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Check for entity incompatibilities and migrate if needed.
        
        Args:
            config_entry_id: Config entry ID
            expected_devices: Dictionary of expected devices from managed_devices.json
            
        Returns:
            Migration report with statistics and recreated devices
        """
        _LOGGER.info("🔍 Checking entity compatibility for %d devices", len(expected_devices))
        
        report = {
            "checked_devices": 0,
            "incompatible_entities": 0,
            "migrated_devices": 0,
            "recreated_entities": [],
            "failed_migrations": [],
            "timestamp": datetime.now().isoformat(),
        }
        
        # Get all entities for this integration
        existing_entities = self._get_existing_entities(config_entry_id)
        
        for serial_number, device_info in expected_devices.items():
            report["checked_devices"] += 1
            
            # Check if device has incompatible entities
            incompatibilities = self._find_incompatibilities(
                serial_number,
                device_info,
                existing_entities
            )
            
            if incompatibilities:
                _LOGGER.info("⚠️ Found %d incompatible entities for device %s",
                              len(incompatibilities), serial_number[-8:])
                report["incompatible_entities"] += len(incompatibilities)
                
                # Attempt to migrate
                success = await self._migrate_device_entities(
                    serial_number,
                    device_info,
                    incompatibilities,
                    report
                )
                
                if success:
                    report["migrated_devices"] += 1
        
        # Log migration summary (debug level for detail)
        if report["migrated_devices"] > 0:
            _LOGGER.debug("✅ Migration complete: %d devices, %d entities recreated",
                        report["migrated_devices"], len(report["recreated_entities"]))
        
        return report
    
    def _get_existing_entities(self, config_entry_id: str) -> List[er.RegistryEntry]:
        """Get all existing entities for this config entry."""
        all_entities = self.entity_registry.entities.values()
        return [
            entity for entity in all_entities
            if entity.config_entry_id == config_entry_id
        ]
    
    def _find_incompatibilities(
        self,
        serial_number: str,
        device_info: Dict[str, Any],
        existing_entities: List[er.RegistryEntry]
    ) -> List[Dict[str, Any]]:
        """Find entities with incompatible unique_ids.
        
        Returns list of incompatible entities with their metadata.
        """
        incompatibilities = []
        
        # Get expected entity specs from device_info
        entity_specs = device_info.get("entities", [])
        if not entity_specs:
            return incompatibilities
        
        # Build set of expected unique_ids
        expected_unique_ids = {
            spec.get("unique_id") for spec in entity_specs
            if spec.get("unique_id")
        }
        
        # Find entities for this device that don't match expected unique_ids
        for entity in existing_entities:
            if not entity.unique_id:
                continue
            
            # Check if entity belongs to this device (use startswith for safe matching)
            if not entity.unique_id.startswith(serial_number):
                continue
            
            # Check if unique_id matches expected format
            if entity.unique_id not in expected_unique_ids:
                # Check if this is a known migration pattern
                migrated_id = self._apply_migration_pattern(entity.unique_id)
                
                if migrated_id and migrated_id in expected_unique_ids:
                    incompatibilities.append({
                        "entity_id": entity.entity_id,
                        "old_unique_id": entity.unique_id,
                        "new_unique_id": migrated_id,
                        "platform": entity.domain,
                        "name": entity.name or entity.original_name,
                        "disabled": entity.disabled,
                        "device_id": entity.device_id,
                    })
        
        return incompatibilities
    
    def _apply_migration_pattern(self, old_unique_id: str) -> Optional[str]:
        """Apply known migration patterns to convert old unique_id to new format."""
        for old_pattern, new_pattern in UNIQUE_ID_MIGRATIONS.items():
            if old_pattern in old_unique_id:
                return old_unique_id.replace(old_pattern, new_pattern)
        return None
    
    async def _migrate_device_entities(
        self,
        serial_number: str,
        device_info: Dict[str, Any],
        incompatibilities: List[Dict[str, Any]],
        report: Dict[str, Any]
    ) -> bool:
        """Migrate incompatible entities for a device.
        
        Strategy:
        1. Update unique_id in entity registry (preferred)
        2. If update fails, mark old entity as disabled and create migration note
        
        Returns True if migration succeeded.
        """
        success_count = 0
        
        for incompat in incompatibilities:
            try:
                # Try to update unique_id in entity registry
                entity_entry = self.entity_registry.async_get(incompat["entity_id"])
                if not entity_entry:
                    _LOGGER.warning("Entity %s not found in registry", incompat["entity_id"])
                    continue
                
                # Update unique_id
                _LOGGER.info("🔄 Migrating entity %s: %s -> %s",
                           incompat["entity_id"],
                           incompat["old_unique_id"][-16:],
                           incompat["new_unique_id"][-16:])
                
                self.entity_registry.async_update_entity(
                    incompat["entity_id"],
                    new_unique_id=incompat["new_unique_id"]
                )
                
                report["recreated_entities"].append({
                    "entity_id": incompat["entity_id"],
                    "old_unique_id": incompat["old_unique_id"],
                    "new_unique_id": incompat["new_unique_id"],
                    "platform": incompat["platform"],
                })
                
                success_count += 1
                
            except Exception as e:
                _LOGGER.error("❌ Failed to migrate entity %s: %s",
                            incompat["entity_id"], e)
                report["failed_migrations"].append({
                    "entity_id": incompat["entity_id"],
                    "error": str(e),
                })
        
        return success_count > 0
    
    async def create_fallback_device_config(
        self,
        serial_number: str,
        old_entities: List[er.RegistryEntry]
    ) -> Dict[str, Any]:
        """Create fallback device configuration from old entities.
        
        When entity migration fails, this extracts device information
        from existing entities to recreate the device with new unique_ids.
        
        Args:
            serial_number: Device serial number
            old_entities: List of old entities for this device
            
        Returns:
            Device configuration dict that can be used to recreate the device
        """
        if not old_entities:
            return {}
        
        # Extract device name from first entity
        device_name = None
        device_area = None
        
        # Get device info from device registry if available
        if old_entities[0].device_id:
            from homeassistant.helpers import device_registry as dr
            device_registry = dr.async_get(self.hass)
            device_entry = device_registry.async_get(old_entities[0].device_id)
            
            if device_entry:
                device_name = device_entry.name
                device_area = device_entry.area_id
        
        # Fallback: extract from entity names
        if not device_name and old_entities:
            # Use common prefix from entity names
            entity_names = [e.name or e.original_name for e in old_entities if e.name or e.original_name]
            if entity_names:
                # Find common prefix
                device_name = self._find_common_prefix(entity_names)
        
        # Analyze entity types to determine device type
        entity_platforms = {e.domain for e in old_entities}
        
        device_type = "unknown"
        if "binary_sensor" in entity_platforms and any("btn" in e.unique_id for e in old_entities):
            device_type = "ew_transmitter"
        elif "sensor" in entity_platforms and any("temperature" in e.unique_id for e in old_entities):
            device_type = "ewneo_sensor"
        elif "cover" in entity_platforms:
            device_type = "ew_receiver"
        elif "switch" in entity_platforms:
            device_type = "ewneo_transceiver"
        
        fallback_config = {
            "serial_number": serial_number,
            "name": device_name or f"Device {serial_number[-8:]}",
            "type": device_type,
            "area": device_area,
            "recreated_from_entities": True,
            "recreated_at": datetime.now().isoformat(),
            "original_entities": [
                {
                    "entity_id": e.entity_id,
                    "unique_id": e.unique_id,
                    "platform": e.domain,
                    "name": e.name or e.original_name,
                }
                for e in old_entities
            ],
        }
        
        _LOGGER.info("📦 Created fallback config for device %s (type=%s, %d entities)",
                    serial_number[-8:], device_type, len(old_entities))
        
        return fallback_config
    
    def _find_common_prefix(self, names: List[str]) -> str:
        """Find common prefix from a list of names."""
        if not names:
            return "Device"
        
        if len(names) == 1:
            # Remove common suffixes like " Temperature", " Button 1", etc.
            name = names[0]
            for suffix in [" Temperature", " Humidity", " Battery", " Button", " Last", " State"]:
                if name.endswith(suffix):
                    return name[:-len(suffix)].strip()
            return name
        
        # Find common prefix among multiple names
        prefix = names[0]
        for name in names[1:]:
            while not name.startswith(prefix) and prefix:
                prefix = prefix[:-1]
        
        return prefix.strip() or "Device"
    
    async def cleanup_orphaned_entities(
        self,
        config_entry_id: str,
        valid_serials: Set[str]
    ) -> int:
        """Remove orphaned entities that don't belong to any valid device.
        
        SAFETY: Uses startswith() instead of substring matching to avoid
        false positives. Has a safety limit of 20 entities per cleanup pass.
        
        Args:
            config_entry_id: Config entry ID
            valid_serials: Set of valid device serial numbers
            
        Returns:
            Number of entities removed
        """
        removed_count = 0
        MAX_REMOVALS = 20  # Safety limit to prevent mass deletion
        
        existing_entities = self._get_existing_entities(config_entry_id)
        
        if not valid_serials:
            _LOGGER.info("⚠️ No valid serials provided for orphan cleanup — skipping to prevent data loss")
            return 0
        
        for entity in existing_entities:
            if removed_count >= MAX_REMOVALS:
                _LOGGER.warning("⚠️ Safety limit reached: stopped orphan cleanup after removing %d entities", MAX_REMOVALS)
                break
            
            if not entity.unique_id:
                continue
            
            # Check if entity belongs to any valid device (use startswith for safe matching)
            belongs_to_valid_device = any(
                entity.unique_id.startswith(serial) for serial in valid_serials
            )
            
            if not belongs_to_valid_device:
                # Check if it's the gateway sensor or other special entities
                if "gateway" in entity.unique_id or "rx11" in entity.unique_id.lower():
                    continue
                
                _LOGGER.info("🗑️ Removing orphaned entity: %s (unique_id=%s)",
                           entity.entity_id, entity.unique_id[-16:])
                
                try:
                    self.entity_registry.async_remove(entity.entity_id)
                    removed_count += 1
                except Exception as e:
                    _LOGGER.warning("⚠️ Failed to remove orphaned entity %s: %s", entity.entity_id, e)
        
        if removed_count > 0:
            _LOGGER.info("✅ Removed %d orphaned entities", removed_count)
        
        return removed_count


async def cleanup_legacy_battery_sensors(
    hass: HomeAssistant,
    config_entry_id: str,
    managed_devices: Dict[str, Dict[str, Any]]
) -> int:
    """Remove legacy battery percentage sensors for EWneo devices.
    
    EWneo sensors should only have a battery_warning binary_sensor,
    not a battery percentage sensor. This removes any legacy battery
    sensors that may have been created by older code.
    
    Returns:
        Number of legacy sensors removed
    """
    entity_registry = er.async_get(hass)
    removed_count = 0
    
    for serial_number, device_info in managed_devices.items():
        device_type = device_info.get("type", device_info.get("device_type", ""))
        
        # Only process EWneo sensor devices
        if device_type not in ["ewneo_sensor", "ew_sensor"]:
            continue
        
        # Find and remove battery percentage sensor
        # Pattern: {serial}_battery (without "warning")
        battery_sensor_id = f"{serial_number}_battery"
        
        for entity in entity_registry.entities.values():
            if entity.config_entry_id != config_entry_id:
                continue
            if entity.platform != DOMAIN:
                continue
            
            # Check for exact battery sensor (not battery_warning)
            if entity.unique_id and entity.unique_id.endswith("_battery"):
                # Make sure it's not battery_warning, and use startswith for safe matching
                if entity.unique_id.startswith(serial_number) and "warning" not in entity.unique_id:
                    _LOGGER.info("🔋 Removing legacy battery percentage sensor: %s", entity.entity_id)
                    try:
                        entity_registry.async_remove(entity.entity_id)
                        removed_count += 1
                    except Exception as e:
                        _LOGGER.warning("⚠️ Failed to remove legacy battery sensor %s: %s", entity.entity_id, e)
    
    if removed_count > 0:
        _LOGGER.info("🔋 Removed %d legacy battery percentage sensors for EWneo devices", removed_count)
    
    return removed_count


async def cleanup_duplicate_entities(
    hass: HomeAssistant,
    config_entry_id: str,
    managed_devices: Dict[str, Dict[str, Any]]
) -> int:
    """Remove duplicate entities that exist both with and without registration_id suffix.
    
    When a device is re-learned with the registration_id system, new entities
    with a suffix like '_a1b2c3' are created. If old entities without suffix
    still exist in the registry, this causes duplicates.
    
    This function removes the older entities (without suffix) when duplicates exist.
    
    Returns:
        Number of duplicate entities removed
    """
    entity_registry = er.async_get(hass)
    entities_to_remove = []
    
    # Get all entities for this config entry
    all_entities = [
        entity for entity in entity_registry.entities.values()
        if entity.config_entry_id == config_entry_id and entity.platform == DOMAIN
    ]
    
    # Group entities by base pattern (serial_number_type)
    for serial_number, device_info in managed_devices.items():
        registration_id = device_info.get("registration_id")
        
        # Only check devices that have registration_id (new system)
        if not registration_id:
            continue
        
        # Find entities for this device (use startswith to avoid substring false-matches)
        device_entities = [e for e in all_entities if e.unique_id and e.unique_id.startswith(serial_number)]
        
        # Separate entities with and without registration_id suffix
        entities_with_suffix = []
        entities_without_suffix = []
        
        for entity in device_entities:
            unique_id = entity.unique_id
            # Check if unique_id has a registration_id suffix (ends with _xxxxxx where x is hex)
            parts = unique_id.rsplit('_', 1)
            if len(parts) == 2 and len(parts[1]) == 6 and all(c in '0123456789abcdef' for c in parts[1]):
                entities_with_suffix.append(entity)
            else:
                entities_without_suffix.append(entity)
        
        # If device has entities with suffix, remove matching entities without suffix
        if entities_with_suffix and entities_without_suffix:
            # Build a map of entity types from suffix entities
            suffix_base_ids = set()
            for entity in entities_with_suffix:
                # Get base unique_id without suffix
                base_id = entity.unique_id.rsplit('_', 1)[0]
                suffix_base_ids.add(base_id)
            
            # Remove entities without suffix that match base IDs
            for entity in entities_without_suffix:
                if entity.unique_id in suffix_base_ids:
                    entities_to_remove.append(entity)
                    _LOGGER.info("🧹 Marking duplicate entity for removal: %s (old: %s)", 
                               entity.entity_id, entity.unique_id)
    
    # Remove duplicates
    removed_count = 0
    for entity in entities_to_remove:
        try:
            entity_registry.async_remove(entity.entity_id)
            removed_count += 1
            _LOGGER.debug("  - Removed: %s", entity.entity_id)
        except Exception as e:
            _LOGGER.warning("⚠️ Failed to remove duplicate entity %s: %s", entity.entity_id, e)
    
    if removed_count > 0:
        _LOGGER.info("🧹 Removed %d duplicate entities (old format without registration_id suffix)", removed_count)
    
    return removed_count


async def migrate_entities_if_needed(
    hass: HomeAssistant,
    config_entry_id: str,
    managed_devices: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """Main entry point for entity migration.
    
    Call this during integration setup to check for and handle
    entity incompatibilities.
    
    Args:
        hass: Home Assistant instance
        config_entry_id: Config entry ID
        managed_devices: Dictionary of managed devices from DeviceManager
        
    Returns:
        Migration report
    """
    helper = EntityMigrationHelper(hass)
    
    # Check and migrate incompatible entities
    report = await helper.check_and_migrate_entities(config_entry_id, managed_devices)
    
    # Cleanup duplicate entities (old format without registration_id suffix)
    duplicates_removed = await cleanup_duplicate_entities(hass, config_entry_id, managed_devices)
    report["duplicate_entities_removed"] = duplicates_removed
    
    # Cleanup legacy battery percentage sensors for EWneo devices
    legacy_battery_removed = await cleanup_legacy_battery_sensors(hass, config_entry_id, managed_devices)
    report["legacy_battery_sensors_removed"] = legacy_battery_removed
    
    return report
