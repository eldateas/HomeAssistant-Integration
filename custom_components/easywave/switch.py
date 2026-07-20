"""Switch platform — HACS naming/icons for receivers and neo actuators."""

from typing import Any, override

from easywave_home_control.codec import (
    SwitchChangeCommand,
    SwitchDesiredAction,
    SwitchOnOffState,
    SwitchPosition,
)

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EasywaveConfigEntry
from .const import (
    BUTTON_A,
    BUTTON_B,
    CONF_CHANNELS,
    CONF_DEVICE_TYPE_CODE,
    CONF_ENTRY_TYPE,
    CONF_RECEIVER_KIND,
    DEVICE_TYPE_CODE_DUAL_SWITCH,
    DEVICE_TYPE_CODE_QUAD_SWITCH,
    DEVICE_TYPE_CODE_SWITCH,
    ENTRY_TYPE_NEO_ACTUATOR,
    ENTRY_TYPE_RECEIVER,
    RECEIVER_KIND_HEATING_COOLING,
    RECEIVER_KIND_SWITCH_2BUTTON,
)
from .devices import get_devices
from .entity import (
    EasywaveDeviceEntry,
    EasywaveNeoActuatorEntity,
    EasywaveReceiverEntity,
)

_SWITCH_TYPE_CODES = {
    DEVICE_TYPE_CODE_SWITCH,
    DEVICE_TYPE_CODE_DUAL_SWITCH,
    DEVICE_TYPE_CODE_QUAD_SWITCH,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EasywaveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Easywave switches."""
    for device in get_devices(entry):
        entry_type = device.data.get(CONF_ENTRY_TYPE)
        if entry_type == ENTRY_TYPE_RECEIVER:
            kind = str(device.data.get(CONF_RECEIVER_KIND, ""))
            if kind in {RECEIVER_KIND_SWITCH_2BUTTON, RECEIVER_KIND_HEATING_COOLING}:
                async_add_entities(
                    [EasywaveReceiverSwitch(entry, device)],
                    config_subentry_id=device.subentry_id,
                )
        elif entry_type == ENTRY_TYPE_NEO_ACTUATOR:
            type_code = int(device.data.get(CONF_DEVICE_TYPE_CODE, 0))
            if type_code not in _SWITCH_TYPE_CODES:
                continue
            channels = int(device.data.get(CONF_CHANNELS, 1))
            entities: list[SwitchEntity] = []
            if channels <= 1:
                entities.append(EasywaveNeoSwitch(entry, device))
            else:
                for channel in range(channels):
                    entities.append(EasywaveNeoSwitch(entry, device, channel=channel))
            async_add_entities(entities, config_subentry_id=device.subentry_id)


class EasywaveReceiverSwitch(EasywaveReceiverEntity, SwitchEntity):
    """Optimistic EW receiver switch — empty name (device title only)."""

    _attr_name = None
    _attr_assumed_state = True

    def __init__(
        self, entry: EasywaveConfigEntry, device: EasywaveDeviceEntry
    ) -> None:
        """Initialize."""
        super().__init__(entry, device, "switch")
        self._attr_is_on = False
        self._heating = (
            str(device.data.get(CONF_RECEIVER_KIND)) == RECEIVER_KIND_HEATING_COOLING
        )

    @property
    def icon(self) -> str:
        """Return HACS-style icons."""
        if self._heating:
            return "mdi:radiator" if self.is_on else "mdi:radiator-off"
        return "mdi:light-switch" if self.is_on else "mdi:light-switch-off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on via button A."""
        if await self.async_send_button(BUTTON_A):
            self._attr_is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off via button B."""
        if await self.async_send_button(BUTTON_B):
            self._attr_is_on = False
            self.async_write_ha_state()


class EasywaveNeoSwitch(EasywaveNeoActuatorEntity, SwitchEntity):
    """Bidirectional EWneo switch — Channel N naming for multi-channel."""

    def __init__(
        self,
        entry: EasywaveConfigEntry,
        device: EasywaveDeviceEntry,
        channel: int | None = None,
    ) -> None:
        """Initialize."""
        super().__init__(entry, device, "switch", channel=channel)
        self._attr_is_on = False
        if channel is None:
            # Single channel: empty name → device title only
            self._attr_name = None
        else:
            self._attr_translation_key = f"channel_{channel + 1}"

    @property
    def icon(self) -> str:
        """Return switch icon."""
        return "mdi:light-switch" if self.is_on else "mdi:light-switch-off"

    @override
    def handle_state(self, state: Any) -> None:
        """Update from parsed switch state."""
        super().handle_state(state)
        if isinstance(state, SwitchOnOffState):
            self._attr_is_on = state.position == SwitchPosition.ON
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the neo switch on."""
        mode = self._channel or 0
        await self.async_change_state(
            SwitchChangeCommand(action=SwitchDesiredAction.ON), mode=mode
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the neo switch off."""
        mode = self._channel or 0
        await self.async_change_state(
            SwitchChangeCommand(action=SwitchDesiredAction.OFF), mode=mode
        )
