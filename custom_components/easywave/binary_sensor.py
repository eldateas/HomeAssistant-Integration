"""Binary sensor entities for ELDAT integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict
from datetime import datetime, timedelta

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, EVENT_DEVICE_ADDED, BUTTON_LABELS
from .entity_registry import get_entity_registry
from .translations import (
    get_language,
    get_button_label,
    t_state,
    translate,
    DEFAULT_LANGUAGE,
    is_up_state,
    is_down_state,
    is_stop_state,
    is_on_state,
    is_off_state,
)

from .coordinator import EldatCoordinator
from .entity import EldatEntity
from .device_icons import get_entity_config_for_device

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ELDAT binary sensor entities."""
    _LOGGER.info("🔧 Starting Binary Sensor Platform Setup")
    
    coordinator: EldatCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    ha_entity_registry = er.async_get(hass)
    
    # Setup binary sensors for all devices
    _LOGGER.info("📊 Setting up binary sensors")
    all_devices = coordinator.get_all_devices()
    _LOGGER.info("📊 Coordinator has %d total devices", len(all_devices))
    
    # Count Easywave Transmitters that need binary sensors
    ew_transmitter_count = len([d for d in all_devices.values() if d.get("type") == "ew_transmitter"])
    _LOGGER.info("🎛️ Found %d Easywave Transmitter devices that need binary sensors", ew_transmitter_count)
    
    # Create binary sensors for all existing devices (including restored ones)
    _LOGGER.info("🔍 Setting up binary sensors for %d existing devices", len(coordinator.get_all_devices()))
    
    def _create_binary_sensors_for_device(serial_number: str, device_info: Dict[str, Any]) -> list:
        """Create binary sensor entities for a device based on entity_specs."""
        from .entity_specs import create_entity_specs_for_device
        
        binary_sensors = []
        device_type = device_info.get("type", "unknown")
        
        _LOGGER.info("🔍 Processing device %s: type=%s, button_count=%s", 
                    serial_number, device_type, device_info.get("button_count", "unknown"))
        
        # Easywave Transmitters: Use entity_specs to determine if binary sensors are needed
        if device_type == "ew_transmitter":
            # Get entity specs for this device - this respects operating_type, switch_mode, etc.
            entity_specs = create_entity_specs_for_device(serial_number, device_info)
            
            # Only create binary sensors if entity_specs says so
            binary_sensor_specs = entity_specs.get("binary_sensor", [])
            
            if not binary_sensor_specs:
                # No binary sensors needed for this device (e.g., switch_mode == "permanent")
                _LOGGER.info("📝 No binary sensors needed for Easywave Transmitter %s (switch_mode=%s, grouping_mode=%s)", 
                           serial_number, 
                           device_info.get("switch_mode", "unknown"),
                           device_info.get("grouping_mode", "unknown"))
                return []
            
            device_name = device_info.get("name", f"Easywave Transmitter {serial_number}")
            _LOGGER.info("🎛️ Creating %d binary sensors for Easywave Transmitter %s", len(binary_sensor_specs), device_name)
            
            # Create binary sensor for each spec
            for spec in binary_sensor_specs:
                sensor_type = spec.get("sensor_type")
                
                # Battery warning sensor
                if sensor_type == "battery_warning":
                    entity = EldatBatteryWarningSensor(
                        coordinator=coordinator,
                        serial_number=serial_number,
                        device_info=device_info,
                        entity_spec=spec,
                    )
                    binary_sensors.append(entity)
                    _LOGGER.info("✅ Created battery warning sensor: %s", entity.unique_id)
                    continue
                
                # Transmitter state sensor
                if sensor_type == "transmitter_state":
                    entity = EldatTransmitterStateBinarySensor(
                        coordinator=coordinator,
                        serial_number=serial_number,
                        device_info=device_info,
                        entity_spec=spec,
                    )
                    binary_sensors.append(entity)
                    _LOGGER.info("✅ Created transmitter state binary sensor: %s", entity.unique_id)
                    continue

                # Button sensors
                button_id = spec.get("button_index", 0)
                entity = EldatTransmitterButtonSensor(
                    coordinator=coordinator,
                    serial_number=serial_number,
                    device_info=device_info,
                    button_id=button_id,
                    entity_spec=spec,  # Pass the full entity spec for switch_mode etc.
                )
                # Preserve existing entity_id/unique_id if registered with legacy format
                old_unique_id = f"{serial_number}_btn{button_id}"
                existing_entity_id = None
                old_entity_id = ha_entity_registry.async_get_entity_id(
                    "binary_sensor", DOMAIN, old_unique_id
                )
                if old_entity_id:
                    entity._attr_unique_id = old_unique_id
                    entity._entity_spec["unique_id"] = old_unique_id
                    existing_entity_id = old_entity_id

                # Re-enable if previously disabled by integration
                if not existing_entity_id:
                    existing_entity_id = ha_entity_registry.async_get_entity_id(
                        "binary_sensor", DOMAIN, entity.unique_id
                    )
                if existing_entity_id:
                    entry = ha_entity_registry.async_get(existing_entity_id)
                    if entry and entry.disabled_by == "integration":
                        ha_entity_registry.async_update_entity(existing_entity_id, disabled_by=None)
                binary_sensors.append(entity)
                _LOGGER.info("✅ Created binary sensor: %s (switch_mode=%s)", entity.unique_id, spec.get("switch_mode", "impulse"))
            
            _LOGGER.info("✅ Successfully created %d binary sensors for device %s", len(binary_sensors), serial_number)
            return binary_sensors
        
        # EWneo-Sensors: Create battery binary sensor (niedrig/normal based on battery_level)
        if device_type in ["ewneo_sensor", "ew_sensor"]:
            device_name = device_info.get("name", f"Easywave neo Sensor {serial_number}")
            _LOGGER.info("🌡️ Creating battery binary sensor for EWneo-Sensor %s", device_name)
            
            # Create battery sensor (reads battery_level from coordinator.devices)
            entity = EWneoBatterySensor(
                coordinator=coordinator,
                serial_number=serial_number,
                device_info=device_info,
            )
            binary_sensors.append(entity)
            _LOGGER.info("✅ Created battery binary sensor for EWneo-Sensor: %s", entity.unique_id)
            
            return binary_sensors
        
        # No binary sensors needed for this device type
        _LOGGER.debug("Device %s (%s) does not need binary sensors", serial_number, device_type)
        return []
    
    binary_sensors = []
    
    # Create binary sensors for all existing devices (including restored ones)
    _LOGGER.info("🔍 Setting up binary sensors for %d existing devices", len(coordinator.get_all_devices()))
    
    # Debug: List all devices and their types
    for serial_number, device_info in coordinator.get_all_devices().items():
        device_type = device_info.get("type", "unknown")
        device_name = device_info.get("name", serial_number)
        _LOGGER.debug("Found device: %s (%s) - Type: %s", device_name, serial_number, device_type)
    
    for serial_number, device_info in coordinator.get_all_devices().items():
        device_type = device_info.get("type", "unknown")
        device_name = device_info.get("name", serial_number)
        
        # Only log for devices that might have binary sensors
        if device_type in ["ew_transmitter", "ewneo_sensor"]:
            device_name = device_info.get("name", serial_number)
            _LOGGER.info("🎛️ Checking %s %s for binary sensors", device_type, device_name)
        
        device_binary_sensors = _create_binary_sensors_for_device(serial_number, device_info)
        
        if device_binary_sensors:
            binary_sensors.extend(device_binary_sensors)
            _LOGGER.info("✅ Created %d binary sensor entities for device: %s (%s)", 
                        len(device_binary_sensors), device_name, device_type)

    if binary_sensors:
        _LOGGER.info("📝 Adding %d binary sensor entities to Home Assistant", len(binary_sensors))
        try:
            # Add entities without update_before_add to avoid state issues during setup
            async_add_entities(binary_sensors, update_before_add=False)
            
            # Note: Event listeners are registered in async_added_to_hass, not here
            # because at this point entities don't have a hass instance yet
            
            _LOGGER.info("✅ Successfully added %d binary sensor entities for %d Easywave Transmitter devices", 
                        len(binary_sensors), len([d for d in coordinator.get_all_devices().values() if d.get("type") == "ew_transmitter"]))
        except Exception as e:
            _LOGGER.error("❌ Failed to add binary sensor entities: %s", e)
    else:
        # Check if there are Easywave Transmitter devices that should have binary sensors
        # Note: Transmitters in "group" mode don't get binary sensors, they get a sensor entity instead
        ew_transmitters = [d for d in coordinator.get_all_devices().values() if d.get("type") == "ew_transmitter"]
        single_mode_transmitters = [d for d in ew_transmitters if d.get("grouping_mode", "single") == "single"]
        if single_mode_transmitters:
            _LOGGER.warning("⚠️ Found %d Easywave Transmitters in single mode but no binary sensors created", len(single_mode_transmitters))
            for serial, device in [(s, d) for s, d in coordinator.get_all_devices().items() 
                                   if d.get("type") == "ew_transmitter" and d.get("grouping_mode", "single") == "single"]:
                _LOGGER.warning("   Easywave Transmitter: %s (%s) - grouping=%s, switch_mode=%s", 
                              device.get("name", "Unknown"), serial, 
                              device.get("grouping_mode", "single"), device.get("switch_mode", "impulse"))
        elif ew_transmitters:
            # Transmitters in group mode - this is expected, they get sensor entities instead
            _LOGGER.info("📊 Found %d Easywave Transmitters in group mode - no binary sensors needed (using sensor entities)", len(ew_transmitters))
        else:
            _LOGGER.debug("No binary sensor entities created (no Easywave Transmitter devices found)")
    
    # Listen for new devices and create entities dynamically
    async def _handle_device_added(event):
        """Handle device added event."""
        entity_registry = get_entity_registry()
        try:
            serial_number = event.data.get("serial_number")
            device_info = event.data.get("device_info")
            device_type = device_info.get("type", "unknown") if device_info else "unknown"
            force_create = event.data.get("force_create", False)
            
            _LOGGER.info("🔍 Binary sensor handler received event for device %s (type: %s, force_create: %s)", 
                        serial_number if serial_number else "unknown", device_type, force_create)
            
            if not serial_number or not device_info:
                _LOGGER.error("Device added event missing serial_number or device_info")
                return
            
            # Only log for devices that need binary sensors
            if device_type in ["ew_transmitter", "ewneo_sensor", "ew_sensor"]:
                _LOGGER.info("Binary sensor handler received device added event: %s (%s)", 
                            serial_number, device_type)
                _LOGGER.info("🔍 About to call _create_binary_sensors_for_device...")
            else:
                _LOGGER.debug("Device %s (%s) does not need binary sensors, skipping", 
                            serial_number, device_type)
                return
            
            # Check if entities already exist for this device to avoid duplicates
            existing_entity_id = ha_entity_registry.async_get_entity_id(
                "binary_sensor", DOMAIN, f"{serial_number}_button_0"
            )
            if existing_entity_id and not force_create:
                _LOGGER.debug("Binary sensors already exist for device %s, skipping creation", serial_number)
                return
            
            new_binary_sensors = _create_binary_sensors_for_device(serial_number, device_info)
            _LOGGER.info("🔍 _create_binary_sensors_for_device returned %d sensors", len(new_binary_sensors) if new_binary_sensors else 0)
            
            if new_binary_sensors:
                # Filter out entities that already exist
                filtered_sensors = []
                for sensor in new_binary_sensors:
                    existing = ha_entity_registry.async_get_entity_id(
                        "binary_sensor", DOMAIN, sensor.unique_id
                    )
                    if not existing:
                        filtered_sensors.append(sensor)
                    else:
                        _LOGGER.debug("Entity %s already exists, skipping", sensor.unique_id)
                
                if not filtered_sensors:
                    _LOGGER.debug("All binary sensors for device %s already exist", serial_number)
                    return
                    
                # Add entities without update_before_add to avoid state issues
                async_add_entities(filtered_sensors, update_before_add=False)
                
                _LOGGER.info("✅ Added %d binary sensor entities for new device %s (%s)", 
                           len(filtered_sensors), serial_number, device_type)
                
                # Wichtig: Warte einen Moment und stelle dann sicher, dass Event Listeners registriert sind
                async def ensure_listeners_after_delay():
                    await asyncio.sleep(1.0)  # Warte bis Entitäten vollständig registriert sind
                    for sensor in filtered_sensors:
                        try:
                            if not getattr(sensor, '_listeners_registered', False) and sensor.hass:
                                await sensor._register_event_listeners_async()
                                sensor._listeners_registered = True
                                _LOGGER.info("🎯 Ensured delayed event listeners for %s", sensor.unique_id)
                        except Exception as e:
                            _LOGGER.error("Error ensuring delayed listeners for %s: %s", sensor.unique_id, e)
                
                hass.async_create_task(ensure_listeners_after_delay())
            # No need to warn about devices that don't need binary sensors
                    
        except Exception as e:
            _LOGGER.error("Error creating binary sensor entities for new device: %s", e)
    
    # Register primary event listener for device additions
    config_entry.async_on_unload(
        hass.bus.async_listen(EVENT_DEVICE_ADDED, _handle_device_added)
    )
    
    # Also listen for direct binary sensor creation event (fallback)
    async def _handle_direct_binary_sensor_creation(event):
        """Handle direct binary sensor creation event."""
        try:
            serial_number = event.data.get("serial_number")
            device_info = event.data.get("device_info")
            device_type = device_info.get("type", "unknown") if device_info else "unknown"
            force_create = event.data.get("force_create", False)
            
            if force_create:
                _LOGGER.info("Force creating binary sensors for %s", serial_number if serial_number else "unknown")
                
                new_binary_sensors = _create_binary_sensors_for_device(serial_number, device_info)
                
                if new_binary_sensors:
                    # Filter out entities that already exist
                    filtered_sensors = []
                    for sensor in new_binary_sensors:
                        existing = ha_entity_registry.async_get_entity_id(
                            "binary_sensor", DOMAIN, sensor.unique_id
                        )
                        if not existing:
                            filtered_sensors.append(sensor)
                        else:
                            _LOGGER.debug("Entity %s already exists, skipping", sensor.unique_id)
                    
                    if not filtered_sensors:
                        _LOGGER.debug("All binary sensors for device %s already exist", serial_number)
                        return
                    
                    # Add entities without update_before_add to avoid state issues
                    async_add_entities(filtered_sensors, update_before_add=False)
                    
                    # Give Home Assistant time to register the entities properly  
                    await asyncio.sleep(0.3)
                    
                    # Ensure event listeners are active for all new sensors
                    for sensor in filtered_sensors:
                        try:
                            # Force listener registration if not done yet
                            if not getattr(sensor, '_listeners_registered', False):
                                if sensor.hass:
                                    sensor._register_event_listeners() 
                                    sensor._listeners_registered = True
                                    _LOGGER.info("🎯 Force-registered event listeners for %s", sensor.unique_id)
                                else:
                                    # Schedule retry if entity not ready
                                    async def retry_later():
                                        await asyncio.sleep(0.5)
                                        if sensor.hass and not getattr(sensor, '_listeners_registered', False):
                                            sensor._register_event_listeners()
                                            sensor._listeners_registered = True
                                            _LOGGER.info("🎯 Retry: Registered event listeners for %s", sensor.unique_id)
                                    hass.async_create_task(retry_later())
                        except Exception as e:
                            _LOGGER.error("Error ensuring listeners for %s: %s", sensor.unique_id, e)
                    
                    _LOGGER.info("✅ Force-created %d binary sensor entities for %s", 
                               len(filtered_sensors), serial_number if serial_number else "unknown")
                    
        except Exception as e:
            _LOGGER.error("Error in force binary sensor creation: %s", e)
    
    # Register backup event listener
    config_entry.async_on_unload(
        hass.bus.async_listen("eldat_binary_sensor_create", _handle_direct_binary_sensor_creation)
    )
    
    _LOGGER.info("✅ Binary sensor platform setup complete with event listeners")
    
    config_entry.async_on_unload(
        hass.bus.async_listen("eldat_create_binary_sensors", _handle_direct_binary_sensor_creation)
    )
    
    config_entry.async_on_unload(
        hass.bus.async_listen(f"{EVENT_DEVICE_ADDED}_binary_sensor", _handle_device_added)
    )
    
    _LOGGER.info("✅ Binary sensor platform setup complete with event listeners")


class EldatTransmitterButtonSensor(EldatEntity, RestoreEntity, BinarySensorEntity):
    """Binary sensor for Easywave Transmitter button state with press/hold detection.
    
    Supports two switch modes:
    - "impulse": Status is ON while button is pressed, OFF when released
    - "permanent": Status toggles on each button press (persistent state)
    """
    
    # Class variable to track which buttons are currently pressed per device
    _active_buttons = {}

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        button_id: int,
        entity_spec: Dict[str, Any] = None,
    ) -> None:
        """Initialize transmitter button binary sensor."""
        _LOGGER.warning("🔍 Initializing binary sensor for device %s, button %d", serial_number, button_id)
        
        self._button_id = button_id
        self._entity_spec = entity_spec or {}
        spec_label = self._entity_spec.get("button_label") or self._entity_spec.get("button")
        # Fallback: Button-Buchstabe aus button_id (0=A, 1=B, 2=C, 3=D)
        button_letters = {0: "A", 1: "B", 2: "C", 3: "D"}
        self._button_name = spec_label or button_letters.get(button_id, str(button_id))
        self._switch_mode = self._entity_spec.get("switch_mode", "impulse")  # "impulse" or "permanent"
        self._last_press_time = None
        self._is_on = False
        self._is_holding = False
        self._hold_timer = None
        self._release_timer = None
        self._auto_release_timer = None  # Fallback timer if no release event comes
        self._battery_low = False  # Track battery low status
        
        # Hold detection settings
        self._hold_threshold_ms = 800  # 800ms threshold for hold detection
        self._auto_reset_ms = 300     # Auto reset after 300ms for press events
        self._max_press_duration = 30000  # Maximum press duration before auto-release (30 seconds)
        
        # Minimum display duration for 1-button impulse mode (1 second)
        self._min_display_duration_ms = 1000  # Minimum 1 second display time
        self._delayed_release_timer = None  # Timer for delayed release
        
        # Initialize parent classes
        super().__init__(coordinator, serial_number, device_info)
        
        self._attr_unique_id = self._entity_spec.get("unique_id", f"{serial_number}_btn{button_id}")
        
        # Store translation_key for HA automatic state translation
        # This allows HA to translate entity names via translations/*.json
        self._translation_key = self._entity_spec.get("translation_key")
        self._attr_translation_key = self._translation_key  # Required for HA translation
        self._static_name = self._entity_spec.get("name")  # Fallback static name
        
        # Ensure entity is enabled by default
        self._attr_entity_registry_enabled_default = True
        
        # Get device-specific configuration
        device_type = device_info.get("type", "unknown")
        device_config = get_entity_config_for_device(
            device_type=device_type,
            entity_type="binary_sensor"
        )
        
        # Use icon and device_class from entity_spec if available
        self._attr_icon = self._entity_spec.get("icon") or device_config.get("icon", "mdi:radiobox-blank")
        device_class_str = self._entity_spec.get("device_class")
        if device_class_str:
            try:
                self._attr_device_class = BinarySensorDeviceClass(device_class_str)
            except ValueError:
                self._attr_device_class = None
        else:
            self._attr_device_class = None  # No specific device class for remote buttons
        
        _LOGGER.warning("🔍 Binary sensor initialized: %s (unique_id: %s, switch_mode: %s)", 
                       self._translation_key or self._static_name or f"Button {button_id}", self._attr_unique_id, self._switch_mode)

    async def async_added_to_hass(self) -> None:
        """Called when entity is added to Home Assistant."""
        await super().async_added_to_hass()
        _LOGGER.info("🔍 Binary sensor added to hass: %s", self.name)

        # Restore persistent state after restart (permanent mode only)
        if self._switch_mode == "permanent":
            persistent = self.coordinator.get_device_state(self._serial_number) or {}
            persistent_key = f"button_{self._button_id}"
            if persistent_key in persistent:
                self._is_on = bool(persistent[persistent_key])
                self.async_write_ha_state()
            elif (last_state := await self.async_get_last_state()) is not None:
                self._is_on = last_state.state == "on"
                self.async_write_ha_state()

        # No automatic disable here; keep original behavior
        
        # Listen for devices loaded event to update availability
        @callback
        def devices_loaded_callback(event):
            """Update availability when devices are loaded."""
            _LOGGER.info("📡 Devices loaded event received for %s - updating availability", self.name)
            self.async_write_ha_state()
            
        self._devices_loaded_listener = self.hass.bus.async_listen(
            "eldat_devices_loaded",
            devices_loaded_callback
        )
        
        # Längerer Delay um sicherzustellen, dass die Entity vollständig registriert ist
        await asyncio.sleep(0.5)
        
        # Register event listeners mit verbesserter Fehlerbehandlung
        try:
            if not getattr(self, '_listeners_registered', False):
                await self._register_event_listeners_async()
                self._listeners_registered = True
                _LOGGER.info("🎯 Event listeners successfully registered for: %s", self.name)
            else:
                _LOGGER.debug("Event listeners already registered for: %s", self.name)
            
            # Schedule availability check after coordinator is fully loaded
            async def check_availability_after_startup():
                """Check availability after coordinator has loaded devices."""
                await asyncio.sleep(2.0)  # Wait for coordinator to load
                if hasattr(self.coordinator, 'devices') and self.coordinator.devices:
                    _LOGGER.info("📡 Updating availability for %s after startup", self.name)
                    self.async_write_ha_state()  # Trigger availability update
                    
            self.hass.async_create_task(check_availability_after_startup())
            
        except Exception as e:
            _LOGGER.error("Error in async_added_to_hass for %s: %s", self.name, e)
            # Retry mit längerem Delay
            async def retry_listeners():
                await asyncio.sleep(2.0)  # Längerer Retry-Delay
                try:
                    if not getattr(self, '_listeners_registered', False):
                        await self._register_event_listeners_async()
                        self._listeners_registered = True
                        _LOGGER.info("🔄 Retry successful: Event listeners registered for: %s", self.name)
                    else:
                        _LOGGER.info("🔄 Retry check: Event listeners already registered for: %s", self.name)
                except Exception as retry_e:
                    _LOGGER.error("Retry failed for %s: %s", self.name, retry_e)
                    # Finaler Retry nach noch längerem Delay
                    async def final_retry():
                        await asyncio.sleep(5.0)
                        try:
                            if not getattr(self, '_listeners_registered', False):
                                self._register_event_listeners()  # Synchrone Version als letzter Versuch
                                self._listeners_registered = True
                                _LOGGER.info("⚙️ Final retry successful: Event listeners registered for: %s", self.name)
                        except Exception as final_e:
                            _LOGGER.error("Final retry failed for %s: %s", self.name, final_e)
                    
                    self.hass.async_create_task(final_retry())
            
            self.hass.async_create_task(retry_listeners())

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        await super().async_will_remove_from_hass()
        _LOGGER.info("🔍 Binary sensor being removed: %s", self.name)
        
        # Unregister devices loaded listener
        if hasattr(self, '_devices_loaded_listener') and self._devices_loaded_listener:
            self._devices_loaded_listener()
            
        # Unregister event listeners and cleanup timers
        self._unregister_event_listeners()
        self._cleanup_timers()
        
        # Mark listeners as unregistered
        self._listeners_registered = False

    def _register_event_listeners(self) -> None:
        """Register event listeners for button press/release with hold detection."""
        
        # Validate that hass is available
        if not self.hass:
            _LOGGER.error("Cannot register event listeners - hass not available for %s", self.name)
            return
            
        # Check if already registered
        if getattr(self, '_listeners_registered', False):
            _LOGGER.debug("Event listeners already registered for %s", self.name)
            return
        
        @callback
        def handle_button_press(event):
            """Handle button press event with hold detection."""
            try:
                event_serial = event.data.get("serial_number")
                event_device_id = event.data.get("device_id")
                event_button = event.data.get("button")
                is_press = event.data.get("is_press", True)
                is_release = event.data.get("is_release", False)
                is_test = event.data.get("test", False)
                is_low_battery = event.data.get("is_low_battery", False)
                battery_level = event.data.get("battery_level", 100)

                # Normalize button id from name if missing or ambiguous
                if event_button is None:
                    button_name = event.data.get("button_name") or event.data.get("subtype") or ""
                    normalized_name = button_name.strip()
                    # Support both German and English button labels
                    name_lower = normalized_name.lower()
                    if name_lower.startswith("taste ") or name_lower.startswith("button "):
                        normalized_name = normalized_name.split()[-1]
                    name_map = {"A": 0, "B": 1, "C": 2, "D": 3}
                    if normalized_name in name_map:
                        event_button = name_map[normalized_name]

                # Normalize numeric string button ids
                if isinstance(event_button, str) and event_button.isdigit():
                    event_button = int(event_button)

                # Mask button id if function bits are included
                if isinstance(event_button, int) and event_button > 3:
                    event_button = event_button & 0x03
                
                # Update battery low status if this event is for our device and button
                if (event_serial == self._serial_number or 
                    event_device_id == f"eldat_transmitter_{self._serial_number.lower()}") and event_button == self._button_id:
                    if is_low_battery:
                        self._battery_low = True
                        _LOGGER.warning("🪫 Battery low detected for %s button %s (level: %s%%)", 
                                      self.name, self._button_name, battery_level)
                    else:
                        # Update battery status from normal events too
                        if battery_level <= 20:  # Consider <20% as low
                            self._battery_low = True
                        else:
                            self._battery_low = False
                
                # Match by serial_number or device_id with last-8 fallback
                matches_device = False
                if event_serial:
                    if event_serial == self._serial_number:
                        matches_device = True
                    elif len(event_serial) >= 8 and len(self._serial_number) >= 8:
                        matches_device = event_serial[-8:] == self._serial_number[-8:]
                if not matches_device and event_device_id:
                    if event_device_id == f"eldat_transmitter_{self._serial_number.lower()}":
                        matches_device = True
                    elif len(event_device_id) >= 8 and len(self._serial_number) >= 8:
                        matches_device = event_device_id[-8:] == self._serial_number[-8:]
                
                if is_test:
                    _LOGGER.info("🧪 Test event received by %s", self.name)
                
                _LOGGER.debug("🔍 Event received: serial=%s, device_id=%s, button=%s, press=%s, release=%s, matches=%s", 
                             event_serial if event_serial else "None", 
                             event_device_id,
                             event_button, is_press, is_release, matches_device)
                
                # RX11 hardware limitation: Release telegram always contains button 0 (A)
                # We need to release the button that is actually active for this sensor
                # The actual button info comes from the _active_buttons tracking
                
                if matches_device and is_release and self._switch_mode == "impulse":
                    # Check if THIS button is currently active - if so, release it
                    active_set = self._active_buttons.get(self._serial_number, set())
                    if self._button_id in active_set:
                        _LOGGER.info("🔄 Release received, button %s is active - releasing", self._button_name)
                        # Don't release immediately - a new Press might follow
                        # The delayed release logic below handles this
                        pass  # Fall through to the normal release handling
                    else:
                        # This button is not active, ignore the release
                        _LOGGER.debug("🔄 Release received but button %s not active - ignoring", self._button_name)
                        return
                
                if matches_device and event_button == self._button_id:
                    _LOGGER.info("🎯 Binary sensor %s received matching button event: button=%s, press=%s, release=%s", 
                                self.name, event_button, is_press, is_release)
                    
                    if is_press:
                        # Button pressed
                        # First, implicitly release any OTHER button that was active (button switch)
                        # This handles: B held -> A pressed -> Release(old) + Press(A) sequence
                        active_set = self._active_buttons.get(self._serial_number, set())
                        
                        if self._serial_number not in self._active_buttons:
                            self._active_buttons[self._serial_number] = set()
                        self._active_buttons[self._serial_number].add(self._button_id)
                        
                        current_time = datetime.now()
                        self._last_press_time = current_time
                        
                        # Handle based on switch_mode
                        if self._switch_mode == "permanent":
                            # Toggle mode: flip state on each press
                            self._is_on = not self._is_on
                            _LOGGER.info("✅ Button %s pressed on %s - toggled to %s (permanent mode)", 
                                       self._button_name, self._serial_number, "ON" if self._is_on else "OFF")
                            self.coordinator.set_device_state(
                                self._serial_number, {f"button_{self._button_id}": self._is_on}
                            )
                        else:
                            # Impulse mode: ON while pressed
                            self._is_on = True
                            _LOGGER.info("✅ Button %s pressed on %s - state ON (impulse mode)", 
                                       self._button_name, self._serial_number)
                        
                        self._is_holding = False
                        
                        # Cancel existing timers
                        if self._hold_timer:
                            self._hold_timer.cancel()
                        if self._release_timer:
                            self._release_timer.cancel()
                        if self._auto_release_timer:
                            self._auto_release_timer.cancel()
                        
                        # Update state immediately
                        self.async_write_ha_state()
                        
                        # Set hold detection timer
                        async def _check_for_hold():
                            await asyncio.sleep(self._hold_threshold_ms / 1000.0)
                            if self._is_on and self._last_press_time == current_time:
                                self._is_holding = True
                                self.async_write_ha_state()
                                _LOGGER.debug("Button %s hold detected on %s", self._button_name, self._serial_number)
                                
                                # Fire hold event (emulated, > 1 second)
                                self.hass.bus.async_fire(
                                    "eldat_button_hold",
                                    {
                                        "device_id": self._serial_number,
                                        "subtype": self._button_name,
                                        "button": self._button_id,
                                        "button_name": self._button_name,
                                        "hold_duration": self._hold_threshold_ms,
                                    }
                                )
                        
                        # Set auto-release timer (fallback) - only for impulse mode
                        async def _auto_release():
                            await asyncio.sleep(self._max_press_duration / 1000.0)
                            if self._is_on and self._switch_mode == "impulse":
                                self._handle_button_release()
                                _LOGGER.warning("Auto-released button %s after %dms", self._button_name, self._max_press_duration)
                        
                        # Start timers
                        self._hold_timer = asyncio.create_task(_check_for_hold())
                        if self._switch_mode == "impulse":
                            self._auto_release_timer = asyncio.create_task(_auto_release())
                        
                    elif is_release:
                        # Button released
                        if self._switch_mode == "impulse":
                            # Check if this is 1-button mode (operating_type == "1")
                            operating_type = self._device_info.get("operating_type")
                            if operating_type == "1" and self._last_press_time:
                                # 1-Tast-Bedienung + Impuls: Mindestens 1 Sekunde anzeigen
                                elapsed_ms = (datetime.now() - self._last_press_time).total_seconds() * 1000
                                remaining_ms = self._min_display_duration_ms - elapsed_ms
                                
                                if remaining_ms > 0:
                                    # Weniger als 1 Sekunde vergangen - verzögere Release
                                    _LOGGER.info("⏱️ Button %s auf %s: Verzögere Release um %.0fms (Mindest-Anzeigedauer)",
                                               self._button_name, self._serial_number, remaining_ms)
                                    
                                    # Cancel existing delayed release timer if any
                                    if self._delayed_release_timer:
                                        self._delayed_release_timer.cancel()
                                    
                                    async def _delayed_release():
                                        await asyncio.sleep(remaining_ms / 1000.0)
                                        if self._is_on:  # Only release if still on
                                            self._handle_button_release()
                                            _LOGGER.info("✅ Button %s released on %s nach verzögerter Anzeigedauer (impulse mode)",
                                                       self._button_name, self._serial_number)
                                    
                                    self._delayed_release_timer = asyncio.create_task(_delayed_release())
                                else:
                                    # Mehr als 1 Sekunde vergangen - sofort Release
                                    self._handle_button_release()
                                    _LOGGER.info("✅ Button %s released on %s - state OFF (impulse mode)",
                                               self._button_name, self._serial_number)
                            else:
                                # Andere Modi: sofort Release
                                self._handle_button_release()
                                _LOGGER.info("✅ Button %s released on %s - state OFF (impulse mode)",
                                           self._button_name, self._serial_number)
                        else:
                            # Permanent mode: state stays as-is, just log release
                            _LOGGER.info("✅ Button %s released on %s - state remains %s (permanent mode)", 
                                       self._button_name, self._serial_number, "ON" if self._is_on else "OFF")
                        
            except Exception as e:
                _LOGGER.error("Error handling button event: %s", e)
        
        # Register event listeners for button press/release events
        # Listen to the events that coordinator fires: eldat_button_press and eldat_button_release
        self._press_listener = self.hass.bus.async_listen(
            "eldat_button_press",
            handle_button_press
        )
        
        self._release_listener = self.hass.bus.async_listen(
            "eldat_button_release", 
            handle_button_press  # Same handler, uses is_press/is_release flags
        )
        
        # Mark listeners as registered
        self._listeners_registered = True
        
        _LOGGER.info("📡 Event listeners registered for binary sensor %s: listening for eldat_button_press/release", 
                    self.name)
        _LOGGER.debug("🎯 Event matching: serial=%s, button=%s", self._serial_number, self._button_id)

    async def _register_event_listeners_async(self) -> None:
        """Async version of event listener registration for better timing control."""
        if not self.hass:
            _LOGGER.error("Cannot register listeners - no hass instance for %s", self.name)
            return
            
        try:
            # Ensure we're not double-registering
            if getattr(self, '_listeners_registered', False):
                _LOGGER.debug("Event listeners already registered for %s", self.name)
                return
            
            # Additional delay to ensure Home Assistant is ready
            await asyncio.sleep(0.2)
            
            # Call the synchronous version which has the full implementation
            self._register_event_listeners()
            
            _LOGGER.info("✅ Async event listeners registered for %s", self.name)
        except Exception as e:
            _LOGGER.error("Error in async listener registration for %s: %s", self.name, e)
            raise  # Re-raise so retry mechanism works

    def _unregister_event_listeners(self) -> None:
        """Unregister event listeners."""
        if hasattr(self, '_press_listener') and self._press_listener:
            self._press_listener()
            self._press_listener = None
            
        if hasattr(self, '_release_listener') and self._release_listener:
            self._release_listener()
            self._release_listener = None

    def _cleanup_timers(self) -> None:
        """Clean up any active timers."""
        if self._hold_timer:
            self._hold_timer.cancel()
            self._hold_timer = None
        if self._release_timer:
            self._release_timer.cancel() 
            self._release_timer = None
        if self._auto_release_timer:
            self._auto_release_timer.cancel()
            self._auto_release_timer = None
        if self._delayed_release_timer:
            self._delayed_release_timer.cancel()
            self._delayed_release_timer = None

    def _handle_button_release(self, force_release: bool = False) -> None:
        """Handle button release with proper state management."""
        if not self._is_on:
            return  # Already released
            
        current_time = datetime.now()
        press_duration = 0
        
        if self._last_press_time:
            press_duration = (current_time - self._last_press_time).total_seconds() * 1000
        
        was_holding = self._is_holding
        
        # Remove from active buttons
        if self._serial_number in self._active_buttons:
            self._active_buttons[self._serial_number].discard(self._button_id)
            if not self._active_buttons[self._serial_number]:  # Remove empty set
                del self._active_buttons[self._serial_number]
        
        # Reset state
        self._is_on = False
        if self._switch_mode == "impulse" and self._device_info.get("operating_type") == "1":
            self._last_press_time = None
        self._is_holding = False
        
        # Cancel timers
        self._cleanup_timers()
        
        # Update state
        self.async_write_ha_state()
        
        _LOGGER.debug("Button %s released on %s (duration: %.0fms, was_holding: %s)", 
                    self._button_name, self._serial_number, press_duration, was_holding)

    def _cleanup_timers(self) -> None:
        """Cancel all active timers."""
        if self._hold_timer:
            self._hold_timer.cancel()
            self._hold_timer = None
        if self._release_timer:
            self._release_timer.cancel()
            self._release_timer = None
        if self._auto_release_timer:
            self._auto_release_timer.cancel()
            self._auto_release_timer = None
        if self._delayed_release_timer:
            self._delayed_release_timer.cancel()
            self._delayed_release_timer = None

    def _unregister_event_listeners(self) -> None:
        """Unregister event listeners."""
        if hasattr(self, '_press_listener') and self._press_listener:
            self._press_listener()
            self._press_listener = None
            
        if hasattr(self, '_release_listener') and self._release_listener:
            self._release_listener()
            self._release_listener = None
            
        _LOGGER.debug("🧹 Event listeners unregistered for %s", self.name)

    @property
    def is_on(self) -> bool | None:
        """Return true if button is pressed."""
        if self._switch_mode == "impulse" and self._device_info.get("operating_type") == "1":
            if self._last_press_time is None and not self._is_on:
                return False
        return self._is_on

    @property
    def icon(self) -> str:
        """Return the icon based on button state."""
        if self._device_info.get("operating_type") == "1":
            return "mdi:radiobox-marked" if self._is_on else "mdi:radiobox-blank"
        if self._is_holding:
            return "mdi:record-circle-outline"
        if self._is_on:
            return "mdi:radiobox-marked"
        return "mdi:radiobox-blank"

    @property
    def available(self) -> bool:
        """Return if entity is available.
        
        EW Transmitter buttons inherit the RX11 transceiver connection status 
        via via_device linkage. Additionally, they check if the device exists.
        """
        # Base: RX11 must be connected
        if not self._is_rx11_connected():
            return False
        
        # Additionally check if device exists in coordinator
        return self._serial_number in self.coordinator.devices

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        # Convert button_id to letter for last_button attribute (0=A, 1=B, 2=C, 3=D)
        button_letters = {0: "A", 1: "B", 2: "C", 3: "D"}
        
        attrs = {
            "serial_number": self._serial_number,
            "button_name": self._button_name,
            "last_button": button_letters.get(self._button_id, str(self._button_id)),
        }
        
        if self._last_press_time:
            # Lokale Zeit für bessere Lesbarkeit
            attrs["last_received"] = self._last_press_time.strftime("%d.%m.%Y %H:%M:%S")
        
        # Add battery low warning if active
        if self._battery_low:
            attrs["battery_low"] = True
        
        return attrs


class EldatTransmitterStateBinarySensor(EldatEntity, RestoreEntity, BinarySensorEntity):
    """Binary sensor for Easywave Transmitter Auf/Zu state with persistence."""

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
        
        # Use untranslated state keys - HA translates these via translations/*.json
        # Default options for cover: ["up", "down"] (not translated values like "Auf", "Zu")
        self._options = entity_spec.get("options", ["up", "down"])
        self._state_key = entity_spec.get("state_key", "transmitter_state")
        # on_label/off_label are the untranslated keys, not translated display values
        self._on_label = entity_spec.get("on_label", self._options[0] if self._options else "up")
        self._off_label = entity_spec.get("off_label", self._options[1] if len(self._options) > 1 else "down")

        # Handle entity naming consistently with device name prefix
        self._attr_has_entity_name = True  # Always prepend device name
        
        # Store translation_key for HA automatic state translation
        # This allows HA to translate entity names via translations/*.json
        self._translation_key = entity_spec.get("translation_key")
        self._attr_translation_key = self._translation_key  # Required for HA translation
        self._static_name = entity_spec.get("name")  # Fallback static name
        
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_state_binary")
        self._attr_icon = entity_spec.get("icon", "mdi:window-shutter")

        device_class_str = entity_spec.get("device_class")
        if device_class_str:
            try:
                self._attr_device_class = BinarySensorDeviceClass(device_class_str)
            except ValueError:
                self._attr_device_class = None
        else:
            self._attr_device_class = None

        self._current_state: str | None = None
        self._last_update_time: datetime | None = None
        self._last_button: int | None = None
        self._listeners_registered = False

    # NOTE: No custom name property - HA uses _attr_translation_key automatically
    # This ensures proper translation based on user's language setting

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        persistent = self.coordinator.get_device_state(self._serial_number) or {}
        if self._state_key in persistent and persistent[self._state_key] in self._options:
            self._current_state = persistent[self._state_key]
        elif (last_state := await self.async_get_last_state()) is not None:
            if last_state.state == "on":
                self._current_state = self._on_label
            elif last_state.state == "off":
                self._current_state = self._off_label

        self._register_event_listeners()

    @property
    def is_on(self) -> bool:
        return self._current_state == self._on_label

    @property
    def icon(self) -> str:
        """Return dynamic icon based on current state."""
        # Use language-independent state checking
        # Cover states: check for up/down using state helper functions
        if is_up_state(self._current_state):
            return "mdi:window-shutter-open"
        elif is_down_state(self._current_state):
            return "mdi:window-shutter"
        elif is_stop_state(self._current_state):
            return "mdi:stop-circle"
        # Switch states: check for on/off
        elif is_on_state(self._current_state):
            return "mdi:toggle-switch"
        elif is_off_state(self._current_state):
            return "mdi:toggle-switch-off"
        # Fallback to configured icon or default
        return self._attr_icon

    def _register_event_listeners(self) -> None:
        if self._listeners_registered or not self.hass:
            return

        @callback
        def _handle_button_event(event):
            event_serial = event.data.get("serial_number", "") or event.data.get("device_id", "")
            event_button = event.data.get("button")
            if event_button is None:
                event_button = event.data.get("button_id")

            # Normalize button id from name if missing
            if event_button is None:
                button_name = event.data.get("button_name") or event.data.get("subtype") or ""
                normalized_name = button_name.strip()
                # Support both German and English button labels
                name_lower = normalized_name.lower()
                if name_lower.startswith("taste ") or name_lower.startswith("button "):
                    normalized_name = normalized_name.split()[-1]
                name_map = {"A": 0, "B": 1, "C": 2, "D": 3}
                if normalized_name in name_map:
                    event_button = name_map[normalized_name]

            # Normalize numeric string button ids
            if isinstance(event_button, str) and event_button.isdigit():
                event_button = int(event_button)

            # Mask button id if function bits are included
            if isinstance(event_button, int) and event_button > 3:
                event_button = event_button & 0x03

            # Compare full serial numbers or match by last 8 characters as fallback
            matches_device = False
            if event_serial:
                if event_serial == self._serial_number:
                    matches_device = True
                elif len(event_serial) >= 8 and len(self._serial_number) >= 8:
                    matches_device = event_serial[-8:] == self._serial_number[-8:]

            if not matches_device:
                event_device_id = event.data.get("device_id")
                if event_device_id == f"eldat_transmitter_{self._serial_number.lower()}":
                    matches_device = True
                elif event_device_id and len(event_device_id) >= 8 and len(self._serial_number) >= 8:
                    matches_device = event_device_id[-8:] == self._serial_number[-8:]

            if not matches_device:
                return

            if event_button in self._button_map:
                new_state = self._button_map[event_button]
                if new_state in self._options:
                    old_state = self._current_state
                    self._current_state = new_state
                    self._last_update_time = datetime.now()
                    self._last_button = event_button
                    self.coordinator.set_device_state(
                        self._serial_number, {self._state_key: new_state}
                    )
                    self.async_write_ha_state()
                    _LOGGER.debug("🔄 Transmitter binary sensor %s: %s -> %s (button %s)", 
                               self.name or self._attr_unique_id, old_state, new_state, event_button)

        self.async_on_remove(
            self.hass.bus.async_listen("eldat_button_press", _handle_button_event)
        )

        self._listeners_registered = True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes with the human-readable state label."""
        attrs = {
            "serial_number": self._serial_number,
        }
        
        # Add the human-readable state label (state label in current language)
        if self._current_state:
            attrs["state_label"] = self._current_state
        
        # Add last received timestamp and button code if available
        if hasattr(self, '_last_update_time') and self._last_update_time:
            attrs["last_received"] = self._last_update_time.strftime("%d.%m.%Y %H:%M:%S")
        
        if hasattr(self, '_last_button') and self._last_button is not None:
            # Convert button ID to letter (0=A, 1=B, 2=C, 3=D)
            button_letters = {0: "A", 1: "B", 2: "C", 3: "D"}
            attrs["last_button"] = button_letters.get(self._last_button, str(self._last_button))
        
        return attrs

    async def _register_event_listeners_async(self) -> None:
        if not self.hass:
            return
        await asyncio.sleep(0)
        self._register_event_listeners()


class EWneoBatterySensor(EldatEntity, BinarySensorEntity):
    """Battery binary sensor for EWneo-Sensoren.
    
    Reads battery_level from coordinator.devices and reports:
    - is_on = True (niedrig) when battery_level 0-6
    - is_on = False (normal) when battery_level = 7
    """

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any] = None,
    ) -> None:
        """Initialize EWneo battery sensor."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._entity_spec = entity_spec or {}
        self._attr_device_class = BinarySensorDeviceClass.BATTERY
        self._attr_unique_id = self._entity_spec.get("unique_id", f"{serial_number}_battery_warning")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_has_entity_name = True
        
        # Use translation_key for proper HA translation
        translation_key = self._entity_spec.get("translation_key", "battery_warning")
        self._attr_translation_key = translation_key
        # Do NOT set _attr_name when using translation_key
        
        _LOGGER.info("🔋 EWneo battery sensor initialized: translation_key=%s (serial: %s)", 
                    translation_key, serial_number[-8:])

    @property
    def is_on(self) -> bool:
        """Return True if battery is low (0-6), False if normal (7)."""
        device_data = self.coordinator.devices.get(self._serial_number, {})
        battery_level = device_data.get("battery_level")
        
        if battery_level is None:
            # No data yet - assume normal
            return False
        
        # battery_level 0-6 = low (ON), 7 = normal (OFF)
        is_low = battery_level < 7
        
        return is_low

    @property
    def available(self) -> bool:
        """Return if entity is available.
        
        EWneo battery sensors inherit the RX11 transceiver connection status 
        via via_device linkage. Additionally, they check if the device exists.
        """
        # Base: RX11 must be connected
        if not self._is_rx11_connected():
            return False
        
        # Additionally check if device exists
        return self._serial_number in self.coordinator.devices

    @property
    def icon(self) -> str:
        """Return icon based on battery status."""
        if self.is_on:  # Battery low
            return "mdi:battery-alert"
        return "mdi:battery"  # Battery normal

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        device_data = self.coordinator.devices.get(self._serial_number, {})
        battery_level = device_data.get("battery_level")
        
        attrs = {}
        if battery_level is not None:
            attrs["battery_raw"] = battery_level
            attrs["battery_status"] = "niedrig" if battery_level < 7 else "normal"
        
        return attrs

    async def _register_event_listeners_async(self) -> None:
        """Register event listeners (not needed for coordinator-based sensor)."""
        # This sensor updates via CoordinatorEntity, no additional listeners needed
        pass


class EldatBatteryWarningSensor(EldatEntity, BinarySensorEntity):
    """Binary sensor for battery warning.
    
    This sensor is ON when battery is low (for Easywave Transmitters).
    """

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any] = None,
    ) -> None:
        """Initialize battery warning sensor."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._entity_spec = entity_spec or {}
        self._attr_device_class = BinarySensorDeviceClass.BATTERY
        self._attr_unique_id = self._entity_spec.get("unique_id", f"{serial_number}_battery_warning")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_has_entity_name = True
        
        # Store translation_key for dynamic name resolution
        self._translation_key = self._entity_spec.get("translation_key", "battery_warning")
        # Set _attr_translation_key so HA handles translation automatically
        self._attr_translation_key = self._translation_key
        
        self._battery_warning = False
        self._listeners_registered = False
        
        _LOGGER.info("🔋 Battery warning sensor initialized: translation_key=%s", self._translation_key)

    # NOTE: No custom name property - HA uses _attr_translation_key automatically
    # This ensures proper translation based on user's language setting

    @property
    def is_on(self) -> bool:
        """Return True if battery warning is active."""
        return self._battery_warning

    @property
    def available(self) -> bool:
        """Return if entity is available.
        
        Battery warning sensors are always available since the battery state
        is persistent and doesn't change based on transceiver connection.
        This prevents unnecessary availability changes in the logbook.
        """
        return True

    async def async_added_to_hass(self) -> None:
        """Called when entity is added to hass."""
        await super().async_added_to_hass()
        self._register_event_listeners()

    def _register_event_listeners(self) -> None:
        """Register event listeners for battery status updates."""
        if self._listeners_registered or not self.hass:
            return

        @callback
        def handle_battery_update(event):
            """Handle battery status update events."""
            try:
                event_serial = event.data.get("serial_number") or event.data.get("device_id")
                
                # Check if this event is for our device
                if event_serial != self._serial_number:
                    # Try matching last 8 characters
                    if not (event_serial and len(event_serial) >= 8 and 
                            len(self._serial_number) >= 8 and 
                            event_serial[-8:] == self._serial_number[-8:]):
                        return
                
                # Only process if is_low_battery is explicitly set in the event
                # This prevents reacting to regular button events without battery info
                if "is_low_battery" not in event.data:
                    return
                
                is_low_battery = event.data["is_low_battery"]
                
                _LOGGER.debug("🔋 Battery status update for transmitter %s: is_low=%s", 
                            self._serial_number, is_low_battery)
                
                if is_low_battery:
                    if not self._battery_warning:
                        self._battery_warning = True
                        _LOGGER.warning("🪫 Battery LOW for transmitter %s", self._serial_number)
                        self.async_write_ha_state()
                else:
                    # Battery is OK
                    if self._battery_warning:
                        self._battery_warning = False
                        _LOGGER.info("🔋 Battery OK for transmitter %s", self._serial_number)
                        self.async_write_ha_state()
                
            except Exception as e:
                _LOGGER.error("Error handling battery update event: %s", e)

        @callback
        def handle_sensor_update(event):
            """Handle sensor update events (for EWneo sensors)."""
            try:
                telegram_data = event.data.get("data", {}) or event.data
                event_serial = telegram_data.get("id") or telegram_data.get("serial_number")
                
                # Check if this event is for our device
                if event_serial and self._serial_number != event_serial:
                    return
                
                # For EWneo-Sensors: check battery_warning flag
                battery_warning = telegram_data.get("battery_warning", False)
                if battery_warning != self._battery_warning:
                    self._battery_warning = battery_warning
                    if battery_warning:
                        _LOGGER.warning("🔋 Battery warning ON for %s (battery empty)", self.name)
                    else:
                        _LOGGER.info("🔋 Battery warning OFF for %s", self.name)
                    self.async_write_ha_state()
                
            except Exception as e:
                _LOGGER.error("Error handling sensor update event: %s", e)

        # Listen for battery events (Easywave Transmitter) - new dedicated events
        self.async_on_remove(
            self.hass.bus.async_listen("eldat_battery_low", handle_battery_update)
        )
        self.async_on_remove(
            self.hass.bus.async_listen("eldat_battery_ok", handle_battery_update)
        )
        self.async_on_remove(
            self.hass.bus.async_listen("eldat_battery_reset", handle_battery_update)
        )
        
        # Listen for sensor updates (EWneo-Sensors)
        self.async_on_remove(
            self.hass.bus.async_listen("eldat_sensor_update", handle_sensor_update)
        )
        
        self._listeners_registered = True
        _LOGGER.info("🎯 Event listeners registered for battery warning sensor: %s", self._attr_unique_id)

    async def async_update(self) -> None:
        """Update the sensor state from coordinator data."""
        device_data = self.coordinator.data.get(self._serial_number, {})
        
        # Check for battery_warning in device data
        battery_warning = device_data.get("battery_warning", False)
        if battery_warning != self._battery_warning:
            self._battery_warning = battery_warning
            if battery_warning:
                _LOGGER.warning("🔋 Battery warning ON for %s (from coordinator)", self.name)

    @property
    def icon(self) -> str:
        """Return icon based on battery status."""
        if self._battery_warning:
            return "mdi:battery-alert"  # Kritisch - rotes Alert-Icon
        return "mdi:battery"  # Normal - grünes Battery-Icon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "battery_status": "Kritisch" if self._battery_warning else "Normal",
        }
