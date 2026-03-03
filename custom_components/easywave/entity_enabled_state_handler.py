"""Helper module for handling entity enabled state during device lifecycle.

This module ensures that when a device is disabled, deleted, and re-learned,
its entities are properly re-enabled instead of staying disabled.
"""
from __future__ import annotations

import logging
from typing import Optional

from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)


def ensure_entity_enabled(
    ha_entity_registry: er.EntityRegistry,
    entity_type: str,
    domain: str,
    unique_id: str,
) -> bool:
    """Ensure an entity is enabled, even if it was previously disabled.
    
    This function checks if an entity exists and has been disabled.
    If so, it re-enables it. This is critical for handling the case where:
    1. A device is disabled
    2. The device is deleted
    3. The device is re-learned
    
    Without this, the old disabled entity persists and prevents the new one from being created.
    
    Args:
        ha_entity_registry: Home Assistant entity registry
        entity_type: Type of entity (e.g., "switch", "light", "sensor")
        domain: Integration domain (e.g., "easywave")
        unique_id: Unique ID of the entity
        
    Returns:
        True if entity was found and potentially updated, False if not found
    """
    try:
        # Find the entity by its unique_id
        existing_entity_id = ha_entity_registry.async_get_entity_id(
            entity_type, domain, unique_id
        )
        
        if not existing_entity_id:
            return False
        
        # Get the full entity entry
        entry = ha_entity_registry.async_get(existing_entity_id)
        
        if not entry:
            return True  # Entity ID found but entry not available (unusual)
        
        # Check if entity is disabled
        if entry.disabled_by:
            _LOGGER.debug(
                "Found disabled entity %s (disabled_by=%s), re-enabling it",
                existing_entity_id,
                entry.disabled_by
            )
            # Re-enable the entity
            ha_entity_registry.async_update_entity(existing_entity_id, disabled_by=None)
            _LOGGER.info("✅ Re-enabled entity: %s", existing_entity_id)
            return True
        else:
            _LOGGER.debug(
                "Entity %s already exists and is enabled, will be skipped from creation",
                existing_entity_id
            )
            return True
    except Exception as e:
        _LOGGER.error("Error checking/enabling entity for unique_id %s: %s", unique_id, e)
        return False


def should_skip_entity_creation(
    ha_entity_registry: er.EntityRegistry,
    entity_type: str,
    domain: str,
    unique_id: str,
    session_registry,
) -> bool:
    """Determine if an entity should be skipped during creation.
    
    An entity should be skipped ONLY if:
    - It exists in the registry AND
    - It is NOT disabled (disabled entities need to be re-enabled)
    - It was not created in this session already
    
    Args:
        ha_entity_registry: Home Assistant entity registry
        entity_type: Type of entity
        domain: Integration domain
        unique_id: Unique ID of the entity
        session_registry: Our custom session entity registry
        
    Returns:
        True if entity should be skipped, False if it should be (re-)created
    """
    try:
        # Check if created this session
        if session_registry.is_entity_created_this_session(unique_id):
            _LOGGER.debug("Entity %s already created in this session, skipping", unique_id[-16:])
            return True
        
        # Check if entity exists in registry
        existing_entity_id = ha_entity_registry.async_get_entity_id(
            entity_type, domain, unique_id
        )
        
        if not existing_entity_id:
            # Entity doesn't exist, don't skip (it will be created)
            return False
        
        # Entity exists, check if it's disabled
        entry = ha_entity_registry.async_get(existing_entity_id)
        
        if entry and entry.disabled_by:
            # Entity exists but is disabled - DON'T skip!
            # It will be re-created and re-enabled
            _LOGGER.debug(
                "Entity %s exists but is disabled (disabled_by=%s), will be re-created",
                existing_entity_id,
                entry.disabled_by
            )
            return False
        
        # Entity exists and is enabled - skip creation
        _LOGGER.debug("Entity %s already exists and is enabled, skipping creation", existing_entity_id)
        return True
        
    except Exception as e:
        _LOGGER.error("Error determining if entity should be skipped: %s", e)
        # On error, don't skip (safer to create and let HA handle duplicates)
        return False
