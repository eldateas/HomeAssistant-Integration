"""Base entities for the Easywave integration."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import (
    CONF_ACTUATOR_SERIAL,
    CONF_BUTTON_COUNT,
    CONF_DEVICE_TYPE_CODE,
    CONF_GROUPING_MODE,
    CONF_OPERATING_TYPE,
    CONF_RECEIVER_KIND,
    CONF_RECEIVER_SERIAL,
    CONF_RX11_INDEX,
    CONF_SENSOR_CAPABILITIES,
    CONF_SENSOR_SERIAL,
    CONF_SWITCH_MODE,
    CONF_TRANSMITTER_SERIAL,
    DOMAIN,
    TRANSMITTER_GROUPING_GROUP,
    TRANSMITTER_SWITCH_PERMANENT,
    ewneo_device_type_label,
)

if TYPE_CHECKING:
    from . import EasywaveConfigEntry
    from .coordinator import EasywaveCoordinator


@dataclass
class EasywaveDeviceEntry:
    """Device configuration stored as a gateway config subentry."""

    device_id: str
    title: str
    data: dict[str, Any]
    subentry_id: str


def _transmitter_model(data: dict[str, Any]) -> str:
    """Return a human-readable model string describing the transmitter configuration."""
    op = str(data.get(CONF_OPERATING_TYPE, "1"))
    parts: list[str] = []
    if op == "1":
        parts.append("1-Button Operation")
        count = data.get(CONF_BUTTON_COUNT, 1)
        parts.append(f"{count} Button{'s' if count != 1 else ''}")
        grouping = data.get(CONF_GROUPING_MODE, TRANSMITTER_GROUPING_GROUP)
        parts.append(
            "Group" if grouping == TRANSMITTER_GROUPING_GROUP else "Individual"
        )
        mode = data.get(CONF_SWITCH_MODE, "")
        parts.append("Permanent" if mode == TRANSMITTER_SWITCH_PERMANENT else "Impulse")
    elif op == "2":
        parts.append("2-Button Operation")
        parts.append(str(data.get("usage_type") or "switch").title())
    elif op == "3":
        parts.append("3-Button Cover Operation")
    return ", ".join(parts) or "Easywave Transmitter"


def _neo_sensor_model(data: dict[str, Any]) -> str:
    """Return a human-readable model string for an EWneo sensor."""
    capabilities = data.get(CONF_SENSOR_CAPABILITIES, 0)
    parts = ["Easywave neo sensor"]
    if (capabilities >> 4) & 1:
        parts.append("Temperature")
    if (capabilities >> 5) & 1:
        parts.append("Humidity")
    if (capabilities >> 6) & 1:
        parts.append("Wind")
    if (capabilities >> 7) & 1:
        parts.append("Rain")
    return ", ".join(parts)


def _receiver_model(data: dict[str, Any]) -> str:
    """Return a human-readable model for an EW receiver."""
    kind = str(data.get(CONF_RECEIVER_KIND, "impulse")).replace("_", " ")
    return f"Easywave Receiver ({kind})"


def _actuator_model(data: dict[str, Any]) -> str:
    """Return a human-readable model for an EWneo actuator."""
    code = int(data.get(CONF_DEVICE_TYPE_CODE, 0))
    label = ewneo_device_type_label(code, "en")
    return f"Easywave neo {label}"


class EasywaveTransmitterEntity(Entity):
    """Base entity for an Easywave transmitter."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: EasywaveConfigEntry,
        device: EasywaveDeviceEntry,
        unique_id_suffix: str,
    ) -> None:
        """Initialize the transmitter entity."""
        self._entry = entry
        self._transmitter_serial: str = device.data[CONF_TRANSMITTER_SERIAL]
        self._device_id: str = device.device_id
        self._device_data = device.data

        self._attr_unique_id = f"{device.device_id}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.title,
            manufacturer="ELDAT",
            model=_transmitter_model(device.data),
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def _coordinator(self) -> EasywaveCoordinator:
        """Return the coordinator from the shared runtime data."""
        return self._entry.runtime_data.coordinator

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates and register for telegram dispatch."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
        coordinator = self._coordinator
        coordinator.register_transmitter_entities([self])
        self.async_on_remove(lambda: coordinator.unregister_transmitter_entity(self))

    @property
    def transmitter_serial(self) -> str:
        """Return the transmitter serial for matching telegrams."""
        return self._transmitter_serial

    @property
    def device_id(self) -> str:
        """Return the device id (used for device identifier lookup)."""
        return self._device_id

    @override
    @property
    def available(self) -> bool:
        """Return if entity is available (transceiver connected)."""
        return self._coordinator.transceiver.is_connected

    def handle_battery_status(self, is_low: bool) -> None:
        """Handle a battery status update from a PUSH telegram."""

    def handle_telegram(self, event: Any) -> None:
        """Handle an incoming transmitter telegram."""


class EasywaveNeoSensorEntity(Entity):
    """Base entity for an Easywave neo sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: EasywaveConfigEntry,
        device: EasywaveDeviceEntry,
        unique_id_suffix: str,
    ) -> None:
        """Initialize the neo sensor entity."""
        self._entry = entry
        self._sensor_serial: str = device.data[CONF_SENSOR_SERIAL]
        self._device_id: str = device.device_id

        self._attr_unique_id = f"{device.device_id}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.title,
            manufacturer="ELDAT",
            model=_neo_sensor_model(device.data),
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def _coordinator(self) -> EasywaveCoordinator:
        """Return the coordinator from the shared runtime data."""
        return self._entry.runtime_data.coordinator

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates and register for telegram dispatch."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
        coordinator = self._coordinator
        coordinator.register_sensor_entities([self])
        self.async_on_remove(lambda: coordinator.unregister_sensor_entity(self))

    @property
    def sensor_serial(self) -> str:
        """Return the sensor serial for matching telegrams."""
        return self._sensor_serial

    @override
    @property
    def available(self) -> bool:
        """Return if entity is available (transceiver connected)."""
        return self._coordinator.transceiver.is_connected

    def handle_telegram(self, event: Any) -> None:
        """Handle an incoming neo sensor telegram."""
        raise NotImplementedError


class EasywaveReceiverEntity(Entity):
    """Base entity for an Easywave EW receiver (TX from Home Assistant)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: EasywaveConfigEntry,
        device: EasywaveDeviceEntry,
        unique_id_suffix: str,
    ) -> None:
        """Initialize the receiver entity."""
        self._entry = entry
        self._receiver_serial: str = device.data[CONF_RECEIVER_SERIAL]
        self._rx11_index: int = int(device.data[CONF_RX11_INDEX])
        self._device_id: str = device.device_id
        self._device_data = device.data

        self._attr_unique_id = f"{device.device_id}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.title,
            manufacturer="ELDAT",
            model=_receiver_model(device.data),
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def _coordinator(self) -> EasywaveCoordinator:
        """Return the coordinator from the shared runtime data."""
        return self._entry.runtime_data.coordinator

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def receiver_serial(self) -> str:
        """Return the receiver serial hex."""
        return self._receiver_serial

    @property
    def rx11_index(self) -> int:
        """Return the RX11 transmitter index used to send commands."""
        return self._rx11_index

    @property
    def device_id(self) -> str:
        """Return the device id."""
        return self._device_id

    @override
    @property
    def available(self) -> bool:
        """Return if entity is available (transceiver connected)."""
        return self._coordinator.transceiver.is_connected

    async def async_send_button(self, button: int) -> bool:
        """Send an EW button command via the RX11 index serial."""
        return await self._coordinator.async_send_ew_button(self._rx11_index, button)


class EasywaveNeoActuatorEntity(Entity):
    """Base entity for an Easywave neo bidirectional actuator."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: EasywaveConfigEntry,
        device: EasywaveDeviceEntry,
        unique_id_suffix: str,
        *,
        channel: int | None = None,
    ) -> None:
        """Initialize the neo actuator entity."""
        self._entry = entry
        self._actuator_serial: str = device.data[CONF_ACTUATOR_SERIAL]
        self._gateway_serial: str = str(device.data.get("gateway_serial") or "")
        self._device_type_code: int = int(device.data[CONF_DEVICE_TYPE_CODE])
        self._ewneo_index: int = int(device.data.get("ewneo_index", 0))
        self._channel = channel
        self._device_id: str = device.device_id
        self._device_data = device.data
        self._parsed_state: Any = None

        suffix = unique_id_suffix
        if channel is not None:
            suffix = f"{unique_id_suffix}_ch{channel}"
        self._attr_unique_id = f"{device.device_id}_{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.title,
            manufacturer="ELDAT",
            model=_actuator_model(device.data),
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def _coordinator(self) -> EasywaveCoordinator:
        """Return the coordinator from the shared runtime data."""
        return self._entry.runtime_data.coordinator

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates and register for EWB dispatch."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
        coordinator = self._coordinator
        coordinator.register_actuator_entities([self])
        self.async_on_remove(lambda: coordinator.unregister_actuator_entity(self))

    @property
    def actuator_serial(self) -> str:
        """Return the actuator serial hex."""
        return self._actuator_serial

    @property
    def device_type_code(self) -> int:
        """Return the EWB device type code."""
        return self._device_type_code

    @property
    def device_id(self) -> str:
        """Return the device id."""
        return self._device_id

    @override
    @property
    def available(self) -> bool:
        """Return if entity is available (transceiver connected)."""
        return self._coordinator.transceiver.is_connected

    def handle_state(self, state: Any, *, mode: int = 0) -> None:
        """Handle a parsed EWB state update."""
        self._parsed_state = state
        self.async_write_ha_state()

    async def async_change_state(self, command: Any, *, mode: int = 0) -> Any:
        """Send an EWB change-state command and return the parsed response state."""
        return await self._coordinator.async_ewb_change_state(
            gateway_serial=self._gateway_serial,
            actuator_serial=self._actuator_serial,
            device_type_code=self._device_type_code,
            mode=mode,
            command=command,
        )
