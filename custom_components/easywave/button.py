"""Button platform for Easywave EW receivers (HACS naming/icons)."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EasywaveConfigEntry
from .const import (
    BUTTON_A,
    BUTTON_LETTERS,
    CONF_ENTRY_TYPE,
    CONF_RECEIVER_KIND,
    ENTRY_TYPE_RECEIVER,
    RECEIVER_KIND_IMPULSE,
    RECEIVER_KIND_UNIVERSAL_4BUTTON,
)
from .devices import get_devices
from .entity import EasywaveDeviceEntry, EasywaveReceiverEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EasywaveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Easywave buttons for EW receivers."""
    for device in get_devices(entry):
        if device.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_RECEIVER:
            continue
        kind = str(device.data.get(CONF_RECEIVER_KIND, RECEIVER_KIND_IMPULSE))
        entities: list[ButtonEntity] = []
        if kind == RECEIVER_KIND_IMPULSE:
            entities.append(EasywaveReceiverImpulseButton(entry, device))
        elif kind == RECEIVER_KIND_UNIVERSAL_4BUTTON:
            for index, letter in enumerate(BUTTON_LETTERS):
                entities.append(
                    EasywaveReceiverChannelButton(entry, device, index, letter)
                )
        if entities:
            async_add_entities(entities, config_subentry_id=device.subentry_id)


class EasywaveReceiverImpulseButton(EasywaveReceiverEntity, ButtonEntity):
    """Impulse receiver — empty name so UI shows device title only."""

    _attr_name = None
    _attr_icon = "mdi:gesture-tap-button"

    def __init__(
        self, entry: EasywaveConfigEntry, device: EasywaveDeviceEntry
    ) -> None:
        """Initialize."""
        super().__init__(entry, device, "toggle")

    async def async_press(self) -> None:
        """Send impulse / toggle command."""
        await self.async_send_button(BUTTON_A)


class EasywaveReceiverChannelButton(EasywaveReceiverEntity, ButtonEntity):
    """Universal 4-button receiver channel (Button Code A–D)."""

    def __init__(
        self,
        entry: EasywaveConfigEntry,
        device: EasywaveDeviceEntry,
        button_index: int,
        letter: str,
    ) -> None:
        """Initialize."""
        super().__init__(entry, device, f"button_{letter}")
        self._button_index = button_index
        self._attr_translation_key = f"state_{letter}"
        self._attr_icon = f"mdi:alpha-{letter}-circle"

    async def async_press(self) -> None:
        """Send the channel button."""
        await self.async_send_button(self._button_index)
