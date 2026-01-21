"""Support for Eldat select entities."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import EldatCoordinator
from .entity_specs import create_entity_specs_for_device

_LOGGER = logging.getLogger(__name__)

# Event name used for button presses (matches coordinator.py)
EVENT_BUTTON_SHORT_PRESS = "eldat_button_short_press"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eldat select platform."""
    coordinator: EldatCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    selects = []
    
    # Create select entities for registered devices
    registered_devices = coordinator.get_all_registered_devices()
    _LOGGER.debug("🔄 Checking %d registered devices for select entities", len(registered_devices))
    
    for serial_number, device_info in registered_devices.items():
        device_type = device_info.get("type", "unknown")
        device_name = device_info.get("name", serial_number)
        
        # Get entity specs for this device
        entity_specs = create_entity_specs_for_device(serial_number, device_info)
        
        # Check if there are select entities
        select_specs = entity_specs.get("select", [])
        if select_specs:
            _LOGGER.info("📝 Creating %d select entities for device %s (%s)", 
                        len(select_specs), device_name, device_type)
            for entity_spec in select_specs:
                selects.append(EldatSelect(coordinator, serial_number, device_info, entity_spec))
    
    if selects:
        _LOGGER.info("📝 Adding %d select entities to Home Assistant", len(selects))
        async_add_entities(selects, update_before_add=False)
    else:
        _LOGGER.debug("No select entities created (no devices with select configuration)")
    
    _LOGGER.info("✅ Select platform setup complete")


class EldatSelect(SelectEntity):
    """Representation of an Eldat select entity for grouped button state."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EldatCoordinator,
        serial_number: str,
        device_info: dict,
        entity_spec: dict,
    ) -> None:
        """Initialize the select entity."""
        self.coordinator = coordinator
        self._serial_number = serial_number
        self._device_info = device_info
        self._entity_spec = entity_spec
        
        # Set up options from entity spec
        self._attr_options = entity_spec.get("options", ["A", "B", "C", "D"])
        self._attr_current_option = None
        
        # Set up entity attributes
        self._attr_unique_id = entity_spec.get("unique_id", f"{serial_number}_state_select")
        self._attr_name = entity_spec.get("name", f"{device_info.get('name', 'Transmitter')} State")
        self._attr_icon = entity_spec.get("icon", "mdi:form-select")
        
        # Device info for device registry
        device_name = device_info.get("name", f"EW-Transmitter {serial_number[-6:]}")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial_number)},
            name=device_name,
            manufacturer="ELDAT",
            model="EW-Transmitter",
        )
        
        _LOGGER.debug(
            "EldatSelect initialized: %s with options %s",
            self.unique_id,
            self.options,
        )

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added."""
        await super().async_added_to_hass()
        
        # Listen for button press events
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_BUTTON_SHORT_PRESS, self._handle_button_press
            )
        )
        _LOGGER.debug("Select entity %s registered for button events", self.unique_id)

    @callback
    def _handle_button_press(self, event) -> None:
        """Handle incoming button press events."""
        event_serial = event.data.get("serial_number", "") or event.data.get("device_id", "")
        
        # Check if this event is for our device
        if not event_serial.endswith(self._serial_number[-6:]):
            return

        # Get button name from event (e.g., "A", "B", "C", "D")
        button_name = event.data.get("button_name", event.data.get("subtype", ""))
        
        # Check if this button is in our options
        if button_name in self.options:
            if self._attr_current_option != button_name:
                _LOGGER.debug(
                    "[%s] State changed to: %s (button %s pressed)",
                    self.unique_id,
                    button_name,
                    button_name,
                )
                self._attr_current_option = button_name
                self.async_write_ha_state()
        else:
            _LOGGER.debug(
                "[%s] Ignoring button %s (not in options: %s)",
                self.unique_id,
                button_name,
                self.options,
            )

    async def async_select_option(self, option: str) -> None:
        """Change the selected option (manual selection from UI)."""
        if option not in self.options:
            _LOGGER.warning(
                "Invalid option for %s: %s (available: %s)",
                self.unique_id,
                option,
                self.options,
            )
            return

        _LOGGER.debug(
            "[%s] Manually selected option: %s",
            self.unique_id,
            option
        )
        
        # Update state
        self._attr_current_option = option
        self.async_write_ha_state()

    @property
    def current_option(self) -> str | None:
        """Return the selected option."""
        return self._attr_current_option
