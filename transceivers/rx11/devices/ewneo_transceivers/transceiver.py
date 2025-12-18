"""EWneo transceiver implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import BaseReceiver, DeviceType, DeviceSubtype, OperatingMode

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EWNEO_STATE_CHANGE = [0x05, 0xF2]


class RX11EWneoTransceiver(BaseReceiver):
    """EWneo transceiver implementation for RX11 transceiver.
    
    Handles bidirectional communication with EWneo transceivers.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize EWneo transceiver."""
        super().__init__(*args, device_type=DeviceType.EWNEO_TRANSCEIVER, 
                        subtype=DeviceSubtype.TRANSCEIVER, **kwargs)
        self._transceiver_state = {}
        self._setup_operating_mode(**kwargs)
    
    def _setup_operating_mode(self, **kwargs) -> None:
        """Setup operating mode for transceiver."""
        self.operating_mode = OperatingMode.BIDIRECTIONAL
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["sensor", "button"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for EWneo transceiver."""
        info_type = telegram_data.get("info_type")
        
        if info_type in TELEGRAM_EWNEO_STATE_CHANGE:
            self._handle_state_change(telegram_data)
        
        return self.get_transceiver_data()
    
    def _handle_state_change(self, telegram_data: Dict[str, Any]) -> None:
        """Handle state change telegram."""
        data = telegram_data.get("data", {})
        
        # Update transceiver state
        for key, value in data.items():
            self._transceiver_state[key] = value
        
        _LOGGER.debug("EWneo transceiver %s: State updated: %s", 
                     self.serial_number[-6:], data)
    
    def get_transceiver_data(self) -> Dict[str, Any]:
        """Get current transceiver data."""
        return {
            "state": self._transceiver_state.copy(),
            "last_seen": self._last_seen,
        }
    
    async def send_command(self, command: str, **kwargs) -> bool:
        """Send command to EWneo transceiver."""
        try:
            # Implementation depends on RX11 wrapper capabilities
            _LOGGER.info("Sending command %s to EWneo transceiver %s", 
                        command, self.serial_number[-6:])
            # TODO: Implement actual command sending via RX11
            return True
        except Exception as e:
            _LOGGER.error("Failed to send command to EWneo transceiver %s: %s", 
                         self.serial_number[-6:], e)
            return False
    
    async def set_state(self, channel: int, state: Any) -> bool:
        """Set state for a specific channel (abstract method implementation)."""
        try:
            # Store state locally and send command to device
            self._transceiver_state[f"channel_{channel}"] = state
            return await self.send_command(f"set_channel_{channel}", state=state)
        except Exception as e:
            _LOGGER.error("Failed to set state for channel %d: %s", channel, e)
            return False
    
    async def get_state(self, channel: int) -> Any:
        """Get current state for a specific channel (abstract method implementation)."""
        return self._transceiver_state.get(f"channel_{channel}")


def create_rx11_ewneo_transceiver(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11EWneoTransceiver:
    """Factory function to create EWneo transceiver."""
    return RX11EWneoTransceiver(
        serial_number=serial_number,
        device_info=device_info,
        **kwargs
    )