"""Switch receiver implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import (
    BaseReceiver, 
    DeviceType, 
    DeviceSubtype, 
    OperatingMode,
    SwitchBehaviorMixin,
    EntitySpecsMixin,
)

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EWB_STATE_CHANGE = [0x03, 0xF1]


class RX11SwitchReceiver(SwitchBehaviorMixin, EntitySpecsMixin, BaseReceiver):
    """Switch receiver implementation for RX11 transceiver.
    
    Handles EW and EWB switch devices with on/off control.
    Inherits Switch behavior from SwitchBehaviorMixin.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize switch receiver."""
        super().__init__(*args, device_type=DeviceType.EW_RECEIVER, 
                        subtype=DeviceSubtype.SWITCH, **kwargs)
        self._channel_states = {}
        # Store operating mode for button entity generation
        self._operating_mode = kwargs.get('operating_mode', 2)  # Default to 2-Tast
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
        """Return supported entity types. Switches use button entities for stateless operation."""
        return ["button"]
    
    def get_entity_specs(self) -> Dict[str, List[Dict[str, Any]]]:
        """Generate entity specifications for this switch device.
        
        Creates button entities for stateless operation:
        - Mode 1 (Eintastbedienung): 1 Toggle button
        - Mode 2 (Zweitastbedienung): 2 buttons (Ein/On, Aus/Off)
        """
        specs = {
            "switch": [],
            "light": [],
            "cover": [],
            "sensor": [],
            "binary_sensor": [],
            "button": []
        }
        
        # Determine operating mode
        operating_mode = getattr(self, '_operating_mode', 2)  # Default to 2-Tast
        
        # Create button entities for each channel based on operating mode
        for channel in range(self.channel_count):
            channel_suffix = f" CH{channel+1}" if self.channel_count > 1 else ""
            
            if operating_mode == 1:
                # Eintastbedienung: 1 Toggle button (A)
                specs["button"].append({
                    "type": "button",
                    "name": f"{self.name}{channel_suffix} A - Toggle",
                    "unique_id": f"{self.serial_number}_toggle_ch{channel}",
                    "channel": channel,
                    "button_code": 0,  # TM_BUTTON_A
                    "action": "toggle",
                    "icon": "mdi:gesture-tap-button",
                    "operating_mode": operating_mode,
                    "receiver_kind": "switch"
                })
            elif operating_mode == 2:
                # Zweitastbedienung: A - Ein, B - Aus
                specs["button"].append({
                    "type": "button",
                    "name": f"{self.name}{channel_suffix} A - Ein",
                    "unique_id": f"{self.serial_number}_on_ch{channel}",
                    "channel": channel,
                    "button_code": 0,  # TM_BUTTON_A
                    "action": "on",
                    "icon": "mdi:lightbulb-on",
                    "operating_mode": operating_mode,
                    "receiver_kind": "switch"
                })
                specs["button"].append({
                    "type": "button",
                    "name": f"{self.name}{channel_suffix} B - Aus",
                    "unique_id": f"{self.serial_number}_off_ch{channel}",
                    "channel": channel,
                    "button_code": 1,  # TM_BUTTON_B
                    "action": "off",
                    "icon": "mdi:lightbulb-off",
                    "operating_mode": operating_mode,
                    "receiver_kind": "switch"
                })
        
        return specs
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for switch receiver."""
        info_type = telegram_data.get("info_type")
        
        if info_type in TELEGRAM_EWB_STATE_CHANGE:
            self._handle_state_change(telegram_data)
        
        return self.get_state_data()
    
    def _handle_state_change(self, telegram_data: Dict[str, Any]) -> None:
        """Handle state change telegram."""
        channel = telegram_data.get("channel", 0)
        state = telegram_data.get("state")
        
        if state is not None:
            state_bool = bool(state)
            self._channel_states[channel] = state_bool
            self.set_switch_state(channel, state_bool)  # Update mixin state
            _LOGGER.debug("EW Switch Receiver %s channel %d state: %s", 
                        self.serial_number[-6:], channel, state)
    
    async def set_state(self, channel: int, state: bool) -> bool:
        """Set switch state for a specific channel."""
        try:
            # TODO: Implement actual RX11 command sending
            self._channel_states[channel] = bool(state)
            _LOGGER.info("EW Switch Receiver %s channel %d set to %s", 
                       self.serial_number[-6:], channel, state)
            return True
        except Exception as e:
            _LOGGER.error("Failed to set switch state: %s", e)
            return False
    
    async def get_state(self, channel: int) -> bool:
        """Get current state for a specific channel."""
        return self._channel_states.get(channel, False)
    
    def get_state_data(self) -> Dict[str, Any]:
        """Get current state data for coordinator."""
        data = {}
        
        # Add channel states
        for channel, state in self._channel_states.items():
            data[f"channel_{channel}_state"] = state
        
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


def create_rx11_switch_receiver(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11SwitchReceiver:
    """Factory function to create switch receiver."""
    return RX11SwitchReceiver(
        serial_number, 
        name=device_info.get('name'),
        channels=device_info.get('channels', 1),
        **kwargs
    )