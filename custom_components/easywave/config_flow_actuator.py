"""Subentry flow for adding Easywave neo actuators."""

import time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult

from .config_flow_learning import EasywaveDeviceFlowMixin
from .const import (
    CONF_ACTUATOR_SERIAL,
    CONF_CHANNELS,
    CONF_DEVICE_TYPE_CODE,
    CONF_ENTRY_TYPE,
    CONF_EWNEO_INDEX,
    CONF_GATEWAY_SERIAL,
    DEVICE_TYPE_CODE_TO_CHANNELS,
    ENTRY_TYPE_NEO_ACTUATOR,
    EWB_LEARNING_TIMEOUT,
    device_id_for_neo_actuator,
    normalize_serial_hex,
)


class EasywaveNeoActuatorSubentryFlowHandler(
    ConfigSubentryFlow, EasywaveDeviceFlowMixin
):
    """Handle adding Easywave neo actuators to the RX11 hub."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize."""
        super().__init__(*args, **kwargs)
        self._init_device_flow()
        self._ewneo_index: int | None = None
        self._gateway_serial: str | None = None
        self._actuator_serial: str | None = None
        self._device_type_code: int | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Entry point for adding an Easywave neo actuator."""
        coordinator = self._get_coordinator()
        if coordinator is None or not coordinator.transceiver.is_connected:
            return self.async_abort(reason="device_not_connected")
        return await self.async_step_actuator_intro()

    async def async_step_actuator_intro(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Introduce neo actuator pairing."""
        return self.async_show_menu(
            step_id="actuator_intro",
            menu_options=["learn_actuator"],
        )

    async def async_step_learn_actuator(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Allocate index and wait for EWB join."""
        self._learn_progress_action = "waiting_for_actuator"
        self._learn_confirm_step = "actuator_confirm"
        self._learn_step = "learn_actuator"
        self._learn_timeout_step = "learn_timeout_actuator"
        self._learn_back_step = "actuator_intro"
        return await self._await_learning_task(
            progress_action=self._learn_progress_action,
            confirm_step=self._learn_confirm_step,
            learn_step=self._learn_step,
        )

    async def async_step_learn_timeout_actuator(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle actuator learning timeout."""
        return await self.async_step_learn_timeout(user_input)

    async def _do_learning(self, coordinator: Any) -> dict[str, Any] | None:
        """Join an EWneo actuator on a free gateway index."""
        index = coordinator.allocate_ewneo_index()
        if index is None:
            return None
        gateway = await coordinator.transceiver.get_ewb_gateway_serial(index)
        if gateway is None:
            return None

        await coordinator.suspend_telegram_listener()
        try:
            deadline = time.monotonic() + EWB_LEARNING_TIMEOUT
            remaining = max(1.0, deadline - time.monotonic())
            joined = await coordinator.transceiver.ewb_join_device(
                gateway, timeout=remaining
            )
            if joined is None:
                return None
            device_type, receiver = joined
            return {
                "ewneo_index": index,
                "gateway_serial": normalize_serial_hex(gateway),
                "actuator_serial": normalize_serial_hex(receiver),
                "device_type_code": int(device_type),
            }
        finally:
            coordinator.resume_telegram_listener()

    async def async_step_actuator_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Confirm and save the neo actuator."""
        if self._learned_device is None:
            return self.async_abort(reason="no_device_learned")

        type_code = int(self._learned_device["device_type_code"])
        serial_hex = str(self._learned_device["actuator_serial"])
        unique_id = device_id_for_neo_actuator(serial_hex, type_code)

        if self._is_duplicate(
            unique_id,
            entry_type=ENTRY_TYPE_NEO_ACTUATOR,
            serial_hex=serial_hex,
        ):
            return self.async_abort(reason="already_configured")

        if user_input is not None and "title" in user_input:
            data = {
                CONF_ENTRY_TYPE: ENTRY_TYPE_NEO_ACTUATOR,
                CONF_ACTUATOR_SERIAL: serial_hex,
                CONF_EWNEO_INDEX: int(self._learned_device["ewneo_index"]),
                CONF_GATEWAY_SERIAL: str(self._learned_device["gateway_serial"]),
                CONF_DEVICE_TYPE_CODE: type_code,
                CONF_CHANNELS: DEVICE_TYPE_CODE_TO_CHANNELS.get(type_code, 1),
            }
            return await self._async_save_device(
                title=user_input["title"],
                unique_id=unique_id,
                data=data,
            )

        return self.async_show_form(
            step_id="actuator_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "title",
                        default=self._next_default_name(ENTRY_TYPE_NEO_ACTUATOR),
                    ): str,
                }
            ),
            description_placeholders={
                "type_code": f"0x{type_code:02X}",
                "channels": str(DEVICE_TYPE_CODE_TO_CHANNELS.get(type_code, 1)),
            },
        )
