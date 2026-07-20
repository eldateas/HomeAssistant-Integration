"""Thin Home Assistant wrapper around easywave_home_control.EasywaveGateway.

Connection management, health checks, and protocol handling live in the
easywave-home-control library. This module only adapts the gateway API to
Home Assistant callbacks and entity lifecycle.
"""

from collections.abc import Callable
import logging
from typing import Any

from easywave_home_control import (
    EasywaveGateway,
    GatewayCallbacks,
    GatewayConfig,
    GatewayInfo,
)
from easywave_home_control.codec import EwbRcvEvent, EwbState

from homeassistant.core import HomeAssistant

from .const import SUPPORTED_USB_IDS

_LOGGER = logging.getLogger(__name__)


class RX11Transceiver:
    """Thin Home Assistant wrapper around easywave_home_control.EasywaveGateway."""

    def __init__(self, hass: HomeAssistant, device_path: str | None = None) -> None:
        """Initialize the RX11 gateway wrapper."""
        self.hass = hass
        self._disconnect_callback: Callable[[], None] | None = None
        self._connected_callback: Callable[[], None] | None = None
        self._gateway = EasywaveGateway(
            GatewayConfig(
                transceiver_id="RX11",
                port=device_path,
                usb_ids=SUPPORTED_USB_IDS,
                auto_reconnect=False,
                auto_listen=False,
            ),
            callbacks=GatewayCallbacks(
                on_connected=self._notify_connected,
                on_disconnected=self._notify_disconnect,
            ),
        )
        self._gateway_serial_cache: dict[int, bytes] = {}

    @property
    def is_connected(self) -> bool:
        """Return whether the gateway is connected."""
        return self._gateway.is_connected

    @property
    def device_path(self) -> str | None:
        """Return the serial device path."""
        return self._gateway.device_path

    @property
    def usb_serial_number(self) -> str | None:
        """Return the USB serial number of the connected stick."""
        return self._gateway.usb_serial_number

    @property
    def hw_version(self) -> str | None:
        """Return the hardware version reported by the transceiver."""
        return self._gateway.hw_version

    @property
    def fw_version(self) -> str | None:
        """Return the firmware version reported by the transceiver."""
        return self._gateway.fw_version

    @property
    def gateway(self) -> EasywaveGateway:
        """Return the underlying library gateway."""
        return self._gateway

    def set_disconnect_callback(self, callback: Callable[[], None] | None) -> None:
        """Register a callback for connection loss."""
        self._disconnect_callback = callback

    def set_connected_callback(self, callback: Callable[[], None] | None) -> None:
        """Register a callback for successful connection."""
        self._connected_callback = callback

    def _notify_connected(self, _info: GatewayInfo) -> None:
        """Forward library connect events to the integration callback."""
        if not self._connected_callback:
            return
        try:
            self.hass.loop.call_soon_threadsafe(self._connected_callback)
        except (OSError, RuntimeError) as err:
            _LOGGER.error("Error in connected callback: %s", err)

    def _notify_disconnect(self) -> None:
        """Forward library disconnect events to the integration callback."""
        if not self._disconnect_callback:
            return
        try:
            self.hass.loop.call_soon_threadsafe(self._disconnect_callback)
        except (OSError, RuntimeError) as err:
            _LOGGER.error("Error in disconnect callback: %s", err)

    async def connect(self) -> bool:
        """Connect to the RX11 transceiver."""
        return await self._gateway.connect()

    async def disconnect(self) -> None:
        """Disconnect from the RX11 transceiver."""
        await self._gateway.disconnect()

    async def dispose(self) -> None:
        """Stop the gateway and release resources."""
        await self._gateway.stop()

    async def reconnect(self) -> bool:
        """Reconnect to the RX11 transceiver."""
        return await self._gateway.reconnect()

    async def receive_telegram(self, timeout: float = 30.0) -> EwbRcvEvent | None:
        """Wait for an EW/EWneo telegram."""
        return await self._gateway.ew.receive_ex(timeout=timeout)

    async def cancel_pending_receives(self) -> None:
        """Cancel pending receive requests on the hardware."""
        await self._gateway.cancel_pending_receives()

    async def get_ew_gateway_serial(self, index: int) -> bytes | None:
        """Return (and cache) the EW transmitter serial for an RX11 index."""
        if index in self._gateway_serial_cache:
            return self._gateway_serial_cache[index]
        serial = await self._gateway.ew.get_gateway_serial(index)
        if serial is not None:
            self._gateway_serial_cache[index] = serial
        return serial

    async def get_ewb_gateway_serial(self, index: int) -> bytes | None:
        """Return the EWB gateway serial for an index."""
        return await self._gateway.ewb.get_gateway_serial(index)

    async def send_ew_command(self, index: int, button: int) -> bool:
        """Send an EW button command using the serial of the given RX11 index."""
        gateway = await self.get_ew_gateway_serial(index)
        if gateway is None:
            _LOGGER.warning("No EW serial allocated for index %s", index)
            return False
        return await self._gateway.ew.send_command(gateway, button)

    async def ewb_join_device(
        self, gateway: bytes, *, timeout: float = 30.0
    ) -> tuple[int, bytes] | None:
        """Pair an EWneo actuator; returns (device_type, receiver_serial)."""
        return await self._gateway.ewb.join_device(gateway, timeout=timeout)

    async def ewb_change_state(
        self,
        gateway: bytes,
        receiver: bytes,
        device_type: int,
        mode: int,
        command: Any,
    ) -> EwbState | None:
        """Send an EWB change-state command and return the parsed recent state."""
        result = await self._gateway.ewb.change_state(
            gateway, receiver, device_type, mode, command
        )
        if result is None:
            return None
        _error_code, state = result
        return state

    async def ewb_query_state(
        self,
        gateway: bytes,
        receiver: bytes,
        device_type: int,
        mode: int = 0,
    ) -> EwbState | None:
        """Query EWB state for a neo actuator."""
        result = await self._gateway.ewb.query_state(
            gateway, receiver, device_type, mode
        )
        if result is None:
            return None
        _error_code, state = result
        return state
