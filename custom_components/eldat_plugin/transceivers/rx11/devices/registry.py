"""Device factory and registry for RX11 transceiver devices."""
from __future__ import annotations

import logging
from typing import Dict, Optional, List, Any

from ...base import BaseDevice, DeviceType, DeviceSubtype

_LOGGER = logging.getLogger(__name__)

# Import individual device classes
sensor_imports = {}
receiver_imports = {}
transmitter_imports = {}
ewneo_imports = {}

# EWneo Sensor class (universal, combinable sensor types)
try:
    from .ewneo_sensors.ewneo_sensor import (
        create_ewneo_sensor,
        create_temperature_sensor,
        create_humidity_sensor,
        create_wind_sensor,
        create_rain_sensor,
        NEO_TYPE_SENSOR_MAP,
    )
    # Use the universal sensor creator
    sensor_imports['ewneo'] = create_ewneo_sensor
    # Keep individual creators for specific sensor types
    sensor_imports['temperature'] = create_temperature_sensor
    sensor_imports['humidity'] = create_humidity_sensor
    sensor_imports['combined'] = create_ewneo_sensor  # Use universal for combined types
    sensor_imports['wind'] = create_wind_sensor
    sensor_imports['rain'] = create_rain_sensor
except ImportError as e:
    _LOGGER.warning(f"Could not import EWneo sensor: {e}")
    sensor_imports['ewneo'] = lambda *args, **kwargs: None
    sensor_imports['temperature'] = lambda *args, **kwargs: None
    sensor_imports['humidity'] = lambda *args, **kwargs: None
    sensor_imports['combined'] = lambda *args, **kwargs: None
    sensor_imports['wind'] = lambda *args, **kwargs: None
    sensor_imports['rain'] = lambda *args, **kwargs: None

# Receiver classes
try:
    from .ew_receivers.switch import create_rx11_switch_receiver
    receiver_imports['switch'] = create_rx11_switch_receiver
except ImportError as e:
    _LOGGER.warning(f"Could not import switch receiver: {e}")
    receiver_imports['switch'] = lambda *args, **kwargs: None

# EW dimmer receiver removed - doesn't exist

try:
    from .ew_receivers.motor import create_rx11_motor_receiver
    receiver_imports['motor'] = create_rx11_motor_receiver
except ImportError as e:
    _LOGGER.warning(f"Could not import motor receiver: {e}")
    receiver_imports['motor'] = lambda *args, **kwargs: None

try:
    from .ew_receivers.climate import create_rx11_climate_receiver
    receiver_imports['climate'] = create_rx11_climate_receiver
except ImportError as e:
    _LOGGER.warning(f"Could not import climate receiver: {e}")
    receiver_imports['climate'] = lambda *args, **kwargs: None

# Transmitter classes
try:
    from .ew_transmitters.single_button import create_rx11_single_button_transmitter
    transmitter_imports['single'] = create_rx11_single_button_transmitter
except ImportError as e:
    _LOGGER.warning(f"Could not import single button transmitter: {e}")
    transmitter_imports['single'] = lambda *args, **kwargs: None

try:
    from .ew_transmitters.dual_button import create_rx11_dual_button_transmitter
    transmitter_imports['dual'] = create_rx11_dual_button_transmitter
except ImportError as e:
    _LOGGER.warning(f"Could not import dual button transmitter: {e}")
    transmitter_imports['dual'] = lambda *args, **kwargs: None

try:
    from .ew_transmitters.triple_button import create_rx11_triple_button_transmitter
    transmitter_imports['triple'] = create_rx11_triple_button_transmitter
except ImportError as e:
    _LOGGER.warning(f"Could not import triple button transmitter: {e}")
    transmitter_imports['triple'] = lambda *args, **kwargs: None

try:
    from .ew_transmitters.quad_button import create_rx11_quad_button_transmitter
    transmitter_imports['quad'] = create_rx11_quad_button_transmitter
except ImportError as e:
    _LOGGER.warning(f"Could not import quad button transmitter: {e}")
    transmitter_imports['quad'] = lambda *args, **kwargs: None

# EWneo transceiver classes
try:
    from .ewneo_transceivers.transceiver import create_rx11_ewneo_transceiver
    ewneo_imports['transceiver'] = create_rx11_ewneo_transceiver
except ImportError as e:
    _LOGGER.warning(f"Could not import ewneo transceiver: {e}")
    ewneo_imports['transceiver'] = lambda *args, **kwargs: None

try:
    from .ewneo_transceivers.switch import create_rx11_ewneo_switch
    ewneo_imports['switch'] = create_rx11_ewneo_switch
except ImportError as e:
    _LOGGER.warning(f"Could not import ewneo switch: {e}")
    ewneo_imports['switch'] = lambda *args, **kwargs: None

try:
    from .ewneo_transceivers.dual_switch import create_rx11_ewneo_dual_switch
    ewneo_imports['dual_switch'] = create_rx11_ewneo_dual_switch
except ImportError as e:
    _LOGGER.warning(f"Could not import ewneo dual switch: {e}")
    ewneo_imports['dual_switch'] = lambda *args, **kwargs: None

try:
    from .ewneo_transceivers.quad_switch import create_rx11_ewneo_quad_switch
    ewneo_imports['quad_switch'] = create_rx11_ewneo_quad_switch
except ImportError as e:
    _LOGGER.warning(f"Could not import ewneo quad switch: {e}")
    ewneo_imports['quad_switch'] = lambda *args, **kwargs: None

try:
    from .ewneo_transceivers.dimmer import create_rx11_ewneo_dimmer
    ewneo_imports['dimmer'] = create_rx11_ewneo_dimmer
except ImportError as e:
    _LOGGER.warning(f"Could not import ewneo dimmer: {e}")
    ewneo_imports['dimmer'] = lambda *args, **kwargs: None

try:
    from .ewneo_transceivers.motor import create_rx11_ewneo_motor
    ewneo_imports['motor'] = create_rx11_ewneo_motor
except ImportError as e:
    _LOGGER.warning(f"Could not import ewneo motor: {e}")
    ewneo_imports['motor'] = lambda *args, **kwargs: None

try:
    from .ewneo_transceivers.dual_motor import create_rx11_ewneo_dual_motor
    ewneo_imports['dual_motor'] = create_rx11_ewneo_dual_motor
except ImportError as e:
    _LOGGER.warning(f"Could not import ewneo dual motor: {e}")
    ewneo_imports['dual_motor'] = lambda *args, **kwargs: None

try:
    from .ewneo_transceivers.quad_motor import create_rx11_ewneo_quad_motor
    ewneo_imports['quad_motor'] = create_rx11_ewneo_quad_motor
except ImportError as e:
    _LOGGER.warning(f"Could not import ewneo quad motor: {e}")
    ewneo_imports['quad_motor'] = lambda *args, **kwargs: None


class RX11DeviceFactory:
    """Factory class for creating RX11-specific device instances."""
    
    # ELDAT Device Type Mappings
    DEVICE_TYPE_MAPPING = {
        # EasyWave (EW) Geräte - Numeric IDs (ohne Sensoren)
        0x10: DeviceType.EW_RECEIVER,
        0x11: DeviceType.EW_TRANSMITTER,
        0x13: DeviceType.EWNEO_SENSOR,  # Only EWneo has sensors
        
        # EasyWave Bidi (EWB) Geräte - Numeric IDs (from rx_module.py DeviceType)
        0x01: DeviceType.EWNEO_BIDI_TRANSMITTER,  # EWB_DT_BIDI_TR
        0x03: DeviceType.EWNEO_SWITCH,            # EWB_DT_SWITCH
        0x04: DeviceType.EWNEO_DIMMER,            # EWB_DT_DIMMER
        0x05: DeviceType.EWNEO_MOTOR,             # EWB_DT_MOTOR
        0x06: DeviceType.EWNEO_DUAL_SWITCH,       # EWB_DT_DUAL_SWITCH
        0x07: DeviceType.EWNEO_QUAD_SWITCH,       # EWB_DT_QUAD_SWITCH
        0x08: DeviceType.EWNEO_DUAL_MOTOR,        # EWB_DT_DUAL_MOTOR
        0x09: DeviceType.EWNEO_QUAD_MOTOR,        # EWB_DT_QUAD_MOTOR
        0x0A: DeviceType.EWNEO_TRANSCEIVER,       # EWB_DT_PART_SWITCH
        0x0B: DeviceType.EWNEO_TRANSCEIVER,       # EWB_DT_PART_MOTOR
        
        # String-basierte Mappings für Kompatibilität
        "ew_receiver": DeviceType.EW_RECEIVER,
        "ew_transmitter": DeviceType.EW_TRANSMITTER,
        "ewneo_sensor": DeviceType.EWNEO_SENSOR,  # Only EWneo has sensors
        "ewneo_receiver": DeviceType.EWNEO_RECEIVER,

        "ewneo_transceiver": DeviceType.EWNEO_TRANSCEIVER,
        "ewneo_bidi_transmitter": DeviceType.EWNEO_BIDI_TRANSMITTER,
        "ewneo_switch": DeviceType.EWNEO_SWITCH,
        "ewneo_dual_switch": DeviceType.EWNEO_DUAL_SWITCH,
        "ewneo_quad_switch": DeviceType.EWNEO_QUAD_SWITCH,
        "ewneo_dimmer": DeviceType.EWNEO_DIMMER,
        "ewneo_motor": DeviceType.EWNEO_MOTOR,
        "ewneo_dual_motor": DeviceType.EWNEO_DUAL_MOTOR,
        "ewneo_quad_motor": DeviceType.EWNEO_QUAD_MOTOR,
        
        # Legacy mappings
        "switch": DeviceType.EW_RECEIVER,
        "dimmer": DeviceType.EW_RECEIVER,
        "motor": DeviceType.EW_RECEIVER,
    }
    
    # NEO Telegram Type Mappings (ohne Motion/Door_Window Sensoren)
    NEO_TYPE_MAPPING = {
        0x30: (DeviceType.EWNEO_RECEIVER, DeviceSubtype.SWITCH),
        0x31: (DeviceType.EWNEO_RECEIVER, DeviceSubtype.DIMMER),
        0x32: (DeviceType.EWNEO_RECEIVER, DeviceSubtype.MOTOR),
        0x33: (DeviceType.EWNEO_SENSOR, DeviceSubtype.TEMPERATURE),
        0x34: (DeviceType.EWNEO_SENSOR, DeviceSubtype.HUMIDITY),
        0x37: (DeviceType.EWNEO_SENSOR, DeviceSubtype.WIND_SPEED),
        0x38: (DeviceType.EWNEO_SENSOR, DeviceSubtype.RAIN),
    }
    
    # Factory function mappings for different device types and subtypes
    # All EWneo sensors now use the universal EWneoSensor class
    SENSOR_FACTORY_FUNCTIONS = {
        # Universal EWneo Sensor (all subtypes use the same class)
        (DeviceType.EWNEO_SENSOR, DeviceSubtype.TEMPERATURE): sensor_imports.get('temperature'),
        (DeviceType.EWNEO_SENSOR, DeviceSubtype.HUMIDITY): sensor_imports.get('humidity'),
        (DeviceType.EWNEO_SENSOR, DeviceSubtype.UNKNOWN): sensor_imports.get('combined'),
        (DeviceType.EWNEO_SENSOR, DeviceSubtype.WIND_SPEED): sensor_imports.get('wind'),
        (DeviceType.EWNEO_SENSOR, DeviceSubtype.RAIN): sensor_imports.get('rain'),
    }
    
    RECEIVER_FACTORY_FUNCTIONS = {
        # EW/EWB Receivers (no dimmer - doesn't exist)
        (DeviceType.EW_RECEIVER, DeviceSubtype.SWITCH): receiver_imports.get('switch'),
        (DeviceType.EW_RECEIVER, DeviceSubtype.MOTOR): receiver_imports.get('motor'),
        (DeviceType.EW_RECEIVER, DeviceSubtype.HEATING_COOLING): receiver_imports.get('climate'),
        # EWneo Receivers (include dimmer for EWneo)
        (DeviceType.EWNEO_RECEIVER, DeviceSubtype.SWITCH): receiver_imports.get('switch'),
        (DeviceType.EWNEO_RECEIVER, DeviceSubtype.DIMMER): receiver_imports.get('dimmer'),
        (DeviceType.EWNEO_RECEIVER, DeviceSubtype.MOTOR): receiver_imports.get('motor'),
        (DeviceType.EWNEO_RECEIVER, DeviceSubtype.HEATING_COOLING): receiver_imports.get('climate'),
    }
    
    TRANSMITTER_FACTORY_FUNCTIONS = {
        # EW Transmitters - based on button count
        (DeviceType.EW_TRANSMITTER, 1): transmitter_imports.get('single'),
        (DeviceType.EW_TRANSMITTER, 2): transmitter_imports.get('dual'),
        (DeviceType.EW_TRANSMITTER, 3): transmitter_imports.get('triple'),
        (DeviceType.EW_TRANSMITTER, 4): transmitter_imports.get('quad'),
    }
    
    EWNEO_TRANSCEIVER_FACTORY_FUNCTIONS = {
        # EWneo Transceivers - specific device types
        DeviceType.EWNEO_TRANSCEIVER: ewneo_imports.get('transceiver'),
        DeviceType.EWNEO_SWITCH: ewneo_imports.get('switch'),
        DeviceType.EWNEO_DUAL_SWITCH: ewneo_imports.get('dual_switch'),
        DeviceType.EWNEO_QUAD_SWITCH: ewneo_imports.get('quad_switch'),
        DeviceType.EWNEO_DIMMER: ewneo_imports.get('dimmer'),
        DeviceType.EWNEO_MOTOR: ewneo_imports.get('motor'),
        DeviceType.EWNEO_DUAL_MOTOR: ewneo_imports.get('dual_motor'),
        DeviceType.EWNEO_QUAD_MOTOR: ewneo_imports.get('quad_motor'),
    }
    
    @classmethod
    def create_device(
        cls, 
        serial_number: str, 
        device_info: Dict[str, Any], 
        telegram_data: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Optional[BaseDevice]:
        """Create a device instance based on device information and telegram data."""
        try:
            _LOGGER.info("🏭 Creating device for %s: device_type=%s, type=%s", 
                        serial_number[-6:], 
                        device_info.get("device_type"), 
                        device_info.get("type"))
            
            # Merge telegram data with device info for better type detection
            combined_info = dict(device_info)
            if telegram_data:
                combined_info.update(telegram_data)
            
            device_type = cls._determine_device_type(combined_info)
            _LOGGER.info("🏭 Determined device_type: %s for %s", device_type, serial_number[-6:])
            
            if device_type == DeviceType.UNKNOWN:
                _LOGGER.warning("Unknown device type for device %s: device_info=%s, telegram_data=%s", 
                              serial_number[-6:], device_info, telegram_data)
                return None
            
            # Determine device subtype and get appropriate factory function
            factory_func = None
            
            if device_type == DeviceType.EWNEO_SENSOR:
                # Handle EWneo sensors - determine sensor types from neo_type or device_info
                sensor_types = cls._determine_sensor_types(combined_info)
                _LOGGER.info("🏭 EWneo sensor types: %s for %s", sensor_types, serial_number[-6:])
                
                # Add sensor_types to device_info for the factory function
                if sensor_types:
                    device_info['sensor_types'] = sensor_types
                    # If multiple sensor types, use combined/UNKNOWN subtype
                    if len(sensor_types) > 1:
                        subtype = DeviceSubtype.UNKNOWN
                    else:
                        # Single sensor type - determine specific subtype
                        subtype = cls._sensor_type_to_subtype(sensor_types[0])
                else:
                    # Fallback to legacy behavior
                    subtype = cls._determine_sensor_subtype(combined_info, device_type)
                
                # For new universal sensor, prefer the ewneo factory if available
                factory_func = sensor_imports.get('ewneo')
                _LOGGER.info("🏭 Using factory function: %s for %s", 
                           factory_func.__name__ if factory_func else "None", 
                           serial_number[-6:])
                
                if not factory_func:
                    # Fallback to subtype-specific factory
                    factory_func = cls.SENSOR_FACTORY_FUNCTIONS.get((device_type, subtype))
                
            elif device_type in [DeviceType.EW_RECEIVER, DeviceType.EWNEO_RECEIVER]:
                # Handle receivers - EWneo receivers use the same individual classes as EW
                subtype = cls._determine_receiver_subtype(combined_info)
                factory_func = cls.RECEIVER_FACTORY_FUNCTIONS.get((device_type, subtype))
                
                # For EWneo receivers, try the EW receiver factory instead
                if not factory_func and device_type == DeviceType.EWNEO_RECEIVER:
                    factory_func = cls.RECEIVER_FACTORY_FUNCTIONS.get((DeviceType.EW_RECEIVER, subtype))
                
            elif device_type == DeviceType.EW_TRANSMITTER:
                # Handle transmitters
                button_count = cls._determine_button_count(combined_info)
                factory_func = cls.TRANSMITTER_FACTORY_FUNCTIONS.get((device_type, button_count))
                
            elif device_type in cls.EWNEO_TRANSCEIVER_FACTORY_FUNCTIONS:
                # Handle EWneo transceivers (bidirectional devices)
                factory_func = cls.EWNEO_TRANSCEIVER_FACTORY_FUNCTIONS.get(device_type)
            
            if not factory_func:
                _LOGGER.warning("No factory available for device type %s", device_type)
                return None
            
            # Create device using the factory function
            _LOGGER.info("🏭 Calling factory function for %s with sensor_types=%s", 
                        serial_number[-6:], device_info.get('sensor_types'))
            
            device = factory_func(
                serial_number,
                device_info,
                **kwargs
            )
            
            if device:
                _LOGGER.info("✅ Successfully created %s device %s", 
                            device.__class__.__name__, serial_number[-6:])
            else:
                _LOGGER.warning("⚠️ Factory function returned None for %s", serial_number[-6:])
            
            return device
        
        except Exception as e:
            _LOGGER.error(f"Failed to create device {serial_number[-6:]}: {e}")
            return None
    
    @classmethod
    def _determine_device_type(cls, device_info: Dict[str, Any]) -> DeviceType:
        """Determine device type from device information."""
        
        # Check for explicit device_type first (our new format)
        device_type_raw = device_info.get("device_type")
        if device_type_raw:
            if isinstance(device_type_raw, DeviceType):
                return device_type_raw
            elif isinstance(device_type_raw, str):
                # Try to find matching DeviceType by value
                for device_type in DeviceType:
                    if device_type.value == device_type_raw.lower():
                        return device_type
        
        # Check for legacy explicit type in device_info
        device_type_raw = device_info.get("type")
        if device_type_raw:
            if isinstance(device_type_raw, int):
                mapped_type = cls.DEVICE_TYPE_MAPPING.get(device_type_raw)
                if mapped_type:
                    return mapped_type
            elif isinstance(device_type_raw, str):
                mapped_type = cls.DEVICE_TYPE_MAPPING.get(device_type_raw.lower())
                if mapped_type:
                    return mapped_type
        
        # Check for NEO telegram type
        neo_type = device_info.get("neo_type")
        if neo_type and neo_type in cls.NEO_TYPE_MAPPING:
            device_type, _ = cls.NEO_TYPE_MAPPING[neo_type]
            return device_type
        
        # Try to determine from name patterns
        device_name = device_info.get("name", "").lower()
        
        # Check for transmitter indicators
        if any(word in device_name for word in ["transmitter", "sender", "fernbedienung", "taster"]):
            return DeviceType.EW_TRANSMITTER
        
        # Check for sensor indicators - only EWneo has sensors
        elif any(word in device_name for word in ["sensor", "temperature", "humidity", "wind", "rain"]):
            return DeviceType.EWNEO_SENSOR
        
        # Check for receiver indicators
        elif any(word in device_name for word in ["receiver", "empfänger", "switch", "dimmer", "motor"]):
            if "neo" in device_name:
                return DeviceType.EWNEO_RECEIVER
            else:
                return DeviceType.EW_RECEIVER
        
        # Fallback based on device characteristics
        button_count = device_info.get("button_count", device_info.get("channels", 0))
        if button_count > 0:
            # Devices with few buttons are usually transmitters
            # Devices with more channels/buttons are usually receivers
            return DeviceType.EW_TRANSMITTER if button_count <= 4 else DeviceType.EW_RECEIVER
        
        return DeviceType.UNKNOWN
    
    @classmethod
    def _sensor_type_to_subtype(cls, sensor_type: str) -> DeviceSubtype:
        """Convert sensor type string to DeviceSubtype enum.
        
        Args:
            sensor_type: Sensor type name (e.g., 'temperature', 'humidity')
            
        Returns:
            Corresponding DeviceSubtype enum value
        """
        mapping = {
            'temperature': DeviceSubtype.TEMPERATURE,
            'humidity': DeviceSubtype.HUMIDITY,
            'wind_speed': DeviceSubtype.WIND_SPEED,
            'rain': DeviceSubtype.RAIN,
        }
        return mapping.get(sensor_type, DeviceSubtype.UNKNOWN)
    
    @classmethod
    def _determine_sensor_types(cls, device_info: Dict[str, Any]) -> List[str]:
        """Determine list of sensor types from device information.
        
        This is used for the new universal EWneoSensor class that can handle
        multiple sensor types in a single device instance.
        
        Args:
            device_info: Device information dictionary
            
        Returns:
            List of sensor type strings (e.g., ['temperature', 'humidity'])
        """
        # Check if sensor_types already defined
        if 'sensor_types' in device_info:
            return device_info['sensor_types']
        
        # Check for neo_types list (from telegram data or learning)
        neo_types = device_info.get('neo_types', [])
        if neo_types:
            sensor_types = []
            for neo_type in neo_types:
                sensor_type = NEO_TYPE_SENSOR_MAP.get(neo_type)
                if sensor_type and sensor_type not in sensor_types:
                    sensor_types.append(sensor_type)
            if sensor_types:
                return sensor_types
        
        # Check for single neo_type
        neo_type = device_info.get('neo_type')
        if neo_type:
            sensor_type = NEO_TYPE_SENSOR_MAP.get(neo_type)
            if sensor_type:
                return [sensor_type]
        
        # No explicit sensor types found - will fall back to legacy detection
        return []
    
    @classmethod
    def _determine_sensor_subtype(cls, device_info: Dict[str, Any], device_type: DeviceType) -> DeviceSubtype:
        """Determine sensor subtype from device information."""
        # Check explicit device_subtype first (new format)
        if 'device_subtype' in device_info:
            device_subtype = device_info['device_subtype']
            if isinstance(device_subtype, DeviceSubtype):
                return device_subtype
        
        # Check explicit subtype string (legacy format)
        subtype_str = device_info.get('subtype', '').lower()
        if subtype_str:
            for subtype in DeviceSubtype:
                if subtype.value == subtype_str:
                    return subtype
        
        # EWneo sensors only - no EW sensors exist
        # Check available sensors for sensor configuration
        
        # Check device name for hints
        device_name = device_info.get('name', '').lower()
        if any(word in device_name for word in ['temp', 'temperature']):
            return DeviceSubtype.TEMPERATURE
        elif any(word in device_name for word in ['humid', 'humidity']):
            return DeviceSubtype.HUMIDITY
        elif any(word in device_name for word in ['wind']):
            return DeviceSubtype.WIND_SPEED
        elif any(word in device_name for word in ['rain', 'regen']):
            return DeviceSubtype.RAIN
        
        # Default for EWneo sensors (only sensor type that exists)
        return DeviceSubtype.TEMPERATURE  # Default for EWneo
    
    @classmethod
    def _determine_receiver_subtype(cls, device_info: Dict[str, Any]) -> DeviceSubtype:
        """Determine receiver subtype from device information."""
        # Check explicit subtype first
        subtype_str = device_info.get('subtype', '').lower()
        if subtype_str:
            for subtype in DeviceSubtype:
                if subtype.value == subtype_str:
                    return subtype
        
        # Check device name for hints
        device_name = device_info.get('name', '').lower()
        if any(word in device_name for word in ['motor', 'rollo', 'blind', 'jalousie', 'cover']):
            return DeviceSubtype.MOTOR
        elif any(word in device_name for word in ['heating', 'heiz', 'climate', 'thermo']):
            return DeviceSubtype.HEATING_COOLING
        # Note: EW dimmer removed - only EWneo has dimmers
        elif any(word in device_name for word in ['dimmer', 'dim']) and 'neo' in device_name:
            return DeviceSubtype.DIMMER
        
        # Default to switch
        return DeviceSubtype.SWITCH
    
    @classmethod
    def _determine_button_count(cls, device_info: Dict[str, Any]) -> int:
        """Determine button count from device information."""
        # Check explicit button count first
        button_count = device_info.get('button_count', device_info.get('channels'))
        if button_count is not None:
            return int(button_count)
        
        # Try to determine from name
        device_name = device_info.get('name', '').lower()
        button_keywords = {
            1: ['1-tast', 'eintast', 'single'],
            2: ['2-tast', 'zweitast', 'dual'],
            3: ['3-tast', 'dreitast', 'three'],
            4: ['4-tast', 'viertast', 'quad', 'four'],
        }
        
        for count, keywords in button_keywords.items():
            if any(keyword in device_name for keyword in keywords):
                return count
        
        # Default to 4 buttons
        return 4
    
    @classmethod
    def get_supported_device_types(cls) -> List[DeviceType]:
        """Get list of supported device types for RX11."""
        return [
            DeviceType.EW_RECEIVER,
            DeviceType.EW_TRANSMITTER,
            DeviceType.EWNEO_SENSOR,
            DeviceType.EWNEO_TRANSCEIVER,
            DeviceType.EWNEO_SWITCH,
            DeviceType.EWNEO_DUAL_SWITCH,
            DeviceType.EWNEO_QUAD_SWITCH,
            DeviceType.EWNEO_DIMMER,
            DeviceType.EWNEO_MOTOR,
            DeviceType.EWNEO_DUAL_MOTOR,
            DeviceType.EWNEO_QUAD_MOTOR,
        ]


# Global factory instance
rx11_device_factory = RX11DeviceFactory()

# Legacy compatibility - provide registry interface that wraps factory
class RX11DeviceHandlerRegistry:
    """Legacy device handler registry (compatibility layer).
    
    This class provides backward compatibility for existing code that expects
    a registry interface. It wraps the new factory-based approach.
    """
    
    def __init__(self):
        """Initialize registry wrapper."""
        self._factory = rx11_device_factory
    
    def get_supported_device_types(self):
        """Get supported device types (legacy interface)."""
        return set([dt.value for dt in self._factory.get_supported_device_types()])
    
    def create_device(self, *args, **kwargs):
        """Create device using factory (legacy interface)."""
        return self._factory.create_device(*args, **kwargs)


rx11_device_registry = RX11DeviceHandlerRegistry()