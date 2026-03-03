"""Universal EWneo sensor implementation for RX11 transceiver.

This module provides a flexible sensor class that can handle any combination
of sensor types (temperature, humidity, wind, rain) in a single device.
Sensor capabilities are determined during the learning process based on neo_type values.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set
import logging
import time
from datetime import datetime
from dataclasses import dataclass

from ....base import (
    BaseSensor, 
    DeviceType, 
    DeviceSubtype, 
    OperatingMode,
    SensorBehaviorMixin,
    EntitySpecsMixin,
)
from .....translations import translate

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_NEO_SENSOR_DATA = 0x02

# Neo type to sensor type mapping
NEO_TYPE_SENSOR_MAP = {
    0x33: "temperature",  # Temperature sensor
    0x34: "humidity",     # Humidity sensor
    0x37: "wind_speed",   # Wind speed sensor
    0x38: "rain",         # Rain sensor
}


@dataclass
class SensorTypeConfig:
    """Configuration for a sensor type."""
    translation_key: str  # Key for translations module
    neo_type: int
    device_class: Optional[str]
    unit: Optional[str]
    icon: str
    entity_type: str  # "sensor" or "binary_sensor"
    value_key: str  # Key in sensor_data dict
    
    @property
    def name(self) -> str:
        """Get translated name for this sensor type."""
        return translate(self.translation_key)


# Sensor type configurations (use translation keys for HA display)
SENSOR_TYPE_CONFIGS = {
    "temperature": SensorTypeConfig(
        translation_key="sensor.temperature",
        neo_type=0x33,
        device_class="temperature",
        unit="°C",
        icon="mdi:thermometer",
        entity_type="sensor",
        value_key="temperature"
    ),
    "humidity": SensorTypeConfig(
        translation_key="sensor.humidity",
        neo_type=0x34,
        device_class="humidity",
        unit="%",
        icon="mdi:water-percent",
        entity_type="sensor",
        value_key="humidity"
    ),
    "wind_speed": SensorTypeConfig(
        translation_key="sensor.wind",
        neo_type=0x37,
        device_class="wind_speed",
        unit="m/s",
        icon="mdi:weather-windy",
        entity_type="sensor",
        value_key="wind_speed"
    ),
    "rain": SensorTypeConfig(
        translation_key="sensor.rain",
        neo_type=0x38,
        device_class="moisture",
        unit=None,
        icon="mdi:weather-rainy",
        entity_type="binary_sensor",
        value_key="rain_detected"
    ),
}


class EWneoSensor(SensorBehaviorMixin, EntitySpecsMixin, BaseSensor):
    """Universal EWneo sensor implementation.
    
    This class can handle any combination of sensor types. The available sensor
    types are configured during initialization, typically based on information
    gathered during the learning process.
    
    Supported sensor types:
    - temperature: Temperature measurement in °C
    - humidity: Relative humidity in %
    - wind_speed: Wind speed in m/s
    - rain: Binary rain detection
    
    Multiple sensor types can be combined in a single device instance.
    """
    
    def __init__(self, *args, sensor_types: Optional[List[str]] = None, **kwargs):
        """Initialize universal EWneo sensor.
        
        Args:
            sensor_types: List of sensor type names that this device supports.
                         Valid values: "temperature", "humidity", "wind_speed", "rain"
                         If None or empty, defaults to ["temperature"]
            *args, **kwargs: Arguments passed to BaseSensor
        """
        # Extract device_type from kwargs
        device_type = kwargs.pop('device_type', DeviceType.EWNEO_SENSOR)
        
        # Determine subtype based on sensor types
        subtype = self._determine_subtype(sensor_types or ["temperature"])
        
        super().__init__(*args, device_type=device_type, subtype=subtype, **kwargs)
        self.operating_mode = OperatingMode.EVENT_TRIGGERED
        
        # Configure available sensor types
        self._sensor_types: Set[str] = set(sensor_types or ["temperature"])
        
        # Validate sensor types
        for sensor_type in self._sensor_types:
            if sensor_type not in SENSOR_TYPE_CONFIGS:
                _LOGGER.warning(
                    "Unknown sensor type '%s' for device %s, ignoring",
                    sensor_type, self.serial_number
                )
                self._sensor_types.discard(sensor_type)
        
        # Ensure we have at least one valid sensor type
        if not self._sensor_types:
            _LOGGER.warning(
                "No valid sensor types for device %s, defaulting to temperature",
                self.serial_number
            )
            self._sensor_types.add("temperature")
        
        # Initialize battery warning status
        self._battery_warning = False
        
        # Telegram timing tracking (Byte 7)
        self._max_telegram_interval: Optional[float] = None  # Maximum interval in seconds
        self._last_telegram_timestamp: Optional[float] = None  # Unix timestamp of last telegram
        
        _LOGGER.info(
            "EWneo Sensor %s initialized with sensor types: %s",
            self.serial_number, ", ".join(sorted(self._sensor_types))
        )
    
    def _parse_max_telegram_interval(self, byte7: int) -> float:
        """Parse Byte 7 to calculate maximum telegram interval.
        
        Formula: t = c · m · 2^x
        where:
        - c = 15 seconds (constant)
        - x = exponent (bits 7-4)
        - m = mantissa (bits 3-0)
        
        Args:
            byte7: Byte 7 from telegram data
            
        Returns:
            Maximum interval in seconds
        """
        exponent = (byte7 >> 4) & 0x0F  # Bits 7-4
        mantissa = byte7 & 0x0F  # Bits 3-0
        
        # Calculate interval: t = 15 * mantissa * 2^exponent
        interval = 15.0 * mantissa * (2 ** exponent)
        
        _LOGGER.debug(
            "EWneo sensor %s: Parsed Byte 7 (0x%02X) - exponent=%d, mantissa=%d, interval=%.1fs",
            self.serial_number, byte7, exponent, mantissa, interval
        )
        
        return interval
    
    def _determine_subtype(self, sensor_types: List[str]) -> DeviceSubtype:
        """Determine device subtype based on configured sensor types.
        
        Args:
            sensor_types: List of sensor type names
            
        Returns:
            DeviceSubtype enum value
        """
        # If multiple sensor types, use UNKNOWN (combined)
        if len(sensor_types) > 1:
            return DeviceSubtype.UNKNOWN
        
        # Single sensor type - map to specific subtype
        sensor_type = sensor_types[0] if sensor_types else "temperature"
        
        subtype_map = {
            "temperature": DeviceSubtype.TEMPERATURE,
            "humidity": DeviceSubtype.HUMIDITY,
            "wind_speed": DeviceSubtype.WIND_SPEED,
            "rain": DeviceSubtype.RAIN,
        }
        
        return subtype_map.get(sensor_type, DeviceSubtype.UNKNOWN)
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types based on configured sensors."""
        entity_types = set()
        
        for sensor_type in self._sensor_types:
            config = SENSOR_TYPE_CONFIGS.get(sensor_type)
            if config:
                entity_types.add(config.entity_type)
        
        # Always include binary_sensor for battery warning
        entity_types.add("binary_sensor")
        
        return list(entity_types)
    
    @property
    def sensor_types(self) -> List[str]:
        """Get list of configured sensor types."""
        return sorted(list(self._sensor_types))
    
    def get_entity_specs(self) -> Dict[str, List[Dict[str, Any]]]:
        """Generate entity specifications for this sensor.
        
        Creates entities for each configured sensor type plus battery.
        """
        specs = {
            "switch": [],
            "light": [],
            "cover": [],
            "sensor": [],
            "binary_sensor": [],
            "button": []
        }
        
        # Create entities for each configured sensor type
        for sensor_type in sorted(self._sensor_types):
            config = SENSOR_TYPE_CONFIGS.get(sensor_type)
            if not config:
                continue
            
            # Use translation_key for HA to translate entity names
            # HA will look up entity.sensor.<translation_key>.name in translations/*.json
            entity_spec = self._create_base_entity_spec(
                config.entity_type,
                name=None,  # Will be set via translation_key
                device_class=config.device_class,
                icon=config.icon,
                unit_of_measurement=config.unit
            )
            
            # Add translation_key for proper HA translation
            entity_spec["translation_key"] = sensor_type  # "temperature", "humidity", etc.
            
            # Use registration_id (UUID) as unique_id base
            reg_id = self._get_registration_id()
            entity_spec["unique_id"] = f"{reg_id}_{sensor_type}"
            
            # Add sensor type identifier for value retrieval
            entity_spec["sensor_type"] = sensor_type
            
            specs[config.entity_type].append(entity_spec)
        
        # Add battery status binary sensor (instead of percentage sensor)
        battery_warning_spec = self._create_base_entity_spec(
            "binary_sensor",
            name=None,  # Will be set via translation_key
            device_class="battery",
            icon="mdi:battery"
        )
        # Add translation_key for proper HA translation
        battery_warning_spec["translation_key"] = "battery_warning"
        # Use registration_id (UUID) as unique_id base
        reg_id = self._get_registration_id()
        battery_warning_spec["unique_id"] = f"{reg_id}_battery_warning"
        battery_warning_spec["sensor_type"] = "battery_warning"
        specs["binary_sensor"].append(battery_warning_spec)
        
        # Note: "Zuletzt gesehen" is now shown as an attribute on temperature/humidity sensors
        # instead of a separate entity to avoid logbook spam
        
        return specs
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for EWneo sensor.
        
        Extracts sensor values based on neo_type and updates internal state.
        
        Args:
            telegram_data: Telegram data dictionary
            
        Returns:
            Current sensor data
        """
        info_type = telegram_data.get("info_type")
        
        if info_type == TELEGRAM_NEO_SENSOR_DATA:
            self._process_neo_sensor_data(telegram_data)
        else:
            _LOGGER.debug(
                "Unexpected info_type %s for EWneo sensor %s",
                info_type, self.serial_number
            )
        
        return self.get_sensor_data()
    
    def _process_neo_sensor_data(self, telegram_data: Dict[str, Any]) -> None:
        """Process EWneo sensor telegram data.
        
        Parses the 8-byte sensor telegram format:
        Byte 0: Version (bits 2-0, should be 0)
        Byte 1: Battery info (bit 7=0 for measurement, bit 6=has battery, bits 5-3=battery level)
        Byte 2: Sensor type (bits 7-2: 4=temperature, 5=humidity) 
        Bytes 3-4: Measurement value (Big-Endian unsigned 16-bit)
        Bytes 5-6: Reference value (optional, Big-Endian unsigned 16-bit)
        Byte 7: Max telegram interval (bits 7-4=exponent, bits 3-0=mantissa)
        
        Args:
            telegram_data: Telegram data dictionary with 'data' field containing bytes
        """
        timestamp = datetime.now()
        self.properties["timestamp"] = timestamp
        self._last_seen = timestamp.timestamp()
        
        # Track telegram timestamp for availability monitoring
        self._last_telegram_timestamp = time.time()
        _LOGGER.debug("🕐 Setting _last_telegram_timestamp to %s for device %s", 
                       self._last_telegram_timestamp, self.serial_number[-8:])
        
        # Extract raw data bytes from telegram
        data = telegram_data.get("data", [])
        if not data or len(data) < 5:
            _LOGGER.warning("EWneo sensor %s: Invalid telegram data length %d, expected at least 5 bytes",
                          self.serial_number, len(data) if data else 0)
            return
        
        # Byte 0: Version check
        version = data[0] & 0x07  # Bits 2-0
        if version != 0:
            _LOGGER.warning("EWneo sensor %s: Unknown telegram version %d, ignoring",
                          self.serial_number, version)
            return
        
        # Byte 1: Battery information
        byte1 = data[1]
        is_learn = (byte1 & 0x80) != 0  # Bit 7: 1=learn, 0=measurement
        has_battery = (byte1 & 0x40) != 0  # Bit 6
        battery_level_raw = (byte1 >> 3) & 0x07  # Bits 5-3
        
        if is_learn:
            _LOGGER.debug("EWneo sensor %s: Learn telegram detected, skipping data processing",
                        self.serial_number)
            return
        
        # Process battery if present
        if has_battery:
            # Convert 0-7 scale to 0-100% (7=full, 0=weak)
            battery_pct = round((battery_level_raw / 7.0) * 100)
            self.battery_level = battery_pct
            
            # Set battery warning if level is 0 (empty)
            if battery_level_raw == 0:
                self._battery_warning = True
                _LOGGER.warning(
                    "🔋 Battery warning: EWneo sensor %s battery is empty (level=0)",
                    self.serial_number
                )
            else:
                self._battery_warning = False
            
            _LOGGER.debug("EWneo sensor %s: Battery level=%d/7 (%d%%)",
                        self.serial_number, battery_level_raw, battery_pct)
        
        # Byte 2: Sensor type
        byte2 = data[2]
        sensor_type_code = (byte2 >> 2) & 0x3F  # Bits 7-2
        has_reference = (byte2 & 0x01) != 0  # Bit 0
        
        # Map sensor type code to sensor type name
        if sensor_type_code == 4:
            sensor_type = "temperature"
        elif sensor_type_code == 5:
            sensor_type = "humidity"
        else:
            _LOGGER.warning("EWneo sensor %s: Unknown sensor type code %d, ignoring",
                          self.serial_number, sensor_type_code)
            return
        
        # Only process if this sensor type is configured
        if sensor_type not in self._sensor_types:
            _LOGGER.debug("Received %s data but device %s is not configured for this type",
                        sensor_type, self.serial_number)
            return
        
        # Bytes 3-4: Measurement value (Big-Endian unsigned 16-bit)
        if len(data) >= 5:
            raw_value = (data[3] << 8) | data[4]
            
            # Convert based on sensor type
            if sensor_type == "temperature":
                # Temperature: value * (1/20) Kelvin, convert to Celsius
                temp_kelvin = raw_value / 20.0
                temp_celsius = temp_kelvin - 273.15
                value = round(temp_celsius, 1)
            elif sensor_type == "humidity":
                # Humidity: value * (100/4095) %
                value = round((raw_value / 4095.0) * 100, 1)
            else:
                value = raw_value
            
            self.update_sensor_value(sensor_type, value)
            _LOGGER.info("EWneo Sensor %s: %s = %s%s (raw=0x%04X)",
                        self.serial_number,
                        sensor_type,
                        value,
                        "°C" if sensor_type == "temperature" else "%",
                        raw_value)
        
        # Bytes 5-6: Reference value (optional)
        if has_reference and len(data) >= 7:
            ref_raw_value = (data[5] << 8) | data[6]
            _LOGGER.debug("EWneo sensor %s: Reference value raw=0x%04X",
                        self.serial_number, ref_raw_value)
        
        # Byte 7: Maximum telegram interval
        if len(data) >= 8:
            byte7 = data[7]
            self._max_telegram_interval = self._parse_max_telegram_interval(byte7)
            _LOGGER.info(
                "EWneo sensor %s: Max telegram interval = %.1fs (%.1f minutes)",
                self.serial_number,
                self._max_telegram_interval,
                self._max_telegram_interval / 60.0
            )
    
    def is_available(self) -> bool:
        """Check if sensor is available based on telegram timing.
        
        Returns:
            True if sensor is available (last telegram within 2x max interval),
            False otherwise
        """
        # If we haven't received any telegram yet, consider unavailable
        if self._last_telegram_timestamp is None:
            return False
        
        # If max interval not set, consider available (fallback)
        if self._max_telegram_interval is None:
            return True
        
        # Calculate time since last telegram
        time_since_last = time.time() - self._last_telegram_timestamp
        
        # Unavailable if more than 2x max interval
        return time_since_last <= (2.0 * self._max_telegram_interval)
    
    def get_availability_status(self) -> Dict[str, Any]:
        """Get detailed availability status for the sensor.
        
        Returns:
            Dictionary with availability information
        """
        if self._last_telegram_timestamp is None or self._max_telegram_interval is None:
            return {
                "available": False,
                "warning": False,
                "time_since_last": None,
                "max_interval": None,
                "status": "no_data"
            }
        
        time_since_last = time.time() - self._last_telegram_timestamp
        
        # Determine status
        if time_since_last > (2.0 * self._max_telegram_interval):
            status = "unavailable"
            available = False
            warning = False
        elif time_since_last > self._max_telegram_interval:
            status = "warning"
            available = True
            warning = True
        else:
            status = "ok"
            available = True
            warning = False
        
        return {
            "available": available,
            "warning": warning,
            "time_since_last": time_since_last,
            "max_interval": self._max_telegram_interval,
            "status": status,
            "last_telegram_time": datetime.fromtimestamp(self._last_telegram_timestamp).isoformat() if self._last_telegram_timestamp else None
        }
    
    def get_sensor_data(self) -> Dict[str, Any]:
        """Get current sensor data for all configured sensor types.
        
        Returns:
            Dictionary with sensor values, battery warning, and availability status
        """
        data = {}
        
        # Add all configured sensor values
        for sensor_type in self._sensor_types:
            value = self._sensor_values.get(sensor_type)
            if value is not None:
                data[sensor_type] = value
        
        # Add battery warning status
        data["battery_warning"] = self._battery_warning
        
        # Add battery level for legacy compatibility (if needed elsewhere)
        if self.battery_level is not None:
            data["battery_level"] = self.battery_level
        
        # Add timestamp
        if "timestamp" in self.properties:
            data["timestamp"] = self.properties["timestamp"]
        
        # Add availability status
        availability = self.get_availability_status()
        data["availability_status"] = availability.get("status")
        data["availability_warning"] = availability.get("warning", False)
        data["last_telegram_time"] = availability.get("last_telegram_time")
        data["time_since_last_telegram"] = availability.get("time_since_last")
        data["max_telegram_interval"] = availability.get("max_interval")
        
        return data
    
    def get_state(self) -> Dict[str, Any]:
        """Get current state (alias for get_sensor_data for coordinator compatibility).
        
        Returns:
            Dictionary with sensor values and battery status
        """
        return self.get_sensor_data()
    
    def get_measurement_unit(self, sensor_type: str) -> Optional[str]:
        """Get measurement unit for a sensor type.
        
        Args:
            sensor_type: Sensor type name
            
        Returns:
            Unit string or None
        """
        # Handle battery separately
        if sensor_type == "battery":
            return "%"
        
        # Get unit from sensor type configuration
        config = SENSOR_TYPE_CONFIGS.get(sensor_type)
        return config.unit if config else None
    
    def add_sensor_type(self, sensor_type: str) -> bool:
        """Add a sensor type to this device.
        
        This can be used during learning to add additional sensor capabilities.
        
        Args:
            sensor_type: Sensor type name to add
            
        Returns:
            True if sensor type was added, False if invalid or already present
        """
        if sensor_type not in SENSOR_TYPE_CONFIGS:
            _LOGGER.warning(
                "Cannot add unknown sensor type '%s' to device %s",
                sensor_type, self.serial_number
            )
            return False
        
        if sensor_type in self._sensor_types:
            _LOGGER.debug(
                "Sensor type '%s' already configured for device %s",
                sensor_type, self.serial_number
            )
            return False
        
        self._sensor_types.add(sensor_type)
        
        # Update subtype if needed
        self.subtype = self._determine_subtype(list(self._sensor_types))
        
        _LOGGER.info(
            "Added sensor type '%s' to EWneo sensor %s",
            sensor_type, self.serial_number
        )
        return True
    
    def remove_sensor_type(self, sensor_type: str) -> bool:
        """Remove a sensor type from this device.
        
        Args:
            sensor_type: Sensor type name to remove
            
        Returns:
            True if sensor type was removed, False if not present or last type
        """
        if sensor_type not in self._sensor_types:
            return False
        
        if len(self._sensor_types) <= 1:
            _LOGGER.warning(
                "Cannot remove last sensor type '%s' from device %s",
                sensor_type, self.serial_number
            )
            return False
        
        self._sensor_types.discard(sensor_type)
        
        # Update subtype
        self.subtype = self._determine_subtype(list(self._sensor_types))
        
        _LOGGER.info(
            "Removed sensor type '%s' from EWneo sensor %s",
            sensor_type, self.serial_number
        )
        return True


def create_ewneo_sensor(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> EWneoSensor:
    """Factory function to create EWneo sensor.
    
    Args:
        serial_number: Device serial number
        device_info: Device information dictionary, may contain:
            - name: Device name
            - sensor_types: List of sensor type names
            - neo_types: List of neo_type values (will be converted to sensor_types)
        **kwargs: Additional arguments passed to EWneoSensor
        
    Returns:
        Configured EWneoSensor instance
    """
    _LOGGER.info("🏗️ create_ewneo_sensor called for %s with sensor_types=%s", 
                serial_number, device_info.get('sensor_types'))
    
    # Determine sensor types from device_info
    sensor_types = device_info.get('sensor_types')
    
    # If no sensor_types but neo_types provided, convert them
    if not sensor_types:
        neo_types = device_info.get('neo_types', [])
        if neo_types:
            sensor_types = []
            for neo_type in neo_types:
                sensor_type = NEO_TYPE_SENSOR_MAP.get(neo_type)
                if sensor_type:
                    sensor_types.append(sensor_type)
    
    # Default to temperature if nothing specified
    if not sensor_types:
        _LOGGER.debug("No sensor_types specified for %s, defaulting to temperature", 
                       serial_number)
        sensor_types = ["temperature"]
    
    _LOGGER.info("🏗️ Creating EWneoSensor with sensor_types=%s", sensor_types)
    
    # Filter out device_type from kwargs if it exists to avoid conflicts
    filtered_kwargs = {k: v for k, v in kwargs.items() if k != 'device_type'}
    
    try:
        sensor = EWneoSensor(
            serial_number,
            name=device_info.get('name', f"EWneo Sensor {serial_number}"),
            sensor_types=sensor_types,
            device_type=DeviceType.EWNEO_SENSOR,
            **filtered_kwargs
        )
        _LOGGER.info("✅ Successfully created EWneoSensor for %s", serial_number)
        return sensor
    except Exception as e:
        _LOGGER.error("❌ Error creating EWneoSensor for %s: %s", 
                     serial_number, e, exc_info=True)
        raise


# Convenience factory functions for specific sensor type combinations
def create_temperature_sensor(serial_number: str, device_info: Dict[str, Any], **kwargs) -> EWneoSensor:
    """Create temperature-only sensor."""
    device_info['sensor_types'] = ['temperature']
    return create_ewneo_sensor(serial_number, device_info, **kwargs)


def create_humidity_sensor(serial_number: str, device_info: Dict[str, Any], **kwargs) -> EWneoSensor:
    """Create humidity-only sensor."""
    device_info['sensor_types'] = ['humidity']
    return create_ewneo_sensor(serial_number, device_info, **kwargs)


def create_wind_sensor(serial_number: str, device_info: Dict[str, Any], **kwargs) -> EWneoSensor:
    """Create wind speed sensor."""
    device_info['sensor_types'] = ['wind_speed']
    return create_ewneo_sensor(serial_number, device_info, **kwargs)


def create_rain_sensor(serial_number: str, device_info: Dict[str, Any], **kwargs) -> EWneoSensor:
    """Create rain detection sensor."""
    device_info['sensor_types'] = ['rain']
    return create_ewneo_sensor(serial_number, device_info, **kwargs)
