"""Python wrapper for ELDAT RX11."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional

from .rx_module import (
    RxModule,
    ErrorCode,
    InfoType,
    DeviceType,
    TM_BUTTON_A,
    TM_BUTTON_B,
    TM_BUTTON_C,
    TM_BUTTON_D,
)

_LOGGER = logging.getLogger(__name__)


class RX11Wrapper:
    """Python wrapper for ELDAT RX11 RxModule.
    
    Provides high-level async interface to the RX11 transceiver.
    
    Structured Naming Convention:
    - rx11_<device_type>_<operation>: RX11 specific operations
      - rx11_ew_receiver_*: Operations for EW receivers
      - rx11_ew_transmitter_*: Operations for EW transmitters  
      - rx11_ewb_sensor_*: Operations for EWB sensors
      - rx11_ew_receiver_button_*: Button-specific receiver operations
    
    Device Types:
    - EW Receiver (ew_receiver): Devices that receive commands
    - EW Transmitter (ew_transmitter): Devices that send commands
    - EWB Sensor (ewb_sensor): Bidirectional sensor devices
    
    Button Types: A(0), B(1), C(2), D(3)
    """
    
    def __init__(self, device_path: str):
        """Initialize RX11 wrapper."""
        self.device_path = device_path
        self._module = RxModule(port=device_path, baudrate=115200, debug=True)
        self._connected = False
        self._lock = asyncio.Lock()
        
        # USB device information
        self._usb_serial_number = None  # Will be set from config or detected
        
        # Device tracking
        self._used_receivers: dict[int, str] = {}  # {index: serial} for used receivers
        self._used_ewb_indices: dict[int, str] = {}  # {index: gateway_serial} for EWB
        self._ewb_device_serials: dict[str, str] = {}  # {gateway_serial: device_serial}
        
        # Serial cache
        self._serial_cache: dict[int, str] = {}
        self._cache_timestamp: dict[int, float] = {}
        self._cache_max_age = 300.0  # 5 minutes
        
        # Version caching
        self._hw_version_cache: Optional[str] = None
        self._fw_version_cache: Optional[str] = None
        self._versions_fetched = False
        
        # Continuous receive loop for EWB sensors
        self._ewb_receive_task: Optional[asyncio.Task] = None
        self._stop_ewb_receive = False
        self._telegram_callback: Optional[Callable] = None
        
        # Error tracking
        self._serial_error_count = 0
        self._consecutive_errors = 0
        
        # Health check and connection monitoring
        self._health_check_task: Optional[asyncio.Task] = None
        self._stop_health_check = False
        self._health_check_interval: float = 30.0  # Check connection 30 seconds after last communication
        self._last_successful_communication: float = 0.0  # Timestamp of last successful communication
        self._connection_lost_threshold: int = 3  # Mark disconnected after 3 consecutive failures
        self._reconnect_in_progress: bool = False
        self._stop_reconnect: bool = False  # Flag to stop reconnection loop on shutdown
        self._reconnect_lock = asyncio.Lock()
        
        # Coordinator reference for status updates
        self._coordinator = None
        
        _LOGGER.info("✅ RX11 Pure Python wrapper initialized for %s", device_path)
    
    def set_device_path(self, device_path: str) -> None:
        """Update the device path for reconnection after USB port change.
        
        This recreates the RxModule with the new port path.
        """
        if device_path == self.device_path:
            return
        
        _LOGGER.info("🔄 Updating RX11 device path: %s → %s", self.device_path, device_path)
        
        # Disconnect existing module if connected
        if self._connected:
            try:
                self._module.dispose()
            except Exception as e:
                _LOGGER.debug("Error disposing old module: %s", e)
        
        self.device_path = device_path
        self._connected = False
        
        # Create new module with new path
        self._module = RxModule(port=device_path, baudrate=115200, debug=True)
        
        # Reset version cache to force re-fetch
        self._hw_version_cache = None
        self._fw_version_cache = None
        self._versions_fetched = False
    
    # ================================================================================================
    # CONNECTION MANAGEMENT
    # ================================================================================================
    
    async def connect(self) -> bool:
        """Connect to RX11 device."""
        async with self._lock:
            if self._connected:
                _LOGGER.info("✅ Already connected to RX11 at %s", self.device_path)
                return True
            
            _LOGGER.info("🔌 Connecting to RX11 at %s (Pure Python)...", self.device_path)
            
            try:
                # Connect using pure Python implementation
                success = self._module.connect()
                
                if success:
                    self._connected = True
                    _LOGGER.info("✅ Connected to RX11 at %s", self.device_path)
                    
                    # Register disconnect callback for immediate notification on hardware errors
                    self._module.set_disconnect_callback(self._on_hardware_disconnect)
                    # Register reconnect callback to restart receive loops after reconnect
                    self._module.set_reconnect_callback(self._on_hardware_reconnect)
                    
                    # Do second buffer clear after a short async delay (non-blocking)
                    # This removes any remaining garbage that arrived during the initial connect
                    await asyncio.sleep(0.05)
                    if self._module._serial:
                        self._module._serial.reset_input_buffer()
                    
                    # Wait for serial interface to be fully ready (avoid race conditions)
                    await asyncio.sleep(0.8)  # Increased settle time to prevent startup errors
                    
                    if not self._versions_fetched:
                        # Retry hardware version query with exponential backoff
                        hw_version = None
                        for attempt in range(3):
                            hw_version = await self.get_hardware_version()
                            if hw_version:
                                _LOGGER.info("✅ Connection verified - Hardware: %s", hw_version)
                                self._versions_fetched = True
                                break
                            else:
                                if attempt < 2:
                                    wait_time = 0.5 * (attempt + 1)
                                    _LOGGER.debug("Hardware version query failed (attempt %d/3), retrying in %.1fs...", attempt + 1, wait_time)
                                    await asyncio.sleep(wait_time)
                                else:
                                    _LOGGER.warning("⚠️ Hardware version query failed after 3 attempts")
                    
                    # Start continuous EWB receive loop for background telegram monitoring
                    _LOGGER.info("🚀 Starting continuous EWB receive loop...")
                    await self.rx11_ewb_sensor_start_receive_loop()
                    
                    # Start health check task for connection monitoring
                    _LOGGER.info("🏥 Starting health check task...")
                    await self._start_health_check()
                    
                    # Initialize all EWneo devices by querying their state
                    # This is done after health check starts to ensure connection is stable
                    _LOGGER.info("🔄 Initializing EWneo device states...")
                    await self._refresh_all_ewneo_device_states()
                    
                    self._serial_error_count = 0
                    self._consecutive_errors = 0
                    
                    return True
                else:
                    _LOGGER.debug("RX11 connect() returned False")
                    return False
                    
            except Exception as e:
                _LOGGER.debug("Exception connecting to RX11: %s", e)
                return False
    
    async def disconnect(self):
        """Disconnect from RX11 device."""
        async with self._lock:
            if not self._connected and not self._reconnect_in_progress:
                return
            
            # Stop reconnection attempts if in progress
            self._stop_reconnect = True
            
            # Stop health check task
            await self._stop_health_check_task()
            
            # Stop EWB receive loop
            await self.rx11_ewb_sensor_stop_receive_loop()
            
            try:
                # Run dispose in executor to avoid blocking the event loop
                await asyncio.get_event_loop().run_in_executor(
                    None, self._module.dispose
                )
                self._connected = False
                _LOGGER.info("✅ Disconnected from RX11 at %s", self.device_path)
            except Exception as e:
                _LOGGER.error("❌ Exception during RX11 disconnect: %s", e)
                self._connected = False
    
    def _on_hardware_disconnect(self) -> None:
        """Called by RxModule when a hardware error (USB disconnect) occurs.
        
        This is called from the serial handler thread, so we need to be
        thread-safe and schedule any async operations properly.
        
        After stopping the loops, this method initiates reconnection attempts
        which will keep trying until the device is found again.
        """
        _LOGGER.info("🔴 RX11 hardware disconnect detected - initiating reconnection...")
        
        # Mark as disconnected immediately
        self._connected = False
        self._consecutive_errors += 1
        
        # Stop receive loops and health check on the event loop, then start reconnection
        if self._coordinator and hasattr(self._coordinator, 'hass'):
            try:
                # Schedule async operations to stop loops and start reconnection
                async def stop_loops_and_reconnect():
                    try:
                        # Stop EWB receive loop
                        _LOGGER.info("⏹️ Stopping EWB receive loop due to disconnect...")
                        await self.rx11_ewb_sensor_stop_receive_loop()
                        
                        # Stop health check (will be restarted after reconnection)
                        _LOGGER.info("⏹️ Stopping health check due to disconnect...")
                        await self._stop_health_check_task()
                        
                        # Notify coordinator about disconnect
                        self._coordinator.async_update_listeners()
                        _LOGGER.info("✅ Loops gestoppt - starte Reconnect-Versuch...")
                        
                        # Start reconnection attempts - this will keep trying until device is found
                        await self._handle_connection_lost()
                        
                        # If reconnection succeeded, restart the loops
                        if self._connected:
                            _LOGGER.info("🚀 Reconnect erfolgreich - starte Loops neu...")
                            
                            # Re-add all EWB filters after reconnect
                            gateway_serials = await self._get_all_gateway_serials()
                            if gateway_serials:
                                await self.rx11_ewb_sensor_clear_filter()
                                for gateway_serial in gateway_serials:
                                    await self.rx11_ewb_sensor_add_filter(gateway_serial)
                                _LOGGER.info("✅ %d EWB-Filter neu gesetzt", len(gateway_serials))
                            
                            # Restart EWB receive loop
                            await self.rx11_ewb_sensor_start_receive_loop()
                            
                            # Restart health check
                            await self._start_health_check()
                            
                            # Notify coordinator about reconnection
                            self._coordinator.async_update_listeners()
                            _LOGGER.info("✅ RX11 vollständig wiederverbunden!")
                        
                    except Exception as e:
                        _LOGGER.error("❌ Fehler beim Disconnect-Handler: %s", e)
                
                # Schedule on event loop
                asyncio.run_coroutine_threadsafe(
                    stop_loops_and_reconnect(),
                    self._coordinator.hass.loop
                )
            except Exception as e:
                _LOGGER.warning("⚠️ Fehler beim Schedulen des Disconnect-Handlers: %s", e)
        else:
            # No coordinator available - log warning, reconnect will be handled by coordinator polling
            _LOGGER.warning("⚠️ Kein Coordinator verfügbar - Reconnect wird vom Coordinator übernommen")
    
    def _on_hardware_reconnect(self) -> None:
        """Called by RxModule after a successful reconnect.
        
        This is called from the serial handler thread, so we need to be
        thread-safe and schedule any async operations properly.
        """
        _LOGGER.info("🔄 RX11 Hardware-Reconnect erfolgreich - starte RCV-Loop neu")
        
        # Mark as connected
        self._connected = True
        self._serial_error_count = 0
        self._consecutive_errors = 0
        
        # Restart receive loops on the event loop
        if self._coordinator and hasattr(self._coordinator, 'hass'):
            try:
                # Schedule async operations on the event loop
                async def restart_loops():
                    try:
                        # Re-add all EWB filters after reconnect
                        # EWneo devices require their gateway serials to be registered in the RX11 filter
                        _LOGGER.info("🔧 Setze EWB-Filter nach Reconnect neu...")
                        gateway_serials = await self._get_all_gateway_serials()
                        if gateway_serials:
                            # Clear existing filters first
                            await self.rx11_ewb_sensor_clear_filter()
                            _LOGGER.info("🧹 EWB-Filter geleert")
                            
                            # Add all gateway serials
                            for gateway_serial in gateway_serials:
                                success = await self.rx11_ewb_sensor_add_filter(gateway_serial)
                                if success:
                                    _LOGGER.info("✅ EWB-Filter gesetzt für Gateway %s", gateway_serial[-8:])
                                else:
                                    _LOGGER.error("❌ Fehler beim Setzen des EWB-Filters für Gateway %s", gateway_serial[-8:])
                            _LOGGER.info("✅ %d EWB-Filter erfolgreich neu gesetzt", len(gateway_serials))
                        else:
                            _LOGGER.debug("Keine Gateway-Seriennummern für EWB-Filter gefunden")
                        
                        # Start fresh EWB receive loop (already stopped by disconnect handler)
                        _LOGGER.info("🚀 Starting fresh EWB receive loop after reconnect...")
                        await self.rx11_ewb_sensor_start_receive_loop()
                        
                        # Start fresh health check (already stopped by disconnect handler)
                        _LOGGER.info("🏥 Starting fresh health check after reconnect...")
                        await self._start_health_check()
                        
                        # Query state of all EWneo devices to refresh their current state
                        await self._refresh_all_ewneo_device_states()
                        
                        # Notify coordinator
                        self._coordinator.async_update_listeners()
                        _LOGGER.info("✅ RCV-Loop erfolgreich neugestartet nach Reconnect")
                    except Exception as e:
                        _LOGGER.error("❌ Fehler beim Neustarten der RCV-Loop: %s", e)
                
                # Schedule on event loop
                asyncio.run_coroutine_threadsafe(
                    restart_loops(),
                    self._coordinator.hass.loop
                )
            except Exception as e:
                _LOGGER.error("❌ Fehler beim Schedulen des Reconnect-Handlers: %s", e)
    
    def get_connection_stats(self) -> dict:
        """Get connection and error statistics."""
        return {
            'connected': self._connected,
            'serial_error_count': self._serial_error_count,
            'consecutive_errors': self._consecutive_errors,
            'device_path': self.device_path,
            'last_successful_communication': self._last_successful_communication,
            'health_check_interval': self._health_check_interval,
            'reconnect_in_progress': self._reconnect_in_progress,
            'ewb_receive_loop_running': (
                self._ewb_receive_task is not None and 
                not self._ewb_receive_task.done()
            ) if self._ewb_receive_task else False,
            'health_check_running': (
                self._health_check_task is not None and 
                not self._health_check_task.done()
            ) if self._health_check_task else False
        }
    
    # ================================================================================================
    # HARDWARE INFORMATION
    # ================================================================================================
    
    async def get_hardware_version(self) -> Optional[str]:
        """Get hardware version."""
        if self._hw_version_cache:
            return self._hw_version_cache
        
        try:
            result, hw_str = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ma_query_hw_ver_request
            )
            
            if result == ErrorCode.SUCCESS:
                # Convert bytes to string
                null_index = hw_str.find(0)
                if null_index >= 0:
                    hw_str = hw_str[:null_index]
                version = hw_str.decode('ascii', errors='ignore').strip()
                self._hw_version_cache = version
                self._last_successful_communication = time.time()
                return version
            else:
                error_name = self._get_error_name(result)
                if result == 0xFF:  # ERR_FAILSTATE
                    _LOGGER.debug("Hardware version query failed: %s (0x%02x) - Device in fail state", error_name, result)
                    # Mark for reconnection
                    self._consecutive_errors += 1
                else:
                    _LOGGER.debug("Hardware version query failed: %s (0x%02x)", error_name, result)
                return None
        except (OSError, IOError) as e:
            _LOGGER.error("Hardware version I/O error (USB getrennt?): %s", e)
            self._consecutive_errors += 1
            return None
        except Exception as e:
            _LOGGER.error("Exception getting hardware version: %s", e)
            return None
    
    async def get_firmware_version(self) -> Optional[str]:
        """Get firmware version."""
        if self._fw_version_cache:
            return self._fw_version_cache
        
        try:
            result, major, minor, incomplete = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ma_query_fw_ver_request
            )
            
            if result == ErrorCode.SUCCESS:
                version = f"{major}.{minor}"
                if incomplete:
                    version += " (incomplete)"
                self._fw_version_cache = version
                self._last_successful_communication = time.time()
                return version
            else:
                error_name = self._get_error_name(result)
                if result == 0xFF:  # ERR_FAILSTATE
                    _LOGGER.warning("Firmware version query failed: %s (0x%02x) - Gerät im Fehlerzustand", error_name, result)
                else:
                    _LOGGER.warning("Firmware version query failed: %s (0x%02x)", error_name, result)
                return None
        except (OSError, IOError) as e:
            _LOGGER.error("Firmware version I/O error (USB getrennt?): %s", e)
            return None
        except Exception as e:
            _LOGGER.error("Exception getting firmware version: %s", e)
            return None
    
    # Alias methods for coordinator compatibility
    async def get_hw_version(self) -> Optional[str]:
        """Alias for get_hardware_version() for coordinator compatibility."""
        return await self.get_hardware_version()
    
    async def get_fw_version(self) -> Optional[str]:
        """Alias for get_firmware_version() for coordinator compatibility."""
        return await self.get_firmware_version()
    
    def set_usb_serial_number(self, serial_number: str) -> None:
        """Set USB serial number for identification."""
        self._usb_serial_number = serial_number
        _LOGGER.debug("📋 USB Serial Number: %s", serial_number)
    
    def get_usb_serial_number(self) -> Optional[str]:
        """Get USB serial number for identification."""
        return self._usb_serial_number
    
    def get_device_info(self) -> dict:
        """Get complete device information including USB details.
        
        Returns dictionary with device identification and version information.
        """
        return {
            "device_path": self.device_path,
            "usb_serial_number": self._usb_serial_number,
            "hw_version": self._hw_version_cache,
            "fw_version": self._fw_version_cache,
            "connected": self._connected,
        }
    
    # ================================================================================================
    # EW RECEIVER OPERATIONS
    # ================================================================================================
    
    async def rx11_ew_receiver_get_serial_by_index(self, index: int) -> Optional[str]:
        """Get EW receiver serial by index.
        
        Returns the gateway serial for the given index from the RX11.
        The RX11 has pre-configured gateway serials for each index (0-127).
        """
        # Check cache first
        cached = self._get_cached_serial(index)
        if cached:
            return cached
        
        try:
            result, serial = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ew_get_fd_serial_request, index
            )
            
            if result == ErrorCode.SUCCESS:
                serial_hex = ''.join(f'{b:02X}' for b in serial)
                # Cache and return even if all zeros - the RX11 uses the index as address
                # A null serial means the slot is available but has a valid address
                self._cache_serial(index, serial_hex)
                _LOGGER.debug("📋 EW serial at index %d: %s", index, serial_hex[-8:])
                return serial_hex
            else:
                _LOGGER.warning("❌ Failed to get EW serial at index %d: error code %d", index, result)
            return None
        except Exception as e:
            _LOGGER.error("Exception getting EW serial at index %d: %s", index, e)
            return None
    
    async def rx11_ew_receiver_scan_all(self, max_index: int = 255) -> dict[int, str]:
        """Scan for all EW receivers."""
        found_receivers = {}
        
        for index in range(max_index + 1):
            serial = await self.rx11_ew_receiver_get_serial_by_index(index)
            if serial:
                found_receivers[index] = serial
                _LOGGER.info("💾 Found EW receiver at index %d: %s", index, serial[-8:])
        
        _LOGGER.info("✅ Scan completed: %d receivers found", len(found_receivers))
        return found_receivers
    
    async def rx11_ew_receiver_get_next_available(self) -> Optional[tuple[int, str]]:
        """Get next available EW receiver (index, serial)."""
        for index in range(127):
            if index not in self._used_receivers:
                serial = await self.rx11_ew_receiver_get_serial_by_index(index)
                if serial and serial != "00" * 16:
                    _LOGGER.info("📍 Next available receiver: Index %d, Serial %s", 
                               index, serial[-8:])
                    return (index, serial)
        return None
    
    # ================================================================================================
    # EW TRANSMITTER OPERATIONS
    # ================================================================================================
    
    async def rx11_ew_transmitter_send_command(
        self, gateway_serial: str, button: int, timeout: float = 5.0
    ) -> bool:
        """Send command to EW receiver."""
        try:
            gateway_bytes = bytes.fromhex(gateway_serial)
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ew_send_cmd_request, gateway_bytes, button, timeout
            )
            
            success = result == ErrorCode.SUCCESS
            if not success:
                _LOGGER.warning("Send command failed: 0x%02x", result)
            return success
        except Exception as e:
            _LOGGER.error("Exception sending command: %s", e)
            return False
    
    async def rx11_ew_transmitter_start_continuous_send(
        self, gateway_serial: str, button: int
    ):
        """Start continuous sending (dead man's switch)."""
        try:
            gateway_bytes = bytes.fromhex(gateway_serial)
            await asyncio.get_event_loop().run_in_executor(
                None, self._module.start_ew_send_cmd_loop, gateway_bytes, button
            )
            _LOGGER.info("🔄 Started continuous send for %s button %d", 
                        gateway_serial[-8:], button)
        except Exception as e:
            _LOGGER.error("Exception starting continuous send: %s", e)
    
    async def rx11_ew_transmitter_stop_continuous_send(self):
        """Stop continuous sending."""
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, self._module.stop_ew_send_cmd_loop
            )
            _LOGGER.info("⏹️ Stopped continuous send")
        except Exception as e:
            _LOGGER.error("Exception stopping continuous send: %s", e)
    
    # ================================================================================================
    # EW BUTTON RECEIVE
    # ================================================================================================
    
    async def rx11_ew_receiver_button_wait_for_press(
        self, timeout: float = 30.0
    ) -> Optional[tuple[int, str, int]]:
        """
        Wait for EW button press.
        
        Returns:
            (info_type, transmitter_serial, button) or None
        """
        try:
            result, info_type, transmitter, info_data = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ew_rcv_button_request, timeout
            )
            
            if result == ErrorCode.SUCCESS:
                transmitter_hex = ''.join(f'{b:02X}' for b in transmitter)
                button_data = info_data[0] if len(info_data) > 0 else 0
                button = button_data & 0x03  # Extract button (bits 0-1)
                return (info_type, transmitter_hex, button)
            return None
        except Exception as e:
            _LOGGER.error("Exception waiting for button press: %s", e)
            return None
    
    async def set_learning_mode(self, enabled: bool, duration: int = 60) -> bool:
        """
        Set learning mode for device pairing.
        
        Args:
            enabled: True to start learning, False to stop
            duration: Learning duration in seconds (only used when enabled=True)
            
        Returns:
            True if learning mode was set successfully
        """
        if enabled:
            _LOGGER.info("🎓 Learning mode started for %d seconds", duration)
            # Learning mode is implemented via button wait in the integration
            # Just return True to indicate readiness
            return True
        else:
            _LOGGER.info("🎓 Learning mode stopped")
            return True
    
    # ================================================================================================
    # EW RECEIVER SEND OPERATIONS
    # ================================================================================================
    
    async def rx11_ew_receiver_send_command(self, serial_number: str, command: bytes) -> bool:
        """Send command to specific EW receiver using optimized cached index lookup."""
        if not self._connected:
            _LOGGER.error("❌ Cannot send command - RX11 not connected")
            return False
        
        # Check RX11 internal state
        if hasattr(self._module, '_state_good'):
            _LOGGER.debug("🔍 RX11 _state_good: %s", self._module._state_good)
            if not self._module._state_good:
                _LOGGER.error("🔴 RX11 is in bad state (_state_good=False), attempting to recover...")
                # Try to recover by resetting the state
                self._module._state_good = True
                _LOGGER.info("🔄 Reset _state_good to True")
            
        try:
            # Extract button code from command
            # Command format: bytes([0x01, button_code, 0x00, 0x00, 0x00]) or bytes([button_code])
            # The button_code is at index 1 if command starts with 0x01, otherwise at index 0
            _LOGGER.debug("📦 Command bytes received: %s (len=%d)", command.hex() if isinstance(command, bytes) else str(command), len(command))
            
            # Check command format and extract button code
            if len(command) >= 2 and command[0] == 0x01:
                # New format: bytes([0x01, button_code, ...])
                button = command[1]
                _LOGGER.debug("🔢 Using NEW format: button at command[1]")
            elif len(command) > 0:
                # Old/simple format: bytes([button_code])
                button = command[0]
                _LOGGER.debug("🔢 Using OLD format: button at command[0]")
            else:
                button = 0x00
                _LOGGER.debug("🔢 Empty command, defaulting to button A")
            
            button_letter = ['A', 'B', 'C', 'D'][button] if button < 4 else '?'
            _LOGGER.debug("🔢 Extracted button: %s (code=0x%02X)", button_letter, button)
            
            # Step 1: Check if this serial is in _used_receivers (from persistent tracking)
            short_serial = serial_number[-8:].upper()
            receiver_index = None
            receiver_serial = serial_number  # Default to using the stored serial directly
            
            _LOGGER.debug("🔍 Searching for receiver with serial: %s (input: %s)", short_serial, serial_number)
            _LOGGER.debug("📋 Available in _used_receivers: %s", {k: v[-8:] for k, v in self._used_receivers.items()})
            
            # First check _used_receivers for direct index lookup
            for index, used_serial in self._used_receivers.items():
                _LOGGER.debug("🔎 Checking index %d: %s vs %s", index, used_serial[-8:].upper(), short_serial)
                if used_serial[-8:].upper() == short_serial:
                    receiver_index = index
                    receiver_serial = used_serial
                    _LOGGER.info("🎯 Found receiver in used_receivers: index %d, serial=%s", 
                                  receiver_index, receiver_serial[-8:])
                    break
            
            # Step 2: If not found in used_receivers, check device-specific cache
            if receiver_index is None:
                if not hasattr(self, '_device_index_cache'):
                    self._device_index_cache = {}
                
                if short_serial in self._device_index_cache:
                    cached_index, cached_serial, cached_time = self._device_index_cache[short_serial]
                    # Use cached mapping if less than 60 seconds old
                    import time
                    if time.time() - cached_time < 60:
                        receiver_index = cached_index
                        receiver_serial = cached_serial
                        _LOGGER.debug("🎯 Using cached mapping for %s: index %d", short_serial, receiver_index)
            
            # Step 3: If still not found, try RX11 indices to find the device
            if receiver_index is None:
                # Search efficiently through known indices
                for index in range(10):  # Limit to first 10 for performance
                    cached_serial = await self.rx11_ew_receiver_get_serial_by_index(index)
                    if cached_serial and cached_serial[-8:].upper() == short_serial:
                        receiver_index = index
                        receiver_serial = cached_serial
                        
                        # Cache this mapping for future use
                        import time
                        self._device_index_cache[short_serial] = (index, cached_serial, time.time())
                        _LOGGER.debug("📌 Cached new mapping for %s: index %d", short_serial, index)
                        break
            
            # If we couldn't find index in RX11, use stored serial directly
            if receiver_index is None:
                _LOGGER.info("⚠️ Receiver not found in RX11 - using stored serial_number: %s", short_serial)
                receiver_serial = serial_number
            
            # Step 3: Send command using receiver serial as gateway
            # For EW receivers, the gateway is the receiver's own serial
            # Ensure serial is exactly 32 hex chars (16 bytes) by padding with leading zeros
            _LOGGER.debug("🔧 Before padding: receiver_serial=%s (len=%d)", receiver_serial, len(receiver_serial))
            receiver_serial_padded = receiver_serial.zfill(32)
            _LOGGER.debug("🔧 After padding: receiver_serial_padded=%s (len=%d)", receiver_serial_padded, len(receiver_serial_padded))
            
            try:
                gateway_bytes = bytes.fromhex(receiver_serial_padded)
                _LOGGER.debug("🔧 Gateway bytes length: %d bytes", len(gateway_bytes))
            except ValueError as e:
                _LOGGER.error("❌ Invalid hex string: %s, error: %s", receiver_serial_padded, e)
                return False
            
            # Log command being sent
            button_letter = ['A', 'B', 'C', 'D'][button] if button < 4 else '?'
            index_str = f"index {receiver_index}" if receiver_index is not None else "direct serial (old RX11)"
            _LOGGER.info("📤 Sending command to receiver: %s (%s, button %s)", 
                          short_serial, index_str, button_letter)
            
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ew_send_cmd_request, gateway_bytes, button, 5.0
            )
            
            button_letter = ['A', 'B', 'C', 'D'][button] if button < 4 else '?'
            
            if result == ErrorCode.SUCCESS:
                _LOGGER.warning("📢 Command sent to receiver %s: Button %s - SUCCESS", 
                              short_serial, button_letter)
                return True
            else:
                # Map error code to human-readable message
                error_names = {
                    0x01: "ERR_CANCELED",
                    0x02: "ERR_OUT_OF_QUEUE",
                    0x03: "ERR_INVALID_REQUEST",
                    0x04: "ERR_SIZE_MISMATCH",
                    0x05: "ERR_INVALID_PARAMETER",
                    0x06: "ERR_INCOMPLETE_FW",
                    0x07: "ERR_RF_TIMEOUT",
                    0x08: "ERR_INVALID_SERIAL",
                    0x09: "ERR_SUPERSEDED",
                    0x0A: "ERR_INCOMPAT_FW",
                    0x0B: "ERR_SERIAL_FILTER",
                    0x0C: "ERR_FILTER_OUT_OF_MEM",
                    0x0D: "ERR_INVALID_SEC_REPLY",
                    0x0E: "ERR_TOO_LATE",
                    0xFF: "ERR_FAILSTATE"
                }
                error_name = error_names.get(result, f"UNKNOWN_ERROR_{result}")
                _LOGGER.warning("📢 Command sent to receiver %s: Button %s - FAILED (%s)", 
                             short_serial, button_letter, error_name)
                
                # ERR_FAILSTATE (0xFF) means the RX11 is in a bad state
                if result == 0xFF:
                    _LOGGER.error("🔴 RX11 is in FAILSTATE - device may need reset or reconnection")
                
                return False
                
        except Exception as e:
            _LOGGER.error("❌ Error sending command to receiver %s: %s", serial_number[-8:], e)
            return False
    
    # ================================================================================================
    # EWB SENSOR OPERATIONS
    # ================================================================================================
    
    async def rx11_ewb_sensor_get_serial_by_index(self, index: int) -> Optional[str]:
        """Get EWB gateway serial by index.
        
        Note: Returns the gateway serial even if the index is not yet "joined".
        The RX11 has a fixed gateway serial for each index (0-127), regardless of
        whether a receiver has been joined to that index or not.
        """
        try:
            result, serial = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ewb_get_fd_serial_request, index
            )
            
            # Return the serial regardless of result code!
            # The RX11 returns the gateway serial even if the index is not yet joined
            if serial and serial != bytes(16):
                serial_hex = ''.join(f'{b:02X}' for b in serial)
                return serial_hex
            return None
        except Exception as e:
            _LOGGER.error("Exception getting EWB serial at index %d: %s", index, e)
            return None
    
    async def rx11_ewb_get_next_available_index_single(self) -> Optional[int]:
        """Get next available EWB index (not yet used)."""
        try:
            # RX11 supports 128 EWB indices (0-127), but we use 0-9 for single devices
            for index in range(10):
                # Query the serial at this index
                result, serial = await asyncio.get_event_loop().run_in_executor(
                    None, self._module.ewb_get_fd_serial_request, index
                )
                
                _LOGGER.info("🔍 Checking EWB index %d: result=%d, serial=%s", 
                           index, result, serial.hex() if serial else "None")
                
                # If error or empty serial, this index is available
                if result != ErrorCode.SUCCESS or not serial or serial == bytes(16):
                    _LOGGER.info("✅ Found available EWB index: %d", index)
                    return index
            
            _LOGGER.warning("⚠️ No available EWB index found in range 0-9")
            return None
        except Exception as e:
            _LOGGER.error("Exception getting next EWB index: %s", e)
            return None
    
    async def rx11_ewb_get_serial_by_index(self, index: int) -> Optional[str]:
        """Alias for rx11_ewb_sensor_get_serial_by_index."""
        return await self.rx11_ewb_sensor_get_serial_by_index(index)
    
    async def rx11_ewb_add_filter(self, gateway_serial: str) -> bool:
        """Alias for rx11_ewb_sensor_add_filter."""
        return await self.rx11_ewb_sensor_add_filter(gateway_serial)
    
    async def rx11_ewb_clear_filter(self) -> bool:
        """Alias for rx11_ewb_sensor_clear_filter."""
        return await self.rx11_ewb_sensor_clear_filter()
    
    async def _get_all_gateway_serials(self) -> list[str]:
        """Get all gateway serial numbers from registered EWneo devices.
        
        Returns:
            List of unique gateway serial numbers
        """
        gateway_serials = set()
        
        # Get from coordinator if available
        if self._coordinator:
            # Access registered devices from coordinator
            registered_devices = getattr(self._coordinator, '_registered_devices', {})
            for device_serial, device_info in registered_devices.items():
                # Check if device is EWneo type (has gateway_serial)
                gateway_serial = device_info.get('gateway_serial')
                if gateway_serial:
                    gateway_serials.add(gateway_serial)
        
        return list(gateway_serials)
    
    async def _refresh_all_ewneo_device_states(self) -> None:
        """Query state of all EWneo devices after reconnect to update their current state.
        
        This ensures that the Home Assistant state reflects the actual device state
        after a reconnection or startup. Uses retry logic for robustness.
        """
        if not self._coordinator:
            _LOGGER.debug("No coordinator available - skipping EWneo state refresh")
            return
        
        # Get all registered EWneo devices
        registered_devices = getattr(self._coordinator, '_registered_devices', {})
        ewneo_devices = []
        
        for device_serial, device_info in registered_devices.items():
            # Check if device is EWneo type (has gateway_serial and neo_device flag)
            if device_info.get('neo_device') and device_info.get('gateway_serial'):
                ewneo_devices.append({
                    'serial': device_serial,
                    'gateway_serial': device_info.get('gateway_serial'),
                    'name': device_info.get('name', device_serial[-8:]),
                    'device_type': device_info.get('device_type', 'unknown')
                })
        
        if not ewneo_devices:
            _LOGGER.debug("No EWneo devices found - skipping state refresh")
            return
        
        _LOGGER.info("🔄 Refreshing state for %d EWneo device(s) after reconnect...", len(ewneo_devices))
        
        # Query state for each device with retry logic (2 attempts, no waiting)
        success_count = 0
        for device in ewneo_devices:
            device_serial = device['serial']
            device_name = device['name']
            device_type = device['device_type']
            
            # Get device instance from transceiver
            device_instance = None
            if hasattr(self._coordinator.transceiver, '_device_instances'):
                device_instance = self._coordinator.transceiver._device_instances.get(device_serial)
            
            if not device_instance or not hasattr(device_instance, 'async_initialize'):
                _LOGGER.warning("⚠️ Device instance not found or not initializable: %s", device_serial[-8:])
                continue
            
            # Try twice without waiting
            init_success = False
            
            for attempt in range(1, 3):  # 2 attempts
                try:
                    _LOGGER.debug("🔧 Initializing EWneo %s %s (attempt %d/2)...", 
                                device_type, device_name, attempt)
                    
                    # Use device's async_initialize method which queries the state
                    init_success = await device_instance.async_initialize(is_restoration=True, max_retries=1)
                    
                    if init_success:
                        success_count += 1
                        _LOGGER.info("✅ State refreshed for %s %s (attempt %d)", 
                                   device_type, device_name, attempt)
                        
                        # Fire state update event so entities can update
                        if hasattr(device_instance, 'get_motor_data'):
                            motor_data = device_instance.get_motor_data()
                            # Convert motor data to parsed_state format for cover entity
                            parsed_state = self._convert_motor_data_to_parsed_state(motor_data, device_type)
                            if parsed_state and self._coordinator:
                                self._coordinator.hass.bus.async_fire(
                                    "eldat_ewneo_state_update",
                                    {
                                        "serial_number": device_serial,
                                        "device_id": device_serial,
                                        "parsed_state": parsed_state,
                                        "source": "initialization",
                                    }
                                )
                                _LOGGER.info("📡 Fired state update event for %s after initialization", device_serial[-8:])
                        
                        break  # Success - no need to retry
                    else:
                        _LOGGER.debug("⚠️ Failed to refresh state for %s (attempt %d/2)", 
                                      device_name, attempt)
                        
                except Exception as e:
                    _LOGGER.error("❌ Error refreshing state for %s (attempt %d/2): %s", 
                                device_name, attempt, e)
                    
                    # Don't retry on exceptions, move to next device
                    break
            
            # Log final result for this device - mark as unreachable but don't disable entities
            if not init_success:
                _LOGGER.warning("❌ Device %s nicht erreichbar - Entitäten bleiben verfügbar", 
                              device_name)
        
        # Log summary
        if success_count > 0:
            _LOGGER.info("✅ Successfully refreshed state for %d/%d EWneo device(s)", 
                        success_count, len(ewneo_devices))
        else:
            _LOGGER.warning("⚠️ Failed to refresh state for any EWneo devices")
    
    def _convert_motor_data_to_parsed_state(self, motor_data: dict, device_type: str) -> dict:
        """Convert motor device data to parsed_state format for cover entities.
        
        Args:
            motor_data: Data from device_instance.get_motor_data()
            device_type: Device type string (ewneo_motor, ewneo_dual_motor, etc.)
            
        Returns:
            Dict in parsed_state format that cover entities expect
        """
        try:
            # Single channel motor data
            state = motor_data.get("state", "stopped")
            position = motor_data.get("position")  # Already converted: 0=closed, 100=open
            current_position_pct = motor_data.get("current_position_pct")
            target_position_pct = motor_data.get("target_position_pct")
            control_mode = motor_data.get("control_mode", "positionless")
            is_moving = motor_data.get("is_moving", False)
            is_calibrating = motor_data.get("is_calibrating", False)
            
            # Determine movement direction
            is_opening = state == "opening"
            is_closing = state == "closing"
            
            # Build parsed state
            parsed_state = {
                "type": "cover",
                "motor_status": state,
                "motor_status_code": motor_data.get("motor_status_code", 126),
                "is_opening": is_opening,
                "is_closing": is_closing,
                "is_moving": is_moving,
                "is_calibrating": is_calibrating,
                "runtime_measured": control_mode == "position",
                "supports_position": control_mode == "position",
                "position_available": position is not None,
            }
            
            # Add position if available
            if position is not None:
                parsed_state["position"] = position
            if current_position_pct is not None:
                parsed_state["current_position_pct"] = current_position_pct
            if target_position_pct is not None:
                parsed_state["target_position"] = target_position_pct
            
            _LOGGER.debug("🔄 Converted motor data to parsed_state: %s", parsed_state)
            return parsed_state
            
        except Exception as e:
            _LOGGER.error("Error converting motor data to parsed_state: %s", e)
            return {}

    async def rx11_ewb_sensor_add_filter(self, gateway_serial: str) -> bool:
        """Add gateway to EWB filter.
        
        Returns True if filter was added successfully OR if filter already exists.
        """
        try:
            gateway_bytes = bytes.fromhex(gateway_serial)
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ewb_add_nfilter_request, gateway_bytes
            )
            # SUCCESS = filter added
            # ERR_SERIAL_FILTER = filter already exists (also OK)
            if result == ErrorCode.SUCCESS:
                _LOGGER.debug("EWB filter added for gateway %s", gateway_serial[-8:])
                return True
            elif result == ErrorCode.ERR_SERIAL_FILTER:
                _LOGGER.debug("EWB filter already exists for gateway %s", gateway_serial[-8:])
                return True
            else:
                _LOGGER.warning("EWB add filter returned unexpected code %d (0x%02X) for gateway %s", 
                               result, result, gateway_serial[-8:])
                return False
        except Exception as e:
            _LOGGER.error("Exception adding EWB filter: %s", e)
            return False
    
    async def rx11_ewb_sensor_clear_filter(self) -> bool:
        """Clear EWB filter."""
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ewb_clear_nfilter_request
            )
            return result == ErrorCode.SUCCESS
        except Exception as e:
            _LOGGER.error("Exception clearing EWB filter: %s", e)
            return False
    
    async def rx11_ewb_sensor_receive_telegram(
        self, timeout: float = 30.0
    ) -> Optional[dict[str, Any]]:
        """Receive EWB telegram."""
        try:
            result, info_type, receiver_transmitter, info_data = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ewb_rcv_request, timeout
            )
            
            if result == ErrorCode.SUCCESS:
                serial_hex = ''.join(f'{b:02X}' for b in receiver_transmitter)
                
                telegram = {
                    'info_type': info_type,
                    'serial': serial_hex,
                    'info_data': list(info_data)
                }
                
                # Parse based on info type
                if info_type == InfoType.TM_IT_EASW_PUSH:
                    button_data = info_data[0] if len(info_data) > 0 else 0
                    telegram['button'] = button_data & 0x03
                    telegram['function'] = button_data & 0xFC
                elif info_type == InfoType.TM_IT_SENSOR_DATA:
                    telegram['sensor_data'] = info_data[:8]
                elif info_type == InfoType.TM_IT_EWBIDI_STATE:
                    telegram['mode'] = info_data[0]
                    telegram['state'] = info_data[1:5]
                
                return telegram
            return None
        except Exception as e:
            _LOGGER.error("Exception receiving EWB telegram: %s", e)
            return None
    
    async def rx11_ewb_sensor_start_receive_loop(self):
        """Start continuous EWB receive loop."""
        if self._ewb_receive_task and not self._ewb_receive_task.done():
            _LOGGER.debug("EWB receive loop already running")
            return
        
        self._stop_ewb_receive = False
        self._ewb_receive_task = asyncio.create_task(self._ewb_receive_loop())
        _LOGGER.info("🚀 Started EWB receive loop")
    
    async def rx11_ewb_sensor_stop_receive_loop(self):
        """Stop continuous EWB receive loop."""
        self._stop_ewb_receive = True
        
        if self._ewb_receive_task and not self._ewb_receive_task.done():
            try:
                # Cancel the task first
                self._ewb_receive_task.cancel()
                # Then wait for it with a shorter timeout
                await asyncio.wait_for(self._ewb_receive_task, timeout=0.5)
            except asyncio.TimeoutError:
                _LOGGER.debug("EWB receive loop stop timeout (expected)")
            except asyncio.CancelledError:
                _LOGGER.debug("EWB receive loop cancelled (expected)")
            except Exception as e:
                _LOGGER.error("Error stopping EWB receive loop: %s", e)
            finally:
                self._ewb_receive_task = None
        
        _LOGGER.info("⏹️ Stopped EWB receive loop")
    
    async def _ewb_receive_loop(self):
        """Internal continuous EWB receive loop.
        
        This loop:
        1. Sends EWB_RCV requests to receive telegrams from EWB devices
        2. EWB_RCV is a blocking "long-poll" command that waits until a telegram arrives
        3. No timeout - the separate health check task monitors connection health
        4. Only SUCCESS and ERR_CANCELED/ERR_SUPERSEDED are expected results
        5. Real errors (FAILSTATE, I/O errors) are logged, health check handles reconnection
        6. Waits for reconnection if connection is lost
        """
        _LOGGER.info("🔄 EWB receive loop started (no timeout, health check monitors connection)")
        
        self._consecutive_errors = 0
        
        while not self._stop_ewb_receive:
            # Wait for connection if disconnected
            if not self._connected:
                _LOGGER.debug("EWB receive loop waiting for connection...")
                await asyncio.sleep(1.0)
                continue
                
            try:
                # Send EWB_RCV and wait for telegram (blocking until telegram arrives)
                # Use very long timeout (1 hour) - health check handles connection monitoring
                result, info_type, receiver_transmitter, info_data = await asyncio.get_event_loop().run_in_executor(
                    None, self._module.ewb_rcv_request, 3600.0
                )
                
                if result == ErrorCode.SUCCESS:
                    # Successful telegram received - update timestamp for health check
                    self._last_successful_communication = time.time()
                    self._consecutive_errors = 0
                    
                    # Log received telegram details
                    serial_hex = ''.join(f'{b:02X}' for b in receiver_transmitter)
                    data_hex = ' '.join(f'{b:02X}' for b in info_data)
                    _LOGGER.debug("📥 RX11 Telegram received: Serial=%s, InfoType=%d, Data=[%s]", 
                                serial_hex[-8:], info_type, data_hex)
                    
                    if self._telegram_callback:
                        try:
                            # Call callback with raw data (matching C library signature)
                            self._telegram_callback(info_type, receiver_transmitter, info_data)
                        except Exception as e:
                            _LOGGER.error("❌ Error in telegram callback: %s", e, exc_info=True)
                    else:
                        _LOGGER.warning("⚠️ Telegram received but NO callback set!")
                        
                elif result == ErrorCode.ERR_SUPERSEDED:
                    # SUPERSEDED means a new request replaced this one - normal during reconnect
                    self._consecutive_errors = 0
                    _LOGGER.debug("🔄 EWB_RCV superseded - new request sent")
                    
                elif result == ErrorCode.ERR_CANCELED:
                    # Request was canceled - normal during shutdown or reconnect
                    _LOGGER.debug("🛑 EWB_RCV canceled")
                    
                elif result == ErrorCode.ERR_RF_TIMEOUT:
                    # RF timeout - extremely rare with 1h timeout, just continue
                    self._consecutive_errors = 0
                    _LOGGER.debug("⏱️ EWB_RCV timeout - continuing")
                    
                elif result == ErrorCode.ERR_FAILSTATE:
                    # RX11 is in a bad state - health check will handle reconnection
                    self._consecutive_errors += 1
                    _LOGGER.error("🔴 RX11 FAILSTATE detected in EWB receive loop")
                    # Don't trigger reconnect here - let health check handle it
                    await asyncio.sleep(1.0)
                    
                else:
                    # Other error - log it
                    self._consecutive_errors += 1
                    error_name = self._get_error_name(result)
                    _LOGGER.warning("⚠️ EWB_RCV error: %s (0x%02X)", error_name, result)
                    await asyncio.sleep(0.5)
                        
            except asyncio.CancelledError:
                _LOGGER.info("🛑 EWB receive loop cancelled")
                break
            except (OSError, IOError) as e:
                # OS/IO error typically indicates USB disconnection
                # Health check will detect this and trigger reconnect
                _LOGGER.error("🔌 I/O Error in EWB receive loop: %s", e)
                await asyncio.sleep(1.0)
            except Exception as e:
                if not self._stop_ewb_receive:
                    _LOGGER.error("❌ Unexpected error in EWB receive loop: %s", e, exc_info=True)
                    await asyncio.sleep(0.5)
        
        _LOGGER.info("🔄 EWB receive loop stopped")
    
    # ================================================================================================
    # HEALTH CHECK
    # ================================================================================================
    
    async def _start_health_check(self):
        """Start the health check task."""
        if self._health_check_task and not self._health_check_task.done():
            _LOGGER.debug("Health check already running")
            return
        
        self._stop_health_check = False
        self._last_successful_communication = time.time()
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        _LOGGER.info("🏥 Started health check task (interval: %ds after last communication)", 
                    int(self._health_check_interval))
    
    async def _stop_health_check_task(self):
        """Stop the health check task."""
        self._stop_health_check = True
        
        if self._health_check_task and not self._health_check_task.done():
            try:
                self._health_check_task.cancel()
                await asyncio.wait_for(self._health_check_task, timeout=2.0)
            except asyncio.TimeoutError:
                _LOGGER.debug("Health check stop timeout (expected)")
            except asyncio.CancelledError:
                _LOGGER.debug("Health check cancelled (expected)")
            except Exception as e:
                _LOGGER.error("Error stopping health check: %s", e)
            finally:
                self._health_check_task = None
        
        _LOGGER.info("⏹️ Stopped health check task")
    
    async def _health_check_loop(self):
        """Health check loop - queries hardware version to verify connection.
        
        This loop:
        1. Waits until 30 seconds have passed since last successful communication
        2. Queries the hardware version as a quick health check
        3. If the query fails, attempts reconnection
        4. If communication happens (EWB telegram received), the timer resets
        5. Waits during reconnection, then resumes monitoring
        """
        _LOGGER.info("🏥 Health check loop started")
        health_check_errors = 0
        
        while not self._stop_health_check:
            # Wait for connection if disconnected (reconnection in progress or hardware error)
            if not self._connected:
                _LOGGER.debug("Health check waiting for connection...")
                await asyncio.sleep(2.0)
                health_check_errors = 0  # Reset errors after reconnect
                continue
            
            # Check if hardware error occurred - initiate reconnect immediately
            if self._module.has_hardware_error:
                _LOGGER.warning("🔴 Hardware-Fehler erkannt - initiiere sofortigen Reconnect")
                self._connected = False
                # Clear the hardware error flag before attempting reconnect
                self._module.clear_hardware_error()
                await self._handle_connection_lost()
                health_check_errors = 0
                continue
                
            try:
                # Calculate time since last successful communication
                time_since_last_comm = time.time() - self._last_successful_communication
                
                if time_since_last_comm >= self._health_check_interval:
                    # 30 seconds since last communication - perform health check
                    _LOGGER.debug("🏥 Performing health check (%.1fs since last communication)", 
                                time_since_last_comm)
                    
                    # Query hardware version as health check
                    # This is a quick command that should respond immediately
                    hw_version = await self._perform_health_check()
                    
                    if hw_version:
                        # Health check successful
                        self._last_successful_communication = time.time()
                        health_check_errors = 0
                        _LOGGER.debug("🏥 Health check OK - Hardware: %s", hw_version)
                    else:
                        # Health check failed
                        health_check_errors += 1
                        _LOGGER.warning("🏥 Health check failed (error %d/%d)", 
                                       health_check_errors, self._connection_lost_threshold)
                        
                        if health_check_errors >= self._connection_lost_threshold:
                            _LOGGER.error("🔴 Health check failed %d times - initiating reconnection", 
                                        health_check_errors)
                            await self._handle_connection_lost()
                            health_check_errors = 0
                
                # Sleep for 5 seconds, then check again
                # This allows us to react quickly when communication happens
                await asyncio.sleep(5.0)
                
            except asyncio.CancelledError:
                _LOGGER.info("🛑 Health check loop cancelled")
                break
            except Exception as e:
                if not self._stop_health_check:
                    _LOGGER.error("❌ Error in health check loop: %s", e, exc_info=True)
                    await asyncio.sleep(5.0)
        
        _LOGGER.info("🏥 Health check loop stopped")
    
    async def _perform_health_check(self) -> Optional[str]:
        """Perform a health check by querying the hardware version.
        
        Returns:
            Hardware version string if successful, None if failed
        """
        try:
            # Don't use cache - we want to actually communicate with the device
            result, hw_str = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ma_query_hw_ver_request
            )
            
            if result == ErrorCode.SUCCESS:
                # Convert bytes to string
                null_index = hw_str.find(0)
                if null_index >= 0:
                    hw_str = hw_str[:null_index]
                version = hw_str.decode('ascii', errors='ignore').strip()
                return version if version else None
            else:
                error_name = self._get_error_name(result)
                _LOGGER.warning("🏥 Health check query failed: %s (0x%02X)", error_name, result)
                return None
        except (OSError, IOError) as e:
            _LOGGER.error("🏥 Health check I/O error (USB disconnected?): %s", e)
            return None
        except Exception as e:
            _LOGGER.error("🏥 Health check exception: %s", e)
            return None
    
    def _get_error_name(self, error_code: int) -> str:
        """Get human-readable error name from error code."""
        error_names = {
            0x00: "SUCCESS",
            0x01: "ERR_CANCELED",
            0x02: "ERR_OUT_OF_QUEUE",
            0x03: "ERR_INVALID_REQUEST",
            0x04: "ERR_SIZE_MISMATCH",
            0x05: "ERR_INVALID_PARAMETER",
            0x06: "ERR_INCOMPLETE_FW",
            0x07: "ERR_RF_TIMEOUT",
            0x08: "ERR_INVALID_SERIAL",
            0x09: "ERR_SUPERSEDED",
            0x0A: "ERR_INCOMPAT_FW",
            0x0B: "ERR_SERIAL_FILTER",
            0x0C: "ERR_FILTER_OUT_OF_MEM",
            0x0D: "ERR_INVALID_SEC_REPLY",
            0x0E: "ERR_TOO_LATE",
            0xFF: "ERR_FAILSTATE"
        }
        return error_names.get(error_code, f"UNKNOWN_ERROR_{error_code}")
    
    async def _handle_connection_lost(self):
        """Handle connection lost - attempt to reconnect with device re-discovery.
        
        This method:
        1. Properly disposes of the old connection
        2. Resets all internal state (caches, error counters)
        3. Scans for the RX11 device (may be at a different port after replug)
        4. Attempts reconnection with exponential backoff
        5. Keeps trying indefinitely until device is found again
        """
        async with self._reconnect_lock:
            if self._reconnect_in_progress:
                _LOGGER.debug("Reconnection already in progress, skipping")
                return
            
            self._reconnect_in_progress = True
            self._stop_reconnect = False  # Reset stop flag for new reconnection attempt
        
        try:
            _LOGGER.info("🔄 Connection lost - attempting to reconnect to RX11...")
            
            # Mark as disconnected
            self._connected = False
            
            # Try to close existing connection gracefully and reset state
            await self._dispose_and_reset()
            
            # Wait a moment before starting reconnection attempts
            await asyncio.sleep(2.0)
            
            # Keep trying to reconnect indefinitely (until stop requested via _stop_reconnect)
            attempt = 0
            while not self._stop_reconnect:
                attempt += 1
                
                # Exponential backoff with max 60 seconds
                if attempt > 1:
                    delay = min(2 ** min(attempt - 1, 6), 60)
                    _LOGGER.info("🔄 Waiting %ds before reconnection attempt %d...", delay, attempt)
                    await asyncio.sleep(delay)
                
                _LOGGER.info("🔄 Reconnection attempt %d - scanning for RX11 device...", attempt)
                
                # Try to find the RX11 device (may be at different port)
                new_device_path = await self._find_rx11_device()
                
                if not new_device_path:
                    _LOGGER.info("⚠️ No RX11 device found - will retry...")
                    continue
                
                # Device found - update path if changed
                if new_device_path != self.device_path:
                    _LOGGER.info("📍 RX11 found at new port: %s (was: %s)", 
                               new_device_path, self.device_path)
                    self.device_path = new_device_path
                else:
                    _LOGGER.info("📍 RX11 found at same port: %s", new_device_path)
                
                # Try to connect
                try:
                    # Create fresh RxModule instance with clean state
                    self._module = RxModule(port=self.device_path, baudrate=115200, debug=True)
                    
                    # Connect
                    success = await asyncio.get_event_loop().run_in_executor(
                        None, self._module.connect
                    )
                    
                    if success:
                        # Register callbacks for future disconnect/reconnect events
                        self._module.set_disconnect_callback(self._on_hardware_disconnect)
                        self._module.set_reconnect_callback(self._on_hardware_reconnect)
                        
                        # Wait for serial interface to stabilize after reconnect
                        await asyncio.sleep(0.5)
                        
                        # Verify connection with hardware version query
                        hw_version = await self.get_hardware_version()
                        if hw_version:
                            self._connected = True
                            self._consecutive_errors = 0
                            self._last_successful_communication = time.time()
                            
                            # Clear version cache to force re-fetch
                            self._hw_version_cache = None
                            self._fw_version_cache = None
                            self._versions_fetched = False
                            
                            _LOGGER.info("✅ Reconnection successful! Hardware: %s", hw_version)
                            return
                        else:
                            _LOGGER.warning("⚠️ Connected but hardware version query failed")
                            # Dispose and try again
                            try:
                                await asyncio.get_event_loop().run_in_executor(
                                    None, self._module.dispose
                                )
                            except Exception:
                                pass
                    else:
                        _LOGGER.warning("❌ Connection to %s failed", self.device_path)
                        
                except Exception as e:
                    _LOGGER.error("❌ Reconnection attempt %d error: %s", attempt, e)
            
            _LOGGER.info("🛑 Reconnection loop stopped (shutdown requested)")
            
        finally:
            async with self._reconnect_lock:
                self._reconnect_in_progress = False
    
    async def _dispose_and_reset(self):
        """Dispose of current connection and reset all internal state."""
        _LOGGER.debug("Disposing connection and resetting state...")
        
        # Try to close existing module connection
        if self._module:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._module.dispose
                )
            except Exception as e:
                _LOGGER.debug("Error during dispose: %s", e)
        
        # Reset error counters
        self._serial_error_count = 0
        self._consecutive_errors = 0
        
        # Reset caches
        self._serial_cache.clear()
        self._cache_timestamp.clear()
        self._hw_version_cache = None
        self._fw_version_cache = None
        self._versions_fetched = False
        
        # Reset used receiver tracking
        self._used_receivers.clear()
        self._used_ewb_indices.clear()
        self._ewb_device_serials.clear()
        
        _LOGGER.debug("State reset complete")
    
    async def _find_rx11_device(self) -> Optional[str]:
        """Find RX11 device, potentially at a new port after USB replug.
        
        Returns:
            Device path if found, None otherwise
        """
        try:
            import serial.tools.list_ports
            
            # Scan for RX11 devices
            all_ports = await asyncio.get_event_loop().run_in_executor(
                None, serial.tools.list_ports.comports
            )
            
            # RX11 USB identifiers
            RX11_VID = 0x155A
            RX11_PIDS = [0x1014]
            
            for port in all_ports:
                if port.vid == RX11_VID and port.pid in RX11_PIDS:
                    _LOGGER.debug("Found RX11 at %s (VID: 0x%04X, PID: 0x%04X)", 
                                port.device, port.vid, port.pid)
                    return port.device
            
            return None
            
        except Exception as e:
            _LOGGER.error("Error scanning for RX11 device: %s", e)
            return None
    
    def set_telegram_callback(self, callback: Callable):
        """Set callback for received telegrams."""
        self._telegram_callback = callback
        _LOGGER.debug("🔗 RX11 Wrapper: Telegram callback SET to: %s (type: %s)", 
                       callback, type(callback).__name__ if callback else "None")
    
    def is_connected(self) -> bool:
        """Check if connected to RX11 device."""
        return self._connected
    
    def set_coordinator(self, coordinator) -> None:
        """Set coordinator reference for callbacks and coordination."""
        self._coordinator = coordinator
        _LOGGER.debug("Coordinator reference set for RX11Wrapper")
    
    # ================================================================================================
    # EWB DEVICE CONTROL
    # ================================================================================================
    
    async def rx11_ewb_device_join(
        self, gateway_serial: str, timeout: float = 2.0
    ) -> Optional[tuple[int, str]]:
        """Join EWB device - single attempt.
        
        Args:
            gateway_serial: Gateway serial number (hex string)
            timeout: Timeout in seconds for this single attempt (default: 2.0s)
        
        Returns:
            Tuple of (device_type, receiver_serial) on success, None on timeout/failure
        """
        try:
            gateway_bytes = bytes.fromhex(gateway_serial)
            result, device_type, receiver = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ewb_join_device_request, gateway_bytes, timeout
            )
            
            from .rx_module import ErrorCode
            
            if result == ErrorCode.SUCCESS:
                receiver_hex = ''.join(f'{b:02X}' for b in receiver)
                _LOGGER.info("✅ EWB_JOIN_DEVICE SUCCESS: Type=0x%02X, Serial=%s (full: %s, len=%d)",
                           device_type, receiver_hex[-8:], receiver_hex, len(receiver_hex))
                return (device_type, receiver_hex)
            else:
                # Timeout or other error - expected during learning, just return None
                _LOGGER.debug("⏸️ EWB_JOIN_DEVICE: result=0x%02X (timeout expected during learning)", result)
            
            return None
        except Exception as e:
            _LOGGER.error("Exception joining EWB device: %s", e, exc_info=True)
            return None
    
    async def rx11_ewb_join_device(
        self, gateway_serial: str, timeout: float = 30.0
    ) -> Optional[tuple[int, str]]:
        """Alias for rx11_ewb_device_join."""
        return await self.rx11_ewb_device_join(gateway_serial, timeout)
    
    async def rx11_ewb_device_remove(
        self, gateway_serial: str, receiver_serial: str
    ) -> bool:
        """Remove EWB device."""
        try:
            _LOGGER.info("📤 EWB_REMOVE_DEVICE: Gateway=%s, Receiver=%s",
                        gateway_serial[-8:], receiver_serial[-8:])
            
            gateway_bytes = bytes.fromhex(gateway_serial)
            receiver_bytes = bytes.fromhex(receiver_serial)
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ewb_remove_device_request, gateway_bytes, receiver_bytes
            )
            
            _LOGGER.info("📥 EWB_REMOVE_DEVICE result: %d (%s)",
                        result, "SUCCESS" if result == ErrorCode.SUCCESS else "FAILED")
            
            return result == ErrorCode.SUCCESS
        except Exception as e:
            _LOGGER.error("Exception removing EWB device: %s", e)
            return False
    
    # Alias for coordinator compatibility
    async def rx11_ewb_remove_device(
        self, gateway_serial: str, receiver_serial: str
    ) -> bool:
        """Alias for rx11_ewb_device_remove()."""
        return await self.rx11_ewb_device_remove(gateway_serial, receiver_serial)
    
    async def rx11_ewb_device_change_state(
        self, gateway_serial: str, receiver_serial: str,
        desired_mode: int, desired_state: list
    ) -> Optional[tuple[int, list]]:
        """Change EWB device state."""
        try:
            _LOGGER.info("📤 EWB_CHANGE_STATE: Gateway=%s, Receiver=%s, Mode=%d, State=%s",
                        gateway_serial[-8:], receiver_serial[-8:], desired_mode, 
                        [f"0x{b:02X}" for b in desired_state])
            
            gateway_bytes = bytes.fromhex(gateway_serial)
            receiver_bytes = bytes.fromhex(receiver_serial)
            # Convert list to bytes if needed
            state_bytes = bytes(desired_state) if isinstance(desired_state, list) else desired_state
            result, recent_mode, recent_state = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ewb_change_state_request,
                gateway_bytes, receiver_bytes, desired_mode, state_bytes
            )
            
            # Detailed error code evaluation
            if result == ErrorCode.SUCCESS:
                _LOGGER.info("📥 EWB_CHANGE_STATE SUCCESS: recent_mode=%d, recent_state=%s",
                            recent_mode, [f"0x{b:02X}" for b in recent_state] if recent_state else "None")
                return (recent_mode, list(recent_state))
            elif result == ErrorCode.ERR_RF_TIMEOUT:
                _LOGGER.error("❌ EWneo-Empfänger %s nicht erreichbar: Timeout (Gateway: %s)",
                             receiver_serial[-8:], gateway_serial[-8:])
                return ("ERR_RF_TIMEOUT", receiver_serial, gateway_serial)
            elif result == ErrorCode.ERR_INVALID_SERIAL:
                _LOGGER.error("❌ Ungültige Seriennummer für EWneo-Empfänger %s (Gateway: %s)",
                             receiver_serial[-8:], gateway_serial[-8:])
                return ("ERR_INVALID_SERIAL", receiver_serial, gateway_serial)
            elif result == ErrorCode.ERR_CANCELED:
                _LOGGER.warning("⚠️ EWB_CHANGE_STATE abgebrochen für Empfänger %s",
                               receiver_serial[-8:])
                return ("ERR_CANCELED", receiver_serial, gateway_serial)
            elif result == ErrorCode.ERR_SERIAL_FILTER:
                _LOGGER.error("❌ Gateway %s nicht im RX11-Filter - Filter neu setzen erforderlich",
                             gateway_serial[-8:])
                # Return error code so caller can handle it
                return ("ERR_SERIAL_FILTER", gateway_serial, receiver_serial)
            else:
                _LOGGER.error("❌ EWB_CHANGE_STATE fehlgeschlagen: ErrorCode=%d (0x%02X) für Empfänger %s",
                             result, result, receiver_serial[-8:])
                return ("ERR_UNKNOWN", result, receiver_serial)
        except Exception as e:
            _LOGGER.error("❌ Exception changing EWB state: %s", e)
            return None
    
    async def rx11_ewb_device_query_state(
        self, gateway_serial: str, receiver_serial: str, desired_mode: int
    ) -> Optional[tuple[int, list]]:
        """Query EWB device state."""
        try:
            _LOGGER.debug("📤 EWB_QUERY_STATE: Gateway=%s, Receiver=%s, Mode=%d",
                        gateway_serial[-8:] if gateway_serial else "None",
                        receiver_serial[-8:] if receiver_serial else "None", 
                        desired_mode)
            
            # Validate serial lengths - must be 32 hex chars (16 bytes)
            if not gateway_serial or len(gateway_serial) != 32:
                _LOGGER.error("❌ Invalid gateway_serial length: expected 32 chars, got %d chars (%s)",
                            len(gateway_serial) if gateway_serial else 0, gateway_serial)
                return None
            if not receiver_serial or len(receiver_serial) != 32:
                _LOGGER.error("❌ Invalid receiver_serial length: expected 32 chars, got %d chars (%s)",
                            len(receiver_serial) if receiver_serial else 0, receiver_serial)
                return None
            
            gateway_bytes = bytes.fromhex(gateway_serial)
            receiver_bytes = bytes.fromhex(receiver_serial)
            result, recent_mode, state = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ewb_query_state_request,
                gateway_bytes, receiver_bytes, desired_mode
            )
            
            _LOGGER.debug("📥 EWB_QUERY_STATE result: %d (mode=%d, state=%s)",
                        result, recent_mode, [f"0x{b:02X}" for b in state] if state else "None")
            
            if result == ErrorCode.SUCCESS:
                # Convert response bytes to list
                return (recent_mode, list(state))
            return None
        except Exception as e:
            _LOGGER.error("Exception querying EWB state: %s", e)
            return None
    
    # Alias method for coordinator compatibility
    async def rx11_ewb_query_state(self, gateway_serial: str, receiver_serial: str, 
                                   mode: int = 0) -> Optional[tuple[int, list]]:
        """Alias for rx11_ewb_device_query_state() for coordinator compatibility.
        
        Args:
            gateway_serial: Gateway device serial number
            receiver_serial: Target device serial number
            mode: Query mode (0=summary, 2=motor1 full, 10=motor2 full, etc.)
        
        Returns:
            Tuple of (recent_mode, state_list) or None if query failed
        """
        return await self.rx11_ewb_device_query_state(gateway_serial, receiver_serial, mode)
    
    # Alias method for cover compatibility
    async def rx11_ewb_change_state(self, gateway_serial: str, receiver_serial: str, 
                                    desired_mode: int, desired_state: list) -> Optional[tuple[int, list]]:
        """Change EWB device state and return (recent_mode, recent_state).
        
        Args:
            gateway_serial: Gateway device serial number
            receiver_serial: Target device serial number
            desired_mode: Desired mode value
            desired_state: List of desired state bytes
        
        Returns:
            Tuple of (recent_mode, recent_state) if successful, None otherwise
        """
        return await self.rx11_ewb_device_change_state(gateway_serial, receiver_serial, desired_mode, desired_state)
    
    # ================================================================================================
    # RECEIVER INDEX MANAGEMENT
    # ================================================================================================
    
    async def get_next_available_receiver(self) -> Optional[tuple[int, str]]:
        """Get the next available EW receiver (index, serial) that is not yet used.
        
        Returns:
            Tuple of (index, serial) for the next available receiver, or None if none available
        """
        # Use the existing rx11_ew_receiver_get_next_available method
        return await self.rx11_ew_receiver_get_next_available()
    
    def mark_receiver_used(self, index: int, serial: str):
        """Mark receiver as used."""
        self._used_receivers[index] = serial
        self._cache_serial(index, serial)
        _LOGGER.info("✅ Marked receiver as used: Index %d, Serial %s", index, serial[-8:])
    
    def mark_receiver_available(self, index: int):
        """Mark receiver as available."""
        if index in self._used_receivers:
            serial = self._used_receivers.pop(index)
            _LOGGER.info("♻️ Marked receiver as available: Index %d, Serial %s", 
                        index, serial[-8:])
    
    def is_index_used(self, index: int) -> bool:
        """Check if index is used."""
        return index in self._used_receivers
    
    def get_used_indices(self) -> set[int]:
        """Get used indices."""
        return set(self._used_receivers.keys())
    
    def get_next_free_index(self) -> Optional[int]:
        """Get next free index."""
        used_indices = self.get_used_indices()
        for index in range(255):
            if index not in used_indices:
                return index
        return None
    
    # ================================================================================================
    # EWB INDEX MANAGEMENT
    # ================================================================================================
    
    def mark_ewb_index_used(self, index: int, gateway_serial: str, device_serial: str = None):
        """Mark EWB index as used."""
        self._used_ewb_indices[index] = gateway_serial
        if device_serial:
            self._ewb_device_serials[gateway_serial] = device_serial
        _LOGGER.debug("📍 Marked EWB index %d as used (gateway: %s)", index, gateway_serial[-8:])
    
    def mark_ewb_index_free(self, index: int):
        """Mark EWB index as free."""
        if index in self._used_ewb_indices:
            gateway_serial = self._used_ewb_indices.pop(index)
            self._ewb_device_serials.pop(gateway_serial, None)
            _LOGGER.debug("♻️ Marked EWB index %d as free", index)
    
    def get_next_free_ewb_index(self) -> Optional[int]:
        """Get next free EWB index."""
        used_indices = set(self._used_ewb_indices.keys())
        for index in range(256):
            if index not in used_indices:
                return index
        return None
    
    # ================================================================================================
    # CACHE MANAGEMENT
    # ================================================================================================
    
    def _cache_serial(self, index: int, serial: str):
        """Cache serial for index."""
        import time
        self._serial_cache[index] = serial
        self._cache_timestamp[index] = time.time()
    
    def _get_cached_serial(self, index: int) -> Optional[str]:
        """Get cached serial if valid."""
        if index not in self._serial_cache:
            return None
        
        import time
        age = time.time() - self._cache_timestamp.get(index, 0)
        if age > self._cache_max_age:
            return None
        
        return self._serial_cache[index]
    
    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        import time
        current_time = time.time()
        valid_entries = sum(
            1 for timestamp in self._cache_timestamp.values()
            if (current_time - timestamp) < self._cache_max_age
        )
        
        return {
            "total_entries": len(self._serial_cache),
            "valid_entries": valid_entries,
            "expired_entries": len(self._serial_cache) - valid_entries,
            "cache_max_age": self._cache_max_age,
            "used_receivers": len(self._used_receivers),
            "used_ewb_indices": len(self._used_ewb_indices)
        }
