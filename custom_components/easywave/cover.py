"""Cover platform — HACS naming/icons for receivers and neo motors."""

from typing import Any, override

from easywave_home_control.codec import (
    MotorActivity,
    MotorCommand,
    MotorFullState,
    MotorMoveCommand,
)

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EasywaveConfigEntry
from .const import (
    BUTTON_A,
    BUTTON_B,
    BUTTON_C,
    CONF_CHANNELS,
    CONF_DEVICE_TYPE_CODE,
    CONF_ENTRY_TYPE,
    CONF_RECEIVER_KIND,
    DEVICE_TYPE_CODE_DUAL_MOTOR,
    DEVICE_TYPE_CODE_MOTOR,
    DEVICE_TYPE_CODE_QUAD_MOTOR,
    ENTRY_TYPE_NEO_ACTUATOR,
    ENTRY_TYPE_RECEIVER,
    RECEIVER_KIND_COVER_2BUTTON,
    RECEIVER_KIND_MOTOR_3BUTTON,
)
from .devices import get_devices
from .entity import (
    EasywaveDeviceEntry,
    EasywaveNeoActuatorEntity,
    EasywaveReceiverEntity,
)

_MOTOR_TYPE_CODES = {
    DEVICE_TYPE_CODE_MOTOR,
    DEVICE_TYPE_CODE_DUAL_MOTOR,
    DEVICE_TYPE_CODE_QUAD_MOTOR,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EasywaveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Easywave covers."""
    for device in get_devices(entry):
        entry_type = device.data.get(CONF_ENTRY_TYPE)
        if entry_type == ENTRY_TYPE_RECEIVER:
            kind = str(device.data.get(CONF_RECEIVER_KIND, ""))
            if kind in {RECEIVER_KIND_COVER_2BUTTON, RECEIVER_KIND_MOTOR_3BUTTON}:
                async_add_entities(
                    [EasywaveReceiverCover(entry, device)],
                    config_subentry_id=device.subentry_id,
                )
        elif entry_type == ENTRY_TYPE_NEO_ACTUATOR:
            type_code = int(device.data.get(CONF_DEVICE_TYPE_CODE, 0))
            if type_code not in _MOTOR_TYPE_CODES:
                continue
            channels = int(device.data.get(CONF_CHANNELS, 1))
            entities: list[CoverEntity] = []
            if channels <= 1:
                entities.append(EasywaveNeoCover(entry, device))
            else:
                for channel in range(channels):
                    entities.append(EasywaveNeoCover(entry, device, channel=channel))
            async_add_entities(entities, config_subentry_id=device.subentry_id)


class EasywaveReceiverCover(EasywaveReceiverEntity, CoverEntity):
    """Optimistic EW receiver cover — empty name (device title only)."""

    _attr_name = None

    def __init__(
        self, entry: EasywaveConfigEntry, device: EasywaveDeviceEntry
    ) -> None:
        """Initialize."""
        super().__init__(entry, device, "cover")
        self._has_stop = (
            str(device.data.get(CONF_RECEIVER_KIND)) == RECEIVER_KIND_MOTOR_3BUTTON
        )
        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        if self._has_stop:
            features |= CoverEntityFeature.STOP
        self._attr_supported_features = features
        self._attr_is_closed: bool | None = None

    @property
    def icon(self) -> str:
        """Return shutter icon based on state."""
        if self.is_closed is True:
            return "mdi:window-shutter"
        if self.is_closed is False:
            return "mdi:window-shutter-open"
        return "mdi:window-shutter-alert"

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open cover (button A)."""
        await self.async_send_button(BUTTON_A)
        self._attr_is_closed = False
        self.async_write_ha_state()

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close cover (button B)."""
        await self.async_send_button(BUTTON_B)
        self._attr_is_closed = True
        self.async_write_ha_state()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop cover (button C)."""
        if self._has_stop:
            await self.async_send_button(BUTTON_C)


class EasywaveNeoCover(EasywaveNeoActuatorEntity, CoverEntity):
    """Bidirectional EWneo motor cover — Channel N for multi-channel."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(
        self,
        entry: EasywaveConfigEntry,
        device: EasywaveDeviceEntry,
        channel: int | None = None,
    ) -> None:
        """Initialize."""
        super().__init__(entry, device, "cover", channel=channel)
        self._attr_is_closed: bool | None = None
        if channel is None:
            self._attr_name = None
        else:
            self._attr_translation_key = f"channel_{channel + 1}"

    @property
    def icon(self) -> str:
        """Return shutter icon."""
        if self.is_closed is True:
            return "mdi:window-shutter"
        if self.is_closed is False:
            return "mdi:window-shutter-open"
        return "mdi:window-shutter-alert"

    @override
    def handle_state(self, state: Any) -> None:
        """Update from parsed motor state."""
        super().handle_state(state)
        if isinstance(state, MotorFullState):
            activity = state.activity
            if activity in {
                MotorActivity.OPENING_RUNTIME,
                MotorActivity.OPENING_120S,
                MotorActivity.OPENING_TO_POSITION,
            }:
                self._attr_is_closed = False
            elif activity in {
                MotorActivity.CLOSING_RUNTIME,
                MotorActivity.CLOSING_120S,
                MotorActivity.CLOSING_TO_POSITION,
            }:
                self._attr_is_closed = True
            elif activity == MotorActivity.STOPPED:
                position = getattr(state, "position", None)
                if isinstance(position, int):
                    self._attr_is_closed = position >= 95
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the motor."""
        mode = self._channel or 0
        await self.async_change_state(
            MotorMoveCommand(command=MotorCommand.OPEN_RUNTIME), mode=mode
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the motor."""
        mode = self._channel or 0
        await self.async_change_state(
            MotorMoveCommand(command=MotorCommand.CLOSE_RUNTIME), mode=mode
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the motor."""
        mode = self._channel or 0
        await self.async_change_state(
            MotorMoveCommand(command=MotorCommand.STOP), mode=mode
        )
