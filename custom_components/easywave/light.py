"""Light platform for Easywave neo dimmers (HACS Channel naming)."""

from typing import Any, override

from easywave_home_control.codec import DimmerLevelCommand, DimmerLevelState

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EasywaveConfigEntry
from .const import (
    CONF_DEVICE_TYPE_CODE,
    CONF_ENTRY_TYPE,
    DEVICE_TYPE_CODE_DIMMER,
    ENTRY_TYPE_NEO_ACTUATOR,
)
from .devices import get_devices
from .entity import EasywaveDeviceEntry, EasywaveNeoActuatorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EasywaveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Easywave lights."""
    for device in get_devices(entry):
        if device.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_NEO_ACTUATOR:
            continue
        if int(device.data.get(CONF_DEVICE_TYPE_CODE, 0)) != DEVICE_TYPE_CODE_DIMMER:
            continue
        async_add_entities(
            [EasywaveNeoDimmer(entry, device)],
            config_subentry_id=device.subentry_id,
        )


class EasywaveNeoDimmer(EasywaveNeoActuatorEntity, LightEntity):
    """Bidirectional EWneo dimmer — empty name (device title)."""

    _attr_name = None
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(
        self, entry: EasywaveConfigEntry, device: EasywaveDeviceEntry
    ) -> None:
        """Initialize."""
        super().__init__(entry, device, "dimmer")
        self._attr_is_on = False
        self._attr_brightness = 0

    @property
    def icon(self) -> str:
        """Return lightbulb icon."""
        return "mdi:lightbulb-on" if self.is_on else "mdi:lightbulb-off"

    @override
    def handle_state(self, state: Any) -> None:
        """Update from parsed dimmer state."""
        super().handle_state(state)
        if isinstance(state, DimmerLevelState):
            level = int(state.current_level or 0)
            self._attr_brightness = max(0, min(255, int(level * 255 / 100)))
            self._attr_is_on = level > 0
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on / set brightness."""
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness or 255)
        level = max(1, min(100, int(brightness * 100 / 255)))
        await self.async_change_state(
            DimmerLevelCommand(target_level=level, duration=None)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the dimmer."""
        await self.async_change_state(
            DimmerLevelCommand(target_level=0, duration=None)
        )
