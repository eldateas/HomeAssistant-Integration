"""Subentry flow for adding Easywave neo actuators."""

import logging
import time
from typing import Any

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
    ewneo_device_type_label,
    normalize_serial_hex,
)
from .devices import get_devices

_LOGGER = logging.getLogger(__name__)

# Short join attempts match HACS 0.6 / firmware RF timeout behaviour.
_EWB_JOIN_ATTEMPT_TIMEOUT = 2.0


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
        """Join an EWneo actuator on a free gateway index.

        Follows the easywave-home-control pairing sequence:
        exclusive IO → EWB_GET_FD_SERIAL → EWB_ADD_NFILTER → EWB_JOIN_DEVICE.
        """
        index = coordinator.allocate_ewneo_index()
        if index is None:
            _LOGGER.error("No free EWB gateway index available for neo actuator learn")
            return None

        # Listener must be stopped first — concurrent EW_RCV_EX blocks short EWB
        # requests and made get_gateway_serial / join fail immediately.
        await coordinator.suspend_telegram_listener()
        try:
            gateway = await coordinator.transceiver.get_ewb_gateway_serial(index)
            if gateway is None:
                _LOGGER.error(
                    "Failed to load EWB gateway serial for index %s", index
                )
                return None

            if not await coordinator.transceiver.ewb_add_gateway_filter(gateway):
                _LOGGER.error(
                    "Failed to add EWB gateway filter for index %s", index
                )
                return None

            _LOGGER.info(
                "EWB join waiting on index %s gateway …%s (timeout %ss)",
                index,
                normalize_serial_hex(gateway)[-8:],
                EWB_LEARNING_TIMEOUT,
            )

            deadline = time.monotonic() + EWB_LEARNING_TIMEOUT
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                joined = await coordinator.transceiver.ewb_join_device(
                    gateway,
                    timeout=min(_EWB_JOIN_ATTEMPT_TIMEOUT, remaining),
                )
                if joined is not None:
                    device_type, receiver = joined
                    _LOGGER.info(
                        "EWB join success: type=0x%02X serial=…%s",
                        int(device_type),
                        normalize_serial_hex(receiver)[-8:],
                    )
                    return {
                        "ewneo_index": index,
                        "gateway_serial": normalize_serial_hex(gateway),
                        "actuator_serial": normalize_serial_hex(receiver),
                        "device_type_code": int(device_type),
                    }

            _LOGGER.warning(
                "EWB join timed out after %ss (index %s)",
                EWB_LEARNING_TIMEOUT,
                index,
            )
            return None
        except (OSError, TimeoutError, ValueError) as err:
            _LOGGER.error("EWB neo actuator learning failed: %s", err)
            return None
        finally:
            coordinator.resume_telegram_listener()

    def _type_label(self, type_code: int) -> str:
        """Return a localized friendly type label for the confirm UI."""
        return ewneo_device_type_label(type_code, self.hass.config.language)

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
            title = str(user_input["title"]).strip() or self._next_default_name(
                ENTRY_TYPE_NEO_ACTUATOR
            )
            data = {
                CONF_ENTRY_TYPE: ENTRY_TYPE_NEO_ACTUATOR,
                CONF_ACTUATOR_SERIAL: serial_hex,
                CONF_EWNEO_INDEX: int(self._learned_device["ewneo_index"]),
                CONF_GATEWAY_SERIAL: str(self._learned_device["gateway_serial"]),
                CONF_DEVICE_TYPE_CODE: type_code,
                CONF_CHANNELS: DEVICE_TYPE_CODE_TO_CHANNELS.get(type_code, 1),
            }
            # Runtime/position capability is detected automatically from EWB state
            # (MotorFullState.runtime_measured / bit 7), not asked in the UI.
            return await self._async_save_device(
                title=title,
                unique_id=unique_id,
                data=data,
                area_id=self._area_id_from_input(user_input),
            )

        type_label = self._type_label(type_code)
        count = sum(
            1
            for device in get_devices(self._get_entry())
            if device.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_NEO_ACTUATOR
        )
        return self.async_show_form(
            step_id="actuator_confirm",
            data_schema=self._confirm_name_area_schema(
                title_default=f"Easywave neo {type_label} {count + 1}",
            ),
            description_placeholders={
                "device_type_name": type_label,
                "channels": str(DEVICE_TYPE_CODE_TO_CHANNELS.get(type_code, 1)),
            },
        )
