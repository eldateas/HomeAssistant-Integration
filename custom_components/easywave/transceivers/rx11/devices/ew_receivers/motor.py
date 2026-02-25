"""Motor receiver implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import (
    BaseReceiver, 
    DeviceType, 
    DeviceSubtype, 
    OperatingMode,
    CoverBehaviorMixin,
    EntitySpecsMixin,
)

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EWB_STATE_CHANGE = [0x03, 0xF1]


class RX11MotorReceiver(CoverBehaviorMixin, EntitySpecsMixin, BaseReceiver):
    """Motor receiver implementation for RX11 transceiver.
    
    Handles EWB/EWneo motor devices for covers, blinds, and shutters.
    NOT used for classic Easywave Receivers - those use entity_specs.py.
    Inherits Cover behavior from CoverBehaviorMixin.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize motor receiver."""
        super().__init__(*args, device_type=DeviceType.EW_RECEIVER, 
                        subtype=DeviceSubtype.MOTOR, **kwargs)
        self._channel_states = {}
        self._positions = {}
        self._setup_operating_mode(**kwargs)
    
    def _setup_operating_mode(self, **kwargs) -> None:
        """Setup operating mode based on channel count."""
        channels = kwargs.get('channels', 1)
        mode_mapping = {
            1: OperatingMode.SINGLE_CHANNEL,
            2: OperatingMode.DUAL_CHANNEL,
            4: OperatingMode.QUAD_CHANNEL,
        }
        self.operating_mode = mode_mapping.get(channels, OperatingMode.SINGLE_CHANNEL)
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types for EWneo motor devices."""
        return ["cover"]
    
    def get_entity_specs(self) -> Dict[str, List[Dict[str, Any]]]:
        """Generate entity specifications for EWneo/EWB motor devices.
        
        EWneo motors create cover entities with all features (open/close/stop/position).
        This is NOT used for classic Easywave Receivers.
        """
        from .....helpers_unique_id import make_unique_id
        
        specs = {
            "switch": [],
            "light": [],
            "cover": [],
            "sensor": [],
            "binary_sensor": [],
            "button": []
        }
        
        # Create cover entities for each channel
        for channel in range(self.channel_count):
            channel_suffix = f" CH{channel+1}" if self.channel_count > 1 else ""
            
            specs["cover"].append({
                "type": "cover",
                "name": f"{self.name}{channel_suffix}",
                "unique_id": make_unique_id(self.serial_number, "cover", channel if self.channel_count > 1 else None),
                "device_class": "shade",
                "icon": "mdi:window-shutter",
                "channel": channel,
                "supports_stop": True,
                "supports_position": True,
                "entity_category": None
            })
        
        return specs
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for motor receiver."""
        info_type = telegram_data.get("info_type")
        
        if info_type in TELEGRAM_EWB_STATE_CHANGE:
            self._handle_state_change(telegram_data)
        
        return self.get_state_data()
    
    def _handle_state_change(self, telegram_data: Dict[str, Any]) -> None:
        """Handle state change telegram."""
        channel = telegram_data.get("channel", 0)
        state = telegram_data.get("state")
        position = telegram_data.get("position")
        
        if state is not None:
            self._channel_states[channel] = state
            self.set_cover_state(channel, state)  # Update mixin state
            _LOGGER.debug("EW Motor Receiver %s channel %d state: %s", 
                        self.serial_number, channel, state)
        
        if position is not None:
            pos = max(0, min(100, position))
            self._positions[channel] = pos
            self.set_cover_position(channel, pos)  # Update mixin state
            _LOGGER.debug("EW Motor Receiver %s channel %d position: %d", 
                        self.serial_number, channel, position)
    
    def _parse_motor_command(self, state: Any) -> tuple[Optional[str], Optional[int]]:
        """Parse motor command from various input formats."""
        if isinstance(state, dict):
            command = state.get('command')  # 'open', 'close', 'stop'
            position = state.get('position')  # 0-100
        elif isinstance(state, str):
            command = state.lower()
            position = None
        elif isinstance(state, (int, float)):
            command = None
            position = max(0, min(100, int(state)))
        else:
            command = None
            position = None
        
        return command, position
    
    async def set_state(self, channel: int, state: Any) -> bool:
        """Set motor state (position) for a specific channel."""
        try:
            command, position = self._parse_motor_command(state)
            
            # TODO: Implement actual RX11 motor command sending
            if command:
                if command == 'open':
                    self._positions[channel] = 100
                    self._channel_states[channel] = 'opening'
                elif command == 'close':
                    self._positions[channel] = 0
                    self._channel_states[channel] = 'closing'
                elif command == 'stop':
                    self._channel_states[channel] = 'stopped'
                # Keep current position for stop
            elif position is not None:
                self._positions[channel] = position
                self._channel_states[channel] = 'positioning'
            
            current_pos = self._positions.get(channel, 0)
            _LOGGER.info("EW Motor Receiver %s channel %d: command=%s, position=%d", 
                       self.serial_number, channel, command, current_pos)
            return True
        except Exception as e:
            _LOGGER.error("Failed to set motor state: %s", e)
            return False
    
    async def get_state(self, channel: int) -> str:
        """Get current state for a specific channel."""
        return self._channel_states.get(channel, 'stopped')
    
    async def get_position(self, channel: int) -> int:
        """Get current position for a channel."""
        return self._positions.get(channel, 0)
    
    async def set_position(self, channel: int, position: int) -> bool:
        """Set position for a specific channel."""
        try:
            position = max(0, min(100, position))
            
            # TODO: Implement actual RX11 motor position command sending
            self._positions[channel] = position
            self._channel_states[channel] = 'positioning'
            
            _LOGGER.info("EW Motor Receiver %s channel %d position set to %d", 
                       self.serial_number, channel, position)
            return True
        except Exception as e:
            _LOGGER.error("Failed to set motor position: %s", e)
            return False
    
    async def open_cover(self, channel: int) -> bool:
        """Open cover for a specific channel."""
        return await self.set_state(channel, 'open')
    
    async def close_cover(self, channel: int) -> bool:
        """Close cover for a specific channel."""
        return await self.set_state(channel, 'close')
    
    async def stop_cover(self, channel: int) -> bool:
        """Stop cover for a specific channel."""
        return await self.set_state(channel, 'stop')
    
    def get_state_data(self) -> Dict[str, Any]:
        """Get current state data for coordinator."""
        data = {}
        
        # Add channel states and positions
        for channel in set(self._channel_states.keys()) | set(self._positions.keys()):
            data[f"channel_{channel}_state"] = self._channel_states.get(channel, 'stopped')
            data[f"channel_{channel}_position"] = self._positions.get(channel, 0)
        
        return data
    
    @property
    def channel_count(self) -> int:
        """Get number of channels."""
        if self.operating_mode == OperatingMode.SINGLE_CHANNEL:
            return 1
        elif self.operating_mode == OperatingMode.DUAL_CHANNEL:
            return 2
        elif self.operating_mode == OperatingMode.QUAD_CHANNEL:
            return 4
        return 1


def create_rx11_motor_receiver(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11MotorReceiver:
    """Factory function to create EWneo/EWB motor receiver.
    
    NOT used for classic Easywave Receivers.
    """
    return RX11MotorReceiver(
        serial_number, 
        name=device_info.get('name'),
        channels=device_info.get('channels', 1),
        **kwargs
    )