"""Wind sensor implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging
from datetime import datetime

from ....base import BaseSensor, DeviceType, DeviceSubtype, OperatingMode

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_NEO_SENSOR_DATA = 0x02


class RX11WindSensor(BaseSensor):
    """Wind sensor implementation for RX11 transceiver.
    
    Supports EWneo wind speed sensors.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize wind sensor."""
        super().__init__(*args, device_type=DeviceType.EWNEO_SENSOR, 
                        subtype=DeviceSubtype.WIND_SPEED, **kwargs)
        self.operating_mode = OperatingMode.EVENT_TRIGGERED
        self._wind_speed = 0.0  # Wind speed in m/s
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["sensor"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for wind sensor."""
        info_type = telegram_data.get("info_type")
        
        if info_type == TELEGRAM_NEO_SENSOR_DATA:
            self._process_wind_data(telegram_data)
        
        return self.get_sensor_data()
    
    def _process_wind_data(self, telegram_data: Dict[str, Any]) -> None:
        """Process wind sensor data."""
        data = telegram_data.get("data", {})
        
        # Extract wind speed
        wind_speed = data.get("wind_speed", 0.0)
        if wind_speed != self._wind_speed:
            self._wind_speed = wind_speed
            self._last_seen = datetime.now()
            _LOGGER.debug("Wind sensor %s: Speed=%.1f m/s", 
                         self.serial_number[-6:], wind_speed)
        
        # Process battery data if available
        if "battery" in data:
            self._battery_level = data["battery"]
    
    def _process_battery_data(self, telegram_data: Dict[str, Any]) -> None:
        """Process battery data."""
        data = telegram_data.get("data", {})
        if "battery" in data:
            self._battery_level = data["battery"]
    
    def get_sensor_data(self) -> Dict[str, Any]:
        """Get current sensor data."""
        return {
            "wind_speed": self._wind_speed,
            "battery": self._battery_level,
            "last_seen": self._last_seen,
        }
    
    def get_measurement_unit(self, sensor_type: str) -> Optional[str]:
        """Get measurement unit for sensor type."""
        units = {
            "wind_speed": "m/s",
            "battery": "%"
        }
        return units.get(sensor_type)


def create_rx11_wind_sensor(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11WindSensor:
    """Factory function to create wind sensor."""
    return RX11WindSensor(
        serial_number=serial_number,
        device_info=device_info,
        **kwargs
    )