"""Cover platform for ELDAT motor devices."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
    PLATFORM_SCHEMA,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EldatCoordinator
from .entity import EldatEntity
from .translations import translate, get_language

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ELDAT cover entities from a config entry."""
    from .const import EVENT_DEVICE_ADDED, EVENT_FORCE_CREATE
    
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    entities = []
    
    # Restore covers from saved devices based on entity specs
    for serial_number, device_info in coordinator.get_all_devices().items():
        entity_specs = device_info.get("entities", [])
        
        _LOGGER.info("🔍 Checking device %s: type=%s, receiver_kind=%s, %d entity_specs", 
                    serial_number, device_info.get("type"), 
                    device_info.get("receiver_kind"), len(entity_specs))
        
        for entity_spec in entity_specs:
            if entity_spec.get("type") == "cover":
                _LOGGER.info("✅ Found cover entity_spec for %s", serial_number)
                try:
                    if device_info.get("neo_device"):
                        # EWneo-Motor entity - check for multi-motor devices
                        device_type_code = device_info.get("device_type_code", 0)
                        if device_type_code == 0x08:  # EWB_DT_DUAL_MOTOR
                            channel = entity_spec.get("channel", 0)
                            entities.append(EldatEWneoDualMotorCover(coordinator, serial_number, device_info, entity_spec))
                            _LOGGER.info("🔧 Restored EWneo-DualMotor cover CH%d for device %s", channel + 1, serial_number[-8:])
                        elif device_type_code == 0x09:  # EWB_DT_QUAD_MOTOR
                            channel = entity_spec.get("channel", 0)
                            entities.append(EldatEWneoQuadMotorCover(coordinator, serial_number, device_info, entity_spec))
                            _LOGGER.info("🔧 Restored EWneo-QuadMotor cover CH%d for device %s", channel + 1, serial_number[-8:])
                        else:
                            # Single motor or other EWneo-Motor
                            entities.append(EldatEWneoCover(coordinator, serial_number, device_info, entity_spec))
                            _LOGGER.info("🔧 Restored EWneo-Motor entity for device %s", serial_number[-8:])
                    else:
                        # Regular cover entity
                        entities.append(EldatCover(coordinator, serial_number, device_info, entity_spec))
                except Exception as e:
                    _LOGGER.warning("Failed to create cover for device %s: %s", 
                                  serial_number, e)
    
    if entities:
        async_add_entities(entities, update_before_add=False)
        _LOGGER.info("Added %d cover entities", len(entities))
    
    # Event handler for new devices
    async def _handle_device_added(event):
        try:
            serial_number = event.data.get("serial_number")
            device_info = event.data.get("device_info")
            entities = event.data.get("entities", [])
            
            if not serial_number or not entities:
                _LOGGER.debug("Cover: Device added event missing data")
                return
            
            new_covers = []
            for entity_spec in entities:
                if entity_spec.get("type") == "cover":
                    if device_info.get("neo_device"):
                        # Check for multi motor devices
                        device_type_code = device_info.get("device_type_code", 0)
                        if device_type_code == 0x08:  # EWB_DT_DUAL_MOTOR
                            # Entity specs already contain channel info - use directly
                            # Channel is 0-based in entity_spec, but we need 1-based for display
                            channel = entity_spec.get("channel", 0)
                            new_covers.append(EldatEWneoDualMotorCover(coordinator, serial_number, device_info, entity_spec))
                            _LOGGER.info("✅ Created EWneo-DualMotor cover CH%d for device %s", channel + 1, serial_number[-8:])
                        elif device_type_code == 0x09:  # EWB_DT_QUAD_MOTOR
                            # Entity specs already contain channel info - use directly
                            # Channel is 0-based in entity_spec, but we need 1-based for display
                            channel = entity_spec.get("channel", 0)
                            new_covers.append(EldatEWneoQuadMotorCover(coordinator, serial_number, device_info, entity_spec))
                            _LOGGER.info("✅ Created EWneo-QuadMotor cover CH%d for device %s", channel + 1, serial_number[-8:])
                        else:
                            new_covers.append(EldatEWneoCover(coordinator, serial_number, device_info, entity_spec))
                            _LOGGER.info("✅ Created EWneo-Motor entity for device %s", serial_number[-8:])
                    else:
                        new_covers.append(EldatCover(coordinator, serial_number, device_info, entity_spec))
            
            if new_covers:
                async_add_entities(new_covers)
                _LOGGER.info("Created %d cover entities for device %s", len(new_covers), serial_number)
                
        except Exception:
            _LOGGER.exception("Error creating cover entities from event")
    
    # Force create handler
    async def _handle_force_create(event):
        try:
            serial_number = event.data.get("serial_number")
            entity_type = event.data.get("entity_type")
            device_info = event.data.get("device_info")
            
            if entity_type != "cover" or not serial_number or not device_info:
                return
            
            _LOGGER.info("🔧 Force creating cover entities for device %s", serial_number)
            
            # Create cover entities from entity specs
            from .entity_specs import create_entity_specs_for_device
            entity_specs = create_entity_specs_for_device(serial_number, device_info)
            cover_entities = entity_specs.get("cover", [])
            
            new_covers = []
            for entity_spec in cover_entities:
                if device_info.get("neo_device"):
                    new_covers.append(EldatEWneoCover(coordinator, serial_number, device_info, entity_spec))
                else:
                    new_covers.append(EldatCover(coordinator, serial_number, device_info, entity_spec))
            
            if new_covers:
                async_add_entities(new_covers)
                _LOGGER.info("✅ Force-created %d cover entities for device %s", len(new_covers), serial_number)
            
        except Exception:
            _LOGGER.exception("Error force-creating cover entities")
    
    # Register event listeners
    hass.bus.async_listen(EVENT_DEVICE_ADDED, _handle_device_added)
    hass.bus.async_listen(EVENT_FORCE_CREATE, _handle_force_create)


class EldatCover(EldatEntity, CoverEntity):
    """ELDAT cover entity for motor devices."""
    
    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: dict[str, Any],
        entity_spec: dict[str, Any],
    ) -> None:
        """Initialize the cover."""
        super().__init__(coordinator, serial_number, device_info)
        
        # Extract channel from entity_spec
        self._channel = entity_spec.get("channel", 1)
        self._entity_spec = entity_spec
        
        # Get device class from entity_spec or default to SHUTTER
        device_class_str = entity_spec.get("device_class", "shade")
        if device_class_str == "blind":
            self._attr_device_class = CoverDeviceClass.BLIND
        elif device_class_str == "shutter":
            self._attr_device_class = CoverDeviceClass.SHUTTER
        elif device_class_str == "shade":
            self._attr_device_class = CoverDeviceClass.SHADE
        elif device_class_str == "garage":
            self._attr_device_class = CoverDeviceClass.GARAGE
        else:
            self._attr_device_class = CoverDeviceClass.SHUTTER
        
        # Set features based on entity_spec - only add STOP if supports_stop is True
        self._supports_stop = entity_spec.get("supports_stop", True)
        if self._supports_stop:
            self._attr_supported_features = (
                CoverEntityFeature.OPEN |
                CoverEntityFeature.CLOSE |
                CoverEntityFeature.STOP
            )
        else:
            self._attr_supported_features = (
                CoverEntityFeature.OPEN |
                CoverEntityFeature.CLOSE
            )
        
        # Set unique ID and name from entity_spec
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_cover")
        translation_key = entity_spec.get("translation_key")
        if translation_key:
            self._attr_translation_key = translation_key
            self._attr_name = None  # Let HA use translation_key
        else:
            self._attr_name = entity_spec.get("name", device_info.get('name', 'Motor'))
        
        # Store base icon and state-specific icons from entity_spec
        self._base_icon = entity_spec.get("icon", "mdi:window-shutter")
        self._icon_open = entity_spec.get("icon_open", "mdi:window-shutter-open")
        self._icon_closed = entity_spec.get("icon_closed", "mdi:window-shutter")
        self._icon_unknown = entity_spec.get("icon_unknown", self._base_icon)
        self._icon_stopped = entity_spec.get("icon_stopped", "mdi:stop-circle-outline")
        
        # Remove _attr_icon set by parent class so dynamic icon property works
        if hasattr(self, '_attr_icon'):
            del self._attr_icon
        
        self._attr_is_closed = None
        self._attr_is_closing = False
        self._attr_is_opening = False
        self._stopped = False  # Neuer Zustand für "Gestoppt"
        
        # Store operating mode for Easywave Receiver
        self._operating_mode = entity_spec.get("operating_mode", device_info.get("operating_mode", 1))
        self._receiver_kind = entity_spec.get("receiver_kind", device_info.get("receiver_kind", "motor"))
        
        # Store button config for Easywave Receiver (direct RX11 commands)
        self._button_config = entity_spec.get("button_config", {})
        self._rx11_index = device_info.get("rx11_index")
        self._stateless = entity_spec.get("stateless", False)
        self._assumed_state = entity_spec.get("assumed_state", self._stateless)
        
        # Track last command for display in attributes
        self._last_command = None  # "OPEN", "CLOSE", "STOP"
        self._last_command_time = None
    
    @property
    def assumed_state(self) -> bool:
        """Return True to always show action buttons instead of toggle."""
        return self._assumed_state
    
    @property
    def icon(self) -> str:
        """Return icon based on current state."""
        if self._stopped:
            return self._icon_stopped
        elif self._attr_is_closed is None:
            return self._icon_unknown
        elif self._attr_is_closed:
            return self._icon_closed
        else:
            return self._icon_open
    
    @property
    def is_closed(self) -> bool | None:
        """Return if cover is closed. Shows last known state even for assumed_state covers."""
        return self._attr_is_closed
    
    @property
    def state(self) -> str | None:
        """Return the state of the cover with stopped support."""
        if self._stopped:
            return "stopped"  # HA translates via translations/xx.json entity.cover.channel.state.stopped
        # Fallback to parent implementation
        if self._attr_is_opening:
            return "opening"
        if self._attr_is_closing:
            return "closing"
        if self._attr_is_closed is True:
            return "closed"
        if self._attr_is_closed is False:
            return "open"
        return None
    
    @property
    def is_closing(self) -> bool:
        """Return if cover is closing."""
        if self._stateless:
            return False
        return self._attr_is_closing
    
    @property
    def is_opening(self) -> bool:
        """Return if cover is opening."""
        if self._stateless:
            return False
        return self._attr_is_opening
    
    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        try:
            # For Easywave Receiver with button_config, send command via coordinator
            if self._button_config and "open" in self._button_config:
                button_code = self._button_config["open"]
                _LOGGER.info("📤 Easywave Receiver Cover OPEN: serial=%s, button=%d, stateless=%s", 
                           self._serial_number[-8:], button_code, self._stateless)
                
                # Send command via coordinator (routes to RX11 for EW receivers)
                success = await self._send_ew_command(button_code)
                
                if success:
                    _LOGGER.info("✅ Cover %s OPEN command sent successfully", self._serial_number[-8:])
                    # Update state to show last action (even for assumed_state covers)
                    self._attr_is_closed = False
                    self._attr_is_opening = False
                    self._attr_is_closing = False
                    self._stopped = False
                    # Track last command
                    from datetime import datetime
                    self._last_command = "OPEN"
                    self._last_command_time = datetime.now()
                    self.async_write_ha_state()
                else:
                    _LOGGER.warning("❌ Failed to send OPEN command for cover %s", self._serial_number[-8:])
                return
            
            # Fallback: Try device instance for other device types
            device_instance = None
            if hasattr(self.coordinator.transceiver, '_device_instances'):
                device_instance = self.coordinator.transceiver._device_instances.get(self._serial_number)
            
            if device_instance and hasattr(device_instance, 'open_cover'):
                self._attr_is_opening = True
                self._attr_is_closing = False
                self.async_write_ha_state()
                
                success = await device_instance.open_cover(self._channel)
                if success:
                    _LOGGER.debug("Cover %s channel %d opened successfully", 
                                self._serial_number, self._channel)
                else:
                    _LOGGER.warning("Failed to open cover %s channel %d", 
                                  self._serial_number, self._channel)
                
                # Reset opening state after command
                self._attr_is_opening = False
                self._attr_is_closed = False
                self.async_write_ha_state()
            else:
                _LOGGER.warning("No method to open cover %s - no button_config and no device_instance", 
                              self._serial_number[-8:])
        except Exception as e:
            _LOGGER.error("Error opening cover %s channel %d: %s", 
                         self._serial_number, self._channel, e)
            self._attr_is_opening = False
            self.async_write_ha_state()
    
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        try:
            # For Easywave Receiver with button_config, send command via coordinator
            if self._button_config and "close" in self._button_config:
                button_code = self._button_config["close"]
                _LOGGER.info("📤 Easywave Receiver Cover CLOSE: serial=%s, button=%d, stateless=%s", 
                           self._serial_number[-8:], button_code, self._stateless)
                
                # Send command via coordinator (routes to RX11 for EW receivers)
                success = await self._send_ew_command(button_code)
                
                if success:
                    _LOGGER.info("✅ Cover %s CLOSE command sent successfully", self._serial_number[-8:])
                    # Update state to show last action (even for assumed_state covers)
                    self._attr_is_closed = True
                    self._attr_is_opening = False
                    self._attr_is_closing = False
                    self._stopped = False
                    # Track last command
                    from datetime import datetime
                    self._last_command = "CLOSE"
                    self._last_command_time = datetime.now()
                    self.async_write_ha_state()
                else:
                    _LOGGER.warning("❌ Failed to send CLOSE command for cover %s", self._serial_number[-8:])
                return
            
            # Fallback: Try device instance for other device types
            device_instance = None
            if hasattr(self.coordinator.transceiver, '_device_instances'):
                device_instance = self.coordinator.transceiver._device_instances.get(self._serial_number)
            
            if device_instance and hasattr(device_instance, 'close_cover'):
                self._attr_is_closing = True
                self._attr_is_opening = False
                self.async_write_ha_state()
                
                success = await device_instance.close_cover(self._channel)
                if success:
                    _LOGGER.debug("Cover %s channel %d closed successfully", 
                                self._serial_number, self._channel)
                else:
                    _LOGGER.warning("Failed to close cover %s channel %d", 
                                  self._serial_number, self._channel)
                
                # Reset closing state after command
                self._attr_is_closing = False
                self._attr_is_closed = True
                self.async_write_ha_state()
            else:
                _LOGGER.warning("No method to close cover %s - no button_config and no device_instance", 
                              self._serial_number[-8:])
        except Exception as e:
            _LOGGER.error("Error closing cover %s channel %d: %s", 
                         self._serial_number, self._channel, e)
            self._attr_is_closing = False
            self.async_write_ha_state()
    
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        # Only if stop is supported
        if not self._supports_stop:
            _LOGGER.debug("Stop not supported for cover %s", self._serial_number[-8:])
            return
            
        try:
            # For Easywave Receiver with button_config, send command directly via RX11
            if self._button_config and "stop" in self._button_config:
                button_code = self._button_config["stop"]
                _LOGGER.info("📤 Easywave Receiver Cover STOP: serial=%s, button=%d", 
                           self._serial_number[-8:], button_code)
                
                # Send command via RX11
                success = await self._send_ew_command(button_code)
                
                if success:
                    _LOGGER.info("✅ Cover %s STOP command sent successfully", self._serial_number[-8:])
                    # Update state to show "Gestoppt"
                    self._attr_is_closing = False
                    self._attr_is_opening = False
                    self._stopped = True
                    # Track last command
                    from datetime import datetime
                    self._last_command = "STOP"
                    self._last_command_time = datetime.now()
                    self.async_write_ha_state()
                else:
                    _LOGGER.warning("❌ Failed to send STOP command for cover %s", self._serial_number[-8:])
                return
            
            # Fallback: Try device instance for other device types
            device_instance = None
            if hasattr(self.coordinator.transceiver, '_device_instances'):
                device_instance = self.coordinator.transceiver._device_instances.get(self._serial_number)
            
            if device_instance and hasattr(device_instance, 'stop_cover'):
                self._attr_is_closing = False
                self._attr_is_opening = False
                self.async_write_ha_state()
                
                success = await device_instance.stop_cover(self._channel)
                if success:
                    _LOGGER.debug("Cover %s channel %d stopped successfully", 
                                self._serial_number, self._channel)
                else:
                    _LOGGER.warning("Failed to stop cover %s channel %d", 
                                  self._serial_number, self._channel)
        except Exception as e:
            _LOGGER.error("Error stopping cover %s channel %d: %s", 
                         self._serial_number, self._channel, e)
            self._attr_is_closing = False
            self._attr_is_opening = False
            self.async_write_ha_state()
    
    async def _send_ew_command(self, button_code: int) -> bool:
        """Send EW command via Coordinator (which routes to RX11 for EW receivers)."""
        try:
            # Use coordinator.send_command like the switch entity does
            # This properly routes to send_command_to_receiver for EW receivers
            command = bytes([button_code])
            success = await self.coordinator.send_command(
                self._serial_number,
                command,
                action="cover_command"
            )
            _LOGGER.debug("EW cover command sent: serial=%s, button=%d, success=%s",
                         self._serial_number[-8:], button_code, success)
            return success
        except Exception as e:
            _LOGGER.error("Error sending EW cover command: %s", e)
            return False
    
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes for EW receiver cover."""
        attrs = {
            "serial_number": self._serial_number,
        }
        
        # Add last command info if available
        if self._last_command:
            attrs["last_command"] = self._last_command
        if self._last_command_time:
            attrs["last_command_time"] = self._last_command_time.strftime("%d.%m.%Y %H:%M:%S")
        
        return attrs
    
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Update cover state based on coordinator data
        device_data = self.coordinator.devices.get(self._serial_number, {})
        
        # Safely get channel data
        if not isinstance(device_data, dict):
            self.async_write_ha_state()
            return
            
        # Check for motor state updates
        channels = device_data.get("channels", {})
        
        # Handle both dict and non-dict channel data
        if isinstance(channels, dict):
            channel_data = channels.get(str(self._channel), {})
        else:
            # If not a dict, skip processing
            channel_data = {}
        
        # Update position if available (only if channel_data is a dict)
        if isinstance(channel_data, dict) and "position" in channel_data:
            position = channel_data["position"]
            self._attr_is_closed = position <= 5  # Consider closed if position <= 5%
        
        self.async_write_ha_state()


class EldatEWneoCover(EldatEntity, CoverEntity):
    """EWneo-Motor entity with bidirectional EWB_CHANGE_STATE control for motors."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        super().__init__(coordinator, serial_number, device_info)
        self._entity_spec = entity_spec
        self._channel = int(entity_spec.get("channel", 1))
        self._available = True
        
        # EWneo specific attributes
        self._gateway_serial = device_info.get("gateway_serial")
        self._device_type_code = device_info.get("device_type_code", 0)
        
        # Cover state
        self._is_opening = False
        self._is_closing = False
        self._current_cover_position = None  # None = unknown, 0-100 = position
        self._target_cover_position = None
        
        # Motor status from EWB specification
        self._motor_status_code = 126  # Default: stopped
        self._runtime_measured = False
        self._tilt_measured = False
        self._recent_tilt = False
        self._auto_tilt = False
        self._terrace_function = False
        self._stored_position = 0
        
        # Runtime measurement activation tracking
        self._runtime_ever_measured = False  # Tracks if runtime measurement was ever detected
        
        # Reachability tracking
        self._reachable = True  # Device reachability status
        self._last_seen = None  # Last successful communication timestamp
        
        _LOGGER.info("🔧 Initializing EWneo-Motor: %s (gateway: %s, type_code: 0x%02X)", 
                    serial_number[-8:], self._gateway_serial[-8:] if self._gateway_serial else "None", self._device_type_code)
        
        # Initialize state from device's initial_state if available
        initial_state = device_info.get("initial_state", {})
        if initial_state.get("type") == "cover":
            self._current_cover_position = initial_state.get("position")
            self._runtime_measured = initial_state.get("runtime_measured", False)
            _LOGGER.info("🎯 EWneo-Motor %s: Loaded initial state: position=%s, runtime_measured=%s", 
                        serial_number[-8:], self._current_cover_position, self._runtime_measured)
        
        # Set up entity attributes
        translation_key = entity_spec.get("translation_key")
        if translation_key:
            self._attr_translation_key = translation_key
            self._attr_name = None  # Let HA use translation_key
        else:
            self._attr_name = entity_spec.get("name", f"EWneo-Motor {serial_number}")
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_ewneo_cover_{self._channel}")
        self._attr_device_class = CoverDeviceClass.SHUTTER
        
        # Dynamic icons based on open/closed state
        self._icon_open = entity_spec.get("icon_open", "mdi:window-shutter-open")
        self._icon_closed = entity_spec.get("icon_closed", "mdi:window-shutter")
        self._base_icon = entity_spec.get("icon", "mdi:window-shutter")
        
        # Set supported features based on runtime measurement
        self._update_supported_features()
        
        _LOGGER.info("✅ EWneo-Motor entity initialized: %s (%s)", self._attr_name, self._attr_unique_id)
        
    async def _async_query_initial_state(self) -> None:
        """Query initial motor state to check for runtime measurement capability."""
        max_retries = 2
        
        for attempt in range(1, max_retries + 1):
            try:
                _LOGGER.info("🔍 EWneo-Motor %s: Querying initial state (attempt %d/%d)", 
                            self._serial_number[-8:], attempt, max_retries)
                
                # Use coordinator to perform EwbQueryState
                result = await self.coordinator._query_ewneo_state(
                    self._gateway_serial,
                    self._serial_number
                )
                
                if result:
                    _LOGGER.info("✅ EWneo-Motor %s: Initial query successful, state will be processed via event", self._serial_number[-8:])
                    # Mark device as reachable and update last_seen timestamp
                    from datetime import datetime
                    self._reachable = True
                    self._last_seen = datetime.now()
                    return  # Success - exit retry loop
                else:
                    _LOGGER.warning("⚠️ EWneo-Motor %s: Initial state query failed (attempt %d/%d)", 
                                   self._serial_number[-8:], attempt, max_retries)
                    self._reachable = False
                    await self.coordinator.report_ewneo_communication_failure(self._serial_number)
                    
            except Exception as e:
                _LOGGER.error("❌ EWneo-Motor %s: Error during initial state query (attempt %d/%d): %s", 
                             self._serial_number[-8:], attempt, max_retries, e)
                self._reachable = False
                await self.coordinator.report_ewneo_communication_failure(self._serial_number)
        
    def _update_supported_features(self) -> None:
        """Update supported features based on motor capabilities."""
        features = (
            CoverEntityFeature.OPEN |
            CoverEntityFeature.CLOSE |
            CoverEntityFeature.STOP
        )
        
        # Add position control if runtime measurement was ever detected
        if self._runtime_ever_measured:
            features |= CoverEntityFeature.SET_POSITION
            
        # Add tilt control if tilt measurement is available
        if self._tilt_measured:
            features |= CoverEntityFeature.SET_TILT_POSITION
            
        self._attr_supported_features = features

    @property
    def current_cover_position(self) -> Optional[int]:
        """Return the current position of cover where 0 means closed and 100 is fully open.
        
        Returns None if:
        - No runtime measurement has been done
        - Position is temporarily unknown (during calibration or 120s movements)
        """
        return self._current_cover_position
    
    @property
    def target_cover_position(self) -> Optional[int]:
        """Return the target position of cover where 0 means closed and 100 is fully open."""
        return self._target_cover_position
    
    @property
    def icon(self) -> str:
        """Return dynamic icon based on cover state."""
        # Moving states - use base icon
        if self._is_opening or self._is_closing:
            return self._base_icon
        # Closed state
        if self._current_cover_position == 0:
            return self._icon_closed
        # Open or partially open
        if self._current_cover_position is not None and self._current_cover_position > 0:
            return self._icon_open
        # Unknown state - use base icon
        return self._base_icon
    
    @property 
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        return self._is_opening
    
    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        return self._is_closing
    
    @property
    def is_closed(self) -> Optional[bool]:
        """Return if the cover is closed.
        
        Returns None if position is unknown AND motor is not moving.
        When motor is moving, return False (not closed).
        When motor is stopped without position info, return None.
        """
        # If we have position information, use it
        if self._current_cover_position is not None:
            return self._current_cover_position == 0
        
        # If motor is moving, it's definitely not in a closed state
        if self._is_opening or self._is_closing:
            return False
            
        # Motor stopped but no position info - return None (unknown)
        return None
    
    @property
    def state(self) -> str | None:
        """Return the state of the cover with stopped support.
        
        Shows 'stopped' when motor is stopped without position info,
        or position percentage when runtime measurement is available.
        """
        # Check for moving states first
        if self._is_opening:
            return "opening"
        if self._is_closing:
            return "closing"
        
        # If we have position information, use standard open/closed states
        if self._current_cover_position is not None:
            if self._current_cover_position == 0:
                return "closed"
            else:
                return "open"
        
        # Motor stopped without position info - show "stopped" instead of unknown
        # motor_status_code 126 = stopped
        if self._motor_status_code == 126 or (not self._is_opening and not self._is_closing):
            return "stopped"
        
        return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        attrs = {
            "serial_number": self._serial_number,
            "motor_status_code": self._motor_status_code,
            "runtime_measured": self._runtime_measured,
            "tilt_measured": self._tilt_measured,
            "terrace_function": self._terrace_function,
            "device_type_code": f"0x{self._device_type_code:02X}",
            "supports_tilt": self._tilt_measured,
            "reachable": self._reachable,
        }
        
        # Add EWneo index from device info
        device_data = self.coordinator.devices.get(self._serial_number, {})
        ewneo_index = device_data.get("ewneo_index")
        if ewneo_index is not None:
            attrs["ewneo_index"] = ewneo_index
        
        # Add last_seen timestamp if available (lokale Zeit)
        if self._last_seen:
            attrs["last_seen"] = self._last_seen.strftime("%d.%m.%Y %H:%M:%S")
        
        # Add position info if available
        if self._runtime_measured:
            if self._current_cover_position is not None:
                attrs["position_status"] = "known"
            else:
                if self._motor_status_code in [117, 118]:
                    attrs["position_status"] = "calibrating"
                elif self._motor_status_code in [120, 121]:
                    attrs["position_status"] = "runtime_movement"
                else:
                    attrs["position_status"] = "temporarily_unknown"
        else:
            attrs["position_status"] = "no_runtime_measurement"
        
        return attrs
    
    @property
    def available(self) -> bool:
        """Return if entity is available - follows RX11 connection status.
        
        EWneo covers inherit the RX11 transceiver connection status via via_device 
        linkage.
        """
        return self._is_rx11_connected()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, set up event listeners."""
        await super().async_added_to_hass()
        
        # Add NFILTER for gateway serial to enable bidirectional communication
        if self._gateway_serial:
            try:
                filter_success = await self.coordinator.transceiver.rx11_ewb_add_filter(self._gateway_serial)
                if filter_success:
                    _LOGGER.info("✅ EWneo-Motor %s: Added NFILTER for gateway %s", 
                                self._serial_number[-8:], self._gateway_serial[-8:])
                else:
                    _LOGGER.warning("⚠️ EWneo-Motor %s: Failed to add NFILTER for gateway %s", 
                                   self._serial_number[-8:], self._gateway_serial[-8:])
            except Exception as e:
                _LOGGER.error("❌ EWneo-Motor %s: Error adding NFILTER: %s", self._serial_number[-8:], e)
        else:
            _LOGGER.warning("⚠️ EWneo-Motor %s: No gateway serial configured, bidirectional communication may not work", 
                           self._serial_number[-8:])
        
        # Listen for EWneo state update events
        def handle_ewneo_state_update(event):
            """Handle EWneo state update events."""
            if event.data.get("serial_number") == self._serial_number:
                _LOGGER.info("🔄 EWneo-Motor %s: Received state update event", self._serial_number[-8:])
                parsed_state = event.data.get("parsed_state", {})
                if parsed_state and parsed_state.get("type") == "cover":
                    # Use thread-safe add_job to schedule state update
                    self.hass.add_job(self._async_update_state_from_parsed, parsed_state)
        
        # Register the event listener
        self.hass.bus.async_listen("eldat_ewneo_state_update", handle_ewneo_state_update)
        _LOGGER.debug("🔗 EWneo-Motor %s: Registered state update event listener", self._serial_number[-8:])
        
        # Perform initial state query to check for runtime measurement
        self.hass.async_create_task(self._async_query_initial_state())
    
    async def _async_update_state_from_parsed(self, parsed_state: Dict[str, Any]) -> None:
        """Update cover state from parsed EWneo state (async for thread safety)."""
        old_position = self._current_cover_position
        old_is_opening = self._is_opening
        old_is_closing = self._is_closing
        old_features = self._attr_supported_features
        old_runtime_measured = self._runtime_measured
        old_runtime_ever_measured = self._runtime_ever_measured
        
        # Update motor capabilities FIRST
        new_runtime_measured = parsed_state.get("runtime_measured", False)
        new_supports_position = parsed_state.get("supports_position", False)
        
        # Use both runtime_measured AND supports_position flags for detection
        if new_runtime_measured or new_supports_position:
            if not self._runtime_ever_measured:
                self._runtime_ever_measured = True
                _LOGGER.info("🎯 EWneo-Motor %s: Runtime measurement DETECTED and ACTIVATED! Position control now available (from state update)", self._serial_number[-8:])
            self._runtime_measured = True
        else:
            # Only update if we have explicit information
            if "runtime_measured" in parsed_state:
                self._runtime_measured = new_runtime_measured
        
        # Update position (only if available and valid)
        if parsed_state.get("position_available", False) and "position" in parsed_state:
            self._current_cover_position = parsed_state["position"]
            # If we have a position, runtime measurement must be working
            if not self._runtime_ever_measured:
                self._runtime_ever_measured = True
                self._runtime_measured = True
                _LOGGER.info("🎯 EWneo-Motor %s: Runtime measurement DETECTED from position data! Position control activated", self._serial_number[-8:])
        elif not self._runtime_measured:
            # No runtime measurement - position unknown
            self._current_cover_position = None
        # Keep existing position if runtime measured but position temporarily unknown
        
        # Update target position
        if "target_position" in parsed_state:
            self._target_cover_position = parsed_state["target_position"]
        
        # Update movement state based on motor status
        self._is_opening = parsed_state.get("is_opening", False)
        self._is_closing = parsed_state.get("is_closing", False)
        
        # Update other capabilities
        self._tilt_measured = parsed_state.get("tilt_measured", self._tilt_measured)
        self._terrace_function = parsed_state.get("terrace_function", self._terrace_function)
        
        # Update motor status info
        self._motor_status_code = parsed_state.get("motor_status_code", self._motor_status_code)
        
        # Update supported features based on capabilities
        self._update_supported_features()
        
        # Log comprehensive state changes including runtime measurement status
        state_changed = (old_position != self._current_cover_position or
                        old_is_opening != self._is_opening or 
                        old_is_closing != self._is_closing or
                        old_features != self._attr_supported_features or
                        old_runtime_measured != self._runtime_measured or
                        old_runtime_ever_measured != self._runtime_ever_measured)
        
        if state_changed:
            motor_status = parsed_state.get("motor_status", "unknown")
            
            # Enhanced logging with runtime measurement status
            _LOGGER.info("🎯 Easywave neo Motor %s: State update - Status: %s, Runtime: %s->%s, Ever: %s->%s, Position: %s, Features: %s", 
                        self._serial_number[-8:], motor_status, 
                        old_runtime_measured, self._runtime_measured,
                        old_runtime_ever_measured, self._runtime_ever_measured,
                        self._current_cover_position,
                        "Pos+Tilt" if self._tilt_measured else "Pos" if self._runtime_ever_measured else "Basic")
            
            # Enhanced logging with position info
            if self._runtime_measured:
                if self._current_cover_position is not None:
                    pos_text = f"{self._current_cover_position}%"
                else:
                    pos_text = "unknown (calibrating or 120s mode)"
                    
                target_text = ""
                if self._target_cover_position is not None:
                    target_text = f", Target: {self._target_cover_position}%"
                    
                _LOGGER.debug("🎯 Easywave neo Motor %s: %s - Position: %s%s [Runtime: ✓, Features: %s]", 
                            self._serial_number[-8:], motor_status, pos_text, target_text,
                            "Pos+Tilt" if self._tilt_measured else "Pos")
            else:
                _LOGGER.debug("🎯 Easywave neo Motor %s: %s - Positionless mode [No runtime measurement]", 
                            self._serial_number[-8:], motor_status)
            
            # Mark device as reachable and update last_seen timestamp
            from datetime import datetime
            self._reachable = True
            self._last_seen = datetime.now()
            
            self.async_write_ha_state()

    def _create_motor_position_command(self, position: int, tilt_after: bool = False) -> tuple[int, list]:
        """Create position command for Easywave neo Motor according to EWB_CHANGE_STATE specification.
        
        Args:
            position: 0-100 where 0=open, 100=closed
            tilt_after: Whether to tilt slats after positioning (if tilt measurement done)
        """
        if not self._runtime_ever_measured and not self._runtime_measured:
            _LOGGER.warning("EWneo cover %s: Position control requires runtime measurement to be activated first (runtime=%s, ever=%s)", 
                           self._serial_number, self._runtime_measured, self._runtime_ever_measured)
            return None, None
            
        if not (0 <= position <= 100):
            _LOGGER.warning("EWneo cover %s: Invalid position %d, must be 0-100", self._serial_number, position)
            return None, None
        
        # Mode 0: Position control
        # Bits 31-25: Desired position (0=open, 100=closed)
        # Bit 24: Tilt after positioning (if tilt measurement done)
        # Bits 23-0: Reserved
        
        state_word = (position << 25)
        
        if tilt_after and self._tilt_measured:
            state_word |= (1 << 24)  # Set tilt bit
        
        # Convert to 4 bytes in big-endian order
        state_bytes = [
            (state_word >> 24) & 0xFF,
            (state_word >> 16) & 0xFF,
            (state_word >> 8) & 0xFF,
            state_word & 0xFF
        ]
        
        _LOGGER.debug("EWneo cover %s: Position command - position: %d%% (0=open), tilt_after: %s, runtime_measured: %s", 
                     self._serial_number, position, tilt_after, self._runtime_measured)
        
        return (0, state_bytes)
    
    def _create_motor_movement_command(self, command: str) -> tuple[int, list]:
        """Create movement command for Easywave neo Motor.
        
        Args:
            command: 'stop', 'stop_tilt', 'open_runtime', 'close_runtime', 'open_120s', 'close_120s'
        """
        # Mode 0: Motor commands
        # Bits 31-25: Special command codes
        command_codes = {
            "stop": 126,         # Stop immediately
            "stop_tilt": 119,   # Stop and tilt to horizontal (if tilt measurement done)
            "open_runtime": 120, # Open for runtime (if runtime measurement done)
            "close_runtime": 121,# Close for runtime (if runtime measurement done) 
            "open_120s": 122,   # Open for 120 seconds (independent of runtime measurement)
            "close_120s": 123   # Close for 120 seconds (independent of runtime measurement)
        }
        
        if command not in command_codes:
            _LOGGER.warning("EWneo cover %s: Unknown command '%s'", self._serial_number, command)
            return None, None
        
        command_code = command_codes[command]
        
        # Check if command requires specific measurements
        if command == "stop_tilt" and not self._tilt_measured:
            _LOGGER.warning("EWneo cover %s: Tilt stop requires tilt measurement", self._serial_number)
            command_code = 126  # Fallback to simple stop
        elif command in ["open_runtime", "close_runtime"] and not self._runtime_measured:
            _LOGGER.info("EWneo cover %s: Using runtime commands (120/121) for position tracking", 
                        self._serial_number)
            # Still use runtime codes - they work even without prior measurement
            # and enable position tracking once motor completes full travel
        
        # Create state word (big-endian)
        state_word = (command_code << 25)
        
        # Convert to 4 bytes in big-endian order
        state_bytes = [
            (state_word >> 24) & 0xFF,
            (state_word >> 16) & 0xFF,
            (state_word >> 8) & 0xFF,
            state_word & 0xFF
        ]
        
        _LOGGER.debug("EWneo cover %s: Movement command - %s (code: %d)", 
                     self._serial_number, command, command_code)
        
        return (0, state_bytes)
    
    def _create_terrace_function_command(self, enable: bool) -> tuple[int, list]:
        """Create terrace function command for Easywave neo Motor.
        
        Args:
            enable: True to enable terrace function, False to disable
        """
        # Mode 2: Terrace function control
        # Bits 31-24: 1=enable, 0=disable
        # Bits 23-0: Reserved
        
        state_word = (1 << 24) if enable else 0
        
        # Convert to 4 bytes in big-endian order
        state_bytes = [
            (state_word >> 24) & 0xFF,
            (state_word >> 16) & 0xFF,
            (state_word >> 8) & 0xFF,
            state_word & 0xFF
        ]
        
        _LOGGER.debug("EWneo cover %s: Terrace function command - %s", 
                     self._serial_number, "enable" if enable else "disable")
        
        return (2, state_bytes)

    async def _send_ewb_change_state(self, mode: int, state_bytes: list) -> bool:
        """Send EWB_CHANGE_STATE command to Easywave neo Motor."""
        if not self._gateway_serial:
            _LOGGER.error("EWneo cover %s: No gateway serial available for EWB command", self._serial_number)
            return False
        
        try:
            _LOGGER.info("🔄 Sending EWB_CHANGE_STATE to EWneo cover %s: mode=%d, state_bytes=%s", 
                        self._serial_number, mode, [f"0x{b:02X}" for b in state_bytes])
            
            # Send command via coordinator's transceiver with automatic retry on failure
            retry_attempted = False
            result = await self.coordinator.transceiver.rx11_ewb_change_state(
                self._gateway_serial, self._serial_number, mode, state_bytes
            )
            
            # Check for error responses
            if result and isinstance(result, tuple) and len(result) == 3 and isinstance(result[0], str) and result[0].startswith("ERR_"):
                error_type = result[0]
                
                # Automatic retry for RF_TIMEOUT (once, without delay)
                if error_type == "ERR_RF_TIMEOUT" and not retry_attempted:
                    retry_attempted = True
                    _LOGGER.info("🔄 EWneo cover %s: Timeout - automatischer Wiederholungsversuch...", self._serial_number[-8:])
                    result = await self.coordinator.transceiver.rx11_ewb_change_state(
                        self._gateway_serial, self._serial_number, mode, state_bytes
                    )
                    # Re-check result after retry
                    if result and isinstance(result, tuple) and len(result) == 3 and isinstance(result[0], str) and result[0].startswith("ERR_"):
                        error_type = result[0]
                    else:
                        error_type = None  # Retry succeeded
                
                if error_type == "ERR_RF_TIMEOUT":
                    error_type, receiver_serial, gateway_serial = result
                    # Mark device as unreachable but keep it controllable
                    self._reachable = False
                    self.async_write_ha_state()
                    
                    # Report failure to coordinator (handles counting and notification after 2 failures)
                    await self.coordinator.report_ewneo_communication_failure(self._serial_number)
                    
                    _LOGGER.debug("⚠️ EWneo cover %s communication failure reported", self._serial_number[-8:])
                    return False
                else:
                    _LOGGER.error("❌ EWneo cover %s Fehler: %s", self._serial_number[-8:], error_type)
                    return False
            elif result:
                response_mode, response_state_bytes = result
                _LOGGER.info("✅ EWneo cover %s: Received EWB response - mode=%d, state_bytes=%s", 
                            self._serial_number, response_mode, [f"0x{b:02X}" for b in response_state_bytes])
                
                # Report success to coordinator (resets failure counter, dismisses notification)
                await self.coordinator.report_ewneo_communication_success(self._serial_number)
                
                # Ensure we have 4 bytes for proper parsing
                if len(response_state_bytes) >= 4:
                    # Parse the 4-byte response to update local state immediately
                    parsed_state = self.coordinator._parse_ewneo_state(
                        self._device_type_code, response_state_bytes[:4], "ewneo_motor"
                    )
                    
                    if parsed_state and parsed_state.get("type") == "cover":
                        # Update state immediately and synchronously
                        await self._async_update_state_from_parsed(parsed_state)
                        _LOGGER.info("🎯 EWneo cover %s: State updated from EWB response - position=%s, runtime=%s, status=%s", 
                                    self._serial_number, 
                                    parsed_state.get("position"),
                                    parsed_state.get("runtime_measured"),
                                    parsed_state.get("motor_status"))
                    else:
                        _LOGGER.warning("⚠️ EWneo cover %s: Failed to parse EWB response state", self._serial_number)
                        
                    # Also fire an event for consistency with other state updates
                    self.hass.bus.async_fire(
                        "eldat_ewneo_state_update",
                        {
                            "serial_number": self._serial_number,
                            "device_id": self._serial_number,
                            "parsed_state": parsed_state,
                            "state_bytes": response_state_bytes[:4],
                            "source": "ewb_change_state_response",
                            "timestamp": None,
                        }
                    )
                else:
                    _LOGGER.warning("⚠️ EWneo cover %s: EWB response too short (%d bytes, expected 4+)", 
                                   self._serial_number, len(response_state_bytes))
                
                # Mark device as reachable and update timestamp
                from datetime import datetime
                self._reachable = True
                self._last_seen = datetime.now()
                
                # Force immediate Home Assistant state update
                self.async_write_ha_state()
                return True
            else:
                _LOGGER.error("❌ EWneo cover %s: EWB_CHANGE_STATE command failed", self._serial_number)
                return False
                
        except Exception as e:
            _LOGGER.error("❌ Failed to set EWneo cover %s state: %s", self._serial_number, e)
            return False

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        if self._runtime_measured:
            # Use runtime-based opening
            mode, state_bytes = self._create_motor_movement_command("open_runtime")
        else:
            # Fallback to 120s opening
            mode, state_bytes = self._create_motor_movement_command("open_120s")
        
        if mode is not None:
            success = await self._send_ewb_change_state(mode, state_bytes)
            if success:
                self._is_opening = True
                self._is_closing = False
                self.async_write_ha_state()
    
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close cover."""
        if self._runtime_measured:
            # Use runtime-based closing
            mode, state_bytes = self._create_motor_movement_command("close_runtime")
        else:
            # Fallback to 120s closing
            mode, state_bytes = self._create_motor_movement_command("close_120s")
        
        if mode is not None:
            success = await self._send_ewb_change_state(mode, state_bytes)
            if success:
                self._is_opening = False
                self._is_closing = True
                self.async_write_ha_state()
    
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        # Check for tilt parameter
        tilt = kwargs.get("tilt", False)
        
        if tilt and self._tilt_measured:
            mode, state_bytes = self._create_motor_movement_command("stop_tilt")
        else:
            mode, state_bytes = self._create_motor_movement_command("stop")
        
        if mode is not None:
            success = await self._send_ewb_change_state(mode, state_bytes)
            if success:
                self._is_opening = False
                self._is_closing = False
                self.async_write_ha_state()
    
    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Tilt the cover to horizontal position (open tilt)."""
        if not self._tilt_measured:
            _LOGGER.warning("EWneo cover %s: Tilt control requires tilt measurement", self._serial_number)
            return
            
        # For EWneo, "open tilt" means tilt to horizontal
        mode, state_bytes = self._create_motor_movement_command("stop_tilt")
        if mode is not None:
            success = await self._send_ewb_change_state(mode, state_bytes)
            if success:
                self._is_opening = False
                self._is_closing = False
                self.async_write_ha_state()
    
    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close tilt (return to normal position)."""
        if not self._tilt_measured:
            _LOGGER.warning("EWneo cover %s: Tilt control requires tilt measurement", self._serial_number)
            return
            
        # For EWneo, "close tilt" means return to normal (no tilt)
        mode, state_bytes = self._create_motor_movement_command("stop")
        if mode is not None:
            success = await self._send_ewb_change_state(mode, state_bytes)
            if success:
                self._is_opening = False
                self._is_closing = False  
                self.async_write_ha_state()
    
    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        position = kwargs.get(ATTR_POSITION)
        if position is None:
            _LOGGER.warning("EWneo cover %s: No position specified", self._serial_number)
            return
        
        # Check if runtime measurement is available
        if not self._runtime_measured and not self._runtime_ever_measured:
            _LOGGER.warning("EWneo cover %s: Position control requires runtime measurement. Current status: runtime=%s, ever=%s", 
                         self._serial_number, self._runtime_measured, self._runtime_ever_measured)
            
            # Try to query current state to check for runtime measurement
            _LOGGER.info("🔍 EWneo cover %s: Attempting to query current state to detect runtime measurement", self._serial_number)
            query_result = await self.coordinator._query_ewneo_state(self._gateway_serial, self._serial_number)
            
            if query_result:
                _LOGGER.info("✅ EWneo cover %s: State query sent, waiting for response to update runtime status", self._serial_number)
                # Give some time for the response to be processed
                await asyncio.sleep(0.5)
            
            # Check again after query
            if not self._runtime_measured and not self._runtime_ever_measured:
                _LOGGER.error("EWneo cover %s: Runtime measurement still not detected. Fallback to simple open/close based on position", self._serial_number)
                # Fallback to simple open/close based on position
                if position >= 50:
                    await self.async_open_cover()
                else:
                    await self.async_close_cover()
                return
            else:
                _LOGGER.info("✅ EWneo cover %s: Runtime measurement detected after query! Proceeding with position control", self._serial_number)
        
        # Check for tilt parameter
        tilt_after = kwargs.get("tilt_after", False)
        
        # Convert Home Assistant position (0=closed, 100=open) to protocol position (0=open, 100=closed)
        protocol_position = 100 - position
        
        _LOGGER.info("EWneo cover %s: Setting position to %d%% (protocol: %d%%), tilt_after=%s", 
                     self._serial_number, position, protocol_position, tilt_after)
        
        mode, state_bytes = self._create_motor_position_command(protocol_position, tilt_after)
        if mode is not None:
            self._target_cover_position = position
            # Set movement state based on position difference
            if self._current_cover_position is not None:
                if position > self._current_cover_position:
                    self._is_opening = True
                    self._is_closing = False
                elif position < self._current_cover_position:
                    self._is_opening = False
                    self._is_closing = True
                else:
                    self._is_opening = False
                    self._is_closing = False
            
            self.async_write_ha_state()  # Update UI immediately
            await self._send_ewb_change_state(mode, state_bytes)
    
    async def async_set_terrace_function(self, enable: bool) -> bool:
        """Enable or disable terrace function."""
        mode, state_bytes = self._create_terrace_function_command(enable)
        if mode is not None:
            success = await self._send_ewb_change_state(mode, state_bytes)
            if success:
                self._terrace_function = enable
                _LOGGER.info("EWneo cover %s: Terrace function %s", 
                            self._serial_number, "enabled" if enable else "disabled")
            return success
        return False
    
    def process_state_update(self, telegram_data: Dict[str, Any]) -> None:
        """Process incoming state updates from EWB telegrams."""
        try:
            parsed_state = self.coordinator._parse_ewneo_state(
                self._device_type_code, 
                telegram_data.get("state_bytes", []), 
                "ewneo_cover"
            )
            
            if parsed_state and parsed_state.get("type") == "cover":
                # Use thread-safe add_job for state update
                self.hass.add_job(self._async_update_state_from_parsed, parsed_state)
                    
        except Exception as e:
            _LOGGER.error("Error processing EWneo cover %s state update: %s", 
                         self._serial_number, e)


class EldatEWneoDualMotorCover(EldatEntity, CoverEntity):
    """EWneo-DualMotor cover entity with individual motor control and runtime measurement activation."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        super().__init__(coordinator, serial_number, device_info)
        self._entity_spec = entity_spec
        # Smart channel handling: entity_spec['channel'] can be 0-based OR 1-based
        # For dual motors: 0-based = (0,1), 1-based = (1,2)
        # Detect and normalize to 1-based (1,2)
        raw_channel = int(entity_spec.get("channel", 0))
        unique_id = entity_spec.get("unique_id", "")
        
        # Check if 0-based by looking for _ch0 or _ch1 in unique_id
        if "_ch0" in unique_id or "_ch1" in unique_id:
            # 0-based detected: ch0 or ch1 suffix
            self._channel = raw_channel + 1  # 0->1, 1->2
            _LOGGER.warning("🆕 Dual Motor: Detected 0-based (unique_id=%s), converting %d -> %d", 
                           unique_id, raw_channel, self._channel)
        elif raw_channel == 0:
            # Channel 0 without _ch0/_ch1 in unique_id: likely missing, use 1 as default
            self._channel = 1
            _LOGGER.warning("⚠️ Dual Motor: Channel 0 detected, defaulting to 1")
        else:
            # raw_channel >= 1: Assume 1-based
            self._channel = raw_channel
        
        self._available = True
        
        _LOGGER.warning("🆕 Dual Motor: entity_spec['channel']=%s (raw) -> self._channel=%d (normalized)", 
                       raw_channel, self._channel)
        
        # EWneo specific attributes
        self._gateway_serial = device_info.get("gateway_serial")
        self._device_type_code = device_info.get("device_type_code", 0)
        
        # Cover state for specific channel
        self._is_opening = False
        self._is_closing = False
        self._current_cover_position = None  # None = unknown, 0-100 = position
        self._target_cover_position = None
        
        # Motor status from EWB specification for this channel
        self._motor_status_code = 126  # Default: stopped
        self._runtime_measured = False
        self._tilt_measured = False
        self._recent_tilt = False
        self._auto_tilt = False
        self._terrace_function = False
        self._stored_position = 0
        
        # Runtime measurement activation tracking
        self._runtime_ever_measured = False  # Tracks if runtime measurement was ever detected
        
        # Reachability tracking
        self._reachable = True
        self._last_seen = None
        
        _LOGGER.info("🔧 Initializing EWneo-DualMotor cover CH%d: %s (gateway: %s, type_code: 0x%02X)", 
                    self._channel, serial_number[-8:], self._gateway_serial[-8:] if self._gateway_serial else "None", self._device_type_code)
        
        # Set up entity attributes
        # Store translation_key for dynamic name resolution AND state translation
        self._translation_key = entity_spec.get("translation_key")
        self._attr_translation_key = self._translation_key  # For HA state translations
        self._static_name = entity_spec.get("name", f"EWneo DualMotor CH{self._channel} {serial_number}")
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_ewneo_dual_motor_ch{self._channel}")
        self._attr_device_class = CoverDeviceClass.SHUTTER
        
        # Dynamic icons based on open/closed state
        self._icon_open = entity_spec.get("icon_open", "mdi:window-shutter-open")
        self._icon_closed = entity_spec.get("icon_closed", "mdi:window-shutter")
        self._base_icon = entity_spec.get("icon", "mdi:window-shutter")
        
        # Initially no position features - will be enabled after QUERY_STATE response
        self._update_supported_features()
        
        _LOGGER.info("✅ EWneo-DualMotor cover entity CH%d initialized: %s (%s)", self._channel, self._translation_key or self._static_name or f"Channel {self._channel}", self._attr_unique_id)
        
    async def _async_query_initial_state(self) -> None:
        """Query initial motor state for this specific channel to check for runtime measurement capability.
        
        For multi-channel motors, we query each channel individually with Mode 2/10/18/26
        to get the full state including persistent runtime measurement information.
        Mode 0 only provides summary status without runtime_measured flag.
        """
        max_retries = 2
        
        # Map channel to query mode: CH1=2, CH2=10, CH3=18, CH4=26
        channel_mode_map = {1: 2, 2: 10, 3: 18, 4: 26}
        query_mode = channel_mode_map.get(self._channel, 0)
        
        for attempt in range(1, max_retries + 1):
            try:
                _LOGGER.info("🔍 EWneo-DualMotor CH%d %s: Querying initial state with Mode %d (attempt %d/%d)", 
                            self._channel, self._serial_number[-8:], query_mode, attempt, max_retries)
                
                # Use coordinator to perform EwbQueryState with channel-specific mode
                result = await self.coordinator._query_ewneo_state_with_mode(
                    self._gateway_serial,
                    self._serial_number,
                    query_mode
                )
                
                if result:
                    _LOGGER.info("✅ EWneo-DualMotor CH%d %s: Mode %d query successful, state will be processed via event", 
                                self._channel, self._serial_number[-8:], query_mode)
                    # Mark device as reachable and update last_seen timestamp
                    from datetime import datetime
                    self._reachable = True
                    self._last_seen = datetime.now()
                    return  # Success - exit retry loop
                else:
                    _LOGGER.warning("⚠️ EWneo-DualMotor CH%d %s: Mode %d state query failed (attempt %d/%d)", 
                                   self._channel, self._serial_number[-8:], query_mode, attempt, max_retries)
                    self._reachable = False
                    await self.coordinator.report_ewneo_communication_failure(self._serial_number)
                    
            except Exception as e:
                _LOGGER.error("❌ EWneo-DualMotor CH%d %s: Error during initial state query (attempt %d/%d): %s", 
                             self._channel, self._serial_number[-8:], attempt, max_retries, e)
                self._reachable = False
                await self.coordinator.report_ewneo_communication_failure(self._serial_number)
        
    def _update_supported_features(self) -> None:
        """Update supported features based on motor capabilities (DualMotor)."""
        features = (
            CoverEntityFeature.OPEN |
            CoverEntityFeature.CLOSE |
            CoverEntityFeature.STOP
        )
        
        # Add position control if runtime measurement was ever detected
        # (not just currently active - once measured, always available)
        if self._runtime_ever_measured:
            features |= CoverEntityFeature.SET_POSITION
            _LOGGER.debug("EWneo-DualMotor CH%d: Position control enabled (runtime_ever_measured=True)", self._channel)
        else:
            _LOGGER.debug("EWneo-DualMotor CH%d: Position control disabled (runtime_ever_measured=False)", self._channel)
            
        # Note: Tilt features removed - Easywave neo Motors don't support tilt control
            
        self._attr_supported_features = features

    @property
    def current_cover_position(self) -> Optional[int]:
        """Return the current position of cover where 0 means closed and 100 is fully open."""
        return self._current_cover_position
    
    @property
    def target_cover_position(self) -> Optional[int]:
        """Return the target position of cover where 0 means closed and 100 is fully open."""
        return self._target_cover_position
    
    @property
    def icon(self) -> str:
        """Return dynamic icon based on cover state."""
        # Moving states - use base icon
        if self._is_opening or self._is_closing:
            return self._base_icon
        # Closed state
        if self._current_cover_position == 0:
            return self._icon_closed
        # Open or partially open
        if self._current_cover_position is not None and self._current_cover_position > 0:
            return self._icon_open
        # Unknown state - use base icon
        return self._base_icon
    
    @property 
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        return self._is_opening
    
    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        return self._is_closing
    
    @property
    def is_closed(self) -> Optional[bool]:
        """Return if the cover is closed."""
        if self._current_cover_position is not None:
            return self._current_cover_position == 0
        return None
    
    @property
    def state(self) -> str | None:
        """Return the state of the cover with stopped support.
        
        Shows 'stopped' when motor is stopped without position info,
        or position percentage when runtime measurement is available.
        """
        # Check for moving states first
        if self._is_opening:
            return "opening"
        if self._is_closing:
            return "closing"
        
        # If we have position information, use standard open/closed states
        if self._current_cover_position is not None:
            if self._current_cover_position == 0:
                return "closed"
            else:
                return "open"
        
        # Motor stopped without position info - show "stopped" instead of unknown
        # motor_status_code 126 = stopped
        if self._motor_status_code == 126 or (not self._is_opening and not self._is_closing):
            return "stopped"
        
        return None

    @property
    def name(self) -> str | None:
        """Return the dynamically translated name."""
        if self._translation_key:
            # Get current language and translate dynamically
            lang = get_language(self.hass) if self.hass else "en"
            # Map translation_key to translation path
            key_map = {
                "channel_1": "entity.channel",
                "channel_2": "entity.channel",
                "channel_3": "entity.channel",
                "channel_4": "entity.channel",
            }
            if self._translation_key in key_map:
                translated = translate("entity.channel", lang)
                if translated != "entity.channel":
                    return translated.format(channel=self._channel)
                return f"Channel {self._channel}"
        elif self._device_type_code in [0x08, 0x09]:  # Dual or Quad motor
            # Dynamic translation for channel
            lang = get_language(self.hass) if self.hass else "en"
            translated = translate("entity.channel", lang)
            if translated != "entity.channel":
                return translated.format(channel=self._channel)
            return f"Channel {self._channel}"
        # Fallback to static name or None (single channel uses device name only)
        return self._static_name

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        attrs = {
            "serial_number": self._serial_number,
            "motor_channel": self._channel,
            "motor_status_code": self._motor_status_code,
            "runtime_measured": self._runtime_measured,
            "tilt_measured": self._tilt_measured,
            "terrace_function": self._terrace_function,
            "device_type_code": f"0x{self._device_type_code:02X}",
            "reachable": self._reachable,
        }
        
        # Add EWneo index from device info
        device_data = self.coordinator.devices.get(self._serial_number, {})
        ewneo_index = device_data.get("ewneo_index")
        if ewneo_index is not None:
            attrs["ewneo_index"] = ewneo_index
        
        if self._last_seen:
            attrs["last_seen"] = self._last_seen.strftime("%d.%m.%Y %H:%M:%S")
        
        if self._recent_tilt:
            attrs["recent_tilt"] = True
            
        if self._stored_position:
            attrs["stored_position"] = self._stored_position
        
            
        return attrs

    async def async_added_to_hass(self) -> None:
        """Handle entity added to hass."""
        await super().async_added_to_hass()
        
        # Add NFILTER for gateway serial to enable bidirectional communication
        if self._gateway_serial:
            try:
                filter_success = await self.coordinator.transceiver.rx11_ewb_add_filter(self._gateway_serial)
                if filter_success:
                    _LOGGER.info("✅ EWneo dual motor CH%d %s: Added NFILTER for gateway %s", 
                                self._channel, self._serial_number[-8:], self._gateway_serial[-8:])
                else:
                    _LOGGER.warning("⚠️ EWneo dual motor CH%d %s: Failed to add NFILTER for gateway %s", 
                                   self._channel, self._serial_number[-8:], self._gateway_serial[-8:])
            except Exception as e:
                _LOGGER.error("❌ EWneo dual motor CH%d %s: Error adding NFILTER: %s", 
                             self._channel, self._serial_number[-8:], e)
        else:
            _LOGGER.warning("⚠️ EWneo dual motor CH%d %s: No gateway serial configured", 
                           self._channel, self._serial_number[-8:])
        
        # Subscribe to EWneo state updates for dual motor devices
        async def handle_ewneo_state_update(event):
            try:
                event_data = event.data
                if event_data.get("serial_number") == self._serial_number:
                    parsed_state = event_data.get("parsed_state", {})
                    
                    # DualMotor handles BOTH:
                    # 1. Mode 0 (multi_cover): All motors in one 32-bit word - extract channel
                    # 2. Mode 2/10 (cover): Individual motor detail - use directly for matching channel
                    if parsed_state.get("type") == "multi_cover":
                        channel_state = self._extract_channel_state(parsed_state)
                        if channel_state:
                            _LOGGER.debug("🔄 DualMotor CH%d: Updating state from multi_cover", self._channel)
                            self.hass.add_job(self._async_update_state_from_parsed, channel_state)
                        else:
                            _LOGGER.warning("⚠️ DualMotor CH%d: Could not extract channel state", self._channel)
                    elif parsed_state.get("type") == "cover":
                        # Individual motor response (Mode 2/10) - check if it matches our channel
                        # Mode 2 = CH1, Mode 10 = CH2, Mode 18 = CH3, Mode 26 = CH4
                        event_mode = event_data.get("query_mode")
                        channel_mode_map = {2: 1, 10: 2, 18: 3, 26: 4}
                        event_channel = channel_mode_map.get(event_mode)
                        
                        if event_channel == self._channel:
                            _LOGGER.debug("🔄 DualMotor CH%d: Updating state from individual mode %s", self._channel, event_mode)
                            self.hass.add_job(self._async_update_state_from_parsed, parsed_state)
                        elif event_mode is None:
                            # Mode not specified in event - could be initial query response
                            # For safety, update only if we're the only channel or this looks like a direct response
                            _LOGGER.debug("🔄 DualMotor CH%d: Received cover state without mode, updating", self._channel)
                            self.hass.add_job(self._async_update_state_from_parsed, parsed_state)
            except Exception as e:
                _LOGGER.error("Error in dual motor CH%d state update handler: %s", self._channel, e)
        
        self.hass.bus.async_listen("eldat_ewneo_state_update", handle_ewneo_state_update)
        _LOGGER.debug("🔗 EWneo dual motor CH%d %s: Registered state update event listener", 
                     self._channel, self._serial_number[-8:])
        
        # Perform initial state query for this specific motor channel
        self.hass.async_create_task(self._async_query_initial_state())
    
    def _extract_channel_state(self, dual_motor_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract state information for this specific motor channel."""
        try:
            if not dual_motor_state or dual_motor_state.get("type") != "multi_cover":
                return None
            
            channel_states = dual_motor_state.get("channel_states", {})
            channel_key = f"channel_{self._channel}"
            
            if channel_key not in channel_states:
                return None
            
            channel_data = channel_states[channel_key]
            
            # Convert multi-cover channel data to single cover format
            return {
                "type": "cover",
                "position": channel_data.get("position"),
                "target_position": channel_data.get("target_position"),
                "position_available": channel_data.get("position_available", False),
                "runtime_measured": channel_data.get("runtime_measured", False),
                "tilt_measured": channel_data.get("tilt_measured", False),
                "is_opening": channel_data.get("is_opening", False),
                "is_closing": channel_data.get("is_closing", False),
                "is_stopped": channel_data.get("is_stopped", True),
                "motor_status": channel_data.get("motor_status", "stopped"),
                "motor_status_code": channel_data.get("motor_status_code", 126),
                "recent_tilt": channel_data.get("recent_tilt", False),
                "terrace_function": channel_data.get("terrace_function", False),
            }
            
        except Exception as e:
            _LOGGER.error("Error extracting channel %d state: %s", self._channel, e)
            return None
    
    async def _async_update_state_from_parsed(self, parsed_state: Dict[str, Any]) -> None:
        """Update cover state from parsed EWneo dual motor state (async for thread safety)."""
        _LOGGER.warning("🔄 Dual Motor CH%d: Received state update: %s", self._channel, parsed_state)
        
        old_position = self._current_cover_position
        old_is_opening = self._is_opening
        old_is_closing = self._is_closing
        old_features = self._attr_supported_features
        
        # Update position (only if available and valid)
        if parsed_state.get("position_available", False) and "position" in parsed_state:
            self._current_cover_position = parsed_state["position"]
        elif not parsed_state.get("runtime_measured", False):
            # No runtime measurement - position is UNKNOWN, set to None
            self._current_cover_position = None
        else:
            # Runtime measured but no position available (e.g., during calibration or 120s mode)
            # Keep current position or set to None if we don't have one
            if self._current_cover_position is None:
                self._current_cover_position = None
        
        # Update target position
        if "target_position" in parsed_state:
            self._target_cover_position = parsed_state["target_position"]
        
        # Update movement state
        self._is_opening = parsed_state.get("is_opening", False)
        self._is_closing = parsed_state.get("is_closing", False)
        
        # Update motor capabilities
        new_runtime_measured = parsed_state.get("runtime_measured", self._runtime_measured)
        
        # Detect first-time runtime measurement activation
        if new_runtime_measured and not self._runtime_ever_measured:
            self._runtime_ever_measured = True
            _LOGGER.info("🎯 EWneo dual motor CH%d %s: Runtime measurement ACTIVATED! Position control now available", 
                        self._channel, self._serial_number[-8:])
        
        self._runtime_measured = new_runtime_measured
        self._tilt_measured = parsed_state.get("tilt_measured", self._tilt_measured)
        self._terrace_function = parsed_state.get("terrace_function", self._terrace_function)
        self._recent_tilt = parsed_state.get("recent_tilt", self._recent_tilt)
        
        # Update motor status info
        self._motor_status_code = parsed_state.get("motor_status_code", self._motor_status_code)
        
        # Update supported features based on capabilities
        self._update_supported_features()
        
        # Log comprehensive state changes
        state_changed = (old_position != self._current_cover_position or
                        old_is_opening != self._is_opening or 
                        old_is_closing != self._is_closing or
                        old_features != self._attr_supported_features)
        
        if state_changed:
            motor_status = parsed_state.get("motor_status", "unknown")
            
            if self._runtime_measured:
                if self._current_cover_position is not None:
                    pos_text = f"{self._current_cover_position}%"
                else:
                    pos_text = "unknown (calibrating or 120s mode)"
                    
                target_text = ""
                if self._target_cover_position is not None:
                    target_text = f", Target: {self._target_cover_position}%"
                    
                _LOGGER.info("🎯 EWneo dual motor CH%d %s: %s - Position: %s%s [Runtime: ✓, Features: %s]", 
                            self._channel, self._serial_number[-8:], motor_status, pos_text, target_text,
                            "Pos+Tilt" if self._tilt_measured else "Pos")
            else:
                _LOGGER.info("🎯 EWneo dual motor CH%d %s: %s - Positionless mode [No runtime measurement]", 
                            self._channel, self._serial_number[-8:], motor_status)
            
            # Mark device as reachable and update last_seen timestamp
            from datetime import datetime
            self._reachable = True
            self._last_seen = datetime.now()
            
            self.async_write_ha_state()

    async def _send_ewb_change_state(self, mode: int, state_bytes: list) -> bool:
        """Send EWB_CHANGE_STATE command to EWneo dual motor."""
        if not self._gateway_serial:
            _LOGGER.error("EWneo dual motor %s CH%d: No gateway serial available", self._serial_number, self._channel)
            return False
        
        try:
            _LOGGER.warning("🔄 Sending EWB_CHANGE_STATE to EWneo dual motor %s CH%d: mode=%d, state_bytes=%s", 
                        self._serial_number, self._channel, mode, [f"0x{b:02X}" for b in state_bytes])
            
            # Send command via coordinator's transceiver with automatic retry on failure
            retry_attempted = False
            result = await self.coordinator.transceiver.rx11_ewb_change_state(
                self._gateway_serial, self._serial_number, mode, state_bytes
            )
            
            # Check for error responses
            if result and isinstance(result, tuple) and len(result) == 3 and isinstance(result[0], str) and result[0].startswith("ERR_"):
                error_type = result[0]
                
                # Automatic retry for RF_TIMEOUT (once, without delay)
                if error_type == "ERR_RF_TIMEOUT" and not retry_attempted:
                    retry_attempted = True
                    _LOGGER.info("🔄 EWneo dual motor %s CH%d: Timeout - automatischer Wiederholungsversuch...", self._serial_number[-8:], self._channel)
                    result = await self.coordinator.transceiver.rx11_ewb_change_state(
                        self._gateway_serial, self._serial_number, mode, state_bytes
                    )
                    # Re-check result after retry
                    if result and isinstance(result, tuple) and len(result) == 3 and isinstance(result[0], str) and result[0].startswith("ERR_"):
                        error_type = result[0]
                    else:
                        error_type = None  # Retry succeeded
                
                if error_type == "ERR_RF_TIMEOUT":
                    # Mark device as unreachable but keep it controllable
                    self._reachable = False
                    self.async_write_ha_state()
                    
                    # Report failure to coordinator (handles counting and notification after 2 failures)
                    # Use device serial (not channel-specific) for device-level tracking
                    await self.coordinator.report_ewneo_communication_failure(self._serial_number)
                    
                    _LOGGER.debug("⚠️ EWneo dual motor %s CH%d communication failure reported", self._serial_number[-8:], self._channel)
                    return False
                elif error_type:
                    _LOGGER.warning("⚠️ EWneo dual motor %s CH%d Fehler: %s", self._serial_number[-8:], self._channel, error_type)
                    return False
            elif result:
                response_mode, response_state_bytes = result
                
                # Parse response for dual motor state
                if len(response_state_bytes) >= 4:
                    parsed_state = self.coordinator._parse_ewneo_state(
                        self._device_type_code, response_state_bytes[:4], "ewneo_dual_motor", self._serial_number, mode=response_mode
                    )
                    
                    if parsed_state:
                        if parsed_state.get("type") == "cover":
                            # Individual motor response (Mode 2/10) - directly update state
                            await self._async_update_state_from_parsed(parsed_state)
                            _LOGGER.info("🎯 EWneo dual motor %s CH%d: State updated from individual response - opening=%s, closing=%s, stopped=%s", 
                                        self._serial_number, self._channel,
                                        parsed_state.get("is_opening", False),
                                        parsed_state.get("is_closing", False),
                                        parsed_state.get("is_stopped", True))
                        elif parsed_state.get("type") == "multi_cover":
                            # Summary response (Mode 0) - extract our channel's state
                            channel_state = self._extract_channel_state(parsed_state)
                            
                            if channel_state:
                                await self._async_update_state_from_parsed(channel_state)
                                _LOGGER.info("🎯 EWneo dual motor %s CH%d: State updated from summary response - opening=%s, closing=%s, stopped=%s", 
                                            self._serial_number, self._channel,
                                            channel_state.get("is_opening", False),
                                            channel_state.get("is_closing", False),
                                            channel_state.get("is_stopped", True))
                            else:
                                _LOGGER.warning("⚠️ EWneo dual motor %s CH%d: Could not extract channel state from summary response", 
                                               self._serial_number, self._channel)
                        else:
                            _LOGGER.warning("⚠️ EWneo dual motor %s CH%d: Unknown response type: %s", 
                                           self._serial_number, self._channel, parsed_state.get("type"))
                    else:
                        _LOGGER.warning("⚠️ EWneo dual motor %s CH%d: Failed to parse EWB response state", 
                                       self._serial_number, self._channel)
                else:
                    _LOGGER.warning("⚠️ EWneo dual motor %s CH%d: EWB response too short (%d bytes, expected 4+)", 
                                   self._serial_number, self._channel, len(response_state_bytes))
                
                # Mark device as reachable and update timestamp
                from datetime import datetime
                self._reachable = True
                self._last_seen = datetime.now()
                
                # Report success to coordinator (resets failure counter, dismisses notification)
                await self.coordinator.report_ewneo_communication_success(self._serial_number)
                
                # Force immediate Home Assistant state update
                self.async_write_ha_state()
                return True
            else:
                return False
                
        except Exception as e:
            _LOGGER.error("❌ Failed to set EWneo dual motor %s CH%d state: %s", 
                         self._serial_number, self._channel, e)
            return False
    
    def _create_dual_motor_command(self, channel: int, command: str) -> tuple[int, list]:
        """Create dual motor command for specific channel using Mode 0.
        
        IMPORTANT: Dual motors use Mode 0 (NOT Mode 2/10!) with channel-specific bit positioning,
        exactly like dual switches. This ensures all channels receive commands in the same telegram.
        
        State word format for Mode 0 (dual motor, 32-bit big-endian):
        - Bits 31-25: Motor #1 status code (0-100 position or 117-127 special command)
        - Bit 24: Motor #1 auto-tilt flag
        - Bits 23-17: Motor #2 status code
        - Bit 16: Motor #2 auto-tilt flag
        - Bits 15-0: Reserved
        
        Command codes:
        - 0-100: Move to position (0=open, 100=closed)
        - 126: Stop immediately
        - 119: Stop and tilt to horizontal (requires tilt measurement)
        - 120: Open for runtime duration
        - 121: Close for runtime duration
        - 122: Open for 120 seconds
        - 123: Close for 120 seconds
        
        Args:
            channel: 1 or 2
            command: 'stop', 'open', 'close', 'tilt_stop'
        """
        if channel not in [1, 2]:
            _LOGGER.error("Invalid channel %d for dual motor, must be 1 or 2", channel)
            return None, None
            
        # Command mapping according to EWB specification
        # Use runtime commands (120/121) for position tracking capability
        # Position commands use percentage values (handled separately in async_set_cover_position)
        command_map = {
            "stop": 126,         # Stop immediately
            "open": 120,         # Open for runtime duration (enables position tracking)
            "close": 121,        # Close for runtime duration (enables position tracking)
            "tilt_stop": 119,    # Stop and tilt to horizontal
        }
        
        if command not in command_map:
            _LOGGER.error("Invalid command '%s' for dual motor", command)
            return None, None
            
        cmd_code = command_map[command]
        
        _LOGGER.warning("✅ Dual motor CH%d command='%s' -> code=%d (open=120, close=121, stop=126)", 
                       channel, command, cmd_code)
        
        # **USE MODE 0** for all dual motors
        mode = 0
        
        # Build state word with EXPLICIT bit positioning
        # Bit layout (32-bit big-endian):
        #   Bits 31-25 (7 bits): Motor #1 command code
        #   Bit  24:             Motor #1 auto-tilt (0=off)
        #   Bits 23-17 (7 bits): Motor #2 command code
        #   Bit  16:             Motor #2 auto-tilt (0=off)
        #   Bits 15-0:           Reserved (0)
        #
        # 127 (0x7F) = "remain at current state" for motors
        
        # Mask command codes to 7 bits (0-127)
        motor1_code = 0x7F  # Default: remain at state
        motor2_code = 0x7F  # Default: remain at state
        
        if channel == 1:
            # Channel 1 = Motor #1: Set active command, Motor #2 stays at 127
            motor1_code = cmd_code & 0x7F  # Mask to 7 bits
            motor2_code = 127
            _LOGGER.warning("🎯 Dual motor CH1 ACTIVE: motor1_code=%d (0x%02X), motor2_code=%d (0x%02X)", 
                          motor1_code, motor1_code, motor2_code, motor2_code)
        elif channel == 2:
            # Channel 2 = Motor #2: Set active command, Motor #1 stays at 127
            motor1_code = 127
            motor2_code = cmd_code & 0x7F  # Mask to 7 bits
            _LOGGER.warning("🎯 Dual motor CH2 ACTIVE: motor1_code=%d (0x%02X), motor2_code=%d (0x%02X)", 
                          motor1_code, motor1_code, motor2_code, motor2_code)
        
        # Build 32-bit state word with explicit bit positioning
        # Auto-tilt bits (24, 16) are 0 (off)
        state_word = ((motor1_code & 0x7F) << 25) | ((motor2_code & 0x7F) << 17)
        
        _LOGGER.warning("📦 State word constructed: 0x%08X (motor1=%d at bits 31-25, motor2=%d at bits 23-17)", 
                       state_word, motor1_code, motor2_code)
        
        # Convert to 4 bytes in big-endian order
        state_bytes = [
            (state_word >> 24) & 0xFF,
            (state_word >> 16) & 0xFF,
            (state_word >> 8) & 0xFF,
            state_word & 0xFF
        ]
        
        _LOGGER.warning("🔧 Dual motor CH%d command=%s: mode=%d, code=%d, state_word=0x%08X, bytes=[0x%02X, 0x%02X, 0x%02X, 0x%02X]", 
                     channel, command, mode, cmd_code, state_word, state_bytes[0], state_bytes[1], state_bytes[2], state_bytes[3])
        return (mode, state_bytes)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        _LOGGER.warning("🔼 EWneo dual motor %s: async_open_cover called for channel %d", self._serial_number, self._channel)
        mode, state_bytes = self._create_dual_motor_command(self._channel, "open")
        if mode is not None:
            # Save previous state
            prev_opening = self._is_opening
            prev_closing = self._is_closing
            
            # Set optimistic state for immediate UI feedback
            self._is_opening = True
            self._is_closing = False
            self.async_write_ha_state()
            
            # Send command - response will update to actual state
            success = await self._send_ewb_change_state(mode, state_bytes)
            
            # If command failed, restore previous state
            if not success:
                self._is_opening = prev_opening
                self._is_closing = prev_closing
                self.async_write_ha_state()
        
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        _LOGGER.warning("🔽 EWneo dual motor %s: async_close_cover called for channel %d", self._serial_number, self._channel)
        mode, state_bytes = self._create_dual_motor_command(self._channel, "close")
        if mode is not None:
            # Save previous state
            prev_opening = self._is_opening
            prev_closing = self._is_closing
            
            # Set optimistic state for immediate UI feedback
            self._is_opening = False
            self._is_closing = True
            self.async_write_ha_state()
            
            # Send command - response will update to actual state
            success = await self._send_ewb_change_state(mode, state_bytes)
            
            # If command failed, restore previous state
            if not success:
                self._is_opening = prev_opening
                self._is_closing = prev_closing
                self.async_write_ha_state()
        
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        mode, state_bytes = self._create_dual_motor_command(self._channel, "stop")
        if mode is not None:
            success = await self._send_ewb_change_state(mode, state_bytes)
            if success:
                self._is_opening = False
                self._is_closing = False
                self.async_write_ha_state()
        
    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        position = kwargs.get(ATTR_POSITION)
        if position is None:
            return
        
        _LOGGER.info("🎯 EWneo dual motor %s: async_set_cover_position called for channel %d, position %d%%", 
                    self._serial_number, self._channel, position)
            
        if not self._runtime_ever_measured:
            _LOGGER.warning("EWneo dual motor CH%d %s: Position control requires runtime measurement activation", 
                           self._channel, self._serial_number)
            return
        
        # Convert HA position (0=closed, 100=open) to EWB position (0=open, 100=closed)
        ewb_position = 100 - position
        
        # **USE MODE 0** for position commands
        mode = 0
        
        # Build state word with EXPLICIT bit positioning
        # Bit layout same as command: Bits 31-25 (motor1), Bits 23-17 (motor2)
        # 127 = "remain at current state"
        
        motor1_pos = 127  # Default: remain
        motor2_pos = 127  # Default: remain
        
        if self._channel == 1:
            # Channel 1 = Motor #1: Set position, Motor #2 stays at 127
            motor1_pos = ewb_position & 0x7F
            motor2_pos = 127
            _LOGGER.warning("🎯 Dual motor CH1 position: motor1=%d%%, motor2=remain(127)", motor1_pos)
        elif self._channel == 2:
            # Channel 2 = Motor #2: Set position, Motor #1 stays at 127
            motor1_pos = 127
            motor2_pos = ewb_position & 0x7F
            _LOGGER.warning("🎯 Dual motor CH2 position: motor1=remain(127), motor2=%d%%", motor2_pos)
        
        # Build 32-bit state word
        state_word = ((motor1_pos & 0x7F) << 25) | ((motor2_pos & 0x7F) << 17)
        
        # Convert to 4 bytes in big-endian order
        state_bytes = [
            (state_word >> 24) & 0xFF,
            (state_word >> 16) & 0xFF,
            (state_word >> 8) & 0xFF,
            state_word & 0xFF
        ]
        
        _LOGGER.info("🎯 Dual motor CH%d position %d%% (EWB:%d): mode=%d, state_word=0x%08X, bytes=[0x%02X, 0x%02X, 0x%02X, 0x%02X]", 
                    self._channel, position, ewb_position, mode, state_word, 
                    state_bytes[0], state_bytes[1], state_bytes[2], state_bytes[3])
        
        self._target_cover_position = position
        await self._send_ewb_change_state(mode, state_bytes)


class EldatEWneoQuadMotorCover(EldatEntity, CoverEntity):
    """EWneo-QuadMotor cover entity with individual motor control and runtime measurement activation."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        super().__init__(coordinator, serial_number, device_info)
        self._entity_spec = entity_spec
        # Smart channel handling: entity_spec['channel'] can be 0-based OR 1-based  
        # For quad motors: 0-based = (0,1,2,3), 1-based = (1,2,3,4)
        # Detect and normalize to 1-based (1,2,3,4)
        raw_channel = int(entity_spec.get("channel", 0))
        if raw_channel <= 3:
            # Could be 0-based (0,1,2,3) or 1-based (1,2,3)
            # Check unique_id for hints: _ch0, _ch1, _ch2, _ch3 indicate 0-based
            unique_id = entity_spec.get("unique_id", "")
            if "_ch0" in unique_id or "_ch1" in unique_id or "_ch2" in unique_id or "_ch3" in unique_id:
                # 0-based detected
                self._channel = raw_channel + 1  # 0->1, 1->2, 2->3, 3->4
                _LOGGER.warning("🆕 Quad Motor: Detected 0-based channel (unique_id=%s), converting %d -> %d", 
                               unique_id, raw_channel, self._channel)
            else:
                # Assume 1-based (but validate)
                if raw_channel == 0:
                    # Channel 0 is never valid for 1-based, so must be 0-based index
                    self._channel = 1
                    _LOGGER.warning("⚠️ Quad Motor: Channel 0 invalid, correcting to 1")
                else:
                    self._channel = raw_channel  # Keep as is
        else:
            # raw_channel == 4: Must be 1-based channel 4
            self._channel = raw_channel
        
        self._available = True
        
        _LOGGER.warning("🆕 Quad Motor: entity_spec['channel']=%s (raw), unique_id=%s -> self._channel=%d (normalized)", 
                       raw_channel, entity_spec.get("unique_id", "N/A"), self._channel)
        
        # EWneo specific attributes
        self._gateway_serial = device_info.get("gateway_serial")
        self._device_type_code = device_info.get("device_type_code", 0)
        
        # Cover state for specific channel
        self._is_opening = False
        self._is_closing = False
        self._current_cover_position = None  # None = unknown, 0-100 = position
        self._target_cover_position = None
        
        # Motor status from EWB specification for this channel
        self._motor_status_code = 126  # Default: stopped
        self._runtime_measured = False
        self._tilt_measured = False
        self._recent_tilt = False
        self._auto_tilt = False
        self._terrace_function = False
        self._stored_position = 0
        
        # Runtime measurement activation tracking
        self._runtime_ever_measured = False  # Tracks if runtime measurement was ever detected
        
        # Reachability tracking
        self._reachable = True
        self._last_seen = None
        
        _LOGGER.info("🔧 Initializing EWneo-QuadMotor cover CH%d: %s (gateway: %s, type_code: 0x%02X)", 
                    self._channel, serial_number[-8:], self._gateway_serial[-8:] if self._gateway_serial else "None", self._device_type_code)
        
        # Set up entity attributes
        # Store translation_key for dynamic name resolution AND state translation
        self._translation_key = entity_spec.get("translation_key")
        self._attr_translation_key = self._translation_key  # For HA state translations
        self._static_name = entity_spec.get("name", f"EWneo-QuadMotor CH{self._channel} {serial_number}")
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_ewneo_quad_motor_ch{self._channel}")
        self._attr_device_class = CoverDeviceClass.SHUTTER
        
        # Dynamic icons based on open/closed state
        self._icon_open = entity_spec.get("icon_open", "mdi:window-shutter-open")
        self._icon_closed = entity_spec.get("icon_closed", "mdi:window-shutter")
        self._base_icon = entity_spec.get("icon", "mdi:window-shutter")
        
        # Initially no position features - will be enabled after QUERY_STATE response
        self._update_supported_features()
        
        _LOGGER.info("✅ EWneo-QuadMotor cover entity CH%d initialized: %s (%s)", self._channel, self._translation_key or self._static_name or f"Channel {self._channel}", self._attr_unique_id)
        
    async def _async_query_initial_state(self) -> None:
        """Query initial motor state for this specific channel to check for runtime measurement capability.
        
        For multi-channel motors, we query each channel individually with Mode 2/10/18/26
        to get the full state including persistent runtime measurement information.
        Mode 0 only provides summary status without runtime_measured flag.
        """
        max_retries = 2
        
        # Map channel to query mode: CH1=2, CH2=10, CH3=18, CH4=26
        channel_mode_map = {1: 2, 2: 10, 3: 18, 4: 26}
        query_mode = channel_mode_map.get(self._channel, 0)
        
        for attempt in range(1, max_retries + 1):
            try:
                _LOGGER.info("🔍 EWneo-QuadMotor CH%d %s: Querying initial state with Mode %d (attempt %d/%d)", 
                            self._channel, self._serial_number[-8:], query_mode, attempt, max_retries)
                
                # Use coordinator to perform EwbQueryState with channel-specific mode
                result = await self.coordinator._query_ewneo_state_with_mode(
                    self._gateway_serial,
                    self._serial_number,
                    query_mode
                )
                
                if result:
                    _LOGGER.info("✅ EWneo-QuadMotor CH%d %s: Mode %d query successful, state will be processed via event", 
                                self._channel, self._serial_number[-8:], query_mode)
                    # Mark device as reachable and update last_seen timestamp
                    from datetime import datetime
                    self._reachable = True
                    self._last_seen = datetime.now()
                    return  # Success - exit retry loop
                else:
                    _LOGGER.warning("⚠️ EWneo-QuadMotor CH%d %s: Mode %d state query failed (attempt %d/%d)", 
                                   self._channel, self._serial_number[-8:], query_mode, attempt, max_retries)
                    self._reachable = False
                    await self.coordinator.report_ewneo_communication_failure(self._serial_number)
                    
            except Exception as e:
                _LOGGER.error("❌ EWneo-QuadMotor CH%d %s: Error during initial state query (attempt %d/%d): %s", 
                             self._channel, self._serial_number[-8:], attempt, max_retries, e)
                self._reachable = False
                await self.coordinator.report_ewneo_communication_failure(self._serial_number)
        
    def _update_supported_features(self) -> None:
        """Update supported features based on motor capabilities (QuadMotor)."""
        features = (
            CoverEntityFeature.OPEN |
            CoverEntityFeature.CLOSE |
            CoverEntityFeature.STOP
        )
        
        # Add position control if runtime measurement was ever detected
        # (not just currently active - once measured, always available)
        if self._runtime_ever_measured:
            features |= CoverEntityFeature.SET_POSITION
            _LOGGER.debug("EWneo-QuadMotor CH%d: Position control enabled (runtime_ever_measured=True)", self._channel)
        else:
            _LOGGER.debug("EWneo-QuadMotor CH%d: Position control disabled (runtime_ever_measured=False)", self._channel)
            
        # Note: Tilt features removed - Easywave neo Motors don't support tilt control
            
        self._attr_supported_features = features

    @property
    def current_cover_position(self) -> Optional[int]:
        """Return the current position of cover where 0 means closed and 100 is fully open."""
        return self._current_cover_position
    
    @property
    def target_cover_position(self) -> Optional[int]:
        """Return the target position of cover where 0 means closed and 100 is fully open."""
        return self._target_cover_position
    
    @property
    def icon(self) -> str:
        """Return dynamic icon based on cover state."""
        # Moving states - use base icon
        if self._is_opening or self._is_closing:
            return self._base_icon
        # Closed state
        if self._current_cover_position == 0:
            return self._icon_closed
        # Open or partially open
        if self._current_cover_position is not None and self._current_cover_position > 0:
            return self._icon_open
        # Unknown state - use base icon
        return self._base_icon
    
    @property 
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        return self._is_opening
    
    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        return self._is_closing
    
    @property
    def is_closed(self) -> Optional[bool]:
        """Return if the cover is closed."""
        if self._current_cover_position is not None:
            return self._current_cover_position == 0
        return None
    
    @property
    def state(self) -> str | None:
        """Return the state of the cover with stopped support.
        
        Shows 'stopped' when motor is stopped without position info,
        or position percentage when runtime measurement is available.
        """
        # Check for moving states first
        if self._is_opening:
            return "opening"
        if self._is_closing:
            return "closing"
        
        # If we have position information, use standard open/closed states
        if self._current_cover_position is not None:
            if self._current_cover_position == 0:
                return "closed"
            else:
                return "open"
        
        # Motor stopped without position info - show "stopped" instead of unknown
        # motor_status_code 126 = stopped
        if self._motor_status_code == 126 or (not self._is_opening and not self._is_closing):
            return "stopped"
        
        return None

    @property
    def name(self) -> str | None:
        """Return the dynamically translated name."""
        if self._translation_key:
            # Get current language and translate dynamically
            lang = get_language(self.hass) if self.hass else "en"
            # Map translation_key to translation path
            key_map = {
                "channel_1": "entity.channel",
                "channel_2": "entity.channel",
                "channel_3": "entity.channel",
                "channel_4": "entity.channel",
            }
            if self._translation_key in key_map:
                translated = translate("entity.channel", lang)
                if translated != "entity.channel":
                    return translated.format(channel=self._channel)
                return f"Channel {self._channel}"
        elif self._device_type_code in [0x08, 0x09]:  # Dual or Quad motor
            # Dynamic translation for channel
            lang = get_language(self.hass) if self.hass else "en"
            translated = translate("entity.channel", lang)
            if translated != "entity.channel":
                return translated.format(channel=self._channel)
            return f"Channel {self._channel}"
        # Fallback to static name or None (single channel uses device name only)
        return self._static_name

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        attrs = {
            "serial_number": self._serial_number,
            "motor_channel": self._channel,
            "motor_status_code": self._motor_status_code,
            "runtime_measured": self._runtime_measured,
            "tilt_measured": self._tilt_measured,
            "terrace_function": self._terrace_function,
            "device_type_code": f"0x{self._device_type_code:02X}",
            "reachable": self._reachable,
        }
        
        # Add EWneo index from device info
        device_data = self.coordinator.devices.get(self._serial_number, {})
        ewneo_index = device_data.get("ewneo_index")
        if ewneo_index is not None:
            attrs["ewneo_index"] = ewneo_index
        
        if self._last_seen:
            attrs["last_seen"] = self._last_seen.strftime("%d.%m.%Y %H:%M:%S")
        
        if self._recent_tilt:
            attrs["recent_tilt"] = True
            
        if self._stored_position:
            attrs["stored_position"] = self._stored_position
        
            
        return attrs

    async def async_added_to_hass(self) -> None:
        """Handle entity added to hass."""
        await super().async_added_to_hass()
        
        # Add NFILTER for gateway serial to enable bidirectional communication
        if self._gateway_serial:
            try:
                filter_success = await self.coordinator.transceiver.rx11_ewb_add_filter(self._gateway_serial)
                if filter_success:
                    _LOGGER.info("✅ EWneo-QuadMotor CH%d %s: Added NFILTER for gateway %s", 
                                self._channel, self._serial_number[-8:], self._gateway_serial[-8:])
                else:
                    _LOGGER.warning("⚠️ EWneo quad motor CH%d %s: Failed to add NFILTER for gateway %s", 
                                   self._channel, self._serial_number[-8:], self._gateway_serial[-8:])
            except Exception as e:
                _LOGGER.error("❌ EWneo quad motor CH%d %s: Error adding NFILTER: %s", 
                             self._channel, self._serial_number[-8:], e)
        else:
            _LOGGER.warning("⚠️ EWneo quad motor CH%d %s: No gateway serial configured", 
                           self._channel, self._serial_number[-8:])
        
        # Subscribe to EWneo state updates for quad motor devices
        async def handle_ewneo_state_update(event):
            try:
                event_data = event.data
                if event_data.get("serial_number") == self._serial_number:
                    parsed_state = event_data.get("parsed_state", {})
                    
                    # QuadMotor handles BOTH:
                    # 1. Mode 0 (multi_cover): All motors in one 32-bit word - extract channel
                    # 2. Mode 2/10/18/26 (cover): Individual motor detail - use directly for matching channel
                    if parsed_state.get("type") == "multi_cover":
                        channel_state = self._extract_channel_state(parsed_state)
                        if channel_state:
                            _LOGGER.debug("🔄 QuadMotor CH%d: Updating state from multi_cover", self._channel)
                            self.hass.add_job(self._async_update_state_from_parsed, channel_state)
                        else:
                            _LOGGER.warning("⚠️ QuadMotor CH%d: Could not extract channel state", self._channel)
                    elif parsed_state.get("type") == "cover":
                        # Individual motor response (Mode 2/10/18/26) - check if it matches our channel
                        # Mode 2 = CH1, Mode 10 = CH2, Mode 18 = CH3, Mode 26 = CH4
                        event_mode = event_data.get("query_mode")
                        channel_mode_map = {2: 1, 10: 2, 18: 3, 26: 4}
                        event_channel = channel_mode_map.get(event_mode)
                        
                        if event_channel == self._channel:
                            _LOGGER.debug("🔄 QuadMotor CH%d: Updating state from individual mode %s", self._channel, event_mode)
                            self.hass.add_job(self._async_update_state_from_parsed, parsed_state)
                        elif event_mode is None:
                            # Mode not specified in event - could be initial query response
                            _LOGGER.debug("🔄 QuadMotor CH%d: Received cover state without mode, updating", self._channel)
                            self.hass.add_job(self._async_update_state_from_parsed, parsed_state)
            except Exception as e:
                _LOGGER.error("Error in quad motor CH%d state update handler: %s", self._channel, e)
        
        self.hass.bus.async_listen("eldat_ewneo_state_update", handle_ewneo_state_update)
        _LOGGER.debug("🔗 EWneo quad motor CH%d %s: Registered state update event listener", 
                     self._channel, self._serial_number[-8:])
        
        # Perform initial state query for this specific motor channel
        self.hass.async_create_task(self._async_query_initial_state())
    
    def _extract_channel_state(self, quad_motor_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract state information for this specific motor channel."""
        try:
            if not quad_motor_state or quad_motor_state.get("type") != "multi_cover":
                return None
            
            channel_states = quad_motor_state.get("channel_states", {})
            channel_key = f"channel_{self._channel}"
            
            if channel_key not in channel_states:
                return None
            
            channel_data = channel_states[channel_key]
            
            # Convert multi-cover channel data to single cover format
            return {
                "type": "cover",
                "position": channel_data.get("position"),
                "target_position": channel_data.get("target_position"),
                "position_available": channel_data.get("position_available", False),
                "runtime_measured": channel_data.get("runtime_measured", False),
                "tilt_measured": channel_data.get("tilt_measured", False),
                "is_opening": channel_data.get("is_opening", False),
                "is_closing": channel_data.get("is_closing", False),
                "is_stopped": channel_data.get("is_stopped", True),
                "motor_status": channel_data.get("motor_status", "stopped"),
                "motor_status_code": channel_data.get("motor_status_code", 126),
                "recent_tilt": channel_data.get("recent_tilt", False),
                "terrace_function": channel_data.get("terrace_function", False),
            }
            
        except Exception as e:
            _LOGGER.error("Error extracting channel %d state: %s", self._channel, e)
            return None
    
    async def _async_update_state_from_parsed(self, parsed_state: Dict[str, Any]) -> None:
        """Update cover state from parsed EWneo quad motor state (async for thread safety)."""
        _LOGGER.warning("🔄 Quad Motor CH%d: Received state update: %s", self._channel, parsed_state)
        
        old_position = self._current_cover_position
        old_is_opening = self._is_opening
        old_is_closing = self._is_closing
        old_features = self._attr_supported_features
        
        # Update position (only if available and valid)
        if parsed_state.get("position_available", False) and "position" in parsed_state:
            self._current_cover_position = parsed_state["position"]
        elif not parsed_state.get("runtime_measured", False):
            # No runtime measurement - position is UNKNOWN, set to None
            self._current_cover_position = None
        else:
            # Runtime measured but no position available (e.g., during calibration or 120s mode)
            # Keep current position or set to None if we don't have one
            if self._current_cover_position is None:
                self._current_cover_position = None
        
        # Update target position
        if "target_position" in parsed_state:
            self._target_cover_position = parsed_state["target_position"]
        
        # Update movement state
        self._is_opening = parsed_state.get("is_opening", False)
        self._is_closing = parsed_state.get("is_closing", False)
        
        # Update motor capabilities
        new_runtime_measured = parsed_state.get("runtime_measured", self._runtime_measured)
        
        # Detect first-time runtime measurement activation
        if new_runtime_measured and not self._runtime_ever_measured:
            self._runtime_ever_measured = True
            _LOGGER.info("🎯 EWneo quad motor CH%d %s: Runtime measurement ACTIVATED! Position control now available", 
                        self._channel, self._serial_number[-8:])
        
        self._runtime_measured = new_runtime_measured
        self._tilt_measured = parsed_state.get("tilt_measured", self._tilt_measured)
        self._terrace_function = parsed_state.get("terrace_function", self._terrace_function)
        self._recent_tilt = parsed_state.get("recent_tilt", self._recent_tilt)
        
        # Update motor status info
        self._motor_status_code = parsed_state.get("motor_status_code", self._motor_status_code)
        
        # Update supported features based on capabilities
        self._update_supported_features()
        
        # Log comprehensive state changes
        state_changed = (old_position != self._current_cover_position or
                        old_is_opening != self._is_opening or 
                        old_is_closing != self._is_closing or
                        old_features != self._attr_supported_features)
        
        if state_changed:
            motor_status = parsed_state.get("motor_status", "unknown")
            
            if self._runtime_measured:
                if self._current_cover_position is not None:
                    pos_text = f"{self._current_cover_position}%"
                else:
                    pos_text = "unknown (calibrating or 120s mode)"
                    
                target_text = ""
                if self._target_cover_position is not None:
                    target_text = f", Target: {self._target_cover_position}%"
                    
                _LOGGER.info("🎯 EWneo quad motor CH%d %s: %s - Position: %s%s [Runtime: ✓, Features: %s]", 
                            self._channel, self._serial_number[-8:], motor_status, pos_text, target_text,
                            "Pos+Tilt" if self._tilt_measured else "Pos")
            else:
                _LOGGER.info("🎯 EWneo quad motor CH%d %s: %s - Positionless mode [No runtime measurement]", 
                            self._channel, self._serial_number[-8:], motor_status)
            
            # Mark device as reachable and update last_seen timestamp
            from datetime import datetime
            self._reachable = True
            self._last_seen = datetime.now()
            
            self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        _LOGGER.warning("🔼 EWneo quad motor %s: async_open_cover called for channel %d", self._serial_number, self._channel)
        mode, state_bytes = self._create_quad_motor_command(self._channel, "open")
        if mode is not None:
            # Save previous state
            prev_opening = self._is_opening
            prev_closing = self._is_closing
            
            # Set optimistic state for immediate UI feedback
            self._is_opening = True
            self._is_closing = False
            self.async_write_ha_state()
            
            # Send command - response will update to actual state
            success = await self._send_ewb_change_state(mode, state_bytes)
            
            # If command failed, restore previous state
            if not success:
                self._is_opening = prev_opening
                self._is_closing = prev_closing
                self.async_write_ha_state()
        
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        _LOGGER.warning("🔽 EWneo quad motor %s: async_close_cover called for channel %d", self._serial_number, self._channel)
        mode, state_bytes = self._create_quad_motor_command(self._channel, "close")
        if mode is not None:
            # Save previous state
            prev_opening = self._is_opening
            prev_closing = self._is_closing
            
            # Set optimistic state for immediate UI feedback
            self._is_opening = False
            self._is_closing = True
            self.async_write_ha_state()
            
            # Send command - response will update to actual state
            success = await self._send_ewb_change_state(mode, state_bytes)
            
            # If command failed, restore previous state
            if not success:
                self._is_opening = prev_opening
                self._is_closing = prev_closing
                self.async_write_ha_state()
        
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        mode, state_bytes = self._create_quad_motor_command(self._channel, "stop")
        if mode is not None:
            success = await self._send_ewb_change_state(mode, state_bytes)
            if success:
                self._is_opening = False
                self._is_closing = False
                self.async_write_ha_state()
        
    async def _send_ewb_change_state(self, mode: int, state_bytes: list) -> bool:
        """Send EWB_CHANGE_STATE command to EWneo quad motor."""
        if not self._gateway_serial:
            _LOGGER.error("EWneo quad motor %s CH%d: No gateway serial available", self._serial_number, self._channel)
            return False
        
        try:
            _LOGGER.info("🔄 Sending EWB_CHANGE_STATE to EWneo quad motor %s CH%d: mode=%d, state_bytes=%s", 
                        self._serial_number, self._channel, mode, [f"0x{b:02X}" for b in state_bytes])
            
            # Send command via coordinator's transceiver with automatic retry on failure
            retry_attempted = False
            result = await self.coordinator.transceiver.rx11_ewb_change_state(
                self._gateway_serial, self._serial_number, mode, state_bytes
            )
            
            # Check for error responses
            if result and isinstance(result, tuple) and len(result) == 3 and isinstance(result[0], str) and result[0].startswith("ERR_"):
                error_type = result[0]
                
                # Automatic retry for RF_TIMEOUT (once, without delay)
                if error_type == "ERR_RF_TIMEOUT" and not retry_attempted:
                    retry_attempted = True
                    _LOGGER.info("🔄 EWneo quad motor %s CH%d: Timeout - automatischer Wiederholungsversuch...", self._serial_number[-8:], self._channel)
                    result = await self.coordinator.transceiver.rx11_ewb_change_state(
                        self._gateway_serial, self._serial_number, mode, state_bytes
                    )
                    # Re-check result after retry
                    if result and isinstance(result, tuple) and len(result) == 3 and isinstance(result[0], str) and result[0].startswith("ERR_"):
                        error_type = result[0]
                    else:
                        error_type = None  # Retry succeeded
                
                if error_type == "ERR_RF_TIMEOUT":
                    # Mark device as unreachable but keep it controllable
                    self._reachable = False
                    self.async_write_ha_state()
                    
                    # Report failure to coordinator (handles counting and notification after 2 failures)
                    # Use device serial (not channel-specific) for device-level tracking
                    await self.coordinator.report_ewneo_communication_failure(self._serial_number)
                    
                    _LOGGER.debug("⚠️ EWneo quad motor %s CH%d communication failure reported", self._serial_number[-8:], self._channel)
                    return False
                elif error_type:
                    _LOGGER.warning("⚠️ EWneo quad motor %s CH%d Fehler: %s", self._serial_number[-8:], self._channel, error_type)
                    return False
            elif result:
                response_mode, response_state_bytes = result
                
                # Parse response for quad motor state
                if len(response_state_bytes) >= 4:
                    parsed_state = self.coordinator._parse_ewneo_state(
                        self._device_type_code, response_state_bytes[:4], "ewneo_quad_motor", self._serial_number, mode=response_mode
                    )
                    
                    if parsed_state:
                        if parsed_state.get("type") == "cover":
                            # Individual motor response (Mode 2/10/18/26) - directly update state
                            await self._async_update_state_from_parsed(parsed_state)
                            _LOGGER.info("🎯 EWneo quad motor %s CH%d: State updated from individual response - opening=%s, closing=%s, stopped=%s", 
                                        self._serial_number, self._channel,
                                        parsed_state.get("is_opening", False),
                                        parsed_state.get("is_closing", False),
                                        parsed_state.get("is_stopped", True))
                        elif parsed_state.get("type") == "multi_cover":
                            # Summary response (Mode 0) - extract our channel's state
                            channel_state = self._extract_channel_state(parsed_state)
                            
                            if channel_state:
                                await self._async_update_state_from_parsed(channel_state)
                                _LOGGER.info("🎯 EWneo quad motor %s CH%d: State updated from summary response - opening=%s, closing=%s, stopped=%s", 
                                            self._serial_number, self._channel,
                                            channel_state.get("is_opening", False),
                                            channel_state.get("is_closing", False),
                                            channel_state.get("is_stopped", True))
                            else:
                                _LOGGER.warning("⚠️ EWneo quad motor %s CH%d: Could not extract channel state from summary response", 
                                               self._serial_number, self._channel)
                        else:
                            _LOGGER.warning("⚠️ EWneo quad motor %s CH%d: Unknown response type: %s", 
                                           self._serial_number, self._channel, parsed_state.get("type"))
                    else:
                        _LOGGER.warning("⚠️ EWneo quad motor %s CH%d: Failed to parse EWB response state", 
                                       self._serial_number, self._channel)
                else:
                    _LOGGER.warning("⚠️ EWneo quad motor %s CH%d: EWB response too short (%d bytes, expected 4+)", 
                                   self._serial_number, self._channel, len(response_state_bytes))
                
                # Mark device as reachable and update timestamp
                from datetime import datetime
                self._reachable = True
                self._last_seen = datetime.now()
                
                # Report success to coordinator (resets failure counter, dismisses notification)
                await self.coordinator.report_ewneo_communication_success(self._serial_number)
                
                # Force immediate Home Assistant state update
                self.async_write_ha_state()
                return True
            else:
                return False
                
        except Exception as e:
            _LOGGER.error("❌ Failed to set EWneo quad motor %s CH%d state: %s", 
                         self._serial_number, self._channel, e)
            return False
    
    def _create_quad_motor_command(self, channel: int, command: str) -> tuple[int, list]:
        """Create quad motor command for specific channel using Mode 0.
        
        IMPORTANT: Quad motors use Mode 0 (NOT Mode 2/10/18/26!) with channel-specific bit positioning,
        exactly like quad switches. This ensures all channels receive commands in the same telegram.
        
        State word format for Mode 0 (quad motor, 32-bit big-endian):
        - Bits 31-25: Motor #1 status code (0-100 position or 117-127 special command)
        - Bit 24: Motor #1 auto-tilt flag
        - Bits 23-17: Motor #2 status code
        - Bit 16: Motor #2 auto-tilt flag
        - Bits 15-9: Motor #3 status code
        - Bit 8: Motor #3 auto-tilt flag
        - Bits 7-1: Motor #4 status code
        - Bit 0: Motor #4 auto-tilt flag
        
        Command codes:
        - 0-100: Move to position (0=open, 100=closed)
        - 126: Stop immediately
        - 119: Stop and tilt to horizontal (requires tilt measurement)
        - 120: Open for runtime duration
        - 121: Close for runtime duration
        - 122: Open for 120 seconds
        - 123: Close for 120 seconds
        
        Args:
            channel: 1, 2, 3, or 4
            command: 'stop', 'open', 'close', 'tilt_stop'
        """
        if channel not in [1, 2, 3, 4]:
            _LOGGER.error("Invalid channel %d for quad motor, must be 1-4", channel)
            return None, None
            
        # Command mapping according to EWB specification
        # Use runtime commands (120/121) for position tracking capability
        # Position commands use percentage values (handled separately in async_set_cover_position)
        command_map = {
            "stop": 126,         # Stop immediately
            "open": 120,         # Open for runtime duration (enables position tracking)
            "close": 121,        # Close for runtime duration (enables position tracking)
            "tilt_stop": 119,    # Stop and tilt to horizontal
        }
        
        if command not in command_map:
            _LOGGER.error("Invalid command '%s' for quad motor", command)
            return None, None
            
        cmd_code = command_map[command]
        
        _LOGGER.warning("✅ Quad motor CH%d command='%s' -> code=%d (open=120, close=121, stop=126)", 
                       channel, command, cmd_code)
        
        # **USE MODE 0** for all quad motors
        mode = 0
        
        # Build state word with EXPLICIT bit positioning
        # Bit layout (32-bit big-endian):
        #   Bits 31-25 (7 bits): Motor #1 command code
        #   Bit  24:             Motor #1 auto-tilt (0=off)
        #   Bits 23-17 (7 bits): Motor #2 command code
        #   Bit  16:             Motor #2 auto-tilt (0=off)
        #   Bits 15-9  (7 bits): Motor #3 command code
        #   Bit  8:              Motor #3 auto-tilt (0=off)
        #   Bits 7-1   (7 bits): Motor #4 command code
        #   Bit  0:              Motor #4 auto-tilt (0=off)
        #
        # 127 (0x7F) = "remain at current state" for motors
        
        # Initialize all motors to "remain at state" (127)
        motor1_code = 127
        motor2_code = 127
        motor3_code = 127
        motor4_code = 127
        
        # Set the active channel's command code
        if channel == 1:
            motor1_code = cmd_code & 0x7F
            _LOGGER.warning("🎯 Quad motor CH1 ACTIVE: code=%d, others=127", motor1_code)
        elif channel == 2:
            motor2_code = cmd_code & 0x7F
            _LOGGER.warning("🎯 Quad motor CH2 ACTIVE: code=%d, others=127", motor2_code)
        elif channel == 3:
            motor3_code = cmd_code & 0x7F
            _LOGGER.warning("🎯 Quad motor CH3 ACTIVE: code=%d, others=127", motor3_code)
        elif channel == 4:
            motor4_code = cmd_code & 0x7F
            _LOGGER.warning("🎯 Quad motor CH4 ACTIVE: code=%d, others=127", motor4_code)
        
        # Build 32-bit state word with explicit bit positioning
        # Auto-tilt bits (24, 16, 8, 0) are all 0 (off)
        state_word = (
            ((motor1_code & 0x7F) << 25) |  # Bits 31-25: Motor #1
            ((motor2_code & 0x7F) << 17) |  # Bits 23-17: Motor #2
            ((motor3_code & 0x7F) << 9)  |  # Bits 15-9:  Motor #3
            ((motor4_code & 0x7F) << 1)     # Bits 7-1:   Motor #4
        )
        
        _LOGGER.warning("📦 State word: 0x%08X (m1=%d, m2=%d, m3=%d, m4=%d)", 
                       state_word, motor1_code, motor2_code, motor3_code, motor4_code)
        
        # Convert to 4 bytes in big-endian order
        state_bytes = [
            (state_word >> 24) & 0xFF,
            (state_word >> 16) & 0xFF,
            (state_word >> 8) & 0xFF,
            state_word & 0xFF
        ]
        
        _LOGGER.info("🔧 Quad motor CH%d command=%s: mode=%d, code=%d, state_word=0x%08X, bytes=[0x%02X, 0x%02X, 0x%02X, 0x%02X]", 
                     channel, command, mode, cmd_code, state_word, state_bytes[0], state_bytes[1], state_bytes[2], state_bytes[3])
        return (mode, state_bytes)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        _LOGGER.info("🔼 EWneo quad motor %s: async_open_cover called for channel %d", self._serial_number, self._channel)
        mode, state_bytes = self._create_quad_motor_command(self._channel, "open")
        if mode is not None:
            # Set optimistic state for immediate UI feedback
            self._is_opening = True
            self._is_closing = False
            self.async_write_ha_state()
            
            # Send command - response will update to actual state
            success = await self._send_ewb_change_state(mode, state_bytes)
            
            # If command completely failed, reset to stopped
            if not success:
                _LOGGER.warning("⚠️ Quad motor CH%d: Command failed, resetting to stopped", self._channel)
                self._is_opening = False
                self._is_closing = False
                self.async_write_ha_state()
        
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        _LOGGER.info("🔽 EWneo quad motor %s: async_close_cover called for channel %d", self._serial_number, self._channel)
        mode, state_bytes = self._create_quad_motor_command(self._channel, "close")
        if mode is not None:
            # Set optimistic state for immediate UI feedback
            self._is_opening = False
            self._is_closing = True
            self.async_write_ha_state()
            
            # Send command - response will update to actual state
            success = await self._send_ewb_change_state(mode, state_bytes)
            
            # If command completely failed, reset to stopped
            if not success:
                _LOGGER.warning("⚠️ Quad motor CH%d: Command failed, resetting to stopped", self._channel)
                self._is_opening = False
                self._is_closing = False
                self.async_write_ha_state()
        
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        mode, state_bytes = self._create_quad_motor_command(self._channel, "stop")
        if mode is not None:
            self._is_opening = False
            self._is_closing = False
            self.async_write_ha_state()
            await self._send_ewb_change_state(mode, state_bytes)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        position = kwargs.get(ATTR_POSITION)
        if position is None:
            return
        
        _LOGGER.info("🎯 EWneo quad motor %s: async_set_cover_position called for channel %d, position %d%%", 
                    self._serial_number, self._channel, position)
            
        if not self._runtime_ever_measured:
            _LOGGER.warning("EWneo quad motor CH%d %s: Position control requires runtime measurement activation", 
                           self._channel, self._serial_number)
            return
        
        # Convert HA position (0=closed, 100=open) to EWB position (0=open, 100=closed)
        ewb_position = 100 - position
        
        # **USE MODE 0** for position commands
        mode = 0
        
        # Build state word with EXPLICIT bit positioning
        # Initialize all motors to "remain at state" (127)
        motor1_pos = 127
        motor2_pos = 127
        motor3_pos = 127
        motor4_pos = 127
        
        # Set the active channel's position
        if self._channel == 1:
            motor1_pos = ewb_position & 0x7F
            _LOGGER.warning("🎯 Quad CH1 position: m1=%d%%, others=127", motor1_pos)
        elif self._channel == 2:
            motor2_pos = ewb_position & 0x7F
            _LOGGER.warning("🎯 Quad CH2 position: m2=%d%%, others=127", motor2_pos)
        elif self._channel == 3:
            motor3_pos = ewb_position & 0x7F
            _LOGGER.warning("🎯 Quad CH3 position: m3=%d%%, others=127", motor3_pos)
        elif self._channel == 4:
            motor4_pos = ewb_position & 0x7F
            _LOGGER.warning("🎯 Quad CH4 position: m4=%d%%, others=127", motor4_pos)
        
        # Build 32-bit state word
        state_word = (
            ((motor1_pos & 0x7F) << 25) |
            ((motor2_pos & 0x7F) << 17) |
            ((motor3_pos & 0x7F) << 9)  |
            ((motor4_pos & 0x7F) << 1)
        )
        
        # Convert to 4 bytes in big-endian order
        state_bytes = [
            (state_word >> 24) & 0xFF,
            (state_word >> 16) & 0xFF,
            (state_word >> 8) & 0xFF,
            state_word & 0xFF
        ]
        
        _LOGGER.info("🎯 Quad motor CH%d position %d%% (EWB:%d): mode=%d, state_word=0x%08X, bytes=[0x%02X, 0x%02X, 0x%02X, 0x%02X]", 
                    self._channel, position, ewb_position, mode, state_word, 
                    state_bytes[0], state_bytes[1], state_bytes[2], state_bytes[3])
        
        self._target_cover_position = position
        await self._send_ewb_change_state(mode, state_bytes)