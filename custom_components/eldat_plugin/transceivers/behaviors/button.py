"""Button behavior mixin for transmitter buttons."""
from __future__ import annotations

from typing import Optional


class ButtonBehaviorMixin:
    """Mixin für Button-Verhalten (Transmitter Buttons)."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_button_press = {}  # button_index -> timestamp
        self._button_press_count = {}  # button_index -> count
    
    def register_button_press(self, button_index: int) -> None:
        """Register a button press event."""
        import time
        self._last_button_press[button_index] = time.time()
        self._button_press_count[button_index] = self._button_press_count.get(button_index, 0) + 1
    
    def get_last_button_press(self, button_index: int) -> Optional[float]:
        """Get timestamp of last button press."""
        return self._last_button_press.get(button_index)
    
    def get_button_press_count(self, button_index: int) -> int:
        """Get total number of button presses."""
        return self._button_press_count.get(button_index, 0)
    
    async def async_press_button(self, button_index: int) -> bool:
        """Simulate button press."""
        self.register_button_press(button_index)
        # Override in concrete class to send actual command
        return True
