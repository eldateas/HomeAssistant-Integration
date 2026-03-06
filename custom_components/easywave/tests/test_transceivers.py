"""Tests for EASYWAVE transceiver base classes and factory."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.easywave.transceivers import TransceiverType
from custom_components.easywave.transceivers.base import (
    DeviceSubtype,
    DeviceType,
    OperatingMode,
    DeviceInfo,
    TransceiverCapabilities,
    BaseDevice,
    BaseReceiver,
    BaseSensor,
    BaseTransceiver,
    BaseTransmitter,
)
from custom_components.easywave.transceivers.factory import TransceiverFactory


# ═══════════════════════════════════════════════════════
# TransceiverType enum
# ═══════════════════════════════════════════════════════


class TestTransceiverType:
    def test_rx11_value(self) -> None:
        assert TransceiverType.RX11.value == "rx11"

    def test_rx11_name(self) -> None:
        assert TransceiverType.RX11.name == "RX11"


# ═══════════════════════════════════════════════════════
# DeviceType enum
# ═══════════════════════════════════════════════════════


class TestDeviceType:
    def test_has_common_types(self) -> None:
        assert hasattr(DeviceType, "EW_RECEIVER")
        assert hasattr(DeviceType, "EW_TRANSMITTER")
        assert hasattr(DeviceType, "EWNEO_SWITCH")
        assert hasattr(DeviceType, "UNKNOWN")

    def test_values_are_strings(self) -> None:
        for member in DeviceType:
            assert isinstance(member.value, str)


# ═══════════════════════════════════════════════════════
# DeviceSubtype enum
# ═══════════════════════════════════════════════════════


class TestDeviceSubtype:
    def test_has_common_subtypes(self) -> None:
        assert hasattr(DeviceSubtype, "SWITCH")
        assert hasattr(DeviceSubtype, "MOTOR")
        assert hasattr(DeviceSubtype, "DIMMER")
        assert hasattr(DeviceSubtype, "UNKNOWN")

    def test_values(self) -> None:
        assert DeviceSubtype.SWITCH.value == "switch"


# ═══════════════════════════════════════════════════════
# OperatingMode enum
# ═══════════════════════════════════════════════════════


class TestOperatingMode:
    def test_has_members(self) -> None:
        assert hasattr(OperatingMode, "ONE_BUTTON")
        assert hasattr(OperatingMode, "TWO_BUTTON")
        assert hasattr(OperatingMode, "UNKNOWN")

    def test_count(self) -> None:
        assert len(list(OperatingMode)) >= 5


# ═══════════════════════════════════════════════════════
# DeviceInfo dataclass
# ═══════════════════════════════════════════════════════


class TestDeviceInfo:
    def test_create_device_info(self) -> None:
        di = DeviceInfo(serial_number="AABB0001", device_type="ew_transmitter")
        assert di.serial_number == "AABB0001"
        assert di.device_type == "ew_transmitter"

    def test_optional_fields(self) -> None:
        di = DeviceInfo(serial_number="AABB0001", device_type="ew_receiver")
        assert di.name is None
        # capabilities defaults to {} via __post_init__
        assert di.capabilities == {}
        assert di.rx11_index is None

    def test_with_all_fields(self) -> None:
        di = DeviceInfo(
            serial_number="AABB0001",
            device_type="ew_receiver",
            name="Test",
            capabilities={"supports_learning": True},
            rx11_index=5,
        )
        assert di.name == "Test"
        assert di.rx11_index == 5


# ═══════════════════════════════════════════════════════
# TransceiverCapabilities dataclass
# ═══════════════════════════════════════════════════════


class TestTransceiverCapabilities:
    def test_create_defaults(self) -> None:
        tc = TransceiverCapabilities()
        assert tc.supports_learning is False
        assert tc.supports_bidirectional is False
        assert tc.max_devices == 255

    def test_create_custom(self) -> None:
        tc = TransceiverCapabilities(
            supports_learning=True,
            supports_bidirectional=True,
            max_devices=128,
        )
        assert tc.supports_learning is True
        assert tc.max_devices == 128


# ═══════════════════════════════════════════════════════
# BaseDevice abstract class
# ═══════════════════════════════════════════════════════


class TestBaseDevice:
    def _make_concrete_device(self, serial="AABB0001", name=None):
        class ConcreteDevice(BaseDevice):
            @property
            def supported_entity_types(self):
                return ["switch"]
            def process_telegram(self, telegram_data):
                pass

        return ConcreteDevice(serial, DeviceType.EW_TRANSMITTER, name=name or "Test")

    def test_create(self) -> None:
        dev = self._make_concrete_device()
        assert dev.serial_number == "AABB0001"
        assert dev.device_type == DeviceType.EW_TRANSMITTER
        assert dev.supported_entity_types == ["switch"]

    def test_battery_level(self) -> None:
        dev = self._make_concrete_device()
        dev.battery_level = 85
        assert dev.battery_level == 85

    def test_get_state(self) -> None:
        dev = self._make_concrete_device(name="TestDev")
        state = dev.get_state()
        assert isinstance(state, dict)
        assert state["name"] == "TestDev"
        assert state["device_type"] == "ew_transmitter"

    def test_default_name(self) -> None:
        dev = self._make_concrete_device(name="Custom")
        assert dev.name == "Custom"


# ═══════════════════════════════════════════════════════
# BaseReceiver
# ═══════════════════════════════════════════════════════


class TestBaseReceiver:
    def test_concrete_subclass(self) -> None:
        class ConcreteReceiver(BaseReceiver):
            @property
            def supported_entity_types(self):
                return ["switch"]
            def process_telegram(self, _):
                pass
            def set_state(self, channel, state):
                return True
            def get_state(self, channel=None):
                return None

        rcv = ConcreteReceiver("RCV001", DeviceType.EW_RECEIVER, name="Rcv")
        assert rcv.serial_number == "RCV001"
        rcv.add_learned_transmitter("TX001")
        assert "TX001" in rcv._learned_transmitters


# ═══════════════════════════════════════════════════════
# BaseTransmitter
# ═══════════════════════════════════════════════════════


class TestBaseTransmitter:
    def test_concrete_subclass(self) -> None:
        class ConcreteTX(BaseTransmitter):
            @property
            def supported_entity_types(self):
                return ["binary_sensor"]
            def process_telegram(self, _):
                pass

        tx = ConcreteTX("TX001", DeviceType.EW_TRANSMITTER, name="TX")
        tx.set_button_state(0, True)
        assert tx.get_button_state(0) is True
        assert tx.get_button_state(1) is None


# ═══════════════════════════════════════════════════════
# BaseSensor
# ═══════════════════════════════════════════════════════


class TestBaseSensor:
    def test_concrete_subclass(self) -> None:
        class ConcreteSensor(BaseSensor):
            @property
            def supported_entity_types(self):
                return ["sensor"]
            def process_telegram(self, _):
                pass
            def get_measurement_unit(self, sensor_type):
                return "°C" if sensor_type == "temperature" else None

        sensor = ConcreteSensor("SENS001", DeviceType.EWNEO_SENSOR, name="TempSens")
        sensor.update_sensor_value("temperature", 22.5)
        state = sensor.get_state()
        assert state["sensor_values"]["temperature"] == 22.5
        assert sensor.get_measurement_unit("temperature") == "°C"
        assert sensor.get_measurement_unit("unknown") is None


# ═══════════════════════════════════════════════════════
# TransceiverFactory
# ═══════════════════════════════════════════════════════


class TestTransceiverFactory:
    def test_rx11_in_registry(self) -> None:
        assert TransceiverType.RX11 in TransceiverFactory._registry

    def test_create_transceiver_rx11(self) -> None:
        tc = TransceiverFactory.create_transceiver(
            TransceiverType.RX11, "/dev/ttyUSB0"
        )
        assert tc is not None

    def test_create_transceiver_unknown_type(self) -> None:
        with pytest.raises((ValueError, KeyError)):
            TransceiverFactory.create_transceiver("unknown", "/dev/ttyUSB0")

    def test_register_custom_transceiver(self) -> None:
        """Test that registry accepts new transceiver types."""
        # Verify existing registry has RX11
        assert TransceiverType.RX11 in TransceiverFactory._registry
        # Direct dict insertion to avoid .value requirement on non-enum keys
        TransceiverFactory._registry["test_type"] = lambda p: None
        assert "test_type" in TransceiverFactory._registry
        del TransceiverFactory._registry["test_type"]


# ═══════════════════════════════════════════════════════
# Behavior Mixins
# ═══════════════════════════════════════════════════════


class TestBehaviorMixins:
    def test_switch_behavior_import(self) -> None:
        from custom_components.easywave.transceivers.behaviors import SwitchBehaviorMixin
        assert SwitchBehaviorMixin is not None

    def test_cover_behavior_import(self) -> None:
        from custom_components.easywave.transceivers.behaviors import CoverBehaviorMixin
        assert CoverBehaviorMixin is not None

    def test_light_behavior_import(self) -> None:
        from custom_components.easywave.transceivers.behaviors import LightBehaviorMixin
        assert LightBehaviorMixin is not None

    def test_button_behavior_import(self) -> None:
        from custom_components.easywave.transceivers.behaviors import ButtonBehaviorMixin
        assert ButtonBehaviorMixin is not None

    def test_sensor_behavior_import(self) -> None:
        from custom_components.easywave.transceivers.behaviors import SensorBehaviorMixin
        assert SensorBehaviorMixin is not None

    def test_entity_specs_mixin_import(self) -> None:
        from custom_components.easywave.transceivers.behaviors import EntitySpecsMixin
        assert EntitySpecsMixin is not None


# ═══════════════════════════════════════════════════════
# RX11 Device Registry
# ═══════════════════════════════════════════════════════


class TestRX11DeviceRegistry:
    def test_import_factory(self) -> None:
        from custom_components.easywave.transceivers.rx11.devices.registry import RX11DeviceFactory
        assert RX11DeviceFactory is not None

    def test_import_handler_registry(self) -> None:
        from custom_components.easywave.transceivers.rx11.devices.registry import RX11DeviceHandlerRegistry
        assert RX11DeviceHandlerRegistry is not None

    def test_get_device_class_for_info(self) -> None:
        from custom_components.easywave.transceivers.rx11.devices.registry import get_device_class_for_info
        result = get_device_class_for_info({"type": "unknown"})
        assert result is None or callable(result)


# ═══════════════════════════════════════════════════════
# BaseTransceiver properties
# ═══════════════════════════════════════════════════════


class TestBaseTransceiverProperties:
    def test_abstract_methods(self) -> None:
        import inspect
        from custom_components.easywave.transceivers.base.transceiver import BaseTransceiver
        abstract_methods = {
            name for name, _ in inspect.getmembers(BaseTransceiver)
            if getattr(getattr(BaseTransceiver, name, None), "__isabstractmethod__", False)
        }
        assert len(abstract_methods) >= 1
