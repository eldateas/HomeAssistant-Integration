"""Base device class for all EASYWAVE devices."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .enums import DeviceType, DeviceSubtype, OperatingMode


class BaseDevice(ABC):
    """Abstrakte Basisklasse für alle Geräte."""
    
    def __init__(
        self,
        serial_number: str,
        device_type: DeviceType,
        subtype: DeviceSubtype = DeviceSubtype.UNKNOWN,
        operating_mode: OperatingMode = OperatingMode.UNKNOWN,
        name: str = None,
        **kwargs
    ):
        """Initialize the device."""
        self.serial_number = serial_number
        self.device_type = device_type
        self.subtype = subtype
        self.operating_mode = operating_mode
        self.name = name or self._generate_default_name()
        self.properties = kwargs
        self._last_seen = None
        self._battery_level = None
    
    def _generate_default_name(self) -> str:
        """Generate a default name based on device type and serial."""
        type_name = self.device_type.value.replace("_", " ").title()
        # Fix Ewneo naming (should be Easywave neo, not Ewneo)
        type_name = type_name.replace("Ewneo", "Easywave neo")
        short_serial = self.serial_number if len(self.serial_number) > 6 else self.serial_number
        return f"{type_name} {short_serial}"
    
    @property
    @abstractmethod
    def supported_entity_types(self) -> List[str]:
        """Return list of supported Home Assistant entity types."""
        pass
    
    @property
    def device_info_dict(self) -> Dict[str, Any]:
        """Return device information for Home Assistant."""
        return {
            "identifiers": {("easywave", self.serial_number)},
            "name": self.name,
            "model": self._get_model_name(),
        }
    
    def _get_model_name(self) -> str:
        """Get model name based on device type and subtype."""
        base_name = self.device_type.value.replace("_", " ").title()
        # Fix Ewneo naming (should be Easywave neo, not Ewneo)
        base_name = base_name.replace("Ewneo", "Easywave neo")
        if self.subtype != DeviceSubtype.UNKNOWN:
            base_name += f" {self.subtype.value.title()}"
        return base_name
    
    @property
    def battery_level(self) -> Optional[int]:
        """Return battery level if available."""
        return self._battery_level
    
    @battery_level.setter
    def battery_level(self, value: Optional[int]) -> None:
        """Set battery level."""
        self._battery_level = value
    
    def update_properties(self, **kwargs) -> None:
        """Update device properties."""
        self.properties.update(kwargs)
    
    def get_state(self) -> Dict[str, Any]:
        """Get current device state for coordinator."""
        state = {
            "name": self.name,
            "device_type": self.device_type.value,
            "subtype": self.subtype.value if self.subtype != DeviceSubtype.UNKNOWN else None,
            "operating_mode": self.operating_mode.value if self.operating_mode != OperatingMode.UNKNOWN else None,
            "battery_level": self.battery_level,
            "last_seen": self._last_seen,
        }
        
        # Add device-specific properties
        state.update(self.properties)
        
        return state
    
    @abstractmethod
    def process_telegram(self, telegram_data: Dict[str, Any]) -> None:
        """Process incoming telegram for this device."""
        pass
