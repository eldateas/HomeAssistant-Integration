"""Dual button transmitter implementation for RX11 transceiver."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging
from datetime import datetime

from ....base import (
    BaseTransmitter, 
    DeviceType, 
    DeviceSubtype, 
    OperatingMode,
    ButtonBehaviorMixin,
    EntitySpecsMixin,
)

_LOGGER = logging.getLogger(__name__)

# Telegram type constants
TELEGRAM_BUTTON_RELEASE = 0x00
TELEGRAM_BUTTON_PUSH = 0x01


class RX11DualButtonTransmitter(ButtonBehaviorMixin, EntitySpecsMixin, BaseTransmitter):
    """Dual button transmitter implementation for RX11 transceiver.
    
    Handles EW transmitters with two buttons (Button A and B).
    Inherits Button behavior from ButtonBehaviorMixin.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize dual button transmitter."""
        super().__init__(*args, device_type=DeviceType.EW_TRANSMITTER, 
                        subtype=DeviceSubtype.DUAL_BUTTON, 
                        operating_mode=OperatingMode.TWO_BUTTON,
                        button_count=2, **kwargs)
        self._button_press_times = {}
        self._button_last_actions = {}
        self.button_labels = ["A", "B"]
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["binary_sensor", "device_trigger"]
    
    def get_entity_specs(self) -> Dict[str, List[Dict[str, Any]]]:
        """Generate entity specifications for this dual button transmitter."""
        specs = {
            "switch": [],
            "light": [],
            "cover": [],
            "sensor": [],
            "binary_sensor": [],
            "button": []
        }
        
        # Create binary sensor entities for each button
        for button_idx in range(2):
            button_label = self.button_labels[button_idx]
            specs["binary_sensor"].append(self._create_base_entity_spec(
                "binary_sensor",
                channel=button_idx,
                name=f"{self.name} Button {button_label}",
                device_class="motion",
                icon="mdi:gesture-tap-button"
            ))
        
        return specs
    
    def get_button_state(self, button: int) -> Optional[bool]:
        """Get current state for a specific button."""
        return self._button_states.get(button)
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for dual button transmitter."""
        info_type = telegram_data.get("info_type")
        button = telegram_data.get("button", 0)
        current_time = datetime.now()
        
        if info_type == TELEGRAM_BUTTON_RELEASE:
            self._handle_button_release(button, current_time, telegram_data)
        elif info_type == TELEGRAM_BUTTON_PUSH:
            self._handle_button_push(button, current_time, telegram_data)
        
        return self.get_button_data()
    
    def _handle_button_push(self, button: int, timestamp: datetime, telegram_data: Dict[str, Any]) -> None:
        """Handle button push event."""
        if 0 <= button < 2:  # Button A (0) and B (1)
            self._button_press_times[button] = timestamp
            self._button_last_actions[button] = 'push'
            self.set_button_state(button, True)
            
            button_label = self.button_labels[button]
            _LOGGER.debug("EW Dual Button Transmitter %s button %s pushed", 
                        self.serial_number[-6:], button_label)
    
    def _handle_button_release(self, button: int, timestamp: datetime, telegram_data: Dict[str, Any]) -> None:
        """Handle button release event."""
        if 0 <= button < 2:  # Button A (0) and B (1)
            self._button_last_actions[button] = 'release'
            self.set_button_state(button, False)
            
            # Calculate press duration if we have push time
            press_duration = self._calculate_press_duration(button, timestamp)
            
            button_label = self.button_labels[button]
            _LOGGER.debug("EW Dual Button Transmitter %s button %s released (duration: %.0fms)", 
                        self.serial_number[-6:], button_label, press_duration or 0)
    
    def _calculate_press_duration(self, button: int, release_time: datetime) -> Optional[float]:
        """Calculate press duration and cleanup press time."""
        if button in self._button_press_times:
            duration = (release_time - self._button_press_times[button]).total_seconds() * 1000
            del self._button_press_times[button]
            return duration
        return None
    
    def get_button_press_duration(self, button: int) -> Optional[float]:
        """Get current press duration for a button in milliseconds."""
        if button in self._button_press_times:
            return (datetime.now() - self._button_press_times[button]).total_seconds() * 1000
        return None
    
    def get_button_data(self) -> Dict[str, Any]:
        """Get current button data for coordinator."""
        data = {}
        
        # Add button states and actions
        for button in range(2):
            button_label = self.button_labels[button].lower()
            
            # Add button state
            button_state = self.get_button_state(button)
            if button_state is not None:
                data[f"button_{button_label}_state"] = button_state
            
            # Add last action
            last_action = self._button_last_actions.get(button)
            if last_action:
                data[f"button_{button_label}_last_action"] = last_action
        
        return data
    
    @property
    def button_labels(self) -> List[str]:
        """Get list of button labels."""
        return ["A", "B"]


def create_rx11_dual_button_transmitter(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11DualButtonTransmitter:
    """Factory function to create dual button transmitter."""
    return RX11DualButtonTransmitter(
        serial_number, 
        name=device_info.get('name'),
        **kwargs
    )