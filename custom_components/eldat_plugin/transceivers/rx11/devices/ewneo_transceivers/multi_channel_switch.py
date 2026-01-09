"""EWneo multi-channel switch implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import DeviceType, DeviceSubtype, OperatingMode
from .base import EWneoBaseDevice

_LOGGER = logging.getLogger(__name__)


class RX11EWneoMultiChannelSwitch(EWneoBaseDevice):
    """EWneo multi-channel switch implementation for RX11 transceiver.
    
    Handles 2-channel and 4-channel EWneo switch devices with bidirectional communication.
    Inherits common EWB state parsing from EWneoBaseDevice.
    """
    
    def __init__(self, *args, num_channels: int = 2, **kwargs):
        """Initialize EWneo multi-channel switch.
        
        Args:
            num_channels: Number of channels (2 or 4)
        """
        if num_channels not in [2, 4]:
            raise ValueError(f"Invalid number of channels: {num_channels}. Must be 2 or 4.")
        
        self._num_channels = num_channels
        
        # Determine device type and subtype based on number of channels
        if num_channels == 2:
            device_type = DeviceType.EWNEO_DUAL_SWITCH
            subtype = DeviceSubtype.DUAL_SWITCH
            operating_mode = OperatingMode.DUAL_CHANNEL
        else:  # 4 channels
            device_type = DeviceType.EWNEO_QUAD_SWITCH
            subtype = DeviceSubtype.QUAD_SWITCH
            operating_mode = OperatingMode.QUAD_CHANNEL
        
        super().__init__(*args, device_type=device_type, subtype=subtype, **kwargs)
        # Note: self._mode is now in base class
        
        # Initialize channel states
        self._switch_states = {ch: False for ch in range(1, num_channels + 1)}
        self._switch_counters = {ch: 0 for ch in range(1, num_channels + 1)}
        self._switch_reasons = {ch: 1 for ch in range(1, num_channels + 1)}  # 1=off, 2=on, 3=timer, 5=logic
        
        self.operating_mode = operating_mode
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["switch"]
    
    @property
    def num_channels(self) -> int:
        """Return number of channels."""
        return self._num_channels
    
    def _parse_mode0_state(self, state_word: int) -> None:
        """Parse Mode 0 state for multi-channel switch.
        
        Multi-channel switches always use Mode 0.
        State word format (32-bit, big-endian):
        
        For 2-channel:
        - Bits 31-27: Channel 1 counter
        - Bits 26-24: Channel 1 reason (1=off, 2=on, 3=timer, 5=logic)
        - Bits 23-19: Channel 2 counter
        - Bits 18-16: Channel 2 reason
        - Bits 15-0: Reserved
        
        For 4-channel:
        - Bits 31-27: Channel 1 counter
        - Bits 26-24: Channel 1 reason
        - Bits 23-19: Channel 2 counter
        - Bits 18-16: Channel 2 reason
        - Bits 15-11: Channel 3 counter
        - Bits 10-8: Channel 3 reason
        - Bits 7-3: Channel 4 counter
        - Bits 2-0: Channel 4 reason
        """
        # Store previous states for change detection
        old_states = self._switch_states.copy()
        
        if self._num_channels == 2:
            self._parse_2channel_state(state_word)
        else:  # 4 channels
            self._parse_4channel_state(state_word)
        
        # Log state changes
        self._log_state_changes(old_states)
    
    def _parse_2channel_state(self, state_word: int) -> None:
        """Parse state for 2-channel switch."""
        # Channel 1: bits 31-24
        ch1_counter = (state_word >> 27) & 0x1F   # Bits 31-27
        ch1_reason = (state_word >> 24) & 0x07    # Bits 26-24
        
        # Channel 2: bits 23-16
        ch2_counter = (state_word >> 19) & 0x1F   # Bits 23-19
        ch2_reason = (state_word >> 16) & 0x07    # Bits 18-16
        
        # Update states
        self._update_channel_state(1, ch1_counter, ch1_reason)
        self._update_channel_state(2, ch2_counter, ch2_reason)
    
    def _parse_4channel_state(self, state_word: int) -> None:
        """Parse state for 4-channel switch."""
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
        
        # Update all channels
        self._update_channel_state(1, ch1_counter, ch1_reason)
        self._update_channel_state(2, ch2_counter, ch2_reason)
        self._update_channel_state(3, ch3_counter, ch3_reason)
        self._update_channel_state(4, ch4_counter, ch4_reason)
    
    def _update_channel_state(self, channel: int, counter: int, reason: int) -> None:
        """Update state for a single channel."""
        self._switch_counters[channel] = counter
        self._switch_reasons[channel] = reason
        # Reason: 1=off, 2=on, 3=timer on, 5=logic on
        self._switch_states[channel] = reason in [2, 3, 5]
    
    def _log_state_changes(self, old_states: Dict[int, bool]) -> None:
        """Log state changes for all channels."""
        reason_map = {1: "off", 2: "on", 3: "timer", 5: "logic"}
        
        for channel in range(1, self._num_channels + 1):
            if old_states[channel] != self._switch_states[channel]:
                reason_text = reason_map.get(self._switch_reasons[channel], "unknown")
                _LOGGER.info("EWneo %dch switch %s CH%d: State=%s (reason: %s, counter: %d)", 
                            self._num_channels, self.serial_number[-6:], channel,
                            "ON" if self._switch_states[channel] else "OFF", 
                            reason_text, self._switch_counters[channel])
    
    def _get_device_data(self) -> Dict[str, Any]:
        """Get switch data for coordinator (implements abstract method)."""
        return self.get_switch_data()
    
    def get_switch_data(self) -> Dict[str, Any]:
        """Get current switch data for all channels."""
        data = {
            "states": self._switch_states.copy(),
            "last_seen": self._last_seen,
            "num_channels": self._num_channels,
        }
        
        # Add reason texts for debugging
        reason_map = {1: "off", 2: "on", 3: "timer", 5: "logic"}
        data["reason_texts"] = {
            ch: reason_map.get(self._switch_reasons[ch], "unknown")
            for ch in range(1, self._num_channels + 1)
        }
        
        return data
    
    async def set_state(self, channel: int, state: bool) -> bool:
        """Set switch state for specific channel."""
        if channel not in range(1, self._num_channels + 1):
            _LOGGER.error("Invalid channel %d for %d-channel switch", channel, self._num_channels)
            return False
        
        try:
            _LOGGER.info("Setting EWneo %dch switch %s channel %d to %s", 
                        self._num_channels, self.serial_number[-6:], channel, "ON" if state else "OFF")
            # TODO: Implement actual command sending via RX11
            self._switch_states[channel] = state
            return True
        except Exception as e:
            _LOGGER.error("Failed to set EWneo switch %s channel %d state: %s", 
                         self.serial_number[-6:], channel, e)
            return False
    
    async def get_state(self, channel: int) -> Optional[bool]:
        """Get current state for specific channel."""
        if channel not in range(1, self._num_channels + 1):
            return None
        return self._switch_states[channel]


# Factory functions for backward compatibility
def create_rx11_ewneo_dual_switch(
    serial_number: str,
    name: str = None,
    **kwargs
) -> Optional[RX11EWneoMultiChannelSwitch]:
    """Create RX11 EWneo dual switch instance."""
    try:
        return RX11EWneoMultiChannelSwitch(
            serial_number=serial_number,
            name=name or f"EWneo Dual Switch {serial_number[-6:]}",
            num_channels=2,
            **kwargs
        )
    except Exception as e:
        _LOGGER.error(f"Error creating EWneo dual switch: {e}")
        return None


def create_rx11_ewneo_quad_switch(
    serial_number: str,
    name: str = None,
    **kwargs
) -> Optional[RX11EWneoMultiChannelSwitch]:
    """Create RX11 EWneo quad switch instance."""
    try:
        return RX11EWneoMultiChannelSwitch(
            serial_number=serial_number,
            name=name or f"EWneo Quad Switch {serial_number[-6:]}",
            num_channels=4,
            **kwargs
        )
    except Exception as e:
        _LOGGER.error(f"Error creating EWneo quad switch: {e}")
        return None
