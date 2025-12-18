"""Combined temperature/humidity sensor implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging
from datetime import datetime

from ....base import BaseSensor, DeviceType, DeviceSubtype, OperatingMode

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EW_SENSOR_DATA = 0x02


class RX11CombinedSensor(BaseSensor):
    """Combined temperature/humidity sensor implementation for RX11 transceiver.
    
    Handles sensors that provide both temperature and humidity readings.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize combined sensor."""
        # Extract and remove device_type and available_sensors from kwargs
        device_type = kwargs.pop('device_type', DeviceType.EWNEO_SENSOR)  # Only EWneo has sensors
        available_sensors = kwargs.pop("available_sensors", ["temperature", "humidity"])
        
        super().__init__(*args, device_type=device_type, subtype=DeviceSubtype.UNKNOWN, **kwargs)
        self.operating_mode = OperatingMode.EVENT_TRIGGERED
        
        # Track available sensors
        self.available_sensors = available_sensors
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["sensor"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        # Process incoming telegram for combined sensor
        info_type = telegram_data.get("info_type")
        
        # Only EWneo sensors exist, so use NEO telegram processing
        if info_type == TELEGRAM_NEO_SENSOR_DATA:
            self._process_combined_sensor_data(telegram_data)
        
        return self.get_sensor_data()
    
    def _process_combined_sensor_data(self, telegram_data: Dict[str, Any]) -> None:
        """Process combined sensor data."""
        timestamp = datetime.now()
        self.properties["timestamp"] = timestamp
        self._last_seen = timestamp.timestamp()
        
        # Process temperature if available
        if "temperature" in self.available_sensors:
            temperature = telegram_data.get("temperature")
            if temperature is not None:
                self.update_sensor_value("temperature", temperature)
                _LOGGER.debug("Combined Sensor %s temperature: %.1f°C", 
                            self.serial_number[-6:], temperature)
        
        # Process humidity if available
        if "humidity" in self.available_sensors:
            humidity = telegram_data.get("humidity")
            if humidity is not None:
                self.update_sensor_value("humidity", humidity)
                _LOGGER.debug("Combined Sensor %s humidity: %.1f%%", 
                            self.serial_number[-6:], humidity)
        
        # Process battery
        self._process_battery_data(telegram_data)
    
    def _process_battery_data(self, telegram_data: Dict[str, Any]) -> None:
        """Process battery data."""
        battery_level = telegram_data.get("battery_level")
        if battery_level is not None:
            self.update_sensor_value("battery", battery_level)
            self.battery_level = battery_level
            _LOGGER.debug("Combined Sensor %s battery: %d%%", 
                        self.serial_number[-6:], battery_level)
    
    def get_sensor_data(self) -> Dict[str, Any]:
        """Get current sensor data."""
        data = {}
        
        # Add all available sensor values
        for sensor_type in self.available_sensors:
            value = self._sensor_values.get(sensor_type)
            if value is not None:
                data[sensor_type] = value
        
        # Add battery data
        if self.battery_level is not None:
            data["battery_level"] = self.battery_level
            data["battery_status"] = "good" if self.battery_level > 20 else "low"
        
        return data
    
    def get_measurement_unit(self, sensor_type: str) -> Optional[str]:
        """Get measurement unit for sensor type."""
        units = {
            "temperature": "°C",
            "humidity": "%",
            "battery": "%",
        }
        return units.get(sensor_type)
    
    def get_available_sensors(self) -> List[str]:
        """Get list of available sensors."""
        return self.available_sensors


def create_rx11_combined_sensor(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11CombinedSensor:
    """Factory function to create combined sensor."""
    # Extract device_type from device_info to avoid duplicate parameter
    device_type = device_info.get('device_type', DeviceType.EWNEO_SENSOR)  # Only EWneo has sensors
    
    # Filter out device_type from kwargs if it exists
    filtered_kwargs = {k: v for k, v in kwargs.items() if k != 'device_type'}
    
    return RX11CombinedSensor(
        serial_number, 
        name=device_info.get('name'),
        available_sensors=device_info.get('available_sensors', ['temperature', 'humidity']),
        device_type=device_type,
        **filtered_kwargs
    )