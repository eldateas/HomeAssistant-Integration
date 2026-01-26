"""Unified button transmitter implementation for RX11 transceiver.

This module provides a single, configurable button transmitter class that handles
all button counts (1-4). This replaces the separate single/dual/triple/quad
button transmitter classes with a unified implementation.
"""
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

# Telegram type constants - unified for all button transmitters
TELEGRAM_BUTTON_RELEASE = 0x00
TELEGRAM_BUTTON_PUSH = 0x01
# Legacy constants for compatibility
TELEGRAM_EW_BUTTON_PRESS = 0x04
TELEGRAM_EW_BUTTON_RELEASE = 0x05


class RX11ButtonTransmitter(ButtonBehaviorMixin, EntitySpecsMixin, BaseTransmitter):
    """Unified button transmitter for RX11 transceiver.
    
    Handles EW transmitters with 1-4 buttons. The button count is configurable
    during initialization, replacing the need for separate Single/Dual/Triple/Quad
    button transmitter classes.
    
    Button Labels:
    - 1 button: A
    - 2 buttons: A, B
    - 3 buttons: A, B, C
    - 4 buttons: A, B, C, D
    """
    
    # Button count to subtype mapping
    BUTTON_COUNT_TO_SUBTYPE = {
        1: DeviceSubtype.SINGLE_BUTTON,
        2: DeviceSubtype.DUAL_BUTTON,
        3: DeviceSubtype.TRIPLE_BUTTON,
        4: DeviceSubtype.QUAD_BUTTON,
    }
    
    # Button count to operating mode mapping
    BUTTON_COUNT_TO_MODE = {
        1: OperatingMode.ONE_BUTTON,
        2: OperatingMode.TWO_BUTTON,
        3: OperatingMode.THREE_BUTTON,
        4: OperatingMode.FOUR_BUTTON,
    }
    
    # Full button labels
    BUTTON_LABELS = ["A", "B", "C", "D"]
    
    def __init__(
        self, 
        serial_number: str,
        button_count: int = 4,
        name: Optional[str] = None,
        **kwargs
    ):
        """Initialize button transmitter.
        
        Args:
            serial_number: Device serial number
            button_count: Number of buttons (1-4)
            name: Optional device name
            **kwargs: Additional arguments passed to BaseTransmitter
        """
        # Validate and set button count
        if button_count not in range(1, 5):
            _LOGGER.warning(
                "Invalid button count %d, defaulting to 4", button_count
            )
            button_count = 4
        
        # Determine subtype and operating mode
        subtype = self.BUTTON_COUNT_TO_SUBTYPE[button_count]
        operating_mode = self.BUTTON_COUNT_TO_MODE[button_count]
        
        # Initialize with proper configuration
        super().__init__(
            serial_number=serial_number,
            device_type=DeviceType.EW_TRANSMITTER,
            subtype=subtype,
            operating_mode=operating_mode,
            button_count=button_count,
            name=name,
            **kwargs
        )
        
        self._button_count = button_count
        self._button_press_times: Dict[int, datetime] = {}
        self._button_last_actions: Dict[int, str] = {}
        self._button_labels = self.BUTTON_LABELS[:button_count]
        self._battery_low = False  # Track battery low status
    
    @property
    def button_count(self) -> int:
        """Return number of buttons."""
        return self._button_count
    
    @property
    def button_labels(self) -> List[str]:
        """Get list of button labels."""
        return self._button_labels
    
    @property
    def supported_entity_types(self) -> List[str]:
        """Return supported entity types."""
        return ["binary_sensor", "device_trigger"]
    
    def get_entity_specs(self) -> Dict[str, List[Dict[str, Any]]]:
        """Generate entity specifications for this button transmitter."""
        specs = {
            "switch": [],
            "light": [],
            "cover": [],
            "sensor": [],
            "binary_sensor": [],
            "button": []
        }
        
        # Create binary sensor entities for each button
        for button_idx in range(self._button_count):
            button_label = self._button_labels[button_idx]
            specs["binary_sensor"].append(self._create_base_entity_spec(
                "binary_sensor",
                channel=button_idx,
                name=f"Taste {button_label}",
                device_class="button",
                icon="mdi:gesture-tap-button"
            ))
        
        # Create battery status binary sensor
        battery_warning_spec = self._create_base_entity_spec(
            "binary_sensor",
            name="Batteriestand",
            device_class="battery",
            icon="mdi:battery"
        )
        battery_warning_spec["unique_id"] = f"{self.serial_number}_battery_warning"
        battery_warning_spec["sensor_type"] = "battery_warning"
        specs["binary_sensor"].append(battery_warning_spec)
        
        return specs
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram for button transmitter."""
        info_type = telegram_data.get("info_type")
        button = telegram_data.get("button", 0)
        current_time = datetime.now()
        
        if info_type == 0:
            # Update battery low status
            is_low_battery = telegram_data.get("is_low_battery", False)
            self._battery_low = is_low_battery
            _LOGGER.warning(
                "🔋 Low battery detected for EW %d-Button Transmitter %s",
                self._button_count,
                self.serial_number[-6:]
            )
        
        # Handle different telegram formats
        if info_type in [TELEGRAM_BUTTON_RELEASE, TELEGRAM_EW_BUTTON_RELEASE]:
            self._handle_button_release(button, current_time, telegram_data)
        elif info_type in [TELEGRAM_BUTTON_PUSH, TELEGRAM_EW_BUTTON_PRESS]:
            self._handle_button_press(button, current_time, telegram_data)
        
        return self.get_button_data()
    
    def _handle_button_press(
        self, 
        button: int, 
        timestamp: datetime, 
        telegram_data: Dict[str, Any]
    ) -> None:
        """Handle button press event."""
        # Support both 0-indexed and 1-indexed button IDs
        button_id = self._normalize_button_id(button, telegram_data)
        
        if 0 <= button_id < self._button_count:
            self._button_press_times[button_id] = timestamp
            self._button_last_actions[button_id] = 'press'
            self.set_button_state(button_id, True)
            self.register_button_press(button_id)  # From ButtonBehaviorMixin
            self._last_seen = timestamp
            
            _LOGGER.debug(
                "EW %d-Button Transmitter %s button %s pressed", 
                self._button_count,
                self.serial_number[-6:], 
                self._button_labels[button_id]
            )
    
    def _handle_button_release(
        self, 
        button: int, 
        timestamp: datetime, 
        telegram_data: Dict[str, Any]
    ) -> None:
        """Handle button release event."""
        # Support both 0-indexed and 1-indexed button IDs
        button_id = self._normalize_button_id(button, telegram_data)
        
        if 0 <= button_id < self._button_count:
            self._button_last_actions[button_id] = 'release'
            self.set_button_state(button_id, False)
            self._last_seen = timestamp
            
            # Calculate press duration
            press_duration = self._calculate_press_duration(button_id, timestamp)
            
            _LOGGER.debug(
                "EW %d-Button Transmitter %s button %s released (duration: %.0fms)", 
                self._button_count,
                self.serial_number[-6:], 
                self._button_labels[button_id],
                press_duration or 0
            )
    
    def _normalize_button_id(
        self, 
        button: int, 
        telegram_data: Dict[str, Any]
    ) -> int:
        """Normalize button ID to 0-indexed format.
        
        Handles both 0-indexed (0-3) and 1-indexed (1-4) button IDs.
        """
        # Check for button_id in data (may be 1-indexed)
        data = telegram_data.get("data", {})
        if "button_id" in data:
            btn = data["button_id"]
            # Convert 1-indexed to 0-indexed if needed
            if btn >= 1 and btn <= 4:
                return btn - 1
            return btn
        
        # Use the button parameter directly (assumed 0-indexed)
        return button
    
    def _calculate_press_duration(
        self, 
        button: int, 
        release_time: datetime
    ) -> Optional[float]:
        """Calculate press duration in milliseconds."""
        if button in self._button_press_times:
            duration = (
                release_time - self._button_press_times[button]
            ).total_seconds() * 1000
            del self._button_press_times[button]
            return duration
        return None
    
    def get_button_press_duration(self, button: int) -> Optional[float]:
        """Get current press duration for a button in milliseconds."""
        if button in self._button_press_times:
            return (
                datetime.now() - self._button_press_times[button]
            ).total_seconds() * 1000
        return None
    
    def get_button_data(self) -> Dict[str, Any]:
        """Get current button data for coordinator."""
        data = {
            "button_count": self._button_count,
            "button_states": {},
            "last_actions": {},
            "battery_warning": self._battery_low,  # Add battery warning status
        }
        
        # Add button states and actions
        for button in range(self._button_count):
            button_label = self._button_labels[button].lower()
            
            # Add button state
            button_state = self.get_button_state(button)
            if button_state is not None:
                data["button_states"][button_label] = button_state
                # Legacy format for compatibility
                data[f"button_{button_label}_state"] = button_state
            
            # Add last action
            last_action = self._button_last_actions.get(button)
            if last_action:
                data["last_actions"][button_label] = last_action
                # Legacy format for compatibility
                data[f"button_{button_label}_last_action"] = last_action
        
        return data
    
    def is_button_pressed(self, button_id: int) -> bool:
        """Check if specific button is currently pressed."""
        return bool(self.get_button_state(button_id))


# Factory functions for creating button transmitters
def create_rx11_button_transmitter(
    serial_number: str,
    device_info: Dict[str, Any],
    button_count: int = 4,
    **kwargs
) -> RX11ButtonTransmitter:
    """Factory function to create button transmitter with specified button count."""
    return RX11ButtonTransmitter(
        serial_number=serial_number,
        button_count=button_count,
        name=device_info.get('name'),
        **kwargs
    )


# Backward compatibility factory functions
def create_rx11_single_button_transmitter(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11ButtonTransmitter:
    """Factory function for single button transmitter (backward compatibility)."""
    return create_rx11_button_transmitter(
        serial_number, device_info, button_count=1, **kwargs
    )


def create_rx11_dual_button_transmitter(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11ButtonTransmitter:
    """Factory function for dual button transmitter (backward compatibility)."""
    return create_rx11_button_transmitter(
        serial_number, device_info, button_count=2, **kwargs
    )


def create_rx11_triple_button_transmitter(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11ButtonTransmitter:
    """Factory function for triple button transmitter (backward compatibility)."""
    return create_rx11_button_transmitter(
        serial_number, device_info, button_count=3, **kwargs
    )


def create_rx11_quad_button_transmitter(
    serial_number: str,
    device_info: Dict[str, Any],
    **kwargs
) -> RX11ButtonTransmitter:
    """Factory function for quad button transmitter (backward compatibility)."""
    return create_rx11_button_transmitter(
        serial_number, device_info, button_count=4, **kwargs
    )


# Backward compatibility class aliases
RX11SingleButtonTransmitter = RX11ButtonTransmitter
RX11DualButtonTransmitter = RX11ButtonTransmitter
RX11TripleButtonTransmitter = RX11ButtonTransmitter
RX11QuadButtonTransmitter = RX11ButtonTransmitter
