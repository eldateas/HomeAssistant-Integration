"""
Python implementation of ELDAT RX11 RxModule - © ELDAT EaS GmbH 2024
Converted from C to Python by GitHub Copilot

This module provides a Python implementation of the RxModule communication
protocol for the ELDAT RX11 transceiver.

Filename:       rx_module.py
Original:       RxModule.c by L.Koepping
Revised:        2024-12-18
Revision:       v2.0.0 (Python port)
"""

from __future__ import annotations

import asyncio
import logging
import struct
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Optional

import serial

_LOGGER = logging.getLogger(__name__)


# ====================================================================================================
# PROTOCOL CONSTANTS - SOP, EOP, Byte Stuffing
# ====================================================================================================

PREFIX = 0x80  # Prefix for byte stuffing
SOP = 0x81  # Start-of-packet
EOP = 0x82  # End-of-packet

STUFFING_MIN = 0x80  # Start of range which is stuffed
STUFFING_MAX = 0x82  # End of range which is stuffed
STUFFING_ADDEND = 0xFF & (-0x80)  # Addend to get the replacement (0x80)


# ====================================================================================================
# FUNCTION CODES
# ====================================================================================================

class FunctionCode(IntEnum):
    """Function codes for different IRP operations."""
    EW_RCV_BUTTON = 0x01
    EW_SEND_CMD = 0x02
    EW_RCV_EX = 0x03
    EWB_JOIN_DEVICE = 0x04
    EWB_REMOVE_DEVICE = 0x05
    EWB_CLEAR_NFILTER = 0x06
    EWB_ADD_NFILTER = 0x07
    EWB_RCV = 0x08
    EWB_CHANGE_STATE = 0x09
    EWB_QUERY_STATE = 0x0A
    EWB_TRLRN_CONTROL = 0x0B
    EW_GET_FD_SERIAL = 0x20
    EWB_GET_FD_SERIAL = 0x21
    TR_ADD_NFILTER = 0x30
    TR_LEARN_U = 0x31
    TR_LEARN = 0x32
    TR_CHANGE_STATE_U = 0x33
    TR_CHANGE_STATE = 0x34
    TR_QUERY_STATE = 0x35
    TR_RCV = 0x36
    SEC_RCV = 0xA1
    SEC_LEARN = 0xA2
    SEC_REPLY_QUERY = 0xA3
    SEC_DELETE = 0xA4
    SEC_STAT = 0xA5
    SEC_WR_USERDATA = 0xA6
    SEC_SEND_CMD_TEL = 0xA7
    SEC_SEND_LRN_TEL = 0xA8
    SEC_DELETE_ALL = 0xA9
    MA_QUERY_HW_VER = 0xC0
    MA_QUERY_FW_VER = 0xC1
    MA_UPDATE_FW = 0xC2
    CANCEL_IO = 0xFE
    CANCEL_ALL_IO = 0xFF
    PING_RCV = 0xF0


# Continuous receive function codes - these wait indefinitely for incoming telegrams
# and should not be considered "stuck" in health checks
CONTINUOUS_RCV_FUNCTIONS: frozenset[int] = frozenset({
    FunctionCode.EW_RCV_BUTTON,  # EasyWave button receive
    FunctionCode.EW_RCV_EX,      # EasyWave extended receive
    FunctionCode.EWB_RCV,        # EasyWave Neo receive
    FunctionCode.TR_RCV,         # Transceiver receive
    FunctionCode.SEC_RCV,        # Security receive
})


# ====================================================================================================
# ERROR CODES
# ====================================================================================================

class ErrorCode(IntEnum):
    """Error codes returned by operations."""
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


# ====================================================================================================
# INFO TYPES
# ====================================================================================================

class InfoType(IntEnum):
    """Info types for received telegrams."""
    TM_IT_EASW_RELEASE = 0x00  # Easywave transmitter; button release
    TM_IT_EASW_PUSH = 0x01  # Easywave transmitter; button push and hold
    TM_IT_SENSOR_DATA = 0x02  # Sensor data message
    TM_IT_EWBIDI_STATE = 0x03  # Easywave Bidi receiver state change
    TM_IT_EWBIDI_ABORT = 0x40  # Easywave Bidi notification about an aborted learn or removal
    TM_IT_EWBIDI_ADD_TR = 0x41  # Easywave Bidi notification about a learned transmitter
    TM_IT_EWBIDI_RMV_TR = 0x42  # Easywave Bidi notification about a removed transmitter
    TM_IT_EWBIDI_LN_T = 0xF0  # Easywave Bidi learn ack for transmitter
    TM_IT_EWBIDI_CHG_T = 0xF1  # Easywave Bidi receiver change state for transmitters
    TM_IT_EWBIDI_QUR_T = 0xF2  # Easywave Bidi receiver query state for transmitters


# ====================================================================================================
# DEVICE TYPES
# ====================================================================================================

class DeviceType(IntEnum):
    """Device types for EWB devices."""
    EMPTY_TYPE = 0x00
    EWB_DT_BIDI_TR = 0x01  # Generic Easywave Bidi transmitter
    EWB_DT_SWITCH = 0x03  # Switch (on/off)
    EWB_DT_DIMMER = 0x04  # Dimmer
    EWB_DT_MOTOR = 0x05  # Motor control
    EWB_DT_DUAL_SWITCH = 0x06  # Dual switch (on/off)
    EWB_DT_QUAD_SWITCH = 0x07  # Quadruple switch (on/off)
    EWB_DT_DUAL_MOTOR = 0x08  # Dual motor control
    EWB_DT_QUAD_MOTOR = 0x09  # Quadruple motor control
    EWB_DT_PART_SWITCH = 0x0A  # Part of a dual/quadruple switch
    EWB_DT_PART_MOTOR = 0x0B  # Part of a dual/quadruple motor


# ====================================================================================================
# BUTTON CONSTANTS
# ====================================================================================================

TM_BUTTON_MASK = 0x03
TM_BUTTON_A = 0
TM_BUTTON_B = 1
TM_BUTTON_C = 2
TM_BUTTON_D = 3

TM_BUTTON_FUNC_MASK = 0xFC
TM_BUTTON_DEFAULT = 0x00
TM_BUTTON_LRN_DEL = 0x04
TM_BUTTON_LRN_ADD = 0x08
TM_BUTTON_LRN_RESET = 0x0C
TM_BUTTON_LRN_TIMER = 0x10
TM_BUTTON_HOLD = 0x14
TM_BUTTON_RELEASE = 0x18
TM_BUTTON_LOWBAT = 0x80


# ====================================================================================================
# REQUEST & COMPLETION STRUCTURES
# ====================================================================================================

@dataclass
class IRP:
    """I/O Request Packet - represents a command to be sent."""
    function: int
    params: bytes = field(default_factory=bytes)


@dataclass
class ICP:
    """I/O Completion Packet - represents a response received."""
    handle: int = 0
    result: int = ErrorCode.ERR_FAILSTATE
    data: bytes = field(default_factory=bytes)


@dataclass
class Request:
    """Represents a pending request with its IRP and expected ICP."""
    irp: IRP
    irp_byte_count: int
    expected_icp_byte_count: int
    req_str: str
    
    # State tracking
    completed: bool = False
    queued: bool = False
    cancel: bool = False
    handle: int = 0
    is_continuous: bool = False  # True for RCV requests that wait for incoming telegrams
    
    # Result
    icp: ICP = field(default_factory=lambda: ICP())
    
    # Synchronization
    event: threading.Event = field(default_factory=threading.Event)
    
    def wait(self, timeout: Optional[float] = None) -> bool:
        """Wait for request completion."""
        return self.event.wait(timeout)
    
    def signal(self):
        """Signal request completion."""
        self.completed = True
        self.event.set()


# ====================================================================================================
# BYTE STUFFING FUNCTIONS
# ====================================================================================================

def apply_byte_stuffing(data: bytes) -> bytes:
    """Apply byte stuffing to data bytes."""
    result = bytearray()
    for byte in data:
        if STUFFING_MIN <= byte <= STUFFING_MAX:
            result.append(PREFIX)
            result.append((byte + STUFFING_ADDEND) & 0xFF)
        else:
            result.append(byte)
    return bytes(result)


def remove_byte_stuffing(data: bytes) -> tuple[bytes, bool]:
    """
    Remove byte stuffing from data.
    
    Returns:
        (unstuffed_data, success)
    """
    result = bytearray()
    i = 0
    while i < len(data):
        byte = data[i]
        if byte == PREFIX:
            if i + 1 >= len(data):
                return bytes(result), False
            next_byte = data[i + 1]
            # Check if next byte is in the stuffed range
            if ((STUFFING_MIN + STUFFING_ADDEND) & 0xFF) <= next_byte <= ((STUFFING_MAX + STUFFING_ADDEND) & 0xFF):
                result.append((next_byte + STUFFING_ADDEND) & 0xFF)
                i += 2
            else:
                return bytes(result), False
        else:
            result.append(byte)
            i += 1
    return bytes(result), True


# ====================================================================================================
# PACKET ENCODING/DECODING
# ====================================================================================================

def encode_irp(irp: IRP) -> bytes:
    """
    Encode an IRP into a byte packet with SOP, function, params, stuffing, and EOP.
    """
    # Start with function code
    buffer = bytearray([irp.function])
    
    # Add parameters
    buffer.extend(irp.params)
    
    # Apply byte stuffing to everything except SOP
    stuffed = apply_byte_stuffing(bytes(buffer))
    
    # Add SOP and EOP
    packet = bytearray([SOP])
    packet.extend(stuffed)
    packet.append(EOP)
    
    return bytes(packet)


def decode_packet(raw_buffer: bytes) -> tuple[Optional[ICP], int, bool]:
    """
    Decode a received packet into an ICP.
    
    Note: raw_buffer is already unstuffed by _process_received_byte()!
    It still contains SOP and EOP markers but byte stuffing has been removed.
    
    Returns:
        (icp, function_code, success)
    """
    # Remove SOP and EOP
    if len(raw_buffer) < 2 or raw_buffer[0] != SOP or raw_buffer[-1] != EOP:
        return None, 0, False
    
    # Payload is already unstuffed - no need to call remove_byte_stuffing()!
    payload = raw_buffer[1:-1]
    
    if len(payload) < 2:
        return None, 0, False
    
    # Parse handle (first 2 bytes)
    handle = (payload[0] << 8) | payload[1]
    
    # Check if this is just an IPP (handle only) or a full ICP
    if len(payload) == 2:
        # IPP - just a handle
        icp = ICP(handle=handle, result=ErrorCode.SUCCESS, data=bytes())
        return icp, 0, True
    
    # Full ICP - handle + result + data
    result = payload[2]
    data = payload[3:] if len(payload) > 3 else bytes()
    
    icp = ICP(handle=handle, result=result, data=data)
    return icp, 0, True


# ====================================================================================================
# IRP PARAMETER ENCODING
# ====================================================================================================

def encode_serial_array(serial: bytes) -> bytes:
    """Encode a 16-byte serial number."""
    if len(serial) != 16:
        raise ValueError(f"Serial must be 16 bytes, got {len(serial)}")
    return serial


def encode_uint16(value: int) -> bytes:
    """Encode a uint16 as big-endian bytes."""
    return struct.pack('>H', value)


def encode_uint32(value: int) -> bytes:
    """Encode a uint32 as big-endian bytes."""
    return struct.pack('>I', value)


def encode_state_array(state: bytes) -> bytes:
    """Encode a 4-byte state array."""
    if len(state) != 4:
        raise ValueError(f"State must be 4 bytes, got {len(state)}")
    return state


# ====================================================================================================
# ICP DATA PARSING
# ====================================================================================================

def parse_icp_data(self, icp: ICP, function: int) -> dict[str, Any]:
    """Parse ICP data based on the function code."""
    data = icp.data
    result = {}
    
    if icp.result != ErrorCode.SUCCESS:
        return result
    
    offset = 0
    
    try:
        if function == FunctionCode.EWB_JOIN_DEVICE:
            result['receiver'] = data[offset:offset+16]
            offset += 16
            result['device_type'] = data[offset]
            
        elif function in (FunctionCode.EW_RCV_BUTTON, FunctionCode.EW_RCV_EX, 
                         FunctionCode.EWB_RCV, FunctionCode.TR_RCV):
            result['info_type'] = data[offset]
            offset += 1
            result['receiver_or_transmitter'] = data[offset:offset+16]
            offset += 16
            
            # Parse InfoData based on InfoType
            info_type = result['info_type']
            if info_type == InfoType.TM_IT_EASW_PUSH:
                result['info_data'] = data[offset:offset+1]
            elif info_type == InfoType.TM_IT_SENSOR_DATA:
                result['info_data'] = data[offset:offset+8]
            elif info_type == InfoType.TM_IT_EWBIDI_STATE:
                result['info_data'] = data[offset:offset+5]
            else:
                result['info_data'] = bytes(8)  # Zeroed
                
        elif function in (FunctionCode.EWB_CHANGE_STATE, FunctionCode.EWB_QUERY_STATE,
                         FunctionCode.EWB_TRLRN_CONTROL, FunctionCode.TR_LEARN_U,
                         FunctionCode.TR_LEARN, FunctionCode.TR_CHANGE_STATE_U,
                         FunctionCode.TR_CHANGE_STATE, FunctionCode.TR_QUERY_STATE):
            result['mode'] = data[offset]
            offset += 1
            result['state'] = data[offset:offset+4]
            
        elif function in (FunctionCode.EW_GET_FD_SERIAL, FunctionCode.EWB_GET_FD_SERIAL):
            result['gateway'] = data[offset:offset+16]
            
        elif function in (FunctionCode.SEC_RCV, FunctionCode.SEC_LEARN):
            result['stor_index'] = struct.unpack('>H', data[offset:offset+2])[0]
            offset += 2
            result['sec_query'] = data[offset]
            offset += 1
            result['sec_cmd'] = struct.unpack('>H', data[offset:offset+2])[0]
            offset += 2
            result['user_data'] = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4
            result['sec_flags'] = data[offset]
            offset += 1
            result['lrn_tel'] = data[offset]
            
        elif function in (FunctionCode.SEC_DELETE, FunctionCode.SEC_STAT):
            result['is_used'] = data[offset] != 0
            offset += 1
            result['user_data'] = struct.unpack('>I', data[offset:offset+4])[0]
            
        elif function in (FunctionCode.SEC_SEND_CMD_TEL, FunctionCode.SEC_SEND_LRN_TEL):
            result['sys_state'] = struct.unpack('>H', data[offset:offset+2])[0]
            offset += 2
            result['app_state'] = struct.unpack('>H', data[offset:offset+2])[0]
            
        elif function == FunctionCode.MA_QUERY_HW_VER:
            result['hw_version_str'] = data[offset:offset+16]
            
        elif function == FunctionCode.MA_QUERY_FW_VER:
            result['major_version'] = data[offset]
            offset += 1
            result['minor_version'] = data[offset]
            offset += 1
            result['incomplete_fw'] = data[offset] != 0
            
        elif function == FunctionCode.MA_UPDATE_FW:
            result['is_restarting'] = data[offset] != 0
            
    except (IndexError, struct.error) as e:
        _LOGGER.error("Error parsing ICP data for function 0x%02x: %s", function, e)
        
    return result


# ====================================================================================================
# MAIN RX MODULE CLASS
# ====================================================================================================

class RxModule:
    """
    Pure Python implementation of ELDAT RX11 RxModule communication protocol.
    
    This class provides a complete Python implementation without requiring C libraries.
    """
    
    MAX_REQUEST_COUNT = 8
    MAX_REQUEST_QUEUED = 12
    
    def __init__(self, port: str, baudrate: int = 115200, debug: bool = False):
        """Initialize the RxModule."""
        self.port = port
        self.baudrate = baudrate
        self.debug = debug
        
        # Serial connection
        self._serial: Optional[serial.Serial] = None
        self._connected = False
        self._shutdown_requested = False
        
        # Request queues
        self._tx_req_queued: list[Optional[Request]] = [None] * self.MAX_REQUEST_QUEUED
        self._tx_req_queued_front = -1
        self._tx_req_queued_rear = -1
        self._tx_req_queued_size = 0
        
        self._tx_req_sent: list[Optional[Request]] = [None] * self.MAX_REQUEST_COUNT
        self._tx_req_sent_front = -1
        self._tx_req_sent_rear = -1
        self._tx_req_sent_size = 0
        
        self._req_pending: dict[int, Request] = {}  # handle -> Request
        
        # State tracking
        self._state_good = True
        self._connection_healthy = True
        self._hardware_error = False
        self._last_error: Optional[str] = None
        self._last_successful_communication = time.time()
        self._reconnect_attempts = 0
        self._reconnect_delay_base = 1.0  # seconds
        self._health_check_interval = 30.0  # seconds
        
        # Cancel tolerance - allow unknown handle ICPs for a short time after cancel_all_io
        self._cancel_tolerance_until: float = 0.0  # timestamp until which to tolerate unknown handles
        
        # RX state
        self._rx_sop = False
        self._rx_raw_buffer = bytearray()
        self._rx_stuffing = False
        
        # Threading
        self._protocol_lock = threading.Lock()
        self._file_lock = threading.Lock()
        self._serial_handler_thread: Optional[threading.Thread] = None
        
        # Send command loop (for dead man's switch)
        self._send_cmd_loop_thread: Optional[threading.Thread] = None
        self._send_cmd_loop_running = False
        self._send_cmd_loop_gateway: Optional[bytes] = None
        self._send_cmd_loop_button: Optional[int] = None
        
        # Disconnect callback for immediate notification
        self._disconnect_callback: Optional[Callable[[], None]] = None
        self._reconnect_callback: Optional[Callable[[], None]] = None
    
    # ================================================================================================
    # PROPERTIES
    # ================================================================================================
    
    def set_disconnect_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """Set a callback to be called when a disconnect/hardware error occurs."""
        self._disconnect_callback = callback
    
    def set_reconnect_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """Set a callback to be called after a successful reconnect."""
        self._reconnect_callback = callback
    
    def _notify_disconnect(self) -> None:
        """Notify the disconnect callback if registered."""
        if self._disconnect_callback is not None:
            try:
                self._disconnect_callback()
            except Exception as e:
                _LOGGER.warning("Fehler beim Aufrufen des Disconnect-Callbacks: %s", e)
    
    def _notify_reconnect(self) -> None:
        """Notify the reconnect callback if registered."""
        if self._reconnect_callback is not None:
            try:
                self._reconnect_callback()
            except Exception as e:
                _LOGGER.warning("Fehler beim Aufrufen des Reconnect-Callbacks: %s", e)
    
    @property
    def is_connected(self) -> bool:
        """Check if module is connected and healthy."""
        with self._protocol_lock:
            return self._connected and self._connection_healthy and self._state_good
    
    @property
    def connection_status(self) -> str:
        """Get detailed connection status."""
        with self._protocol_lock:
            if not self._connected:
                return "disconnected"
            elif self._hardware_error:
                return "hardware_error"
            elif not self._connection_healthy:
                return "reconnecting"
            elif not self._state_good:
                return "error"
            else:
                return "connected"
    
    @property
    def has_hardware_error(self) -> bool:
        """Check if a hardware error occurred (USB disconnected, I/O error, etc.)."""
        with self._protocol_lock:
            return self._hardware_error
    
    @property
    def last_error(self) -> Optional[str]:
        """Get the last error message."""
        with self._protocol_lock:
            return self._last_error
    
    def clear_hardware_error(self) -> None:
        """Clear hardware error flag (called after successful reconnect)."""
        with self._protocol_lock:
            self._hardware_error = False
            self._last_error = None
    
    # ================================================================================================
    # CONNECTION MANAGEMENT
    # ================================================================================================
    
    def connect(self) -> bool:
        """Connect to the RX11 module."""
        try:
            self._shutdown_requested = False
            
            # Open serial connection
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.001,  # 1ms timeout for non-blocking reads
                write_timeout=1.0
            )
            
            # Reset queues
            self._reset_queues()

            # Cancel all requests
            self.cancel_all_io_request()
            
            # Reset RX state
            self._rx_sop = False
            self._rx_raw_buffer = bytearray()
            self._rx_stuffing = False
            
            # Reset health state
            with self._protocol_lock:
                self._state_good = True
                self._connection_healthy = True
                self._hardware_error = False
                self._last_error = None
                self._last_successful_communication = time.time()
                self._reconnect_attempts = 0
            
            # Clear any existing data
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            
            # Start serial handler thread
            self._serial_handler_thread = threading.Thread(
                target=self._serial_handler,
                daemon=True,
                name="RxModule-SerialHandler"
            )
            self._serial_handler_thread.start()
            
            # Only mark as connected after successful thread start
            self._connected = True
            
            if self.debug:
                _LOGGER.info("Successfully connected to RxModule (%s)!", self.port)
            
            return True
            
        except Exception as e:
            # Use debug level to avoid log spam during reconnect attempts
            _LOGGER.debug("RxModule connection failed: %s", e)
            return False
    
    def dispose(self):
        """Disconnect and cleanup."""
        self._shutdown_requested = True
        
        # Wait for thread to finish (use usleep for non-blocking in sync context)
        import time
        time.sleep(0.02)  # Minimal sleep in sync context, coordinator calls this properly
        
        # Cancel all requests
        self.cancel_all_io_request()
        
        # Wait for serial handler thread
        if self._serial_handler_thread and self._serial_handler_thread.is_alive():
            self._serial_handler_thread.join(timeout=1.0)
        
        # Close serial connection
        if self._serial:
            try:
                self._serial.close()
            except Exception as e:
                _LOGGER.error("Error closing serial connection: %s", e)
            finally:
                self._serial = None
        
        self._connected = False
        
        if self.debug:
            _LOGGER.info("RxModule disconnected")
    
    def _find_eldat_usb_port(self) -> Optional[str]:
        """Find ELDAT USB device by VID/PID, handling port changes.
        
        Returns the device path if found, None otherwise.
        """
        try:
            import serial.tools.list_ports
            
            ports = list(serial.tools.list_ports.comports())
            
            # Look for ELDAT USB devices (VID: 0x155A, PID: 0x1006 or 0x1014)
            for port in ports:
                if port.vid == 0x155A and port.pid in [0x1006, 0x1014]:
                    _LOGGER.info("🔍 Found ELDAT device at %s (VID:0x%04X PID:0x%04X)", 
                               port.device, port.vid, port.pid)
                    return port.device
            
            _LOGGER.debug("No ELDAT USB device found")
            return None
            
        except ImportError:
            _LOGGER.warning("pyserial not available for USB device detection")
            return None
        except Exception as e:
            _LOGGER.error("Error searching for USB device: %s", e)
            return None

    def _reconnect(self) -> bool:
        """Attempt to reconnect to the RX11 module with exponential backoff.
        
        If the original port is not available, searches for the device by VID/PID
        to handle USB port changes (e.g., /dev/ttyUSB0 -> /dev/ttyUSB1).
        """
        
        _LOGGER.warning("Connection lost - attempting reconnect...")
        # Notify disconnect callback
        self._notify_disconnect()
        self.cancel_all_io_request()
        
        # Completely cleanup existing connection before reconnecting
        if self._serial:
            try:
                self._serial.close()

                # Reset buffers before closing
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
            except Exception:
                pass
            
            # Allow time for serial port to be released by OS
            time.sleep(0.1)
            self._serial = None
        
        # Reset connection state
        self._connected = False
        
        original_port = self.port
        
        # Attempt reconnect with exponential backoff
        while not self._shutdown_requested:
            delay = min(self._reconnect_delay_base * (2 ** self._reconnect_attempts), 60.0)
            _LOGGER.info("Reconnect attempt %d after %.1fs delay...", 
                        self._reconnect_attempts + 1, delay)
            
            time.sleep(delay)
            
            with self._protocol_lock:
                self._reconnect_attempts += 1
            
            # First, try the original/current port
            if self.connect():
                _LOGGER.info("✅ Reconnect successful on %s!", self.port)
                # Notify reconnect callback to restart receive loops
                self._notify_reconnect()
                return True
            
            # If original port failed, search for device by VID/PID
            new_port = self._find_eldat_usb_port()
            if new_port and new_port != self.port:
                _LOGGER.info("🔄 USB port changed: %s → %s", original_port, new_port)
                self.port = new_port
                
                if self.connect():
                    _LOGGER.info("✅ Reconnect successful on new port %s!", self.port)
                    # Notify reconnect callback to restart receive loops
                    self._notify_reconnect()
                    return True
        
        return False
    
    def _check_connection_health(self):
        """Check if connection is healthy based on recent activity.
        
        The RX11 protocol works as follows:
        - IRP (I/O Request Packet) is sent to the module
        - For async requests: IPP (I/O Pending Packet) with handle is returned, then ICP later
        - For sync requests (handle=0): ICP is returned directly
        
        Connection is unhealthy if:
        - IRPs were sent (in _tx_req_sent) but no IPP/ICP response came
        - Non-continuous command requests are pending without ICP response
        
        Continuous RCV requests (EWB_RCV, TR_RCV, etc.) are excluded as they
        wait indefinitely for incoming telegrams.
        """
        with self._protocol_lock:
            if not self._connection_healthy:
                return  # Already marked unhealthy
            
            time_since_comm = time.time() - self._last_successful_communication
            
            # Count requests waiting for IPP (IRPs sent but no acknowledgment yet)
            irps_waiting_for_ipp = self._tx_req_sent_size
            
            # Count only non-continuous pending requests (waiting for ICP)
            # Continuous RCV requests wait for incoming telegrams and should not
            # trigger unhealthy state just because no telegrams arrived
            non_continuous_pending = sum(
                1 for req in self._req_pending.values() 
                if not req.is_continuous
            )
            
            # Count continuous RCV requests (for logging only)
            continuous_pending = len(self._req_pending) - non_continuous_pending
            
            # Log health status periodically (every 30s check)
            if time_since_comm >= self._health_check_interval:
                _LOGGER.info(
                    "🔍 RX11 Health Check: %.1fs since last comm, "
                    "IRPs waiting for IPP=%d, Commands waiting for ICP=%d, "
                    "Continuous RCV requests=%d",
                    time_since_comm, irps_waiting_for_ipp, non_continuous_pending, continuous_pending
                )
            
            # Connection is unhealthy if:
            # 1. No communication for too long AND
            # 2. There are IRPs waiting for IPP (stuck in send queue) OR
            #    There are non-continuous commands waiting for ICP
            if time_since_comm > self._health_check_interval and (
                irps_waiting_for_ipp > 0 or non_continuous_pending > 0
            ):
                _LOGGER.error(
                    "🔴 RX11 Connection UNHEALTHY - no response for %.1fs! "
                    "(IRPs without IPP=%d, Commands without ICP=%d)", 
                    time_since_comm, irps_waiting_for_ipp, non_continuous_pending
                )
                self._state_good = False
    
    def _mark_communication_success(self):
        """Mark that we had a successful communication."""
        with self._protocol_lock:
            self._last_successful_communication = time.time()
            if self._reconnect_attempts > 0:
                self._reconnect_attempts = 0  # Reset backoff on success
    
    def _reset_queues(self):
        """Reset all request queues."""
        self._tx_req_queued = [None] * self.MAX_REQUEST_QUEUED
        self._tx_req_queued_front = -1
        self._tx_req_queued_rear = -1
        self._tx_req_queued_size = 0
        
        self._tx_req_sent = [None] * self.MAX_REQUEST_COUNT
        self._tx_req_sent_front = -1
        self._tx_req_sent_rear = -1
        self._tx_req_sent_size = 0
        
        self._req_pending.clear()
    
    # ================================================================================================
    # REQUEST MANAGEMENT
    # ================================================================================================
    
    def _create_request(self, irp: IRP, irp_byte_count: int, 
                       expected_icp_byte_count: int, req_str: str) -> Request:
        """Create a new request."""
        return Request(
            irp=irp,
            irp_byte_count=irp_byte_count,
            expected_icp_byte_count=expected_icp_byte_count,
            req_str=req_str,
            is_continuous=irp.function in CONTINUOUS_RCV_FUNCTIONS
        )
    
    def _place_request(self, req: Request):
        """Place a request in the queue or send it immediately."""
        with self._protocol_lock:
            if not self._state_good:
                # Complete request as failed
                req.icp = ICP(handle=0, result=ErrorCode.ERR_FAILSTATE)
                req.signal()
                return
            
            # Check if we can send immediately
            if (self._tx_req_queued_size == 0 and 
                (self._tx_req_sent_size + len(self._req_pending)) < self.MAX_REQUEST_COUNT):
                # Send immediately
                self._enqueue_sent(req)
                self._write_to_buffer(req.irp, req.req_str)
            elif self._tx_req_queued_size < self.MAX_REQUEST_QUEUED:
                # Queue the request
                self._remove_canceled_queued_requests()
                self._enqueue_queued(req)
                req.queued = True
            else:
                # Queue is full
                req.icp = ICP(handle=0, result=ErrorCode.ERR_OUT_OF_QUEUE)
                req.signal()
    
    def _enqueue_queued(self, req: Request):
        """Add request to queued list."""
        if self._tx_req_queued_front == -1:
            self._tx_req_queued_front = 0
        self._tx_req_queued_rear = (self._tx_req_queued_rear + 1) % self.MAX_REQUEST_QUEUED
        self._tx_req_queued[self._tx_req_queued_rear] = req
        self._tx_req_queued_size += 1
    
    def _dequeue_queued(self) -> Optional[Request]:
        """Remove request from queued list."""
        if self._tx_req_queued_size == 0:
            return None
        req = self._tx_req_queued[self._tx_req_queued_front]
        self._tx_req_queued[self._tx_req_queued_front] = None
        self._tx_req_queued_front = (self._tx_req_queued_front + 1) % self.MAX_REQUEST_QUEUED
        self._tx_req_queued_size -= 1
        return req
    
    def _enqueue_sent(self, req: Request):
        """Add request to sent list."""
        if self._tx_req_sent_front == -1:
            self._tx_req_sent_front = 0
        self._tx_req_sent_rear = (self._tx_req_sent_rear + 1) % self.MAX_REQUEST_COUNT
        self._tx_req_sent[self._tx_req_sent_rear] = req
        self._tx_req_sent_size += 1
    
    def _dequeue_sent(self) -> Optional[Request]:
        """Remove request from sent list."""
        if self._tx_req_sent_size == 0:
            return None
        req = self._tx_req_sent[self._tx_req_sent_front]
        self._tx_req_sent[self._tx_req_sent_front] = None
        self._tx_req_sent_front = (self._tx_req_sent_front + 1) % self.MAX_REQUEST_COUNT
        self._tx_req_sent_size -= 1
        return req
    
    def _remove_canceled_queued_requests(self):
        """Remove canceled requests from the queued list."""
        while (self._tx_req_queued_size > 0 and 
               self._tx_req_queued[self._tx_req_queued_front] and
               self._tx_req_queued[self._tx_req_queued_front].cancel):
            self._dequeue_queued()
    
    # ================================================================================================
    # SERIAL COMMUNICATION
    # ================================================================================================
    
    def _write_to_buffer(self, irp: IRP, req_str: str):
        """Write an IRP to the serial connection."""
        if not self._serial or self._shutdown_requested:
            return
        
        try:
            # First check if serial port is still valid (USB might have been removed)
            if not self._check_serial_port_valid():
                _LOGGER.error("ERROR [writeToBuffer] - Serial port no longer valid (USB disconnected?)")  
                with self._protocol_lock:
                    self._state_good = False
                    self._connection_healthy = False
                    self._reconnect()
                return
            
            # Encode the packet
            packet = encode_irp(irp)
            
            # Write to serial
            with self._file_lock:
                if self._serial and not self._shutdown_requested:
                    bytes_written = self._serial.write(packet)
                    
                    if bytes_written != len(packet):
                        _LOGGER.error("ERROR [writeToBuffer] - serial bus, %d of %d written",
                                    bytes_written, len(packet))
                        with self._protocol_lock:
                            self._state_good = False
                            self._connection_healthy = False
                        return
                    
                    if self.debug:
                        hex_str = ' '.join(f'{b:02x}' for b in packet)
                        _LOGGER.info("Tx-Uart: %s IRP %s", hex_str, req_str)
        
        except (serial.SerialException, OSError) as e:
            if not self._shutdown_requested:
                _LOGGER.error("ERROR [writeToBuffer] - Serial error (USB disconnected?): %s", e)
                with self._protocol_lock:
                    self._state_good = False
                    self._connection_healthy = False
        except Exception as e:
            if not self._shutdown_requested:
                _LOGGER.error("ERROR [writeToBuffer] - %s", e)
                with self._protocol_lock:
                    self._state_good = False
    
    def _check_serial_port_valid(self) -> bool:
        """Check if the serial port is still valid (USB device still connected)."""
        try:
            if not self._serial:
                return False
            
            # Try to check if the port is still accessible
            # This will fail if the USB device has been removed
            import os
            if hasattr(self._serial, 'port') and self._serial.port:
                # On Linux, check if the device file still exists
                if not os.path.exists(self._serial.port):
                    _LOGGER.warning("Serial port %s no longer exists", self._serial.port)
                    return False
            
            # Also check if the serial connection is still open
            if not self._serial.is_open:
                _LOGGER.warning("Serial port is not open")
                return False
                
            return True
        except Exception as e:
            _LOGGER.warning("Error checking serial port validity: %s", e)
            return False
    
    def _serial_handler(self):
        """Main serial handler thread - reads and processes incoming data."""
        last_health_check = time.time()
        
        while not self._shutdown_requested:
            try:
                # Check connection health periodically
                if time.time() - last_health_check > self._health_check_interval:
                    self._check_connection_health()
                    last_health_check = time.time()
                
                # Check if serial port is still valid (USB might have been removed)
                if not self._check_serial_port_valid():
                    if not self._shutdown_requested:
                        _LOGGER.error("Serial port no longer valid - USB device may have been removed")
                        with self._protocol_lock:
                            self._state_good = False
                            self._connection_healthy = False
                    break
                
                # Read available bytes (no lock needed for reading)
                try:
                    while self._serial and self._serial.in_waiting > 0:
                        byte_data = self._serial.read(1)
                        if not byte_data:
                            break
                        
                        byte = byte_data[0]
                        if self.debug:
                            _LOGGER.info("RX byte: 0x%02x (buf_len=%d, sop=%s, stuffing=%s)", 
                                        byte, len(self._rx_raw_buffer), self._rx_sop, self._rx_stuffing)
                        self._process_received_byte(byte)
                except (serial.SerialException, OSError) as e:
                    if not self._shutdown_requested:
                        error_str = str(e)
                        # Log only once as WARNING (not ERROR) to reduce log spam
                        _LOGGER.warning("🔴 RX11 USB getrennt - Reconnect wird gestartet")
                        _LOGGER.debug("Disconnect details: %s", e)
                        with self._protocol_lock:
                            self._state_good = False
                            self._connection_healthy = False
                            self._hardware_error = True
                            self._last_error = error_str
                            self._connected = False  # Immediately mark as disconnected
                        # Notify disconnect callback if registered - this triggers reconnect
                        self._notify_disconnect()
                    break
                
                # Process queued requests (uses lock internally)
                self._process_queued_requests()
                
                # Small sleep to prevent CPU spinning
                time.sleep(0.001)
                
            except (serial.SerialException, OSError) as e:
                if not self._shutdown_requested:
                    error_str = str(e)
                    _LOGGER.warning("🔴 RX11 Verbindung unterbrochen")
                    _LOGGER.debug("Serial handler error: %s", e)
                    with self._protocol_lock:
                        self._state_good = False
                        self._connection_healthy = False
                        self._hardware_error = True
                        self._last_error = error_str
                        self._connected = False  # Immediately mark as disconnected
                    # Notify disconnect callback if registered - this triggers reconnect
                    self._notify_disconnect()
                break
            except Exception as e:
                if not self._shutdown_requested:
                    _LOGGER.error("Unexpected error in serial handler: %s", e)
                    with self._protocol_lock:
                        self._state_good = False
                        self._connection_healthy = False
                time.sleep(0.01)
        
        _LOGGER.info("Serial handler thread exiting")
    
    def _process_received_byte(self, byte: int):
        """Process a single received byte."""
        if not self._state_good:
            return
        
        # Add byte to buffer FIRST (like C: m_RxRawBuffer[m_RxRawOffset] = d; m_RxRawOffset++;)
        self._rx_raw_buffer.append(byte)
        
        # Handle SOP
        if byte == SOP:
            if self._rx_sop:
                # Unexpected SOP
                self._state_good = False
                self._connection_healthy = False
                _LOGGER.error("ERROR [RxHandler] - Unexpected data on SOP")
                self._reconnect()
                return
            self._rx_sop = True
            self._rx_stuffing = False
            return
        
        # Must have SOP before processing other bytes
        if not self._rx_sop:
            # _LOGGER.error("ERROR [RxHandler] - Unexpected byte without SOP: 0x%02x", byte)
            return
        
        # Handle EOP
        if byte == EOP:
            if self._rx_stuffing:
                self._state_good = False
                self._connection_healthy = False
                _LOGGER.error("ERROR [RxHandler] - Unexpected EOP on Unstuffing")
                self._reconnect()
                return
            # Debug: Log complete packet before processing
            _LOGGER.info("Complete packet received (len=%d): %s", len(self._rx_raw_buffer), self._rx_raw_buffer.hex())
            # Process complete packet
            self._process_complete_packet()
            return
        
        # Handle byte stuffing PREFIX (like C: m_RxRawOffset--)
        if not self._rx_stuffing and byte == PREFIX:
            self._rx_stuffing = True
            # Remove PREFIX from buffer (overwrite it with next byte, like C: m_RxRawOffset--)
            self._rx_raw_buffer.pop()
            return
        
        # Handle stuffed byte
        if self._rx_stuffing:
            # Verify stuffed byte is in correct range
            if ((STUFFING_MIN + STUFFING_ADDEND) & 0xFF) <= byte <= ((STUFFING_MAX + STUFFING_ADDEND) & 0xFF):
                # Unstuff: add STUFFING_ADDEND and overwrite last position (like C: m_RxRawBuffer[(m_RxRawOffset - 1)] = d)
                unstuffed = (byte + STUFFING_ADDEND) & 0xFF
                self._rx_raw_buffer[-1] = unstuffed
                self._rx_stuffing = False
            else:
                # Invalid stuffed byte - reset packet but don't kill connection
                _LOGGER.warning("Invalid stuffed byte 0x%02x after PREFIX (expected 0x00-0x02), discarding packet", byte)
                self._rx_raw_buffer = bytearray()
                self._rx_sop = False
                self._rx_stuffing = False
                return
        
        # Check for buffer overflow
        if len(self._rx_raw_buffer) >= 128:
            _LOGGER.error("ERROR [RxHandler] - Unexpected large packet, buffer overflow")
            # Reset packet state but don't kill connection - this might be transient
            self._rx_raw_buffer = bytearray()
            self._rx_sop = False
            self._rx_stuffing = False
            with self._protocol_lock:
                self._state_good = False
                self._connection_healthy = False
                self._reconnect()
    
    def _process_complete_packet(self):
        """Process a complete received packet."""
        raw_buffer = bytes(self._rx_raw_buffer)
        
        # Reset for next packet
        self._rx_raw_buffer = bytearray()
        self._rx_sop = False
        
        # Decode packet
        icp, _, success = decode_packet(raw_buffer)
        if not success:
            # Log detailed info for debugging but don't set state_good to False for startup noise
            if len(raw_buffer) > 0:
                _LOGGER.warning("Failed to decode packet (len=%d, hex=%s) - possibly startup noise", 
                              len(raw_buffer), raw_buffer.hex()[:80])
            return
        
        # Mark successful communication
        self._mark_communication_success()
        
        # Calculate ICP byte count (without SOP and EOP)
        icp_byte_count = len(raw_buffer) - 2
        
        # Handle based on packet type
        handle = icp.handle
        
        if icp_byte_count == 2:
            # IPP - just a handle acknowledgment
            self._process_ipp(handle, raw_buffer)
        else:
            # ICP - complete response
            self._process_icp(handle, icp, icp_byte_count, raw_buffer)
    
    def _process_ipp(self, handle: int, raw_buffer: bytes):
        """Process an I/O Pending Packet (just a handle)."""
        if self._tx_req_sent_size == 0:
            with self._protocol_lock:
                self._state_good = False
                self._connection_healthy = False
            _LOGGER.error("ERROR [RxHandler] - Unexpected IPP")
            self._reconnect()
            return
        
        # Move request from sent to pending
        req = self._dequeue_sent()
        if not req:
            return
        
        if handle == 0:
            with self._protocol_lock:
                self._state_good = False
                self._connection_healthy = False
            _LOGGER.error("ERROR [RxHandler] - Unexpected zero handle")
            self._reconnect()
            return
        
        # Check for duplicate handle
        if handle in self._req_pending:
            with self._protocol_lock:
                self._state_good = False
                self._connection_healthy = False
            _LOGGER.error("ERROR [RxHandler] - Duplicated handle: 0x%04x", handle)
            self._reconnect()
            return
        
        # Store in pending with handle
        req.handle = handle
        self._req_pending[handle] = req
        
        if self.debug:
            hex_str = ' '.join(f'{b:02x}' for b in raw_buffer)
            _LOGGER.debug("Rx-Uart: %s IPP %s Handle %04x", hex_str, req.req_str, handle)
        
        # If request was canceled, send cancel
        if req.cancel:
            self._cancel_request_impl(req)
    
    def _process_icp(self, handle: int, icp: ICP, icp_byte_count: int, raw_buffer: bytes):
        """Process an I/O Completion Packet."""
        # Find the request
        req: Optional[Request] = None
        
        if handle != 0:
            # Look in pending requests
            req = self._req_pending.pop(handle, None)
            if not req:
                # Check if we're in the tolerance period after cancel_all_io
                if time.time() < self._cancel_tolerance_until:
                    # This is expected after restart - device sends ICP for pre-restart operations
                    _LOGGER.debug("ICP for unknown handle 0x%04x during cancel tolerance period (expected after restart)", handle)
                    return
                
                # Outside tolerance period - this is a real error
                self._state_good = False
                self._connection_healthy = False
                _LOGGER.warning("ERROR [RxHandler] - ICP for unknown handle: 0x%04x", handle)
                self._reconnect()
                return
        else:
            # Synchronous request (no handle)
            req = self._dequeue_sent()
            if not req:
                self._state_good = False
                self._connection_healthy = False
                _LOGGER.error("ERROR [RxHandler] - Unexpected synchronous ICP")
                self._reconnect()
                return
        
        # Validate ICP length
        if icp.result == ErrorCode.SUCCESS:
            if icp_byte_count != req.expected_icp_byte_count:
                with self._protocol_lock:
                    self._state_good = False
                    self._connection_healthy = False
                _LOGGER.error("ERROR [RxHandler] - Unexpected ICP length: %d / %d - initiating reconnect",
                            icp_byte_count, req.expected_icp_byte_count)
                self._reconnect()
                return
        elif icp_byte_count != 3:
            with self._protocol_lock:
                self._state_good = False
                self._connection_healthy = False
            _LOGGER.error("ERROR [RxHandler] - Unexpected error ICP length: %d - initiating reconnect", icp_byte_count)
            self._reconnect()
            return
        
        if self.debug:
            hex_str = ' '.join(f'{b:02x}' for b in raw_buffer)
            _LOGGER.info("Rx-Uart: %s ICP %s", hex_str, req.req_str)
        
        # Handle ERR_OUT_OF_QUEUE - requeue the request
        if icp.result == ErrorCode.ERR_OUT_OF_QUEUE:
            if self._tx_req_queued_size < self.MAX_REQUEST_QUEUED:
                self._enqueue_queued(req)
                return
            else:
                with self._protocol_lock:
                    self._state_good = False
                    self._connection_healthy = False
                _LOGGER.error("ERROR [RxHandler] - QueuedRequest full")
                return
        
        # Complete the request
        req.icp = icp
        req.signal()
    
    def _process_queued_requests(self):
        """Process queued requests and send if possible."""
        self._remove_canceled_queued_requests()
        
        while (self._tx_req_queued_size > 0 and
               (self._tx_req_sent_size + len(self._req_pending)) < self.MAX_REQUEST_COUNT):
            req = self._dequeue_queued()
            if not req:
                break
            
            # Check state (thread-safe read)
            with self._protocol_lock:
                state_good = self._state_good
            
            if not state_good:
                # Fail the request
                req.icp = ICP(handle=0, result=ErrorCode.ERR_FAILSTATE)
                req.signal()
            else:
                # Send the request
                req.queued = False
                self._enqueue_sent(req)
                self._write_to_buffer(req.irp, req.req_str)
    
    # ================================================================================================
    # CANCEL OPERATIONS
    # ================================================================================================
    
    def _cancel_request_impl(self, req: Request):
        """Send a cancel IRP for a specific request."""
        cancel_params = encode_uint16(req.handle)
        cancel_irp = IRP(function=FunctionCode.CANCEL_IO, params=cancel_params)
        self._write_to_buffer(cancel_irp, "CANCEL_IO")
    
    def cancel_all_io_request(self):
        """Cancel all pending requests."""
        # Cancel queued requests
        while self._tx_req_queued_size > 0:
            req = self._dequeue_queued()
            if req and not req.cancel:
                req.icp = ICP(handle=0, result=ErrorCode.ERR_CANCELED)
                req.signal()
        
        with self._protocol_lock:
            # Send CANCEL_ALL_IO
            cancel_irp = IRP(function=FunctionCode.CANCEL_ALL_IO, params=bytes())
            self._write_to_buffer(cancel_irp, "CANCEL_ALL_IO")
            
            # Cancel all pending requests
            for handle, req in list(self._req_pending.items()):
                if not req.cancel:
                    req.icp = ICP(handle=0, result=ErrorCode.ERR_CANCELED)
                    req.signal()
            self._req_pending.clear()
            
            # Allow unknown handle ICPs for 2 seconds after cancel
            # This is normal after restart - device may still send ICPs for pre-restart operations
            self._cancel_tolerance_until = time.time() + 2.0
    
    # ================================================================================================
    # HIGH-LEVEL API FUNCTIONS
    # ================================================================================================
    
    def ew_get_fd_serial_request(self, index: int, timeout: float = 5.0) -> tuple[int, bytes]:
        """Get EW serial number at index."""
        params = encode_uint16(index)
        irp = IRP(function=FunctionCode.EW_GET_FD_SERIAL, params=params)
        req = self._create_request(irp, 3, 19, "EW_GET_FD_SERIAL")
        
        self._place_request(req)
        req.wait(timeout)
        
        if req.icp.result == ErrorCode.SUCCESS:
            parsed = parse_icp_data(self, req.icp, FunctionCode.EW_GET_FD_SERIAL)
            serial = parsed.get('gateway', bytes(16))
            return req.icp.result, serial
        return req.icp.result, bytes(16)
    
    def ew_rcv_button_request(self, timeout: float = 30.0) -> tuple[int, int, bytes, bytes]:
        """Receive EW button press."""
        irp = IRP(function=FunctionCode.EW_RCV_BUTTON, params=bytes())
        req = self._create_request(irp, 1, 21, "EW_RCV_BUTTON")
        
        self._place_request(req)
        req.wait(timeout)
        
        if req.icp.result == ErrorCode.SUCCESS:
            parsed = parse_icp_data(self, req.icp, FunctionCode.EW_RCV_BUTTON)
            info_type = parsed.get('info_type', 0)
            transmitter = parsed.get('receiver_or_transmitter', bytes(16))
            info_data = parsed.get('info_data', bytes(8))
            return req.icp.result, info_type, transmitter, info_data
        return req.icp.result, 0, bytes(16), bytes(8)
    
    def ew_send_cmd_request(self, gateway: bytes, button: int, timeout: float = 5.0) -> int:
        """Send EW command."""
        params = encode_serial_array(gateway) + bytes([button])
        irp = IRP(function=FunctionCode.EW_SEND_CMD, params=params)
        req = self._create_request(irp, 18, 3, "EW_SEND_CMD")
        
        self._place_request(req)
        req.wait(timeout)
        
        return req.icp.result
    
    def start_ew_send_cmd_loop(self, gateway: bytes, button: int):
        """Start continuous EW send command loop (dead man's switch)."""
        if self._send_cmd_loop_running:
            self.stop_ew_send_cmd_loop()
        
        self._send_cmd_loop_gateway = gateway
        self._send_cmd_loop_button = button
        self._send_cmd_loop_running = True
        
        self._send_cmd_loop_thread = threading.Thread(
            target=self._ew_send_cmd_loop,
            daemon=True,
            name="RxModule-SendCmdLoop"
        )
        self._send_cmd_loop_thread.start()
    
    def stop_ew_send_cmd_loop(self):
        """Stop continuous EW send command loop."""
        self._send_cmd_loop_running = False
        if self._send_cmd_loop_thread:
            self._send_cmd_loop_thread.join(timeout=1.0)
            self._send_cmd_loop_thread = None
    
    def _ew_send_cmd_loop(self):
        """Internal loop for continuous EW send commands."""
        while self._send_cmd_loop_running:
            result = self.ew_send_cmd_request(
                self._send_cmd_loop_gateway,
                self._send_cmd_loop_button,
                timeout=2.0
            )
            if result != ErrorCode.SUCCESS:
                _LOGGER.warning("EW_SEND_CMD_LOOP error: 0x%02x", result)
                time.sleep(0.1)
    
    def ew_rcv_ex_request(self, timeout: float = 30.0) -> tuple[int, int, bytes, bytes]:
        """Receive EW extended telegram."""
        irp = IRP(function=FunctionCode.EW_RCV_EX, params=bytes())
        req = self._create_request(irp, 1, 28, "EW_RCV_EX")
        
        self._place_request(req)
        req.wait(timeout)
        
        if req.icp.result == ErrorCode.SUCCESS:
            parsed = parse_icp_data(self, req.icp, FunctionCode.EW_RCV_EX)
            info_type = parsed.get('info_type', 0)
            transmitter = parsed.get('receiver_or_transmitter', bytes(16))
            info_data = parsed.get('info_data', bytes(8))
            return req.icp.result, info_type, transmitter, info_data
        return req.icp.result, 0, bytes(16), bytes(8)
    
    def ewb_get_fd_serial_request(self, index: int, timeout: float = 5.0) -> tuple[int, bytes]:
        """Get EWB serial number at index."""
        params = encode_uint16(index)
        irp = IRP(function=FunctionCode.EWB_GET_FD_SERIAL, params=params)
        req = self._create_request(irp, 3, 19, "EWB_GET_FD_SERIAL")
        
        self._place_request(req)
        req.wait(timeout)
        
        if req.icp.result == ErrorCode.SUCCESS:
            parsed = parse_icp_data(self, req.icp, FunctionCode.EWB_GET_FD_SERIAL)
            serial = parsed.get('gateway', bytes(16))
            return req.icp.result, serial
        return req.icp.result, bytes(16)
    
    def ewb_add_nfilter_request(self, gateway: bytes, timeout: float = 5.0) -> int:
        """Add EWB gateway to filter."""
        params = encode_serial_array(gateway)
        irp = IRP(function=FunctionCode.EWB_ADD_NFILTER, params=params)
        req = self._create_request(irp, 17, 3, "EWB_ADD_NFILTER")
        
        self._place_request(req)
        req.wait(timeout)
        
        return req.icp.result
    
    def ewb_clear_nfilter_request(self, timeout: float = 5.0) -> int:
        """Clear EWB filter."""
        irp = IRP(function=FunctionCode.EWB_CLEAR_NFILTER, params=bytes())
        req = self._create_request(irp, 1, 3, "EWB_CLEAR_NFILTER")
        
        self._place_request(req)
        req.wait(timeout)
        
        return req.icp.result
    
    def ewb_join_device_request(self, gateway: bytes, timeout: float = 30.0) -> tuple[int, int, bytes]:
        """Join EWB device."""
        params = encode_serial_array(gateway)
        irp = IRP(function=FunctionCode.EWB_JOIN_DEVICE, params=params)
        req = self._create_request(irp, 17, 20, "EWB_JOIN_DEVICE")
        
        self._place_request(req)
        req.wait(timeout)
        
        if req.icp.result == ErrorCode.SUCCESS:
            parsed = parse_icp_data(self, req.icp, FunctionCode.EWB_JOIN_DEVICE)
            device_type = parsed.get('device_type', 0)
            receiver = parsed.get('receiver', bytes(16))
            return req.icp.result, device_type, receiver
        return req.icp.result, 0, bytes(16)
    
    def ewb_remove_device_request(self, gateway: bytes, receiver: bytes, timeout: float = 5.0) -> int:
        """Remove EWB device."""
        params = encode_serial_array(gateway) + encode_serial_array(receiver)
        irp = IRP(function=FunctionCode.EWB_REMOVE_DEVICE, params=params)
        req = self._create_request(irp, 33, 3, "EWB_REMOVE_DEVICE")
        
        self._place_request(req)
        req.wait(timeout)
        
        return req.icp.result
    
    def ewb_rcv_request(self, timeout: float = 30.0) -> tuple[int, int, bytes, bytes]:
        """Receive EWB telegram."""
        irp = IRP(function=FunctionCode.EWB_RCV, params=bytes())
        req = self._create_request(irp, 1, 28, "EWB_RCV")
        
        self._place_request(req)
        req.wait(timeout)
        
        if req.icp.result == ErrorCode.SUCCESS:
            parsed = parse_icp_data(self, req.icp, FunctionCode.EWB_RCV)
            info_type = parsed.get('info_type', 0)
            receiver_transmitter = parsed.get('receiver_or_transmitter', bytes(16))
            info_data = parsed.get('info_data', bytes(8))
            return req.icp.result, info_type, receiver_transmitter, info_data
        return req.icp.result, 0, bytes(16), bytes(8)
    
    def ewb_change_state_request(self, gateway: bytes, receiver: bytes, 
                                desired_mode: int, desired_state: bytes,
                                timeout: float = 5.0) -> tuple[int, int, bytes]:
        """Change EWB device state."""
        params = (encode_serial_array(gateway) + encode_serial_array(receiver) +
                 bytes([desired_mode]) + encode_state_array(desired_state))
        irp = IRP(function=FunctionCode.EWB_CHANGE_STATE, params=params)
        req = self._create_request(irp, 38, 8, "EWB_CHANGE_STATE")
        
        self._place_request(req)
        req.wait(timeout)
        
        if req.icp.result == ErrorCode.SUCCESS:
            parsed = parse_icp_data(self, req.icp, FunctionCode.EWB_CHANGE_STATE)
            recent_mode = parsed.get('mode', 0)
            recent_state = parsed.get('state', bytes(4))
            return req.icp.result, recent_mode, recent_state
        return req.icp.result, 0, bytes(4)
    
    def ewb_query_state_request(self, gateway: bytes, receiver: bytes,
                               desired_mode: int, timeout: float = 5.0) -> tuple[int, int, bytes]:
        """Query EWB device state."""
        params = (encode_serial_array(gateway) + encode_serial_array(receiver) +
                 bytes([desired_mode]))
        irp = IRP(function=FunctionCode.EWB_QUERY_STATE, params=params)
        req = self._create_request(irp, 34, 8, "EWB_QUERY_STATE")
        
        self._place_request(req)
        req.wait(timeout)
        
        if req.icp.result == ErrorCode.SUCCESS:
            parsed = parse_icp_data(self, req.icp, FunctionCode.EWB_QUERY_STATE)
            recent_mode = parsed.get('mode', 0)
            state = parsed.get('state', bytes(4))
            return req.icp.result, recent_mode, state
        return req.icp.result, 0, bytes(4)
    
    def ewb_tr_lrn_control_request(self, gateway: bytes, receiver: bytes, ctrl: int,
                                   desired_mode: int, desired_state: bytes,
                                   timeout: float = 30.0) -> tuple[int, int, bytes]:
        """Control EWB transmitter learning."""
        params = (encode_serial_array(gateway) + encode_serial_array(receiver) +
                 bytes([ctrl, desired_mode]) + encode_state_array(desired_state))
        irp = IRP(function=FunctionCode.EWB_TRLRN_CONTROL, params=params)
        req = self._create_request(irp, 39, 8, "EWB_TRLRN_CONTROL")
        
        self._place_request(req)
        req.wait(timeout)
        
        if req.icp.result == ErrorCode.SUCCESS:
            parsed = parse_icp_data(self, req.icp, FunctionCode.EWB_TRLRN_CONTROL)
            recent_mode = parsed.get('mode', 0)
            recent_state = parsed.get('state', bytes(4))
            return req.icp.result, recent_mode, recent_state
        return req.icp.result, 0, bytes(4)
    
    def ma_query_hw_ver_request(self, timeout: float = 5.0) -> tuple[int, bytes]:
        """Query hardware version."""
        irp = IRP(function=FunctionCode.MA_QUERY_HW_VER, params=bytes())
        req = self._create_request(irp, 1, 19, "MA_QUERY_HW_VER")
        
        self._place_request(req)
        req.wait(timeout)
        
        if req.icp.result == ErrorCode.SUCCESS:
            parsed = parse_icp_data(self, req.icp, FunctionCode.MA_QUERY_HW_VER)
            hw_str = parsed.get('hw_version_str', bytes(16))
            return req.icp.result, hw_str
        return req.icp.result, bytes(16)
    
    def ma_query_fw_ver_request(self, timeout: float = 5.0) -> tuple[int, int, int, bool]:
        """Query firmware version."""
        irp = IRP(function=FunctionCode.MA_QUERY_FW_VER, params=bytes())
        req = self._create_request(irp, 1, 6, "MA_QUERY_FW_VER")
        
        self._place_request(req)
        req.wait(timeout)
        
        if req.icp.result == ErrorCode.SUCCESS:
            parsed = parse_icp_data(self, req.icp, FunctionCode.MA_QUERY_FW_VER)
            major = parsed.get('major_version', 0)
            minor = parsed.get('minor_version', 0)
            incomplete = parsed.get('incomplete_fw', False)
            return req.icp.result, major, minor, incomplete
        return req.icp.result, 0, 0, False
    
    def ping_request(self, duration: int = 5) -> int:
        """Ping the module."""
        irp = IRP(function=FunctionCode.PING_RCV, params=bytes())
        req = self._create_request(irp, 1, 1, "PING_RCV")
        
        self._place_request(req)
        if not req.wait(duration):
            return ErrorCode.ERR_RF_TIMEOUT
        
        return req.icp.result
