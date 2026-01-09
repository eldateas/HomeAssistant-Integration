"""EWneo multi-channel motor implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import DeviceType, DeviceSubtype, OperatingMode
from .base import EWneoBaseDevice

_LOGGER = logging.getLogger(__name__)


class RX11EWneoMultiChannelMotor(EWneoBaseDevice):
    """EWneo multi-channel motor implementation for RX11 transceiver.
    
    Handles 2-channel and 4-channel EWneo motor devices for covers and blinds.
    Inherits common EWB state parsing from EWneoBaseDevice.
    
    Mode 0: Summary for all motors (condensed status codes + tilt flags)
    Mode 2/10/18/26: Full info for individual motor (ch1/ch2/ch3/ch4)
    """
    
    def __init__(self, *args, num_channels: int = 2, device_info=None, **kwargs):
        """Initialize EWneo multi-channel motor.
        
        Args:
            num_channels: Number of channels (2 or 4)
            device_info: Optional device information dictionary
        """
        if num_channels not in [2, 4]:
            raise ValueError(f"Invalid number of channels: {num_channels}. Must be 2 or 4.")
        
        self._num_channels = num_channels
        self._device_info = device_info or {}
        
        # Determine device type and subtype based on number of channels
        if num_channels == 2:
            device_type = DeviceType.EWNEO_DUAL_MOTOR
            subtype = DeviceSubtype.DUAL_MOTOR
            operating_mode = OperatingMode.DUAL_CHANNEL
        else:  # 4 channels
            device_type = DeviceType.EWNEO_QUAD_MOTOR
            subtype = DeviceSubtype.QUAD_MOTOR
            operating_mode = OperatingMode.QUAD_CHANNEL
        
        super().__init__(*args, device_type=device_type, subtype=subtype, **kwargs)
        # Note: self._mode is now in base class
        
        # Initialize channel states
        self._motor_states = {ch: "stop" for ch in range(1, num_channels + 1)}
        self._positions = {ch: None for ch in range(1, num_channels + 1)}  # None=positionless, 0-100=position
        
        # Motor status information
        self._motor_status_codes = {ch: 126 for ch in range(1, num_channels + 1)}  # 126 = stopped
        self._motor_status_texts = {ch: "stopped" for ch in range(1, num_channels + 1)}
        
        # Position information
        self._current_positions = {ch: 126 for ch in range(1, num_channels + 1)}  # 0-100 or 126=unknown
        self._target_positions = {ch: 126 for ch in range(1, num_channels + 1)}
        
        # Tilt and configuration flags
        self._recent_tilts = {ch: False for ch in range(1, num_channels + 1)}
        self._auto_tilts = {ch: False for ch in range(1, num_channels + 1)}
        self._runtime_measured = {ch: False for ch in range(1, num_channels + 1)}
        self._tilt_measured = {ch: False for ch in range(1, num_channels + 1)}
        self._terrace_functions = {ch: False for ch in range(1, num_channels + 1)}
        self._stored_positions = {ch: 0 for ch in range(1, num_channels + 1)}
        
        self.operating_mode = operating_mode
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["cover"]
    
    @property
    def num_channels(self) -> int:
        """Return number of channels."""
        return self._num_channels
    
    def _parse_mode0_state(self, state_word: int) -> None:
        """Parse Mode 0 state for multi-channel motor (summary mode).
        
        Mode 0 provides condensed summary for all motors.
        
        For 2-channel:
        - Bits 31-25: Motor 1 status code (117-126)
        - Bit 24: Motor 1 tilt flag
        - Bits 23-17: Motor 2 status code
        - Bit 16: Motor 2 tilt flag
        - Bits 15-0: Reserved
        
        For 4-channel:
        - Bits 31-25: Motor 1 status code
        - Bit 24: Motor 1 tilt flag
        - Bits 23-17: Motor 2 status code
        - Bit 16: Motor 2 tilt flag
        - Bits 15-9: Motor 3 status code
        - Bit 8: Motor 3 tilt flag
        - Bits 7-1: Motor 4 status code
        - Bit 0: Motor 4 tilt flag
        """
        if self._num_channels == 2:
            self._parse_2channel_summary(state_word)
        else:  # 4 channels
            self._parse_4channel_summary(state_word)
    
    def _parse_2channel_summary(self, state_word: int) -> None:
        """Parse summary state for 2-channel motor."""
        # Motor 1: bits 31-24
        motor1_status = (state_word >> 25) & 0x7F
        motor1_tilt = bool(state_word & (1 << 24))
        
        # Motor 2: bits 23-16
        motor2_status = (state_word >> 17) & 0x7F
        motor2_tilt = bool(state_word & (1 << 16))
        
        # Update states
        self._update_motor_summary(1, motor1_status, motor1_tilt)
        self._update_motor_summary(2, motor2_status, motor2_tilt)
    
    def _parse_4channel_summary(self, state_word: int) -> None:
        """Parse summary state for 4-channel motor."""
        # Motor 1: bits 31-24
        motor1_status = (state_word >> 25) & 0x7F
        motor1_tilt = bool(state_word & (1 << 24))
        
        # Motor 2: bits 23-16
        motor2_status = (state_word >> 17) & 0x7F
        motor2_tilt = bool(state_word & (1 << 16))
        
        # Motor 3: bits 15-8
        motor3_status = (state_word >> 9) & 0x7F
        motor3_tilt = bool(state_word & (1 << 8))
        
        # Motor 4: bits 7-0
        motor4_status = (state_word >> 1) & 0x7F
        motor4_tilt = bool(state_word & 0x01)
        
        # Update all motors
        self._update_motor_summary(1, motor1_status, motor1_tilt)
        self._update_motor_summary(2, motor2_status, motor2_tilt)
        self._update_motor_summary(3, motor3_status, motor3_tilt)
        self._update_motor_summary(4, motor4_status, motor4_tilt)
    
    def _update_motor_summary(self, channel: int, status_code: int, tilt: bool) -> None:
        """Update motor state from summary information."""
        old_state = self._motor_states[channel]
        old_position = self._positions[channel]
        
        self._motor_status_codes[channel] = status_code
        self._recent_tilts[channel] = tilt
        
        self._interpret_motor_status(channel)
        self._update_position_info(channel)
        
        # Log changes
        if old_state != self._motor_states[channel] or old_position != self._positions[channel]:
            self._log_motor_state_change(channel)
    
    def _interpret_motor_status(self, channel: int) -> None:
        """Interpret motor status code into readable state."""
        status_code = self._motor_status_codes[channel]
        
        status_map = {
            126: ("stopped", "Motor has stopped"),
            119: ("stopped", "Motor stopped, terrace function active"),
            120: ("opening", "Moving to open direction (runtime)"),
            121: ("closing", "Moving to close direction (runtime)"),
            122: ("opening", "Moving to open direction (120s)"),
            123: ("closing", "Moving to close direction (120s)"),
            124: ("opening", "Moving to open direction (position)"),
            125: ("closing", "Moving to close direction (position)"),
            117: ("calibrating", "Runtime measurement in progress"),
            118: ("calibrating", "Tilt measurement in progress")
        }
        
        if status_code in status_map:
            self._motor_states[channel], self._motor_status_texts[channel] = status_map[status_code]
            # Infer runtime measurement capability from status codes
            if status_code in [124, 125]:  # Position-based movement
                self._runtime_measured[channel] = True
        else:
            self._motor_states[channel] = "unknown"
            self._motor_status_texts[channel] = f"Unknown status code: {status_code}"
    
    def _update_position_info(self, channel: int) -> None:
        """Update position information based on current state."""
        if not self._runtime_measured[channel]:
            # Positionless mode - no position tracking available
            self._positions[channel] = None
            return
        
        # Use current position if known
        if self._current_positions[channel] <= 100:
            # Convert from protocol format (0=open, 100=closed) to HA format (0=closed, 100=open)
            self._positions[channel] = 100 - self._current_positions[channel]
    
    def _log_motor_state_change(self, channel: int) -> None:
        """Log motor state change for specific channel."""
        if not self._runtime_measured[channel]:
            # Positionless mode
            flags = []
            if self._terrace_functions.get(channel):
                flags.append("terrace")
            if self._recent_tilts[channel]:
                flags.append("tilted")
            flags.append("no-runtime-cal")
            
            flags_text = f" [{', '.join(flags)}]" if flags else ""
            _LOGGER.info("EWneo %dch motor %s CH%d: %s (positionless)%s", 
                        self._num_channels, self.serial_number[-6:], channel,
                        self._motor_status_texts[channel], flags_text)
        else:
            # Position-aware mode
            pos_text = f"{self._positions[channel]}%" if self._current_positions[channel] <= 100 else "unknown"
            _LOGGER.info("EWneo %dch motor %s CH%d: %s, Position=%s", 
                        self._num_channels, self.serial_number[-6:], channel,
                        self._motor_status_texts[channel], pos_text)
    
    def _get_device_data(self) -> Dict[str, Any]:
        """Get motor data for coordinator (implements abstract method)."""
        return self.get_motor_data()
    
    def get_motor_data(self) -> Dict[str, Any]:
        """Get comprehensive motor data for all channels."""
        data = {
            "states": self._motor_states.copy(),
            "positions": self._positions.copy(),
            "last_seen": self._last_seen,
            "num_channels": self._num_channels,
        }
        
        # Add per-channel status
        for channel in range(1, self._num_channels + 1):
            data[f"ch{channel}_status"] = self._motor_status_texts[channel]
            data[f"ch{channel}_control_mode"] = "position" if self._runtime_measured[channel] else "positionless"
        
        return data
    
    async def set_state(self, channel: int, command: str, position: Optional[int] = None) -> bool:
        """Set motor state for specific channel."""
        if channel not in range(1, self._num_channels + 1):
            _LOGGER.error("Invalid channel %d for %d-channel motor", channel, self._num_channels)
            return False
        
        if command not in ["up", "down", "stop", "open", "close"]:
            _LOGGER.error("Invalid command: %s", command)
            return False
        
        # Normalize command names
        if command == "open":
            command = "up"
        elif command == "close":
            command = "down"
        
        try:
            if not self._runtime_measured[channel] and position is not None:
                _LOGGER.warning("EWneo motor %s CH%d: Position control not available - runtime measurement required", 
                               self.serial_number[-6:], channel)
                return False
            
            pos_text = f" (position: {position}%)" if position is not None else ""
            _LOGGER.info("Setting EWneo %dch motor %s channel %d to %s%s", 
                        self._num_channels, self.serial_number[-6:], channel, command, pos_text)
            
            # TODO: Implement actual command sending via RX11
            self._motor_states[channel] = command
            if position is not None:
                self._positions[channel] = position
            return True
        except Exception as e:
            _LOGGER.error("Failed to set EWneo motor %s channel %d state: %s", 
                         self.serial_number[-6:], channel, e)
            return False
    
    async def get_state(self, channel: int) -> tuple[Optional[str], Optional[int]]:
        """Get current motor state and position for specific channel.
        
        Returns:
            tuple: (state, position) where position is None in positionless mode
        """
        if channel not in range(1, self._num_channels + 1):
            return None, None
        return self._motor_states[channel], self._positions[channel]
    
    @property
    def gateway_serial(self) -> Optional[str]:
        """Get gateway serial number from device info."""
        return self._device_info.get("gateway_serial")


# Factory functions for backward compatibility
def create_rx11_ewneo_dual_motor(
    serial_number: str,
    name: str = None,
    device_info: Dict[str, Any] = None,
    **kwargs
) -> Optional[RX11EWneoMultiChannelMotor]:
    """Create RX11 EWneo dual motor instance."""
    try:
        return RX11EWneoMultiChannelMotor(
            serial_number=serial_number,
            name=name or f"EWneo Dual Motor {serial_number[-6:]}",
            num_channels=2,
            device_info=device_info,
            **kwargs
        )
    except Exception as e:
        _LOGGER.error(f"Error creating EWneo dual motor: {e}")
        return None


def create_rx11_ewneo_quad_motor(
    serial_number: str,
    name: str = None,
    device_info: Dict[str, Any] = None,
    **kwargs
) -> Optional[RX11EWneoMultiChannelMotor]:
    """Create RX11 EWneo quad motor instance."""
    try:
        return RX11EWneoMultiChannelMotor(
            serial_number=serial_number,
            name=name or f"EWneo Quad Motor {serial_number[-6:]}",
            num_channels=4,
            device_info=device_info,
            **kwargs
        )
    except Exception as e:
        _LOGGER.error(f"Error creating EWneo quad motor: {e}")
        return None
