"""Base entity for ELDAT integration."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DEVICE_ICONS, DEVICE_TYPE_CODE_MAP
from .coordinator import EldatCoordinator
from .helpers import build_model_description
from .translations import get_language, t_receiver, t_transmitter, t_sensor_device, DEFAULT_LANGUAGE

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
    
    def _get_gateway_identifier(self) -> tuple | None:
        """Get the RX11 gateway identifier for via_device linkage.
        
        Returns the identifier tuple for the RX11 gateway device if available,
        enabling devices to inherit the gateway's availability status.
        """
        if hasattr(self.coordinator, 'config_entry') and self.coordinator.config_entry:
            return (DOMAIN, f"{self.coordinator.config_entry.entry_id}_gateway")
        return None
    
    @property
    def device_info(self) -> DeviceInfo:
        """Return device info - dynamically generated from current coordinator data.
        
        Devices are linked to the RX11 gateway via via_device, which allows
        Home Assistant to inherit the gateway's availability status automatically.
        """
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
        
        # Get language for translations
        lang = get_language(self.hass) if self.hass else DEFAULT_LANGUAGE
        
        # Build model description with current data (language-aware)
        model_description = build_model_description(device_type, current_device_info, lang)
        
        # Check if device already exists in registry
        # If the device exists and has a name_by_user, keep the original default name
        # so that HA continues to use the user-defined override
        existing_default_name = None
        if self.hass:
            from homeassistant.helpers import device_registry as dr
            device_registry = dr.async_get(self.hass)
            existing_device = device_registry.async_get_device(identifiers={(DOMAIN, self._serial_number)})
            if existing_device:
                # Device exists - use the existing default name to preserve name_by_user
                existing_default_name = existing_device.name
        
        # Generate device name (default name for new devices, or use existing default)
        if existing_default_name:
            # Use existing default name - this preserves name_by_user
            device_name = existing_default_name
        elif device_type.startswith("ewneo_") and device_type != "ewneo_sensor":
            # EWneo bidirectional devices: use ewneo_index (EWB_GET_FD_SERIAL index)
            ewneo_index = current_device_info.get("ewneo_index")
            receiver_label = t_receiver(lang)
            if ewneo_index is not None:
                device_name = f"Easywave neo {receiver_label} #{ewneo_index + 1}"
            else:
                # Fallback if no ewneo_index available
                device_name = f"Easywave neo {receiver_label} {self._serial_number[-4:]}"
        else:
            # Other devices: use existing name or generate default
            device_name = current_device_info.get("name")
            if not device_name:
                device_name = f"Easywave device {self._serial_number}"
        
        # Get gateway identifier for via_device linkage
        # This links devices to the RX11 gateway, allowing inheritance of availability status
        gateway_identifier = self._get_gateway_identifier()
        
        # Return device_info with via_device to link to RX11 gateway
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial_number)},
            name=device_name,
            model=model_description,
            via_device=gateway_identifier,
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
        # Get current device info from coordinator (always up-to-date)
        current_device_info = (
            self.coordinator._registered_devices.get(self._serial_number) or 
            self.coordinator.devices.get(self._serial_number) or 
            self._device_info
        )
        
        attributes = {
            "serial_number": self._serial_number,
        }
        
        # Add EWneo index if available (for bidirectional EWneo devices)
        ewneo_index = current_device_info.get("ewneo_index")
        if ewneo_index is not None:
            attributes["ewneo_index"] = ewneo_index
        
        return attributes

    def _is_rx11_connected(self) -> bool:
        """Check if the RX11 transceiver is connected.
        
        This is the base availability check that all entities inherit.
        Subclasses can add additional availability checks on top of this.
        """
        # Check coordinator update success
        if not self.coordinator.last_update_success:
            return False
        
        # Check transceiver connection
        transceiver = getattr(self.coordinator, 'transceiver', None)
        if transceiver and hasattr(transceiver, 'is_connected'):
            return transceiver.is_connected
        
        return True

    @property
    def available(self) -> bool:
        """Return True if entity is available - follows RX11 connection status.
        
        This base implementation checks the RX11 transceiver connection.
        Since devices are linked via via_device to the RX11 gateway,
        Home Assistant will automatically propagate unavailability.
        
        Subclasses can override this to add additional device-specific
        availability checks (e.g., timeout, reachability) while still
        inheriting the RX11 connection status via the base check.
        """
        return self._is_rx11_connected()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        _LOGGER.debug("Added entity: %s (%s)", self.name, self._serial_number)

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        await super().async_will_remove_from_hass()
        _LOGGER.debug("Removing entity: %s (%s)", self.name, self._serial_number)
        
        # Purge history/recorder data for this entity when it's removed
        try:
            if self.entity_id:
                await self.coordinator._purge_entity_history([self.entity_id])
                _LOGGER.info("🧹 Entity history purged: %s", self.entity_id)
        except Exception as e:
            _LOGGER.debug("Could not purge history for entity %s: %s", self.entity_id, e)