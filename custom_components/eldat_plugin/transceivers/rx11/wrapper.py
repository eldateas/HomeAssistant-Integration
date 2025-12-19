"""Python wrapper for ELDAT RX11."""
from __future__ import annotations

import asyncio
import logging
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
        
        _LOGGER.info("✅ RX11 Pure Python wrapper initialized for %s", device_path)
    
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
                    
                    # Test connection and fetch versions
                    await asyncio.sleep(0.3)  # Initial settle time
                    
                    if not self._versions_fetched:
                        hw_version = await self.get_hardware_version()
                        if hw_version:
                            _LOGGER.info("✅ Connection verified - Hardware: %s", hw_version)
                            self._versions_fetched = True
                        else:
                            _LOGGER.warning("⚠️ Hardware version query failed")
                    
                    # Start continuous EWB receive loop for background telegram monitoring
                    _LOGGER.info("🚀 Starting continuous EWB receive loop...")
                    await self.rx11_ewb_sensor_start_receive_loop()
                    
                    self._serial_error_count = 0
                    self._consecutive_errors = 0
                    
                    return True
                else:
                    _LOGGER.error("❌ Failed to connect to RX11")
                    return False
                    
            except Exception as e:
                _LOGGER.error("❌ Exception connecting to RX11: %s", e)
                return False
    
    async def disconnect(self):
        """Disconnect from RX11 device."""
        async with self._lock:
            if not self._connected:
                return
            
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
    
    def get_connection_stats(self) -> dict:
        """Get connection and error statistics."""
        return {
            'connected': self._connected,
            'serial_error_count': self._serial_error_count,
            'consecutive_errors': self._consecutive_errors,
            'device_path': self.device_path
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
                return version
            else:
                _LOGGER.warning("Hardware version query failed: 0x%02x", result)
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
                return version
            else:
                _LOGGER.warning("Firmware version query failed: 0x%02x", result)
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
    
    # ================================================================================================
    # EW RECEIVER OPERATIONS
    # ================================================================================================
    
    async def rx11_ew_receiver_get_serial_by_index(self, index: int) -> Optional[str]:
        """Get EW receiver serial by index."""
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
                if serial_hex != "00" * 16:
                    self._cache_serial(index, serial_hex)
                    return serial_hex
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
            _LOGGER.warning("🔍 RX11 _state_good: %s", self._module._state_good)
            if not self._module._state_good:
                _LOGGER.error("🔴 RX11 is in bad state (_state_good=False), attempting to recover...")
                # Try to recover by resetting the state
                self._module._state_good = True
                _LOGGER.warning("🔄 Reset _state_good to True")
            
        try:
            # Extract button from command
            button = command[0] if len(command) > 0 else 0x10
            
            # Step 1: Check if this serial is in _used_receivers (from persistent tracking)
            short_serial = serial_number[-8:].upper()
            receiver_index = None
            receiver_serial = None
            
            _LOGGER.warning("🔍 Searching for receiver with serial: %s (input: %s)", short_serial, serial_number)
            _LOGGER.warning("📋 Available in _used_receivers: %s", {k: v[-8:] for k, v in self._used_receivers.items()})
            
            # First check _used_receivers for direct index lookup
            for index, used_serial in self._used_receivers.items():
                _LOGGER.warning("🔎 Checking index %d: %s vs %s", index, used_serial[-8:].upper(), short_serial)
                if used_serial[-8:].upper() == short_serial:
                    receiver_index = index
                    receiver_serial = used_serial
                    _LOGGER.warning("🎯 Found receiver in used_receivers: index %d, serial length=%d, serial=%s", 
                                  receiver_index, len(receiver_serial), receiver_serial)
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
            
            # Step 3: If still not found, do efficient search through RX11 indices
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
                        
            if receiver_index is None or not receiver_serial:
                _LOGGER.error("❌ Could not find receiver %s in any index", short_serial)
                return False
            
            # Step 3: Send command using receiver serial as gateway
            # For EW receivers, the gateway is the receiver's own serial
            # Ensure serial is exactly 32 hex chars (16 bytes) by padding with leading zeros
            _LOGGER.warning("🔧 Before padding: receiver_serial=%s (len=%d)", receiver_serial, len(receiver_serial))
            receiver_serial_padded = receiver_serial.zfill(32)
            _LOGGER.warning("🔧 After padding: receiver_serial_padded=%s (len=%d)", receiver_serial_padded, len(receiver_serial_padded))
            
            try:
                gateway_bytes = bytes.fromhex(receiver_serial_padded)
                _LOGGER.warning("🔧 Gateway bytes length: %d bytes", len(gateway_bytes))
            except ValueError as e:
                _LOGGER.error("❌ Invalid hex string: %s, error: %s", receiver_serial_padded, e)
                return False
            
            # Log command being sent
            _LOGGER.warning("📤 RX11 Sending command: ReceiverSerial=%s (full=%s), Index=%d, Button=0x%02X", 
                          short_serial, receiver_serial_padded, receiver_index, button)
            
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ew_send_cmd_request, gateway_bytes, button, 5.0
            )
            
            if result == ErrorCode.SUCCESS:
                _LOGGER.warning("✅ RX11 Command sent successfully to receiver %s (index %d, button: 0x%02X)", 
                              short_serial, receiver_index, button)
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
                _LOGGER.error("⚠️ Failed to send command to receiver %s, error: %d (%s)", 
                             short_serial, result, error_name)
                
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
    
    async def rx11_ewb_sensor_add_filter(self, gateway_serial: str) -> bool:
        """Add gateway to EWB filter."""
        try:
            gateway_bytes = bytes.fromhex(gateway_serial)
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ewb_add_nfilter_request, gateway_bytes
            )
            return result == ErrorCode.SUCCESS
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
        """Internal continuous EWB receive loop."""
        _LOGGER.info("🔄 EWB receive loop started")
        
        while not self._stop_ewb_receive and self._connected:
            try:
                # Get raw telegram data from module
                result, info_type, receiver_transmitter, info_data = await asyncio.get_event_loop().run_in_executor(
                    None, self._module.ewb_rcv_request, 5.0
                )
                
                if result == ErrorCode.SUCCESS and self._telegram_callback:
                    try:
                        # Log received telegram details
                        serial_hex = ''.join(f'{b:02X}' for b in receiver_transmitter)
                        data_hex = ' '.join(f'{b:02X}' for b in info_data)
                        _LOGGER.warning("📥 RX11 Telegram received: Serial=%s, InfoType=%d, Data=[%s]", 
                                      serial_hex[-8:], info_type, data_hex)
                        
                        # Call callback with raw data (matching C library signature)
                        self._telegram_callback(info_type, receiver_transmitter, info_data)
                    except Exception as e:
                        _LOGGER.error("Error in telegram callback: %s", e)
                        
            except Exception as e:
                if not self._stop_ewb_receive:
                    _LOGGER.error("Error in EWB receive loop: %s", e)
                await asyncio.sleep(0.5)
        
        _LOGGER.info("🔄 EWB receive loop stopped")
    
    def set_telegram_callback(self, callback: Callable):
        """Set callback for received telegrams."""
        self._telegram_callback = callback
    
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
        self, gateway_serial: str, timeout: float = 30.0
    ) -> Optional[tuple[int, str]]:
        """Join EWB device."""
        try:
            gateway_bytes = bytes.fromhex(gateway_serial)
            result, device_type, receiver = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ewb_join_device_request, gateway_bytes, timeout
            )
            
            if result == ErrorCode.SUCCESS:
                receiver_hex = ''.join(f'{b:02X}' for b in receiver)
                return (device_type, receiver_hex)
            return None
        except Exception as e:
            _LOGGER.error("Exception joining EWB device: %s", e)
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
            gateway_bytes = bytes.fromhex(gateway_serial)
            receiver_bytes = bytes.fromhex(receiver_serial)
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ewb_remove_device_request, gateway_bytes, receiver_bytes
            )
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
            
            _LOGGER.info("📥 EWB_CHANGE_STATE result: %d (recent_mode=%d, recent_state=%s)",
                        result, recent_mode, [f"0x{b:02X}" for b in recent_state] if recent_state else "None")
            
            if result == ErrorCode.SUCCESS:
                # Convert response bytes to list
                return (recent_mode, list(recent_state))
            return None
        except Exception as e:
            _LOGGER.error("Exception changing EWB state: %s", e)
            return None
    
    async def rx11_ewb_device_query_state(
        self, gateway_serial: str, receiver_serial: str, desired_mode: int
    ) -> Optional[tuple[int, list]]:
        """Query EWB device state."""
        try:
            _LOGGER.info("📤 EWB_QUERY_STATE: Gateway=%s, Receiver=%s, Mode=%d",
                        gateway_serial[-8:], receiver_serial[-8:], desired_mode)
            
            gateway_bytes = bytes.fromhex(gateway_serial)
            receiver_bytes = bytes.fromhex(receiver_serial)
            result, recent_mode, state = await asyncio.get_event_loop().run_in_executor(
                None, self._module.ewb_query_state_request,
                gateway_bytes, receiver_bytes, desired_mode
            )
            
            _LOGGER.info("📥 EWB_QUERY_STATE result: %d (recent_mode=%d, state=%s)",
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
