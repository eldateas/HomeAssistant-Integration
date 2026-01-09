"""EWneo transceiver implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import DeviceType, DeviceSubtype, OperatingMode
from .base import EWneoBaseDevice

_LOGGER = logging.getLogger(__name__)


class RX11EWneoTransceiver(EWneoBaseDevice):
    """EWneo transceiver implementation for RX11 transceiver.
    
    Handles bidirectional communication with EWneo transceivers.
    Inherits common EWB state parsing from EWneoBaseDevice.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize EWneo transceiver."""
        super().__init__(*args, device_type=DeviceType.EWNEO_TRANSCEIVER, 
                        subtype=DeviceSubtype.TRANSCEIVER, **kwargs)
        # Note: self._mode is now in base class
        
        self._transceiver_state = {}
        self._setup_operating_mode(**kwargs)
    
    def _setup_operating_mode(self, **kwargs) -> None:
        """Setup operating mode for transceiver."""
        self.operating_mode = OperatingMode.BIDIRECTIONAL
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["sensor", "button"]
    
    def _parse_mode0_state(self, state_word: int) -> None:
        """Parse Mode 0 state for transceiver (implements abstract method).
        
        Transceivers may have custom state formats depending on their type.
        This implementation stores the raw state word for generic handling.
        """
        # Store raw state for generic transceiver handling
        self._transceiver_state["raw_state"] = state_word
        
        _LOGGER.debug("EWneo transceiver %s: State word=0x%08X", 
                     self.serial_number[-6:], state_word)
    
    def _get_device_data(self) -> Dict[str, Any]:
        """Get transceiver data for coordinator (implements abstract method)."""
        return self.get_transceiver_data()
    
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
    name: str = None,
    **kwargs
) -> Optional[RX11EWneoTransceiver]:
    """Create RX11 EWneo transceiver instance."""
    try:
        return RX11EWneoTransceiver(
            serial_number=serial_number,
            name=name or f"EWneo Transceiver {serial_number[-6:]}",
            **kwargs
        )
    except Exception as e:
        _LOGGER.error(f"Error creating EWneo transceiver: {e}")
        return None