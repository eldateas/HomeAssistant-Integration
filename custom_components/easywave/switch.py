"""Switch entities for ELDAT integration."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, EVENT_DEVICE_ADDED, EVENT_FORCE_CREATE
from .coordinator import EldatCoordinator
from .entity import EldatEntity
from .device_icons import get_entity_config_for_device

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ELDAT switch entities."""
    coordinator: EldatCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    switches: List[SwitchEntity] = []
    
    # Track which devices have had entities created to prevent duplicates
    created_device_serials = set()

    # Restore switches from saved devices (both legacy and registered)
    for serial_number, device_info in coordinator.get_all_devices().items():
        device_entities = device_info.get("entities", [])
        
        _LOGGER.info("🔍 Switch setup: checking device %s (type=%s, receiver_kind=%s, %d entities)", 
                    serial_number, device_info.get("type"), 
                    device_info.get("receiver_kind"), len(device_entities))
        
        # Determine if this is an EWneo device
        is_neo_device = device_info.get("neo_device", False) or device_info.get("type", "").startswith("ewneo_")
        
        # Create switches from entity specs
        for entity_spec in device_entities:
            if entity_spec.get("type") == "switch":
                _LOGGER.info("✅ Creating switch from entity_spec for %s (neo_device: %s)", serial_number, is_neo_device)
                # Use the correct switch class based on device type
                if is_neo_device:
                    switches.append(EldatEWneoSwitch(coordinator, serial_number, device_info, entity_spec))
                else:
                    switches.append(EldatEWReceiverSwitch(coordinator, serial_number, device_info, entity_spec))
                created_device_serials.add(serial_number)
        
        # Skip further processing if we created entities from specs
        if serial_number in created_device_serials:
            continue
        
        # Legacy fallback: check for heating_cooling devices
        receiver_kind = device_info.get("receiver_kind")
        if receiver_kind == "heating_cooling":
            # Force creation of heating_cooling switches if not already created
            from .entity_specs import create_entity_specs_for_device
            entity_specs = create_entity_specs_for_device(serial_number, device_info)
            switch_entities = entity_specs.get("switch", [])
            
            for entity_spec in switch_entities:
                switches.append(EldatEWReceiverSwitch(coordinator, serial_number, device_info, entity_spec))
            
            created_device_serials.add(serial_number)
            _LOGGER.info("🌡️ Created %d heating/cooling switch entities for device %s", 
                       len(switch_entities), serial_number[-8:])
            continue
        
        # Skip devices that already have configured entities of any type (non-heating_cooling and non-EWneo)
        is_neo_device = device_info.get("neo_device", False) or device_info.get("device_type_code") in [0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B]
        has_configured_entities = any(entity.get("type") in ["button", "light", "cover", "switch"] for entity in device_entities)
        
        # Create EWneo switches during setup - they need to be created here for startup
        if is_neo_device:
            device_switches = _create_switches_for_device(coordinator, serial_number, device_info)
            switches.extend(device_switches)
            created_device_serials.add(serial_number)
            if device_switches:
                _LOGGER.info("🔧 Created %d EWneo switch entities for device %s during setup", len(device_switches), serial_number[-8:])
            continue
        
        # For non-EWneo devices without configured entities, use legacy creation
        if not has_configured_entities:
            device_switches = _create_switches_for_device(coordinator, serial_number, device_info)
            switches.extend(device_switches)
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
            entities = event.data.get("entities", [])
            platforms = event.data.get("platforms", set())

            if not serial_number or not device_info:
                _LOGGER.debug("Device added event missing data, skipping")
                return
            
            # Skip if this device already had entities created during setup
            if serial_number in created_device_serials:
                _LOGGER.debug("Skipping switch creation for %s - already created during setup", serial_number[-8:])
                return

            # Check if we have switch entities in the entities list
            switch_entities = [e for e in entities if e.get("type") == "switch"]
            
            if not switch_entities and "switch" not in platforms:
                _LOGGER.debug("No switch entities or platform for device %s, skipping", serial_number[-8:])
                return

            # Check if this is an EWneo device - skip here, will be handled by platform-specific handler
            device_type_code = device_info.get("device_type_code", 0)
            is_neo_device = device_type_code in [0x03, 0x06, 0x07] or device_info.get("neo_device", False)
            
            if is_neo_device:
                _LOGGER.debug("Skipping EWneo device %s in generic handler - will be handled by platform-specific handler", serial_number[-8:])
                return
            
            _LOGGER.info("🔧 Creating switch entities for device %s: neo=%s, entities=%d", 
                        serial_number[-8:], is_neo_device, len(switch_entities))

            new_switches = []
            if switch_entities:
                # Use entity specs from the event (non-EWneo devices only)
                for entity_spec in switch_entities:
                    new_switches.append(EldatEWReceiverSwitch(coordinator, serial_number, device_info, entity_spec))
            else:
                # Fallback to legacy creation
                new_switches = _create_switches_for_device(coordinator, serial_number, device_info)

            if new_switches:
                async_add_entities(new_switches)
                _LOGGER.info("✅ Added %d switch entities for device %s", len(new_switches), serial_number[-8:])

        except Exception:
            _LOGGER.exception("Error handling device added event for switches")

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
            
            # Skip if this device already had entities created during setup
            if serial_number in created_device_serials and not force_create:
                _LOGGER.debug("Skipping switch creation for %s - already created during setup", serial_number[-8:])
                return

            # Check if this is an EWneo device - if so, create EWneo switch entities
            device_type_code = device_info.get("device_type_code", 0)
            is_neo_device = device_type_code in [0x03, 0x06, 0x07] or device_info.get("neo_device", False)
            
            if is_neo_device:
                _LOGGER.info("🔧 Creating EWneo switch entities from event for device %s", serial_number[-8:])
                new_switches = []
                for entity_spec in entities:
                    if entity_spec.get("type") == "switch":
                        new_switches.append(EldatEWneoSwitch(coordinator, serial_number, device_info, entity_spec))
                
                if new_switches:
                    async_add_entities(new_switches)
                    created_device_serials.add(serial_number)
                    _LOGGER.info("✅ Created %d EWneo switch entities for device %s", len(new_switches), serial_number[-8:])
                return

            # Regular EW devices
            new_switches: List[SwitchEntity] = []
            for entity_spec in entities:
                if entity_spec.get("type") == "switch":
                    new_switches.append(
                        EldatEWReceiverSwitch(
                            coordinator=coordinator,
                            serial_number=serial_number,
                            device_info=device_info,
                            entity_spec=entity_spec,
                        )
                    )

            if new_switches:
                async_add_entities(new_switches)
                _LOGGER.info("Created %d switch entities for device %s (force: %s)", 
                           len(new_switches), serial_number, force_create)

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
                       serial_number, force_repair)

            # Create switch entities for this device using entity specs
            from .entity_specs import create_entity_specs_for_device
            entity_specs = create_entity_specs_for_device(serial_number, device_info)
            switch_entities = entity_specs.get("switch", [])
            
            new_switches = []
            for entity_spec in switch_entities:
                new_switches.append(
                    EldatEWReceiverSwitch(
                        coordinator=coordinator,
                        serial_number=serial_number,
                        device_info=device_info,
                        entity_spec=entity_spec,
                    )
                )
            
            if new_switches:
                async_add_entities(new_switches)
                _LOGGER.info("✅ Force-created %d switch entities for device %s", 
                           len(new_switches), serial_number)
            else:
                _LOGGER.warning("⚠️ No switch entity specs found for device %s", serial_number)

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
                _LOGGER.info("🌡️ Registered heating/cooling device detected: %s", serial_number)
                
                # Generate switch entities
                from .entity_specs import create_entity_specs_for_device
                entity_specs = create_entity_specs_for_device(serial_number, device_info)
                switch_entities = entity_specs.get("switch", [])
                
                new_switches = []
                for entity_spec in switch_entities:
                    new_switches.append(
                        EldatEWReceiverSwitch(
                            coordinator=coordinator,
                            serial_number=serial_number,
                            device_info=device_info,
                            entity_spec=entity_spec,
                        )
                    )
                
                if new_switches:
                    async_add_entities(new_switches)
                    _LOGGER.info("✅ Created %d heating/cooling switch entities for registered device %s", 
                               len(new_switches), serial_number)
        except Exception:
            _LOGGER.exception("Error handling registered device for switches")

    # Register event listeners (only once!)
    config_entry.async_on_unload(hass.bus.async_listen(EVENT_DEVICE_ADDED, _handle_device_added))
    config_entry.async_on_unload(hass.bus.async_listen(f"{EVENT_DEVICE_ADDED}_switch", _handle_switch_device_added))
    config_entry.async_on_unload(hass.bus.async_listen(EVENT_FORCE_CREATE, _handle_force_create))
    config_entry.async_on_unload(hass.bus.async_listen("eldat_device_registered", _handle_registered_device_added))


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
    if device_type in ["ew_sensor", "ewneo_sensor"]:
        _LOGGER.debug("Skipping switch creation for sensor device %s (type: %s)", serial_number[-8:], device_type)
        return switches
    
    # For EW transmitters, use entity_specs to determine what to create
    # Transmitters in "Dauer" (permanent) mode with single button mode get switch entities
    if device_type == "ew_transmitter":
        from .entity_specs import create_entity_specs_for_device
        entity_specs = create_entity_specs_for_device(serial_number, device_info)
        switch_specs = entity_specs.get("switch", [])
        
        if switch_specs:
            for spec in switch_specs:
                if spec.get("switch_type") == "transmitter_state":
                    switches.append(EldatTransmitterStateSwitch(coordinator, serial_number, device_info, spec))
                else:
                    switches.append(EldatTransmitterSwitch(coordinator, serial_number, device_info, spec))
            _LOGGER.info("✅ Created %d switch entities for transmitter %s (Dauer mode)", 
                        len(switches), serial_number[-8:])
        else:
            _LOGGER.debug("No switch entities for transmitter %s (not in Dauer mode)", serial_number[-8:])
        return switches
    
    # For EW-Receivers, only create switches if explicitly configured as switches
    if device_type == "ew_receiver":
        entity_specs = device_info.get("entities", [])
        
        # Check if there are any configured entities
        if entity_specs:
            # Only create switch entities that are explicitly configured
            for spec in entity_specs:
                if spec.get("type") == "switch":
                    switches.append(EldatEWReceiverSwitch(coordinator, serial_number, device_info, spec))
                    
            # For heating/cooling receivers, always create a switch even if not explicitly configured
            # but only if no switch entities were already created
            if not switches:
                receiver_kind = device_info.get("receiver_kind", "switch")
                if receiver_kind in ["heating", "cooling", "heating_cooling"]:
                    switches.append(EldatSwitch(coordinator, serial_number, device_info, 0))
                    _LOGGER.info("✅ Created default switch for configured EW-Receiver %s (kind: %s)", serial_number, receiver_kind)
            return switches
        else:
            # Legacy device without configured entities - create switch for switch type and heating/cooling receivers
            receiver_kind = device_info.get("receiver_kind", "switch")
            if receiver_kind in ["switch", "heating", "cooling", "heating_cooling"]:
                switches.append(EldatSwitch(coordinator, serial_number, device_info, 0))
                _LOGGER.info("✅ Created legacy switch for EW-Receiver %s (kind: %s)", serial_number, receiver_kind)
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
        # Initialize parent without automatic coordinator updates
        # We manage state updates manually from command responses
        super().__init__(coordinator, serial_number, device_info)
        
        # Disable automatic coordinator updates
        self._attr_should_poll = False
        
        self._entity_spec = entity_spec
        self._channel = int(entity_spec.get("channel", 0))  # 0-based channel index
        self._available = True
        self._initial_state_queried = False  # Track if initial state query completed
        
        # Command lock to prevent simultaneous commands to the same device
        self._command_lock = asyncio.Lock()
        
        # EWneo specific attributes - cached but can be refreshed from coordinator
        self._gateway_serial_cache = device_info.get("gateway_serial")
        self._device_type_code = device_info.get("device_type_code", 0)
        self._is_on = False
        
        # Reachability tracking
        self._reachable = True
        self._last_seen = None
        
        _LOGGER.info("🔧 Initializing EWneo switch: %s (gateway: %s, type_code: 0x%02X)", 
                    serial_number[-8:], self._gateway_serial_cache[-8:] if self._gateway_serial_cache else "None", self._device_type_code)
        
        # Initialize state from device's initial_state if available
        initial_state = device_info.get("initial_state", {})
        if initial_state.get("type") == "switch":
            self._is_on = initial_state.get("on", False)
            _LOGGER.info("🎯 EWneo switch %s: Loaded initial state: %s", serial_number[-8:], "ON" if self._is_on else "OFF")
        
        # Set up entity attributes
        # For multi-channel devices (dual/quad), use "Kanal X" as entity name
        # For single-channel devices, use None so only device name is shown
        if self._device_type_code in [0x06, 0x07]:  # Dual or Quad switch
            default_name = f"Kanal {self._channel + 1}"
        else:  # Single channel - no entity name, use device name only
            default_name = None
        self._attr_name = entity_spec.get("name", default_name)
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_ewneo_switch_{self._channel}")
        self._attr_device_class = SwitchDeviceClass.SWITCH
        
        _LOGGER.info("✅ EWneo switch entity initialized: %s (%s)", self._attr_name, self._attr_unique_id)
    
    @property
    def _gateway_serial(self) -> str | None:
        """Get gateway serial from coordinator (dynamically updated)."""
        # Try to get fresh data from coordinator
        if hasattr(self.coordinator, 'devices') and self._serial_number in self.coordinator.devices:
            device_data = self.coordinator.devices[self._serial_number]
            gateway_serial = device_data.get("gateway_serial")
            if gateway_serial:
                # Update cache
                self._gateway_serial_cache = gateway_serial
                return gateway_serial
        
        # Fallback to cached value
        return self._gateway_serial_cache
    
    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        return self._is_on
    
    @property
    def available(self) -> bool:
        """Return if entity is available.
        
        EWneo devices require transceiver connection for bidirectional communication.
        Can also be unavailable if CHANGE_STATE request times out.
        """
        # Base: RX11 must be connected
        if not self.coordinator.last_update_success:
            return False
        if not self.coordinator.transceiver or not self.coordinator.transceiver.is_connected:
            return False
        
        # EWneo specific: check if device is reachable (no timeout)
        # _available is set to False if CHANGE_STATE times out
        return self._available
    
    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        # Call super() to subscribe to coordinator updates for availability changes
        # We ignore state updates in _handle_coordinator_update, but need it for availability
        await super().async_added_to_hass()
        
        # Add NFILTER for gateway serial to enable bidirectional communication
        if self._gateway_serial:
            try:
                filter_success = await self.coordinator.transceiver.rx11_ewb_add_filter(self._gateway_serial)
                if filter_success:
                    _LOGGER.info("✅ EWneo switch %s: Added NFILTER for gateway %s", 
                                self._serial_number[-8:], self._gateway_serial[-8:])
                else:
                    _LOGGER.warning("⚠️ EWneo switch %s: Failed to add NFILTER for gateway %s", 
                                   self._serial_number[-8:], self._gateway_serial[-8:])
            except Exception as e:
                _LOGGER.error("❌ EWneo switch %s: Error adding NFILTER: %s", self._serial_number[-8:], e)
        else:
            _LOGGER.warning("⚠️ EWneo switch %s: No gateway serial configured, bidirectional communication may not work", 
                           self._serial_number[-8:])
        
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
        self.hass.bus.async_listen("eldat_ewneo_state_update", handle_ewneo_state_update)
        _LOGGER.debug("🔗 EWneo switch %s: Registered state update event listener", self._serial_number[-8:])
        
        # Query initial state from device
        if self._gateway_serial:
            self.hass.async_create_task(self._query_initial_state())
    
    async def _query_initial_state(self) -> None:
        """Query initial state from device using EWB_QUERY_STATE."""
        try:
            # Skip if a command has already been sent (prevents race condition)
            if self._initial_state_queried:
                _LOGGER.debug("⏭️ Skipping initial state query for EWneo switch %s CH%d (command already sent)", 
                            self._serial_number, self._channel + 1 if self._device_type_code in [0x06, 0x07] else 1)
                return
            
            _LOGGER.info("🔍 Querying initial state for EWneo switch %s CH%d", 
                        self._serial_number, self._channel + 1 if self._device_type_code in [0x06, 0x07] else 1)
            
            # Query with mode 0 (on/off state)
            result = await self.coordinator.transceiver.rx11_ewb_query_state(
                self._gateway_serial, self._serial_number, mode=0
            )
            
            if result:
                recent_mode, recent_state_bytes = result
                _LOGGER.debug("EWneo switch %s: Query state successful, mode=%d, state=%s", 
                             self._serial_number, recent_mode, 
                             [f"0x{b:02X}" for b in recent_state_bytes])
                
                # Parse the state
                parsed_state = self.coordinator._parse_ewneo_state(
                    self._device_type_code, recent_state_bytes, "ewneo_switch", self._serial_number
                )
                
                if parsed_state and parsed_state.get("type") == "switch":
                    # For dual/quad switches, extract the channel-specific state
                    if self._device_type_code in [0x06, 0x07]:
                        channel_key = f"channel_{self._channel + 1}"
                        channel_state = parsed_state.get(channel_key, {})
                        self._is_on = channel_state.get("on", False)
                        _LOGGER.info("✅ EWneo dual/quad switch %s CH%d: Initial state is %s", 
                                    self._serial_number, self._channel + 1, "ON" if self._is_on else "OFF")
                    else:
                        # Single switch
                        self._is_on = parsed_state.get("on", False)
                        _LOGGER.info("✅ EWneo switch %s: Initial state is %s", 
                                    self._serial_number, "ON" if self._is_on else "OFF")
                    
                    self._initial_state_queried = True
                    self.async_write_ha_state()
                else:
                    _LOGGER.warning("⚠️ Could not parse initial state for EWneo switch %s", self._serial_number)
            else:
                _LOGGER.warning("⚠️ Failed to query initial state for EWneo switch %s", self._serial_number)
                
        except Exception as e:
            _LOGGER.error("Error querying initial state for EWneo switch %s: %s", self._serial_number, e)
    
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator.
        
        For EWneo switches, we ignore coordinator state updates because the state
        comes directly from command responses. However, we DO update availability
        when the transceiver connection status changes.
        """
        # Only update HA state if availability might have changed
        # This ensures the UI reflects when the RX11 is disconnected/reconnected
        self.async_write_ha_state()
    
    async def _async_update_state_from_parsed(self, parsed_state: Dict[str, Any]) -> None:
        """Update switch state from parsed EWneo state (async for thread safety)."""
        old_state = self._is_on
        
        # For dual/quad switches, extract the channel-specific state
        if self._device_type_code in [0x06, 0x07]:
            channel_key = f"channel_{self._channel + 1}"  # Coordinator uses 1-based channel keys
            channel_state = parsed_state.get(channel_key, {})
            self._is_on = channel_state.get("on", self._is_on)
            
            # Log state change
            if old_state != self._is_on:
                _LOGGER.info("🎯 EWneo dual/quad switch %s CH%d: State updated from telegram - %s -> %s (reason: %s, counter: %s)", 
                            self._serial_number, self._channel + 1,
                            "ON" if old_state else "OFF",
                            "ON" if self._is_on else "OFF",
                            channel_state.get("reason", "?"),
                            channel_state.get("counter", "?"))
                self.async_write_ha_state()
        else:
            # Single switch
            self._is_on = parsed_state.get("on", self._is_on)
            
            # Log state change
            if old_state != self._is_on:
                _LOGGER.info("🎯 EWneo switch %s: State updated from telegram - %s -> %s (reason: %s, counter: %s)", 
                            self._serial_number,
                            "ON" if old_state else "OFF",
                            "ON" if self._is_on else "OFF",
                            parsed_state.get("reason", "?"),
                            parsed_state.get("counter", "?"))
                self.async_write_ha_state()


    def _create_switch_state_command(self, turn_on: bool, timer_duration: Optional[int] = None) -> tuple[int, list]:
        """Create state command for EWneo switch according to EWB_CHANGE_STATE specification.
        
        For dual/quad switches (0x06, 0x07), the state word has different layouts:
        - Single switch (0x03): Bits 26-24 for switch state
        - Dual switch (0x06): Bits 26-24 for switch #1, Bits 18-16 for switch #2
        - Quad switch (0x07): Similar pattern for 4 channels
        """
        # Determine if this is a dual/quad switch
        is_dual_quad = self._device_type_code in [0x06, 0x07]
        
        if timer_duration is not None:
            # Mode 1: Timer with specific duration
            # Note: Timer mode is not well-defined for dual/quad switches in the spec
            # Using single-channel approach for now
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
                         self._serial_number, mantissa * (2 ** exponent), exponent, mantissa)
            
            return (1, state_bytes)
        else:
            # Mode 0: Simple on/off
            reason_code = 2 if turn_on else 1  # 2=on, 1=off, 0=remain at state
            
            if is_dual_quad:
                # For dual/quad switches, position the reason code based on channel
                # Channel 0 (CH1): Bits 26-24, Channel 1 (CH2): Bits 18-16, etc.
                # We only change our channel, others get 0 (remain at state)
                state_word = 0
                
                if self._channel == 0:
                    # Channel 0 = Switch #1: Bits 26-24
                    state_word = (reason_code << 24)
                    _LOGGER.debug("EWneo dual/quad switch %s CH1: Command - %s (reason: %d, bits 26-24)", 
                                 self._serial_number, "ON" if turn_on else "OFF", reason_code)
                elif self._channel == 1:
                    # Channel 1 = Switch #2: Bits 18-16
                    state_word = (reason_code << 16)
                    _LOGGER.debug("EWneo dual/quad switch %s CH2: Command - %s (reason: %d, bits 18-16)", 
                                 self._serial_number, "ON" if turn_on else "OFF", reason_code)
                elif self._channel == 2:
                    # Channel 2 = Switch #3: Bits 10-8 (quad only)
                    state_word = (reason_code << 8)
                    _LOGGER.debug("EWneo quad switch %s CH3: Command - %s (reason: %d, bits 10-8)", 
                                 self._serial_number, "ON" if turn_on else "OFF", reason_code)
                elif self._channel == 3:
                    # Channel 3 = Switch #4: Bits 2-0 (quad only)
                    state_word = reason_code
                    _LOGGER.debug("EWneo quad switch %s CH4: Command - %s (reason: %d, bits 2-0)", 
                                 self._serial_number, "ON" if turn_on else "OFF", reason_code)
                else:
                    _LOGGER.error("Invalid channel %d for dual/quad switch", self._channel)
                    state_word = (reason_code << 24)  # Fallback to channel 0 (CH1)
            else:
                # Single switch: Bits 26-24
                state_word = (reason_code << 24)
                _LOGGER.debug("EWneo switch %s: Simple command - %s (reason: %d)", 
                             self._serial_number, "ON" if turn_on else "OFF", reason_code)
            
            # Convert to 4 bytes in big-endian order
            # All EWneo communication uses big-endian byte order
            state_bytes = [
                (state_word >> 24) & 0xFF,
                (state_word >> 16) & 0xFF,
                (state_word >> 8) & 0xFF,
                state_word & 0xFF
            ]
            
            return (0, state_bytes)
    
    async def _send_ewb_change_state(self, turn_on: bool, timer_duration: Optional[int] = None) -> bool:
        """Send EWB_CHANGE_STATE command to EWneo device."""
        if not self._gateway_serial:
            _LOGGER.error("EWneo switch %s: No gateway serial available for EWB command", self._serial_number)
            return False
        
        # Use lock to prevent simultaneous commands
        async with self._command_lock:
            try:
                # Mark that we've had a command (prevents initial state query from overwriting)
                self._initial_state_queried = True
                
                # Log current state before command
                old_state = self._is_on
                _LOGGER.debug("🔵 EWneo switch %s CH%d: State BEFORE command: %s, requesting: %s",
                             self._serial_number, self._channel + 1 if self._device_type_code in [0x06, 0x07] else 1,
                             "ON" if old_state else "OFF", "ON" if turn_on else "OFF")
                
                # Create state command
                mode, state_bytes = self._create_switch_state_command(turn_on, timer_duration)
                
                _LOGGER.info("🔄 Sending EWB_CHANGE_STATE to EWneo switch %s: %s%s", 
                            self._serial_number, "ON" if turn_on else "OFF",
                            f" (timer: {timer_duration}s)" if timer_duration else "")
                
                # Send command via coordinator's transceiver
                result = await self.coordinator.transceiver.rx11_ewb_change_state(
                    self._gateway_serial, self._serial_number, mode, state_bytes
                )
                
                # Check for errors and attempt automatic recovery
                if result and isinstance(result, tuple) and len(result) == 3 and isinstance(result[0], str) and result[0].startswith("ERR_"):
                    error_type = result[0]
                    
                    if error_type == "ERR_SERIAL_FILTER":
                        _LOGGER.warning("⚠️ Gateway-Filter-Fehler erkannt - versuche automatische Wiederherstellung...")
                        error_type, gateway_serial, receiver_serial = result
                        
                        # Try to re-add gateway to filter
                        if hasattr(self.coordinator.transceiver, 'rx11_ewb_add_filter'):
                            filter_success = await self.coordinator.transceiver.rx11_ewb_add_filter(gateway_serial)
                            if filter_success:
                                _LOGGER.info("✅ Gateway-Filter wiederhergestellt - wiederhole Befehl...")
                                # Retry the command
                                result = await self.coordinator.transceiver.rx11_ewb_change_state(
                                    self._gateway_serial, self._serial_number, mode, state_bytes
                                )
                                if not result or (isinstance(result, tuple) and len(result) == 3 and isinstance(result[0], str) and result[0].startswith("ERR_")):
                                    # Still failed
                                    raise HomeAssistantError(
                                        f"Gateway-Filter konnte nicht wiederhergestellt werden. "
                                        f"Bitte starten Sie Home Assistant neu oder fügen Sie das Gerät erneut hinzu."
                                    )
                            else:
                                raise HomeAssistantError(
                                    f"Gateway {gateway_serial[-8:]} nicht im RX11-Filter. "
                                    f"Bitte starten Sie Home Assistant neu oder fügen Sie das Gerät erneut hinzu."
                                )
                        else:
                            raise HomeAssistantError(
                                f"Gateway {gateway_serial[-8:]} nicht im RX11-Filter. "
                                f"Filter-Funktion nicht verfügbar - bitte Home Assistant neu starten."
                            )
                    
                    elif error_type == "ERR_RF_TIMEOUT":
                        error_type, receiver_serial, gateway_serial = result
                        # Mark device as unreachable
                        self._reachable = False
                        self.async_write_ha_state()
                        _LOGGER.warning("⚠️ EWneo switch %s nicht erreichbar (RF-Timeout)", self._serial_number[-8:])
                        return False
                    
                    elif error_type == "ERR_INVALID_SERIAL":
                        error_type, receiver_serial, gateway_serial = result
                        raise HomeAssistantError(
                            f"Ungültige Seriennummer {receiver_serial[-8:]}. "
                            f"Bitte löschen und fügen Sie das Gerät erneut hinzu."
                        )
                    
                    elif error_type == "ERR_CANCELED":
                        error_type, receiver_serial, gateway_serial = result
                        _LOGGER.warning("⚠️ Befehl wurde abgebrochen (möglicherweise zu schnelle Befehle) - Vorgang ignoriert")
                        # Don't raise error for canceled commands, just log and return success
                        # The device might have already processed a previous command
                        return True
                    
                    else:  # ERR_UNKNOWN or other
                        error_type, error_code, receiver_serial_from_error = result
                        # ErrorCode 255 (0xFF) often means device communication issue
                        if isinstance(error_code, int) and error_code == 255:
                            raise HomeAssistantError(
                                f"Kommunikationsfehler mit Empfänger {receiver_serial_from_error[-8:]}. "
                                f"Das Gerät antwortet nicht korrekt. Mögliche Ursachen: "
                                f"Gerät ist ausgeschaltet, zu weit entfernt, oder es gibt Funkstörungen. "
                                f"Versuchen Sie es erneut oder prüfen Sie die Geräteplatzierung."
                            )
                        else:
                            raise HomeAssistantError(
                                f"Unbekannter Fehler beim Senden des Befehls (Code: {error_type}, Details: {error_code}). "
                                f"Bitte versuchen Sie es erneut oder starten Sie Home Assistant neu."
                            )
                if result and isinstance(result, tuple) and len(result) == 2:
                    recent_mode, recent_state_bytes = result
                    _LOGGER.info("📥 EWneo switch %s: Received response - mode=%d, bytes=%s", 
                                 self._serial_number, recent_mode,
                                 [f"0x{b:02X}" for b in recent_state_bytes])
                    
                    # Parse the response to update local state
                    # The response contains the UPDATED state from the device
                    parsed_state = self.coordinator._parse_ewneo_state(
                        self._device_type_code, recent_state_bytes, "ewneo_switch", self._serial_number
                    )
                    
                    _LOGGER.info("🔍 EWneo switch %s: Parsed state = %s", 
                                 self._serial_number, parsed_state)
                    
                    if parsed_state and parsed_state.get("type") == "switch":
                        # For dual/quad switches, extract the channel-specific state
                        if self._device_type_code in [0x06, 0x07]:
                            channel_key = f"channel_{self._channel + 1}"  # Coordinator uses 1-based channel keys
                            channel_state = parsed_state.get(channel_key, {})
                            new_state = channel_state.get("on", turn_on)
                            _LOGGER.info("🎯 EWneo dual/quad switch %s CH%d: Extracted channel state - key=%s, state_dict=%s, new_state=%s", 
                                        self._serial_number, self._channel + 1, channel_key, channel_state, new_state)
                            self._is_on = new_state
                            _LOGGER.info("✅ EWneo dual/quad switch %s CH%d: State confirmed as %s from device response", 
                                        self._serial_number, self._channel + 1, "ON" if self._is_on else "OFF")
                        else:
                            # Single switch
                            self._is_on = parsed_state.get("on", turn_on)
                            _LOGGER.info("✅ EWneo switch %s: State confirmed as %s from device response", 
                                        self._serial_number, "ON" if self._is_on else "OFF")
                    else:
                        # Fallback: optimistic update
                        self._is_on = turn_on
                        _LOGGER.warning("⚠️ EWneo switch %s CH%d: Could not parse response, using optimistic state: %s", 
                                      self._serial_number, self._channel + 1 if self._device_type_code in [0x06, 0x07] else 1, "ON" if turn_on else "OFF")
                    
                    # Mark device as reachable and update timestamp
                    from datetime import datetime
                    self._reachable = True
                    self._last_seen = datetime.now()
                        
                    # Update Home Assistant state
                    _LOGGER.debug("🟢 EWneo switch %s CH%d: State AFTER update: %s",
                                 self._serial_number, self._channel + 1 if self._device_type_code in [0x06, 0x07] else 1,
                                 "ON" if self._is_on else "OFF")
                    self.async_write_ha_state()
                    return True
                else:
                    return False
                    
            except Exception as e:
                _LOGGER.error("Failed to set EWneo switch %s state: %s", self._serial_number, e)
                return False
    
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the EWneo switch on."""
        # Check if transceiver is connected
        if not self.coordinator.transceiver.is_connected:
            raise ServiceValidationError("RX11 nicht verbunden - Befehl kann nicht gesendet werden")
        
        # Check for timer duration in kwargs
        timer_duration = kwargs.get("timer_duration")
        success = await self._send_ewb_change_state(True, timer_duration)
        if not success:
            # Get device name from device_info, use entity name as fallback
            device_name = self.device_info.get("name") if self.device_info else None
            friendly_name = device_name or self._attr_name or self.name or f"Gerät {self._serial_number[-8:]}"
            raise ServiceValidationError(f"Befehl an '{friendly_name}' fehlgeschlagen - Gerät antwortet nicht")
    
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the EWneo switch off."""
        # Check if transceiver is connected
        if not self.coordinator.transceiver.is_connected:
            raise ServiceValidationError("RX11 nicht verbunden - Befehl kann nicht gesendet werden")
        
        success = await self._send_ewb_change_state(False)
        if not success:
            # Get device name from device_info, use entity name as fallback
            device_name = self.device_info.get("name") if self.device_info else None
            friendly_name = device_name or self._attr_name or self.name or f"Gerät {self._serial_number[-8:]}"
            raise ServiceValidationError(f"Befehl an '{friendly_name}' fehlgeschlagen - Gerät antwortet nicht")
    


class EldatEWReceiverSwitch(EldatEntity, SwitchEntity):
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
                        _LOGGER.warning("Invalid last_command_time format during init for %s", serial_number)
                        
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
        
        # Use name directly from entity_spec (like EldatSwitch does)
        # This preserves the original entity name from entity_specs.py
        self._attr_name = entity_spec.get("name", device_info.get('name', serial_number))
        
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
        """Return if entity is available - follows RX11 connection status."""
        return (
            self.coordinator.last_update_success 
            and self.coordinator.transceiver 
            and self.coordinator.transceiver.is_connected
        )

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
            success = await self.coordinator.send_command(
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
                        _LOGGER.warning("⚠️ Invalid last_command_time format for %s, resetting", self._serial_number)
                        
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
                _LOGGER.info("🆕 No stored state found for %s, initializing as OFF but available", self._serial_number)
                return None
                
        except Exception as e:
            _LOGGER.error("Error restoring persistent state for %s: %s", self._serial_number, e)
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
                    _LOGGER.info("📴 Default OFF state sent and saved for %s", self._serial_number)
                else:
                    _LOGGER.warning("⚠️ Failed to send default OFF state for %s", self._serial_number)
                    
        except Exception as e:
            _LOGGER.error("Error sending default OFF state for %s: %s", self._serial_number, e)

    async def _send_initial_state(self):
        """Send initial or restored state command."""
        if not self._is_heating_cooling:
            return
            
        # Only send initial state if we have a previous command to restore
        if not self._last_command_code or not self._last_command_time:
            _LOGGER.debug("📝 No previous state to restore for %s, skipping initial state send", self._serial_number)
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
                               self._serial_number, 
                               "ON" if self._is_on else "OFF", 
                               self._last_command_code)
                else:
                    _LOGGER.warning("⚠️ Failed to restore state for %s", self._serial_number)
                    
        except Exception as e:
            _LOGGER.error("Error sending initial state for %s: %s", self._serial_number, e)

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
            
            if self._is_heating_cooling:
                # Heating/cooling: Always send 0x00 (Command A for ON)
                button = 0x00
            elif self._operating_mode == 1:
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
            
            if self._is_heating_cooling:
                # Heating/cooling: Always send 0x01 (Command B for OFF)
                button = 0x01
            elif self._operating_mode == 1:
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
            _LOGGER.debug("💾 State saved to coordinator for %s: %s", self._serial_number, state_data)
        except Exception as e:
            _LOGGER.error("Failed to save state to coordinator for %s: %s", self._serial_number, e)
        
        # Update Home Assistant state
        self.async_write_ha_state()
        
        # Schedule repeat timer
        self._schedule_repeat_timer()
        
        _LOGGER.info("🔄 EW-Receiver heating/cooling %s state updated: %s → %s (Code: %s)", 
                    self._serial_number, 
                    "ON" if old_state else "OFF",
                    "ON" if self._is_on else "OFF",
                    command_code)

    def _schedule_repeat_timer(self) -> None:
        """Schedule timer to repeat last command after 4 hours."""
        if not self._is_heating_cooling:
            return
            
        # Cancel existing timer
        self._cancel_repeat_timer()
        
        # Only schedule timer if we have a command and hass is available
        if not self._last_command_code or not self.hass:
            return
        
        # Don't start timer during startup - wait until entity is added to hass
        if not self.hass.is_running:
            _LOGGER.debug("⏰ Skipping timer schedule during startup for %s", self._serial_number)
            return
        
        async def _repeat_last_command():
            """Repeat the last command."""
            try:
                await asyncio.sleep(self._repeat_interval)
                
                # Check if we're still active before sending
                if not self._last_command_code or not self.hass or not self.hass.is_running:
                    return
                
                _LOGGER.info("🔁 Repeating last command for EW-Receiver heating/cooling %s: Code %s (%s)", 
                           self._serial_number, self._last_command_code,
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
                                       self._serial_number)
                            # Schedule next repeat
                            self._schedule_repeat_timer()
                        else:
                            _LOGGER.warning("❌ Failed to repeat command for EW-Receiver heating/cooling %s", 
                                          self._serial_number)
                            
            except asyncio.CancelledError:
                _LOGGER.debug("⏰ Repeat timer cancelled for EW-Receiver heating/cooling %s", 
                            self._serial_number)
                raise
            except Exception as e:
                _LOGGER.error("Error repeating command for EW-Receiver heating/cooling %s: %s", 
                            self._serial_number, e)
        
        # Create and store the timer task
        self._repeat_timer = self.hass.async_create_task(_repeat_last_command())
        _LOGGER.debug("⏰ Scheduled repeat timer for EW-Receiver heating/cooling %s (4 hours)", 
                     self._serial_number)

    def _cancel_repeat_timer(self) -> None:
        """Cancel the repeat timer."""
        if self._repeat_timer and not self._repeat_timer.done():
            self._repeat_timer.cancel()
            _LOGGER.debug("⏰ Cancelled repeat timer for EW-Receiver heating/cooling %s", self._serial_number)
        self._repeat_timer = FileNotFoundError


class EldatTransmitterSwitch(EldatEntity, SwitchEntity):
    """Switch entity for ELDAT transmitters in 'Dauer' (permanent) mode.
    
    This creates a persistent toggle switch for each button on a transmitter.
    When the button is pressed, the switch state toggles (On -> Off or Off -> On).
    The state persists between button presses.
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
        self._button = entity_spec.get("button", "A")
        self._channel = entity_spec.get("channel", 0)
        self._is_on = False  # Persistent state
        self._available = True
        self._icon_on = entity_spec.get("icon_on", "mdi:toggle-switch")
        self._icon_off = entity_spec.get("icon_off", "mdi:toggle-switch-off")
        
        # Set up entity attributes
        self._attr_name = entity_spec.get("name", f"Transmitter {serial_number} Button {self._button}")
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_transmitter_switch_{self._button}")
        self._attr_icon = entity_spec.get("icon", self._icon_off)
        self._attr_device_class = SwitchDeviceClass.SWITCH
        self._attr_entity_registry_enabled_default = True
        
        _LOGGER.info("✅ Transmitter switch initialized: %s (button: %s)", 
                    self._attr_name, self._button)

    async def async_added_to_hass(self) -> None:
        """Register for button press events when entity is added to hass."""
        await super().async_added_to_hass()
        
        # Listen for button press events from this device
        # Multiple event types are fired for button presses, use the short_press event
        EVENT_BUTTON_SHORT_PRESS = "eldat_button_press"
        
        @callback
        def _handle_button_press(event):
            """Handle button press event - toggle state."""
            event_serial = event.data.get("device_id", "") or event.data.get("serial_number", "")
            
            # Check if this event is for our device
            if not event_serial.endswith(self._serial_number):
                return
            
            # Check if this is the button for this switch
            # button_name is the string like "A", "B", "C", "D"
            button_name = event.data.get("button_name", event.data.get("subtype", ""))
            if button_name == self._button:
                # Toggle state on button press
                self._is_on = not self._is_on
                _LOGGER.info("🔄 Transmitter switch %s toggled to %s (button %s pressed)",
                            self._attr_name, "ON" if self._is_on else "OFF", self._button)
                self.async_write_ha_state()
        
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_BUTTON_SHORT_PRESS, _handle_button_press)
        )
    
    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        return self._is_on
    
    @property
    def available(self) -> bool:
        """Return if entity is available - follows RX11 connection status."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.transceiver 
            and self.coordinator.transceiver.is_connected
        )

    @property
    def icon(self) -> str:
        if self._is_on:
            return self._icon_on
        return self._icon_off

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the switch on (manual override - not typical for transmitter switches)."""
        self._is_on = True
        self.async_write_ha_state()
        _LOGGER.debug("Transmitter switch %s manually turned ON", self._attr_name)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the switch off (manual override - not typical for transmitter switches)."""
        self._is_on = False
        self.async_write_ha_state()
        _LOGGER.debug("Transmitter switch %s manually turned OFF", self._attr_name)


class EldatTransmitterStateSwitch(EldatEntity, RestoreEntity, SwitchEntity):
    """Switch entity for EW-Transmitters in 2-button modes (Auf/Zu).

    Maintains persistent state mapped from button events.
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
        self._options = entity_spec.get("options", ["Auf", "Zu"])
        self._state_key = entity_spec.get("state_key", "transmitter_state")
        self._on_label = entity_spec.get("on_label", self._options[0])
        self._off_label = entity_spec.get("off_label", self._options[1] if len(self._options) > 1 else "Aus")

        self._attr_name = entity_spec.get("name", f"Transmitter {serial_number} State")
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_state")
        self._attr_icon = entity_spec.get("icon", "mdi:window-shutter")
        self._attr_device_class = SwitchDeviceClass.SWITCH
        self._attr_entity_registry_enabled_default = True

        self._current_state: str | None = None

        _LOGGER.info("✅ Transmitter state switch initialized: %s", self._attr_name)

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
                if normalized_name.lower().startswith("taste "):
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
                if new_state in self._options and new_state != self._current_state:
                    self._current_state = new_state
                    self.coordinator.set_device_state(
                        self._serial_number, {self._state_key: new_state}
                    )
                    self.async_write_ha_state()

        self.async_on_remove(
            self.hass.bus.async_listen("eldat_button_press", _handle_button_event)
        )
        self.async_on_remove(
            self.hass.bus.async_listen("eldat_button_press", _handle_button_event)
        )

    @property
    def is_on(self) -> bool:
        return self._current_state == self._on_label

    @property
    def available(self) -> bool:
        """Return if entity is available - follows RX11 connection status."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.transceiver
            and self.coordinator.transceiver.is_connected
        )

    async def async_turn_on(self, **kwargs) -> None:
        self._current_state = self._on_label
        self.coordinator.set_device_state(self._serial_number, {self._state_key: self._on_label})
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._current_state = self._off_label
        self.coordinator.set_device_state(self._serial_number, {self._state_key: self._off_label})
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "state_label": self._current_state,
            "on_label": self._on_label,
            "off_label": self._off_label,
        }


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
        # Entity is available if coordinator is running AND transceiver is connected
        return (
            self._available 
            and self.coordinator.last_update_success 
            and self.coordinator.transceiver.is_connected
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            # Check if transceiver is connected
            if not self.coordinator.transceiver.is_connected:
                raise ServiceValidationError("RX11 nicht verbunden - Befehl kann nicht gesendet werden")
            
            command = bytes([0x01, self._channel, 0xFF])
            success = await self.coordinator.send_command(self._serial_number, command)
            if success:
                if self._is_heating_cooling:
                    # Update persistent state for heating/cooling
                    self._update_persistent_state(True, "A")
                else:
                    # Stateless update for other types
                    self.async_write_ha_state()
            else:
                device_name = self._device_info.get("name", f"Gerät {self._serial_number[-8:]}")
                raise ServiceValidationError(f"{device_name}: RX11 antwortet nicht")
        except HomeAssistantError:
            raise
        except Exception as e:
            _LOGGER.exception("Error turning on switch %s channel %d", self._serial_number, self._channel)
            raise ServiceValidationError(f"Fehler beim Einschalten: {str(e)}")

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            # Check if transceiver is connected
            if not self.coordinator.transceiver.is_connected:
                raise ServiceValidationError("RX11 nicht verbunden - Befehl kann nicht gesendet werden")
            
            command = bytes([0x01, self._channel, 0x00])
            success = await self.coordinator.send_command(self._serial_number, command)
            if success:
                if self._is_heating_cooling:
                    # Update persistent state for heating/cooling
                    self._update_persistent_state(False, "B")
                else:
                    # Stateless update for other types
                    self.async_write_ha_state()
            else:
                device_name = self._device_info.get("name", f"Gerät {self._serial_number[-8:]}")
                raise ServiceValidationError(f"{device_name}: RX11 antwortet nicht")
        except HomeAssistantError:
            raise
        except Exception as e:
            _LOGGER.exception("Error turning off switch %s channel %d", self._serial_number, self._channel)
            raise ServiceValidationError(f"Fehler beim Ausschalten: {str(e)}")

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
                    self._serial_number, 
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
                                   self._serial_number, self._last_command_code,
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
                                       self._serial_number)
                            # Schedule next repeat
                            self._schedule_repeat_timer()
                        else:
                            _LOGGER.warning("❌ Failed to repeat command for EW-Receiver heating/cooling %s", 
                                          self._serial_number)
                            
                except Exception as e:
                    _LOGGER.error("Error repeating command for EW-Receiver heating/cooling %s: %s", 
                                self._serial_number, e)
            
            # Create and store the timer task
            self._repeat_timer = self.hass.async_create_task(_repeat_last_command())
            _LOGGER.debug("⏰ Scheduled repeat timer for EW-Receiver heating/cooling %s (4 hours)", 
                         self._serial_number)

    def _cancel_repeat_timer(self) -> None:
        """Cancel the repeat timer."""
        if self._repeat_timer and not self._repeat_timer.done():
            self._repeat_timer.cancel()
            _LOGGER.debug("⏰ Cancelled repeat timer for EW-Receiver heating/cooling %s", self._serial_number)
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