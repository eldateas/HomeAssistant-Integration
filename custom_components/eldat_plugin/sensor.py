"""Sensor entities for ELDAT integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.const import (
    UnitOfTemperature,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
)

from .const import (
    DOMAIN,
    SCAN_INTERVAL_SENSORS,
    UNIT_CELSIUS,
    UNIT_PERCENT,
    UNIT_DBM,
)
from .coordinator import EldatCoordinator
from .entity import EldatEntity
from .entity_registry import get_entity_registry
from .device_icons import (
    get_entity_config_for_device,
    get_extra_state_attributes_for_device
)

_LOGGER = logging.getLogger(__name__)

# Global tracking to prevent duplicate entity creation
_processed_sensor_devices: set[str] = set()
_created_sensor_entity_ids: set[str] = set()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ELDAT sensor entities with persistence support."""
    from .const import EVENT_DEVICE_ADDED
    
    coordinator: EldatCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    # Store async_add_entities for later use in event handlers
    _async_add_entities_callback = async_add_entities

    sensors = []
    
    # Single gateway sensor entity representing RX11 USB transceiver state
    gateway_entity = EldatGatewaySensor(coordinator)
    sensors.append(gateway_entity)
    
    # Only create device sensors if transceiver is connected
    if not coordinator.transceiver or not coordinator.transceiver.is_connected:
        _LOGGER.info("⚠️  USB transmitter not connected - device sensors will be unavailable")
    
    # Get entity registry for session tracking
    entity_registry = get_entity_registry()
    
    # Create sensors ONLY for registered devices 
    # This ensures only managed devices get sensor entities
    registered_devices = coordinator.get_all_registered_devices()
    _LOGGER.warning("🔄 Restoring sensor entities for %d registered devices", len(registered_devices))
    for serial_number, device_info in registered_devices.items():
        device_type = device_info.get("type", "unknown")
        device_name = device_info.get("name", serial_number)
        
        _LOGGER.warning("🔍 Processing device for sensors: %s (%s) - type=%s", 
                    device_name, serial_number[-8:], device_type)
        
        # Create sensors based on device capabilities
        # Note: We create sensor objects even for existing entities - HA needs them for restore
        device_sensors = _create_sensors_for_device(coordinator, serial_number, device_info)
        
        if device_sensors:
            # Mark sensors as processed in this session to avoid duplicate creation attempts
            for sensor in device_sensors:
                unique_id = getattr(sensor, '_attr_unique_id', None)
                if unique_id:
                    entity_registry.mark_entity_created(unique_id, serial_number)
            
            sensors.extend(device_sensors)
            _LOGGER.debug("Prepared %d sensor entities for device: %s (%s)", 
                        len(device_sensors), device_name, device_type)
        else:
            _LOGGER.warning("⚠️ No sensors created for device %s (%s) - checking registry...", 
                          device_name, device_type)
            
            # If no sensors were created but device should have sensors, log warning
            if device_type in ["ew_sensor", "ewneo_sensor", "ew_transceiver"]:
                _LOGGER.warning("⚠️ No sensors created for EW-Sensor device %s - may need manual intervention", serial_number[-8:])
    
    if sensors:
        async_add_entities(sensors)
        _LOGGER.info("✅ Added %d sensor entities (%d devices + gateway)", 
                    len(sensors), len(coordinator.get_all_devices()))
    else:
        _LOGGER.warning("⚠️ No sensors to add during initial setup")
        
        sensors.append(gateway_entity)  # Re-add gateway
        
        if sensors:
            async_add_entities(sensors)
            _LOGGER.info("✅ Emergency recovery: Added %d sensor entities after registry reset", len(sensors))
        else:
            _LOGGER.error("❌ Emergency recovery failed - still no sensors created")
    
    # Listen for new devices and create sensors dynamically
    async def _handle_device_added(event):
        """Handle device added event.
        
        This handler creates sensors for devices that need them,
        especially battery sensors for EW-Transmitter.
        """
        global _processed_sensor_devices
        try:
            serial_number = event.data.get("serial_number")
            device_info = event.data.get("device_info")
            entity_info = event.data.get("entity_info", {})
            platforms = entity_info.get("platforms", set())
            
            if not serial_number or not device_info:
                _LOGGER.warning("Device added event missing serial_number or device_info")
                return
            
            # Spezielle Behandlung für EW-Transmitter (brauchen nur Battery-Sensoren!)
            device_type = device_info.get("type", "unknown")
            
            # Check HA's entity registry before creating anything
            from homeassistant.helpers import entity_registry as er
            from .entity_registry import get_entity_registry
            ha_entity_registry = er.async_get(coordinator.hass)
            entity_registry = get_entity_registry()
            
            if device_type == "ew_transmitter":
                _LOGGER.info("🔋 Creating battery sensor for EW-Transmitter: %s", serial_number[-6:])
                new_sensors = _create_sensors_for_device(coordinator, serial_number, device_info)
                
                # Filter out already existing sensors
                sensors_to_add = []
                for sensor in new_sensors:
                    unique_id = getattr(sensor, '_attr_unique_id', None)
                    if unique_id:
                        existing_entity = ha_entity_registry.async_get_entity_id("sensor", "eldat_plugin", unique_id)
                        if not existing_entity and not entity_registry.is_entity_created_this_session(unique_id):
                            sensors_to_add.append(sensor)
                            entity_registry.mark_entity_created(unique_id, serial_number)
                
                if sensors_to_add:
                    async_add_entities(sensors_to_add, update_before_add=False)
                    _LOGGER.info("✅ Added %d sensor entities (battery) for EW-Transmitter %s", len(sensors_to_add), serial_number[-6:])
                return
            
            # Nur EW-Sensor devices bekommen temperature/humidity sensors
            if device_type not in ["ew_sensor", "ewneo_sensor"]:
                _LOGGER.debug("Skipping sensor creation for device %s - not an EW-Sensor (type: %s)", serial_number[-6:], device_type)
                return
            
            # Legacy handling for devices without specific platform handlers
            _LOGGER.info("Creating legacy sensor entities for device: %s", serial_number)
            new_sensors = _create_sensors_for_device(coordinator, serial_number, device_info)
            
            # Filter out already existing sensors
            sensors_to_add = []
            for sensor in new_sensors:
                unique_id = getattr(sensor, '_attr_unique_id', None)
                if unique_id:
                    existing_entity = ha_entity_registry.async_get_entity_id("sensor", "eldat_plugin", unique_id)
                    if not existing_entity and not entity_registry.is_entity_created_this_session(unique_id):
                        sensors_to_add.append(sensor)
                        entity_registry.mark_entity_created(unique_id, serial_number)
            
            if sensors_to_add:
                async_add_entities(sensors_to_add, update_before_add=False)
                await asyncio.sleep(0.2)
                _LOGGER.info("Added %d legacy sensor entities for device %s", len(sensors_to_add), serial_number)
                    
        except Exception as e:
            _LOGGER.error("Error creating sensors for new device: %s", e)
    
    # Also listen for platform-specific sensor events
    async def _handle_sensor_device_added(event):
        """Handle sensor-specific device added event."""
        _LOGGER.info("🎯 Sensor device added event received: %s", event.data.keys())
        _LOGGER.info("🎯 Event data: %s", {k: str(v)[:100] if isinstance(v, (dict, list)) else v for k, v in event.data.items()})
        try:
            serial_number = event.data.get("serial_number")
            device_info = event.data.get("device_info")
            entities = event.data.get("entities", [])
            device_type = event.data.get("device_type", "unknown")
            force_create = event.data.get("force_create", False)
            orphaned_repair = event.data.get("orphaned_device_repair", False)
            
            _LOGGER.info("📊 Processing sensor event - Serial: %s, Type: %s, Entities: %d, Force: %s, Orphaned: %s", 
                        serial_number[-8:] if serial_number else "None", device_type, len(entities), force_create, orphaned_repair)
            
            if not serial_number:
                _LOGGER.error("❌ No serial_number in event!")
                return
            
            if not entities:
                _LOGGER.warning("⚠️ No entities in event - will try to create from device_info")
            else:
                _LOGGER.info("📋 Entities to create: %s", [e.get('sensor_type') for e in entities])
            
            # Check if we have entity specs or need to create from device info
            if not entities and device_type in ["ew_sensor", "ew_transceiver"]:
                # Create entities based on device capabilities
                _LOGGER.info("🌡️ No entity specs provided, creating EW-Sensor entities from device info")
                new_sensors = _create_sensors_for_device(coordinator, serial_number, device_info)
            else:
                # Create entities from provided specs
                _LOGGER.info("📊 Creating %d sensor entities for device %s from specs: %s", 
                           len(entities), serial_number[-8:], [e.get('sensor_type') for e in entities])
                new_sensors = []
                
                for entity_spec in entities:
                    if entity_spec.get("type") == "sensor":
                        _LOGGER.info("📊 Creating sensor entity: %s", entity_spec.get('sensor_type'))
                        sensor = _create_configured_sensor(coordinator, serial_number, device_info, entity_spec)
                        if sensor:
                            new_sensors.append(sensor)
                            _LOGGER.info("✅ Sensor created: %s", entity_spec.get('sensor_type'))
                        else:
                            _LOGGER.warning("❌ Failed to create sensor: %s", entity_spec.get('sensor_type'))
            
            if new_sensors:
                from .entity_registry import get_entity_registry
                # Get Home Assistant's entity registry - the single source of truth
                from homeassistant.helpers import entity_registry as er
                ha_entity_registry = er.async_get(coordinator.hass)
                entity_registry = get_entity_registry()
                
                # Filter sensors: only add if NOT in HA's entity registry or if this is an orphan repair
                sensors_to_add = []
                for sensor in new_sensors:
                    unique_id = getattr(sensor, '_attr_unique_id', None) or getattr(sensor, 'unique_id', None)
                    if not unique_id:
                        continue
                    
                    # Check HA's entity registry (single source of truth)
                    existing_entity = ha_entity_registry.async_get_entity_id("sensor", "eldat_plugin", unique_id)
                    
                    if existing_entity:
                        # Entity already exists in HA - skip unless forced
                        if force_create or orphaned_repair:
                            _LOGGER.info("🔄 Entity %s exists but forced/orphaned - will attempt restore", unique_id[-16:])
                            # Don't add - HA will handle existing entities automatically
                        else:
                            _LOGGER.debug("Entity %s already exists in HA, skipping", unique_id[-16:])
                        continue
                    
                    # For force_create, bypass session tracking
                    if not force_create:
                        # Check session tracking to avoid duplicate attempts THIS session
                        if entity_registry.is_entity_created_this_session(unique_id):
                            _LOGGER.debug("Entity %s already created this session, skipping", unique_id[-16:])
                            continue
                    else:
                        _LOGGER.info("🔥 Force creating entity %s (bypassing session check)", unique_id[-16:])
                    
                    # New entity - safe to add
                    _LOGGER.info("🆕 Creating new sensor entity %s", unique_id[-16:])
                    sensors_to_add.append(sensor)
                    # Mark in session tracking
                    entity_registry.mark_entity_created(unique_id, serial_number)
                
                if sensors_to_add:
                    _LOGGER.info("🚀 Adding %d sensor entities to Home Assistant...", len(sensors_to_add))
                    for sensor in sensors_to_add:
                        unique_id = getattr(sensor, '_attr_unique_id', None) or getattr(sensor, 'unique_id', None)
                        name = getattr(sensor, '_attr_name', None) or getattr(sensor, 'name', 'unknown')
                        _LOGGER.info("  ➤ %s (%s)", name, unique_id[-16:] if unique_id else 'no-id')
                    
                    try:
                        _async_add_entities_callback(sensors_to_add, update_before_add=False)
                        _LOGGER.info("✅ async_add_entities called successfully for %d entities", len(sensors_to_add))
                    except Exception as add_error:
                        _LOGGER.error("❌ Error calling async_add_entities: %s", add_error, exc_info=True)
                    
                    # Give entities time to be properly registered
                    await asyncio.sleep(0.2)
                    
                    # Update coordinator with entity information
                    entity_ids = []
                    for sensor in sensors_to_add:
                        entity_id = getattr(sensor, '_attr_entity_id', None) or getattr(sensor, 'entity_id', None)
                        if not entity_id and hasattr(sensor, '_attr_unique_id'):
                            # Generate entity_id from unique_id if not set
                            unique_id = sensor._attr_unique_id
                            entity_id = f"sensor.{unique_id.lower()}"
                        if entity_id:
                            entity_ids.append(entity_id)
                    
                    if entity_ids:
                        await coordinator.update_device_ha_info(serial_number, entity_ids=entity_ids)
                    
                    _LOGGER.info("✅ Created %d sensor entities for device %s", len(sensors_to_add), serial_number[-8:])
                else:
                    # Check if entities already exist (which is normal)
                    if new_sensors:
                        _LOGGER.debug("All %d sensor entities for device %s already exist in HA", len(new_sensors), serial_number[-8:])
                    else:
                        _LOGGER.warning("⚠️ No sensors generated for device %s - this may indicate a problem", serial_number[-8:])
                
        except Exception as e:
            _LOGGER.error("Error creating sensor entities: %s", e, exc_info=True)
    
    # Register event listeners
    config_entry.async_on_unload(
        hass.bus.async_listen(EVENT_DEVICE_ADDED, _handle_device_added)
    )
    config_entry.async_on_unload(
        hass.bus.async_listen(f"{EVENT_DEVICE_ADDED}_sensor", _handle_sensor_device_added)
    )
    
    _LOGGER.info("📻 Registered sensor event listeners: '%s' and '%s'", 
                EVENT_DEVICE_ADDED, f"{EVENT_DEVICE_ADDED}_sensor")


def _create_configured_sensor(coordinator: EldatCoordinator, serial_number: str, device_info: Dict[str, Any], entity_spec: Dict[str, Any]) -> SensorEntity | None:
    """Create a sensor entity based on entity specification."""
    sensor_type = entity_spec.get("sensor_type")
    device_type = device_info.get("type", device_info.get("device_type", "unknown"))
    device_class = entity_spec.get("device_class")
    
    # Use dedicated EWneoSensorEntity for ewneo_sensor devices
    if device_type == "ewneo_sensor":
        return EWneoSensorEntity(coordinator, serial_number, device_info, entity_spec)
    
    # Handle enum sensors (Last Button sensor for EW-Transmitters in grouped mode)
    if device_class == "enum":
        return EldatLastButtonSensor(coordinator, serial_number, device_info, entity_spec)
    
    # Legacy sensor entities for other types
    if sensor_type == "temperature":
        return EldatEWReceiverTemperatureSensor(coordinator, serial_number, device_info, entity_spec)
    elif sensor_type == "humidity":
        return EldatEWReceiverHumiditySensor(coordinator, serial_number, device_info, entity_spec)
    elif sensor_type == "battery":
        return EldatEWReceiverBatterySensor(coordinator, serial_number, device_info, entity_spec)
    elif sensor_type == "rain":
        return EldatEWReceiverRainSensor(coordinator, serial_number, device_info, entity_spec)
    elif sensor_type == "wind":
        return EldatEWReceiverWindSensor(coordinator, serial_number, device_info, entity_spec)
    else:
        _LOGGER.warning("Unknown sensor type: %s (device_class: %s)", sensor_type, device_class)
        return None


def _create_sensors_for_device(coordinator: EldatCoordinator, serial_number: str, device_info: Dict[str, Any]) -> list:
    """Create sensor entities for a device based on its capabilities."""
    from .entity_registry import get_entity_registry
    
    sensors = []
    device_type = device_info.get("type", "unknown")
    sensor_types = device_info.get("sensor_types", [])
    measurement_types = device_info.get("measurement_types", [])
    entity_registry = get_entity_registry()
    
    # Always try to create/restore sensor entities for supported devices
    device_key = f"{serial_number}_{device_type}"
    
    _LOGGER.info("🔧 Creating/restoring sensors for device %s (type: %s, sensor_types: %s, measurement_types: %s)", 
                 serial_number[-8:], device_type, sensor_types, measurement_types)
    
    # Create temperature/humidity sensors ONLY for actual EW-Sensors (EWneo devices)
    if device_type in ["ew_sensor", "ewneo_sensor"]:
        _LOGGER.info("🌡️ Creating/restoring EW-Sensor entities for device %s (type: %s)", 
                     serial_number[-8:], device_type)
        
        # Ensure device has persistent serial number storage
        ew_receiver_serial = device_info.get("ew_receiver_serial")
        if ew_receiver_serial:
            _LOGGER.info("📝 EW-Receiver serial for device %s: %s", 
                        serial_number[-8:], ew_receiver_serial[-8:])
        
        # Create sensor entities for EW-Sensors (temperature, humidity, battery)
        default_sensors = ["temperature", "humidity", "battery"]
        
        for sensor_type in default_sensors:
            unique_id = f"{serial_number}_{sensor_type}"
            
            # Simple logging - HA will handle duplicate prevention
            _LOGGER.info("Creating %s sensor entity for device %s", sensor_type, serial_number[-8:])
                
            # Get last stored value for restoration
            last_value = device_info.get(f"last_{sensor_type}")
            
            # Create sensor based on type (always create the entity object, regardless of registry state)
            if sensor_type == "temperature":
                sensor = EWneoSensorEntity(
                    coordinator=coordinator,
                    serial_number=serial_number,
                    device_info=device_info,
                    entity_spec={
                        "unique_id": unique_id,
                        "name": "Temperature",
                        "sensor_type": "temperature",
                        "device_class": SensorDeviceClass.TEMPERATURE,
                        "unit_of_measurement": UnitOfTemperature.CELSIUS,
                        "icon": "mdi:thermometer",
                        "current_value": last_value,
                        "ew_receiver_serial": ew_receiver_serial
                    }
                )
            elif sensor_type == "humidity":
                sensor = EWneoSensorEntity(
                    coordinator=coordinator,
                    serial_number=serial_number,
                    device_info=device_info,
                    entity_spec={
                        "unique_id": unique_id,
                        "name": "Humidity",
                        "sensor_type": "humidity",
                        "device_class": SensorDeviceClass.HUMIDITY,
                        "unit_of_measurement": PERCENTAGE,
                        "icon": "mdi:water-percent",
                        "current_value": last_value,
                        "ew_receiver_serial": ew_receiver_serial
                    }
                )
            elif sensor_type == "battery":
                sensor = EWneoSensorEntity(
                    coordinator=coordinator,
                    serial_number=serial_number,
                    device_info=device_info,
                    entity_spec={
                        "unique_id": unique_id,
                        "name": "Battery",
                        "sensor_type": "battery",
                        "device_class": SensorDeviceClass.BATTERY,
                        "unit_of_measurement": PERCENTAGE,
                        "icon": "mdi:battery",
                        "current_value": last_value,
                        "ew_receiver_serial": ew_receiver_serial
                    }
                )
            
            sensors.append(sensor)
            
            # Log restoration status
            if last_value is not None:
                unit = "°C" if sensor_type == "temperature" else "%"
                _LOGGER.info("🔄 Restored %s sensor for %s with value %s%s", 
                           sensor_type, serial_number[-8:], last_value, unit)
            else:
                _LOGGER.info("🔄 Created %s sensor for %s (no stored value)", 
                           sensor_type, serial_number[-8:])
    
    # Battery sensor for devices that need battery monitoring (including EW-Transmitter for low battery warnings)
    # Note: ew_sensor and ewneo_sensor get battery sensors created above in the main sensor block
    elif device_type in ["ew_transceiver", "ewneo_transceiver", "ewneo_bidi_transmitter"]:
        sensors.append(EldatBatterySensor(
            coordinator=coordinator,
            serial_number=serial_number,
            device_info=device_info,
        ))
        _LOGGER.info("🔋 Created battery sensor for device %s", serial_number[-8:])
    
    # Legacy sensor creation for old EWneo-Sensoren only (not EW-Transmitters!)
    elif device_info.get("supports_sensors", False) and device_type not in ["ew_transmitter"]:
        if "temperature" in str(device_info.get("info_type", "")).lower():
            sensors.append(EldatTemperatureSensor(
                coordinator=coordinator,
                serial_number=serial_number,
                device_info=device_info,
            ))
            _LOGGER.info("🌡️ Created legacy temperature sensor for %s", serial_number[-8:])
        
    # EW-Transmitter devices get battery sensors and optionally a "Last Button" sensor for grouped mode
    elif device_type == "ew_transmitter":
        _LOGGER.warning("🔋 Creating sensors for EW-Transmitter %s (grouping_mode=%s, switch_mode=%s)", 
                       serial_number[-8:], device_info.get("grouping_mode"), device_info.get("switch_mode"))
        
        # Battery sensor (always created)
        sensors.append(EldatBatterySensor(
            coordinator=coordinator,
            serial_number=serial_number,
            device_info=device_info,
        ))
        
        # Check if we need a "Last Button" sensor for grouped mode
        from .entity_specs import create_entity_specs_for_device
        entity_specs = create_entity_specs_for_device(serial_number, device_info)
        sensor_specs = entity_specs.get("sensor", [])
        
        _LOGGER.warning("📊 EW-Transmitter %s entity_specs returned %d sensor specs: %s", 
                       serial_number[-8:], len(sensor_specs), sensor_specs)
        
        for spec in sensor_specs:
            if spec.get("sensor_type") == "transmitter_state":
                sensors.append(EldatTransmitterStateSensor(
                    coordinator=coordinator,
                    serial_number=serial_number,
                    device_info=device_info,
                    entity_spec=spec,
                ))
                _LOGGER.warning("✅ Created transmitter state sensor for EW-Transmitter %s", serial_number[-8:])
            elif spec.get("device_class") == "enum":
                # Create Last Button sensor for grouped mode
                sensors.append(EldatLastButtonSensor(
                    coordinator=coordinator,
                    serial_number=serial_number,
                    device_info=device_info,
                    entity_spec=spec,
                ))
                _LOGGER.warning("✅ Created 'Last Button' sensor for grouped EW-Transmitter %s (switch_mode=%s)", 
                              serial_number[-8:], spec.get("switch_mode", "impulse"))
        
        _LOGGER.warning("✅ Created %d sensors for EW-Transmitter %s", len(sensors), serial_number[-8:])
    
    return sensors
class EldatGatewaySensor(SensorEntity):
    """Represents the RX11 USB gateway connectivity/state."""

    def __init__(self, coordinator: EldatCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_rx11_gateway"
        self._attr_name = "ELDAT RX11 Gateway"
        self._attr_icon = "mdi:usb"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._last_status = None
        
        # Get version info from transceiver (loaded at connect time)
        transceiver = coordinator.transceiver
        hw_version = getattr(transceiver, '_hw_version', None) or "Unknown"
        sw_version = getattr(transceiver, '_fw_version', None) or "Unknown"
        
        # Device info for RX11 Gateway - all buttons will be grouped here
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_gateway")},
            name="ELDAT RX11 USB Transceiver",
            manufacturer="ELDAT EaS GmbH",
            model="RX11",
            sw_version=sw_version,
            hw_version=hw_version,
        )

    @property
    def native_value(self) -> str:
        """Return detailed connection status."""
        status = self._get_connection_status()
        return status
    
    def _get_connection_status(self) -> str:
        """Get detailed connection status string."""
        transceiver = self.coordinator.transceiver
        if not transceiver:
            return "nicht konfiguriert"
        
        # Check for hardware error first
        if hasattr(transceiver, '_rx11_wrapper') and transceiver._rx11_wrapper:
            wrapper = transceiver._rx11_wrapper
            if hasattr(wrapper, '_rx_module') and wrapper._rx_module:
                rx_module = wrapper._rx_module
                if hasattr(rx_module, 'connection_status'):
                    status = rx_module.connection_status
                    if status == "hardware_error":
                        return "Hardware-Fehler (USB getrennt?)"
                    elif status == "reconnecting":
                        return "Verbindung wird hergestellt..."
                    elif status == "error":
                        return "Fehler"
                    elif status == "disconnected":
                        return "Getrennt"
                    elif status == "connected":
                        return "Verbunden"
        
        # Fallback to simple connected check
        if self._is_connected():
            return "Verbunden"
        return "Getrennt"

    def _is_connected(self) -> bool:
        transceiver = self.coordinator.transceiver
        return bool(transceiver and hasattr(transceiver, 'is_connected') and transceiver.is_connected)

    @property
    def available(self) -> bool:
        """Gateway sensor is always available to show status."""
        # Gateway sensor should always be available so users can see the connection status
        return True
    
    @property
    def icon(self) -> str:
        """Return icon based on connection status."""
        if self._is_connected():
            return "mdi:usb"
        return "mdi:usb-off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        transceiver = self.coordinator.transceiver
        attrs = {
            "device_path": getattr(transceiver, "device_path", None),
            "known_devices": len(self.coordinator.devices),
            "learning_mode": getattr(transceiver, "_learning_mode", False),
            "continuous_monitoring": getattr(transceiver, "_ewb_rcv_running", False),
            "connected": self._is_connected(),
        }
        
        # Add reconnect info if available
        if hasattr(self.coordinator, '_reconnect_attempts'):
            attrs["reconnect_attempts"] = self.coordinator._reconnect_attempts
        
        # Add hardware error info if available
        if hasattr(transceiver, '_rx11_wrapper') and transceiver._rx11_wrapper:
            wrapper = transceiver._rx11_wrapper
            if hasattr(wrapper, '_rx_module') and wrapper._rx_module:
                rx_module = wrapper._rx_module
                if hasattr(rx_module, 'has_hardware_error'):
                    attrs["hardware_error"] = rx_module.has_hardware_error
                if hasattr(rx_module, 'last_error') and rx_module.last_error:
                    attrs["last_error"] = rx_module.last_error
        
        return attrs

    async def async_update(self) -> None:
        self._last_status = self._get_connection_status()


class EldatTemperatureSensor(EldatEntity, SensorEntity):
    """Temperature sensor for ELDAT devices."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
    ) -> None:
        """Initialize temperature sensor."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._attr_unique_id = f"{serial_number}_temperature"
        self._attr_name = "Temperature"
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        
        # Value tracking
        self._last_reading: Optional[float] = None
        self._last_update: Optional[datetime] = None
        
        # Get device-specific configuration
        device_type = device_info.get("type", "unknown")
        device_config = get_entity_config_for_device(
            device_type=device_type,
            sensor_type="temperature",
            entity_type="sensor"
        )
        
        if device_config.get("icon"):
            self._attr_icon = device_config["icon"]
        
        # Event listeners will be setup in async_added_to_hass

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        # Sensors need active coordinator AND transceiver connection for live data
        if not self.coordinator.last_update_success:
            return False
        
        # Check if transceiver is connected for live sensor readings
        if not hasattr(self.coordinator, 'transceiver') or not self.coordinator.transceiver:
            return False
            
        return self.coordinator.transceiver.is_connected

    async def async_added_to_hass(self) -> None:
        """Setup event listeners when entity is added to hass."""
        await super().async_added_to_hass()
        self._setup_event_listeners()

    def _setup_event_listeners(self):
        """Setup event listeners for telegram updates."""
        from .const import EVENT_DEVICE_UPDATED, EVENT_TELEGRAM_RECEIVED, DOMAIN
        
        def handle_device_update(event):
            if event.data.get("serial_number") == self._serial_number:
                telegram_data = event.data.get("telegram_data", {})
                if "temperature" in telegram_data:
                    self._last_reading = telegram_data["temperature"]
                    self._last_update = datetime.now()
                    _LOGGER.debug("Temperature sensor %s updated via EVENT_DEVICE_UPDATED: %.1f°C", 
                                self._serial_number[-6:], self._last_reading)
                    self.async_schedule_update_ha_state()
        
        def handle_telegram(event):
            if event.data.get("serial_number") == self._serial_number:
                telegram_data = event.data.get("telegram_data", {})
                if "temperature" in telegram_data:
                    self._last_reading = telegram_data["temperature"]
                    self._last_update = datetime.now()
                    _LOGGER.debug("Temperature sensor %s updated via EVENT_TELEGRAM_RECEIVED: %.1f°C", 
                                self._serial_number[-6:], self._last_reading)
                    self.async_schedule_update_ha_state()
        
        # Also listen for sensor_update events
        def handle_sensor_update(event):
            event_data = event.data
            if (event_data.get("serial_number") == self._serial_number and 
                event_data.get("measurement_type") == "temperature"):
                temperature = event_data.get("value")
                if temperature is not None:
                    self._last_reading = temperature
                    self._last_update = datetime.now()
                    _LOGGER.info("📡 Temperature sensor %s updated via sensor_update: %.1f°C", 
                               self._serial_number[-6:], self._last_reading)
                    self.async_schedule_update_ha_state()
        
        # Register listeners and store removal functions
        self._device_update_listener = self.coordinator.hass.bus.async_listen(EVENT_DEVICE_UPDATED, handle_device_update)
        self._telegram_listener = self.coordinator.hass.bus.async_listen(EVENT_TELEGRAM_RECEIVED, handle_telegram)
        self._sensor_update_listener = self.coordinator.hass.bus.async_listen(f"{DOMAIN}_sensor_update", handle_sensor_update)
        
        _LOGGER.debug("🎯 Temperature sensor %s event listeners registered for serial %s", 
                     self._attr_unique_id, self._serial_number[-8:])

    async def async_added_to_hass(self) -> None:
        """Called when entity is added to Home Assistant."""
        await super().async_added_to_hass()
        
        # Ensure event listeners are set up
        if not hasattr(self, '_device_update_listener'):
            self._setup_event_listeners()
            _LOGGER.info("🔄 Temperature sensor %s: Event listeners registered after restart", self.name)
    
    async def async_will_remove_from_hass(self) -> None:
        """Called when entity will be removed from Home Assistant."""
        await super().async_will_remove_from_hass()
        
        # Clean up event listeners
        if hasattr(self, '_device_update_listener') and self._device_update_listener:
            self._device_update_listener()
        if hasattr(self, '_telegram_listener') and self._telegram_listener:
            self._telegram_listener()
        if hasattr(self, '_sensor_update_listener') and self._sensor_update_listener:
            self._sensor_update_listener()

    @property
    def native_value(self) -> float | None:
        """Return the current temperature."""
        return self._last_reading

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Consider unavailable if no reading in last 2 hours
        if self._last_update is None:
            return False
        
        return (datetime.now() - self._last_update) < timedelta(hours=2)

    async def async_update(self) -> None:
        """Update sensor state from coordinator data."""
        try:
            # Get latest data from coordinator
            device_data = self.coordinator.devices.get(self._serial_number, {})
            if "temperature" in device_data:
                self._last_reading = device_data["temperature"]
                if "last_seen" in device_data:
                    self._last_update = datetime.fromtimestamp(device_data["last_seen"])
        except Exception as e:
            _LOGGER.error("Error updating temperature sensor %s: %s", 
                         self._serial_number, e)


class EldatHumiditySensor(EldatEntity, SensorEntity):
    """Humidity sensor for ELDAT devices."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
    ) -> None:
        """Initialize humidity sensor."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._attr_unique_id = f"{serial_number}_humidity"
        self._attr_name = "Humidity"
        self._attr_device_class = SensorDeviceClass.HUMIDITY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_icon = "mdi:water-percent"
        
        # Value tracking
        self._last_reading: Optional[float] = None
        self._last_update: Optional[datetime] = None
        
        # Event listeners will be setup in async_added_to_hass

    async def async_added_to_hass(self) -> None:
        """Setup event listeners when entity is added to hass."""
        await super().async_added_to_hass()
        self._setup_event_listeners()

    def _setup_event_listeners(self):
        """Setup event listeners for telegram updates."""
        from .const import EVENT_DEVICE_UPDATED, EVENT_TELEGRAM_RECEIVED, DOMAIN
        
        def handle_device_update(event):
            if event.data.get("serial_number") == self._serial_number:
                telegram_data = event.data.get("telegram_data", {})
                if "humidity" in telegram_data:
                    self._last_reading = telegram_data["humidity"]
                    self._last_update = datetime.now()
                    _LOGGER.debug("Humidity sensor %s updated via EVENT_DEVICE_UPDATED: %.1f%%", 
                                self._serial_number[-6:], self._last_reading)
                    self.async_schedule_update_ha_state()
        
        def handle_telegram(event):
            if event.data.get("serial_number") == self._serial_number:
                telegram_data = event.data.get("telegram_data", {})
                if "humidity" in telegram_data:
                    self._last_reading = telegram_data["humidity"]
                    self._last_update = datetime.now()
                    _LOGGER.debug("Humidity sensor %s updated via EVENT_TELEGRAM_RECEIVED: %.1f%%", 
                                self._serial_number[-6:], self._last_reading)
                    self.async_schedule_update_ha_state()
        
        # Also listen for sensor_update events
        def handle_sensor_update(event):
            event_data = event.data
            if (event_data.get("serial_number") == self._serial_number and 
                event_data.get("measurement_type") == "humidity"):
                humidity = event_data.get("value")
                if humidity is not None:
                    self._last_reading = humidity
                    self._last_update = datetime.now()
                    _LOGGER.info("📡 Humidity sensor %s updated via sensor_update: %.1f%%", 
                               self._serial_number[-6:], self._last_reading)
                    self.async_schedule_update_ha_state()
        
        # Register listeners and store removal functions
        self._device_update_listener = self.coordinator.hass.bus.async_listen(EVENT_DEVICE_UPDATED, handle_device_update)
        self._telegram_listener = self.coordinator.hass.bus.async_listen(EVENT_TELEGRAM_RECEIVED, handle_telegram)
        self._sensor_update_listener = self.coordinator.hass.bus.async_listen(f"{DOMAIN}_sensor_update", handle_sensor_update)
        
        _LOGGER.debug("🎯 Humidity sensor %s event listeners registered for serial %s", 
                     self._attr_unique_id, self._serial_number[-8:])

    async def async_added_to_hass(self) -> None:
        """Called when entity is added to Home Assistant."""
        await super().async_added_to_hass()
        
        # Ensure event listeners are set up
        if not hasattr(self, '_device_update_listener'):
            self._setup_event_listeners()
            _LOGGER.info("🔄 Humidity sensor %s: Event listeners registered after restart", self.name)
    
    async def async_will_remove_from_hass(self) -> None:
        """Called when entity will be removed from Home Assistant."""
        await super().async_will_remove_from_hass()
        
        # Clean up event listeners
        if hasattr(self, '_device_update_listener') and self._device_update_listener:
            self._device_update_listener()
        if hasattr(self, '_telegram_listener') and self._telegram_listener:
            self._telegram_listener()
        if hasattr(self, '_sensor_update_listener') and self._sensor_update_listener:
            self._sensor_update_listener()

    @property
    def native_value(self) -> float | None:
        """Return the current humidity."""
        return self._last_reading

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Consider unavailable if no reading in last 2 hours
        if self._last_update is None:
            return False
        
        return (datetime.now() - self._last_update) < timedelta(hours=2)

    async def async_update(self) -> None:
        """Update sensor state from coordinator data."""
        try:
            # Get latest data from coordinator
            device_data = self.coordinator.devices.get(self._serial_number, {})
            if "humidity" in device_data:
                self._last_reading = device_data["humidity"]
                if "last_seen" in device_data:
                    self._last_update = datetime.fromtimestamp(device_data["last_seen"])
        except Exception as e:
            _LOGGER.error("Error updating humidity sensor %s: %s", 
                         self._serial_number, e)


class EldatLastButtonSensor(EldatEntity, RestoreEntity, SensorEntity):
    """Sensor showing the last pressed button for EW-Transmitters in grouped mode.
    
    Supports two switch modes:
    - "impulse": State is reset after button release
    - "permanent": State persists until next button press
    """

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        """Initialize last button sensor."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._entity_spec = entity_spec
        self._switch_mode = entity_spec.get("switch_mode", "impulse")
        self._options = entity_spec.get("options", ["A", "B", "C", "D"])
        self._unknown_value = "Aus" if "Aus" in self._options else None
        
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_last_button")
        self._attr_has_entity_name = True
        self._attr_name = "Taste"  # Short name, device name is added automatically
        self._attr_icon = entity_spec.get("icon", "mdi:radiobox-marked")
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = self._options
        
        self._current_button = None
        self._reset_timer = None
        self._reset_delay_ms = 500  # Reset after 500ms for impulse mode
        
        _LOGGER.info("📍 Last Button sensor initialized: %s (switch_mode=%s, options=%s)", 
                    self._attr_name, self._switch_mode, self._options)

    @property
    def available(self) -> bool:
        """Return True - sensor is always available even without a button press."""
        return True

    @property
    def native_value(self) -> str | None:
        """Return the last pressed button."""
        return self._current_button

    @property
    def icon(self) -> str:
        """Return icon based on current state."""
        if self._current_button in (None, "Aus", "Unknown", "unknown"):
            return "mdi:radiobox-blank"
        return "mdi:radiobox-marked"

    async def async_added_to_hass(self) -> None:
        """Register for button press events when entity is added to hass."""
        await super().async_added_to_hass()
        
        # Restore previous state if available
        if self._switch_mode != "impulse":
            persistent = self.coordinator.get_device_state(self._serial_number) or {}
            if "last_button" in persistent and persistent["last_button"] in self._options:
                self._current_button = persistent["last_button"]
                _LOGGER.info("📍 Restored last button state: %s for %s (persistent)", 
                           self._current_button, self._attr_name)
            elif (last_state := await self.async_get_last_state()) is not None:
                if last_state.state in self._options:
                    self._current_button = last_state.state
                    _LOGGER.info("📍 Restored last button state: %s for %s", 
                               self._current_button, self._attr_name)

        if self._switch_mode == "impulse" and self._unknown_value and self._current_button is None:
            self._current_button = self._unknown_value
            self.async_write_ha_state()
        
        @callback
        def _handle_button_press(event):
            """Handle button press - update last button state."""
            event_serial = event.data.get("serial_number", "") or event.data.get("device_id", "")
            
            _LOGGER.warning("📍 Last Button sensor %s received event: %s", self._attr_name, event.data)
            
            # Check if this event is for our device (compare last 8 chars for both full and short serials)
            my_short_serial = self._serial_number[-8:]
            event_short_serial = event_serial[-8:] if len(event_serial) >= 8 else event_serial
            
            if my_short_serial != event_short_serial:
                _LOGGER.debug("📍 Event not for this sensor: %s != %s", event_short_serial, my_short_serial)
                return
            
            action_label = event.data.get("action_label")
            button_name = event.data.get("button_name", event.data.get("subtype", ""))
            selected_label = action_label if action_label in self._options else button_name
            
            if selected_label in self._options:
                self._current_button = selected_label
                self.async_write_ha_state()
                _LOGGER.warning("📍 Last Button sensor %s: button %s pressed (switch_mode=%s)",
                            self._attr_name, selected_label, self._switch_mode)
                if self._switch_mode != "impulse":
                    self.coordinator.set_device_state(
                        self._serial_number, {"last_button": selected_label}
                    )
            else:
                _LOGGER.warning("📍 Button '%s' not in options %s", selected_label, self._options)
        
        @callback
        def _handle_button_release(event):
            """Handle button release - reset state for impulse mode."""
            if self._switch_mode != "impulse":
                return
            
            event_serial = event.data.get("serial_number", "") or event.data.get("device_id", "")
            
            # Check if this event is for our device
            if event_serial != self._serial_number:
                if len(event_serial) >= 8 and len(self._serial_number) >= 8:
                    if event_serial[-8:] != self._serial_number[-8:]:
                        return
                else:
                    return
            
            # Cancel any existing reset timer
            if self._reset_timer:
                self._reset_timer.cancel()
            
            # Reset after short delay
            async def _reset_state():
                await asyncio.sleep(self._reset_delay_ms / 1000.0)
                self._current_button = self._unknown_value if self._unknown_value else None
                self.async_write_ha_state()
                _LOGGER.debug("📍 Last Button sensor %s: state reset (impulse mode)", self._attr_name)
            
            self._reset_timer = asyncio.create_task(_reset_state())
        
        # Listen for button press events
        self.async_on_remove(
            self.hass.bus.async_listen("eldat_button_short_press", _handle_button_press)
        )
        
        # Listen for button release events (for impulse mode reset)
        if self._switch_mode == "impulse":
            self.async_on_remove(
                self.hass.bus.async_listen("eldat_button_press_end", _handle_button_release)
            )
        
        _LOGGER.info("📍 Last Button sensor %s registered for events", self._attr_name)


class EldatTransmitterStateSensor(EldatEntity, RestoreEntity, SensorEntity):
    """State sensor for EW-Transmitters in 2/3-button modes.

    Updates its enum state based on button press events.
    """

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        super().__init__(coordinator, serial_number, device_info)

        self._entity_spec = entity_spec
        self._button_map = entity_spec.get("button_map", {})
        self._options = entity_spec.get("options", [])

        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_state")
        self._attr_has_entity_name = False
        self._attr_name = entity_spec.get("name", "Status")
        self._attr_icon = entity_spec.get("icon", "mdi:toggle-switch")
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = self._options

        self._current_state = None
        self._icon_on, self._icon_off = self._resolve_state_icons()

        _LOGGER.info(
            "📍 Transmitter state sensor initialized: %s (options=%s, buttons=%s)",
            self._attr_name,
            self._options,
            list(self._button_map.keys()),
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str | None:
        return self._current_state

    def _resolve_state_icons(self) -> tuple[str, str]:
        options = set(self._options)
        if {"An", "Aus"}.issubset(options):
            return "mdi:light-switch", "mdi:light-switch-off"
        if {"Auf", "Zu"}.issubset(options):
            return "mdi:window-shutter-open", "mdi:window-shutter"
        return self._attr_icon, self._attr_icon

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        persistent = self.coordinator.get_device_state(self._serial_number) or {}
        if "transmitter_state" in persistent and persistent["transmitter_state"] in self._options:
            self._current_state = persistent["transmitter_state"]
            _LOGGER.info("📍 Restored transmitter state: %s for %s (persistent)", self._current_state, self._attr_name)
        elif (last_state := await self.async_get_last_state()) is not None:
            if last_state.state in self._options:
                self._current_state = last_state.state
                _LOGGER.info("📍 Restored transmitter state: %s for %s", self._current_state, self._attr_name)

        @callback
        def _handle_button_event(event):
            event_serial = event.data.get("serial_number", "") or event.data.get("device_id", "")
            event_button = event.data.get("button")
            if event_button is None:
                event_button = event.data.get("button_id")

            # Compare full serial numbers or match by last 8 characters as fallback
            if event_serial != self._serial_number:
                # Fallback: compare last 8 characters if both are long enough
                if len(event_serial) >= 8 and len(self._serial_number) >= 8:
                    if event_serial[-8:] != self._serial_number[-8:]:
                        return
                else:
                    return

            if event_button in self._button_map:
                new_state = self._button_map[event_button]
                if new_state != self._current_state:
                    self._current_state = new_state
                    self.async_write_ha_state()
                    _LOGGER.debug("📍 Transmitter state %s -> %s", self._attr_name, new_state)
                    self.coordinator.set_device_state(
                        self._serial_number, {"transmitter_state": new_state}
                    )

        self.async_on_remove(
            self.hass.bus.async_listen("eldat_button_short_press", _handle_button_event)
        )
        self.async_on_remove(
            self.hass.bus.async_listen("eldat_button_press_start", _handle_button_event)
        )

    @property
    def icon(self) -> str:
        if self._current_state in ("An", "Auf"):
            return self._icon_on
        if self._current_state in ("Aus", "Zu"):
            return self._icon_off
        return self._attr_icon




class EldatBatterySensor(EldatEntity, SensorEntity):
    """Battery level sensor for ELDAT devices."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
    ) -> None:
        """Initialize battery sensor."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._attr_unique_id = f"{serial_number}_battery"
        self._attr_name = "Battery"
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_icon = "mdi:battery"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._battery_level = None
        self._last_update = None

    @property
    def native_value(self) -> int | None:
        """Return the current battery level."""
        return self._battery_level

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Battery info comes from telegrams, so consider available if we have recent data
        if self._last_update is None:
            return False
        
        return (datetime.now() - self._last_update) < timedelta(days=1)

    @property
    def icon(self) -> str:
        """Return battery icon based on level."""
        if self._battery_level is None:
            return "mdi:battery-unknown"
        
        if self._battery_level <= 10:
            return "mdi:battery-outline"
        elif self._battery_level <= 20:
            return "mdi:battery-20"
        elif self._battery_level <= 30:
            return "mdi:battery-30"
        elif self._battery_level <= 40:
            return "mdi:battery-40"
        elif self._battery_level <= 50:
            return "mdi:battery-50"
        elif self._battery_level <= 60:
            return "mdi:battery-60"
        elif self._battery_level <= 70:
            return "mdi:battery-70"
        elif self._battery_level <= 80:
            return "mdi:battery-80"
        elif self._battery_level <= 90:
            return "mdi:battery-90"
        else:
            return "mdi:battery"

    async def update_battery_level(self, level: int) -> None:
        """Update battery level from telegram."""
        old_level = self._battery_level
        if 0 <= level <= 100:
            self._battery_level = level
            self._last_update = datetime.now()
            self.async_write_ha_state()
            _LOGGER.info("🔋 Battery sensor %s updated: %s%% -> %d%% (entity: %s)", 
                        self.entity_id, old_level if old_level is not None else "None", 
                        level, self.name)
        else:
            _LOGGER.warning("🔋 Invalid battery level %s for sensor %s", level, self.entity_id)

    async def async_added_to_hass(self) -> None:
        """Add event listeners when entity is added to hass."""
        await super().async_added_to_hass()
        
        # Listen for button events (EW-Transmitter battery updates)
        self._button_press_listener = self.hass.bus.async_listen(
            f"{DOMAIN}_button_press",
            self._handle_button_event
        )
        
        self._button_release_listener = self.hass.bus.async_listen(
            f"{DOMAIN}_button_release",
            self._handle_button_event
        )
        
        # Also listen for sensor update events (EW-Sensor battery updates)
        self._sensor_update_listener = self.hass.bus.async_listen(
            f"{DOMAIN}_sensor_update",
            self._handle_sensor_update
        )
        
        # Setup live telegram listeners for real-time updates
        await self._setup_telegram_listeners()
        
        _LOGGER.debug("🔋 Battery sensor %s added with listeners", self.entity_id)

    async def async_will_remove_from_hass(self) -> None:
        """Remove event listeners when entity is removed."""
        if hasattr(self, '_button_press_listener') and self._button_press_listener:
            self._button_press_listener()
        if hasattr(self, '_button_release_listener') and self._button_release_listener:
            self._button_release_listener()
        if hasattr(self, '_sensor_update_listener') and self._sensor_update_listener:
            self._sensor_update_listener()
        await super().async_will_remove_from_hass()

    async def _setup_telegram_listeners(self) -> None:
        """Setup live telegram event listeners for real-time battery updates."""
        from .const import EVENT_DEVICE_UPDATED, EVENT_TELEGRAM_RECEIVED
        
        async def handle_device_update(event):
            """Handle device update event."""
            try:
                data = event.data
                if data.get("serial_number") == self._serial_number:
                    battery_level = data.get("battery_level")
                    if battery_level is not None:
                        _LOGGER.info("📡 Battery sensor %s device update: %d%% (device: %s)",
                                    self.entity_id, battery_level, self._serial_number[-6:])
                        await self.update_battery_level(battery_level)
            except Exception as e:
                _LOGGER.error("❌ Battery sensor %s device update error: %s", self.entity_id, e)

        async def handle_telegram(event):
            """Handle telegram event with live battery data."""
            try:
                data = event.data
                telegram_data = data.get("data", {})
                
                # Check if this telegram is for our device (match last 6 digits)
                telegram_id = telegram_data.get("id")
                if not telegram_id or self._serial_number[-6:] != telegram_id[-6:]:
                    return
                
                # Extract battery data
                battery_level = telegram_data.get("battery_level") or telegram_data.get("battery")
                if battery_level is not None:
                    _LOGGER.info("📡 Battery sensor %s telegram update: %d%% (device: %s)",
                                self.entity_id, battery_level, self._serial_number[-6:])
                    await self.update_battery_level(battery_level)
                        
            except Exception as e:
                _LOGGER.error("❌ Battery sensor %s telegram error: %s", self.entity_id, e)
        
        self.coordinator.hass.bus.async_listen(EVENT_DEVICE_UPDATED, handle_device_update)
        self.coordinator.hass.bus.async_listen(EVENT_TELEGRAM_RECEIVED, handle_telegram)

    async def _handle_button_event(self, event) -> None:
        """Handle button press/release event (for EW-Transmitter)."""
        try:
            event_data = event.data
            event_serial = event_data.get("serial_number")
            
            # Check if this event is for our device
            if event_serial != self._serial_number:
                return
            
            # Get battery level from button event
            battery_level = event_data.get("battery_level")
            if battery_level is not None:
                _LOGGER.info("🔲 Battery sensor %s button event: %d%% (device: %s)", 
                           self.entity_id, battery_level, self._serial_number[-6:])
                await self.update_battery_level(battery_level)
                
        except Exception as e:
            _LOGGER.error("Error handling button event in battery sensor %s: %s", self.entity_id, e)

    async def _handle_sensor_update(self, event) -> None:
        """Handle sensor update event."""
        try:
            event_data = event.data
            event_serial = event_data.get("serial_number")
            
            # Check if this event is for our device
            if event_serial != self._serial_number:
                return
            
            # Get battery level from sensor update
            battery_level = event_data.get("battery_level")
            if battery_level is not None:
                _LOGGER.info("📊 Battery sensor %s sensor update: %d%% (device: %s)", 
                           self.entity_id, battery_level, self._serial_number[-6:])
                await self.update_battery_level(battery_level)
                
        except Exception as e:
            _LOGGER.error("Error handling sensor update in battery sensor %s: %s", self.entity_id, e)
            event_serial = event_data.get("device_id") or event_data.get("serial_number")
            
            if event_serial != self._serial_number:
                return
            
            battery_level = event_data.get("battery_level")
            if battery_level is not None:
                _LOGGER.info("🔋 Battery sensor %s updating to %d%% (from button event)", 
                            self.name, battery_level)
                self.update_battery_level(battery_level)
                
        except Exception as e:
            _LOGGER.error("Error handling button event in battery sensor %s: %s", self.name, e)

    async def _handle_sensor_update(self, event) -> None:
        """Handle sensor update event (for EW-Sensor)."""
        try:
            event_data = event.data
            event_serial = event_data.get("serial_number")
            
            if event_serial != self._serial_number:
                return
            
            battery_level = event_data.get("battery_level")
            if battery_level is not None:
                _LOGGER.info("🔋 Battery sensor %s updating to %d%% (from sensor_update)", 
                            self.name, battery_level)
                self.update_battery_level(battery_level)
                
        except Exception as e:
            _LOGGER.error("Error handling sensor update in battery sensor %s: %s", self.name, e)


class EldatSignalStrengthSensor(EldatEntity, SensorEntity):
    """Signal strength sensor for ELDAT devices."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
    ) -> None:
        """Initialize signal strength sensor."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._attr_unique_id = f"{serial_number}_signal_strength"
        self._attr_name = "Signal Strength"
        self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
        self._attr_icon = "mdi:wifi"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._signal_strength = None
        self._last_update = None

    @property
    def native_value(self) -> int | None:
        """Return the current signal strength."""
        return self._signal_strength

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Signal info comes from telegrams, so consider available if we have recent data
        if self._last_update is None:
            return False
        
        return (datetime.now() - self._last_update) < timedelta(hours=6)

    @property
    def icon(self) -> str:
        """Return signal strength icon based on level."""
        if self._signal_strength is None:
            return "mdi:wifi-strength-outline"
        
        if self._signal_strength <= -80:
            return "mdi:wifi-strength-1"
        elif self._signal_strength <= -70:
            return "mdi:wifi-strength-2"
        elif self._signal_strength <= -60:
            return "mdi:wifi-strength-3"
        else:
            return "mdi:wifi-strength-4"

    def update_signal_strength(self, strength: int) -> None:
        """Update signal strength from telegram."""
        if -120 <= strength <= 0:  # Reasonable range for signal strength
            self._signal_strength = strength
            self._last_update = datetime.now()
            self.schedule_update_ha_state()


class EldatDiagnosticSensor(EldatEntity, SensorEntity):
    """Diagnostic sensor for ELDAT integration status."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
    ) -> None:
        """Initialize diagnostic sensor."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._attr_unique_id = f"{serial_number}_last_seen"
        self._attr_name = "Last Seen"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:clock"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._last_seen = None

    @property
    def native_value(self) -> datetime | None:
        """Return when device was last seen."""
        return self._last_seen

    def update_last_seen(self) -> None:
        """Update last seen timestamp."""
        self._last_seen = datetime.now()
        self.schedule_update_ha_state()


class EWneoSensorEntity(EldatEntity, SensorEntity):
    """Universal sensor entity for EWneo-Sensoren.
    
    Communicates directly with EWneoSensor device class for readings.
    """

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        """Initialize EWneo-Sensoren entity."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._entity_spec = entity_spec
        self._sensor_type = entity_spec.get("sensor_type", "unknown")
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_{self._sensor_type}")
        self._attr_name = entity_spec.get("name", f"{device_info['name']} {self._sensor_type.title()}")
        self._attr_device_class = entity_spec.get("device_class")
        
        # Battery sensors go into diagnostics category
        if self._sensor_type == "battery":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_state_class = None  # Battery is status, not measurement
        else:
            self._attr_entity_category = None
            self._attr_state_class = SensorStateClass.MEASUREMENT
            
        self._attr_native_unit_of_measurement = entity_spec.get("unit_of_measurement")
        self._attr_icon = entity_spec.get("icon")
        
        self._last_reading = None
        self._last_update = None

    @property
    def native_value(self) -> float | int | None:
        """Return the sensor value by querying the device class."""
        try:
            # PRIMARY: Get value from coordinator's device data (most reliable)
            device_data = self.coordinator.devices.get(self._serial_number, {})
            
            # Battery is stored as "battery_level" in coordinator.devices
            lookup_key = "battery_level" if self._sensor_type == "battery" else self._sensor_type
            value = device_data.get(lookup_key)
            
            if value is not None:
                self._last_reading = value
                self._last_update = datetime.now()
                return value
            
            # SECONDARY: Try to get from device instance if available
            device = self.coordinator.get_device_instance(self._serial_number)
            if device and hasattr(device, 'get_sensor_data'):
                sensor_data = device.get_sensor_data()
                value = sensor_data.get(self._sensor_type)
                
                if value is not None:
                    self._last_reading = value
                    self._last_update = datetime.now()
                    return value
            
            # FALLBACK: Return last known reading
            return self._last_reading
            
        except Exception as e:
            _LOGGER.error("Error getting sensor value for %s: %s", self.entity_id, e)
            return self._last_reading

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Check if we have data in coordinator (most reliable)
        if self._serial_number in self.coordinator.devices:
            return True
        
        # Check if we have any reading
        if self._last_reading is not None:
            return True
        
        # Check device instance as fallback
        device = self.coordinator.get_device_instance(self._serial_number)
        if device and hasattr(device, 'get_sensor_data'):
            return True
        
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes."""
        attributes = super().extra_state_attributes or {}
        
        if self._last_update:
            attributes["last_update"] = self._last_update.isoformat()
        
        attributes["sensor_type"] = self._sensor_type
        
        # Get additional data from device
        try:
            device = self.coordinator.get_device_instance(self._serial_number)
            if device and hasattr(device, 'get_sensor_data'):
                sensor_data = device.get_sensor_data()
                
                # Add battery info if available
                if "battery_level" in sensor_data:
                    attributes["battery_level"] = sensor_data["battery_level"]
                if "battery_status" in sensor_data:
                    attributes["battery_status"] = sensor_data["battery_status"]
        except Exception as e:
            _LOGGER.debug("Could not get extra attributes for %s: %s", self.entity_id, e)
        
        return attributes

    async def async_added_to_hass(self) -> None:
        """Add listeners when entity is added to hass."""
        await super().async_added_to_hass()
        
        # Try to get initial value from coordinator device data
        try:
            device_data = self.coordinator.devices.get(self._serial_number, {})
            value = device_data.get(self._sensor_type)
            if value is not None:
                self._last_reading = value
                self._last_update = datetime.now()
                _LOGGER.info("🔄 EWneo %s: Initial %s value loaded from coordinator: %s", 
                            self._serial_number[-6:], self._sensor_type, value)
        except Exception as e:
            _LOGGER.debug("Could not load initial value for %s: %s", self.entity_id, e)
        
        # Subscribe to coordinator updates
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
        
        _LOGGER.warning("✅ EWneoSensorEntity added: %s (serial=%s)", 
                       self.entity_id, self._serial_number[:8]+"..."+self._serial_number[-8:])

    async def async_update(self) -> None:
        """Update the entity."""
        # Trigger coordinator update which will call our native_value property
        await self.coordinator.async_request_refresh()


class EldatEWReceiverTemperatureSensor(EldatEntity, SensorEntity):
    """Temperature sensor created from entity specification with EW-Receiver support."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        """Initialize configured temperature sensor with EW-Receiver serial storage."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._entity_spec = entity_spec
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_temperature")
        self._attr_name = entity_spec.get("name", "Temperature")
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = entity_spec.get("unit", UnitOfTemperature.CELSIUS)
        
        # Store EW-Receiver serial for persistent identification
        self._ew_receiver_serial = entity_spec.get("ew_receiver_serial") or device_info.get("ew_receiver_serial")
        if self._ew_receiver_serial:
            _LOGGER.debug("🔗 Temperature sensor %s linked to EW-Receiver serial %s", 
                        self.entity_id, self._ew_receiver_serial[-8:])
        
        # Get device-specific configuration
        device_type = device_info.get("type", "unknown")
        device_config = get_entity_config_for_device(
            device_type=device_type,
            sensor_type="temperature",
            entity_type="sensor"
        )
        
        self._attr_icon = entity_spec.get("icon") or device_config.get("icon", "mdi:thermometer")
        
        self._last_reading = entity_spec.get("current_value")
        self._last_update = datetime.now() if self._last_reading is not None else None
        self._remove_listener = None
        
        # Track if this is a restored entity
        self._restored = self._last_reading is not None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes including EW-Receiver serial."""
        attributes = super().extra_state_attributes or {}
        if self._ew_receiver_serial:
            attributes["ew_receiver_serial"] = self._ew_receiver_serial[-8:]  # Show last 8 digits
        return attributes

    async def async_added_to_hass(self) -> None:
        """Add event listeners when entity is added to hass."""
        await super().async_added_to_hass()
        
        # Listen for sensor_update events for this device
        self._remove_listener = self.hass.bus.async_listen(
            f"{DOMAIN}_sensor_update",
            self._handle_sensor_update
        )
        
        if self._restored:
            _LOGGER.info("🔄 Temperature sensor %s restored with value %.1f°C", 
                        self.entity_id, self._last_reading)
        else:
            _LOGGER.debug("Temperature sensor %s listening for sensor_update events", self.entity_id)
        
        # Setup live telegram listeners
        await self._setup_telegram_listeners()

    async def _setup_telegram_listeners(self) -> None:
        """Setup live telegram event listeners for real-time updates."""
        from .const import EVENT_DEVICE_UPDATED, EVENT_TELEGRAM_RECEIVED
        
        async def handle_device_update(event):
            """Handle device update event."""
            try:
                data = event.data
                _LOGGER.debug("🔍 Temperature sensor %s received device_update event: %s", self.entity_id, data)
                if data.get("serial_number") == self._serial_number:
                    temperature = data.get("temperature")
                    if temperature is not None:
                        converted_temperature = self._convert_temperature(temperature)
                        _LOGGER.info("📡 Temperature sensor %s device update: %.1f°C (raw: %s)",
                                    self.entity_id, converted_temperature, temperature)
                        self._last_reading = converted_temperature
                        self._last_update = datetime.now()
                        self.async_schedule_update_ha_state()
            except Exception as e:
                _LOGGER.error("❌ Temperature sensor %s device update error: %s", self.entity_id, e)

        async def handle_telegram(event):
            """Handle telegram event with live measurement data."""
            try:
                data = event.data
                _LOGGER.debug("🔍 Temperature sensor %s received telegram event: %s", self.entity_id, data)
                telegram_data = data.get("data", {})
                
                # Check if this telegram is for our device (match last 6 digits)
                telegram_id = telegram_data.get("id")
                if not telegram_id or self._serial_number[-6:] != telegram_id[-6:]:
                    return
                
                # Extract temperature data
                raw_temperature = telegram_data.get("temperature")
                if raw_temperature is not None:
                    converted_temperature = self._convert_temperature(raw_temperature, telegram_data)
                    _LOGGER.info("📡 Temperature sensor %s telegram update: %.1f°C (raw: %s, device: %s)",
                                self.entity_id, converted_temperature, raw_temperature,
                                self._serial_number[-6:])
                    self._last_reading = converted_temperature
                    self._last_update = datetime.now()
                    self.async_schedule_update_ha_state()
                        
            except Exception as e:
                _LOGGER.error("❌ Temperature sensor %s telegram error: %s", self.entity_id, e)

        # Also listen for sensor_update events (critical for new sensor updates!)
        async def handle_sensor_update(event):
            """Handle sensor_update event for new telegram data."""
            try:
                event_data = event.data
                if (event_data.get("serial_number") == self._serial_number and 
                    event_data.get("measurement_type") == "temperature"):
                    temperature = event_data.get("value")
                    if temperature is not None:
                        converted_temperature = self._convert_temperature(temperature)
                        _LOGGER.info("📡 Temperature sensor %s sensor_update: %.1f°C (raw: %s, device: %s)",
                                    self.entity_id, converted_temperature, temperature,
                                    self._serial_number[-6:])
                        self._last_reading = converted_temperature
                        self._last_update = datetime.now()
                        self.async_schedule_update_ha_state()
            except Exception as e:
                _LOGGER.error("❌ Temperature sensor %s sensor_update error: %s", self.entity_id, e)
        
        # Store listener removal functions
        self._device_update_listener = self.coordinator.hass.bus.async_listen(EVENT_DEVICE_UPDATED, handle_device_update)
        self._telegram_listener = self.coordinator.hass.bus.async_listen(EVENT_TELEGRAM_RECEIVED, handle_telegram)
        self._sensor_update_listener = self.coordinator.hass.bus.async_listen(f"{DOMAIN}_sensor_update", handle_sensor_update)
        
        _LOGGER.debug("🎯 Configured temperature sensor %s event listeners registered for serial %s", 
                     self._attr_unique_id, self._serial_number[-8:])

    async def async_will_remove_from_hass(self) -> None:
        """Remove event listeners when entity is removed."""
        await super().async_will_remove_from_hass()
        
        # Remove sensor_update listener
        if hasattr(self, '_remove_listener') and self._remove_listener:
            self._remove_listener()
            
        # Remove telegram listeners
        if hasattr(self, '_device_update_listener') and self._device_update_listener:
            self._device_update_listener()
        if hasattr(self, '_telegram_listener') and self._telegram_listener:
            self._telegram_listener()
        if hasattr(self, '_sensor_update_listener') and self._sensor_update_listener:
            self._sensor_update_listener()

    async def _handle_sensor_update(self, event) -> None:
        """Handle sensor update event."""
        try:
            event_data = event.data
            event_serial = event_data.get("serial_number")
            
            # Check if this event is for our device
            if event_serial != self._serial_number:
                return
            
            # Get measurement data directly from event
            measurement_type = event_data.get("measurement_type")
            value = event_data.get("value")
            battery_level = event_data.get("battery_level")
            
            # Also check for direct temperature value in combined events
            temperature = event_data.get("temperature")
            
            _LOGGER.debug("Temperature sensor %s received event: type=%s, value=%s, temp=%s", 
                         self.entity_id, measurement_type, value, temperature)
            
            # Handle direct temperature measurement
            temperature_value = None
            if measurement_type == "temperature" and value is not None:
                temperature_value = value
            elif temperature is not None:
                temperature_value = temperature
                
            if temperature_value is not None:
                _LOGGER.info("Temperature sensor %s updating to %.1f°C", self.entity_id, temperature_value)
                self.update_value(temperature_value)
                
                # Update battery info if available
                if battery_level is not None:
                    battery_status = event_data.get("battery_status", "unknown")
                    self._update_battery_info(battery_level, battery_status)
                    
        except Exception as e:
            _LOGGER.error("Error handling sensor update in temperature sensor %s: %s", self.entity_id, e)

    def _update_battery_info(self, level: int, status: str) -> None:
        """Update battery information (for future battery entity integration)."""
        # This could be used to update a related battery entity
        _LOGGER.debug("Temperature sensor %s battery: %s (level %d/7)", self.entity_id, status, level)

    @property
    def native_value(self) -> float | None:
        """Return the current temperature."""
        return self._last_reading

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # If we have a restored value, consider available for up to 24 hours
        # This allows sensors to remain available after restart even if not yet updated
        if self._last_update is None:
            # If we have a current value (from restore), consider available
            return self._last_reading is not None
        
        # Consider available if updated within last 24 hours (sensors don't update frequently)
        return (datetime.now() - self._last_update) < timedelta(hours=24)

    def _convert_temperature(self, raw_value: Any, telegram_data: Dict[str, Any] = None) -> float:
        """Convert raw temperature value to Celsius according to ELDAT specification.
        
        ELDAT specification:
        - 2 bytes unsigned integer, big-endian
        - Range: 0 to 65535
        - Formula: T = n / 20 (result in Kelvin)
        - Convert to Celsius: T_celsius = T_kelvin - 273.15
        """
        try:
            if isinstance(raw_value, (int, float)):
                # Check if this is already a converted temperature value
                # If the value is in a reasonable temperature range (-100 to +200°C),
                # it's likely already converted by the transceiver
                if -100.0 <= raw_value <= 200.0:
                    _LOGGER.debug("🌡️ Temperature value %.1f°C appears to be already converted, using as-is", raw_value)
                    
                    # Process additional telegram data if available
                    if telegram_data:
                        self._process_additional_data(telegram_data)
                    
                    return round(float(raw_value), 1)
                
                # Otherwise, treat as raw ELDAT value for conversion
                n = int(raw_value)
                if n < 0 or n > 65535:
                    _LOGGER.warning("⚠️ Temperature value %d outside valid range (0-65535)", n)
                    # Clamp to valid range
                    n = max(0, min(65535, n))
                
                # Convert according to ELDAT specification
                # T = n / 20 (Kelvin)
                temp_kelvin = n / 20.0
                
                # Convert Kelvin to Celsius
                temp_celsius = temp_kelvin - 273.15
                
                # Process additional telegram data if available
                if telegram_data:
                    self._process_additional_data(telegram_data)
                
                _LOGGER.info("🌡️ Temperature converted from raw %d to %.1f°C (%.1f K)", n, temp_celsius, temp_kelvin)
                return round(temp_celsius, 1)
                
            elif isinstance(raw_value, str):
                # Parse string value
                temp_val = float(raw_value)
                return self._convert_temperature(temp_val, telegram_data)
            else:
                _LOGGER.warning("⚠️ Invalid temperature value type: %s (%s)", type(raw_value), raw_value)
                return 0.0
        except (ValueError, TypeError) as e:
            _LOGGER.error("❌ Temperature conversion error for value %s: %s", raw_value, e)
            return 0.0

    def _process_additional_data(self, telegram_data: Dict[str, Any]) -> None:
        """Process additional ELDAT telegram data (reference value, telegram interval)."""
        try:
            # Extract raw telegram bytes if available
            raw_data = telegram_data.get("raw_data") or telegram_data.get("payload")
            if not raw_data:
                return
            
            # Convert to bytes if string
            if isinstance(raw_data, str):
                # Assume hex string
                try:
                    raw_bytes = bytes.fromhex(raw_data.replace(" ", ""))
                except ValueError:
                    return
            elif isinstance(raw_data, (list, tuple)):
                raw_bytes = bytes(raw_data)
            else:
                return
            
            if len(raw_bytes) < 8:
                return  # Need at least 8 bytes for offsets 5-7
            
            # Offset 2: Check bit 0 for reference value validity
            if len(raw_bytes) > 2:
                offset2 = raw_bytes[2]
                reference_valid = bool(offset2 & 0x01)  # Bit 0
                
                if reference_valid and len(raw_bytes) >= 7:
                    # Offset 5-6: Reference value (2 bytes, big-endian)
                    ref_raw = (raw_bytes[5] << 8) | raw_bytes[6]
                    ref_temp_kelvin = ref_raw / 20.0
                    ref_temp_celsius = ref_temp_kelvin - 273.15
                    
                    _LOGGER.info("🎯 Temperature sensor %s reference value: %.1f°C (raw: %d)",
                               self.entity_id, ref_temp_celsius, ref_raw)
                    
                    # Store as attribute for UI display
                    self._attr_extra_state_attributes = getattr(self, '_attr_extra_state_attributes', {}) or {}
                    self._attr_extra_state_attributes['reference_value'] = round(ref_temp_celsius, 1)
                    self._attr_extra_state_attributes['reference_adjustable'] = True
                else:
                    # Reference value not available
                    if hasattr(self, '_attr_extra_state_attributes') and self._attr_extra_state_attributes:
                        self._attr_extra_state_attributes.pop('reference_value', None)
                        self._attr_extra_state_attributes['reference_adjustable'] = False
            
            # Offset 7: Maximum telegram interval
            if len(raw_bytes) > 7:
                offset7 = raw_bytes[7]
                exponent = (offset7 >> 4) & 0x0F  # Bits 7-4
                mantissa = offset7 & 0x0F  # Bits 3-0
                
                # t = c * m * 2^x where c = 15s
                max_interval_seconds = 15 * mantissa * (2 ** exponent)
                
                _LOGGER.debug("⏱️ Temperature sensor %s max telegram interval: %d seconds (exp=%d, mant=%d)",
                            self.entity_id, max_interval_seconds, exponent, mantissa)
                
                # Store as attribute
                self._attr_extra_state_attributes = getattr(self, '_attr_extra_state_attributes', {}) or {}
                self._attr_extra_state_attributes['max_telegram_interval'] = max_interval_seconds
                
                # Convert to human readable format
                if max_interval_seconds >= 3600:
                    interval_str = f"{max_interval_seconds // 3600}h {(max_interval_seconds % 3600) // 60}m"
                elif max_interval_seconds >= 60:
                    interval_str = f"{max_interval_seconds // 60}m {max_interval_seconds % 60}s"
                else:
                    interval_str = f"{max_interval_seconds}s"
                
                self._attr_extra_state_attributes['max_telegram_interval_formatted'] = interval_str
                
        except Exception as e:
            _LOGGER.error("❌ Error processing additional telegram data: %s", e)

    def update_value(self, value: float) -> None:
        """Update sensor value synchronously."""
        # Convert raw value using ELDAT specification
        converted_value = self._convert_temperature(value)
        self._last_reading = converted_value
        self._last_update = datetime.now()
        self.schedule_update_ha_state()


class EldatEWReceiverHumiditySensor(EldatEntity, SensorEntity):
    """Humidity sensor created from entity specification with EW-Receiver support."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        """Initialize configured humidity sensor with EW-Receiver serial storage."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._entity_spec = entity_spec
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_humidity")
        self._attr_name = entity_spec.get("name", "Humidity")
        self._attr_device_class = SensorDeviceClass.HUMIDITY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = entity_spec.get("unit", PERCENTAGE)
        self._attr_icon = entity_spec.get("icon", "mdi:water-percent")
        
        # Store EW-Receiver serial for persistent identification
        self._ew_receiver_serial = entity_spec.get("ew_receiver_serial") or device_info.get("ew_receiver_serial")
        if self._ew_receiver_serial:
            _LOGGER.debug("🔗 Humidity sensor %s linked to EW-Receiver serial %s", 
                        self.entity_id, self._ew_receiver_serial[-8:])
        
        self._last_reading = entity_spec.get("current_value")
        self._last_update = datetime.now() if self._last_reading is not None else None
        
        # Track if this is a restored entity and event listener
        self._restored = self._last_reading is not None
        self._remove_listener = None

    async def async_added_to_hass(self) -> None:
        """Add event listeners when entity is added to hass."""
        await super().async_added_to_hass()
        
        # Listen for sensor_update events for this device
        self._remove_listener = self.hass.bus.async_listen(
            f"{DOMAIN}_sensor_update",
            self._handle_sensor_update
        )
        
        if self._restored:
            _LOGGER.info("🔄 Humidity sensor %s restored with value %.1f%%", 
                        self.entity_id, self._last_reading)
        else:
            _LOGGER.debug("Humidity sensor %s listening for sensor_update events", self.entity_id)
        
        # Setup live telegram listeners
        await self._setup_telegram_listeners()

    async def _setup_telegram_listeners(self) -> None:
        """Setup live telegram event listeners for real-time updates."""
        from .const import EVENT_DEVICE_UPDATED, EVENT_TELEGRAM_RECEIVED, DOMAIN
        
        async def handle_device_update(event):
            """Handle device update event."""
            try:
                data = event.data
                if data.get("serial_number") == self._serial_number:
                    humidity = data.get("humidity")
                    if humidity is not None:
                        converted_humidity = self._convert_humidity(humidity)
                        _LOGGER.info("📡 Humidity sensor %s device update: %.1f%% (raw: %s)",
                                    self.entity_id, converted_humidity, humidity)
                        self._last_reading = converted_humidity
                        self._last_update = datetime.now()
                        self.async_schedule_update_ha_state()
            except Exception as e:
                _LOGGER.error("❌ Humidity sensor %s device update error: %s", self.entity_id, e)

        # Also listen for sensor_update events (critical for new sensor updates!)
        async def handle_sensor_update(event):
            """Handle sensor_update event for new telegram data."""
            try:
                event_data = event.data
                if (event_data.get("serial_number") == self._serial_number and 
                    event_data.get("measurement_type") == "humidity"):
                    humidity = event_data.get("value")
                    if humidity is not None:
                        converted_humidity = self._convert_humidity(humidity)
                        _LOGGER.info("📡 Humidity sensor %s sensor_update: %.1f%% (raw: %s, device: %s)",
                                    self.entity_id, converted_humidity, humidity,
                                    self._serial_number[-6:])
                        self._last_reading = converted_humidity
                        self._last_update = datetime.now()
                        self.async_schedule_update_ha_state()
            except Exception as e:
                _LOGGER.error("❌ Humidity sensor %s sensor_update error: %s", self.entity_id, e)
        
        # Store listener removal functions
        self._device_update_listener = self.coordinator.hass.bus.async_listen(EVENT_DEVICE_UPDATED, handle_device_update)
        self._sensor_update_listener = self.coordinator.hass.bus.async_listen(f"{DOMAIN}_sensor_update", handle_sensor_update)
        
        _LOGGER.debug("🎯 Configured humidity sensor %s event listeners registered for serial %s", 
                     self._attr_unique_id, self._serial_number[-8:])

    async def async_will_remove_from_hass(self) -> None:
        """Remove event listeners when entity is removed."""
        if self._remove_listener:
            self._remove_listener()
        await super().async_will_remove_from_hass()

    async def _handle_sensor_update(self, event) -> None:
        """Handle sensor update event."""
        try:
            event_data = event.data
            event_serial = event_data.get("serial_number")
            
            # Check if this event is for our device
            if event_serial != self._serial_number:
                return
            
            # Get measurement data directly from event
            measurement_type = event_data.get("measurement_type")
            value = event_data.get("value")
            
            # Also check for direct humidity value in combined events
            humidity = event_data.get("humidity")
            
            _LOGGER.debug("Humidity sensor %s received event: type=%s, value=%s, hum=%s", 
                         self.entity_id, measurement_type, value, humidity)
            
            # Handle direct humidity measurement
            humidity_value = None
            if measurement_type == "humidity" and value is not None:
                humidity_value = value
            elif humidity is not None:
                humidity_value = humidity
            
            if humidity_value is not None:
                converted_humidity = self._convert_humidity(humidity_value)
                _LOGGER.info("📡 Humidity sensor %s sensor_update event: %.1f%% (raw: %s)",
                           self.entity_id, converted_humidity, humidity_value)
                self._last_reading = converted_humidity
                self._last_update = datetime.now()
                self.async_schedule_update_ha_state()
        except Exception as e:
            _LOGGER.error("Error handling sensor_update in humidity sensor %s: %s", self.entity_id, e)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes including EW-Receiver serial."""
        attributes = super().extra_state_attributes or {}
        if self._ew_receiver_serial:
            attributes["ew_receiver_serial"] = self._ew_receiver_serial[-8:]  # Show last 8 digits
        return attributes
        
    def _setup_telegram_listeners(self):
        """Set up telegram listeners for live updates."""
        from .const import EVENT_DEVICE_UPDATED, EVENT_TELEGRAM_RECEIVED
        
        def handle_device_update(event):
            if event.data.get("serial_number") == self._serial_number:
                telegram_data = event.data.get("telegram_data", {})
                raw_humidity = telegram_data.get("humidity")
                if raw_humidity is not None:
                    # Convert raw humidity value to percentage
                    converted_humidity = self._convert_humidity(raw_humidity, telegram_data)
                    # Use synchronous update to avoid RuntimeWarning
                    self._last_reading = converted_humidity
                    self._last_update = datetime.now()
                    # Schedule state update using asyncio
                    import asyncio
                    asyncio.create_task(self.async_write_ha_state())
                    _LOGGER.debug("💧 Humidity sensor %s telegram handler DEVICE_UPDATED: %.1f%% (raw: %s)", 
                                self._serial_number[-6:], converted_humidity, raw_humidity)
        
        def handle_telegram(event):
            if event.data.get("serial_number") == self._serial_number:
                telegram_data = event.data.get("telegram_data", {})
                raw_humidity = telegram_data.get("humidity")
                if raw_humidity is not None:
                    converted_humidity = self._convert_humidity(raw_humidity, telegram_data)
                    # Use synchronous update to avoid RuntimeWarning
                    self._last_reading = converted_humidity
                    self._last_update = datetime.now()
                    # Schedule state update using asyncio
                    import asyncio
                    asyncio.create_task(self.async_write_ha_state())
                    _LOGGER.debug("💧 Humidity sensor %s telegram handler TELEGRAM_RECEIVED: %.1f%% (raw: %s)", 
                                self._serial_number[-6:], converted_humidity, raw_humidity)
        
        self.coordinator.hass.bus.async_listen(EVENT_DEVICE_UPDATED, handle_device_update)
        self.coordinator.hass.bus.async_listen(EVENT_TELEGRAM_RECEIVED, handle_telegram)
        
    def _convert_humidity(self, raw_value: Any, telegram_data: Dict[str, Any] = None) -> float:
        """Convert raw humidity value to percentage according to ELDAT specification.
        
        ELDAT specification:
        - 2 bytes unsigned integer, big-endian  
        - Range: 0 to 4095
        - Formula: φ = n * (100/4095) (result in %)
        """
        try:
            if isinstance(raw_value, (int, float)):
                # Ensure value is in valid range
                n = int(raw_value)
                if n < 0 or n > 4095:
                    _LOGGER.warning("⚠️ Humidity value %d outside valid range (0-4095)", n)
                    # Clamp to valid range
                    n = max(0, min(4095, n))
                
                # Convert according to ELDAT specification
                # φ = n * (100/4095) %
                humidity_percent = n * (100.0 / 4095.0)
                
                # Ensure result is within 0-100%
                humidity_percent = max(0.0, min(100.0, humidity_percent))
                
                # Process additional telegram data if available
                if telegram_data:
                    self._process_additional_data(telegram_data)
                
                return round(humidity_percent, 1)
                
            elif isinstance(raw_value, str):
                # Parse string value
                humidity_val = int(float(raw_value))
                return self._convert_humidity(humidity_val, telegram_data)
            else:
                _LOGGER.warning("⚠️ Invalid humidity value type: %s (%s)", type(raw_value), raw_value)
                return 0.0
        except (ValueError, TypeError) as e:
            _LOGGER.error("❌ Humidity conversion error for value %s: %s", raw_value, e)
            return 0.0

    def _process_additional_data(self, telegram_data: Dict[str, Any]) -> None:
        """Process additional ELDAT telegram data (reference value, telegram interval)."""
        try:
            # Extract raw telegram bytes if available
            raw_data = telegram_data.get("raw_data") or telegram_data.get("payload")
            if not raw_data:
                return
            
            # Convert to bytes if string
            if isinstance(raw_data, str):
                # Assume hex string
                try:
                    raw_bytes = bytes.fromhex(raw_data.replace(" ", ""))
                except ValueError:
                    return
            elif isinstance(raw_data, (list, tuple)):
                raw_bytes = bytes(raw_data)
            else:
                return
            
            if len(raw_bytes) < 8:
                return  # Need at least 8 bytes for offsets 5-7
            
            # Offset 2: Check bit 0 for reference value validity
            if len(raw_bytes) > 2:
                offset2 = raw_bytes[2]
                reference_valid = bool(offset2 & 0x01)  # Bit 0
                
                if reference_valid and len(raw_bytes) >= 7:
                    # Offset 5-6: Reference value (2 bytes, big-endian)
                    ref_raw = (raw_bytes[5] << 8) | raw_bytes[6]
                    ref_humidity_percent = ref_raw * (100.0 / 4095.0)
                    ref_humidity_percent = max(0.0, min(100.0, ref_humidity_percent))
                    
                    _LOGGER.info("🎯 Humidity sensor %s reference value: %.1f%% (raw: %d)",
                               self.entity_id, ref_humidity_percent, ref_raw)
                    
                    # Store as attribute for UI display
                    self._attr_extra_state_attributes = getattr(self, '_attr_extra_state_attributes', {}) or {}
                    self._attr_extra_state_attributes['reference_value'] = round(ref_humidity_percent, 1)
                    self._attr_extra_state_attributes['reference_adjustable'] = True
                else:
                    # Reference value not available
                    if hasattr(self, '_attr_extra_state_attributes') and self._attr_extra_state_attributes:
                        self._attr_extra_state_attributes.pop('reference_value', None)
                        self._attr_extra_state_attributes['reference_adjustable'] = False
            
            # Offset 7: Maximum telegram interval
            if len(raw_bytes) > 7:
                offset7 = raw_bytes[7]
                exponent = (offset7 >> 4) & 0x0F  # Bits 7-4
                mantissa = offset7 & 0x0F  # Bits 3-0
                
                # t = c * m * 2^x where c = 15s
                max_interval_seconds = 15 * mantissa * (2 ** exponent)
                
                _LOGGER.debug("⏱️ Humidity sensor %s max telegram interval: %d seconds (exp=%d, mant=%d)",
                            self.entity_id, max_interval_seconds, exponent, mantissa)
                
                # Store as attribute
                self._attr_extra_state_attributes = getattr(self, '_attr_extra_state_attributes', {}) or {}
                self._attr_extra_state_attributes['max_telegram_interval'] = max_interval_seconds
                
                # Convert to human readable format
                if max_interval_seconds >= 3600:
                    interval_str = f"{max_interval_seconds // 3600}h {(max_interval_seconds % 3600) // 60}m"
                elif max_interval_seconds >= 60:
                    interval_str = f"{max_interval_seconds // 60}m {max_interval_seconds % 60}s"
                else:
                    interval_str = f"{max_interval_seconds}s"
                
                self._attr_extra_state_attributes['max_telegram_interval_formatted'] = interval_str
                
        except Exception as e:
            _LOGGER.error("❌ Error processing additional telegram data: %s", e)

    async def async_added_to_hass(self) -> None:
        """Add event listeners when entity is added to hass."""
        await super().async_added_to_hass()
        
        # Listen for sensor_update events for this device (contains processed values)
        self._remove_listener = self.hass.bus.async_listen(
            f"{DOMAIN}_sensor_update",
            self._handle_sensor_update
        )
        
        if self._restored:
            _LOGGER.info("🔄 Humidity sensor %s restored with value %.1f%%", 
                        self.entity_id, self._last_reading)
        else:
            _LOGGER.debug("Humidity sensor %s listening for sensor_update events", self.entity_id)

    async def async_will_remove_from_hass(self) -> None:
        """Remove event listeners when entity is removed."""
        await super().async_will_remove_from_hass()
        
        # Remove sensor_update listener
        if hasattr(self, '_remove_listener') and self._remove_listener:
            self._remove_listener()
            
        # Remove telegram listeners
        if hasattr(self, '_device_update_listener') and self._device_update_listener:
            self._device_update_listener()
        if hasattr(self, '_sensor_update_listener') and self._sensor_update_listener:
            self._sensor_update_listener()

    async def _handle_sensor_update(self, event) -> None:
        """Handle sensor update event."""
        try:
            event_data = event.data
            event_serial = event_data.get("serial_number")
            
            # Check if this event is for our device
            if event_serial != self._serial_number:
                return
            
            # Get measurement data directly from event
            measurement_type = event_data.get("measurement_type")
            value = event_data.get("value")
            battery_level = event_data.get("battery_level")
            
            # Also check for direct humidity value in combined events
            humidity = event_data.get("humidity")
            
            _LOGGER.debug("Humidity sensor %s received event: type=%s, value=%s, hum=%s", 
                         self.entity_id, measurement_type, value, humidity)
            
            # Handle direct humidity measurement
            humidity_value = None
            if measurement_type == "humidity" and value is not None:
                humidity_value = value
            elif humidity is not None:
                humidity_value = humidity
                
            if humidity_value is not None:
                _LOGGER.info("Humidity sensor %s ASYNC HANDLER updating to %.1f%% (processed value from coordinator)", self.entity_id, humidity_value)
                # The value from coordinator is already processed, set it directly
                self._last_reading = humidity_value
                self._last_update = datetime.now()
                self.async_write_ha_state()
                
                # Update battery info if available
                if battery_level is not None:
                    battery_status = event_data.get("battery_status", "unknown")
                    self._update_battery_info(battery_level, battery_status)
                    
        except Exception as e:
            _LOGGER.error("Error handling sensor update in humidity sensor %s: %s", self.entity_id, e)

    def _update_battery_info(self, level: int, status: str) -> None:
        """Update battery information (for future battery entity integration)."""
        # This could be used to update a related battery entity
        _LOGGER.debug("Humidity sensor %s battery: %s (level %d/7)", self.entity_id, status, level)

    @property
    def native_value(self) -> float | None:
        """Return the current humidity."""
        _LOGGER.debug("💧 Humidity sensor %s native_value called, returning: %s", self.entity_id, self._last_reading)
        return self._last_reading

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # If we have a restored value, consider available for up to 24 hours
        # This allows sensors to remain available after restart even if not yet updated
        if self._last_update is None:
            # If we have a current value (from restore), consider available
            return self._last_reading is not None
        
        # Consider available if updated within last 24 hours (sensors don't update frequently)
        return (datetime.now() - self._last_update) < timedelta(hours=24)

    def update_value(self, value: float) -> None:
        """Update sensor value synchronously."""
        self._last_reading = value
        self._last_update = datetime.now()
        _LOGGER.debug("💧 Humidity sensor %s _last_reading set to: %.1f%%", self.entity_id, self._last_reading)
        # Schedule state update using asyncio
        import asyncio
        asyncio.create_task(self.async_write_ha_state())


class EldatEWReceiverBatterySensor(EldatEntity, SensorEntity):
    """Battery sensor created from entity specification with EW-Receiver support."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        """Initialize configured battery sensor with EW-Receiver serial storage."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._entity_spec = entity_spec
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_battery")
        self._attr_name = entity_spec.get("name", "Battery")
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = entity_spec.get("unit", PERCENTAGE)
        self._attr_icon = entity_spec.get("icon", "mdi:battery")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        
        # Store EW-Receiver serial for persistent identification
        self._ew_receiver_serial = entity_spec.get("ew_receiver_serial") or device_info.get("ew_receiver_serial")
        if self._ew_receiver_serial:
            _LOGGER.debug("🔗 Battery sensor %s linked to EW-Receiver serial %s", 
                        self.entity_id, self._ew_receiver_serial[-8:])
        
        self._battery_level = entity_spec.get("current_value")
        self._last_update = datetime.now() if self._battery_level is not None else None
        
        # Track if this is a restored entity and event listener
        self._restored = self._battery_level is not None
        self._remove_listener = None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes including EW-Receiver serial."""
        attributes = super().extra_state_attributes or {}
        if self._ew_receiver_serial:
            attributes["ew_receiver_serial"] = self._ew_receiver_serial[-8:]  # Show last 8 digits
        return attributes

    async def async_added_to_hass(self) -> None:
        """Add event listeners when entity is added to hass."""
        await super().async_added_to_hass()
        
        # Listen for sensor_update events (EW-Sensor) and button events (EW-Transmitter)
        self._remove_listener = self.hass.bus.async_listen(
            f"{DOMAIN}_sensor_update",
            self._handle_sensor_update
        )
        
        # Also listen for button press events (EW-Transmitter battery updates)
        self._button_press_listener = self.hass.bus.async_listen(
            f"{DOMAIN}_button_press",
            self._handle_button_event
        )
        
        self._button_release_listener = self.hass.bus.async_listen(
            f"{DOMAIN}_button_release",
            self._handle_button_event
        )
        
        if self._battery_level is not None:
            _LOGGER.info("🔄 Battery sensor %s restored with value %d%%", 
                        self.entity_id, self._battery_level)

    async def async_will_remove_from_hass(self) -> None:
        """Remove event listeners when entity is removed."""
        if self._remove_listener:
            self._remove_listener()
        if hasattr(self, '_button_press_listener') and self._button_press_listener:
            self._button_press_listener()
        if hasattr(self, '_button_release_listener') and self._button_release_listener:
            self._button_release_listener()
        await super().async_will_remove_from_hass()

    async def _handle_sensor_update(self, event) -> None:
        """Handle sensor update event (for EW-Sensor)."""
        try:
            event_data = event.data
            event_serial = event_data.get("serial_number")
            
            if event_serial != self._serial_number:
                return
            
            battery_level = event_data.get("battery_level")
            if battery_level is not None:
                _LOGGER.debug("Battery sensor %s updating to %d%% (from sensor_update)", 
                            self.entity_id, battery_level)
                self.update_battery_level(battery_level)
                
        except Exception as e:
            _LOGGER.error("Error handling sensor update in battery sensor %s: %s", self.entity_id, e)

    async def _handle_button_event(self, event) -> None:
        """Handle button press/release event (for EW-Transmitter)."""
        try:
            event_data = event.data
            event_serial = event_data.get("device_id")
            
            _LOGGER.debug("🔋 Battery sensor %s received button event from %s", 
                        self.entity_id, event_serial[-6:] if event_serial else "unknown")
            
            if event_serial != self._serial_number:
                return
            
            battery_level = event_data.get("battery_level")
            if battery_level is not None:
                _LOGGER.info("🔋 Battery sensor %s updating to %d%% (from button event)", 
                            self.entity_id, battery_level)
                self.update_battery_level(battery_level)
            else:
                _LOGGER.debug("🔋 Battery sensor %s: No battery_level in button event", self.entity_id)
                
        except Exception as e:
            _LOGGER.error("Error handling button event in battery sensor %s: %s", self.entity_id, e)

    @property
    def native_value(self) -> int | None:
        """Return the current battery level."""
        return self._battery_level

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if self._last_update is None:
            return False
        
        return (datetime.now() - self._last_update) < timedelta(days=1)

    @property
    def icon(self) -> str:
        """Return battery icon based on level."""
        if self._battery_level is None:
            return "mdi:battery-unknown"
        
        if self._battery_level <= 10:
            return "mdi:battery-outline"
        elif self._battery_level <= 20:
            return "mdi:battery-20"
        elif self._battery_level <= 30:
            return "mdi:battery-30"
        elif self._battery_level <= 40:
            return "mdi:battery-40"
        elif self._battery_level <= 50:
            return "mdi:battery-50"
        elif self._battery_level <= 60:
            return "mdi:battery-60"
        elif self._battery_level <= 70:
            return "mdi:battery-70"
        elif self._battery_level <= 80:
            return "mdi:battery-80"
        elif self._battery_level <= 90:
            return "mdi:battery-90"
        else:
            return "mdi:battery"

    def update_battery_level(self, level: int, telegram_data: Dict[str, Any] = None) -> None:
        """Update battery level from telegram."""
        old_level = self._battery_level
        if 0 <= level <= 100:
            self._battery_level = level
            self._last_update = datetime.now()
            
            # Process additional telegram data if available
            if telegram_data:
                self._process_additional_data(telegram_data)
            
            self.schedule_update_ha_state()
            _LOGGER.info("🔋 Battery sensor %s updated: %s%% -> %d%% (entity: %s)", 
                        self.entity_id, old_level if old_level is not None else "None", 
                        level, self.name)
        else:
            _LOGGER.warning("🔋 Invalid battery level %s for sensor %s", level, self.entity_id)

    def _process_additional_data(self, telegram_data: Dict[str, Any]) -> None:
        """Process additional ELDAT telegram data (telegram interval)."""
        try:
            # Extract raw telegram bytes if available
            raw_data = telegram_data.get("raw_data") or telegram_data.get("payload")
            if not raw_data:
                return
            
            # Convert to bytes if string
            if isinstance(raw_data, str):
                # Assume hex string
                try:
                    raw_bytes = bytes.fromhex(raw_data.replace(" ", ""))
                except ValueError:
                    return
            elif isinstance(raw_data, (list, tuple)):
                raw_bytes = bytes(raw_data)
            else:
                return
            
            if len(raw_bytes) < 8:
                return  # Need at least 8 bytes for offset 7
            
            # Offset 7: Maximum telegram interval
            if len(raw_bytes) > 7:
                offset7 = raw_bytes[7]
                exponent = (offset7 >> 4) & 0x0F  # Bits 7-4
                mantissa = offset7 & 0x0F  # Bits 3-0
                
                # t = c * m * 2^x where c = 15s
                max_interval_seconds = 15 * mantissa * (2 ** exponent)
                
                _LOGGER.debug("⏱️ Battery sensor %s max telegram interval: %d seconds (exp=%d, mant=%d)",
                            self.entity_id, max_interval_seconds, exponent, mantissa)
                
                # Store as attribute
                self._attr_extra_state_attributes = getattr(self, '_attr_extra_state_attributes', {}) or {}
                self._attr_extra_state_attributes['max_telegram_interval'] = max_interval_seconds
                
                # Convert to human readable format
                if max_interval_seconds >= 3600:
                    interval_str = f"{max_interval_seconds // 3600}h {(max_interval_seconds % 3600) // 60}m"
                elif max_interval_seconds >= 60:
                    interval_str = f"{max_interval_seconds // 60}m {max_interval_seconds % 60}s"
                else:
                    interval_str = f"{max_interval_seconds}s"
                
                self._attr_extra_state_attributes['max_telegram_interval_formatted'] = interval_str
                
        except Exception as e:
            _LOGGER.error("❌ Error processing additional telegram data: %s", e)


class EldatEWReceiverRainSensor(EldatEntity, SensorEntity):
    """Rain sensor created from entity specification."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        """Initialize configured rain sensor."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._entity_spec = entity_spec
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_rain")
        self._attr_name = entity_spec.get("name", "Rain")
        self._attr_device_class = SensorDeviceClass.PRECIPITATION
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = entity_spec.get("unit", "mm")
        self._attr_icon = entity_spec.get("icon", "mdi:weather-rainy")
        
        self._last_reading = entity_spec.get("current_value")
        self._last_update = datetime.now() if self._last_reading is not None else None

    @property
    def native_value(self) -> float | None:
        """Return the current rain measurement."""
        return self._last_reading

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if self._last_update is None:
            return False
        
        return (datetime.now() - self._last_update) < timedelta(hours=6)

    def update_value(self, value: float) -> None:
        """Update sensor value."""
        self._last_reading = value
        self._last_update = datetime.now()
        self.schedule_update_ha_state()


class EldatEWReceiverWindSensor(EldatEntity, SensorEntity):
    """Wind sensor created from entity specification."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        """Initialize configured wind sensor."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._entity_spec = entity_spec
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_wind")
        self._attr_name = entity_spec.get("name", "Wind Speed")
        self._attr_device_class = SensorDeviceClass.WIND_SPEED
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = entity_spec.get("unit", "m/s")
        self._attr_icon = entity_spec.get("icon", "mdi:weather-windy")
        
        self._last_reading = entity_spec.get("current_value")
        self._last_update = datetime.now() if self._last_reading is not None else None

    @property
    def native_value(self) -> float | None:
        """Return the current wind speed."""
        return self._last_reading

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if self._last_update is None:
            return False
        
        return (datetime.now() - self._last_update) < timedelta(hours=2)

    def update_value(self, value: float) -> None:
        """Update sensor value."""
        self._last_reading = value
        self._last_update = datetime.now()
        self.schedule_update_ha_state()