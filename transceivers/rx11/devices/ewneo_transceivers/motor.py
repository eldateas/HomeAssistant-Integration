"""EWneo motor implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import BaseReceiver, DeviceType, DeviceSubtype, OperatingMode

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EWNEO_STATE_CHANGE = [0x05, 0xF2]


class RX11EWneoMotor(BaseReceiver):
    """EWneo motor implementation for RX11 transceiver.
    
    Handles single-channel EWneo motor devices for covers and blinds.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize EWneo motor."""
        super().__init__(*args, device_type=DeviceType.EWNEO_MOTOR, 
                        subtype=DeviceSubtype.MOTOR, **kwargs)
        self._motor_state = "stop"
        self._position = None  # None=positionless mode, 0-100=position mode
        self._mode = 0  # Should always be 0 for motor
        
        # Motor status information (bits 31-25)
        self._motor_status_code = 126  # Default to stopped
        self._motor_status = "stopped"
        
        # Position information
        self._current_position = 126  # 0-100 or 126=unknown
        self._target_position = 126   # 0-100 or 126=unknown
        
        # Tilt information
        self._recent_tilt = False     # Bit 16: recently tilted to horizontal
        self._auto_tilt = False       # Bit 8: auto-tilt after positioning
        
        # Configuration flags
        self._runtime_measured = False    # Bit 7: runtime measurement done
        self._tilt_measured = False      # Bit 6: tilt measurement done
        self._terrace_function = False   # Bit 2: terrace function active
        self._stored_position = 0        # Bits 1-0: stored position (0-3)
        
        self._setup_operating_mode(**kwargs)
    
    def _setup_operating_mode(self, **kwargs) -> None:
        """Setup operating mode for motor."""
        self.operating_mode = OperatingMode.SINGLE_CHANNEL
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["cover"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for EWneo motor."""
        info_type = telegram_data.get("info_type")
        
        if info_type in TELEGRAM_EWNEO_STATE_CHANGE:
            self._handle_state_change(telegram_data)
        
        return self.get_motor_data()
    
    def _handle_state_change(self, telegram_data: Dict[str, Any]) -> None:
        """Handle state change telegram with 5-byte EWB_RCV parsing for motor."""
        data = telegram_data.get("data", {})
        
        # Parse the 5-byte response: 1 byte mode + 4 bytes state
        raw_data = data.get("raw_data")  # Should be 5 bytes
        if raw_data and len(raw_data) >= 5:
            self._parse_ewneo_motor_response(raw_data)
        else:
            # Fallback to simple state extraction if raw data not available
            command, position = self._parse_motor_command(data.get("motor_command"))
            
            if command and command != self._motor_state:
                self._motor_state = command
                if position is not None:
                    self._position = position
                
                _LOGGER.debug("EWneo motor %s: Command=%s, Position=%d%% (fallback)", 
                             self.serial_number[-6:], command, self._position)
    
    def _parse_motor_command(self, motor_value: Any) -> tuple[Optional[str], Optional[int]]:
        """Parse motor command from various input formats."""
        if motor_value is None:
            return None, None
        
        if isinstance(motor_value, str):
            command = motor_value.lower()
            if command in ["up", "down", "stop"]:
                return command, None
        
        if isinstance(motor_value, dict):
            command = motor_value.get("command", "stop").lower()
            position = motor_value.get("position")
            if position is not None:
                position = max(0, min(100, int(position)))
            return command, position
        
        return None, None
    
    def _parse_ewneo_motor_response(self, raw_data: bytes) -> None:
        """Parse 5-byte EWB_RCV response for EWneo motor.
        
        Format: 1 byte mode + 4 bytes motor state (big-endian)
        Mode should always be 0 for motor.
        """
        if len(raw_data) < 5:
            _LOGGER.warning("EWneo motor %s: Invalid response length %d, expected 5 bytes", 
                           self.serial_number[-6:], len(raw_data))
            return
            
        # Parse mode (byte 0) - should always be 0 for motor
        self._mode = raw_data[0]
        
        if self._mode != 0:
            _LOGGER.warning("EWneo motor %s: Unexpected mode %d, expected 0", 
                           self.serial_number[-6:], self._mode)
            return
        
        # Parse state (bytes 1-4, BIG-ENDIAN 32-bit word for motor)
        state_word = int.from_bytes(raw_data[1:5], byteorder='big')
        self._parse_motor_state_word(state_word)
    
    def _parse_motor_state_word(self, state_word: int) -> None:
        """Parse motor state word with comprehensive shutter/blind information."""
        # Extract motor status code (bits 31-25)
        self._motor_status_code = (state_word >> 25) & 0x7F
        
        # Bit 24 is reserved
        
        # Extract position information
        self._current_position = (state_word >> 17) & 0x7F  # Bits 23-17
        self._recent_tilt = bool(state_word & (1 << 16))     # Bit 16
        self._target_position = (state_word >> 9) & 0x7F    # Bits 15-9
        self._auto_tilt = bool(state_word & (1 << 8))        # Bit 8
        
        # Extract configuration flags
        self._runtime_measured = bool(state_word & (1 << 7))  # Bit 7
        self._tilt_measured = bool(state_word & (1 << 6))     # Bit 6
        # Bits 5-3 are reserved
        self._terrace_function = bool(state_word & (1 << 2))  # Bit 2
        self._stored_position = state_word & 0x03             # Bits 1-0
        
        # Interpret motor status code
        old_state = self._motor_state
        old_position = self._position
        
        self._interpret_motor_status()
        self._update_position_info()
        
        # Log state changes
        if (old_state != self._motor_state or old_position != self._position or 
            self._motor_status_code in [117, 118]):  # Always log measurement modes
            self._log_motor_state_change()
    
    def _interpret_motor_status(self) -> None:
        """Interpret motor status code into readable state."""
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
        
        if self._motor_status_code in status_map:
            self._motor_state, self._motor_status = status_map[self._motor_status_code]
        else:
            self._motor_state = "unknown"
            self._motor_status = f"Unknown status code: {self._motor_status_code}"
    
    def _update_position_info(self) -> None:
        """Update position information based on current state."""
        if not self._runtime_measured:
            # Positionless mode - no position tracking available
            self._position = None  # Indicates position control not available
            return
            
        # Use current position if known, otherwise keep existing
        if self._current_position <= 100:
            # Convert from protocol format (0=open, 100=closed) to HA format (0=closed, 100=open)
            self._position = 100 - self._current_position
        # If current_position is 126 (unknown), keep existing position
    
    def _log_motor_state_change(self) -> None:
        """Log comprehensive motor state information."""
        if not self._runtime_measured:
            # Positionless mode - only show basic state
            flags = []
            if self._terrace_function:
                flags.append("terrace")
            if self._recent_tilt:
                flags.append("tilted")
            flags.append("no-runtime-cal")
            
            flags_text = f" [{', '.join(flags)}]" if flags else ""
            _LOGGER.info("EWneo motor %s: %s (positionless mode)%s", 
                        self.serial_number[-6:], self._motor_status, flags_text)
            return
            
        # Position-aware mode
        pos_text = f"{self._position}%" if self._current_position <= 100 else "unknown"
        target_text = f"{100 - self._target_position}%" if self._target_position <= 100 else "unknown"
        
        flags = []
        if self._terrace_function:
            flags.append("terrace")
        if self._recent_tilt:
            flags.append("tilted")
        if self._auto_tilt:
            flags.append("auto-tilt")
        if self._stored_position > 0:
            flags.append(f"pos#{self._stored_position}")
        
        flags_text = f" [{', '.join(flags)}]" if flags else ""
        
        _LOGGER.info("EWneo motor %s: %s, Position=%s, Target=%s%s", 
                    self.serial_number[-6:], self._motor_status, pos_text, target_text, flags_text)

    def get_motor_data(self) -> Dict[str, Any]:
        """Get comprehensive motor data."""
        data = {
            "state": self._motor_state,
            "position": self._position,
            "last_seen": self._last_seen,
            "mode": self._mode,
            "motor_status_code": self._motor_status_code,
            "motor_status": self._motor_status,
            "current_position": self._current_position,
            "target_position": self._target_position,
            "recent_tilt": self._recent_tilt,
            "auto_tilt": self._auto_tilt,
            "runtime_measured": self._runtime_measured,
            "tilt_measured": self._tilt_measured,
            "terrace_function": self._terrace_function,
            "stored_position": self._stored_position,
            "is_calibrating": self._motor_status_code in [117, 118],
            "is_moving": self._motor_status_code in [120, 121, 122, 123, 124, 125],
            "control_mode": "position" if self._runtime_measured else "positionless",
            "supports_position_control": self._runtime_measured
        }
        
        # Add position-specific data only if runtime measurement is done
        if self._runtime_measured:
            data.update({
                "current_position_pct": 100 - self._current_position if self._current_position <= 100 else None,
                "target_position_pct": 100 - self._target_position if self._target_position <= 100 else None,
                "position_known": self._current_position <= 100
            })
        else:
            data.update({
                "current_position_pct": None,
                "target_position_pct": None,
                "position_known": False
            })
            
        return data
    
    async def set_state(self, command: str, position: Optional[int] = None) -> bool:
        """Set motor state and position."""
        if command not in ["up", "down", "stop", "open", "close"]:
            return False
        
        # Normalize command names
        if command == "open":
            command = "up"
        elif command == "close":
            command = "down"
        
        try:
            if not self._runtime_measured:
                # Positionless mode - only basic up/down/stop commands
                if position is not None:
                    _LOGGER.warning("EWneo motor %s: Position control not available - runtime measurement required", 
                                   self.serial_number[-6:])
                    return False
                
                _LOGGER.info("Setting EWneo motor %s to %s (positionless mode)", 
                            self.serial_number[-6:], command)
                # TODO: Implement actual command sending via RX11
                self._motor_state = command
                return True
            else:
                # Position-aware mode
                if position is not None:
                    position = max(0, min(100, position))
                    # Convert from HA format to protocol format for positioning
                    protocol_position = 100 - position
                    
                _LOGGER.info("Setting EWneo motor %s to %s%s", 
                            self.serial_number[-6:], command, 
                            f" (position: {position}%)" if position is not None else "")
                # TODO: Implement actual command sending via RX11
                self._motor_state = command
                if position is not None:
                    self._position = position
                return True
                
        except Exception as e:
            _LOGGER.error("Failed to set EWneo motor %s state: %s", 
                         self.serial_number[-6:], e)
            return False
    
    async def get_state(self) -> tuple[str, Optional[int]]:
        """Get current motor state and position.
        
        Returns:
            tuple: (state, position) where position is None in positionless mode
        """
        return self._motor_state, self._position


def create_rx11_ewneo_motor(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11EWneoMotor:
    """Factory function to create EWneo motor."""
    return RX11EWneoMotor(
        serial_number=serial_number,
        device_info=device_info,
        **kwargs
    )