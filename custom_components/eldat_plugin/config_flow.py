"""Modern config flow for ELDAT integration with modular transceiver support."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult, AbortFlow
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_DEVICE_PATH,
    CONF_DEVICE_NAME,
    CONF_TRANSCEIVER_TYPE,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_DEVICE_NAME,
    LEARNING_TIMEOUT,
    EVENT_DEVICE_ADDED,
)
from .transceivers import TransceiverFactory, TransceiverType
from .transceivers.rx11 import find_rx11_devices, validate_rx11_device
from .helpers import run_learning, battery_percentage_from_level
from .entity_specs import create_entity_specs_for_device

_LOGGER = logging.getLogger(__name__)

# Learning timeout in seconds (30 seconds for all learning operations)
LEARNING_TIMEOUT_SECONDS = 30


class ModernEldatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Modern config flow for ELDAT with transceiver modularity."""

    VERSION = 2
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self):
        """Initialize flow."""
        _LOGGER.info("ModernEldatConfigFlow.__init__ called")
        self._transceiver_type: TransceiverType | None = None
        self._discovered_devices: list[dict[str, Any]] = []
        self._device_path: str | None = None
        # For device flow
        self._device_type: str | None = None
        self._learned_device: dict[str, Any] | None = None
        self._device_config: dict[str, Any] = {}  # Initialize device config
        self._learn_task = None
        self._learn_cancel_event = None
        self._telegram_listener_remove = None
        self._ewneo_learn_task = None
        self._ewneo_poll_task = None
        self._transmitter_learn_task = None
    
    async def _cleanup_learning_mode(self, coordinator=None) -> None:
        """Clean up learning mode and tasks."""
        if self._learn_cancel_event:
            self._learn_cancel_event.set()
        
        # Cancel all learning tasks
        for task_attr in [
            '_learn_task',
            '_learning_task',
            '_sensor_learning_task',
            '_ewneo_learn_task',
            '_ewneo_poll_task',
            '_transmitter_learn_task',
        ]:
            task = getattr(self, task_attr, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                setattr(self, task_attr, None)
        
        if coordinator:
            # Use modern coordinator interface
            if hasattr(coordinator, 'transceiver'):
                await coordinator.transceiver.rx11_system_set_learning_mode(False)
            coordinator._config_flow_learning = False
        
        self._learn_cancel_event = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - automatically detect transceiver type."""
        # If integration already configured, go to device adding flow
        existing_entries = self._async_current_entries()
        if existing_entries:
            _LOGGER.info("ELDAT Integration bereits konfiguriert - leite zu Geräte-Flow weiter")
            return await self.async_step_device()

        # Automatically detect and setup transceiver
        return await self.async_step_auto_detection(user_input)

    async def async_step_auto_detection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Automatically detect transceiver type from connected devices."""
        _LOGGER.info("Starte automatische Transceiver-Erkennung...")
        
        try:
            # Check for RX11 devices first
            rx11_devices = await self.hass.async_add_executor_job(find_rx11_devices)
            
            if rx11_devices:
                _LOGGER.info("RX11 Geräte erkannt: %s", len(rx11_devices))
                self._transceiver_type = TransceiverType.RX11
                self._discovered_devices = rx11_devices
                return await self.async_step_rx11_setup()
            
            # Future: Check for other transceiver types
            # rx21_devices = await self.hass.async_add_executor_job(find_rx21_devices)
            # if rx21_devices:
            #     self._transceiver_type = TransceiverType.RX21
            #     return await self.async_step_rx21_setup()
            
            # No supported transceivers found
            _LOGGER.warning("Keine unterstützten ELDAT Transceiver gefunden")
            return self.async_abort(
                reason="no_devices",
                description_placeholders={
                    "details": "Keine RX11 USB-Transceiver gefunden. Stellen Sie sicher, dass ein ELDAT RX11 Gerät angeschlossen und erkannt wird."
                }
            )
        
        except Exception as e:
            _LOGGER.error("Fehler bei automatischer Transceiver-Erkennung: %s", e)
            return self.async_abort(
                reason="detection_failed",
                description_placeholders={"error": str(e)}
            )

    async def async_step_rx11_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Setup RX11 transceiver."""
        errors: dict[str, str] = {}
        
        # Refresh devices if requested
        if user_input and user_input.get(CONF_DEVICE_PATH) == "refresh":
            _LOGGER.info("Aktualisiere RX11 Geräteliste...")
            try:
                self._discovered_devices = await self.hass.async_add_executor_job(find_rx11_devices)
                _LOGGER.info("Gefunden: %d RX11-Geräte", len(self._discovered_devices))
            except Exception as e:
                _LOGGER.warning("Fehler bei der RX11-Gerätesuche: %s", e)
                self._discovered_devices = []
            # Show form again after refresh
            user_input = None

        if user_input is not None:
            device_path = user_input.get(CONF_DEVICE_PATH)
            device_name = user_input.get(CONF_DEVICE_NAME, "ELDAT RX11 USB-Transceiver")

            if device_path and device_path != "refresh":
                # Skip connection test - let the actual setup validate the connection
                await self.async_set_unique_id(device_path)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=device_name,
                    data={
                        CONF_TRANSCEIVER_TYPE: TransceiverType.RX11.value,
                        CONF_DEVICE_PATH: device_path,
                        CONF_DEVICE_NAME: device_name,
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    },
                )

        # Build device options - only detected devices and refresh
        device_options = {}
        for device in self._discovered_devices:
            name = f"{device['name']} ({device['device']})"
            device_options[device["device"]] = name

        device_options["refresh"] = "🔄 Erneut suchen"

        # Show error if no devices found
        if not self._discovered_devices:
            errors["base"] = "no_rx11_found"

        data_schema = vol.Schema({
            vol.Required(CONF_DEVICE_PATH): vol.In(device_options),
            vol.Optional(CONF_DEVICE_NAME, default="ELDAT RX11 USB-Transceiver"): str,
        })

        description_placeholders = {
            "found_devices": len(self._discovered_devices),
            "example_path": "/dev/ttyUSB0 or COM3",
        }

        return self.async_show_form(
            step_id="rx11_setup",
            data_schema=data_schema,
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual device path input."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_path = user_input.get(CONF_DEVICE_PATH)
            device_name = user_input.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)

            if device_path:
                # Skip connection test - let the actual setup validate the connection
                # This avoids unnecessary connect/disconnect cycles
                if not self._transceiver_type:
                    errors["base"] = "no_transceiver_selected"

                if not errors:
                    await self.async_set_unique_id(device_path)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=device_name,
                        data={
                            CONF_TRANSCEIVER_TYPE: self._transceiver_type.value if self._transceiver_type else "unknown",
                            CONF_DEVICE_PATH: device_path,
                            CONF_DEVICE_NAME: device_name,
                            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                        },
                    )

        data_schema = vol.Schema({
            vol.Required(CONF_DEVICE_PATH): str,
            vol.Optional(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
        })

        return self.async_show_form(
            step_id="manual",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "example_path": "/dev/ttyUSB0 or COM3",
                "transceiver_type": self._transceiver_type.value if self._transceiver_type else "unknown",
            }
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle adding a new device to an existing integration.

        This step checks that a coordinator exists and then redirects
        to the device type selection flow used for learning specific devices.
        """
        # Ensure an entry exists
        entries = [entry for entry in self._async_current_entries() if entry.domain == DOMAIN]
        if not entries:
            return self.async_abort(reason="no_transceiver_configured")

        # Ensure coordinator is available
        coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id)
        if not coordinator:
            return self.async_abort(reason="transceiver_not_available")

        # Redirect to device type selection
        return await self.async_step_device_type_select(user_input)

    async def async_step_device_type_select(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Select device type to add - shown as a menu."""
        # Show a menu with device type options
        return self.async_show_menu(
            step_id="device_type_select",
            menu_options=["device_transmitter_config", "device_receiver", "device_sensor", "device_ewneo_receiver"],
        )
    
    async def async_step_device_cancel(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Cancel the device adding flow - return to main menu instead of aborting."""
        # Reset device config
        self._device_config = {}
        # Go back to device type selection
        return await self.async_step_device_type_select()

    async def async_step_device_transmitter_config(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure transmitter operating mode - Step 1: Select button operation type via menu."""
        # Initialize device config for transmitter
        self._device_config = {
            "device_type": "ew_transmitter",
        }
        
        # Show menu with operating type options
        return self.async_show_menu(
            step_id="device_transmitter_config",
            menu_options=["device_transmitter_1button", "device_transmitter_2button", "device_transmitter_3button", "device_type_select"],
        )

    async def async_step_device_transmitter_1button(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle 1-button operating type selection."""
        self._device_config["operating_type"] = "1"
        return await self.async_step_device_transmitter_grouping()

    async def async_step_device_transmitter_2button(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle 2-button operating type selection."""
        self._device_config["operating_type"] = "2"
        return await self.async_step_device_transmitter_2button_usage()

    async def async_step_device_transmitter_3button(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle 3-button operating type selection (cover mode)."""
        self._device_config["operating_type"] = "3"
        self._device_config["grouping_mode"] = "cover"
        self._device_config["switch_mode"] = "cover"
        self._device_config["button_count"] = 4
        self._device_config["channels"] = 4
        self._device_config["cover_mode"] = True
        return await self.async_step_device_transmitter_description()

    async def async_step_device_transmitter_grouping(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure transmitter grouping mode for 1-button operation via menu."""
        return self.async_show_menu(
            step_id="device_transmitter_grouping",
            menu_options=["device_transmitter_grouping_single", "device_transmitter_grouping_group", "device_transmitter_config"],
        )

    async def async_step_device_transmitter_grouping_single(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle single grouping mode selection."""
        self._device_config["grouping_mode"] = "single"
        return await self.async_step_device_transmitter_switch_mode()

    async def async_step_device_transmitter_grouping_group(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle group grouping mode selection."""
        self._device_config["grouping_mode"] = "group"
        return await self.async_step_device_transmitter_switch_mode()

    async def async_step_device_transmitter_switch_mode(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure transmitter switch mode via menu - Impulse or Permanent."""
        return self.async_show_menu(
            step_id="device_transmitter_switch_mode",
            menu_options=["device_transmitter_switch_impulse", "device_transmitter_switch_permanent", "device_transmitter_grouping"],
        )

    async def async_step_device_transmitter_switch_impulse(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle impulse switch mode selection."""
        self._device_config["switch_mode"] = "impulse"
        return await self.async_step_device_transmitter_button_count()

    async def async_step_device_transmitter_switch_permanent(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle permanent switch mode selection."""
        self._device_config["switch_mode"] = "permanent"
        return await self.async_step_device_transmitter_button_count()

    async def async_step_device_transmitter_button_count(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure transmitter button count via menu."""
        return self.async_show_menu(
            step_id="device_transmitter_button_count",
            menu_options=["device_transmitter_buttons_1", "device_transmitter_buttons_2", "device_transmitter_buttons_3", "device_transmitter_buttons_4", "device_transmitter_switch_mode"],
        )

    async def async_step_device_transmitter_buttons_1(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle 1 button selection."""
        self._device_config["button_count"] = 1
        self._device_config["channels"] = 1
        return await self.async_step_device_transmitter_description()

    async def async_step_device_transmitter_buttons_2(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle 2 buttons selection."""
        self._device_config["button_count"] = 2
        self._device_config["channels"] = 2
        return await self.async_step_device_transmitter_description()

    async def async_step_device_transmitter_buttons_3(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle 3 buttons selection."""
        self._device_config["button_count"] = 3
        self._device_config["channels"] = 3
        return await self.async_step_device_transmitter_description()

    async def async_step_device_transmitter_buttons_4(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle 4 buttons selection."""
        self._device_config["button_count"] = 4
        self._device_config["channels"] = 4
        return await self.async_step_device_transmitter_description()

    async def async_step_device_transmitter_2button_usage(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure 2-button transmitter usage via menu - EIN/AUS or AUF/ZU."""
        return self.async_show_menu(
            step_id="device_transmitter_2button_usage",
            menu_options=["device_transmitter_2button_switch", "device_transmitter_2button_cover", "device_transmitter_config"],
        )

    async def async_step_device_transmitter_2button_switch(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle 2-button switch usage selection."""
        self._device_config["usage_type"] = "switch"
        return await self.async_step_device_transmitter_2button_button_count()

    async def async_step_device_transmitter_2button_cover(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle 2-button cover usage selection."""
        self._device_config["usage_type"] = "cover"
        return await self.async_step_device_transmitter_2button_button_count()

    async def async_step_device_transmitter_2button_button_count(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure 2-button transmitter button count via menu - 2 or 4 buttons."""
        usage_type = self._device_config.get("usage_type", "switch")
        
        return self.async_show_menu(
            step_id="device_transmitter_2button_button_count",
            menu_options=["device_transmitter_2button_2", "device_transmitter_2button_4", "device_transmitter_2button_usage", "device_cancel"],
            description_placeholders={
                "usage_type": usage_type,
            }
        )

    async def async_step_device_transmitter_2button_2(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle 2 buttons selection for 2-button mode."""
        usage_type = self._device_config.get("usage_type", "switch")
        self._device_config["button_count"] = 2
        self._device_config["channels"] = 2
        self._device_config["grouping_mode"] = "single"
        self._device_config["switch_mode"] = usage_type
        return await self.async_step_device_transmitter_description()

    async def async_step_device_transmitter_2button_4(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle 4 buttons selection for 2-button mode."""
        usage_type = self._device_config.get("usage_type", "switch")
        self._device_config["button_count"] = 4
        self._device_config["channels"] = 4
        self._device_config["grouping_mode"] = "dual"
        self._device_config["switch_mode"] = usage_type
        return await self.async_step_device_transmitter_description()


    async def async_step_device_transmitter_description(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show learning menu for EW-Sender with start/cancel options."""
        button_count = self._device_config.get("button_count", 4)
        operating_type = self._device_config.get("operating_type", "1")
        grouping_mode = self._device_config.get("grouping_mode", "single")
        switch_mode = self._device_config.get("switch_mode", "impulse")
        usage_type = self._device_config.get("usage_type", "switch")
        
        # Get coordinator to retrieve next sender index
        entries = [entry for entry in self._async_current_entries() if entry.domain == DOMAIN]
        coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None
        
        sender_index = "?"
        if coordinator:
            sender_index = str(coordinator.get_next_ew_sender_index())
        
        # Build readable settings descriptions
        # Betriebsart
        if operating_type == "1":
            operating_mode = "1-Tast-Bedienung"
        elif operating_type == "2":
            # Bei 2-Tast den Usage-Typ mit anzeigen
            if usage_type == "cover":
                operating_mode = "2-Tast-Bedienung (AUF/ZU)"
            else:
                operating_mode = "2-Tast-Bedienung (EIN/AUS)"
        else:  # operating_type == "3"
            operating_mode = "3-Tast-Bedienung (AUF/STOPP/ZU)"
        
        # Bedienung (nur bei 1-Tast) - als komplette Zeile
        grouping_mode_line = ""
        if operating_type == "1":
            if grouping_mode == "single":
                grouping_text = "Einzeln schalten"
            else:
                grouping_text = "Als Gruppe schalten"
            grouping_mode_line = f"\n• **Bedienung:** {grouping_text}"
        
        # Verhalten (nur bei 1-Tast) - als komplette Zeile
        behavior_line = ""
        if operating_type == "1":
            if switch_mode == "impulse":
                behavior_text = "Impuls"
            else:
                behavior_text = "Dauer"
            behavior_line = f"\n• **Verhalten:** {behavior_text}"
        
        # Tastenanzahl
        button_count_text = f"{button_count} {'Taste' if button_count == 1 else 'Tasten'}"
        
        # Store description placeholders for use in strings.json
        placeholders = {
            "button_count": str(button_count),
            "operating_type": operating_type,
            "sender_index": sender_index,
            "operating_mode": operating_mode,
            "grouping_mode_line": grouping_mode_line,
            "behavior_line": behavior_line,
            "button_count_text": button_count_text,
        }
        
        return self.async_show_menu(
            step_id="device_transmitter_description",
            menu_options=["device_transmitter_learn_start", "device_transmitter_config"],
            description_placeholders=placeholders
        )

    async def async_step_device_transmitter_learn_start(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Start transmitter learning - intermediate step to allow transition to progress."""
        return await self.async_step_device_transmitter_learn_progress()

    async def async_step_device_transmitter_learn_progress(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show progress indicator during transmitter learning."""
        entries = [entry for entry in self._async_current_entries() if entry.domain == DOMAIN]
        coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None

        if not coordinator:
            return self.async_abort(reason="transceiver_not_available")

        # Initialize learning task if not started
        if self._transmitter_learn_task is None:
            self._transmitter_learn_task = self.hass.async_create_task(
                self._do_transmitter_learning_with_timeout(coordinator)
            )

        # If task finished, route to next step
        if self._transmitter_learn_task.done():
            result = "error"
            try:
                result = self._transmitter_learn_task.result()
            except asyncio.CancelledError:
                result = "cancelled"
            except Exception as e:
                _LOGGER.error("Learning task error: %s", e)
                result = "error"
            finally:
                self._transmitter_learn_task = None

            if result == "success":
                return self.async_show_progress_done(next_step_id="device_transmitter_verify")
            if result == "timeout":
                return self.async_show_progress_done(next_step_id="device_transmitter_learn_timeout")
            if result == "already_exists":
                return self.async_show_progress_done(next_step_id="device_transmitter_already_exists")
            if result == "cancelled":
                return self.async_show_progress_done(next_step_id="device_transmitter_description")
            return self.async_abort(reason="unknown")

        return self.async_show_progress(
            step_id="device_transmitter_learn_progress",
            progress_action="waiting_for_transmitter_telegram",
            progress_task=self._transmitter_learn_task,
        )

    async def async_step_device_transmitter_verify(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Verify and configure the learned transmitter with name and optional area."""
        if not self._learned_device:
            return self.async_abort(reason="no_learned_device")
        
        if user_input is not None:
            # Save device name and optional area
            device_name = user_input.get("device_name")
            if device_name:
                self._learned_device["name"] = device_name
            
            # Store area_id if provided (will be used during device creation)
            area_id = user_input.get("area_id")
            if area_id:
                self._learned_device["area_id"] = area_id
            
            # Proceed to device creation
            return await self.async_step_device_transmitter_complete()
        
        # Get coordinator to retrieve next sender index
        entries = [entry for entry in self._async_current_entries() if entry.domain == DOMAIN]
        coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None
        
        sender_index = "?"
        if coordinator:
            sender_index = str(coordinator.get_next_ew_sender_index())
        
        suggested_name = f"EW-Sender #{sender_index}"
        
        # Get available areas for selection
        from homeassistant.helpers import area_registry as ar
        area_reg = ar.async_get(self.hass)
        areas = {area.id: area.name for area in area_reg.async_list_areas()}
        
        # Build data schema with name and optional area
        data_schema = vol.Schema({
            vol.Required("device_name", default=suggested_name): str,
        })
        
        # Add area selector if areas are available
        if areas:
            from homeassistant.helpers.selector import AreaSelector
            data_schema = data_schema.extend({
                vol.Optional("area_id"): AreaSelector(),
            })
        
        return self.async_show_form(
            step_id="device_transmitter_verify",
            data_schema=data_schema,
        )

    async def async_step_device_transmitter_complete(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Complete transmitter setup and create device."""
        if not self._learned_device:
            return self.async_abort(reason="no_learned_device")
        
        _LOGGER.info("✅ Transmitter confirmed: %s", self._learned_device.get("name"))
        return await self.async_step_device_save()

    async def _do_transmitter_learning_with_timeout(self, coordinator) -> str:
        """Run transmitter learning with an enforced timeout for progress UI."""
        try:
            # Add a small buffer to ensure the task completes and the UI advances
            return await asyncio.wait_for(
                self._do_transmitter_learning(coordinator),
                timeout=LEARNING_TIMEOUT_SECONDS + 2,
            )
        except asyncio.TimeoutError:
            return "timeout"



    async def _do_transmitter_learning(self, coordinator) -> str:
        """Perform transmitter learning in background task."""
        _LOGGER.info("=== TRANSMITTER LEARNING START ===")
        
        try:
            def _match_transmitter(dev: dict) -> Optional[dict]:
                is_transmitter = (
                    dev.get("type") == "ew_transmitter"
                    or dev.get("device_type") == "transmitter"
                    or dev.get("info_type") == 1
                )
                if is_transmitter and not dev.get("added_manually", False):
                    return dev
                return None

            coordinator.start_setup_mode(timeout_seconds=LEARNING_TIMEOUT_SECONDS)
            
            learned = await run_learning(coordinator, _match_transmitter, timeout=LEARNING_TIMEOUT_SECONDS)

            if learned:
                _LOGGER.info("Transmitter Telegramm empfangen: %s", learned)
                received_serial = learned.get("serial_number", learned.get("serial", "?"))
                
                # Get next sender index for name
                sender_index = coordinator.get_next_ew_sender_index()

                if received_serial in coordinator.devices:
                    existing_device = coordinator.devices[received_serial]
                    existing_device_name = existing_device.get("name", f"EW-Sender {received_serial[-6:]}")
                    self._learned_device = {
                        "name": existing_device_name,
                        "serial_number": received_serial,
                        "type": "ew_transmitter",
                        "device_type": "ew_transmitter",
                    }
                    coordinator.stop_setup_mode()
                    return "already_exists"
                
                # Get configuration from previous steps
                operating_type = self._device_config.get("operating_type", "1")
                grouping_mode = self._device_config.get("grouping_mode", "single")
                switch_mode = self._device_config.get("switch_mode", "impulse")
                usage_type = self._device_config.get("usage_type", "switch")
                cover_mode = self._device_config.get("cover_mode", False)
                
                
                
                self._learned_device = {
                    "name": f"EW-Sender #{sender_index}",
                    "serial_number": received_serial,
                    "type": "ew_transmitter",
                    "device_type": "ew_transmitter",
                    "button_count": self._device_config.get("button_count", 4),
                    "channels": self._device_config.get("channels", 4),
                    "operating_type": operating_type,
                    "grouping_mode": grouping_mode,
                    "switch_mode": switch_mode,
                    "usage_type": usage_type,
                    "cover_mode": cover_mode,
                    "last_telegram": {
                        "info_type": learned.get("info_type"),
                        "button": learned.get("button"),
                        "function": learned.get("function"),
                        "raw_data": learned.get("raw_data"),
                        "timestamp": learned.get("timestamp"),
                    },
                }
                coordinator.stop_setup_mode()
                return "success"
                
            coordinator.stop_setup_mode()
            return "timeout"
            
        except asyncio.CancelledError:
            coordinator.stop_setup_mode()
            raise
        except Exception as e:
            _LOGGER.error("Error in transmitter learning: %s", e, exc_info=True)
            coordinator.stop_setup_mode()
            return "error"

    async def async_step_device_transmitter_learn_timeout(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show timeout menu with retry/cancel options."""
        return self.async_show_menu(
            step_id="device_transmitter_learn_timeout",
            menu_options=["device_transmitter_learn_start", "device_transmitter_description"],
        )

    async def async_step_device_transmitter_already_exists(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show info that device already exists."""
        serial_number = "?"
        device_name = "Unbekannt"
        if self._learned_device:
            serial_number = self._learned_device.get("serial_number", "?")
            device_name = self._learned_device.get("name", "Unbekannt")

        return self.async_show_menu(
            step_id="device_transmitter_already_exists",
            menu_options=["device_transmitter_learn_start", "device_transmitter_description"],
            description_placeholders={
                "device_name": device_name,
            },
        )

    async def async_step_device_sensor(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Start EW-Sensor learning - go to description step."""
        return await self.async_step_device_sensor_description()

    async def async_step_device_sensor_description(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show learning menu for EWneo-Sensor with start/cancel options."""
        return self.async_show_menu(
            step_id="device_sensor_description",
            menu_options=["device_sensor_learn_start", "device_type_select"],
        )

    async def async_step_device_sensor_learn_start(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Start sensor learning - intermediate step to allow transition to progress."""
        return await self.async_step_device_sensor_learn_progress()

    async def async_step_device_sensor_learn_progress(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show progress indicator during sensor learning with cancel option."""
        entries = [entry for entry in self._async_current_entries() if entry.domain == DOMAIN]
        coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None

        if not coordinator:
            return self.async_abort(reason="transceiver_not_available")

        # Initialize learning task if not started
        if not hasattr(self, '_sensor_learn_task') or self._sensor_learn_task is None:
            self._sensor_learn_task = self.hass.async_create_task(
                self._do_sensor_learning(coordinator)
            )
            # Show progress - this will wait for the task to complete
            return self.async_show_progress(
                step_id="device_sensor_learn_progress",
                progress_action="waiting_for_sensor_telegram",
                    progress_task=self._sensor_learn_task,
            )
        
        # Check if learning task is done (called when progress is done)
        if self._sensor_learn_task.done():
            result = "error"
            try:
                result = self._sensor_learn_task.result()
            except asyncio.CancelledError:
                result = "cancelled"
            except Exception as e:
                _LOGGER.error("Sensor learning task error: %s", e)
                result = "error"
            finally:
                self._sensor_learn_task = None
            
            if result == "success":
                return self.async_show_progress_done(next_step_id="device_sensor_verify")
            elif result == "already_exists":
                return self.async_show_progress_done(next_step_id="device_sensor_already_exists")
            elif result == "timeout":
                return self.async_show_progress_done(next_step_id="device_sensor_learn_timeout")
            elif result == "cancelled":
                return self.async_show_progress_done(next_step_id="device_sensor_description")
            else:
                return self.async_abort(reason="unknown")
        
        # Still waiting - show progress
        return self.async_show_progress(
            step_id="device_sensor_learn_progress",
            progress_action="waiting_for_sensor_telegram",
            progress_task=self._sensor_learn_task,
        )


    async def _do_sensor_learning(self, coordinator) -> str:
        """Perform sensor learning in background task."""
        _LOGGER.info("=== SENSOR LEARNING START ===")
        
        try:
            def _match_sensor(dev: dict) -> Optional[dict]:
                is_sensor = (
                    dev.get("type") == "ew_sensor"
                    or dev.get("type") == "ewneo_sensor"
                    or dev.get("device_type") == "sensor"
                    or dev.get("device_type") == "ewneo_sensor"
                    or dev.get("info_type") == 2
                )
                is_learn = dev.get("is_learn_telegram", False)
                
                # Only accept LEARN telegrams, not regular sensor data telegrams
                if is_sensor and is_learn and not dev.get("added_manually", False):
                    _LOGGER.info("✅ EWneo-Sensor Learn-Telegramm erkannt: %s", dev.get("serial_number", "?")[-6:])
                    return dev
                elif is_sensor and not is_learn:
                    _LOGGER.debug("⏭️ Messwert-Telegramm ignoriert (kein Lerntelegramm): %s", dev.get("serial_number", "?")[-6:])
                return None

            coordinator.start_setup_mode(timeout_seconds=LEARNING_TIMEOUT_SECONDS)
            
            learned = await run_learning(coordinator, _match_sensor, timeout=LEARNING_TIMEOUT_SECONDS)

            if learned:
                _LOGGER.info("Sensor Telegramm empfangen: %s", learned)
                
                available_sensors = learned.get("available_sensors", [])
                measurement_types = learned.get("measurement_types", [])
                sensor_capabilities = learned.get("sensor_capabilities", [])
                
                detected_sensors = set()
                if sensor_capabilities:
                    detected_sensors.update(sensor_capabilities)
                elif available_sensors:
                    detected_sensors.update(available_sensors)
                elif measurement_types:
                    detected_sensors.update(measurement_types)
                
                received_serial = learned.get("serial_number", learned.get("serial", "?"))
                
                sensor_types = [s for s in detected_sensors if s != "battery"]
                
                next_index = getattr(coordinator, "_next_ewneo_sensor_index", 1)
                display_name = f"EWneo-Sensor #{next_index}"

                # Check if sensor already exists (similar to transmitter)
                if received_serial in coordinator.devices:
                    existing_device = coordinator.devices[received_serial]
                    existing_device_name = existing_device.get("name", f"EWneo-Sensor {received_serial[-6:]}")
                    self._learned_device = {
                        "name": existing_device_name,
                        "serial_number": received_serial,
                        "type": "ewneo_sensor",
                        "device_type": "ewneo_sensor",
                    }
                    coordinator.stop_setup_mode()
                    return "already_exists"

                self._learned_device = {
                    "name": display_name,
                    "serial_number": received_serial,
                    "type": "ewneo_sensor",
                    "device_type": "ewneo_sensor",
                    "is_learn_telegram": True,
                    "available_sensors": list(detected_sensors),
                    "measurement_types": measurement_types,
                    "sensor_capabilities": sensor_capabilities,
                    "sensor_types": sensor_types,
                    "has_battery": True,
                    "battery_level": learned.get("battery_level", 100),
                    "last_telegram": {
                        "info_type": learned.get("info_type"),
                        "raw_data": learned.get("raw_data"),
                        "timestamp": learned.get("timestamp"),
                    },
                }
                
                coordinator.stop_setup_mode()
                return "success"
            
            coordinator.stop_setup_mode()
            return "timeout"
            
        except asyncio.CancelledError:
            coordinator.stop_setup_mode()
            raise
        except Exception as e:
            _LOGGER.error("Error in sensor learning: %s", e, exc_info=True)
            coordinator.stop_setup_mode()
            return "error"

    async def async_step_device_sensor_learn_timeout(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show timeout menu with retry/cancel options for sensor learning."""
        return self.async_show_menu(
            step_id="device_sensor_learn_timeout",
            menu_options=["device_sensor_learn_start", "device_sensor_description"],
        )

    async def async_step_device_sensor_already_exists(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show already exists menu for sensor - similar to transmitter."""
        if not self._learned_device:
            return await self.async_step_device_sensor_description()
        
        device_name = self._learned_device.get("name", "Unknown Device")
        
        return self.async_show_menu(
            step_id="device_sensor_already_exists",
            menu_options=["device_sensor_learn_start"],
            description_placeholders={
                "device_name": device_name,
            }
        )

    async def async_step_device_sensor_verify(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Verify and configure the learned sensor with name and optional area."""
        if not self._learned_device:
            return self.async_abort(reason="no_learned_device")
        
        if user_input is not None:
            # Save device name and optional area
            device_name = user_input.get("device_name")
            if device_name:
                self._learned_device["name"] = device_name
            
            # Store area_id if provided (will be used during device creation)
            area_id = user_input.get("area_id")
            if area_id:
                self._learned_device["area_id"] = area_id
            
            # Proceed to device creation
            return await self.async_step_device_sensor_complete()
        
        # Get coordinator to retrieve next sensor index
        entries = [entry for entry in self._async_current_entries() if entry.domain == DOMAIN]
        coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None
        
        sensor_index = "?"
        if coordinator:
            sensor_index = str(getattr(coordinator, "_next_ewneo_sensor_index", 1))
        
        suggested_name = f"EWneo-Sensor #{sensor_index}"
        
        # Get sensor information from learned device
        sensor_types = self._learned_device.get("sensor_types", [])
        available_sensors = self._learned_device.get("available_sensors", [])
        serial_number = self._learned_device.get("serial_number", "?")
        
        # German translations for sensor types
        SENSOR_TRANSLATIONS_DE = {
            "temperature": "Temperatur",
            "humidity": "Luftfeuchtigkeit",
            "air_pressure": "Luftdruck",
            "brightness": "Helligkeit",
            "wind_speed": "Windgeschwindigkeit",
            "rain": "Regen",
            "battery": "Batterie",
        }
        
        # Build sensor list for description with German translations
        sensors_to_show = sensor_types or available_sensors
        translated_sensors = []
        for s in sensors_to_show:
            if s in SENSOR_TRANSLATIONS_DE:
                translated_sensors.append(f"  • {SENSOR_TRANSLATIONS_DE[s]}")
            else:
                translated_sensors.append(f"  • {s.replace('_', ' ').title()}")
        
        sensor_list = "\n".join(translated_sensors) if translated_sensors else "  • Auto-Erkennung bei Empfang"
        
        # Get available areas for selection
        from homeassistant.helpers import area_registry as ar
        area_reg = ar.async_get(self.hass)
        areas = {area.id: area.name for area in area_reg.async_list_areas()}
        
        # Build data schema with name and optional area
        data_schema = vol.Schema({
            vol.Required("device_name", default=suggested_name): str,
        })
        
        # Add area selector if areas are available
        if areas:
            from homeassistant.helpers.selector import AreaSelector
            data_schema = data_schema.extend({
                vol.Optional("area_id"): AreaSelector(),
            })
        
        return self.async_show_form(
            step_id="device_sensor_verify",
            data_schema=data_schema,
            description_placeholders={
                "sensor_list": sensor_list,
                "suggested_name": suggested_name,
                "serial_short": serial_number[-8:] if serial_number != "?" else "?",
            }
        )

    async def async_step_device_sensor_complete(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Complete sensor setup and create device."""
        if not self._learned_device:
            return self.async_abort(reason="no_learned_device")
        
        _LOGGER.info("✅ Sensor confirmed: %s", self._learned_device.get("name"))
        return await self.async_step_device_save()

    async def async_step_device_receiver(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure EW receiver device - Load next available receiver from RX11."""
        # Get next available receiver from RX11 central management
        try:
            entries = self.hass.config_entries.async_entries(DOMAIN)
            coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None
            if coordinator and coordinator.transceiver:
                _LOGGER.info("🔍 Getting next available EW-Receiver from RX11...")
                
                # Get next available receiver index from persistent tracking
                try:
                    index = await coordinator.get_next_free_ew_receiver_index()
                    _LOGGER.info("✅ Got next free EW-Receiver index from persistent tracking: %d", index)
                except ValueError:
                    return self.async_abort(reason="no_available_receivers")
                
                # Get serial for this index from RX11 via EW_GET_FD_SERIAL
                # The RX11 has pre-configured gateway serials for each index
                serial = await coordinator.transceiver.rx11_ew_receiver_get_serial_by_index(index)
                
                if not serial:
                    _LOGGER.error("❌ Failed to get serial for EW-Receiver index %d from RX11", index)
                    return self.async_abort(reason="no_receiver_serial")
                
                self._device_config = {
                    "device_type": "ew_receiver",
                    "type": "ew_receiver",
                    "serial_number": serial,
                    "rx11_index": index,
                    "name": f"EW-Empfänger #{index + 1}"
                }
                _LOGGER.info("✅ Using EW-Receiver: Index %d, Serial %s", index, serial[-8:])
                return await self.async_step_device_receiver_type()
            else:
                return self.async_abort(reason="no_coordinator")
        except Exception as e:
            _LOGGER.error("Error getting next available EW-Receiver: %s", e)
            return self.async_abort(reason="receiver_load_failed")

    async def async_step_device_receiver_type(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure EW receiver device - Step 1: Select operating mode via menu."""
        serial = self._device_config.get("serial_number", "Unknown")
        rx11_index = self._device_config.get("rx11_index", 0)
        
        return self.async_show_menu(
            step_id="device_receiver_type",
            menu_options=["device_receiver_mode_impulse", "device_receiver_mode_on_off", "device_receiver_mode_up_down", "device_receiver_mode_up_stop_down", "device_receiver_mode_heating", "device_receiver_mode_universal", "device_type_select"],
            description_placeholders={
                "device_name": f"EW-Empfänger #{rx11_index + 1}",
                "serial": serial[-12:] if serial else "Unknown",
            }
        )

    async def async_step_device_receiver_mode_impulse(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle Impuls (1-Tast) mode selection - creates toggle button."""
        rx11_index = self._device_config.get("rx11_index", 0)
        self._device_config["receiver_kind"] = "impulse"
        self._device_config["operating_mode"] = 1
        self._device_config["name"] = f"EW-Empfänger #{rx11_index + 1}"
        return await self.async_step_device_receiver_description()

    async def async_step_device_receiver_mode_on_off(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle EIN/AUS (2-Tast) mode selection - creates stateless switch entity."""
        rx11_index = self._device_config.get("rx11_index", 0)
        self._device_config["receiver_kind"] = "switch_2button"
        self._device_config["operating_mode"] = 2
        self._device_config["name"] = f"EW-Empfänger #{rx11_index + 1}"
        return await self.async_step_device_receiver_description()

    async def async_step_device_receiver_mode_up_down(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle AUF/ZU (2-Tast) mode selection - creates stateless cover entity (no stop)."""
        rx11_index = self._device_config.get("rx11_index", 0)
        self._device_config["receiver_kind"] = "cover_2button"
        self._device_config["operating_mode"] = 2
        self._device_config["name"] = f"EW-Empfänger #{rx11_index + 1}"
        return await self.async_step_device_receiver_description()

    async def async_step_device_receiver_mode_up_stop_down(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle AUF/STOPP/ZU (3-Tast) mode selection - creates stateless motor entity."""
        rx11_index = self._device_config.get("rx11_index", 0)
        self._device_config["receiver_kind"] = "motor_3button"
        self._device_config["operating_mode"] = 3
        self._device_config["name"] = f"EW-Empfänger #{rx11_index + 1}"
        return await self.async_step_device_receiver_description()

    async def async_step_device_receiver_mode_heating(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle EIN/AUS (Heizung) mode selection - creates heating switch with 4h repeat."""
        rx11_index = self._device_config.get("rx11_index", 0)
        self._device_config["receiver_kind"] = "heating_cooling"
        self._device_config["operating_mode"] = 1
        self._device_config["name"] = f"EW-Empfänger #{rx11_index + 1}"
        return await self.async_step_device_receiver_description()

    async def async_step_device_receiver_mode_universal(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle UNIVERSAL (4-Tast) mode selection - creates 4 individual buttons."""
        rx11_index = self._device_config.get("rx11_index", 0)
        self._device_config["receiver_kind"] = "universal_4button"
        self._device_config["operating_mode"] = 4
        self._device_config["name"] = f"EW-Empfänger #{rx11_index + 1}"
        return await self.async_step_device_receiver_description()
    
    async def async_step_device_receiver_description(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show receiver learning overview with start/back options."""
        receiver_kind = self._device_config.get("receiver_kind", "switch")
        
        # Map receiver_kind to readable operating mode description
        mode_descriptions = {
            "impulse": "Impuls (1-Tast)",
            "switch_2button": "EIN / AUS (2-Tast)",
            "cover_2button": "AUF / ZU (2-Tast)",
            "motor_3button": "AUF / STOPP / ZU (3-Tast)",
            "heating_cooling": "EIN / AUS (Heizung)",
            "universal_4button": "UNIVERSAL (4-Tast)",
        }
        
        operating_mode = mode_descriptions.get(receiver_kind, receiver_kind)
        
        placeholders = {
            "operating_mode": operating_mode,
        }
        
        return self.async_show_menu(
            step_id="device_receiver_description",
            menu_options=["device_receiver_learn_start", "device_receiver_type"],
            description_placeholders=placeholders
        )
    
    async def async_step_device_receiver_learn_start(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Send Code A to receiver and proceed to confirmation."""
        try:
            entries = self.hass.config_entries.async_entries(DOMAIN)
            coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None
            serial = self._device_config.get("serial_number")
            rx11_index = self._device_config.get("rx11_index")
            
            if coordinator and coordinator.transceiver and serial and rx11_index is not None:
                # Send Code A to receiver
                try:
                    success = await coordinator.transceiver.send_command_to_receiver(serial, bytes([0]))
                    if success:
                        _LOGGER.info("✅ Code A sent successfully to receiver %s", serial[-8:])
                    else:
                        _LOGGER.warning("⚠️ Code A sending failed")
                        return self.async_abort(reason="code_send_failed")
                except Exception as e:
                    _LOGGER.error("❌ Code A sending error: %s", e)
                    return self.async_abort(reason="code_send_error")
            
            # Proceed to learning confirmation
            return await self.async_step_device_receiver_confirm_learning()
                
        except Exception as e:
            _LOGGER.error("Error in receiver learn start: %s", e)
            return self.async_abort(reason="learning_error")
    
    async def async_step_device_receiver_confirm_learning(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Ask user to confirm that LED acknowledged learning."""
        return self.async_show_menu(
            step_id="device_receiver_confirm_learning",
            menu_options=["device_receiver_verify", "device_receiver_description"],
        )
    
    async def async_step_device_receiver_verify(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Final step: Enter device name and area."""
        if user_input is not None:
            # User provided name and area
            device_name = user_input.get("device_name", self._device_config.get("name"))
            area_id = user_input.get("area_id")
            
            self._device_config["name"] = device_name
            if area_id:
                self._device_config["area_id"] = area_id
            
            # Mark receiver as used and create device
            entries = self.hass.config_entries.async_entries(DOMAIN)
            coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None
            serial = self._device_config.get("serial_number")
            rx11_index = self._device_config.get("rx11_index")
            
            if coordinator and serial and rx11_index is not None:
                coordinator.mark_ew_receiver_index_used(rx11_index, serial, serial, device_name)
                _LOGGER.info("🔒 Marked receiver as used: Index %d", rx11_index)
            
            # Create the device
            self._device_config.update({
                "device_type": "ew_receiver",
                "type": "ew_receiver",
                "entity_type": "button"
            })
            _LOGGER.info("✅ Creating EW-Receiver device: %s", device_name)
            return await self.async_step_device_save()
        
        # Show form to enter name and area
        rx11_index = self._device_config.get("rx11_index", 0)
        default_name = f"EW-Empfänger #{rx11_index + 1}"
        
        return self.async_show_form(
            step_id="device_receiver_verify",
            data_schema=vol.Schema({
                vol.Required("device_name", default=default_name): str,
                vol.Optional("area_id"): selector.AreaSelector(),
            }),
        )
        
    # Note: Operating mode selection is now combined into async_step_device_receiver_type
    # Each mode directly sets receiver_kind and operating_mode
        
    async def async_step_device_receiver_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure EW receiver device - Step 3: Confirmation menu."""
        confirm_data = self._get_confirm_form_data()
        
        return self.async_show_menu(
            step_id="device_receiver_confirm",
            menu_options=["device_receiver_create", "device_receiver_type"],
            description_placeholders=confirm_data["description_placeholders"],
        )

    async def async_step_device_receiver_create(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Create the EW-Receiver device and send Code A."""
        try:
            entries = self.hass.config_entries.async_entries(DOMAIN)
            coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None
            serial = self._device_config.get("serial_number")
            rx11_index = self._device_config.get("rx11_index")
            
            if coordinator and coordinator.transceiver and serial and rx11_index is not None:
                # Try to send Code A to real hardware
                try:
                    success = await coordinator.transceiver.send_command_to_receiver(serial, bytes([0]))
                    if success:
                        _LOGGER.info("✅ Code A sent successfully to receiver %s", serial[-8:])
                    else:
                        _LOGGER.warning("⚠️ Code A sending failed, but continuing with device creation")
                except Exception as e:
                    _LOGGER.warning("⚠️ Code A sending error (continuing): %s", e)
                
                # Mark receiver as used persistently in coordinator
                device_name = self._device_config.get("name", f"EW-Empfänger #{rx11_index + 1}")
                coordinator.mark_ew_receiver_index_used(rx11_index, serial, serial, device_name)
                _LOGGER.info("🔒 Marked receiver as used persistently: Index %d", rx11_index)
            
            # Always create the device
            self._device_config.update({
                "device_type": "ew_receiver",
                "type": "ew_receiver",
                "entity_type": "button"
            })
            _LOGGER.info("✅ Creating EW-Receiver device with serial: %s", serial[-8:] if serial else "Unknown")
            return await self.async_step_device_save()
                
        except Exception as e:
            _LOGGER.error("Error in device creation: %s", e)
            return self.async_abort(reason="device_creation_error")
        
    async def async_step_device_receiver_manual(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manual EW receiver configuration as fallback."""
        if user_input is not None:
            rx11_index = user_input.get("rx11_index", 0)
            self._device_config = {
                "serial_number": user_input.get("serial_number"),
                "name": user_input.get("name", f"EW-Receiver (Index {rx11_index})"),  # Temporär, wird mit receiver_kind aktualisiert
                "rx11_index": rx11_index
            }
            return await self.async_step_device_receiver_type()
            
        return self.async_show_form(
            step_id="device_receiver_manual",
            data_schema=vol.Schema({
                vol.Required("serial_number"): str,
                vol.Optional("name"): str,
                vol.Optional("rx11_index"): int
            }),
            description_placeholders={
                "instruction": "Enter the EW-Receiver serial number manually."
            }
        )
        
        
    def _get_confirm_form_data(self) -> dict:
        """Get form data for confirmation dialog."""
        receiver_kind = self._device_config.get("receiver_kind", "switch")
        operating_mode = self._device_config.get("operating_mode", 1)
        serial = self._device_config.get("serial_number", "Unknown")
        name = self._device_config.get("name", "EW-Receiver")
        rx11_index = self._device_config.get("rx11_index", "Unknown")
        
        # Generate operating mode description based on new receiver_kind values
        if receiver_kind == "impulse":
            mode_desc = "Impuls (1-Tast)"
            button_summary = "• Toggle-Button (zustandslos)"
            learn_instructions = "Empfänger in 1-Tast Lernmodus versetzen."
        elif receiver_kind == "switch_2button":
            mode_desc = "EIN / AUS (2-Tast)"
            button_summary = "• Zustandsloser Schalter (A→Ein, B→Aus)"
            learn_instructions = "Empfänger in 2-Tast Lernmodus versetzen (Ein + Aus)."
        elif receiver_kind == "cover_2button":
            mode_desc = "AUF / ZU (2-Tast)"
            button_summary = "• Zustandslose Abdeckung (A→Auf, B→Zu)"
            learn_instructions = "Empfänger in 2-Tast Lernmodus versetzen (Auf + Zu)."
        elif receiver_kind == "motor_3button":
            mode_desc = "AUF / STOPP / ZU (3-Tast)"
            button_summary = "• Zustandsloser Motor (A→Auf, B→Zu, C→Stopp)"
            learn_instructions = "Empfänger in 3-Tast Lernmodus versetzen (Auf + Zu + Stopp)."
        elif receiver_kind == "heating_cooling":
            mode_desc = "EIN / AUS (Heizung)"
            button_summary = "• Heizungsschalter (Toggle mit 4h Wiederholung)"
            learn_instructions = "Empfänger in Toggle-Lernmodus versetzen."
        elif receiver_kind == "universal_4button":
            mode_desc = "UNIVERSAL (4-Tast)"
            button_summary = "• Button A\n• Button B\n• Button C\n• Button D"
            learn_instructions = "Empfänger in 4-Tast Lernmodus versetzen."
        else:
            # Legacy fallback for old configurations
            if operating_mode == 1:
                mode_desc = "1-Tast (Toggle)"
                button_summary = "• Toggle Button"
            elif operating_mode == 2:
                mode_desc = "2-Tast"
                button_summary = "• Button A\n• Button B"
            elif operating_mode == 3:
                mode_desc = "3-Tast"
                button_summary = "• Button A\n• Button B\n• Button C"
            else:
                mode_desc = f"Modus {operating_mode}"
                button_summary = "• Standardkonfiguration"
            learn_instructions = f"Empfänger in {operating_mode}-Tast Lernmodus versetzen."
        
        return {
            "data_schema": vol.Schema({}),
            "description_placeholders": {
                "device_name": name,
                "serial": serial[-12:] if serial else "Unknown",
                "rx11_index": str(rx11_index),
                "receiver_kind": receiver_kind.replace("_", "/").title(),
                "operating_mode": mode_desc,
                "button_summary": button_summary,
                "learn_instructions": learn_instructions,
            }
        }

    def _get_receiver_operating_mode_placeholders(self) -> dict[str, str]:
        """Build description placeholders for the receiver operating mode step."""
        receiver_kind = self._device_config.get("receiver_kind", "switch")

        if receiver_kind == "switch":
            receiver_kind_label = "Switch"
            mode_descriptions = "• 🔘 1-Button (Toggle)\n• 🔘🔘 2-Button (On + Off)"
        elif receiver_kind == "motor":
            receiver_kind_label = "Motor/Cover"
            mode_descriptions = (
                "• 🔘 1-Button (Toggle)\n"
                "• 🔘🔘 2-Button (Up + Down)\n"
                "• 🔘🔘🔘 3-Button (Up + Down + Stop)"
            )
        elif receiver_kind == "heating_cooling":
            receiver_kind_label = "Heating/Cooling"
            mode_descriptions = "• 🔘 1-Button (Toggle, 4h auto-repeat)"
        else:
            receiver_kind_label = receiver_kind.replace("_", "/").title()
            mode_descriptions = "• 🔘 1-Button\n• 🔘🔘 2-Button"

        return {
            "receiver_kind": receiver_kind_label,
            "mode_descriptions": mode_descriptions,
        }

    async def async_step_device_ewneo_receiver(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure EWneo receiver device - start with preparation."""
        # Clean up any leftover preparation data
        if hasattr(self, '_ewneo_preparation'):
            delattr(self, '_ewneo_preparation')
        return await self.async_step_device_ewneo_receiver_prepare()

    async def async_step_device_ewneo_receiver_back(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Go back from EWneo receiver description to main menu."""
        if hasattr(self, '_ewneo_preparation'):
            delattr(self, '_ewneo_preparation')
        return await self.async_step_device_type_select(user_input)

    async def async_step_device_ewneo_receiver_prepare(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 1: Prepare EWneo receiver learning (get index, gateway serial, set filter)."""
        try:
            # Get coordinator
            entries = [entry for entry in self._async_current_entries() if entry.domain == DOMAIN]
            coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None
            
            if not coordinator:
                return self.async_abort(reason="no_coordinator")
            
            # Check RX11 connection
            if not hasattr(coordinator.transceiver, '_rx11_wrapper') or not coordinator.transceiver._rx11_wrapper:
                return self.async_abort(reason="no_rx11_connection")
                
            wrapper = coordinator.transceiver._rx11_wrapper
            if not wrapper.is_connected():
                return self.async_abort(reason="rx11_not_connected")

            _LOGGER.info("=== EWNEO RECEIVER PREPARATION START ===")

            # Step 1: Get next available EWneo index from coordinator's persistent tracking
            _LOGGER.info("🔍 Getting next available EWneo index...")
            try:
                ewneo_index = await coordinator.get_next_free_ewb_index()
            except ValueError:
                # All 256 indices are used
                return self.async_abort(reason="no_available_ewneo_index")
                
            _LOGGER.info("✅ Using EWneo index: %d", ewneo_index)

            # Step 2: Get gateway serial number for this index
            _LOGGER.info("📡 Loading gateway serial for index %d...", ewneo_index)
            gateway_serial = await coordinator.transceiver.rx11_ewb_get_serial_by_index(ewneo_index)
            
            if not gateway_serial or gateway_serial == "00" * 32:
                return self.async_abort(reason="no_gateway_serial")
                
            _LOGGER.info("✅ Gateway serial loaded: %s", gateway_serial[-8:])

            # Step 3: Add gateway to receive filter
            _LOGGER.info("🔧 Adding gateway %s to receive filter...", gateway_serial[-8:])
            filter_success = await coordinator.transceiver.rx11_ewb_add_filter(gateway_serial)
            
            if not filter_success:
                _LOGGER.warning("⚠️ Failed to add gateway to filter, but continuing...")

            # Store preparation data for the learning step
            self._ewneo_preparation = {
                "coordinator": coordinator,
                "wrapper": wrapper, 
                "ewneo_index": ewneo_index,
                "gateway_serial": gateway_serial
            }
            
            _LOGGER.info("✅ EWneo preparation completed - Index: %d, Gateway: %s", 
                        ewneo_index, gateway_serial[-8:])
            
            # Proceed to description step before learning
            return await self.async_step_device_ewneo_receiver_description()

        except Exception as e:
            _LOGGER.error("Error during EWneo preparation: %s", e)
            return self.async_abort(reason="ewneo_learning_failed")

    async def async_step_device_ewneo_receiver_description(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show EWneo receiver description with index and gateway serial before learning."""
        prep = getattr(self, "_ewneo_preparation", None) or {}
        ewneo_index = prep.get("ewneo_index")
        gateway_serial = prep.get("gateway_serial")

        return self.async_show_menu(
            step_id="device_ewneo_receiver_description",
            description_placeholders={
                "ewneo_index": str(ewneo_index) if ewneo_index is not None else "-",
                "gateway_serial": gateway_serial or "-",
            },
            menu_options=["device_ewneo_receiver_learn_start", "device_ewneo_receiver_back"],
        )

    async def async_step_device_ewneo_receiver_learn_start(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Start learning - immediately start the task and show progress."""
        _LOGGER.warning("🔵 async_step_device_ewneo_receiver_learn_start CALLED! user_input=%s", user_input)
        
        # Check if preparation data exists
        if not hasattr(self, '_ewneo_preparation') or not self._ewneo_preparation:
            _LOGGER.error("❌ No preparation data!")
            return self.async_abort(reason="no_preparation_data")
        
        coordinator = self._ewneo_preparation.get("coordinator")
        ewneo_index = self._ewneo_preparation.get("ewneo_index")
        gateway_serial = self._ewneo_preparation.get("gateway_serial")
        
        _LOGGER.warning("🔵 Preparation data: index=%s, gateway=%s", ewneo_index, gateway_serial[-8:] if gateway_serial else None)
        
        if not coordinator or ewneo_index is None or not gateway_serial:
            _LOGGER.error("❌ Missing coordinator or index or gateway!")
            return self.async_abort(reason="no_preparation_data")
        
        # Start the learning task immediately
        _LOGGER.warning("🚀 Starting EWneo learning task from learn_start...")
        self._ewneo_learn_task = self.hass.async_create_task(
            self._do_ewneo_receiver_learning_with_timeout(coordinator, ewneo_index, gateway_serial)
        )
        
        _LOGGER.warning("🔵 Task created, showing progress...")
        
        # Show progress immediately
        return self.async_show_progress(
            step_id="device_ewneo_receiver_learn_progress",
            progress_action="waiting_for_ewneo_join",
            progress_task=self._ewneo_learn_task,
        )

    async def async_step_device_ewneo_receiver_learn_progress(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 2: Perform the EWneo receiver learning with progress indicator."""
        _LOGGER.warning("🟣 async_step_device_ewneo_receiver_learn_progress CALLED!")
        
        # Check if preparation data exists
        if not hasattr(self, '_ewneo_preparation') or not self._ewneo_preparation:
            _LOGGER.warning("🟣 No preparation data - aborting")
            return self.async_abort(reason="no_preparation_data")
        
        try:
            coordinator = self._ewneo_preparation.get("coordinator")
            ewneo_index = self._ewneo_preparation.get("ewneo_index")
            gateway_serial = self._ewneo_preparation.get("gateway_serial")
            
            if not coordinator or ewneo_index is None or not gateway_serial:
                _LOGGER.warning("🟣 Missing data - aborting")
                return self.async_abort(reason="no_preparation_data")

            _LOGGER.warning("🟣 Task exists: %s, Task done: %s", 
                           self._ewneo_learn_task is not None,
                           self._ewneo_learn_task.done() if self._ewneo_learn_task else "N/A")

            # Initialize learning task if not started
            if self._ewneo_learn_task is None:
                _LOGGER.warning("🟣 Task is None - starting new task...")
                self._ewneo_learn_task = self.hass.async_create_task(
                    self._do_ewneo_receiver_learning_with_timeout(coordinator, ewneo_index, gateway_serial)
                )

            # If task finished, route to next step
            if self._ewneo_learn_task.done():
                _LOGGER.warning("🟣 Task is DONE - getting result...")
                result = "error"
                try:
                    result = self._ewneo_learn_task.result()
                    _LOGGER.warning("🟣 Task result: %s", result)
                except asyncio.CancelledError:
                    _LOGGER.warning("🟣 Task was cancelled")
                    result = "cancelled"
                except Exception as e:
                    _LOGGER.error("🟣 Task error: %s", e, exc_info=True)
                    result = "error"
                finally:
                    self._ewneo_learn_task = None
                    self._ewneo_poll_task = None

                if result == "success":
                    _LOGGER.warning("🟣 SUCCESS - going to device_confirm")
                    return self.async_show_progress_done(next_step_id="device_confirm")
                if result == "timeout":
                    _LOGGER.warning("🟣 TIMEOUT - going to timeout menu")
                    return self.async_show_progress_done(next_step_id="device_ewneo_receiver_learn_timeout")
                if result == "cancelled":
                    _LOGGER.warning("🟣 CANCELLED - going back")
                    return self.async_show_progress_done(next_step_id="device_ewneo_receiver_description")
                _LOGGER.warning("🟣 ERROR - aborting")
                return self.async_abort(reason="ewneo_learning_failed")

            # Show progress
            _LOGGER.warning("🟣 Task NOT done - showing progress...")
            return self.async_show_progress(
                step_id="device_ewneo_receiver_learn_progress",
                progress_action="waiting_for_ewneo_join",
                progress_task=self._ewneo_learn_task,
            )

        except Exception as e:
            _LOGGER.error("Error during EWneo receiver learning: %s", e, exc_info=True)
            if hasattr(self, '_ewneo_preparation'):
                delattr(self, '_ewneo_preparation')
            return self.async_abort(reason="ewneo_learning_failed")

    async def _do_ewneo_receiver_learning_with_timeout(self, coordinator, ewneo_index: int, gateway_serial: str) -> str:
        """Run EWneo receiver learning with an enforced timeout for progress UI."""
        _LOGGER.warning("🟠 _do_ewneo_receiver_learning_with_timeout ENTERED!")
        try:
            _LOGGER.warning("🟠 Calling _do_ewneo_receiver_learning via wait_for...")
            # Add a small buffer to ensure the task completes and the UI advances
            result = await asyncio.wait_for(
                self._do_ewneo_receiver_learning(coordinator, ewneo_index, gateway_serial),
                timeout=LEARNING_TIMEOUT_SECONDS + 2,
            )
            _LOGGER.warning("🟠 _do_ewneo_receiver_learning returned: %s", result)
            return result
        except asyncio.TimeoutError:
            _LOGGER.warning("🟠 asyncio.TimeoutError in wait_for wrapper!")
            return "timeout"
        except Exception as e:
            _LOGGER.error("🟠 Exception in _with_timeout: %s", e, exc_info=True)
            raise

    async def async_step_device_ewneo_receiver_learn_wait(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show wait menu while EWneo receiver learning is running."""
        # Check if task is done and redirect
        if self._ewneo_learn_task is not None and self._ewneo_learn_task.done():
            return await self.async_step_device_ewneo_receiver_learn()

        # If user clicked an option, handle it
        if user_input is not None:
            # User chose to continue waiting, go back to progress
            return await self.async_step_device_ewneo_receiver_learn()

        return self.async_show_menu(
            step_id="device_ewneo_receiver_learn_wait",
            menu_options=["device_ewneo_receiver_learn_wait", "device_ewneo_receiver_learn_cancel"],
        )

    async def async_step_device_ewneo_receiver_learn_cancel(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Cancel EWneo receiver learning and go back."""
        if self._ewneo_learn_task:
            self._ewneo_learn_task.cancel()
            try:
                await self._ewneo_learn_task
            except asyncio.CancelledError:
                pass
            self._ewneo_learn_task = None
        self._ewneo_poll_task = None
        if hasattr(self, '_ewneo_preparation'):
            delattr(self, '_ewneo_preparation')
        await self._cleanup_learning_mode()
        return await self.async_step_device_ewneo_receiver()

    async def async_step_device_ewneo_receiver_learn_timeout(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show timeout menu with retry/cancel options for EWneo receiver learning."""
        # Reset the learning task so it can be restarted
        self._ewneo_learn_task = None
        
        return self.async_show_menu(
            step_id="device_ewneo_receiver_learn_timeout",
            menu_options=["device_ewneo_receiver_learn_start", "device_ewneo_receiver_back"],
        )

    async def _do_ewneo_receiver_learning(self, coordinator, ewneo_index: int, gateway_serial: str) -> str:
        """Run EWneo receiver join loop in a background task."""
        _LOGGER.warning("🟢 _do_ewneo_receiver_learning ENTERED!")
        _LOGGER.warning("🟢 coordinator=%s, ewneo_index=%s, gateway_serial=%s", 
                       type(coordinator).__name__, ewneo_index, gateway_serial[-8:] if gateway_serial else None)
        _LOGGER.info("=== EWNEO TRANSCEIVER LEARNING START ===")
        _LOGGER.info("🔗 Starting EWneo receiver join loop (max 30 seconds)...")

        try:
            _LOGGER.warning("🟢 Inside try block, starting loop...")
            start_time = time.time()
            join_result = None
            attempt = 0
            join_timeout = 2.0  # 2 seconds per join attempt
            
            while (time.time() - start_time) < LEARNING_TIMEOUT_SECONDS:
                attempt += 1
                remaining = int(LEARNING_TIMEOUT_SECONDS - (time.time() - start_time))
                
                if remaining <= 0:
                    break
                    
                _LOGGER.info("🔄 EWB_JOIN_DEVICE attempt %d (remaining: %ds)...", attempt, remaining)
                
                try:
                    # Call join with 2s timeout per attempt
                    join_result = await coordinator.transceiver.rx11_ewb_join_device(
                        gateway_serial, 
                        timeout=min(join_timeout, remaining)
                    )
                    
                    if join_result:
                        _LOGGER.info("✅ EWB_JOIN_DEVICE SUCCESS on attempt %d!", attempt)
                        break
                    
                    _LOGGER.debug("⏳ Attempt %d: No device joined (timeout), retrying...", attempt)
                    
                except Exception as e:
                    _LOGGER.warning("⏳ Join attempt %d failed: %s, retrying...", attempt, e)
                    await asyncio.sleep(0.5)  # Brief pause before retry on error

            if not join_result:
                _LOGGER.warning("❌ EWneo learning timeout after %d attempts (30 seconds)", attempt)
                # DON'T delete _ewneo_preparation here - let the timeout menu handle cleanup
                # Recycle index on timeout
                coordinator.mark_ewb_index_free(ewneo_index)
                return "timeout"

            device_type_code, receiver_serial = join_result
            _LOGGER.info("✅ EWneo receiver joined - Type: 0x%02X, Serial: %s", 
                        device_type_code, receiver_serial[-8:])

            from .const import DEVICE_TYPES
            device_type_name = DEVICE_TYPES.get(device_type_code, f"unknown_0x{device_type_code:02X}")
            
            # German-friendly device names for UI
            EWNEO_DEVICE_NAMES = {
                0x03: "Schalter",           # ewneo_switch - 1 channel
                0x04: "Dimmer",             # ewneo_dimmer
                0x05: "Motor",              # ewneo_motor - 1 channel
                0x06: "2Kanal-Schalter",    # ewneo_dual_switch
                0x07: "4Kanal-Schalter",    # ewneo_quad_switch
                0x08: "2Kanal-Motor",       # ewneo_dual_motor
                0x09: "4Kanal-Motor",       # ewneo_quad_motor
            }
            friendly_type_name = EWNEO_DEVICE_NAMES.get(device_type_code, "Gerät")

            self._learned_device = {
                "device_type": "ewneo_receiver",
                "serial_number": receiver_serial,
                "ewneo_index": ewneo_index,
                "gateway_serial": gateway_serial,
                "device_type_code": device_type_code,
                "device_type_name": device_type_name,
                "bidirectional": True,
                "neo_device": True,
                "channels": 1,
                "supports_feedback": True,
                "is_learn_telegram": True,
            }

            _LOGGER.info("🎯 EWneo-Receiver joined successfully (Index: %d, Type: 0x%02X)",
                        ewneo_index, device_type_code)

            device_name = f"EWneo-{friendly_type_name} #{ewneo_index + 1}"
            self._learned_device["name"] = device_name
            coordinator.mark_ewb_index_used(ewneo_index, gateway_serial, receiver_serial, device_name)

            # DON'T delete _ewneo_preparation here - it's needed by the progress callback
            # It will be cleaned up in device_confirm or on error

            return "success"
            
        except asyncio.CancelledError:
            # Recycle index on cancel
            if hasattr(self, '_ewneo_preparation'):
                coordinator.mark_ewb_index_free(ewneo_index)
                delattr(self, '_ewneo_preparation')
            return "cancelled"
        except Exception as e:
            _LOGGER.error("Error during EWneo receiver learning: %s", e, exc_info=True)
            # Recycle index on error
            if hasattr(self, '_ewneo_preparation'):
                coordinator.mark_ewb_index_free(ewneo_index)
                delattr(self, '_ewneo_preparation')
            return "error"

    async def async_step_device_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Unified confirmation step for all learned devices."""
        if not self._learned_device:
            return self.async_abort(reason="no_learned_device")
        
        device_type = self._learned_device.get("device_type", self._learned_device.get("type", "unknown"))
        serial_number = self._learned_device.get("serial_number", "?")
        
        # Generate description based on device type
        if device_type == "ew_transmitter":
            button_count = self._learned_device.get("button_count", 4)
            
            # Get coordinator to retrieve next sender index for correct name
            entries = [entry for entry in self._async_current_entries() if entry.domain == DOMAIN]
            coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None
            
            sender_index = "?"
            if coordinator:
                sender_index = str(coordinator.get_next_ew_sender_index())
            
            suggested_name = f"EW-Sender #{sender_index}"
            last_telegram = self._learned_device.get("last_telegram", {})
            button = last_telegram.get("button", "?")
            
            description = (
                f"✅ **EW-Sender erfolgreich erkannt!**\n\n"
                f"**🔍 Detected device info:**\n"
                f"• Serial: `{serial_number[-8:]}`\n"
                f"• Name: {suggested_name}\n"
                f"• Button count: {button_count}\n"
                f"• Button pressed: {button}\n\n"
                f"**⚡ What will be created:**\n"
                f"• 1 EW-Sender device\n"
                f"• {button_count} button entities\n\n"
                f"**⚙️ Gerät anlegen:**\n"
                f"Wählen Sie 'Anlegen' um das Gerät zu erstellen."
            )
        elif device_type in ["ew_sensor", "ewneo_sensor"]:
            sensor_types = self._learned_device.get("sensor_types", [])
            available_sensors = self._learned_device.get("available_sensors", [])
            suggested_name = self._learned_device.get("name", "EWneo-Sensor #?")
            
            sensor_list = "\n".join([f"• {s.replace('_', ' ').title()}" for s in (sensor_types or available_sensors)])
            if not sensor_list:
                sensor_list = "• Auto-detection on reception"
            
            # Use translation placeholders for EWneo-Sensor description
            description_placeholders = {
                "serial_short": serial_number[-8:],
                "suggested_name": suggested_name,
                "sensor_list": sensor_list,
            }
            
            # Description will be fetched from strings.json via device_confirm_ewneo_sensor step
            description = ""
        elif device_type == "ewneo_receiver":
            device_type_code = self._learned_device.get("device_type_code", 0)
            device_type_name = self._learned_device.get("device_type_name", "unknown")
            ewneo_index = self._learned_device.get("ewneo_index", "?")
            
            # Determine entity type for display
            if device_type_code == 0x04:
                entity_type_display = "Dimmer (Light)"
            elif device_type_code in [0x05, 0x08, 0x09]:
                entity_type_display = "Motor (Cover)"
            elif device_type_code in [0x03, 0x06, 0x07]:
                entity_type_display = "Switch"
            else:
                entity_type_display = "Switch"
            
            # Use stored name or generate fallback with index
            EWNEO_DEVICE_NAMES = {
                0x03: "Schalter",
                0x04: "Dimmer",
                0x05: "Motor",
                0x06: "2Kanal-Schalter",
                0x07: "4Kanal-Schalter",
                0x08: "2Kanal-Motor",
                0x09: "4Kanal-Motor",
            }
            friendly_type_name = EWNEO_DEVICE_NAMES.get(device_type_code, "Gerät")
            suggested_name = self._learned_device.get("name", f"EWneo-{friendly_type_name} #{ewneo_index + 1}")
            
            description = (
                f"✅ **EWneo-Transceiver successfully learned!**\n\n"
                f"**🔍 Detected device info:**\n"
                f"• Serial: `{serial_number[-8:]}`\n"
                f"• Name: {suggested_name}\n"
                f"• EWneo-Index: `{ewneo_index}`\n"
                f"• Device type: `{device_type_name}` (Code: 0x{device_type_code:02X})\n"
                f"• Entity type: `{entity_type_display}`\n\n"
                f"**⚡ What will be created:**\n"
                f"• 1 EWneo-Transceiver device\n"
                f"• {entity_type_display} entity with status feedback\n\n"
                f"**⚙️ Gerät anlegen:**\n"
                f"Wählen Sie 'Anlegen' um das Gerät zu erstellen."
            )
        else:
            suggested_name = self._learned_device.get("name", f"ELDAT Device ({serial_number[-6:]})")
            description = (
                f"✅ **Device successfully detected!**\n\n"
                f"**🔍 Detected device info:**\n"
                f"• Serial: `{serial_number[-8:]}`\n"
                f"• Name: {suggested_name}\n"
                f"• Type: {device_type}\n\n"
                f"**⚙️ Gerät anlegen:**\n"
                f"Wählen Sie 'Anlegen' um das Gerät zu erstellen."
            )

        self._learned_device["name"] = suggested_name

        # Prepare description_placeholders based on device type
        if device_type in ["ew_sensor", "ewneo_sensor"]:
            final_description_placeholders = description_placeholders
        else:
            final_description_placeholders = {"description": description}

        # Use specialized step for EWneo-Sensor to enable translations
        step_id = "device_confirm_ewneo_sensor" if device_type in ["ew_sensor", "ewneo_sensor"] else "device_confirm"

        return self.async_show_menu(
            step_id=step_id,
            menu_options={
                "device_confirm_create": "✅ Anlegen",
                "device_confirm_rename": "✏️ Namen ändern",
                "device_confirm_back": "⬅️ Zurück",
            },
            description_placeholders=final_description_placeholders
        )

    async def async_step_device_confirm_create(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Create the learned device from the confirmation menu."""
        if not self._learned_device:
            return self.async_abort(reason="no_learned_device")

        device_type = self._learned_device.get("device_type", self._learned_device.get("type", "unknown"))
        serial_number = self._learned_device.get("serial_number", "?")

        if device_type == "ewneo_receiver":
            device_type_code = self._learned_device.get("device_type_code")
            if device_type_code == 0x04:  # EWB_DT_DIMMER
                entity_type = "light"
                supports_dimming = True
                supports_color = False
            elif device_type_code in [0x05, 0x08, 0x09]:  # EWB_DT_MOTOR
                entity_type = "cover"
                supports_dimming = False
                supports_color = False
            elif device_type_code in [0x03, 0x06, 0x07]:  # EWB_DT_SWITCH
                entity_type = "switch"
                supports_dimming = False
                supports_color = False
            else:
                entity_type = "switch"
                supports_dimming = False
                supports_color = False

            self._learned_device.update({
                "entity_type": entity_type,
                "supports_dimming": supports_dimming,
                "supports_color": supports_color,
            })

        _LOGGER.info("✅ Device confirmed: %s (%s)", self._learned_device.get("name"), serial_number[-8:])
        return await self.async_step_device_save()

    async def async_step_device_confirm_back(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Return from device confirmation menu."""
        if not self._learned_device:
            return await self.async_step_device_type_select()

        device_type = self._learned_device.get("device_type", self._learned_device.get("type", "unknown"))
        self._learned_device = None
        if device_type == "ew_transmitter":
            return await self.async_step_device_transmitter_description()
        if device_type in ["ew_sensor", "ewneo_sensor"]:
            return await self.async_step_device_sensor_description()
        if device_type == "ewneo_receiver":
            return await self.async_step_device_ewneo_receiver()
        return await self.async_step_device_type_select()

    async def async_step_device_confirm_ewneo_sensor(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """EWneo Sensor specific confirmation step - redirects to device_confirm for menu handling."""
        # This step is only used for displaying the translated description
        # The menu options point to the shared handlers
        return await self.async_step_device_confirm(user_input)

    async def async_step_device_confirm_rename(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Rename the learned device before creation."""
        if not self._learned_device:
            return self.async_abort(reason="no_learned_device")

        if user_input is not None:
            device_name = user_input.get("device_name")
            if device_name:
                self._learned_device["name"] = device_name
            # Return to appropriate confirmation step
            device_type = self._learned_device.get("device_type", self._learned_device.get("type", "unknown"))
            if device_type in ["ew_sensor", "ewneo_sensor"]:
                # Generate placeholders again for EWneo sensor
                sensor_types = self._learned_device.get("sensor_types", [])
                available_sensors = self._learned_device.get("available_sensors", [])
                sensor_list = "\\n".join([f"• {s.replace('_', ' ').title()}" for s in (sensor_types or available_sensors)])
                if not sensor_list:
                    sensor_list = "• Auto-detection on reception"
                
                serial_number = self._learned_device.get("serial_number", "?")
                
                return self.async_show_menu(
                    step_id="device_confirm_ewneo_sensor",
                    menu_options={
                        "device_confirm_create": "✅ Anlegen",
                        "device_confirm_rename": "✏️ Namen ändern",
                        "device_confirm_back": "⬅️ Zurück",
                    },
                    description_placeholders={
                        "serial_short": serial_number[-8:],
                        "suggested_name": device_name,
                        "sensor_list": sensor_list,
                    }
                )
            return await self.async_step_device_confirm()

        current_name = self._learned_device.get("name", "")
        return self.async_show_form(
            step_id="device_confirm_rename",
            data_schema=vol.Schema({
                vol.Required("device_name", default=current_name): str,
            }),
            description_placeholders={
                "current_name": current_name,
            },
        )
        
    async def async_step_device_ewneo_receiver_confirm_legacy(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Legacy: Confirm the learned EWneo device type and entity configuration."""
        if not self._learned_device:
            return self.async_abort(reason="no_learned_device")

        # Extract learned device information
        device_type_code = self._learned_device.get("device_type_code")
        device_type_name = self._learned_device.get("device_type_name")
        serial_number = self._learned_device.get("serial_number")
        ewneo_index = self._learned_device.get("ewneo_index")
        
        # Suggest default name and entity type based on device type
        suggested_name = f"EWneo-Device ({serial_number[-6:]})"
        suggested_entity_type = "switch"  # Default to switch
        
        # Try to determine better defaults based on device type code
        if device_type_code:
            if device_type_code == 0x04:  # EWB_DT_DIMMER
                suggested_entity_type = "light"
                suggested_name = f"EWneo-Dimmer ({serial_number[-6:]})"
            elif device_type_code in [0x05, 0x08, 0x09]:  # EWB_DT_MOTOR, EWB_DT_DUAL_MOTOR, EWB_DT_QUAD_MOTOR
                suggested_entity_type = "cover"
                suggested_name = f"EWneo-Motor ({serial_number[-6:]})"
            elif device_type_code in [0x03, 0x06, 0x07]:  # EWB_DT_SWITCH, EWB_DT_DUAL_SWITCH, EWB_DT_QUAD_SWITCH
                suggested_entity_type = "switch"
                suggested_name = f"EWneo-Switch ({serial_number[-6:]})"
            else:  # Unknown device type
                suggested_entity_type = "switch"
                suggested_name = f"EWneo-Device ({serial_number[-6:]})"

        if not self._learned_device.get("name"):
            self._learned_device["name"] = suggested_name

        return self.async_show_menu(
            step_id="device_ewneo_receiver_confirm",
            menu_options={
                "device_ewneo_receiver_confirm_create": "✅ Anlegen",
                "device_ewneo_receiver_confirm_rename": "✏️ Namen ändern",
                "device_ewneo_receiver_confirm_back": "⬅️ Zurück",
            },
            description_placeholders={
                "ewneo_index": str(ewneo_index),
                "serial": serial_number[-8:] if serial_number else "Unknown",
                "device_name": self._learned_device.get("name", suggested_name),
            }
        )

    async def async_step_device_ewneo_receiver_confirm_create(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Create EWneo receiver from legacy confirmation menu."""
        if not self._learned_device:
            return self.async_abort(reason="no_learned_device")

        device_name = self._learned_device.get("name")
        device_type_code = self._learned_device.get("device_type_code")

        if device_type_code == 0x04:  # EWB_DT_DIMMER
            entity_type = "light"
            supports_dimming = True
            supports_color = False
        elif device_type_code in [0x05, 0x08, 0x09]:  # EWB_DT_MOTOR, EWB_DT_DUAL_MOTOR, EWB_DT_QUAD_MOTOR
            entity_type = "cover"
            supports_dimming = False
            supports_color = False
        elif device_type_code in [0x03, 0x06, 0x07]:  # EWB_DT_SWITCH, EWB_DT_DUAL_SWITCH, EWB_DT_QUAD_SWITCH
            entity_type = "switch"
            supports_dimming = False
            supports_color = False
        else:  # Unknown device type - default to switch
            entity_type = "switch"
            supports_dimming = False
            supports_color = False

        self._learned_device.update({
            "entity_type": entity_type,
            "name": device_name,
            "supports_dimming": supports_dimming,
            "supports_color": supports_color,
        })

        _LOGGER.info("✅ EWneo device confirmed: %s as %s (type_code: 0x%02X)",
                    device_name, entity_type, device_type_code)
        return await self.async_step_device_save()

    async def async_step_device_ewneo_receiver_confirm_back(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Return from legacy EWneo receiver confirmation menu."""
        self._learned_device = None
        return await self.async_step_device_ewneo_receiver()

    async def async_step_device_ewneo_receiver_confirm_rename(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Rename EWneo receiver before creation (legacy confirmation menu)."""
        if not self._learned_device:
            return self.async_abort(reason="no_learned_device")

        if user_input is not None:
            device_name = user_input.get("device_name")
            if device_name:
                self._learned_device["name"] = device_name
            return await self.async_step_device_ewneo_receiver_confirm_legacy()

        current_name = self._learned_device.get("name", "")
        return self.async_show_form(
            step_id="device_ewneo_receiver_confirm_rename",
            data_schema=vol.Schema({
                vol.Required("device_name", default=current_name): str,
            }),
            description_placeholders={
                "current_name": current_name,
            },
        )

    async def async_step_device_save(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Save the learned device and create appropriate entities."""
        # Register the device in the coordinator
        entries = [entry for entry in self._async_current_entries() if entry.domain == DOMAIN]
        coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None
        
        # Determine device data source (learned vs configured)
        device_data = self._learned_device if self._learned_device else getattr(self, '_device_config', {})
        
        if coordinator and device_data:
            # Generate serial number if not available (for manually configured devices)
            serial_number = device_data.get("serial_number") or device_data.get("serial")
            if not serial_number:
                # Generate serial for manually configured devices
                import time
                serial_number = f"MANUAL_{int(time.time())}"
                device_data["serial_number"] = serial_number
                
            device_type = device_data.get("device_type", device_data.get("type", "unknown"))
            
            if serial_number:
                # Check if device already exists and remove it cleanly first
                from .entity_registry import get_entity_registry
                entity_registry = get_entity_registry()
                
                if serial_number in coordinator.devices:
                    _LOGGER.info("🔄 Device %s already exists, removing before re-adding", serial_number[-8:])
                    
                    # Mark device for removal in registry
                    entity_registry.mark_device_for_removal(serial_number)
                    
                    # Remove from coordinator
                    await coordinator.async_remove_device(serial_number, force=True)
                    
                    # Complete removal cleanup
                    entity_registry.complete_device_removal(serial_number)
                    
                    # Wait a bit to ensure cleanup
                    await asyncio.sleep(0.5)
                
                # Determine specific device properties and entities to create
                entity_info = self._determine_device_entities()
                
                _LOGGER.warning("🔍 _determine_device_entities returned: %d entities, platforms: %s", 
                              len(entity_info.get("entities", [])), entity_info.get("platforms", []))
                for entity in entity_info.get("entities", []):
                    _LOGGER.warning("  📋 Entity: type=%s, sensor_type=%s, name=%s, unique_id=%s", 
                                  entity.get("type"), entity.get("sensor_type"), 
                                  entity.get("name"), entity.get("unique_id", "")[-16:])
                
                # Update device info with entity specifications
                device_data.update(entity_info)
                
                # Note: Entity creation is now fully handled by HA's entity registry
                # No manual reset needed - HA will handle duplicate prevention automatically
                _LOGGER.info("🔄 Device %s ready for entity creation via HA registry", serial_number[-8:])
                
                # Register device PERMANENTLY in the registered devices list FIRST
                # Note: register_device_permanently will fire all necessary events
                coordinator.devices[serial_number] = device_data
                await coordinator.register_device_permanently(serial_number, device_data)
                _LOGGER.info("✅ Device saved to registry: %s", serial_number[-8:])
                
                # Register device in Home Assistant's Device Registry
                from homeassistant.helpers import device_registry as dr
                device_registry = dr.async_get(self.hass)
                
                device_name = device_data.get("name", f"{device_type} {serial_number[-6:]}")
                manufacturer = "ELDAT"
                model = device_data.get("device_type_name", device_type).replace("_", " ").title()
                
                device_entry = device_registry.async_get_or_create(
                    config_entry_id=entries[0].entry_id,
                    identifiers={(DOMAIN, serial_number)},
                    name=device_name,
                    manufacturer=manufacturer,
                    model=model,
                    serial_number=serial_number,
                    sw_version=device_data.get("firmware_version"),
                )
                _LOGGER.info("✅ Device registered in HA Device Registry: %s (ID: %s)", device_name, device_entry.id)
                
                # Assign device to area if specified
                area_id = device_data.get("area_id")
                if area_id and device_entry:
                    device_registry.async_update_device(device_entry.id, area_id=area_id)
                    _LOGGER.info("📍 Device assigned to area: %s", area_id)
                
                # Ensure the device data is saved before firing events
                await coordinator._save_device_configuration()
                _LOGGER.info("✅ Device configuration saved")

                # For EWneo devices, query initial state with EwbQueryState Mode 0
                if device_data.get("neo_device") and device_data.get("gateway_serial"):
                    _LOGGER.info("🔍 Querying initial state for EWneo device %s...", serial_number[-8:])
                    gateway_serial = device_data.get("gateway_serial")
                    receiver_serial = serial_number
                    
                    try:
                        state_result = await coordinator.transceiver.rx11_ewb_query_state(
                            gateway_serial, receiver_serial, mode=0
                        )
                        
                        if state_result:
                            mode, state_bytes = state_result
                            device_type_code = device_data.get("device_type_code", 0)
                            device_type_name = device_data.get("device_type_name", "unknown")
                            
                            # Parse the state using the existing parsing function
                            parsed_state = coordinator._parse_ewneo_state(device_type_code, state_bytes, device_type_name)
                            
                            if parsed_state:
                                _LOGGER.info("✅ Initial EWneo state parsed successfully: %s", parsed_state)
                                # Store initial state in device data for entity initialization
                                device_data["initial_state"] = parsed_state
                                coordinator.devices[serial_number] = device_data  # Update with initial state
                            else:
                                _LOGGER.warning("⚠️ Could not parse initial EWneo state")
                        else:
                            _LOGGER.warning("⚠️ EwbQueryState failed for EWneo device %s", serial_number[-8:])
                    except Exception as e:
                        _LOGGER.warning("⚠️ Error querying initial EWneo state: %s", e)

                # Register for telegram monitoring if not send-only
                if device_type != "ew_receiver":
                    # Use modern transceiver interface
                    if hasattr(coordinator, 'transceiver'):
                        await coordinator.transceiver.register_device(serial_number, device_data)
                    coordinator._known_devices.add(serial_number)
                else:
                    _LOGGER.info("EW-Receiver %s is send-only; not registering for incoming telegram monitoring", serial_number)

                _LOGGER.info("Device permanently registered: %s (%s) with %d entities", 
                           device_data.get("name", device_data.get("device_type", "Unbekanntes Gerät")), 
                           serial_number[-8:], len(entity_info.get("entities", [])))
                
                # SKIP: register_device_permanently already fired EVENT_DEVICE_ADDED and platform-specific events
                # Firing again would cause duplicate entity creation
                # await self._fire_device_creation_events(serial_number, entity_info)
                
                # Ensure telegram callback is properly set after device registration
                # This is critical because device registration might override the callback
                if hasattr(coordinator, 'transceiver') and coordinator.transceiver:
                    _LOGGER.info("🔗 Re-setting telegram callback after device registration")
                    coordinator.transceiver.set_telegram_callback(coordinator._handle_telegram)
                
                # SKIP: register_device_permanently already fired platform-specific events
                # Firing them again would cause duplicate entity creation
                _LOGGER.info("✅ Skipping duplicate platform event firing (already done by register_device_permanently)")
                
                # Give event handlers sufficient time to process and create entities
                # Try multiple times with delays to allow async processing
                _LOGGER.info("⏳ Waiting for entity creation events to be processed...")
                
                from homeassistant.helpers import entity_registry as er
                entity_reg = er.async_get(self.hass)
                expected_entities = len(entity_info.get("entities", []))
                
                # Wait and check multiple times
                max_attempts = 5
                for attempt in range(max_attempts):
                    await asyncio.sleep(0.3)
                    
                    actual_entities = 0
                    created_entity_types = []
                    missing_entity_types = []
                    
                    for entity_spec in entity_info.get("entities", []):
                        unique_id = entity_spec.get("unique_id")
                        entity_type = entity_spec.get("type", "sensor")
                        sensor_type = entity_spec.get("sensor_type", entity_spec.get("button_type", "unknown"))
                        
                        if unique_id:
                            entity_id = entity_reg.async_get_entity_id(entity_type, DOMAIN, unique_id)
                            if entity_id:
                                actual_entities += 1
                                created_entity_types.append(f"{sensor_type}")
                            else:
                                missing_entity_types.append(f"{sensor_type}")
                    
                    _LOGGER.info("📊 Check %d/%d: %d/%d entities in registry", 
                               attempt + 1, max_attempts, actual_entities, expected_entities)
                    
                    # If all entities are created, break early
                    if actual_entities >= expected_entities:
                        _LOGGER.info("✅ All entities created successfully: %s", created_entity_types)
                        break
                else:
                    # Loop completed without break - not all entities created
                    _LOGGER.warning("⚠️ Only %d/%d entities created. Missing: %s", 
                                  actual_entities, expected_entities, missing_entity_types)
                    _LOGGER.info("💡 Missing entities will appear after Home Assistant restart")
                
                # Force a coordinator refresh to update states
                if coordinator:
                    await asyncio.sleep(0.2)
                    _LOGGER.info("🔄 Forcing coordinator refresh...")
                    await coordinator.async_request_refresh()
        
        # Determine if entities were created
        entity_count = len(entity_info.get("entities", [])) if 'entity_info' in locals() else 0
        device_name = device_data.get("name", device_data.get("device_type", "Unbekanntes Gerät")) if 'device_data' in locals() else "Neues Gerät"
        serial = serial_number[-8:] if 'serial_number' in locals() and serial_number else "?"
        
        # Check if entities actually exist in HA
        from homeassistant.helpers import entity_registry as er
        entity_reg = er.async_get(self.hass)
        created_count = 0
        if 'entity_info' in locals() and 'serial_number' in locals():
            for entity_spec in entity_info.get("entities", []):
                unique_id = entity_spec.get("unique_id")
                if unique_id and entity_reg.async_get_entity_id(entity_spec.get("type", "sensor"), DOMAIN, unique_id):
                    created_count += 1
        
        # Abort with success message
        return self.async_abort(
            reason="device_added",
            description_placeholders={
                "device_name": device_name
            }
        )

    def _determine_device_entities(self) -> dict:
        """DEPRECATED: Determine which entities to create based on device type and properties.
        
        This method now delegates to entity_specs.create_entity_specs_for_device()
        for consistency across the codebase.
        """
        from .entity_specs import create_entity_specs_for_device
        
        # Use device data from either learned device or config
        device_data = self._learned_device if self._learned_device else getattr(self, '_device_config', {})
        device_type = device_data.get("device_type", device_data.get("type", "unknown"))
        serial_number = device_data.get("serial_number", "unknown")
        
        _LOGGER.warning("🔍 _determine_device_entities called:")
        _LOGGER.warning("  📋 device_type: %s", device_type)
        _LOGGER.warning("  📋 serial_number: %s", serial_number[-16:])
        _LOGGER.warning("  📋 sensor_types: %s", device_data.get("sensor_types"))
        _LOGGER.warning("  📋 available_sensors: %s", device_data.get("available_sensors"))
        
        # Get entity specs from centralized function
        entity_specs = create_entity_specs_for_device(serial_number, device_data)
        
        _LOGGER.warning("🔍 create_entity_specs_for_device returned: %s", 
                      {k: len(v) for k, v in entity_specs.items() if v})
        
        # Convert entity_specs format (dict of lists) to old format (single list + platforms)
        all_entities = []
        all_platforms = set()
        
        for platform, entities in entity_specs.items():
            if entities:
                all_platforms.add(platform)
                all_entities.extend(entities)
        
        # Determine device_class and category from device type
        device_class = "unknown"
        category = "config"
        
        if device_type == "ew_transmitter":
            device_class = "remote_control"
            category = "remote"
        elif device_type in ["ew_sensor", "ewneo_sensor"]:
            device_class = "sensor"
            category = "sensor"
        elif device_type == "ew_receiver" or device_type == "EW-Receiver":
            receiver_kind = device_data.get("receiver_kind", "switch")
            if receiver_kind in ["cover_2button", "motor_3button"]:
                device_class = "garage"
                category = "cover"
            elif receiver_kind == "heating_cooling":
                device_class = "switch"
                category = "switch"
            elif receiver_kind == "switch_2button":
                device_class = "switch"
                category = "switch"
            elif receiver_kind == "impulse":
                device_class = "button"
                category = "button"
            elif receiver_kind == "universal_4button":
                device_class = "button"
                category = "button"
            else:
                device_class = "switch"
                category = "switch"
        elif device_type == "ewneo_receiver":
            entity_type = device_data.get("entity_type", "switch")
            device_class = entity_type
            category = "actuator"
        
        return {
            "entities": all_entities,
            "platforms": all_platforms,
            "device_class": device_class,
            "category": category,
        }

    async def _fire_device_creation_events(self, serial_number: str, entity_info: dict) -> None:
        """Fire events to create appropriate entities for the device."""
        from .const import EVENT_DEVICE_ADDED
        
        # Use device data from either learned device or config
        device_data = self._learned_device if self._learned_device else getattr(self, '_device_config', {})
        
        # Fire general device added event
        self.hass.bus.async_fire(
            EVENT_DEVICE_ADDED,
            {
                "serial_number": serial_number,
                "device_info": device_data,
                "device_type": device_data.get("device_type", device_data.get("type")),
                "device_name": device_data.get("name"),
                "entity_info": entity_info,
                "entities": entity_info.get("entities", []),
                "platforms": list(entity_info.get("platforms", set())),
            }
        )

    

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> EldatOptionsFlow:
        """Get the options flow for this handler."""
        return EldatOptionsFlow(config_entry)


class EldatOptionsFlow(config_entries.OptionsFlow):
    """Handle ELDAT options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Initial options menu with device management actions."""
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        learning_active = False
        device_count = 0
        if coordinator:
            # Use modern coordinator interface
            if hasattr(coordinator, 'transceiver'):
                learning_active = await coordinator.transceiver.is_learning_mode()
            device_count = len(coordinator.devices)

        if user_input is not None:
            action = user_input.get("action")
            if action == "save_settings":
                # Persist scan interval only
                new_options = {
                    CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
                }
                return self.async_create_entry(title="", data=new_options)
            elif action == "enable_learning" and coordinator:
                timeout = user_input.get("learning_duration", 60)
                if hasattr(coordinator, 'transceiver'):
                    await coordinator.transceiver.set_learning_mode(True, timeout)
                return await self.async_step_init()
            elif action == "disable_learning" and coordinator:
                if hasattr(coordinator, 'transceiver'):
                    await coordinator.transceiver.set_learning_mode(False)
                return await self.async_step_init()
            elif action == "manage_devices" and coordinator:
                return await self.async_step_manage_devices()

        action_options = {
            "enable_learning": "🟢 Enable learning" if not learning_active else "",
            "disable_learning": "🛑 Disable learning" if learning_active else "",
            "manage_devices": f"🔧 Manage devices ({device_count})",
            "save_settings": "💾 Save"
        }
        # Remove empty entries
        action_options = {k: v for k, v in action_options.items() if v}

        data_schema = vol.Schema({
            vol.Optional(CONF_SCAN_INTERVAL, default=self.config_entry.options.get(
                CONF_SCAN_INTERVAL,
                self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            )): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
            vol.Required("action"): vol.In(action_options),
            vol.Optional("learning_duration", default=60): vol.All(int, vol.Range(min=10, max=600))
        })

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
            description_placeholders={
                "device_count": str(device_count),
                "learning_status": "active" if learning_active else "inactive",
                "transceiver_type": self.config_entry.data.get(CONF_TRANSCEIVER_TYPE, "unknown"),
            }
        )

    async def async_step_manage_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage existing devices - view and remove."""
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        
        if not coordinator:
            return self.async_abort(reason="no_coordinator")

        errors = {}
        devices = coordinator.devices or {}
        
        if user_input is not None:
            action = user_input.get("action")
            
            if action == "back":
                return await self.async_step_init()
            elif action == "remove_device":
                device_serial = user_input.get("device_to_remove")
                if device_serial and device_serial in devices:
                    try:
                        success = await coordinator.async_remove_device(device_serial, force=True)
                        if success:
                            _LOGGER.info("Gerät %s erfolgreich entfernt", device_serial)
                            # Refresh device list
                            return await self.async_step_manage_devices()
                        else:
                            errors["base"] = "remove_failed"
                    except Exception as e:
                        _LOGGER.error("Fehler beim Entfernen von Gerät %s: %s", device_serial, e)
                        errors["base"] = "remove_error"
                else:
                    errors["base"] = "invalid_device"

        # Build device selection options
        device_options = {}
        for serial, device_info in devices.items():
            device_name = device_info.get("name", f"Device {serial[-6:]}")
            device_type = device_info.get("type", "unknown")
            device_options[serial] = f"{device_name} ({device_type})"

        if not device_options:
            # No devices to manage
            data_schema = vol.Schema({
                vol.Required("action", default="back"): vol.In({"back": "🔙 Back"})
            })
        else:
            data_schema = vol.Schema({
                vol.Required("action", default="back"): vol.In({
                    "back": "🔙 Back",
                    "remove_device": "🗑️ Remove device"
                }),
                vol.Optional("device_to_remove"): vol.In(device_options)
            })

        return self.async_show_form(
            step_id="manage_devices",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "device_count": str(len(devices)),
                "devices": ", ".join([info.get("name", serial[-6:]) for serial, info in devices.items()][:5])
            }
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidDevice(Exception):
    """Error to indicate the device is invalid."""