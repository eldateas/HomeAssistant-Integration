"""Simplified device configuration facade for EASYWAVE integration.

This is a thin wrapper around DeviceManager for backward compatibility.
Most operations are delegated directly to DeviceManager.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant

from .device_manager import DeviceManager, DeviceAvailability
from .device_migration import migrate_to_device_manager, create_compatibility_wrapper

_LOGGER = logging.getLogger(__name__)


class DeviceConfigManager:
    """Simplified facade for device configuration (delegates to DeviceManager)."""

    def __init__(self, hass: HomeAssistant, config_entry_id: str) -> None:
        """Initialize device config manager."""
        self.hass = hass
        self.config_entry_id = config_entry_id
        self.device_manager = DeviceManager(hass, config_entry_id)
        self._compatibility_wrapper = create_compatibility_wrapper(self.device_manager)
        
    async def load_devices(self) -> Dict[str, Any]:
        """Load devices from configuration file."""
        await self.device_manager.load()
        return await self._compatibility_wrapper.load_devices()

    async def save_device_config(self, devices: Dict[str, Dict[str, Any]], force: bool = False) -> bool:
        """Save device configuration."""
        return await self._compatibility_wrapper.save_device_config(devices, force)

    async def load_device_whitelist(self) -> Dict[str, Dict[str, Any]]:
        """Load device whitelist."""
        return await self._compatibility_wrapper.load_device_whitelist()

    async def save_device_whitelist(self, whitelist: Dict[str, Dict[str, Any]]) -> bool:
        """Save device whitelist."""
        return await self._compatibility_wrapper.save_device_whitelist(whitelist)

    async def add_device_to_whitelist(
        self,
        serial_number: str,
        device_type: str,
        name: Optional[str] = None,
        rx11_index: Optional[int] = None,
        area: Optional[str] = None,
        additional_info: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Add device to whitelist."""
        self.device_manager.add_device(
            serial_number=serial_number,
            device_type=device_type,
            name=name,
            rx11_index=rx11_index,
            area=area,
            extra_data=additional_info
        )
        return await self.device_manager.save()

    async def remove_device_from_whitelist(self, serial_number: str) -> bool:
        """Remove device from whitelist."""
        result = self.device_manager.remove_device(serial_number)
        if result:
            await self.device_manager.save()
        return result

    async def remove_device(self, serial_number: str) -> bool:
        """Remove a device from configuration."""
        result = self.device_manager.remove_device(serial_number)
        if result:
            await self.device_manager.save()
        return result
