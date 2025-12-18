"""Triple button transmitter implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging
from datetime import datetime

from ....base import BaseTransmitter, DeviceType, DeviceSubtype, OperatingMode

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EW_BUTTON_PRESS = 0x04
TELEGRAM_EW_BUTTON_RELEASE = 0x05


class RX11TripleButtonTransmitter(BaseTransmitter):
    """Triple button transmitter implementation for RX11 transceiver.
    
    Handles 3-button EW transmitters with individual button tracking.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize triple button transmitter."""
        super().__init__(*args, device_type=DeviceType.EW_TRANSMITTER, 
                        subtype=DeviceSubtype.TRIPLE_BUTTON, **kwargs)
        self._button_count = 3  # Use private attribute instead of property
        self.operating_mode = OperatingMode.PUSH_BUTTON
        self._button_states = {1: False, 2: False, 3: False}
        self._button_press_times = {1: None, 2: None, 3: None}
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["binary_sensor"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for triple button transmitter."""
        info_type = telegram_data.get("info_type")
        
        if info_type == TELEGRAM_EW_BUTTON_PRESS:
            self._handle_button_press(telegram_data)
        elif info_type == TELEGRAM_EW_BUTTON_RELEASE:
            self._handle_button_release(telegram_data)
        
        return self.get_button_states()
    
    def _handle_button_press(self, telegram_data: Dict[str, Any]) -> None:
        """Handle button press event."""
        data = telegram_data.get("data", {})
        button_id = data.get("button_id")
        
        if button_id and 1 <= button_id <= 3:
            self._button_states[button_id] = True
            self._button_press_times[button_id] = datetime.now()
            self._last_seen = datetime.now()
            _LOGGER.debug("Triple button transmitter %s: Button %d pressed", 
                         self.serial_number[-6:], button_id)
    
    def _handle_button_release(self, telegram_data: Dict[str, Any]) -> None:
        """Handle button release event."""
        data = telegram_data.get("data", {})
        button_id = data.get("button_id")
        
        if button_id and 1 <= button_id <= 3:
            press_duration = None
            if self._button_press_times[button_id]:
                press_duration = (datetime.now() - self._button_press_times[button_id]).total_seconds()
            
            self._button_states[button_id] = False
            self._button_press_times[button_id] = None
            self._last_seen = datetime.now()
            _LOGGER.debug("Triple button transmitter %s: Button %d released (duration: %.2fs)", 
                         self.serial_number[-6:], button_id, press_duration or 0)
    
    def get_button_states(self) -> Dict[str, Any]:
        """Get current button states."""
        return {
            "button_states": self._button_states.copy(),
            "button_count": self.button_count,
            "last_seen": self._last_seen,
        }
    
    def is_button_pressed(self, button_id: int) -> bool:
        """Check if specific button is currently pressed."""
        return self._button_states.get(button_id, False)
    
    def get_press_duration(self, button_id: int) -> Optional[float]:
        """Get current press duration for a button."""
        if self._button_press_times.get(button_id):
            return (datetime.now() - self._button_press_times[button_id]).total_seconds()
        return None


def create_rx11_triple_button_transmitter(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11TripleButtonTransmitter:
    """Factory function to create triple button transmitter."""
    return RX11TripleButtonTransmitter(
        serial_number=serial_number,
        device_info=device_info,
        **kwargs
    )