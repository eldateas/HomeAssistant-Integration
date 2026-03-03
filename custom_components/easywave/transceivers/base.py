"""Base classes for EASYWAVE transceivers and devices.

DEPRECATED: This file is kept for backward compatibility.
All classes have been moved to the 'base' and 'behaviors' submodules.

New imports:
    from .base import BaseTransceiver, BaseDevice, BaseReceiver, BaseTransmitter, BaseSensor
    from .base import DeviceType, DeviceSubtype, OperatingMode, TransceiverType
    from .behaviors import CoverBehaviorMixin, SwitchBehaviorMixin, etc.

Structured Naming Convention:
- transceiver_<type>_<device_type>_<operation>: Structured operation naming
  Example: rx11_ew_receiver_button_start_continuous
  
Hierarchy: transceiver -> type -> device_type -> button_type
- transceiver: RX11, RX21, Gateway
- type: EW (EasyWave), EWneo, EWB (EasyWave Bidirectional) 
- device_type: receiver, transmitter, sensor
- button_type: A(0), B(1), C(2), D(3)

This provides a clear, hierarchical naming structure for all operations
across different EASYWAVE device types and transceivers.
"""
from __future__ import annotations

# Re-export all classes from new structure for backward compatibility
from .base import (
    BaseDevice,
    BaseDeviceHandler,
    BaseReceiver,
    BaseSensor,
    BaseTransceiver,
    BaseTransmitter,
    DeviceInfo,
    DeviceSubtype,
    DeviceType,
    OperatingMode,
    TransceiverCapabilities,
    TransceiverType,
)
from .behaviors import (
    ButtonBehaviorMixin,
    CoverBehaviorMixin,
    EntitySpecsMixin,
    LightBehaviorMixin,
    SensorBehaviorMixin,
    SwitchBehaviorMixin,
)

__all__ = [
    # Enums
    "TransceiverType",
    "DeviceType",
    "DeviceSubtype",
    "OperatingMode",
    # Data classes
    "DeviceInfo",
    "TransceiverCapabilities",
    # Base classes
    "BaseTransceiver",
    "BaseDeviceHandler",
    "BaseDevice",
    "BaseReceiver",
    "BaseTransmitter",
    "BaseSensor",
    # Behavior Mixins
    "CoverBehaviorMixin",
    "SwitchBehaviorMixin",
    "LightBehaviorMixin"]


"""Supported transceiver types following structured naming.
    
Each transceiver type supports different device types:
- RX11: EW receivers, transmitters, EWB sensors
- RX21: Future advanced transceiver
- Gateway: Network-based transceiver
"""
RX11 = "rx11"
# Future transceivers can be added here
# RX21 = "rx21"
# Gateway = "gateway"


class DeviceType(Enum):
    """Grundlegende Device-Typen basierend auf EASYWAVE Spezifikation."""
    # EasyWave (EW) Geräte - ohne Sensoren (nur EWneo hat Sensoren)
    EW_RECEIVER = "ew_receiver"
    EW_TRANSMITTER = "ew_transmitter"
    EWNEO_SENSOR = "ewneo_sensor"
    EWNEO_RECEIVER = "ewneo_receiver"
    
    # EasyWave Neo (EWB) Geräte
    EWNEO_TRANSCEIVER = "ewneo_transceiver"
    EWNEO_BIDI_TRANSMITTER = "ewneo_bidi_transmitter"
    EWNEO_SWITCH = "ewneo_switch"
    EWNEO_DUAL_SWITCH = "ewneo_dual_switch"
    EWNEO_QUAD_SWITCH = "ewneo_quad_switch"
    EWNEO_DIMMER = "ewneo_dimmer"
    EWNEO_MOTOR = "ewneo_motor"
    EWNEO_DUAL_MOTOR = "ewneo_dual_motor"
    EWNEO_QUAD_MOTOR = "ewneo_quad_motor"
    
    UNKNOWN = "unknown"


class DeviceSubtype(Enum):
    """Device-Subtypen für verschiedene Funktionalitäten."""
    # Receiver Subtypen
    MOTOR = "motor"
    SWITCH = "switch"
    DIMMER = "dimmer"
    HEATING_COOLING = "heating_cooling"
    TRANSCEIVER = "transceiver"
    
    # Multi-channel variants
    DUAL_SWITCH = "dual_switch"
    QUAD_SWITCH = "quad_switch"
    DUAL_MOTOR = "dual_motor"
    QUAD_MOTOR = "quad_motor"
    
    # Sensor Subtypen - nur für EWneo
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    WIND_SPEED = "wind_speed"
    RAIN = "rain"
    
    # Transmitter Subtypen - nur typisierte 1-Button Varianten (A/B/C/D)
    SINGLE_BUTTON_A = "single_button_a"  # 1-Button Type A
    SINGLE_BUTTON_B = "single_button_b"  # 1-Button Type B
    SINGLE_BUTTON_C = "single_button_c"  # 1-Button Type C
    SINGLE_BUTTON_D = "single_button_d"  # 1-Button Type D
    DUAL_BUTTON = "dual_button"
    TRIPLE_BUTTON = "triple_button"
    QUAD_BUTTON = "quad_button"
    
    UNKNOWN = "unknown"


class OperatingMode(Enum):
    """Betriebsarten für Geräte."""
    # Transmitter Modi
    ONE_BUTTON = "1_button"
    TWO_BUTTON = "2_button"
    THREE_BUTTON = "3_button"
    FOUR_BUTTON = "4_button"
    PUSH_BUTTON = "push_button"  # General push button mode
    
    # Receiver Modi
    SINGLE_CHANNEL = "single_channel"
    DUAL_CHANNEL = "dual_channel"
    QUAD_CHANNEL = "quad_channel"
    BIDIRECTIONAL = "bidirectional"  # For EWneo transceivers
    
    # Sensor Modi
    CONTINUOUS = "continuous"
    PERIODIC = "periodic"
    EVENT_TRIGGERED = "event_triggered"
    
    UNKNOWN = "unknown"


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


class BaseTransceiver(ABC):
    """Abstract base class for EASYWAVE transceivers."""
    
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


# Neue abstrakte Device-Klassen

class BaseDevice(ABC):
    """Abstrakte Basisklasse für alle Geräte."""
    
    def __init__(
        self,
        serial_number: str,
        device_type: DeviceType,
        subtype: DeviceSubtype = DeviceSubtype.UNKNOWN,
        operating_mode: OperatingMode = OperatingMode.UNKNOWN,
        name: str = None,
        **kwargs
    ):
        """Initialize the device."""
        self.serial_number = serial_number
        self.device_type = device_type
        self.subtype = subtype
        self.operating_mode = operating_mode
        self.name = name or self._generate_default_name()
        self.properties = kwargs
        self._last_seen = None
        self._battery_level = None
    
    def _generate_default_name(self) -> str:
        """Generate a default name based on device type and serial."""
        type_name = self.device_type.value.replace("_", " ").title()
        short_serial = self.serial_number if len(self.serial_number) > 6 else self.serial_number
        return f"{type_name} {short_serial}"
    
    @property
    @abstractmethod
    def supported_entity_types(self) -> List[str]:
        """Return list of supported Home Assistant entity types."""
        pass
    
    @property
    def device_info_dict(self) -> Dict[str, Any]:
        """Return device information for Home Assistant."""
        try:
            from ..const import usb_device_name
            mfr, _ = usb_device_name(0x155A, 0x1014)
        except Exception:
            mfr = "EASYWAVE EaS GmbH"
        return {
            "identifiers": {("easywave", self.serial_number)},
            "name": self.name,
            "manufacturer": mfr,
            "model": self._get_model_name(),
            "serial_number": self.serial_number,
            "sw_version": self.properties.get("firmware_version"),
        }
    
    def _get_model_name(self) -> str:
        """Get model name based on device type and subtype."""
        base_name = self.device_type.value.replace("_", " ").title()
        if self.subtype != DeviceSubtype.UNKNOWN:
            base_name += f" {self.subtype.value.title()}"
        return base_name
    
    @property
    def battery_level(self) -> Optional[int]:
        """Return battery level if available."""
        return self._battery_level
    
    @battery_level.setter
    def battery_level(self, value: Optional[int]) -> None:
        """Set battery level."""
        self._battery_level = value
    
    def update_properties(self, **kwargs) -> None:
        """Update device properties."""
        self.properties.update(kwargs)
    
    def get_state(self) -> Dict[str, Any]:
        """Get current device state for coordinator."""
        state = {
            "name": self.name,
            "device_type": self.device_type.value,
            "subtype": self.subtype.value if self.subtype != DeviceSubtype.UNKNOWN else None,
            "operating_mode": self.operating_mode.value if self.operating_mode != OperatingMode.UNKNOWN else None,
            "battery_level": self.battery_level,
            "last_seen": self._last_seen,
        }
        
        # Add device-specific properties
        state.update(self.properties)
        
        return state
    
    @abstractmethod
    def process_telegram(self, telegram_data: Dict[str, Any]) -> None:
        """Process incoming telegram for this device."""
        pass


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


class BaseTransmitter(BaseDevice):
    """Abstrakte Basisklasse für alle Transmitter."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._button_count = self._determine_button_count()
        self._button_states = {}
    
    def _determine_button_count(self) -> int:
        """Determine button count based on operating mode."""
        mode_to_buttons = {
            OperatingMode.ONE_BUTTON: 1,
            OperatingMode.TWO_BUTTON: 2,
            OperatingMode.THREE_BUTTON: 3,
            OperatingMode.FOUR_BUTTON: 4,
        }
        return mode_to_buttons.get(self.operating_mode, 4)
    
    @property
    def button_count(self) -> int:
        """Return number of buttons."""
        return self._button_count
    
    @property
    def button_states(self) -> Dict[int, Any]:
        """Return current button states."""
        return self._button_states
    
    def set_button_state(self, button: int, state: Any) -> None:
        """Set state for a specific button."""
        if 0 <= button < self._button_count:
            self._button_states[button] = state


class BaseSensor(BaseDevice):
    """Abstrakte Basisklasse für alle Sensoren."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sensor_values = {}
        self._last_reading = None
    
    @property
    def sensor_values(self) -> Dict[str, Any]:
        """Return current sensor values."""
        return self._sensor_values
    
    @property
    def last_reading(self) -> Optional[Any]:
        """Return timestamp of last reading."""
        return self._last_reading
    
    def update_sensor_value(self, sensor_type: str, value: Any) -> None:
        """Update a sensor value."""
        self._sensor_values[sensor_type] = value
        self._last_reading = self.properties.get("timestamp")
    
    def get_state(self) -> Dict[str, Any]:
        """Get current device state including sensor values."""
        state = super().get_state()
        
        # Add sensor-specific data
        state.update(self._sensor_values)
        state["sensor_values"] = self._sensor_values.copy()
        state["last_reading"] = self._last_reading
        
        return state
    
    @abstractmethod
    def get_measurement_unit(self, sensor_type: str) -> Optional[str]:
        """Get measurement unit for a sensor type."""
        pass


# ============================================================
# Entity Behavior Mixins - für Device-Klassen
# ============================================================

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


class SwitchBehaviorMixin:
    """Mixin für Switch-Verhalten (Ein/Aus)."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._switch_state = {}  # channel -> bool
    
    def get_switch_state(self, channel: int = 0) -> bool:
        """Get current switch state."""
        return self._switch_state.get(channel, False)
    
    def set_switch_state(self, channel: int, state: bool) -> None:
        """Set switch state internally."""
        self._switch_state[channel] = bool(state)
    
    async def async_turn_on(self, channel: int = 0) -> bool:
        """Turn on the switch."""
        self.set_switch_state(channel, True)
        return await self.set_state(channel, True)
    
    async def async_turn_off(self, channel: int = 0) -> bool:
        """Turn off the switch."""
        self.set_switch_state(channel, False)
        return await self.set_state(channel, False)
    
    async def async_toggle(self, channel: int = 0) -> bool:
        """Toggle the switch."""
        current = self.get_switch_state(channel)
        return await (self.async_turn_off(channel) if current else self.async_turn_on(channel))


class LightBehaviorMixin:
    """Mixin für Light-Verhalten (Dimmer)."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._light_state = {}  # channel -> bool (on/off)
        self._brightness = {}  # channel -> 0-255
    
    def get_light_state(self, channel: int = 0) -> bool:
        """Get current light state (on/off)."""
        return self._light_state.get(channel, False)
    
    def get_brightness(self, channel: int = 0) -> int:
        """Get current brightness (0-255)."""
        return self._brightness.get(channel, 255)
    
    def set_light_state(self, channel: int, state: bool) -> None:
        """Set light state internally."""
        self._light_state[channel] = bool(state)
    
    def set_brightness(self, channel: int, brightness: int) -> None:
        """Set brightness internally."""
        self._brightness[channel] = max(0, min(255, brightness))
        if brightness > 0:
            self.set_light_state(channel, True)
        else:
            self.set_light_state(channel, False)
    
    async def async_turn_on_light(self, channel: int = 0, brightness: Optional[int] = None) -> bool:
        """Turn on the light."""
        if brightness is not None:
            self.set_brightness(channel, brightness)
        else:
            self.set_light_state(channel, True)
        return await self.set_state(channel, {
            'state': True,
            'brightness': self.get_brightness(channel)
        })
    
    async def async_turn_off_light(self, channel: int = 0) -> bool:
        """Turn off the light."""
        self.set_light_state(channel, False)
        return await self.set_state(channel, {'state': False})
    
    @property
    def supports_brightness(self) -> bool:
        """Return if device supports brightness control."""
        return True


class SensorBehaviorMixin:
    """Mixin für Sensor-Verhalten (Temperatur, Feuchtigkeit, etc.)."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not hasattr(self, '_sensor_values'):
            self._sensor_values = {}
    
    def get_sensor_value(self, sensor_type: str) -> Optional[Any]:
        """Get current sensor value."""
        return self._sensor_values.get(sensor_type)
    
    def set_sensor_value(self, sensor_type: str, value: Any) -> None:
        """Set sensor value internally."""
        self._sensor_values[sensor_type] = value
    
    def get_temperature(self) -> Optional[float]:
        """Get temperature value."""
        return self.get_sensor_value('temperature')
    
    def get_humidity(self) -> Optional[float]:
        """Get humidity value."""
        return self.get_sensor_value('humidity')
    
    def get_wind_speed(self) -> Optional[float]:
        """Get wind speed value."""
        return self.get_sensor_value('wind_speed')
    
    def get_rain_rate(self) -> Optional[float]:
        """Get rain rate value."""
        return self.get_sensor_value('rain')


class ButtonBehaviorMixin:
    """Mixin für Button-Verhalten (Transmitter Buttons)."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_button_press = {}  # button_index -> timestamp
        self._button_press_count = {}  # button_index -> count
    
    def register_button_press(self, button_index: int) -> None:
        """Register a button press event."""
        import time
        self._last_button_press[button_index] = time.time()
        self._button_press_count[button_index] = self._button_press_count.get(button_index, 0) + 1
    
    def get_last_button_press(self, button_index: int) -> Optional[float]:
        """Get timestamp of last button press."""
        return self._last_button_press.get(button_index)
    
    def get_button_press_count(self, button_index: int) -> int:
        """Get total number of button presses."""
        return self._button_press_count.get(button_index, 0)
    
    async def async_press_button(self, button_index: int) -> bool:
        """Simulate button press."""
        self.register_button_press(button_index)
        # Override in concrete class to send actual command
        return True


class EntitySpecsMixin:
    """Mixin für automatische Entity-Spezifikations-Generierung."""
    
    def get_entity_specs(self) -> Dict[str, List[Dict[str, Any]]]:
        """Generate entity specifications for this device."""
        specs = {
            "switch": [],
            "light": [],
            "cover": [],
            "sensor": [],
            "binary_sensor": [],
            "button": []
        }
        
        # Wird von konkreten Device-Klassen überschrieben
        return specs
    
    def _create_base_entity_spec(self, entity_type: str, channel: int = 0, **kwargs) -> Dict[str, Any]:
        """Create a base entity specification."""
        from ..helpers_unique_id import make_unique_id
        
        channel_num = channel if channel > 0 else None
        
        # Use registration_id (UUID) as unique_id base
        reg_id = self._get_registration_id()
        
        # If name is explicitly None, don't set a default - HA will use device_class translation
        entity_name = kwargs.get("name")
        channel_suffix = f"_ch{channel}" if channel > 0 else ""
        if "name" not in kwargs:
            entity_name = f"{self.name} {entity_type.title()}{channel_suffix}"
        
        spec = {
            "type": entity_type,
            "name": entity_name,
            "unique_id": make_unique_id(reg_id, entity_type, channel_num),
            "channel": channel,
            "device_class": kwargs.get("device_class"),
            "icon": kwargs.get("icon"),
            "unit_of_measurement": kwargs.get("unit_of_measurement"),
            "has_entity_name": True,  # Always set for proper HA naming
        }
        
        # Remove None values (except has_entity_name which should stay)
        spec = {k: v for k, v in spec.items() if v is not None}
        
        return spec

    def _get_registration_id(self) -> str:
        """Get the full registration_id (UUID) for unique_id generation.
        
        Returns the UUID registration_id, or empty string if not available.
        """
        return self.properties.get("registration_id", "")