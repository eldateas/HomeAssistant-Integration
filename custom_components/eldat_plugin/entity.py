"""Base entity for ELDAT integration."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DEVICE_ICONS
from .coordinator import EldatCoordinator

_LOGGER = logging.getLogger(__name__)


class EldatEntity(CoordinatorEntity):
    """Base class for ELDAT entities."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
    ) -> None:
        """Initialize entity."""
        super().__init__(coordinator)
        
        self._serial_number = serial_number
        
        # CRITICAL: Remove ALL fields that could link to old/wrong config entries
        # Home Assistant will automatically use the correct config_entry_id from the platform
        cleaned_device_info = device_info.copy() if device_info else {}
        for problematic_field in ['config_entry_id', 'via_device', 'config_subentry_id', 
                                  'via_device_id', 'entry_id']:
            cleaned_device_info.pop(problematic_field, None)
        
        self._device_info = cleaned_device_info
        self._attr_has_entity_name = True
        
        # Set coordinator context to None for default behavior
        self.coordinator_context = None
        
        # Set device info - WICHTIG: Verwende immer die aktuelle config_entry_id
        device_type = cleaned_device_info.get("type", "unknown")
        
        # Erstelle device_info OHNE via_device, um Probleme mit alten config_entry_ids zu vermeiden
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial_number)},
            name=cleaned_device_info.get("name", f"ELDAT Device {serial_number[-6:]}"),
            manufacturer="ELDAT EaS GmbH",
            model=device_type.replace("_", " ").title(),
            sw_version="1.0.0",
            hw_version="Unknown",
        )
        
        # Explizit config_entry_id NICHT setzen - Home Assistant verwendet automatisch die richtige
        # Dies verhindert Probleme mit alten/ungültigen config_entry_ids aus gespeicherten Daten
        
        # Set default icon based on device type
        if not hasattr(self, '_attr_icon'):
            self._attr_icon = DEVICE_ICONS.get(device_type, "mdi:devices")

    @property
    def serial_number(self) -> str:
        """Return device serial number."""
        return self._serial_number

    @property
    def device_type(self) -> str:
        """Return device type."""
        return self._device_info.get("type", "unknown")

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        attributes = {
            "serial_number": self._serial_number,
            "device_type": self._device_info.get("type", "unknown"),
            "integration": DOMAIN,
        }
        
        # Add device-specific attributes
        if "channels" in self._device_info:
            attributes["channels"] = self._device_info["channels"]
        
        if "detected_via" in self._device_info:
            attributes["detected_via"] = self._device_info["detected_via"]
        
        if "info_type" in self._device_info:
            attributes["info_type"] = self._device_info["info_type"]
        
        return attributes

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        # Entity is available if coordinator is working AND transceiver is connected
        if not self.coordinator.last_update_success:
            return False
        # Check transceiver connection
        transceiver = getattr(self.coordinator, 'transceiver', None)
        if transceiver and hasattr(transceiver, 'is_connected'):
            return transceiver.is_connected
        return True

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        _LOGGER.debug("Added entity: %s (%s)", self.name, self._serial_number)

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        await super().async_will_remove_from_hass()
        _LOGGER.debug("Removing entity: %s (%s)", self.name, self._serial_number)