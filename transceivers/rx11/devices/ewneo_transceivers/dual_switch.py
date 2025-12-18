"""EWneo dual switch implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import BaseReceiver, DeviceType, DeviceSubtype, OperatingMode

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EWNEO_STATE_CHANGE = [0x05, 0xF2]


class RX11EWneoDualSwitch(BaseReceiver):
    """EWneo dual switch implementation for RX11 transceiver.
    
    Handles 2-channel EWneo switch devices with bidirectional communication.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize EWneo dual switch."""
        super().__init__(*args, device_type=DeviceType.EWNEO_DUAL_SWITCH, 
                        subtype=DeviceSubtype.DUAL_SWITCH, **kwargs)
        self._switch_states = {1: False, 2: False}
        self._mode = 0  # Should always be 0 for dual switch
        self._switch_counters = {1: 0, 2: 0}  # Counters for each channel
        self._switch_reasons = {1: 1, 2: 1}  # Reasons for each channel (1=off, 2=on, 3=timer, 5=logic)
        self._setup_operating_mode(**kwargs)
    
    def _setup_operating_mode(self, **kwargs) -> None:
        """Setup operating mode for dual switch."""
        self.operating_mode = OperatingMode.DUAL_CHANNEL
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["switch"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for EWneo dual switch."""
        info_type = telegram_data.get("info_type")
        
        if info_type in TELEGRAM_EWNEO_STATE_CHANGE:
            self._handle_state_change(telegram_data)
        
        return self.get_switch_data()
    
    def _handle_state_change(self, telegram_data: Dict[str, Any]) -> None:
        """Handle state change telegram with 5-byte EWB_RCV parsing for dual switch."""
        data = telegram_data.get("data", {})
        
        # Parse the 5-byte response: 1 byte mode + 4 bytes state
        raw_data = data.get("raw_data")  # Should be 5 bytes
        if raw_data and len(raw_data) >= 5:
            self._parse_ewneo_dual_response(raw_data)
        else:
            # Fallback to simple state extraction if raw data not available
            channel = data.get("channel")
            if channel and 1 <= channel <= 2:
                new_state = data.get("switch_state")
                if isinstance(new_state, bool) and new_state != self._switch_states[channel]:
                    self._switch_states[channel] = new_state
                    _LOGGER.debug("EWneo dual switch %s channel %d: State changed to %s (fallback)", 
                                 self.serial_number[-6:], channel, "ON" if new_state else "OFF")
    
    def _parse_ewneo_dual_response(self, raw_data: bytes) -> None:
        """Parse 5-byte EWB_RCV response for EWneo dual switch.
        
        Format: 1 byte mode + 4 bytes dual channel state
        Mode should always be 0 for dual switch.
        """
        if len(raw_data) < 5:
            _LOGGER.warning("EWneo dual switch %s: Invalid response length %d, expected 5 bytes", 
                           self.serial_number[-6:], len(raw_data))
            return
            
        # Parse mode (byte 0) - should always be 0 for dual switch
        self._mode = raw_data[0]
        
        if self._mode != 0:
            _LOGGER.warning("EWneo dual switch %s: Unexpected mode %d, expected 0", 
                           self.serial_number[-6:], self._mode)
            return
        
        # Parse state (bytes 1-4, little-endian 32-bit word)
        state_word = int.from_bytes(raw_data[1:5], byteorder='little')
        self._parse_dual_onoff_state(state_word)
    
    def _parse_dual_onoff_state(self, state_word: int) -> None:
        """Parse dual channel on/off state word (mode 0)."""
        # Extract fields for channel 1 from 32-bit state word
        ch1_counter = (state_word >> 27) & 0x1F   # Bits 31-27
        ch1_reason = (state_word >> 24) & 0x07    # Bits 26-24
        
        # Extract fields for channel 2 from 32-bit state word
        ch2_counter = (state_word >> 19) & 0x1F   # Bits 23-19
        ch2_reason = (state_word >> 16) & 0x07    # Bits 18-16
        
        # Bits 15-0 are reserved
        
        # Update channel 1 state
        old_ch1_state = self._switch_states[1]
        self._switch_counters[1] = ch1_counter
        self._switch_reasons[1] = ch1_reason
        # Channel 1: 1=off, 2=on, 3=timer on, 5=logic on
        self._switch_states[1] = ch1_reason in [2, 3, 5]
        
        # Update channel 2 state
        old_ch2_state = self._switch_states[2]
        self._switch_counters[2] = ch2_counter
        self._switch_reasons[2] = ch2_reason
        # Channel 2: 1=off, 2=on, 3=timer on, 5=logic on
        self._switch_states[2] = ch2_reason in [2, 3, 5]
        
        # Log state changes
        if old_ch1_state != self._switch_states[1]:
            reason_text = {1: "off", 2: "on", 3: "timer", 5: "logic"}.get(ch1_reason, "unknown")
            _LOGGER.info("EWneo dual switch %s CH1: State changed to %s (reason: %s, counter: %d)", 
                        self.serial_number[-6:], "ON" if self._switch_states[1] else "OFF", 
                        reason_text, ch1_counter)
                        
        if old_ch2_state != self._switch_states[2]:
            reason_text = {1: "off", 2: "on", 3: "timer", 5: "logic"}.get(ch2_reason, "unknown")
            _LOGGER.info("EWneo dual switch %s CH2: State changed to %s (reason: %s, counter: %d)", 
                        self.serial_number[-6:], "ON" if self._switch_states[2] else "OFF", 
                        reason_text, ch2_counter)

    def get_switch_data(self) -> Dict[str, Any]:
        """Get current switch data for both channels."""
        return {
            "states": self._switch_states.copy(),
            "last_seen": self._last_seen,
            "mode": self._mode,
            "counters": self._switch_counters.copy(),
            "reasons": self._switch_reasons.copy(),
            "reason_texts": {
                1: {1: "off", 2: "on", 3: "timer", 5: "logic"}.get(self._switch_reasons[1], "unknown"),
                2: {1: "off", 2: "on", 3: "timer", 5: "logic"}.get(self._switch_reasons[2], "unknown")
            }
        }
    
    async def set_state(self, channel: int, state: bool) -> bool:
        """Set switch state for specific channel."""
        if channel not in [1, 2]:
            return False
        
        try:
            _LOGGER.info("Setting EWneo dual switch %s channel %d to %s", 
                        self.serial_number[-6:], channel, "ON" if state else "OFF")
            # TODO: Implement actual command sending via RX11
            self._switch_states[channel] = state
            return True
        except Exception as e:
            _LOGGER.error("Failed to set EWneo dual switch %s channel %d state: %s", 
                         self.serial_number[-6:], channel, e)
            return False
    
    async def get_state(self, channel: int) -> bool:
        """Get current switch state for specific channel."""
        return self._switch_states.get(channel, False)


def create_rx11_ewneo_dual_switch(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11EWneoDualSwitch:
    """Factory function to create EWneo dual switch."""
    return RX11EWneoDualSwitch(
        serial_number=serial_number,
        device_info=device_info,
        **kwargs
    )