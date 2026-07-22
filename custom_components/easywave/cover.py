"""Cover platform — HACS naming/icons for receivers and neo motors."""

from typing import Any, override

from easywave_home_control.codec import (
    MotorActivity,
    MotorCommand,
    MotorFullState,
    MotorMoveCommand,
)
from easywave_home_control.codec.states import (
    MultiMotorFullState,
    MultiMotorSummaryState,
)

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
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
    CONF_RUNTIME_MEASURED,
    DEVICE_TYPE_CODE_DUAL_MOTOR,
    DEVICE_TYPE_CODE_MOTOR,
    DEVICE_TYPE_CODE_QUAD_MOTOR,
    ENTRY_TYPE_NEO_ACTUATOR,
    ENTRY_TYPE_RECEIVER,
    RECEIVER_KIND_COVER_2BUTTON,
    RECEIVER_KIND_MOTOR_3BUTTON,
    neo_motor_full_mode,
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

_OPENING_ACTIVITIES = {
    MotorActivity.OPENING_RUNTIME,
    MotorActivity.OPENING_120S,
    MotorActivity.OPENING_TO_POSITION,
    int(MotorActivity.OPENING_RUNTIME),
    int(MotorActivity.OPENING_120S),
    int(MotorActivity.OPENING_TO_POSITION),
}
_CLOSING_ACTIVITIES = {
    MotorActivity.CLOSING_RUNTIME,
    MotorActivity.CLOSING_120S,
    MotorActivity.CLOSING_TO_POSITION,
    int(MotorActivity.CLOSING_RUNTIME),
    int(MotorActivity.CLOSING_120S),
    int(MotorActivity.CLOSING_TO_POSITION),
}


def _protocol_to_ha_position(protocol_position: int | None) -> int | None:
    """Convert EWB protocol position (0=open, 100=closed) to HA (0=closed, 100=open)."""
    if protocol_position is None or protocol_position > 100:
        return None
    return 100 - int(protocol_position)


def _ha_to_protocol_position(ha_position: int) -> int:
    """Convert HA cover position to EWB protocol position."""
    return 100 - max(0, min(100, int(ha_position)))


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
    _attr_assumed_state = True

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
        self._stopped = False

    @override
    @property
    def state(self) -> str | None:
        """Return cover state, including stopped for 3-button receivers."""
        if self._has_stop and self._stopped:
            return "stopped"
        return super().state

    @property
    def icon(self) -> str:
        """Return shutter icon based on state."""
        if self._has_stop and self._stopped:
            return "mdi:stop-circle-outline"
        if self.is_closed is True:
            return "mdi:window-shutter"
        if self.is_closed is False:
            return "mdi:window-shutter-open"
        return "mdi:window-shutter-alert"

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open cover (button A)."""
        await self.async_send_button(BUTTON_A)
        self._stopped = False
        self._attr_is_closed = False
        self.async_write_ha_state()

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close cover (button B)."""
        await self.async_send_button(BUTTON_B)
        self._stopped = False
        self._attr_is_closed = True
        self.async_write_ha_state()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop cover (button C) and show Gestoppt / stopped."""
        if not self._has_stop:
            return
        if await self.async_send_button(BUTTON_C):
            self._stopped = True
            self._attr_is_opening = False
            self._attr_is_closing = False
            self.async_write_ha_state()


class EasywaveNeoCover(EasywaveNeoActuatorEntity, CoverEntity):
    """Bidirectional EWneo motor cover — Channel N for multi-channel."""

    def __init__(
        self,
        entry: EasywaveConfigEntry,
        device: EasywaveDeviceEntry,
        channel: int | None = None,
    ) -> None:
        """Initialize."""
        super().__init__(entry, device, "cover", channel=channel)
        self._attr_is_closed: bool | None = None
        self._attr_is_opening = False
        self._attr_is_closing = False
        self._attr_current_cover_position: int | None = None
        # Optional seed from migration; live value comes from EWB bit 7.
        self._runtime_measured = bool(device.data.get(CONF_RUNTIME_MEASURED, False))
        self._runtime_ever_measured = self._runtime_measured
        self._update_supported_features()
        if channel is None:
            self._attr_name = None
        else:
            self._attr_translation_key = f"channel_{channel + 1}"

    def _command_mode(self) -> int:
        """Return EWB mode for change/query commands on this channel."""
        return neo_motor_full_mode(self._device_type_code, self._channel)

    @override
    async def async_added_to_hass(self) -> None:
        """Register for dispatch, then query this channel's full state.

        Coordinator bulk restore runs during setup before entities exist, so each
        motor channel queries EWB_QUERY_STATE with its full mode (0 / 2 / 10 / …)
        here to detect ``runtime_measured`` and current position.
        """
        await super().async_added_to_hass()
        self.hass.async_create_task(
            self._async_query_initial_state(),
            name=f"easywave_query_{self._attr_unique_id}",
        )

    async def _async_query_initial_state(self) -> None:
        """EWB_QUERY_STATE for this channel's full mode (Laufzeit bit 7)."""
        await self._coordinator.async_query_actuator_state(
            gateway_serial=self._gateway_serial,
            actuator_serial=self._actuator_serial,
            device_type_code=self._device_type_code,
            mode=self._command_mode(),
        )

    def _update_supported_features(self) -> None:
        """Enable SET_POSITION once runtime measurement is known."""
        features = (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        )
        if self._runtime_ever_measured:
            features |= CoverEntityFeature.SET_POSITION
        self._attr_supported_features = features

    @property
    def icon(self) -> str:
        """Return shutter icon."""
        if self._attr_is_opening or self._attr_is_closing:
            return "mdi:window-shutter-alert"
        if self.is_closed is True:
            return "mdi:window-shutter"
        if self.is_closed is False:
            return "mdi:window-shutter-open"
        return "mdi:window-shutter-alert"

    @override
    @property
    def state(self) -> str | None:
        """Return opening/closing/open/closed/stopped like HACS 0.6."""
        if self._attr_is_opening:
            return "opening"
        if self._attr_is_closing:
            return "closing"
        if self._attr_current_cover_position is not None:
            return "closed" if self._attr_current_cover_position == 0 else "open"
        return "stopped"

    def _apply_activity(self, activity: MotorActivity | int) -> None:
        """Update opening/closing flags from a motor activity code."""
        if activity in _OPENING_ACTIVITIES:
            self._attr_is_opening = True
            self._attr_is_closing = False
            self._attr_is_closed = False
        elif activity in _CLOSING_ACTIVITIES:
            self._attr_is_opening = False
            self._attr_is_closing = True
            self._attr_is_closed = False
        else:
            self._attr_is_opening = False
            self._attr_is_closing = False

    def _apply_motor_full_state(self, state: MotorFullState) -> None:
        """Apply a parsed MotorFullState (single or unwrapped multi)."""
        if state.runtime_measured:
            self._runtime_measured = True
            if not self._runtime_ever_measured:
                self._runtime_ever_measured = True
                self._update_supported_features()

        self._apply_activity(state.activity)

        if self._runtime_measured:
            ha_position = _protocol_to_ha_position(state.current_position)
            self._attr_current_cover_position = ha_position
            if (
                ha_position is not None
                and not self._attr_is_opening
                and not self._attr_is_closing
            ):
                self._attr_is_closed = ha_position == 0
        else:
            self._attr_current_cover_position = None

    @override
    def handle_state(self, state: Any, *, mode: int = 0) -> None:
        """Update from parsed EWB_RCV / query / change-state motor payload."""
        self._parsed_state = state

        if isinstance(state, MotorFullState):
            self._apply_motor_full_state(state)
        elif isinstance(state, MultiMotorFullState):
            # Library channel is 1-based; entity channel is 0-based.
            if self._channel is not None and int(state.channel) != int(self._channel) + 1:
                return
            self._apply_motor_full_state(state.state)
        elif isinstance(state, MultiMotorSummaryState):
            if self._channel is None:
                # Single-entity dual/quad device shouldn't happen; ignore summary.
                return
            target = int(self._channel) + 1
            for channel_state in state.channels:
                if int(channel_state.channel) == target:
                    self._apply_activity(channel_state.activity)
                    break
            else:
                return
        else:
            return

        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the motor (runtime command if measured, else 120s)."""
        command = (
            MotorCommand.OPEN_RUNTIME
            if self._runtime_measured
            else MotorCommand.OPEN_120S
        )
        await self.async_change_state(
            MotorMoveCommand(command=command), mode=self._command_mode()
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the motor (runtime command if measured, else 120s)."""
        command = (
            MotorCommand.CLOSE_RUNTIME
            if self._runtime_measured
            else MotorCommand.CLOSE_120S
        )
        await self.async_change_state(
            MotorMoveCommand(command=command), mode=self._command_mode()
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the motor."""
        await self.async_change_state(
            MotorMoveCommand(command=MotorCommand.STOP), mode=self._command_mode()
        )

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move to a HA position percentage (requires runtime measurement)."""
        if not self._runtime_ever_measured:
            return
        if ATTR_POSITION not in kwargs:
            return
        ha_position = int(kwargs[ATTR_POSITION])
        protocol_position = _ha_to_protocol_position(ha_position)
        await self.async_change_state(
            MotorMoveCommand(command=protocol_position, position=protocol_position),
            mode=self._command_mode(),
        )
