"""EWneo quad motor implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import BaseReceiver, DeviceType, DeviceSubtype, OperatingMode

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EWNEO_STATE_CHANGE = [0x05, 0xF2]


class RX11EWneoQuadMotor(BaseReceiver):
    """EWneo quad motor implementation for RX11 transceiver.
    
    Handles 4-channel EWneo motor devices for covers and blinds.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize EWneo quad motor."""
        super().__init__(*args, device_type=DeviceType.EWNEO_QUAD_MOTOR, 
                        subtype=DeviceSubtype.QUAD_MOTOR, **kwargs)
        self._motor_states = {1: "stop", 2: "stop", 3: "stop", 4: "stop"}
        self._positions = {1: None, 2: None, 3: None, 4: None}  # None=positionless mode, 0-100=position mode
        self._mode = 0  # Should always be 0 for quad motor
        
        # Motor status codes for each channel
        self._motor_status_codes = {1: 126, 2: 126, 3: 126, 4: 126}  # Default to stopped
        self._motor_status_texts = {1: "stopped", 2: "stopped", 3: "stopped", 4: "stopped"}
        
        # Position information for each channel
        self._current_positions = {1: 126, 2: 126, 3: 126, 4: 126}  # 0-100 or 126=unknown
        
        # Tilt information for each channel
        self._recent_tilts = {1: False, 2: False, 3: False, 4: False}  # Recently tilted to horizontal
        
        # Configuration flags (derived from status codes)
        self._runtime_measured = {1: False, 2: False, 3: False, 4: False}  # Can be inferred from status codes
        
        self._setup_operating_mode(**kwargs)
    
    def _setup_operating_mode(self, **kwargs) -> None:
        """Setup operating mode for quad motor."""
        self.operating_mode = OperatingMode.QUAD_CHANNEL
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["cover"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for EWneo quad motor."""
        info_type = telegram_data.get("info_type")
        
        if info_type in TELEGRAM_EWNEO_STATE_CHANGE:
            self._handle_state_change(telegram_data)
        
        return self.get_motor_data()
    
    def _handle_state_change(self, telegram_data: Dict[str, Any]) -> None:
        """Handle state change telegram with 5-byte EWB_RCV parsing for quad motor."""
        data = telegram_data.get("data", {})
        
        # Parse the 5-byte response: 1 byte mode + 4 bytes state
        raw_data = data.get("raw_data")  # Should be 5 bytes
        if raw_data and len(raw_data) >= 5:
            self._parse_ewneo_quad_motor_response(raw_data)
        else:
            # Fallback to simple state extraction if raw data not available
            channel = data.get("channel")
            if channel and 1 <= channel <= 4:
                command, position = self._parse_motor_command(data.get("motor_command"))
                
                if command and command != self._motor_states[channel]:
                    self._motor_states[channel] = command
                    if position is not None:
                        self._positions[channel] = position
                    
                    _LOGGER.debug("EWneo quad motor %s channel %d: Command=%s, Position=%s (fallback)", 
                                 self.serial_number[-6:], channel, command, 
                                 f"{self._positions[channel]}%" if self._positions[channel] is not None else "unknown")
    
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
    
    def _parse_ewneo_quad_motor_response(self, raw_data: bytes) -> None:
        """Parse 5-byte EWB_RCV response for EWneo quad motor.
        
        Format: 1 byte mode + 4 bytes state (big-endian)
        Mode 0: Summary for all 4 motors (bits 31-25: motor1, bits 23-17: motor2, bits 15-9: motor3, bits 7-1: motor4)
        Mode 2: Full info for motor 1
        Mode 10: Full info for motor 2  
        Mode 18: Full info for motor 3
        Mode 26: Full info for motor 4
        """
        if len(raw_data) < 5:
            _LOGGER.warning("EWneo quad motor %s: Invalid response length %d, expected 5 bytes", 
                           self.serial_number[-6:], len(raw_data))
            return
            
        # Parse mode (byte 0)
        mode = raw_data[0]
        
        # Parse state (bytes 1-4, BIG-ENDIAN 32-bit word)
        state_word = int.from_bytes(raw_data[1:5], byteorder='big')
        
        if mode == 0:
            # Summary mode: All 4 motors with basic info
            self._parse_quad_motor_summary_mode(state_word)
        elif mode == 2:
            # Full info for motor 1
            self._parse_individual_motor_full_info(1, state_word)
        elif mode == 10:
            # Full info for motor 2
            self._parse_individual_motor_full_info(2, state_word)
        elif mode == 18:
            # Full info for motor 3
            self._parse_individual_motor_full_info(3, state_word)
        elif mode == 26:
            # Full info for motor 4
            self._parse_individual_motor_full_info(4, state_word)
        else:
            _LOGGER.warning("EWneo quad motor %s: Unsupported mode %d", 
                           self.serial_number[-6:], mode)
            
        self._mode = mode
    
    def _parse_quad_motor_summary_mode(self, state_word: int) -> None:
        """Parse quad motor state word in Mode 0 (summary for all 4 motors)."""
        # Extract motor 1 status code (bits 31-25) and tilt flag (bit 24)
        motor1_status = (state_word >> 25) & 0x7F
        motor1_tilt = bool(state_word & (1 << 24))
        
        # Extract motor 2 status code (bits 23-17) and tilt flag (bit 16)
        motor2_status = (state_word >> 17) & 0x7F
        motor2_tilt = bool(state_word & (1 << 16))
        
        # Extract motor 3 status code (bits 15-9) and tilt flag (bit 8)
        motor3_status = (state_word >> 9) & 0x7F
        motor3_tilt = bool(state_word & (1 << 8))
        
        # Extract motor 4 status code (bits 7-1) and tilt flag (bit 0)
        motor4_status = (state_word >> 1) & 0x7F
        motor4_tilt = bool(state_word & (1 << 0))
        
        # Update motor states
        self._motor_status_codes[1] = motor1_status
        self._motor_status_codes[2] = motor2_status
        self._motor_status_codes[3] = motor3_status
        self._motor_status_codes[4] = motor4_status
        
        self._recent_tilts[1] = motor1_tilt
        self._recent_tilts[2] = motor2_tilt
        self._recent_tilts[3] = motor3_tilt
        self._recent_tilts[4] = motor4_tilt
        
        # Process each motor with summary info
        for channel in [1, 2, 3, 4]:
            old_state = self._motor_states[channel]
            old_position = self._positions[channel]
            
            self._interpret_motor_status_summary(channel)
            self._update_position_info(channel)
            
            # Log changes
            if (old_state != self._motor_states[channel] or 
                old_position != self._positions[channel]):
                self._log_motor_state_change(channel)
    
    def _parse_individual_motor_full_info(self, channel: int, state_word: int) -> None:
        """Parse individual motor full information (Mode 2/10/18/26 for motors 1/2/3/4)."""
        # Extract motor status code (bits 31-25)
        motor_status_code = (state_word >> 25) & 0x7F
        
        # Bit 24: Reserved
        
        # Current position (bits 23-17, 0=open, 100=closed)
        current_position_raw = (state_word >> 17) & 0x7F
        
        # Bit 16: Recent tilt to horizontal
        recent_tilt = bool(state_word & (1 << 16))
        
        # Target position (bits 15-9, 0=open, 100=closed)
        target_position_raw = (state_word >> 9) & 0x7F
        
        # Bit 8: Auto-tilt after positioning
        auto_tilt = bool(state_word & (1 << 8))
        
        # Configuration flags
        runtime_measured = bool(state_word & (1 << 7))  # Bit 7
        tilt_measured = bool(state_word & (1 << 6))     # Bit 6
        # Bits 5-3: Reserved
        terrace_function = bool(state_word & (1 << 2))  # Bit 2
        stored_position = state_word & 0x03             # Bits 1-0
        
        # Store old states for change detection
        old_state = self._motor_states[channel]
        old_position = self._positions[channel]
        old_runtime = self._runtime_measured[channel]
        
        # Update comprehensive motor information
        self._motor_status_codes[channel] = motor_status_code
        self._recent_tilts[channel] = recent_tilt
        self._runtime_measured[channel] = runtime_measured
        
        # Store additional motor info (extend class if needed)
        if not hasattr(self, '_target_positions'):
            self._target_positions = {1: None, 2: None, 3: None, 4: None}
            self._tilt_measured = {1: False, 2: False, 3: False, 4: False}
            self._terrace_function = {1: False, 2: False, 3: False, 4: False}
            self._stored_position = {1: 0, 2: 0, 3: 0, 4: 0}
            self._auto_tilt = {1: False, 2: False, 3: False, 4: False}
            
        self._tilt_measured[channel] = tilt_measured
        self._terrace_function[channel] = terrace_function
        self._stored_position[channel] = stored_position
        self._auto_tilt[channel] = auto_tilt
        
        # Update positions
        if runtime_measured and current_position_raw <= 100:
            self._current_positions[channel] = current_position_raw
            # Convert to HA format (0=closed, 100=open)
            self._positions[channel] = 100 - current_position_raw
        else:
            self._current_positions[channel] = 126  # Unknown
            if not runtime_measured:
                self._positions[channel] = None  # Positionless mode
        
        if runtime_measured and target_position_raw <= 100:
            self._target_positions[channel] = 100 - target_position_raw
        else:
            self._target_positions[channel] = None
        
        # Interpret motor status
        self._interpret_motor_status_full(channel)
        
        # Detect runtime measurement activation
        if runtime_measured and not old_runtime:
            _LOGGER.info("🎯 EWneo quad motor %s CH%d: Runtime measurement ACTIVATED! Position control now available", 
                        self.serial_number[-6:], channel)
        
        # Log comprehensive state changes
        if (old_state != self._motor_states[channel] or 
            old_position != self._positions[channel] or
            old_runtime != self._runtime_measured[channel] or
            motor_status_code in [117, 118]):  # Always log calibration
            self._log_motor_full_state_change(channel)
    
    def _interpret_motor_status_summary(self, channel: int) -> None:
        """Interpret motor status code for summary mode (limited info)."""
        status_code = self._motor_status_codes[channel]
        
        # Basic status interpretation for summary mode
        if 0 <= status_code <= 100:
            self._motor_states[channel] = "stopped"
            self._motor_status_texts[channel] = f"Stopped at {status_code}%"
            self._current_positions[channel] = status_code
            self._runtime_measured[channel] = True
        elif status_code == 126:
            self._motor_states[channel] = "stopped"
            self._motor_status_texts[channel] = "Stopped (position unknown)"
            self._current_positions[channel] = 126
            self._runtime_measured[channel] = False
        elif status_code == 119:
            self._motor_states[channel] = "stopped"
            self._motor_status_texts[channel] = "Stopped (terrace function)"
            self._current_positions[channel] = 126
        elif status_code in [120, 122, 124]:
            self._motor_states[channel] = "opening"
            self._motor_status_texts[channel] = "Opening"
        elif status_code in [121, 123, 125]:
            self._motor_states[channel] = "closing"
            self._motor_status_texts[channel] = "Closing"
        elif status_code in [117, 118]:
            self._motor_states[channel] = "calibrating"
            self._motor_status_texts[channel] = "Runtime measurement" if status_code == 117 else "Tilt measurement"
            self._runtime_measured[channel] = False
        else:
            self._motor_states[channel] = "unknown"
            self._motor_status_texts[channel] = f"Unknown status: {status_code}"
            
    def _interpret_motor_status_full(self, channel: int) -> None:
        """Interpret motor status code for full info mode (comprehensive info)."""
        status_code = self._motor_status_codes[channel]
        
        # Full status interpretation with comprehensive info
        status_map = {
            126: ("stopped", "Motor stopped"),
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
            state, status_text = status_map[status_code]
            self._motor_states[channel] = state
            self._motor_status_texts[channel] = status_text
        else:
            self._motor_states[channel] = "unknown"
            self._motor_status_texts[channel] = f"Unknown status code: {status_code}"
            
    def _log_motor_full_state_change(self, channel: int) -> None:
        """Log comprehensive motor state information for individual motor query."""
        if not self._runtime_measured[channel]:
            # Positionless mode
            flags = []
            if self._recent_tilts[channel]:
                flags.append("tilted")
            if hasattr(self, '_terrace_function') and self._terrace_function[channel]:
                flags.append("terrace")
            
            flags_text = f" [{', '.join(flags)}]" if flags else ""
            _LOGGER.info("🏠 EWneo quad motor %s CH%d: %s (positionless mode)%s", 
                        self.serial_number[-6:], channel, self._motor_status_texts[channel], flags_text)
        else:
            # Position-aware mode
            pos_text = f"{self._positions[channel]}%" if self._current_positions[channel] <= 100 else "unknown"
            
            target_text = ""
            if hasattr(self, '_target_positions') and self._target_positions[channel] is not None:
                target_text = f", Target: {self._target_positions[channel]}%"
            
            flags = []
            if self._recent_tilts[channel]:
                flags.append("tilted")
            if hasattr(self, '_tilt_measured') and self._tilt_measured[channel]:
                flags.append("tilt-cal")
            if hasattr(self, '_terrace_function') and self._terrace_function[channel]:
                flags.append("terrace")
                
            flags_text = f" [{', '.join(flags)}]" if flags else ""
            
            _LOGGER.info("🎯 EWneo quad motor %s CH%d: %s - Position: %s%s%s", 
                        self.serial_number[-6:], channel, self._motor_status_texts[channel], 
                        pos_text, target_text, flags_text)
    
    def _update_position_info(self, channel: int) -> None:
        """Update position information for specific channel based on current state."""
        if not self._runtime_measured[channel]:
            # Positionless mode - no position tracking available
            self._positions[channel] = None
            return
            
        # Use current position if known, otherwise keep existing
        if self._current_positions[channel] <= 100:
            # Convert from protocol format (0=open, 100=closed) to HA format (0=closed, 100=open)
            self._positions[channel] = 100 - self._current_positions[channel]
        # If current_position is 126 (unknown), keep existing position
    
    def _log_motor_state_change(self, channel: int) -> None:
        """Log motor state information for specific channel."""
        if not self._runtime_measured[channel]:
            # Positionless mode - only show basic state
            flags = []
            if self._recent_tilts[channel]:
                flags.append("tilted")
            if not self._runtime_measured[channel]:
                flags.append("no-runtime-cal")
            
            flags_text = f" [{', '.join(flags)}]" if flags else ""
            _LOGGER.info("EWneo quad motor %s CH%d: %s (positionless mode)%s", 
                        self.serial_number[-6:], channel, self._motor_status_texts[channel], flags_text)
            return
            
        # Position-aware mode
        pos_text = f"{self._positions[channel]}%" if self._current_positions[channel] <= 100 else "unknown"
        
        flags = []
        if self._recent_tilts[channel]:
            flags.append("tilted")
        
        flags_text = f" [{', '.join(flags)}]" if flags else ""
        
        _LOGGER.info("EWneo quad motor %s CH%d: %s, Position=%s%s", 
                    self.serial_number[-6:], channel, self._motor_status_texts[channel], 
                    pos_text, flags_text)

    def get_motor_data(self) -> Dict[str, Any]:
        """Get comprehensive quad motor data."""
        # Base motor data
        motor_data = {
            "states": self._motor_states.copy(),
            "positions": self._positions.copy(),
            "last_seen": self._last_seen,
            "mode": self._mode,
            "motor_status_codes": self._motor_status_codes.copy(),
            "motor_status_texts": self._motor_status_texts.copy(),
            "current_positions": self._current_positions.copy(),
            "recent_tilts": self._recent_tilts.copy(),
            "runtime_measured": self._runtime_measured.copy(),
            "control_modes": {
                ch: "position" if self._runtime_measured[ch] else "positionless"
                for ch in [1, 2, 3, 4]
            },
            "supports_position_control": {
                ch: self._runtime_measured[ch] for ch in [1, 2, 3, 4]
            },
            "current_positions_pct": {
                ch: 100 - self._current_positions[ch] if self._current_positions[ch] <= 100 else None
                for ch in [1, 2, 3, 4]
            },
            "positions_known": {
                ch: self._current_positions[ch] <= 100 for ch in [1, 2, 3, 4]
            },
            "is_calibrating": {
                ch: self._motor_status_codes[ch] in [117, 118] for ch in [1, 2, 3, 4]
            },
            "is_moving": {
                ch: self._motor_status_codes[ch] in [120, 121, 122, 123, 124, 125] for ch in [1, 2, 3, 4]
            }
        }
        
        # Add extended information if available from full info queries
        if hasattr(self, '_target_positions'):
            motor_data.update({
                "target_positions": getattr(self, '_target_positions', {1: None, 2: None, 3: None, 4: None}).copy(),
                "tilt_measured": getattr(self, '_tilt_measured', {1: False, 2: False, 3: False, 4: False}).copy(),
                "terrace_function": getattr(self, '_terrace_function', {1: False, 2: False, 3: False, 4: False}).copy(),
                "stored_position": getattr(self, '_stored_position', {1: 0, 2: 0, 3: 0, 4: 0}).copy(),
                "auto_tilt": getattr(self, '_auto_tilt', {1: False, 2: False, 3: False, 4: False}).copy(),
                "supports_tilt_control": {
                    ch: getattr(self, '_tilt_measured', {1: False, 2: False, 3: False, 4: False})[ch] for ch in [1, 2, 3, 4]
                },
                "has_full_info": True
            })
        else:
            motor_data["has_full_info"] = False
            
        return motor_data
    
    async def set_state(self, channel: int, command: str, position: Optional[int] = None) -> bool:
        """Set motor state and position for specific channel."""
        if channel not in [1, 2, 3, 4] or command not in ["up", "down", "stop", "open", "close"]:
            return False
        
        # Normalize command names
        if command == "open":
            command = "up"
        elif command == "close":
            command = "down"
        
        try:
            if not self._runtime_measured[channel]:
                # Positionless mode - only basic up/down/stop commands
                if position is not None:
                    _LOGGER.warning("EWneo quad motor %s CH%d: Position control not available - runtime measurement required", 
                                   self.serial_number[-6:], channel)
                    return False
                
                _LOGGER.info("Setting EWneo quad motor %s channel %d to %s (positionless mode)", 
                            self.serial_number[-6:], channel, command)
                # TODO: Implement actual command sending via RX11
                self._motor_states[channel] = command
                return True
            else:
                # Position-aware mode
                if position is not None:
                    position = max(0, min(100, position))
                    # Convert from HA format to protocol format for positioning
                    protocol_position = 100 - position
                
                _LOGGER.info("Setting EWneo quad motor %s channel %d to %s%s", 
                            self.serial_number[-6:], channel, command, 
                            f" (position: {position}%)" if position is not None else "")
                # TODO: Implement actual command sending via RX11
                self._motor_states[channel] = command
                if position is not None:
                    self._positions[channel] = position
                return True
                
        except Exception as e:
            _LOGGER.error("Failed to set EWneo quad motor %s channel %d state: %s", 
                         self.serial_number[-6:], channel, e)
            return False
    
    async def query_motor_state(self, channel: int, coordinator=None) -> bool:
        """Query individual motor state for runtime measurement detection.
        
        Args:
            channel: Motor channel (1, 2, 3, or 4)
            coordinator: EldatCoordinator instance for sending query commands
            
        Returns:
            bool: True if query was sent successfully
        """
        if channel not in [1, 2, 3, 4]:
            _LOGGER.error("Invalid motor channel %d for quad motor %s", channel, self.serial_number[-6:])
            return False
            
        if not coordinator:
            _LOGGER.error("No coordinator provided for motor state query")
            return False
            
        try:
            # Mode mapping: Mode 2 = Motor 1, Mode 10 = Motor 2, Mode 18 = Motor 3, Mode 26 = Motor 4
            mode_map = {1: 2, 2: 10, 3: 18, 4: 26}
            query_mode = mode_map[channel]
            
            _LOGGER.info("🔍 EWneo quad motor %s: Querying motor %d state (mode %d) for runtime measurement", 
                        self.serial_number[-6:], channel, query_mode)
            
            # Get gateway serial from device info
            gateway_serial = getattr(self, 'gateway_serial', None)
            if not gateway_serial:
                _LOGGER.error("No gateway serial available for quad motor %s", self.serial_number[-6:])
                return False
                
            # Use coordinator's query method with specific mode
            result = await coordinator._query_ewneo_state_with_mode(
                gateway_serial,
                self.serial_number,
                query_mode
            )
            
            if result:
                _LOGGER.debug("✅ Motor %d query sent successfully", channel)
                return True
            else:
                _LOGGER.warning("⚠️ Motor %d query failed", channel)
                return False
                
        except Exception as e:
            _LOGGER.error("❌ Error querying motor %d state: %s", channel, e)
            return False
    
    async def query_all_motors_state(self, coordinator=None) -> Dict[int, bool]:
        """Query state of all motors for comprehensive runtime measurement detection.
        
        Returns:
            Dict[int, bool]: Results for each motor channel
        """
        results = {}
        
        for channel in [1, 2, 3, 4]:
            results[channel] = await self.query_motor_state(channel, coordinator)
            
        _LOGGER.info("🔍 EWneo quad motor %s: Queried all motors - Results: %s", 
                    self.serial_number[-6:], results)
        
        return results
    
    def get_channel_info(self, channel: int) -> Optional[Dict[str, Any]]:
        """Get detailed information for specific motor channel.
        
        Returns:
            Dict with channel-specific motor information or None if invalid channel
        """
        if channel not in [1, 2, 3, 4]:
            return None
            
        channel_info = {
            "channel": channel,
            "state": self._motor_states[channel],
            "position": self._positions[channel],
            "motor_status_code": self._motor_status_codes[channel],
            "motor_status_text": self._motor_status_texts[channel],
            "current_position_raw": self._current_positions[channel],
            "recent_tilt": self._recent_tilts[channel],
            "runtime_measured": self._runtime_measured[channel],
            "control_mode": "position" if self._runtime_measured[channel] else "positionless",
            "supports_position_control": self._runtime_measured[channel],
            "position_known": self._current_positions[channel] <= 100,
            "is_calibrating": self._motor_status_codes[channel] in [117, 118],
            "is_moving": self._motor_status_codes[channel] in [120, 121, 122, 123, 124, 125]
        }
        
        # Add extended info if available
        if hasattr(self, '_target_positions'):
            channel_info.update({
                "target_position": getattr(self, '_target_positions', {}).get(channel),
                "tilt_measured": getattr(self, '_tilt_measured', {}).get(channel, False),
                "terrace_function": getattr(self, '_terrace_function', {}).get(channel, False),
                "stored_position": getattr(self, '_stored_position', {}).get(channel, 0),
                "auto_tilt": getattr(self, '_auto_tilt', {}).get(channel, False),
                "supports_tilt_control": getattr(self, '_tilt_measured', {}).get(channel, False)
            })
            
        return channel_info

    async def get_state(self, channel: int) -> tuple[str, Optional[int]]:
        """Get current motor state and position for specific channel.
        
        Returns:
            tuple: (state, position) where position is None in positionless mode
        """
        return (self._motor_states.get(channel, "stop"), 
                self._positions.get(channel, None))


def create_rx11_ewneo_quad_motor(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11EWneoQuadMotor:
    """Factory function to create EWneo quad motor."""
    return RX11EWneoQuadMotor(
        serial_number=serial_number,
        device_info=device_info,
        **kwargs
    )