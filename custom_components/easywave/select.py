"""Support for Easywave select entities."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import EasywaveCoordinator
from .entity_specs import create_entity_specs_for_device

_LOGGER = logging.getLogger(__name__)

# Event name used for button presses (matches coordinator.py)
EVENT_BUTTON_SHORT_PRESS = "easywave_button_press"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Easywave select platform."""
    from homeassistant.helpers import entity_registry as er
    
    coordinator: EasywaveCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Get entity registry to prevent duplicate unique_ids
    entity_registry = er.async_get(hass)
    existing_unique_ids = set()
    
    # Collect all existing unique_ids for selects in this integration
    if entity_registry:
        for entity_entry in entity_registry.entities.values():
            # Only look at selects for our integration and config entry
            if (entity_entry.platform == "easywave" and 
                entity_entry.config_entry_id == config_entry.entry_id and
                entity_entry.domain == "select"):
                if entity_entry.unique_id:
                    existing_unique_ids.add(entity_entry.unique_id)
                    _LOGGER.debug("Found existing select unique_id: %s", entity_entry.unique_id[-20:])
    
    _LOGGER.debug("Checking %d existing select unique_ids for duplicates", len(existing_unique_ids))

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
                selects.append(EasywaveSelect(coordinator, serial_number, device_info, entity_spec))
    
    if selects:
        _LOGGER.info("📝 Adding %d select entities to Home Assistant", len(selects))
        async_add_entities(selects, update_before_add=False)
    else:
        _LOGGER.debug("No select entities created (no devices with select configuration)")
    
    _LOGGER.info("✅ Select platform setup complete")


class EasywaveSelect(SelectEntity):
    """Representation of an Easywave select entity for grouped button state."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EasywaveCoordinator,
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
        self._attr_unique_id = entity_spec.get("unique_id", f"{device_info['registration_id']}_state_select")
        self._attr_name = entity_spec.get("name", f"{device_info.get('name', 'Transmitter')} State")
        self._attr_icon = entity_spec.get("icon", "mdi:form-select")
        
        # Device info for device registry - check if device exists to preserve user-defined names
        # Use UUID-based identifier when available
        device_identifier = device_info['registration_id']
        from homeassistant.helpers import device_registry as dr
        device_registry = dr.async_get(coordinator.hass)
        existing_device = device_registry.async_get_device(identifiers={(DOMAIN, device_identifier)})
        if not existing_device:
            # Fallback: try legacy serial-based identifier
            existing_device = device_registry.async_get_device(identifiers={(DOMAIN, serial_number)})
        
        if existing_device and existing_device.name:
            # Use existing name to preserve name_by_user
            device_name = existing_device.name
        else:
            # New device - use default name
            device_name = device_info.get("name", f"Easywave Transmitter {serial_number}")
        
        from .const import usb_device_name
        _mfr, _ = usb_device_name(0x155A, 0x1014)  # default manufacturer
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_identifier)},
            serial_number=serial_number,
            name=device_name,
            manufacturer=_mfr,
            model="Easywave Transmitter",
        )
        
        _LOGGER.debug(
            "EasywaveSelect initialized: %s with options %s",
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
        if not event_serial.endswith(self._serial_number):
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
