"""Cover behavior mixin for cover devices (blinds, shutters)."""
from __future__ import annotations

from typing import Any, Optional


class CoverBehaviorMixin:
    """Mixin für Cover-Verhalten (Rolladen, Jalousien)."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._position = {}  # channel -> position (0-100)
        self._tilt_position = {}  # channel -> tilt position (0-100)
        self._cover_state = {}  # channel -> 'open', 'closed', 'opening', 'closing', 'stopped'
    
    def get_cover_position(self, channel: int = 0) -> int:
        """Get current cover position (0=closed, 100=open)."""
        return self._position.get(channel, 0)
    
    def get_cover_tilt_position(self, channel: int = 0) -> Optional[int]:
        """Get current tilt position if supported."""
        return self._tilt_position.get(channel)
    
    def get_cover_state(self, channel: int = 0) -> str:
        """Get current cover state."""
        return self._cover_state.get(channel, 'unknown')
    
    def set_cover_position(self, channel: int, position: int) -> None:
        """Set cover position internally."""
        self._position[channel] = max(0, min(100, position))
    
    def set_cover_tilt_position(self, channel: int, tilt: int) -> None:
        """Set cover tilt position internally."""
        self._tilt_position[channel] = max(0, min(100, tilt))
    
    def set_cover_state(self, channel: int, state: str) -> None:
        """Set cover state internally."""
        self._cover_state[channel] = state
    
    async def async_open_cover(self, channel: int = 0) -> bool:
        """Open the cover."""
        self.set_cover_state(channel, 'opening')
        self.set_cover_position(channel, 100)
        # Wird von konkreter Klasse überschrieben für echte Befehle
        return await self.set_state(channel, {'command': 'open'})
    
    async def async_close_cover(self, channel: int = 0) -> bool:
        """Close the cover."""
        self.set_cover_state(channel, 'closing')
        self.set_cover_position(channel, 0)
        return await self.set_state(channel, {'command': 'close'})
    
    async def async_stop_cover(self, channel: int = 0) -> bool:
        """Stop the cover."""
        self.set_cover_state(channel, 'stopped')
        return await self.set_state(channel, {'command': 'stop'})
    
    async def async_set_cover_position(self, channel: int, position: int) -> bool:
        """Set cover to specific position."""
        self.set_cover_position(channel, position)
        if position == 100:
            self.set_cover_state(channel, 'open')
        elif position == 0:
            self.set_cover_state(channel, 'closed')
        else:
            self.set_cover_state(channel, 'stopped')
        return await self.set_state(channel, {'position': position})
    
    async def async_set_cover_tilt_position(self, channel: int, tilt: int) -> bool:
        """Set cover tilt to specific position."""
        self.set_cover_tilt_position(channel, tilt)
        return await self.set_state(channel, {'tilt': tilt})
    
    @property
    def supports_position(self) -> bool:
        """Return if device supports position control."""
        return True
    
    @property
    def supports_tilt(self) -> bool:
        """Return if device supports tilt control."""
        return False  # Override in subclass if supported
