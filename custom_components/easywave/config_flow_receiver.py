"""Subentry flow for adding Easywave EW receivers."""

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult

from .config_flow_learning import EasywaveDeviceFlowMixin
from .const import (
    BUTTON_A,
    CONF_ENTRY_TYPE,
    CONF_OPERATING_MODE,
    CONF_RECEIVER_KIND,
    CONF_RECEIVER_SERIAL,
    CONF_RX11_INDEX,
    ENTRY_TYPE_RECEIVER,
    RECEIVER_KIND_COVER_2BUTTON,
    RECEIVER_KIND_HEATING_COOLING,
    RECEIVER_KIND_IMPULSE,
    RECEIVER_KIND_MOTOR_3BUTTON,
    RECEIVER_KIND_SWITCH_2BUTTON,
    RECEIVER_KIND_UNIVERSAL_4BUTTON,
    device_id_for_receiver,
    normalize_serial_hex,
)

_KIND_STEPS: dict[str, str] = {
    "receiver_kind_impulse": RECEIVER_KIND_IMPULSE,
    "receiver_kind_switch": RECEIVER_KIND_SWITCH_2BUTTON,
    "receiver_kind_cover": RECEIVER_KIND_COVER_2BUTTON,
    "receiver_kind_motor": RECEIVER_KIND_MOTOR_3BUTTON,
    "receiver_kind_heating": RECEIVER_KIND_HEATING_COOLING,
    "receiver_kind_universal": RECEIVER_KIND_UNIVERSAL_4BUTTON,
}


class EasywaveReceiverSubentryFlowHandler(
    ConfigSubentryFlow, EasywaveDeviceFlowMixin
):
    """Handle adding Easywave EW receivers to the RX11 hub."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize."""
        super().__init__(*args, **kwargs)
        self._init_device_flow()
        self._receiver_kind = RECEIVER_KIND_IMPULSE
        self._rx11_index: int | None = None
        self._receiver_serial: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Entry point for adding an Easywave receiver."""
        coordinator = self._get_coordinator()
        if coordinator is None or not coordinator.transceiver.is_connected:
            return self.async_abort(reason="device_not_connected")
        return await self.async_step_receiver_kind()

    async def async_step_receiver_kind(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Select receiver kind."""
        return self.async_show_menu(
            step_id="receiver_kind",
            menu_options=list(_KIND_STEPS),
        )

    async def _async_set_kind(self, kind: str) -> SubentryFlowResult:
        self._receiver_kind = kind
        return await self.async_step_receiver_allocate()

    async def async_step_receiver_kind_impulse(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Impulse receiver."""
        return await self._async_set_kind(RECEIVER_KIND_IMPULSE)

    async def async_step_receiver_kind_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Switch receiver."""
        return await self._async_set_kind(RECEIVER_KIND_SWITCH_2BUTTON)

    async def async_step_receiver_kind_cover(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Cover receiver."""
        return await self._async_set_kind(RECEIVER_KIND_COVER_2BUTTON)

    async def async_step_receiver_kind_motor(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Motor receiver."""
        return await self._async_set_kind(RECEIVER_KIND_MOTOR_3BUTTON)

    async def async_step_receiver_kind_heating(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Heating/cooling receiver."""
        return await self._async_set_kind(RECEIVER_KIND_HEATING_COOLING)

    async def async_step_receiver_kind_universal(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Universal 4-button receiver."""
        return await self._async_set_kind(RECEIVER_KIND_UNIVERSAL_4BUTTON)

    async def async_step_receiver_allocate(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Allocate RX11 index and pair by sending code A."""
        coordinator = self._get_coordinator()
        if coordinator is None or not coordinator.transceiver.is_connected:
            return self.async_abort(reason="device_not_connected")

        index = coordinator.allocate_rx11_index()
        if index is None:
            return self.async_abort(reason="no_free_index")
        self._rx11_index = index

        serial = await coordinator.transceiver.get_ew_gateway_serial(index)
        if serial is None:
            return self.async_abort(reason="index_serial_unavailable")
        self._receiver_serial = normalize_serial_hex(serial)

        # Put receiver into learn mode by sending button A (Code A)
        await coordinator.async_send_ew_button(index, BUTTON_A)
        return await self.async_step_receiver_confirm()

    async def async_step_receiver_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Confirm and save the receiver."""
        if self._rx11_index is None or self._receiver_serial is None:
            return self.async_abort(reason="no_device_learned")

        unique_id = device_id_for_receiver(self._receiver_serial)
        if self._is_duplicate(
            unique_id,
            entry_type=ENTRY_TYPE_RECEIVER,
            serial_hex=self._receiver_serial,
        ):
            return self.async_abort(reason="already_configured")

        if user_input is not None and "title" in user_input:
            data = {
                CONF_ENTRY_TYPE: ENTRY_TYPE_RECEIVER,
                CONF_RECEIVER_SERIAL: self._receiver_serial,
                CONF_RX11_INDEX: self._rx11_index,
                CONF_RECEIVER_KIND: self._receiver_kind,
                CONF_OPERATING_MODE: "1",
            }
            return await self._async_save_device(
                title=user_input["title"],
                unique_id=unique_id,
                data=data,
            )

        return self.async_show_form(
            step_id="receiver_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "title",
                        default=self._next_default_name(ENTRY_TYPE_RECEIVER),
                    ): str,
                }
            ),
            description_placeholders={
                "index": str(self._rx11_index),
                "kind": self._receiver_kind,
            },
        )
