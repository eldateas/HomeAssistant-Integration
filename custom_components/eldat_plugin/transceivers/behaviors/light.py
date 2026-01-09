"""Light behavior mixin for dimmable lights."""
from __future__ import annotations

from typing import Optional


class LightBehaviorMixin:
    """Mixin für Light-Verhalten (Dimmer)."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._light_state = {}  # channel -> bool (on/off)
        self._brightness = {}  # channel -> 0-255
    
    def get_light_state(self, channel: int = 0) -> bool:
        """Get current light state (on/off)."""
        return self._light_state.get(channel, False)
    
    def get_brightness(self, channel: int = 0) -> int:
        """Get current brightness (0-255)."""
        return self._brightness.get(channel, 255)
    
    def set_light_state(self, channel: int, state: bool) -> None:
        """Set light state internally."""
        self._light_state[channel] = bool(state)
    
    def set_brightness(self, channel: int, brightness: int) -> None:
        """Set brightness internally."""
        self._brightness[channel] = max(0, min(255, brightness))
        if brightness > 0:
            self.set_light_state(channel, True)
        else:
            self.set_light_state(channel, False)
    
    async def async_turn_on_light(self, channel: int = 0, brightness: Optional[int] = None) -> bool:
        """Turn on the light."""
        if brightness is not None:
            self.set_brightness(channel, brightness)
        else:
            self.set_light_state(channel, True)
        return await self.set_state(channel, {
            'state': True,
            'brightness': self.get_brightness(channel)
        })
    
    async def async_turn_off_light(self, channel: int = 0) -> bool:
        """Turn off the light."""
        self.set_light_state(channel, False)
        return await self.set_state(channel, {'state': False})
    
    @property
    def supports_brightness(self) -> bool:
        """Return if device supports brightness control."""
        return True
