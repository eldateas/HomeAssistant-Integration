"""Sensor behavior mixin for sensor devices."""
from __future__ import annotations

from typing import Any, Optional


class SensorBehaviorMixin:
    """Mixin für Sensor-Verhalten (Temperatur, Feuchtigkeit, etc.)."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not hasattr(self, '_sensor_values'):
            self._sensor_values = {}
    
    def get_sensor_value(self, sensor_type: str) -> Optional[Any]:
        """Get current sensor value."""
        return self._sensor_values.get(sensor_type)
    
    def set_sensor_value(self, sensor_type: str, value: Any) -> None:
        """Set sensor value internally."""
        self._sensor_values[sensor_type] = value
    
    def get_temperature(self) -> Optional[float]:
        """Get temperature value."""
        return self.get_sensor_value('temperature')
    
    def get_humidity(self) -> Optional[float]:
        """Get humidity value."""
        return self.get_sensor_value('humidity')
    
    def get_wind_speed(self) -> Optional[float]:
        """Get wind speed value."""
        return self.get_sensor_value('wind_speed')
    
    def get_rain_rate(self) -> Optional[float]:
        """Get rain rate value."""
        return self.get_sensor_value('rain')
