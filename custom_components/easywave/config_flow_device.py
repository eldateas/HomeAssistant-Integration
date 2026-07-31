"""Device add flow steps for the Easywave config flow."""

from typing import Any

from easywave_home_control.codec import (
    ButtonPushEvent,
    SensorLearnPayload,
    SensorTelegramEvent,
)

from homeassistant.config_entries import SubentryFlowResult

from .config_flow_learning import EasywaveDeviceFlowMixin
from .const import (
    CONF_BUTTON_COUNT,
    CONF_COVER_MODE,
    CONF_ENTRY_TYPE,
    CONF_GROUPING_MODE,
    CONF_OPERATING_TYPE,
    CONF_SENSOR_CAPABILITIES,
    CONF_SENSOR_SERIAL,
    CONF_SWITCH_MODE,
    CONF_TRANSMITTER_SERIAL,
    CONF_USAGE_TYPE,
    ENTRY_TYPE_NEO_SENSOR,
    ENTRY_TYPE_TRANSMITTER,
    TRANSMITTER_GROUPING_COVER,
    TRANSMITTER_GROUPING_DUAL,
    TRANSMITTER_GROUPING_GROUP,
    TRANSMITTER_GROUPING_SINGLE,
    TRANSMITTER_SWITCH_COVER,
    TRANSMITTER_SWITCH_IMPULSE,
    TRANSMITTER_SWITCH_PERMANENT,
    TRANSMITTER_SWITCH_SWITCH,
    device_id_for_neo_sensor,
    device_id_for_transmitter,
)

_BUTTON_COUNT_MAP: dict[str, int] = {
    "buttons_1": 1,
    "buttons_2": 2,
    "buttons_3": 3,
    "buttons_4": 4,
}


def _normalize_learned_transmitter(telegram: Any) -> dict[str, Any] | None:
    """Return learned transmitter data from a codec event."""
    if not isinstance(telegram, ButtonPushEvent):
        return None
    return {
        "serial": telegram.transmitter_serial,
        "button": telegram.button,
    }


def _normalize_learned_sensor(telegram: Any) -> dict[str, Any] | None:
    """Return learned neo sensor data from a codec event."""
    if not isinstance(telegram, SensorTelegramEvent):
        return None
    if not isinstance(telegram.payload, SensorLearnPayload):
        return None
    return {
        "serial": telegram.sensor_serial,
        "capabilities": telegram.payload.capabilities,
        "has_battery": telegram.payload.has_battery,
        "measures_temperature": telegram.payload.measures_temperature,
        "measures_humidity": telegram.payload.measures_humidity,
    }


class EasywaveDeviceAddFlowMixin(EasywaveDeviceFlowMixin):
    """Device learning steps used by transmitter/sensor subentry flows."""

    _grouping_mode: str
    _switch_mode: str
    _button_count: int
    _operating_type: str
    _usage_type: str
    _cover_mode: bool

    def _init_transmitter_flow_state(self) -> None:
        """Initialize transmitter-specific flow state."""
        self._grouping_mode = TRANSMITTER_GROUPING_GROUP
        self._switch_mode = TRANSMITTER_SWITCH_IMPULSE
        self._button_count = 4
        self._operating_type = "1"
        self._usage_type = "switch"
        self._cover_mode = False

    async def async_step_transmitter(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Start transmitter setup from the add-device menu."""
        self._init_transmitter_flow_state()
        self._learn_progress_action = "waiting_for_transmitter"
        self._learn_confirm_step = "transmitter_confirm"
        self._learn_step = "learn_transmitter"
        self._learn_timeout_step = "learn_timeout_transmitter"
        self._learn_back_step = "transmitter_operating_type"
        self._accept_telegram = _normalize_learned_transmitter

        coordinator = self._get_coordinator()
        if coordinator is None or not coordinator.transceiver.is_connected:
            return self.async_abort(reason="device_not_connected")
        return await self.async_step_transmitter_operating_type()

    async def async_step_transmitter_operating_type(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Select transmitter operating type (1/2/3 button operation)."""
        return self.async_show_menu(
            step_id="transmitter_operating_type",
            menu_options=[
                "transmitter_op_1",
                "transmitter_op_2",
                "transmitter_op_3",
            ],
        )

    async def async_step_transmitter_op_1(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """1-button operation."""
        self._operating_type = "1"
        return await self.async_step_transmitter_grouping()

    async def async_step_transmitter_op_2(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """2-button operation."""
        self._operating_type = "2"
        return await self.async_step_transmitter_usage()

    async def async_step_transmitter_op_3(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """3-button cover operation."""
        self._operating_type = "3"
        self._button_count = 4
        self._grouping_mode = TRANSMITTER_GROUPING_COVER
        self._switch_mode = TRANSMITTER_SWITCH_COVER
        self._cover_mode = True
        self._learn_back_step = "transmitter_operating_type"
        return await self.async_step_learn()

    async def async_step_transmitter_grouping(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Select grouping for 1-button transmitters."""
        return self.async_show_menu(
            step_id="transmitter_grouping",
            menu_options=[
                "transmitter_grouping_group",
                "transmitter_grouping_single",
                "transmitter_operating_type",
            ],
        )

    async def async_step_transmitter_grouping_group(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Group mode."""
        self._grouping_mode = TRANSMITTER_GROUPING_GROUP
        return await self.async_step_transmitter_switch_mode()

    async def async_step_transmitter_grouping_single(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Individual mode."""
        self._grouping_mode = TRANSMITTER_GROUPING_SINGLE
        return await self.async_step_transmitter_switch_mode()

    async def async_step_transmitter_switch_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Select impulse vs permanent."""
        return self.async_show_menu(
            step_id="transmitter_switch_mode",
            menu_options=[
                "transmitter_switch_impulse",
                "transmitter_switch_permanent",
                "transmitter_grouping",
            ],
        )

    async def async_step_transmitter_switch_impulse(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Impulse mode."""
        self._switch_mode = TRANSMITTER_SWITCH_IMPULSE
        self._learn_back_step = "transmitter_switch_mode"
        return await self.async_step_button_count_select()

    async def async_step_transmitter_switch_permanent(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Permanent mode."""
        self._switch_mode = TRANSMITTER_SWITCH_PERMANENT
        self._learn_back_step = "transmitter_switch_mode"
        return await self.async_step_button_count_select()

    async def async_step_transmitter_usage(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Select switch vs cover usage for 2-button transmitters."""
        return self.async_show_menu(
            step_id="transmitter_usage",
            menu_options=[
                "transmitter_usage_switch",
                "transmitter_usage_cover",
                "transmitter_operating_type",
            ],
        )

    async def async_step_transmitter_usage_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """2-button switch usage."""
        self._usage_type = "switch"
        self._switch_mode = TRANSMITTER_SWITCH_SWITCH
        self._grouping_mode = TRANSMITTER_GROUPING_GROUP
        self._button_count = 2
        self._learn_back_step = "transmitter_usage"
        return await self.async_step_learn()

    async def async_step_transmitter_usage_cover(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """2-button cover usage."""
        self._usage_type = "cover"
        self._switch_mode = TRANSMITTER_SWITCH_COVER
        self._grouping_mode = TRANSMITTER_GROUPING_DUAL
        self._button_count = 2
        self._cover_mode = True
        self._learn_back_step = "transmitter_usage"
        return await self.async_step_learn()

    async def async_step_button_count_select(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Select number of transmitter buttons."""
        return self.async_show_menu(
            step_id="button_count_select",
            menu_options=[*list(_BUTTON_COUNT_MAP), "transmitter_switch_mode"],
        )

    async def _async_set_button_count(self, count_key: str) -> SubentryFlowResult:
        self._button_count = _BUTTON_COUNT_MAP[count_key]
        return await self.async_step_learn()

    async def async_step_buttons_1(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """1 button."""
        return await self._async_set_button_count("buttons_1")

    async def async_step_buttons_2(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """2 buttons."""
        return await self._async_set_button_count("buttons_2")

    async def async_step_buttons_3(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """3 buttons."""
        return await self._async_set_button_count("buttons_3")

    async def async_step_buttons_4(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """4 buttons."""
        return await self._async_set_button_count("buttons_4")

    async def async_step_transmitter_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Confirm the learned transmitter and create a subentry."""
        if self._learned_device is None:
            return self.async_abort(reason="no_device_learned")  # pragma: no cover

        serial_hex = self._learned_device["serial"].hex()
        unique_id = device_id_for_transmitter(serial_hex)

        if self._is_duplicate(
            unique_id,
            entry_type=ENTRY_TYPE_TRANSMITTER,
            serial_hex=serial_hex,
        ):
            return self.async_abort(reason="already_configured")

        if user_input is not None and "title" in user_input:
            data: dict[str, Any] = {
                CONF_ENTRY_TYPE: ENTRY_TYPE_TRANSMITTER,
                CONF_TRANSMITTER_SERIAL: serial_hex,
                CONF_OPERATING_TYPE: self._operating_type,
                CONF_BUTTON_COUNT: self._button_count,
                CONF_GROUPING_MODE: self._grouping_mode,
                CONF_SWITCH_MODE: self._switch_mode,
            }
            if self._operating_type == "2":
                data[CONF_USAGE_TYPE] = self._usage_type
            if self._cover_mode:
                data[CONF_COVER_MODE] = True
            return await self._async_save_device(
                title=user_input["title"],
                unique_id=unique_id,
                data=data,
                area_id=self._area_id_from_input(user_input),
            )

        return self.async_show_form(
            step_id="transmitter_confirm",
            data_schema=self._confirm_name_area_schema(
                title_default=self._next_default_name(ENTRY_TYPE_TRANSMITTER),
            ),
        )

    async def async_step_neo_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Start neo sensor setup from the add-device menu."""
        self._learn_progress_action = "waiting_for_sensor"
        self._learn_confirm_step = "sensor_confirm"
        self._learn_step = "learn_sensor"
        self._learn_timeout_step = "learn_timeout_sensor"
        self._learn_back_step = "sensor_learn_intro"
        self._accept_telegram = _normalize_learned_sensor

        coordinator = self._get_coordinator()
        if coordinator is None or not coordinator.transceiver.is_connected:
            return self.async_abort(reason="device_not_connected")
        return await self.async_step_sensor_learn_intro()

    async def async_step_sensor_learn_intro(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Describe neo sensor learning before starting the listen step."""
        menu_options = ["learn"]
        return self.async_show_menu(
            step_id="sensor_learn_intro",
            menu_options=menu_options,
        )

    async def async_step_sensor_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Confirm the learned neo sensor and create a subentry."""
        if self._learned_device is None:
            return self.async_abort(reason="no_device_learned")  # pragma: no cover

        serial_hex = self._learned_device["serial"].hex()
        unique_id = device_id_for_neo_sensor(serial_hex)

        if self._is_duplicate(
            unique_id,
            entry_type=ENTRY_TYPE_NEO_SENSOR,
            serial_hex=serial_hex,
        ):
            return self.async_abort(reason="already_configured")

        if user_input is not None and "title" in user_input:
            data: dict[str, Any] = {
                CONF_ENTRY_TYPE: ENTRY_TYPE_NEO_SENSOR,
                CONF_SENSOR_SERIAL: serial_hex,
                CONF_SENSOR_CAPABILITIES: self._learned_device["capabilities"],
            }
            return await self._async_save_device(
                title=user_input["title"],
                unique_id=unique_id,
                data=data,
                area_id=self._area_id_from_input(user_input),
            )

        return self.async_show_form(
            step_id="sensor_confirm",
            data_schema=self._confirm_name_area_schema(
                title_default=self._next_default_name(ENTRY_TYPE_NEO_SENSOR),
            ),
            description_placeholders={
                "sensor_list": await self._async_format_neo_sensor_list(
                    self._learned_device
                ),
            },
        )
