"""Unified EWneo motor implementation for RX11 transceiver.

This module provides a single, configurable motor class that handles
all channel counts (1, 2, 4). This replaces the separate single/dual/quad
motor classes with a unified implementation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import DeviceType, DeviceSubtype, OperatingMode
from .base import EWneoBaseDevice

_LOGGER = logging.getLogger(__name__)


class RX11EWneoMotor(EWneoBaseDevice):
    """Unified EWneo motor implementation for RX11 transceiver.
    
    Handles 1, 2, or 4 channel EWneo motor devices for covers and blinds.
    Inherits common EWB state parsing from EWneoBaseDevice.
    
    Channel configuration:
    - 1 channel: Single motor
    - 2 channels: Dual motor
    - 4 channels: Quad motor
    
    State word format:
    - Single channel: Full motor status with position and configuration
    - Multi-channel Mode 0: Summary status codes + tilt flags for all motors
    - Multi-channel Mode 2/10/18/26: Full info for individual motor
    
    Motor status codes (7-bit):
    - 117: Runtime measurement in progress
    - 118: Tilt measurement in progress
    - 119: Stopped, terrace function active
    - 120: Moving to open (runtime mode)
    - 121: Moving to close (runtime mode)
    - 122: Moving to open (120s mode)
    - 123: Moving to close (120s mode)
    - 124: Moving to open (position mode)
    - 125: Moving to close (position mode)
    - 126: Stopped
    """
    
    # Channel count to device type mapping
    CHANNEL_TO_DEVICE_TYPE = {
        1: DeviceType.EWNEO_MOTOR,
        2: DeviceType.EWNEO_DUAL_MOTOR,
        4: DeviceType.EWNEO_QUAD_MOTOR,
    }
    
    # Channel count to subtype mapping
    CHANNEL_TO_SUBTYPE = {
        1: DeviceSubtype.MOTOR,
        2: DeviceSubtype.DUAL_MOTOR,
        4: DeviceSubtype.QUAD_MOTOR,
    }
    
    # Channel count to operating mode mapping
    CHANNEL_TO_MODE = {
        1: OperatingMode.SINGLE_CHANNEL,
        2: OperatingMode.DUAL_CHANNEL,
        4: OperatingMode.QUAD_CHANNEL,
    }
    
    # Motor status code mappings
    STATUS_CODES = {
        117: ("calibrating", "Runtime measurement in progress"),
        118: ("calibrating", "Tilt measurement in progress"),
        119: ("stopped", "Motor stopped, terrace function active"),
        120: ("opening", "Moving to open direction (runtime)"),
        121: ("closing", "Moving to close direction (runtime)"),
        122: ("opening", "Moving to open direction (120s)"),
        123: ("closing", "Moving to close direction (120s)"),
        124: ("opening", "Moving to open direction (position)"),
        125: ("closing", "Moving to close direction (position)"),
        126: ("stopped", "Motor has stopped"),
    }
    
    # Position modes for individual channel query
    CHANNEL_MODE_MAP = {
        1: 2,   # Mode 2 for channel 1
        2: 10,  # Mode 10 for channel 2
        3: 18,  # Mode 18 for channel 3
        4: 26,  # Mode 26 for channel 4
    }
    
    def __init__(
        self,
        serial_number: str,
        num_channels: int = 1,
        name: Optional[str] = None,
        device_info: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        """Initialize EWneo motor.
        
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
                "Invalid channel count %d for motor, defaulting to 1",
                num_channels
            )
            num_channels = 1
        
        self._num_channels = num_channels
        self._device_info = device_info or {}
        
        # Determine device type, subtype, and operating mode
        device_type = self.CHANNEL_TO_DEVICE_TYPE[num_channels]
        subtype = self.CHANNEL_TO_SUBTYPE[num_channels]
        
        super().__init__(
            serial_number=serial_number,
            device_type=device_type,
            subtype=subtype,
            name=name,
            **kwargs
        )
        
        self.operating_mode = self.CHANNEL_TO_MODE[num_channels]
        
        # Initialize per-channel state (1-indexed)
        self._motor_states: Dict[int, str] = {
            ch: "stopped" for ch in range(1, num_channels + 1)
        }
        self._positions: Dict[int, Optional[int]] = {
            ch: None for ch in range(1, num_channels + 1)
        }
        
        # Motor status information
        self._motor_status_codes: Dict[int, int] = {
            ch: 126 for ch in range(1, num_channels + 1)
        }
        self._motor_status_texts: Dict[int, str] = {
            ch: "stopped" for ch in range(1, num_channels + 1)
        }
        
        # Position information (0-100 or 126=unknown)
        self._current_positions: Dict[int, int] = {
            ch: 126 for ch in range(1, num_channels + 1)
        }
        self._target_positions: Dict[int, int] = {
            ch: 126 for ch in range(1, num_channels + 1)
        }
        
        # Tilt and configuration flags
        self._recent_tilts: Dict[int, bool] = {
            ch: False for ch in range(1, num_channels + 1)
        }
        self._auto_tilts: Dict[int, bool] = {
            ch: False for ch in range(1, num_channels + 1)
        }
        self._runtime_measured: Dict[int, bool] = {
            ch: False for ch in range(1, num_channels + 1)
        }
        self._tilt_measured: Dict[int, bool] = {
            ch: False for ch in range(1, num_channels + 1)
        }
        self._terrace_functions: Dict[int, bool] = {
            ch: False for ch in range(1, num_channels + 1)
        }
        self._stored_positions: Dict[int, int] = {
            ch: 0 for ch in range(1, num_channels + 1)
        }
        
        _LOGGER.debug(
            "EWneo %d-channel motor %s initialized",
            num_channels, serial_number[-6:]
        )
    
    @property
    def num_channels(self) -> int:
        """Return number of channels."""
        return self._num_channels
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["cover"]
    
    def _parse_mode0_state(self, state_word: int) -> None:
        """Parse Mode 0 state word.
        
        Format depends on channel count:
        
        Single channel (full status):
        - Bits 31-25: Motor status code (117-126)
        - Bit 24: Reserved
        - Bits 23-17: Current position (0-100 or 126=unknown)
        - Bit 16: Recently tilted to horizontal
        - Bits 15-9: Target position (0-100 or 126=unknown)
        - Bit 8: Auto-tilt after positioning
        - Bit 7: Runtime measured
        - Bit 6: Tilt measured
        - Bits 5-3: Reserved
        - Bit 2: Terrace function active
        - Bits 1-0: Stored position (0-3)
        
        Multi-channel (summary):
        - Each motor: 7-bit status + 1-bit tilt flag
        - 2-channel: CH1 bits 31-24, CH2 bits 23-16
        - 4-channel: CH1 bits 31-24, CH2 bits 23-16, CH3 bits 15-8, CH4 bits 7-0
        """
        if self._num_channels == 1:
            self._parse_single_channel_state(state_word)
        elif self._num_channels == 2:
            self._parse_dual_channel_summary(state_word)
        else:  # 4 channels
            self._parse_quad_channel_summary(state_word)
    
    def _parse_single_channel_state(self, state_word: int) -> None:
        """Parse full state for single-channel motor."""
        channel = 1
        
        # Extract motor status code (bits 31-25)
        self._motor_status_codes[channel] = (state_word >> 25) & 0x7F
        
        # Bit 24 is reserved
        
        # Extract position information
        self._current_positions[channel] = (state_word >> 17) & 0x7F
        self._recent_tilts[channel] = bool(state_word & (1 << 16))
        self._target_positions[channel] = (state_word >> 9) & 0x7F
        self._auto_tilts[channel] = bool(state_word & (1 << 8))
        
        # Extract configuration flags
        self._runtime_measured[channel] = bool(state_word & (1 << 7))
        self._tilt_measured[channel] = bool(state_word & (1 << 6))
        self._terrace_functions[channel] = bool(state_word & (1 << 2))
        self._stored_positions[channel] = state_word & 0x03
        
        # Interpret status and update position
        self._interpret_motor_status(channel)
        self._update_position_info(channel)
        
        # Log state change
        self._log_motor_state_change(channel)
    
    def _parse_dual_channel_summary(self, state_word: int) -> None:
        """Parse summary state for dual-channel motor."""
        # Motor 1: bits 31-24
        motor1_status = (state_word >> 25) & 0x7F
        motor1_tilt = bool(state_word & (1 << 24))
        
        # Motor 2: bits 23-16
        motor2_status = (state_word >> 17) & 0x7F
        motor2_tilt = bool(state_word & (1 << 16))
        
        self._update_motor_summary(1, motor1_status, motor1_tilt)
        self._update_motor_summary(2, motor2_status, motor2_tilt)
    
    def _parse_quad_channel_summary(self, state_word: int) -> None:
        """Parse summary state for quad-channel motor."""
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
        
        self._update_motor_summary(1, motor1_status, motor1_tilt)
        self._update_motor_summary(2, motor2_status, motor2_tilt)
        self._update_motor_summary(3, motor3_status, motor3_tilt)
        self._update_motor_summary(4, motor4_status, motor4_tilt)
    
    def _update_motor_summary(
        self, 
        channel: int, 
        status_code: int, 
        tilt: bool
    ) -> None:
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
    
    def _parse_extended_mode_state(self, state_word: int) -> None:
        """Parse extended mode states.
        
        Modes 2, 10, 18, 26 provide full info for individual channels.
        """
        # Check if this is a channel-specific mode
        for channel, mode in self.CHANNEL_MODE_MAP.items():
            if self._mode == mode and channel <= self._num_channels:
                self._parse_channel_full_state(channel, state_word)
                return
        
        # Unknown mode
        super()._parse_extended_mode_state(state_word)
    
    def _parse_channel_full_state(self, channel: int, state_word: int) -> None:
        """Parse full state for a specific channel (multi-channel mode 2/10/18/26)."""
        # Same format as single-channel mode 0
        self._motor_status_codes[channel] = (state_word >> 25) & 0x7F
        self._current_positions[channel] = (state_word >> 17) & 0x7F
        self._recent_tilts[channel] = bool(state_word & (1 << 16))
        self._target_positions[channel] = (state_word >> 9) & 0x7F
        self._auto_tilts[channel] = bool(state_word & (1 << 8))
        self._runtime_measured[channel] = bool(state_word & (1 << 7))
        self._tilt_measured[channel] = bool(state_word & (1 << 6))
        self._terrace_functions[channel] = bool(state_word & (1 << 2))
        self._stored_positions[channel] = state_word & 0x03
        
        self._interpret_motor_status(channel)
        self._update_position_info(channel)
        self._log_motor_state_change(channel)
    
    def _interpret_motor_status(self, channel: int) -> None:
        """Interpret motor status code into readable state."""
        status_code = self._motor_status_codes[channel]
        
        if status_code in self.STATUS_CODES:
            state, text = self.STATUS_CODES[status_code]
            self._motor_states[channel] = state
            self._motor_status_texts[channel] = text
            
            # Infer runtime measurement from position-based movement
            if status_code in [124, 125]:
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
            # Convert from protocol (0=open, 100=closed) to HA (0=closed, 100=open)
            self._positions[channel] = 100 - self._current_positions[channel]
        # If 126 (unknown), keep existing position
    
    def _log_motor_state_change(self, channel: int) -> None:
        """Log motor state change for specific channel."""
        ch_prefix = "" if self._num_channels == 1 else f"CH{channel}: "
        
        if not self._runtime_measured[channel]:
            # Positionless mode
            flags = []
            if self._terrace_functions.get(channel):
                flags.append("terrace")
            if self._recent_tilts[channel]:
                flags.append("tilted")
            flags.append("no-runtime-cal")
            
            flags_text = f" [{', '.join(flags)}]" if flags else ""
            _LOGGER.info(
                "EWneo %dch motor %s %s%s (positionless)%s",
                self._num_channels,
                self.serial_number[-6:],
                ch_prefix,
                self._motor_status_texts[channel],
                flags_text
            )
        else:
            # Position-aware mode
            pos = self._positions[channel]
            pos_text = f"{pos}%" if pos is not None else "unknown"
            
            target_pos = self._target_positions[channel]
            target_text = f"{100 - target_pos}%" if target_pos <= 100 else "unknown"
            
            _LOGGER.info(
                "EWneo %dch motor %s %s%s, Position=%s, Target=%s",
                self._num_channels,
                self.serial_number[-6:],
                ch_prefix,
                self._motor_status_texts[channel],
                pos_text,
                target_text
            )
    
    def _get_device_data(self) -> Dict[str, Any]:
        """Get motor data for coordinator (implements abstract method)."""
        return self.get_motor_data()
    
    def get_motor_data(self) -> Dict[str, Any]:
        """Get comprehensive motor data for all channels."""
        data = {
            "num_channels": self._num_channels,
            "last_seen": self._last_seen,
            "mode": self._mode,
        }
        
        if self._num_channels == 1:
            # Single channel - flat structure for backward compatibility
            channel = 1
            data.update({
                "state": self._motor_states[channel],
                "position": self._positions[channel],
                "motor_status": self._motor_status_texts[channel],
                "control_mode": "position" if self._runtime_measured[channel] else "positionless",
                "is_calibrating": self._motor_status_codes[channel] in [117, 118],
                "is_moving": self._motor_status_codes[channel] in [120, 121, 122, 123, 124, 125],
            })
            
            # Add position-specific data if runtime measurement is done
            if self._runtime_measured[channel]:
                curr_pos = self._current_positions[channel]
                target_pos = self._target_positions[channel]
                data.update({
                    "current_position_pct": 100 - curr_pos if curr_pos <= 100 else None,
                    "target_position_pct": 100 - target_pos if target_pos <= 100 else None,
                })
        else:
            # Multi-channel - structured data
            data.update({
                "states": self._motor_states.copy(),
                "positions": self._positions.copy(),
            })
            
            # Per-channel status
            for channel in range(1, self._num_channels + 1):
                data[f"ch{channel}_status"] = self._motor_status_texts[channel]
                data[f"ch{channel}_control_mode"] = (
                    "position" if self._runtime_measured[channel] else "positionless"
                )
        
        return data
    
    async def set_state(
        self, 
        command: str, 
        channel: int = 1,
        position: Optional[int] = None
    ) -> bool:
        """Set motor state for a specific channel.
        
        Args:
            command: Motor command (up/down/stop/open/close)
            channel: Channel number (1-indexed)
            position: Optional target position (0-100)
            
        Returns:
            True if state was set successfully
        """
        if channel not in range(1, self._num_channels + 1):
            _LOGGER.error(
                "Invalid channel %d for %d-channel motor",
                channel, self._num_channels
            )
            return False
        
        # Normalize command names
        command = command.lower()
        if command == "open":
            command = "up"
        elif command == "close":
            command = "down"
        
        if command not in ["up", "down", "stop"]:
            _LOGGER.error("Invalid motor command: %s", command)
            return False
        
        try:
            # Check for position control availability
            if position is not None and not self._runtime_measured[channel]:
                _LOGGER.warning(
                    "EWneo motor %s CH%d: Position control not available - "
                    "runtime measurement required",
                    self.serial_number[-6:], channel
                )
                return False
            
            pos_text = f" (position: {position}%)" if position is not None else ""
            _LOGGER.info(
                "Setting EWneo %dch motor %s CH%d to %s%s",
                self._num_channels,
                self.serial_number[-6:],
                channel,
                command,
                pos_text
            )
            
            # Update internal state
            self._motor_states[channel] = command
            if position is not None:
                self._positions[channel] = position
            
            return True
            
        except Exception as e:
            _LOGGER.error(
                "Failed to set EWneo motor %s CH%d state: %s",
                self.serial_number[-6:], channel, e
            )
            return False
    
    async def get_state(
        self, 
        channel: int = 1
    ) -> tuple[Optional[str], Optional[int]]:
        """Get current motor state and position for specific channel.
        
        Returns:
            tuple: (state, position) where position is None in positionless mode
        """
        if channel not in range(1, self._num_channels + 1):
            return None, None
        return self._motor_states[channel], self._positions[channel]
    
    @property
    def gateway_serial(self) -> Optional[str]:
        """Get gateway serial number."""
        return self._gateway_serial or self._device_info.get("gateway_serial")


# Factory functions
def create_rx11_ewneo_motor(
    serial_number: str,
    device_info: Dict[str, Any] = None,
    num_channels: int = 1,
    name: Optional[str] = None,
    **kwargs
) -> RX11EWneoMotor:
    """Factory function to create EWneo motor."""
    device_info = device_info or {}
    return RX11EWneoMotor(
        serial_number=serial_number,
        num_channels=num_channels,
        name=name or device_info.get('name'),
        device_info=device_info,
        **kwargs
    )


def create_rx11_ewneo_dual_motor(
    serial_number: str,
    device_info: Dict[str, Any] = None,
    name: Optional[str] = None,
    **kwargs
) -> RX11EWneoMotor:
    """Factory function to create dual-channel EWneo motor (backward compatibility)."""
    device_info = device_info or {}
    return RX11EWneoMotor(
        serial_number=serial_number,
        num_channels=2,
        name=name or device_info.get('name') or f"EWneo Dual Motor {serial_number[-6:]}",
        device_info=device_info,
        **kwargs
    )


def create_rx11_ewneo_quad_motor(
    serial_number: str,
    device_info: Dict[str, Any] = None,
    name: Optional[str] = None,
    **kwargs
) -> RX11EWneoMotor:
    """Factory function to create quad-channel EWneo motor (backward compatibility)."""
    device_info = device_info or {}
    return RX11EWneoMotor(
        serial_number=serial_number,
        num_channels=4,
        name=name or device_info.get('name') or f"EWneo Quad Motor {serial_number[-6:]}",
        device_info=device_info,
        **kwargs
    )


# Backward compatibility aliases
RX11EWneoMultiChannelMotor = RX11EWneoMotor
RX11EWneoDualMotor = RX11EWneoMotor
RX11EWneoQuadMotor = RX11EWneoMotor
