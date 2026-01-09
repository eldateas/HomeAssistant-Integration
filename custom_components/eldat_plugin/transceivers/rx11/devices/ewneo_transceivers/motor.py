"""EWneo motor implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import DeviceType, DeviceSubtype, OperatingMode
from .base import EWneoBaseDevice

_LOGGER = logging.getLogger(__name__)


class RX11EWneoMotor(EWneoBaseDevice):
    """EWneo motor implementation for RX11 transceiver.
    
    Handles single-channel EWneo motor devices for covers and blinds.
    Inherits common EWB state parsing from EWneoBaseDevice.
    """
    
    def __init__(self, *args, device_info=None, **kwargs):
        """Initialize EWneo motor."""
        super().__init__(*args, device_type=DeviceType.EWNEO_MOTOR, 
                        subtype=DeviceSubtype.MOTOR, **kwargs)
        # Note: self._mode is now in base class
        
        self._device_info = device_info or {}
        self._motor_state = "stop"
        self._position = None  # None=positionless mode, 0-100=position mode
        
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
    
    def _parse_mode0_state(self, state_word: int) -> None:
        """Parse Mode 0 state for motor (implements abstract method).
        
        Motor always uses Mode 0.
        State word format (32-bit, big-endian):
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
        """
        self._parse_motor_state_word(state_word)
    
    def _get_device_data(self) -> Dict[str, Any]:
        """Get motor data for coordinator (implements abstract method)."""
        return self.get_motor_data()
    
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
            "motor_status": self._motor_status,
            "control_mode": "position" if self._runtime_measured else "positionless",
        }
        
        # Add status details
        data.update({
            "is_calibrating": self._motor_status_code in [117, 118],
            "is_moving": self._motor_status_code in [120, 121, 122, 123, 124, 125],
        })
        
        # Add position-specific data only if runtime measurement is done
        if self._runtime_measured:
            data.update({
                "current_position_pct": 100 - self._current_position if self._current_position <= 100 else None,
                "target_position_pct": 100 - self._target_position if self._target_position <= 100 else None,
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
    
    @property
    def gateway_serial(self) -> Optional[str]:
        """Get gateway serial number from device info."""
        return self._device_info.get("gateway_serial")


def create_rx11_ewneo_motor(
    serial_number: str,
    name: str = None,
    device_info: Dict[str, Any] = None,
    **kwargs
) -> Optional[RX11EWneoMotor]:
    """Create RX11 EWneo motor instance."""
    try:
        return RX11EWneoMotor(
            serial_number=serial_number,
            name=name or f"EWneo Motor {serial_number[-6:]}",
            device_info=device_info,
            **kwargs
        )
    except Exception as e:
        _LOGGER.error(f"Error creating EWneo motor: {e}")
        return None