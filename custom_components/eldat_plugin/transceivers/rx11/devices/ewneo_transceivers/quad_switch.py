"""EWneo quad switch implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import BaseReceiver, DeviceType, DeviceSubtype, OperatingMode

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EWNEO_STATE_CHANGE = [0x05, 0xF2]


class RX11EWneoQuadSwitch(BaseReceiver):
    """EWneo quad switch implementation for RX11 transceiver.
    
    Handles 4-channel EWneo switch devices with bidirectional communication.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize EWneo quad switch."""
        super().__init__(*args, device_type=DeviceType.EWNEO_QUAD_SWITCH, 
                        subtype=DeviceSubtype.QUAD_SWITCH, **kwargs)
        self._switch_states = {1: False, 2: False, 3: False, 4: False}
        self._mode = 0  # Should always be 0 for quad switch
        self._switch_counters = {1: 0, 2: 0, 3: 0, 4: 0}  # Counters for each channel
        self._switch_reasons = {1: 1, 2: 1, 3: 1, 4: 1}  # Reasons for each channel (1=off, 2=on, 3=timer, 5=logic)
        self._setup_operating_mode(**kwargs)
    
    def _setup_operating_mode(self, **kwargs) -> None:
        """Setup operating mode for quad switch."""
        self.operating_mode = OperatingMode.QUAD_CHANNEL
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["switch"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for EWneo quad switch."""
        info_type = telegram_data.get("info_type")
        
        if info_type in TELEGRAM_EWNEO_STATE_CHANGE:
            self._handle_state_change(telegram_data)
        
        return self.get_switch_data()
    
    def _handle_state_change(self, telegram_data: Dict[str, Any]) -> None:
        """Handle state change telegram with 5-byte EWB_RCV parsing for quad switch."""
        data = telegram_data.get("data", {})
        
        # Parse the 5-byte response: 1 byte mode + 4 bytes state
        raw_data = data.get("raw_data")  # Should be 5 bytes
        if raw_data and len(raw_data) >= 5:
            self._parse_ewneo_quad_response(raw_data)
        else:
            # Fallback to simple state extraction if raw data not available
            channel = data.get("channel")
            if channel and 1 <= channel <= 4:
                new_state = data.get("switch_state")
                if isinstance(new_state, bool) and new_state != self._switch_states[channel]:
                    self._switch_states[channel] = new_state
                    _LOGGER.debug("EWneo quad switch %s channel %d: State changed to %s (fallback)", 
                                 self.serial_number[-6:], channel, "ON" if new_state else "OFF")
    
    def _parse_ewneo_quad_response(self, raw_data: bytes) -> None:
        """Parse 5-byte EWB_RCV response for EWneo quad switch.
        
        Format: 1 byte mode + 4 bytes quad channel state (big-endian)
        Mode should always be 0 for quad switch.
        """
        if len(raw_data) < 5:
            _LOGGER.warning("EWneo quad switch %s: Invalid response length %d, expected 5 bytes", 
                           self.serial_number[-6:], len(raw_data))
            return
            
        # Parse mode (byte 0) - should always be 0 for quad switch
        self._mode = raw_data[0]
        
        if self._mode != 0:
            _LOGGER.warning("EWneo quad switch %s: Unexpected mode %d, expected 0", 
                           self.serial_number[-6:], self._mode)
            return
        
        # Parse state (bytes 1-4, BIG-ENDIAN 32-bit word for quad switch)
        state_word = int.from_bytes(raw_data[1:5], byteorder='big')
        self._parse_quad_onoff_state(state_word)
    
    def _parse_quad_onoff_state(self, state_word: int) -> None:
        """Parse quad channel on/off state word (mode 0).
        
        All 4 channels are packed into a single 32-bit word with counters and reasons.
        """
        # Extract fields for all 4 channels from 32-bit state word
        # Channel 1: bits 31-24
        ch1_counter = (state_word >> 27) & 0x1F   # Bits 31-27
        ch1_reason = (state_word >> 24) & 0x07    # Bits 26-24
        
        # Channel 2: bits 23-16  
        ch2_counter = (state_word >> 19) & 0x1F   # Bits 23-19
        ch2_reason = (state_word >> 16) & 0x07    # Bits 18-16
        
        # Channel 3: bits 15-8
        ch3_counter = (state_word >> 11) & 0x1F   # Bits 15-11
        ch3_reason = (state_word >> 8) & 0x07     # Bits 10-8
        
        # Channel 4: bits 7-0
        ch4_counter = (state_word >> 3) & 0x1F    # Bits 7-3
        ch4_reason = state_word & 0x07            # Bits 2-0
        
        # Store previous states for change detection
        old_states = self._switch_states.copy()
        
        # Update all channel states
        channels_data = [
            (1, ch1_counter, ch1_reason),
            (2, ch2_counter, ch2_reason),
            (3, ch3_counter, ch3_reason),
            (4, ch4_counter, ch4_reason)
        ]
        
        for channel, counter, reason in channels_data:
            self._switch_counters[channel] = counter
            self._switch_reasons[channel] = reason
            # All channels: 1=off, 2=on, 3=timer on, 5=logic on
            self._switch_states[channel] = reason in [2, 3, 5]
            
            # Log state changes
            if old_states[channel] != self._switch_states[channel]:
                reason_text = {1: "off", 2: "on", 3: "timer", 5: "logic"}.get(reason, "unknown")
                _LOGGER.info("EWneo quad switch %s CH%d: State changed to %s (reason: %s, counter: %d)", 
                            self.serial_number[-6:], channel, 
                            "ON" if self._switch_states[channel] else "OFF", 
                            reason_text, counter)

    def get_switch_data(self) -> Dict[str, Any]:
        """Get current switch data for all 4 channels."""
        return {
            "states": self._switch_states.copy(),
            "last_seen": self._last_seen,
            "mode": self._mode,
            "counters": self._switch_counters.copy(),
            "reasons": self._switch_reasons.copy(),
            "reason_texts": {
                ch: {1: "off", 2: "on", 3: "timer", 5: "logic"}.get(self._switch_reasons[ch], "unknown")
                for ch in [1, 2, 3, 4]
            },
            "query_modes": {
                1: 1,   # EWB_QUERY_STATE mode for channel 1 timer info
                2: 9,   # EWB_QUERY_STATE mode for channel 2 timer info  
                3: 17,  # EWB_QUERY_STATE mode for channel 3 timer info
                4: 25   # EWB_QUERY_STATE mode for channel 4 timer info
            }
        }
    
    async def set_state(self, channel: int, state: bool) -> bool:
        """Set switch state for specific channel."""
        if channel not in [1, 2, 3, 4]:
            return False
        
        try:
            _LOGGER.info("Setting EWneo quad switch %s channel %d to %s", 
                        self.serial_number[-6:], channel, "ON" if state else "OFF")
            # TODO: Implement actual command sending via RX11
            self._switch_states[channel] = state
            return True
        except Exception as e:
            _LOGGER.error("Failed to set EWneo quad switch %s channel %d state: %s", 
                         self.serial_number[-6:], channel, e)
            return False
    
    async def get_state(self, channel: int) -> bool:
        """Get current switch state for specific channel."""
        return self._switch_states.get(channel, False)


def create_rx11_ewneo_quad_switch(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11EWneoQuadSwitch:
    """Factory function to create EWneo quad switch."""
    return RX11EWneoQuadSwitch(
        serial_number=serial_number,
        device_info=device_info,
        **kwargs
    )