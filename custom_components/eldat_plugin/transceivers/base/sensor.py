"""Base sensor class for all ELDAT sensors."""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, Optional

from .device import BaseDevice


class BaseSensor(BaseDevice):
    """Abstrakte Basisklasse für alle Sensoren."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sensor_values = {}
        self._last_reading = None
    
    @property
    def sensor_values(self) -> Dict[str, Any]:
        """Return current sensor values."""
        return self._sensor_values
    
    @property
    def last_reading(self) -> Optional[Any]:
        """Return timestamp of last reading."""
        return self._last_reading
    
    def update_sensor_value(self, sensor_type: str, value: Any) -> None:
        """Update a sensor value."""
        self._sensor_values[sensor_type] = value
        self._last_reading = self.properties.get("timestamp")
    
    def get_state(self) -> Dict[str, Any]:
        """Get current device state including sensor values."""
        state = super().get_state()
        
        # Add sensor-specific data
        state.update(self._sensor_values)
        state["sensor_values"] = self._sensor_values.copy()
        state["last_reading"] = self._last_reading
        
        return state
    
    @abstractmethod
    def get_measurement_unit(self, sensor_type: str) -> Optional[str]:
        """Get measurement unit for a sensor type."""
        pass
