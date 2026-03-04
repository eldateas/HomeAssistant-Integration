"""Button entities for EASYWAVE integration with 1.6s press detection system."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional, Callable

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    BUTTON_LABELS,
    TM_BUTTON_A,
    TM_BUTTON_B,
    TM_BUTTON_C,
    TM_BUTTON_D,
    DEVICE_ICONS,
    EVENT_DEVICE_ADDED,
)
from .coordinator import EasywaveCoordinator
from .entity import EasywaveEntity
from .entity_registry import get_entity_registry
from .translations import translate

_LOGGER = logging.getLogger(__name__)

# Long press threshold in seconds (Doppelklick-Erkennung)
LONG_PRESS_THRESHOLD = 1.0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EASYWAVE button entities."""
    from homeassistant.helpers import entity_registry as er
    
    coordinator: EasywaveCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    # Get entity registry to prevent duplicate unique_ids
    entity_registry = er.async_get(hass)
    existing_unique_ids = set()
    
    # Collect all existing unique_ids for buttons in this integration
    if entity_registry:
        for entity_entry in entity_registry.entities.values():
            # Only look at buttons for our integration and config entry
            if (entity_entry.platform == "easywave" and 
                entity_entry.config_entry_id == config_entry.entry_id and
                entity_entry.domain == "button"):
                if entity_entry.unique_id:
                    existing_unique_ids.add(entity_entry.unique_id)
                    _LOGGER.debug("Found existing button unique_id: %s", entity_entry.unique_id[-20:])
    
    _LOGGER.debug("Checking %d existing button unique_ids for duplicates", len(existing_unique_ids))
    
    # Track devices that already have button entities created during initial setup
    devices_with_button_entities = set()
    
    def _create_buttons_for_device(serial_number: str, device_info: Dict[str, Any]) -> list:
        """Create additional button entities for a device (excluding remove button)."""
        device_buttons = []
        device_type = device_info.get("type", "unknown")
        channels = device_info.get("channels", 1)
        
        # Create buttons based on device type
        # NOTE: Easywave Transmitters fire events instead of creating button entities
        # Only create button entities for bidirectional/receiver devices
        if device_type in ["ewneo_transceiver", "ewneo_bidi_transmitter"]:
            # Multi-channel transceivers get individual button entities for sending commands
            for button_id in range(channels):
                device_buttons.append(EasywaveButton(
                    coordinator=coordinator,
                    serial_number=serial_number,
                    device_info=device_info,
                    button_id=button_id,
                ))
        
        # NOTE: Remove button is now added separately in the main logic
        # to ensure it's created for ALL device types
        
        return device_buttons
    
    buttons = []
    
    # Only create entities if transceiver is connected
    if not coordinator.transceiver or not coordinator.transceiver.is_connected:
        _LOGGER.info("⚠️  USB transmitter not connected - button entities will be unavailable")
        
    # Create button entities ONLY for registered devices
    registered_devices = coordinator.get_all_registered_devices()
    _LOGGER.info("🔄 Restoring button entities for %d registered devices", len(registered_devices))
    
    # Get HA's entity registry to check for existing buttons
    from homeassistant.helpers import entity_registry as er
    ha_entity_registry = er.async_get(coordinator.hass)
    entity_registry = get_entity_registry()
    
    for serial_number, device_info in registered_devices.items():
        device_type = device_info.get("type", "unknown")
        device_name = device_info.get("name", serial_number)
        
        # NOTE: Remove buttons are no longer created - devices can be deleted via the
        # three-dot menu in the integration view (async_remove_config_entry_device)
        
        # PRIORITY: Use stored entity specs from registered_devices.json (SINGLE SOURCE OF TRUTH)
        stored_entities = device_info.get("entities", [])
        button_entities = [e for e in stored_entities if e.get("type") == "button"]
        
        # Fallback: regenerate (only for new devices without stored entities)
        if not button_entities:
            from .entity_specs import create_entity_specs_for_device
            entity_specs = create_entity_specs_for_device(serial_number, device_info)
            button_entities = entity_specs.get("button", [])
        
        has_action_buttons = False  # Track if device has actual action buttons (not just remove)
        
        # Skip button creation for heating/cooling Easywave Receivers and EWneo devices
        # Motor receivers use configured button entities from entity specs
        device_type = device_info.get("type", "unknown")
        receiver_kind = device_info.get("receiver_kind", "switch")
        is_neo_device = device_info.get("neo_device", False) or device_type.startswith("ewneo_")
        skip_action_buttons = ((device_type == "ew_receiver" and 
                               receiver_kind in ["heating", "cooling", "heating_cooling"]) or
                              is_neo_device)
        
        if button_entities and not skip_action_buttons:
            # Create configured button entities (for EW receivers)
            for entity_spec in button_entities:
                try:
                    # Skip remove buttons as we already created one above
                    if entity_spec.get("action") == "remove_device":
                        continue
                    
                    has_action_buttons = True  # This device has real action buttons
                    
                    # Get action from multiple possible locations
                    action = (entity_spec.get("action") or 
                             entity_spec.get("button_config", {}).get("type"))
                    
                    # Normalize entity spec to have consistent fields
                    entity_spec = entity_spec.copy()
                    if not entity_spec.get("action"):
                        entity_spec["action"] = action
                    
                    # Create standard button
                    button = EasywaveEWReceiverButton(coordinator, serial_number, device_info, entity_spec)
                    
                    buttons.append(button)
                    _LOGGER.info("✅ Created Easywave Receiver button: %s (%s)", 
                                entity_spec.get('translation_key') or entity_spec.get('name'), action)
                except Exception as e:
                    _LOGGER.error("Error creating configured button for %s: %s", serial_number, e)
        elif skip_action_buttons and is_neo_device:
            _LOGGER.debug("Skipped action button creation for EWneo device %s - uses switch entities only", 
                        serial_number)
        elif skip_action_buttons:
            _LOGGER.debug("Skipped action button creation for Easywave Receiver %s (heating/cooling) - using switch entity instead", 
                        serial_number)
        else:
            # Create additional legacy button entities for bidirectional device types
            additional_buttons = _create_buttons_for_device(serial_number, device_info)
            if additional_buttons:
                buttons.extend(additional_buttons)
                has_action_buttons = len(additional_buttons) > 0
                _LOGGER.debug("Created %d additional button entities for device: %s (%s)", 
                            len(additional_buttons), device_name, device_type)
        
        # Only track devices with actual action buttons (not just remove buttons)
        if has_action_buttons:
            devices_with_button_entities.add(serial_number)
            _LOGGER.debug("Tracking device %s as having action buttons", serial_number)
    
    if buttons:
        async_add_entities(buttons)
        _LOGGER.info("✅ Added %d button entities for %d devices", 
                    len(buttons), len(coordinator.get_all_devices()))
    
    # ═══ CENTRAL DISPATCHER ═══
    # Create async handler for this platform to be called by central dispatcher
    async def _handle_button_from_dispatcher(serial_number: str, device_info: Dict[str, Any], entity_specs: List[Dict[str, Any]]) -> None:
        """Handle button entity creation for a device.
        
        Called by central dispatcher with entity specs already prepared.
        This replaces all the old event listener logic.
        """
        new_buttons = []
        device_type = device_info.get("type", "unknown")
        
        for entity_spec in entity_specs:
            try:
                # Skip remove buttons as we create them separately
                if entity_spec.get("action") == "remove_device":
                    continue
                
                button = EasywaveEWReceiverButton(coordinator, serial_number, device_info, entity_spec)
                new_buttons.append(button)
                _LOGGER.debug("Created button entity: %s (%s)", entity_spec.get('name'), entity_spec.get('action'))
            except Exception as e:
                _LOGGER.error("Error creating button for %s: %s", serial_number[-8:], e)
        
        if new_buttons:
            async_add_entities(new_buttons, update_before_add=False)
            _LOGGER.debug("Added %d button entities for %s", len(new_buttons), serial_number[-8:])
            devices_with_button_entities.add(serial_number)
    
    # Register handler with central dispatcher
    coordinator.register_platform_handler("button", _handle_button_from_dispatcher)


class EasywaveButton(EasywaveEntity, ButtonEntity):
    """Basic EASYWAVE button entity."""
    
    def __init__(
        self,
        coordinator: EasywaveCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        button_id: int,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._button_id = button_id
        from .helpers_unique_id import make_unique_id
        reg_id = device_info["registration_id"]
        self._attr_unique_id = make_unique_id(reg_id, "button", button_id)
        
        self._attr_name = f"Button {button_id + 1}"
        
        # Set icon based on device type
        device_type = device_info.get("type", "unknown")
        self._attr_icon = DEVICE_ICONS.get(device_type, "mdi:button-pointer")

    @property
    def available(self) -> bool:
        """Return if entity is available.
        
        Easywave buttons inherit the RX11 transceiver connection status via via_device 
        linkage.
        """
        return self._is_rx11_connected()

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            _LOGGER.debug("Button %s pressed", self.entity_id)
            
            # Create button press command for the device
            success = await self.coordinator.transceiver.send_command_to_device(
                self._serial_number, self._create_button_press_command()
            )
            
            if success:
                _LOGGER.debug("Button press successful: %s", self.entity_id)
            else:
                _LOGGER.error("Button press failed: %s", self.entity_id)
                
        except Exception as e:
            _LOGGER.error("Error in button press: %s", e)

    def _create_button_press_command(self) -> bytes:
        """Create button press command bytes."""
        # Simple implementation - can be enhanced for specific device types
        return bytes([0x02, self._button_id, 0x01])


class EWReceiverUIButton(EasywaveEntity, ButtonEntity):
    """Easywave Receiver button optimized for Home Assistant UI with explicit short/long press actions."""
    
    def __init__(
        self,
        coordinator: EasywaveCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        """Initialize the Easywave Receiver UI button."""
        super().__init__(coordinator, serial_number, device_info)
        
        # Entity configuration from spec
        self._entity_spec = entity_spec
        self._attr_unique_id = entity_spec.get("unique_id", f"{device_info['registration_id']}_{entity_spec.get('action', 'button')}")
        self._attr_name = entity_spec.get("name", "Easywave Receiver Button")
        self._attr_icon = entity_spec.get("icon", "mdi:gesture-tap")
        
        # Button configuration
        self._channel = entity_spec.get("channel", 0)
        self._button_code = entity_spec.get("button_code", 0)  # TM_BUTTON_A/B/C/D (0-3)
        self._action = entity_spec.get("action", "toggle")
        self._action_type = entity_spec.get("action_type", "press")  # "press" or "press_and_hold"
        self._receiver_kind = entity_spec.get("receiver_kind", "switch")
        self._button_config = entity_spec.get("button_config", {})
        self._long_press_duration = entity_spec.get("long_press_duration", 3.0)
        
        # Check if this button supports long press via config
        self._supports_long_press = (entity_spec.get("supports_long_press", False) or
                                   entity_spec.get("button_config", {}).get("supports_long_press", False))
        
        # Toggle state for long press (Schiebeschalter)
        self._is_long_press_active = False
        self._continuous_task: Optional[asyncio.Task] = None
        
        # Double-click detection for unified long press (like EWReceiverLongPressButton)
        self._press_start_time: Optional[float] = None
        self._long_press_task: Optional[asyncio.Task] = None
        self._last_pressed_time: Optional[datetime] = None
        
        _LOGGER.debug("✅ Easywave Receiver UI Button created: %s (Channel: %d, Action: %s, Type: %s, Supports Long Press: %s)", 
                     self._attr_name, self._channel, self._action, self._action_type, self._supports_long_press)

    @property
    def available(self) -> bool:
        """Return if entity is available.
        
        EW Receiver UI buttons inherit the RX11 transceiver connection status 
        via via_device linkage.
        """
        return self._is_rx11_connected()

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        attrs = {
            "serial_number": self._serial_number,
            "channel": self._channel,
            "button_code": self._button_code,
            "action": self._action,
            "action_type": self._action_type,
            "supports_long_press": self._supports_long_press,
        }
        
        # Add last pressed timestamp if available (lokale Zeit)
        if self._last_pressed_time:
            attrs["last_pressed"] = self._last_pressed_time.strftime("%d.%m.%Y %H:%M:%S")
        
        if self._supports_long_press:
            attrs["longpress_active"] = self._is_long_press_active
            if self._is_long_press_active:
                attrs["status"] = translate("config_flow.button_status_active_stop", hass=self.hass)
            else:
                attrs["status"] = translate("config_flow.button_status_ready_doubleclick", hass=self.hass)
        elif self._action_type == "press_and_hold":
            attrs["longpress_active"] = self._is_long_press_active
            if self._is_long_press_active:
                attrs["status"] = translate("config_flow.button_status_active_stop", hass=self.hass)
            else:
                attrs["status"] = translate("config_flow.button_status_ready_click_hold", hass=self.hass)
        else:
            attrs["status"] = translate("config_flow.button_status_ready_click", hass=self.hass)
            
        return attrs

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        if self._supports_long_press and self._is_long_press_active:
            return "Stop Command"  # Clear indication that button stops the active command
        elif self._action_type == "press_and_hold" and self._is_long_press_active:
            return "Stop Command"  # Clear indication that button stops the active command
        else:
            return self._entity_spec.get("name", "Easywave Receiver Button")

    @property
    def icon(self) -> str:
        """Return the icon to be used for this entity."""
        if self._supports_long_press and self._is_long_press_active:
            return "mdi:stop-circle"  # Show stop icon when long press is active
        elif self._action_type == "press_and_hold" and self._is_long_press_active:
            return "mdi:stop-circle"  # Show stop icon when long press is active
        else:
            return self._attr_icon  # Use original icon

    async def async_press(self) -> None:
        """Handle button press - execute the configured action type or handle double-click detection."""
        _LOGGER.info("🔘 Easywave Receiver UI Button pressed: %s (Type: %s, Supports Long Press: %s)", 
                    self._attr_name, self._action_type, self._supports_long_press)
        
        # Update last pressed timestamp
        self._last_pressed_time = datetime.now()
        
        try:
            # If this button supports long press via config, use double-click detection
            if self._supports_long_press:
                await self._handle_long_press_button_click()
                return
            
            # Original logic for buttons without long press support
            if self._action_type == "press_and_hold":
                # Toggle long press action (Schiebeschalter)
                if self._is_long_press_active:
                    # Stop long press
                    success = await self._stop_long_press()
                    press_type = "long_stop"
                else:
                    # Start long press
                    success = await self._start_long_press()
                    press_type = "long_start"
            else:
                # Execute short press action
                success = await self._execute_short_press()
                press_type = "short"
            
            if success:
                _LOGGER.info("✅ Easywave Receiver %s successful: %s", press_type, self._attr_name)
                
                # Update button icon based on state
                if self._action_type == "press_and_hold":
                    if self._is_long_press_active:
                        self._attr_icon = "mdi:stop-circle"  # Show stop icon when active
                    else:
                        self._attr_icon = "mdi:gesture-tap-hold"  # Show hold icon when inactive
                
                # Fire unified events basierend auf RX11 Grundfunktionen
                if press_type == "short":
                    self.hass.bus.async_fire("easywave_button_press", {
                        "device_id": self._serial_number,
                        "entity_id": self.entity_id,
                        "subtype": self._action,
                        "action": self._action,
                        "action_type": self._action_type,
                        "channel": self._channel,
                        "receiver_kind": self._receiver_kind,
                        "device_name": self._device_info.get("name", "Unknown")
                    })
                elif press_type == "long_start":
                    self.hass.bus.async_fire("easywave_button_hold", {
                        "device_id": self._serial_number,
                        "entity_id": self.entity_id,
                        "subtype": self._action,
                        "action": self._action,
                        "action_type": self._action_type,
                        "channel": self._channel,
                        "receiver_kind": self._receiver_kind,
                        "device_name": self._device_info.get("name", "Unknown")
                    })
                elif press_type == "long_stop":
                    self.hass.bus.async_fire("easywave_button_release", {
                        "device_id": self._serial_number,
                        "entity_id": self.entity_id,
                        "subtype": self._action,
                        "action": self._action,
                        "action_type": self._action_type,
                        "channel": self._channel,
                        "receiver_kind": self._receiver_kind,
                        "device_name": self._device_info.get("name", "Unknown")
                    })
            else:
                _LOGGER.warning("❌ Easywave Receiver %s failed: %s", press_type, self._attr_name)
                
        except Exception as e:
            _LOGGER.error("❌ Error in Easywave Receiver UI button press for %s: %s", self._attr_name, e)

    async def _handle_long_press_button_click(self) -> None:
        """Handle button press with double-click detection for long press support.
        
        Verhalten:
        - Erster Klick: Startet 1-Sekunden-Timer für Kurzbefehl
        - Zweiter Klick (innerhalb 1s): Aktiviert Dauerbetrieb mit 'Stop Command'
        - Klick während Dauerbetrieb: Stoppt Dauerbetrieb
        """
        current_time = time.time()
        
        # Check if this is a second press within 1-second window (double-click detection)
        if (self._press_start_time is not None and 
            current_time - self._press_start_time <= LONG_PRESS_THRESHOLD):
            # Double-click detected within 1 second = activate continuous mode
            _LOGGER.info("🔒 Doppelklick erkannt (%.1fs) - aktiviere Dauerbetrieb für %s", 
                       LONG_PRESS_THRESHOLD, self._attr_name)
            await self._handle_unified_long_press_detected()
            return
        
        # Check if there's an ongoing continuous mode and this is the "stop" click
        if self._is_long_press_active:
            _LOGGER.info("🛑 Stop-Klick erkannt - beende Dauerbetrieb für %s", self._attr_name)
            await self._handle_unified_long_press_release()
            return
            
        # This is a new press - start tracking for potential double-click
        await self._handle_unified_new_press()

    async def _handle_unified_new_press(self) -> None:
        """Handle a new button press - start 1-second timer for short press detection."""
        self._press_start_time = time.time()
        
        # Cancel any existing timer
        if self._long_press_task:
            self._long_press_task.cancel()
            
        # Start 1-second timer to execute short press if no double-click comes
        self._long_press_task = asyncio.create_task(self._wait_and_execute_unified_short_press())
        
        _LOGGER.debug("⏱️ Unified new press - waiting 1s for possible double-click")

    async def _wait_and_execute_unified_short_press(self) -> None:
        """Wait 1 second, then execute short press if no double-click occurred."""
        try:
            await asyncio.sleep(LONG_PRESS_THRESHOLD)
            
            # If we reach here, no double-click occurred - execute short press
            if not self._is_long_press_active:
                _LOGGER.info("👆 1-Sekunden-Timeout erreicht - führe Kurzbefehl aus für %s", self._attr_name)
                success = await self._execute_short_press()
                if success:
                    # Fire press event (RX11 grundfunktion)
                    self.hass.bus.async_fire("easywave_button_press", {
                        "device_id": self._serial_number,
                        "entity_id": self.entity_id,
                        "subtype": self._action,
                        "action": self._action,
                        "channel": self._channel,
                        "receiver_kind": self._receiver_kind,
                        "device_name": self._device_info.get("name", "Unknown")
                    })
                self._reset_unified_press_state()
                
        except asyncio.CancelledError:
            # Normal cancellation when double-click occurs
            pass
        except Exception as e:
            _LOGGER.error("❌ Error in unified short press timer: %s", e)

    async def _handle_unified_long_press_detected(self) -> None:
        """Handle detection of double-click (continuous mode activation)."""
        # Cancel the short press timer
        if self._long_press_task:
            self._long_press_task.cancel()
            
        # Clear the press start time since we're transitioning to continuous mode
        self._press_start_time = None
        self._is_long_press_active = True
        
        # Force UI update to show stop icon and 'Stop Command' text immediately
        self._attr_icon = "mdi:stop-circle"
        self.async_write_ha_state()
        
        _LOGGER.info("🚀 Unified Dauerbetrieb aktiviert für %s - starte kontinuierliches Senden", self._attr_name)
        
        success = await self._start_unified_continuous_sending()
        if success:
            # Fire hold event (emuliert, > 1 sekunde)
            self.hass.bus.async_fire("easywave_button_hold", {
                "device_id": self._serial_number,
                "entity_id": self.entity_id,
                "subtype": self._action,
                "action": self._action,
                "channel": self._channel,
                "receiver_kind": self._receiver_kind,
                "device_name": self._device_info.get("name", "Unknown")
            })

    async def _handle_unified_long_press_release(self) -> None:
        """Handle stop click (third click to stop continuous mode)."""
        _LOGGER.info("🛑 Unified Dauerbetrieb gestoppt für %s - beende kontinuierliches Senden", self._attr_name)
        success = await self._stop_unified_continuous_sending()
        
        # Fire release event (RX11 grundfunktion)
        if success:
            self.hass.bus.async_fire("easywave_button_release", {
                "device_id": self._serial_number,
                "entity_id": self.entity_id,
                "subtype": self._action,
                "action": self._action,
                "channel": self._channel,
                "receiver_kind": self._receiver_kind,
                "device_name": self._device_info.get("name", "Unknown")
            })
        
        # Reset icon and force UI update
        self._attr_icon = self._entity_spec.get("icon", "mdi:gesture-tap-hold")
        self.async_write_ha_state()
        
        self._reset_unified_press_state()

    def _reset_unified_press_state(self) -> None:
        """Reset all unified press state variables."""
        self._press_start_time = None
        self._is_long_press_active = False
        if self._long_press_task:
            self._long_press_task.cancel()
            self._long_press_task = None

    async def _start_unified_continuous_sending(self) -> bool:
        """Start continuous sending using StartEwSendCmdLoop for unified system."""
        try:
            # Get device info to check device type
            device_info = self._device_info or {}
            device_type = device_info.get("type", "unknown")
            
            # Only use receiver methods for actual receivers
            if device_type not in ["ew_receiver", "EW_Receiver"]:
                _LOGGER.warning("❌ Cannot start continuous sending for device type '%s' - receiver methods only work for Easywave Receivers", device_type)
                return False
            
            # Get transceiver access to wrapper functions
            transceiver = self.coordinator.transceiver
            
            # Check if we have RX11 transceiver with wrapper
            if hasattr(transceiver, '_rx11_wrapper') and transceiver._rx11_wrapper:
                wrapper = transceiver._rx11_wrapper
                
                # Use the new rx11_ew_receiver_button_start_continuous method
                if hasattr(wrapper, 'rx11_ew_receiver_button_start_continuous'):
                    success = await wrapper.rx11_ew_receiver_button_start_continuous(self._serial_number, self._channel)
                    
                    if success:
                        _LOGGER.info("🚀 Unified StartEwSendCmdLoop started for %s (channel %d)", 
                                   self._attr_name, self._channel)
                    
                    return success
                    
            # Fallback to transceiver's method if available
            elif hasattr(transceiver, '_start_continuous_sending'):
                success = await transceiver._start_continuous_sending(
                    self._serial_number, self._channel
                )
                
                if success:
                    _LOGGER.info("🚀 Unified continuous sending started for %s (channel %d)", 
                               self._attr_name, self._channel)
                
                return success
            else:
                _LOGGER.warning("⚠️ Unified continuous sending not available - no suitable method found")
                return False
                
        except Exception as e:
            _LOGGER.error("❌ Error starting unified continuous sending: %s", e)
            return False

    async def _stop_unified_continuous_sending(self) -> bool:
        """Stop continuous sending using StopEwSendCmdLoop for unified system."""
        try:
            # Get transceiver access to wrapper functions
            transceiver = self.coordinator.transceiver
            
            # Check if we have RX11 transceiver with wrapper
            if hasattr(transceiver, '_rx11_wrapper') and transceiver._rx11_wrapper:
                wrapper = transceiver._rx11_wrapper
                
                # Use the new rx11_ew_receiver_button_stop_continuous method
                if hasattr(wrapper, 'rx11_ew_receiver_button_stop_continuous'):
                    success = await wrapper.rx11_ew_receiver_button_stop_continuous(self._serial_number, self._channel)
                    
                    if success:
                        _LOGGER.info("🛑 Unified StopEwSendCmdLoop completed for %s (channel %d)", 
                                   self._attr_name, self._channel)
                        return True
                    else:
                        _LOGGER.warning("⚠️ StopEwSendCmdLoop failed, trying fallback release command")
                else:
                    _LOGGER.warning("⚠️ rx11_ew_receiver_button_stop_continuous not available, using fallback")
                
                # Fallback 1: Use a release command to stop
                try:
                    fallback_success = await transceiver.send_command_to_device(
                        self._serial_number, 
                        bytes([0x00, self._channel, 0x00, 0x00, 0x00])  # Stop continuous (release)
                    )
                    
                    if fallback_success:
                        _LOGGER.info("🛑 Unified continuous sending stopped using fallback release for %s (channel %d)", 
                                   self._attr_name, self._channel)
                        return True
                    else:
                        _LOGGER.error("❌ Fallback release command also failed for %s", self._attr_name)
                        
                except Exception as fallback_error:
                    _LOGGER.error("❌ Fallback release command error: %s", fallback_error)
                
            # Fallback 2: Try transceiver's method if available
            elif hasattr(transceiver, '_stop_continuous_sending'):
                success = await transceiver._stop_continuous_sending(
                    self._serial_number
                )
                
                if success:
                    _LOGGER.info("🛑 Unified continuous sending stopped for %s (channel %d)", 
                               self._attr_name, self._channel)
                    return True
            else:
                _LOGGER.warning("⚠️ No stop methods available")
                
            # If all methods fail, still return True to reset UI state
            _LOGGER.warning("⚠️ All stop methods failed, but resetting UI state for %s", self._attr_name)
            return False
                
        except Exception as e:
            _LOGGER.error("❌ Error stopping unified continuous sending: %s", e)
            return False

    async def _execute_short_press(self) -> bool:
        """Execute a short press command."""
        try:
            # Send single push command using button_code (TM_BUTTON_A/B/C/D)
            # button_code: 0=A, 1=B, 2=C, 3=D
            button_letter = ['A', 'B', 'C', 'D'][self._button_code] if self._button_code < 4 else '?'
            cmd_bytes = bytes([0x01, self._button_code, 0x00, 0x00, 0x00])
            _LOGGER.warning("🔘 EWReceiverUIButton: name='%s', Button=%s (code=%d), action=%s, cmd=%s", 
                          self._attr_name, button_letter, self._button_code, self._action, cmd_bytes.hex())
            success = await self.coordinator.transceiver.send_command_to_device(
                self._serial_number, cmd_bytes
            )
            
            if success:
                _LOGGER.info("✅ Button %s pressed for %s (button_code=%d, action=%s)", 
                           button_letter, self._attr_name, self._button_code, self._action)
            
            return success
            
        except Exception as e:
            _LOGGER.error("❌ Error executing short press command: %s", e)
            return False

    async def _start_long_press(self) -> bool:
        """Start long press (toggle on - Schiebeschalter aktivieren)."""
        try:
            # Send command to start continuous sending using button_code
            button_letter = ['A', 'B', 'C', 'D'][self._button_code] if self._button_code < 4 else '?'
            cmd_bytes = bytes([0x01, self._button_code, 0x00, 0x00, 0x00])
            success = await self.coordinator.transceiver.send_command_to_device(
                self._serial_number, cmd_bytes
            )
            
            if success:
                self._is_long_press_active = True
                _LOGGER.info("🚀 Long press STARTED for %s - Button %s (code=%d, action=%s) - Schiebeschalter EIN", 
                           self._attr_name, button_letter, self._button_code, self._action)
                
                # Update icon immediately to show stop icon
                self._attr_icon = "mdi:stop-circle"
                self.async_write_ha_state()  # Force UI update
                
                # Start continuous sending task
                self._continuous_task = asyncio.create_task(self._continuous_sending_loop())
            
            return success
            
        except Exception as e:
            _LOGGER.error("❌ Error starting long press: %s", e)
            return False

    async def _stop_long_press(self) -> bool:
        """Stop long press (toggle off - Schiebeschalter deaktivieren)."""
        try:
            # Cancel continuous task
            if self._continuous_task and not self._continuous_task.done():
                self._continuous_task.cancel()
                try:
                    await self._continuous_task
                except asyncio.CancelledError:
                    pass
                self._continuous_task = None
            
            # Send stop command (same as start, but we stop the loop)
            cmd_bytes = bytes([0x01, self._button_code, 0x00, 0x00, 0x00])
            success = await self.coordinator.transceiver.send_command_to_device(
                self._serial_number, cmd_bytes
            )
            
            button_letter = ['A', 'B', 'C', 'D'][self._button_code] if self._button_code < 4 else '?'
            self._is_long_press_active = False
            _LOGGER.info("🛑 Long press STOPPED for %s - Button %s (code=%d, action=%s) - Schiebeschalter AUS", 
                       self._attr_name, button_letter, self._button_code, self._action)
                       
            # Reset icon back to default
            if self._action_type == "press_and_hold":
                self._attr_icon = "mdi:gesture-tap-hold"
                self.async_write_ha_state()  # Force UI update
            
            return True  # Always consider stop successful
            
        except Exception as e:
            _LOGGER.error("❌ Error stopping long press: %s", e)
            self._is_long_press_active = False
            return False

    async def _continuous_sending_loop(self) -> None:
        """Continuous sending loop for long press."""
        try:
            while self._is_long_press_active:
                # Send command every 300ms for continuous effect
                cmd_bytes = bytes([0x01, self._button_code, 0x00, 0x00, 0x00])
                try:
                    await self.coordinator.transceiver.send_command_to_device(
                        self._serial_number, cmd_bytes
                    )
                    await asyncio.sleep(0.3)  # 300ms interval for faster response
                except Exception as e:
                    _LOGGER.debug("Command failed in continuous loop: %s", e)
                    await asyncio.sleep(0.3)  # Continue trying
                    
        except asyncio.CancelledError:
            _LOGGER.debug("Continuous sending loop cancelled for %s", self._attr_name)
        except Exception as e:
            _LOGGER.error("❌ Error in continuous sending loop: %s", e)
        finally:
            self._is_long_press_active = False


# EWReceiverLongPressButton class removed - no longer needed
# Easywave Receiver buttons now use EasywaveEWReceiverButton without longpress support


class EasywaveEWReceiverButton(EasywaveEntity, ButtonEntity):
    """EASYWAVE button entity with LongPress support for Easywave Receiver stateless buttons."""
    
    def __init__(
        self,
        coordinator: EasywaveCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
        entity_spec: Dict[str, Any],
    ) -> None:
        """Initialize the configured button."""
        super().__init__(coordinator, serial_number, device_info)
        
        # Entity configuration from spec
        self._entity_spec = entity_spec
        self._attr_unique_id = entity_spec.get("unique_id", f"{device_info['registration_id']}_{entity_spec.get('action', 'button')}")
        
        # Use translation_key for HA's translation system if available
        translation_key = entity_spec.get("translation_key")
        if translation_key:
            self._attr_translation_key = translation_key
            self._attr_has_entity_name = True
        else:
            self._attr_name = entity_spec.get("name", "EASYWAVE Button")
        
        self._operating_mode = entity_spec.get("operating_mode", 1)
        self._button_config = entity_spec.get("button_config", {})
        self._receiver_kind = entity_spec.get("receiver_kind", "switch")
        self._channel = entity_spec.get("channel", 1)
        self._button_code = entity_spec.get("button_code", 0)  # TM_BUTTON_A/B/C/D (0-3)
        
        # New button configuration format
        self._button_type = self._button_config.get("type", "toggle")
        self._supports_long_press = self._button_config.get("supports_long_press", False)
        self._is_stateless = self._button_config.get("stateless", True)
        self._device_class = entity_spec.get("device_class", "switch")
        
        # Use icon from entity_spec if provided, otherwise use default based on button type
        if "icon" in entity_spec and entity_spec["icon"]:
            self._attr_icon = entity_spec["icon"]
        else:
            self._attr_icon = self._get_icon_for_button_type()
        
        # Track last pressed time for UI display
        self._last_pressed_time: Optional[datetime] = None
        
        # Helper for logging - use translation_key or name
        entity_label = getattr(self, '_attr_translation_key', None) or getattr(self, '_attr_name', None) or "Button"
        _LOGGER.debug("✅ Konfigurierter Button erstellt: %s (button_code: %d, icon: %s)", 
                     entity_label, self._button_code, self._attr_icon)

    @property
    def _entity_label(self) -> str:
        """Get entity label for logging (translation_key or name)."""
        return getattr(self, '_attr_translation_key', None) or getattr(self, '_attr_name', None) or self.entity_id or "Button"

    @property
    def available(self) -> bool:
        """Return if entity is available.
        
        Configured buttons inherit the RX11 transceiver connection status 
        via via_device linkage.
        """
        return self._is_rx11_connected()

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        attrs = {
            "serial_number": self._serial_number,
            "button_type": self._button_type,
            "channel": self._channel,
            "button_code": self._button_code,
        }
        
        # Add last pressed timestamp if available (lokale Zeit)
        if hasattr(self, '_last_pressed_time') and self._last_pressed_time:
            attrs["last_pressed"] = self._last_pressed_time.strftime("%d.%m.%Y %H:%M:%S")
        
        return attrs

    def _get_icon_for_button_type(self) -> str:
        """Get appropriate icon based on button type and device class."""
        icon_map = {
            "toggle": "mdi:toggle-switch",
            "turn_on": "mdi:power-on",
            "turn_off": "mdi:power-off",
            "open": "mdi:arrow-up",
            "close": "mdi:arrow-down", 
            "stop": "mdi:stop",
            "brighten": "mdi:brightness-6",
            "darken": "mdi:brightness-4",
            "heat_cool": "mdi:thermostat"
        }
        
        # Use device class specific icons if available
        if self._device_class == "garage":
            return icon_map.get(self._button_type, "mdi:window-shutter")
        elif self._device_class == "heat":
            return icon_map.get(self._button_type, "mdi:thermostat")
        else:
            return icon_map.get(self._button_type, "mdi:button-pointer")

    async def async_press(self) -> None:
        """Handle button press - simple immediate execution."""
        _LOGGER.info("🔘 Button pressed: %s", self._entity_label)
        
        # Update last pressed timestamp
        self._last_pressed_time = datetime.now()
        
        # Execute button press immediately without any timeout
        await self._execute_simple_press()

    async def _execute_simple_press(self) -> bool:
        """Execute a simple button press."""
        try:
            success = await self._send_normal_press_command()
            
            if success:
                _LOGGER.info("✅ Simple press successful: %s", self._entity_label)
                
                # Fire press event (RX11 grundfunktion)
                self.hass.bus.async_fire("easywave_button_press", {
                    "device_id": self._serial_number,
                    "entity_id": self.entity_id,
                    "subtype": self._action if hasattr(self, '_action') else "press",
                    "button_type": self._button_type,
                    "channel": self._channel,
                    "receiver_kind": self._receiver_kind,
                    "device_name": self._device_info.get("name", "Unknown")
                })
            
            return success
            
        except Exception as e:
            _LOGGER.error("❌ Error executing simple press: %s", e)
            return False

    async def _start_continuous_command(self) -> bool:
        """Start continuous command using the transceiver's method."""
        try:
            # Get device info to check device type
            device_info = self._device_info or {}
            device_type = device_info.get("type", "unknown")
            
            # Only use receiver methods for actual receivers
            if device_type not in ["ew_receiver", "EW_Receiver"]:
                _LOGGER.warning("❌ Cannot start continuous command for device type '%s' - receiver methods only work for Easywave Receivers", device_type)
                return False
            
            # Get transceiver access to wrapper functions
            transceiver = self.coordinator.transceiver
            
            # Check if we have RX11 transceiver with wrapper
            if hasattr(transceiver, '_rx11_wrapper') and transceiver._rx11_wrapper:
                wrapper = transceiver._rx11_wrapper
                
                # Use the new rx11_ew_receiver_button_start_continuous method
                if hasattr(wrapper, 'rx11_ew_receiver_button_start_continuous'):
                    success = await wrapper.rx11_ew_receiver_button_start_continuous(self._serial_number, self._channel)
                    
                    if success:
                        _LOGGER.info("🚀 StartEwSendCmdLoop started for %s (channel %d)", 
                                   self._entity_label, self._channel)
                        
                        # Fire hold event (emuliert, kontinuierliches Senden)
                        self.hass.bus.async_fire("easywave_button_hold", {
                            "device_id": self._serial_number,
                            "entity_id": self.entity_id,
                            "subtype": self._action if hasattr(self, '_action') else "hold",
                            "button_type": self._button_type,
                            "channel": self._channel,
                            "receiver_kind": self._receiver_kind,
                            "device_name": self._device_info.get("name", "Unknown")
                        })
                    
                    return success
            
            # Fallback to old method if new one not available
            return await self._send_long_press_command()
                
        except Exception as e:
            _LOGGER.error("❌ Error starting continuous command: %s", e)
            return False

    async def _stop_continuous_command(self) -> bool:
        """Stop continuous command using the transceiver's method."""
        try:
            # Get transceiver access to wrapper functions
            transceiver = self.coordinator.transceiver
            
            # Check if we have RX11 transceiver with wrapper
            if hasattr(transceiver, '_rx11_wrapper') and transceiver._rx11_wrapper:
                wrapper = transceiver._rx11_wrapper
                
                # Use the new rx11_ew_receiver_button_stop_continuous method
                if hasattr(wrapper, 'rx11_ew_receiver_button_stop_continuous'):
                    success = await wrapper.rx11_ew_receiver_button_stop_continuous(self._serial_number, self._channel)
                    
                    if success:
                        _LOGGER.info("🛑 StopEwSendCmdLoop completed for %s (channel %d)", 
                                   self._entity_label, self._channel)
                        
                        # Fire release event (RX11 grundfunktion)
                        self.hass.bus.async_fire("easywave_button_release", {
                            "device_id": self._serial_number,
                            "entity_id": self.entity_id,
                            "subtype": self._action if hasattr(self, '_action') else "release",
                            "button_type": self._button_type,
                            "channel": self._channel,
                            "receiver_kind": self._receiver_kind,
                            "device_name": self._device_info.get("name", "Unknown")
                        })
                    
                    return success
            
            # Fallback - just return True since there's no specific stop command in old system
            return True
                
        except Exception as e:
            _LOGGER.error("❌ Error stopping continuous command: %s", e)
            return False
    async def _send_long_press_command(self) -> bool:
        """Send LongPress command to Easywave Receiver (legacy method)."""
        try:
            # Create LongPress command based on button type and channel
            command = self._create_long_press_command()
            
            success = await self.coordinator.transceiver.send_command_to_device(
                self._serial_number, command
            )
            
            if success:
                _LOGGER.debug("✅ LongPress-Befehl gesendet: %s (Kanal %d)", 
                             self._button_type, self._channel)
            
            return success
            
        except Exception as e:
            _LOGGER.error("❌ Fehler beim Senden des LongPress-Befehls: %s", e)
            return False

    async def _send_normal_press_command(self) -> bool:
        """Send normal press command to Easywave Receiver."""
        try:
            # Create normal command based on button type and channel
            command = self._create_normal_command()
            
            success = await self.coordinator.transceiver.send_command_to_device(
                self._serial_number, command
            )
            
            if success:
                _LOGGER.debug("✅ Normal-Befehl gesendet: %s (Kanal %d)", 
                             self._button_type, self._channel)
            
            return success
            
        except Exception as e:
            _LOGGER.error("❌ Fehler beim Senden des Normal-Befehls: %s", e)
            return False

    def _create_long_press_command(self) -> bytes:
        """Create LongPress command based on button configuration."""
        # Create 5-byte command for long press: [telegram_type, button_code, 0x00, 0x00, 0x00]
        # For now, use same format as normal press (0x01 = PUSH)
        # The continuous sending loop in wrapper will handle the long press behavior
        cmd_bytes = bytes([0x01, self._button_code, 0x00, 0x00, 0x00])
        
        _LOGGER.debug("🔒 LongPress-Befehl erstellt: %s für button_code %d", 
                     cmd_bytes.hex(), self._button_code)
        
        return cmd_bytes

    def _create_normal_command(self) -> bytes:
        """Create normal command based on button configuration.""" 
        # Create 5-byte command: [telegram_type, button_code, 0x00, 0x00, 0x00]
        # telegram_type: 0x01 = PUSH
        # button_code: 0=A, 1=B, 2=C, 3=D (TM_BUTTON_A/B/C/D)
        cmd_bytes = bytes([0x01, self._button_code, 0x00, 0x00, 0x00])
        
        _LOGGER.debug("🔘 Normal-Befehl erstellt: %s für button_code %d (Typ: %s)", 
                     cmd_bytes.hex(), self._button_code, self._button_type)
        
        return cmd_bytes





class EasywaveTestButton(EasywaveEntity, ButtonEntity):
    """Button entity for testing device communication."""
    
    def __init__(
        self,
        coordinator: EasywaveCoordinator,
        serial_number: str,
        device_info: Dict[str, Any],
    ) -> None:
        """Initialize the test button."""
        super().__init__(coordinator, serial_number, device_info)
        
        self._attr_unique_id = f"{device_info['registration_id']}_test"
        self._attr_name = "Test"
        self._attr_icon = "mdi:test-tube"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Return if entity is available.
        
        Test buttons inherit the RX11 transceiver connection status 
        via via_device linkage.
        """
        return self._is_rx11_connected()

    async def async_press(self) -> None:
        """Handle the test button press."""
        try:
            _LOGGER.info("Test button pressed for device: %s", self._serial_number)
            
            success = await self.coordinator.transceiver.send_command_to_device(
                self._serial_number, self._create_test_command()
            )
            
            if success:
                _LOGGER.info("Test command sent successfully")
            else:
                _LOGGER.warning("Test command failed")
                
        except Exception as e:
            _LOGGER.error("Error in test button press: %s", e)

    def _create_test_command(self) -> bytes:
        """Create test command bytes."""
        return bytes([0x01, 0x00, 0x01])


