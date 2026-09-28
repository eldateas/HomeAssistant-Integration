"""FD-serial index pools: 128 EW + 128 EWB, independent of transmitters."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "custom_components" / "easywave"


def _load_const():
    if "custom_components" not in sys.modules:
        pkg = type(sys)("custom_components")
        pkg.__path__ = [str(ROOT / "custom_components")]  # type: ignore[attr-defined]
        sys.modules["custom_components"] = pkg
    if "custom_components.easywave" not in sys.modules:
        ew = type(sys)("custom_components.easywave")
        ew.__path__ = [str(COMPONENT)]  # type: ignore[attr-defined]
        sys.modules["custom_components.easywave"] = ew

    mod_name = "custom_components.easywave.const"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, COMPONENT / "const.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


const = _load_const()


def _device(entry_type: str, **fields: object) -> SimpleNamespace:
    data = {const.CONF_ENTRY_TYPE: entry_type, **fields}
    return SimpleNamespace(data=data)


def _used_rx11(devices: list[SimpleNamespace]) -> set[int]:
    """Mirror coordinator.used_rx11_indices."""
    used: set[int] = set()
    for device in devices:
        if device.data.get(const.CONF_ENTRY_TYPE) != const.ENTRY_TYPE_RECEIVER:
            continue
        index = device.data.get(const.CONF_RX11_INDEX)
        if index is not None:
            used.add(int(index))
    return used


def _used_ewneo(devices: list[SimpleNamespace]) -> set[int]:
    """Mirror coordinator.used_ewneo_indices."""
    used: set[int] = set()
    for device in devices:
        if device.data.get(const.CONF_ENTRY_TYPE) != const.ENTRY_TYPE_NEO_ACTUATOR:
            continue
        index = device.data.get(const.CONF_EWNEO_INDEX)
        if index is not None:
            used.add(int(index))
    return used


async def _allocate(
    used: set[int],
    count: int,
    get_serial,
) -> tuple[int, bytes] | None:
    """Mirror coordinator.async_allocate_ew(_/b)_gateway."""
    for index in range(count):
        if index in used:
            continue
        serial = await get_serial(index)
        if serial is not None:
            return index, serial
    return None


def test_fd_serial_pool_sizes() -> None:
    """RX11 exposes 128 EW and 128 EWB factory-serial slots (0-127)."""
    assert const.EW_FD_SERIAL_INDEX_COUNT == 128
    assert const.EWB_FD_SERIAL_INDEX_COUNT == 128


def test_transmitters_and_sensors_do_not_consume_fd_indices() -> None:
    """Only receivers / neo actuators occupy EW / EWB index pools."""
    devices = [
        _device(const.ENTRY_TYPE_TRANSMITTER),
        _device(const.ENTRY_TYPE_NEO_SENSOR),
        *[_device(const.ENTRY_TYPE_TRANSMITTER) for _ in range(80)],
        _device(const.ENTRY_TYPE_RECEIVER, **{const.CONF_RX11_INDEX: 3}),
        _device(const.ENTRY_TYPE_NEO_ACTUATOR, **{const.CONF_EWNEO_INDEX: 7}),
    ]
    assert _used_rx11(devices) == {3}
    assert _used_ewneo(devices) == {7}


def test_ew_and_ewb_pools_are_independent() -> None:
    """Filling all EW slots must not block EWB index 0 (and vice versa)."""
    devices = [
        _device(const.ENTRY_TYPE_RECEIVER, **{const.CONF_RX11_INDEX: i})
        for i in range(const.EW_FD_SERIAL_INDEX_COUNT)
    ] + [
        _device(const.ENTRY_TYPE_NEO_ACTUATOR, **{const.CONF_EWNEO_INDEX: i})
        for i in range(10)
    ]
    assert len(_used_rx11(devices)) == 128
    assert _used_ewneo(devices) == set(range(10))
    assert 0 not in _used_ewneo(
        [
            _device(const.ENTRY_TYPE_RECEIVER, **{const.CONF_RX11_INDEX: i})
            for i in range(128)
        ]
    )


def test_allocate_skips_empty_serials_and_uses_slot_past_63() -> None:
    """Unreadable low slots must not surface as learn timeout; use next free serial."""

    async def get_serial(index: int) -> bytes | None:
        # Simulate stick where indices 0..63 are empty, but 64+ still work.
        if index < 64:
            return None
        return bytes([index]) * 16

    used = set(range(50))  # 50 neo actuators already learned
    result = asyncio.run(
        _allocate(used, const.EWB_FD_SERIAL_INDEX_COUNT, get_serial)
    )
    assert result is not None
    index, serial = result
    assert index == 64
    assert serial == bytes([64]) * 16


def test_allocate_exhausted_at_128() -> None:
    """Allocation fails only when all 0-127 slots are used or unreadable."""
    used = set(range(const.EWB_FD_SERIAL_INDEX_COUNT))

    async def get_serial(_index: int) -> bytes | None:
        return b"\x01" * 16

    assert (
        asyncio.run(_allocate(used, const.EWB_FD_SERIAL_INDEX_COUNT, get_serial))
        is None
    )

    used_sparse = {0, 1, 2}

    async def ok(index: int) -> bytes | None:
        return bytes([index & 0xFF]) * 16

    result = asyncio.run(_allocate(used_sparse, const.EW_FD_SERIAL_INDEX_COUNT, ok))
    assert result == (3, bytes([3]) * 16)
