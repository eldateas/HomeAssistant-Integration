"""EWneo dimmer implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import BaseReceiver, DeviceType, DeviceSubtype, OperatingMode

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EWNEO_STATE_CHANGE = [0x05, 0xF2]


class RX11EWneoDimmer(BaseReceiver):
    """EWneo dimmer implementation for RX11 transceiver.
    
    Handles single-channel EWneo dimmer devices with brightness control.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize EWneo dimmer."""
        super().__init__(*args, device_type=DeviceType.EWNEO_DIMMER, 
                        subtype=DeviceSubtype.DIMMER, **kwargs)
        self._dimmer_state = False
        self._brightness_level = 0  # 0-100
        self._mode = 0  # Should always be 0 for dimmer
        self._desired_level = 0  # 0-255 desired brightness level
        self._current_level = 0   # 0-255 current brightness level
        self._dimming_time = 0    # Time remaining for smooth dimming (seconds)
        self._is_dimming = False  # Whether smooth dimming is active
        self._setup_operating_mode(**kwargs)
    
    def _setup_operating_mode(self, **kwargs) -> None:
        """Setup operating mode for dimmer."""
        self.operating_mode = OperatingMode.SINGLE_CHANNEL
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["light"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for EWneo dimmer."""
        info_type = telegram_data.get("info_type")
        
        if info_type in TELEGRAM_EWNEO_STATE_CHANGE:
            self._handle_state_change(telegram_data)
        
        return self.get_dimmer_data()
    
    def _handle_state_change(self, telegram_data: Dict[str, Any]) -> None:
        """Handle state change telegram with 5-byte EWB_RCV parsing for dimmer."""
        data = telegram_data.get("data", {})
        
        # Parse the 5-byte response: 1 byte mode + 4 bytes state
        raw_data = data.get("raw_data")  # Should be 5 bytes
        if raw_data and len(raw_data) >= 5:
            self._parse_ewneo_dimmer_response(raw_data)
        else:
            # Fallback to simple state extraction if raw data not available
            state, brightness = self._parse_dimmer_state(data.get("dimmer_value"))
            
            if state != self._dimmer_state or brightness != self._brightness_level:
                self._dimmer_state = state
                self._brightness_level = brightness
                _LOGGER.debug("EWneo dimmer %s: State=%s, Brightness=%d%% (fallback)", 
                             self.serial_number[-6:], "ON" if state else "OFF", brightness)
    
    def _parse_dimmer_state(self, dimmer_value: Any) -> tuple[bool, int]:
        """Parse dimmer state from various input formats."""
        if dimmer_value is None:
            return self._dimmer_state, self._brightness_level
        
        if isinstance(dimmer_value, bool):
            return dimmer_value, 100 if dimmer_value else 0
        
        if isinstance(dimmer_value, (int, float)):
            brightness = max(0, min(100, int(dimmer_value)))
            state = brightness > 0
            return state, brightness
        
        return self._dimmer_state, self._brightness_level
    
    def _parse_ewneo_dimmer_response(self, raw_data: bytes) -> None:
        """Parse 5-byte EWB_RCV response for EWneo dimmer.
        
        Format: 1 byte mode + 4 bytes dimmer state (big-endian)
        Mode should always be 0 for dimmer.
        """
        if len(raw_data) < 5:
            _LOGGER.warning("EWneo dimmer %s: Invalid response length %d, expected 5 bytes", 
                           self.serial_number[-6:], len(raw_data))
            return
            
        # Parse mode (byte 0) - should always be 0 for dimmer
        self._mode = raw_data[0]
        
        if self._mode != 0:
            _LOGGER.warning("EWneo dimmer %s: Unexpected mode %d, expected 0", 
                           self.serial_number[-6:], self._mode)
            return
        
        # Parse state (bytes 1-4, BIG-ENDIAN 32-bit word for dimmer)
        state_word = int.from_bytes(raw_data[1:5], byteorder='big')
        self._parse_dimmer_level_state(state_word)
    
    def _parse_dimmer_level_state(self, state_word: int) -> None:
        """Parse dimmer level state word (mode 0).
        
        Contains desired level, current level, and smooth dimming information.
        """
        # Extract fields from 32-bit state word (big-endian)
        self._desired_level = (state_word >> 24) & 0xFF   # Bits 31-24: desired level (0-255)
        self._current_level = (state_word >> 16) & 0xFF   # Bits 23-16: current level (0-255)
        
        # Extract smooth dimming information
        dimming_exp = (state_word >> 12) & 0x0F          # Bits 15-12: exponent
        dimming_mantissa = (state_word >> 4) & 0xFF       # Bits 11-4: mantissa
        # Bits 3-0 are reserved
        
        # Calculate dimming time using mantissa * 2^exponent formula
        if dimming_mantissa > 0:
            self._dimming_time = dimming_mantissa * (2 ** dimming_exp)
            self._is_dimming = True
        else:
            self._dimming_time = 0
            self._is_dimming = False
        
        # Convert to Home Assistant format (0-100%)
        old_state = self._dimmer_state
        old_brightness = self._brightness_level
        
        # Use current level for actual state, desired level shows target
        self._brightness_level = round((self._current_level / 255) * 100)
        self._dimmer_state = self._current_level > 0
        
        # Log state changes with smooth dimming info
        if old_state != self._dimmer_state or old_brightness != self._brightness_level:
            if self._is_dimming:
                desired_pct = round((self._desired_level / 255) * 100)
                _LOGGER.info("EWneo dimmer %s: State=%s, Current=%d%%, Target=%d%%, Dimming=%ds", 
                            self.serial_number[-6:], "ON" if self._dimmer_state else "OFF", 
                            self._brightness_level, desired_pct, self._dimming_time)
            else:
                _LOGGER.info("EWneo dimmer %s: State=%s, Brightness=%d%% (stable)", 
                            self.serial_number[-6:], "ON" if self._dimmer_state else "OFF", 
                            self._brightness_level)

    def get_dimmer_data(self) -> Dict[str, Any]:
        """Get current dimmer data."""
        return {
            "state": self._dimmer_state,
            "brightness": self._brightness_level,
            "last_seen": self._last_seen,
            "mode": self._mode,
            "desired_level": self._desired_level,
            "current_level": self._current_level,
            "desired_brightness": round((self._desired_level / 255) * 100) if self._desired_level > 0 else 0,
            "current_brightness": round((self._current_level / 255) * 100) if self._current_level > 0 else 0,
            "is_dimming": self._is_dimming,
            "dimming_time_remaining": self._dimming_time if self._is_dimming else 0
        }
    
    async def set_state(self, state: bool, brightness: Optional[int] = None) -> bool:
        """Set dimmer state and brightness."""
        try:
            if brightness is not None:
                brightness = max(0, min(100, brightness))
                state = brightness > 0
            else:
                brightness = 100 if state else 0
            
            _LOGGER.info("Setting EWneo dimmer %s to %s (brightness: %d%%)", 
                        self.serial_number[-6:], "ON" if state else "OFF", brightness)
            # TODO: Implement actual command sending via RX11
            self._dimmer_state = state
            self._brightness_level = brightness
            return True
        except Exception as e:
            _LOGGER.error("Failed to set EWneo dimmer %s state: %s", 
                         self.serial_number[-6:], e)
            return False
    
    async def get_state(self) -> tuple[bool, int]:
        """Get current dimmer state and brightness."""
        return self._dimmer_state, self._brightness_level


def create_rx11_ewneo_dimmer(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11EWneoDimmer:
    """Factory function to create EWneo dimmer."""
    return RX11EWneoDimmer(
        serial_number=serial_number,
        device_info=device_info,
        **kwargs
    )