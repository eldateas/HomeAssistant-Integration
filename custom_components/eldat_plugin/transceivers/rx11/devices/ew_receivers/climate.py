"""Climate receiver implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from ....base import (
    BaseReceiver, 
    DeviceType, 
    DeviceSubtype, 
    OperatingMode,
    EntitySpecsMixin,
)

_LOGGER = logging.getLogger(__name__)

# Constants for telegram processing
TELEGRAM_EWB_STATE_CHANGE = [0x03, 0xF1]


class RX11ClimateReceiver(EntitySpecsMixin, BaseReceiver):
    """Climate receiver implementation for RX11 transceiver.
    
    Handles EW and EWB climate devices for heating and cooling control.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize climate receiver."""
        super().__init__(*args, device_type=DeviceType.EW_RECEIVER, 
                        subtype=DeviceSubtype.HEATING_COOLING, **kwargs)
        self._channel_states = {}
        self._temperatures = {}
        self._target_temperatures = {}
        self._modes = {}  # 'heat', 'cool', 'off', 'auto'
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
        """Return supported entity types."""
        return ["button", "climate", "sensor"]
    
    def get_entity_specs(self) -> Dict[str, List[Dict[str, Any]]]:
        """Generate entity specifications for this climate device.
        
        Creates button entities for stateless operation:
        - Mode 1 (Eintastbedienung): 1 Toggle button (Ein/Aus)
        """
        specs = {
            "switch": [],
            "light": [],
            "cover": [],
            "sensor": [],
            "binary_sensor": [],
            "button": []
        }
        
        # Create button entities for each channel
        for channel in range(self.channel_count):
            channel_suffix = f" CH{channel+1}" if self.channel_count > 1 else ""
            
            # Eintastbedienung: 1 Toggle button (A) für Heizung/Kühlung
            specs["button"].append({
                "type": "button",
                "name": f"{self.name}{channel_suffix} A - Ein/Aus",
                "unique_id": f"{self.serial_number}_toggle_ch{channel}",
                "channel": channel,
                "button_code": 0,  # TM_BUTTON_A
                "action": "toggle",
                "icon": "mdi:thermostat",
                "operating_mode": 1,
                "receiver_kind": "heating_cooling"
            })
        
        return specs
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for climate receiver."""
        info_type = telegram_data.get("info_type")
        
        if info_type in TELEGRAM_EWB_STATE_CHANGE:
            self._handle_state_change(telegram_data)
        
        return self.get_state_data()
    
    def _handle_state_change(self, telegram_data: Dict[str, Any]) -> None:
        """Handle state change telegram."""
        channel = telegram_data.get("channel", 0)
        state = telegram_data.get("state")
        temperature = telegram_data.get("temperature")
        target_temperature = telegram_data.get("target_temperature")
        mode = telegram_data.get("mode")
        
        if state is not None:
            self._channel_states[channel] = state
            _LOGGER.debug("EW Climate Receiver %s channel %d state: %s", 
                        self.serial_number[-6:], channel, state)
        
        if temperature is not None:
            self._temperatures[channel] = temperature
            _LOGGER.debug("EW Climate Receiver %s channel %d temperature: %.1f°C", 
                        self.serial_number[-6:], channel, temperature)
        
        if target_temperature is not None:
            self._target_temperatures[channel] = target_temperature
            _LOGGER.debug("EW Climate Receiver %s channel %d target: %.1f°C", 
                        self.serial_number[-6:], channel, target_temperature)
        
        if mode is not None:
            self._modes[channel] = mode
            _LOGGER.debug("EW Climate Receiver %s channel %d mode: %s", 
                        self.serial_number[-6:], channel, mode)
    
    def _parse_climate_state(self, state: Any) -> tuple[str, Optional[float], Optional[float]]:
        """Parse climate state from various input formats."""
        if isinstance(state, dict):
            mode = state.get('mode', 'off')
            target_temp = state.get('target_temperature')
            current_temp = state.get('current_temperature')
        else:
            mode = 'heat' if state else 'off'
            target_temp = None
            current_temp = None
        
        return mode, target_temp, current_temp
    
    async def set_state(self, channel: int, state: Any) -> bool:
        """Set heating/cooling state for a specific channel."""
        try:
            mode, target_temp, current_temp = self._parse_climate_state(state)
            
            # TODO: Implement actual RX11 heating/cooling command sending
            self._modes[channel] = mode
            
            if target_temp is not None:
                self._target_temperatures[channel] = target_temp
            if current_temp is not None:
                self._temperatures[channel] = current_temp
            
            _LOGGER.info("EW Climate Receiver %s channel %d: mode=%s, target=%.1f°C", 
                       self.serial_number[-6:], channel, mode, target_temp or 0)
            return True
        except Exception as e:
            _LOGGER.error("Failed to set climate state: %s", e)
            return False
    
    async def get_state(self, channel: int) -> str:
        """Get current state for a specific channel."""
        return self._channel_states.get(channel, 'off')
    
    async def get_mode(self, channel: int) -> str:
        """Get current mode for a channel."""
        return self._modes.get(channel, 'off')
    
    async def set_mode(self, channel: int, mode: str) -> bool:
        """Set climate mode for a specific channel."""
        try:
            # TODO: Implement actual RX11 climate mode command sending
            self._modes[channel] = mode
            
            _LOGGER.info("EW Climate Receiver %s channel %d mode set to %s", 
                       self.serial_number[-6:], channel, mode)
            return True
        except Exception as e:
            _LOGGER.error("Failed to set climate mode: %s", e)
            return False
    
    async def get_target_temperature(self, channel: int) -> Optional[float]:
        """Get target temperature for a channel."""
        return self._target_temperatures.get(channel)
    
    async def set_target_temperature(self, channel: int, temperature: float) -> bool:
        """Set target temperature for a specific channel."""
        try:
            # TODO: Implement actual RX11 temperature command sending
            self._target_temperatures[channel] = temperature
            
            _LOGGER.info("EW Climate Receiver %s channel %d target temperature set to %.1f°C", 
                       self.serial_number[-6:], channel, temperature)
            return True
        except Exception as e:
            _LOGGER.error("Failed to set target temperature: %s", e)
            return False
    
    async def get_current_temperature(self, channel: int) -> Optional[float]:
        """Get current temperature for a channel."""
        return self._temperatures.get(channel)
    
    def get_state_data(self) -> Dict[str, Any]:
        """Get current state data for coordinator."""
        data = {}
        
        # Get all channels that have any data
        all_channels = set()
        all_channels.update(self._channel_states.keys())
        all_channels.update(self._temperatures.keys())
        all_channels.update(self._target_temperatures.keys())
        all_channels.update(self._modes.keys())
        
        # Add channel data
        for channel in all_channels:
            data[f"channel_{channel}_state"] = self._channel_states.get(channel, 'off')
            data[f"channel_{channel}_mode"] = self._modes.get(channel, 'off')
            
            if channel in self._temperatures:
                data[f"channel_{channel}_temperature"] = self._temperatures[channel]
            
            if channel in self._target_temperatures:
                data[f"channel_{channel}_target_temperature"] = self._target_temperatures[channel]
        
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
    
    @property
    def supported_modes(self) -> List[str]:
        """Get list of supported climate modes."""
        return ["off", "heat", "cool", "auto"]


def create_rx11_climate_receiver(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11ClimateReceiver:
    """Factory function to create climate receiver."""
    return RX11ClimateReceiver(
        serial_number, 
        name=device_info.get('name'),
        channels=device_info.get('channels', 1),
        **kwargs
    )