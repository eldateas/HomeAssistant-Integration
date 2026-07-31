"""Base entities for the Easywave integration."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import (
    CONF_ACTUATOR_SERIAL,
    CONF_DEVICE_TYPE_CODE,
    CONF_RECEIVER_SERIAL,
    CONF_RX11_INDEX,
    CONF_SENSOR_SERIAL,
    CONF_TRANSMITTER_SERIAL,
    DOMAIN,
)
from .device_model import (
    actuator_model,
    neo_sensor_model,
    receiver_model,
    transmitter_model,
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


# hass.data[DOMAIN]["pending_areas"][device_id] → one-shot area from learn confirm
_PENDING_AREAS = "pending_areas"


def _hass_language(hass: Any) -> str | None:
    """Return the Home Assistant UI language when available."""
    if hass is None:
        return None
    return getattr(getattr(hass, "config", None), "language", None)


def register_pending_area(
    hass: HomeAssistant, device_id: str, area_id: str
) -> None:
    """Queue an area to apply once when the device registry entry is created."""
    hass.data.setdefault(DOMAIN, {}).setdefault(_PENDING_AREAS, {})[device_id] = (
        area_id
    )


def _pop_pending_area(hass: HomeAssistant, device_id: str) -> str | None:
    """Return and clear a pending learn-flow area for ``device_id``."""
    pending = hass.data.get(DOMAIN, {}).get(_PENDING_AREAS)
    if not isinstance(pending, dict):
        return None
    area_id = pending.pop(device_id, None)
    return area_id if isinstance(area_id, str) and area_id else None


def _finalize_device_registry(hass: HomeAssistant, device_id: str) -> None:
    """Clear manufacturer and apply a one-shot pending area from the learn flow."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, device_id)})
    if device is None:
        return

    updates: dict[str, Any] = {}
    if device.manufacturer is not None:
        updates["manufacturer"] = None

    area_id = _pop_pending_area(hass, device_id)
    if area_id and device.area_id is None:
        updates["area_id"] = area_id

    if updates:
        device_registry.async_update_device(device.id, **updates)


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
        self._device_title = device.title

        self._attr_unique_id = f"{device.device_id}_{unique_id_suffix}"
        self._attr_device_info = self._build_device_info(language=None)

    def _build_device_info(self, language: str | None) -> DeviceInfo:
        """Build DeviceInfo without exposing the radio serial number."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._device_title,
            model=transmitter_model(self._device_data, language),
            via_device=(DOMAIN, self._entry.entry_id),
        )

    @property
    def _coordinator(self) -> EasywaveCoordinator:
        """Return the coordinator from the shared runtime data."""
        return self._entry.runtime_data.coordinator

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates and register for telegram dispatch."""
        self._attr_device_info = self._build_device_info(_hass_language(self.hass))
        await super().async_added_to_hass()
        _finalize_device_registry(self.hass, self._device_id)
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
        self._device_data = device.data
        self._device_title = device.title

        self._attr_unique_id = f"{device.device_id}_{unique_id_suffix}"
        self._attr_device_info = self._build_device_info(language=None)

    def _build_device_info(self, language: str | None) -> DeviceInfo:
        """Build DeviceInfo without exposing the radio serial number."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._device_title,
            model=neo_sensor_model(language),
            via_device=(DOMAIN, self._entry.entry_id),
        )

    @property
    def _coordinator(self) -> EasywaveCoordinator:
        """Return the coordinator from the shared runtime data."""
        return self._entry.runtime_data.coordinator

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates and register for telegram dispatch."""
        self._attr_device_info = self._build_device_info(_hass_language(self.hass))
        await super().async_added_to_hass()
        _finalize_device_registry(self.hass, self._device_id)
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
        self._device_title = device.title

        self._attr_unique_id = f"{device.device_id}_{unique_id_suffix}"
        self._attr_device_info = self._build_device_info(language=None)

    def _build_device_info(self, language: str | None) -> DeviceInfo:
        """Build DeviceInfo without exposing the radio serial number."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._device_title,
            model=receiver_model(self._device_data, language),
            via_device=(DOMAIN, self._entry.entry_id),
        )

    @property
    def _coordinator(self) -> EasywaveCoordinator:
        """Return the coordinator from the shared runtime data."""
        return self._entry.runtime_data.coordinator

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self._attr_device_info = self._build_device_info(_hass_language(self.hass))
        await super().async_added_to_hass()
        _finalize_device_registry(self.hass, self._device_id)
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
        self._device_title = device.title
        self._parsed_state: Any = None

        suffix = unique_id_suffix
        if channel is not None:
            suffix = f"{unique_id_suffix}_ch{channel}"
        self._attr_unique_id = f"{device.device_id}_{suffix}"
        self._attr_device_info = self._build_device_info(language=None)

    def _build_device_info(self, language: str | None) -> DeviceInfo:
        """Build DeviceInfo without exposing the radio serial number."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._device_title,
            model=actuator_model(self._device_data, language),
            via_device=(DOMAIN, self._entry.entry_id),
        )

    @property
    def _coordinator(self) -> EasywaveCoordinator:
        """Return the coordinator from the shared runtime data."""
        return self._entry.runtime_data.coordinator

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates and register for EWB dispatch."""
        self._attr_device_info = self._build_device_info(_hass_language(self.hass))
        await super().async_added_to_hass()
        _finalize_device_registry(self.hass, self._device_id)
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
