"""Light (dimmer) entities for ELDAT receivers."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from homeassistant.components.light import (
    LightEntity,
    ATTR_BRIGHTNESS,
    ColorMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, EVENT_FORCE_CREATE
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
    """Set up light entities for ELDAT devices."""
    from .const import EVENT_DEVICE_ADDED

    coordinator: EldatCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    lights: list[LightEntity] = []

    # Only create entities if transceiver is connected
    if not coordinator.transceiver or not coordinator.transceiver.is_connected:
        _LOGGER.info("⚠️  USB transmitter not connected - light entities will be unavailable")
        
    _LOGGER.info("🔄 Restoring light entities for %d devices", len(coordinator.get_all_devices()))
    for serial, device_info in coordinator.get_all_devices().items():
        for entity_spec in device_info.get("entities", []):
            if entity_spec.get("type") != "light":
                continue
            try:
                lt = EldatEWReceiverDimmer(coordinator, serial, device_info, entity_spec)
                if lt.unique_id not in coordinator.created_entity_unique_ids:
                    lights.append(lt)
                    coordinator.created_entity_unique_ids.add(lt.unique_id)
                else:
                    _LOGGER.warning("Light: Skipping duplicate light with unique_id: %s", lt.unique_id)
            except Exception as e:
                _LOGGER.error("Error creating light for %s: %s", serial, e)

    if lights:
        async_add_entities(lights)
        _LOGGER.info("✅ Added %d light entities", len(lights))

    async def _handle_device_added(event):
        try:
            serial = event.data.get("serial_number")
            device_info = event.data.get("device_info")
            
            _LOGGER.debug("Light: Handling device added event for %s with device_info: %s", serial, device_info)
            
            # First try to get specs from event data (direct entities)
            specs = [e for e in event.data.get("entities", []) if e.get("type") == "light"]
            if not specs:
                # Try from entity_info structure
                entity_info = event.data.get("entity_info", {})
                specs = [e for e in entity_info.get("entities", []) if e.get("type") == "light"]
            if not specs:
                # Fallback: get specs from device_info
                specs = [e for e in device_info.get("entities", []) if e.get("type") == "light"]
            
            _LOGGER.debug("Light: Found %d light specs for %s: %s", len(specs), serial, specs)

            new = []
            for spec in specs:
                try:
                    # Check if this is an EWneo device
                    if device_info.get("neo_device"):
                        # EWneo light entity
                        lt = EldatEWneoLight(coordinator, serial, device_info, spec)
                        _LOGGER.info("🔧 Created EWneo light entity for device %s", serial[-8:])
                    else:
                        # Regular light entity
                        lt = EldatEWReceiverDimmer(coordinator, serial, device_info, spec)
                    
                    _LOGGER.debug("Light: Created light entity with unique_id: %s", lt.unique_id)
                    
                    # Check if this entity already exists in Home Assistant
                    entity_registry = er.async_get(hass)
                    existing_entity = entity_registry.async_get_entity_id("light", "eldat_plugin", lt.unique_id)
                    
                    if existing_entity:
                        _LOGGER.debug("Light: Entity with unique_id %s already exists as %s, skipping", lt.unique_id, existing_entity)
                        continue
                    
                    if lt.unique_id not in coordinator.created_entity_unique_ids:
                        new.append(lt)
                        coordinator.created_entity_unique_ids.add(lt.unique_id)
                        _LOGGER.debug("Light: Added new light entity with unique_id: %s", lt.unique_id)
                    else:
                        _LOGGER.warning("Light: Skipping duplicate light with unique_id: %s from event", lt.unique_id)
                except Exception as e:
                    _LOGGER.error("Light: Error creating light from event for %s: %s", serial, e)
            
            if new:
                async_add_entities(new)
                _LOGGER.info("Light: Added %d light entities for %s", len(new), serial)
            else:
                _LOGGER.debug("Light: No new light entities to add for %s", serial)
        except Exception as e:
            _LOGGER.error("Light: Error handling device added for lights: %s", e)

    config_entry.async_on_unload(hass.bus.async_listen(EVENT_DEVICE_ADDED, _handle_device_added))
    
    # Force create handler
    async def _handle_force_create(event):
        try:
            serial_number = event.data.get("serial_number")
            entity_type = event.data.get("entity_type")
            device_info = event.data.get("device_info")
            
            if entity_type != "light" or not serial_number or not device_info:
                return
                
            _LOGGER.info("🔧 Force creating light entities for device %s", serial_number[-6:])
            
            # Create light entities from entity specs
            from .entity_specs import create_entity_specs_for_device
            entity_specs = create_entity_specs_for_device(serial_number, device_info)
            light_entities = entity_specs.get("light", [])
            
            new_lights = []
            for entity_spec in light_entities:
                if device_info.get("neo_device"):
                    new_lights.append(EldatEWneoLight(coordinator, serial_number, device_info, entity_spec))
                else:
                    new_lights.append(EldatEWReceiverDimmer(coordinator, serial_number, device_info, entity_spec))
                    
            if new_lights:
                async_add_entities(new_lights)
                _LOGGER.info("✅ Force-created %d light entities for device %s", len(new_lights), serial_number[-6:])
                
        except Exception:
            _LOGGER.exception("Error force-creating light entities")
    
    config_entry.async_on_unload(hass.bus.async_listen(EVENT_FORCE_CREATE, _handle_force_create))


class EldatEWReceiverDimmer(EldatEntity, LightEntity):
    """Light/dimmer entity created from entity specification with operating mode support."""

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        super().__init__(coordinator, serial_number, device_info)
        self._entity_spec = entity_spec
        self._channel = entity_spec.get("channel", 0)
        self._receiver_kind = entity_spec.get("receiver_kind", "dimmer")
        self._operating_mode = entity_spec.get("operating_mode", 1)
        
        # Create unique ID that matches config flow pattern
        base_unique_id = entity_spec.get("unique_id")
        if base_unique_id:
            # Use the unique_id from entity_spec (created by config flow)
            self._attr_unique_id = base_unique_id
        else:
            # Fallback: create unique ID that matches config flow pattern
            self._attr_unique_id = f"{serial_number}_light_{self._receiver_kind}_mode{self._operating_mode}_ch{self._channel}"
        
        # Operating mode and button configuration
        self._button_config = entity_spec.get("button_config", {})
        self._supports_long_press = entity_spec.get("supports_long_press", False)
        
        # Get device-specific configuration
        device_type = device_info.get("type", "unknown")
        device_config = get_entity_config_for_device(
            device_type=device_type,
            receiver_kind=self._receiver_kind,
            operating_mode=self._operating_mode,
            entity_type="light"
        )
        
        # Set name with action hints
        base_name = device_info.get('name', serial_number)
        self._attr_name = create_entity_name_with_action_hint(
            base_name=base_name,
            device_type=device_type,
            receiver_kind=self._receiver_kind,
            operating_mode=self._operating_mode,
            entity_type="light"
        )
        
        # Set device-specific icon
        self._attr_icon = entity_spec.get("icon") or device_config.get("icon", "mdi:brightness-6")
        
        # State tracking
        self._brightness = 128  # Default to 50% brightness
        self._is_on = False
        
        # Use supported_color_modes instead of deprecated SUPPORT_BRIGHTNESS
        if entity_spec.get("supports_brightness", True):
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF
        
        # For continuous dimming (long press)
        self._continuous_dimming_task = None

    @property
    def unique_id(self) -> str | None:
        """Return unique ID for the entity."""
        return self._attr_unique_id

    @property
    def is_on(self) -> bool | None:
        """Return light state - STATELESS: immer None für wiederholte Aktionen."""
        # STATELESS: Kein Zustandstracking - erlaubt wiederholte Ein/Aus/Dimm Aktionen
        return None

    @property
    def brightness(self) -> int | None:
        """Return brightness (0-255) - STATELESS: returns last set brightness."""
        # STATELESS: Return last set brightness regardless of on/off state
        # This allows repeated brightness commands to always be sent
        return self._brightness
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes with device-specific action descriptions."""
        base_attrs = super().extra_state_attributes
        
        device_type = self._device_info.get("type", "unknown")
        device_attrs = get_extra_state_attributes_for_device(
            device_type=device_type,
            receiver_kind=self._receiver_kind,
            operating_mode=self._operating_mode,
            entity_type="light"
        )
        
        # Merge with base attributes
        base_attrs.update(device_attrs)
        return base_attrs

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the light based on operating mode."""
        try:
            brightness = kwargs.get("brightness")
            
            if self._operating_mode == 1:
                # Mode 1: Toggle with button A
                button = self._button_config.get("toggle", 0)
                command = bytes([button])
                success = await self.coordinator.async_send_command(
                    self._serial_number, command, action="single"
                )
                if success:
                    # STATELESS: Kein Zustandstracking - erlaubt wiederholte Ein-Aktionen
                    if brightness is not None:
                        self._brightness = int(brightness)
                        
            elif self._operating_mode == 2:
                # Mode 2: On/Off with long press for dimming
                if brightness is not None and brightness != self._brightness:
                    # Need to adjust brightness
                    await self._adjust_brightness_continuous(brightness)
                else:
                    # Just turn on
                    button = self._button_config.get("on", 0)
                    command = bytes([button])
                    success = await self.coordinator.async_send_command(
                        self._serial_number, command, action="single"
                    )
                    if success:
                        # STATELESS: Kein Zustandstracking - erlaubt wiederholte Ein-Aktionen
                        pass
                        
            elif self._operating_mode == 3:
                # Mode 3: Separate brighter/darker/toggle buttons
                if brightness is not None:
                    # Need to adjust brightness
                    await self._adjust_brightness_step(brightness)
                else:
                    # Just turn on using toggle button
                    button = self._button_config.get("toggle", 2)
                    command = bytes([button])
                    success = await self.coordinator.async_send_command(
                        self._serial_number, command, action="single"
                    )
                    if success:
                        # STATELESS: Kein Zustandstracking - erlaubt wiederholte Ein-Aktionen
                        pass
            
            self.async_write_ha_state()
            
        except Exception as e:
            _LOGGER.error("Error turning on light %s: %s", self._attr_unique_id, e)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off the light based on operating mode."""
        try:
            if self._operating_mode == 1:
                # Mode 1: Toggle with button A (same as turn_on)
                button = self._button_config.get("toggle", 0)
                command = bytes([button])
                success = await self.coordinator.async_send_command(
                    self._serial_number, command, action="single"
                )
                if success:
                    # STATELESS: Kein Zustandstracking - erlaubt wiederholte Aus-Aktionen
                    pass
                    
            elif self._operating_mode == 2:
                # Mode 2: Separate off button
                button = self._button_config.get("off", 1)
                command = bytes([button])
                success = await self.coordinator.async_send_command(
                    self._serial_number, command, action="single"
                )
                if success:
                    # STATELESS: Kein Zustandstracking - erlaubt wiederholte Aus-Aktionen
                    pass
                    
            elif self._operating_mode == 3:
                # Mode 3: Toggle button to turn off
                button = self._button_config.get("toggle", 2)
                command = bytes([button])
                success = await self.coordinator.async_send_command(
                    self._serial_number, command, action="single"
                )
                if success:
                    # STATELESS: Kein Zustandstracking - erlaubt wiederholte Aus-Aktionen
                    pass
                    
            # Stop any continuous dimming when turning off
            if self._continuous_dimming_task:
                await self.coordinator.async_send_command(
                    self._serial_number, bytes([0]), action="continuous_stop"
                )
                self._continuous_dimming_task = None
            
            self.async_write_ha_state()
                
        except Exception as e:
            _LOGGER.error("Error turning off light %s: %s", self._attr_unique_id, e)
    
    async def _adjust_brightness_step(self, target_brightness: int) -> None:
        """Adjust brightness using step commands (Mode 3)."""
        try:
            current_brightness = self._brightness or 0  # Use stored brightness or 0 if None
            target_brightness = int(target_brightness)
            
            if target_brightness > current_brightness:
                # Need to brighten
                button = self._button_config.get("brighter", 0)
                steps = (target_brightness - current_brightness) // 25  # Rough step calculation
                
                for _ in range(max(1, steps)):
                    command = bytes([button])
                    await self.coordinator.async_send_command(
                        self._serial_number, command, action="single"
                    )
                    await asyncio.sleep(0.1)  # Small delay between steps
                    
            elif target_brightness < current_brightness:
                # Need to darken
                button = self._button_config.get("darker", 1)
                steps = (current_brightness - target_brightness) // 25
                
                for _ in range(max(1, steps)):
                    command = bytes([button])
                    await self.coordinator.async_send_command(
                        self._serial_number, command, action="single"
                    )
                    await asyncio.sleep(0.1)
            
            self._brightness = target_brightness
            # STATELESS: Kein Zustandstracking - brightness wird trotzdem gespeichert für UI
            
        except Exception as e:
            _LOGGER.error("Error adjusting brightness with steps for %s: %s", self._attr_unique_id, e)
    
    async def _adjust_brightness_continuous(self, target_brightness: int) -> None:
        """Adjust brightness using continuous sending (Mode 2 long press)."""
        try:
            current_brightness = self._brightness or 0  # Use stored brightness or 0 if None
            target_brightness = int(target_brightness)
            
            if target_brightness > current_brightness:
                # Need to brighten - use dim_up button with continuous sending
                button = self._button_config.get("dim_up", 0)
            elif target_brightness < current_brightness:
                # Need to darken - use dim_down button with continuous sending
                button = self._button_config.get("dim_down", 1)
            else:
                # No change needed
                return
            
            # Start continuous sending for dimming
            command = bytes([button])
            success = await self.coordinator.async_send_command(
                self._serial_number, command, action="continuous_start"
            )
            
            if success:
                # Calculate how long to hold the button (estimate)
                brightness_diff = abs(target_brightness - current_brightness)
                hold_time = brightness_diff / 255.0 * 2.0  # Estimate 2 seconds for full range
                
                # Hold for the calculated time
                await asyncio.sleep(hold_time)
                
                # Stop continuous sending
                await self.coordinator.async_send_command(
                    self._serial_number, bytes([0]), action="continuous_stop"
                )
                
                self._brightness = target_brightness
                # STATELESS: Kein Zustandstracking - brightness wird trotzdem gespeichert für UI
            
        except Exception as e:
            _LOGGER.error("Error adjusting brightness continuously for %s: %s", self._attr_unique_id, e)


class EldatEWneoLight(EldatEntity, LightEntity):
    """EWneo light entity with bidirectional EWB_CHANGE_STATE control."""

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
        self._brightness = None
        
        # Configure supported features based on device capabilities
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        
        if entity_spec.get("supports_brightness", True):
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_color_mode = ColorMode.ONOFF
            
        if entity_spec.get("supports_color", False):
            self._attr_supported_color_modes.add(ColorMode.RGB)

    @property
    def name(self) -> str:
        """Return the name of the light."""
        base_name = self._entity_spec.get("name", "Light")
        device_name = self._device_info.get("name", f"EWneo-{self._serial_number[-6:]}")
        return f"{device_name} {base_name}"

    @property
    def unique_id(self) -> str:
        """Return unique ID for this light."""
        return self._entity_spec.get("unique_id", f"{self._serial_number}_light_{self._channel}")

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return self._is_on

    @property
    def brightness(self) -> int | None:
        """Return the brightness of this light between 0..255."""
        if self._attr_color_mode == ColorMode.BRIGHTNESS:
            return self._brightness
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._available and self.coordinator.transceiver and self.coordinator.transceiver.is_connected

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, set up event listeners."""
        await super().async_added_to_hass()
        
        # Add NFILTER for gateway serial to enable bidirectional communication
        if self._gateway_serial:
            try:
                filter_success = await self.coordinator.transceiver.rx11_ewb_add_filter(self._gateway_serial)
                if filter_success:
                    _LOGGER.info("✅ EWneo light %s: Added NFILTER for gateway %s", 
                                self._serial_number[-8:], self._gateway_serial[-8:])
                else:
                    _LOGGER.warning("⚠️ EWneo light %s: Failed to add NFILTER for gateway %s", 
                                   self._serial_number[-8:], self._gateway_serial[-8:])
            except Exception as e:
                _LOGGER.error("❌ EWneo light %s: Error adding NFILTER: %s", self._serial_number[-8:], e)
        else:
            _LOGGER.warning("⚠️ EWneo light %s: No gateway serial configured, bidirectional communication may not work", 
                           self._serial_number[-8:])
        
        # Listen for EWneo state update events
        def handle_ewneo_state_update(event):
            """Handle EWneo state update events."""
            if event.data.get("serial_number") == self._serial_number:
                _LOGGER.info("🔄 EWneo light %s: Received state update event", self._serial_number[-8:])
                parsed_state = event.data.get("parsed_state", {})
                if parsed_state and parsed_state.get("type") == "light":
                    # Use thread-safe add_job to schedule state update
                    self.hass.add_job(self._async_update_state_from_parsed, parsed_state)
        
        # Register the event listener
        self.hass.bus.async_listen(f"{DOMAIN}_ewneo_state_update", handle_ewneo_state_update)
        _LOGGER.debug("🔗 EWneo light %s: Registered state update event listener", self._serial_number[-8:])
    
    async def _async_update_state_from_parsed(self, parsed_state: Dict[str, Any]) -> None:
        """Update light state from parsed EWneo state (async for thread safety)."""
        old_on = self._is_on
        old_brightness = self._brightness
        
        # Update on/off state
        self._is_on = parsed_state.get("on", self._is_on)
        
        # Update brightness if available
        if "brightness_pct" in parsed_state and self._attr_color_mode == ColorMode.BRIGHTNESS:
            brightness_pct = parsed_state["brightness_pct"]
            self._brightness = int((brightness_pct / 100.0) * 255)
        elif "brightness" in parsed_state and self._attr_color_mode == ColorMode.BRIGHTNESS:
            self._brightness = parsed_state["brightness"]
        
        # Log changes and update state
        state_changed = (old_on != self._is_on or old_brightness != self._brightness)
        
        if state_changed:
            if self._attr_color_mode == ColorMode.BRIGHTNESS:
                brightness_pct = round((self._brightness / 255.0) * 100, 1)
                _LOGGER.info("🎯 EWneo light %s: State updated - On: %s, Brightness: %d%% (%d/255)", 
                            self._serial_number[-8:], self._is_on, brightness_pct, self._brightness)
            else:
                _LOGGER.info("🎯 EWneo light %s: State updated - On: %s", 
                            self._serial_number[-8:], self._is_on)
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on using EWB_CHANGE_STATE."""
        if not self._gateway_serial:
            _LOGGER.error("EWneo light %s: No gateway serial available", self.unique_id)
            return

        try:
            # Prepare state data for turning on
            if ATTR_BRIGHTNESS in kwargs and self._attr_color_mode == ColorMode.BRIGHTNESS:
                # Set specific brightness level
                brightness = kwargs[ATTR_BRIGHTNESS]
                brightness_percent = int((brightness / 255.0) * 100)
                state_data = [brightness_percent, 0, 0, 0]
                _LOGGER.debug("EWneo light %s: Setting brightness to %d%% (%d/255)", 
                            self.unique_id, brightness_percent, brightness)
            else:
                # Just turn on (100% brightness or simple on)
                state_data = [100, 0, 0, 0] if self._attr_color_mode == ColorMode.BRIGHTNESS else [1, 0, 0, 0]

            # Send EWB_CHANGE_STATE command
            success = await self.coordinator.transceiver.rx11_ewb_change_state(
                gateway_serial=self._gateway_serial,
                receiver_serial=self._serial_number,
                mode=0,  # Standard mode
                state_data=state_data
            )

            if success:
                self._is_on = True
                if ATTR_BRIGHTNESS in kwargs and self._attr_color_mode == ColorMode.BRIGHTNESS:
                    self._brightness = kwargs[ATTR_BRIGHTNESS]
                elif self._attr_color_mode == ColorMode.BRIGHTNESS:
                    self._brightness = 255  # Full brightness
                    
                _LOGGER.info("EWneo light %s: Turned on successfully", self.unique_id)
            else:
                _LOGGER.warning("EWneo light %s: Failed to turn on", self.unique_id)

        except Exception as e:
            _LOGGER.error("EWneo light %s: Error turning on: %s", self.unique_id, e)
        finally:
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off using EWB_CHANGE_STATE."""
        if not self._gateway_serial:
            _LOGGER.error("EWneo light %s: No gateway serial available", self.unique_id)
            return

        try:
            # Send EWB_CHANGE_STATE command to turn off
            success = await self.coordinator.transceiver.rx11_ewb_change_state(
                gateway_serial=self._gateway_serial,
                receiver_serial=self._serial_number,
                mode=0,  # Standard mode
                state_data=[0, 0, 0, 0]  # 0 = off
            )

            if success:
                self._is_on = False
                _LOGGER.info("EWneo light %s: Turned off successfully", self.unique_id)
            else:
                _LOGGER.warning("EWneo light %s: Failed to turn off", self.unique_id)

        except Exception as e:
            _LOGGER.error("EWneo light %s: Error turning off: %s", self.unique_id, e)
        finally:
            self.async_write_ha_state()

    def _handle_telegram(self, telegram_data: dict) -> None:
        """Handle incoming EWneo state telegrams."""
        # This will be called by the coordinator when state updates are received
        try:
            raw_data = telegram_data.get("raw_data")
            if not raw_data:
                return

            # Parse state data for light state and brightness
            if isinstance(raw_data, str) and len(raw_data) >= 2:
                # Extract state from first byte
                state_byte = int(raw_data[:2], 16)
                
                if self._attr_color_mode == ColorMode.BRIGHTNESS:
                    # For brightness mode, state_byte represents brightness level (0-100%)
                    if state_byte == 0:
                        self._is_on = False
                        self._brightness = 0
                    else:
                        self._is_on = True
                        # Convert percentage to 0-255 scale
                        self._brightness = int((state_byte / 100.0) * 255)
                    
                    _LOGGER.debug("EWneo light %s: Brightness updated to %d%% (%d/255)", 
                                self.unique_id, state_byte, self._brightness)
                else:
                    # For on/off mode, any non-zero value means on
                    self._is_on = state_byte > 0
                    _LOGGER.debug("EWneo light %s: State updated to %s", 
                                self.unique_id, "on" if self._is_on else "off")
                
                self.async_write_ha_state()

        except Exception as e:
            _LOGGER.debug("EWneo light %s: Error parsing telegram data: %s", self.unique_id, e)
