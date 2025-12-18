"""Binary sensor entities for ELDAT integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict
from datetime import datetime, timedelta

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, EVENT_DEVICE_ADDED, BUTTON_LABELS
from .entity_registry import get_entity_registry

from .coordinator import EldatCoordinator
from .entity import EldatEntity
from .device_icons import (
    get_entity_config_for_device,
    get_extra_state_attributes_for_device
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ELDAT binary sensor entities."""
    _LOGGER.info("🔧 Starting Binary Sensor Platform Setup")
    
    coordinator: EldatCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    # Setup binary sensors for all devices
    _LOGGER.info("📊 Setting up binary sensors")
    all_devices = coordinator.get_all_devices()
    _LOGGER.info("📊 Coordinator has %d total devices", len(all_devices))
    
    # Count EW-Transmitters that need binary sensors
    ew_transmitter_count = len([d for d in all_devices.values() if d.get("type") == "ew_transmitter"])
    _LOGGER.info("🎛️ Found %d EW-Transmitter devices that need binary sensors", ew_transmitter_count)
    
    # Create binary sensors for all existing devices (including restored ones)
    _LOGGER.info("🔍 Setting up binary sensors for %d existing devices", len(coordinator.get_all_devices()))
    
    def _create_binary_sensors_for_device(serial_number: str, device_info: Dict[str, Any]) -> list:
        """Create binary sensor entities for a device."""
        binary_sensors = []
        device_type = device_info.get("type", "unknown")
        
        _LOGGER.info("🔍 Processing device %s: type=%s, button_count=%s", 
                    serial_number[-6:], device_type, device_info.get("button_count", "unknown"))
        
        # EW-Transmitters: Create binary sensors for button states
        if device_type == "ew_transmitter":
            button_count = device_info.get("button_count", device_info.get("channels", 4))
            device_name = device_info.get("name", f"EW-Transmitter {serial_number[-6:]}")
            
            _LOGGER.info("🎛️ Creating %d binary sensors for EW-Transmitter %s", button_count, device_name)
            
            # Create binary sensor for each button (HA will handle duplicates)
            for button_id in range(button_count):
                entity = EldatTransmitterButtonSensor(
                    coordinator=coordinator,
                    serial_number=serial_number,
                    device_info=device_info,
                    button_id=button_id,
                )
                binary_sensors.append(entity)
                _LOGGER.info("✅ Created binary sensor: %s", entity.name)
            
            _LOGGER.info("✅ Successfully created %d binary sensors for device %s", len(binary_sensors), serial_number[-6:])
            return binary_sensors
        
        # No binary sensors needed for this device type
        _LOGGER.debug("Device %s (%s) does not need binary sensors", serial_number[-6:], device_type)
        return []
    
    binary_sensors = []
    
    # Create binary sensors for all existing devices (including restored ones)
    _LOGGER.info("🔍 Setting up binary sensors for %d existing devices", len(coordinator.get_all_devices()))
    
    # Debug: List all devices and their types
    for serial_number, device_info in coordinator.get_all_devices().items():
        device_type = device_info.get("type", "unknown")
        device_name = device_info.get("name", serial_number)
        _LOGGER.debug("Found device: %s (%s) - Type: %s", device_name, serial_number[-6:], device_type)
    
    for serial_number, device_info in coordinator.get_all_devices().items():
        device_type = device_info.get("type", "unknown")
        device_name = device_info.get("name", serial_number)
        
        # Only log for devices that might have binary sensors
        if device_type == "ew_transmitter":
            device_name = device_info.get("name", serial_number)
            _LOGGER.info("🎛️ Checking EW-Transmitter %s for binary sensors", device_name)
        
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
            
            # Wichtig: Event Listener für alle Binary Sensors registrieren
            for sensor in binary_sensors:
                if hasattr(sensor, '_register_event_listeners'):
                    try:
                        sensor._register_event_listeners()
                        _LOGGER.debug("🎯 Event listeners registered for binary sensor %s", sensor.name)
                    except Exception as e:
                        _LOGGER.error("Failed to register event listeners for %s: %s", sensor.name, e)
            
            _LOGGER.info("✅ Successfully added %d binary sensor entities for %d EW-Transmitter devices", 
                        len(binary_sensors), len([d for d in coordinator.get_all_devices().values() if d.get("type") == "ew_transmitter"]))
        except Exception as e:
            _LOGGER.error("❌ Failed to add binary sensor entities: %s", e)
    else:
        # Check if there are EW-Transmitter devices that should have binary sensors
        ew_transmitters = [d for d in coordinator.get_all_devices().values() if d.get("type") == "ew_transmitter"]
        if ew_transmitters:
            _LOGGER.error("❌ Found %d EW-Transmitters but no binary sensors created - this is a bug!", len(ew_transmitters))
            for serial, device in [(s, d) for s, d in coordinator.get_all_devices().items() if d.get("type") == "ew_transmitter"]:
                _LOGGER.error("   EW-Transmitter: %s (%s) - %s", device.get("name", "Unknown"), serial[-6:], device)
        else:
            _LOGGER.debug("No binary sensor entities created (no EW-Transmitter devices found)")
    
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
                        serial_number[-6:] if serial_number else "unknown", device_type, force_create)
            
            if not serial_number or not device_info:
                _LOGGER.error("Device added event missing serial_number or device_info")
                return
            
            # Only log for devices that need binary sensors
            if device_type == "ew_transmitter":
                _LOGGER.info("Binary sensor handler received device added event: %s (%s)", 
                            serial_number[-6:], device_type)
                _LOGGER.info("🔍 About to call _create_binary_sensors_for_device...")
            else:
                _LOGGER.debug("Device %s (%s) does not need binary sensors, skipping", 
                            serial_number[-6:], device_type)
                return
            
            new_binary_sensors = _create_binary_sensors_for_device(serial_number, device_info)
            _LOGGER.info("🔍 _create_binary_sensors_for_device returned %d sensors", len(new_binary_sensors) if new_binary_sensors else 0)
            
            if new_binary_sensors:
                # Add entities without update_before_add to avoid state issues
                async_add_entities(new_binary_sensors, update_before_add=False)
                
                _LOGGER.info("✅ Added %d binary sensor entities for new device %s (%s)", 
                           len(new_binary_sensors), serial_number[-6:], device_type)
                
                # Wichtig: Warte einen Moment und stelle dann sicher, dass Event Listeners registriert sind
                async def ensure_listeners_after_delay():
                    await asyncio.sleep(1.0)  # Warte bis Entitäten vollständig registriert sind
                    for sensor in new_binary_sensors:
                        try:
                            if not getattr(sensor, '_listeners_registered', False) and sensor.hass:
                                await sensor._register_event_listeners_async()
                                sensor._listeners_registered = True
                                _LOGGER.info("🎯 Ensured delayed event listeners for %s", sensor.name)
                        except Exception as e:
                            _LOGGER.error("Error ensuring delayed listeners for %s: %s", sensor.name, e)
                
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
                _LOGGER.info("Force creating binary sensors for %s", serial_number[-6:] if serial_number else "unknown")
                
                new_binary_sensors = _create_binary_sensors_for_device(serial_number, device_info)
                
                if new_binary_sensors:
                    # Add entities without update_before_add to avoid state issues
                    async_add_entities(new_binary_sensors, update_before_add=False)
                    
                    # Give Home Assistant time to register the entities properly  
                    await asyncio.sleep(0.3)
                    
                    # Ensure event listeners are active for all new sensors
                    for sensor in new_binary_sensors:
                        try:
                            # Force listener registration if not done yet
                            if not getattr(sensor, '_listeners_registered', False):
                                if sensor.hass:
                                    sensor._register_event_listeners() 
                                    sensor._listeners_registered = True
                                    _LOGGER.info("🎯 Force-registered event listeners for %s", sensor.name)
                                else:
                                    # Schedule retry if entity not ready
                                    async def retry_later():
                                        await asyncio.sleep(0.5)
                                        if sensor.hass and not getattr(sensor, '_listeners_registered', False):
                                            sensor._register_event_listeners()
                                            sensor._listeners_registered = True
                                            _LOGGER.info("🎯 Retry: Registered event listeners for %s", sensor.name)
                                    hass.async_create_task(retry_later())
                        except Exception as e:
                            _LOGGER.error("Error ensuring listeners for %s: %s", sensor.name, e)
                    
                    _LOGGER.info("✅ Force-created %d binary sensor entities for %s", 
                               len(new_binary_sensors), serial_number[-6:] if serial_number else "unknown")
                    
        except Exception as e:
            _LOGGER.error("Error in force binary sensor creation: %s", e)
    
    # Register backup event listener
    config_entry.async_on_unload(
        hass.bus.async_listen("eldat_plugin_binary_sensor_create", _handle_direct_binary_sensor_creation)
    )
    
    _LOGGER.info("✅ Binary sensor platform setup complete with event listeners")
    
    config_entry.async_on_unload(
        hass.bus.async_listen(f"{DOMAIN}_create_binary_sensors", _handle_direct_binary_sensor_creation)
    )
    
    config_entry.async_on_unload(
        hass.bus.async_listen(f"{EVENT_DEVICE_ADDED}_binary_sensor", _handle_device_added)
    )
    
    _LOGGER.info("✅ Binary sensor platform setup complete with event listeners")


class EldatTransmitterButtonSensor(EldatEntity, BinarySensorEntity):
    """Binary sensor for EW-Transmitter button state with press/hold detection."""
    
    # Class variable to track which buttons are currently pressed per device
    _active_buttons = {}

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        button_id: int,
    ) -> None:
        """Initialize transmitter button binary sensor."""
        _LOGGER.warning("🔍 Initializing binary sensor for device %s, button %d", serial_number, button_id)
        
        self._button_id = button_id
        self._button_name = BUTTON_LABELS.get(button_id, f"Taste {button_id + 1}")
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
        
        # Initialize parent classes
        super().__init__(coordinator, serial_number, device_info)
        
        self._attr_unique_id = f"{serial_number}_btn{button_id}"
        self._attr_name = f"{device_info['name']} {self._button_name}"
        
        # Ensure entity is enabled by default
        self._attr_entity_registry_enabled_default = True
        
        # Get device-specific configuration
        device_type = device_info.get("type", "unknown")
        device_config = get_entity_config_for_device(
            device_type=device_type,
            entity_type="binary_sensor"
        )
        
        self._attr_icon = device_config.get("icon", "mdi:gesture-tap-button")
        self._attr_device_class = None  # No specific device class for remote buttons
        
        _LOGGER.warning("🔍 Binary sensor initialized: %s (unique_id: %s, enabled_default: %s)", 
                       self._attr_name, self._attr_unique_id, self._attr_entity_registry_enabled_default)

    async def async_added_to_hass(self) -> None:
        """Called when entity is added to Home Assistant."""
        await super().async_added_to_hass()
        _LOGGER.info("🔍 Binary sensor added to hass: %s", self._attr_name)
        
        # Listen for devices loaded event to update availability
        @callback
        def devices_loaded_callback(event):
            """Update availability when devices are loaded."""
            _LOGGER.info("📡 Devices loaded event received for %s - updating availability", self._attr_name)
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
                _LOGGER.info("🎯 Event listeners successfully registered for: %s", self._attr_name)
            else:
                _LOGGER.debug("Event listeners already registered for: %s", self._attr_name)
            
            # Schedule availability check after coordinator is fully loaded
            async def check_availability_after_startup():
                """Check availability after coordinator has loaded devices."""
                await asyncio.sleep(2.0)  # Wait for coordinator to load
                if hasattr(self.coordinator, 'devices') and self.coordinator.devices:
                    _LOGGER.info("📡 Updating availability for %s after startup", self._attr_name)
                    self.async_write_ha_state()  # Trigger availability update
                    
            self.hass.async_create_task(check_availability_after_startup())
            
        except Exception as e:
            _LOGGER.error("Error in async_added_to_hass for %s: %s", self._attr_name, e)
            # Retry mit längerem Delay
            async def retry_listeners():
                await asyncio.sleep(2.0)  # Längerer Retry-Delay
                try:
                    if not getattr(self, '_listeners_registered', False):
                        await self._register_event_listeners_async()
                        self._listeners_registered = True
                        _LOGGER.info("🔄 Retry successful: Event listeners registered for: %s", self._attr_name)
                    else:
                        _LOGGER.info("🔄 Retry check: Event listeners already registered for: %s", self._attr_name)
                except Exception as retry_e:
                    _LOGGER.error("Retry failed for %s: %s", self._attr_name, retry_e)
                    # Finaler Retry nach noch längerem Delay
                    async def final_retry():
                        await asyncio.sleep(5.0)
                        try:
                            if not getattr(self, '_listeners_registered', False):
                                self._register_event_listeners()  # Synchrone Version als letzter Versuch
                                self._listeners_registered = True
                                _LOGGER.info("⚙️ Final retry successful: Event listeners registered for: %s", self._attr_name)
                        except Exception as final_e:
                            _LOGGER.error("Final retry failed for %s: %s", self._attr_name, final_e)
                    
                    self.hass.async_create_task(final_retry())
            
            self.hass.async_create_task(retry_listeners())

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        await super().async_will_remove_from_hass()
        _LOGGER.info("🔍 Binary sensor being removed: %s", self._attr_name)
        
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
            _LOGGER.error("Cannot register event listeners - hass not available for %s", self._attr_name)
            return
            
        # Check if already registered
        if getattr(self, '_listeners_registered', False):
            _LOGGER.debug("Event listeners already registered for %s", self._attr_name)
            return
        
        @callback
        def handle_button_press(event):
            """Handle button press event with hold detection."""
            try:
                event_serial = event.data.get("serial_number")
                event_device_id = event.data.get("device_id")
                event_button = event.data.get("button")
                is_push = event.data.get("is_push", True)
                is_release = event.data.get("is_release", False)
                is_test = event.data.get("test", False)
                is_low_battery = event.data.get("is_low_battery", False)
                battery_level = event.data.get("battery_level", 100)
                
                # Update battery low status if this event is for our device and button
                if (event_serial == self._serial_number or 
                    event_device_id == f"eldat_transmitter_{self._serial_number.lower()}") and event_button == self._button_id:
                    if is_low_battery:
                        self._battery_low = True
                        _LOGGER.warning("🪫 Battery low detected for %s button %s (level: %s%%)", 
                                      self._attr_name, self._button_name, battery_level)
                    else:
                        # Update battery status from normal events too
                        if battery_level <= 20:  # Consider <20% as low
                            self._battery_low = True
                        else:
                            self._battery_low = False
                
                # Match by serial_number OR device_id
                matches_device = (
                    event_serial == self._serial_number or 
                    event_device_id == f"eldat_transmitter_{self._serial_number.lower()}"
                )
                
                if is_test:
                    _LOGGER.info("🧪 Test event received by %s", self.name)
                
                _LOGGER.debug("🔍 Event received: serial=%s, device_id=%s, button=%s, push=%s, release=%s, matches=%s", 
                             event_serial[-6:] if event_serial else "None", 
                             event_device_id,
                             event_button, is_push, is_release, matches_device)
                
                # Handle general release for all buttons (Button A/0 release releases all)
                if (matches_device and 
                    is_release and event_button == 0 and 
                    self._button_id != 0):
                    # Button A release -> release all other buttons of this transmitter
                    _LOGGER.info("🔄 Button A released on %s - releasing button %s as well", 
                               event_serial[-6:] if event_serial else "Unknown", self._button_name)
                    self._handle_button_release()
                    return
                
                if matches_device and event_button == self._button_id:
                    _LOGGER.info("🎯 Binary sensor %s received matching button event: button=%s, push=%s, release=%s", 
                                self._attr_name, event_button, is_push, is_release)
                    
                    if is_push:
                        # Button pressed
                        if self._serial_number not in self._active_buttons:
                            self._active_buttons[self._serial_number] = set()
                        self._active_buttons[self._serial_number].add(self._button_id)
                        
                        current_time = datetime.now()
                        self._last_press_time = current_time
                        self._is_on = True
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
                        _LOGGER.info("✅ Button %s pressed on %s - state updated", self._button_name, self._serial_number[-6:])
                        
                        # Fire button press start event for device automation
                        self.hass.bus.async_fire(
                            "eldat_button_press_start", 
                            {
                                "device_id": self._serial_number,
                                "subtype": self._button_name,
                                "button": self._button_id,
                                "button_name": self._button_name,
                                "device_name": self._device_info.get("name", "Unknown")
                            }
                        )
                        
                        # Set hold detection timer
                        async def _check_for_hold():
                            await asyncio.sleep(self._hold_threshold_ms / 1000.0)
                            if self._is_on and self._last_press_time == current_time:
                                self._is_holding = True
                                self.async_write_ha_state()
                                _LOGGER.debug("Button %s hold detected on %s", self._button_name, self._serial_number[-6:])
                                
                                # Fire events for device automation system
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
                                # Fire compatible event for device triggers
                                self.hass.bus.async_fire(
                                    "eldat_button_long_press_start",
                                    {
                                        "device_id": self._serial_number,
                                        "subtype": self._button_name,
                                        "button": self._button_id,
                                        "button_name": self._button_name,
                                        "hold_duration": self._hold_threshold_ms,
                                    }
                                )
                        
                        # Set auto-release timer (fallback)
                        async def _auto_release():
                            await asyncio.sleep(self._max_press_duration / 1000.0)
                            if self._is_on:
                                self._handle_button_release()
                                _LOGGER.warning("Auto-released button %s after %dms", self._button_name, self._max_press_duration)
                        
                        # Start timers
                        self._hold_timer = asyncio.create_task(_check_for_hold())
                        self._auto_release_timer = asyncio.create_task(_auto_release())
                        
                    elif is_release:
                        # Button released (specific button or Button A release for this specific button)
                        self._handle_button_release()
                        _LOGGER.info("✅ Button %s released on %s - state updated", self._button_name, self._serial_number[-6:])
                        
            except Exception as e:
                _LOGGER.error("Error handling button event: %s", e)
        
        # Register event listeners for button press/release events
        self._press_listener = self.hass.bus.async_listen(
            f"{DOMAIN}_button_press",
            handle_button_press
        )
        
        self._release_listener = self.hass.bus.async_listen(
            f"{DOMAIN}_button_release", 
            handle_button_press  # Same handler, uses is_push/is_release flags
        )
        
        # Register listener for battery low events
        self._battery_low_listener = self.hass.bus.async_listen(
            f"{DOMAIN}_button_low_battery",
            handle_button_press  # Same handler, but will set battery_low flag
        )
        
        # Mark listeners as registered
        self._listeners_registered = True
        
        _LOGGER.info("📡 Event listeners registered for binary sensor %s: listening for %s_button_press/release", 
                    self._attr_name, DOMAIN)
        _LOGGER.debug("🎯 Event matching: serial=%s, button=%s", self._serial_number, self._button_id)

    async def _register_event_listeners_async(self) -> None:
        """Async version of event listener registration for better timing control."""
        if not self.hass:
            _LOGGER.error("Cannot register listeners - no hass instance for %s", self._attr_name)
            return
            
        try:
            # Ensure we're not double-registering
            if getattr(self, '_listeners_registered', False):
                _LOGGER.debug("Event listeners already registered for %s", self._attr_name)
                return
            
            # Additional delay to ensure Home Assistant is ready
            await asyncio.sleep(0.2)
            
            # Call the synchronous version which has the full implementation
            self._register_event_listeners()
            
            _LOGGER.info("✅ Async event listeners registered for %s", self._attr_name)
        except Exception as e:
            _LOGGER.error("Error in async listener registration for %s: %s", self._attr_name, e)
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
        self._is_holding = False
        
        # Cancel timers
        self._cleanup_timers()
        
        # Update state
        self.async_write_ha_state()
        
        # Fire appropriate event (unless it's a forced auto-release)
        if not force_release:
            if was_holding:
                _LOGGER.debug("Button %s hold released on %s (duration: %.0fms)", 
                            self._button_name, self._serial_number[-6:], press_duration)
                # Original event
                self.hass.bus.async_fire(
                    f"eldat_button_hold_release",
                    {
                        "device_id": self._serial_number,
                        "device_name": self._device_info.get("name", "Unknown"),
                        "button": self._button_id,
                        "button_name": self._button_name,
                        "duration": press_duration
                    }
                )
                # Device trigger compatible event
                self.hass.bus.async_fire(
                    "eldat_button_long_press_end",
                    {
                        "device_id": self._serial_number,
                        "subtype": self._button_name,
                        "device_name": self._device_info.get("name", "Unknown"),
                        "button": self._button_id,
                        "button_name": self._button_name,
                        "duration": press_duration
                    }
                )
            else:
                _LOGGER.debug("Button %s released on %s (duration: %.0fms)", 
                            self._button_name, self._serial_number[-6:], press_duration)
                # Determine if it was a short press based on duration
                if press_duration < self._hold_threshold_ms:
                    # Short press
                    self.hass.bus.async_fire(
                        "eldat_button_short_press",
                        {
                            "device_id": self._serial_number,
                            "subtype": self._button_name,
                            "device_name": self._device_info.get("name", "Unknown"),
                            "button": self._button_id,
                            "button_name": self._button_name,
                            "duration": press_duration
                        }
                    )
                
                # Original event
                self.hass.bus.async_fire(
                    f"eldat_button_release",
                    {
                        "device_id": self._serial_number,
                        "device_name": self._device_info.get("name", "Unknown"),
                        "button": self._button_id,
                        "button_name": self._button_name,
                        "duration": press_duration
                    }
                )
                # Device trigger compatible event
                self.hass.bus.async_fire(
                    "eldat_button_press_end",
                    {
                        "device_id": self._serial_number,
                        "subtype": self._button_name,
                        "device_name": self._device_info.get("name", "Unknown"),
                        "button": self._button_id,
                        "button_name": self._button_name,
                        "duration": press_duration
                    }
                )
        
        _LOGGER.debug("Button release event processed for %s", self._button_name)

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

    def _unregister_event_listeners(self) -> None:
        """Unregister event listeners."""
        if hasattr(self, '_press_listener') and self._press_listener:
            self._press_listener()
            self._press_listener = None
            
        if hasattr(self, '_release_listener') and self._release_listener:
            self._release_listener()
            self._release_listener = None
            
        _LOGGER.debug("🧹 Event listeners unregistered for %s", self._attr_name)

    @property
    def is_on(self) -> bool:
        """Return true if button is pressed."""
        return self._is_on

    @property
    def icon(self) -> str:
        """Return the icon based on button state."""
        if self._is_holding:
            return "mdi:gesture-tap-hold"
        elif self._is_on:
            return "mdi:gesture-tap-button"
        else:
            return "mdi:radiobox-blank"

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Check multiple conditions for availability
        if not self.coordinator:
            return False
            
        # Check if coordinator is ready and has device data
        if not hasattr(self.coordinator, 'devices') or not self.coordinator.devices:
            # During startup, devices might not be loaded yet - consider available if coordinator exists
            return bool(self.coordinator)
            
        # Check if our specific device is in the coordinator's device list
        device_exists = self._serial_number in self.coordinator.devices
        
        # During restart, device might not be immediately available - be more lenient
        if not device_exists:
            # Check if transceiver is connected (indicates system is operational)
            if hasattr(self.coordinator, 'transceiver') and self.coordinator.transceiver:
                if hasattr(self.coordinator.transceiver, 'is_connected'):
                    return self.coordinator.transceiver.is_connected()
                    
        return device_exists or bool(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs = {
            "button_id": self._button_id,
            "button_name": self._button_name,
            "is_holding": self._is_holding,
            "hold_threshold_ms": self._hold_threshold_ms,
            "battery_low": self._battery_low,
        }
        
        if self._last_press_time:
            attrs["last_pressed"] = self._last_press_time.isoformat()
            
            # Calculate current press duration if actively pressed
            if self._is_on:
                current_duration = (datetime.now() - self._last_press_time).total_seconds() * 1000
                attrs["current_press_duration"] = round(current_duration, 0)
        
        # Add battery level if available
        device_info = self.coordinator.devices.get(self._serial_number, {})
        battery_level = device_info.get("battery_level")
        if battery_level is not None:
            attrs["battery_level"] = battery_level
        
        return attrs

    @property
    def icon(self) -> str:
        """Return icon with battery low indication."""
        if self._battery_low:
            return "mdi:radiobox-marked-outline"  # Different icon for low battery
        elif self._is_on:
            return "mdi:radiobox-marked"  # Pressed state
        else:
            return "mdi:radiobox-blank"   # Released state
