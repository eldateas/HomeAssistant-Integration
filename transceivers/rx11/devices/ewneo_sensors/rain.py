"""Rain sensor implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging
from datetime import datetime

from ....base import BaseSensor, DeviceType, DeviceSubtype, OperatingMode

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_NEO_SENSOR_DATA = 0x02


class RX11RainSensor(BaseSensor):
    """Rain sensor implementation for RX11 transceiver.
    
    Supports EWneo rain sensors (binary detection).
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize rain sensor."""
        super().__init__(*args, device_type=DeviceType.EWNEO_SENSOR, 
                        subtype=DeviceSubtype.RAIN, **kwargs)
        self.operating_mode = OperatingMode.EVENT_TRIGGERED
        self._rain_detected = False
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["binary_sensor"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for rain sensor."""
        info_type = telegram_data.get("info_type")
        
        if info_type == TELEGRAM_NEO_SENSOR_DATA:
            self._process_rain_data(telegram_data)
        
        return self.get_sensor_data()
    
    def _process_rain_data(self, telegram_data: Dict[str, Any]) -> None:
        """Process rain sensor data."""
        data = telegram_data.get("data", {})
        
        # Extract rain detection state
        rain_detected = data.get("rain_detected", False)
        if rain_detected != self._rain_detected:
            self._rain_detected = rain_detected
            self._last_seen = datetime.now()
            _LOGGER.debug("Rain sensor %s: Rain %s", 
                         self.serial_number[-6:], "detected" if rain_detected else "not detected")
        
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
            "rain_detected": self._rain_detected,
            "battery": self._battery_level,
            "last_seen": self._last_seen,
        }
    
    def get_measurement_unit(self, sensor_type: str) -> Optional[str]:
        """Get measurement unit for sensor type."""
        units = {
            "rain_detected": None,  # Binary sensor
            "battery": "%"
        }
        return units.get(sensor_type)


def create_rx11_rain_sensor(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11RainSensor:
    """Factory function to create rain sensor."""
    return RX11RainSensor(
        serial_number=serial_number,
        device_info=device_info,
        **kwargs
    )