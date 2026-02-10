"""EWneo dimmer implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import DeviceType, DeviceSubtype, OperatingMode
from .base import EWneoBaseDevice
from .....translations import translate, DEFAULT_LANGUAGE

_LOGGER = logging.getLogger(__name__)


class RX11EWneoDimmer(EWneoBaseDevice):
    """EWneo dimmer implementation for RX11 transceiver.
    
    Handles single-channel EWneo dimmer devices with brightness control.
    Inherits common EWB state parsing from EWneoBaseDevice.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize EWneo dimmer."""
        super().__init__(*args, device_type=DeviceType.EWNEO_DIMMER, 
                        subtype=DeviceSubtype.DIMMER, **kwargs)
        # Note: self._mode is now in base class
        
        # Dimmer-specific state
        self._dimmer_state = False
        self._brightness_level = 0  # 0-100%
        
        # Internal dimmer levels (0-255)
        self._desired_level = 0    # Target brightness
        self._current_level = 0    # Actual brightness
        self._dimming_time = 0     # Smooth dimming duration in seconds
        self._is_dimming = False   # Currently dimming
        
        self._setup_operating_mode(**kwargs)
    
    def _setup_operating_mode(self, **kwargs) -> None:
        """Setup operating mode for dimmer."""
        self.operating_mode = OperatingMode.SINGLE_CHANNEL
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["light"]
    
    def _get_model_name(self, language: str = DEFAULT_LANGUAGE) -> str:
        """Get detailed model name for device info.
        
        Uses translation system for proper language support.
        """
        return translate("ewneo.dimmer", language)
    
    def _parse_mode0_state(self, state_word: int) -> None:
        """Parse Mode 0 state for dimmer (brightness levels).
        
        Dimmer always uses Mode 0.
        State word format (32-bit, big-endian):
        - Bits 31-24: Desired level (0-255)
        - Bits 23-16: Current level (0-255)
        - Bits 15-12: Dimming time exponent
        - Bits 11-4: Dimming time mantissa
        - Bits 3-0: Reserved
        """
        # Extract fields from 32-bit state word (big-endian)
        self._desired_level = (state_word >> 24) & 0xFF   # Bits 31-24
        self._current_level = (state_word >> 16) & 0xFF   # Bits 23-16
        
        # Extract smooth dimming information
        dimming_exp = (state_word >> 12) & 0x0F          # Bits 15-12
        dimming_mantissa = (state_word >> 4) & 0xFF       # Bits 11-4
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
                            self.serial_number, "ON" if self._dimmer_state else "OFF", 
                            self._brightness_level, desired_pct, self._dimming_time)
            else:
                _LOGGER.info("EWneo dimmer %s: State=%s, Brightness=%d%% (stable)", 
                            self.serial_number, "ON" if self._dimmer_state else "OFF", 
                            self._brightness_level)
    
    def _get_device_data(self) -> Dict[str, Any]:
        """Get dimmer data for coordinator (implements abstract method)."""
        return self.get_dimmer_data()

    def get_dimmer_data(self) -> Dict[str, Any]:
        """Get current dimmer data."""
        data = {
            "state": self._dimmer_state,
            "brightness": self._brightness_level,
            "last_seen": self._last_seen,
        }
        
        # Add dimming information if currently dimming
        if self._is_dimming:
            data.update({
                "is_dimming": True,
                "current_level": self._current_level,
                "desired_level": self._desired_level,
                "dimming_time": self._dimming_time,
            })
        
        return data
    
    async def set_state(self, state: bool, brightness: Optional[int] = None) -> bool:
        """Set dimmer state and brightness."""
        try:
            if brightness is not None:
                brightness = max(0, min(100, brightness))
                state = brightness > 0
            else:
                brightness = 100 if state else 0
            
            _LOGGER.info("Setting EWneo dimmer %s to %s (brightness: %d%%)", 
                        self.serial_number, "ON" if state else "OFF", brightness)
            # TODO: Implement actual command sending via RX11
            self._dimmer_state = state
            self._brightness_level = brightness
            return True
        except Exception as e:
            _LOGGER.error("Failed to set EWneo dimmer %s state: %s", 
                         self.serial_number, e)
            return False
    
    async def get_state(self) -> tuple[bool, int]:
        """Get current dimmer state and brightness."""
        return self._dimmer_state, self._brightness_level


def create_rx11_ewneo_dimmer(
    serial_number: str,
    device_info: Dict[str, Any] = None,
    **kwargs
) -> Optional[RX11EWneoDimmer]:
    """Create RX11 EWneo dimmer instance."""
    try:
        device_info = device_info or {}
        name = device_info.get('name') or f"EWneo-Dimmer {serial_number}"
        return RX11EWneoDimmer(
            serial_number=serial_number,
            name=name,
            **kwargs
        )
    except Exception as e:
        _LOGGER.error(f"Error creating EWneo dimmer: {e}")
        return None