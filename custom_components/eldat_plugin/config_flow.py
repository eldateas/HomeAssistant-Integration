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
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import device_registry as dr
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

# Simple schema used when waiting for a sensor telegram during learning
STEP_DEVICE_SENSOR_SCHEMA = vol.Schema({
    vol.Required("action", default="wait"): vol.In({
        "wait": "⏳ Weiter warten",
        "cancel": "❌ Abbrechen",
    })
})


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
    
    async def _cleanup_learning_mode(self, coordinator=None) -> None:
        """Clean up learning mode and tasks."""
        if self._learn_cancel_event:
            self._learn_cancel_event.set()
        
        # Cancel all learning tasks
        for task_attr in ['_learn_task', '_learning_task', '_sensor_learning_task']:
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
        
        # Devices already discovered in auto_detection step
        # Refresh devices if requested
        if user_input and user_input.get(CONF_DEVICE_PATH) == "refresh":
            _LOGGER.info("Aktualisiere RX11 Geräteliste...")
            try:
                self._discovered_devices = await self.hass.async_add_executor_job(find_rx11_devices)
                _LOGGER.info("Gefunden: %d RX11-Geräte", len(self._discovered_devices))
            except Exception as e:
                _LOGGER.warning("Fehler bei der RX11-Gerätesuche: %s", e)
                self._discovered_devices = []

        if user_input is not None:
            device_path = user_input.get(CONF_DEVICE_PATH)
            device_name = user_input.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)

            if device_path == "manual":
                return await self.async_step_manual()
            if device_path == "refresh":
                self._discovered_devices = []
                return await self.async_step_rx11_setup()
            if device_path:
                # Skip connection test - let the actual setup validate the connection
                # This avoids unnecessary connect/disconnect cycles
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

        device_options = {}
        for device in self._discovered_devices:
            name = f"{device['name']} ({device['device']})"
            device_options[device["device"]] = name

        device_options["manual"] = "🔧 Manueller Pfad eingeben"
        device_options["refresh"] = "🔄 Geräteliste aktualisieren"

        if not self._discovered_devices:
            errors["base"] = "no_devices_found"

        data_schema = vol.Schema({
            vol.Required(CONF_DEVICE_PATH): vol.In(device_options),
            vol.Optional(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
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
        """Select device type to add."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_type = user_input.get("device_type")
            self._device_type = device_type

            if device_type == "ew_transmitter":
                return await self.async_step_device_transmitter_config()
            if device_type == "ew_sensor":
                return await self.async_step_device_sensor()
            if device_type == "ew_receiver":
                return await self.async_step_device_receiver()
            if device_type == "ewneo_receiver":
                return await self.async_step_device_ewneo_receiver()

        device_type_options = {
            "ew_transmitter": "🎛️ EW-Transmitter (Handsender/Fernbedienung)",
            "ew_sensor": "🌡️ EWneo-Sensor (Temperatur/Feuchtigkeit/Wetter)", 
            "ew_receiver": "📥 EW-Empfänger (konfigurieren ohne Lernen)",
            "ewneo_receiver": "📥 EWneo-Switch/Motor (Neo-Schaltaktor)",
        }

        data_schema = vol.Schema({
            vol.Required("device_type"): vol.In(device_type_options),
        })

        return self.async_show_form(
            step_id="device_type_select",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "instructions": "Wählen Sie den Gerätetyp aus, den Sie hinzufügen möchten."
            },
        )

    async def async_step_device_transmitter_config(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure transmitter button count before learning."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            button_count = int(user_input["button_count"])
            self._device_config = {
                "device_type": "ew_transmitter",
                "button_count": button_count,
                "channels": button_count
            }
            return await self.async_step_device_transmitter_description()
        
        data_schema = vol.Schema({
            vol.Required("button_count", default="4"): vol.In({
                "1": "🔘 Eintaster (1 Taste)",
                "2": "🔘🔘 Zweitaster (2 Tasten)", 
                "3": "🔘🔘🔘 Dreitaster (3 Tasten A/B/C)",
                "4": "🔘🔘🔘🔘 Viertaster (4 Tasten A/B/C/D)"
            })
        })
        
        return self.async_show_form(
            step_id="device_transmitter_config",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "instructions": (
                    f"**⚙️ EW-Transmitter Konfiguration**\n\n"
                    f"Wählen Sie die Anzahl der Tasten, die Ihr Handsender hat.\n"
                    f"Im nächsten Schritt erhalten Sie Anweisungen zum Einlernen.\n\n"
                    f"Für jeden Taster wird automatisch eine binary_sensor Entität erstellt."
                )
            }
        )



    async def async_step_device_transmitter_learn(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle EW-Transmitter learning after button count configuration."""
        # This method is for direct progression - go to the actual learning step
        # Get coordinator
        entries = [entry for entry in self._async_current_entries() if entry.domain == DOMAIN]
        coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None

        if not coordinator:
            return self.async_abort(reason="transceiver_not_available")

        # Start progress indicator
        button_count = self._device_config.get("button_count", 4)
        progress_task = self.async_show_progress(
            step_id="device_transmitter_learn",
            progress_action="waiting_for_transmitter_telegram"
        )
        
        _LOGGER.info("=== TRANSMITTER LEARNING START ===")

        # Wait for telegram using the central learning helper
        _LOGGER.info("Waiting for transmitter telegram...")
        try:
            def _match_transmitter(dev: dict) -> Optional[dict]:
                # Accept both 'type' and 'device_type' markers and InfoType==1
                is_transmitter = (
                    dev.get("type") == "ew_transmitter"
                    or dev.get("device_type") == "transmitter"
                    or dev.get("info_type") == 1
                )
                if is_transmitter and not dev.get("added_manually", False):
                    return dev
                return None

            # Start setup mode for device registration
            coordinator.start_setup_mode(timeout_seconds=180)
            
            learned = await run_learning(coordinator, _match_transmitter, timeout=180)

            if learned:
                _LOGGER.info("Transmitter Telegramm-Event empfangen: %s", learned)
                received_serial = learned.get("serial_number", learned.get("serial", "?"))
                _LOGGER.info("📡 EW-Transmitter empfangen - Seriennummer: %s", received_serial)
                
                self._learned_device = {
                    "name": learned.get("name", f"EW-Transmitter {received_serial[-6:] if received_serial != '?' else '?'}"),
                    "serial_number": received_serial,
                    "type": "ew_transmitter",
                    "device_type": "ew_transmitter",
                    "button_count": self._device_config.get("button_count", 4),
                    "channels": self._device_config.get("channels", 4),
                    "last_telegram": {
                        "info_type": learned.get("info_type"),
                        "button": learned.get("button"),
                        "function": learned.get("function"),
                        "raw_data": learned.get("raw_data"),
                        "timestamp": learned.get("timestamp"),
                    },
                }
                _LOGGER.info("ConfigFlow: self._learned_device gesetzt: %s", self._learned_device)
                # Direkt speichern ohne Bestätigung
                return await self.async_step_device_save()
                
            _LOGGER.warning("Kein Telegramm-Event empfangen (learning_timeout)")
            coordinator.stop_setup_mode()
            return self.async_abort(reason="learning_timeout")
            
        except Exception as e:
            _LOGGER.error("Error in transmitter learning: %s", e, exc_info=True)
            coordinator._config_flow_learning = False
            coordinator.stop_setup_mode()
            return self.async_abort(reason="unknown")

    async def async_step_device_transmitter_description(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show detailed learning instructions for EW-Transmitter."""
        if user_input is not None:
            action = user_input.get("action")
            if action == "start_learning":
                return await self.async_step_device_transmitter_learn()
            elif action == "cancel":
                return self.async_abort(reason="user_cancelled")
        
        button_count = self._device_config.get("button_count", 4)
        
        return self.async_show_form(
            step_id="device_transmitter_description",
            data_schema=vol.Schema({
                vol.Required("action", default="start_learning"): vol.In({
                    "start_learning": "▶️ Einlernen starten",
                    "cancel": "❌ Abbrechen"
                })
            }),
            description_placeholders={
                "instructions": (
                    f"🎛️ **EW-Transmitter Einlernen**\n\n"
                    f"**📋 Konfiguration:**\n"
                    f"• Tastenanzahl: {button_count}\n"
                    f"• Typ: EW-Transmitter (Handsender/Fernbedienung)\n\n"
                    f"**📡 Lernvorgang:**\n\n"
                    f"**1.** Klicken Sie auf 'Einlernen starten'\n"
                    f"**2.** Drücken Sie anschließend eine beliebige Taste am EW-Transmitter\n"
                    f"**3.** Das System wartet bis zu 3 Minuten auf das Funksignal\n"
                    f"**4.** Nach Empfang werden die Geräteinformationen angezeigt\n"
                    f"**5.** Bestätigen Sie die Erstellung des Geräts\n\n"
                    f"⚡ **Was wird erstellt:**\n"
                    f"• Ein EW-Transmitter Gerät\n"
                    f"• {button_count} Tasten-Entitäten (eine pro Taste)\n"
                    f"• Automatische Statusaktualisierung bei Tastendruck\n\n"
                    f"🔴 **Wichtig:** Halten Sie den EW-Transmitter bereit!"
                )
            }
        )

    async def async_step_device_sensor(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Start EW-Sensor learning - go to description step."""
        return await self.async_step_device_sensor_description()

    async def async_step_device_sensor_description(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show detailed learning instructions for EW-Sensor."""
        if user_input is not None:
            action = user_input.get("action")
            if action == "start_learning":
                return await self.async_step_device_sensor_start()
            elif action == "cancel":
                return self.async_abort(reason="user_cancelled")
        
        return self.async_show_form(
            step_id="device_sensor_description",
            data_schema=vol.Schema({
                vol.Required("action", default="start_learning"): vol.In({
                    "start_learning": "▶️ Einlernen starten",
                    "cancel": "❌ Abbrechen"
                })
            }),
            description_placeholders={
                "instructions": (
                    "🌡️ **EWneo-Sensoren Einlernen**\n\n"
                    "**📋 Gerätetyp:** EWneo-Sensoren (Temperatur/Feuchtigkeit/Wetter)\n\n"
                    "**📡 Lernvorgang:**\n\n"
                    "**1.** Klicken Sie auf 'Einlernen starten'\n"
                    "**2.** Betätigen Sie anschließend die Lerntaste am EWneo-Sensoren\n"
                    "**3.** Das System wartet bis zu 3 Minuten auf das Lerntelegramm\n"
                    "**4.** Nach Empfang werden die Sensorinformationen angezeigt\n"
                    "**5.** Bestätigen Sie die Erstellung des Sensors\n\n"
                    "📊 **Automatische Erkennung:**\n"
                    "• 🌡️ Temperatur-Sensor (falls verfügbar)\n"
                    "• 💧 Feuchtigkeits-Sensor (falls verfügbar)\n"
                    "• ☔ Regen-Sensor (falls verfügbar)\n"
                    "• 💨 Wind-Sensor (falls verfügbar)\n"
                    "• 🔋 Batterie-Status wird überwacht\n\n"
                    "⚡ **Was wird erstellt:**\n"
                    "• Ein EWneo-Sensoren Gerät\n"
                    "• Automatische Sensor-Entitäten für alle erkannten Messgrößen\n"
                    "• Regelmäßige Aktualisierung bei empfangenen Telegrammen\n\n"
                    "🔴 **Wichtig:** Halten Sie den EWneo-Sensoren mit der Lerntaste bereit!"
                )
            }
        )

    async def async_step_device_sensor_start(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Start EW-Sensor learning after description display."""
        return await self.async_step_device_sensor_learn_actual()

    async def async_step_device_sensor_learn_actual(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Wait for sensor telegram with progress indicator."""
        # Get coordinator
        entries = [entry for entry in self._async_current_entries() if entry.domain == DOMAIN]
        coordinator = self.hass.data.get(DOMAIN, {}).get(entries[0].entry_id) if entries else None

        if not coordinator:
            return self.async_abort(reason="transceiver_not_available")

        # Start progress indicator
        progress_task = self.async_show_progress(
            step_id="device_sensor_learn_actual",
            progress_action="waiting_for_sensor_telegram"
        )
        
        _LOGGER.info("=== SENSOR LEARNING START ===")

        # Wait for sensor telegram using the central learning helper
        _LOGGER.info("Waiting for EWneo-Sensoren LEARN telegram...")
        try:
            def _match_sensor(dev: dict) -> Optional[dict]:
                # Only accept EWneo sensor LEARN telegrams (not measurement telegrams)
                is_sensor = (
                    dev.get("type") == "ew_sensor"
                    or dev.get("device_type") == "sensor"
                    or dev.get("info_type") == 2  # Sensor info type
                )
                # CRITICAL: Only accept learn telegrams, reject normal measurement telegrams
                is_learn = dev.get("is_learn_telegram", False)
                
                if is_sensor and is_learn and not dev.get("added_manually", False):
                    _LOGGER.info("✅ EWneo-Sensoren Learn-Telegramm erkannt: %s", dev.get("serial_number", "?  ")[-6:])
                    return dev
                elif is_sensor and not is_learn:
                    _LOGGER.info("⏭️ EWneo-Sensoren Messwert-Telegramm ignoriert (nur Lerntelegramme werden akzeptiert): %s", dev.get("serial_number", "?")[-6:])
                return None

            # Start setup mode for device registration
            coordinator.start_setup_mode(timeout_seconds=180)
            
            learned = await run_learning(coordinator, _match_sensor, timeout=180)

            if learned:
                _LOGGER.info("Sensor Telegramm-Event empfangen: %s", learned)
                
                # Extract available sensors from learn telegram
                available_sensors = learned.get("available_sensors", [])
                measurement_types = learned.get("measurement_types", [])
                sensor_capabilities = learned.get("sensor_capabilities", [])
                
                _LOGGER.info("📋 Parsed sensor data: available_sensors=%s, measurement_types=%s, sensor_capabilities=%s",
                           available_sensors, measurement_types, sensor_capabilities)
                
                # Determine sensor types from telegram data
                # Use sensor_capabilities as it includes battery
                detected_sensors = set()
                if sensor_capabilities:
                    detected_sensors.update(sensor_capabilities)
                elif available_sensors:
                    detected_sensors.update(available_sensors)
                elif measurement_types:
                    detected_sensors.update(measurement_types)
                
                # Log what was detected
                if detected_sensors:
                    _LOGGER.info("✅ Detected sensor capabilities from learn telegram: %s", detected_sensors)
                else:
                    _LOGGER.warning("⚠️ No sensor capabilities detected in learn telegram! Telegram may be incomplete.")
                
                received_serial = learned.get("serial_number", learned.get("serial", "?"))
                _LOGGER.info("📡 EW-Sensor empfangen - Seriennummer: %s", received_serial)
                
                # Convert sensor capabilities to sensor_types for new EWneoSensor class
                sensor_types = []
                for sensor_cap in detected_sensors:
                    # Skip battery - it's handled separately
                    if sensor_cap != "battery":
                        sensor_types.append(sensor_cap)
                
                self._learned_device = {
                    "name": learned.get("name", f"EWneo-Sensoren {received_serial[-6:] if received_serial != '?' else '?'}"),
                    "serial_number": received_serial,
                    "type": "ewneo_sensor",  # WICHTIG: ewneo_sensor statt ew_sensor
                    "device_type": "ewneo_sensor",
                    "is_learn_telegram": True,
                    "available_sensors": list(detected_sensors),
                    "measurement_types": measurement_types,
                    "sensor_capabilities": sensor_capabilities,
                    "sensor_types": sensor_types,  # NEU: Für neue EWneoSensor Klasse
                    "has_battery": True,
                    "battery_level": learned.get("battery_level", 100),
                    "last_telegram": {
                        "info_type": learned.get("info_type"),
                        "raw_data": learned.get("raw_data"),
                        "timestamp": learned.get("timestamp"),
                    },
                }
                
                _LOGGER.info("EWneo-Sensoren configured with sensor_types: %s", sensor_types)
                # Direkt speichern ohne Bestätigung
                return await self.async_step_device_save()
            
            _LOGGER.warning("Kein Sensor-Telegramm empfangen (learning_timeout)")
            coordinator.stop_setup_mode()
            return self.async_abort(reason="learning_timeout")
            
        except Exception as e:
            _LOGGER.error("Error in sensor learning: %s", e, exc_info=True)
            coordinator._config_flow_learning = False
            coordinator.stop_setup_mode()
            return self.async_abort(reason="unknown")

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
                
                # Get serial for this index from RX11
                serial = await coordinator.transceiver.rx11_ew_receiver_get_serial_by_index(index)
                
                if serial:
                    self._device_config = {
                        "device_type": "ew_receiver",
                        "type": "ew_receiver",
                        "serial_number": serial,
                        "rx11_index": index,
                        "name": f"EW-Receiver (Index {index})"  # Temporär, wird mit receiver_kind aktualisiert
                    }
                    _LOGGER.info("✅ Using EW-Receiver: Index %d, Serial %s", index, serial[-8:])
                    return await self.async_step_device_receiver_type()
                else:
                    return self.async_abort(reason="no_receiver_serial")
            else:
                return self.async_abort(reason="no_coordinator")
        except Exception as e:
            _LOGGER.error("Error getting next available EW-Receiver: %s", e)
            return self.async_abort(reason="receiver_load_failed")

    async def async_step_device_receiver_type(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure EW receiver device - Step 1: Select device type."""
        if user_input is not None:
            device_type = user_input.get("receiver_type")
            self._device_config["receiver_kind"] = device_type
            
            # Update device name with receiver type
            rx11_index = self._device_config.get("rx11_index", 0)
            type_names = {
                "switch": "Switch",
                "motor": "Motor",
                "heating_cooling": "Heizung"
            }
            type_name = type_names.get(device_type, "Receiver")
            self._device_config["name"] = f"EW-{type_name} (Index {rx11_index})"
            
            # For heating/cooling, skip operating mode selection - always use toggle (mode 1)
            if device_type == "heating_cooling":
                self._device_config["operating_mode"] = 1  # Force toggle mode for heating/cooling
                return await self.async_step_device_receiver_confirm()
            else:
                return await self.async_step_device_receiver_operating_mode()
            
        serial = self._device_config.get("serial_number", "Unbekannt")
        name = self._device_config.get("name", "EW-Receiver")
        
        return self.async_show_form(
            step_id="device_receiver_type",
            data_schema=vol.Schema({
                vol.Required("receiver_type"): vol.In({
                    "switch": "🔌 Schalter",
                    "motor": "🏠 Motor/Rollo",
                    "heating_cooling": "🌡️ Heizen/Kühlen (nur Toggle-Bedienung)"
                })
            }),
            description_placeholders={
                "device_name": name,
                "serial": serial[-12:] if serial else "Unbekannt",
                "instructions": (
                    f"📥 **EW-Receiver Konfiguration - Schritt 1/3**\n\n"
                    f"**📋 Geräteinformationen:**\n"
                    f"• Gerät: {name}\n"
                    f"• Seriennummer: {serial[-12:] if serial else 'Unbekannt'}\n"
                    f"• Typ: EW-Receiver (Schaltaktor)\n\n"
                    f"**🎛️ Gerätetyp auswählen:**\n\n"
                    f"**🔌 Schalter:** Standard Ein/Aus-Schaltung für Beleuchtung, Steckdosen, etc.\n"
                    f"**🏠 Motor/Rollo:** Steuerung für Rollläden, Jalousien, Markisen mit Auf/Ab/Stopp\n"
                    f"**🌡️ Heizen/Kühlen:** Temperaturregelung für Heizungen, Klimaanlagen mit Toggle-Bedienung (automatische 4h Wiederholung)\n\n"
                    f"💡 **Hinweis:** Der gewählte Gerätetyp bestimmt die verfügbaren Steuerungsoptionen und Button-Konfigurationen."
                )
            }
        )
        
    async def async_step_device_receiver_operating_mode(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure EW receiver device - Step 2: Select operating mode."""
        if user_input is not None:
            operating_mode = user_input.get("operating_mode")
            self._device_config["operating_mode"] = operating_mode
            return await self.async_step_device_receiver_confirm()
            
        receiver_kind = self._device_config.get("receiver_kind", "switch")
        serial = self._device_config.get("serial_number", "Unbekannt")
        
        # Different operating modes based on device type
        if receiver_kind == "heating_cooling":
            # Heating/Cooling: Only toggle operation (mode 1)
            mode_options = {
                1: "🔘 Toggle-Bedienung (Ein/Aus mit automatischer Wiederholung)"
            }
            type_desc = "Heizen/Kühlen (Temperaturregelung)"
            mode_descriptions = {
                1: "**Toggle-Bedienung:** Eine Taste für Ein/Aus-Steuerung\n• Toggle zwischen Ein/Aus\n• Automatische Statuswiederholung alle 4 Stunden\n• Einfache und zuverlässige Bedienung"
            }
        elif receiver_kind == "switch":
            # Switch: 1 or 2 button operation
            mode_options = {
                1: "🔘 1-Tast-Bedienung (Toggle)",
                2: "🔘🔘 2-Tast-Bedienung (Ein + Aus)"
            }
            type_desc = "Schalter (Ein/Aus-Steuerung)"
            mode_descriptions = {
                1: "**1-Tast-Bedienung:** Eine Taste wechselt zwischen Ein und Aus\n• Einfache Toggle-Funktion\n• Ideal für Lichtschalter",
                2: "**2-Tast-Bedienung:** Separate Tasten für Ein und Aus\n• Ein-Button: Gerät einschalten\n• Aus-Button: Gerät ausschalten\n• Präzise Kontrolle"
            }
        elif receiver_kind == "motor":
            # Motor: 1, 2 or 3 button operation
            mode_options = {
                1: "🔘 1-Tast-Bedienung (Toggle)",
                2: "🔘🔘 2-Tast-Bedienung (Ein + Aus)",
                3: "🔘🔘🔘 3-Tast-Bedienung (Auf + Zu + Stopp)"
            }
            type_desc = "Motor/Rollo (Auf/Ab-Steuerung)"
            mode_descriptions = {
                1: "**1-Tast-Bedienung:** Eine Taste für Start/Stopp\n• Toggle-Funktion für Motor\n• Einfache Bedienung",
                2: "**2-Tast-Bedienung:** Auf und Ab ohne separaten Stopp\n• Auf-Button: Motor vorwärts\n• Ab-Button: Motor rückwärts\n• Stopp durch nochmaliges Drücken",
                3: "**3-Tast-Bedienung:** Auf, Ab und separater Stopp\n• Auf-Button: Motor vorwärts\n• Ab-Button: Motor rückwärts\n• Stopp-Button: Sofortiger Halt"
            }
        else:
            # Fallback
            mode_options = {
                1: "🔘 1-Tast-Bedienung",
                2: "🔘🔘 2-Tast-Bedienung"
            }
            type_desc = "Empfänger"
            mode_descriptions = {
                1: "**1-Tast-Bedienung:** Eine Steuerungstaste",
                2: "**2-Tast-Bedienung:** Zwei Steuerungstasten"
            }
        
        # Generate combined description
        mode_desc_text = "\n\n".join([f"{desc}" for mode, desc in mode_descriptions.items() if mode in mode_options])
            
        return self.async_show_form(
            step_id="device_receiver_operating_mode",
            data_schema=vol.Schema({
                vol.Required("operating_mode"): vol.In(mode_options)
            }),
            description_placeholders={
                "device_name": self._device_config.get("name", "EW-Receiver"),
                "receiver_kind": type_desc,
                "instructions": (
                    f"📥 **EW-Receiver Konfiguration - Schritt 2/3**\n\n"
                    f"**📋 Gewählter Gerätetyp:** {type_desc}\n\n"
                    f"**🎛️ Betriebsart auswählen:**\n\n"
                    f"{mode_desc_text}\n\n"
                    f"💡 **Hinweis:** Die Betriebsart bestimmt, welche Buttons in Home Assistant erstellt werden und wie der Empfänger gesteuert wird."
                )
            }
        )
        
    async def async_step_device_receiver_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure EW receiver device - Step 3: Confirmation and learning mode."""
        if user_input is not None:
            action = user_input.get("action")
            if action == "confirm":
                # Send Code A and mark receiver as used
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
                        device_name = self._device_config.get("name", f"EW-Receiver (Index {rx11_index})")
                        coordinator.mark_ew_receiver_index_used(rx11_index, serial, serial, device_name)
                        _LOGGER.info("🔒 Marked receiver as used persistently: Index %d", rx11_index)
                    
                    # Always create the device
                    self._device_config.update({
                        "device_type": "ew_receiver",
                        "type": "ew_receiver",  # Add type field for consistency
                        "entity_type": "button"  # Always create button entities
                    })
                    _LOGGER.info("✅ Creating EW-Receiver device with serial: %s", serial[-8:] if serial else "Unknown")
                    return await self.async_step_device_save()
                    
                except Exception as e:
                    _LOGGER.error("Error in device creation: %s", e)
                    return self.async_show_form(
                        step_id="device_receiver_confirm",
                        errors={"base": "device_creation_error"},
                        **self._get_confirm_form_data()
                    )
            elif action == "cancel":
                return self.async_abort(reason="user_cancelled")
                
        return self.async_show_form(
            step_id="device_receiver_confirm",
            **self._get_confirm_form_data()
        )
        
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
                "instruction": "Geben Sie die Seriennummer des EW-Empfängers manuell ein."
            }
        )
        
        
    def _get_confirm_form_data(self) -> dict:
        """Get form data for confirmation dialog."""
        receiver_kind = self._device_config.get("receiver_kind", "switch")
        operating_mode = self._device_config.get("operating_mode", 1)
        serial = self._device_config.get("serial_number", "Unbekannt")
        name = self._device_config.get("name", "EW-Receiver")
        rx11_index = self._device_config.get("rx11_index", "Unbekannt")
        
        # Generate operating mode description
        if receiver_kind == "heating_cooling":
            if operating_mode == 1:
                mode_desc = "Toggle-Bedienung (Ein/Aus mit 4h-Wiederholung)"
                button_summary = "• Ein/Aus Toggle Switch\n• Automatische Statuswiederholung alle 4 Stunden"
            else:
                # Fallback for any non-1 modes (should not happen for heating_cooling now)
                mode_desc = "Toggle-Bedienung (Ein/Aus mit 4h-Wiederholung)"
                button_summary = "• Ein/Aus Toggle Switch\n• Automatische Statuswiederholung alle 4 Stunden"
        elif operating_mode == 1:
            mode_desc = "1-Tast-Bedienung (Toggle)"
            button_summary = "• Toggle Button"
        elif operating_mode == 2:
            if receiver_kind == "switch":
                mode_desc = "2-Tast-Bedienung (Ein + Aus)"
                button_summary = "• Ein Button\n• Aus Button"
            elif receiver_kind == "motor":
                mode_desc = "2-Tast-Bedienung (Ein + Aus)"
                button_summary = "• Ein Button\n• Aus Button"
        elif operating_mode == 3:
            mode_desc = "3-Tast-Bedienung (Auf + Zu + Stopp)"
            button_summary = "• Auf Button\n• Zu Button\n• Stopp Button"
        else:
            mode_desc = f"Betriebsmodus {operating_mode}"
            button_summary = "• Standardkonfiguration"
            
        # Learning mode instructions
        if receiver_kind == "heating_cooling":
            learn_instructions = "Versetzen Sie den Empfänger in den Lernmodus für Toggle-Bedienung (Ein/Aus)."
        elif receiver_kind == "motor":
            if operating_mode == 2:
                learn_instructions = "Versetzen Sie den Empfänger in den 2-Tasten Lernmodus (Auf + Ab)."
            elif operating_mode == 3:
                learn_instructions = "Versetzen Sie den Empfänger in den 3-Tasten Lernmodus (Auf + Ab + Stopp)."
            else:
                learn_instructions = f"Versetzen Sie den Empfänger in den {operating_mode}-Tasten Lernmodus."
        else:
            learn_instructions = f"Versetzen Sie den Empfänger in den {operating_mode}-Tasten Lernmodus."
        
        return {
            "data_schema": vol.Schema({
                vol.Required("action"): vol.In({
                    "confirm": "✅ Bestätigen und Code A senden",
                    "cancel": "❌ Abbrechen"
                })
            }),
            "description_placeholders": {
                "device_name": name,
                "serial": serial[-12:] if serial else "Unbekannt",
                "rx11_index": str(rx11_index),
                "receiver_kind": receiver_kind.replace("_", "/").title(),
                "operating_mode": mode_desc,
                "button_summary": button_summary,
                "learn_instructions": learn_instructions,
                "instructions": (
                    f"📥 **EW-Receiver Konfiguration - Schritt 3/3**\n\n"
                    f"**📋 Konfiguration:**\n"
                    f"• Gerät: {name}\n"
                    f"• Seriennummer: {serial[-12:] if serial else 'Unbekannt'}\n"
                    f"• RX11-Index: {rx11_index}\n"
                    f"• Gerätetyp: {receiver_kind.replace('_', '/').title()}\n"
                    f"• Betriebsart: {mode_desc}\n\n"
                    f"**🎛️ Zu erstellende Buttons:**\n"
                    f"{button_summary}\n\n"
                    f"**📡 Lernvorgang:**\n\n"
                    f"**1.** {learn_instructions}\n"
                    f"**2.** Klicken Sie auf 'Bestätigen und Code A senden'\n"
                    f"**3.** Das System sendet Code A zum Einlernen an den Empfänger\n"
                    f"**4.** Das Gerät wird in Home Assistant angelegt\n\n"
                    f"💡 **Hinweis:** Nach der Bestätigung wird der Empfänger automatisch konfiguriert und die entsprechenden Button-Entitäten in Home Assistant erstellt."
                )
            }
        }

    async def async_step_device_ewneo_receiver(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure EWneo receiver device with learning process."""
        # Direkt zum Einlernen springen - kein Gerätetypenauswahl vorher
        return await self.async_step_device_ewneo_receiver_learn()

    async def async_step_device_ewneo_receiver_learn(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Perform the EWneo receiver preparation (index, serial, filter setup)."""
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

            # Step 1: Get next available EWneo index from coordinator's persistent tracking
            _LOGGER.info("🔍 Getting next available EWneo index...")
            try:
                ewneo_index = await coordinator.get_next_free_ewb_index()
            except ValueError:
                # All 256 indices are used
                return self.async_abort(reason="no_available_ewneo_index")
                
            _LOGGER.info("✅ Using EWneo index: %d", ewneo_index)

            # Step 2: Get gateway serial number for this index
            # Note: The RX11 has a fixed gateway serial for each index, even if not yet joined
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

            # Store preparation data for next step
            self._ewneo_preparation = {
                "coordinator": coordinator,
                "wrapper": wrapper, 
                "ewneo_index": ewneo_index,
                "gateway_serial": gateway_serial
            }
            
            _LOGGER.info("✅ EWneo preparation completed - Index: %d, Gateway: %s", 
                        ewneo_index, gateway_serial[-8:])
            
            # Go to learning mode instruction step
            return await self.async_step_device_ewneo_receiver_learning_mode()

        except Exception as e:
            _LOGGER.error("Error during EWneo receiver preparation: %s", e)
            return self.async_abort(reason="ewneo_learning_failed")

    async def async_step_device_ewneo_receiver_learning_mode(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Instruct user to put EWneo transceiver into learning mode."""
        if user_input is not None:
            if user_input.get("action") == "proceed":
                # User confirmed device is in learning mode, proceed with join
                return await self.async_step_device_ewneo_receiver_join()
            else:
                # User cancelled
                return self.async_abort(reason="user_cancelled")
        
        if not hasattr(self, '_ewneo_preparation') or not self._ewneo_preparation:
            return self.async_abort(reason="no_preparation_data")
            
        ewneo_index = self._ewneo_preparation["ewneo_index"]
        gateway_serial = self._ewneo_preparation["gateway_serial"]
        
        return self.async_show_form(
            step_id="device_ewneo_receiver_learning_mode",
            data_schema=vol.Schema({
                vol.Required("action", default="proceed"): vol.In({
                    "proceed": "✅ Bereit - Gerät ist im Lernmodus",
                    "cancel": "❌ Abbrechen"
                })
            }),
            description_placeholders={
                "instructions": (
                    f"**🔧 EWneo-Transceiver in Lernmodus versetzen**\n\n"
                    f"**📋 Vorbereitung abgeschlossen:**\n"
                    f"• EWneo-Index: `{ewneo_index}`\n"
                    f"• Gateway-Serial: `{gateway_serial[-8:]}`\n"
                    f"• Empfangsfilter: ✅ Gesetzt\n\n"
                    f"**⚡ Jetzt erforderlich:**\n\n"
                    f"1. **Versetzen Sie den EWneo-Transceiver in den Lernmodus:**\n"
                    f"   • Drücken Sie die **Lerntaste** am EWneo-Gerät\n"
                    f"   • Die **LED sollte blinken** oder anders signalisieren\n"
                    f"   • Der Transceiver wartet nun auf Verbindungsaufbau\n\n"
                    f"2. **Bestätigen Sie, wenn das Gerät bereit ist:**\n"
                    f"   • Klicken Sie '✅ Bereit' wenn die Lern-LED aktiv ist\n"
                    f"   • Das System führt dann `EWB_JOIN_DEVICE` aus\n\n"
                    f"**⚠️ Wichtig:** Der Lernmodus ist zeitlich begrenzt. "
                    f"Stellen Sie sicher, dass das Gerät bereit ist, bevor Sie fortfahren."
                )
            }
        )

    async def async_step_device_ewneo_receiver_join(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Execute the EWB_JOIN_DEVICE command and handle the result."""
        try:
            if not hasattr(self, '_ewneo_preparation') or not self._ewneo_preparation:
                return self.async_abort(reason="no_preparation_data")
                
            coordinator = self._ewneo_preparation["coordinator"]
            wrapper = self._ewneo_preparation["wrapper"]
            ewneo_index = self._ewneo_preparation["ewneo_index"]
            gateway_serial = self._ewneo_preparation["gateway_serial"]

            # Execute EWB_JOIN_DEVICE
            _LOGGER.info("🔗 Starting EWneo receiver join process...")
            join_result = await coordinator.transceiver.rx11_ewb_join_device(gateway_serial)
            
            if not join_result:
                return self.async_abort(reason="ewneo_join_failed")
                
            device_type_code, receiver_serial = join_result
            _LOGGER.info("✅ EWneo receiver joined - Type: 0x%02X, Serial: %s", device_type_code, receiver_serial[-8:])

            # Map device type code to device type string
            from .const import DEVICE_TYPES
            device_type_name = DEVICE_TYPES.get(device_type_code, f"unknown_0x{device_type_code:02X}")
            
            # Store the learned device data for final confirmation
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
                "is_learn_telegram": True
            }
            
            _LOGGER.info("🎯 EWneo-Receiver joined successfully (Index: %d, Type: 0x%02X)", 
                        ewneo_index, device_type_code)
            
            # Mark the EWB index as used persistently in coordinator
            # This also updates the wrapper tracking automatically
            device_name = f"EWneo-{device_type_name} ({receiver_serial[-6:]})"
            coordinator.mark_ewb_index_used(ewneo_index, gateway_serial, receiver_serial, device_name)
            
            # Clean up preparation data
            delattr(self, '_ewneo_preparation')
            
            # Go to final confirmation step
            return await self.async_step_device_ewneo_receiver_confirm()

        except Exception as e:
            _LOGGER.error("Error during EWneo receiver join: %s", e)
            return self.async_abort(reason="ewneo_join_failed")

    async def async_step_device_ewneo_receiver_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Confirm the learned EWneo device type and entity configuration."""
        if user_input is not None:
            # User confirmed the device - use device type determined from EwbJoinDeviceRequest
            device_name = user_input.get("device_name")
            device_type_code = self._learned_device.get("device_type_code")
            
            # Determine entity type based on device type code from EwbJoinDeviceRequest
            if device_type_code == 0x04:  # EWB_DT_DIMMER
                entity_type = "light"
                # For dimmer devices, enable dimming support by default
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
            
            # Update the learned device with final configuration
            self._learned_device.update({
                "entity_type": entity_type,
                "name": device_name,
                "supports_dimming": supports_dimming,
                "supports_color": supports_color,
            })
            
            _LOGGER.info("✅ EWneo device confirmed: %s as %s (type_code: 0x%02X)", 
                        device_name, entity_type, device_type_code)
            return await self.async_step_device_save()

        if not self._learned_device:
            return self.async_abort(reason="no_learned_device")

        # Extract learned device information
        device_type_code = self._learned_device.get("device_type_code")
        device_type_name = self._learned_device.get("device_type_name")
        serial_number = self._learned_device.get("serial_number")
        ewneo_index = self._learned_device.get("ewneo_index")
        
        # Suggest default name and entity type based on device type
        suggested_name = f"EWneo-Gerät ({serial_number[-6:]})"
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
                suggested_name = f"EWneo-Schalter ({serial_number[-6:]})"
            else:  # Unknown device type
                suggested_entity_type = "switch"
                suggested_name = f"EWneo-Gerät ({serial_number[-6:]})"

        return self.async_show_form(
            step_id="device_ewneo_receiver_confirm",
            data_schema=vol.Schema({
                vol.Required("device_name", default=suggested_name): str,
            }),
            description_placeholders={
                "instructions": (
                    f"**📋 EWneo-Receiver erfolgreich eingelernt**\n\n"
                    f"**🔍 Erkannte Geräteinformationen:**\n"
                    f"• Seriennummer: `{serial_number[-8:]}`\n"
                    f"• EWneo-Index: `{ewneo_index}`\n"
                    f"• Gerätetyp: `{device_type_name}` (Code: 0x{device_type_code:02X})\n"
                    f"• Entity-Typ: `{suggested_entity_type}` (automatisch bestimmt)\n\n"
                    f"**ℹ️ Der Entity-Typ wird automatisch anhand des EwbJoinDeviceRequests bestimmt:**\n"
                    f"• 0x04: Dimmer-Entität (EWB_DT_DIMMER)\n"
                    f"• 0x05, 0x08, 0x09: Motor-Entität (EWB_DT_MOTOR/DUAL/QUAD)\n"
                    f"• 0x03, 0x06, 0x07: Schalter-Entität (EWB_DT_SWITCH/DUAL/QUAD)\n"
                    f"• Andere: Standard Schalter-Entität\n\n"
                    f"**⚙️ Gerätename bestätigen:**\n"
                    f"Bitte bestätigen Sie den Gerätenamen. Der Entity-Typ wird automatisch gesetzt."
                )
            }
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
                    sw_version=device_data.get("firmware_version"),
                )
                _LOGGER.info("✅ Device registered in HA Device Registry: %s (ID: %s)", device_name, device_entry.id)
                
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
        
        # Build success message
        if created_count == 0 and entity_count > 0:
            description = (
                f"✅ **Gerät gespeichert:** {device_name} ({serial})\n\n"
                f"⚠️ **Entities werden nach Neustart angezeigt**\n\n"
                f"🔄 Bitte starten Sie Home Assistant neu, damit die {entity_count} Entities erscheinen."
            )
        else:
            description = (
                f"✅ **Gerät hinzugefügt:** {device_name} ({serial})\n\n"
                f"📊 **{created_count} Entities erstellt**"
            )
        
        # Abort with success message
        return self.async_abort(
            reason="device_added",
            description_placeholders={
                "device_name": device_name,
                "serial_number": serial,
                "description": description
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
        
        _LOGGER.info("🔍 _determine_device_entities: device_type='%s', delegating to entity_specs", device_type)
        
        # Get entity specs from centralized function
        entity_specs = create_entity_specs_for_device(serial_number, device_data)
        
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
            if receiver_kind == "motor" or receiver_kind == "cover":
                device_class = "garage"
                category = "cover"
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
            "enable_learning": "🟢 Lernmodus aktivieren" if not learning_active else "",
            "disable_learning": "🛑 Lernmodus deaktivieren" if learning_active else "",
            "manage_devices": f"🔧 Geräte verwalten ({device_count} Geräte)",
            "save_settings": "💾 Speichern"
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
                "learning_status": "aktiv" if learning_active else "inaktiv",
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
            device_name = device_info.get("name", f"Gerät {serial[-6:]}")
            device_type = device_info.get("type", "unknown")
            device_options[serial] = f"{device_name} ({device_type})"

        if not device_options:
            # No devices to manage
            data_schema = vol.Schema({
                vol.Required("action", default="back"): vol.In({"back": "🔙 Zurück"})
            })
        else:
            data_schema = vol.Schema({
                vol.Required("action", default="back"): vol.In({
                    "back": "🔙 Zurück",
                    "remove_device": "🗑️ Gerät entfernen"
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