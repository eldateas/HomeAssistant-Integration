"""Simplified entity tracking - relies on HomeAssistant's entity registry for persistence.

This module provides a lightweight in-memory cache to track entity creation during the current
session only. All persistent tracking is handled by HomeAssistant's core entity registry.
"""
from __future__ import annotations

import logging
from typing import Set, Dict, Any, Optional
from threading import Lock

_LOGGER = logging.getLogger(__name__)


class EldatEntityRegistry:
    """Lightweight in-memory entity tracker for the current session only.
    
    This class does NOT persist entity information - it only tracks entities created
    in the current session to prevent duplicate creation attempts. All persistent
    entity tracking is handled by HomeAssistant's core entity registry.
    """
    
    def __init__(self):
        """Initialize the in-memory entity tracker."""
        self._lock = Lock()
        # Simple set to track entities created THIS SESSION only
        self._session_entities: Set[str] = set()
        # Track devices being removed to prevent race conditions
        self._devices_being_removed: Set[str] = set()
        
    def is_entity_created_this_session(self, unique_id: str) -> bool:
        """Check if an entity was created during this session."""
        with self._lock:
            return unique_id in self._session_entities
    
    def mark_entity_created(self, unique_id: str, device_serial: str) -> bool:
        """Mark an entity as created in this session. Returns True if newly marked, False if already exists.
        
        Note: This only tracks session state. Always check HA's entity registry first
        for persistent entity state.
        """
        with self._lock:
            # Check if device is being removed
            if device_serial in self._devices_being_removed:
                _LOGGER.debug("Device %s is being removed, skipping entity creation tracking", 
                              device_serial[-8:])
                return False
                
            if unique_id in self._session_entities:
                _LOGGER.debug("Entity %s already tracked in this session", unique_id[-16:])
                return False
            
            self._session_entities.add(unique_id)
            _LOGGER.debug("Marked entity %s as created in this session", unique_id[-16:])
            return True
    
    def mark_device_for_removal(self, device_serial: str) -> None:
        """DEPRECATED: Use coordinator.device_lifecycle_manager instead.
        
        This method is kept for backwards compatibility only.
        Device lifecycle is now managed by DeviceLifecycleManager.
        """
        with self._lock:
            self._devices_being_removed.add(device_serial)
            _LOGGER.debug("⚠️ DEPRECATED: mark_device_for_removal() used - migrate to DeviceLifecycleManager")
    
    def complete_device_removal(self, device_serial: str) -> None:
        """DEPRECATED: Use coordinator.device_lifecycle_manager instead.
        
        This method is kept for backwards compatibility only.
        Device lifecycle is now managed by DeviceLifecycleManager.
        """
        with self._lock:
            # Remove from removal tracking
            self._devices_being_removed.discard(device_serial)
            
            # Clean up session entities for this device (use startswith for safety)
            entities_to_remove = [e for e in self._session_entities if e.startswith(device_serial)]
            for entity_id in entities_to_remove:
                self._session_entities.discard(entity_id)
                
            _LOGGER.debug("⚠️ DEPRECATED: complete_device_removal() used - migrate to DeviceLifecycleManager")
    
    def clear(self) -> None:
        """Clear all session tracking (useful for integration reload)."""
        with self._lock:
            entity_count = len(self._session_entities)
            self._session_entities.clear()
            self._devices_being_removed.clear()
            _LOGGER.info("Cleared session entity tracking (%d entities)", entity_count)


# Global instance to be shared across all platforms
_entity_registry: Optional[EldatEntityRegistry] = None


def get_entity_registry() -> EldatEntityRegistry:
    """Get the global entity registry instance."""
    global _entity_registry
    if _entity_registry is None:
        _entity_registry = EldatEntityRegistry()
        _LOGGER.info("🚀 Initialized global ELDAT entity registry")
    return _entity_registry


def reset_entity_registry() -> None:
    """Reset the global entity registry (for testing or cleanup)."""
    global _entity_registry
    if _entity_registry:
        _entity_registry.clear()
    _entity_registry = EldatEntityRegistry()
    _LOGGER.info("🔄 Reset global ELDAT entity registry")