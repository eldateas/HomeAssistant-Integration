"""Base transceiver class for ELDAT transceivers."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional, Set

from .enums import TransceiverType
from .device_info import DeviceInfo, TransceiverCapabilities


class BaseTransceiver(ABC):
    """Abstract base class for ELDAT transceivers."""
    
    def __init__(self, device_path: str = None):
        """Initialize the transceiver."""
        self.device_path = device_path
        self.connected_devices: Dict[str, DeviceInfo] = {}
        self._telegram_callback: Optional[Callable] = None
        self._device_action_callback: Optional[Callable] = None
        self._listening_for_telegram = False
        self._learning_mode = False
        self._disposed = False
        
        # Concurrency protection
        self._lock = asyncio.Lock()

    @property
    @abstractmethod
    def transceiver_type(self) -> TransceiverType:
        """Return the transceiver type."""
        pass

    @property
    @abstractmethod
    def capabilities(self) -> TransceiverCapabilities:
        """Return the transceiver capabilities."""
        pass

    @abstractmethod
    async def async_setup(self, hass) -> bool:
        """Set up the transceiver."""
        pass

    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the transceiver."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the transceiver."""
        pass

    @abstractmethod
    async def get_hw_version(self) -> Optional[str]:
        """Get hardware version."""
        pass

    @abstractmethod
    async def get_fw_version(self) -> Optional[str]:
        """Get firmware version."""
        pass

    @abstractmethod
    async def start_learning_mode(self, duration: int = 60) -> bool:
        """Start learning mode."""
        pass

    @abstractmethod
    async def stop_learning_mode(self) -> bool:
        """Stop learning mode."""
        pass

    @abstractmethod
    async def send_command_to_device(self, serial_number: str, command: bytes) -> bool:
        """Send command to a specific device."""
        pass

    @abstractmethod
    async def start_telegram_listening(self, callback: Callable) -> bool:
        """Start listening for telegrams."""
        pass

    @abstractmethod
    async def stop_telegram_listening(self) -> bool:
        """Stop listening for telegrams."""
        pass

    def register_telegram_callback(self, callback: Callable) -> None:
        """Register callback for incoming telegrams."""
        self._telegram_callback = callback

    def register_device_action_callback(self, callback: Callable) -> None:
        """Register callback for device actions."""
        self._device_action_callback = callback

    def register_known_device(self, serial_number: str, device_info: Dict[str, Any]) -> None:
        """Register a known device."""
        device = DeviceInfo(
            serial_number=serial_number,
            device_type=device_info.get("type", "unknown"),
            name=device_info.get("name"),
            capabilities=device_info.get("capabilities", {}),
            rx11_index=device_info.get("rx11_index")
        )
        self.connected_devices[serial_number] = device

    def get_all_devices(self) -> Dict[str, DeviceInfo]:
        """Get all connected devices."""
        return self.connected_devices.copy()

    async def async_shutdown(self) -> None:
        """Shutdown the transceiver."""
        if self._disposed:
            return
            
        self._disposed = True
        
        # Stop telegram listening
        if self._listening_for_telegram:
            await self.stop_telegram_listening()
        
        # Stop learning mode
        if self._learning_mode:
            await self.stop_learning_mode()
            
        # Disconnect
        await self.disconnect()


class BaseDeviceHandler(ABC):
    """Abstract base class for device type handlers."""
    
    def __init__(self, transceiver: BaseTransceiver):
        """Initialize the device handler."""
        self.transceiver = transceiver
    
    @property
    @abstractmethod
    def supported_device_types(self) -> Set[str]:
        """Return supported device types."""
        pass
    
    @abstractmethod
    async def handle_telegram(self, serial_number: str, telegram_data: Dict[str, Any]) -> None:
        """Handle incoming telegram for this device type."""
        pass
    
    @abstractmethod
    async def create_entity_data(self, device_info: DeviceInfo) -> Dict[str, Any]:
        """Create entity data for this device type."""
        pass
    
    @abstractmethod
    async def send_command(self, device_info: DeviceInfo, command: str, **kwargs) -> bool:
        """Send command to device."""
        pass
