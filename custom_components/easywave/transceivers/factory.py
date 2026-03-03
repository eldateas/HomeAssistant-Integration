"""Factory for creating transceivers."""
from __future__ import annotations

import logging
from typing import Optional, Type

from .base import BaseTransceiver, TransceiverType
from .rx11 import RX11Transceiver

_LOGGER = logging.getLogger(__name__)


class TransceiverFactory:
    """Factory for creating EASYWAVE transceivers."""
    
    _registry = {
        TransceiverType.RX11: RX11Transceiver,
        # Future transceivers can be added here:
        # TransceiverType.RX21: RX21Transceiver,
        # TransceiverType.Gateway: GatewayTransceiver,
    }
    
    @classmethod
    def get_supported_types(cls) -> list[TransceiverType]:
        """Get list of supported transceiver types."""
        return list(cls._registry.keys())
    
    @classmethod
    def create_transceiver(cls, transceiver_type: TransceiverType, 
                          device_path: str = None) -> BaseTransceiver:
        """Create a transceiver instance."""
        transceiver_class = cls._registry.get(transceiver_type)
        if not transceiver_class:
            raise ValueError(f"Unsupported transceiver type: {transceiver_type}")
            
        return transceiver_class(device_path)
    
    @classmethod
    def detect_transceiver_type(cls, device_path: str) -> Optional[TransceiverType]:
        """Detect transceiver type from device path."""
        # For now, just check if it's an RX11 device
        try:
            from .rx11.transceiver import validate_rx11_device
            if validate_rx11_device(device_path):
                return TransceiverType.RX11
        except Exception as e:
            _LOGGER.debug("Error detecting transceiver type: %s", e)
            
        return None
    
    @classmethod
    def get_available_types(cls) -> list[TransceiverType]:
        """Get list of available transceiver types."""
        return list(cls._registry.keys())
    
    @classmethod
    def register_transceiver(cls, transceiver_type: TransceiverType, 
                           transceiver_class: Type[BaseTransceiver]) -> None:
        """Register a new transceiver type."""
        cls._registry[transceiver_type] = transceiver_class
        _LOGGER.info("Registered transceiver type: %s", transceiver_type.value)