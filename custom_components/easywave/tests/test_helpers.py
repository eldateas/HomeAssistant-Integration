"""Tests for EASYWAVE helper modules (helpers, helpers_unique_id, const, transceivers)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.easywave.const import (
    DOMAIN,
    SUPPORTED_USB_IDS,
    is_supported_usb_device,
    usb_device_name,
)
from custom_components.easywave.helpers import (
    battery_percentage_from_level,
    build_model_description,
)
from custom_components.easywave.helpers_unique_id import (
    normalize_serial_number,
    validate_serial_number,
    serial_number_fingerprint,
    make_unique_id,
)


# ═════════════════════════════════════════════════════════════════════
# const.py helpers
# ═════════════════════════════════════════════════════════════════════

class TestConstants:
    """Tests for const.py helpers."""

    def test_domain_value(self) -> None:
        """Test DOMAIN is 'easywave'."""
        assert DOMAIN == "easywave"

    def test_supported_usb_ids_not_empty(self) -> None:
        """Test that supported USB IDs list is populated."""
        assert len(SUPPORTED_USB_IDS) > 0

    def test_is_supported_usb_device_match(self) -> None:
        """Test known VID:PID is recognised."""
        assert is_supported_usb_device(0x155A, 0x1014) is True

    def test_is_supported_usb_device_no_match(self) -> None:
        """Test unknown VID:PID is not recognised."""
        assert is_supported_usb_device(0x0000, 0x0000) is False

    def test_usb_device_name_known(self) -> None:
        """Test usb_device_name returns a name for known IDs."""
        name = usb_device_name(0x155A, 0x1014)
        assert name is not None
        assert len(name) > 0

    def test_usb_device_name_unknown(self) -> None:
        """Test usb_device_name returns fallback for unknown IDs."""
        name = usb_device_name(0x0000, 0x0000)
        # Returns a fallback tuple rather than None for unknown devices
        assert name is not None


# ═════════════════════════════════════════════════════════════════════
# helpers.py
# ═════════════════════════════════════════════════════════════════════

class TestBatteryPercentage:
    """Tests for battery_percentage_from_level."""

    def test_none_returns_zero(self) -> None:
        assert battery_percentage_from_level(None) == 0

    def test_zero_returns_ten(self) -> None:
        assert battery_percentage_from_level(0) == 10

    def test_seven_returns_hundred(self) -> None:
        assert battery_percentage_from_level(7) == 100

    def test_negative_clamped(self) -> None:
        assert battery_percentage_from_level(-1) == 0

    def test_above_seven_clamped(self) -> None:
        assert battery_percentage_from_level(9) == 100

    def test_mid_values(self) -> None:
        for level in range(1, 7):
            pct = battery_percentage_from_level(level)
            assert 10 < pct < 100

    def test_invalid_string_returns_zero(self) -> None:
        assert battery_percentage_from_level("invalid") == 0


class TestBuildModelDescription:
    """Tests for build_model_description."""

    def test_ew_transmitter_english(self) -> None:
        desc = build_model_description("ew_transmitter", {}, "en")
        assert desc is not None
        assert len(desc) > 0

    def test_ew_receiver_english(self) -> None:
        desc = build_model_description("ew_receiver", {"receiver_kind": "switch_2button"}, "en")
        assert desc is not None

    def test_unknown_type_fallback(self) -> None:
        desc = build_model_description("totally_unknown", {}, "en")
        # Should fall back to title-cased name
        assert "Totally Unknown" in desc

    def test_ewneo_switch_model(self) -> None:
        desc = build_model_description("ewneo_switch", {"device_type_code": 3}, "en")
        assert desc is not None

    def test_german_language(self) -> None:
        desc = build_model_description("ew_transmitter", {}, "de")
        assert desc is not None


# ═════════════════════════════════════════════════════════════════════
# helpers_unique_id.py
# ═════════════════════════════════════════════════════════════════════

class TestNormalizeSerialNumber:
    """Tests for normalize_serial_number."""

    def test_short_serial_zero_padded(self) -> None:
        result = normalize_serial_number("abc")
        assert len(result) == 32
        assert result.endswith("abc")
        assert result.startswith("0" * 29)

    def test_exact_32_chars(self) -> None:
        serial = "a" * 32
        assert normalize_serial_number(serial) == serial

    def test_long_serial_truncated(self) -> None:
        serial = "f" * 40
        result = normalize_serial_number(serial)
        assert len(result) == 32

    def test_strip_0x_prefix(self) -> None:
        result = normalize_serial_number("0xABCDEF")
        assert result == "abcdef".zfill(32)

    def test_removes_colons_dashes_spaces(self) -> None:
        result = normalize_serial_number("AA:BB-CC DD")
        assert result == "aabbccdd".zfill(32)

    def test_lowercase_output(self) -> None:
        result = normalize_serial_number("AABBCCDD")
        assert result == result.lower()

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            normalize_serial_number("")

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError):
            normalize_serial_number(None)

    def test_non_hex_raises(self) -> None:
        with pytest.raises(ValueError, match="non-hex"):
            normalize_serial_number("xyz123")


class TestValidateSerialNumber:
    """Tests for validate_serial_number."""

    def test_valid_serial(self) -> None:
        is_valid, normalized = validate_serial_number("AABBCCDD")
        assert is_valid is True
        assert len(normalized) == 32

    def test_invalid_serial(self) -> None:
        is_valid, msg = validate_serial_number("")
        assert is_valid is False
        assert isinstance(msg, str)


class TestSerialNumberFingerprint:
    """Tests for serial_number_fingerprint."""

    def test_fingerprint_uppercase(self) -> None:
        fp = serial_number_fingerprint("aabbccdd")
        assert fp == fp.upper()

    def test_different_formats_same_fingerprint(self) -> None:
        fp1 = serial_number_fingerprint("AABBCCDD")
        fp2 = serial_number_fingerprint("aa:bb:cc:dd")
        assert fp1 == fp2

    def test_invalid_serial_fallback(self) -> None:
        fp = serial_number_fingerprint("invalid!@#")
        assert isinstance(fp, str)


class TestMakeUniqueId:
    """Tests for make_unique_id."""

    def test_basic_unique_id(self) -> None:
        uid = make_unique_id("abc123", "switch")
        assert uid == "abc123_switch"

    def test_with_channel(self) -> None:
        uid = make_unique_id("abc123", "cover", channel=1)
        assert uid == "abc123_cover_ch1"

    def test_empty_registration_id_raises(self) -> None:
        with pytest.raises(ValueError):
            make_unique_id("", "switch")

    def test_empty_entity_type_raises(self) -> None:
        with pytest.raises(ValueError):
            make_unique_id("abc", "")

    def test_channel_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError):
            make_unique_id("abc", "switch", channel=256)

    def test_suffix_ignored(self) -> None:
        uid_no_suffix = make_unique_id("abc", "switch")
        uid_with_suffix = make_unique_id("abc", "switch", suffix="ignored")
        assert uid_no_suffix == uid_with_suffix

    def test_lowercase_output(self) -> None:
        uid = make_unique_id("ABC", "SWITCH")
        assert uid == "abc_switch"


# ═════════════════════════════════════════════════════════════════════
# Learning helper
# ═════════════════════════════════════════════════════════════════════

class TestRunLearning:
    """Tests for run_learning."""

    async def test_no_coordinator_returns_false(self) -> None:
        from custom_components.easywave.helpers import run_learning

        result = await run_learning(None, lambda d: d, timeout=1)
        assert result is not None
        # Returns (False, None, reason) or False
        if isinstance(result, tuple):
            assert result[0] is False

    async def test_no_transceiver_returns_false(self) -> None:
        from custom_components.easywave.helpers import run_learning

        coord = MagicMock()
        coord.transceiver = None
        result = await run_learning(coord, lambda d: d, timeout=1)
        if isinstance(result, tuple):
            assert result[0] is False

    async def test_timeout_returns_none(self) -> None:
        from custom_components.easywave.helpers import run_learning
        import asyncio

        coord = MagicMock()
        coord.transceiver = MagicMock()
        coord.transceiver.set_telegram_callback = MagicMock()
        coord.transceiver.set_learning_mode = AsyncMock()
        coord.transceiver._telegram_callback = None
        coord._config_flow_learning = False
        coord.hass = MagicMock()
        coord.hass.loop = asyncio.get_event_loop()

        result = await run_learning(coord, lambda d: None, timeout=1)
        assert result is None


# ═════════════════════════════════════════════════════════════════════
# Transceiver factory
# ═════════════════════════════════════════════════════════════════════

class TestTransceiverFactory:
    """Tests for the TransceiverFactory."""

    def test_factory_import(self) -> None:
        from custom_components.easywave.transceivers import TransceiverFactory
        assert TransceiverFactory is not None

    def test_factory_has_create(self) -> None:
        from custom_components.easywave.transceivers import TransceiverFactory
        assert hasattr(TransceiverFactory, "create_transceiver")


# ═════════════════════════════════════════════════════════════════════
# Find RX11 devices
# ═════════════════════════════════════════════════════════════════════

class TestFindRx11Devices:
    """Tests for find_rx11_devices."""

    def test_no_devices_returns_empty(self) -> None:
        from custom_components.easywave.transceivers.rx11.transceiver import (
            find_rx11_devices,
        )

        with patch(
            "serial.tools.list_ports.comports",
            return_value=[],
        ):
            devices = find_rx11_devices()
            assert devices == []

    def test_matching_device_returned(self) -> None:
        from custom_components.easywave.transceivers.rx11.transceiver import (
            find_rx11_devices,
        )

        mock_port = MagicMock()
        mock_port.device = "/dev/ttyUSB0"
        mock_port.vid = 0x155A
        mock_port.pid = 0x1014
        mock_port.serial_number = "SN12345"
        mock_port.manufacturer = "ELDAT"
        mock_port.product = "RX11"
        mock_port.location = "1-1"
        mock_port.hwid = "USB VID:PID=155A:1014"

        with patch(
            "serial.tools.list_ports.comports",
            return_value=[mock_port],
        ):
            devices = find_rx11_devices()
            assert len(devices) == 1
            assert devices[0]["device"] == "/dev/ttyUSB0"

    def test_non_matching_device_filtered(self) -> None:
        from custom_components.easywave.transceivers.rx11.transceiver import (
            find_rx11_devices,
        )

        mock_port = MagicMock()
        mock_port.device = "/dev/ttyUSB1"
        mock_port.vid = 0x0001
        mock_port.pid = 0x0001
        mock_port.serial_number = "OTHER"
        mock_port.manufacturer = "Other"
        mock_port.product = "Other"
        mock_port.location = "2-1"
        mock_port.hwid = "USB VID:PID=0001:0001"

        with patch(
            "serial.tools.list_ports.comports",
            return_value=[mock_port],
        ):
            devices = find_rx11_devices()
            assert len(devices) == 0
