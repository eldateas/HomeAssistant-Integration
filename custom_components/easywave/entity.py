"""Base entity for ELDAT integration."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DEVICE_ICONS, DEVICE_TYPE_CODE_MAP
from .coordinator import EldatCoordinator
from .helpers import build_model_description

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
        
        # Set default icon based on device type from initial device_info
        device_type = cleaned_device_info.get("type", "unknown")
        if device_type == "unknown" and "device_type_code" in cleaned_device_info:
            device_type_code = cleaned_device_info.get("device_type_code")
            device_type = DEVICE_TYPE_CODE_MAP.get(device_type_code, device_type)
        
        if not hasattr(self, '_attr_icon'):
            self._attr_icon = DEVICE_ICONS.get(device_type, "mdi:devices")
    
    @property
    def device_info(self) -> DeviceInfo:
        """Return device info - dynamically generated from current coordinator data."""
        # Get current device info from coordinator (always up-to-date)
        # Check both registered_devices and devices for the most complete data
        current_device_info = (
            self.coordinator._registered_devices.get(self._serial_number) or 
            self.coordinator.devices.get(self._serial_number) or 
            self._device_info
        )
        
        # Get device type
        device_type = current_device_info.get("type", "unknown")
        if device_type == "unknown" and "device_type_code" in current_device_info:
            device_type_code = current_device_info.get("device_type_code")
            device_type = DEVICE_TYPE_CODE_MAP.get(device_type_code, device_type)
        
        # Build model description with current data
        model_description = build_model_description(device_type, current_device_info)
        
        # Generate device name - use short, simple names for EWneo devices
        if device_type.startswith("ewneo_") and device_type != "ewneo_sensor":
            # EWneo bidirectional devices: use ewneo_index (EWB_GET_FD_SERIAL index)
            ewneo_index = current_device_info.get("ewneo_index")
            if ewneo_index is not None:
                device_name = f"EWneo-Empfänger #{ewneo_index + 1}"
            else:
                # Fallback if no ewneo_index available
                device_name = f"EWneo-Empfänger {self._serial_number[-4:]}"
        else:
            # Other devices: use existing name or generate default
            device_name = current_device_info.get("name")
            if not device_name:
                device_name = f"Easywave device {self._serial_number}"
        
        # Return device_info WITHOUT via_device to prevent issues with old config_entry_ids
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial_number)},
            name=device_name,
            model=model_description,
        )

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
        """Return True if entity is available - follows RX11 connection status."""
        # All entities follow RX11 transceiver connection status
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