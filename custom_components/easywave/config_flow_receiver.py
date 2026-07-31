"""Subentry flow for adding Easywave EW receivers."""

from __future__ import annotations

import logging
from typing import Any

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

_LOGGER = logging.getLogger(__name__)

_KIND_STEPS: dict[str, str] = {
    "receiver_kind_impulse": RECEIVER_KIND_IMPULSE,
    "receiver_kind_switch": RECEIVER_KIND_SWITCH_2BUTTON,
    "receiver_kind_cover": RECEIVER_KIND_COVER_2BUTTON,
    "receiver_kind_motor": RECEIVER_KIND_MOTOR_3BUTTON,
    "receiver_kind_heating": RECEIVER_KIND_HEATING_COOLING,
    "receiver_kind_universal": RECEIVER_KIND_UNIVERSAL_4BUTTON,
}

# Programming-mode labels used in the prepare-step placeholder {operating_mode}.
_PROGRAMMING_MODE: dict[str, dict[str, str]] = {
    "en": {
        RECEIVER_KIND_IMPULSE: "PULSE",
        RECEIVER_KIND_SWITCH_2BUTTON: "ON / OFF",
        RECEIVER_KIND_COVER_2BUTTON: "UP / DOWN",
        RECEIVER_KIND_MOTOR_3BUTTON: "UP / STOP / DOWN",
        RECEIVER_KIND_HEATING_COOLING: "ON / OFF",
        RECEIVER_KIND_UNIVERSAL_4BUTTON: "UNIVERSAL",
    },
    "de": {
        RECEIVER_KIND_IMPULSE: "IMPULS",
        RECEIVER_KIND_SWITCH_2BUTTON: "EIN / AUS",
        RECEIVER_KIND_COVER_2BUTTON: "AUF / ZU",
        RECEIVER_KIND_MOTOR_3BUTTON: "AUF / STOPP / ZU",
        RECEIVER_KIND_HEATING_COOLING: "EIN / AUS",
        RECEIVER_KIND_UNIVERSAL_4BUTTON: "UNIVERSAL",
    },
    "fr": {
        RECEIVER_KIND_IMPULSE: "IMPULSION",
        RECEIVER_KIND_SWITCH_2BUTTON: "ON / OFF",
        RECEIVER_KIND_COVER_2BUTTON: "HAUT / BAS",
        RECEIVER_KIND_MOTOR_3BUTTON: "HAUT / STOP / BAS",
        RECEIVER_KIND_HEATING_COOLING: "ON / OFF",
        RECEIVER_KIND_UNIVERSAL_4BUTTON: "UNIVERSEL",
    },
}

# Type labels used in the confirm-step placeholder {receiver_type}.
_RECEIVER_TYPE_LABEL: dict[str, dict[str, str]] = {
    "en": {
        RECEIVER_KIND_IMPULSE: "Impulse (1-Button)",
        RECEIVER_KIND_SWITCH_2BUTTON: "ON/OFF (2-Button)",
        RECEIVER_KIND_COVER_2BUTTON: "UP/DOWN (2-Button)",
        RECEIVER_KIND_MOTOR_3BUTTON: "UP/STOP/DOWN (3-Button)",
        RECEIVER_KIND_HEATING_COOLING: "Heating",
        RECEIVER_KIND_UNIVERSAL_4BUTTON: "Universal (4-Button)",
    },
    "de": {
        RECEIVER_KIND_IMPULSE: "Impuls (1-Tast)",
        RECEIVER_KIND_SWITCH_2BUTTON: "EIN/AUS (2-Tast)",
        RECEIVER_KIND_COVER_2BUTTON: "AUF/ZU (2-Tast)",
        RECEIVER_KIND_MOTOR_3BUTTON: "AUF/STOPP/ZU (3-Tast)",
        RECEIVER_KIND_HEATING_COOLING: "Heizung",
        RECEIVER_KIND_UNIVERSAL_4BUTTON: "Universal (4-Tast)",
    },
    "fr": {
        RECEIVER_KIND_IMPULSE: "Impulsion (1 touche)",
        RECEIVER_KIND_SWITCH_2BUTTON: "ON/OFF (2 touches)",
        RECEIVER_KIND_COVER_2BUTTON: "HAUT/BAS (2 touches)",
        RECEIVER_KIND_MOTOR_3BUTTON: "HAUT/STOP/BAS (3 touches)",
        RECEIVER_KIND_HEATING_COOLING: "Chauffage",
        RECEIVER_KIND_UNIVERSAL_4BUTTON: "Universel (4 touches)",
    },
}


def _locale(language: str | None) -> str:
    """Map Home Assistant language to a supported receiver-flow locale."""
    lang = (language or "en").lower()
    if lang.startswith("de"):
        return "de"
    if lang.startswith("fr"):
        return "fr"
    return "en"


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
        self._prepare_step = "receiver_prepare"

    def _label(self, table: dict[str, dict[str, str]], kind: str) -> str:
        """Return a localized label for the current receiver kind."""
        locale = _locale(getattr(getattr(self.hass, "config", None), "language", None))
        return table.get(locale, table["en"]).get(kind) or table["en"].get(kind, kind)

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
        """Store the selected kind and allocate an RX11 index."""
        self._receiver_kind = kind
        if kind == RECEIVER_KIND_HEATING_COOLING:
            self._prepare_step = "receiver_prepare_heating"
        elif kind == RECEIVER_KIND_UNIVERSAL_4BUTTON:
            self._prepare_step = "receiver_prepare_universal"
        else:
            self._prepare_step = "receiver_prepare"
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
        """Allocate RX11 index and serial, then show the prepare dialog."""
        coordinator = self._get_coordinator()
        if coordinator is None or not coordinator.transceiver.is_connected:
            return self.async_abort(reason="device_not_connected")

        if self._rx11_index is None or self._receiver_serial is None:
            index = coordinator.allocate_rx11_index()
            if index is None:
                return self.async_abort(reason="no_free_index")
            self._rx11_index = index

            serial = await coordinator.transceiver.get_ew_gateway_serial(index)
            if serial is None:
                return self.async_abort(reason="index_serial_unavailable")
            self._receiver_serial = normalize_serial_hex(serial)

        return await self._async_show_prepare()

    async def _async_show_prepare(self) -> SubentryFlowResult:
        """Show the kind-specific prepare (programming mode) dialog."""
        step = self._prepare_step
        if step == "receiver_prepare_heating":
            return await self.async_step_receiver_prepare_heating()
        if step == "receiver_prepare_universal":
            return await self.async_step_receiver_prepare_universal()
        return await self.async_step_receiver_prepare()

    async def async_step_receiver_prepare(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask the user to put the receiver into programming mode."""
        self._prepare_step = "receiver_prepare"
        return self.async_show_menu(
            step_id="receiver_prepare",
            menu_options=["receiver_send_code", "receiver_kind"],
            description_placeholders={
                "operating_mode": self._label(
                    _PROGRAMMING_MODE, self._receiver_kind
                ),
            },
        )

    async def async_step_receiver_prepare_heating(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask the user to put a heating receiver into programming mode."""
        self._prepare_step = "receiver_prepare_heating"
        return self.async_show_menu(
            step_id="receiver_prepare_heating",
            menu_options=["receiver_send_code", "receiver_kind"],
            description_placeholders={
                "operating_mode": self._label(
                    _PROGRAMMING_MODE, RECEIVER_KIND_HEATING_COOLING
                ),
            },
        )

    async def async_step_receiver_prepare_universal(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask the user to put a universal receiver into programming mode."""
        self._prepare_step = "receiver_prepare_universal"
        return self.async_show_menu(
            step_id="receiver_prepare_universal",
            menu_options=["receiver_send_code", "receiver_kind"],
        )

    async def async_step_receiver_send_code(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Send Code A to the receiver, then ask for LED acknowledgement."""
        coordinator = self._get_coordinator()
        if (
            coordinator is None
            or not coordinator.transceiver.is_connected
            or self._rx11_index is None
        ):
            return self.async_abort(reason="device_not_connected")

        success = await coordinator.async_send_ew_button(self._rx11_index, BUTTON_A)
        if not success:
            _LOGGER.warning(
                "Failed to send learn Code A on RX11 index %s", self._rx11_index
            )
            return self.async_abort(reason="code_send_failed")

        return await self.async_step_receiver_confirm_learning()

    async def async_step_receiver_confirm_learning(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask whether the receiver LED acknowledged learning."""
        return self.async_show_menu(
            step_id="receiver_confirm_learning",
            menu_options=["receiver_confirm", "receiver_retry_prepare"],
        )

    async def async_step_receiver_retry_prepare(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Return to the prepare dialog after a failed learn acknowledgement."""
        return await self._async_show_prepare()

    async def async_step_receiver_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Confirm learning and save the receiver under a user-chosen name."""
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
                area_id=self._area_id_from_input(user_input),
            )

        return self.async_show_form(
            step_id="receiver_confirm",
            data_schema=self._confirm_name_area_schema(
                title_default=self._next_default_name(ENTRY_TYPE_RECEIVER),
            ),
            description_placeholders={
                "receiver_type": self._label(
                    _RECEIVER_TYPE_LABEL, self._receiver_kind
                ),
            },
        )
