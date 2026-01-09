"""Base class for EWneo bidirectional devices with common state parsing."""
from __future__ import annotations

from typing import Any, Dict, Optional
import logging
from abc import abstractmethod

from ....base import BaseReceiver

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EWNEO_STATE_CHANGE = [0x05, 0xF2]  # EWB_RCV telegram
TELEGRAM_QUERY_STATE_RESPONSE = [0x06, 0xF3]  # EWB_QUERY_STATE response
TELEGRAM_CHANGE_STATE_CONFIRM = [0x07, 0xF4]  # EWB_CHANGE_STATE confirmation


class EWneoBaseDevice(BaseReceiver):
    """Base class for all EWneo bidirectional devices.
    
    Provides common functionality for parsing 5-byte state responses that
    are used across QUERY_STATE, CHANGE_STATE confirmations, and EWB_RCV telegrams.
    
    All EWneo devices use Mode 0 for normal state reporting.
    """
    
    def __init__(self, *args, gateway_serial: str = None, transceiver = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._mode = 0  # Current mode from state response
        self._last_state_word = None  # Last parsed state word for debugging
        self._gateway_serial = gateway_serial
        self._transceiver = transceiver
        self._initialized = False
    
    async def async_initialize(self, is_restoration: bool = False) -> bool:
        """Initialize device by querying current state.
        
        Called for BOTH fresh creation and restoration to ensure consistent behavior.
        
        Args:
            is_restoration: True if restoring from persistent storage
            
        Returns:
            True if initialization successful
        """
        if not self._transceiver or not self._gateway_serial:
            _LOGGER.warning("%s %s: Cannot initialize - missing transceiver or gateway_serial",
                          self.__class__.__name__, self.serial_number[-6:])
            return False
        
        source = "restoration" if is_restoration else "fresh creation"
        _LOGGER.info("🔧 Initializing %s via %s: %s", 
                    self.__class__.__name__, source, self.serial_number[-8:])
        
        try:
            # Query current state (Mode 0)
            if hasattr(self._transceiver, 'rx11_ewb_query_state'):
                result = await self._transceiver.rx11_ewb_query_state(
                    gateway_serial=self._gateway_serial,
                    receiver_serial=self.serial_number,
                    mode=0
                )
                
                if result:
                    recent_mode, state_bytes = result
                    _LOGGER.debug("📥 QueryState result: mode=%d, state=%s", 
                                recent_mode, [f"0x{b:02X}" for b in state_bytes] if state_bytes else "None")
                    
                    # Parse state
                    if state_bytes and len(state_bytes) >= 5:
                        self._parse_state_response(bytes(state_bytes))
                        self._initialized = True
                        _LOGGER.info("✅ %s initialized successfully", self.__class__.__name__)
                        return True
                    else:
                        _LOGGER.warning("⚠️ QueryState returned invalid state bytes")
                else:
                    _LOGGER.warning("⚠️ QueryState returned no result")
            else:
                _LOGGER.warning("⚠️ Transceiver does not support rx11_ewb_query_state")
            
            return False
            
        except Exception as e:
            _LOGGER.error("❌ Error initializing %s: %s", self.__class__.__name__, e)
            return False
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram and extract state if present.
        
        Handles:
        - EWB_RCV (0x05/0xF2): Unsolicited state updates from device
        - QUERY_STATE response (0x06/0xF3): Response to state query
        - CHANGE_STATE confirmation (0x07/0xF4): Confirmation after state change
        """
        info_type = telegram_data.get("info_type")
        
        # All of these telegram types contain 5-byte state data in Mode 0
        if info_type in TELEGRAM_EWNEO_STATE_CHANGE + TELEGRAM_QUERY_STATE_RESPONSE + TELEGRAM_CHANGE_STATE_CONFIRM:
            self._handle_state_telegram(telegram_data)
        
        return self._get_device_data()
    
    def _handle_state_telegram(self, telegram_data: Dict[str, Any]) -> None:
        """Handle any telegram containing 5-byte EWB state data.
        
        This method extracts the raw 5-byte state data and calls the
        device-specific parsing method.
        """
        data = telegram_data.get("data", {})
        info_type = telegram_data.get("info_type")
        
        # Extract raw 5-byte state data (1 byte mode + 4 bytes state)
        raw_data = data.get("raw_data")
        
        if raw_data and len(raw_data) >= 5:
            telegram_type = {
                0x05: "EWB_RCV",
                0xF2: "EWB_RCV",
                0x06: "QUERY_STATE",
                0xF3: "QUERY_STATE",
                0x07: "CHANGE_STATE",
                0xF4: "CHANGE_STATE"
            }.get(info_type, f"0x{info_type:02X}")
            
            _LOGGER.debug("%s %s: Received %s telegram with state data",
                         self.__class__.__name__, self.serial_number[-6:], telegram_type)
            
            self._parse_state_response(raw_data)
        else:
            _LOGGER.warning("%s %s: State telegram without valid raw_data",
                           self.__class__.__name__, self.serial_number[-6:])
    
    def _parse_state_response(self, raw_data: bytes) -> None:
        """Parse 5-byte state response common to all EWneo telegrams.
        
        Format: 1 byte mode + 4 bytes state
        
        The mode byte determines the interpretation of the state bytes:
        - Mode 0: Normal operation state (different for each device type)
        - Mode 1+: Device-specific extended modes (timers, etc.)
        
        Args:
            raw_data: 5 bytes containing mode + state
        """
        if len(raw_data) < 5:
            _LOGGER.warning("%s %s: Invalid state response length %d, expected 5 bytes",
                           self.__class__.__name__, self.serial_number[-6:], len(raw_data))
            return
        
        # Extract mode (byte 0)
        self._mode = raw_data[0]
        
        # Extract state word (bytes 1-4, big-endian 32-bit)
        state_word = int.from_bytes(raw_data[1:5], byteorder='big')
        self._last_state_word = state_word
        
        _LOGGER.debug("%s %s: Parsing state - Mode=%d, StateWord=0x%08X",
                     self.__class__.__name__, self.serial_number[-6:], 
                     self._mode, state_word)
        
        # Call device-specific parsing based on mode
        if self._mode == 0:
            # Mode 0: Normal operation - device-specific interpretation
            self._parse_mode0_state(state_word)
        else:
            # Extended modes (timers, etc.) - device-specific
            self._parse_extended_mode_state(state_word)
    
    @abstractmethod
    def _parse_mode0_state(self, state_word: int) -> None:
        """Parse Mode 0 state word (device-specific normal operation state).
        
        This method must be implemented by each device type:
        - Switch: on/off state, reason, counter
        - Motor: position, movement state, status code
        - Dimmer: brightness levels, dimming time
        
        Args:
            state_word: 32-bit state word to parse
        """
        pass
    
    def _parse_extended_mode_state(self, state_word: int) -> None:
        """Parse extended mode state (optional, override if device supports it).
        
        Extended modes include:
        - Mode 1: Timer information (for switches)
        - Other device-specific modes
        
        Args:
            state_word: 32-bit state word to parse
        """
        _LOGGER.debug("%s %s: Extended mode %d not implemented (state=0x%08X)",
                     self.__class__.__name__, self.serial_number[-6:], 
                     self._mode, state_word)
    
    @abstractmethod
    def _get_device_data(self) -> Dict[str, Any]:
        """Get current device data for coordinator.
        
        Returns:
            Dictionary with device-specific data
        """
        pass
    
    def get_last_state_info(self) -> Dict[str, Any]:
        """Get debug information about last parsed state.
        
        Returns:
            Dictionary with mode, state word, and other debug info
        """
        return {
            "mode": self._mode,
            "state_word": f"0x{self._last_state_word:08X}" if self._last_state_word else None,
            "device_type": self.__class__.__name__,
        }
