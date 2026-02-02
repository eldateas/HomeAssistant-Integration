"""Switch behavior mixin for on/off devices."""
from __future__ import annotations


class SwitchBehaviorMixin:
    """Mixin für Switch-Verhalten (Ein/Aus)."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._switch_state = {}  # channel -> bool
    
    def get_switch_state(self, channel: int = 0) -> bool:
        """Get current switch state."""
        return self._switch_state.get(channel, False)
    
    def set_switch_state(self, channel: int, state: bool) -> None:
        """Set switch state internally."""
        self._switch_state[channel] = bool(state)
    
    async def async_turn_on(self, channel: int = 0) -> bool:
        """Turn on the switch."""
        self.set_switch_state(channel, True)
        return await self.set_state(channel, True)
    
    async def async_turn_off(self, channel: int = 0) -> bool:
        """Turn off the switch."""
        self.set_switch_state(channel, False)
        return await self.set_state(channel, False)
    
    async def async_toggle(self, channel: int = 0) -> bool:
        """Toggle the switch."""
        current = self.get_switch_state(channel)
        return await (self.async_turn_off(channel) if current else self.async_turn_on(channel))
