"""Switch entities for ELDAT integration."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, EVENT_DEVICE_ADDED, EVENT_FORCE_CREATE
from .coordinator import EldatCoordinator
from .entity import EldatEntity
from .device_icons import (
    get_entity_config_for_device,
    create_entity_name_with_action_hint,
    get_extra_state_attributes_for_device
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ELDAT switch entities."""
    coordinator: EldatCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    switches: List[SwitchEntity] = []

    # Restore switches from saved devices (both legacy and registered)
    for serial_number, device_info in coordinator.get_all_devices().items():
        # Always check for heating_cooling devices and create switch entities
        receiver_kind = device_info.get("receiver_kind")
        if receiver_kind == "heating_cooling":
            # Force creation of heating_cooling switches
            from .entity_specs import create_entity_specs_for_device
            entity_specs = create_entity_specs_for_device(serial_number, device_info)
            switch_entities = entity_specs.get("switch", [])
            
            for entity_spec in switch_entities:
                switches.append(EldatConfiguredSwitch(coordinator, serial_number, device_info, entity_spec))
            
            _LOGGER.info("🌡️ Restored %d heating/cooling switch entities for device %s", 
                       len([e for e in switches if e._serial_number == serial_number]), serial_number[-8:])
            continue
        
        # Skip devices that already have configured entities of any type (non-heating_cooling and non-EWneo)
        device_entities = device_info.get("entities", [])
        is_neo_device = device_info.get("neo_device", False)
        has_configured_entities = any(entity.get("type") in ["button", "light", "cover", "switch"] for entity in device_entities)
        
        # EWneo devices must always go through _create_switches_for_device for proper entity creation
        if not has_configured_entities or is_neo_device:
            device_switches = _create_switches_for_device(coordinator, serial_number, device_info)
            switches.extend(device_switches)
            if is_neo_device:
                _LOGGER.info("🔧 Created %d EWneo switch entities for device %s", len(device_switches), serial_number[-8:])
        else:
            _LOGGER.debug("Skipping legacy switch creation for %s - device has %d configured entities", serial_number[-8:], len(device_entities))

    if switches:
        async_add_entities(switches)
        _LOGGER.debug("Added %d switch entities", len(switches))

    # Generic handler for devices added events (fallback)
    async def _handle_device_added(event):
        try:
            serial_number = event.data.get("serial_number")
            device_info = event.data.get("device_info")
            entity_info = event.data.get("entity_info", {})
            platforms = entity_info.get("platforms", set())

            if not serial_number or not device_info:
                _LOGGER.debug("Device added event missing data, skipping")
                return

            # Skip if this device has platform-specific handler OR any configured entities
            device_entities = device_info.get("entities", [])
            has_configured_entities = any(entity.get("type") in ["button", "light", "cover", "switch"] for entity in device_entities)
            
            if "switch" in platforms or has_configured_entities:
                return

            new_switches = _create_switches_for_device(coordinator, serial_number, device_info)
            if new_switches:
                async_add_entities(new_switches)
                _LOGGER.info("Added %d legacy switch entities for device %s", len(new_switches), serial_number)

        except Exception:  # log inside
            _LOGGER.exception("Error handling generic device added event for switches")

    # Platform-specific handler
    async def _handle_switch_device_added(event):
        try:
            serial_number = event.data.get("serial_number")
            device_info = event.data.get("device_info")
            entities = event.data.get("entities", [])
            force_create = event.data.get("force_create", False)

            if not serial_number or not entities:
                _LOGGER.debug("Switch-specific device added event missing data")
                return

            # Check if this is an EWneo device - if so, create EWneo switch entities
            is_neo_device = device_info.get("neo_device", False)
            if is_neo_device:
                _LOGGER.info("🔧 Creating EWneo switch entities from event for device %s", serial_number[-8:])
                new_switches = []
                for entity_spec in entities:
                    if entity_spec.get("type") == "switch":
                        new_switches.append(EldatEWneoSwitch(coordinator, serial_number, device_info, entity_spec))
                
                if new_switches:
                    async_add_entities(new_switches)
                    _LOGGER.info("✅ Created %d EWneo switch entities for device %s", len(new_switches), serial_number[-8:])
                return

            # Regular EW devices
            new_switches: List[SwitchEntity] = []
            for entity_spec in entities:
                if entity_spec.get("type") == "switch":
                    new_switches.append(
                        EldatConfiguredSwitch(
                            coordinator=coordinator,
                            serial_number=serial_number,
                            device_info=device_info,
                            entity_spec=entity_spec,
                        )
                    )

            if new_switches:
                async_add_entities(new_switches)
                _LOGGER.info("Created %d switch entities for device %s (force: %s)", 
                           len(new_switches), serial_number[-6:], force_create)

        except Exception:
            _LOGGER.exception("Error creating switch entities from event")

    # Force create handler
    async def _handle_force_create(event):
        try:
            serial_number = event.data.get("serial_number")
            entity_type = event.data.get("entity_type")
            device_info = event.data.get("device_info")
            force_repair = event.data.get("force_repair", False)

            if entity_type != "switch" or not serial_number or not device_info:
                return

            _LOGGER.info("🔧 Force creating switch entities for device %s (repair: %s)", 
                       serial_number[-6:], force_repair)

            # Create switch entities for this device using entity specs
            from .entity_specs import create_entity_specs_for_device
            entity_specs = create_entity_specs_for_device(serial_number, device_info)
            switch_entities = entity_specs.get("switch", [])
            
            new_switches = []
            for entity_spec in switch_entities:
                new_switches.append(
                    EldatConfiguredSwitch(
                        coordinator=coordinator,
                        serial_number=serial_number,
                        device_info=device_info,
                        entity_spec=entity_spec,
                    )
                )
            
            if new_switches:
                async_add_entities(new_switches)
                _LOGGER.info("✅ Force-created %d switch entities for device %s", 
                           len(new_switches), serial_number[-6:])
            else:
                _LOGGER.warning("⚠️ No switch entity specs found for device %s", serial_number[-6:])

        except Exception:
            _LOGGER.exception("Error force-creating switch entities")

    # Also listen for registered device events
    async def _handle_registered_device_added(event):
        try:
            serial_number = event.data.get("serial_number")
            device_info = event.data.get("device_info")
            
            if not serial_number or not device_info:
                return
                
            # Check if this is a heating_cooling device
            receiver_kind = device_info.get("receiver_kind")
            if device_info.get("type") == "ew_receiver" and receiver_kind == "heating_cooling":
                _LOGGER.info("🌡️ Registered heating/cooling device detected: %s", serial_number[-6:])
                
                # Generate switch entities
                from .entity_specs import create_entity_specs_for_device
                entity_specs = create_entity_specs_for_device(serial_number, device_info)
                switch_entities = entity_specs.get("switch", [])
                
                new_switches = []
                for entity_spec in switch_entities:
                    new_switches.append(
                        EldatConfiguredSwitch(
                            coordinator=coordinator,
                            serial_number=serial_number,
                            device_info=device_info,
                            entity_spec=entity_spec,
                        )
                    )
                
                if new_switches:
                    async_add_entities(new_switches)
                    _LOGGER.info("✅ Created %d heating/cooling switch entities for registered device %s", 
                               len(new_switches), serial_number[-6:])
        except Exception:
            _LOGGER.exception("Error handling registered device for switches")

    config_entry.async_on_unload(hass.bus.async_listen(EVENT_DEVICE_ADDED, _handle_device_added))
    config_entry.async_on_unload(hass.bus.async_listen(f"{EVENT_DEVICE_ADDED}_switch", _handle_switch_device_added))
    config_entry.async_on_unload(hass.bus.async_listen(EVENT_FORCE_CREATE, _handle_force_create))
    config_entry.async_on_unload(hass.bus.async_listen(f"{DOMAIN}_device_registered", _handle_registered_device_added))
    config_entry.async_on_unload(hass.bus.async_listen(f"{EVENT_DEVICE_ADDED}_switch", _handle_switch_device_added))
    config_entry.async_on_unload(hass.bus.async_listen(EVENT_FORCE_CREATE, _handle_force_create))
    config_entry.async_on_unload(hass.bus.async_listen(f"{DOMAIN}_device_registered", _handle_registered_device_added))


def _create_switches_for_device(coordinator: EldatCoordinator, serial_number: str, device_info: Dict[str, Any]) -> List[SwitchEntity]:
    """Create switch entities for a device based on stored device_info.
    
    Only creates switches for devices that have entities with type='switch' in their spec.
    Dimmers (type='light') and Motors (type='cover') are handled by their respective platforms.
    Sensors (ew_sensor) don't get switches at all - they only provide sensor data.
    """
    switches: List[SwitchEntity] = []
    device_type = device_info.get("type", "unknown")
    is_neo_device = device_info.get("neo_device", False)
    
    _LOGGER.info("🔍 Creating switches for device %s: type=%s, neo_device=%s", 
                serial_number[-8:], device_type, is_neo_device)
    
    # Skip sensors entirely - they should not have switch entities
    if device_type == "ew_sensor":
        return switches
    
    # Skip EW transmitters entirely - they should only have binary sensor entities
    if device_type == "ew_transmitter":
        return switches
    
    # For EW-Receivers, only create switches if explicitly configured as switches
    if device_type == "ew_receiver":
        entity_specs = device_info.get("entities", [])
        
        # Check if there are any configured entities
        if entity_specs:
            # Only create switch entities that are explicitly configured
            for spec in entity_specs:
                if spec.get("type") == "switch":
                    switches.append(EldatConfiguredSwitch(coordinator, serial_number, device_info, spec))
                    
            # For heating/cooling receivers, always create a switch even if not explicitly configured
            # but only if no switch entities were already created
            if not switches:
                receiver_kind = device_info.get("receiver_kind", "switch")
                if receiver_kind in ["heating", "cooling", "heating_cooling"]:
                    switches.append(EldatSwitch(coordinator, serial_number, device_info, 0))
                    _LOGGER.info("✅ Created default switch for configured EW-Receiver %s (kind: %s)", serial_number[-6:], receiver_kind)
            return switches
        else:
            # Legacy device without configured entities - create switch for switch type and heating/cooling receivers
            receiver_kind = device_info.get("receiver_kind", "switch")
            if receiver_kind in ["switch", "heating", "cooling", "heating_cooling"]:
                switches.append(EldatSwitch(coordinator, serial_number, device_info, 0))
                _LOGGER.info("✅ Created legacy switch for EW-Receiver %s (kind: %s)", serial_number[-6:], receiver_kind)
            return switches
    
    # Handle EWneo devices (bidirectional receivers)
    is_neo_device = device_info.get("neo_device", False)
    _LOGGER.info("🔍 Device %s: neo_device=%s, type=%s", serial_number[-8:], is_neo_device, device_info.get("type", "unknown"))
    
    if is_neo_device:
        entity_specs = device_info.get("entities", [])
        _LOGGER.info("📋 EWneo device %s: Found %d entity specs", serial_number[-8:], len(entity_specs))
        
        # Check if there are configured entities with type=switch
        for spec in entity_specs:
            entity_type = spec.get("type")
            _LOGGER.info("🔧 Creating entity type '%s' for EWneo device %s", entity_type, serial_number[-8:])
            if entity_type == "switch":
                switch_entity = EldatEWneoSwitch(coordinator, serial_number, device_info, spec)
                switches.append(switch_entity)
                _LOGGER.info("✅ Created EWneo switch entity for device %s (total: %d)", serial_number[-8:], len(switches))
            elif entity_type == "button":
                _LOGGER.warning("⚠️ Button entity found for EWneo device %s - buttons should not send EW commands", serial_number[-8:])
        
        _LOGGER.info("📊 EWneo device %s: Created %d switch entities", serial_number[-8:], len(switches))
        return switches
    
    # For other device types (legacy handling)
    channels = int(device_info.get("channels", 1))
    for channel in range(max(1, channels)):
        switches.append(EldatSwitch(coordinator, serial_number, device_info, channel))

    return switches


class EldatEWneoSwitch(EldatEntity, SwitchEntity):
    """EWneo switch entity with bidirectional EWB_CHANGE_STATE control."""

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
        self._is_on = False
        
        _LOGGER.info("🔧 Initializing EWneo switch: %s (gateway: %s, type_code: 0x%02X)", 
                    serial_number[-8:], self._gateway_serial[-8:] if self._gateway_serial else "None", self._device_type_code)
        
        # Initialize state from device's initial_state if available
        initial_state = device_info.get("initial_state", {})
        if initial_state.get("type") == "switch":
            self._is_on = initial_state.get("on", False)
            _LOGGER.info("🎯 EWneo switch %s: Loaded initial state: %s", serial_number[-8:], "ON" if self._is_on else "OFF")
        
        # Set up entity attributes
        self._attr_name = entity_spec.get("name", f"EWneo Switch {serial_number[-6:]}")
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_ewneo_switch_{self._channel}")
        self._attr_device_class = SwitchDeviceClass.SWITCH
        
        _LOGGER.info("✅ EWneo switch entity initialized: %s (%s)", self._attr_name, self._attr_unique_id)
        
    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        return self._is_on
    
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
                _LOGGER.info("🔄 EWneo switch %s: Received state update event", self._serial_number[-8:])
                parsed_state = event.data.get("parsed_state", {})
                if parsed_state and parsed_state.get("type") == "switch":
                    # Use thread-safe add_job to schedule state update
                    self.hass.add_job(self._async_update_state_from_parsed, parsed_state)
        
        # Register the event listener
        self.hass.bus.async_listen(f"{DOMAIN}_ewneo_state_update", handle_ewneo_state_update)
        _LOGGER.debug("🔗 EWneo switch %s: Registered state update event listener", self._serial_number[-8:])
    
    async def _async_update_state_from_parsed(self, parsed_state: Dict[str, Any]) -> None:
        """Update switch state from parsed EWneo state (async for thread safety)."""
        old_state = self._is_on
        self._is_on = parsed_state.get("on", self._is_on)
        
        if old_state != self._is_on:
            _LOGGER.info("🎯 EWneo switch %s: State updated to %s (from event)", 
                       self._serial_number[-8:], "ON" if self._is_on else "OFF")
            self.async_write_ha_state()
    
    def _create_switch_state_command(self, turn_on: bool, timer_duration: Optional[int] = None) -> tuple[int, list]:
        """Create state command for EWneo switch according to EWB_CHANGE_STATE specification."""
        if timer_duration is not None:
            # Mode 1: Timer with specific duration
            if timer_duration <= 0:
                _LOGGER.warning("Invalid timer duration %d, using simple on/off", timer_duration)
                return self._create_switch_state_command(turn_on, None)
                
            # Find best exponent and mantissa combination
            exponent = 0
            while (timer_duration >> exponent) > 255 and exponent < 15:
                exponent += 1
                
            mantissa = min(255, timer_duration >> exponent)
            if mantissa == 0:
                mantissa = 1  # Avoid undefined behavior
                
            # Create state word (big-endian)
            state_word = (exponent << 28) | (mantissa << 20) | 0  # No warning for now
            
            # Convert to 4 bytes in big-endian order
            state_bytes = [
                (state_word >> 24) & 0xFF,
                (state_word >> 16) & 0xFF, 
                (state_word >> 8) & 0xFF,
                state_word & 0xFF
            ]
            
            _LOGGER.debug("EWneo switch %s: Timer command - duration: %ds (exp: %d, mantissa: %d)", 
                         self._serial_number[-6:], mantissa * (2 ** exponent), exponent, mantissa)
            
            return (1, state_bytes)
        else:
            # Mode 0: Simple on/off
            reason_code = 2 if turn_on else 1  # 2=on, 1=off
                
            # Create state word (big-endian)
            state_word = (reason_code << 24)
            
            # Convert to 4 bytes in big-endian order
            state_bytes = [
                (state_word >> 24) & 0xFF,
                (state_word >> 16) & 0xFF,
                (state_word >> 8) & 0xFF, 
                state_word & 0xFF
            ]
            
            _LOGGER.debug("EWneo switch %s: Simple command - %s (reason: %d)", 
                         self._serial_number[-6:], "ON" if turn_on else "OFF", reason_code)
            
            return (0, state_bytes)
    
    async def _send_ewb_change_state(self, turn_on: bool, timer_duration: Optional[int] = None) -> bool:
        """Send EWB_CHANGE_STATE command to EWneo device."""
        if not self._gateway_serial:
            _LOGGER.error("EWneo switch %s: No gateway serial available for EWB command", self._serial_number[-6:])
            return False
        
        try:
            # Create state command
            mode, state_bytes = self._create_switch_state_command(turn_on, timer_duration)
            
            _LOGGER.info("🔄 Sending EWB_CHANGE_STATE to EWneo switch %s: %s%s", 
                        self._serial_number[-6:], "ON" if turn_on else "OFF",
                        f" (timer: {timer_duration}s)" if timer_duration else "")
            
            # Send command via coordinator's transceiver
            result = await self.coordinator.transceiver.rx11_ewb_change_state(
                self._gateway_serial, self._serial_number, mode, state_bytes
            )
            
            if result:
                recent_mode, recent_state_bytes = result
                _LOGGER.debug("EWneo switch %s: Change state successful, parsing response...", 
                             self._serial_number[-6:])
                
                # Parse the response to update local state
                parsed_state = self.coordinator._parse_ewneo_state(
                    self._device_type_code, recent_state_bytes, "ewneo_switch"
                )
                
                if parsed_state and parsed_state.get("type") == "switch":
                    self._is_on = parsed_state.get("on", turn_on)
                    _LOGGER.debug("EWneo switch %s: State updated to %s from response", 
                                 self._serial_number[-6:], "ON" if self._is_on else "OFF")
                else:
                    # Fallback to assume command worked
                    self._is_on = turn_on
                    
                # Update Home Assistant state
                self.async_write_ha_state()
                return True
            else:
                _LOGGER.error("EWneo switch %s: Failed to change state", self._serial_number[-6:])
                return False
                
        except Exception as e:
            _LOGGER.error("Failed to set EWneo switch %s state: %s", self._serial_number[-6:], e)
            return False
    
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the EWneo switch on."""
        # Check for timer duration in kwargs
        timer_duration = kwargs.get("timer_duration")
        await self._send_ewb_change_state(True, timer_duration)
    
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the EWneo switch off."""
        await self._send_ewb_change_state(False)
    
    def process_state_update(self, telegram_data: Dict[str, Any]) -> None:
        """Process incoming state updates from EWB telegrams."""
        try:
            parsed_state = self.coordinator._parse_ewneo_state(
                self._device_type_code, 
                telegram_data.get("state_bytes", []), 
                "ewneo_switch"
            )
            
            if parsed_state and parsed_state.get("type") == "switch":
                # Use thread-safe add_job for state update
                self.hass.add_job(self._async_update_state_from_parsed, parsed_state)
                    
        except Exception as e:
            _LOGGER.error("Error processing EWneo switch %s state update: %s", 
                         self._serial_number[-6:], e)


class EldatConfiguredSwitch(EldatEntity, SwitchEntity):
    """Switch entity created from an entity specification saved in device store."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        super().__init__(coordinator, serial_number, device_info)
        self._entity_spec = entity_spec
        self._channel = int(entity_spec.get("channel", 0))
        # STATELESS: Kein Zustandstracking - Switch ist vollständig zustandslos
        self._available = True

        # Get operating mode and button configuration from entity spec
        self._operating_mode = entity_spec.get("operating_mode", 1)
        self._button_config = entity_spec.get("button_config", {})
        self._receiver_kind = entity_spec.get("receiver_kind", "switch")

        # Initialize persistent state for heating/cooling receivers
        device_type = device_info.get("type", "unknown")
        self._is_heating_cooling = (device_type == "ew_receiver" and 
                                  self._receiver_kind in ["heating", "cooling", "heating_cooling"])
        
        # 4-hour repetition support for heating/cooling devices
        self._supports_4h_repetition = self._is_heating_cooling
        
        if self._is_heating_cooling:
            # Persistent state for heating/cooling
            self._is_on = False
            self._last_command_time = None
            self._last_command_code = None  # 'A' for on, 'B' for off
            self._repeat_timer = None
            self._repeat_interval = 4 * 60 * 60  # 4 hours in seconds
            
            # Try to restore state from coordinator during initialization
            stored_state = self.coordinator.get_device_state(serial_number)
            if stored_state:
                self._is_on = stored_state.get("is_on", False)
                last_command_time_str = stored_state.get("last_command_time")
                if last_command_time_str:
                    try:
                        if isinstance(last_command_time_str, str):
                            self._last_command_time = datetime.fromisoformat(last_command_time_str.replace("Z", "+00:00"))
                        else:
                            self._last_command_time = last_command_time_str
                    except (ValueError, AttributeError):
                        self._last_command_time = None
                        _LOGGER.warning("Invalid last_command_time format during init for %s", serial_number[-6:])
                        
                self._last_command_code = stored_state.get("last_command_code")
                _LOGGER.info("🔄 Restored heating/cooling state during init: %s (Code: %s, Available: %s)", 
                            "ON" if self._is_on else "OFF", self._last_command_code, "True")
            else:
                # New device - set default OFF state 
                self._is_on = False
                self._last_command_code = None
                _LOGGER.info("🆕 New heating/cooling receiver - default state: OFF, will initialize on first add")
        else:
            # Stateless for other receiver types
            self._is_on = None

        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_configured_switch_{self._channel}_{int(time.time())}")
        
        # For heating/cooling switches, ensure they are always enabled by default
        if self._is_heating_cooling:
            self._attr_entity_registry_enabled_default = True
            # Also ensure the entity is marked as available during initialization
            self._available = True
            _LOGGER.info("🌡️ Heating/cooling switch initialized: %s (enabled by default)", 
                       entity_spec.get("name", "Unknown"))
        
        # Get device-specific configuration
        device_type = device_info.get("type", "unknown")
        device_config = get_entity_config_for_device(
            device_type=device_type,
            receiver_kind=self._receiver_kind,
            operating_mode=self._operating_mode,
            entity_type="switch"
        )
        
        # Set name with action hints
        base_name = device_info.get('name', serial_number)
        self._attr_name = create_entity_name_with_action_hint(
            base_name=base_name,
            device_type=device_type,
            receiver_kind=self._receiver_kind,
            operating_mode=self._operating_mode,
            entity_type="switch"
        )
        
        # Set device-specific icon
        self._attr_icon = entity_spec.get("icon") or device_config.get("icon", "mdi:toggle-switch-variant")

    @property
    def is_on(self) -> bool | None:
        """Return switch state."""
        if self._is_heating_cooling:
            # Return actual state for heating/cooling receivers
            return self._is_on
        else:
            # STATELESS: Return None for other types to allow repeated actions
            return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Heating/cooling switches are always available when coordinator is working
        if self._is_heating_cooling:
            return self.coordinator.last_update_success
        return self._available and self.coordinator.last_update_success
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes with device-specific action descriptions."""
        base_attrs = super().extra_state_attributes
        
        device_type = self._device_info.get("type", "unknown")
        device_attrs = get_extra_state_attributes_for_device(
            device_type=device_type,
            receiver_kind=self._receiver_kind,
            operating_mode=self._operating_mode,
            entity_type="switch"
        )
        
        # Merge with base attributes
        base_attrs.update(device_attrs)
        return base_attrs

    async def async_added_to_hass(self) -> None:
        """Called when entity is added to Home Assistant."""
        await super().async_added_to_hass()
        
        if self._is_heating_cooling:
            _LOGGER.info("🔍 Heating/cooling switch added to hass: %s", self._attr_name)
            
            # Force entity to be available immediately
            self._available = True
            
            # Try to restore state from coordinator persistent storage
            restored_state = await self._restore_persistent_state()
            
            # Update Home Assistant state immediately to show as available
            self.async_write_ha_state()
            
            # Send initial state or restored state
            await self._send_initial_state()
            
            # Start timer if we have a persistent state and device was used before
            if restored_state and self._supports_4h_repetition and self._last_command_time:
                # Calculate time since last command
                now = datetime.now()
                time_since_last = now - self._last_command_time
                
                # If it's been more than 4 hours, send command immediately and start timer
                if time_since_last.total_seconds() > 14400:  # 4 hours
                    _LOGGER.info("🕐 More than 4h since last command, sending repeat command")
                    await self._send_repeat_command()
                    self._last_command_time = now
                
                # Start the timer for future repeats
                self._schedule_repeat_timer()
                _LOGGER.debug("🔄 Started 4h repeat timer for heating/cooling switch %s (last command: %s)", 
                             self._serial_number[-8:], self._last_command_time.isoformat())
            else:
                _LOGGER.debug("🔄 No timer started - device not used before or no 4h support")
                
            _LOGGER.info("✅ Heating/cooling switch fully initialized: %s (State: %s)", 
                       self._attr_name, "ON" if self._is_on else "OFF")

    async def _send_repeat_command(self):
        """Send a repeat command for heating/cooling devices."""
        if not self._is_heating_cooling or not self._last_command_code:
            return
            
        try:
            success = await self.coordinator.send_command_to_device(
                self._serial_number, self._last_command_code
            )
            if success:
                _LOGGER.info("🔄 Sent repeat command %s to %s", 
                           self._last_command_code, self._serial_number[-8:])
            else:
                _LOGGER.warning("⚠️ Failed to send repeat command %s to %s", 
                              self._last_command_code, self._serial_number[-8:])
        except Exception as e:
            _LOGGER.error("❌ Error sending repeat command: %s", e)

    async def _restore_persistent_state(self):
        """Restore persistent state for heating/cooling switches."""
        if not self._is_heating_cooling:
            return None
            
        try:
            stored_state = self.coordinator.get_device_state(self._serial_number)
            if stored_state:
                # Restore from coordinator storage
                self._is_on = stored_state.get("is_on", False)
                last_command_time_str = stored_state.get("last_command_time")
                if last_command_time_str:
                    try:
                        # Handle both ISO format and datetime objects
                        if isinstance(last_command_time_str, str):
                            self._last_command_time = datetime.fromisoformat(last_command_time_str.replace("Z", "+00:00"))
                        else:
                            self._last_command_time = last_command_time_str
                    except (ValueError, AttributeError):
                        self._last_command_time = None
                        _LOGGER.warning("⚠️ Invalid last_command_time format for %s, resetting", self._serial_number[-6:])
                        
                self._last_command_code = stored_state.get("last_command_code")
                
                # Entity should be available after successful state restoration
                self._available = True
                _LOGGER.info("🔄 Restored heating/cooling state from storage: %s (Code: %s, Time: %s, Available: True)", 
                           "ON" if self._is_on else "OFF", 
                           self._last_command_code or "None",
                           self._last_command_time.isoformat() if self._last_command_time else "None")
                return stored_state
            else:
                # No stored state - initialize as OFF but available
                self._is_on = False
                self._last_command_code = None
                self._last_command_time = None
                self._available = True
                _LOGGER.info("🆕 No stored state found for %s, initializing as OFF but available", self._serial_number[-6:])
                return None
                
        except Exception as e:
            _LOGGER.error("Error restoring persistent state for %s: %s", self._serial_number[-6:], e)
            # Safe fallback
            self._is_on = False
            self._last_command_code = None
            self._last_command_time = None
            return None

    async def async_update(self) -> None:
        """Update the entity."""
        # For heating/cooling switches, ensure they stay enabled
        if self._is_heating_cooling:
            self._available = True

    async def _send_default_off_state(self):
        """Send default OFF state for new heating/cooling devices."""
        if not self._is_heating_cooling:
            return
            
        try:
            # Send OFF command based on operating mode
            if self._operating_mode == 1:
                # Toggle mode - if no previous state, assume we want OFF
                button = self._button_config.get("toggle", 0)
            elif self._operating_mode == 2:
                # Separate buttons - use OFF button
                button = self._button_config.get("off", 1)
            else:
                # Fallback
                button = 1
                
            if button is not None:
                command = bytes([button])
                success = await self.coordinator.send_command(
                    self._serial_number, 
                    command,
                    action="initial_off"
                )
                if success:
                    # Update state to OFF
                    self._update_persistent_state(False, "B")
                    _LOGGER.info("📴 Default OFF state sent and saved for %s", self._serial_number[-6:])
                else:
                    _LOGGER.warning("⚠️ Failed to send default OFF state for %s", self._serial_number[-6:])
                    
        except Exception as e:
            _LOGGER.error("Error sending default OFF state for %s: %s", self._serial_number[-6:], e)

    async def _send_initial_state(self):
        """Send initial or restored state command."""
        if not self._is_heating_cooling:
            return
            
        # Only send initial state if we have a previous command to restore
        if not self._last_command_code or not self._last_command_time:
            _LOGGER.debug("📝 No previous state to restore for %s, skipping initial state send", self._serial_number[-6:])
            return
            
        try:
            # Send the last known command to restore state
            if self._last_command_code == "A":
                # Restore ON state
                if self._operating_mode == 1:
                    button = self._button_config.get("toggle", 0)
                elif self._operating_mode == 2:
                    button = self._button_config.get("on", 0)
                else:
                    button = 0
            else:  # Code B or unknown
                # Restore OFF state
                if self._operating_mode == 1:
                    button = self._button_config.get("toggle", 0)
                elif self._operating_mode == 2:
                    button = self._button_config.get("off", 1)
                else:
                    button = 1
                    
            if button is not None:
                command = bytes([button])
                success = await self.coordinator.send_command(
                    self._serial_number, 
                    command,
                    action="restore_state"
                )
                if success:
                    _LOGGER.info("🔄 Restored state sent for %s: %s (Code: %s)", 
                               self._serial_number[-6:], 
                               "ON" if self._is_on else "OFF", 
                               self._last_command_code)
                else:
                    _LOGGER.warning("⚠️ Failed to restore state for %s", self._serial_number[-6:])
                    
        except Exception as e:
            _LOGGER.error("Error sending initial state for %s: %s", self._serial_number[-6:], e)

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        await super().async_will_remove_from_hass()
        
        if self._is_heating_cooling:
            _LOGGER.info("🔍 Heating/cooling switch being removed: %s", self._attr_name)
            # Cancel repeat timer
            self._cancel_repeat_timer()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on based on operating mode."""
        try:
            button = None
            
            if self._operating_mode == 1:
                # Mode 1: Toggle with button A
                button = self._button_config.get("toggle", 0)
            elif self._operating_mode == 2:
                # Mode 2: Separate On/Off buttons - use On button
                button = self._button_config.get("on", 0)
            
            if button is not None:
                command = bytes([button])
                success = await self.coordinator.send_command(
                    self._serial_number, 
                    command,
                    action="single"
                )
                if success:
                    if self._is_heating_cooling:
                        # Update persistent state for heating/cooling
                        self._update_persistent_state(True, "A")
                    else:
                        # Stateless update for other types
                        self.async_write_ha_state()
                    
        except Exception:
            _LOGGER.exception("Error turning on configured switch %s", self._attr_unique_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off based on operating mode."""
        try:
            button = None
            
            if self._operating_mode == 1:
                # Mode 1: Toggle with button A (same as turn_on)
                button = self._button_config.get("toggle", 0)
            elif self._operating_mode == 2:
                # Mode 2: Separate On/Off buttons - use Off button
                button = self._button_config.get("off", 1)
            
            if button is not None:
                command = bytes([button])
                success = await self.coordinator.send_command(
                    self._serial_number, 
                    command,
                    action="single"
                )
                if success:
                    if self._is_heating_cooling:
                        # Update persistent state for heating/cooling
                        self._update_persistent_state(False, "B")
                    else:
                        # Stateless update for other types
                        self.async_write_ha_state()
        except Exception:
            _LOGGER.exception("Error turning off configured switch %s", self._attr_unique_id)

                    
    async def _send_initial_state(self):
        """Send initial or restored state command."""
        if self._is_heating_cooling:
            device_state = self.coordinator.get_device_state(self._serial_number)
            
            if device_state and device_state.get("is_on") is not None:
                # Restored state - send the last command
                command_code = device_state.get("last_command_code")
                if command_code:
                    formatted_command = command_code.replace(" ", "")  # Convert to hex string
                    await self.coordinator.send_command(
                        self._serial_number,
                        formatted_command
                    )
                    _LOGGER.info("🔄 Restored state sent for %s: %s", self._attr_name, command_code)
            else:
                # Default OFF state for new devices
                button = self._button_config.get("off", 1) if self._operating_mode == 2 else self._button_config.get("toggle", 0)
                if button is not None:
                    command = bytes([button])
                    await self.coordinator.send_command(self._serial_number, command, action="single")
                    _LOGGER.info("📴 Default OFF state sent for %s", self._attr_name)



    def _update_persistent_state(self, is_on: bool, command_code: str) -> None:
        """Update persistent state and schedule repeat timer for heating/cooling receivers."""
        if not self._is_heating_cooling:
            return
            
        old_state = self._is_on
        self._is_on = is_on
        self._last_command_time = datetime.now()
        self._last_command_code = command_code
        
        # Save state to coordinator with enhanced error handling
        try:
            state_data = {
                "is_on": self._is_on,
                "last_command_time": self._last_command_time.isoformat(),
                "last_command_code": self._last_command_code,
                "device_type": "heating_cooling",
                "operating_mode": self._operating_mode,
                "updated_at": datetime.now().isoformat()
            }
            self.coordinator.set_device_state(self._serial_number, state_data)
            _LOGGER.debug("💾 State saved to coordinator for %s: %s", self._serial_number[-6:], state_data)
        except Exception as e:
            _LOGGER.error("Failed to save state to coordinator for %s: %s", self._serial_number[-6:], e)
        
        # Update Home Assistant state
        self.async_write_ha_state()
        
        # Schedule repeat timer
        self._schedule_repeat_timer()
        
        _LOGGER.info("🔄 EW-Receiver heating/cooling %s state updated: %s → %s (Code: %s)", 
                    self._serial_number[-6:], 
                    "ON" if old_state else "OFF",
                    "ON" if self._is_on else "OFF",
                    command_code)

    def _schedule_repeat_timer(self) -> None:
        """Schedule timer to repeat last command after 4 hours."""
        if not self._is_heating_cooling:
            return
            
        # Cancel existing timer
        self._cancel_repeat_timer()
        
        if self._last_command_code and self.hass:
            async def _repeat_last_command():
                """Repeat the last command."""
                try:
                    await asyncio.sleep(self._repeat_interval)
                    
                    if self._last_command_code:
                        _LOGGER.info("🔁 Repeating last command for EW-Receiver heating/cooling %s: Code %s (%s)", 
                                   self._serial_number[-6:], self._last_command_code,
                                   "ON" if self._last_command_code == "A" else "OFF")
                        
                        # Send the same command again
                        if self._last_command_code == "A":
                            # Repeat "on" command based on operating mode
                            if self._operating_mode == 1:
                                button = self._button_config.get("toggle", 0)
                            elif self._operating_mode == 2:
                                button = self._button_config.get("on", 0)
                            else:
                                button = 0
                        else:  # Code B
                            # Repeat "off" command based on operating mode  
                            if self._operating_mode == 1:
                                button = self._button_config.get("toggle", 0)
                            elif self._operating_mode == 2:
                                button = self._button_config.get("off", 1)
                            else:
                                button = 1
                        
                        command = bytes([button])
                        success = await self.coordinator.send_command(
                            self._serial_number, 
                            command,
                            action="repeat"
                        )
                        
                        if success:
                            _LOGGER.info("✅ Successfully repeated command for EW-Receiver heating/cooling %s", 
                                       self._serial_number[-6:])
                            # Schedule next repeat
                            self._schedule_repeat_timer()
                        else:
                            _LOGGER.warning("❌ Failed to repeat command for EW-Receiver heating/cooling %s", 
                                          self._serial_number[-6:])
                            
                except Exception as e:
                    _LOGGER.error("Error repeating command for EW-Receiver heating/cooling %s: %s", 
                                self._serial_number[-6:], e)
            
            # Create and store the timer task
            self._repeat_timer = self.hass.async_create_task(_repeat_last_command())
            _LOGGER.debug("⏰ Scheduled repeat timer for EW-Receiver heating/cooling %s (4 hours)", 
                         self._serial_number[-6:])

    def _cancel_repeat_timer(self) -> None:
        """Cancel the repeat timer."""
        if self._repeat_timer and not self._repeat_timer.done():
            self._repeat_timer.cancel()
            _LOGGER.debug("⏰ Cancelled repeat timer for EW-Receiver heating/cooling %s", self._serial_number[-6:])
        self._repeat_timer = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes with device-specific action descriptions."""
        base_attrs = super().extra_state_attributes
        
        # Add heating/cooling specific attributes
        if self._is_heating_cooling:
            heating_cooling_attrs = {
                "last_command_code": self._last_command_code,
                "repeat_interval_hours": self._repeat_interval / 3600,
                "operating_mode": self._operating_mode,
                "receiver_kind": self._receiver_kind,
            }
            
            if self._last_command_time:
                heating_cooling_attrs["last_telegram_sent"] = self._last_command_time.isoformat()
                
                # Calculate time until next repeat
                if self._last_command_code:
                    next_repeat_time = self._last_command_time + timedelta(seconds=self._repeat_interval)
                    time_until_repeat = (next_repeat_time - datetime.now()).total_seconds()
                    if time_until_repeat > 0:
                        heating_cooling_attrs["next_repeat_in_seconds"] = round(time_until_repeat)
                        heating_cooling_attrs["next_repeat_time"] = next_repeat_time.isoformat()
            
            base_attrs.update(heating_cooling_attrs)
        
        device_type = self._device_info.get("type", "unknown")
        device_attrs = get_extra_state_attributes_for_device(
            device_type=device_type,
            receiver_kind=self._receiver_kind,
            operating_mode=self._operating_mode,
            entity_type="switch"
        )
        
        # Merge with base attributes
        base_attrs.update(device_attrs)
        return base_attrs


class EldatSwitch(EldatEntity, SwitchEntity):
    """Simple switch entity for ELDAT devices."""

    def __init__(self, coordinator: EldatCoordinator, serial_number: str, device_info: Dict[str, Any], channel: int = 0) -> None:
        super().__init__(coordinator, serial_number, device_info)
        self._channel = int(channel)
        
        # Initialize persistent state for heating/cooling receivers
        device_type = device_info.get("type", "unknown")
        receiver_kind = device_info.get("receiver_kind", "switch")
        self._is_heating_cooling = (device_type == "ew_receiver" and 
                                  receiver_kind in ["heating", "cooling", "heating_cooling"])
        
        if self._is_heating_cooling:
            # Persistent state for heating/cooling
            self._is_on = False
            self._last_command_time = None
            self._last_command_code = None  # 'A' for on, 'B' for off
            self._repeat_timer = None
            self._repeat_interval = 4 * 60 * 60  # 4 hours in seconds
            
            # Try to restore state from coordinator
            stored_state = self.coordinator.get_device_state(serial_number)
            if stored_state:
                self._is_on = stored_state.get("is_on", False)
                # Handle both string and datetime objects for last_command_time
                last_cmd_time = stored_state.get("last_command_time")
                if isinstance(last_cmd_time, str):
                    try:
                        self._last_command_time = datetime.fromisoformat(last_cmd_time)
                    except ValueError:
                        self._last_command_time = None
                else:
                    self._last_command_time = last_cmd_time
                self._last_command_code = stored_state.get("last_command_code")
                _LOGGER.info("🔄 Restored heating/cooling state: %s (Code: %s)", 
                            "ON" if self._is_on else "OFF", self._last_command_code)
            else:
                # Default OFF state for new heating/cooling devices
                self._is_on = False
                self._last_command_time = None
                self._last_command_code = "B"  # B for OFF
                _LOGGER.info("📴 New heating/cooling device created with default OFF state")
            # Stateless for other receiver types
            self._is_on = None
            
        # STATELESS: Kein Zustandstracking - Switch ist vollständig zustandslos (except heating/cooling)
        self._available = True

        if channel > 0:
            self._attr_unique_id = f"{serial_number}_switch_{channel}"
            self._attr_name = f"{device_info.get('name', serial_number)} Channel {channel + 1}"
        else:
            self._attr_unique_id = f"{serial_number}_switch"
            self._attr_name = f"{device_info.get('name', serial_number)} Switch"

        # Get device-specific icon
        device_type = device_info.get("type", "unknown")
        device_config = get_entity_config_for_device(
            device_type=device_type,
            entity_type="switch"
        )
        self._attr_icon = device_config.get("icon", "mdi:toggle-switch-variant")

    @property
    def is_on(self) -> bool | None:
        """Return switch state."""
        if self._is_heating_cooling:
            # Return actual state for heating/cooling receivers  
            return self._is_on
        else:
            # STATELESS: Return None for other types to allow repeated actions
            return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._available and self.coordinator.last_update_success

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            command = bytes([0x01, self._channel, 0xFF])
            success = await self.coordinator.send_command(self._serial_number, command)
            if success:
                if self._is_heating_cooling:
                    # Update persistent state for heating/cooling
                    self._update_persistent_state(True, "A")
                else:
                    # Stateless update for other types
                    self.async_write_ha_state()
        except Exception:
            _LOGGER.exception("Error turning on switch %s channel %d", self._serial_number, self._channel)

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            command = bytes([0x01, self._channel, 0x00])
            success = await self.coordinator.send_command(self._serial_number, command)
            if success:
                if self._is_heating_cooling:
                    # Update persistent state for heating/cooling
                    self._update_persistent_state(False, "B")
                else:
                    # Stateless update for other types
                    self.async_write_ha_state()
        except Exception:
            _LOGGER.exception("Error turning off switch %s channel %d", self._serial_number, self._channel)

    def _update_persistent_state(self, is_on: bool, command_code: str) -> None:
        """Update persistent state and schedule repeat timer for heating/cooling receivers."""
        if not self._is_heating_cooling:
            return
            
        old_state = self._is_on
        self._is_on = is_on
        self._last_command_time = datetime.now()
        self._last_command_code = command_code
        
        # Save state to coordinator
        self.coordinator.set_device_state(self._serial_number, {
            "is_on": self._is_on,
            "last_command_time": self._last_command_time.isoformat(),
            "last_command_code": self._last_command_code
        })
        
        # Update Home Assistant state
        self.async_write_ha_state()
        
        # Schedule repeat timer
        self._schedule_repeat_timer()
        
        _LOGGER.info("🔄 EW-Receiver heating/cooling %s state updated: %s → %s (Code: %s)", 
                    self._serial_number[-6:], 
                    "ON" if old_state else "OFF",
                    "ON" if self._is_on else "OFF",
                    command_code)

    def _schedule_repeat_timer(self) -> None:
        """Schedule timer to repeat last command after 4 hours."""
        if not self._is_heating_cooling:
            return
            
        # Cancel existing timer
        self._cancel_repeat_timer()
        
        if self._last_command_code and self.hass:
            async def _repeat_last_command():
                """Repeat the last command."""
                try:
                    await asyncio.sleep(self._repeat_interval)
                    
                    if self._last_command_code:
                        _LOGGER.info("🔁 Repeating last command for EW-Receiver heating/cooling %s: Code %s (%s)", 
                                   self._serial_number[-6:], self._last_command_code,
                                   "ON" if self._last_command_code == "A" else "OFF")
                        
                        # Send the same command again
                        command = bytes([0x01, self._channel, 0xFF if self._last_command_code == "A" else 0x00])
                        success = await self.coordinator.send_command(
                            self._serial_number, 
                            command,
                            action="repeat"
                        )
                        
                        if success:
                            _LOGGER.info("✅ Successfully repeated command for EW-Receiver heating/cooling %s", 
                                       self._serial_number[-6:])
                            # Schedule next repeat
                            self._schedule_repeat_timer()
                        else:
                            _LOGGER.warning("❌ Failed to repeat command for EW-Receiver heating/cooling %s", 
                                          self._serial_number[-6:])
                            
                except Exception as e:
                    _LOGGER.error("Error repeating command for EW-Receiver heating/cooling %s: %s", 
                                self._serial_number[-6:], e)
            
            # Create and store the timer task
            self._repeat_timer = self.hass.async_create_task(_repeat_last_command())
            _LOGGER.debug("⏰ Scheduled repeat timer for EW-Receiver heating/cooling %s (4 hours)", 
                         self._serial_number[-6:])

    def _cancel_repeat_timer(self) -> None:
        """Cancel the repeat timer."""
        if self._repeat_timer and not self._repeat_timer.done():
            self._repeat_timer.cancel()
            _LOGGER.debug("⏰ Cancelled repeat timer for EW-Receiver heating/cooling %s", self._serial_number[-6:])
        self._repeat_timer = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {}
        
        # Add heating/cooling specific attributes
        if self._is_heating_cooling:
            attrs.update({
                "last_command_code": self._last_command_code,
                "repeat_interval_hours": self._repeat_interval / 3600,
                "channel": self._channel,
            })
            
            if self._last_command_time:
                attrs["last_telegram_sent"] = self._last_command_time.isoformat()
                
                # Calculate time until next repeat
                if self._last_command_code:
                    next_repeat_time = self._last_command_time + timedelta(seconds=self._repeat_interval)
                    time_until_repeat = (next_repeat_time - datetime.now()).total_seconds()
                    if time_until_repeat > 0:
                        attrs["next_repeat_in_seconds"] = round(time_until_repeat)
                        attrs["next_repeat_time"] = next_repeat_time.isoformat()
        
        return attrs

    async def async_added_to_hass(self) -> None:
        """Called when entity is added to Home Assistant."""
        await super().async_added_to_hass()
        
        if self._is_heating_cooling:
            _LOGGER.info("🔍 Heating/cooling switch added to hass: %s", self._attr_name)
            
            # Send initial state or restored state
            await self._send_initial_state()
            
            # Schedule repeat timer if we have a last command
            if self._last_command_time and self._last_command_code:
                self._schedule_repeat_timer()

    async def _send_initial_state(self):
        """Send initial or restored state command."""
        if self._is_heating_cooling:
            device_state = self.coordinator.get_device_state(self._serial_number)
            
            if device_state and device_state.get("is_on") is not None:
                # Restored state - send the last command
                command_code = device_state.get("last_command_code")
                if command_code:
                    # For EldatSwitch, use bytes format
                    command = bytes([0x01 if command_code == "A" else 0x00, self._channel, 0xFF])
                    await self.coordinator.send_command(self._serial_number, command)
                    _LOGGER.info("🔄 Restored state sent for %s: %s", self._attr_name, command_code)
            else:
                # Default OFF state for new devices
                command = bytes([0x00, self._channel, 0xFF])
                await self.coordinator.send_command(self._serial_number, command)
                _LOGGER.info("📴 Default OFF state sent for %s", self._attr_name)

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        await super().async_will_remove_from_hass()
        
        if self._is_heating_cooling:
            _LOGGER.info("🔍 Heating/cooling switch being removed: %s", self._attr_name)
            # Cancel repeat timer
            self._cancel_repeat_timer()


class EldatDimmerSwitch(EldatSwitch):
    """Dimmer switch entity for devices that support brightness."""

    def __init__(self, coordinator: EldatCoordinator, serial_number: str, device_info: Dict[str, Any], channel: int = 0) -> None:
        super().__init__(coordinator, serial_number, device_info, channel)
        self._brightness = 255
        self._attr_icon = "mdi:brightness-6"

    @property
    def brightness(self) -> int:
        return self._brightness

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = int(kwargs.get("brightness", 255))
        try:
            command = bytes([0x02, self._channel, brightness])
            success = await self.coordinator.send_command(self._serial_number, command)
            if success:
                # STATELESS: Kein Zustandstracking - erlaubt wiederholte Dimmer-Aktionen
                self._brightness = brightness
                self.async_write_ha_state()
        except Exception:
            _LOGGER.exception("Error turning on dimmer %s channel %d", self._serial_number, self._channel)