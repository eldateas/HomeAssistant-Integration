"""Tests for the EASYWAVE integration setup and teardown."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywave.const import (
    DOMAIN,
    CONF_DEVICE_PATH,
    CONF_DEVICE_NAME,
    CONF_TRANSCEIVER_TYPE,
)
from custom_components.easywave import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
    async_migrate_entry,
    async_remove_config_entry_device,
)

from .conftest import (
    MOCK_VID,
    MOCK_PID,
    MOCK_SERIAL_NUMBER,
    MOCK_DEVICE_PATH,
    MOCK_DEVICE_NAME,
    MOCK_CONFIG_DATA,
    create_mock_transceiver,
    create_mock_coordinator,
)


# ─────── async_setup ─────────────────────────────────────────────────

async def test_async_setup(hass: HomeAssistant) -> None:
    """Test that async_setup returns True (YAML not supported)."""
    result = await async_setup(hass, {})
    assert result is True


# ─────── async_setup_entry ───────────────────────────────────────────

async def test_setup_entry_success(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test successful setup of a config entry."""
    mock_config_entry.add_to_hass(hass)

    mock_tc = create_mock_transceiver(connected=True)
    mock_coord = create_mock_coordinator(hass, mock_config_entry, connected=True)
    mock_coord.get_all_registered_devices.return_value = {}

    with (
        patch(
            "custom_components.easywave.TransceiverFactory.create_transceiver",
            return_value=mock_tc,
        ),
        patch(
            "custom_components.easywave.EasywaveCoordinator",
            return_value=mock_coord,
        ),
        patch(
            "custom_components.easywave._find_usb_device_path",
            return_value=(MOCK_DEVICE_PATH, {
                "device": MOCK_DEVICE_PATH,
                "vid": MOCK_VID,
                "pid": MOCK_PID,
                "serial_number": MOCK_SERIAL_NUMBER,
                "manufacturer": "ELDAT",
                "product": "RX11 USB Transceiver",
            }),
        ),
        patch(
            "custom_components.easywave.device_migration.migrate_to_device_manager",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "custom_components.easywave.entity_migration.migrate_entities_if_needed",
            new_callable=AsyncMock,
            return_value={"migrated_devices": 0, "recreated_entities": [], "duplicate_entities_removed": 0, "legacy_battery_sensors_removed": 0},
        ),
        patch(
            "custom_components.easywave.services.async_setup_services",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await async_setup_entry(hass, mock_config_entry)

    assert result is True
    assert DOMAIN in hass.data
    assert mock_config_entry.entry_id in hass.data[DOMAIN]


async def test_setup_entry_no_transceiver_type(
    hass: HomeAssistant,
) -> None:
    """Test setup fails when transceiver type is missing."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Easywave Gateway",
        data={
            CONF_DEVICE_PATH: MOCK_DEVICE_PATH,
            CONF_DEVICE_NAME: MOCK_DEVICE_NAME,
        },
        version=2,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.easywave._find_usb_device_path",
            return_value=(MOCK_DEVICE_PATH, {"device": MOCK_DEVICE_PATH}),
        ),
        pytest.raises(ConfigEntryNotReady, match="Transceiver type missing"),
    ):
        await async_setup_entry(hass, entry)


async def test_setup_entry_invalid_transceiver_type(
    hass: HomeAssistant,
) -> None:
    """Test setup fails with invalid transceiver type."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Easywave Gateway",
        data={
            CONF_TRANSCEIVER_TYPE: "invalid_type",
            CONF_DEVICE_PATH: MOCK_DEVICE_PATH,
            CONF_DEVICE_NAME: MOCK_DEVICE_NAME,
        },
        version=2,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.easywave._find_usb_device_path",
            return_value=(MOCK_DEVICE_PATH, {"device": MOCK_DEVICE_PATH}),
        ),
        pytest.raises(ConfigEntryNotReady, match="Invalid transceiver type"),
    ):
        await async_setup_entry(hass, entry)


async def test_setup_entry_double_setup_guard(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that double setup is prevented."""
    mock_config_entry.add_to_hass(hass)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = "already_setup"

    with patch(
        "custom_components.easywave._find_usb_device_path",
        return_value=(MOCK_DEVICE_PATH, {"device": MOCK_DEVICE_PATH}),
    ):
        result = await async_setup_entry(hass, mock_config_entry)

    assert result is True


# ─────── async_unload_entry ──────────────────────────────────────────

async def test_unload_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test unloading a config entry."""
    mock_config_entry.add_to_hass(hass)

    coordinator = create_mock_coordinator(hass, mock_config_entry)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    with (
        patch.object(
            hass.config_entries, "async_unload_platforms",
            return_value=True,
        ),
        patch(
            "custom_components.easywave.services.async_unload_services",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await async_unload_entry(hass, mock_config_entry)

    assert result is True
    assert mock_config_entry.entry_id not in hass.data[DOMAIN]
    coordinator.async_shutdown.assert_called_once()


async def test_unload_entry_no_coordinator(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test unloading when no coordinator is found."""
    mock_config_entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})

    with (
        patch.object(
            hass.config_entries, "async_unload_platforms",
            return_value=True,
        ),
        patch(
            "custom_components.easywave.services.async_unload_services",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await async_unload_entry(hass, mock_config_entry)

    assert result is True


# ─────── async_migrate_entry ─────────────────────────────────────────

async def test_migrate_entry_v1_to_v2(
    hass: HomeAssistant,
) -> None:
    """Test migration from version 1 to version 2."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Easywave Gateway",
        data={
            CONF_DEVICE_PATH: MOCK_DEVICE_PATH,
            CONF_DEVICE_NAME: MOCK_DEVICE_NAME,
        },
        version=1,
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)
    assert result is True
    assert entry.data.get(CONF_TRANSCEIVER_TYPE) == "rx11"


async def test_migrate_entry_already_v2(
    hass: HomeAssistant,
) -> None:
    """Test that version 2 entry doesn't need migration."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Easywave Gateway",
        data=MOCK_CONFIG_DATA.copy(),
        version=2,
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)
    assert result is True


# ─────── async_remove_config_entry_device ────────────────────────────

async def test_remove_device_success(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test removing a device from the integration."""
    mock_config_entry.add_to_hass(hass)

    coordinator = create_mock_coordinator(hass, mock_config_entry)
    coordinator.get_serial_by_ha_identifier.return_value = "AABBCCDDEEFF0011"
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    device_reg = dr.async_get(hass)
    device_entry = device_reg.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "test_device_id")},
        name="Test Device",
    )

    result = await async_remove_config_entry_device(
        hass, mock_config_entry, device_entry
    )
    assert result is True


async def test_remove_device_gateway_protected(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the RX11 gateway device cannot be removed."""
    mock_config_entry.add_to_hass(hass)

    coordinator = create_mock_coordinator(hass, mock_config_entry)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    device_reg = dr.async_get(hass)
    gateway_id = f"{mock_config_entry.entry_id}_gateway"
    device_entry = device_reg.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, gateway_id)},
        name="RX11 Gateway",
    )

    with pytest.raises(HomeAssistantError):
        await async_remove_config_entry_device(
            hass, mock_config_entry, device_entry
        )


async def test_remove_device_no_coordinator(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test remove device when coordinator is missing."""
    mock_config_entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})

    device_reg = dr.async_get(hass)
    device_entry = device_reg.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "orphan_device")},
        name="Orphan Device",
    )

    result = await async_remove_config_entry_device(
        hass, mock_config_entry, device_entry
    )
    assert result is False


async def test_remove_device_no_identifier(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test remove device when device has no EASYWAVE identifier."""
    mock_config_entry.add_to_hass(hass)

    coordinator = create_mock_coordinator(hass, mock_config_entry)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator

    device_reg = dr.async_get(hass)
    device_entry = device_reg.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={("other_domain", "other_id")},
        name="Other Device",
    )

    result = await async_remove_config_entry_device(
        hass, mock_config_entry, device_entry
    )
    assert result is True
