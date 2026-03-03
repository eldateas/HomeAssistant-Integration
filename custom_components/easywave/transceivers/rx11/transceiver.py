"""RX11 USB Transceiver implementation."""
from __future__ import annotations

import asyncio
import logging
import serial
import serial.tools.list_ports
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..base import BaseTransceiver, TransceiverType, TransceiverCapabilities, DeviceInfo
from .wrapper import RX11Wrapper

_LOGGER = logging.getLogger(__name__)

# USB device identifiers — derived from the central registry in const.py
from ...const import SUPPORTED_USB_IDS, is_supported_usb_device, usb_device_name


def find_rx11_devices():
    """Find all connected Easywave USB devices with detailed information.
    
    Scans for every (VID, PID) pair registered in USB_DEVICE_NAMES.
    """
    rx11_devices = []
    
    try:
        _LOGGER.info("Scanning for Easywave USB devices...")
        all_ports = serial.tools.list_ports.comports()
        _LOGGER.debug("Found %d total USB ports", len(all_ports))
        
        for port in all_ports:
            _LOGGER.debug("Checking device: %s (VID: 0x%04X, PID: 0x%04X)", 
                         port.device, port.vid or 0, port.pid or 0)
            
            if is_supported_usb_device(port.vid, port.pid):
                mfr, prod = usb_device_name(port.vid, port.pid)
                device_info = {
                    "device": port.device,
                    "name": prod,
                    "manufacturer": mfr,
                    "serial_number": port.serial_number,
                    "vid": port.vid,
                    "pid": port.pid,
                    "location": port.location,
                    "hwid": port.hwid if hasattr(port, 'hwid') else None
                }
                rx11_devices.append(device_info)
                _LOGGER.info("Found Easywave device: %s at %s", prod, port.device)
        
        _LOGGER.info("Found %d Easywave USB device(s)", len(rx11_devices))
        return rx11_devices
        
    except Exception as e:
        _LOGGER.error("Error scanning for Easywave USB devices: %s", e)
        return []


def validate_rx11_device(device_path: str) -> bool:
    """Validate that a device path points to a supported Easywave USB device."""
    try:
        all_ports = serial.tools.list_ports.comports()
        
        for port in all_ports:
            if port.device == device_path:
                is_valid = is_supported_usb_device(port.vid, port.pid)
                _LOGGER.debug("Device %s validation: VID=0x%04X, PID=0x%04X, supported=%s", 
                             device_path, port.vid or 0, port.pid or 0, is_valid)
                return is_valid
        
        _LOGGER.debug("Device %s not found in system", device_path)
        return False
        
    except Exception as e:
        _LOGGER.error("Error validating device %s: %s", device_path, e)
        return False


class RX11Transceiver(BaseTransceiver):
    """RX11 USB Transceiver implementation.
    
    Structured Naming Convention:
    - rx11_<device_type>_<operation>: Device-specific operations
      - rx11_ew_receiver_*: EW receiver operations
      - rx11_ew_transmitter_*: EW transmitter operations
      - rx11_ewb_sensor_*: EWB sensor operations
      - rx11_ew_receiver_button_*: Button-specific operations
    
    This class acts as a higher-level interface to the RX11 wrapper,
    providing structured access to different device types and operations.
    """
    
    def __init__(self, device_path: str = None):
        """Initialize RX11 transceiver."""
        super().__init__(device_path)
        
        # Home Assistant reference (set during async_setup)
        self._hass = None
        
        # RX11-specific attributes
        self._rx11_wrapper: Optional[RX11Wrapper] = None
        self._hw_version: Optional[str] = None
        self._fw_version: Optional[str] = None
        
        # RX11-specific state
        self._continuous_sending_tasks: Dict[str, Dict] = {}  # serial -> {task, cancel_event, commands}
        self._rx11_indices: Dict[str, int] = {}  # serial -> rx11_index mapping
        self._serial_connection = None  # Fallback for simple serial communication
        # Battery status: None=unknown, "low"=battery low, "pending_recovery"=first normal press seen, "normal"=battery OK
        self._battery_status: Dict[str, Optional[str]] = {}
        self._saw_battery_telegram: Dict[str, bool] = {}  # Track if 0x80 was seen in current press cycle
        self._release_processed: Dict[str, bool] = {}  # Track if release was already processed for this cycle
        self._last_pressed_button: Dict[str, int] = {}  # Track last pressed button per transmitter for release telegrams
        
        # Initialize RX11 wrapper if device path is provided
        if device_path:
            try:
                self._rx11_wrapper = RX11Wrapper(device_path)
                _LOGGER.info("✅ RX11 wrapper initialized successfully")
            except Exception as error:
                _LOGGER.error("Failed to initialize RX11 wrapper: %s", error)
                self._rx11_wrapper = None

        # Initialize device factory
        from .devices.registry import RX11DeviceFactory
        self.device_factory = RX11DeviceFactory()
        self._device_instances: Dict[str, Any] = {}

    @property
    def transceiver_type(self) -> TransceiverType:
        """Return RX11 transceiver type."""
        return TransceiverType.RX11

    @property
    def capabilities(self) -> TransceiverCapabilities:
        """Return RX11 capabilities."""
        return TransceiverCapabilities(
            supports_learning=True,
            supports_bidirectional=True,
            supports_continuous_sending=True,
            supports_security=False,
            max_devices=255,
            device_types={
                "EW_Transmitter", "EW_Receiver", "EW_Dimmer", 
                "EW_Sensor", "Motor", "WinDim"
            }
        )
    
    @property
    def wrapper(self) -> Optional[RX11Wrapper]:
        """Return the RX11 wrapper instance for direct access."""
        return self._rx11_wrapper

    def set_usb_serial_number(self, serial_number: str) -> None:
        """Set USB serial number for device identification."""
        if self._rx11_wrapper:
            self._rx11_wrapper.set_usb_serial_number(serial_number)
        _LOGGER.debug("📋 RX11 USB Serial Number set: %s", serial_number)
    
    def update_usb_identity(
        self,
        serial_number: Optional[str] = None,
        vid: Optional[int] = None,
        pid: Optional[int] = None,
    ) -> None:
        """Update full USB device identity (serial, VID, PID).
        
        Delegates to wrapper which detects stick swaps and invalidates
        version caches when the physical device has changed.
        """
        if self._rx11_wrapper:
            self._rx11_wrapper.update_usb_identity(
                serial_number=serial_number, vid=vid, pid=pid,
            )
    
    def get_usb_serial_number(self) -> Optional[str]:
        """Get USB serial number for device identification."""
        if self._rx11_wrapper:
            return self._rx11_wrapper.get_usb_serial_number()
        return None
    
    def get_device_info(self) -> dict:
        """Get complete device information including USB and version details.
        
        Returns dictionary with device identification, USB information, and versions.
        """
        device_info = {
            'device_path': self.device_path,
            'usb_serial_number': self.get_usb_serial_number(),
            'hw_version': self._hw_version,
            'fw_version': self._fw_version,
            'connected': self.is_connected
        }
        
        if self._rx11_wrapper:
            wrapper_info = self._rx11_wrapper.get_device_info()
            device_info.update(wrapper_info)
        
        return device_info

    def _update_usb_identity_from_device_info(self, device_info: dict) -> None:
        """Update USB identity from a find_rx11_devices() result dict.
        
        Called after a successful VID/PID-based reconnect so the wrapper
        (and therefore get_usb_serial_number()) reflects the actual stick.
        """
        self.update_usb_identity(
            serial_number=device_info.get("serial_number"),
            vid=device_info.get("vid"),
            pid=device_info.get("pid"),
        )

    async def _refresh_usb_identity_for_connected_device(self) -> None:
        """Read actual USB serial/VID/PID from the connected port and update identity.
        
        Handles the case where a different stick is plugged into the same
        port — the device_path hasn't changed but the hardware has.
        """
        if not self.device_path or not self._hass:
            return
        try:
            import serial.tools.list_ports
            def _scan():
                for port in serial.tools.list_ports.comports():
                    if port.device == self.device_path:
                        return port
                return None

            port = await self._hass.async_add_executor_job(_scan)
            if port and port.vid and port.pid:
                self.update_usb_identity(
                    serial_number=port.serial_number,
                    vid=port.vid,
                    pid=port.pid,
                )
        except Exception as e:
            _LOGGER.debug("Could not refresh USB identity: %s", e)

    def get_connection_health_stats(self) -> dict:
        """Get connection health and error statistics."""
        if self._rx11_wrapper:
            stats = self._rx11_wrapper.get_connection_stats()
            stats.update({
                'device_path': self.device_path,
                'hw_version': self._hw_version,
                'fw_version': self._fw_version,
                'transceiver_connected': self.is_connected
            })
            return stats
        else:
            return {
                'connected': False,
                'serial_error_count': 0,
                'consecutive_errors': 0,
                'reconnect_in_progress': False,
                'last_reconnect_attempt': 0,
                'device_path': self.device_path,
                'error': 'RX11 wrapper not available'
            }

    async def async_setup(self, hass) -> bool:
        """Set up the RX11 transceiver."""
        # Store hass reference for proper event loop access
        self._hass = hass
        
        if not self.device_path:
            _LOGGER.info("RX11 transceiver in offline mode - skipping device validation")
            return True
            
        # Validate device asynchronously
        is_valid = await hass.async_add_executor_job(validate_rx11_device, self.device_path)
        
        if not is_valid:
            _LOGGER.debug("Device %s is not a supported Easywave USB device", self.device_path)
            return False
        
        _LOGGER.info("RX11 device %s validated successfully", self.device_path)
        return True
    
    def set_coordinator_reference(self, coordinator) -> None:
        """Set coordinator reference for EWB index tracking."""
        if self._rx11_wrapper:
            self._rx11_wrapper.set_coordinator(coordinator)
            _LOGGER.debug("Set coordinator reference in RX11 wrapper for EWB tracking")

    async def connect(self) -> bool:
        """Connect to the RX11 transceiver.
        
        If the configured device path is not available, this method will
        search for the device by VID/PID to handle USB port changes.
        Also works when no device_path is set (offline start) — searches
        by VID/PID directly.
        """
        # Always check actual connection status, don't trust cached state
        async with self._lock:
            # Add small delay if recently disconnected to allow device to reset
            import time
            if hasattr(self, '_last_disconnect_time'):
                time_since_disconnect = time.time() - self._last_disconnect_time
                if time_since_disconnect < 1.0:  # Less than 1 second
                    delay = 1.0 - time_since_disconnect
                    _LOGGER.debug("Waiting %.2fs for RX11 device to reset", delay)
                    await asyncio.sleep(delay)
            
            # First, try the current device path (if available)
            if self.device_path:
                _LOGGER.info("🔌 Connecting to RX11 at %s...", self.device_path)
            
                success = await self._try_connect_to_path(self.device_path)
                if success:
                    # Refresh USB identity — a different stick may be on the same port
                    await self._refresh_usb_identity_for_connected_device()
                    return True
            
            # If original path failed (or no path set), search for device by VID/PID
            if self.device_path:
                if not hasattr(self, '_search_logged'):
                    _LOGGER.info("⚠️ Connection to %s failed, searching for RX11 device by VID/PID...", 
                                  self.device_path)
                    self._search_logged = True
                else:
                    _LOGGER.debug("Connection to %s failed, searching for alternative device...", self.device_path)
            else:
                _LOGGER.debug("No device path configured — searching for RX11 device by VID/PID...")
            
            # Search for RX11 devices asynchronously
            if self._hass:
                rx11_devices = await self._hass.async_add_executor_job(find_rx11_devices)
            else:
                rx11_devices = find_rx11_devices()
            
            if not rx11_devices:
                _LOGGER.debug("No supported Easywave USB device found")
                return False
            
            # Try each found device
            for device_info in rx11_devices:
                new_path = device_info['device']
                if new_path == self.device_path:
                    continue  # Already tried this path
                
                _LOGGER.info("🔄 USB device found: %s → %s", self.device_path or "(none)", new_path)
                
                # Update the device path in wrapper
                old_path = self.device_path
                self.device_path = new_path
                
                if self._rx11_wrapper:
                    self._rx11_wrapper.set_device_path(new_path)
                
                success = await self._try_connect_to_path(new_path)
                if success:
                    # Update USB identity so serial/VID/PID reflect the actual stick
                    self._update_usb_identity_from_device_info(device_info)
                    _LOGGER.info("✅ RX11 connected on port %s (was %s)", new_path, old_path or "(none)")
                    return True
            
            _LOGGER.debug("No RX11 device found on any port")
            return False

    async def _try_connect_to_path(self, device_path: str) -> bool:
        """Try to connect to RX11 at a specific device path.
        
        Returns True if connection successful, False otherwise.
        """
        try:
            # Try to connect with C library wrapper first
            if self._rx11_wrapper:
                # Ensure wrapper uses the correct path
                if hasattr(self._rx11_wrapper, 'set_device_path'):
                    self._rx11_wrapper.set_device_path(device_path)
                
                success = await self._rx11_wrapper.connect()
                if success:
                    # Always refresh version information after (re)connect
                    # so that stick swaps are reflected correctly
                    try:
                        self._hw_version = await self._rx11_wrapper.get_hw_version()
                        self._fw_version = await self._rx11_wrapper.get_fw_version()
                    except Exception as ver_err:
                        _LOGGER.debug("Could not read versions after connect: %s", ver_err)
                    
                    _LOGGER.info("RX11 connected via C library at %s: HW=%s, FW=%s", 
                               device_path, self._hw_version, self._fw_version)
                    return True
                else:
                    _LOGGER.debug("C library connection to %s failed, trying fallback", device_path)
            
            # Fallback to simple serial connection
            try:
                # Temporarily update device_path for fallback
                old_path = self.device_path
                self.device_path = device_path
                success = await self._setup_simple_serial_connection()
                if success:
                    _LOGGER.info("RX11 connected via serial fallback at %s", device_path)
                    return True
                self.device_path = old_path  # Restore on failure
            except Exception as e:
                _LOGGER.debug("Serial fallback connection to %s failed: %s", device_path, e)
            
            return False
            
        except Exception as e:
            _LOGGER.debug("Connection attempt to %s failed: %s", device_path, e)
            return False

    async def disconnect(self) -> None:
        """Disconnect from the RX11 transceiver."""
        if self._disposed:
            return
            
        async with self._lock:
            # Stop all continuous sending tasks
            for serial_number in list(self._continuous_sending_tasks.keys()):
                await self._stop_continuous_sending(serial_number)
            
            # Log cache statistics before disconnect
            if self._rx11_wrapper and hasattr(self._rx11_wrapper, 'get_cache_stats'):
                stats = self._rx11_wrapper.get_cache_stats()
                _LOGGER.info("📊 RX11 Cache Stats: %d total entries, %d valid, %d expired, %d used receivers", 
                           stats['total_entries'], stats['valid_entries'], 
                           stats['expired_entries'], stats['used_receivers'])
            
            # Disconnect C library wrapper
            if self._rx11_wrapper:
                await self._rx11_wrapper.disconnect()
            
            # Close serial connection
            if self._serial_connection:
                try:
                    self._serial_connection.close()
                except Exception:
                    pass
                self._serial_connection = None
            
            # Record disconnect time for reconnection delay
            import time
            self._last_disconnect_time = time.time()
            
            _LOGGER.info("RX11 transceiver disconnected")

    async def get_hw_version(self) -> Optional[str]:
        """Get hardware version."""
        if self._rx11_wrapper:
            return await self._rx11_wrapper.get_hw_version()
        return self._hw_version

    async def get_fw_version(self) -> Optional[str]:
        """Get firmware version.""" 
        if self._rx11_wrapper:
            return await self._rx11_wrapper.get_fw_version()
        return self._fw_version

    async def start_learning_mode(self, duration: int = 60) -> bool:
        """Start learning mode."""
        if not self._rx11_wrapper:
            _LOGGER.error("Learning mode requires C library wrapper")
            return False
            
        return await self._rx11_wrapper.set_learning_mode(True, duration)

    async def stop_learning_mode(self) -> bool:
        """Stop learning mode."""
        if not self._rx11_wrapper:
            _LOGGER.error("Learning mode requires C library wrapper")
            return False
            
        return await self._rx11_wrapper.set_learning_mode(False)

    async def set_learning_mode(self, enabled: bool, timeout: int = 60) -> bool:
        """Set learning mode on or off."""
        if enabled:
            return await self.start_learning_mode(timeout)
        else:
            return await self.stop_learning_mode()

    async def send_command_to_device(self, serial_number: str, command: bytes) -> bool:
        """Send command to a specific device."""
        try:
            # Convert serial number to int
            if isinstance(serial_number, str):
                serial_int = int(serial_number, 16) if len(serial_number) == 8 else int(serial_number)
            else:
                serial_int = serial_number
            
            # Extract command data
            if len(command) >= 1:
                tm_type = command[0]
                data1 = command[1] if len(command) > 1 else 0
                data2 = command[2] if len(command) > 2 else 0
                data3 = command[3] if len(command) > 3 else 0
                data4 = command[4] if len(command) > 4 else 0
                
                if self._rx11_wrapper:
                    return await self._rx11_wrapper.send_telegram(
                        serial_int, tm_type, data1, data2, data3, data4
                    )
                else:
                    _LOGGER.warning("Command sending requires C library wrapper")
                    return False
            else:
                _LOGGER.error("Invalid command format")
                return False
                
        except Exception as e:
            _LOGGER.error("❌ Error sending command to device %s: %s", serial_number, e)
            return False

    async def send_command_to_receiver(self, serial_number: str, command) -> bool:
        """Send command to EW receiver using cached serial mapping - no repeated EW_GET_FD_SERIAL calls."""
        try:
            # Convert command to bytes if needed
            if isinstance(command, bytes):
                command_bytes = command
            elif isinstance(command, str):
                # Check if it's a valid hex string with even length
                clean_command = command.replace(' ', '')
                if len(clean_command) % 2 == 0 and all(c in '0123456789ABCDEFabcdef' for c in clean_command):
                    command_bytes = bytes.fromhex(clean_command)
                else:
                    # Simple button mapping for EW receivers
                    button_map = {'10': b'\x10', '11': b'\x11', '12': b'\x12', '13': b'\x13', 'A': b'\x10', 'B': b'\x11', 'C': b'\x12', 'D': b'\x13'}
                    command_bytes = button_map.get(command.upper(), b'\x10')  # Default to button A
            else:
                command_bytes = bytes([command]) if isinstance(command, int) else b'\x00'
                
            # Delegate to wrapper's optimized rx11_ew_receiver_send_command method
            # This method now uses central cache and avoids repeated EwGetFdSerialRequest calls
            if self._rx11_wrapper and hasattr(self._rx11_wrapper, 'rx11_ew_receiver_send_command'):
                return await self._rx11_wrapper.rx11_ew_receiver_send_command(serial_number, command_bytes)
            # Fallback to alias for compatibility
            elif self._rx11_wrapper and hasattr(self._rx11_wrapper, 'send_command_to_receiver'):
                return await self._rx11_wrapper.send_command_to_receiver(serial_number, command_bytes)
            else:
                _LOGGER.error("❌ RX11 wrapper not available or method missing")
                return False
            
        except Exception as e:
            _LOGGER.error("❌ Error in send_command_to_receiver: %s", e)
            return False

    async def _send_easywave_protocol_command(self, serial_number: str, command: bytes) -> bool:
        """Send EASYWAVE protocol command via serial fallback."""
        try:
            if not self._serial_connection or not self._serial_connection.is_open:
                _LOGGER.error("No serial connection available for EASYWAVE protocol")
                return False
            
            # Convert serial number to device ID for EASYWAVE protocol
            try:
                device_id = int(serial_number) if serial_number.isdigit() else int(serial_number, 16)
            except ValueError:
                _LOGGER.error("Invalid serial number format: %s", serial_number)
                return False
                
            # Create EASYWAVE protocol frame
            # Basic frame structure: [Start][Length][Command][DeviceID][Data][Checksum]
            frame = bytearray()
            frame.append(0xAA)  # Start byte
            frame.append(len(command) + 5)  # Frame length
            frame.append(0x01)  # Command type (Send)
            
            # Add device ID (4 bytes)
            frame.extend(device_id.to_bytes(4, byteorder='little'))
            
            # Add command data
            frame.extend(command)
            
            # Calculate checksum (XOR of all bytes except start)
            checksum = 0
            for byte in frame[1:]:
                checksum ^= byte
            frame.append(checksum)
            
            # Send frame
            _LOGGER.debug("Sending EASYWAVE protocol frame: %s", frame.hex())
            self._serial_connection.write(frame)
            await asyncio.sleep(0.2)  # Wait for transmission
            
            # Check for response (optional)
            if self._serial_connection.in_waiting > 0:
                response = self._serial_connection.read(self._serial_connection.in_waiting)
                _LOGGER.debug("Received response: %s", response.hex())
            
            _LOGGER.info("✅ EASYWAVE protocol command sent to device %s", serial_number)
            return True
            
        except Exception as e:
            _LOGGER.error("Error in EASYWAVE protocol communication: %s", e)
            return False

    async def start_telegram_listening(self, callback: Callable) -> bool:
        """Start listening for telegrams."""
        if not self._rx11_wrapper:
            _LOGGER.error("Telegram listening requires C library wrapper")
            return False
            
        # Store the additional callback (e.g., for learning) but don't overwrite the main callback
        if hasattr(self, '_learning_callback'):
            self._learning_callback = callback
        else:
            self._additional_callback = callback
            
        self._listening_for_telegram = True
        
        _LOGGER.info("🔍 Starting telegram listening - main callback: %s, additional callback: %s", 
                    bool(self._telegram_callback), bool(callback))
        
        # Start telegram listening thread with internal handler
        success = self._rx11_wrapper.start_telegram_listening(self._handle_telegram)
        
        if success:
            _LOGGER.info("RX11 telegram listening started")
        else:
            self._listening_for_telegram = False
            
        return success

    async def stop_telegram_listening(self) -> bool:
        """Stop listening for telegrams."""
        if not self._listening_for_telegram:
            return True
            
        self._listening_for_telegram = False
        
        if self._rx11_wrapper:
            success = self._rx11_wrapper.stop_telegram_listening()
            if success:
                _LOGGER.info("RX11 telegram listening stopped")
            return success
            
        return True

    def _handle_telegram(self, telegram_data: Dict[str, Any]) -> None:
        """Handle incoming telegram from RX11."""
        try:
            # Convert to standard format
            serial_number = f"{telegram_data['serial']:08X}"
            
            processed_data = {
                'serial_number': serial_number,
                'type': telegram_data['type'],
                'data': [
                    telegram_data['data1'],
                    telegram_data['data2'], 
                    telegram_data['data3'],
                    telegram_data['data4']
                ],
                'raw': telegram_data
            }
            
            # Call registered callback
            if self._telegram_callback:
                asyncio.create_task(self._telegram_callback(processed_data))
                
        except Exception as e:
            _LOGGER.error("Error processing RX11 telegram: %s", e)

    # RX11-specific methods

    def assign_rx11_index(self, serial_number: str, rx11_index: int) -> None:
        """Assign RX11 index to a device."""
        self._rx11_indices[serial_number] = rx11_index

    def get_rx11_index(self, serial_number: str) -> Optional[int]:
        """Get RX11 index for a device."""
        return self._rx11_indices.get(serial_number)

    def get_available_rx11_indices(self) -> List[int]:
        """Get list of available RX11 indices using wrapper management."""
        if self._rx11_wrapper:
            used_indices = self._rx11_wrapper.get_used_indices()
            return [i for i in range(255) if i not in used_indices]
        else:
            # Fallback to old method if no wrapper available
            used_indices = set(self._rx11_indices.values())
            return [i for i in range(1, 256) if i not in used_indices]
            
    def get_next_free_rx11_index(self) -> Optional[int]:
        """Get the next free RX11 index."""
        if self._rx11_wrapper:
            return self._rx11_wrapper.get_next_free_index()
        else:
            available = self.get_available_rx11_indices()
            return available[0] if available else None

    async def _start_continuous_sending(self, serial_number: str, button: int) -> bool:
        """Start continuous sending for RX11."""
        try:
            if serial_number in self._continuous_sending_tasks:
                _LOGGER.warning("Continuous sending already active for %s", serial_number)
                return True
            
            cancel_event = asyncio.Event()
            task = asyncio.create_task(
                self._continuous_sending_worker(serial_number, button, cancel_event)
            )
            
            self._continuous_sending_tasks[serial_number] = {
                'task': task,
                'cancel_event': cancel_event,
                'button': button
            }
            
            _LOGGER.info("Started continuous sending for %s, button %d", serial_number, button)
            return True
            
        except Exception as e:
            _LOGGER.error("Error starting continuous sending for %s: %s", serial_number, e)
            return False

    async def _stop_continuous_sending(self, serial_number: str) -> bool:
        """Stop continuous sending for RX11."""
        try:
            task_info = self._continuous_sending_tasks.pop(serial_number, None)
            if not task_info:
                return True
            
            # Signal cancellation
            task_info['cancel_event'].set()
            
            # Wait for task to finish
            try:
                await asyncio.wait_for(task_info['task'], timeout=2.0)
            except asyncio.TimeoutError:
                task_info['task'].cancel()
                try:
                    await task_info['task']
                except asyncio.CancelledError:
                    pass
            
            _LOGGER.info("Stopped continuous sending for %s", serial_number)
            return True
            
        except Exception as e:
            _LOGGER.error("Error stopping continuous sending for %s: %s", serial_number, e)
            return False

    async def _continuous_sending_worker(self, serial_number: str, button: int, 
                                       cancel_event: asyncio.Event) -> None:
        """Worker for continuous sending."""
        try:
            # Send initial push command
            command = bytes([0x01, button, 0x00, 0x00, 0x00])  # TM_IT_EASW_PUSH
            await self.send_command_to_device(serial_number, command)
            
            # Keep sending while not cancelled
            while not cancel_event.is_set():
                await asyncio.sleep(0.5)  # 500ms interval
                
                if not cancel_event.is_set():
                    await self.send_command_to_device(serial_number, command)
            
            # Send release command
            command = bytes([0x00, button, 0x00, 0x00, 0x00])  # TM_IT_EASW_RELEASE
            await self.send_command_to_device(serial_number, command)
            
        except Exception as e:
            _LOGGER.error("Error in continuous sending worker for %s: %s", serial_number, e)

    async def _setup_simple_serial_connection(self) -> bool:
        """Set up simple serial connection as fallback with EASYWAVE protocol support."""
        try:
            _LOGGER.info("Setting up EASYWAVE protocol serial connection to %s", self.device_path)
            
            self._serial_connection = serial.Serial(
                port=self.device_path,
                baudrate=57600,  # Standard EASYWAVE baud rate
                timeout=1.0,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS
            )
            
            # Clear any existing data in buffers
            self._serial_connection.reset_input_buffer()
            self._serial_connection.reset_output_buffer()
            
            # Send initialization sequence
            await self._initialize_easywave_protocol()
            
            _LOGGER.info("✅ EASYWAVE protocol serial connection established")
            return True
            
        except Exception as e:
            _LOGGER.debug("EASYWAVE protocol connection failed: %s", e)
            return False

    def set_telegram_callback(self, callback: Callable) -> None:
        """Set callback function for incoming telegrams."""
        self._telegram_callback = callback
        _LOGGER.info("🔗 Telegram callback set to: %s (type: %s)", callback, type(callback))
        
        # Verify the callback was stored correctly
        if self._telegram_callback != callback:
            _LOGGER.error("❌ Callback storage failed! Expected %s, got %s", callback, self._telegram_callback)
        else:
            _LOGGER.info("✅ Callback verified and stored successfully")
        
        # Erstelle einen Wrapper-Callback für den RX11 Wrapper
        if self._rx11_wrapper and hasattr(self._rx11_wrapper, 'set_telegram_callback'):
            def wrapper_callback(info_type: int, receiver_transmitter: bytes, info_data: bytes) -> None:
                _LOGGER.debug("WRAPPER_CALLBACK STARTED! info_type=%s, receiver_len=%d, data_len=%d", 
                              info_type, len(receiver_transmitter), len(info_data))
                try:
                    # Parse rohe Daten zu strukturiertem Dictionary
                    telegram_data = self._parse_telegram_data(info_type, receiver_transmitter, info_data)
                    
                    if not telegram_data:
                        # None means telegram should be ignored (e.g., battery status telegram)
                        _LOGGER.debug("Telegram ignored (internal processing only)")
                        return
                    
                    _LOGGER.debug("📨 Parsed telegram: Serial=%s (full), Type=%s", 
                                  telegram_data.get("serial_number", "unknown"),
                                  telegram_data.get("type"))
                    
                    # Verify callback is still available
                    current_callback = self._telegram_callback
                    if not current_callback:
                        _LOGGER.error("❌ No callback registered!")
                        return
                    
                    _LOGGER.debug("📤 Scheduling coordinator callback...")
                    
                    # CRITICAL: Use asyncio.ensure_future for guaranteed execution
                    import asyncio
                    try:
                        # Ensure the coroutine is scheduled on the event loop
                        future = asyncio.ensure_future(current_callback(telegram_data))
                        _LOGGER.debug("✅ Future created and scheduled: %s", future)
                        
                        # Add done callback to log completion
                        def log_done(f):
                            if f.exception():
                                _LOGGER.error("❌ Callback failed: %s", f.exception())
                            else:
                                _LOGGER.debug("✅ Callback completed successfully")
                        future.add_done_callback(log_done)
                        
                    except Exception as e:
                        _LOGGER.error("❌ Failed to schedule callback: %s", e, exc_info=True)
                        
                except Exception as e:
                    _LOGGER.error("❌ Error in telegram callback wrapper: %s", e, exc_info=True)
            
            self._rx11_wrapper.set_telegram_callback(wrapper_callback)
            _LOGGER.info("🔗 Telegram callback weitergegeben an RX11 Wrapper")
        else:
            _LOGGER.error("❌ Cannot set telegram callback - RX11 wrapper not available or missing method")
        
        _LOGGER.debug("Telegram callback set for RX11 transceiver")

    def _parse_telegram_data(self, info_type: int, receiver_transmitter: bytes, info_data: bytes) -> Optional[Dict[str, Any]]:
        """Parse raw telegram data into structured format."""
        try:
            _LOGGER.debug("🔬 PARSING: receiver_transmitter length=%d, bytes=%s", 
                          len(receiver_transmitter), receiver_transmitter.hex().upper() if receiver_transmitter else "None")
            
            # Extrahiere Seriennummer aus receiver_transmitter (16 bytes)
            if len(receiver_transmitter) >= 16:
                serial_hex = receiver_transmitter[:16].hex().upper()
                serial_number = serial_hex
                _LOGGER.debug("🔬 PARSED SERIAL: %s (from %d bytes)", serial_number, len(receiver_transmitter))
            else:
                _LOGGER.warning("Invalid receiver_transmitter length: %d", len(receiver_transmitter))
                return None
            
            # Bestimme Gerätetyp basierend auf info_type
            if info_type == 1:
                device_type = "ew_transmitter"
                type_name = "transmitter"
            elif info_type == 0:  # Release events sind auch Easywave Transmitter
                device_type = "ew_transmitter" 
                type_name = "transmitter"
            elif info_type == 2:
                device_type = "ew_sensor" 
                type_name = "sensor"
            elif info_type == 3:
                # EWB-Telegramm (EWneo bidirectional device)
                device_type = "ewneo_receiver"
                type_name = "ewb_rcv"
            else:
                device_type = "unknown"
                type_name = "unknown"
            
            # Parse zusätzliche Daten aus info_data
            telegram_data = {
                "serial_number": serial_number,
                "serial": serial_number,  # Alias for compatibility
                "type": device_type,
                "device_type": type_name,
                "info_type": info_type,
                "raw_data": {
                    "receiver_transmitter": receiver_transmitter.hex(),
                    "info_data": info_data.hex() if info_data else ""
                },
                "timestamp": time.time()
            }
            
            _LOGGER.debug("🔍 About to check info_type: info_type=%d, type(info_type)=%s", info_type, type(info_type))
            
            # Handle info_type 0 as release for existing devices
            if info_type == 0:
                _LOGGER.debug("🔍 RELEASE detected: info_type=0, info_data_len=%d", len(info_data))
                # This is a release telegram - find the device type and handle accordingly
                # For Easywave Transmitter releases, we need to determine which button was released
                # The button info should be in the first byte of info_data (similar to press)
                
                # Check if this could be an Easywave Transmitter by pattern matching
                if len(info_data) == 8:
                    # Release telegrams always contain button=0 in the data, but we need the actual button
                    # Use the last pressed button ID from the previous push event
                    button_id = self._last_pressed_button.get(serial_number, 0)
                    button_names = {0: "A", 1: "B", 2: "C", 3: "D"}
                    button_name = button_names.get(button_id, "A")

                    current_status = self._battery_status.get(serial_number)
                    _LOGGER.debug("🔍 RELEASE processing: serial=%s, button=%d (%s), battery_status=%s, saw_battery_telegram=%s", 
                               serial_number, 
                               button_id,
                               button_name,
                               current_status,
                               self._saw_battery_telegram.get(serial_number, False))

                    # Process all releases the same way (status changes happen at PUSH)
                    # The second release after battery_low will be processed but has no effect
                    # (button already OFF from first release - Binary Sensor handles this)
                    
                    # Normal release event
                    telegram_data.update({
                        "device_type": "transmitter", 
                        "type": "ew_transmitter",
                        "button": button_id,
                        "button_name": button_name,
                        "function": "release",
                        "is_press": False,
                        "is_release": True,
                    })
                    
                    _LOGGER.debug("🎛️ Easywave Transmitter %s: Button %s (%d) release [status=%s]", 
                               serial_number, button_name, button_id, current_status or "normal")
            
            elif info_type == 1:

                # Für Easywave Transmitter: Parse Button-Info aus den Bytes info_data
                if len(info_data) >= 1:
                    # EWB_RCV Telegramm-Format für Easywave Transmitter:
                    # info_data ist 1 Byte lang (bei Press/Battery Low) oder 8 Bytes (bei Release)
                    # info_type bestimmt Press (1) oder Release (0) 
                    # Byte 0 in info_data: Button-ID (0=A, 1=B, 2=C, 3=D)
                    
                    try:
                        # Extract button from first byte of info_data
                        # Bits 1-0 contain button ID, bit 7 is battery low flag
                        raw_button_byte = info_data[0] if info_data else 0
                        
                        # Battery detection for Easywave Transmitter
                        # Sequence: Push (button) -> [Push (battery low 0x80)] -> Release
                        # Battery status is ONLY evaluated on Release
                        
                        _LOGGER.info("🔍 PRESS processing: serial=%s, raw_byte=0x%02X, is_0x80=%s",
                                   serial_number, raw_button_byte, bool(raw_button_byte & 0x80))
                        
                        if raw_button_byte & 0x80:
                            # Battery low telegram - 0x80 enthält KEINE gültige Button-ID!
                            # Verwende die Button-ID vom vorherigen normalen Press
                            self._battery_status[serial_number] = "low"
                            self._saw_battery_telegram[serial_number] = True
                            self._release_processed[serial_number] = False
                            
                            # WICHTIG: Hole die Button-ID vom vorherigen Press (nicht aus 0x80 extrahieren!)
                            button_id = self._last_pressed_button.get(serial_number, 0)
                            
                            # Button names mapping
                            button_names = {0: "A", 1: "B", 2: "C", 3: "D"}
                            button_name = button_names.get(button_id, "Unknown")
                            
                            _LOGGER.debug("🔋 Battery LOW telegram → status set to 'low' for %s, using previous button=%s (%d)", 
                                          serial_number, button_name, button_id)
                            
                            # Create a battery-only event with button info from previous press
                            telegram_data.update({
                                "device_type": "transmitter",
                                "type": "ew_transmitter", 
                                "button": button_id,  # Use button from previous press
                                "button_name": button_name,
                                "function": "battery_low",
                                "is_press": False,
                                "is_release": False,
                                "is_low_battery": True,
                                "battery_status": "low",
                            })
                            _LOGGER.debug("🔋 Battery LOW event: button %s", button_name)
                            
                        else:
                            # Normal button press - extract button ID from telegram
                            button_id = raw_button_byte & 0x03  # Mask to get bits 1-0 only
                            current_status = self._battery_status.get(serial_number)
                            battery_recovered = False  # Initialize first
                            
                            # Save the pressed button ID for the upcoming release telegram
                            self._last_pressed_button[serial_number] = button_id
                            
                            # Battery recovery state machine - transitions happen at PUSH
                            if current_status == "low":
                                # First normal press after battery low → transition to pending_recovery
                                self._battery_status[serial_number] = "pending_recovery"
                                _LOGGER.debug("🔄 First normal press after battery low for %s → pending_recovery", serial_number)
                                
                            elif current_status == "pending_recovery":
                                # Second normal press after battery low → transition to normal (battery replaced!)
                                self._battery_status[serial_number] = "normal"
                                _LOGGER.warning("✅ Battery REPLACED for %s (second normal press) → normal", serial_number)
                                
                                # Flag this push to also fire battery_reset event
                                battery_recovered = True
                            
                            # Mark start of new press cycle
                            self._saw_battery_telegram[serial_number] = False
                            self._release_processed[serial_number] = False  # Reset - allow release processing
                            
                            # Button names mapping
                            button_names = {0: "A", 1: "B", 2: "C", 3: "D"}
                            button_name = button_names.get(button_id, "Unknown")
                            
                            # Normal press telegram
                            telegram_data.update({
                                "button": button_id,
                                "button_name": button_name,
                                "function": "press",
                                "is_press": True,
                                "is_release": False,
                                "battery_recovered": battery_recovered,  # Flag for coordinator
                            })
                            
                            _LOGGER.debug("🎛️ Easywave Transmitter %s: Button %s (%d) press [status=%s]", 
                                    serial_number, button_name, button_id, self._battery_status.get(serial_number, "normal"))
                        
                    except Exception as e:
                        _LOGGER.warning("Error parsing Easywave Transmitter button data: %s", e, exc_info=True)
                        # Fallback to basic parsing
                        button_data = info_data[0] if info_data else 0
                        button_id = button_data & 0x03  # Extract button ID
                        button_names = {0: "A", 1: "B", 2: "C", 3: "D"}
                        button_name = button_names.get(button_id, "Unknown")
                        telegram_data["button"] = button_id
                        telegram_data["button_name"] = button_name
                        telegram_data["function"] = "press" if info_type == 1 else "release"
                """TODO: Add more detailed logging for out of boundary contents"""
                            
            # Für EWneo-Sensor: Prüfe ob Learn-Telegramm oder Messwert-Telegramm
            elif info_type == 2:
                _LOGGER.debug("🌡️ SENSOR TELEGRAM: Serial=%s, info_data_len=%d, type=%s, data=%s", 
                              serial_number, len(info_data), type(info_data).__name__, info_data.hex() if info_data else "None")
                
                # Default: Kein Lerntelegramm (wird später aus Flags gesetzt)
                telegram_data["is_learn_telegram"] = False
                # Default Sensoren (falls Parsing fehlschlägt)
                available_sensors = ["temperature", "humidity"]
                telegram_data["available_sensors"] = available_sensors
                telegram_data["measurement_types"] = available_sensors
                telegram_data["sensor_capabilities"] = available_sensors + ["battery"]
                
                # Add raw data bytes for device instance processing
                telegram_data["data"] = list(info_data) if info_data else []
                
                # Parse tatsächliche Sensor-Messwerte aus info_data
                if len(info_data) >= 7:
                    # EWneo-Sensor Telegramm-Format (offiziell dokumentiert):
                    # Byte 0: Version (bits 2-0, immer 0)
                    # Byte 1: 
                    #   - Bit 7: 1=Learn-Telegramm, 0=Messwert-Telegramm
                    #   - Bit 6: 1=Hat Batterie, 0=Keine Batterie
                    #   - Bits 5-3: Batterie-Level (0=schwach, 7=voll)
                    
                    _LOGGER.debug("🔬 Starting sensor data parsing for %s, data=%s", serial_number, info_data.hex())
                    try:
                        # Parse Byte 1 flags
                        flags = info_data[1] 
                        is_learn_telegram = bool(flags & 0x80)  # Bit 7
                        # WICHTIG: Setze is_learn_telegram im telegram_data Dictionary
                        telegram_data["is_learn_telegram"] = is_learn_telegram
                        has_battery = bool(flags & 0x40)        # Bit 6
                        battery_level_raw = (flags >> 3) & 0x07  # Bits 5-3
                        
                        _LOGGER.debug("🔋 Parsed flags: byte1=0x%02X, is_learn=%s, has_battery=%s, battery_raw=%d", 
                                      flags, is_learn_telegram, has_battery, battery_level_raw)
                        
                        # Convert battery level: 0=weak (0%), 7=full (100%)
                        if has_battery:
                            battery_level = int((battery_level_raw / 7.0) * 100) if battery_level_raw > 0 else 0
                            telegram_data["battery_level"] = battery_level
                            if battery_level_raw <= 1:  # 0 or 1 = weak
                                telegram_data["battery_status"] = "low"
                            elif battery_level_raw <= 4:  # medium range
                                telegram_data["battery_status"] = "medium" 
                            else:  # 5, 6, 7 = good to full
                                telegram_data["battery_status"] = "good"
                            
                            _LOGGER.debug("🔋 Battery parsed: raw=%d → level=%d%%, status=%s", 
                                          battery_level_raw, battery_level, telegram_data["battery_status"])
                        
                        if is_learn_telegram:
                            # Learn-Telegramm: Bytes 2-7 enthalten Fähigkeiten (6 bytes big-endian)
                            _LOGGER.info("📚 LERNTELEGRAMM empfangen von EWneo-Sensor %s (Byte1=0x%02X, Bit7=1)", 
                                       serial_number, flags)
                            
                            # Bytes 2-7: 48-bit capability field (big-endian)
                            if len(info_data) >= 8:
                                capabilities = int.from_bytes(info_data[2:8], byteorder='big')
                                _LOGGER.info("📚 Capabilities raw: 0x%012X (bytes 2-7: %s)", 
                                           capabilities, info_data[2:8].hex())
                                
                                has_humidity = bool(capabilities & (1 << 5))  # Bit 5
                                has_temperature = bool(capabilities & (1 << 4))  # Bit 4
                                
                                _LOGGER.info("📚 Capability bits: Bit5(humidity)=%s, Bit4(temperature)=%s", 
                                           has_humidity, has_temperature)
                                
                                available_sensors = []
                                if has_temperature:
                                    available_sensors.append("temperature")
                                if has_humidity:
                                    available_sensors.append("humidity")
                                
                                # Immer Battery hinzufügen wenn has_battery gesetzt ist
                                sensor_capabilities = available_sensors.copy()
                                if has_battery:
                                    sensor_capabilities.append("battery")
                                
                                telegram_data["available_sensors"] = available_sensors
                                telegram_data["measurement_types"] = available_sensors
                                telegram_data["sensor_capabilities"] = sensor_capabilities
                                
                                _LOGGER.info("📚 EWneo-Sensor %s Fähigkeiten: Temperature=%s, Humidity=%s, Battery=%s → Entities: %s", 
                                           serial_number, has_temperature, has_humidity, has_battery, sensor_capabilities)
                        else:
                            # Messwert-Telegramm: Byte 2 = Messtyp, Bytes 3-4 = Wert
                            _LOGGER.info("📊 MESSWERT-Telegramm empfangen von EWneo-Sensor %s (Byte1=0x%02X, Bit7=0)", 
                                       serial_number, flags)
                            if len(info_data) >= 5:
                                measurement_type = (info_data[2] >> 2) & 0x3F  # Bits 7-2
                                has_reference = bool(info_data[2] & 0x01)      # Bit 0
                                
                                # Messwert (16-bit big-endian)
                                measurement_raw = int.from_bytes(info_data[3:5], byteorder='big')
                                
                                if measurement_type == 4:  # Temperatur
                                    # Temperature formula from EASYWAVE specification:
                                    # T = a * n where a = 1/20 K
                                    # Range: n = 0 to 65535
                                    a = 1.0 / 20.0  # K per unit
                                    temperature_k = a * measurement_raw
                                    temperature_c = temperature_k - 273.15
                                    
                                    # Sanity check: if temperature is physically impossible, log warning
                                    if temperature_c < -100.0 or temperature_c > 100.0:
                                        _LOGGER.warning("⚠️ Temperature %s°C (raw=%d) seems out of reasonable range, sensor may need calibration", 
                                                      temperature_c, measurement_raw)
                                    
                                    telegram_data["temperature"] = round(temperature_c, 1)
                                    _LOGGER.debug("Temperature measurement: raw=%d → %.1f K → %.1f°C", 
                                                measurement_raw, temperature_k, temperature_c)
                                    
                                elif measurement_type == 5:  # Luftfeuchtigkeit  
                                    # Humidity formula: φ = b * n where b = 100/4095%
                                    # Range: n = 0 to 4095
                                    b = 100.0 / 4095.0  # % per unit
                                    humidity = b * measurement_raw
                                    telegram_data["humidity"] = round(humidity, 1)
                                    _LOGGER.debug("Humidity measurement: raw=%d → %.1f%%", 
                                                measurement_raw, humidity)
                                else:
                                    _LOGGER.debug("Unknown measurement type: %d", measurement_type)
                                
                                # Referenzwert falls vorhanden (Bytes 5-6)
                                if has_reference and len(info_data) >= 7:
                                    reference_raw = int.from_bytes(info_data[5:7], byteorder='big')
                                    _LOGGER.debug("Reference value present: %d", reference_raw)
                        
                        # Log final values with clear indication of telegram type
                        temp_str = f"{telegram_data.get('temperature', 'N/A'):.1f}°C" if 'temperature' in telegram_data else "N/A"
                        hum_str = f"{telegram_data.get('humidity', 'N/A'):.1f}%" if 'humidity' in telegram_data else "N/A"
                        batt_str = f"{telegram_data.get('battery_level', 'N/A')}%" if 'battery_level' in telegram_data else "N/A"
                        telegram_type = "📚 LERNTELEGRAMM" if is_learn_telegram else "📊 MESSWERT"
                        
                        _LOGGER.info("%s - EWneo-Sensor %s: Temp=%s, Hum=%s, Batt=%s (raw: %s)",
                                   telegram_type, serial_number, temp_str, hum_str, batt_str, info_data.hex())
                        
                    except Exception as e:
                        _LOGGER.warning("Error parsing EWneo-Sensor telegram data: %s", e, exc_info=True)
                else:
                    _LOGGER.warning("⚠️ EW-Sensor telegram too short for measurement data: %d bytes (need >= 7)", len(info_data))
            
            # Handle EWB telegrams (info_type == 3) for EWneo devices
            elif info_type == 3:
                _LOGGER.info("📡 EWB telegram received: Serial=%s, Data=%s", 
                           serial_number, info_data.hex() if info_data else "")
                
                # EWB telegrams contain state updates for EWneo devices
                # The info_data contains the state bytes that need to be parsed by the coordinator
                telegram_data["is_ewb_telegram"] = True
                telegram_data["state_bytes"] = list(info_data) if info_data else []
                
                _LOGGER.info("✅ EWB telegram parsed for coordinator processing")
            
            # Generiere Gerätename
            device_name = f"{device_type.replace('_', ' ').title()} {serial_number}"
            telegram_data["name"] = device_name
            
            _LOGGER.debug("Parsed telegram: %s", telegram_data)
            return telegram_data
            
        except Exception as e:
            _LOGGER.error("Error parsing telegram data: %s", e)
            return None
    
    @property
    def is_connected(self) -> bool:
        """Check if RX11 transceiver is connected."""
        if self._rx11_wrapper and self._rx11_wrapper.is_connected():
            return True
        return bool(self._serial_connection and self._serial_connection.is_open)

    def _handle_telegram(self, telegram_data: bytes) -> None:
        """Handle incoming telegram data."""
        _LOGGER.debug("📨 Simple telegram handler called with data: %s", telegram_data.hex() if telegram_data else "None")
        
        # Call main telegram callback (coordinator)
        if hasattr(self, '_telegram_callback') and self._telegram_callback:
            try:
                self._telegram_callback(telegram_data)
                _LOGGER.debug("✅ Main callback executed")
            except Exception as e:
                _LOGGER.error("Error in main telegram callback: %s", e)
        
        # Call additional callback if set (e.g., for learning)
        if hasattr(self, '_additional_callback') and self._additional_callback:
            try:
                self._additional_callback(telegram_data)
                _LOGGER.debug("✅ Additional callback executed")
            except Exception as e:
                _LOGGER.error("Error in additional telegram callback: %s", e)
        
        if not hasattr(self, '_telegram_callback') or not self._telegram_callback:
            _LOGGER.warning("❌ No main telegram callback set: %s", telegram_data.hex() if telegram_data else "None")

    def get_device(self, serial_number: str) -> Optional[Any]:
        """Get device instance by serial number."""
        return self._device_instances.get(serial_number)

    async def register_device(self, serial_number: str, device_info: dict) -> None:
        """Register a device with the RX11 transceiver."""
        _LOGGER.debug("Registering device %s with RX11 transceiver: %s", serial_number, device_info.get('name', 'Unknown'))
        # Store device info for future reference
        if not hasattr(self, '_registered_devices'):
            self._registered_devices = {}
        self._registered_devices[serial_number] = device_info
        _LOGGER.info("Device %s successfully registered with RX11 transceiver", serial_number)

    async def unregister_device(self, serial_number: str) -> bool:
        """Unregister a device from the RX11 transceiver."""
        try:
            _LOGGER.debug("Unregistering device %s from RX11 transceiver", serial_number)
            
            # Remove from registered devices if it exists
            if hasattr(self, '_registered_devices') and serial_number in self._registered_devices:
                del self._registered_devices[serial_number]
                _LOGGER.info("Device %s successfully unregistered from RX11 transceiver", serial_number)
                return True
            else:
                _LOGGER.warning("Device %s was not found in registered devices", serial_number)
                return False
                
        except Exception as e:
            _LOGGER.error("Error unregistering device %s: %s", serial_number, e)
            return False

    async def send_command_to_device(self, serial_number: str, command: bytes) -> bool:
        """Send command to a specific device."""
        try:
            if not self.is_connected:
                _LOGGER.debug("Cannot send command — transceiver not connected")
                return False
                
            # Ensure command is bytes
            if isinstance(command, str):
                # Convert hex string to bytes if needed
                if all(c in '0123456789ABCDEFabcdef' for c in command.replace(' ', '')):
                    command = bytes.fromhex(command.replace(' ', ''))
                else:
                    command = command.encode('utf-8')
                
            if self._rx11_wrapper:
                # Use RX11 wrapper for command sending with device-specific targeting
                return await self._rx11_wrapper.rx11_ew_receiver_send_command(serial_number, command)
            else:
                # Fallback: Implement proper EASYWAVE protocol commands
                _LOGGER.info("Using EASYWAVE protocol fallback for device %s", serial_number[-8:])
                return await self._send_easywave_protocol_command(serial_number, command)
                    
        except Exception as e:
            _LOGGER.error("Error sending command to device %s: %s", serial_number, e)
            return False

    async def _send_easywave_protocol_command(self, serial_number: str, command: bytes) -> bool:
        """Send EASYWAVE protocol command via serial fallback."""
        try:
            if not self._serial_connection or not self._serial_connection.is_open:
                _LOGGER.error("No serial connection available for EASYWAVE protocol")
                return False
            
            # Convert serial number to device ID for EASYWAVE protocol
            try:
                device_id = int(serial_number) if serial_number.isdigit() else int(serial_number, 16)
            except ValueError:
                _LOGGER.error("Invalid serial number format: %s", serial_number)
                return False
                
            # Create EASYWAVE protocol frame
            # Basic frame structure: [Start][Length][Command][DeviceID][Data][Checksum]
            frame = bytearray()
            frame.append(0xAA)  # Start byte
            frame.append(len(command) + 5)  # Frame length
            frame.append(0x01)  # Command type (Send)
            
            # Add device ID (4 bytes)
            frame.extend(device_id.to_bytes(4, byteorder='little'))
            
            # Add command data
            frame.extend(command)
            
            # Calculate checksum (XOR of all bytes except start)
            checksum = 0
            for byte in frame[1:]:
                checksum ^= byte
            frame.append(checksum)
            
            # Send frame
            _LOGGER.debug("Sending EASYWAVE protocol frame: %s", frame.hex())
            self._serial_connection.write(frame)
            await asyncio.sleep(0.2)  # Wait for transmission
            
            # Check for response (optional)
            if self._serial_connection.in_waiting > 0:
                response = self._serial_connection.read(self._serial_connection.in_waiting)
                _LOGGER.debug("Received response: %s", response.hex())
            
            _LOGGER.info("✅ EASYWAVE protocol command sent to device %s", serial_number)
            return True
            
        except Exception as e:
            _LOGGER.error("Error in EASYWAVE protocol communication: %s", e)
            return False

    async def _initialize_easywave_protocol(self) -> bool:
        """Initialize EASYWAVE protocol communication."""
        try:
            # Send Connect equivalent command
            connect_frame = bytearray([
                0xAA,  # Start byte
                0x06,  # Frame length
                0x10,  # Connect command
                0x00, 0x00, 0x00, 0x00,  # Reserved
                0x16   # Checksum (XOR of bytes 1-6)
            ])
            
            _LOGGER.debug("Sending EASYWAVE Connect command: %s", connect_frame.hex())
            self._serial_connection.write(connect_frame)
            await asyncio.sleep(0.2)  # Reduced wait for faster response
            
            # Check for response
            if self._serial_connection.in_waiting > 0:
                response = self._serial_connection.read(self._serial_connection.in_waiting)
                _LOGGER.debug("Connect response: %s", response.hex())
                # Basic validation - should start with 0xAA
                if response and response[0] == 0xAA:
                    _LOGGER.info("✅ EASYWAVE protocol Connect successful")
                    return True
            
            _LOGGER.warning("⚠️ EASYWAVE protocol Connect - no response received")
            return True  # Continue anyway, device might not respond to init
            
        except Exception as e:
            _LOGGER.error("Error in EASYWAVE protocol initialization: %s", e)
            return False

    # EW Receiver Management Methods (Delegate to wrapper)
    async def get_next_available_receiver(self) -> Optional[tuple[int, str]]:
        """Get the next available EW receiver (index, serial) that is not yet used."""
        if self._rx11_wrapper:
            return await self._rx11_wrapper.get_next_available_receiver()
        _LOGGER.warning("RX11 wrapper not available for receiver management")
        return None
        
    def mark_receiver_used(self, index: int, serial: str) -> None:
        """Mark a receiver as used/allocated."""
        if self._rx11_wrapper:
            self._rx11_wrapper.mark_receiver_used(index, serial)
        else:
            _LOGGER.warning("RX11 wrapper not available for receiver management")
            
    def mark_receiver_available(self, index: int) -> None:
        """Mark a receiver as available again (e.g., when device is removed)."""
        if self._rx11_wrapper:
            self._rx11_wrapper.mark_receiver_available(index)
        else:
            _LOGGER.warning("RX11 wrapper not available for receiver management")
            
    def get_used_receivers(self) -> dict[int, str]:
        """Get all currently used receivers."""
        if self._rx11_wrapper:
            return self._rx11_wrapper.get_used_receivers()
        return {}
        
    def get_available_receivers_count(self) -> int:
        """Get count of available (not used) receivers."""
        if self._rx11_wrapper:
            return self._rx11_wrapper.get_available_receivers_count()
        return 0

    # =============================================================================
    # RX11 EW RECEIVER OPERATIONS
    # =============================================================================

    async def rx11_ew_receiver_scan_all(self, max_index: int = 50, force_refresh: bool = False) -> dict[int, str]:
        """Scan for EW receivers and return mapping."""
        if self._rx11_wrapper:
            return await self._rx11_wrapper.rx11_ew_receiver_scan_all(max_index)
        return {}
        
    def rx11_ew_receiver_reset_used_list(self) -> None:
        """Reset the used receivers list (for debugging/recovery)."""
        if self._rx11_wrapper:
            self._rx11_wrapper.reset_used_receivers()
        else:
            _LOGGER.warning("RX11 wrapper not available for receiver management")
            
    async def rx11_ew_receiver_unregister_device(self, serial_number: str) -> None:
        """Unregister a device and mark its receiver as available again."""
        try:
            if self._rx11_wrapper:
                # Check if this serial is in our used receivers and free it
                used_receivers = self._rx11_wrapper.get_used_receivers()
                for index, used_serial in used_receivers.items():
                    if used_serial == serial_number:
                        self._rx11_wrapper.mark_receiver_available(index)
                        _LOGGER.info("♻️ Freed receiver index %d for serial %s", index, serial_number[-8:])
                        
                        # Also remove from legacy tracking
                        if serial_number in self._rx11_indices:
                            del self._rx11_indices[serial_number]
                            
                        break
                else:
                    _LOGGER.debug("Serial %s not found in used receivers list", serial_number[-8:])
                    
                    # Check legacy tracking as fallback
                    if serial_number in self._rx11_indices:
                        index = self._rx11_indices[serial_number]
                        self._rx11_wrapper.mark_receiver_available(index)
                        del self._rx11_indices[serial_number]
                        _LOGGER.info("♻️ Freed receiver index %d (from legacy) for serial %s", index, serial_number[-8:])
            else:
                _LOGGER.warning("RX11 wrapper not available for device unregistration")
        except Exception as e:
            _LOGGER.warning("Error unregistering device %s: %s", serial_number[-8:], e)

    # =============================================================================
    # RX11 EWB (EASYWAVE BIDI) OPERATIONS
    # =============================================================================

    async def rx11_ewb_get_next_available_index(self) -> Optional[int]:
        """Get the next available EWB index."""
        if self._rx11_wrapper:
            return await self._rx11_wrapper.rx11_ewb_get_next_available_index()
        _LOGGER.warning("RX11 wrapper not available for EWB management")
        return None

    async def rx11_ewb_get_next_available_index_single(self) -> Optional[int]:
        """Get the next available EWB index using optimized single GetFdSerial call."""
        if self._rx11_wrapper:
            return await self._rx11_wrapper.rx11_ewb_get_next_available_index_single()
        _LOGGER.warning("RX11 wrapper not available for EWB management")
        return None

    async def rx11_ewb_get_serial_by_index(self, index: int) -> Optional[str]:
        """Get EWB gateway serial number by index."""
        if self._rx11_wrapper:
            return await self._rx11_wrapper.rx11_ewb_get_serial_by_index(index)
        _LOGGER.warning("RX11 wrapper not available for EWB management")
        return None
    
    async def rx11_ew_receiver_get_serial_by_index(self, index: int) -> Optional[str]:
        """Get EW receiver serial number by index."""
        if self._rx11_wrapper:
            return await self._rx11_wrapper.rx11_ew_receiver_get_serial_by_index(index)
        _LOGGER.warning("RX11 wrapper not available for EW receiver management")
        return None

    async def rx11_ewb_add_filter(self, gateway_serial: str) -> bool:
        """Add EWB serial to receive filter."""
        if self._rx11_wrapper:
            return await self._rx11_wrapper.rx11_ewb_add_filter(gateway_serial)
        _LOGGER.warning("RX11 wrapper not available for EWB management")
        return False

    async def rx11_ewb_clear_filter(self) -> bool:
        """Clear EWB receive filter."""
        if self._rx11_wrapper:
            return await self._rx11_wrapper.rx11_ewb_clear_filter()
        _LOGGER.warning("RX11 wrapper not available for EWB management")
        return False

    async def rx11_ewb_join_device(self, gateway_serial: str, timeout: float = 2.0) -> Optional[tuple[int, str]]:
        """Join EWB device - single attempt.
        
        Args:
            gateway_serial: Gateway serial number (hex string)
            timeout: Timeout in seconds for this single attempt (default: 2.0s)
        
        Returns:
            Tuple of (device_type, receiver_serial) on success, None on timeout/failure
        """
        if self._rx11_wrapper:
            return await self._rx11_wrapper.rx11_ewb_join_device(gateway_serial, timeout)
        _LOGGER.warning("RX11 wrapper not available for EWB management")
        return None

    async def rx11_ewb_remove_device(self, gateway_serial: str, receiver_serial: str) -> bool:
        """Remove EWB device using gateway and receiver serials."""
        if self._rx11_wrapper:
            return await self._rx11_wrapper.rx11_ewb_remove_device(gateway_serial, receiver_serial)
        _LOGGER.warning("RX11 wrapper not available for EWB management")
        return False

    async def rx11_ewb_query_state(self, gateway_serial: str, receiver_serial: str, mode: int = 0) -> Optional[tuple[int, list]]:
        """Query EWB device state and return (mode, state_bytes)."""
        if self._rx11_wrapper:
            return await self._rx11_wrapper.rx11_ewb_query_state(gateway_serial, receiver_serial, mode)
        _LOGGER.warning("RX11 wrapper not available for EWB management")
        return None

    async def rx11_ewb_change_state(self, gateway_serial: str, receiver_serial: str, 
                                   desired_mode: int, desired_state: list) -> Optional[tuple[int, list]]:
        """Change EWB device state and return (recent_mode, recent_state)."""
        if self._rx11_wrapper:
            return await self._rx11_wrapper.rx11_ewb_change_state(gateway_serial, receiver_serial, desired_mode, desired_state)
        _LOGGER.warning("RX11 wrapper not available for EWB management")
        return None

    async def rx11_ewb_receive_telegram(self) -> Optional[dict]:
        """Receive EWB telegram for state updates."""
        if self._rx11_wrapper:
            return await self._rx11_wrapper.rx11_ewb_receive_telegram()
        _LOGGER.warning("RX11 wrapper not available for EWB management")
        return None

    # ==================== RX11 Device Helper Functions ====================
    # These functions handle RX11-specific device creation, cleanup and restoration
    # Moved from coordinator.py to maintain proper architectural boundaries
    
    async def create_rx11_ew_device_info(
        self,
        serial_number: str,
        device_type: str,
        device_name: str = None,
        channels: int = 1,
        detected_via: str = "manual",
        info_type: int = None,
        timestamp: float = None,
        device_registry = None,
        ew_receiver_allocator: Callable = None
    ) -> Dict[str, Any] | None:
        """Create simplified device info for RX11-based devices with persistent serial storage.
        
        Args:
            serial_number: Device serial number
            device_type: Type of device (e.g., 'EW_Receiver', 'ew_sensor')
            device_name: Optional device name
            channels: Number of channels (default 1)
            detected_via: Detection method (default 'manual')
            info_type: Optional info type
            timestamp: Optional timestamp
            device_registry: DeviceRegistry instance for persistent storage
            ew_receiver_allocator: Callback to allocate Easywave Receiver (returns Dict with receiver data)
            
        Returns:
            Device info dictionary or None on error
        """
        try:
            from datetime import datetime
            
            # Base device info
            device_info = {
                "serial_number": serial_number,
                "type": device_type,
                "name": device_name or f"EASYWAVE {device_type} {serial_number}",
                "channels": channels,
                "detected_via": detected_via,
                "added_manually": True,
                "timestamp": timestamp or time.time(),
                "last_seen": datetime.now().isoformat(),
                "persistent_storage": True,  # Mark for enhanced persistence
            }
            
            # Special handling for Easywave Receiver devices with persistent serial storage
            if device_type == "EW_Receiver" or device_type.lower() == "ew_receiver":
                if ew_receiver_allocator:
                    ew_receiver_data = await ew_receiver_allocator(serial_number)
                    if ew_receiver_data:
                        device_info.update(ew_receiver_data)
                        _LOGGER.info("📝 Easywave Receiver serial %s permanently stored for device %s", 
                                   ew_receiver_data.get("ew_receiver_serial", "unknown")[-8:], 
                                   serial_number[-8:])
                    else:
                        _LOGGER.error("Failed to allocate Easywave Receiver for device %s", serial_number)
                        return None
                else:
                    _LOGGER.warning("No Easywave Receiver allocator provided for device %s", serial_number)
            
            # For sensor devices, ensure they have sensor capabilities
            elif device_type in ["ew_sensor", "ew_transceiver"]:
                device_info.update({
                    "available_sensors": ["temperature", "humidity"],
                    "measurement_types": ["temperature", "humidity"],
                    "sensor_types": ["temperature", "humidity"],
                    "supports_sensors": True,
                })
            
            if info_type is not None:
                device_info["info_type"] = info_type
                
            return device_info
            
        except Exception as e:
            _LOGGER.error("Error creating RX11 device info: %s", e)
            return None

    async def cleanup_rx11_device(
        self,
        serial_number: str,
        device_info: Dict[str, Any],
        device_registry = None,
        index_free_callback: Callable = None
    ) -> None:
        """Clean up RX11-based device resources including Easywave Receiver mappings.
        
        Args:
            serial_number: Device serial number
            device_info: Device information dictionary
            device_registry: DeviceRegistry instance
            index_free_callback: Callback to free Easywave Receiver index (func(index))
        """
        try:
            device_type = device_info.get("type", "unknown")
            
            # Clean up Easywave Receiver mapping if this was an Easywave Receiver device
            ew_receiver_index = device_info.get("ew_receiver_index") or device_info.get("rx11_index")
            ew_receiver_serial = device_info.get("ew_receiver_serial")
            
            if ew_receiver_index is not None:
                # Remove Easywave Receiver mapping from device registry
                if device_registry and ew_receiver_serial:
                    if device_registry.remove_ew_receiver_mapping(ew_receiver_index):
                        _LOGGER.info("🗑️ Removed Easywave Receiver mapping: Index %d, Serial %s", 
                                   ew_receiver_index, ew_receiver_serial[-8:])
                
                # Free up receiver index persistently
                if index_free_callback:
                    index_free_callback(ew_receiver_index)
                    _LOGGER.info("♻️ Freed Easywave Receiver index %d for reuse", ew_receiver_index)
            
            # Save device manager changes
            if device_registry and hasattr(device_registry, 'save'):
                await device_registry.save()
            
            _LOGGER.info("🧹 RX11 device cleanup completed for %s (%s)", serial_number[-8:], device_type)
            
        except Exception as e:
            _LOGGER.error("Error cleaning up RX11 device %s: %s", serial_number, e)

    async def restore_rx11_device(
        self,
        serial_number: str,
        device_info: Dict[str, Any],
        device_registry = None,
        index_used_callback: Callable = None,
        whitelist_callback: Callable = None
    ) -> None:
        """Restore RX11-based device with persistent Easywave Receiver serial handling.
        
        Args:
            serial_number: Device serial number
            device_info: Device information dictionary
            device_registry: DeviceRegistry instance
            index_used_callback: Callback to mark index as used (func(index, serial, device_serial, name))
            whitelist_callback: Callback to add to whitelist (func(serial, index, type, source))
        """
        try:
            from datetime import datetime
            
            device_type = device_info.get("type", device_info.get("device_type", "unknown"))
            
            # Restore Easywave Receiver serial mapping if present (unidirectional EW devices)
            ew_receiver_serial = device_info.get("ew_receiver_serial")
            ew_receiver_index = device_info.get("ew_receiver_index") or device_info.get("rx11_index")
            
            if ew_receiver_serial and ew_receiver_index is not None:
                # Restore Easywave Receiver mapping in device registry
                if device_registry:
                    device_registry.store_ew_receiver_mapping(
                        ew_receiver_index, ew_receiver_serial, serial_number
                    )
                
                # Mark receiver as used persistently
                if index_used_callback:
                    device_name = device_info.get("name", f"Easywave Receiver ({serial_number})")
                    index_used_callback(ew_receiver_index, ew_receiver_serial, serial_number, device_name)
                
                # Add to whitelist (memory-only during setup)
                if whitelist_callback:
                    whitelist_callback(
                        serial_number=serial_number,
                        rx11_index=ew_receiver_index,
                        device_type=device_type,
                        source="GetFdSerial"
                    )
                
                _LOGGER.info("🔄 Restored Easywave Receiver mapping: Index %d, Serial %s → Device %s", 
                           ew_receiver_index, ew_receiver_serial[-8:], serial_number[-8:])
            
            # Ensure RX11-based devices have proper sensor configuration
            if device_type in ["ew_sensor", "ew_transceiver"] and not device_info.get("available_sensors"):
                device_info.update({
                    "available_sensors": ["temperature", "humidity"],
                    "measurement_types": ["temperature", "humidity"],
                    "sensor_types": ["temperature", "humidity"],
                    "supports_sensors": True,
                    "rx11_based": True,
                })
                _LOGGER.info("🎯 Enhanced RX11 sensor configuration for device %s", serial_number[-8:])
                
        except Exception as e:
            _LOGGER.error("Error restoring RX11 device %s: %s", serial_number, e)