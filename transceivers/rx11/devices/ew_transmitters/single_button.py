"""Single button transmitter implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging
from datetime import datetime

from ....base import BaseTransmitter, DeviceType, DeviceSubtype, OperatingMode

_LOGGER = logging.getLogger(__name__)

# Telegram type constants
TELEGRAM_BUTTON_RELEASE = 0x00
TELEGRAM_BUTTON_PUSH = 0x01


class RX11SingleButtonTransmitter(BaseTransmitter):
    """Single button transmitter implementation for RX11 transceiver.
    
    Handles EW transmitters with one button (Button A).
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize single button transmitter."""
        super().__init__(*args, device_type=DeviceType.EW_TRANSMITTER, 
                        subtype=DeviceSubtype.SINGLE_BUTTON, 
                        operating_mode=OperatingMode.ONE_BUTTON,
                        button_count=1, **kwargs)
        self._button_press_times = {}
        self._button_last_actions = {}
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["binary_sensor", "device_trigger"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for single button transmitter."""
        info_type = telegram_data.get("info_type")
        button = telegram_data.get("button", 0)
        current_time = datetime.now()
        
        if info_type == TELEGRAM_BUTTON_RELEASE:
            self._handle_button_release(button, current_time, telegram_data)
        elif info_type == TELEGRAM_BUTTON_PUSH:
            self._handle_button_push(button, current_time, telegram_data)
        
        return self.get_button_data()
    
    def _handle_button_push(self, button: int, timestamp: datetime, telegram_data: Dict[str, Any]) -> None:
        """Handle button push event."""
        if button == 0:  # Only button A for single button transmitter
            self._button_press_times[button] = timestamp
            self._button_last_actions[button] = 'push'
            self.set_button_state(button, True)
            
            _LOGGER.debug("EW Single Button Transmitter %s button A pushed", 
                        self.serial_number[-6:])
    
    def _handle_button_release(self, button: int, timestamp: datetime, telegram_data: Dict[str, Any]) -> None:
        """Handle button release event."""
        if button == 0:  # Only button A for single button transmitter
            self._button_last_actions[button] = 'release'
            self.set_button_state(button, False)
            
            # Calculate press duration if we have push time
            press_duration = self._calculate_press_duration(button, timestamp)
            
            _LOGGER.debug("EW Single Button Transmitter %s button A released (duration: %.0fms)", 
                        self.serial_number[-6:], press_duration or 0)
    
    def _calculate_press_duration(self, button: int, release_time: datetime) -> Optional[float]:
        """Calculate press duration and cleanup press time."""
        if button in self._button_press_times:
            duration = (release_time - self._button_press_times[button]).total_seconds() * 1000
            del self._button_press_times[button]
            return duration
        return None
    
    def get_button_press_duration(self, button: int) -> Optional[float]:
        """Get current press duration for a button in milliseconds."""
        if button in self._button_press_times:
            return (datetime.now() - self._button_press_times[button]).total_seconds() * 1000
        return None
    
    def get_button_data(self) -> Dict[str, Any]:
        """Get current button data for coordinator."""
        data = {}
        
        # Add button A state
        button_state = self.get_button_state(0)
        if button_state is not None:
            data["button_a_state"] = button_state
        
        # Add last action
        last_action = self._button_last_actions.get(0)
        if last_action:
            data["button_a_last_action"] = last_action
        
        return data
    
    @property
    def button_labels(self) -> List[str]:
        """Get list of button labels."""
        return ["A"]


def create_rx11_single_button_transmitter(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11SingleButtonTransmitter:
    """Factory function to create single button transmitter."""
    return RX11SingleButtonTransmitter(
        serial_number, 
        name=device_info.get('name'),
        **kwargs
    )