"""EWneo switch implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import BaseReceiver, DeviceType, DeviceSubtype, OperatingMode

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EWNEO_STATE_CHANGE = [0x05, 0xF2]


class RX11EWneoSwitch(BaseReceiver):
    """EWneo switch implementation for RX11 transceiver.
    
    Handles single-channel EWneo switch devices with bidirectional communication.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize EWneo switch."""
        super().__init__(*args, device_type=DeviceType.EWNEO_SWITCH, 
                        subtype=DeviceSubtype.SWITCH, **kwargs)
        self._switch_state = False
        self._mode = 0  # 0=On/Off state, 1=Timer mode
        self._switch_counter = 0  # Counter for switching on (bits 31-27)
        self._switch_reason = 1  # 1=off, 2=on, 5=on due to logic function
        self._timer_info = {}  # Timer information when mode=1
        self._setup_operating_mode(**kwargs)
    
    def _setup_operating_mode(self, **kwargs) -> None:
        """Setup operating mode for single switch."""
        self.operating_mode = OperatingMode.SINGLE_CHANNEL
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["switch"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for EWneo switch."""
        info_type = telegram_data.get("info_type")
        
        if info_type in TELEGRAM_EWNEO_STATE_CHANGE:
            self._handle_state_change(telegram_data)
        
        return self.get_switch_data()
    
    def _handle_state_change(self, telegram_data: Dict[str, Any]) -> None:
        """Handle state change telegram with 5-byte EWB_RCV parsing."""
        data = telegram_data.get("data", {})
        
        # Parse the 5-byte response: 1 byte mode + 4 bytes state
        raw_data = data.get("raw_data")  # Should be 5 bytes
        if raw_data and len(raw_data) >= 5:
            self._parse_ewneo_response(raw_data)
        else:
            # Fallback to simple state extraction if raw data not available
            new_state = data.get("switch_state", self._switch_state)
            if isinstance(new_state, bool) and new_state != self._switch_state:
                self._switch_state = new_state
                _LOGGER.debug("EWneo switch %s: State changed to %s (fallback)", 
                             self.serial_number[-6:], "ON" if new_state else "OFF")
    
    def _parse_ewneo_response(self, raw_data: bytes) -> None:
        """Parse 5-byte EWB_RCV response for EWneo switch.
        
        Format: 1 byte mode + 4 bytes state
        """
        if len(raw_data) < 5:
            _LOGGER.warning("EWneo switch %s: Invalid response length %d, expected 5 bytes", 
                           self.serial_number[-6:], len(raw_data))
            return
            
        # Parse mode (byte 0)
        self._mode = raw_data[0]
        
        # Parse state (bytes 1-4, little-endian 32-bit word)
        state_word = int.from_bytes(raw_data[1:5], byteorder='little')
        
        if self._mode == 0:
            # Mode 0: On/off state
            self._parse_onoff_state(state_word)
        elif self._mode == 1:
            # Mode 1: Timer state
            self._parse_timer_state(state_word)
        else:
            _LOGGER.warning("EWneo switch %s: Unknown mode %d", 
                           self.serial_number[-6:], self._mode)
    
    def _parse_onoff_state(self, state_word: int) -> None:
        """Parse on/off state word (mode 0)."""
        # Extract fields from 32-bit state word
        self._switch_counter = (state_word >> 27) & 0x1F  # Bits 31-27
        self._switch_reason = (state_word >> 24) & 0x07   # Bits 26-24
        # Bits 23-0 are reserved
        
        # Determine switch state based on reason
        old_state = self._switch_state
        self._switch_state = self._switch_reason in [2, 5]  # 2=on, 5=on due to logic
        
        if old_state != self._switch_state:
            reason_text = {1: "off", 2: "on", 5: "on (logic)"}.get(self._switch_reason, "unknown")
            _LOGGER.info("EWneo switch %s: State changed to %s (reason: %s, counter: %d)", 
                        self.serial_number[-6:], "ON" if self._switch_state else "OFF", 
                        reason_text, self._switch_counter)
    
    def _parse_timer_state(self, state_word: int) -> None:
        """Parse timer state word (mode 1)."""
        # Extract timer information from 32-bit state word
        start_exp = (state_word >> 28) & 0x0F      # Bits 31-28
        start_mantissa = (state_word >> 20) & 0xFF  # Bits 27-20
        current_exp = (state_word >> 16) & 0x0F     # Bits 19-16
        current_mantissa = (state_word >> 8) & 0xFF # Bits 15-8
        # Bits 7-2 are reserved
        default_duration = bool(state_word & 0x02)  # Bit 1
        warning_active = bool(state_word & 0x01)    # Bit 0
        
        # Calculate durations using mantissa * 2^exponent formula
        start_duration = start_mantissa * (2 ** start_exp) if start_mantissa > 0 else 0
        current_duration = current_mantissa * (2 ** current_exp) if current_mantissa > 0 else 0
        
        self._switch_state = True  # Timer mode means switch is on
        self._timer_info = {
            "start_duration": start_duration,
            "current_duration": current_duration,
            "default_duration": default_duration,
            "warning_active": warning_active
        }
        
        _LOGGER.info("EWneo switch %s: Timer mode - remaining %ds of %ds (warning: %s)", 
                    self.serial_number[-6:], current_duration, start_duration, 
                    "active" if warning_active else "inactive")

    def get_switch_data(self) -> Dict[str, Any]:
        """Get current switch data."""
        data = {
            "state": self._switch_state,
            "last_seen": self._last_seen,
            "mode": self._mode,
        }
        
        if self._mode == 0:
            # Add on/off state information
            data.update({
                "switch_counter": self._switch_counter,
                "switch_reason": self._switch_reason,
                "reason_text": {1: "off", 2: "on", 5: "on (logic)"}.get(self._switch_reason, "unknown")
            })
        elif self._mode == 1:
            # Add timer information
            data.update({
                "timer_info": self._timer_info.copy()
            })
            
        return data
    
    def _create_switch_state_command(self, turn_on: bool, timer_duration: Optional[int] = None) -> tuple[int, list]:
        """Create state command for EWneo switch according to EWB_CHANGE_STATE specification.
        
        Args:
            turn_on: True to turn on, False to turn off
            timer_duration: Optional timer duration in seconds (None for default duration)
            
        Returns:
            Tuple of (mode, state_bytes) for EwbChangeState
        """
        if timer_duration is not None:
            # Mode 1: Timer with specific duration
            # Calculate exponent and mantissa for duration
            # Formula: duration = mantissa * 2^exponent
            if timer_duration <= 0:
                _LOGGER.warning("Invalid timer duration %d, using default", timer_duration)
                return self._create_switch_state_command(turn_on, None)
                
            # Find best exponent and mantissa combination
            exponent = 0
            while (timer_duration >> exponent) > 255 and exponent < 15:
                exponent += 1
                
            mantissa = min(255, timer_duration >> exponent)
            if mantissa == 0:
                mantissa = 1  # Avoid undefined behavior
                
            # Create state word (big-endian)
            # Bits 31-28: Exponent
            # Bits 27-20: Mantissa  
            # Bits 19-1: Reserved (0)
            # Bit 0: Switch-off warning (0 for now)
            state_word = (exponent << 28) | (mantissa << 20) | 0  # No warning
            
            # Convert to 4 bytes in big-endian order
            state_bytes = [
                (state_word >> 24) & 0xFF,
                (state_word >> 16) & 0xFF, 
                (state_word >> 8) & 0xFF,
                state_word & 0xFF
            ]
            
            _LOGGER.debug("EWneo switch %s: Timer command - duration: %ds (exp: %d, mantissa: %d)", 
                         self.serial_number[-6:], mantissa * (2 ** exponent), exponent, mantissa)
            
            return (1, state_bytes)
        else:
            # Mode 0: Simple on/off or default timer
            if turn_on:
                # Option 2: Turn on
                reason_code = 2
            else:
                # Option 1: Turn off  
                reason_code = 1
                
            # Create state word (big-endian)
            # Bits 31-27: Reserved (0)
            # Bits 26-24: Reason code
            # Bits 23-0: Reserved (0)
            state_word = (reason_code << 24)
            
            # Convert to 4 bytes in big-endian order
            state_bytes = [
                (state_word >> 24) & 0xFF,
                (state_word >> 16) & 0xFF,
                (state_word >> 8) & 0xFF, 
                state_word & 0xFF
            ]
            
            _LOGGER.debug("EWneo switch %s: Simple command - %s (reason: %d)", 
                         self.serial_number[-6:], "ON" if turn_on else "OFF", reason_code)
            
            return (0, state_bytes)
    
    async def set_state(self, state: bool, timer_duration: Optional[int] = None) -> bool:
        """Set switch state. Note: This is a device-level method.
        
        Actual EWB_CHANGE_STATE commands should be sent by the entity layer.
        This method is mainly for internal device state management.
        """
        self._switch_state = state
        return True
    
    async def get_state(self) -> bool:
        """Get current switch state."""
        return self._switch_state
    
    async def turn_on_with_timer(self, duration: int, warning: bool = False) -> bool:
        """Turn on switch with specific timer duration.
        
        Args:
            duration: Timer duration in seconds
            warning: Enable switch-off warning
            
        Returns:
            True if successful, False otherwise
        """
        try:
            gateway_serial = self._device_info.get("gateway_serial")
            if not gateway_serial:
                _LOGGER.error("EWneo switch %s: No gateway serial available", self.serial_number[-6:])
                return False
            
            # Mode 1: Timer with specific duration
            exponent = 0
            while (duration >> exponent) > 255 and exponent < 15:
                exponent += 1
                
            mantissa = min(255, duration >> exponent)
            if mantissa == 0:
                mantissa = 1
                
            # Create state word with warning bit
            state_word = (exponent << 28) | (mantissa << 20) | (1 if warning else 0)
            state_bytes = [
                (state_word >> 24) & 0xFF,
                (state_word >> 16) & 0xFF,
                (state_word >> 8) & 0xFF,
                state_word & 0xFF
            ]
            
            _LOGGER.info("Setting EWneo switch %s timer: %ds (warning: %s)", 
                        self.serial_number[-6:], duration, "on" if warning else "off")
            
            if hasattr(self, 'coordinator') and self.coordinator:
                result = await self.coordinator.transceiver.rx11_ewb_change_state(
                    gateway_serial, self.serial_number, 1, state_bytes
                )
                
                if result:
                    recent_mode, recent_state_bytes = result
                    raw_response = bytes([recent_mode] + recent_state_bytes)
                    self._parse_ewneo_response(raw_response)
                    return True
                    
            return False
            
        except Exception as e:
            _LOGGER.error("Failed to set EWneo switch %s timer: %s", self.serial_number[-6:], e)
            return False


def create_rx11_ewneo_switch(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11EWneoSwitch:
    """Factory function to create EWneo switch."""
    return RX11EWneoSwitch(
        serial_number=serial_number,
        device_info=device_info,
        **kwargs
    )