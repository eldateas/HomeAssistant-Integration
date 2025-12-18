"""Temperature sensor implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging
from datetime import datetime

from ....base import (
    BaseSensor, 
    DeviceType, 
    DeviceSubtype, 
    OperatingMode,
    SensorBehaviorMixin,
    EntitySpecsMixin,
)

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EW_SENSOR_DATA = 0x02
TELEGRAM_NEO_SENSOR_DATA = 0x02


class RX11TemperatureSensor(SensorBehaviorMixin, EntitySpecsMixin, BaseSensor):
    """Temperature sensor implementation for RX11 transceiver.
    
    Supports both EW and EWneo temperature sensors.
    Inherits Sensor behavior from SensorBehaviorMixin.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize temperature sensor."""
        # Extract and remove device_type from kwargs before calling super
        device_type = kwargs.pop('device_type', DeviceType.EWNEO_SENSOR)  # Only EWneo has sensors
        super().__init__(*args, device_type=device_type, subtype=DeviceSubtype.TEMPERATURE, **kwargs)
        self.operating_mode = OperatingMode.EVENT_TRIGGERED
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["sensor"]
    
    def get_entity_specs(self) -> Dict[str, List[Dict[str, Any]]]:
        """Generate entity specifications for this temperature sensor."""
        specs = {
            "switch": [],
            "light": [],
            "cover": [],
            "sensor": [],
            "binary_sensor": [],
            "button": []
        }
        
        # Temperature sensor entity
        specs["sensor"].append(self._create_base_entity_spec(
            "sensor",
            name=self.name,
            device_class="temperature",
            icon="mdi:thermometer",
            unit_of_measurement="°C"
        ))
        
        # Battery sensor entity
        specs["sensor"].append(self._create_base_entity_spec(
            "sensor",
            name=f"{self.name} Battery",
            device_class="battery",
            icon="mdi:battery",
            unit_of_measurement="%"
        ))
        
        return specs
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        # Process incoming telegram for temperature sensor
        info_type = telegram_data.get("info_type")
        
        # Only EWneo sensors exist, so use NEO telegram processing
        if info_type == TELEGRAM_NEO_SENSOR_DATA:
                self._process_ew_temperature_data(telegram_data)
        elif self.device_type == DeviceType.EWNEO_SENSOR:
            if info_type == TELEGRAM_NEO_SENSOR_DATA:
                self._process_neo_temperature_data(telegram_data)
        
        return self.get_sensor_data()
    
    def _process_ew_temperature_data(self, telegram_data: Dict[str, Any]) -> None:
        """Process EW temperature sensor data."""
        timestamp = datetime.now()
        self.properties["timestamp"] = timestamp
        self._last_seen = timestamp.timestamp()
        
        # Process temperature
        temperature = telegram_data.get("temperature")
        if temperature is not None:
            self.update_sensor_value("temperature", temperature)
            _LOGGER.debug("EW Temperature Sensor %s: %.1f°C", 
                        self.serial_number[-6:], temperature)
        
        # Process battery
        self._process_battery_data(telegram_data)
    
    def _process_neo_temperature_data(self, telegram_data: Dict[str, Any]) -> None:
        """Process EWneo temperature sensor data."""
        timestamp = datetime.now()
        self.properties["timestamp"] = timestamp
        self._last_seen = timestamp.timestamp()
        
        neo_type = telegram_data.get("neo_type")
        sensor_data = telegram_data.get("sensor_data", {})
        
        if neo_type == 0x33:  # Neo temperature sensor
            temperature = sensor_data.get("temperature")
            if temperature is not None:
                self.update_sensor_value("temperature", temperature)
                _LOGGER.debug("EWneo Temperature Sensor %s: %.1f°C", 
                            self.serial_number[-6:], temperature)
        
        # Process battery
        self._process_battery_data(telegram_data)
    
    def _process_battery_data(self, telegram_data: Dict[str, Any]) -> None:
        """Process battery data."""
        battery_level = telegram_data.get("battery_level")
        if battery_level is not None:
            self.update_sensor_value("battery", battery_level)
            self.battery_level = battery_level
            _LOGGER.debug("Temperature Sensor %s battery: %d%%", 
                        self.serial_number[-6:], battery_level)
    
    def get_sensor_data(self) -> Dict[str, Any]:
        """Get current sensor data."""
        data = {}
        
        # Add temperature value
        temperature = self._sensor_values.get("temperature")
        if temperature is not None:
            data["temperature"] = temperature
        
        # Add battery data
        if self.battery_level is not None:
            data["battery_level"] = self.battery_level
            data["battery_status"] = "good" if self.battery_level > 20 else "low"
        
        return data
    
    def get_measurement_unit(self, sensor_type: str) -> Optional[str]:
        """Get measurement unit for sensor type."""
        units = {
            "temperature": "°C",
            "battery": "%",
        }
        return units.get(sensor_type)


def create_rx11_temperature_sensor(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11TemperatureSensor:
    """Factory function to create temperature sensor."""
    # Extract device_type from device_info to avoid duplicate parameter
    device_type = device_info.get('device_type', DeviceType.EWNEO_SENSOR)  # Only EWneo has sensors
    
    # Filter out device_type from kwargs if it exists
    filtered_kwargs = {k: v for k, v in kwargs.items() if k != 'device_type'}
    
    return RX11TemperatureSensor(
        serial_number, 
        name=device_info.get('name'),
        device_type=device_type,
        **filtered_kwargs
    )