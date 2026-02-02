"""Base transmitter class for all ELDAT transmitters."""
from __future__ import annotations

from typing import Any, Dict

from .device import BaseDevice
from .enums import OperatingMode


class BaseTransmitter(BaseDevice):
    """Abstrakte Basisklasse für alle Transmitter."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._button_count = self._determine_button_count()
        self._button_states = {}
    
    def _determine_button_count(self) -> int:
        """Determine button count based on operating mode."""
        mode_to_buttons = {
            OperatingMode.ONE_BUTTON: 1,
            OperatingMode.TWO_BUTTON: 2,
            OperatingMode.THREE_BUTTON: 3,
            OperatingMode.FOUR_BUTTON: 4,
        }
        return mode_to_buttons.get(self.operating_mode, 4)
    
    @property
    def button_count(self) -> int:
        """Return number of buttons."""
        return self._button_count
    
    @property
    def button_states(self) -> Dict[int, Any]:
        """Return current button states."""
        return self._button_states
    
    def set_button_state(self, button: int, state: Any) -> None:
        """Set state for a specific button."""
        if 0 <= button < self._button_count:
            self._button_states[button] = state
    
    def get_button_state(self, button: int) -> Any:
        """Get state for a specific button."""
        return self._button_states.get(button)
