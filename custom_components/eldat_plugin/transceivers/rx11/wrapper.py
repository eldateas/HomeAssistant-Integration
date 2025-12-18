"""Vereinfachter Python Wrapper für ELDAT RX11 C Bibliothek."""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

_LOGGER = logging.getLogger(__name__)

# Global device locks to prevent multiple connections to same device
_DEVICE_LOCKS = {}
_GLOBAL_LOCK = asyncio.Lock()

# Error codes from RxModule.h
SUCCESS = 0x00
ERR_CANCELED = 0x01
ERR_OUT_OF_QUEUE = 0x02
ERR_INVALID_REQUEST = 0x03
ERR_SIZE_MISMATCH = 0x04
ERR_INVALID_PARAMETER = 0x05
ERR_INCOMPLETE_FW = 0x06
ERR_RF_TIMEOUT = 0x07
ERR_INVALID_SERIAL = 0x08
ERR_SUPERSEDED = 0x09
ERR_INCOMPAT_FW = 0x0A
ERR_SERIAL_FILTER = 0x0B
ERR_FILTER_OUT_OF_MEM = 0x0C
ERR_INVALID_SEC_REPLY = 0x0D
ERR_TOO_LATE = 0x0E
ERR_FAILSTATE = 0xFF

# Info types from RxModule.h
TM_IT_EASW_RELEASE = 0x00
TM_IT_EASW_PUSH = 0x01
TM_IT_SENSOR_DATA = 0x02
TM_IT_EWBIDI_STATE = 0x03

# Button constants
TM_BUTTON_A = 0
TM_BUTTON_B = 1
TM_BUTTON_C = 2
TM_BUTTON_D = 3

# Serial Bus Error Detection und Auto-Reconnect
SERIAL_BUS_ERROR_PATTERN = "ERROR [writeToBuffer] - serial bus"
DUPLICATED_HANDLE_ERROR_PATTERN = "ERROR [RxHandler] - Duplicated handle"
RECONNECT_DELAY_SECONDS = 3.0
MAX_RECONNECT_ATTEMPTS = 5


class RX11Wrapper:
    """Vereinfachter Python Wrapper für ELDAT RX11 RxModule C Bibliothek.
    
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
        self._lib = None
        self._connected = False
        self._disposed = False  # Track disposal state
        self._lock = asyncio.Lock()
        self._used_receivers = {}  # {index: serial} mapping for used receivers
        self._available_receivers = {}  # {index: serial} mapping for available receivers
        
        # EWB (EasyWave Bidirectional) device tracking
        self._used_ewb_indices = {}     # {index: gateway_serial} mapping for used EWB indices
        self._ewb_device_serials = {}   # {gateway_serial: device_serial} mapping
        self._last_scan_time = 0  # Cache timestamp
        
        # Serial Bus Error Detection und Reconnect
        self._serial_error_count = 0
        self._last_reconnect_attempt = 0
        self._reconnect_in_progress = False
        self._consecutive_errors = 0
        
        # Central serial number cache
        self._serial_cache: dict[int, str] = {}         # Central cache: index -> serial
        self._cache_timestamp: dict[int, float] = {}    # Cache timestamp for each index
        self._cache_max_age = 300.0  # Cache valid for 5 minutes
        
        # Version caching - prevent multiple version queries
        self._hw_version_cache: Optional[str] = None
        self._fw_version_cache: Optional[str] = None
        self._versions_fetched = False
        
        # Kontinuierliche EWB Receive-Loop für EWneo-Sensoren, EW-Transmitter und EWneo-Receiver
        self._ewb_receive_task: Optional[asyncio.Task] = None
        self._stop_ewb_receive = False
        self._telegram_callback: Optional[Callable] = None
        
        # Load the C library
        self._load_library()

    # =============================================================================
    # CONNECTION AND HARDWARE MANAGEMENT
    # =============================================================================

    async def _check_for_serial_bus_errors(self) -> bool:
        """Check for serial bus errors and trigger reconnect if needed."""
        # Diese Methode wird von den Command-Methoden aufgerufen um auf Fehler zu reagieren
        if self._serial_error_count > 0:
            current_time = time.time()
            
            # Wenn zu viele Fehler auftreten, versuche Reconnect
            if (self._serial_error_count >= 3 and 
                current_time - self._last_reconnect_attempt > RECONNECT_DELAY_SECONDS and
                not self._reconnect_in_progress):
                
                _LOGGER.warning("🔄 Detected %d serial bus errors, attempting reconnect...", 
                               self._serial_error_count)
                
                return await self._attempt_auto_reconnect()
        
        return True  # No reconnect needed
    
    async def _attempt_auto_reconnect(self) -> bool:
        """Attempt automatic reconnection after serial bus errors with handle cleanup."""
        if self._reconnect_in_progress:
            return False
            
        self._reconnect_in_progress = True
        self._last_reconnect_attempt = time.time()
        
        try:
            _LOGGER.info("🔄 Starting auto-reconnect procedure with handle cleanup...")
            
            # 1. Erweiterte Bereinigung mit Handle-Cleanup
            await self._advanced_device_reset()
            
            # 2. Normale Disconnect-Prozedur
            await self._do_disconnect()
            
            # 3. Zusätzliche Pause für vollständigen Device Reset
            await asyncio.sleep(1.5)
            
            # 4. Versuche Reconnect mit verbesserter Retry-Logic
            for attempt in range(MAX_RECONNECT_ATTEMPTS):
                _LOGGER.debug("🔄 Reconnect attempt %d/%d (with handle cleanup)", attempt + 1, MAX_RECONNECT_ATTEMPTS)
                
                # Vor jedem Reconnect-Versuch Handle-Cleanup durchführen
                if attempt > 0:
                    await self._force_handle_cleanup()
                    await asyncio.sleep(0.5)
                
                success = await self.connect()
                if success:
                    _LOGGER.info("✅ Auto-reconnect successful after %d attempts (handle cleanup applied)", attempt + 1)
                    self._serial_error_count = 0
                    self._consecutive_errors = 0
                    return True
                    
                if attempt < MAX_RECONNECT_ATTEMPTS - 1:
                    delay = min(RECONNECT_DELAY_SECONDS * (1.5 ** attempt), 10.0)  # Exponential backoff
                    _LOGGER.debug("⏰ Waiting %.1fs before next attempt...", delay)
                    await asyncio.sleep(delay)
            
            _LOGGER.error("❌ Auto-reconnect failed after %d attempts", MAX_RECONNECT_ATTEMPTS)
            return False
            
        except Exception as e:
            _LOGGER.error("❌ Error during auto-reconnect: %s", e)
            return False
            
        finally:
            self._reconnect_in_progress = False
    
    def _handle_serial_bus_error(self, error_msg: str = None) -> None:
        """Handle serial bus error detection including duplicated handle errors."""
        self._serial_error_count += 1
        self._consecutive_errors += 1
        
        if error_msg:
            if SERIAL_BUS_ERROR_PATTERN in error_msg:
                _LOGGER.warning("⚠️ Serial bus write error detected: %s", error_msg.strip())
            elif DUPLICATED_HANDLE_ERROR_PATTERN in error_msg:
                _LOGGER.warning("⚠️ Duplicated handle error detected: %s", error_msg.strip())
                # Duplicated Handle ist ein kritischer Fehler - sofortiger Reconnect
                self._serial_error_count = 3  # Force immediate reconnect
            else:
                _LOGGER.warning("⚠️ Communication error detected: %s", error_msg.strip())
        else:
            _LOGGER.warning("⚠️ Serial bus error detected (count: %d)", self._serial_error_count)
    
    def _reset_error_counters(self) -> None:
        """Reset error counters after successful operation."""
        if self._serial_error_count > 0 or self._consecutive_errors > 0:
            _LOGGER.debug("✅ Resetting error counters (was: serial=%d, consecutive=%d)", 
                         self._serial_error_count, self._consecutive_errors)
            
        self._serial_error_count = 0
        self._consecutive_errors = 0

    def get_connection_stats(self) -> dict:
        """Get connection and error statistics."""
        return {
            'connected': self._connected,
            'serial_error_count': self._serial_error_count,
            'consecutive_errors': self._consecutive_errors,
            'reconnect_in_progress': self._reconnect_in_progress,
            'last_reconnect_attempt': self._last_reconnect_attempt
        }

    async def _force_handle_cleanup(self) -> None:
        """Force cleanup of any pending handles to prevent duplicated handle errors."""
        if not self._lib:
            return
            
        try:
            # Only dispose if not already disposed - Dispose already calls CANCEL_ALL_IO
            if not self._disposed:
                _LOGGER.debug("🧽 Calling Dispose for handle cleanup...")
                self._lib.Dispose()
                self._disposed = True
                _LOGGER.debug("✅ Handle cleanup completed")
            else:
                _LOGGER.debug("🧽 Skipping Dispose - already disposed")
            
        except Exception as e:
            _LOGGER.debug("🧽 Handle cleanup error (ignored): %s", e)
            # Ignoriere Fehler beim Cleanup, da es defensiv ist
            pass

    async def _advanced_device_reset(self) -> None:
        """Advanced device reset to clear any lingering state."""
        try:
            _LOGGER.debug("🔄 Performing advanced device reset...")
            
            # 1. Force handle cleanup
            await self._force_handle_cleanup()
            
            # 2. Longer reset pause to allow USB device to fully reset
            await asyncio.sleep(2.0)
            
            # 3. Reset internal state completely
            self._disposed = False
            self._connected = False
            
            _LOGGER.debug("✅ Advanced device reset completed")
            
        except Exception as e:
            _LOGGER.warning("⚠️ Advanced reset error: %s", e)

    async def _reset_usb_device(self):
        """Attempt to reset the USB device by finding and resetting it."""
        try:
            import subprocess
            import os
            
            # Try to find the USB device by path and reset it
            usb_path = None
            if self.device_path.startswith('/dev/ttyACM'):
                # Extract device number
                device_num = self.device_path.replace('/dev/ttyACM', '')
                
                # Try to find USB bus and device info
                try:
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, 
                        lambda: subprocess.run(
                            ['dmesg', '|', 'grep', '-E', f'ttyACM{device_num}.*usb'], 
                            shell=True, capture_output=True, text=True
                        )
                    )
                    _LOGGER.debug("🔄 USB device reset attempt for %s", self.device_path)
                except Exception:
                    pass  # Ignore reset errors
                    
        except Exception as e:
            _LOGGER.debug("🔄 USB reset failed (ignored): %s", e)
    
    def _load_library(self):
        """Load the RX11 C library."""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        lib_path = os.path.join(current_dir, "RxModule.so")
        
        if not os.path.exists(lib_path):
            raise RuntimeError(f"RX11 C library not found at {lib_path}")
            
        try:
            self._lib = ctypes.CDLL(lib_path)
            self._setup_function_prototypes()
            _LOGGER.info("✅ RX11 C library loaded successfully from %s", lib_path)
        except Exception as e:
            _LOGGER.error("❌ Failed to load RX11 C library: %s", e)
            raise
    
    def _setup_function_prototypes(self):
        """Set up function prototypes for the C library.""" 
        if not self._lib:
            return
        
        # Try to enable debug mode for UART logging
        try:
            debug_info = ctypes.c_int.in_dll(self._lib, "DebugInfo")
            original_value = debug_info.value
            debug_info.value = 1
            _LOGGER.debug("🐛 C library debug mode enabled (was: %d, now: %d)", original_value, debug_info.value)
        except Exception as e:
            _LOGGER.debug("Could not enable C library debug mode: %s", e)
            
        # Connect function
        self._lib.Connect.argtypes = [ctypes.c_char_p, ctypes.c_bool]
        self._lib.Connect.restype = ctypes.c_int
        
        # Dispose function
        self._lib.Dispose.argtypes = []
        self._lib.Dispose.restype = None
        
        # Hardware version function
        self._lib.MaQueryHwVerRequest.argtypes = [ctypes.POINTER(ctypes.c_ubyte * 16)]
        self._lib.MaQueryHwVerRequest.restype = ctypes.c_ubyte
        
        # Firmware version function
        self._lib.MaQueryFwVerRequest.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte),  # MajorVer
            ctypes.POINTER(ctypes.c_ubyte),  # MinorVer  
            ctypes.POINTER(ctypes.c_bool)    # bIncompleteFw
        ]
        self._lib.MaQueryFwVerRequest.restype = ctypes.c_ubyte
        
        # EasyWave button receive function
        self._lib.EwRcvButtonRequest.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte),      # InfoType
            ctypes.POINTER(ctypes.c_ubyte * 16), # Transmitter  
            ctypes.POINTER(ctypes.c_ubyte * 8)   # InfoData
        ]
        self._lib.EwRcvButtonRequest.restype = ctypes.c_ubyte
        
        # EasyWave send command function
        self._lib.EwSendCmdRequest.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte * 16), # Gateway
            ctypes.c_ubyte                       # Button
        ]
        self._lib.EwSendCmdRequest.restype = ctypes.c_ubyte
        
        # EasyWave get serial function
        self._lib.EwGetFdSerialRequest.argtypes = [
            ctypes.c_uint16,                     # Index
            ctypes.POINTER(ctypes.c_ubyte * 16)  # Serial
        ]
        self._lib.EwGetFdSerialRequest.restype = ctypes.c_ubyte
        
        # Cancel all IO
        self._lib.CancelAllIoRequest.argtypes = []
        self._lib.CancelAllIoRequest.restype = None
        
        # StartEwSendCmdLoopRequest function for continuous sending
        try:
            self._lib.StartEwSendCmdLoopRequest.argtypes = [
                ctypes.c_uint8 * 16,  # Gateway[16]
                ctypes.c_uint8        # Button
            ]
            self._lib.StartEwSendCmdLoopRequest.restype = None
        except AttributeError:
            _LOGGER.warning("StartEwSendCmdLoopRequest symbol not found in library")
            
        # StopEwSendCmdLoopRequest function to stop continuous sending
        try:
            self._lib.StopEwSendCmdLoopRequest.argtypes = []
            self._lib.StopEwSendCmdLoopRequest.restype = None
        except AttributeError:
            _LOGGER.warning("StopEwSendCmdLoopRequest symbol not found in library")

    async def connect(self) -> bool:
        """Connect to RX11 device."""
        _LOGGER.debug("🔍 Starting connection process for %s", self.device_path)
        
        # Acquire global lock to prevent race conditions with device connections
        _LOGGER.debug("🔒 Acquiring global device lock...")
        async with _GLOBAL_LOCK:
            # Get or create device-specific lock
            if self.device_path not in _DEVICE_LOCKS:
                _DEVICE_LOCKS[self.device_path] = asyncio.Lock()
            device_lock = _DEVICE_LOCKS[self.device_path]
            _LOGGER.debug("🔒 Global lock acquired, got device-specific lock")
        
        _LOGGER.debug("🔒 Acquiring device-specific lock for %s...", self.device_path)
        async with device_lock:
            _LOGGER.debug("🔒 Device-specific lock acquired")
            
            if self._connected:
                _LOGGER.info("✅ Already connected to RX11 at %s (skipping redundant connect)", self.device_path)
                return True
            
            _LOGGER.info("🔌 Connecting to RX11 at %s...", self.device_path)
                
            async with self._lock:
                try:
                    # Connect() in C library already calls CancelAllIoRequest() internally
                    # No need for explicit Dispose() here - it would just cause extra CANCEL_ALL_IO
                    _LOGGER.debug("🔌 Preparing connection (C library will handle cleanup)...")
                    
                    # Reset disposal state for new connection attempt
                    self._disposed = False
                    
                    device_bytes = self.device_path.encode('utf-8')
                    _LOGGER.debug("🔌 Calling C library Connect for %s...", self.device_path)
                    result = self._lib.Connect(device_bytes, True)
                    
                    if result == SUCCESS:
                        self._connected = True
                        _LOGGER.info("✅ Connected to RX11 at %s", self.device_path)
                        
                        # Initialize receiver management on first connection
                        if not hasattr(self, '_receivers_initialized'):
                            _LOGGER.debug("🔄 Initializing receiver management...")
                            self._used_receivers.clear()  # Start fresh
                        
                            # Cache will be populated lazily when receivers are actually used
                            _LOGGER.debug("💾 Cache ready for lazy population of used receivers")
                            
                            self._receivers_initialized = True
                        
                        # Test the connection with a hardware version call (only if not cached)
                        # Add extra delay before first communication attempt
                        await asyncio.sleep(0.3)
                        
                        # Only fetch versions if not already cached
                        if not self._versions_fetched:
                            try:
                                hw_version = await self.get_hardware_version()
                                if hw_version:
                                    _LOGGER.info("✅ Connection verified - Hardware: %s", hw_version)
                                    self._versions_fetched = True
                                else:
                                    _LOGGER.warning("⚠️ Connection established but hardware test failed")
                                    # Connection test failed, mark as disconnected
                                    self._connected = False
                                    return False
                            except Exception as e:
                                _LOGGER.warning("⚠️ Connection test failed: %s", e)
                                # Connection test failed, mark as disconnected
                                self._connected = False
                                return False
                        else:
                            _LOGGER.debug("📦 Versions already cached, skipping version queries")
                        
                        # Starte kontinuierliche EWB Receive-Loop für EWneo-Sensoren, EW-Transmitter und EWneo-Receiver
                        # Diese läuft parallel zur Coordinator EWB monitoring loop
                        _LOGGER.info("🚀 Starte kontinuierliche EWB Receive-Loop...")
                        try:
                            await self.rx11_ewb_sensor_start_receive_loop()
                            _LOGGER.info("✅ EWB Receive-Loop erfolgreich gestartet")
                        except Exception as loop_error:
                            _LOGGER.error("❌ Fehler beim Starten der EWB Receive-Loop: %s", loop_error)
                        
                        # Reset error counters after successful connection
                        self._reset_error_counters()
                        
                        return True
                    else:
                        _LOGGER.error("❌ Failed to connect to RX11: error code %d", result)
                        return False
                        
                except Exception as e:
                    _LOGGER.error("❌ Exception connecting to RX11: %s", e)
                    return False

    async def disconnect(self):
        """Disconnect from RX11 device.""" 
        # Acquire device-specific lock if it exists
        device_lock = None
        async with _GLOBAL_LOCK:
            device_lock = _DEVICE_LOCKS.get(self.device_path)
        
        if device_lock:
            async with device_lock:
                await self._do_disconnect()
        else:
            await self._do_disconnect()
    
    async def _do_disconnect(self):
        """Internal disconnect implementation with enhanced handle cleanup."""
        if not self._connected:
            return
        
        # Stoppe kontinuierliche EWB Receive-Loop
        await self.rx11_ewb_sensor_stop_receive_loop()
            
        async with self._lock:
            try:
                # Enhanced handle cleanup to prevent duplicated handle errors
                await self._force_handle_cleanup()
                
                self._connected = False
                
                # Clear version cache on disconnect (will be re-fetched on next connect)
                # This is only done on explicit disconnect, not on reconnect
                # Comment this out if you want versions to persist across reconnects
                # self._hw_version_cache = None
                # self._fw_version_cache = None
                # self._versions_fetched = False
                
                _LOGGER.info("✅ Disconnected from RX11 at %s (with handle cleanup)", self.device_path)
                
                # Reset initialization state to ensure clean reconnection
                if hasattr(self, '_receivers_initialized'):
                    delattr(self, '_receivers_initialized')
                
            except Exception as e:
                _LOGGER.error("❌ Exception during RX11 disconnect: %s", e)
                # Force state reset even on error
                self._connected = False
                self._disposed = True


        
    # =============================================================================
    # RX11 EW RECEIVER OPERATIONS
    # =============================================================================

    async def _ensure_receiver_cached(self, index: int) -> Optional[str]:
        """Ensure receiver at index is cached, load if necessary."""
        cached_serial = self._get_cached_serial(index)
        if cached_serial is not None:
            return cached_serial
            
        # Load receiver serial on demand
        return await self.rx11_ew_receiver_get_serial_by_index(index)
        
    async def _populate_receiver_cache(self) -> None:
        """Pre-populate cache with receiver serials for indices 0-4."""
        if not self._lib or not self._connected:
            return
            
        _LOGGER.debug("💾 Populating receiver cache for indices 0-4...")
        populated_count = 0
        
        for index in range(5):  # Check indices 0-4
            try:
                serial_array = (ctypes.c_ubyte * 16)()
                result = self._lib.EwGetFdSerialRequest(
                    ctypes.c_uint16(index),
                    ctypes.byref(serial_array)
                )
                
                if result == SUCCESS:
                    serial_hex = ''.join([f'{b:02X}' for b in serial_array])
                    if serial_hex != "00" * 16:  # Valid serial
                        self._cache_serial(index, serial_hex)
                        populated_count += 1
                        _LOGGER.debug("💾 Pre-cached receiver at index %d: %s", index, serial_hex[-8:])
                    else:
                        # Cache empty result to avoid future queries
                        self._cache_serial(index, "")
                        _LOGGER.debug("💫 Empty receiver at index %d cached", index)
                else:
                    # Cache empty result for failed queries
                    self._cache_serial(index, "")
                    _LOGGER.debug("🚫 No receiver at index %d (error %d) - cached", index, result)
                    
            except Exception as e:
                _LOGGER.warning("Failed to populate cache for index %d: %s", index, e)
                
        _LOGGER.info("💾 Cache population complete: %d valid receivers found and cached", populated_count)


    
    async def rx11_ew_receiver_scan_all(self, max_index: int = 255) -> Dict[int, str]:
        """Scan for EW receivers by index and populate cache."""
        found_receivers = {}
        
        # Enhanced debugging for connection state
        _LOGGER.info("🔍 Starting EW receiver scan (lib: %s, connected: %s, max_index: %d)", 
                    bool(self._lib), self._connected, max_index)
        
        if not self._lib:
            _LOGGER.error("❌ C library not loaded - cannot scan EW receivers")
            return found_receivers
            
        if not self._connected:
            _LOGGER.error("❌ RX11 not connected - cannot scan EW receivers")
            return found_receivers
            
        # Test basic hardware version first
        try:
            hw_version = await self.get_hardware_version()
            if hw_version:
                _LOGGER.info("✅ Hardware connection verified - Version: %s", hw_version)
            else:
                _LOGGER.warning("⚠️ Hardware version call returned None - connection may be unstable")
        except Exception as e:
            _LOGGER.error("🚨 Hardware version test failed: %s", e)
            return found_receivers
            
        _LOGGER.info("🔍 EW receiver scan starting (max index: %d)...", max_index)
        scan_attempts = 0
        
        for index in range(max_index + 1):
            scan_attempts += 1
            
            # Use direct hardware query during scan to populate cache
            try:
                serial_array = (ctypes.c_ubyte * 16)()
                result = self._lib.EwGetFdSerialRequest(
                    ctypes.c_uint16(index),
                    ctypes.byref(serial_array)
                )
                
                if result == SUCCESS:
                    serial_hex = ''.join([f'{b:02X}' for b in serial_array])
                    if serial_hex == "00" * 16:  # Empty/null serial
                        _LOGGER.debug("🔶 Index %d: Empty serial (all zeros)", index)
                    else:
                        found_receivers[index] = serial_hex
                        # Populate cache during scan
                        self._cache_serial(index, serial_hex)
                        _LOGGER.info("💾 Found EW receiver at index %d: %s (cached)", index, serial_hex[-8:])
                else:
                    _LOGGER.debug("🚫 Index %d: No serial returned", index)
                    
            except Exception as e:
                _LOGGER.error("Error querying index %d: %s", index, e)
                
            # Log progress every 10 attempts
            if scan_attempts % 10 == 0:
                _LOGGER.debug("📋 Scan progress: %d/%d (found %d so far)", scan_attempts, max_index + 1, len(found_receivers))
        
        _LOGGER.info("✅ EW receiver scan completed: %d receivers found and cached after scanning %d indices", len(found_receivers), scan_attempts)
        return found_receivers
        
    async def rx11_ew_receiver_get_next_available(self) -> Optional[tuple[int, str]]:
        """Get the next available EW receiver (index, serial) using RX11-specific implementation."""
        if not self._lib or not self._connected:
            _LOGGER.error("❌ RX11 not connected - cannot get next receiver")
            return None
            
        _LOGGER.debug("🔍 RX11: Getting next available EW receiver (optimized search)...")
        _LOGGER.debug("📊 Current used receivers: %s", list(self._used_receivers.keys()))
        
        # Find first unused index, prioritizing cached entries
        for index in range(255):  # Max theoretical range
            if index not in self._used_receivers:
                # Check cache first
                cached_serial = self._get_cached_serial(index)
                if cached_serial and cached_serial != "00" * 16:
                    _LOGGER.info("📍 Next available receiver (cached): Index %d, Serial %s", index, cached_serial[-8:])
                    return (index, cached_serial)
                    
                # Only query hardware if not in cache
                serial = await self.rx11_ew_receiver_get_serial_by_index(index)
                if serial and serial != "00" * 16:  # Valid non-empty serial
                    _LOGGER.info("📍 Next available receiver (hardware): Index %d, Serial %s", index, serial[-8:])
                    return (index, serial)
                else:
                    _LOGGER.debug("💫 Index %d: No valid receiver found", index)
                    # Continue to next index
                    
        _LOGGER.warning("⚠️ No available receivers found - all indices used or no hardware available")
        return None
        
    def mark_receiver_used(self, index: int, serial: str) -> None:
        """Mark a receiver as used/allocated and cache the serial."""
        # Prevent double allocation
        if index in self._used_receivers:
            existing_serial = self._used_receivers[index]
            if existing_serial != serial:
                _LOGGER.warning("⚠️ Index %d already used by serial %s, not reassigning to %s", 
                              index, existing_serial[-8:], serial[-8:])
                return
            else:
                _LOGGER.debug("✅ Index %d already correctly assigned to serial %s", index, serial[-8:])
                return
        
        self._used_receivers[index] = serial
        # Also cache the serial for future use
        self._cache_serial(index, serial)
        _LOGGER.info("✅ Marked receiver as used: Index %d, Serial %s", index, serial[-8:])
        
    def mark_receiver_available(self, index: int) -> None:
        """Mark a receiver as available again (e.g., when device is removed)."""
        if index in self._used_receivers:
            serial = self._used_receivers.pop(index)
            _LOGGER.info("♻️ Marked receiver as available: Index %d, Serial %s", index, serial[-8:])
        
    def _is_cache_valid(self, index: int) -> bool:
        """Check if cached serial for index is still valid."""
        if index not in self._cache_timestamp:
            return False
        import time
        age = time.time() - self._cache_timestamp[index]
        return age < self._cache_max_age
    
    def _cache_serial(self, index: int, serial: str) -> None:
        """Cache serial number for index."""
        import time
        
        # Ensure cache dictionaries exist
        if not hasattr(self, '_serial_cache'):
            self._serial_cache = {}
        if not hasattr(self, '_cache_timestamp'):
            self._cache_timestamp = {}
            
        self._serial_cache[index] = serial
        self._cache_timestamp[index] = time.time()
        
        if serial and serial != "00" * 16:
            _LOGGER.debug("📝 Cached receiver at index %d: %s", index, serial[-8:])
    
    def _get_cached_serial(self, index: int) -> Optional[str]:
        """Get cached serial for index if valid."""
        if index not in self._serial_cache:
            return None
            
        if not self._is_cache_valid(index):
            _LOGGER.debug("🕰️ Cache expired for index %d", index)
            return None
            
        serial = self._serial_cache.get(index)
        return serial
    
    def _find_cached_serial_by_device(self, device_serial: str) -> Optional[int]:
        """Find index for device serial in cache."""
        for index, cached_serial in self._serial_cache.items():
            if cached_serial == device_serial and self._is_cache_valid(index):
                return index
        return None
        
    def get_used_indices(self) -> set[int]:
        """Get set of currently used indices."""
        return set(self._used_receivers.keys())
        
    def is_index_used(self, index: int) -> bool:
        """Check if an index is already used."""
        return index in self._used_receivers
        
    def get_next_free_index(self) -> Optional[int]:
        """Get the next free index without checking hardware."""
        used_indices = set(self._used_receivers.keys())
        for index in range(255):
            if index not in used_indices:
                return index
        return None
        
    def get_available_receivers_count(self) -> int:
        """Get count of available (not used) receivers."""
        return len(self._available_receivers) - len(self._used_receivers)
        
    def reset_used_receivers(self) -> None:
        """Reset the used receivers list (for debugging/recovery)."""
        old_count = len(self._used_receivers)
        self._used_receivers.clear()
        _LOGGER.info("🔄 Reset used receivers list (was: %d, now: 0)", old_count)
        
    def get_cached_serial_for_device(self, device_serial: str) -> Optional[int]:
        """Get cached receiver index for a device serial."""
        for index, cached_device_serial in self._used_receivers.items():
            if cached_device_serial == device_serial:
                return index
        return None
        
    # =============================================================================
    # EWB (EasyWave Bidirectional) Index Management
    # =============================================================================
    
    def mark_ewb_index_used(self, index: int, gateway_serial: str, device_serial: str = None) -> None:
        """Mark an EWB index as used by a specific gateway."""
        self._used_ewb_indices[index] = gateway_serial
        if device_serial:
            self._ewb_device_serials[gateway_serial] = device_serial
        _LOGGER.debug("📍 Marked EWB index %d as used (gateway: %s)", index, gateway_serial[-8:])
        
    def mark_ewb_index_free(self, index: int) -> None:
        """Mark an EWB index as free/unused."""
        if index in self._used_ewb_indices:
            gateway_serial = self._used_ewb_indices.pop(index)
            if gateway_serial in self._ewb_device_serials:
                self._ewb_device_serials.pop(gateway_serial)
            _LOGGER.debug("♻️ Marked EWB index %d as free (was: %s)", index, gateway_serial[-8:])
    
    def is_ewb_index_used(self, index: int) -> bool:
        """Check if an EWB index is already used."""
        return index in self._used_ewb_indices
        
    def get_used_ewb_indices(self) -> set[int]:
        """Get set of currently used EWB indices."""
        return set(self._used_ewb_indices.keys())
        
    def get_next_free_ewb_index(self) -> Optional[int]:
        """Get the next free EWB index from cached tracking without hardware calls."""
        used_indices = self.get_used_ewb_indices()
        for index in range(256):  # EWB supports 0-255
            if index not in used_indices:
                _LOGGER.debug("🎯 Next free EWB index (cached): %d", index)
                return index
        return None
        
    def get_ewb_device_by_gateway(self, gateway_serial: str) -> Optional[str]:
        """Get device serial by gateway serial."""
        return self._ewb_device_serials.get(gateway_serial)
        
    # =============================================================================
    # EWB (EasyWave Bidirectional) Index Management
    # =============================================================================
    
    def mark_ewb_index_used(self, index: int, gateway_serial: str, device_serial: str = None) -> None:
        """Mark an EWB index as used by a specific gateway."""
        self._used_ewb_indices[index] = gateway_serial
        if device_serial:
            self._ewb_device_serials[gateway_serial] = device_serial
        _LOGGER.debug("📍 Marked EWB index %d as used (gateway: %s)", index, gateway_serial[-8:])
        
    def mark_ewb_index_free(self, index: int) -> None:
        """Mark an EWB index as free/unused."""
        if index in self._used_ewb_indices:
            gateway_serial = self._used_ewb_indices.pop(index)
            if gateway_serial in self._ewb_device_serials:
                self._ewb_device_serials.pop(gateway_serial)
            _LOGGER.debug("♻️ Marked EWB index %d as free (was: %s)", index, gateway_serial[-8:])
    
    def is_ewb_index_used(self, index: int) -> bool:
        """Check if an EWB index is already used."""
        return index in self._used_ewb_indices
        
    def get_used_ewb_indices(self) -> set[int]:
        """Get set of currently used EWB indices."""
        return set(self._used_ewb_indices.keys())
        
    def get_next_free_ewb_index(self) -> Optional[int]:
        """Get the next free EWB index from cached tracking without hardware calls."""
        used_indices = self.get_used_ewb_indices()
        for index in range(256):  # EWB supports 0-255
            if index not in used_indices:
                _LOGGER.debug("🎯 Next free EWB index (cached): %d", index)
                return index
        return None
        
    def get_ewb_device_by_gateway(self, gateway_serial: str) -> Optional[str]:
        """Get device serial by gateway serial."""
        return self._ewb_device_serials.get(gateway_serial)

    def get_cached_serial_for_device(self, device_serial: str) -> Optional[int]:
        """Get cached index for a device serial number."""
        return self._find_cached_serial_by_device(device_serial)
        
    def cache_device_mapping(self, device_serial: str, index: int) -> None:
        """Cache device serial to index mapping."""
        self._cache_serial(index, device_serial)
        _LOGGER.debug("📝 Cached device mapping: %s -> index %d", device_serial[-8:], index)
        
    def get_cache_stats(self) -> dict:
        """Get cache statistics for debugging."""
        import time
        current_time = time.time()
        valid_entries = sum(1 for timestamp in self._cache_timestamp.values() 
                          if (current_time - timestamp) < self._cache_max_age)
        
        cache_details = {}
        for index, serial in self._serial_cache.items():
            timestamp = self._cache_timestamp.get(index, 0)
            age = current_time - timestamp if timestamp > 0 else float('inf')
            cache_details[index] = {
                'serial': serial[-8:] if serial else 'None',
                'age': f'{age:.1f}s',
                'valid': age < self._cache_max_age
            }
        
        return {
            "total_entries": len(self._serial_cache),
            "valid_entries": valid_entries,
            "expired_entries": len(self._serial_cache) - valid_entries,
            "cache_max_age": self._cache_max_age,
            "used_receivers": len(self._used_receivers),
            "cache_details": cache_details
        }

    # =============================================================================
    # RX11 SYSTEM INFORMATION OPERATIONS
    # =============================================================================

    async def rx11_system_get_hardware_version(self) -> Optional[str]:
        """Get RX11 hardware version using structured naming convention.
        
        Returns:
            Optional[str]: Hardware version string or None if failed
            
        Note:
            Uses MaQueryHwVerRequest from RX11 C library with retry logic.
            Cached after first successful query.
        """
        if not self._lib or not self._connected:
            _LOGGER.warning("🚫 RX11 not connected - cannot get hardware version")
            return None
        
        # Return cached version if available
        if self._hw_version_cache is not None:
            _LOGGER.debug("📦 Using cached hardware version: %s", self._hw_version_cache)
            return self._hw_version_cache
            
        # Try up to 3 times with increasing delays
        for attempt in range(3):
            try:
                if attempt > 0:
                    _LOGGER.debug("🔧 RX11 hardware version retry attempt %d/3", attempt + 1)
                    await asyncio.sleep(0.5 * attempt)  # Increasing delay
                    
                _LOGGER.debug("🔧 RX11: Calling MaQueryHwVerRequest...")
                
                # Add timeout to prevent hanging
                try:
                    hw_str = (ctypes.c_ubyte * 16)()
                    result = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None, lambda: self._lib.MaQueryHwVerRequest(ctypes.byref(hw_str))
                        ), timeout=8.0  # Reduced timeout per attempt
                    )
                    _LOGGER.debug("🔧 RX11: MaQueryHwVerRequest completed with result: %d", result)
                
                    if result == SUCCESS:
                        version_bytes = bytes(hw_str)
                        null_index = version_bytes.find(0)
                        if null_index >= 0:
                            version_bytes = version_bytes[:null_index]
                        hw_version = version_bytes.decode('ascii', errors='ignore')
                        # Cache the version
                        self._hw_version_cache = hw_version
                        _LOGGER.info("✅ RX11 Hardware version retrieved: %s", hw_version)
                        return hw_version
                    else:
                        _LOGGER.warning("RX11 hardware version query failed, error: %d (attempt %d/3)", result, attempt + 1)
                        
                except asyncio.TimeoutError:
                    _LOGGER.warning("⏰ RX11 hardware version query timed out (attempt %d/3)", attempt + 1)
                    
            except Exception as e:
                _LOGGER.warning("Error getting RX11 hardware version (attempt %d/3): %s", attempt + 1, e)
        
        _LOGGER.error("❌ Failed to get RX11 hardware version after 3 attempts")
        return None

    async def rx11_system_get_firmware_version(self) -> Optional[str]:
        """Get RX11 firmware version using structured naming convention.
        
        Returns:
            Optional[str]: Firmware version string (e.g., "2.1", "2.1 (incomplete)") or None if failed
            
        Note:
            Uses MaQueryFwVerRequest from RX11 C library with timeout protection.
            Cached after first successful query.
        """
        if not self._lib or not self._connected:
            _LOGGER.warning("🚫 RX11 not connected - cannot get firmware version")
            return None
        
        # Return cached version if available
        if self._fw_version_cache is not None:
            _LOGGER.debug("📦 Using cached firmware version: %s", self._fw_version_cache)
            return self._fw_version_cache
            
        try:
            major_ver = ctypes.c_ubyte()
            minor_ver = ctypes.c_ubyte() 
            incomplete_fw = ctypes.c_bool()
            
            _LOGGER.debug("🔧 RX11: Calling MaQueryFwVerRequest...")
            
            # Add timeout to prevent hanging
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._lib.MaQueryFwVerRequest(
                        ctypes.byref(major_ver),
                        ctypes.byref(minor_ver), 
                        ctypes.byref(incomplete_fw)
                    )
                ), timeout=10.0
            )
            
            if result == SUCCESS:
                version_str = f"{major_ver.value}.{minor_ver.value}"
                if incomplete_fw.value:
                    version_str += " (incomplete)"
                # Cache the version
                self._fw_version_cache = version_str
                _LOGGER.info("✅ RX11 Firmware version retrieved: %s", version_str)
                return version_str
            else:
                _LOGGER.warning("RX11 firmware version query failed, error: %d", result)
                return None
                
        except asyncio.TimeoutError:
            _LOGGER.error("⏰ RX11 firmware version query timed out")
            return None
        except Exception as e:
            _LOGGER.error("❌ Failed to get RX11 firmware version: %s", e)
            return None

    # Legacy alias methods for backward compatibility
    async def get_hw_version(self) -> Optional[str]:
        """Backward compatibility alias for rx11_system_get_hardware_version."""
        return await self.rx11_system_get_hardware_version()
    
    async def get_fw_version(self) -> Optional[str]:
        """Backward compatibility alias for rx11_system_get_firmware_version."""
        return await self.rx11_system_get_firmware_version()

    async def get_hardware_version(self) -> Optional[str]:
        """Legacy method - redirects to RX11-specific implementation."""
        return await self.rx11_system_get_hardware_version()
    
    async def get_firmware_version(self) -> Optional[str]:
        """Legacy method - redirects to RX11-specific implementation."""
        return await self.rx11_system_get_firmware_version()

    async def rx11_ew_receiver_send_command_legacy(self, gateway_serial: str, button: int) -> bool:
        """Legacy send command to EW receiver (kept for compatibility)."""
        if not self._lib or not self._connected:
            return False
            
        try:
            # Convert hex string to bytes array
            gateway_bytes = bytes.fromhex(gateway_serial.replace(':', ''))
            if len(gateway_bytes) != 16:
                _LOGGER.error("Invalid gateway serial length: %d bytes", len(gateway_bytes))
                return False
                
            # Create ctypes array
            gateway_array = (ctypes.c_ubyte * 16)()
            for i, byte_val in enumerate(gateway_bytes):
                gateway_array[i] = byte_val
            
            result = self._lib.EwSendCmdRequest(
                ctypes.byref(gateway_array),
                ctypes.c_ubyte(button)
            )
            
            if result == SUCCESS:
                _LOGGER.debug("✅ Command sent to %s button %d", gateway_serial[-8:], button)
                return True
            else:
                _LOGGER.error("❌ Failed to send command: error code %d", result)
                return False
                
        except Exception as e:
            _LOGGER.error("❌ Error sending command: %s", e)
            return False

    async def rx11_ew_receiver_get_serial_by_index(self, index: int) -> Optional[str]:
        """Get EW receiver serial number by index using EwGetFdSerialRequest."""
        if not self._lib or not self._connected:
            return None
            
        try:
            serial_array = (ctypes.c_ubyte * 16)()
            result = self._lib.EwGetFdSerialRequest(
                ctypes.c_uint16(index),
                ctypes.byref(serial_array)
            )
            
            if result == SUCCESS:
                # Convert to hex string
                serial_hex = ''.join([f'{b:02X}' for b in serial_array])
                _LOGGER.debug("✅ Retrieved EW serial at index %d: %s", index, serial_hex)
                return serial_hex
            else:
                _LOGGER.debug("No EW serial at index %d, error: %d", index, result)
                return None
                
        except Exception as e:
            _LOGGER.error("Error getting EW serial by index %d: %s", index, e)
            return None

    # =============================================================================
    # RX11 EW TRANSMITTER OPERATIONS
    # =============================================================================

    async def rx11_ew_transmitter_receive_telegram(self) -> Optional[dict]:
        """Receive telegram from EW device."""
        if not self._lib or not self._connected:
            return None
            
        try:
            info_type = ctypes.c_ubyte()
            transmitter = (ctypes.c_ubyte * 16)()
            info_data = (ctypes.c_ubyte * 8)()
            
            result = self._lib.EwRcvButtonRequest(
                ctypes.byref(info_type),
                ctypes.byref(transmitter),
                ctypes.byref(info_data)
            )
            
            if result == SUCCESS:
                # Convert to readable format
                transmitter_hex = ''.join([f'{b:02X}' for b in transmitter])
                info_data_hex = ''.join([f'{b:02X}' for b in info_data[:4]])
                
                telegram = {
                    'info_type': info_type.value,
                    'transmitter': transmitter_hex,
                    'info_data': info_data_hex,
                    'button': info_data[0] & 0x03,  # Extract button from first data byte
                }
                
                _LOGGER.debug("📡 Received telegram: %s", telegram)
                return telegram
            else:
                # Don't log timeouts as errors
                if result != ERR_RF_TIMEOUT:
                    _LOGGER.warning("Failed to receive telegram: error code %d", result)
                return None
                
        except Exception as e:
            _LOGGER.error("Error receiving telegram: %s", e)
            return None

    def is_connected(self) -> bool:
        """Check if connected to RX11."""
        return self._connected

    def cancel_all_operations(self):
        """Cancel all pending operations."""
        if self._lib:
            try:
                self._lib.CancelAllIoRequest()
                _LOGGER.debug("🛑 Cancelled all RX11 operations")
            except Exception as e:
                _LOGGER.error("Error cancelling operations: %s", e)
    
    # =============================================================================
    # RX11 EW RECEIVER BUTTON OPERATIONS  
    # =============================================================================

    async def rx11_ew_receiver_button_start_continuous(self, serial_number: str, button: int) -> bool:
        """Start continuous command using StartEwSendCmdLoopRequest."""
        if not self._lib or not self._connected:
            _LOGGER.error("Not connected to RX11 device")
            return False
            
        try:
            # Step 1: Get receiver index efficiently with caching
            receiver_index = None
            receiver_serial = None
            
            # Check cache first for performance
            for index in range(10):
                cached_serial = await self._ensure_receiver_cached(index)
                if cached_serial and cached_serial[-8:].upper() == serial_number[-8:].upper():
                    receiver_index = index
                    receiver_serial = cached_serial
                    break
                    
            if receiver_index is None or not receiver_serial:
                _LOGGER.error("❌ Could not find receiver %s in any index", serial_number[-8:])
                return False
            
            # Step 2: Convert receiver serial to gateway format
            gateway_bytes = bytes.fromhex(receiver_serial)
            gateway_array = (ctypes.c_ubyte * 16)()
            for i in range(16):
                gateway_array[i] = gateway_bytes[i]
            
            _LOGGER.debug("🔧 Gateway conversion - Serial: %s, Gateway bytes: %s, Button: %d", 
                        receiver_serial, [hex(b) for b in gateway_bytes], button)
            
            # Step 3: Check function availability and call StartEwSendCmdLoopRequest
            if hasattr(self._lib, 'StartEwSendCmdLoopRequest'):
                _LOGGER.info("🚀 Starting continuous command for receiver %s (index %d, button %d) using StartEwSendCmdLoopRequest", 
                           serial_number[-8:], receiver_index, button)
                
                import time
                start_time = time.time()
                
                # Define function to call StartEwSendCmdLoopRequest with proper scope
                def call_start_cmd_loop_request():
                    try:
                        # StartEwSendCmdLoopRequest is void - no return value
                        self._lib.StartEwSendCmdLoopRequest(gateway_array, ctypes.c_ubyte(button))
                        return True  # Always return success since it's void
                    except Exception as e:
                        _LOGGER.error("❌ Exception in StartEwSendCmdLoopRequest call: %s", e)
                        return False
                
                try:
                    # Call StartEwSendCmdLoopRequest - this should return quickly as it just starts a thread
                    result = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(None, call_start_cmd_loop_request),
                        timeout=3.0  # 3 second timeout for start
                    )
                    
                    duration = time.time() - start_time
                    
                    if result:
                        _LOGGER.info("✅ StartEwSendCmdLoopRequest started successfully in %.2fs", duration)
                        return True
                    else:
                        _LOGGER.error("❌ StartEwSendCmdLoopRequest failed")
                        return False
                        
                except asyncio.TimeoutError:
                    _LOGGER.error("❌ StartEwSendCmdLoopRequest timed out after 3 seconds")
                    return False
                except Exception as e:
                    _LOGGER.error("❌ Error calling StartEwSendCmdLoopRequest: %s", e)
                    return False
                    
            else:
                _LOGGER.error("StartEwSendCmdLoopRequest not available in library")
                return False
            
        except Exception as e:
            _LOGGER.error("❌ Error starting continuous command: %s", e)
            return False
    
    async def rx11_ew_receiver_button_stop_continuous(self, serial_number: str, button: int) -> bool:
        """Stop continuous command using StopEwSendCmdLoopRequest."""
        if not self._lib or not self._connected:
            _LOGGER.error("Not connected to RX11 device")
            return False
            
        try:
            # Check if StopEwSendCmdLoopRequest is available
            if hasattr(self._lib, 'StopEwSendCmdLoopRequest'):
                _LOGGER.info("🛑 Stopping continuous command for receiver %s (button %d) using StopEwSendCmdLoopRequest", 
                           serial_number[-8:], button)
                
                import time
                start_time = time.time()
                
                try:
                    # Define function to call StopEwSendCmdLoopRequest
                    def call_stop_cmd_loop_request():
                        # StopEwSendCmdLoopRequest is void and uses pthread_join which can take time
                        self._lib.StopEwSendCmdLoopRequest()
                        return True  # Always return success since it's void
                    
                    # Call StopEwSendCmdLoopRequest with reasonable timeout
                    result = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(None, call_stop_cmd_loop_request),
                        timeout=5.0  # 5 second timeout should now be sufficient with fixed C function
                    )
                    
                    duration = time.time() - start_time
                    _LOGGER.info("✅ StopEwSendCmdLoopRequest completed successfully in %.2fs", duration)
                    
                    # Add small delay to ensure the hardware processes the stop command
                    await asyncio.sleep(0.2)
                    
                    return True
                    
                except asyncio.TimeoutError:
                    _LOGGER.error("❌ StopEwSendCmdLoopRequest timed out after 15 seconds (pthread_join taking too long)")
                    return False
                    
                except Exception as exec_error:
                    _LOGGER.error("❌ StopEwSendCmdLoopRequest execution error: %s", exec_error)
                    return False
            else:
                _LOGGER.error("StopEwSendCmdLoopRequest not available in library")
                return False
            
        except Exception as e:
            _LOGGER.error("❌ Error stopping continuous command: %s", e)
            return False
    
    # Additional methods expected by coordinator
    async def rx11_system_set_learning_mode(self, enabled: bool, timeout: int = 60) -> bool:
        """Set RX11 learning mode using structured naming convention."""
        if enabled:
            _LOGGER.info("🎓 RX11: Learning mode requested (timeout: %ds) - simplified implementation", timeout)
            # For now, just return True as learning may need to be handled differently
            return True
        else:
            _LOGGER.info("🛑 RX11: Learning mode disabled")
            return True
    
    async def rx11_system_is_learning_mode(self) -> bool:
        """Check if RX11 learning mode is active using structured naming convention."""
        return False
    
    # Backward compatibility aliases
    async def set_learning_mode(self, enabled: bool, timeout: int = 60) -> bool:
        """Backward compatibility alias for rx11_system_set_learning_mode."""
        return await self.rx11_system_set_learning_mode(enabled, timeout)
    
    async def is_learning_mode(self) -> bool:
        """Backward compatibility alias for rx11_system_is_learning_mode."""
        return await self.rx11_system_is_learning_mode()
    
    async def rx11_ew_receiver_send_command_to_device(self, device_serial: str, command: str) -> bool:
        """Send command to EW receiver device using RX11-specific implementation.
        
        Args:
            device_serial: Serial number of the target device
            command: Command to send (hex string or bytes)
            
        Returns:
            bool: True if command was sent successfully
        """
        try:
            # Convert string command to bytes if needed
            if isinstance(command, str):
                # Check if it's a hex string
                if all(c in '0123456789ABCDEFabcdef' for c in command.replace(' ', '')):
                    command_bytes = bytes.fromhex(command.replace(' ', ''))
                else:
                    # Convert simple button commands
                    button_map = {'10': 0, '11': 1, '12': 2, '13': 3, '0': 0, '1': 1, '2': 2, '3': 3}
                    if command in button_map:
                        command_bytes = bytes([button_map[command]])
                    else:
                        command_bytes = command.encode('utf-8')
            else:
                command_bytes = bytes(command)
            
            # Use the main rx11_ew_receiver_send_command method
            return await self.rx11_ew_receiver_send_command(device_serial, command_bytes)
            
        except Exception as e:
            _LOGGER.error("❌ Error in rx11_ew_receiver_send_command_to_device: %s", e)
            return False
    
    # Backward compatibility alias
    async def send_command_to_device(self, device_serial: str, command: str) -> bool:
        """Backward compatibility alias for rx11_ew_receiver_send_command_to_device."""
        return await self.rx11_ew_receiver_send_command_to_device(device_serial, command)

    # Additional coordinator compatibility methods
    async def async_setup(self, hass) -> bool:
        """Setup transceiver - simplified implementation."""
        _LOGGER.info("🔧 RX11 async_setup called")
        return True
    
    def set_telegram_callback(self, callback: Callable):
        """Set telegram callback - simplified implementation."""
        _LOGGER.info("📡 Telegram callback set")
        # Store callback for future use
        self._telegram_callback = callback
    
    async def register_device(self, serial_number: str, device_info: Dict[str, Any]) -> bool:
        """Backward compatibility alias for rx11_system_register_device."""
        return await self.rx11_system_register_device(serial_number, device_info)
    
    async def unregister_device(self, serial_number: str) -> bool:
        """Backward compatibility alias for rx11_system_unregister_device."""
        return await self.rx11_system_unregister_device(serial_number)

    async def rx11_system_register_device(self, serial_number: str, device_info: Dict[str, Any]) -> bool:
        """Register device with RX11 system using structured naming convention."""
        _LOGGER.info("📝 RX11: Device registered: %s", serial_number[-8:])
        return True
    
    async def rx11_system_unregister_device(self, serial_number: str) -> bool:
        """Unregister device from RX11 system using structured naming convention."""
        _LOGGER.info("🗑️ RX11: Device unregistered: %s", serial_number[-8:])
        return True
    
    async def rx11_system_send_command(self, command: bytes) -> bool:
        """Send command using RX11 system with cached receiver serial numbers.
        
        Args:
            command: Raw command bytes to send
            
        Returns:
            bool: True if command was sent successfully
        """
        if not self._lib or not self._connected:
            _LOGGER.error("❌ RX11 not connected - cannot send command")
            return False
            
        try:
            # Check for serial bus errors before attempting command
            if not await self._check_for_serial_bus_errors():
                _LOGGER.error("❌ RX11 auto-reconnect failed, cannot send command")
                return False
            
            # Ensure command is bytes
            if isinstance(command, str):
                if all(c in '0123456789ABCDEFabcdef' for c in command.replace(' ', '')):
                    command = bytes.fromhex(command.replace(' ', ''))
                else:
                    command = command.encode('utf-8')
            
            # Fast receiver lookup from cache
            receiver_serial = None
            used_index = None
            
            # Check used receivers first (optimized)
            for index, serial in self._used_receivers.items():
                if self._is_cache_valid(index):
                    receiver_serial = serial
                    used_index = index
                    break
            
            # If no used receiver found, try to get first available receiver
            if not receiver_serial:
                # Try indices 0-4 with lazy loading
                for index in range(5):
                    receiver_serial = await self._ensure_receiver_cached(index)
                    if receiver_serial and receiver_serial != "00" * 16:
                        used_index = index
                        break
                        
                if not receiver_serial:
                    _LOGGER.error("❌ No valid receivers found")
                    return False
            
            # Convert receiver serial to gateway bytes
            gateway_bytes = bytes.fromhex(receiver_serial)
            gateway_array = (ctypes.c_ubyte * 16)()
            for i in range(16):
                gateway_array[i] = gateway_bytes[i]
            
            # Extract button from first byte of command
            button = command[0] if len(command) > 0 else 0x10
            
            # Use EwSendCmdRequest with actual receiver gateway and button with error handling
            result = self._lib.EwSendCmdRequest(
                gateway_array,                # Actual receiver gateway
                ctypes.c_ubyte(button)        # Button code
            )
            
            if result == SUCCESS:
                # Reset error counters on successful operation
                self._reset_error_counters()
                return True
            else:
                # Handle potential serial bus error
                self._handle_serial_bus_error(f"EwSendCmdRequest failed with error: {result}")
                _LOGGER.warning("⚠️ Failed to send direct command, error: %d", result)
                return False
                
        except Exception as e:
            # Handle potential serial bus error and duplicated handle errors
            error_msg = str(e)
            if (SERIAL_BUS_ERROR_PATTERN in error_msg or 
                DUPLICATED_HANDLE_ERROR_PATTERN in error_msg):
                self._handle_serial_bus_error(error_msg)
            
            _LOGGER.error("❌ Error sending direct command: %s", e)
            return False

    async def rx11_ew_receiver_send_command(self, serial_number: str, command: bytes) -> bool:
        """Send command to specific EW receiver using optimized cached index lookup."""
        if not self._lib or not self._connected:
            _LOGGER.error("❌ Cannot send command - RX11 not connected")
            return False
            
        try:
            # Check for serial bus errors before attempting command
            if not await self._check_for_serial_bus_errors():
                _LOGGER.error("❌ Auto-reconnect failed, cannot send command to receiver")
                return False
            
            # Extract button from command
            button = command[0] if len(command) > 0 else 0x10
            
            # Step 1: Optimized cache lookup - check device-specific cache first
            short_serial = serial_number[-8:].upper()
            receiver_index = None
            receiver_serial = None
            
            # Check if we have a cached mapping for this specific device
            cached_mapping = getattr(self, '_device_index_cache', {})
            if short_serial in cached_mapping:
                cached_index, cached_serial, cached_time = cached_mapping[short_serial]
                # Use cached mapping if less than 60 seconds old
                if time.time() - cached_time < 60:
                    receiver_index = cached_index
                    receiver_serial = cached_serial
                    _LOGGER.debug("🎯 Using cached mapping for %s: index %d", short_serial, receiver_index)
            
            # Step 2: If no valid cache, do efficient search
            if receiver_index is None:
                # Initialize cache if needed
                if not hasattr(self, '_device_index_cache'):
                    self._device_index_cache = {}
                
                # Search efficiently with prioritized indices
                search_indices = list(self._used_receivers.keys()) + [i for i in range(10) if i not in self._used_receivers]
                
                for index in search_indices[:10]:  # Limit to first 10 for performance
                    cached_serial = await self._ensure_receiver_cached(index)
                    if cached_serial and cached_serial[-8:].upper() == short_serial:
                        receiver_index = index
                        receiver_serial = cached_serial
                        
                        # Cache this mapping for future use
                        self._device_index_cache[short_serial] = (index, cached_serial, time.time())
                        _LOGGER.debug("📌 Cached new mapping for %s: index %d", short_serial, index)
                        break
                        
            if receiver_index is None or not receiver_serial:
                _LOGGER.error("❌ Could not find receiver %s in any index", short_serial)
                return False
            
            # Step 3: Convert receiver serial to gateway format for EwSendCmdRequest
            gateway_bytes = bytes.fromhex(receiver_serial)
            gateway_array = (ctypes.c_ubyte * 16)()
            for i in range(16):
                gateway_array[i] = gateway_bytes[i]
            
            # Use EwSendCmdRequest to send command to receiver with error handling
            result = self._lib.EwSendCmdRequest(
                gateway_array,                # Gateway array with receiver serial
                ctypes.c_ubyte(button)        # Button code
            )
            
            if result == SUCCESS:
                # Reset error counters on successful operation
                self._reset_error_counters()
                _LOGGER.debug("✅ Command sent successfully to receiver %s (index %d, button: 0x%02X)", 
                           short_serial, receiver_index, button)
                return True
            else:
                # Handle potential serial bus error
                self._handle_serial_bus_error(f"EwSendCmdRequest failed with error: {result}")
                _LOGGER.warning("⚠️ Failed to send command to receiver %s, error: %d", 
                               short_serial, result)
                return False
                
        except Exception as e:
            # Handle potential serial bus error and duplicated handle errors
            error_msg = str(e)
            if (SERIAL_BUS_ERROR_PATTERN in error_msg or 
                DUPLICATED_HANDLE_ERROR_PATTERN in error_msg):
                self._handle_serial_bus_error(error_msg)
            
            _LOGGER.error("❌ Error sending command to receiver %s: %s", serial_number[-8:], e)
            return False

    def set_telegram_callback(self, callback: Callable[[int, bytes, bytes], None]) -> None:
        """Set callback for received telegrams."""
        self._telegram_callback = callback
        _LOGGER.debug("Telegram callback set")

    # =============================================================================
    # RX11 EWB SENSOR OPERATIONS
    # =============================================================================

    async def rx11_ewb_sensor_start_receive_loop(self) -> None:
        """Startet die kontinuierliche EWB Receive-Loop für EWneo-Sensoren, EW-Transmitter und EWneo-Receiver."""
        _LOGGER.debug("🔍 rx11_ewb_sensor_start_receive_loop() aufgerufen")
        
        if self._ewb_receive_task and not self._ewb_receive_task.done():
            _LOGGER.debug("EWB Receive-Loop läuft bereits")
            return
        
        _LOGGER.debug("🔧 Bereite EWB Receive-Loop vor...")
        self._stop_ewb_receive = False
        
        try:
            self._ewb_receive_task = asyncio.create_task(self.rx11_ewb_sensor_continuous_receive_loop())
            _LOGGER.info("🚀 Kontinuierliche EWB Receive-Loop für EWneo-Sensoren, EW-Transmitter und EWneo-Receiver gestartet")
            
            # Warte kurz, um zu sehen, ob die Task sofort fehlschlägt
            await asyncio.sleep(0.1)
            
            if self._ewb_receive_task.done():
                try:
                    await self._ewb_receive_task  # Dies wird eine Exception werfen, falls die Task fehlgeschlagen ist
                except Exception as task_error:
                    _LOGGER.error("❌ EWB Receive-Loop Task ist sofort fehlgeschlagen: %s", task_error)
                    raise
            else:
                _LOGGER.debug("✅ EWB Receive-Loop Task läuft erfolgreich")
                
        except Exception as e:
            _LOGGER.error("❌ Fehler beim Erstellen der EWB Receive-Loop Task: %s", e)
            self._ewb_receive_task = None
            raise

    async def rx11_ewb_sensor_stop_receive_loop(self) -> None:
        """Stoppt die kontinuierliche EWB Receive-Loop."""
        if not self._ewb_receive_task:
            return
        
        self._stop_ewb_receive = True
        
        try:
            # Warte auf das Ende der Loop mit Timeout
            await asyncio.wait_for(self._ewb_receive_task, timeout=2.0)
            _LOGGER.info("🛑 EWB Receive-Loop erfolgreich gestoppt")
        except asyncio.TimeoutError:
            # Erzwinge das Beenden der Task
            self._ewb_receive_task.cancel()
            try:
                await self._ewb_receive_task
            except asyncio.CancelledError:
                pass
            _LOGGER.warning("⚠️ EWB Receive-Loop musste zwangsweise beendet werden")
        except Exception as e:
            _LOGGER.error("❌ Fehler beim Stoppen der EWB Receive-Loop: %s", e)
        
        self._ewb_receive_task = None

    async def rx11_ewb_sensor_continuous_receive_loop(self) -> None:
        """Kontinuierliche Schleife für EwbReceiveRequest - empfängt EWneo-Sensoren, EW-Transmitter und EWneo-Receiver Nachrichten.
        
        Diese Schleife läuft dauerhaft und wartet nach jedem Request auf die Antwort,
        bevor der nächste Request gestartet wird. Das verhindert ERR_SUPERSEDED Fehler.
        """
        _LOGGER.info("📡 EWB Receive-Loop gestartet - wartet auf EWneo-Sensoren, EW-Transmitter und EWneo-Receiver")
        _LOGGER.debug("🔍 rx11_ewb_sensor_continuous_receive_loop() Funktion gestartet")
        
        # Prüfe Voraussetzungen
        if not self._lib or not hasattr(self._lib, 'EwbRcvRequest'):
            _LOGGER.error("❌ C-Library oder EwbRcvRequest nicht verfügbar")
            return
            
        consecutive_errors = 0
        max_consecutive_errors = 5
        loop_iteration = 0
        
        while not self._stop_ewb_receive and self._connected:
            loop_iteration += 1
            # Reduziertes Logging - nur bei Fehlern oder alle 100 Iterationen
            if loop_iteration % 100 == 0:
                _LOGGER.debug("🔄 EWB Receive-Loop Iteration %d", loop_iteration)
            
            try:
                if not self._lib:
                    _LOGGER.error("C-Library nicht verfügbar in EWB Receive-Loop")
                    break
                
                # Prepare output parameters für EwbRcvRequest
                info_type = ctypes.c_uint8(0)
                receiver_transmitter = (ctypes.c_uint8 * 16)()
                info_data = (ctypes.c_uint8 * 8)()
                
                # Führe EwbRcvRequest aus - wartet auf Antwort
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._lib.EwbRcvRequest(
                        ctypes.byref(info_type), 
                        receiver_transmitter, 
                        info_data
                    )
                )
                
                # Prüfe Ergebnis
                if result == SUCCESS:
                    # Erfolgreich Nachricht empfangen
                    consecutive_errors = 0
                    
                    serial_number = bytes(receiver_transmitter).hex().upper()
                    
                    telegram_info = {
                        "info_type": info_type.value,
                        "serial_number": serial_number,
                        "device_id": bytes(receiver_transmitter),
                        "info_data": bytes(info_data),
                        "source": "continuous_ewb_loop",
                        "iteration": loop_iteration
                    }
                    
                    _LOGGER.info("📨 EWB Nachricht empfangen (Iteration %d): InfoType=%d, Serial=%s", 
                               loop_iteration, info_type.value, serial_number)
                    
                    # Rufe Callback auf, falls gesetzt
                    if self._telegram_callback:
                        try:
                            self._telegram_callback(info_type.value, bytes(receiver_transmitter), bytes(info_data))
                        except Exception as cb_error:
                            _LOGGER.error("❌ Fehler in Telegram-Callback (Iteration %d): %s", loop_iteration, cb_error)
                    
                    # Kurze Pause nach erfolgreichem Empfang, um System nicht zu überlasten
                    await asyncio.sleep(0.01)
                    
                elif result == ERR_RF_TIMEOUT:
                    # Normaler Timeout - kein Fehler, sofort weiter versuchen
                    consecutive_errors = 0
                    _LOGGER.debug("⏱️ EwbRcvRequest Timeout (Iteration %d) - warte weiter auf Nachrichten", loop_iteration)
                    
                elif result == ERR_SUPERSEDED:
                    # Request wurde durch neuen Request ersetzt - sollte nicht mehr auftreten
                    consecutive_errors = 0
                    _LOGGER.debug("🔄 EwbRcvRequest ersetzt (Iteration %d) - Request wurde aktualisiert", loop_iteration)
                    
                elif result == ERR_CANCELED:
                    # Request wurde abgebrochen (z.B. beim Shutdown)
                    _LOGGER.debug("🛑 EwbRcvRequest abgebrochen (Iteration %d)", loop_iteration)
                    break
                    
                else:
                    # Unerwarteter Fehler
                    consecutive_errors += 1
                    _LOGGER.warning("⚠️ EwbRcvRequest Fehler (Iteration %d, Code: %d), Fehler %d/%d", 
                                   loop_iteration, result, consecutive_errors, max_consecutive_errors)
                    
                    if consecutive_errors >= max_consecutive_errors:
                        _LOGGER.error("❌ Zu viele aufeinanderfolgende Fehler in EWB Receive-Loop (Iteration %d) - breche ab", loop_iteration)
                        break
                    
                    # Kurze Pause bei Fehlern
                    await asyncio.sleep(0.5)
                
                # WICHTIG: Jeder EwbRcvRequest wartet bereits bis die Antwort kommt
                # Daher keine zusätzliche Pause oder sofortiger neuer Request nötig
                _LOGGER.debug("➡️ Request abgeschlossen, starte nächste Iteration %d", loop_iteration + 1)
                
            except asyncio.CancelledError:
                _LOGGER.debug("🛑 EWB Receive-Loop wurde abgebrochen (Iteration %d)", loop_iteration)
                break
                break
                
            except Exception as e:
                consecutive_errors += 1
                _LOGGER.error("❌ Unerwarteter Fehler in EWB Receive-Loop (Iteration %d, %d/%d): %s", 
                             loop_iteration, consecutive_errors, max_consecutive_errors, e)
                _LOGGER.exception("Vollständige Exception Details:")
                
                if consecutive_errors >= max_consecutive_errors:
                    _LOGGER.error("❌ Zu viele aufeinanderfolgende Fehler - stoppe EWB Receive-Loop (nach Iteration %d)", loop_iteration)
                    break
                
                # Pause bei unerwarteten Fehlern
                await asyncio.sleep(1.0)
        
        _LOGGER.info("🏁 EWB Receive-Loop beendet (nach %d Iterationen)", loop_iteration)
    
    @property
    def transceiver_type(self) -> str:
        """Get transceiver type."""
        return "RX11"
    
    # =============================================================================
    # RX11 EWB (EASYWAVE BIDI) OPERATIONS
    # =============================================================================

    async def rx11_ewb_get_serial_by_index(self, index: int) -> Optional[str]:
        """Get EWB gateway serial number by index using EwbGetFdSerialRequest."""
        if not self._lib or not self._connected:
            return None
            
        try:
            serial_array = (ctypes.c_ubyte * 16)()
            result = self._lib.EwbGetFdSerialRequest(
                ctypes.c_uint16(index),
                ctypes.byref(serial_array)
            )
            
            if result == SUCCESS:
                # Convert to hex string
                serial_hex = ''.join([f'{b:02X}' for b in serial_array])
                _LOGGER.debug("✅ Retrieved EWB serial at index %d: %s", index, serial_hex)
                return serial_hex
            else:
                _LOGGER.debug("No EWB serial at index %d, error: %d", index, result)
                return None
                
        except Exception as e:
            _LOGGER.error("Error getting EWB serial by index %d: %s", index, e)
            return None

    async def rx11_ewb_add_filter(self, gateway_serial: str) -> bool:
        """Add EWB serial to receive filter using EwbAddNFilterRequest."""
        if not self._lib or not self._connected:
            return False
            
        try:
            # Convert hex string to bytes array
            gateway_bytes = bytes.fromhex(gateway_serial)
            if len(gateway_bytes) != 16:
                _LOGGER.error("Invalid gateway serial length: %d (expected 16)", len(gateway_bytes))
                return False
                
            gateway_array = (ctypes.c_ubyte * 16)()
            for i in range(16):
                gateway_array[i] = gateway_bytes[i]
            
            result = self._lib.EwbAddNFilterRequest(gateway_array)
            
            if result == SUCCESS:
                _LOGGER.info("✅ Added EWB gateway %s to receive filter", gateway_serial[-8:])
                return True
            else:
                _LOGGER.error("Failed to add EWB filter for %s: error %d", gateway_serial[-8:], result)
                return False
                
        except Exception as e:
            _LOGGER.error("Error adding EWB filter for %s: %s", gateway_serial[-8:], e)
            return False

    async def rx11_ewb_join_device(self, gateway_serial: str) -> Optional[tuple[int, str]]:
        """Join EWB device using EwbJoinDeviceRequest. Returns (device_type, receiver_serial)."""
        if not self._lib or not self._connected:
            return None
            
        try:
            # Convert hex string to bytes array
            gateway_bytes = bytes.fromhex(gateway_serial)
            if len(gateway_bytes) != 16:
                _LOGGER.error("Invalid gateway serial length: %d (expected 16)", len(gateway_bytes))
                return None
                
            gateway_array = (ctypes.c_ubyte * 16)()
            for i in range(16):
                gateway_array[i] = gateway_bytes[i]
            
            device_type = ctypes.c_ubyte()
            receiver_array = (ctypes.c_ubyte * 16)()
            
            _LOGGER.info("🔗 Starting EWB device join for gateway %s...", gateway_serial[-8:])
            result = self._lib.EwbJoinDeviceRequest(
                gateway_array,
                ctypes.byref(device_type),
                ctypes.byref(receiver_array)
            )
            
            if result == SUCCESS:
                # Convert receiver serial to hex string
                receiver_serial = ''.join([f'{b:02X}' for b in receiver_array])
                _LOGGER.info("✅ EWB device joined successfully - Type: 0x%02X, Serial: %s", 
                           device_type.value, receiver_serial[-8:])
                return (device_type.value, receiver_serial)
            else:
                _LOGGER.error("Failed to join EWB device for gateway %s: error %d", gateway_serial[-8:], result)
                return None
                
        except Exception as e:
            _LOGGER.error("Error joining EWB device for gateway %s: %s", gateway_serial[-8:], e)
            return None

    async def rx11_ewb_remove_device(self, gateway_serial: str, receiver_serial: str) -> bool:
        """Remove EWB device using EwbRemoveDeviceRequest. Returns success status."""
        if not self._lib or not self._connected:
            return False
            
        try:
            # Convert hex strings to bytes arrays
            gateway_bytes = bytes.fromhex(gateway_serial)
            receiver_bytes = bytes.fromhex(receiver_serial)
            
            if len(gateway_bytes) != 16 or len(receiver_bytes) != 16:
                _LOGGER.error("Invalid serial length - Gateway: %d, Receiver: %d (expected 16)", 
                             len(gateway_bytes), len(receiver_bytes))
                return False
                
            gateway_array = (ctypes.c_ubyte * 16)()
            receiver_array = (ctypes.c_ubyte * 16)()
            
            for i in range(16):
                gateway_array[i] = gateway_bytes[i]
                receiver_array[i] = receiver_bytes[i]
            
            _LOGGER.info("🗑️ Removing EWB device - Gateway: %s, Receiver: %s...", 
                        gateway_serial[-8:], receiver_serial[-8:])
            
            result = self._lib.EwbRemoveDeviceRequest(
                gateway_array,
                receiver_array
            )
            
            if result == SUCCESS:
                _LOGGER.info("✅ EWB device removed successfully - Gateway: %s, Receiver: %s", 
                           gateway_serial[-8:], receiver_serial[-8:])
                return True
            else:
                _LOGGER.warning("Failed to remove EWB device - Gateway: %s, Receiver: %s: error %d", 
                               gateway_serial[-8:], receiver_serial[-8:], result)
                return False
                
        except Exception as e:
            _LOGGER.error("Error removing EWB device - Gateway: %s, Receiver: %s: %s", 
                         gateway_serial[-8:], receiver_serial[-8:], e)
            return False

    async def rx11_ewb_query_state(self, gateway_serial: str, receiver_serial: str, mode: int = 0) -> Optional[tuple[int, list]]:
        """Query EWB device state using EwbQueryStateRequest. Returns (mode, state_bytes)."""
        if not self._lib or not self._connected:
            return None
            
        try:
            # Convert hex strings to bytes arrays
            gateway_bytes = bytes.fromhex(gateway_serial)
            receiver_bytes = bytes.fromhex(receiver_serial)
            
            if len(gateway_bytes) != 16 or len(receiver_bytes) != 16:
                _LOGGER.error("Invalid serial length - Gateway: %d, Receiver: %d (expected 16)", 
                             len(gateway_bytes), len(receiver_bytes))
                return None
                
            gateway_array = (ctypes.c_ubyte * 16)()
            receiver_array = (ctypes.c_ubyte * 16)()
            recent_mode = ctypes.c_ubyte()
            state_array = (ctypes.c_ubyte * 4)()
            
            for i in range(16):
                gateway_array[i] = gateway_bytes[i]
                receiver_array[i] = receiver_bytes[i]
            
            _LOGGER.debug("🔍 Querying EWB device state - Gateway: %s, Receiver: %s, Mode: %d...", 
                        gateway_serial[-8:], receiver_serial[-8:], mode)
            
            result = self._lib.EwbQueryStateRequest(
                gateway_array,
                receiver_array,
                ctypes.c_ubyte(mode),
                ctypes.byref(recent_mode),
                ctypes.byref(state_array)
            )
            
            if result == SUCCESS:
                # Convert state array to list
                state_bytes = [state_array[i] for i in range(4)]
                _LOGGER.info("✅ EWB device state queried successfully - Gateway: %s, Receiver: %s, Mode: %d, State: %s", 
                           gateway_serial[-8:], receiver_serial[-8:], recent_mode.value, 
                           " ".join([f"{b:02X}" for b in state_bytes]))
                return (recent_mode.value, state_bytes)
            else:
                _LOGGER.warning("Failed to query EWB device state - Gateway: %s, Receiver: %s: error %d", 
                               gateway_serial[-8:], receiver_serial[-8:], result)
                return None
                
        except Exception as e:
            _LOGGER.error("Error querying EWB device state - Gateway: %s, Receiver: %s: %s", 
                         gateway_serial[-8:], receiver_serial[-8:], e)
            return None

    async def rx11_ewb_change_state(self, gateway_serial: str, receiver_serial: str, 
                                   desired_mode: int, desired_state: list) -> Optional[tuple[int, list]]:
        """Change EWB device state using EwbChangeStateRequest. Returns (recent_mode, recent_state)."""
        if not self._lib or not self._connected:
            return None
            
        try:
            # Convert hex strings to bytes arrays
            gateway_bytes = bytes.fromhex(gateway_serial)
            receiver_bytes = bytes.fromhex(receiver_serial)
            
            if len(gateway_bytes) != 16 or len(receiver_bytes) != 16:
                _LOGGER.error("Invalid serial length - Gateway: %d, Receiver: %d (expected 16)", 
                             len(gateway_bytes), len(receiver_bytes))
                return None
                
            if len(desired_state) != 4:
                _LOGGER.error("Invalid desired state length: %d (expected 4)", len(desired_state))
                return None
                
            gateway_array = (ctypes.c_ubyte * 16)()
            receiver_array = (ctypes.c_ubyte * 16)()
            desired_state_array = (ctypes.c_ubyte * 4)()
            recent_mode = ctypes.c_ubyte()
            recent_state_array = (ctypes.c_ubyte * 4)()
            
            for i in range(16):
                gateway_array[i] = gateway_bytes[i]
                receiver_array[i] = receiver_bytes[i]
            
            for i in range(4):
                desired_state_array[i] = desired_state[i]
            
            _LOGGER.info("🔄 Changing EWB device state - Gateway: %s, Receiver: %s, Mode: %d, State: %s...", 
                        gateway_serial[-8:], receiver_serial[-8:], desired_mode,
                        " ".join([f"{b:02X}" for b in desired_state]))
            
            result = self._lib.EwbChangeStateRequest(
                gateway_array,
                receiver_array,
                ctypes.c_ubyte(desired_mode),
                ctypes.byref(desired_state_array),
                ctypes.byref(recent_mode),
                ctypes.byref(recent_state_array)
            )
            
            if result == SUCCESS:
                # Convert recent state array to list
                recent_state_bytes = [recent_state_array[i] for i in range(4)]
                _LOGGER.info("✅ EWB device state changed successfully - Gateway: %s, Receiver: %s, Recent Mode: %d, Recent State: %s", 
                           gateway_serial[-8:], receiver_serial[-8:], recent_mode.value,
                           " ".join([f"{b:02X}" for b in recent_state_bytes]))
                return (recent_mode.value, recent_state_bytes)
            else:
                _LOGGER.error("Failed to change EWB device state - Gateway: %s, Receiver: %s: error %d", 
                             gateway_serial[-8:], receiver_serial[-8:], result)
                return None
                
        except Exception as e:
            _LOGGER.error("Error changing EWB device state - Gateway: %s, Receiver: %s: %s", 
                         gateway_serial[-8:], receiver_serial[-8:], e)
            return None

    async def rx11_ewb_get_next_available_index(self) -> Optional[int]:
        """Get the next available EWB index by scanning for indices with gateway serials.
        
        Note: This is the legacy method. An index with a gateway serial is AVAILABLE for EWneo devices,
        not 'used'. The new coordinator-based method is preferred.
        """
        if not self._lib or not self._connected:
            return None
            
        _LOGGER.debug("🔍 Searching for next available EWB index (legacy method)...")
        
        # Scan indices 0-255 to find the first one with a valid gateway serial
        for index in range(256):
            serial = await self.rx11_ewb_get_serial_by_index(index)
            if serial and serial != "00" * 16:  # Valid gateway serial found - this is what we want!
                _LOGGER.info("✨ Found available EWB index: %d with gateway serial: %s", index, serial[-8:])
                return index
                
        _LOGGER.warning("⚠️ No EWB indices with gateway serials found")
        return None

    async def rx11_ewb_get_next_available_index_single(self) -> Optional[int]:
        """Get the next available EWB index using coordinator's persistent tracking.
        
        This method uses the coordinator's persistent index tracking to get the next free index
        and returns the index along with its gateway serial for EWneo device setup.
        """
        if not self._lib or not self._connected:
            return None
            
        _LOGGER.debug("🔍 Getting next available EWB index from coordinator tracking...")
        
        # Get the coordinator if available
        coordinator = getattr(self, '_coordinator', None)
        if not coordinator:
            # Fallback to limited search if no coordinator
            return await self._fallback_ewb_index_search()
        
        try:
            # Get next free index from coordinator's persistent tracking
            next_free_index = coordinator.get_next_free_ewb_index()
            
            # Get the gateway serial for this index - this is what we need!
            _LOGGER.debug("🔍 Getting gateway serial for EWB index %d...", next_free_index)
            serial = await self.rx11_ewb_get_serial_by_index(next_free_index)
            
            if not serial or serial == "00" * 16:
                # Empty index - this shouldn't happen if the index tracking is correct
                # Try the next available index
                _LOGGER.debug("🔄 Index %d has no gateway serial, trying next index...", next_free_index)
                
                # Find next index with a valid gateway serial
                for test_index in range(next_free_index, min(next_free_index + 10, 256)):
                    test_serial = await self.rx11_ewb_get_serial_by_index(test_index)
                    if test_serial and test_serial != "00" * 16:
                        _LOGGER.info("✨ Found EWB index %d with gateway serial: %s", test_index, test_serial[-8:])
                        return test_index
                
                # If no valid gateway serial found, fall back
                return await self._fallback_ewb_index_search()
            else:
                # Found valid gateway serial - this is what we want!
                _LOGGER.info("✨ Found available EWB index %d with gateway serial: %s", next_free_index, serial[-8:])
                return next_free_index
                
        except Exception as e:
            _LOGGER.error("Error getting EWB index from coordinator: %s", e)
            return await self._fallback_ewb_index_search()
    
    async def _fallback_ewb_index_search(self) -> Optional[int]:
        """Fallback method for EWB index search when coordinator is not available.
        
        Looks for the first index that has a valid gateway serial (not 00000000...).
        """
        _LOGGER.debug("🔍 Fallback: Limited EWB index search...")
        
        # Check first 32 indices for one with a valid gateway serial
        for index in range(32):
            try:
                serial = await self.rx11_ewb_get_serial_by_index(index)
                if serial and serial != "00" * 16:  # Found valid gateway serial
                    _LOGGER.info("✨ Found available EWB index (fallback): %d with gateway serial: %s", 
                               index, serial[-8:])
                    return index
            except Exception as e:
                _LOGGER.debug("Error checking EWB index %d: %s", index, e)
                continue
                
        _LOGGER.warning("⚠️ No available EWB indices with valid gateway serials found")
        return None
    
    def set_coordinator(self, coordinator) -> None:
        """Set reference to coordinator for persistent EWB index tracking."""
        self._coordinator = coordinator
        _LOGGER.debug("📋 Set coordinator reference for EWB index tracking")

    async def rx11_ewb_receive_telegram(self) -> Optional[dict]:
        """Receive EWB telegram using EwbRcvRequest for state updates."""
        if not self._lib or not self._connected:
            return None
            
        def _ewb_receive():
            """Synchronous wrapper for EwbRcvRequest."""
            try:
                info_type = ctypes.c_ubyte()
                receiver_transmitter = (ctypes.c_ubyte * 16)()
                info_data = (ctypes.c_ubyte * 8)()
                
                result = self._lib.EwbRcvRequest(
                    ctypes.byref(info_type),
                    ctypes.byref(receiver_transmitter),
                    ctypes.byref(info_data)
                )
                
                if result == SUCCESS:
                    # Convert to readable format
                    serial_hex = ''.join([f'{b:02X}' for b in receiver_transmitter])
                    info_data_hex = [f'{b:02X}' for b in info_data]
                    
                    telegram = {
                        'info_type': info_type.value,
                        'serial_number': serial_hex,
                        'info_data': info_data_hex,
                        'raw_info_data': list(info_data),
                        'timestamp': time.time()
                    }
                    
                    _LOGGER.debug("📡 Received EWB telegram: Type=%d, Serial=%s, Data=%s", 
                                info_type.value, serial_hex[-8:], info_data_hex[:4])
                    return telegram
                else:
                    # Don't log timeouts or superseded requests as errors
                    if result not in [ERR_RF_TIMEOUT, ERR_SUPERSEDED]:
                        _LOGGER.warning("Failed to receive EWB telegram: error code %d", result)
                    return None
                    
            except Exception as e:
                _LOGGER.error("Error receiving EWB telegram: %s", e)
                return None
        
        # Run the blocking C call in a thread pool
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _ewb_receive)

    # Backward compatibility alias for send_command
    async def send_command(self, command: bytes) -> bool:
        """Backward compatibility alias for rx11_system_send_command."""
        return await self.rx11_system_send_command(command)
    
    # Backward compatibility alias for get_next_available_receiver  
    async def get_next_available_receiver(self) -> Optional[tuple[int, str]]:
        """Backward compatibility alias for rx11_ew_receiver_get_next_available."""
        return await self.rx11_ew_receiver_get_next_available()