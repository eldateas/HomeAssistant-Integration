"""Binary sensor platform — HACS-style transmitter cover state."""

from typing import Any, override

from easywave_home_control.codec import ButtonPushEvent, SensorMeasurementPayload

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import EasywaveConfigEntry
from .battery_migration import consume_battery_seed
from .const import (
    CONF_BUTTON_COUNT,
    CONF_ENTRY_TYPE,
    ENTRY_TYPE_NEO_SENSOR,
    ENTRY_TYPE_TRANSMITTER,
    EVENT_TYPE_BATTERY_LOW,
    EVENT_TYPE_BATTERY_NORMAL,
    EVENT_TYPE_CLOSED,
    EVENT_TYPE_OPENED,
    EasywaveTransmitterFeature,
)
from .devices import get_devices
from .entity import EasywaveDeviceEntry, EasywaveNeoSensorEntity, EasywaveTransmitterEntity
from .transmitter_entities import async_setup_transmitter_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EasywaveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Easywave binary sensors."""
    for device in get_devices(entry):
        entry_type = device.data.get(CONF_ENTRY_TYPE)
        if entry_type == ENTRY_TYPE_TRANSMITTER:
            await async_setup_transmitter_entities(
                hass, entry, device, async_add_entities, "binary_sensor"
            )
        elif entry_type == ENTRY_TYPE_NEO_SENSOR:
            async_add_entities(
                [EasywaveNeoBatteryBinarySensor(entry, device)],
                config_subentry_id=device.subentry_id,
            )


class EasywaveTransmitterCoverStateBinarySensor(
    EasywaveTransmitterEntity, RestoreEntity, BinarySensorEntity
):
    """Open/Closed cover state for op-2 cover transmitters (HACS UX)."""

    _attr_device_class = BinarySensorDeviceClass.OPENING

    def __init__(
        self,
        entry: EasywaveConfigEntry,
        device: EasywaveDeviceEntry,
        *,
        pair: str,
    ) -> None:
        """Initialize for button pair ab or cd."""
        self._pair = pair
        button_count = int(device.data.get(CONF_BUTTON_COUNT, 2))
        if pair == "cd":
            suffix = "state_cd"
            self._attr_translation_key = "state_cd"
            self._open_button = 2  # C
            self._close_button = 3  # D
        elif button_count >= 4:
            suffix = "state_ab"
            self._attr_translation_key = "state_ab"
            self._open_button = 0  # A
            self._close_button = 1  # B
        else:
            # Single cover pair → HACS name "State" / "Zustand"
            suffix = "state"
            self._attr_translation_key = "transmitter_state_cover"
            self._open_button = 0  # A
            self._close_button = 1  # B
        super().__init__(entry, device, suffix)
        self._attr_is_on = False  # on = open
        self._attr_supported_features = int(
            EasywaveTransmitterFeature.COVER_OPEN
            | EasywaveTransmitterFeature.COVER_CLOSE
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Restore cover open/closed."""
        if (last := await self.async_get_last_state()) is not None:
            self._attr_is_on = last.state == "on"
        await super().async_added_to_hass()

    @override
    @property
    def icon(self) -> str:
        """Return shutter icon matching open/closed (HACS UX)."""
        if self._attr_is_on:
            return "mdi:window-shutter-open"
        return "mdi:window-shutter"

    @override
    def handle_telegram(self, event: Any) -> None:
        """Map A/C → open, B/D → closed."""
        if not isinstance(event, ButtonPushEvent):
            return
        if event.button == self._open_button:
            self._attr_is_on = True
            self.async_write_ha_state()
            self._coordinator.fire_device_event(
                self._device_id, EVENT_TYPE_OPENED, subtype="up"
            )
        elif event.button == self._close_button:
            self._attr_is_on = False
            self.async_write_ha_state()
            self._coordinator.fire_device_event(
                self._device_id, EVENT_TYPE_CLOSED, subtype="down"
            )


class _BatteryWarningBinarySensor(RestoreEntity, BinarySensorEntity):
    """Battery warning shown as the device-page battery icon.

    Home Assistant draws that icon only for device class ``battery``.
    On means the battery is low. Clearing it takes two readings that are
    not low.
    """

    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_translation_key = "battery_warning"

    _CLEAR_THRESHOLD = 2

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last on/off state before the first write."""
        if (last := await self.async_get_last_state()) is not None and last.state in (
            "on",
            "off",
        ):
            self._attr_is_on = last.state == "on"
        elif self._initial_low is not None:
            self._attr_is_on = self._initial_low
        await super().async_added_to_hass()

    def _apply_battery_low(self, is_low: bool) -> None:
        """Apply a low flag immediately and clear it after two good readings."""
        if is_low:
            self._ok_streak = 0
            if self._attr_is_on is True:
                return
            self._attr_is_on = True
            self.async_write_ha_state()
            self._coordinator.fire_device_event(
                self._device_id, EVENT_TYPE_BATTERY_LOW, subtype="low"
            )
            return
        if self._attr_is_on is False:
            return
        self._ok_streak += 1
        if self._ok_streak >= self._CLEAR_THRESHOLD:
            self._attr_is_on = False
            self._ok_streak = 0
            self.async_write_ha_state()
            self._coordinator.fire_device_event(
                self._device_id, EVENT_TYPE_BATTERY_NORMAL, subtype="ok"
            )


class EasywaveTransmitterBatteryBinarySensor(
    EasywaveTransmitterEntity, _BatteryWarningBinarySensor
):
    """Battery warning for an Easywave transmitter."""

    def __init__(self, entry: EasywaveConfigEntry, device: EasywaveDeviceEntry) -> None:
        """Initialize the transmitter battery binary sensor."""
        super().__init__(entry, device, "battery_warning")
        self._ok_streak = 0
        self._initial_low = consume_battery_seed(
            entry.runtime_data.coordinator.hass, device.device_id
        )
        self._attr_is_on = None

    @override
    @callback
    def handle_battery_status(self, is_low: bool) -> None:
        """Update from the LOWBAT flag of a PUSH telegram."""
        self._apply_battery_low(is_low)


class EasywaveNeoBatteryBinarySensor(EasywaveNeoSensorEntity, _BatteryWarningBinarySensor):
    """Battery warning for an Easywave neo sensor."""

    def __init__(self, entry: EasywaveConfigEntry, device: EasywaveDeviceEntry) -> None:
        """Initialize the neo sensor battery binary sensor."""
        super().__init__(entry, device, "battery_warning")
        self._ok_streak = 0
        self._initial_low = consume_battery_seed(
            entry.runtime_data.coordinator.hass, device.device_id
        )
        self._attr_is_on = None

    @override
    @callback
    def handle_telegram(self, event: Any) -> None:
        """Update from the Table 6 battery flag on a measurement telegram."""
        payload = getattr(event, "payload", None)
        if not isinstance(payload, SensorMeasurementPayload) or not payload.has_battery:
            return
        self._apply_battery_low(payload.battery_is_weak)
