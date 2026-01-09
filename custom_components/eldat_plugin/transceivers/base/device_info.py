"""Device information and capabilities data classes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Set


@dataclass
class TransceiverCapabilities:
    """Capabilities of a transceiver."""
    supports_learning: bool = False
    supports_bidirectional: bool = False
    supports_continuous_sending: bool = False
    supports_security: bool = False
    max_devices: int = 255
    device_types: Set[str] = None
    
    def __post_init__(self):
        if self.device_types is None:
            self.device_types = set()


@dataclass
class DeviceInfo:
    """Information about a discovered device."""
    serial_number: str
    device_type: str
    name: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None
    rx11_index: Optional[int] = None
    last_seen: Optional[float] = None
    
    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = {}
