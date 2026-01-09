"""Base receiver class for all ELDAT receivers."""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict

from .device import BaseDevice


class BaseReceiver(BaseDevice):
    """Abstrakte Basisklasse für alle Receiver."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._channels = {}
        self._learned_transmitters = set()
    
    @property
    def channels(self) -> Dict[int, Any]:
        """Return available channels."""
        return self._channels
    
    @property
    def learned_transmitters(self) -> set:
        """Return set of learned transmitter serial numbers."""
        return self._learned_transmitters
    
    def add_learned_transmitter(self, serial_number: str) -> None:
        """Add a learned transmitter."""
        self._learned_transmitters.add(serial_number)
    
    def remove_learned_transmitter(self, serial_number: str) -> None:
        """Remove a learned transmitter."""
        self._learned_transmitters.discard(serial_number)
    
    @abstractmethod
    async def set_state(self, channel: int, state: Any) -> bool:
        """Set state for a specific channel."""
        pass
    
    @abstractmethod
    async def get_state(self, channel: int) -> Any:
        """Get current state for a specific channel."""
        pass
