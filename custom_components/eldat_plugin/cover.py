"""Cover platform for ELDAT motor devices."""
from __future__ import annotations

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
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EldatCoordinator
from .entity import EldatEntity

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
        
        for entity_spec in entity_specs:
            if entity_spec.get("type") == "cover":
                try:
                    if device_info.get("neo_device"):
                        # EWneo cover entity
                        entities.append(EldatEWneoCover(coordinator, serial_number, device_info, entity_spec))
                        _LOGGER.info("🔧 Created EWneo cover entity for device %s", serial_number[-8:])
                    else:
                        # Regular cover entity
                        entities.append(EldatCover(coordinator, serial_number, device_info, entity_spec))
                except Exception as e:
                    _LOGGER.warning("Failed to create cover for device %s: %s", 
                                  serial_number[-6:], e)
    
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
                            # Create covers for each motor channel
                            for channel in [1, 2]:
                                channel_spec = entity_spec.copy()
                                channel_spec["channel"] = channel
                                channel_spec["name"] = f"{entity_spec.get('name', 'Motor')} CH{channel}"
                                channel_spec["unique_id"] = f"{entity_spec.get('unique_id', serial_number)}_ch{channel}"
                                new_covers.append(EldatEWneoDualMotorCover(coordinator, serial_number, device_info, channel_spec))
                                _LOGGER.info("✅ Created EWneo dual motor cover CH%d for device %s", channel, serial_number[-8:])
                        elif device_type_code == 0x09:  # EWB_DT_QUAD_MOTOR
                            # Create covers for each motor channel
                            for channel in [1, 2, 3, 4]:
                                channel_spec = entity_spec.copy()
                                channel_spec["channel"] = channel
                                channel_spec["name"] = f"{entity_spec.get('name', 'Motor')} CH{channel}"
                                channel_spec["unique_id"] = f"{entity_spec.get('unique_id', serial_number)}_ch{channel}"
                                new_covers.append(EldatEWneoQuadMotorCover(coordinator, serial_number, device_info, channel_spec))
                                _LOGGER.info("✅ Created EWneo quad motor cover CH%d for device %s", channel, serial_number[-8:])
                        else:
                            new_covers.append(EldatEWneoCover(coordinator, serial_number, device_info, entity_spec))
                            _LOGGER.info("✅ Created EWneo cover entity for device %s", serial_number[-8:])
                    else:
                        new_covers.append(EldatCover(coordinator, serial_number, device_info, entity_spec))
            
            if new_covers:
                async_add_entities(new_covers)
                _LOGGER.info("Created %d cover entities for device %s", len(new_covers), serial_number[-6:])
                
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
            
            _LOGGER.info("🔧 Force creating cover entities for device %s", serial_number[-6:])
            
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
                _LOGGER.info("✅ Force-created %d cover entities for device %s", len(new_covers), serial_number[-6:])
            
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
        channel: int = 1,
    ) -> None:
        """Initialize the cover."""
        super().__init__(coordinator, serial_number, device_info)
        self._channel = channel
        self._attr_device_class = CoverDeviceClass.SHUTTER
        self._attr_supported_features = (
            CoverEntityFeature.OPEN |
            CoverEntityFeature.CLOSE |
            CoverEntityFeature.STOP
        )
        
        # Set unique ID and name
        if channel > 1:
            self._attr_unique_id = f"{serial_number}_cover_ch{channel}"
            self._attr_name = f"{device_info.get('name', 'Motor')} Channel {channel}"
        else:
            self._attr_unique_id = f"{serial_number}_cover"
            self._attr_name = f"{device_info.get('name', 'Motor')} Cover"
        
        self._attr_is_closed = None
        self._attr_is_closing = False
        self._attr_is_opening = False
    
    @property
    def is_closed(self) -> bool | None:
        """Return if cover is closed."""
        return self._attr_is_closed
    
    @property
    def is_closing(self) -> bool:
        """Return if cover is closing."""
        return self._attr_is_closing
    
    @property
    def is_opening(self) -> bool:
        """Return if cover is opening."""
        return self._attr_is_opening
    
    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        try:
            device_instance = self.coordinator.device_registry.get_device_instance(self._serial_number)
            if device_instance and hasattr(device_instance, 'open_cover'):
                self._attr_is_opening = True
                self._attr_is_closing = False
                self.async_write_ha_state()
                
                success = await device_instance.open_cover(self._channel)
                if success:
                    _LOGGER.debug("Cover %s channel %d opened successfully", 
                                self._serial_number[-6:], self._channel)
                else:
                    _LOGGER.warning("Failed to open cover %s channel %d", 
                                  self._serial_number[-6:], self._channel)
                
                # Reset opening state after command
                self._attr_is_opening = False
                self._attr_is_closed = False
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Error opening cover %s channel %d: %s", 
                         self._serial_number[-6:], self._channel, e)
            self._attr_is_opening = False
            self.async_write_ha_state()
    
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        try:
            device_instance = self.coordinator.device_registry.get_device_instance(self._serial_number)
            if device_instance and hasattr(device_instance, 'close_cover'):
                self._attr_is_closing = True
                self._attr_is_opening = False
                self.async_write_ha_state()
                
                success = await device_instance.close_cover(self._channel)
                if success:
                    _LOGGER.debug("Cover %s channel %d closed successfully", 
                                self._serial_number[-6:], self._channel)
                else:
                    _LOGGER.warning("Failed to close cover %s channel %d", 
                                  self._serial_number[-6:], self._channel)
                
                # Reset closing state after command
                self._attr_is_closing = False
                self._attr_is_closed = True
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Error closing cover %s channel %d: %s", 
                         self._serial_number[-6:], self._channel, e)
            self._attr_is_closing = False
            self.async_write_ha_state()
    
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        try:
            device_instance = self.coordinator.device_registry.get_device_instance(self._serial_number)
            if device_instance and hasattr(device_instance, 'stop_cover'):
                self._attr_is_closing = False
                self._attr_is_opening = False
                self.async_write_ha_state()
                
                success = await device_instance.stop_cover(self._channel)
                if success:
                    _LOGGER.debug("Cover %s channel %d stopped successfully", 
                                self._serial_number[-6:], self._channel)
                else:
                    _LOGGER.warning("Failed to stop cover %s channel %d", 
                                  self._serial_number[-6:], self._channel)
        except Exception as e:
            _LOGGER.error("Error stopping cover %s channel %d: %s", 
                         self._serial_number[-6:], self._channel, e)
            self._attr_is_closing = False
            self._attr_is_opening = False
            self.async_write_ha_state()
    
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Update cover state based on coordinator data
        device_data = self.coordinator.devices.get(self._serial_number, {})
        
        # Check for motor state updates
        channels = device_data.get("channels", {})
        channel_data = channels.get(str(self._channel), {})
        
        # Update position if available
        if "position" in channel_data:
            position = channel_data["position"]
            self._attr_is_closed = position <= 5  # Consider closed if position <= 5%
        
        self.async_write_ha_state()


class EldatEWneoCover(EldatEntity, CoverEntity):
    """EWneo cover entity with bidirectional EWB_CHANGE_STATE control for motors."""

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
        
        _LOGGER.info("🔧 Initializing EWneo cover: %s (gateway: %s, type_code: 0x%02X)", 
                    serial_number[-8:], self._gateway_serial[-8:] if self._gateway_serial else "None", self._device_type_code)
        
        # Initialize state from device's initial_state if available
        initial_state = device_info.get("initial_state", {})
        if initial_state.get("type") == "cover":
            self._current_cover_position = initial_state.get("position")
            self._runtime_measured = initial_state.get("runtime_measured", False)
            _LOGGER.info("🎯 EWneo cover %s: Loaded initial state: position=%s, runtime_measured=%s", 
                        serial_number[-8:], self._current_cover_position, self._runtime_measured)
        
        # Set up entity attributes
        self._attr_name = entity_spec.get("name", f"EWneo Cover {serial_number[-6:]}")
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_ewneo_cover_{self._channel}")
        self._attr_device_class = CoverDeviceClass.SHUTTER
        
        # Set supported features based on runtime measurement
        self._update_supported_features()
        
        _LOGGER.info("✅ EWneo cover entity initialized: %s (%s)", self._attr_name, self._attr_unique_id)
        
    async def _async_query_initial_state(self) -> None:
        """Query initial motor state to check for runtime measurement capability."""
        try:
            _LOGGER.info("🔍 EWneo cover %s: Querying initial state to check runtime measurement", self._serial_number[-8:])
            
            # Use coordinator to perform EwbQueryState
            result = await self.coordinator._query_ewneo_state(
                self._gateway_serial,
                self._serial_number
            )
            
            if result:
                _LOGGER.info("✅ EWneo cover %s: Initial query successful, state will be processed via event", self._serial_number[-8:])
            else:
                _LOGGER.warning("⚠️ EWneo cover %s: Initial state query failed", self._serial_number[-8:])
                
        except Exception as e:
            _LOGGER.error("❌ EWneo cover %s: Error during initial state query: %s", self._serial_number[-8:], e)
        
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
        
        Returns None if position is unknown.
        """
        if self._current_cover_position is not None:
            return self._current_cover_position == 0
        return None
    
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        attrs = {
            "motor_status_code": self._motor_status_code,
            "runtime_measured": self._runtime_measured,
            "runtime_ever_measured": self._runtime_ever_measured,
            "tilt_measured": self._tilt_measured,
            "terrace_function": self._terrace_function,
            "device_type_code": f"0x{self._device_type_code:02X}",
            "supports_positioning": self._runtime_ever_measured,
            "supports_tilt": self._tilt_measured
        }
        
        # Add position info if available
        if self._runtime_measured:
            if self._current_cover_position is not None:
                attrs["position_status"] = "known"
            else:
                if self._motor_status_code in [117, 118]:
                    attrs["position_status"] = "calibrating"
                elif self._motor_status_code in [122, 123]:
                    attrs["position_status"] = "120s_movement"
                else:
                    attrs["position_status"] = "temporarily_unknown"
        else:
            attrs["position_status"] = "no_runtime_measurement"
        
        return attrs
    
    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._available and self.coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, set up event listeners."""
        await super().async_added_to_hass()
        
        # Listen for EWneo state update events
        def handle_ewneo_state_update(event):
            """Handle EWneo state update events."""
            if event.data.get("serial_number") == self._serial_number:
                _LOGGER.info("🔄 EWneo cover %s: Received state update event", self._serial_number[-8:])
                parsed_state = event.data.get("parsed_state", {})
                if parsed_state and parsed_state.get("type") == "cover":
                    # Use thread-safe add_job to schedule state update
                    self.hass.add_job(self._async_update_state_from_parsed, parsed_state)
        
        # Register the event listener
        self.hass.bus.async_listen(f"{DOMAIN}_ewneo_state_update", handle_ewneo_state_update)
        _LOGGER.debug("🔗 EWneo cover %s: Registered state update event listener", self._serial_number[-8:])
        
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
                _LOGGER.info("🎯 EWneo cover %s: Runtime measurement DETECTED and ACTIVATED! Position control now available (from state update)", self._serial_number[-8:])
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
                _LOGGER.info("🎯 EWneo cover %s: Runtime measurement DETECTED from position data! Position control activated", self._serial_number[-8:])
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
            _LOGGER.info("🎯 EWneo motor %s: State update - Status: %s, Runtime: %s->%s, Ever: %s->%s, Position: %s, Features: %s", 
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
                    
                _LOGGER.debug("🎯 EWneo motor %s: %s - Position: %s%s [Runtime: ✓, Features: %s]", 
                            self._serial_number[-8:], motor_status, pos_text, target_text,
                            "Pos+Tilt" if self._tilt_measured else "Pos")
            else:
                _LOGGER.debug("🎯 EWneo motor %s: %s - Positionless mode [No runtime measurement]", 
                            self._serial_number[-8:], motor_status)
            
            self.async_write_ha_state()

    def _create_motor_position_command(self, position: int, tilt_after: bool = False) -> tuple[int, list]:
        """Create position command for EWneo motor according to EWB_CHANGE_STATE specification.
        
        Args:
            position: 0-100 where 0=open, 100=closed
            tilt_after: Whether to tilt slats after positioning (if tilt measurement done)
        """
        if not self._runtime_ever_measured and not self._runtime_measured:
            _LOGGER.warning("EWneo cover %s: Position control requires runtime measurement to be activated first (runtime=%s, ever=%s)", 
                           self._serial_number[-6:], self._runtime_measured, self._runtime_ever_measured)
            return None, None
            
        if not (0 <= position <= 100):
            _LOGGER.warning("EWneo cover %s: Invalid position %d, must be 0-100", self._serial_number[-6:], position)
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
                     self._serial_number[-6:], position, tilt_after, self._runtime_measured)
        
        return (0, state_bytes)
    
    def _create_motor_movement_command(self, command: str) -> tuple[int, list]:
        """Create movement command for EWneo motor.
        
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
            _LOGGER.warning("EWneo cover %s: Unknown command '%s'", self._serial_number[-6:], command)
            return None, None
        
        command_code = command_codes[command]
        
        # Check if command requires specific measurements
        if command == "stop_tilt" and not self._tilt_measured:
            _LOGGER.warning("EWneo cover %s: Tilt stop requires tilt measurement", self._serial_number[-6:])
            command_code = 126  # Fallback to simple stop
        elif command in ["open_runtime", "close_runtime"] and not self._runtime_measured:
            _LOGGER.warning("EWneo cover %s: Runtime commands require runtime measurement, using 120s fallback", 
                           self._serial_number[-6:])
            command_code = 122 if command == "open_runtime" else 123
        
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
                     self._serial_number[-6:], command, command_code)
        
        return (0, state_bytes)
    
    def _create_terrace_function_command(self, enable: bool) -> tuple[int, list]:
        """Create terrace function command for EWneo motor.
        
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
                     self._serial_number[-6:], "enable" if enable else "disable")
        
        return (2, state_bytes)

    async def _send_ewb_change_state(self, mode: int, state_bytes: list) -> bool:
        """Send EWB_CHANGE_STATE command to EWneo motor."""
        if not self._gateway_serial:
            _LOGGER.error("EWneo cover %s: No gateway serial available for EWB command", self._serial_number[-6:])
            return False
        
        try:
            _LOGGER.info("🔄 Sending EWB_CHANGE_STATE to EWneo cover %s: mode=%d, state_bytes=%s", 
                        self._serial_number[-6:], mode, [f"0x{b:02X}" for b in state_bytes])
            
            # Send command via coordinator's transceiver
            result = await self.coordinator.transceiver.rx11_ewb_change_state(
                self._gateway_serial, self._serial_number, mode, state_bytes
            )
            
            if result:
                response_mode, response_state_bytes = result
                _LOGGER.info("✅ EWneo cover %s: Received EWB response - mode=%d, state_bytes=%s", 
                            self._serial_number[-6:], response_mode, [f"0x{b:02X}" for b in response_state_bytes])
                
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
                                    self._serial_number[-6:], 
                                    parsed_state.get("position"),
                                    parsed_state.get("runtime_measured"),
                                    parsed_state.get("motor_status"))
                    else:
                        _LOGGER.warning("⚠️ EWneo cover %s: Failed to parse EWB response state", self._serial_number[-6:])
                        
                    # Also fire an event for consistency with other state updates
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_ewneo_state_update",
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
                                   self._serial_number[-6:], len(response_state_bytes))
                
                # Force immediate Home Assistant state update
                self.async_write_ha_state()
                return True
            else:
                _LOGGER.error("❌ EWneo cover %s: EWB_CHANGE_STATE command failed", self._serial_number[-6:])
                return False
                
        except Exception as e:
            _LOGGER.error("❌ Failed to set EWneo cover %s state: %s", self._serial_number[-6:], e)
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
            self._is_opening = True
            self._is_closing = False
            self.async_write_ha_state()  # Update UI immediately
            await self._send_ewb_change_state(mode, state_bytes)
    
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close cover."""
        if self._runtime_measured:
            # Use runtime-based closing
            mode, state_bytes = self._create_motor_movement_command("close_runtime")
        else:
            # Fallback to 120s closing
            mode, state_bytes = self._create_motor_movement_command("close_120s")
        
        if mode is not None:
            self._is_opening = False
            self._is_closing = True
            self.async_write_ha_state()  # Update UI immediately
            await self._send_ewb_change_state(mode, state_bytes)
    
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        # Check for tilt parameter
        tilt = kwargs.get("tilt", False)
        
        if tilt and self._tilt_measured:
            mode, state_bytes = self._create_motor_movement_command("stop_tilt")
        else:
            mode, state_bytes = self._create_motor_movement_command("stop")
        
        if mode is not None:
            self._is_opening = False
            self._is_closing = False
            self.async_write_ha_state()  # Update UI immediately
            await self._send_ewb_change_state(mode, state_bytes)
    
    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Tilt the cover to horizontal position (open tilt)."""
        if not self._tilt_measured:
            _LOGGER.warning("EWneo cover %s: Tilt control requires tilt measurement", self._serial_number[-6:])
            return
            
        # For EWneo, "open tilt" means tilt to horizontal
        mode, state_bytes = self._create_motor_movement_command("stop_tilt")
        if mode is not None:
            self._is_opening = False
            self._is_closing = False
            self.async_write_ha_state()
            await self._send_ewb_change_state(mode, state_bytes)
    
    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close tilt (return to normal position)."""
        if not self._tilt_measured:
            _LOGGER.warning("EWneo cover %s: Tilt control requires tilt measurement", self._serial_number[-6:])
            return
            
        # For EWneo, "close tilt" means return to normal (no tilt)
        mode, state_bytes = self._create_motor_movement_command("stop")
        if mode is not None:
            self._is_opening = False
            self._is_closing = False  
            self.async_write_ha_state()
            await self._send_ewb_change_state(mode, state_bytes)
    
    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        position = kwargs.get(ATTR_POSITION)
        if position is None:
            _LOGGER.warning("EWneo cover %s: No position specified", self._serial_number[-6:])
            return
        
        # Check if runtime measurement is available
        if not self._runtime_measured and not self._runtime_ever_measured:
            _LOGGER.warning("EWneo cover %s: Position control requires runtime measurement. Current status: runtime=%s, ever=%s", 
                         self._serial_number[-6:], self._runtime_measured, self._runtime_ever_measured)
            
            # Try to query current state to check for runtime measurement
            _LOGGER.info("🔍 EWneo cover %s: Attempting to query current state to detect runtime measurement", self._serial_number[-6:])
            query_result = await self.coordinator._query_ewneo_state(self._gateway_serial, self._serial_number)
            
            if query_result:
                _LOGGER.info("✅ EWneo cover %s: State query sent, waiting for response to update runtime status", self._serial_number[-6:])
                # Give some time for the response to be processed
                await asyncio.sleep(0.5)
            
            # Check again after query
            if not self._runtime_measured and not self._runtime_ever_measured:
                _LOGGER.error("EWneo cover %s: Runtime measurement still not detected. Fallback to simple open/close based on position", self._serial_number[-6:])
                # Fallback to simple open/close based on position
                if position >= 50:
                    await self.async_open_cover()
                else:
                    await self.async_close_cover()
                return
            else:
                _LOGGER.info("✅ EWneo cover %s: Runtime measurement detected after query! Proceeding with position control", self._serial_number[-6:])
        
        # Check for tilt parameter
        tilt_after = kwargs.get("tilt_after", False)
        
        # Convert Home Assistant position (0=closed, 100=open) to protocol position (0=open, 100=closed)
        protocol_position = 100 - position
        
        _LOGGER.info("EWneo cover %s: Setting position to %d%% (protocol: %d%%), tilt_after=%s", 
                     self._serial_number[-6:], position, protocol_position, tilt_after)
        
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
                            self._serial_number[-6:], "enabled" if enable else "disabled")
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
                         self._serial_number[-6:], e)


class EldatEWneoDualMotorCover(EldatEntity, CoverEntity):
    """EWneo dual motor cover entity with individual motor control and runtime measurement activation."""

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
        
        _LOGGER.info("🔧 Initializing EWneo dual motor cover CH%d: %s (gateway: %s, type_code: 0x%02X)", 
                    self._channel, serial_number[-8:], self._gateway_serial[-8:] if self._gateway_serial else "None", self._device_type_code)
        
        # Set up entity attributes
        self._attr_name = entity_spec.get("name", f"EWneo Dual Motor CH{self._channel} {serial_number[-6:]}")
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_ewneo_dual_motor_ch{self._channel}")
        self._attr_device_class = CoverDeviceClass.SHUTTER
        
        # Set supported features based on runtime measurement
        self._update_supported_features()
        
        _LOGGER.info("✅ EWneo dual motor cover entity CH%d initialized: %s (%s)", self._channel, self._attr_name, self._attr_unique_id)
        
    async def _async_query_initial_state(self) -> None:
        """Query initial motor state for this specific channel to check for runtime measurement capability."""
        try:
            _LOGGER.info("🔍 EWneo dual motor CH%d %s: Querying initial state for runtime measurement", 
                        self._channel, self._serial_number[-8:])
            
            # Mode mapping: Mode 2 = Motor 1, Mode 10 = Motor 2
            query_mode = 2 if self._channel == 1 else 10
            
            # Use coordinator to perform individual motor EwbQueryState
            result = await self.coordinator._query_ewneo_state_with_mode(
                self._gateway_serial,
                self._serial_number,
                query_mode
            )
            
            if result:
                _LOGGER.info("✅ EWneo dual motor CH%d %s: Individual motor query successful, state will be processed via event", 
                            self._channel, self._serial_number[-8:])
            else:
                _LOGGER.warning("⚠️ EWneo dual motor CH%d %s: Individual motor state query failed", 
                               self._channel, self._serial_number[-8:])
                
        except Exception as e:
            _LOGGER.error("❌ EWneo dual motor CH%d %s: Error during initial state query: %s", 
                         self._channel, self._serial_number[-8:], e)
        
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
            
        # Add tilt control if tilt measurement is available (binary tilt for EWneo)
        if self._tilt_measured:
            features |= CoverEntityFeature.OPEN_TILT | CoverEntityFeature.CLOSE_TILT
            
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
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        attrs = {
            "motor_channel": self._channel,
            "motor_status_code": self._motor_status_code,
            "runtime_measured": self._runtime_measured,
            "runtime_ever_measured": self._runtime_ever_measured,
            "tilt_measured": self._tilt_measured,
            "terrace_function": self._terrace_function,
            "device_type_code": f"0x{self._device_type_code:02X}",
            "supports_positioning": self._runtime_ever_measured,
        }
        
        if self._recent_tilt:
            attrs["recent_tilt"] = True
            
        if self._stored_position:
            attrs["stored_position"] = self._stored_position
            
        return attrs

    async def async_added_to_hass(self) -> None:
        """Handle entity added to hass."""
        await super().async_added_to_hass()
        
        # Subscribe to EWneo state updates for dual motor devices
        async def handle_ewneo_state_update(event):
            try:
                event_data = event.data
                if event_data.get("serial_number") == self._serial_number:
                    # Extract channel-specific data from dual motor response
                    parsed_state = self._extract_channel_state(event_data.get("parsed_state", {}))
                    if parsed_state:
                        self.hass.add_job(self._async_update_state_from_parsed, parsed_state)
            except Exception as e:
                _LOGGER.error("Error in dual motor state update handler: %s", e)
        
        self.hass.bus.async_listen(f"{DOMAIN}_ewneo_state_update", handle_ewneo_state_update)
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
        old_position = self._current_cover_position
        old_is_opening = self._is_opening
        old_is_closing = self._is_closing
        old_features = self._attr_supported_features
        
        # Update position (only if available and valid)
        if parsed_state.get("position_available", False) and "position" in parsed_state:
            self._current_cover_position = parsed_state["position"]
        elif not parsed_state.get("runtime_measured", False):
            # No runtime measurement - position unknown
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
            
            self.async_write_ha_state()

    async def _send_ewb_change_state(self, mode: int, state_bytes: list) -> bool:
        """Send EWB_CHANGE_STATE command to EWneo dual motor."""
        if not self._gateway_serial:
            _LOGGER.error("EWneo dual motor %s CH%d: No gateway serial available", self._serial_number[-6:], self._channel)
            return False
        
        try:
            _LOGGER.info("🔄 Sending EWB_CHANGE_STATE to EWneo dual motor %s CH%d: mode=%d, state_bytes=%s", 
                        self._serial_number[-6:], self._channel, mode, [f"0x{b:02X}" for b in state_bytes])
            
            # Send command via coordinator's transceiver
            result = await self.coordinator.transceiver.rx11_ewb_change_state(
                self._gateway_serial, self._serial_number, mode, state_bytes
            )
            
            if result:
                response_mode, response_state_bytes = result
                _LOGGER.info("✅ EWneo dual motor %s CH%d: Received EWB response - mode=%d, state_bytes=%s", 
                            self._serial_number[-6:], self._channel, response_mode, [f"0x{b:02X}" for b in response_state_bytes])
                
                # Parse response for dual motor state
                if len(response_state_bytes) >= 4:
                    parsed_state = self.coordinator._parse_ewneo_state(
                        self._device_type_code, response_state_bytes[:4], "ewneo_dual_motor"
                    )
                    
                    if parsed_state and parsed_state.get("type") == "multi_cover":
                        # Extract our channel's state from dual motor response
                        channel_state = self._extract_channel_state(parsed_state)
                        
                        if channel_state:
                            # Update state immediately
                            await self._async_update_state_from_parsed(channel_state)
                            _LOGGER.info("🎯 EWneo dual motor %s CH%d: State updated from EWB response - opening=%s, closing=%s, stopped=%s", 
                                        self._serial_number[-6:], self._channel,
                                        channel_state.get("is_opening", False),
                                        channel_state.get("is_closing", False),
                                        channel_state.get("is_stopped", True))
                        else:
                            _LOGGER.warning("⚠️ EWneo dual motor %s CH%d: Could not extract channel state from response", 
                                           self._serial_number[-6:], self._channel)
                    else:
                        _LOGGER.warning("⚠️ EWneo dual motor %s CH%d: Failed to parse EWB response state", 
                                       self._serial_number[-6:], self._channel)
                else:
                    _LOGGER.warning("⚠️ EWneo dual motor %s CH%d: EWB response too short (%d bytes, expected 4+)", 
                                   self._serial_number[-6:], self._channel, len(response_state_bytes))
                
                # Force immediate Home Assistant state update
                self.async_write_ha_state()
                return True
            else:
                _LOGGER.error("❌ EWneo dual motor %s CH%d: EWB_CHANGE_STATE command failed", 
                             self._serial_number[-6:], self._channel)
                return False
                
        except Exception as e:
            _LOGGER.error("❌ Failed to set EWneo dual motor %s CH%d state: %s", 
                         self._serial_number[-6:], self._channel, e)
            return False
    
    def _create_dual_motor_command(self, channel: int, command: str) -> tuple[int, list]:
        """Create dual motor command for specific channel.
        
        Args:
            channel: 1 or 2
            command: 'stop', 'open', 'close'
        """
        if channel not in [1, 2]:
            _LOGGER.error("Invalid channel %d for dual motor, must be 1 or 2", channel)
            return None, None
            
        # Mode 0: Dual motor commands
        # 2 bits per channel: 00=stop, 01=open, 10=close, 11=reserved
        command_map = {
            "stop": 0,
            "open": 1,
            "close": 2
        }
        
        if command not in command_map:
            _LOGGER.error("Invalid command '%s' for dual motor", command)
            return None, None
            
        cmd_code = command_map[command]
        
        # Channel 1 uses bits 1-0, Channel 2 uses bits 3-2
        if channel == 1:
            state_word = cmd_code
        else:  # channel == 2
            state_word = cmd_code << 2
            
        # Convert to 4 bytes in big-endian order
        state_bytes = [
            (state_word >> 24) & 0xFF,
            (state_word >> 16) & 0xFF,
            (state_word >> 8) & 0xFF,
            state_word & 0xFF
        ]
        
        _LOGGER.debug("Dual motor CH%d command - %s (bits: 0x%08X)", channel, command, state_word)
        return (0, state_bytes)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        mode, state_bytes = self._create_dual_motor_command(self._channel, "open")
        if mode is not None:
            self._is_opening = True
            self._is_closing = False
            self.async_write_ha_state()
            await self._send_ewb_change_state(mode, state_bytes)
        
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        mode, state_bytes = self._create_dual_motor_command(self._channel, "close")
        if mode is not None:
            self._is_opening = False
            self._is_closing = True
            self.async_write_ha_state()
            await self._send_ewb_change_state(mode, state_bytes)
        
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        mode, state_bytes = self._create_dual_motor_command(self._channel, "stop")
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
            
        if not self._runtime_ever_measured:
            _LOGGER.warning("EWneo dual motor CH%d %s: Position control requires runtime measurement activation", 
                           self._channel, self._serial_number[-6:])
            return
            
        _LOGGER.info("Setting EWneo dual motor CH%d %s to position %d%%", 
                    self._channel, self._serial_number[-6:], position)
        # TODO: Implement EWB_CHANGE_STATE command for dual motor positioning


class EldatEWneoQuadMotorCover(EldatEntity, CoverEntity):
    """EWneo quad motor cover entity with individual motor control and runtime measurement activation."""

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
        
        _LOGGER.info("🔧 Initializing EWneo quad motor cover CH%d: %s (gateway: %s, type_code: 0x%02X)", 
                    self._channel, serial_number[-8:], self._gateway_serial[-8:] if self._gateway_serial else "None", self._device_type_code)
        
        # Set up entity attributes
        self._attr_name = entity_spec.get("name", f"EWneo Quad Motor CH{self._channel} {serial_number[-6:]}")
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_ewneo_quad_motor_ch{self._channel}")
        self._attr_device_class = CoverDeviceClass.SHUTTER
        
        # Set supported features based on runtime measurement
        self._update_supported_features()
        
        _LOGGER.info("✅ EWneo quad motor cover entity CH%d initialized: %s (%s)", self._channel, self._attr_name, self._attr_unique_id)
        
    async def _async_query_initial_state(self) -> None:
        """Query initial motor state for this specific channel to check for runtime measurement capability."""
        try:
            _LOGGER.info("🔍 EWneo quad motor CH%d %s: Querying initial state for runtime measurement", 
                        self._channel, self._serial_number[-8:])
            
            # Mode mapping: Mode 2/10/18/26 for motors 1/2/3/4
            mode_map = {1: 2, 2: 10, 3: 18, 4: 26}
            query_mode = mode_map[self._channel]
            
            # Use coordinator to perform individual motor EwbQueryState
            result = await self.coordinator._query_ewneo_state_with_mode(
                self._gateway_serial,
                self._serial_number,
                query_mode
            )
            
            if result:
                _LOGGER.info("✅ EWneo quad motor CH%d %s: Individual motor query successful, state will be processed via event", 
                            self._channel, self._serial_number[-8:])
            else:
                _LOGGER.warning("⚠️ EWneo quad motor CH%d %s: Individual motor state query failed", 
                               self._channel, self._serial_number[-8:])
                
        except Exception as e:
            _LOGGER.error("❌ EWneo quad motor CH%d %s: Error during initial state query: %s", 
                         self._channel, self._serial_number[-8:], e)
        
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
            
        # Add tilt control if tilt measurement is available (binary tilt for EWneo)
        if self._tilt_measured:
            features |= CoverEntityFeature.OPEN_TILT | CoverEntityFeature.CLOSE_TILT
            
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
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        attrs = {
            "motor_channel": self._channel,
            "motor_status_code": self._motor_status_code,
            "runtime_measured": self._runtime_measured,
            "runtime_ever_measured": self._runtime_ever_measured,
            "tilt_measured": self._tilt_measured,
            "terrace_function": self._terrace_function,
            "device_type_code": f"0x{self._device_type_code:02X}",
            "supports_positioning": self._runtime_ever_measured,
        }
        
        if self._recent_tilt:
            attrs["recent_tilt"] = True
            
        if self._stored_position:
            attrs["stored_position"] = self._stored_position
            
        return attrs

    async def async_added_to_hass(self) -> None:
        """Handle entity added to hass."""
        await super().async_added_to_hass()
        
        # Subscribe to EWneo state updates for quad motor devices
        async def handle_ewneo_state_update(event):
            try:
                event_data = event.data
                if event_data.get("serial_number") == self._serial_number:
                    # Extract channel-specific data from quad motor response
                    parsed_state = self._extract_channel_state(event_data.get("parsed_state", {}))
                    if parsed_state:
                        self.hass.add_job(self._async_update_state_from_parsed, parsed_state)
            except Exception as e:
                _LOGGER.error("Error in quad motor state update handler: %s", e)
        
        self.hass.bus.async_listen(f"{DOMAIN}_ewneo_state_update", handle_ewneo_state_update)
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
        old_position = self._current_cover_position
        old_is_opening = self._is_opening
        old_is_closing = self._is_closing
        old_features = self._attr_supported_features
        
        # Update position (only if available and valid)
        if parsed_state.get("position_available", False) and "position" in parsed_state:
            self._current_cover_position = parsed_state["position"]
        elif not parsed_state.get("runtime_measured", False):
            # No runtime measurement - position unknown
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
            
            self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        _LOGGER.info("Opening EWneo quad motor CH%d %s", self._channel, self._serial_number[-6:])
        # TODO: Implement EWB_CHANGE_STATE command for quad motor opening
        
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        _LOGGER.info("Closing EWneo quad motor CH%d %s", self._channel, self._serial_number[-6:])
        # TODO: Implement EWB_CHANGE_STATE command for quad motor closing
        
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        _LOGGER.info("Stopping EWneo quad motor CH%d %s", self._channel, self._serial_number[-6:])
        # TODO: Implement EWB_CHANGE_STATE command for quad motor stop
        
    async def _send_ewb_change_state(self, mode: int, state_bytes: list) -> bool:
        """Send EWB_CHANGE_STATE command to EWneo quad motor."""
        if not self._gateway_serial:
            _LOGGER.error("EWneo quad motor %s CH%d: No gateway serial available", self._serial_number[-6:], self._channel)
            return False
        
        try:
            _LOGGER.info("🔄 Sending EWB_CHANGE_STATE to EWneo quad motor %s CH%d: mode=%d, state_bytes=%s", 
                        self._serial_number[-6:], self._channel, mode, [f"0x{b:02X}" for b in state_bytes])
            
            # Send command via coordinator's transceiver
            result = await self.coordinator.transceiver.rx11_ewb_change_state(
                self._gateway_serial, self._serial_number, mode, state_bytes
            )
            
            if result:
                response_mode, response_state_bytes = result
                _LOGGER.info("✅ EWneo quad motor %s CH%d: Received EWB response - mode=%d, state_bytes=%s", 
                            self._serial_number[-6:], self._channel, response_mode, [f"0x{b:02X}" for b in response_state_bytes])
                
                # Parse response for quad motor state
                if len(response_state_bytes) >= 4:
                    parsed_state = self.coordinator._parse_ewneo_state(
                        self._device_type_code, response_state_bytes[:4], "ewneo_quad_motor"
                    )
                    
                    if parsed_state and parsed_state.get("type") == "multi_cover":
                        # Extract our channel's state from quad motor response
                        channel_state = self._extract_channel_state(parsed_state)
                        
                        if channel_state:
                            # Update state immediately
                            await self._async_update_state_from_parsed(channel_state)
                            _LOGGER.info("🎯 EWneo quad motor %s CH%d: State updated from EWB response - opening=%s, closing=%s, stopped=%s", 
                                        self._serial_number[-6:], self._channel,
                                        channel_state.get("is_opening", False),
                                        channel_state.get("is_closing", False),
                                        channel_state.get("is_stopped", True))
                        else:
                            _LOGGER.warning("⚠️ EWneo quad motor %s CH%d: Could not extract channel state from response", 
                                           self._serial_number[-6:], self._channel)
                    else:
                        _LOGGER.warning("⚠️ EWneo quad motor %s CH%d: Failed to parse EWB response state", 
                                       self._serial_number[-6:], self._channel)
                else:
                    _LOGGER.warning("⚠️ EWneo quad motor %s CH%d: EWB response too short (%d bytes, expected 4+)", 
                                   self._serial_number[-6:], self._channel, len(response_state_bytes))
                
                # Force immediate Home Assistant state update
                self.async_write_ha_state()
                return True
            else:
                _LOGGER.error("❌ EWneo quad motor %s CH%d: EWB_CHANGE_STATE command failed", 
                             self._serial_number[-6:], self._channel)
                return False
                
        except Exception as e:
            _LOGGER.error("❌ Failed to set EWneo quad motor %s CH%d state: %s", 
                         self._serial_number[-6:], self._channel, e)
            return False
    
    def _create_quad_motor_command(self, channel: int, command: str) -> tuple[int, list]:
        """Create quad motor command for specific channel.
        
        Args:
            channel: 1, 2, 3, or 4
            command: 'stop', 'open', 'close'
        """
        if channel not in [1, 2, 3, 4]:
            _LOGGER.error("Invalid channel %d for quad motor, must be 1-4", channel)
            return None, None
            
        # Mode 0: Quad motor commands
        # 2 bits per channel: 00=stop, 01=open, 10=close, 11=reserved
        command_map = {
            "stop": 0,
            "open": 1,
            "close": 2
        }
        
        if command not in command_map:
            _LOGGER.error("Invalid command '%s' for quad motor", command)
            return None, None
            
        cmd_code = command_map[command]
        
        # Channel 1 uses bits 1-0, Channel 2 uses bits 3-2, etc.
        bit_offset = (channel - 1) * 2
        state_word = cmd_code << bit_offset
            
        # Convert to 4 bytes in big-endian order
        state_bytes = [
            (state_word >> 24) & 0xFF,
            (state_word >> 16) & 0xFF,
            (state_word >> 8) & 0xFF,
            state_word & 0xFF
        ]
        
        _LOGGER.debug("Quad motor CH%d command - %s (bits: 0x%08X)", channel, command, state_word)
        return (0, state_bytes)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        mode, state_bytes = self._create_quad_motor_command(self._channel, "open")
        if mode is not None:
            self._is_opening = True
            self._is_closing = False
            self.async_write_ha_state()
            await self._send_ewb_change_state(mode, state_bytes)
        
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        mode, state_bytes = self._create_quad_motor_command(self._channel, "close")
        if mode is not None:
            self._is_opening = False
            self._is_closing = True
            self.async_write_ha_state()
            await self._send_ewb_change_state(mode, state_bytes)
        
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
            
        if not self._runtime_ever_measured:
            _LOGGER.warning("EWneo quad motor CH%d %s: Position control requires runtime measurement activation", 
                           self._channel, self._serial_number[-6:])
            # Fallback to simple open/close
            if position >= 50:
                await self.async_open_cover()
            else:
                await self.async_close_cover()
            return
            
        _LOGGER.info("Setting EWneo quad motor CH%d %s to position %d%%", 
                    self._channel, self._serial_number[-6:], position)
        # TODO: Implement positioning for quad motors when specification is available