"""Unified EWneo switch implementation for RX11 transceiver.

This module provides a single, configurable switch class that handles
all channel counts (1, 2, 4). This replaces the separate single/dual/quad
switch classes with a unified implementation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import DeviceType, DeviceSubtype, OperatingMode
from .base import EWneoBaseDevice
from .....translations import translate, DEFAULT_LANGUAGE

_LOGGER = logging.getLogger(__name__)


class RX11EWneoSwitch(EWneoBaseDevice):
    """Unified EWneo switch implementation for RX11 transceiver.
    
    Handles 1, 2, or 4 channel EWneo switch devices with bidirectional communication.
    Inherits common EWB state parsing from EWneoBaseDevice.
    
    Channel configuration:
    - 1 channel: Single switch
    - 2 channels: Dual switch
    - 4 channels: Quad switch
    
    State word format depends on channel count:
    - Single channel uses bits 31-24 for counter/reason
    - Multi-channel uses packed format for all channels
    """
    
    # Channel count to device type mapping
    CHANNEL_TO_DEVICE_TYPE = {
        1: DeviceType.EWNEO_SWITCH,
        2: DeviceType.EWNEO_DUAL_SWITCH,
        4: DeviceType.EWNEO_QUAD_SWITCH,
    }
    
    # Channel count to subtype mapping
    CHANNEL_TO_SUBTYPE = {
        1: DeviceSubtype.SWITCH,
        2: DeviceSubtype.DUAL_SWITCH,
        4: DeviceSubtype.QUAD_SWITCH,
    }
    
    # Channel count to operating mode mapping
    CHANNEL_TO_MODE = {
        1: OperatingMode.SINGLE_CHANNEL,
        2: OperatingMode.DUAL_CHANNEL,
        4: OperatingMode.QUAD_CHANNEL,
    }
    
    # Reason codes
    REASON_OFF = 1
    REASON_ON = 2
    REASON_TIMER = 3
    REASON_LOGIC = 5
    
    REASON_TEXT_MAP = {
        1: "off",
        2: "on",
        3: "timer",
        5: "logic"
    }
    
    def __init__(
        self, 
        serial_number: str,
        num_channels: int = 1,
        name: Optional[str] = None,
        device_info: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        """Initialize EWneo switch.
        
        Args:
            serial_number: Device serial number
            num_channels: Number of channels (1, 2, or 4)
            name: Optional device name
            device_info: Optional device information dictionary
            **kwargs: Additional arguments passed to EWneoBaseDevice
        """
        # Validate channel count
        if num_channels not in [1, 2, 4]:
            _LOGGER.warning(
                "Invalid channel count %d for switch, defaulting to 1", 
                num_channels
            )
            num_channels = 1
        
        self._num_channels = num_channels
        self._device_info = device_info or {}
        
        # Determine device type, subtype, and operating mode
        device_type = self.CHANNEL_TO_DEVICE_TYPE[num_channels]
        subtype = self.CHANNEL_TO_SUBTYPE[num_channels]
        
        # Generate short name if not provided
        if not name:
            name = f"EWneo-Schalter {serial_number}"
        
        super().__init__(
            serial_number=serial_number,
            device_type=device_type,
            subtype=subtype,
            name=name,
            **kwargs
        )
        
        self.operating_mode = self.CHANNEL_TO_MODE[num_channels]
        
        # Initialize channel states (1-indexed for multi-channel consistency)
        self._switch_states: Dict[int, bool] = {
            ch: False for ch in range(1, num_channels + 1)
        }
        self._switch_counters: Dict[int, int] = {
            ch: 0 for ch in range(1, num_channels + 1)
        }
        self._switch_reasons: Dict[int, int] = {
            ch: self.REASON_OFF for ch in range(1, num_channels + 1)
        }
        
        # Timer information (only for single-channel mode 1)
        self._timer_info: Dict[str, Any] = {}
        
        _LOGGER.debug(
            "EWneo %d-channel switch %s initialized",
            num_channels, serial_number
        )
    
    @property
    def num_channels(self) -> int:
        """Return number of channels."""
        return self._num_channels
    
    def _get_model_name(self, language: str = DEFAULT_LANGUAGE) -> str:
        """Get detailed model name for device info.
        
        Uses translation system for proper language support.
        """
        channel_translation_keys = {
            1: "ewneo.switch",
            2: "ewneo.dual_switch",
            4: "ewneo.quad_switch"
        }
        key = channel_translation_keys.get(self._num_channels, "ewneo.switch")
        return translate(key, language)
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["switch"]
    
    def _parse_mode0_state(self, state_word: int) -> None:
        """Parse Mode 0 state word (normal on/off state).
        
        Format depends on channel count:
        
        Single channel (bits 31-24 used):
        - Bits 31-27: Switch counter
        - Bits 26-24: Reason code
        - Bits 23-0: Reserved
        
        2-channel (bits 31-16 used):
        - Bits 31-27: Channel 1 counter
        - Bits 26-24: Channel 1 reason
        - Bits 23-19: Channel 2 counter
        - Bits 18-16: Channel 2 reason
        - Bits 15-0: Reserved
        
        4-channel (all bits used):
        - Bits 31-27: Channel 1 counter
        - Bits 26-24: Channel 1 reason
        - Bits 23-19: Channel 2 counter
        - Bits 18-16: Channel 2 reason
        - Bits 15-11: Channel 3 counter
        - Bits 10-8: Channel 3 reason
        - Bits 7-3: Channel 4 counter
        - Bits 2-0: Channel 4 reason
        """
        old_states = self._switch_states.copy()
        
        if self._num_channels == 1:
            self._parse_single_channel_state(state_word)
        elif self._num_channels == 2:
            self._parse_dual_channel_state(state_word)
        else:  # 4 channels
            self._parse_quad_channel_state(state_word)
        
        # Log state changes
        self._log_state_changes(old_states)
    
    def _parse_single_channel_state(self, state_word: int) -> None:
        """Parse state word for single-channel switch."""
        counter = (state_word >> 27) & 0x1F   # Bits 31-27
        reason = (state_word >> 24) & 0x07    # Bits 26-24
        
        self._update_channel_state(1, counter, reason)
    
    def _parse_dual_channel_state(self, state_word: int) -> None:
        """Parse state word for dual-channel switch."""
        # Channel 1: bits 31-24
        ch1_counter = (state_word >> 27) & 0x1F
        ch1_reason = (state_word >> 24) & 0x07
        
        # Channel 2: bits 23-16
        ch2_counter = (state_word >> 19) & 0x1F
        ch2_reason = (state_word >> 16) & 0x07
        
        self._update_channel_state(1, ch1_counter, ch1_reason)
        self._update_channel_state(2, ch2_counter, ch2_reason)
    
    def _parse_quad_channel_state(self, state_word: int) -> None:
        """Parse state word for quad-channel switch."""
        # Channel 1: bits 31-24
        ch1_counter = (state_word >> 27) & 0x1F
        ch1_reason = (state_word >> 24) & 0x07
        
        # Channel 2: bits 23-16
        ch2_counter = (state_word >> 19) & 0x1F
        ch2_reason = (state_word >> 16) & 0x07
        
        # Channel 3: bits 15-8
        ch3_counter = (state_word >> 11) & 0x1F
        ch3_reason = (state_word >> 8) & 0x07
        
        # Channel 4: bits 7-0
        ch4_counter = (state_word >> 3) & 0x1F
        ch4_reason = state_word & 0x07
        
        self._update_channel_state(1, ch1_counter, ch1_reason)
        self._update_channel_state(2, ch2_counter, ch2_reason)
        self._update_channel_state(3, ch3_counter, ch3_reason)
        self._update_channel_state(4, ch4_counter, ch4_reason)
    
    def _update_channel_state(self, channel: int, counter: int, reason: int) -> None:
        """Update state for a single channel.
        
        Args:
            channel: Channel number (1-indexed)
            counter: Switch counter value
            reason: Reason code (1=off, 2=on, 3=timer, 5=logic)
        """
        self._switch_counters[channel] = counter
        self._switch_reasons[channel] = reason
        # Reason codes 2, 3, 5 indicate ON state
        self._switch_states[channel] = reason in [
            self.REASON_ON, self.REASON_TIMER, self.REASON_LOGIC
        ]
    
    def _parse_extended_mode_state(self, state_word: int) -> None:
        """Parse extended mode states.
        
        Mode 1: Timer state (only for single-channel)
        """
        if self._mode == 1 and self._num_channels == 1:
            self._parse_timer_state(state_word)
        else:
            super()._parse_extended_mode_state(state_word)
    
    def _parse_timer_state(self, state_word: int) -> None:
        """Parse timer state word (mode 1, single-channel only).
        
        Format:
        - Bits 31-28: Start exponent
        - Bits 27-20: Start mantissa
        - Bits 19-16: Current exponent
        - Bits 15-8: Current mantissa
        - Bits 7-2: Reserved
        - Bit 1: Default duration flag
        - Bit 0: Warning active flag
        """
        start_exp = (state_word >> 28) & 0x0F
        start_mantissa = (state_word >> 20) & 0xFF
        current_exp = (state_word >> 16) & 0x0F
        current_mantissa = (state_word >> 8) & 0xFF
        default_duration = bool(state_word & 0x02)
        warning_active = bool(state_word & 0x01)
        
        # Calculate durations using mantissa * 2^exponent
        start_duration = (
            start_mantissa * (2 ** start_exp) if start_mantissa > 0 else 0
        )
        current_duration = (
            current_mantissa * (2 ** current_exp) if current_mantissa > 0 else 0
        )
        
        # Timer mode means switch is on
        self._switch_states[1] = True
        self._timer_info = {
            "start_duration": start_duration,
            "current_duration": current_duration,
            "default_duration": default_duration,
            "warning_active": warning_active
        }
        
        _LOGGER.info(
            "EWneo switch %s: Timer mode - remaining %ds of %ds (warning: %s)",
            self.serial_number, 
            current_duration, 
            start_duration,
            "active" if warning_active else "inactive"
        )
    
    def _log_state_changes(self, old_states: Dict[int, bool]) -> None:
        """Log state changes for all channels."""
        for channel in range(1, self._num_channels + 1):
            if old_states[channel] != self._switch_states[channel]:
                reason_text = self.REASON_TEXT_MAP.get(
                    self._switch_reasons[channel], "unknown"
                )
                if self._num_channels == 1:
                    _LOGGER.info(
                        "EWneo switch %s: State=%s (reason: %s, counter: %d)",
                        self.serial_number,
                        "ON" if self._switch_states[channel] else "OFF",
                        reason_text,
                        self._switch_counters[channel]
                    )
                else:
                    _LOGGER.info(
                        "EWneo %dch switch %s CH%d: State=%s (reason: %s, counter: %d)",
                        self._num_channels,
                        self.serial_number,
                        channel,
                        "ON" if self._switch_states[channel] else "OFF",
                        reason_text,
                        self._switch_counters[channel]
                    )
    
    def _get_device_data(self) -> Dict[str, Any]:
        """Get switch data for coordinator (implements abstract method)."""
        return self.get_switch_data()
    
    def get_switch_data(self) -> Dict[str, Any]:
        """Get current switch data for all channels."""
        data = {
            "num_channels": self._num_channels,
            "last_seen": self._last_seen,
            "mode": self._mode,
        }
        
        if self._num_channels == 1:
            # Single channel - flat structure for backward compatibility
            data.update({
                "state": self._switch_states[1],
                "switch_counter": self._switch_counters[1],
                "switch_reason": self._switch_reasons[1],
                "reason_text": self.REASON_TEXT_MAP.get(
                    self._switch_reasons[1], "unknown"
                )
            })
            
            # Add timer info if in timer mode
            if self._mode == 1 and self._timer_info:
                data["timer_info"] = self._timer_info.copy()
        else:
            # Multi-channel - structured data
            data["states"] = self._switch_states.copy()
            data["reason_texts"] = {
                ch: self.REASON_TEXT_MAP.get(self._switch_reasons[ch], "unknown")
                for ch in range(1, self._num_channels + 1)
            }
        
        return data
    
    def _create_switch_state_command(
        self, 
        turn_on: bool, 
        channel: int = 1,
        timer_duration: Optional[int] = None
    ) -> tuple[int, list]:
        """Create state command for EWneo switch.
        
        Args:
            turn_on: True to turn on, False to turn off
            channel: Channel number (1-indexed)
            timer_duration: Optional timer duration in seconds (single-channel only)
            
        Returns:
            Tuple of (mode, state_bytes) for EwbChangeState
        """
        if timer_duration is not None and self._num_channels == 1:
            # Mode 1: Timer with specific duration
            return self._create_timer_command(timer_duration)
        else:
            # Mode 0: Simple on/off
            return self._create_simple_command(turn_on, channel)
    
    def _create_simple_command(self, turn_on: bool, channel: int = 1) -> tuple[int, list]:
        """Create simple on/off command (Mode 0)."""
        reason_code = self.REASON_ON if turn_on else self.REASON_OFF
        
        # Build state word based on channel count
        if self._num_channels == 1:
            state_word = (reason_code << 24)
        else:
            # For multi-channel, we need to preserve other channel states
            # and only change the target channel
            state_word = self._build_multichannel_state_word(channel, reason_code)
        
        state_bytes = [
            (state_word >> 24) & 0xFF,
            (state_word >> 16) & 0xFF,
            (state_word >> 8) & 0xFF,
            state_word & 0xFF
        ]
        
        _LOGGER.debug(
            "EWneo switch %s: Simple command - CH%d %s (reason: %d)",
            self.serial_number,
            channel,
            "ON" if turn_on else "OFF",
            reason_code
        )
        
        return (0, state_bytes)
    
    def _build_multichannel_state_word(self, target_channel: int, reason: int) -> int:
        """Build state word for multi-channel switch, preserving other channel states."""
        state_word = 0
        
        for ch in range(1, self._num_channels + 1):
            # Use new reason for target channel, current reason for others
            ch_reason = reason if ch == target_channel else self._switch_reasons[ch]
            ch_counter = self._switch_counters[ch]
            
            if self._num_channels == 2:
                if ch == 1:
                    state_word |= (ch_counter << 27) | (ch_reason << 24)
                else:
                    state_word |= (ch_counter << 19) | (ch_reason << 16)
            else:  # 4 channels
                if ch == 1:
                    state_word |= (ch_counter << 27) | (ch_reason << 24)
                elif ch == 2:
                    state_word |= (ch_counter << 19) | (ch_reason << 16)
                elif ch == 3:
                    state_word |= (ch_counter << 11) | (ch_reason << 8)
                else:
                    state_word |= (ch_counter << 3) | ch_reason
        
        return state_word
    
    def _create_timer_command(self, duration: int, warning: bool = False) -> tuple[int, list]:
        """Create timer command (Mode 1, single-channel only)."""
        if duration <= 0:
            _LOGGER.warning("Invalid timer duration %d, using simple on", duration)
            return self._create_simple_command(True)
        
        # Calculate exponent and mantissa
        exponent = 0
        while (duration >> exponent) > 255 and exponent < 15:
            exponent += 1
        
        mantissa = min(255, duration >> exponent)
        if mantissa == 0:
            mantissa = 1
        
        # Create state word
        state_word = (exponent << 28) | (mantissa << 20) | (1 if warning else 0)
        
        state_bytes = [
            (state_word >> 24) & 0xFF,
            (state_word >> 16) & 0xFF,
            (state_word >> 8) & 0xFF,
            state_word & 0xFF
        ]
        
        actual_duration = mantissa * (2 ** exponent)
        _LOGGER.debug(
            "EWneo switch %s: Timer command - %ds (exp: %d, mantissa: %d)",
            self.serial_number,
            actual_duration,
            exponent,
            mantissa
        )
        
        return (1, state_bytes)
    
    async def set_state(
        self, 
        state: bool, 
        channel: int = 1,
        timer_duration: Optional[int] = None
    ) -> bool:
        """Set switch state for a specific channel.
        
        Args:
            state: True for on, False for off
            channel: Channel number (1-indexed)
            timer_duration: Optional timer duration in seconds (single-channel only)
            
        Returns:
            True if state was set successfully
        """
        if channel not in range(1, self._num_channels + 1):
            _LOGGER.error(
                "Invalid channel %d for %d-channel switch",
                channel, self._num_channels
            )
            return False
        
        try:
            # Update internal state
            self._switch_states[channel] = state
            
            _LOGGER.info(
                "EWneo %dch switch %s CH%d set to %s%s",
                self._num_channels,
                self.serial_number,
                channel,
                "ON" if state else "OFF",
                f" (timer: {timer_duration}s)" if timer_duration else ""
            )
            
            return True
        except Exception as e:
            _LOGGER.error(
                "Failed to set EWneo switch %s CH%d state: %s",
                self.serial_number, channel, e
            )
            return False
    
    async def get_state(self, channel: int = 1) -> Optional[bool]:
        """Get current switch state for a specific channel."""
        if channel not in range(1, self._num_channels + 1):
            return None
        return self._switch_states[channel]
    
    async def turn_on_with_timer(
        self, 
        duration: int, 
        warning: bool = False
    ) -> bool:
        """Turn on switch with specific timer duration (single-channel only).
        
        Args:
            duration: Timer duration in seconds
            warning: Enable switch-off warning
            
        Returns:
            True if successful
        """
        if self._num_channels != 1:
            _LOGGER.warning(
                "Timer mode only available for single-channel switch, not %d-channel",
                self._num_channels
            )
            return False
        
        try:
            # Create timer command
            mode, state_bytes = self._create_timer_command(duration, warning)
            
            gateway_serial = self._gateway_serial or self._device_info.get("gateway_serial")
            if not gateway_serial:
                _LOGGER.error(
                    "EWneo switch %s: No gateway serial available",
                    self.serial_number
                )
                return False
            
            _LOGGER.info(
                "Setting EWneo switch %s timer: %ds (warning: %s)",
                self.serial_number,
                duration,
                "on" if warning else "off"
            )
            
            if self._transceiver and hasattr(self._transceiver, 'rx11_ewb_change_state'):
                result = await self._transceiver.rx11_ewb_change_state(
                    gateway_serial, self.serial_number, mode, state_bytes
                )
                
                if result:
                    recent_mode, recent_state_bytes = result
                    raw_response = bytes([recent_mode] + recent_state_bytes)
                    self._parse_state_response(raw_response)
                    return True
            
            return False
            
        except Exception as e:
            _LOGGER.error(
                "Failed to set EWneo switch %s timer: %s",
                self.serial_number, e
            )
            return False


# Factory functions
def create_rx11_ewneo_switch(
    serial_number: str,
    device_info: Dict[str, Any] = None,
    num_channels: int = 1,
    **kwargs
) -> RX11EWneoSwitch:
    """Factory function to create EWneo switch."""
    device_info = device_info or {}
    return RX11EWneoSwitch(
        serial_number=serial_number,
        num_channels=num_channels,
        name=device_info.get('name'),
        device_info=device_info,
        **kwargs
    )


def create_rx11_ewneo_dual_switch(
    serial_number: str,
    device_info: Dict[str, Any] = None,
    **kwargs
) -> RX11EWneoSwitch:
    """Factory function to create dual-channel EWneo switch (backward compatibility)."""
    device_info = device_info or {}
    return RX11EWneoSwitch(
        serial_number=serial_number,
        num_channels=2,
        name=device_info.get('name'),  # Name wird im __init__ generiert, falls None
        device_info=device_info,
        **kwargs
    )


def create_rx11_ewneo_quad_switch(
    serial_number: str,
    device_info: Dict[str, Any] = None,
    **kwargs
) -> RX11EWneoSwitch:
    """Factory function to create quad-channel EWneo switch (backward compatibility)."""
    device_info = device_info or {}
    return RX11EWneoSwitch(
        serial_number=serial_number,
        num_channels=4,
        name=device_info.get('name'),  # Name wird im __init__ generiert, falls None
        device_info=device_info,
        **kwargs
    )


# Backward compatibility aliases
RX11EWneoMultiChannelSwitch = RX11EWneoSwitch
RX11EWneoDualSwitch = RX11EWneoSwitch
RX11EWneoQuadSwitch = RX11EWneoSwitch
