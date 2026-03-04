"""Coordinator for EASYWAVE integration with transceiver modularity."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import aiofiles

from homeassistant.core import HomeAssistant, Event, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    DOMAIN,
    DEVICE_SCAN_INTERVAL,
    EVENT_DEVICE_ADDED,
    EVENT_DEVICE_REMOVED,
    EVENT_DEVICE_UPDATED,
    EVENT_DEVICE_STATE_UPDATE,
    EVENT_TELEGRAM_RECEIVED,
    EVENT_SENSOR_ADDED,
    CONF_DEVICE_PATH,
    CONF_TRANSCEIVER_TYPE,
    CONF_USB_SERIAL_NUMBER,
    CONF_USB_VID,
    CONF_USB_PID,
    BUTTON_LABELS,
    DEVICE_TYPE_CODE_MAP,
)
from .translations import (
    translate,
    get_button_label,
    get_language,
    DEFAULT_LANGUAGE,
)
from .transceivers import TransceiverFactory, TransceiverType, BaseTransceiver
from .device_config import DeviceConfigManager
from .device_manager import DeviceManager, ManagedDevice, DeviceAvailability, DeviceType
from .device_migration import migrate_to_device_manager
from .device_lifecycle import DeviceLifecycleManager, DeviceLifecycleStateEnum
from .state_manager import StateManager
from .entity_specs import create_entity_specs_for_device
from .helpers_unique_id import make_unique_id
from .index_allocator import IndexAllocator
from .persistence_transaction import PersistenceTransaction, TransactionalPersistenceManager
from .persistence_integrity_checker import PersistenceIntegrityChecker
from .defensive_state_manager import DefensiveStateManager

_LOGGER = logging.getLogger(__name__)


class EasywaveCoordinator(DataUpdateCoordinator):
    """Data coordinator for EASYWAVE with transceiver modularity."""

    def __init__(
        self,
        hass: HomeAssistant,
        transceiver: BaseTransceiver,
        config_entry,
        update_interval: timedelta = DEVICE_SCAN_INTERVAL,
    ):
        """Initialize the coordinator."""
        self.transceiver = transceiver
        self.config_entry = config_entry
        self.devices: Dict[str, Dict[str, Any]] = {}
        self._known_devices: Set[str] = set()
        self._config_flow_learning = False
        self._setup_mode_active = False
        self._setup_timeout = None
        self.device_config_manager = DeviceConfigManager(hass, config_entry.entry_id)
        # Use new DeviceManager from DeviceConfigManager
        self.device_manager = self.device_config_manager.device_manager
        
        # Initialize Device Lifecycle Manager for structured lifecycle management
        self.device_lifecycle_manager = DeviceLifecycleManager(hass)
        
        # NEW: Initialize unified Index Allocator (replaces old separate index tracking)
        self.index_allocator = IndexAllocator(hass, config_entry.entry_id)
        
        # NEW: Initialize Persistence Integrity Checker
        self.integrity_checker = PersistenceIntegrityChecker(
            hass,
            self.device_manager,
            self.index_allocator,
            config_entry.entry_id
        )
        
        # NEW: Transaction manager for atomic persistence
        self.persistence_manager = TransactionalPersistenceManager(timeout=30.0)
        
        # NEW: Defensive State Manager (simplified API for robust persistence)
        self.defensive_state_manager = DefensiveStateManager(
            hass,
            self.device_manager,
            self.index_allocator,
            self.integrity_checker,
            config_entry.entry_id
        )
        
        # Central registered device management - only these devices will be managed
        self._registered_devices: Dict[str, Dict[str, Any]] = {}
        self._registered_devices_file = f"{hass.config.config_dir}/easywave/registered_devices.json"
        # Index tracking (populated by _load_registered_devices)
        self._used_ewb_indices: Dict[int, Dict[str, Any]] = {}
        self._used_ew_receiver_indices: Dict[int, Dict[str, Any]] = {}
        self._next_free_ewb_index: int = 0
        self._next_free_ew_receiver_index: int = 0
        # STRUCTURAL SAFEGUARD: Track entity counts from last save to detect mass entity loss
        self._last_saved_entity_counts: Dict[str, int] = {}
        
        # Entity persistence manager for dashboard entities
        self.state_manager = StateManager(
            hass, 
            config_entry.entry_id
        )
        
        # Device backup system for restoration
        self._device_backup: Dict[str, Dict[str, Any]] = {}
        self._setup_complete: bool = False  # Track if setup is complete
        
        # ═══ CENTRAL EVENT DISPATCHER ═══
        # Platform handlers - platforms register themselves here for centralized event handling
        self._platform_handlers: Dict[str, callable] = {}  # platform_name → async handler function
        # Track which devices we've already dispatched (prevent duplicate dispatch)
        self._dispatched_devices: Set[str] = set()
        
        # Telegram listener
        self._telegram_listener_remove = None
        self._shutdown_event = asyncio.Event()

        # Initialize coordinator state
        self.last_update_success = False
        self._update_count = 0  # Track number of updates for availability grace period
        self._last_connection_state = None  # Track connection state changes
        self._cached_hw_version = None  # Cache hardware version
        self._cached_fw_version = None  # Cache firmware version
        
        # EWneo device reachability tracking
        # Key: device_serial (not channel-specific), Value: consecutive failure count
        self._ewneo_failure_counts: Dict[str, int] = {}
        # Track devices that already have an active unreachable notification
        self._ewneo_unreachable_notified: Set[str] = set()
        
        # Track which devices have already had their entity events fired
        self._devices_with_fired_events: Set[str] = set()
        
        # Reconnect state
        self._reconnect_attempts: int = 0
        self._last_reconnect_time: float = 0
        
        # Background task handles
        self._ewb_monitoring_task = None
        self._usb_monitor_task = None
        self._usb_event_detected: bool = False

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )

    def start_setup_mode(self, timeout_seconds: int = 60):
        """Start device setup mode to allow registration of new devices."""
        self._setup_mode_active = True
        
        # Cancel previous timeout if exists
        if self._setup_timeout:
            self._setup_timeout.cancel()
        
        # Set timeout to automatically stop setup mode
        async def stop_setup():
            await asyncio.sleep(timeout_seconds)
            self.stop_setup_mode()
        
        self._setup_timeout = self.hass.async_create_task(stop_setup())
        _LOGGER.info("Device setup mode activated for %d seconds", timeout_seconds)
    
    def stop_setup_mode(self):
        """Stop device setup mode."""
        self._setup_mode_active = False
        if self._setup_timeout:
            self._setup_timeout.cancel()
            self._setup_timeout = None

    async def report_ewneo_communication_failure(self, device_serial: str) -> bool:
        """Report a communication failure for an EWneo device.
        
        Tracks consecutive failures per device (not per channel).
        After 2 consecutive failures, creates a persistent notification.
        The notification is re-created on each failure after threshold to ensure
        it reappears if the user dismissed it manually.
        
        Args:
            device_serial: The device serial number
            
        Returns:
            True if this failure triggered a notification (threshold reached)
        """
        # Increment failure count
        self._ewneo_failure_counts[device_serial] = self._ewneo_failure_counts.get(device_serial, 0) + 1
        failure_count = self._ewneo_failure_counts[device_serial]
        
        _LOGGER.debug("EWneo device %s: Communication failure #%d", device_serial[-8:], failure_count)
        
        # Check if threshold reached - always create notification (same ID prevents duplicates,
        # but re-creates it if user dismissed it manually)
        if failure_count >= 2:
            self._ewneo_unreachable_notified.add(device_serial)
            
            # Delay notification slightly to ensure device registry is fully loaded at startup
            # This ensures we get the user-defined device name instead of the default
            async def _create_delayed_notification():
                await asyncio.sleep(2)  # Wait for device registry to be fully available
                
                # Get user-defined device name from HA device registry
                friendly_name = await self._get_device_friendly_name(device_serial)
                
                # Get translated notification strings
                lang = get_language(self.hass)
                notification_title = translate("notification.ewneo_unreachable_title", lang)
                notification_message = translate("notification.ewneo_unreachable_message", lang, device_name=friendly_name)
                
                # Create persistent notification
                await self.hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "notification_id": f"easywave_ewneo_unreachable_{device_serial}",
                        "title": notification_title,
                        "message": notification_message,
                    },
                    blocking=False,
                )
                
                _LOGGER.warning("⚠️ EWneo device '%s' (%s) is unreachable after %d failures", 
                               friendly_name, device_serial[-8:], failure_count)
            
            # Schedule the notification with delay
            asyncio.create_task(_create_delayed_notification())
            return True
        
        return False

    async def report_ewneo_communication_success(self, device_serial: str) -> None:
        """Report a successful communication with an EWneo device.
        
        Resets failure counter and dismisses any active unreachable notification.
        
        Args:
            device_serial: The device serial number
        """
        # Reset failure count
        if device_serial in self._ewneo_failure_counts:
            del self._ewneo_failure_counts[device_serial]
        
        # Dismiss notification if one was active
        if device_serial in self._ewneo_unreachable_notified:
            self._ewneo_unreachable_notified.discard(device_serial)
            
            # Dismiss the persistent notification
            await self.hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {
                    "notification_id": f"easywave_ewneo_unreachable_{device_serial}",
                },
                blocking=False,
            )
            
            # Get user-defined device name for logging
            friendly_name = await self._get_device_friendly_name(device_serial)
            _LOGGER.info("✅ EWneo device '%s' (%s) is reachable again", friendly_name, device_serial[-8:])

    async def _get_device_friendly_name(self, device_serial: str) -> str:
        """Get the user-defined friendly name for a device from HA device registry.
        
        Args:
            device_serial: The device serial number
            
        Returns:
            User-defined name, or fallback to default name, or serial suffix
        """
        try:
            # Use _find_ha_device_entry to support both UUID and serial identifiers
            device = self._find_ha_device_entry(device_serial)
            
            if device:
                if device.name_by_user:
                    _LOGGER.debug("Found user-defined name '%s' for device %s", device.name_by_user, device_serial[-8:])
                    return device.name_by_user
                elif device.name:
                    _LOGGER.debug("Found default name '%s' for device %s", device.name, device_serial[-8:])
                    return device.name
            else:
                _LOGGER.debug("Device not found in registry for serial %s", device_serial[-8:])
            
            # Fallback: Check our internal device info
            device_info = self.devices.get(device_serial, {})
            if device_info.get("name"):
                _LOGGER.debug("Using internal device name '%s' for %s", device_info["name"], device_serial[-8:])
                return device_info["name"]
            
            # Final fallback
            _LOGGER.debug("No name found for device %s, using serial suffix", device_serial[-8:])
            return translate("config_flow.device_fallback_name", hass=self.hass, serial=device_serial[-8:])
            
        except Exception as e:
            _LOGGER.warning("Could not get friendly name for %s: %s", device_serial[-8:], e)
            return translate("config_flow.device_fallback_name", hass=self.hass, serial=device_serial[-8:])

    def _get_transmitter_action_label(self, serial_number: str, button: int | None) -> str | None:
        """Return semantic action label for a transmitter button.
        
        Returns UNTRANSLATED state keys (like "on", "off", "up", "down", "stop")
        that match the entity options. Translation is handled by Home Assistant's
        frontend via translations/*.json files.
        """
        if button is None:
            return None

        device_info = self.devices.get(serial_number, {})
        operating_type = device_info.get("operating_type", "1")
        usage_type = device_info.get("usage_type", "switch")
        button_count = device_info.get("button_count", 4)
        
        # Use UNTRANSLATED state keys - HA translates these in the frontend
        # This ensures sensor states match the entity options

        if operating_type == "2":
            is_switch_mode = usage_type == "switch"
            if is_switch_mode:
                if button_count <= 2:
                    return "on" if button == 0 else "off" if button == 1 else None
                if button in (0, 1):
                    return "on" if button == 0 else "off"
                if button in (2, 3):
                    return "on" if button == 2 else "off"
            else:
                if button_count <= 2:
                    return "up" if button == 0 else "down" if button == 1 else None
                if button in (0, 1):
                    return "up" if button == 0 else "down"
                if button in (2, 3):
                    return "up" if button == 2 else "down"

        if operating_type == "3":
            if button == 0:
                return "up"
            if button == 1:
                return "down"
            if button in (2, 3):
                return "stop"

        # For 1-button mode, return button letter (a, b, c, d) for proper HA translation
        button_letters = ["a", "b", "c", "d"]
        if 0 <= button < len(button_letters):
            return button_letters[button]
        
        return None

    def _translate_action_label(self, action_label: str | None) -> str:
        """Translate action label for display in logs and UI.
        
        Translates raw state keys to localized labels for logbook/activity display.
        """
        if action_label is None:
            return translate("state_translated.unknown", hass=self.hass)
        
        translated = translate(f"state_translated.{action_label}", hass=self.hass)
        # If translate returned the key itself (no translation found), return original
        if translated == f"state_translated.{action_label}":
            # Try button labels for a, b, c, d
            button_translated = translate(f"button.{action_label}", hass=self.hass)
            if button_translated != f"button.{action_label}":
                return button_translated
            return action_label
        return translated
        _LOGGER.info("Device setup mode deactivated")
    
    def _validate_device_config(self, serial_number: str, device_info: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate that a device has minimal required configuration.
        
        Returns: (is_valid, error_message)
        """
        device_type = device_info.get('device_type')
        device_name = device_info.get('name', serial_number[-8:])
        entities = device_info.get('entities', [])
        
        # All devices must have at least one entity
        if not entities:
            return False, f"No entities defined for {device_name} (will be removed)"
        
        # Transmitters must have operating_type set
        if device_type == 'ew_transmitter':
            operating_type = device_info.get('operating_type')
            if operating_type is None or operating_type == '':
                return False, f"Transmitter {device_name} has no operating_type set (will be removed)"
        
        # EW Receivers must have receiver_kind or operating_mode
        if device_type == 'ew_receiver':
            receiver_kind = device_info.get('receiver_kind')
            operating_mode = device_info.get('operating_mode')
            if not receiver_kind and not operating_mode:
                return False, f"EW Receiver {device_name} has no receiver_kind or operating_mode (will be removed)"
        
        return True, None
    
    async def _validate_and_clean_devices(self, devices: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """Validate all devices and remove or fix invalid ones.
        
        Returns: (cleaned_devices_dict, any_changes_made)
        """
        invalid_serials = []
        changes_made = False
        
        for serial_number, device_info in list(devices.items()):
            is_valid, error_msg = self._validate_device_config(serial_number, device_info)
            
            if not is_valid:
                _LOGGER.warning("⚠️ Device validation failed: %s", error_msg)
                invalid_serials.append(serial_number)
                changes_made = True
        
        # Remove invalid devices
        for serial in invalid_serials:
            device_name = devices[serial].get('name', 'Unknown')
            del devices[serial]
            _LOGGER.error("❌ Removed invalid device: %s (will need to be re-learned)", device_name)
        
        return devices, changes_made
    
    async def _load_registered_devices(self) -> None:
        """Load registered devices from persistent storage."""
        import json
        import aiofiles
        import os
        import re
        try:
            if os.path.exists(self._registered_devices_file):
                async with aiofiles.open(self._registered_devices_file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                    loaded_devices = data.get('devices', {})
                    devices_updated = False
                    
                    # ══════════════════════════════════════════════════════════
                    # STEP 0: Ensure ALL devices have a registration_id.
                    # Very old (pre-v0.6.4) devices may have been stored
                    # without one.  Assigning a stable UUID here prevents
                    # KeyError crashes in entity.py and allows the entity
                    # migration below to produce correct new-format unique_ids.
                    # ══════════════════════════════════════════════════════════
                    import uuid as _uuid_mod
                    for serial_number, device_info in loaded_devices.items():
                        if not device_info.get("registration_id"):
                            new_uuid = _uuid_mod.uuid4().hex
                            device_info["registration_id"] = new_uuid
                            devices_updated = True
                            _LOGGER.info(
                                "🆔 Assigned registration_id to legacy device %s: %s",
                                serial_number[-8:], new_uuid[:8],
                            )
                    
                    # Validate entity specs for backwards compatibility
                    # Ensures v6.4 entities with serial-based unique_ids get regenerated
                    # to registration_id-based unique_ids (current format).
                    for serial_number, device_info in loaded_devices.items():
                        registration_id = device_info.get("registration_id", "")
                        entities = device_info.get("entities", [])
                        
                        if not registration_id or not entities:
                            continue
                        
                        # Check if entities already use registration_id as prefix.
                        # v0.6.4 used serial_number as prefix; current uses registration_id.
                        # Also check for old hash suffix (_{md5_6hex}) which v0.6.4 appended.
                        uses_new_format = any(
                            entity.get("unique_id", "").startswith(registration_id)
                            for entity in entities
                        )
                        
                        if not uses_new_format:
                            _LOGGER.info("🔄 Device %s needs entity regeneration (serial-based unique_ids → registration_id-based)", 
                                       serial_number[-8:])
                            device_info = await self._regenerate_entity_specs(
                                serial_number, device_info
                            )
                            loaded_devices[serial_number] = device_info
                            devices_updated = True
                    
                    self._registered_devices = loaded_devices
                    
                    # Validate all devices and remove invalid ones
                    cleaned_devices, validation_changed = await self._validate_and_clean_devices(loaded_devices)
                    self._registered_devices = cleaned_devices
                    loaded_devices = cleaned_devices  # Update loaded_devices reference too
                    devices_updated = devices_updated or validation_changed
                    
                    # Initialize entity count tracking from loaded state
                    self._last_saved_entity_counts = {
                        serial: len(info.get("entities", []))
                        for serial, info in loaded_devices.items()
                    }
                    
                    # ══════════════════════════════════════════════════════════
                    # Build ALL indices from device data BEFORE any save call.
                    # This ensures every _save_registered_devices() persists
                    # correct index data — fixes the "all receivers get index 0"
                    # bug caused by saving empty indices during early validation.
                    # ══════════════════════════════════════════════════════════
                    
                    # Rebuild used_ewb_indices from device data (source of truth)
                    # This ensures all EWneo devices are properly tracked
                    indices_from_devices = {}
                    invalid_indices = []  # Track indices with conflicts
                    for serial, device_info in loaded_devices.items():
                        if device_info.get('neo_device') and 'ewneo_index' in device_info:
                            index = device_info['ewneo_index']
                            if index is None:
                                continue
                            gateway_serial = device_info.get('gateway_serial')
                            device_name = device_info.get('name', f"EWneo ({serial})")
                            
                            # Check for duplicate indices
                            if index in indices_from_devices:
                                _LOGGER.error("❌ DUPLICATE INDEX DETECTED: Index %d used by both %s and %s!", 
                                            index, indices_from_devices[index]['device_serial'][-8:], serial[-8:])
                                _LOGGER.warning("⚠️ Device %s has duplicate index %d. Delete and re-learn one of these devices to fix.", 
                                              serial[-8:], index)
                                invalid_indices.append(serial)
                                # Skip this device (don't reassign - user must fix)
                                continue
                            
                            indices_from_devices[index] = {
                                'gateway_serial': gateway_serial,
                                'device_serial': serial,
                                'device_name': device_name,
                                'created_at': device_info.get('registered_at', datetime.now().isoformat())
                            }
                    
                    # Build used_ewb_indices from device data (source of truth)
                    # Never auto-correct or reassign indices - user must delete and re-learn to fix
                    self._used_ewb_indices = indices_from_devices.copy() if indices_from_devices else {}
                    self._next_free_ewb_index = self._find_next_free_ewb_index()
                    
                    _LOGGER.info("📋 Loaded EWB indices: %d devices (next free: %d)", 
                               len(indices_from_devices), self._next_free_ewb_index)
                    
                    if invalid_indices:
                        _LOGGER.warning("⚠️ Found %d devices with index conflicts. These must be fixed by deleting and re-learning.", 
                                      len(invalid_indices))
                    
                    # Load EW Receiver indices from device data (source of truth)
                    # Do NOT auto-correct or reassign - user must delete and re-learn to fix
                    _LOGGER.info("🔄 Loading EW Receiver indices from device data...")
                    ew_receiver_indices_from_devices = {}
                    ew_receiver_invalid = []
                    for serial, device_info in loaded_devices.items():
                        if device_info.get('device_type') == 'ew_receiver' and 'rx11_index' in device_info:
                            index = device_info['rx11_index']
                            # Guard against null/None rx11_index values
                            if index is None:
                                _LOGGER.warning("⚠️ EW Receiver %s has rx11_index=None — skipping index tracking", serial[-8:])
                                continue
                            device_name = device_info.get('name', f"EW Receiver ({serial})")
                            
                            # Check for duplicate indices
                            if index in ew_receiver_indices_from_devices:
                                _LOGGER.error("❌ DUPLICATE EW RECEIVER INDEX DETECTED: Index %d used by both %s and %s!", 
                                            index, ew_receiver_indices_from_devices[index]['device_serial'][-8:], serial[-8:])
                                _LOGGER.warning("⚠️ Device %s has duplicate index %d. Delete and re-learn one of these devices to fix.", 
                                              serial[-8:], index)
                                ew_receiver_invalid.append(serial)
                                # Skip this device (don't reassign - user must fix)
                                continue
                            
                            # Store metadata
                            ew_receiver_indices_from_devices[index] = {
                                'device_serial': serial,
                                'device_name': device_name,
                                'receiver_serial': device_info.get('serial_number'),
                                'created_at': device_info.get('registered_at', datetime.now().isoformat())
                            }
                    
                    # Use device data as source of truth
                    self._used_ew_receiver_indices = ew_receiver_indices_from_devices.copy()
                    self._next_free_ew_receiver_index = self._find_next_free_ew_receiver_index()
                    
                    _LOGGER.info("📋 Loaded EW Receiver indices: %d devices (next free: %d)", 
                               len(ew_receiver_indices_from_devices), self._next_free_ew_receiver_index)
                    
                    if ew_receiver_invalid:
                        _LOGGER.warning("⚠️ Found %d EW Receiver devices with index conflicts. These must be fixed by deleting and re-learning.", 
                                      len(ew_receiver_invalid))
                    
                    # NOW save — indices are populated, so the file will contain correct data
                    if validation_changed:
                        await self._save_registered_devices()
                        _LOGGER.info("💾 Saved device list after validation (removed %d invalid devices)", 
                                   len(loaded_devices) - len(data.get('devices', {})))
                    
                    _LOGGER.info("📋 Loaded %d registered devices from storage", len(self._registered_devices))
                    if devices_updated:
                        await self._save_registered_devices()
                        _LOGGER.info("💾 Updated registered devices with regenerated entity specs")
            else:
                _LOGGER.info("📋 No registered devices file found, starting with empty list")
        except Exception as e:
            _LOGGER.error("❌ Failed to load registered devices: %s", e, exc_info=True)
            # CRITICAL: Do NOT reset _registered_devices to {} here!
            # If partial data was loaded before the exception, keep it.
            # If nothing was loaded yet, _registered_devices is already {} from __init__.
            # Setting it to {} here caused data loss: migration would run and
            # overwrite registered_devices.json with stale managed_devices data.
            _LOGGER.warning("⚠️ Keeping current _registered_devices state (%d devices) after load error",
                          len(self._registered_devices))

    async def _cleanup_old_entities_for_device(self, serial_number: str, device_info: Optional[Dict[str, Any]] = None) -> None:
        """Clean up old entities for a device that is being re-learned.
        
        This removes all entities whose unique_id starts with the serial_number,
        ensuring that re-learning creates fresh entities without old logbook entries.
        Also clears old sensor values and state history.
        
        Uses startswith() instead of substring matching to prevent
        accidental removal of entities from other devices.
        
        Args:
            serial_number: The device serial number.
            device_info: Optional device_info dict. When provided, the
                registration_id is read directly from it instead of looking
                up _registered_devices. This is needed for NEW devices that
                haven't been stored yet.
        """
        try:
            from homeassistant.helpers import entity_registry as er
            
            entity_registry = er.async_get(self.hass)
            
            # Find all entities for this device (match by UUID or legacy serial)
            # NOTE: serial_number is UPPERCASE but unique_ids are lowercase — normalize
            serial_lower = serial_number.lower()
            # Get registration_id: prefer passed device_info (for new devices not yet
            # in _registered_devices), fall back to _registered_devices lookup.
            reg_id = None
            if device_info:
                reg_id = device_info.get('registration_id')
            if not reg_id:
                stored = self._registered_devices.get(serial_number, {})
                reg_id = stored.get('registration_id')
            ha_identifier = reg_id.lower() if reg_id else None
            entities_to_remove = []
            for entity_id, entry in entity_registry.entities.items():
                if entry.unique_id and entry.platform == DOMAIN:
                    uid_lower = entry.unique_id.lower()
                    if (ha_identifier and uid_lower.startswith(ha_identifier)) or uid_lower.startswith(serial_lower):
                        entities_to_remove.append((entity_id, entry.unique_id))
            
            if entities_to_remove:
                _LOGGER.info("🧹 Removing %d old entities for device %s before re-learning", 
                           len(entities_to_remove), serial_number[-8:])
                
                # Collect entity_ids for history purge
                entity_ids_for_purge = []
                
                for entity_id, unique_id in entities_to_remove:
                    entity_ids_for_purge.append(entity_id)
                    entity_registry.async_remove(entity_id)
                    # Also clear from entity creation tracking so re-learned entities
                    # are not skipped as "already created"
                    self.state_manager.unmark_entity_deleted(unique_id)
                    _LOGGER.debug("  - Removed entity: %s (unique_id: %s)", entity_id, unique_id[-20:])
                
                # Try to purge history/recorder data for these entities
                await self._purge_entity_history(entity_ids_for_purge)
            else:
                _LOGGER.debug("🧹 No old entities to remove for device %s", serial_number[-8:])
            
            # Remove any leftover HA device entry from a previous registration.
            # With UUID-based identifiers the new device will get a fresh HA entry
            # anyway, but we still clean up the old one to avoid confusion.
            import homeassistant.helpers.device_registry as dr
            device_registry = dr.async_get(self.hass)
            old_device_entry = self._find_ha_device_entry(serial_number)
            if old_device_entry:
                _LOGGER.info("🧹 Removing leftover HA device entry for %s",
                           serial_number[-8:])
                device_registry.async_remove_device(old_device_entry.id)
                await asyncio.sleep(0.1)
            
            # Clear old sensor values from coordinator.devices
            if serial_number in self.devices:
                old_device = self.devices[serial_number]
                # Clear measurement values but keep basic info
                for key in ["temperature", "humidity", "battery_level", "last_telegram", 
                           "last_seen", "sensor_values", "last_reading"]:
                    if key in old_device:
                        del old_device[key]
                _LOGGER.debug("🧹 Cleared old sensor values from devices dict for %s", serial_number[-8:])
            
            # Clear old values from _registered_devices
            if serial_number in self._registered_devices:
                old_reg = self._registered_devices[serial_number]
                for key in ["temperature", "humidity", "battery_level", "last_telegram",
                           "last_seen", "sensor_values", "last_reading", "entities"]:
                    if key in old_reg:
                        del old_reg[key]
                _LOGGER.debug("🧹 Cleared old values from registered_devices for %s", serial_number[-8:])
            
            # Clear old values from DeviceManager (managed_devices.json)
            if self.device_manager.is_whitelisted(serial_number):
                dm_device = self.device_manager.get_device(serial_number)
                if dm_device:
                    # Clear measurement values from device
                    keys_to_clear = ["temperature", "humidity", "battery_level", "last_telegram",
                                    "last_seen", "sensor_values", "last_reading"]
                    changed = False
                    for key in keys_to_clear:
                        if hasattr(dm_device, key) and getattr(dm_device, key) is not None:
                            setattr(dm_device, key, None)
                            changed = True
                    
                    # Also clear from extra_data
                    if dm_device.extra_data:
                        for key in keys_to_clear + ["entities"]:
                            if key in dm_device.extra_data:
                                del dm_device.extra_data[key]
                                changed = True
                    
                    if changed:
                        await self.device_manager.save()
                        _LOGGER.debug("🧹 Cleared old values from DeviceManager for %s", serial_number[-8:])
                
        except Exception as e:
            _LOGGER.warning("⚠️ Failed to cleanup old entities for %s: %s", serial_number[-8:], e)

    async def _purge_entity_history(self, entity_ids: list) -> None:
        """Purge history/recorder data for specific entities.
        
        This ensures old logbook entries and state history are removed
        when a device is re-learned.
        """
        if not entity_ids:
            return
            
        try:
            # Check if recorder is available
            if "recorder" not in self.hass.data:
                _LOGGER.warning("⚠️ Recorder not available, cannot purge history")
                return
            
            _LOGGER.info("🧹 Purging history for %d entities: %s", len(entity_ids), entity_ids)
            
            # Use recorder's purge_entities service
            # This removes all historical data for these entities
            # Use blocking=True to ensure purge completes before continuing
            await self.hass.services.async_call(
                "recorder",
                "purge_entities",
                {
                    "entity_id": entity_ids,
                    "keep_days": 0,  # Remove ALL history
                },
                blocking=True,  # Wait for completion
            )
            _LOGGER.info("✅ History purge completed for %d entities", len(entity_ids))
            
        except Exception as e:
            # Log as warning so we can see if it fails
            _LOGGER.warning("⚠️ Could not purge entity history: %s", e)

    async def _purge_device_history_by_serial(self, serial_number: str) -> None:
        """Purge all history and activity for a device by serial number.
        
        Finds all entities belonging to this device (by serial in unique_id)
        and removes their complete history.
        
        Used when:
        - Registering an existing device (clean old history)
        - Unregistering a device (remove all activity)
        """
        try:
            from homeassistant.helpers.entity_registry import async_get
            entity_registry = async_get(self.hass)
            
            # Find all entities with this serial number in unique_id
            entity_ids_to_purge = []
            for entity in entity_registry.entities.values():
                if entity.unique_id and serial_number in entity.unique_id:
                    entity_ids_to_purge.append(entity.entity_id)
            
            if entity_ids_to_purge:
                _LOGGER.info("🧹 Purging history for device %s - found %d entities", 
                           serial_number[-8:], len(entity_ids_to_purge))
                await self._purge_entity_history(entity_ids_to_purge)
            else:
                _LOGGER.debug("ℹ️ No entities found for device %s to purge", serial_number[-8:])
        
        except Exception as e:
            _LOGGER.warning("⚠️ Could not purge device history for %s: %s", serial_number[-8:], e)

    def _ensure_default_sender_name(self, device_info: Dict[str, Any]) -> None:
        """Assign default Easywave Sender #N name when no custom name is set.
        
        N = Anzahl der vorhandenen ew_transmitter Geräte + 1
        """
        if device_info.get("type") != "ew_transmitter":
            return

        name = (device_info.get("name") or "").strip()
        should_assign = not name  # True if name is empty

        if should_assign:
            # Zähle alle vorhandenen ew_transmitter Geräte
            existing_count = sum(
                1 for d in self._registered_devices.values()
                if d.get("type") == "ew_transmitter"
            )
            # Use translated transmitter name
            from .translations import t_transmitter, get_language
            lang = get_language(self.hass) if self.hass else DEFAULT_LANGUAGE
            device_info["name"] = f"Easywave {t_transmitter(lang)} #{existing_count + 1}"

    def _ensure_default_ewneo_sensor_name(self, device_info: Dict[str, Any]) -> None:
        """Assign default EWneo-Sensor #N name when no custom name is set.
        
        N = Anzahl der vorhandenen ew_sensor/ewneo_sensor Geräte + 1
        """
        if device_info.get("type") not in ("ew_sensor", "ewneo_sensor"):
            return

        name = (device_info.get("name") or "").strip()
        if not name:
            should_assign = True
        else:
            should_assign = name.startswith("EWneo-Sensor") or name.startswith("EW-Sensor") or name.startswith("Ew Sensor")

        if should_assign:
            # Zähle alle vorhandenen ew_sensor/ewneo_sensor Geräte
            existing_count = sum(
                1 for d in self._registered_devices.values()
                if d.get("type") in ("ew_sensor", "ewneo_sensor")
            )
            device_info["name"] = f"EWneo-Sensor #{existing_count + 1}"

    def get_next_ew_sender_index(self) -> int:
        """Get the next available Easywave Sender index (= count + 1)."""
        count = sum(
            1 for d in self._registered_devices.values()
            if d.get("type") == "ew_transmitter"
        )
        return count + 1
    
    def get_next_ewneo_sensor_index(self) -> int:
        """Get the next available EWneo-Sensor index (= count + 1)."""
        count = sum(
            1 for d in self._registered_devices.values()
            if d.get("type") in ("ew_sensor", "ewneo_sensor")
        )
        return count + 1
    
    def _verify_registration_id_immutability(self, serial_number: str, 
                                            stored_reg_id: str, 
                                            loaded_reg_id: Optional[str]) -> bool:
        """Verify that registration_id hasn't changed between saves.
        
        Returns True if immutability is preserved, False if conflict detected.
        """
        if not loaded_reg_id:
            _LOGGER.warning("⚠️ Device %s loaded without registration_id (corrupted?)", serial_number[-8:])
            return False
        
        if stored_reg_id != loaded_reg_id:
            _LOGGER.error("❌ Device %s registration_id changed! Stored: %s, Loaded: %s. "
                         "This indicates file corruption or improper modification!",
                         serial_number[-8:], stored_reg_id, loaded_reg_id)
            return False
        
        return True
    
    async def _re_enable_device_entities(self, serial_number: str) -> int:
        """Re-enable all entities AND the device for a device that was previously disabled.
        
        This is critical for handling the case where:
        1. User disables a device/entity
        2. Device is deleted from Home Assistant
        3. Device is re-learned/recreated
        
        Without this, the old disabled entity/device entry persists and prevents 
        the new device from being created in an enabled state.
        
        Returns the number of entities that were re-enabled.
        """
        try:
            entity_registry = er.async_get(self.hass)
            re_enabled_count = 0
            
            # CRITICAL: First check and re-enable the HA device entry itself.
            # When a device is disabled in HA, the device entry gets disabled_by set.
            # If this device entry persists (e.g. removal failed), newly created entities
            # will inherit the disabled state.  Clear it so the device starts fresh.
            import homeassistant.helpers.device_registry as dr
            device_registry = dr.async_get(self.hass)
            device_entry = self._find_ha_device_entry(serial_number)
            if device_entry and device_entry.disabled_by:
                _LOGGER.info(
                    "🔓 Re-enabling HA device entry for %s (was disabled_by=%s)",
                    serial_number[-8:],
                    device_entry.disabled_by
                )
                device_registry.async_update_device(
                    device_entry.id, disabled_by=None
                )
            
            # Search for all entities that belong to this device
            # Unique IDs now start with registration_id (UUID) instead of serial_number
            ha_identifier = self.get_ha_device_identifier(serial_number).lower()
            for entity_id, entity_entry in entity_registry.entities.items():
                # Check if entity belongs to our integration and device
                if entity_entry.platform != DOMAIN:
                    continue
                
                # Match by registration_id (UUID) prefix or legacy serial_number prefix
                if not entity_entry.unique_id:
                    continue
                uid_lower = entity_entry.unique_id.lower()
                if not (uid_lower.startswith(ha_identifier) or uid_lower.startswith(serial_number.lower())):
                    continue
                
                # If entity is disabled, re-enable it
                if entity_entry.disabled_by:
                    try:
                        _LOGGER.debug(
                            "Re-enabling entity %s (was disabled_by=%s) for device %s",
                            entity_id,
                            entity_entry.disabled_by,
                            serial_number[-8:]
                        )
                        entity_registry.async_update_entity(entity_id, disabled_by=None)
                        re_enabled_count += 1
                    except Exception as e:
                        _LOGGER.warning(
                            "Failed to re-enable entity %s for device %s: %s",
                            entity_id,
                            serial_number[-8:],
                            e
                        )
            
            if re_enabled_count > 0:
                _LOGGER.info(
                    "✅ Re-enabled %d entities for device %s",
                    re_enabled_count,
                    serial_number[-8:]
                )
            
            return re_enabled_count
            
        except Exception as e:
            _LOGGER.error("Error re-enabling entities for device %s: %s", serial_number[-8:], e)
            return 0
    
    async def _save_registered_devices(self) -> None:
        """Save registered devices to persistent storage.
        
        STRUCTURAL SAFEGUARDS:
        1. Entity integrity check — abort if devices lost all entities unexpectedly.
        2. Device count check — abort if device count dropped by >50% vs. on-disk file.
        3. Atomic write — write to temp file first, then rename to prevent partial writes.
        """
        import json
        import aiofiles
        import os
        import tempfile
        try:
            os.makedirs(os.path.dirname(self._registered_devices_file), exist_ok=True)
            
            # ═══ DEVICE COUNT INTEGRITY CHECK ═══
            # If the on-disk file has significantly more devices than _registered_devices,
            # something went wrong (load failure, accidental clear, etc.) — refuse to save.
            if os.path.exists(self._registered_devices_file):
                try:
                    async with aiofiles.open(self._registered_devices_file, 'r', encoding='utf-8') as f:
                        on_disk = json.loads(await f.read())
                    on_disk_count = len(on_disk.get('devices', {}))
                    current_count = len(self._registered_devices)
                    
                    # Only check if the on-disk file had devices (skip for initial save)
                    if on_disk_count >= 2 and current_count < on_disk_count // 2:
                        _LOGGER.error(
                            "🛑 SAVE ABORTED: Device count dropped from %d (on disk) to %d (in memory). "
                            "This indicates a data corruption bug — refusing to persist. "
                            "Fix the code or restore from backup.",
                            on_disk_count, current_count
                        )
                        return
                except Exception as read_err:
                    _LOGGER.debug("Could not read on-disk file for count check: %s", read_err)
            
            # ═══ ENTITY INTEGRITY CHECK ═══
            # Compare current entity counts with last-saved state to detect mass entity loss.
            # If many devices have lost entities, something went wrong — abort saving.
            if hasattr(self, '_last_saved_entity_counts') and self._last_saved_entity_counts:
                devices_with_loss = 0
                for serial, device_info in self._registered_devices.items():
                    old_count = self._last_saved_entity_counts.get(serial, 0)
                    new_count = len(device_info.get("entities", []))
                    # A device that previously had entities but now has none is suspicious
                    if old_count > 0 and new_count == 0:
                        devices_with_loss += 1
                        _LOGGER.warning("⚠️ ENTITY LOSS DETECTED: Device %s had %d entities, now has 0", 
                                       serial[-8:], old_count)
                
                MAX_ACCEPTABLE_LOSS = 3  # Allow up to 3 devices losing entities (re-learning, deletion)
                if devices_with_loss > MAX_ACCEPTABLE_LOSS:
                    _LOGGER.error(
                        "🛑 SAVE ABORTED: %d devices lost ALL entities — this indicates a bug! "
                        "Refusing to persist corrupted data. Last saved state is preserved.",
                        devices_with_loss
                    )
                    return
            
            # Deep copy devices and convert sets to lists for JSON serialization
            serializable_devices = {}
            ephemeral_fields = {'disabled', 'config_entry_id', 'via_device', 'config_subentry_id', 
                              'departed_at', 'last_telegram', 'last_seen', 'sensor_values', 'last_reading'}
            
            for serial, device_info in self._registered_devices.items():
                device_copy = device_info.copy()
                
                # Filter out ephemeral fields that should NEVER be persisted
                for field in ephemeral_fields:
                    if field in device_copy:
                        device_copy.pop(field, None)
                
                # Convert any sets to lists for JSON serialization
                if 'platforms' in device_copy and isinstance(device_copy['platforms'], set):
                    device_copy['platforms'] = list(device_copy['platforms'])
                
                # Recursively convert any nested sets in entities
                if 'entities' in device_copy:
                    for entity in device_copy['entities']:
                        if isinstance(entity, dict):
                            for key, value in entity.items():
                                if isinstance(value, set):
                                    entity[key] = list(value)
                
                serializable_devices[serial] = device_copy
            
            data = {
                'version': '2.0',  # Version erhöht wegen neuer Struktur mit integrierten Indices
                'last_updated': datetime.now().isoformat(),
                'config_entry_id': self.config_entry.entry_id,
                'device_count': len(serializable_devices),
                'devices': serializable_devices,
                # Integrierte Index-Tracking-Daten (ersetzt separate Dateien)
                'used_ewb_indices': {str(k): v for k, v in self._used_ewb_indices.items()},
                'next_free_ewb_index': self._next_free_ewb_index,
                'used_ew_receiver_indices': {str(k): v for k, v in self._used_ew_receiver_indices.items()},
                'next_free_ew_receiver_index': self._next_free_ew_receiver_index
            }
            
            content = json.dumps(data, indent=2, ensure_ascii=False)
            
            # ═══ ATOMIC WRITE ═══
            # Write to temp file first, then rename. This prevents corruption
            # from partial writes if HA is killed (SIGINT/SIGKILL) mid-write.
            target_dir = os.path.dirname(self._registered_devices_file)
            fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix='.tmp', prefix='reg_devices_')
            try:
                os.close(fd)  # Close the low-level fd, use aiofiles for async write
                async with aiofiles.open(tmp_path, 'w', encoding='utf-8') as f:
                    await f.write(content)
                os.replace(tmp_path, self._registered_devices_file)  # Atomic on POSIX
            except Exception:
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            
            # Update entity count tracking after successful save
            self._last_saved_entity_counts = {
                serial: len(info.get("entities", []))
                for serial, info in self._registered_devices.items()
            }
                
            _LOGGER.info("💾 Saved %d registered devices to storage", len(self._registered_devices))
        except Exception as e:
            _LOGGER.error("❌ Failed to save registered devices: %s", e)
    
    async def _migrate_existing_devices_to_registered_list(self) -> None:
        """Migrate existing devices from legacy sources to registered list (ONE-TIME ONLY).
        
        IMPORTANT: This process happens exactly once - when registered_devices.json doesn't exist yet.
        After that, registered_devices.json is the ONLY source of truth.
        
        If a device exists in multiple legacy sources, we take the MOST RECENT one only.
        We do NOT merge data from multiple sources.
        """
        import json
        import aiofiles
        import os
        
        # If registered_devices.json already has devices, abort migration
        if self._registered_devices:
            _LOGGER.info("ℹ️ registered_devices.json already populated. Skipping legacy migration.")
            return
        
        # CRITICAL GUARD: If the persistent file EXISTS on disk, never migrate — even if
        # loading failed.  A load failure (corrupt JSON, permission error, etc.) sets
        # _registered_devices = {}, but the right reaction is to FIX the file, NOT to
        # silently overwrite it with data from managed_devices.json (which may be stale).
        if os.path.exists(self._registered_devices_file):
            _LOGGER.warning(
                "⚠️ registered_devices.json EXISTS on disk but _registered_devices is empty "
                "(possible load failure). Refusing to overwrite with legacy migration. "
                "Fix or delete the file manually if migration is truly needed."
            )
            return
        
        try:
            migrated_count = 0
            
            # Try sources in priority order (most recent format first)
            # STEP 1: managed_devices.json (most complete format)
            managed_file = f"{os.path.dirname(self._registered_devices_file)}/../easywave/managed_devices.json"
            if os.path.exists(managed_file):
                _LOGGER.info("🔄 Migrating from managed_devices.json (one-time)...")
                try:
                    async with aiofiles.open(managed_file, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        managed_data = json.loads(content)
                    
                    managed_devices = managed_data.get('devices', {})
                    for serial_number, device_data in managed_devices.items():
                        # Convert DeviceManager format to registered_devices format
                        device_info = {
                            'serial_number': serial_number,
                            'device_type': device_data.get('device_type'),
                            'type': device_data.get('device_type'),
                            'name': device_data.get('name', f"Device {serial_number[-8:]}"),
                            'registered_at': device_data.get('created_at', datetime.now().isoformat()),
                            'rx11_index': device_data.get('rx11_index'),
                        }
                        
                        # Copy extra_data if present (but DO NOT merge with other sources later)
                        if device_data.get('extra_data'):
                            device_info.update(device_data['extra_data'])
                        
                        self._registered_devices[serial_number] = device_info
                        migrated_count += 1
                    
                    if migrated_count > 0:
                        _LOGGER.info("✅ Migrated %d devices from managed_devices.json (one-time)", migrated_count)
                        # Save immediately to make registered_devices.json the source of truth
                        await self._save_registered_devices()
                        # No further migration attempts after this
                        return
                
                except Exception as e:
                    _LOGGER.warning("⚠️ Error reading managed_devices.json: %s", e)
            
            # STEP 2: easywave_devices.json (legacy format, only if managed_devices.json not found)
            legacy_file = f"{os.path.dirname(self._registered_devices_file)}/easywave_devices.json"
            if os.path.exists(legacy_file) and not self._registered_devices:
                _LOGGER.info("🔄 Migrating from easywave_devices.json (legacy, one-time)...")
                try:
                    async with aiofiles.open(legacy_file, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        legacy_data = json.loads(content)
                    
                    legacy_devices = legacy_data.get('devices', {})
                    for serial_number, device_info in legacy_devices.items():
                        # Ensure required fields exist
                        if 'name' not in device_info:
                            device_info['name'] = f"Device {serial_number[-8:]}"
                        
                        self._registered_devices[serial_number] = device_info
                        migrated_count += 1
                    
                    if migrated_count > 0:
                        _LOGGER.info("✅ Migrated %d devices from easywave_devices.json (legacy, one-time)", migrated_count)
                        # Save immediately
                        await self._save_registered_devices()
                        # No further migration attempts after this
                        return
                
                except Exception as e:
                    _LOGGER.warning("⚠️ Error reading easywave_devices.json: %s", e)
            
            # If no devices found in any legacy source
            if not self._registered_devices:
                _LOGGER.info("ℹ️ No legacy devices to migrate")
            
        except Exception as e:
            _LOGGER.error("❌ Failed to migrate devices: %s", e)
    
    def is_device_registered(self, serial_number: str) -> bool:
        """Check if device is in registered devices list."""
        return serial_number in self._registered_devices
    
    def get_registered_device(self, serial_number: str) -> Optional[Dict[str, Any]]:
        """Get registered device info."""
        return self._registered_devices.get(serial_number)
    
    def get_all_registered_devices(self) -> Dict[str, Dict[str, Any]]:
        """Get all registered devices."""
        return self._registered_devices.copy()
    
    def get_ha_device_identifier(self, serial_number: str) -> str:
        """Return the HA device-registry identifier for a device.
        
        Every device must have a UUID-based registration_id. This is
        assigned during the config flow before entity creation.
        
        Raises KeyError if the device is not in _registered_devices or
        has no registration_id (indicates a programming error).
        """
        device_info = self._registered_devices.get(serial_number)
        if not device_info:
            raise KeyError(
                f"Device {serial_number[-8:]} not found in _registered_devices. "
                f"Pass device_info directly if the device hasn't been stored yet."
            )
        reg_id = device_info.get('registration_id')
        if not reg_id:
            raise KeyError(
                f"Device {serial_number[-8:]} has no registration_id. "
                f"Ensure register_device_permanently() assigns one before this call."
            )
        return reg_id
    
    def get_serial_by_ha_identifier(self, identifier_value: str) -> Optional[str]:
        """Reverse-lookup: find serial_number by HA device identifier.
        
        The identifier can be either a registration_id (UUID) for new
        devices or a serial_number for legacy devices.
        """
        # Direct serial match (legacy devices)
        if identifier_value in self._registered_devices:
            return identifier_value
        # Search by registration_id (new UUID-based devices)
        for serial, info in self._registered_devices.items():
            if info.get('registration_id') == identifier_value:
                return serial
        return None
    
    def _find_ha_device_entry(self, serial_number: str):
        """Find the HA device entry for a device, trying both identifier schemes.
        
        Checks registration_id first (new UUID-based), then serial_number
        (legacy), so we always find the device regardless of scheme.
        """
        device_registry = dr.async_get(self.hass)
        
        # 1) Try UUID-based identifier
        device_info = self._registered_devices.get(serial_number, {})
        reg_id = device_info.get('registration_id')
        if reg_id:
            entry = device_registry.async_get_device(identifiers={(DOMAIN, reg_id)})
            if entry:
                return entry
        
        # 2) Fallback: serial-based identifier (legacy / during migration)
        entry = device_registry.async_get_device(identifiers={(DOMAIN, serial_number)})
        return entry

    def get_device_instance(self, serial_number: str):
        """Get the device instance for a serial number.
        
        Returns the actual device class instance (e.g., EWneoSensor) if available.
        """
        try:
            if self.transceiver and hasattr(self.transceiver, 'get_device'):
                device = self.transceiver.get_device(serial_number)
                if device:
                    _LOGGER.debug("✅ Found device instance for %s: %s", 
                                 serial_number, type(device).__name__)
                else:
                    # Reduce noise - only log at debug level, device will be created on-demand later
                    _LOGGER.debug("⚠️ No device instance found for %s in transceiver._device_instances", 
                                  serial_number)
                return device
            else:
                _LOGGER.debug("⚠️ Transceiver does not have get_device method")
        except Exception as e:
            _LOGGER.debug("Could not get device instance for %s: %s", serial_number, e)
        return None
    
    async def register_device_permanently(self, serial_number: str, device_info: Dict[str, Any]) -> bool:
        """Register a device for permanent management.
        
        KONSISTENZ-SAFEGUARD: Überschreibe NICHT die registration_id bei Neustarts!
        Ein bereits registriertes Gerät soll die gleiche registration_id behalten,
        damit die Entity-IDs und Unique-IDs über Neustarts hinweg stabil bleiben.
        """
        try:
            # LOG the exact input before any processing
            _LOGGER.debug("📋 register_device_permanently: serial=%s, detected_button_type=%s",
                           serial_number[-8:], device_info.get("detected_button_type"))
            
            # WICHTIG: Nur neue registration_id setzen wenn Gerät NOCH NICHT bekannt ist
            # Bei Neustarts wird das Gerät bereits in _registered_devices geladen,
            # also wird diese Stelle nicht erreicht
            if serial_number not in self._registered_devices:
                # Neues Gerät — erzeuge eine zufällige UUID als registration_id.
                # Damit ist jede Registrierung weltweit eindeutig.
                # Alte Geräte, die bereits eine deterministische ID haben, behalten diese.
                if not device_info.get('registration_id'):
                    import uuid
                    new_uuid = uuid.uuid4().hex  # 32-char hex, no dashes
                    device_info['registration_id'] = new_uuid
                    _LOGGER.info("🆔 Generated UUID registration_id for new %s device: %s",
                                device_info.get('type', 'unknown'), new_uuid[:8])
                else:
                    _LOGGER.debug("📝 Using existing registration_id for device %s", serial_number[-8:])
            else:
                # Bekanntes Gerät beim Neustart - BEHALTE alte registration_id
                existing_info = self._registered_devices[serial_number]
                if existing_info.get('registration_id'):
                    device_info['registration_id'] = existing_info['registration_id']
                    _LOGGER.debug("📝 Preserved registration_id for device %s (%s) on startup", 
                                 serial_number[-8:], device_info['registration_id'][:6])
                else:
                    # Legacy device without registration_id — assign one now.
                    # Every device must have a registration_id.
                    import uuid
                    new_uuid = uuid.uuid4().hex
                    device_info['registration_id'] = new_uuid
                    _LOGGER.info("🆔 Assigned registration_id to legacy device %s: %s",
                                serial_number[-8:], new_uuid[:8])
            
            # ═══ IMMUTABILITY GUARD: registration_id must NEVER change ═══
            # registration_id is the stable identifier that prevents duplicate entities
            # across reboots. Changing it would break all entity unique_ids.
            
            if serial_number in self._registered_devices:
                existing_device = self._registered_devices[serial_number]
                existing_reg_id = existing_device.get('registration_id')
                new_reg_id = device_info.get('registration_id')
                
                # If device already existed and has a registration_id, it MUST NOT change
                if existing_reg_id and new_reg_id and existing_reg_id != new_reg_id:
                    _LOGGER.error("❌ IMMUTABILITY VIOLATION: Device %s registration_id changed from %s to %s! "
                                "This would break all entity unique_ids. Reverting to original.",
                                serial_number[-8:], existing_reg_id, new_reg_id)
                    device_info['registration_id'] = existing_reg_id
                    # Warn user they need to delete and re-learn if they need to change this
                    _LOGGER.warning("⚠️ To change device registration, delete and re-learn the device.")
            
            # Add metadata
            device_info['registered_at'] = datetime.now().isoformat()
            device_info.setdefault('serial_number', serial_number)
            # NICHT speichern: config_entry_id wird beim Entity-Setup automatisch gesetzt
            # device_info['config_entry_id'] = self.config_entry.entry_id

            # WICHTIG: Nur cleanup bei echtem Re-Learning (nicht bei Neustart)
            # Bei Neustart ist device_info bereits in _registered_devices geladen
            # Bei Re-Learning wird es nicht geladen (oder kommt von Discovery)
            is_new_device = serial_number not in self._registered_devices
            is_relearned_device = serial_number in self._registered_devices and device_info.get('re_learn', False)
            
            if is_new_device or is_relearned_device:
                # Cleanup old entities from previous registrations
                # This ensures re-learning creates fresh entities without old logbook entries
                # Pass device_info so registration_id is available even for new devices
                # that haven't been stored in _registered_devices yet.
                await self._cleanup_old_entities_for_device(serial_number, device_info)
                # Also purge old history/activity for this device
                await self._purge_device_history_by_serial(serial_number)
            
            # IMPORTANT: Clear old measurement values from device_info itself
            # These may have been carried over from the learning telegram or old data
            measurement_keys = ["temperature", "humidity", "battery_level", "last_telegram",
                               "last_seen", "sensor_values", "last_reading", "disabled"]
            for key in measurement_keys:
                if key in device_info:
                    del device_info[key]
            _LOGGER.debug("🧹 Cleared old measurement values and disabled flag from device_info for %s", serial_number[-8:])

            # Assign default names if needed
            self._ensure_default_sender_name(device_info)
            self._ensure_default_ewneo_sensor_name(device_info)

            # Regenerate entity specs for all devices to ensure:
            # 1. Correct platforms are assigned
            # 2. registration_id suffix is added to unique_ids (prevents duplicate entities)
            device_type = device_info.get("type")
            if device_type in ["ew_transmitter", "ewneo_sensor", "ew_sensor", "ew_receiver"]:
                _LOGGER.debug("Regenerating entity specs for %s (type=%s, detected_button_type=%s)", 
                             serial_number[-8:], device_type, device_info.get("detected_button_type"))
                device_info = await self._regenerate_entity_specs(serial_number, device_info)
            
            # CRITICAL: Extract and preserve receiver_kind as top-level field
            # This ensures receiver_kind is available even if entities are deleted
            if device_type == "ew_receiver":
                # First, check if receiver_kind already exists in device_info (preserved from config)
                receiver_kind = device_info.get("receiver_kind")
                
                # If not, extract from entity specs
                if not receiver_kind and device_info.get("entities"):
                    for entity in device_info.get("entities", []):
                        if entity.get("receiver_kind"):
                            receiver_kind = entity["receiver_kind"]
                            _LOGGER.info("💾 Extracted receiver_kind='%s' from entity specs for %s", 
                                       receiver_kind, serial_number[-8:])
                            break
                
                # Store as top-level field (will be persisted in registered_devices.json)
                if receiver_kind:
                    device_info["receiver_kind"] = receiver_kind
                    _LOGGER.debug("✅ Persisting receiver_kind='%s' in device_info for %s", 
                                receiver_kind, serial_number[-8:])
                else:
                    _LOGGER.warning("⚠️ Could not extract receiver_kind for EW Receiver %s", serial_number[-8:])
            
            # ⚠️ REMOVED: _clean_device_info() call - ephemeral fields filtered at save time
            
            # Store in registered devices list
            self._registered_devices[serial_number] = device_info
            
            # Also add to legacy devices dict for platform compatibility
            self.devices[serial_number] = device_info
            self._known_devices.add(serial_number)
            
            # Add to DeviceManager (single source of truth)
            # Pass all fields so they get persisted in extra_data
            core_fields = {'serial_number', 'device_type', 'name', 'rx11_index', 'area', 'type'}
            extra_data = {}
            for k, v in device_info.items():
                if k not in core_fields and k not in ['registered_at', 'config_entry_id', '_known_devices']:
                    # Convert sets to lists for JSON serialization
                    if isinstance(v, set):
                        extra_data[k] = list(v)
                    else:
                        extra_data[k] = v
            self.device_manager.add_device(
                serial_number=serial_number,
                device_type=device_info.get("device_type", device_info.get("type", "unknown")),
                name=device_info.get("name", f"Device {serial_number}"),
                rx11_index=device_info.get("rx11_index"),
                area=device_info.get("area"),
                extra_data=extra_data if extra_data else None
            )
            
            # CRITICAL: Save immediately after storing receiver_kind
            # This ensures the data persists even if the device is disabled/deactivated
            await self._save_registered_devices()
            await self.device_manager.save()

            # Determine platforms from entities
            entities = device_info.get("entities", [])
            platforms = set()
            entity_specs_by_platform = {}
            
            for entity in entities:
                entity_type = entity.get("type")
                if entity_type:
                    platforms.add(entity_type)
                    if entity_type not in entity_specs_by_platform:
                        entity_specs_by_platform[entity_type] = []
                    entity_specs_by_platform[entity_type].append(entity)

            # Update device_info with platforms
            device_info["platforms"] = list(platforms)
            
            _LOGGER.debug("Determined platforms for device %s: %s", serial_number, list(platforms))
            
            # Re-enable any previously disabled entities for this device
            # This handles the case where a device was disabled, deleted, and re-learned
            re_enabled_count = await self._re_enable_device_entities(serial_number)

            # Fire event to trigger entity creation in Home Assistant
            self.hass.bus.async_fire(
                EVENT_DEVICE_ADDED,
                {
                    "serial_number": serial_number,
                    "device_info": device_info,
                    "device_type": device_info.get("type"),
                    "device_name": device_info.get("name"),
                    "entities": device_info.get("entities", []),
                    "platforms": platforms,
                    "registered_permanently": True,  # Flag to indicate this is a new permanent registration
                    "force_create": True  # Force creation even if entity registry thinks it exists
                }
            )
            
            # Fire platform-specific events (no delay needed - events are queued)
            for platform, platform_entities in entity_specs_by_platform.items():
                self.hass.bus.async_fire(
                    f"{EVENT_DEVICE_ADDED}_{platform}",
                    {
                        "serial_number": serial_number,
                        "device_info": device_info,
                        "entities": platform_entities,
                        "platforms": {platform},
                        "force_create": True
                    }
                )
            
            _LOGGER.info("✅ Device %s permanently registered", serial_number[-8:])
            return True
        except Exception as e:
            _LOGGER.error("❌ Failed to register device %s: %s", serial_number[-8:], e)
            return False
    
    # =============================================================================
    # EWB INDEX MANAGEMENT
    # =============================================================================
    
    def mark_ewb_index_used(self, index: int, gateway_serial: str, device_serial: str, device_name: str = None) -> None:
        """Mark an EWB index as used by a specific EWneo device.
        
        Note: An index can have a gateway serial but still be available for new EWneo devices.
        Only after a device is successfully joined via EwJoinDevice should it be marked as 'used'.
        """
        from datetime import datetime
        
        # Check if this was a reservation that we're now confirming
        was_reserved = self._used_ewb_indices.get(index, {}).get('reserved', False)
        
        self._used_ewb_indices[index] = {
            'gateway_serial': gateway_serial,
            'device_serial': device_serial, 
            'device_name': device_name or f"EWneo-Device ({device_serial})",
            'created_at': datetime.now().isoformat()
            # Remove 'reserved' flag
        }
        
        # Update next free index cache
        if index == self._next_free_ewb_index:
            self._next_free_ewb_index = self._find_next_free_ewb_index()
        
        if was_reserved:
            _LOGGER.info("✅ Confirmed reservation for EWB index %d (device: %s, gateway: %s)", 
                        index, device_serial[-8:], gateway_serial[-8:])
        else:
            _LOGGER.info("📍 Marked EWB index %d as used (device: %s, gateway: %s)", 
                        index, device_serial[-8:], gateway_serial[-8:])
        
        # Save to persistent storage
        asyncio.create_task(self._save_registered_devices())
        
        # Update transceiver wrapper tracking if available
        if hasattr(self.transceiver, '_rx11_wrapper') and self.transceiver._rx11_wrapper:
            wrapper = self.transceiver._rx11_wrapper
            wrapper.mark_ewb_index_used(index, gateway_serial, device_serial)
    
    def mark_ewb_index_free(self, index: int) -> None:
        """Mark an EWB index as free/available for new EWneo devices.
        
        This removes the EWneo device association but the gateway serial remains.
        """
        if index in self._used_ewb_indices:
            device_info = self._used_ewb_indices.pop(index)
            _LOGGER.info("♻️ Marked EWB index %d as free (was: %s)", index, device_info.get('device_name', 'Unknown'))
            
            # Update next free index cache if this becomes the new earliest free
            if index < self._next_free_ewb_index:
                self._next_free_ewb_index = index
                
            # Save to persistent storage
            asyncio.create_task(self._save_registered_devices())
            
            # Update transceiver wrapper tracking if available
            if hasattr(self.transceiver, '_rx11_wrapper') and self.transceiver._rx11_wrapper:
                wrapper = self.transceiver._rx11_wrapper
                wrapper.mark_ewb_index_free(index)
    
    def reset_all_ewb_indices(self) -> None:
        """Reset all EWB indices - used when integration is removed."""
        _LOGGER.info("🔄 Resetting all EWB indices...")
        
        # Clear all stored indices
        self._used_ewb_indices.clear()
        self._next_free_ewb_index = 0
        
        # Clear wrapper tracking if available
        if hasattr(self.transceiver, '_rx11_wrapper') and self.transceiver._rx11_wrapper:
            wrapper = self.transceiver._rx11_wrapper
            wrapper._used_ewb_indices.clear()
            wrapper._ewb_device_serials.clear()
        
        _LOGGER.info("✅ All EWB indices reset to 0")
    
    async def get_next_free_ewb_index(self) -> int:
        """Get the next free EWB index from persistent tracking (transaction-safe).
        
        Immediately reserves the index to prevent race conditions when multiple
        devices are added simultaneously.
        """
        # Find next free index based on our persistent tracking
        index = self._find_next_free_ewb_index()
        
        # IMMEDIATELY reserve it with a placeholder to prevent race conditions
        # This will be updated with real device info when mark_ewb_index_used() is called
        self._used_ewb_indices[index] = {
            'gateway_serial': None,
            'device_serial': 'RESERVED',
            'device_name': f'Reserved (Index {index})',
            'created_at': datetime.now().isoformat(),
            'reserved': True  # Mark as temporary reservation
        }
        
        # Update next free index cache
        self._next_free_ewb_index = self._find_next_free_ewb_index()
        
        _LOGGER.info("🔒 Reserved EWB index %d (next free: %d)", index, self._next_free_ewb_index)
        
        # Save immediately to prevent concurrent allocations
        await self._save_registered_devices()
        
        # Schedule cleanup of this reservation after 5 minutes if not confirmed
        asyncio.create_task(self._cleanup_stale_reservation(index))
        
        return index
    
    async def _cleanup_stale_reservation(self, index: int) -> None:
        """Clean up a reservation if it hasn't been confirmed after 5 minutes."""
        await asyncio.sleep(300)  # Wait 5 minutes
        
        # Check if still a reservation (not confirmed with real device data)
        if index in self._used_ewb_indices and self._used_ewb_indices[index].get('reserved'):
            _LOGGER.warning("⚠️ Cleaning up stale reservation for index %d", index)
            self.mark_ewb_index_free(index)
    
    def _find_next_free_ewb_index(self) -> int:
        """Find the next EWB index available for EWneo device assignment.
        
        An index is 'free' if no EWneo device is assigned to it yet,
        regardless of whether it has a gateway serial or not.
        """
        used_indices = set(self._used_ewb_indices.keys())
        for index in range(128):  # EWB supports indices 0-127
            if index not in used_indices:
                return index
        raise ValueError("No free EWB indices available (all 0-127 in use)")
    
    def is_ewb_index_used(self, index: int) -> bool:
        """Check if an EWB index is already used by an EWneo device.
        
        Returns True if an EWneo device is assigned to this index,
        False if the index is available for new EWneo device assignment.
        """
        return index in self._used_ewb_indices
        
    def get_used_ewb_indices(self) -> Dict[int, Dict[str, Any]]:
        """Get all used EWB indices with their device information."""
        return self._used_ewb_indices.copy()
        
    def get_ewb_device_by_index(self, index: int) -> Optional[Dict[str, Any]]:
        """Get device information by EWB index."""
        return self._used_ewb_indices.get(index)
    
    # ============================================================
    # Easywave Receiver Index Management (unidirectional receivers)
    # ============================================================
    
    def mark_ew_receiver_index_used(self, index: int, receiver_serial: str, device_serial: str, device_name: str = None) -> None:
        """Mark an Easywave Receiver index as used.
        
        IMPORTANT: Store metadata for consistent access by syncing methods.
        The actual serials will be fetched LIVE from the RX11 when needed.
        """
        # Store metadata for this index
        self._used_ew_receiver_indices[index] = {
            'receiver_serial': receiver_serial,
            'device_serial': device_serial,
            'device_name': device_name or f"Easywave Receiver ({device_serial})",
            'created_at': datetime.now().isoformat()
        }
        
        # Update next free index cache
        if index == self._next_free_ew_receiver_index:
            self._next_free_ew_receiver_index = self._find_next_free_ew_receiver_index()
        
        # Also update device_manager._rx11_index_to_serial for consistency
        # This is used by HA to link devices, but the serial is fetched from RX11
        self.device_manager._rx11_index_to_serial[index] = device_serial
            
        _LOGGER.info("📍 Marked Easywave Receiver index %d as used (device: %s)", index, device_name or device_serial[-8:])
        
        # Save to persistent storage
        asyncio.create_task(self._save_registered_devices())
        
        # Update transceiver wrapper tracking if available
        if hasattr(self.transceiver, '_rx11_wrapper') and self.transceiver._rx11_wrapper:
            wrapper = self.transceiver._rx11_wrapper
            wrapper.mark_receiver_used(index, receiver_serial)
    
    def mark_ew_receiver_index_free(self, index: int) -> None:
        """Mark an Easywave Receiver index as free/available for new devices."""
        if index in self._used_ew_receiver_indices:
            self._used_ew_receiver_indices.pop(index)
            _LOGGER.info("♻️ Marked Easywave Receiver index %d as free", index)
            
            # Update next free index cache if this becomes the new earliest free
            if index < self._next_free_ew_receiver_index:
                self._next_free_ew_receiver_index = index
            
            # Also update device_manager._rx11_index_to_serial
            if index in self.device_manager._rx11_index_to_serial:
                del self.device_manager._rx11_index_to_serial[index]
                
            # Save to persistent storage
            asyncio.create_task(self._save_registered_devices())
    
    def reset_all_ew_receiver_indices(self) -> None:
        """Reset all Easywave Receiver indices - used when integration is removed."""
        _LOGGER.info("🔄 Resetting all Easywave Receiver indices...")
        
        # Clear all stored indices
        self._used_ew_receiver_indices.clear()
        self._next_free_ew_receiver_index = 0
        
        # Clear device_manager indices
        self.device_manager._rx11_index_to_serial.clear()
        
        # Clear wrapper tracking if available
        if hasattr(self.transceiver, '_rx11_wrapper') and self.transceiver._rx11_wrapper:
            wrapper = self.transceiver._rx11_wrapper
            wrapper._used_receivers.clear()
        
        _LOGGER.info("✅ All Easywave Receiver indices reset to 0")
    
    async def get_next_free_ew_receiver_index(self) -> int:
        """Get the next free Easywave Receiver index from persistent tracking.
        
        Uses _used_ew_receiver_indices for tracking which indices are in use.
        This is the authoritative source for EW receiver index allocation.
        """
        # Use the persistent tracking from coordinator
        index = self._find_next_free_ew_receiver_index()
        self._next_free_ew_receiver_index = index
        _LOGGER.info("📍 Next free Easywave Receiver index: %d", index)
        return index
    
    def _find_next_free_ew_receiver_index(self) -> int:
        """Find the next Easywave Receiver index available for assignment."""
        used_indices = set(self._used_ew_receiver_indices.keys())
        for index in range(255):  # EW supports indices 0-254
            if index not in used_indices:
                return index
        raise ValueError("No free Easywave Receiver indices available (all 0-254 in use)")
    
    def is_ew_receiver_index_used(self, index: int) -> bool:
        """Check if an Easywave Receiver index is already used."""
        return index in self._used_ew_receiver_indices
        
    def get_used_ew_receiver_indices(self) -> Dict[int, Dict[str, Any]]:
        """Get all used Easywave Receiver indices with their device information."""
        return self._used_ew_receiver_indices.copy()
    
    async def _sync_device_manager_from_registered(self) -> None:
        """Ensure DeviceManager whitelist matches _registered_devices.
        
        _registered_devices (registered_devices.json) is the authoritative source.
        Any device in _registered_devices that is missing from DeviceManager is added.
        Any device in DeviceManager that is NOT in _registered_devices is removed.
        This prevents the two stores from drifting apart and causing data loss.
        """
        synced_count = 0
        removed_count = 0
        
        registered_serials = set(self._registered_devices.keys())
        dm_serials = set(self.device_manager.get_all_devices().keys())
        
        # Add devices that are in _registered_devices but missing from DeviceManager
        missing_from_dm = registered_serials - dm_serials
        for serial in missing_from_dm:
            device_info = self._registered_devices[serial]
            self.device_manager.add_device(
                serial_number=serial,
                device_type=device_info.get("device_type", device_info.get("type", "unknown")),
                name=device_info.get("name", f"Device {serial}"),
                rx11_index=device_info.get("rx11_index"),
                area=device_info.get("area"),
            )
            synced_count += 1
        
        # Remove devices that are in DeviceManager but NOT in _registered_devices
        orphaned_in_dm = dm_serials - registered_serials
        for serial in orphaned_in_dm:
            self.device_manager.remove_device(serial)
            removed_count += 1
        
        if synced_count > 0 or removed_count > 0:
            await self.device_manager.save()
            _LOGGER.info("🔄 DeviceManager sync: added %d, removed %d (now matches _registered_devices)", 
                        synced_count, removed_count)
        else:
            _LOGGER.debug("DeviceManager already in sync with _registered_devices (%d devices)", 
                         len(registered_serials))

    async def _sync_ewb_indices_with_wrapper(self) -> None:
        """Synchronize EWB index tracking between coordinator and transceiver wrapper."""
        if not hasattr(self.transceiver, '_rx11_wrapper') or not self.transceiver._rx11_wrapper:
            return
            
        wrapper = self.transceiver._rx11_wrapper
        
        # Clear wrapper tracking and rebuild from coordinator's persistent data
        wrapper._used_ewb_indices.clear()
        wrapper._ewb_device_serials.clear()
        
        for index, device_info in self._used_ewb_indices.items():
            gateway_serial = device_info.get('gateway_serial')
            device_serial = device_info.get('device_serial')
            
            if gateway_serial and device_serial:
                wrapper.mark_ewb_index_used(index, gateway_serial, device_serial)
                
        _LOGGER.info("🔄 Synchronized %d EWB indices from coordinator to wrapper", len(self._used_ewb_indices))
    
    async def _sync_ew_receiver_indices_with_wrapper(self) -> None:
        """Synchronize Easywave Receiver index tracking between coordinator, wrapper and device_manager."""
        if not hasattr(self.transceiver, '_rx11_wrapper') or not self.transceiver._rx11_wrapper:
            return
            
        wrapper = self.transceiver._rx11_wrapper
        
        # Clear wrapper tracking and rebuild from coordinator's persistent data
        wrapper._used_receivers.clear()
        
        # Also sync device_manager._rx11_index_to_serial with used_ew_receiver_indices
        # This ensures index allocation is consistent
        self.device_manager._rx11_index_to_serial.clear()
        
        for index, device_info in self._used_ew_receiver_indices.items():
            receiver_serial = device_info.get('receiver_serial')
            device_serial = device_info.get('device_serial')
            
            if receiver_serial:
                # Don't try to re-read from RX11 during sync - it can timeout
                # The actual serial will be fetched LIVE when command is sent (in wrapper.py)
                _LOGGER.debug("🔍 Syncing index %d: using stored serial %s", index, receiver_serial[-8:])
                
                wrapper.mark_receiver_used(int(index), receiver_serial)
                
                # Sync to device_manager._rx11_index_to_serial
                if device_serial:
                    self.device_manager._rx11_index_to_serial[int(index)] = device_serial
                
        _LOGGER.info("🔄 Synchronized %d Easywave Receiver indices from coordinator to wrapper and device_manager", len(self._used_ew_receiver_indices))

    async def _unregister_device_internal(self, serial_number: str) -> bool:
        """Internal: Permanently remove device from all systems.
        
        This is the core removal logic handling:
        1. Get device info for cleanup references
        2. Remove from HA registries (entities + device)
        3. Remove from _registered_devices (authoritative source)
        4. Remove from legacy dicts (self.devices, _known_devices)
        5. Remove from DeviceManager whitelist
        6. Device-specific cleanup (RX11, EWneo, etc.)
        7. Save all persistent stores
        
        Note: For public removal, use async_remove_device() instead.
        """
        try:
            # Get device info before removal for cleanup
            device_info = self._registered_devices.get(serial_number) or self.devices.get(serial_number)
            if not device_info:
                _LOGGER.warning("⚠️ Device %s not found in any tracking dict", serial_number[-8:])
                # Try to clean up orphaned entries anyway
                await self._remove_from_ha_registry(serial_number, {})
                if self.device_manager.is_whitelisted(serial_number):
                    self.device_manager.remove_device(serial_number)
                    await self.device_manager.save()
                return False
            
            device_name = device_info.get('name', serial_number[-8:])
            device_type = device_info.get("type", "unknown")
            _LOGGER.info("🗑️ Permanently removing device: %s (%s, type=%s)", 
                        device_name, serial_number[-8:], device_type)
            
            # Step 0: Purge history and activity for this device
            await self._purge_device_history_by_serial(serial_number)
            
            # Step 1: Remove from HA registries (entities and device entry)
            await self._remove_from_ha_registry(serial_number, device_info)
            
            # Step 2: Remove from _registered_devices (authoritative source)
            if serial_number in self._registered_devices:
                del self._registered_devices[serial_number]
            
            # Step 3: Remove from legacy in-memory dicts
            self.devices.pop(serial_number, None)
            self._known_devices.discard(serial_number)
            self._devices_with_fired_events.discard(serial_number)
            self._dispatched_devices.discard(serial_number)
            
            # Step 4: Remove from DeviceManager whitelist
            if self.device_manager.is_whitelisted(serial_number):
                self.device_manager.remove_device(serial_number)
            
            # Step 5: Device-specific cleanup (RX11, EWneo, etc.)
            is_ewneo = self._is_ewneo_device(device_type, device_info)
            if device_type == "ew_receiver":
                await self._cleanup_rx11_device(serial_number, device_info)
            elif is_ewneo:
                _LOGGER.info("  EWneo device - sending EWB_REMOVE_DEVICE...")
                await self._cleanup_ewneo_device(serial_number, device_info)
            
            # Step 6: Save all persistent stores
            await self._save_registered_devices()
            await self.device_manager.save()
            await self._save_device_configuration()
            
            _LOGGER.info("✅ Device %s permanently removed from all systems", device_name)
            return True
            
        except Exception as e:
            _LOGGER.error("❌ Failed to unregister device %s: %s", serial_number[-8:], e, exc_info=True)
            return False
    
    async def restore_registered_devices_only(self) -> None:
        """Restore ONLY registered devices at startup - ignore others.
        
        IMPORTANT: _registered_devices (registered_devices.json) is the single source
        of truth. Devices in _registered_devices that are NOT in DeviceManager are
        synced INTO DeviceManager (not removed). This prevents data loss when
        managed_devices.json gets out of sync.
        
        EWB and EW Receiver indices are already built by _load_registered_devices()
        from device data (single source of truth).
        
        Also restore receiver_kind from HA entity registry if not stored in device_info.
        """
        _LOGGER.info("🔄 Restoring %d registered devices from persistent storage", 
                   len(self._registered_devices))
        
        # Get entity registry to recover receiver_kind from existing entities
        from homeassistant.helpers import entity_registry as er
        entity_registry = er.async_get(self.hass)
        
        # Clear any existing in-memory devices first
        self.devices.clear()
        self._known_devices.clear()
        
        restored_count = 0
        synced_to_dm_count = 0
        
        for serial_number, device_info in list(self._registered_devices.items()):
            try:
                # Normalize device type fields (handle both 'type' and 'device_type')
                device_type = device_info.get('type') or device_info.get('device_type')
                if device_type and 'type' not in device_info:
                    device_info['type'] = device_type
                
                # Validate device info - skip truly invalid entries
                if not device_info.get('name') or not device_info.get('type'):
                    _LOGGER.warning("⚠️ Skipping invalid registered device %s (missing name or type)", 
                                  serial_number[-8:])
                    continue
                
                # CRITICAL FIX: If EW Receiver has no receiver_kind, try to recover from HA entity registry
                if device_type == 'ew_receiver' and not device_info.get('receiver_kind'):
                    _LOGGER.debug("🔍 Attempting to recover receiver_kind for %s from HA entity registry", serial_number[-8:])
                    
                    # Find entities for this device in HA entity registry
                    # NOTE: serial_number is UPPERCASE but unique_ids are lowercase — normalize
                    serial_lower = serial_number.lower()
                    ha_id_lower = self.get_ha_device_identifier(serial_number).lower()
                    device_entities = []
                    for entity_entry in entity_registry.entities.values():
                        if (entity_entry.unique_id and entity_entry.platform == DOMAIN and
                            (entity_entry.unique_id.lower().startswith(ha_id_lower) or 
                             entity_entry.unique_id.lower().startswith(serial_lower))):
                            device_entities.append(entity_entry)
                            _LOGGER.debug("  Found entity: %s (type: %s)", entity_entry.entity_id, entity_entry.entity_id.split('.')[0])
                    
                    # Infer receiver_kind from entity types
                    if device_entities:
                        entity_types = {entity_entry.entity_id.split('.')[0] for entity_entry in device_entities}
                        _LOGGER.debug("  Entity types found: %s", entity_types)
                        
                        if "cover" in entity_types:
                            device_info["receiver_kind"] = "cover_2button"
                            _LOGGER.info("✅ Recovered receiver_kind='cover_2button' for %s from HA entities", serial_number[-8:])
                        elif "light" in entity_types:
                            device_info["receiver_kind"] = "motor_3button"
                            _LOGGER.info("✅ Recovered receiver_kind='motor_3button' for %s from HA entities", serial_number[-8:])
                        elif "switch" in entity_types:
                            # Could be switch_2button or heating_cooling - check for temperature-related
                            # If there's a "climate" entity or if the switch entity name suggests heating, use heating_cooling
                            if any("heat" in str(e.entity_id).lower() or "climate" in str(e.entity_id).lower() for e in device_entities):
                                device_info["receiver_kind"] = "heating_cooling"
                                _LOGGER.info("✅ Recovered receiver_kind='heating_cooling' for %s from HA entities", serial_number[-8:])
                            else:
                                device_info["receiver_kind"] = "switch_2button"
                                _LOGGER.info("✅ Recovered receiver_kind='switch_2button' for %s from HA entities", serial_number[-8:])
                        elif "button" in entity_types:
                            device_info["receiver_kind"] = "universal_4button"
                            _LOGGER.info("✅ Recovered receiver_kind='universal_4button' for %s from HA entities", serial_number[-8:])
                    else:
                        # No entities found - use safe default
                        device_info["receiver_kind"] = "impulse"
                        _LOGGER.warning("⚠️ No entities found in HA registry for %s - using safe default: impulse", serial_number[-8:])
                
                # SYNC: If device is in _registered_devices but NOT in DeviceManager,
                # add it to DeviceManager instead of removing it. _registered_devices
                # is the authoritative source. Include extra_data to preserve config fields.
                if not self.device_manager.is_whitelisted(serial_number):
                    _LOGGER.info("🔄 Syncing device %s (%s) into DeviceManager (was missing from whitelist)", 
                               serial_number[-8:], device_info.get('name'))
                    # Build extra_data with all non-core fields (preserves config fields)
                    core_fields = {'serial_number', 'device_type', 'name', 'rx11_index', 'area', 'type'}
                    extra_data = {k: (list(v) if isinstance(v, set) else v) 
                                  for k, v in device_info.items() 
                                  if k not in core_fields and k not in {'registered_at', 'config_entry_id'}}
                    self.device_manager.add_device(
                        serial_number=serial_number,
                        device_type=device_info.get("device_type", device_info.get("type", "unknown")),
                        name=device_info.get("name", f"Device {serial_number}"),
                        rx11_index=device_info.get("rx11_index"),
                        area=device_info.get("area"),
                        extra_data=extra_data if extra_data else None,
                    )
                    synced_to_dm_count += 1
                
                # Only regenerate entity specs if device has NO entities stored
                # registered_devices.json is the SINGLE SOURCE OF TRUTH — never overwrite existing entities
                if not device_info.get("entities"):
                    _LOGGER.info("🔄 Device %s has no stored entities, regenerating specs", serial_number[-8:])
                    device_info = await self._regenerate_entity_specs(serial_number, device_info)
                
                # Update _registered_devices with info
                self._registered_devices[serial_number] = device_info
                
                # Populate legacy devices dict for platform compatibility
                self.devices[serial_number] = device_info
                self._known_devices.add(serial_number)
                
                restored_count += 1
                _LOGGER.debug("✅ Restored registered device: %s (%s, receiver_kind=%s)", 
                            device_info.get('name'), device_info.get('type'), device_info.get('receiver_kind'))
                
            except Exception as e:
                _LOGGER.error("❌ Failed to restore device %s: %s", serial_number[-8:], e)
        
        # Save DeviceManager if we synced devices into it
        if synced_to_dm_count > 0:
            await self.device_manager.save()
            _LOGGER.info("🔄 Synced %d devices into DeviceManager whitelist", synced_to_dm_count)
        
        # Persist any entity spec regenerations done during restore (including recovered receiver_kind)
        if restored_count > 0:
            await self._save_registered_devices()
            _LOGGER.info("💾 Persisted recovered receiver_kind values for %d devices", restored_count)
        
        # DO NOT cleanup orphaned devices at startup - it's too risky and causes data loss
        # Cleanup is ONLY called during integration unload (async_unload_entry in __init__.py)
        # to completely remove all associated devices when the user removes the integration
        _LOGGER.debug("ℹ️ Skipping orphan device cleanup at startup (disabled for safety - only runs on unload)")
        
        _LOGGER.info("✅ Restored %d/%d registered devices", 
                   restored_count, len(self._registered_devices))
        
        # Events will be fired by fire_pending_device_events() after platform setup

    async def _cleanup_orphaned_ha_devices(self, manual_call: bool = False) -> None:
        """Clean up devices from HA Device Registry that are not in _registered_devices.
        
        This should ONLY be called during integration unload (@async_unload_entry).
        Never call during startup or normal operation - it causes data loss.
        
        SAFETY: Only removes devices that are in NEITHER _registered_devices NOR DeviceManager.
        Has a safety limit of 5 devices per cleanup pass.
        
        Args:
            manual_call: If True, bypasses device count check. Only set to True during 
                        integration unload. If False (default), skips cleanup if fewer 
                        than 10 devices are loaded (prevents accidental deletion at startup).
        """
        try:
            import homeassistant.helpers.device_registry as dr
            import homeassistant.helpers.entity_registry as er
            
            device_registry = dr.async_get(self.hass)
            entity_registry = er.async_get(self.hass)
            
            devices_to_remove = []
            
            # Safety check: don't cleanup if BOTH _registered_devices AND DeviceManager are empty
            # (could indicate a loading failure)
            has_registered_devices = bool(self._registered_devices)
            has_managed_devices = bool(self.device_manager and self.device_manager.get_all_devices())
            
            if not has_registered_devices and not has_managed_devices:
                _LOGGER.info("ℹ️ No devices loaded (neither _registered_devices nor DeviceManager) — skipping orphan HA device cleanup")
                return
            
            # Additional safety: if not manual call and we have very few devices, skip
            # (indicates we're still loading at startup)
            device_count = len(self._registered_devices) + (len(self.device_manager.get_all_devices()) if has_managed_devices else 0)
            if not manual_call and device_count < 10:
                _LOGGER.info("ℹ️ Only %d devices loaded - skipping cleanup (might still be loading)", device_count)
                return
            
            # If only one source is loaded, use it for cleanup
            valid_serials = set()
            if has_registered_devices:
                valid_serials.update(self._registered_devices.keys())
            if has_managed_devices:
                valid_serials.update(self.device_manager.get_all_devices().keys())
            
            # Find all devices belonging to this integration
            for device in device_registry.devices.values():
                
                # Check if device belongs to this config entry
                if self.config_entry.entry_id not in device.config_entries:
                    continue
                
                # Skip the RX11 gateway device
                is_gateway = any(
                    "_gateway" in str(ident[1]).lower() or "gateway" in str(ident[1]).lower()
                    for ident in device.identifiers
                    if ident[0] == DOMAIN
                )
                if is_gateway:
                    continue
                
                # Extract identifier value from device identifiers
                # Identifier can be a registration_id (UUID) or serial_number (legacy)
                identifier_value = None
                for identifier in device.identifiers:
                    if identifier[0] == DOMAIN:
                        identifier_value = identifier[1]
                        break
                
                if not identifier_value:
                    continue
                
                # Resolve identifier to serial_number (handles both UUID and serial)
                serial_number = self.get_serial_by_ha_identifier(identifier_value)
                
                # Check if this device is known to us (in either _registered_devices or DeviceManager)
                if serial_number and serial_number in valid_serials:
                    continue
                
                # Also check if the raw identifier_value is a known serial (edge case)
                if identifier_value in valid_serials:
                    continue
                
                devices_to_remove.append((device, identifier_value))
                _LOGGER.warning("🧹 Found orphaned HA device: %s (%s) - not in registered_devices or DeviceManager", 
                               device.name, identifier_value[-8:])
            
            # Remove orphaned devices
            for device, identifier_value in devices_to_remove:
                try:
                    # First remove all entities for this device
                    entities = er.async_entries_for_device(entity_registry, device.id)
                    for entity in entities:
                        _LOGGER.debug("  Removing orphaned entity: %s", entity.entity_id)
                        entity_registry.async_remove(entity.entity_id)
                    
                    # Then remove the device
                    _LOGGER.info("🗑️ Removing orphaned HA device: %s (%s)", device.name, identifier_value[-8:])
                    device_registry.async_remove_device(device.id)
                except Exception as e:
                    _LOGGER.warning("⚠️ Failed to remove orphaned device %s: %s", identifier_value[-8:], e)
            
            if devices_to_remove:
                _LOGGER.info("✅ Cleaned up %d orphaned devices from HA Device Registry", len(devices_to_remove))
                
        except Exception as e:
            _LOGGER.error("❌ Error cleaning up orphaned HA devices: %s", e)

    async def fire_pending_device_events(self) -> None:
        """Fire device events after platforms are set up (only once per device).
        
        NOTE: This should NOT fire events for devices loaded at startup, as those
        are already handled by the platform setup code (e.g., binary_sensor.py lines 94-110).
        This should only fire events for devices added AFTER startup (marked with registered_permanently flag).
        """
        # DO NOT fire events for restored devices - they're already created by platform setup
        # Only fire for devices that have the registered_permanently flag (newly added after startup)
        devices_to_fire = [
            (serial, info) for serial, info in self.devices.items()
            if serial not in self._devices_with_fired_events
            and info.get("registered_permanently", False)  # Only newly registered devices
        ]
        
        if not devices_to_fire:
            _LOGGER.debug("No pending devices to fire events for")
            return
            
        _LOGGER.info("🔥 Firing events for %d newly registered devices after platform setup", len(devices_to_fire))
        
        # Fire events for all loaded devices that haven't fired yet
        for serial_number, device_info in devices_to_fire:
            _LOGGER.debug("🔥 [fire_pending] Firing EVENT_DEVICE_ADDED for %s", serial_number[-8:])
            
            self.hass.bus.async_fire(
                EVENT_DEVICE_ADDED,
                {
                    "serial_number": serial_number,
                    "device_info": device_info,
                    "restored_from_registered_list": True,
                    "force_create": True
                }
            )
            
            # Also fire platform-specific events if device has configured entities
            entities = device_info.get("entities", [])
            platforms = device_info.get("platforms", [])
            
            # Fire platform-specific events for each platform with configured entities
            # Use same naming convention as register_device_permanently():
            #   f"{EVENT_DEVICE_ADDED}_{platform}" = "easywave_device_added_sensor" etc.
            for platform in platforms:
                platform_entities = [e for e in entities if e.get("type") == platform]
                if platform_entities:
                    event_name = f"{EVENT_DEVICE_ADDED}_{platform}"
                    _LOGGER.debug("[fire_pending] Firing %s for %s (%d entities)", 
                               event_name, serial_number[-8:], len(platform_entities))
                    self.hass.bus.async_fire(
                        event_name,
                        {
                            "serial_number": serial_number,
                            "device_info": device_info,
                            "entities": platform_entities,
                            "force_create": True
                        }
                    )
            
            # Mark as fired to prevent duplicates
            self._devices_with_fired_events.add(serial_number)
            
            # Small delay to prevent overwhelming the system
            await asyncio.sleep(0.05)
        
        # Check for missing switch entities on heating_cooling devices
        await self._check_and_repair_missing_switch_entities()
        
        _LOGGER.info("✅ All device events fired after platform setup")

    async def _check_and_repair_missing_switch_entities(self) -> None:
        """Check for heating_cooling devices that are missing switch entities and repair them.
        
        Routes through EVENT_DEVICE_ADDED so the central dispatcher handles entity creation
        via the registered platform handlers.
        """
        _LOGGER.debug("🔍 Checking for missing switch entities on heating_cooling devices...")
        
        for serial_number, device_info in self._registered_devices.items():
            receiver_kind = device_info.get("receiver_kind")
            if receiver_kind == "heating_cooling":
                platforms = device_info.get("platforms", [])
                if "switch" not in platforms:
                    _LOGGER.info("🔧 Repairing missing switch entity for heating_cooling device %s", serial_number)
                    
                    # Update device info to include switch platform
                    device_info["platforms"] = platforms + ["switch"]
                    await self._save_registered_devices()
                    
                    # Fire EVENT_DEVICE_ADDED so the central dispatcher creates the entities
                    # via the registered switch platform handler.
                    self.hass.bus.async_fire(
                        EVENT_DEVICE_ADDED,
                        {
                            "serial_number": serial_number,
                            "device_info": device_info,
                            "missing_entity_repair": True,
                            "force_create": True
                        }
                    )
                    
                    _LOGGER.info("✅ Fired switch repair event for device %s", serial_number)
                    await asyncio.sleep(0.1)
    
    async def repair_orphaned_devices(self) -> int:
        """Manually repair devices that exist in storage but have no Home Assistant entities."""
        repaired_count = 0
        
        for serial_number, device_info in self._registered_devices.items():
            ha_device_id = device_info.get("homeassistant_device_id")
            ha_entities = device_info.get("homeassistant_entities", [])
            
            if not ha_device_id or not ha_entities:
                _LOGGER.info("🔧 Repairing orphaned device: %s", device_info.get("name", serial_number[-8:]))
                
                self.hass.bus.async_fire(
                    EVENT_DEVICE_ADDED,
                    {
                        "serial_number": serial_number,
                        "device_info": device_info,
                        "orphaned_device_repair": True,
                        "force_create": True
                    }
                )
                repaired_count += 1
                await asyncio.sleep(0.1)  # Small delay between repairs
        
        _LOGGER.info("✅ Repaired %d orphaned devices", repaired_count)
        return repaired_count
    
    async def update_device_ha_info(self, serial_number: str, device_id: str = None, entity_ids: list = None):
        """Update Home Assistant device and entity information for a registered device."""
        if serial_number not in self._registered_devices:
            return
        
        device_info = self._registered_devices[serial_number]
        updated = False
        
        if device_id and device_info.get("homeassistant_device_id") != device_id:
            device_info["homeassistant_device_id"] = device_id
            updated = True
        
        if entity_ids is not None:
            # Update entity list, preserving existing ones and adding new ones
            existing_entities = device_info.get("homeassistant_entities", [])
            existing_entity_ids = {e.get("entity_id") for e in existing_entities if e.get("entity_id")}
            
            for entity_id in entity_ids:
                if entity_id not in existing_entity_ids:
                    existing_entities.append({
                        "entity_id": entity_id,
                        "platform": DOMAIN,
                        "device_class": None,
                        "name": None
                    })
            
            device_info["homeassistant_entities"] = existing_entities
            updated = True
        
        if updated:
            # Also update the legacy devices dict
            self.devices[serial_number] = device_info
            await self._save_registered_devices()
            _LOGGER.debug("✅ Updated HA info for device %s", serial_number[-8:])

    @property
    def is_setup_mode_active(self) -> bool:
        """Check if setup mode is currently active."""
        return self._setup_mode_active

    @property
    def transceiver_type(self) -> TransceiverType:
        """Get the transceiver type."""
        return self.transceiver.transceiver_type

    async def _detect_and_repair_missing_entities(self) -> int:
        """🔧 NEW: Detect and repair devices missing entities.
        
        Runs AFTER platform setup to ensure entity registry is populated.
        Only repairs devices where entity loss is clearly unintended.
        """
        repaired = 0
        
        er = __import__('homeassistant.helpers.entity_registry', fromlist=['async_get']).async_get
        
        try:
            entity_registry = er(self.hass)
            
            for serial, device_info in self._registered_devices.items():
                device_name = device_info.get('name', f'Device {serial[-8:]}')
                device_type = device_info.get('type', 'unknown')
                
                # Find actual entities for this device in HA
                # NOTE: serial is UPPERCASE but unique_ids are lowercase — normalize
                serial_lower = serial.lower()
                ha_id_lower = self.get_ha_device_identifier(serial).lower()
                actual_entities = [
                    ent for ent in entity_registry.entities.values()
                    if ent.unique_id and ent.platform == DOMAIN and
                       (ent.unique_id.lower().startswith(ha_id_lower) or ent.unique_id.lower().startswith(serial_lower))
                ]
                
                configured_entities = device_info.get('entities', [])
                
                # Only repair if:
                # 1. Device has configured entities 
                # 2. AND no entities exist in HA
                # 3. AND device type should have entities
                if configured_entities and not actual_entities and device_type != "ew_receiver":
                    # For transmitters, it's normal they might not have entities loaded yet
                    # So only log at debug level for transmitters
                    if device_type == "ew_transmitter":
                        _LOGGER.debug("🔍 Transmitter %s waiting for entity platform to create entities", 
                                    device_name)
                    else:
                        # For other types, this is a real problem
                        _LOGGER.warning("⚠️ Device %s (%s) has 0 entities in HA but %d configured", 
                                      device_name, serial[-8:], len(configured_entities))
                        
                        # Only fire repair event for sensor types (transmitters often have delayed platform loading)
                        if device_type in ["ewneo_sensor", "ew_sensor"]:
                            # Regenerate entity specs
                            device_info = await self._regenerate_entity_specs(serial, device_info)
                            
                            # Fire event to recreate
                            self.hass.bus.async_fire(
                                EVENT_DEVICE_ADDED,
                                {
                                    "serial_number": serial,
                                    "device_info": device_info,
                                    "missing_entities_repair": True,
                                    "force_create": True
                                }
                            )
                            
                            repaired += 1
                            await asyncio.sleep(0.1)
        
        except Exception as e:
            _LOGGER.debug("Could not check for missing entities: %s", e)
        
        if repaired > 0:
            _LOGGER.info("✅ Repaired %d devices with missing entities", repaired)
            await self._save_registered_devices()
        
        return repaired

    async def _validate_all_state_sources(self) -> Dict[str, int]:
        """✅ NEW: Validate consistency between all 3 persistence sources.
        
        Checks:
        1. _registered_devices (registered_devices.json) - main source
        2. device_manager (managed_devices.json) - whitelist
        3. HA Device Registry - native HA storage
        
        Keeps them in sync automatically.
        """
        stats = {
            'registered_devices_count': len(self._registered_devices),
            'device_manager_count': 0,
            'entity_registry_count': 0,
            'conflicts_found': 0,
            'corrections_applied': 0,
        }
        
        try:
            from homeassistant.helpers import entity_registry as er
            
            # Count devices in DeviceManager
            if self.device_manager:
                stats['device_manager_count'] = len(self.device_manager.get_all_devices())
            
            # Count our entities in HA Entity Registry
            entity_registry = er.async_get(self.hass)
            our_entities = [
                ent for ent in entity_registry.entities.values()
                if ent.platform == DOMAIN
            ]
            stats['entity_registry_count'] = len(our_entities)
            
            # Check for mismatches between _registered_devices and DeviceManager
            registered_serials = set(self._registered_devices.keys())
            dm_serials = set(self.device_manager.get_all_devices().keys())
            
            # Devices in registered_devices but NOT in DeviceManager
            missing_from_dm = registered_serials - dm_serials
            if missing_from_dm:
                _LOGGER.warning("⚠️ %d devices in _registered_devices but NOT in DeviceManager - syncing", 
                               len(missing_from_dm))
                for serial in missing_from_dm:
                    device_info = self._registered_devices[serial]
                    self.device_manager.add_device(
                        serial_number=serial,
                        device_type=device_info.get("type", "unknown"),
                        name=device_info.get("name"),
                        rx11_index=device_info.get("rx11_index")
                    )
                    stats['corrections_applied'] += 1
            
            # Devices in DeviceManager but NOT in _registered_devices - CRITICAL
            orphaned_in_dm = dm_serials - registered_serials
            if orphaned_in_dm:
                _LOGGER.error("🛑 %d ORPHANED devices only in DeviceManager (not in _registered_devices)!", 
                            len(orphaned_in_dm))
                for serial in orphaned_in_dm:
                    _LOGGER.warning("  ❌ Serial: %s", serial[-8:])
                stats['conflicts_found'] += len(orphaned_in_dm)
            
            if stats['corrections_applied'] > 0:
                await self.device_manager.save()
            
            _LOGGER.info("📊 State Validation: %d registered, %d DeviceManager, %d entities, "
                        "%d conflicts, %d corrections",
                        stats['registered_devices_count'],
                        stats['device_manager_count'],
                        stats['entity_registry_count'],
                        stats['conflicts_found'],
                        stats['corrections_applied'])
            
        except Exception as e:
            _LOGGER.warning("⚠️ State validation error: %s", e)
        
        return stats

    async def async_setup(self) -> bool:
        """Set up the coordinator.
        
        Returns True when device data was loaded successfully and platforms
        can be set up.  Returns False only when data loading itself fails
        (in which case ConfigEntryNotReady should be raised).
        
        If the USB transceiver is not available, the coordinator still returns
        True so that entity objects are created for existing registered devices.
        The entities will be "unavailable" (via _is_rx11_connected()) until the
        transceiver comes online.  The reconnection logic in _async_update_data()
        handles bringing the connection up later.
        """
        try:
            # ═══════════════════════════════════════════════════
            # Phase 1: Load device data (MUST succeed for setup)
            # ═══════════════════════════════════════════════════
            
            # Load consolidated state from previous restarts
            await self.state_manager.load()
            
            # Initialize registered device management with timeout
            try:
                await asyncio.wait_for(self._load_registered_devices(), timeout=10.0)
                await asyncio.wait_for(self._migrate_existing_devices_to_registered_list(), timeout=5.0)
            except asyncio.TimeoutError:
                _LOGGER.error("⏱️ Timeout loading registered devices — keeping partial state (%d devices)",
                            len(self._registered_devices))
                # Do NOT clear _registered_devices — keep whatever was loaded before the timeout.
                # Clearing it would cause migration to run and overwrite good data with stale data.
                return False
            
            # Sync StateManager with loaded devices
            self.state_manager.set_registered_devices(self._registered_devices)
            
            # Sync StateManager with EWB indices (already built by _load_registered_devices)
            self.state_manager.set_ewb_indices(self._used_ewb_indices)
            
            # Sync StateManager with Receiver indices (already built by _load_registered_devices)
            self.state_manager.set_ew_receiver_indices(self._used_ew_receiver_indices)
            
            # IMPORTANT: Sync DeviceManager whitelist FROM _registered_devices with timeout
            try:
                await asyncio.wait_for(self._sync_device_manager_from_registered(), timeout=5.0)
            except asyncio.TimeoutError:
                _LOGGER.error("⏱️ Timeout syncing DeviceManager — continuing with current state")
            
            # Validate all state sources are consistent with timeout
            try:
                await asyncio.wait_for(self._validate_all_state_sources(), timeout=5.0)
            except asyncio.TimeoutError:
                _LOGGER.error("⏱️ Timeout validating state sources — continuing")
            
            # Save to consolidate all data into single file after migration
            await self._save_registered_devices()
            
            # ═══════════════════════════════════════════════════
            # Phase 2: Connect to USB transceiver (may fail gracefully)
            # ═══════════════════════════════════════════════════
            
            transceiver_connected = False
            
            try:
                setup_ok = await asyncio.wait_for(self.transceiver.async_setup(self.hass), timeout=10.0)
                if not setup_ok:
                    _LOGGER.debug("Transceiver setup returned False (offline mode)")
                else:
                    try:
                        transceiver_connected = await asyncio.wait_for(self.transceiver.connect(), timeout=15.0)
                        if not transceiver_connected:
                            _LOGGER.debug("Transceiver connect returned False (offline mode)")
                    except asyncio.TimeoutError:
                        _LOGGER.debug("Timeout connecting to transceiver (offline mode)")
            except asyncio.TimeoutError:
                _LOGGER.debug("Timeout during transceiver setup (offline mode)")
            except Exception as e:
                _LOGGER.debug("Transceiver setup/connect failed: %s (offline mode)", e)
            
            # ═══════════════════════════════════════════════════
            # Phase 3: Post-connection setup (only when connected)
            # ═══════════════════════════════════════════════════
            
            # Set coordinator reference (safe even when disconnected)
            if hasattr(self.transceiver, 'set_coordinator_reference'):
                self.transceiver.set_coordinator_reference(self)
            
            if transceiver_connected:
                # Synchronize EWB index tracking with transceiver wrapper
                await self._sync_ewb_indices_with_wrapper()
                
                # Synchronize Easywave Receiver index tracking with transceiver wrapper
                await self._sync_ew_receiver_indices_with_wrapper()
                
                # Set telegram callback
                self.transceiver.set_telegram_callback(self._handle_telegram)
                
                # Load device configuration from storage
                await self._load_device_configuration(fire_events=False)
                
                # EWB telegram monitoring is handled by the wrapper's continuous loop
                _LOGGER.info("📡 EWB telegram monitoring handled by wrapper continuous loop")
                
                # Mark coordinator as successfully updated (connected)
                self.last_update_success = True
            else:
                # Set telegram callback even when offline — will be ready when connection comes
                self.transceiver.set_telegram_callback(self._handle_telegram)
                
                # Load device configuration from storage (doesn't need transceiver)
                await self._load_device_configuration(fire_events=False)
                
                # Mark as NOT successfully updated (entities will be "unavailable")
                self.last_update_success = False
                _LOGGER.info("📡 Coordinator loaded %d devices in offline mode — "
                           "entities will become available when USB device connects",
                           len(self._registered_devices))
            
            self._setup_complete = True
            
            # Setup USB event monitoring for fast reconnect
            await self._setup_usb_monitoring()
            
            _LOGGER.info("Coordinator setup completed for %s transceiver (connected=%s, devices=%d)", 
                        self.transceiver_type.value, transceiver_connected, len(self._registered_devices))
            return True
            
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout during coordinator data loading")
            self.last_update_success = False
            return False
        except Exception as e:
            _LOGGER.error("Error setting up coordinator: %s", e)
            self.last_update_success = False
            return False

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        _LOGGER.debug("🛑 Shutting down modern coordinator...")
        
        # Set shutdown event
        self._shutdown_event.set()
        
        # Cancel EWB monitoring task
        if hasattr(self, '_ewb_monitoring_task') and self._ewb_monitoring_task:
            self._ewb_monitoring_task.cancel()
            try:
                await self._ewb_monitoring_task
            except asyncio.CancelledError:
                pass
            _LOGGER.info("✅ EWB monitoring task cancelled")
        
        # Remove telegram listener
        if self._telegram_listener_remove:
            self._telegram_listener_remove()
            self._telegram_listener_remove = None
        
        # Stop USB monitoring
        if hasattr(self, '_usb_monitor_task') and self._usb_monitor_task:
            self._usb_monitor_task.cancel()
            try:
                await self._usb_monitor_task
            except asyncio.CancelledError:
                pass
            _LOGGER.debug("USB monitoring stopped")
        
        # Disconnect transceiver
        if self.transceiver:
            try:
                await self.transceiver.disconnect()
                _LOGGER.info("✅ Transceiver disconnected")
            except Exception as e:
                _LOGGER.error("❌ Error disconnecting transceiver: %s", e)
        
        # Sync current state to StateManager before saving
        self.state_manager.set_registered_devices(self._registered_devices)
        self.state_manager.set_ewb_indices(self._used_ewb_indices)
        self.state_manager.set_ew_receiver_indices(self._used_ew_receiver_indices)
        
        # Save consolidated state before shutdown
        await self.state_manager.save()
        
        # Clean up old separate files from previous installations
        await self.state_manager.cleanup_old_files()
        
        _LOGGER.info("✅ Modern coordinator shutdown completed")

    async def async_persistent_save_all(self) -> bool:
        """Save all persistence data atomically (delegates to defensive state manager).
        
        Simplified API - just call this, everything else is handled automatically:
        - Devices saved
        - Indices saved  
        - Transactional consistency guaranteed
        - Integrity checking automatic
        
        Returns:
            True if all saves succeeded, False otherwise (but data is safe)
        """
        return await self.defensive_state_manager.save_all()

    async def async_load_all(self) -> bool:
        """Load all persistence data defensively (delegates to defensive state manager).
        
        Simplified API - just call this, everything else is handled:
        - Devices loaded with backups
        - Indices loaded with backups
        - Never loses data on failure
        - Auto-repair on integrity issues
        
        Returns:
            True if all loads succeeded, False if in degraded mode (but data is safe)
        """
        return await self.defensive_state_manager.load_all()

    async def _setup_usb_monitoring(self) -> None:
        """Setup USB device monitoring for fast reconnect on device insertion."""
        try:
            # Check if pyudev is available
            import pyudev
            
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by(subsystem='usb')
            
            async def usb_event_handler():
                """Handle USB events in async context."""
                try:
                    # Run monitor observation in executor to not block event loop
                    def observe_usb():
                        for device in iter(monitor.poll, None):
                            if self._shutdown_event.is_set():
                                break
                            
                            # Check if this is an EASYWAVE device being added
                            if device.action == 'add':
                                try:
                                    # Check VID/PID
                                    vid = device.get('ID_VENDOR_ID')
                                    pid = device.get('ID_MODEL_ID')
                                    
                                    # Check against all supported VID/PID pairs
                                    from .const import SUPPORTED_USB_IDS
                                    vid_int = int(vid, 16) if vid else 0
                                    pid_int = int(pid, 16) if pid else 0
                                    if (vid_int, pid_int) in SUPPORTED_USB_IDS:
                                        _LOGGER.info("🔌 Easywave USB device detected (VID:0x%04X PID:0x%04X) - triggering fast reconnect", vid_int, pid_int)
                                        # Signal fast reconnect
                                        self._usb_event_detected = True
                                except Exception as e:
                                    _LOGGER.debug("Error checking USB device: %s", e)
                    
                    await self.hass.async_add_executor_job(observe_usb)
                    
                except Exception as e:
                    _LOGGER.debug("USB monitoring stopped: %s", e)
            
            # Start USB monitoring task
            self._usb_monitor_task = asyncio.create_task(usb_event_handler())
            _LOGGER.debug("✅ USB event monitoring started for fast reconnect")
            
        except ImportError:
            _LOGGER.debug("pyudev not available - using standard reconnect timing")
            self._usb_monitor_task = None
        except Exception as e:
            _LOGGER.debug("Could not setup USB monitoring: %s", e)
            self._usb_monitor_task = None

    async def _restore_gateway_filters(self) -> None:
        """Restore all gateway filters after reconnect.
        
        This method clears existing filters first, then collects all unique gateway 
        serials from registered EWneo devices and adds them to the RX11 filter to 
        enable bidirectional communication.
        """
        try:
            if not hasattr(self.transceiver, 'rx11_ewb_add_filter'):
                _LOGGER.debug("Transceiver does not support gateway filters")
                return
            
            # Clear existing filters first to avoid ERR_FILTER_OUT_OF_MEM
            if hasattr(self.transceiver, 'rx11_ewb_clear_filter'):
                try:
                    clear_result = await self.transceiver.rx11_ewb_clear_filter()
                    if clear_result:
                        _LOGGER.debug("✅ EWB filter successfully cleared")
                    else:
                        _LOGGER.warning("⚠️ Failed to clear EWB filter")
                except Exception as e:
                    _LOGGER.error("❌ Exception while clearing EWB filter: %s", e)
            
            # Collect all unique gateway serials from registered devices
            gateway_serials = set()
            
            for serial_number, device_info in self._registered_devices.items():
                device_type = device_info.get("type", "")
                if device_type.startswith("ewneo_") and device_type != "ewneo_sensor":
                    gateway_serial = device_info.get("gateway_serial")
                    if gateway_serial:
                        gateway_serials.add(gateway_serial)
            
            if not gateway_serials:
                _LOGGER.debug("No EWneo devices found - skipping gateway filter restore")
                return
            
            _LOGGER.info("🔧 Restoring %d gateway filter(s) after reconnect...", len(gateway_serials))
            
            # Add each gateway to the filter
            success_count = 0
            for gateway_serial in gateway_serials:
                try:
                    result = await self.transceiver.rx11_ewb_add_filter(gateway_serial)
                    if result:
                        success_count += 1
                        _LOGGER.debug("✅ Gateway %s added to filter", gateway_serial[-8:])
                    else:
                        _LOGGER.warning("⚠️ Failed to add gateway %s to filter", gateway_serial[-8:])
                except Exception as e:
                    _LOGGER.error("❌ Exception adding gateway %s: %s", gateway_serial[-8:], e)
            
            _LOGGER.info("✅ %d of %d gateway filter(s) successfully restored", success_count, len(gateway_serials))
            
        except Exception as e:
            _LOGGER.error("❌ Error restoring gateway filters: %s", e)

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from the transceiver."""
        if not self.transceiver:
            raise UpdateFailed("Transceiver not available")
            
        # Check if transceiver connection changed
        is_connected = self.transceiver.is_connected
        
        # Try to reconnect if disconnected
        if not is_connected:
            if not hasattr(self, '_reconnect_attempts'):
                self._reconnect_attempts = 0
                self._last_reconnect_time = 0
            
            import time
            current_time = time.time()
            
            # Use faster interval if USB event was detected recently
            # First 3 attempts: 0.3s, then 1s, after 10 attempts: 2s
            if self._reconnect_attempts < 3:
                reconnect_interval = 0.3  # Very fast initial attempts
            elif self._reconnect_attempts < 10:
                reconnect_interval = 1.0  # Medium speed
            else:
                reconnect_interval = 2.0  # Standard interval
            
            # Even faster if USB event detected
            if hasattr(self, '_usb_event_detected') and self._usb_event_detected:
                reconnect_interval = 0.1  # Immediate reconnect after USB event
                self._usb_event_detected = False  # Reset flag
            
            # Wait between reconnect attempts
            if current_time - self._last_reconnect_time >= reconnect_interval:
                self._reconnect_attempts += 1
                self._last_reconnect_time = current_time
                
                # Only log first attempt and every 10th attempt to reduce spam
                if self._reconnect_attempts == 1:
                    _LOGGER.debug("Starting reconnect attempts...")
                elif self._reconnect_attempts % 10 == 0:
                    _LOGGER.debug("🔄 Reconnect running (attempt %d)...", self._reconnect_attempts)
                
                try:
                    # Try to reconnect
                    await self.transceiver.disconnect()
                    await asyncio.sleep(0.5)  # Brief pause before reconnect
                    connected = await self.transceiver.connect()
                    
                    if connected:
                        _LOGGER.info("✅ RX11 verbunden nach %d Versuchen", 
                                   self._reconnect_attempts)
                        self._reconnect_attempts = 0
                        is_connected = True
                        
                        # Post-connection setup: sync indices and callbacks
                        # This is critical when starting in offline mode — indices
                        # were loaded but not synced to wrapper yet.
                        try:
                            if hasattr(self.transceiver, 'set_coordinator_reference'):
                                self.transceiver.set_coordinator_reference(self)
                            await self._sync_ewb_indices_with_wrapper()
                            await self._sync_ew_receiver_indices_with_wrapper()
                            self.transceiver.set_telegram_callback(self._handle_telegram)
                        except Exception as sync_err:
                            _LOGGER.warning("⚠️ Post-reconnect sync error (non-fatal): %s", sync_err)
                        
                        # Update config entry with actual device path and USB identity
                        self._update_config_entry_usb_info()
                        
                        # Restore all gateway filters after reconnect
                        await self._restore_gateway_filters()
                        
                        # Immediately update all listeners on successful reconnect
                        self.async_update_listeners()
                    # Don't log every failed attempt - reduce spam
                except Exception as e:
                    _LOGGER.debug("Reconnect attempt %d failed: %s", 
                                  self._reconnect_attempts, e)
            
            # If still not connected after reconnect attempt, raise UpdateFailed
            # This will set last_update_success to False and mark entities as unavailable
            if not is_connected:
                # Update connection state tracking and notify entities BEFORE raising exception
                if not hasattr(self, '_last_connection_state'):
                    self._last_connection_state = True  # Assume was connected
                
                if self._last_connection_state != is_connected:
                    _LOGGER.warning("⚠️ RX11 USB Transceiver nicht verbunden — Entitäten nicht verfügbar")
                    # Show persistent notification about disconnection
                    lang = get_language(self.hass) if self.hass else DEFAULT_LANGUAGE
                    await self.hass.services.async_call(
                        "persistent_notification",
                        "create",
                        {
                            "notification_id": "easywave_rx11_disconnected",
                            "title": translate("notification.rx11_disconnected_title", lang),
                            "message": translate("notification.rx11_disconnected_message", lang),
                        },
                        blocking=False,
                    )
                    
                    # Trigger entity state update for all entities BEFORE exception
                    self.async_update_listeners()
                    self._last_connection_state = is_connected
                
                raise UpdateFailed("RX11 Transceiver not connected")
        else:
            # Reset reconnect counter on successful connection
            if hasattr(self, '_reconnect_attempts') and self._reconnect_attempts > 0:
                _LOGGER.info("✅ RX11 Verbindung wiederhergestellt")
                self._reconnect_attempts = 0
        
        if hasattr(self, '_last_connection_state') and self._last_connection_state != is_connected:
            if is_connected:
                _LOGGER.info("✅ RX11 USB Transceiver verbunden - Entitäten sind verfügbar")
                # Clear any existing disconnection notifications
                await self.hass.services.async_call(
                    "persistent_notification",
                    "dismiss",
                    {"notification_id": "easywave_rx11_disconnected"},
                    blocking=False,
                )
                # Show reconnection success notification
                lang = get_language(self.hass) if self.hass else DEFAULT_LANGUAGE
                await self.hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "notification_id": "easywave_rx11_reconnected",
                        "title": translate("notification.rx11_reconnected_title", lang),
                        "message": translate("notification.rx11_reconnected_message", lang),
                    },
                    blocking=False,
                )
                # Auto-dismiss success notification after 10 seconds
                async def dismiss_success():
                    await asyncio.sleep(10)
                    await self.hass.services.async_call(
                        "persistent_notification",
                        "dismiss",
                        {"notification_id": "easywave_rx11_reconnected"},
                        blocking=False,
                    )
                self.hass.async_create_task(dismiss_success())
            else:
                _LOGGER.debug("⚠️ RX11 USB Transceiver nicht verbunden - Entitäten sind nicht verfügbar")
                # Show persistent notification about disconnection
                lang = get_language(self.hass) if self.hass else DEFAULT_LANGUAGE
                await self.hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "notification_id": "easywave_rx11_disconnected",
                        "title": translate("notification.rx11_disconnected_title", lang),
                        "message": translate("notification.rx11_disconnected_message", lang),
                    },
                    blocking=False,
                )
            
            # Trigger entity state update for all entities
            self.async_update_listeners()
        
        self._last_connection_state = is_connected
            
        try:
            # Increment update counter
            self._update_count += 1
            
            # Get versions only occasionally to reduce hardware queries
            if self._update_count == 1 or self._update_count % 30 == 0:
                try:
                    self._cached_hw_version = await self.transceiver.get_hw_version()
                    self._cached_fw_version = await self.transceiver.get_fw_version()
                    if self._update_count == 1:
                        _LOGGER.debug("✅ Versions cached: HW=%s, FW=%s", self._cached_hw_version, self._cached_fw_version)
                except Exception as e:
                    _LOGGER.debug("Could not get transceiver versions: %s", e)
            
            return {
                "hw_version": self._cached_hw_version,
                "fw_version": self._cached_fw_version,
                "device_count": len(self.devices),
                "transceiver_type": self.transceiver_type.value,
                "last_update": datetime.now().isoformat(),
                "transceiver_connected": is_connected,
            }
        except Exception as e:
            raise UpdateFailed(f"Error fetching data from transceiver: {e}") from e

    def _update_config_entry_usb_info(self) -> None:
        """Persist current USB identity (path, serial, VID, PID) into config entry.
        
        Called after a successful (re)connect so the config entry reflects
        the actually connected stick for the next restart.
        """
        if not hasattr(self.transceiver, 'device_path') or not self.transceiver.device_path:
            return

        new_path = self.transceiver.device_path
        new_serial = (
            self.transceiver.get_usb_serial_number()
            if hasattr(self.transceiver, 'get_usb_serial_number')
            else None
        )

        # Read current VID/PID from wrapper if available
        new_vid = None
        new_pid = None
        if hasattr(self.transceiver, '_rx11_wrapper') and self.transceiver._rx11_wrapper:
            wrapper = self.transceiver._rx11_wrapper
            new_vid = getattr(wrapper, '_usb_vid', None)
            new_pid = getattr(wrapper, '_usb_pid', None)

        old_data = self.config_entry.data
        updates = {}
        if new_path != old_data.get(CONF_DEVICE_PATH):
            updates[CONF_DEVICE_PATH] = new_path
        if new_serial and new_serial != old_data.get(CONF_USB_SERIAL_NUMBER):
            updates[CONF_USB_SERIAL_NUMBER] = new_serial
        if new_vid is not None and new_vid != old_data.get(CONF_USB_VID):
            updates[CONF_USB_VID] = new_vid
        if new_pid is not None and new_pid != old_data.get(CONF_USB_PID):
            updates[CONF_USB_PID] = new_pid

        if updates:
            _LOGGER.info("🔄 Updating config entry USB info: %s", 
                        {k: v for k, v in updates.items()})
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**old_data, **updates},
            )

    async def _handle_telegram(self, telegram_data: Dict[str, Any]) -> None:
        """Handle incoming telegram from transceiver - only process registered devices."""
        try:
            serial_number = telegram_data.get("serial_number")
            _LOGGER.debug("Handling telegram from %s, type=%s", 
                          serial_number[-8:] if serial_number else "unknown",
                          telegram_data.get("type"))
            
            if not serial_number:
                _LOGGER.warning("Received telegram without serial number")
                return

            # Check if this telegram is from a registered EWneo device
            is_ewneo_device = self._is_ewneo_device_telegram(serial_number)
            
            # Fire button events IMMEDIATELY for Easywave Transmitters, but NOT for EWneo devices
            if (telegram_data.get("type") == "ew_transmitter" or telegram_data.get("info_type") in [0, 1]) and not is_ewneo_device:
                _LOGGER.debug("Firing events for Easywave Transmitter telegram")
                
                # Get the function/action from telegram
                action = telegram_data.get("function")
                battery_recovered = telegram_data.get("battery_recovered", False)
                
                # Fire battery events separately from button events
                if action == "battery_low":
                    _LOGGER.debug("🔋 Firing battery_low event (transmitter-wide)")
                    await self._fire_battery_event(serial_number, telegram_data, "battery_low")
                    
                else:
                    # Normal button press/release - fire button events
                    _LOGGER.debug("Firing button events (press/release)")
                    await self._fire_button_events(serial_number, telegram_data)
                    
                    # Fire battery_ok event only when battery was recovered (second normal press after battery_low)
                    if battery_recovered:
                        _LOGGER.warning("🔋 Firing battery_ok event (battery recovered/replaced)")
                        await self._fire_battery_event(serial_number, telegram_data, "battery_ok")
                    
            elif is_ewneo_device:
                _LOGGER.debug("Processing EWneo device telegram %s as state update", serial_number[-8:])
            
            # Check if device is registered for management
            is_registered = self.is_device_registered(serial_number)
            _LOGGER.debug("Device %s registered: %s", serial_number[-8:], is_registered)
            
            if not is_registered:
                # Only process learn telegrams for potential new device registration
                is_learn = telegram_data.get("is_learn_telegram", False) or self.is_setup_mode_active
                if not is_learn:
                    _LOGGER.debug("📡 Ignoring telegram from unregistered device %s (not in setup mode)", 
                                serial_number[-8:])
                    return
                _LOGGER.debug("Processing telegram for potential registration of device %s", 
                           serial_number[-8:])
            
            # Update device info if known device - MUST use FULL serial (32 chars)
            if serial_number in self.devices:
                _LOGGER.debug("Device found in coordinator for serial %s...%s", serial_number[:8], serial_number[-8:])
                self.devices[serial_number]["last_seen"] = time.time()
                self.devices[serial_number]["last_telegram"] = telegram_data
                
                # CRITICAL: For EWneo-Sensoren, directly update temperature/humidity/battery in devices dict
                device_type = self.devices[serial_number].get("type")
                if device_type in ["ewneo_sensor", "ew_sensor"]:
                    # Extract measurements from telegram
                    if "temperature" in telegram_data:
                        self.devices[serial_number]["temperature"] = telegram_data["temperature"]
                        _LOGGER.debug("Temperature updated: %.1f°C", telegram_data["temperature"])
                    
                    if "humidity" in telegram_data:
                        self.devices[serial_number]["humidity"] = telegram_data["humidity"]
                        _LOGGER.debug("Humidity updated: %.1f%%", telegram_data["humidity"])
                    
                    if "battery_level" in telegram_data:
                        self.devices[serial_number]["battery_level"] = telegram_data["battery_level"]
                        _LOGGER.debug("Battery updated in devices dict: %d%%", telegram_data["battery_level"])
                    
                    # Update device instance for timestamp tracking and availability monitoring
                    device_instance = self.get_device_instance(serial_number)
                    if device_instance and hasattr(device_instance, 'process_telegram'):
                        device_instance.process_telegram(telegram_data)
                        _LOGGER.debug("✅ Called process_telegram on device instance %s", serial_number[-8:])
            else:
                # Device not in self.devices, but is registered - add it now!
                if is_registered and serial_number in self._registered_devices:
                    _LOGGER.debug("Adding registered device to devices dict: %s...%s", 
                                  serial_number[:8], serial_number[-8:])
                    self.devices[serial_number] = self._registered_devices[serial_number].copy()
                    self.devices[serial_number]["last_seen"] = time.time()
                    self.devices[serial_number]["last_telegram"] = telegram_data
                    
                    # Update measurements
                    if "temperature" in telegram_data:
                        self.devices[serial_number]["temperature"] = telegram_data["temperature"]
                    if "humidity" in telegram_data:
                        self.devices[serial_number]["humidity"] = telegram_data["humidity"]
                    if "battery_level" in telegram_data:
                        self.devices[serial_number]["battery_level"] = telegram_data["battery_level"]
                        _LOGGER.debug("Battery set in newly added device: %d%%", telegram_data["battery_level"])
                    
                    # Update device instance for timestamp tracking
                    device_type = self.devices[serial_number].get("type")
                    if device_type in ["ewneo_sensor", "ew_sensor"]:
                        device_instance = self.get_device_instance(serial_number)
                        if device_instance and hasattr(device_instance, 'process_telegram'):
                            device_instance.process_telegram(telegram_data)
                            _LOGGER.debug("✅ Called process_telegram on newly added device %s", serial_number[-8:])
                else:
                    _LOGGER.warning("Device NOT found in coordinator.devices! Serial: %s...%s", 
                                  serial_number[:8], serial_number[-8:])
            
            # Fire specific entity events for sensors (temperature, humidity, battery) if registered
            if is_registered and telegram_data.get("type") in ["ewneo_sensor", "ew_sensor"]:
                _LOGGER.debug("Firing sensor-specific events for registered sensor %s", serial_number[-8:])
                await self._fire_specific_entity_events(serial_number, telegram_data)
            
            # ALWAYS fire telegram event with FULL serial (even if not in devices dict)
            self.hass.bus.async_fire(
                EVENT_TELEGRAM_RECEIVED,
                {
                    "serial_number": serial_number,
                    "telegram_data": telegram_data,
                }
            )
            
            # Fire device updated event if device exists in registry
            if serial_number in self.devices:
                _LOGGER.debug("Firing EVENT_DEVICE_UPDATED with full serial: %s", serial_number[:8]+"..."+serial_number[-8:])
                
                # Extract battery_level from telegram_data if present
                event_data = {
                    "serial_number": serial_number,
                    "device_info": self.devices[serial_number],
                    "telegram_data": telegram_data,
                }
                
                # Add battery_level to top level for easier access by sensors
                if "battery_level" in telegram_data:
                    event_data["battery_level"] = telegram_data["battery_level"]
                    _LOGGER.debug("🔋 Including battery_level in EVENT_DEVICE_UPDATED: %d%%", telegram_data["battery_level"])
                
                self.hass.bus.async_fire(EVENT_DEVICE_UPDATED, event_data)
                
                # Process EWneo device state updates
                if is_ewneo_device:
                    await self._process_ewneo_state_update(serial_number, telegram_data)
                
                # Notify listeners directly for faster updates (instead of full refresh)
                _LOGGER.debug("🔔 Notifying coordinator listeners...")
                self.async_set_updated_data(self.devices)
                _LOGGER.debug("Telegram handling complete")
            else:
                # Unknown device - ignore (no auto-discovery)
                device_type = telegram_data.get("type", "unknown")
                _LOGGER.debug("Received telegram from unknown device %s (type: %s) - ignoring", 
                            serial_number, device_type)
                    
        except Exception as e:
            _LOGGER.error("❌ Error handling telegram: %s", e, exc_info=True)

    async def _register_device_with_transceiver(self, serial_number: str, device_info: Dict[str, Any], telegram_data: Dict[str, Any]) -> None:
        """Register device with transceiver using structured device classes."""
        try:
            # Create device instance using device factory first
            if hasattr(self.transceiver, 'device_factory'):
                # Prepare kwargs for EWneo devices
                create_kwargs = {}
                if device_info.get("neo_device"):
                    create_kwargs['gateway_serial'] = device_info.get('gateway_serial')
                    create_kwargs['transceiver'] = self.transceiver
                
                device_instance = self.transceiver.device_factory.create_device(
                    serial_number=serial_number,
                    device_info=device_info,
                    telegram_data=telegram_data,
                    **create_kwargs
                )
                
                if device_instance:
                    # Store device instance in transceiver
                    if not hasattr(self.transceiver, '_device_instances'):
                        self.transceiver._device_instances = {}
                    self.transceiver._device_instances[serial_number] = device_instance
                    
                    # Initialize EWneo devices (queries state)
                    if device_info.get("neo_device") and hasattr(device_instance, 'async_initialize'):
                        _LOGGER.info("🆕 Initializing new EWneo device: %s", serial_number)
                        await device_instance.async_initialize(is_restoration=False)
                    
                    _LOGGER.debug("Created and registered device instance for %s: %s", 
                                serial_number, type(device_instance).__name__)
                else:
                    _LOGGER.warning("Device factory failed to create instance for %s", serial_number)
            
            # Register with basic transceiver for backwards compatibility
            await self.transceiver.register_device(serial_number, device_info)
                
        except Exception as e:
            _LOGGER.error("Error registering device %s with transceiver: %s", serial_number, e)

    async def _check_transmitter_upgrade(self, serial_number: str, telegram_data: Dict[str, Any]) -> None:
        """Check if an ew_transmitter should be upgraded to ew_transceiver when it sends sensor data."""
        try:
            device = self.devices.get(serial_number)
            if not device:
                return
                
            device_type = device.get("type")
            
            # Only upgrade ew_transmitter devices
            if device_type != "ew_transmitter":
                return
                
            # Check if telegram contains sensor data (temperature, humidity, battery)
            has_sensor_data = any(key in telegram_data for key in ["temperature", "humidity", "battery_level"])
            
            if has_sensor_data:
                _LOGGER.warning("🔄 Easywave Transmitter %s is sending sensor data, upgrading to EW-Transceiver", 
                              serial_number[-8:])
                
                # Update device configuration
                device_updates = {
                    "type": "ew_transceiver",
                    "device_type": "ew_transceiver", 
                    "supports_sensors": True,
                    "supports_buttons": True,
                    "bidirectional": True,
                    "sensor_types": ["temperature", "humidity", "battery"],
                    "measurement_types": ["temperature", "humidity"],
                    "available_sensors": ["temperature", "humidity", "battery"],
                    "detected_via": "auto_upgrade_from_telegram",
                    "discovered": True
                }
                
                # Update sensor values if present
                if "temperature" in telegram_data:
                    device_updates["last_temperature"] = telegram_data["temperature"]
                if "humidity" in telegram_data:
                    device_updates["last_humidity"] = telegram_data["humidity"]
                if "battery_level" in telegram_data:
                    device_updates["battery_level"] = telegram_data["battery_level"]
                
                # Update device in memory
                device.update(device_updates)
                
                # Device registry update is handled by the coordinator's device_registry instance
                # No direct device_registry.update_device call needed as it's managed by coordinator
                
                # Add missing sensor entities
                await self._add_transceiver_sensor_entities(serial_number, device)
                
                _LOGGER.info("✅ Successfully upgraded %s from Easywave Transmitter to EW-Transceiver", serial_number[-8:])
                
        except Exception as e:
            _LOGGER.error("❌ Error upgrading transmitter %s: %s", serial_number[-8:], e)

    async def _add_transceiver_sensor_entities(self, serial_number: str, device_info: Dict[str, Any]) -> None:
        """Add sensor entities for an upgraded transceiver."""
        try:
            # Get language for translations
            lang = get_language(self.hass) if self.hass else DEFAULT_LANGUAGE
            
            # Define the sensor entities that should be added
            reg_id = device_info['registration_id']
            new_entities = [
                {
                    "type": "sensor",
                    "sensor_type": "temperature", 
                    "translation_key": "temperature",  # HA looks up entity.sensor.temperature.name
                    "unique_id": make_unique_id(reg_id, "temperature", None),
                    "device_class": "temperature",
                    "unit_of_measurement": "°C",
                    "icon": "mdi:thermometer",
                    "state_class": "measurement",
                    "has_entity_name": True,
                    "current_value": device_info.get("last_temperature")
                },
                {
                    "type": "sensor",
                    "sensor_type": "humidity",
                    "translation_key": "humidity",  # HA looks up entity.sensor.humidity.name
                    "unique_id": make_unique_id(reg_id, "humidity", None),
                    "device_class": "humidity",
                    "unit_of_measurement": "%",
                    "icon": "mdi:water-percent",
                    "state_class": "measurement",
                    "has_entity_name": True,
                    "current_value": device_info.get("last_humidity")
                },
                {
                    "type": "sensor",
                    "sensor_type": "battery",
                    "translation_key": "battery",  # HA looks up entity.sensor.battery.name
                    "unique_id": make_unique_id(reg_id, "battery", None),
                    "device_class": "battery",
                    "unit_of_measurement": "%",
                    "has_entity_name": True, 
                    "icon": "mdi:battery",
                    "state_class": "measurement",
                    "current_value": device_info.get("battery_level")
                }
            ]
            
            # Add entities to device
            if "entities" not in device_info:
                device_info["entities"] = []
                
            # Add new entities if they don't already exist
            existing_unique_ids = {entity.get("unique_id") for entity in device_info["entities"]}
            for new_entity in new_entities:
                if new_entity["unique_id"] not in existing_unique_ids:
                    device_info["entities"].append(new_entity)
                    _LOGGER.info("📊 Added %s sensor entity for upgraded transceiver %s", 
                               new_entity["sensor_type"], serial_number[-8:])
            
            # Fire sensor added event to trigger entity creation
            self.hass.bus.async_fire(
                EVENT_SENSOR_ADDED,
                {
                    "serial_number": serial_number,
                    "device_info": device_info,
                    "entities": new_entities,
                    "source": "auto_upgrade"
                }
            )
            
        except Exception as e:
            _LOGGER.error("❌ Error adding sensor entities for transceiver %s: %s", serial_number[-8:], e)

    async def _fire_battery_event(self, serial_number: str, telegram_data: Dict[str, Any], event_type: str) -> None:
        """Fire battery-related events (battery_low, battery_ok).
        
        Battery events are transmitter-wide and independent of button actions.
        
        Args:
            serial_number: Device serial number
            telegram_data: Telegram data
            event_type: Either "battery_low" or "battery_ok"
        """
        try:
            device_name = self.devices.get(serial_number, {}).get("name", "Unknown")
            
            # Prepare transmitter-wide battery event data
            event_data = {
                "device_id": serial_number,
                "serial_number": serial_number,
                "device_name": device_name,
                "event_type": event_type,
                "is_low_battery": event_type == "battery_low",
                "battery_status": "low" if event_type == "battery_low" else "normal"
            }
            
            # Fire appropriate event
            if event_type == "battery_low":
                self.hass.bus.async_fire("easywave_battery_low", event_data)
                _LOGGER.warning("🪫 Battery LOW: %s", serial_number[-8:])
                
            elif event_type == "battery_ok":
                self.hass.bus.async_fire("easywave_battery_ok", event_data)
                _LOGGER.info("🔋 Battery OK event: %s (transmitter-wide)", serial_number[-8:])
            
        except Exception as e:
            _LOGGER.error("Error firing battery event for %s: %s", serial_number[-8:], e)

    async def _fire_button_events(self, serial_number: str, telegram_data: Dict[str, Any]) -> None:
        """Fire button events for Easywave Transmitter devices (press/release only).
        
        Supported actions:
        - press (function=1): Button pressed
        - release (function=0): Button released
        
        Note: Battery events (battery_low, battery_ok) are handled separately.
        """
        try:
            button = telegram_data.get("button")
            function = telegram_data.get("function")  # 0=release, 1=press
            is_press = telegram_data.get("is_press", False)
            is_release = telegram_data.get("is_release", False)
            
            # Determine action from function or is_press/is_release flags
            if is_press:
                action = "press"
            elif is_release:
                action = "release"
            elif function == 1:
                action = "press"
            elif function == 0:
                action = "release"
            else:
                _LOGGER.warning("⚠️ Cannot determine action from telegram (function=%s, is_press=%s, is_release=%s)", 
                              function, is_press, is_release)
                return
            
            # Button info is required
            if button is None:
                _LOGGER.warning("Button missing from telegram")
                return
                
            _LOGGER.debug("Button event: device=%s, button=%s, action=%s (function=%s)", 
                          serial_number[-8:], button, action, function)
            
            device_name = self.devices.get(serial_number, {}).get("name", "Unknown")
            button_name = telegram_data.get("button_name", f"button_{button}")
            action_label = self._get_transmitter_action_label(serial_number, button)
            if not action_label:
                action_label = button_name
                
            # Prepare button-specific event data
            action_label_translated = self._translate_action_label(action_label)
            event_data = {
                "device_id": serial_number,
                "serial_number": serial_number,
                "button": button,
                "button_name": button_name,
                "action": action,
                "action_label": action_label,
                "action_label_translated": action_label_translated,
                "device_name": device_name,
                "is_press": action == "press",
                "is_release": action == "release"
            }
            
            # Fire appropriate event
            if action == "press":
                self.hass.bus.async_fire("easywave_button_press", event_data)
                _LOGGER.debug("Button press: %s %s", serial_number[-8:], action_label_translated)
                
            elif action == "release":
                self.hass.bus.async_fire("easywave_button_release", event_data)
                _LOGGER.debug("Button release: %s %s", serial_number[-8:], action_label_translated)
            
        except Exception as e:
            _LOGGER.error("Error firing button events for %s: %s", serial_number[-8:], e)

    async def _fire_specific_entity_events(self, serial_number: str, telegram_data: Dict[str, Any]) -> None:
        """Fire specific events that entities are listening for."""
        try:
            device = self.devices.get(serial_number)
            if not device:
                return
                
            device_type = device.get("type")
            info_type = telegram_data.get("info_type")
            
            # Handle Easywave Transmitter button press/release events
            if device_type == "ew_transmitter" or info_type == 1:
                button = telegram_data.get("button")
                function = telegram_data.get("function")
                is_press = telegram_data.get("is_press", False)
                is_release = telegram_data.get("is_release", False)
                is_low_battery = telegram_data.get("is_low_battery", False)
                button_name = telegram_data.get("button_name", f"Button {button}")
                action_label = self._get_transmitter_action_label(serial_number, button)
                if not action_label:
                    action_label = button_name
                action_label_translated = self._translate_action_label(action_label)
                
                if button is not None:
                    # Determine event type based on new parsing format
                    if is_press:
                        if is_low_battery:
                            event_type = "easywave_button_low_battery"
                        else:
                            event_type = "easywave_button_press"
                    elif is_release:
                        event_type = "easywave_button_release"
                    else:
                        # Fallback to old logic for compatibility
                        if function == 1:  # Press
                            event_type = "easywave_button_press"
                        elif function == 0:  # Release  
                            event_type = "easywave_button_release"
                        else:
                            event_type = "easywave_button_press"  # Default to press
                    
                    # Fire button event
                    event_dict = {
                        "device_id": serial_number,
                        "serial_number": serial_number,
                        "button": button,
                        "button_name": button_name,
                        "action_label": action_label,
                        "action_label_translated": action_label_translated,
                        "raw_button_name": button_name,
                        "function": function,
                        "is_press": is_press,
                        "is_release": is_release,
                        "is_low_battery": is_low_battery,
                        "battery_status": telegram_data.get("battery_status", "good"),
                        "additional_info": telegram_data.get("additional_info"),
                        "raw_data": telegram_data.get("raw_data"),
                        "timestamp": telegram_data.get("timestamp"),
                    }
                    
                    # Only add battery_level if explicitly present
                    if "battery_level" in telegram_data:
                        event_dict["battery_level"] = telegram_data["battery_level"]
                    
                    self.hass.bus.async_fire(event_type, event_dict)
                    
                    # ALSO fire the events that binary_sensor and device_trigger are listening for
                    if is_press:
                        self.hass.bus.async_fire("easywave_button_press", event_dict)
                        _LOGGER.debug("Fired easywave_button_press event for device %s: %s", 
                                    serial_number[-8:], action_label_translated)
                    elif is_release:
                        self.hass.bus.async_fire("easywave_button_release", event_dict)
                        _LOGGER.debug("Fired easywave_button_release event for device %s: %s", 
                                    serial_number[-8:], action_label_translated)
                    
                    _LOGGER.debug("Fired %s event for device %s: %s", 
                                event_type, serial_number[-8:], action_label_translated)
            
            # Handle EWneo-Sensoren measurement events (both EW and EWneo-Sensoren)
            elif device_type in ["ew_sensor", "ewneo_sensor"] or info_type == 2:
                # Extract sensor measurements
                temperature = telegram_data.get("temperature")
                humidity = telegram_data.get("humidity")
                battery_level = telegram_data.get("battery_level")
                battery_status = telegram_data.get("battery_status", "unknown")
                
                # Fire individual sensor events for each measurement type
                if temperature is not None:
                    temp_event_data = {
                        "serial_number": serial_number,
                        "device_id": serial_number,
                        "measurement_type": "temperature",
                        "value": temperature,
                        "timestamp": telegram_data.get("timestamp"),
                        "battery_level": battery_level,
                        "battery_status": battery_status,
                        "raw_data": telegram_data.get("raw_data"),
                    }
                    self.hass.bus.async_fire("easywave_sensor_update", temp_event_data)
                    _LOGGER.debug("Temperature event for %s: %.1f°C", 
                               serial_number, temperature)
                
                if humidity is not None:
                    hum_event_data = {
                        "serial_number": serial_number,
                        "device_id": serial_number,
                        "measurement_type": "humidity",
                        "value": humidity,
                        "timestamp": telegram_data.get("timestamp"),
                        "battery_level": battery_level,
                        "battery_status": battery_status,
                        "raw_data": telegram_data.get("raw_data"),
                    }
                    self.hass.bus.async_fire("easywave_sensor_update", hum_event_data)
                    _LOGGER.debug("Humidity event for %s: %.1f%%", 
                               serial_number, humidity)
                
                # Also fire a combined sensor update event (legacy support)
                combined_event_data = {
                    "serial_number": serial_number,
                    "device_id": serial_number,
                    "timestamp": telegram_data.get("timestamp"),
                    "battery_level": battery_level,
                    "battery_status": battery_status,
                    "raw_data": telegram_data.get("raw_data"),
                    "temperature": temperature,
                    "humidity": humidity,
                }
                self.hass.bus.async_fire("easywave_sensor_update", combined_event_data)
                
                _LOGGER.debug("Fired combined sensor_update event for device %s with temp=%s, hum=%s",
                            serial_number, temperature, humidity)
                            
        except Exception as e:
            _LOGGER.error("Error firing specific entity events: %s", e)

    def _is_ewneo_device_telegram(self, serial_number: str) -> bool:
        """Check if a telegram is from a registered EWneo device using stored gateway+receiver pairs."""
        try:
            # Check if this serial number matches any registered EWneo receiver
            for device_serial, device_info in self._registered_devices.items():
                if device_info.get("neo_device", False):
                    # For EWneo devices, check both gateway and receiver serials
                    gateway_serial = device_info.get("gateway_serial")
                    receiver_serial = device_info.get("serial_number", device_serial)
                    
                    # Telegram could be from either gateway or receiver
                    if serial_number == gateway_serial or serial_number == receiver_serial:
                        _LOGGER.debug("📡 EWneo telegram from %s (device: %s, gateway: %s)", 
                                    serial_number[-8:], device_serial[-8:], 
                                    gateway_serial[-8:] if gateway_serial else "None")
                        return True
                        
            return False
            
        except Exception as e:
            _LOGGER.error("Error checking EWneo device telegram: %s", e)
            return False

    async def _process_ewneo_state_update(self, serial_number: str, telegram_data: Dict[str, Any]) -> None:
        """Process state updates for EWneo devices and notify entities."""
        try:
            _LOGGER.debug("🔍 Processing EWneo state update for %s with telegram: %s", 
                         serial_number[-8:], telegram_data)
            
            # Find the EWneo device that matches this telegram
            target_device_info = None
            target_device_serial = None
            
            for device_serial, device_info in self._registered_devices.items():
                if device_info.get("neo_device", False):
                    gateway_serial = device_info.get("gateway_serial")
                    receiver_serial = device_info.get("serial_number", device_serial)
                    
                    # Check if telegram is from this EWneo device
                    if serial_number == gateway_serial or serial_number == receiver_serial:
                        target_device_info = device_info
                        target_device_serial = device_serial
                        break
                        
            if not target_device_info:
                _LOGGER.debug("No matching EWneo device found for telegram from %s", serial_number[-8:])
                return
                
            _LOGGER.debug("Processing EWneo state update for device %s (telegram from %s)", 
                          target_device_serial[-8:], serial_number[-8:])
            
            # Extract state bytes - can be directly in telegram_data or in raw_data
            state_bytes = []
            
            # First try: Direct state_bytes from telegram_data (new format)
            if "state_bytes" in telegram_data:
                state_bytes = telegram_data["state_bytes"]
                _LOGGER.debug("🔍 EWneo device %s: Using direct state_bytes: %s", 
                            serial_number[-8:], [f"0x{b:02X}" for b in state_bytes])
            else:
                # Fallback: Extract from raw_data.info_data (old format)
                raw_data = telegram_data.get("raw_data", {})
                info_data_hex = raw_data.get("info_data", "")
                
                if info_data_hex:
                    try:
                        # Convert hex string to bytes and take first 4 bytes (EWB state data)
                        info_data_bytes = bytes.fromhex(info_data_hex)
                        state_bytes = list(info_data_bytes[:4])  # EWB state is 4 bytes
                        _LOGGER.debug("🔍 EWneo device %s: Extracted state_bytes: %s from info_data: %s", 
                                    serial_number[-8:], [f"0x{b:02X}" for b in state_bytes], info_data_hex)
                    except ValueError as e:
                        _LOGGER.warning("⚠️ Could not parse info_data hex for EWneo device %s: %s (hex: %s)", 
                                      serial_number[-8:], e, info_data_hex)
                        return
                else:
                    _LOGGER.warning("⚠️ No state_bytes or info_data found in telegram for EWneo device %s", serial_number[-8:])
                    return
            
            device_type_code = target_device_info.get("device_type_code", 0)
            device_type_name = target_device_info.get("device_type_name", "ewneo_switch")
            
            _LOGGER.debug("🔍 EWneo device %s: device_type_code=%s, device_type_name=%s", 
                         target_device_serial[-8:], device_type_code, device_type_name)
            
            if state_bytes:
                # Extract query_mode from telegram if available
                query_mode = telegram_data.get("mode")
                
                # Parse the state using our existing parser
                parsed_state = self._parse_ewneo_state(device_type_code, state_bytes, device_type_name, target_device_serial, mode=query_mode)
                
                if parsed_state:
                    _LOGGER.debug("EWneo device %s: Parsed state update: %s", 
                               target_device_serial[-8:], parsed_state)
                    
                    # Fire an event that EWneo entities can listen to using the device serial (not telegram serial)
                    self.hass.bus.async_fire(
                        "easywave_ewneo_state_update",
                        {
                            "serial_number": target_device_serial,  # Use device serial, not telegram serial
                            "device_id": target_device_serial,
                            "parsed_state": parsed_state,
                            "state_bytes": state_bytes,
                            "telegram_data": telegram_data,
                            "telegram_from": serial_number,  # Track which serial sent the telegram
                            "query_mode": query_mode,  # Include query mode for channel filtering
                            "timestamp": telegram_data.get("timestamp"),
                        }
                    )
                    
                    _LOGGER.debug("Fired EWneo state update event for device %s (from %s, mode=%s)", 
                                target_device_serial[-8:], serial_number[-8:], query_mode)
                    
                    # Check for pending runtime measurement queries on multi-channel motors
                    await self._check_and_execute_pending_runtime_queries(target_device_serial, target_device_info)
                else:
                    _LOGGER.warning("⚠️ Could not parse EWneo state for device %s (state_bytes: %s)", 
                                  target_device_serial[-8:], [f"0x{b:02X}" for b in state_bytes])
            else:
                _LOGGER.debug("No state_bytes extracted from telegram for EWneo device %s", target_device_serial[-8:])
                
        except Exception as e:
            _LOGGER.error("Error processing EWneo state update for %s: %s", serial_number[-8:], e)

    async def _check_and_execute_pending_runtime_queries(
        self, 
        device_serial: str, 
        device_info: Dict[str, Any]
    ) -> None:
        """Check for and execute pending runtime measurement queries for multi-channel motors.
        
        When a motor channel reports status 117 (runtime measurement in progress),
        we need to query the detailed state using Mode 2/10/18/26 to get the full
        runtime measurement information.
        
        Args:
            device_serial: Device serial number
            device_info: Device information dictionary
        """
        try:
            # Only for multi-channel motors (dual/quad)
            device_type = device_info.get("device_type_name", "")
            num_channels = device_info.get("num_channels", 1)
            
            if "dual" not in device_type.lower() and "quad" not in device_type.lower():
                return
            if num_channels < 2:
                return
                
            # Get the device instance to check for pending queries
            device_instance = self.get_device_instance(device_serial)
            if not device_instance:
                return
                
            # Check if device has the runtime query methods (RX11EWneoMotor)
            if not hasattr(device_instance, 'has_pending_runtime_queries'):
                return
                
            if not device_instance.has_pending_runtime_queries():
                return
                
            # Get pending channels and their query modes
            pending_channels = device_instance.get_pending_runtime_queries()
            gateway_serial = device_info.get("gateway_serial")
            
            if not gateway_serial:
                _LOGGER.warning("⚠️ No gateway serial for device %s, cannot query runtime", device_serial[-8:])
                return
            
            _LOGGER.info(
                "🔧 EWneo motor %s: Executing pending runtime queries for channels %s",
                device_serial[-8:], pending_channels
            )
            
            # Execute queries for each pending channel
            for channel in pending_channels:
                query_mode = device_instance.get_query_mode_for_channel(channel)
                
                _LOGGER.info(
                    "📡 Querying runtime measurement for %s CH%d (Mode %d)",
                    device_serial[-8:], channel, query_mode
                )
                
                # Query the detailed state
                success = await self._query_ewneo_state_with_mode(
                    gateway_serial, device_serial, mode=query_mode
                )
                
                if success:
                    # Clear the pending flag after successful query
                    device_instance.clear_pending_runtime_query(channel)
                    _LOGGER.info(
                        "✅ Runtime query successful for %s CH%d",
                        device_serial[-8:], channel
                    )
                else:
                    _LOGGER.warning(
                        "⚠️ Runtime query failed for %s CH%d, will retry on next telegram",
                        device_serial[-8:], channel
                    )
                    
                # Small delay between queries to avoid overwhelming the transceiver
                await asyncio.sleep(0.1)
                
        except Exception as e:
            _LOGGER.error("Error executing pending runtime queries for %s: %s", device_serial[-8:], e)

    # Auto-discovery methods removed - devices must be manually added

    async def register_device(self, serial_number: str, device_info: Dict[str, Any]) -> None:
        """Register a new device.
        
        Updates all three tracking systems in the correct order:
        1. _registered_devices (authoritative persistent source)
        2. self.devices (in-memory legacy reference, kept in sync)
        3. DeviceManager (secondary persistent store for whitelist/availability)
        """
        # Store in _registered_devices (authoritative source)
        self._registered_devices[serial_number] = device_info
        
        # Keep legacy dict in sync
        self.devices[serial_number] = device_info
        self._known_devices.add(serial_number)
        
        # Sync to DeviceManager
        self.device_manager.add_device(
            serial_number=serial_number,
            device_type=device_info.get("device_type", device_info.get("type", "unknown")),
            name=device_info.get("name", f"Device {serial_number}"),
            rx11_index=device_info.get("rx11_index"),
            area=device_info.get("area")
        )
        # Save both persistent stores
        if self._setup_complete:
            asyncio.create_task(self.device_manager.save())
            asyncio.create_task(self._save_registered_devices())
        
        # Only register with transceiver if it's connected
        if self.transceiver and self.transceiver.is_connected:
            await self.transceiver.register_device(serial_number, device_info)
        else:
            _LOGGER.warning("Device %s registered but transceiver not connected - device will be unavailable", serial_number)
        
        # Save configuration
        await self._save_device_configuration()
        
        # Only fire events if setup is complete and platforms are loaded
        # Otherwise, events will be fired by fire_pending_device_events()
        if not self._setup_complete:
            _LOGGER.debug("Setup not complete, device %s will be fired later", serial_number[-8:])
            return
        
        # Check if event was already fired for this device (prevent duplicates)
        if serial_number in self._devices_with_fired_events:
            _LOGGER.debug("Event already fired for %s, skipping duplicate", serial_number[-8:])
            return
        
        # Fire device added event with slight delay to ensure Home Assistant is ready
        async def fire_device_added_event():
            await asyncio.sleep(0.1)  # Small delay to ensure readiness
            
            # Import here to avoid circular import
            from .entity_specs import create_entity_specs_for_device
            
            # Create entity specifications for this device
            entity_specs = create_entity_specs_for_device(serial_number, device_info)
            
            # Flatten entity specs into a single list for the event
            all_entities = []
            for platform, entities in entity_specs.items():
                all_entities.extend(entities)
            
            # Store entities in device_info if not already there
            if "entities" not in device_info or not device_info["entities"]:
                device_info["entities"] = all_entities
                _LOGGER.debug("Stored %d entities in device_info for %s", len(all_entities), serial_number[-8:])
            
            # Get platforms from entity specs
            platforms = {platform for platform, entities in entity_specs.items() if entities}
            
            _LOGGER.info("🔥 [register_device] Firing EVENT_DEVICE_ADDED for %s with %d entities", 
                        serial_number[-8:], len(all_entities))
            
            self.hass.bus.async_fire(
                EVENT_DEVICE_ADDED,
                {
                    "serial_number": serial_number,
                    "device_info": device_info,
                    "device_type": device_info.get("type"),
                    "device_name": device_info.get("name"),
                    "entities": all_entities,  # Add flattened entities to event data
                    "platforms": platforms,  # Add detected platforms
                }
            )
            
            # Mark event as fired to prevent duplicates
            self._devices_with_fired_events.add(serial_number)
            
            _LOGGER.info("🚀 [register_device] Device added event fired for %s (platforms: %s, entities: %d)", 
                        serial_number[-8:], platforms, len(all_entities))
            
            # For heating/cooling receivers, also fire a switch-specific event
            receiver_kind = device_info.get("receiver_kind")
            if (device_info.get("type") == "ew_receiver" and 
                receiver_kind in ["heating", "cooling", "heating_cooling"]):
                
                # Create a switch entity spec for the heating/cooling switch
                from .entity_specs import create_entity_specs_for_device
                entity_specs = create_entity_specs_for_device(serial_number, device_info)
                switch_entities = entity_specs.get("switch", [])
                
                if switch_entities:
                    # Fire switch-specific event
                    self.hass.bus.async_fire(
                        f"{EVENT_DEVICE_ADDED}_switch",
                        {
                            "serial_number": serial_number,
                            "device_info": device_info,
                            "entities": switch_entities,
                            "platforms": {"switch"},
                            "force_create": True,
                            "heating_cooling_device": True
                        }
                    )
                    _LOGGER.info("🌡️ Fired switch-specific event for heating/cooling device %s", serial_number)
                else:
                    _LOGGER.warning("⚠️ No switch entities generated for heating/cooling device %s", serial_number)
            
        # Schedule the event firing
        self.hass.async_create_task(fire_device_added_event())
        
        _LOGGER.info("Device registered: %s (%s)", 
                    device_info.get("name"), serial_number)



    def _is_ewneo_device(self, device_type: str, device_info: Dict[str, Any]) -> bool:
        """Determine if device is an EWneo device with multiple fallback checks.
        
        This uses multiple indicators to detect EWneo devices:
        1. Device type starts with "ewneo_"
        2. neo_device flag is True
        3. Has ewneo_index field
        
        Args:
            device_type: The device type string
            device_info: Device information dictionary
            
        Returns:
            True if device is identified as an EWneo device
        """
        # Primary check: type name
        if device_type and device_type.startswith("ewneo_"):
            return True
        
        # Secondary check: neo_device flag
        if device_info.get("neo_device", False):
            return True
        
        # Fallback checks: presence of EWneo-specific fields
        if device_info.get("ewneo_index") is not None:
            return True
        
        return False
    async def _cleanup_rx11_device(self, serial_number: str, device_info: Dict[str, Any]) -> None:
        """Clean up RX11-based device resources - delegates to transceiver."""
        if hasattr(self.transceiver, 'cleanup_rx11_device'):
            await self.transceiver.cleanup_rx11_device(
                serial_number=serial_number,
                device_info=device_info,
                device_registry=self.device_manager,
                index_free_callback=self.mark_ew_receiver_index_free
            )
        else:
            _LOGGER.warning("Transceiver does not support cleanup_rx11_device, using legacy cleanup")
            # Fallback to basic cleanup
            try:
                ew_receiver_index = device_info.get("ew_receiver_index") or device_info.get("rx11_index")
                if ew_receiver_index is not None:
                    self.mark_ew_receiver_index_free(ew_receiver_index)
                    _LOGGER.info("♻️ Freed Easywave Receiver index %d", ew_receiver_index)
            except Exception as e:
                _LOGGER.error("Error in legacy RX11 cleanup: %s", e)

    async def _cleanup_ewneo_device(self, serial_number: str, device_info: Dict[str, Any]) -> None:
        """Clean up EWneo-based device resources including EWB index mappings.
        
        This method ensures that EWB_REMOVE_DEVICE is sent to the gateway when removing EWneo receivers/transceivers.
        Note: EWneo Sensors do NOT require EWB_REMOVE_DEVICE - they are only senders, not registered in the gateway.
        """
        try:
            # Clean up EWB index mapping if this was an EWneo device
            ewneo_index = device_info.get("ewneo_index")
            gateway_serial = device_info.get("gateway_serial")
            receiver_serial = serial_number  # The device's own serial is the receiver serial
            device_name = device_info.get("name", serial_number[-8:])
            device_type = device_info.get("type", "unknown")
            
            _LOGGER.info("🔍 EWneo cleanup starting - Device: %s (%s), Type: %s, EWB Index: %s, Gateway: %s",
                        device_name, receiver_serial[-8:], device_type, ewneo_index,
                        gateway_serial[-8:] if gateway_serial else "N/A")
            
            # Step 1: Determine if EWB_REMOVE_DEVICE should be sent
            # EWB_REMOVE_DEVICE is ONLY for EWneo receivers/transceivers (devices registered in the gateway)
            # EWneo Sensors are transmit-only devices and are NOT registered in the gateway
            is_ewneo_sensor = device_type in ("ewneo_sensor", "ew_sensor")
            
            ewb_remove_success = False
            if is_ewneo_sensor:
                _LOGGER.info("ℹ️ Skipping EWB_REMOVE_DEVICE for %s - EWneo Sensors are not registered in gateway", 
                            device_name)
                ewb_remove_success = True  # Not needed, so treat as "success"
            elif gateway_serial and receiver_serial:
                # Only send EWB_REMOVE_DEVICE for EWneo receivers/transceivers
                ewb_remove_success = await self._send_ewb_remove_device(
                    gateway_serial, receiver_serial, device_name, device_type
                )
            else:
                _LOGGER.error("❌ CRITICAL: Cannot send EWB_REMOVE_DEVICE for %s - Missing critical fields:",
                            device_name)
                if not gateway_serial:
                    _LOGGER.error("   - gateway_serial is MISSING (required for EWB protocol)")
                if not receiver_serial:
                    _LOGGER.error("   - receiver_serial is MISSING (should never happen)")
                _LOGGER.error("   Device will be removed locally but may remain registered in EWneo receiver!")
                    
            # Step 2: Free up EWB index for reuse
            if ewneo_index is not None:
                self.mark_ewb_index_free(ewneo_index)
                _LOGGER.info("♻️ Freed EWB index %d for reuse (Device: %s)", ewneo_index, device_name)
            elif not is_ewneo_sensor:
                # Only warn for non-sensor EWneo devices - sensors don't use EWB indices
                _LOGGER.warning("⚠️ No EWB index to free for device %s", device_name)
            # For sensors, no warning needed - they don't have EWB indices
            
            # Summary
            if ewb_remove_success:
                _LOGGER.info("✅ COMPLETE: EWneo device '%s' successfully removed (EWB_REMOVE_DEVICE sent + local cleanup)", 
                            device_name)
            else:
                _LOGGER.warning("⚠️ PARTIAL: EWneo device '%s' removed locally but EWB_REMOVE_DEVICE may not have been sent", 
                              device_name)
            
        except Exception as e:
            _LOGGER.error("❌ Error cleaning up EWneo device %s: %s", serial_number, e, exc_info=True)
    
    async def _send_ewb_remove_device(self, gateway_serial: str, receiver_serial: str, 
                                     device_name: str, device_type: str, max_retries: int = 3) -> bool:
        """Send EWB_REMOVE_DEVICE command to gateway with retry logic.
        
        Args:
            gateway_serial: Gateway/Transceiver serial number (hex string)
            receiver_serial: EWneo receiver serial number (hex string)  
            device_name: Human-readable device name for logging
            device_type: Device type string for logging
            max_retries: Maximum number of retry attempts
            
        Returns:
            True if EWB_REMOVE_DEVICE was sent successfully, False otherwise
        """
        _LOGGER.info("📤 Sending EWB_REMOVE_DEVICE for %s (Type: %s) to Gateway %s...",
                    device_name, device_type, gateway_serial[-8:])
        
        for attempt in range(1, max_retries + 1):
            try:
                success = await self.transceiver.rx11_ewb_remove_device(gateway_serial, receiver_serial)
                
                if success:
                    _LOGGER.info("✅ EWB_REMOVE_DEVICE successfully sent (Attempt %d/%d) - Device: %s",
                               attempt, max_retries, device_name)
                    return True
                else:
                    _LOGGER.warning("⚠️ EWB_REMOVE_DEVICE failed on attempt %d/%d for %s - Retrying...",
                                  attempt, max_retries, device_name)
                    
                    # Wait before retry (except on last attempt)
                    if attempt < max_retries:
                        await asyncio.sleep(0.5)  # 500ms delay between retries
                        
            except Exception as e:
                _LOGGER.warning("⚠️ EWB_REMOVE_DEVICE exception on attempt %d/%d for %s: %s",
                              attempt, max_retries, device_name, e)
                
                # Wait before retry (except on last attempt)
                if attempt < max_retries:
                    await asyncio.sleep(0.5)  # 500ms delay between retries
        
        # All retries exhausted
        _LOGGER.error("❌ EWB_REMOVE_DEVICE failed after %d attempts for %s (Gateway: %s, Receiver: %s)",
                    max_retries, device_name, gateway_serial[-8:], receiver_serial[-8:])
        return False

    async def _save_device_configuration(self) -> None:
        """Save device configuration to persistent storage."""
        try:
            await self.device_config_manager.save_device_config(self.devices)
        except Exception as e:
            _LOGGER.error("Error saving device configuration: %s", e)

    async def _load_device_configuration(self, fire_events: bool = True) -> None:
        """Load device configuration and register devices with transceiver.
        
        IMPORTANT: registered_devices.json (via _registered_devices) is the SINGLE SOURCE OF TRUTH.
        DeviceManager is only used to discover which devices need transceiver registration.
        Device data always comes from _registered_devices first, falling back to DeviceManager only
        for devices not yet in _registered_devices.
        """
        try:
            # Load devices from DeviceManager to know which devices need transceiver setup
            await self.device_manager.load()
            managed_devices = self.device_manager.get_all_devices()
            
            if not managed_devices and not self._registered_devices:
                _LOGGER.info("📝 No devices to restore (DeviceManager and _registered_devices both empty)")
                return
            
            # ONLY iterate _registered_devices — the SINGLE SOURCE OF TRUTH.
            # Devices in managed_devices but NOT in registered_devices are orphans
            # from previous learning sessions and must NOT be restored (they lack
            # config fields like grouping_mode, causing wrong entity regeneration).
            all_serials = set(self._registered_devices.keys())
            
            # Log orphaned managed_devices entries
            orphaned_in_dm = set(managed_devices.keys()) - all_serials
            if orphaned_in_dm:
                _LOGGER.warning("⚠️ %d devices in DeviceManager but NOT in registered_devices (orphaned, skipping): %s",
                              len(orphaned_in_dm), [s[-8:] for s in orphaned_in_dm])
            
            if all_serials:
                _LOGGER.info("🔐 Loading device configuration: %d registered devices (%d in DeviceManager, %d orphaned)",
                           len(all_serials), len(managed_devices), len(orphaned_in_dm))
            
            # Attempt to restore each device
            loaded_count = 0
            failed_devices = []
            
            for serial_number in all_serials:
                try:
                    # PRIMARY SOURCE: _registered_devices (loaded from registered_devices.json)
                    # This preserves ALL config fields: operating_type, grouping_mode, switch_mode, etc.
                    device_info = self._registered_devices.get(serial_number)
                    
                    if not device_info:
                        _LOGGER.error("❌ Device %s in registered_devices keys but no data — skipping", serial_number[-8:])
                        continue
                    
                    _LOGGER.debug("✅ Using authoritative data from registered_devices.json for %s", serial_number[-8:])
                
                    _LOGGER.debug("Restoring device %s (type=%s)", 
                               serial_number, device_info.get("device_type", "unknown"))
                    
                    # Enhanced RX11-based device restoration
                    await self._restore_rx11_device(serial_number, device_info)
                    
                    # Log warning if device type is unknown (device types are immutable — user must delete and re-learn)
                    original_type = device_info.get("device_type", "unknown")
                    if original_type == "unknown":
                        _LOGGER.warning("⚠️ Device %s has unknown type. Delete and re-learn to fix.", serial_number[-8:])
                    
                    # Ensure both type fields are set consistently
                    device_info["type"] = device_info.get("device_type", "unknown")
                    
                    # Only regenerate entity specs if device has NO entities stored
                    # registered_devices.json is the SINGLE SOURCE OF TRUTH — never overwrite existing entities
                    if not device_info.get("entities"):
                        _LOGGER.info("🔄 Device %s has no stored entities, regenerating specs", serial_number[-8:])
                        device_info = await self._regenerate_entity_specs(serial_number, device_info)
                    
                    self.devices[serial_number] = device_info
                    self._known_devices.add(serial_number)
                    
                    # Mark device as available in DeviceManager (if it has a DeviceManager entry)
                    managed_device = managed_devices.get(serial_number)
                    if managed_device:
                        managed_device.mark_available()
                    loaded_count += 1
                    
                except Exception as restore_error:
                    _LOGGER.error("❌ Failed to restore device %s: %s - will remove from DeviceManager", 
                                serial_number[-8:], restore_error)
                    failed_devices.append(serial_number)
                    continue
                
                # Mark as already fired since entities will be created by platform setup
                # This prevents fire_pending_device_events from firing duplicate events
                self._devices_with_fired_events.add(serial_number)
                
                # Register with transceiver
                await self.transceiver.register_device(serial_number, device_info)
                
                # Create and store device instance using device factory
                _LOGGER.debug("Creating device instance for %s (type=%s)", 
                           serial_number, device_info.get("device_type"))
                
                if hasattr(self.transceiver, 'device_factory'):
                    try:
                        # Prepare kwargs for EWneo devices
                        create_kwargs = {}
                        if device_info.get("neo_device"):
                            create_kwargs['gateway_serial'] = device_info.get('gateway_serial')
                            create_kwargs['transceiver'] = self.transceiver
                        
                        device_instance = self.transceiver.device_factory.create_device(
                            serial_number=serial_number,
                            device_info=device_info,
                            telegram_data={},  # No telegram data during restoration
                            **create_kwargs
                        )
                        
                        if device_instance:
                            # Store device instance in transceiver
                            if not hasattr(self.transceiver, '_device_instances'):
                                self.transceiver._device_instances = {}
                            self.transceiver._device_instances[serial_number] = device_instance
            
                            _LOGGER.debug("Created device instance for %s: %s", 
                            serial_number, type(device_instance).__name__)
                        else:
                            _LOGGER.warning("⚠️ Device factory returned None for %s", serial_number)
                    except Exception as e:
                        _LOGGER.error("❌ Error creating device instance for %s: %s", 
                                     serial_number, e, exc_info=True)
                else:
                    _LOGGER.warning("⚠️ Transceiver has no device_factory attribute")
            
            # Log failed devices but do NOT remove them from DeviceManager
            # A transient error (e.g., serial port issue) should not permanently delete device data
            if failed_devices:
                _LOGGER.warning("⚠️ %d devices failed to restore (will retry on next startup): %s", 
                              len(failed_devices), [s[-8:] for s in failed_devices])
            
            # Log summary
            _LOGGER.info("✅ Restored %d/%d devices successfully (%d failed, kept in DeviceManager)", 
                       loaded_count, len(managed_devices), len(failed_devices))
            
            # Recalculate next free indices after all devices are restored
            # This ensures the indices are correct even if devices were restored with indices
            if self._used_ewb_indices:
                self._next_free_ewb_index = self._find_next_free_ewb_index()
                _LOGGER.info("📍 Recalculated next free EWB index after device restore: %d (used: %d indices)", 
                           self._next_free_ewb_index, len(self._used_ewb_indices))
            
            if self._used_ew_receiver_indices:
                self._next_free_ew_receiver_index = self._find_next_free_ew_receiver_index()
                _LOGGER.info("📍 Recalculated next free Easywave Receiver index after device restore: %d (used: %d indices)", 
                           self._next_free_ew_receiver_index, len(self._used_ew_receiver_indices))
            
            # Fire event to signal that all devices are loaded and available
            self.hass.bus.async_fire(
                "easywave_devices_loaded",
                {
                    "device_count": loaded_count,
                    "devices": list(self.devices.keys())
                }
            )
            _LOGGER.info("📡 Fired device loaded event - %d devices available", loaded_count)
            
            # Force entity refresh after loading all devices
            if loaded_count > 0:
                _LOGGER.info("Triggering entity refresh for %d restored devices", loaded_count)
                await self.async_refresh()
                
        except Exception as e:
            _LOGGER.error("Error loading device configuration: %s", e)

    async def _restore_rx11_device(self, serial_number: str, device_info: Dict[str, Any]) -> None:
        """Restore RX11-based device - delegates to transceiver."""
        if hasattr(self.transceiver, 'restore_rx11_device'):
            # Create a minimal whitelist callback that does nothing
            # DeviceManager already handles device registration
            def noop_whitelist_callback(*args, **kwargs):
                pass
            
            await self.transceiver.restore_rx11_device(
                serial_number=serial_number,
                device_info=device_info,
                device_registry=self.device_manager,
                index_used_callback=self.mark_ew_receiver_index_used,
                whitelist_callback=noop_whitelist_callback
            )
            
            # Additional EWneo handling (if needed)
            ewneo_index = device_info.get("ewneo_index")
            gateway_serial = device_info.get("gateway_serial")
            
            if ewneo_index is not None and gateway_serial:
                device_name = device_info.get("name", f"EWneo Device ({serial_number})")
                self.mark_ewb_index_used(ewneo_index, gateway_serial, serial_number, device_name)
                _LOGGER.debug("Restored EWneo EWB index: %d, Gateway: %s, Device: %s", 
                           ewneo_index, gateway_serial[-8:], serial_number[-8:])
        else:
            _LOGGER.warning("Transceiver does not support restore_rx11_device")

    async def _regenerate_entity_specs(self, serial_number: str, device_info: Dict[str, Any]) -> Dict[str, Any]:
        """Regenerate entity specs with latest format and naming.
        
        NON-DESTRUCTIVE: Keeps a backup of old entities and restores on failure.
        """
        try:
            # CRITICAL: Update device_type based on device_type_code for EWneo devices
            device_type_code = device_info.get("device_type_code")
            if device_type_code is not None:
                correct_type = DEVICE_TYPE_CODE_MAP.get(device_type_code)
                if correct_type:
                    old_type = device_info.get("type")
                    if old_type != correct_type:
                        _LOGGER.info("📝 Updating device type for %s: %s → %s (based on type_code 0x%02X)",
                                   serial_number[-8:], old_type, correct_type, device_type_code)
                        device_info["type"] = correct_type
            
            # NON-DESTRUCTIVE: Keep backup of old entities in case regeneration fails
            old_entities = device_info.get("entities", [])
            old_entities_backup = list(old_entities) if old_entities else []
            
            # CRITICAL FIX: If EW Receiver has no receiver_kind, try to recover it from old entities
            device_type = device_info.get("type")
            if device_type == "ew_receiver":
                receiver_kind = device_info.get("receiver_kind")
                
                # If receiver_kind is missing, try to recover it
                if not receiver_kind:
                    if old_entities_backup:
                        # Try to extract receiver_kind from old entities
                        for entity in old_entities_backup:
                            if entity.get("receiver_kind"):
                                device_info["receiver_kind"] = entity["receiver_kind"]
                                receiver_kind = entity["receiver_kind"]
                                _LOGGER.info("🔧 Recovered receiver_kind='%s' for %s from old entities",
                                           receiver_kind, serial_number[-8:])
                                break
                        
                        # If still not found, try to infer from entity types (best effort)
                        if not receiver_kind:
                            _LOGGER.warning("⚠️ Could not recover receiver_kind for EW Receiver %s - will use entity types as fallback",
                                           serial_number[-8:])
                            entity_types = {e.get("type") for e in old_entities_backup}
                            if "cover" in entity_types:
                                device_info["receiver_kind"] = "cover_2button"
                                receiver_kind = "cover_2button"
                            elif "light" in entity_types:
                                device_info["receiver_kind"] = "motor_3button"
                                receiver_kind = "motor_3button"
                            elif "button" in entity_types:
                                device_info["receiver_kind"] = "universal_4button"
                                receiver_kind = "universal_4button"
                            else:
                                # Default fallback (might be switch_2button or heating_cooling - can't tell from type alone)
                                device_info["receiver_kind"] = "switch_2button"
                                receiver_kind = "switch_2button"
                                _LOGGER.warning("  → Defaulted to switch_2button as receiver_kind")
                    else:
                        # No old entities to recover from - this is a critical condition for ew_receiver
                        _LOGGER.warning("⚠️ EW Receiver %s has NO receiver_kind AND no old entities to recover from", 
                                       serial_number[-8:])
                
                # Log if we have receiver_kind by this point
                if receiver_kind:
                    _LOGGER.debug("✓ EW Receiver %s will use receiver_kind='%s' for entity regeneration", 
                                serial_number[-8:], receiver_kind)
            
            # Remove old entities to allow fresh generation
            if "entities" in device_info:
                del device_info["entities"]
                _LOGGER.debug("Temporarily removed %d old entities for %s before regeneration", 
                            len(old_entities_backup), serial_number[-8:])
            
            _LOGGER.debug("About to regenerate entity specs for %s (type=%s, registration_id=%s, receiver_kind=%s)", 
                        serial_number, 
                        device_info.get("type"),
                        device_info.get("registration_id"),
                        device_info.get("receiver_kind"))
                        
            entity_specs = create_entity_specs_for_device(serial_number, device_info, coordinator=self)
            _LOGGER.debug("Entity specs result: %s platforms, entity_specs=%s", 
                         len(entity_specs) if entity_specs else 0, bool(entity_specs))
                         
            if entity_specs:
                # Merge all entity types into a single entities list
                all_entities = []
                for platform, entities in entity_specs.items():
                    all_entities.extend(entities)
                _LOGGER.debug("Merged entities: %d total", len(all_entities))
                platforms = [platform for platform, entities in entity_specs.items() if entities]
                
                if all_entities:
                    device_info["entities"] = all_entities
                    device_info["platforms"] = platforms
                    
                    for entity in all_entities:
                        _LOGGER.debug("  → Entity: %s (unique_id: %s)", 
                                     entity.get("sensor_type") or entity.get("type"), 
                                     entity.get("unique_id", "N/A")[-20:])
                    
                    _LOGGER.debug("Regenerated %d entity specs for device %s with updated naming", 
                               len(all_entities), serial_number)
                else:
                    # Regeneration returned empty list — RESTORE backup
                    _LOGGER.warning("⚠️ Entity specs generation returned empty list for device %s — restoring %d old entities", 
                                  serial_number, len(old_entities_backup))
                    if old_entities_backup:
                        device_info["entities"] = old_entities_backup
            else:
                # Regeneration returned None — RESTORE backup
                _LOGGER.warning("⚠️ Entity specs generation returned None for device %s — restoring %d old entities", 
                              serial_number, len(old_entities_backup))
                if old_entities_backup:
                    device_info["entities"] = old_entities_backup
                
            return device_info
            
        except Exception as e:
            _LOGGER.error("Error regenerating entity specs for %s: %s — keeping existing entities", serial_number, e)
            # SAFETY: Restore backup on ANY exception
            if 'old_entities_backup' in locals() and old_entities_backup:
                device_info["entities"] = old_entities_backup
            return device_info

    async def send_command(self, serial_number: str, command, **kwargs) -> bool:
        """Send command to a device via transceiver."""
        # Check if transceiver is connected first
        if not self.transceiver.is_connected:
            _LOGGER.debug("Cannot send command — RX11 not connected")
            return False
        
        if serial_number not in self.devices and serial_number not in self._registered_devices:
            _LOGGER.error("Device not found: %s", serial_number)
            return False
            
        try:
            # Get device info to determine device type
            device_info = self._registered_devices.get(serial_number) or self.devices.get(serial_number, {})
            device_type = device_info.get("type", "")
            is_neo_device = device_info.get("neo_device", False) or device_type.startswith("ewneo_")
            is_ew_receiver = device_type == "ew_receiver"
            
            # EWneo devices (bidirectional) should NEVER use this generic command method
            # They use dedicated EWB methods: rx11_ewb_change_state, rx11_ewb_query_state
            if is_neo_device:
                _LOGGER.error("❌ Cannot use send_command() for EWneo device %s (type: %s). Use rx11_ewb_change_state() or rx11_ewb_query_state() instead!", 
                            serial_number[-8:], device_type)
                return False
            
            # For Easywave Receiver devices, always use send_command_to_receiver
            if is_ew_receiver:
                # Convert bytes to command format for send_command_to_receiver
                if isinstance(command, bytes):
                    # send_command_to_receiver expects bytes directly
                    command_bytes = command
                else:
                    command_bytes = command
                    
                if hasattr(self.transceiver, 'send_command_to_receiver'):
                    result = await self.transceiver.send_command_to_receiver(serial_number, command_bytes)
                else:
                    _LOGGER.error("Transceiver does not support send_command_to_receiver for EW receiver")
                    return False
            elif isinstance(command, bytes):
                # Non-Easywave Receiver with bytes - use send_command_to_device
                if hasattr(self.transceiver, 'send_command_to_device'):
                    result = await self.transceiver.send_command_to_device(serial_number, command)
                else:
                    _LOGGER.error("Transceiver does not support send_command_to_device for bytes")
                    return False
            else:
                # String command - use send_command_to_receiver
                if hasattr(self.transceiver, 'send_command_to_receiver'):
                    result = await self.transceiver.send_command_to_receiver(serial_number, command)
                else:
                    _LOGGER.error("Transceiver does not support send_command_to_receiver for strings")
                    return False
            
            if result:
                _LOGGER.debug("Command sent successfully to %s: %s", serial_number, command)
            else:
                _LOGGER.warning("Failed to send command to %s: %s", serial_number, command)
                
            return result
            
        except Exception as e:
            _LOGGER.error("Error sending command to %s: %s", serial_number, e)
            return False

    async def set_learning_mode(self, enabled: bool, timeout: int = 180) -> bool:
        """Set learning mode on the transceiver."""
        try:
            result = await self.transceiver.set_learning_mode(enabled, timeout)
            
            if result:
                _LOGGER.info("Learning mode %s", "enabled" if enabled else "disabled")
            else:
                _LOGGER.warning("Failed to %s learning mode", "enable" if enabled else "disable")
                
            return result
            
        except Exception as e:
            _LOGGER.error("Error setting learning mode: %s", e)
            return False

    async def is_learning_mode(self) -> bool:
        """Check if learning mode is active."""
        try:
            return await self.transceiver.is_learning_mode()
        except Exception as e:
            _LOGGER.error("Error checking learning mode: %s", e)
            return False

    def get_all_devices(self) -> Dict[str, Dict[str, Any]]:
        """Get all devices.
        
        _registered_devices is the authoritative source. self.devices is kept
        as a synchronized reference for backward compatibility only.
        Any device in self.devices that is NOT in _registered_devices is
        included as well (for transition safety), but _registered_devices
        always takes priority.
        """
        all_devices = {}
        
        # Start with self.devices (legacy) as a fallback base
        for serial, device_info in self.devices.items():
            all_devices[serial] = device_info.copy()
        
        # Override/extend with _registered_devices (authoritative source)
        for serial, device_info in self._registered_devices.items():
            all_devices[serial] = device_info.copy()
            
        return all_devices

    def get_device(self, serial_number: str) -> Optional[Dict[str, Any]]:
        """Get device info by serial number.
        
        Checks _registered_devices first (authoritative), then falls back to self.devices.
        """
        return self._registered_devices.get(serial_number) or self.devices.get(serial_number)

    def get_device_state(self, serial_number: str) -> Optional[Dict[str, Any]]:
        """Get persistent state for a device."""
        # First try registered devices
        if serial_number in self._registered_devices:
            device = self._registered_devices[serial_number]
            persistent_state = device.get("persistent_state", {})
            if persistent_state:
                return persistent_state
        
        # Fallback to legacy devices
        device = self.devices.get(serial_number)
        if device:
            return device.get("persistent_state", {})
        return None

    def set_device_state(self, serial_number: str, state_data: Dict[str, Any]) -> None:
        """Set persistent state for a device."""
        # Update both legacy devices and registered devices
        if serial_number in self.devices:
            if "persistent_state" not in self.devices[serial_number]:
                self.devices[serial_number]["persistent_state"] = {}
            self.devices[serial_number]["persistent_state"].update(state_data)
        
        # Also update registered devices
        if serial_number in self._registered_devices:
            if "persistent_state" not in self._registered_devices[serial_number]:
                self._registered_devices[serial_number]["persistent_state"] = {}
            self._registered_devices[serial_number]["persistent_state"].update(state_data)
            
            # Trigger save of registered devices to persist the state
            self.hass.async_create_task(self._save_registered_devices())
            
        _LOGGER.debug("Updated persistent state for device %s: %s", 
                     serial_number, state_data)

    async def async_remove_device_manually(self, serial_number: str, force: bool = False) -> bool:
        """Remove device manually with safety checks."""
        if not serial_number or len(serial_number) != 16:
            _LOGGER.error("Invalid serial number for device removal: %s", serial_number)
            return False
            
        device_info = self.devices.get(serial_number)
        if not device_info:
            _LOGGER.warning("Device %s not found for manual removal", serial_number)
            return False
            
        # Check if device was added manually (unless force is used)
        if not force and not device_info.get("added_manually", False):
            _LOGGER.warning("Device %s was auto-discovered. Use force=True to remove anyway.", serial_number)
            return False
            
        return await self._unregister_device_internal(serial_number)

    # Enhanced alias with additional options
    async def async_remove_device(self, serial_number: str, force: bool = False) -> bool:
        """Remove device with full control options and lifecycle management.
        
        This method coordinates the device removal through the lifecycle manager,
        ensuring proper cleanup at each stage:
        1. Lifecycle state transition to DELETING
        2. Full cleanup of HA registries (entities + device)
        3. Device-specific cleanup (RX11, EWneo, etc.)
        4. Whitelist removal
        5. Persistent storage update
        
        Args:
            serial_number: Device serial number
            force: Force removal even if device is not found
            
        Returns:
            True if removal was successful, False otherwise
        """
        try:
            # Mark device state as DELETING in lifecycle manager
            device_state = self.device_lifecycle_manager.get_device_state(serial_number)
            if device_state:
                _LOGGER.info(
                    "🔄 Device %s in lifecycle state %s, starting removal...",
                    serial_number[-8:],
                    device_state.state.value
                )
            
            # Perform comprehensive removal through _unregister_device_internal
            # This handles entity registry, device registry, and device-specific cleanup
            unregister_result = await self._unregister_device_internal(serial_number)
            
            # Update lifecycle state to DELETED 
            if unregister_result:
                await self.device_lifecycle_manager.complete_device_removal(serial_number)
                _LOGGER.info("✅ Device %s removal completed successfully", serial_number[-8:])
                return True
            else:
                _LOGGER.warning("⚠️ Device %s removal incomplete, but marked as deleted", serial_number[-8:])
                # Mark as deleted anyway for cleanup
                await self.device_lifecycle_manager.complete_device_removal(serial_number)
                return unregister_result
                
        except Exception as e:
            _LOGGER.error("❌ Error in async_remove_device: %s", e)
            if force:
                # If force is true, mark as deleted despite error
                await self.device_lifecycle_manager.complete_device_removal(serial_number)
            return False

    async def _remove_from_ha_registry(self, serial_number: str, device_info: Dict[str, Any]) -> bool:
        """Remove device from Home Assistant device and entity registries."""
        try:
            import homeassistant.helpers.device_registry as dr
            import homeassistant.helpers.entity_registry as er
            
            entity_registry = er.async_get(self.hass)
            device_registry = dr.async_get(self.hass)
            
            # Get device entry before removing (supports both UUID and serial identifiers)
            device_entry = self._find_ha_device_entry(serial_number)
            
            # Find all entities for this device BEFORE removing the device
            entities_to_remove = []
            
            # Method 1: Find by device_id if device exists
            if device_entry:
                _LOGGER.debug("  Found device entry with ID: %s", device_entry.id)
                entities = er.async_entries_for_device(entity_registry, device_entry.id)
                for entity_entry in entities:
                    entities_to_remove.append(entity_entry.entity_id)
                    _LOGGER.debug("    Found entity by device_id: %s (unique_id: %s)", 
                                entity_entry.entity_id, entity_entry.unique_id)
                        
            # Method 2: Find by unique_id pattern (fallback and additional cleanup)
            # This catches entities that might have been orphaned
            # Match by registration_id (UUID) or legacy serial_number prefix
            # Try device_info param first, then _registered_devices lookup
            reg_id = device_info.get('registration_id') if device_info else None
            if not reg_id:
                stored = self._registered_devices.get(serial_number, {})
                reg_id = stored.get('registration_id')
            ha_identifier = reg_id.lower() if reg_id else None
            for entity_entry in list(entity_registry.entities.values()):
                if entity_entry.platform == DOMAIN and entity_entry.unique_id:
                    uid_lower = entity_entry.unique_id.lower()
                    if (ha_identifier and uid_lower.startswith(ha_identifier)) or uid_lower.startswith(serial_number.lower()):
                        if entity_entry.entity_id not in entities_to_remove:
                            entities_to_remove.append(entity_entry.entity_id)
                            _LOGGER.debug("    Found entity by unique_id pattern: %s", entity_entry.entity_id)
            
            # Remove entities first
            _LOGGER.info("🗑️  Removing %d entities for device %s", len(entities_to_remove), serial_number[-8:])
            
            # Purge history for these entities BEFORE removing them
            if entities_to_remove:
                await self._purge_entity_history(entities_to_remove)
            
            for entity_id in entities_to_remove:
                try:
                    # Get unique_id BEFORE removing the entity
                    unique_id = None
                    if entity_id in entity_registry.entities:
                        entity_entry = entity_registry.entities[entity_id]
                        if entity_entry:
                            unique_id = entity_entry.unique_id
                    
                    _LOGGER.debug("  Removing entity: %s (unique_id: %s)", entity_id, unique_id)
                    entity_registry.async_remove(entity_id)
                    
                    # Remove from our tracking
                    if unique_id:
                        self.state_manager.unmark_entity_deleted(unique_id)
                        _LOGGER.debug("    Removed unique_id from tracking: %s", unique_id)
                except Exception as e:
                    _LOGGER.warning("  Failed to remove entity %s: %s", entity_id, e)
            
            # Small delay to ensure registry updates are processed
            await asyncio.sleep(0.3)
                
            # Remove device after entities are removed
            if device_entry:
                _LOGGER.info("🗑️  Removing device from registry: %s", serial_number[-8:])
                device_registry.async_remove_device(device_entry.id)
                
            _LOGGER.info("✅ Removed %d entities and device %s from Home Assistant registries", 
                        len(entities_to_remove), serial_number[-8:])
            return True
            
        except Exception as e:
            _LOGGER.error("Error removing device from HA registry: %s", e)
            return False
    
    # Whitelist management is now handled by DeviceManager
    # Removed: _remove_from_whitelist, _add_to_whitelist, _add_to_whitelist_memory, 
    # _save_whitelist_async, _populate_initial_whitelist
    # Use device_manager.add_device() and device_manager.remove_device() instead


    async def list_removable_devices(self) -> Dict[str, Dict[str, Any]]:
        """List all devices that can be safely removed."""
        removable_devices = {}
        
        for serial_number, device_info in self.devices.items():
            # Include devices that were added manually or can be force-removed
            removable_info = {
                "name": device_info.get("name", serial_number),
                "type": device_info.get("type", "unknown"),
                "added_manually": device_info.get("added_manually", False),
                "can_force_remove": True,
                "last_seen": device_info.get("last_seen"),
                "entities_count": len(device_info.get("entities", []))
            }
            removable_devices[serial_number] = removable_info
            
        return removable_devices

    async def async_add_device_manually(
        self,
        serial_number: str,
        device_type: str,
        device_name: str = None,
        channels: int = 1,
        detected_via: str = "manual",
        info_type: int = None,
        timestamp: float = None,
    ) -> bool:
        """Simplified RX11-based device creation with persistent Easywave Receiver serial storage."""
        try:
            # Check if device already exists
            if serial_number in self.devices:
                _LOGGER.warning("Device %s already exists", serial_number)
                return False

            # Use RX11 transceiver's device creation helper (maintains architectural boundaries)
            if hasattr(self.transceiver, 'create_rx11_ew_device_info'):
                device_info = await self.transceiver.create_rx11_ew_device_info(
                    serial_number=serial_number,
                    device_type=device_type,
                    device_name=device_name,
                    channels=channels,
                    detected_via=detected_via,
                    info_type=info_type,
                    timestamp=timestamp,
                    device_registry=self.device_manager,
                    ew_receiver_allocator=self._allocate_ew_receiver_with_persistence
                )
            else:
                _LOGGER.error("Transceiver does not support create_rx11_ew_device_info")
                return False
            
            if not device_info:
                _LOGGER.error("Failed to create device info for %s", serial_number)
                return False

            # Register the device
            await self.register_device(serial_number, device_info)
            
            _LOGGER.info("✅ Simplified RX11 device creation completed: %s (%s)", 
                        device_info.get("name"), device_type)
            
            return True
            
        except Exception as e:
            _LOGGER.error("Error in simplified RX11 device creation: %s", e)
            return False

    # Device creation/removal helper methods are now in device_lifecycle.py
    # and device-specific transceiver classes

    async def _allocate_ew_receiver_with_persistence(self, device_serial: str) -> Dict[str, Any] | None:
        """Allocate Easywave Receiver with persistent serial number storage."""
        try:
            # Get next available receiver with index and serial
            if hasattr(self.transceiver, 'get_next_available_receiver'):
                receiver_info = await self.transceiver.get_next_available_receiver()
                if receiver_info:
                    ew_receiver_index, ew_receiver_serial = receiver_info
                    
                    # Store mapping persistently in device manager
                    # Update the device with the RX11 index
                    device = self.device_manager.get_device(device_serial)
                    if device:
                        self.device_manager.add_device(
                            serial_number=device_serial,
                            device_type=device.device_type,
                            name=device.name,
                            rx11_index=ew_receiver_index
                        )
                        await self.device_manager.save()
                    
                    # Mark receiver as used to prevent double allocation
                    if hasattr(self.transceiver, '_rx11_wrapper'):
                        self.transceiver._rx11_wrapper.mark_receiver_used(ew_receiver_index, ew_receiver_serial)
                    
                    _LOGGER.info("🎯 Allocated and stored Easywave Receiver: Index %d, Serial %s → Device %s", 
                               ew_receiver_index, ew_receiver_serial[-8:], device_serial[-8:])
                    
                    return {
                        "ew_receiver_index": ew_receiver_index,
                        "ew_receiver_serial": ew_receiver_serial,
                        "supports_continuous": True,
                        "rx11_based": True,
                    }
                else:
                    _LOGGER.error("No available Easywave Receiver index found")
                    return None
            else:
                _LOGGER.error("Transceiver does not support Easywave Receiver allocation")
                return None
                
        except Exception as e:
            _LOGGER.error("Error allocating Easywave Receiver with persistence: %s", e)
            return None
            return False

    async def async_restore_device(self, serial_number: str, device_config: Dict[str, Any]) -> bool:
        """Restore a previously removed device from backup configuration."""
        try:
            # Add back to DeviceManager
            self.device_manager.add_device(
                serial_number=serial_number,
                device_type=device_config.get("device_type", "unknown"),
                name=device_config.get("name", f"Device {serial_number}"),
                rx11_index=device_config.get("rx11_index"),
                area=device_config.get("area")
            )
            await self.device_manager.save()
            
            # Device will be added to DeviceManager during registration
            
            # Restore device configuration
            self.devices[serial_number] = device_config.copy()
            
            # Fire device added event
            device_name = device_config.get("name", serial_number[-8:])
            self.hass.bus.async_fire(
                EVENT_DEVICE_ADDED,
                {
                    "serial_number": serial_number,
                    "device_info": device_config,
                    "device_type": device_config.get("type"),
                    "device_name": device_name,
                    "restored": True
                }
            )
            
            # Update data to trigger entity recreation
            await self.async_update_data()
            
            _LOGGER.info("✅ Device %s (%s) successfully restored", device_name, serial_number[-8:])
            return True
            
        except Exception as e:
            _LOGGER.error("Error restoring device %s: %s", serial_number, e)
            return False
    
    async def get_backup_devices(self) -> List[Dict[str, Any]]:
        """Get list of backup/removed devices that can be restored."""
        backup_devices = []
        
        try:
            # Check if we have a backup storage mechanism
            if hasattr(self, '_device_backup'):
                for serial_number, backup_info in self._device_backup.items():
                    backup_devices.append({
                        "serial_number": serial_number,
                        "name": backup_info.get("name", serial_number[-8:]),
                        "type": backup_info.get("type", "unknown"),
                        "device_config": backup_info,
                        "removed_at": backup_info.get("removed_at"),
                        "can_restore": True
                    })
                    
        except Exception as e:
            _LOGGER.error("Error getting backup devices: %s", e)
            
        return backup_devices
    
    async def async_recreate_device(self, serial_number: str) -> bool:
        """Recreate a device by removing and re-adding it."""
        try:
            # Get current device info
            device_info = self.devices.get(serial_number)
            if not device_info:
                _LOGGER.error("Cannot recreate device %s: not found", serial_number)
                return False
            
            device_name = device_info.get("name", serial_number[-8:])
            
            # Backup device configuration
            device_backup = device_info.copy()
            
            # Remove device
            success = await self.async_remove_device(serial_number, force=True)
            if not success:
                _LOGGER.error("Failed to remove device %s for recreation", serial_number)
                return False
            
            # Wait a moment for cleanup
            await asyncio.sleep(0.5)
            
            # Restore device
            success = await self.async_restore_device(serial_number, device_backup)
            if success:
                _LOGGER.info("✅ Device %s (%s) successfully recreated", device_name, serial_number[-8:])
            else:
                _LOGGER.error("Failed to restore device %s after recreation", serial_number)
                
            return success
            
        except Exception as e:
            _LOGGER.error("Error recreating device %s: %s", serial_number, e)
            return False

    @callback
    def async_add_listener(self, update_callback, context=None) -> callable:
        """Add listener for data updates."""
        if context is not None:
            # New Home Assistant version with context support
            return super().async_add_listener(update_callback, context)
        else:
            # Older Home Assistant version without context
            return super().async_add_listener(update_callback)

    # =============================================================================
    # EWB (EASYWAVE BIDI) MONITORING FOR STATE UPDATES
    # =============================================================================
    
    async def _start_ewb_monitoring(self) -> None:
        """Start EWB telegram monitoring for state updates."""
        if not hasattr(self, '_ewb_monitoring_task') or self._ewb_monitoring_task is None:
            self._ewb_monitoring_task = asyncio.create_task(self._ewb_monitoring_loop())
            _LOGGER.info("🚀 Started EWB monitoring task")

    async def _ewb_monitoring_loop(self) -> None:
        """Continuous loop to monitor EWB telegrams for state updates."""
        _LOGGER.info("🔄 Starting EWB monitoring loop")
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while not self._shutdown_event.is_set():
            try:
                if not hasattr(self.transceiver, 'rx11_ewb_receive_telegram'):
                    _LOGGER.debug("Transceiver does not support EWB receive")
                    await asyncio.sleep(5.0)
                    continue
                    
                # Receive EWB telegram
                telegram = await self.transceiver.rx11_ewb_receive_telegram()
                
                if telegram:
                    consecutive_errors = 0  # Reset error counter on success
                    await self._process_ewb_telegram(telegram)
                else:
                    # Short pause before next attempt
                    await asyncio.sleep(0.1)
                    
            except asyncio.CancelledError:
                _LOGGER.info("🛑 EWB monitoring loop cancelled")
                break
                
            except Exception as e:
                consecutive_errors += 1
                _LOGGER.error("❌ Error in EWB monitoring loop (%d/%d): %s", 
                             consecutive_errors, max_consecutive_errors, e)
                
                if consecutive_errors >= max_consecutive_errors:
                    _LOGGER.error("❌ Too many consecutive errors in EWB monitoring - stopping")
                    break
                
                # Exponential backoff on errors
                await asyncio.sleep(min(2.0 ** consecutive_errors, 30.0))
        
        _LOGGER.info("🏁 EWB monitoring loop ended")

    async def _process_ewb_telegram(self, telegram: dict) -> None:
        """Process received EWB telegram for state updates."""
        try:
            info_type = telegram.get('info_type')
            serial_number = telegram.get('serial_number')
            raw_data = telegram.get('raw_info_data', [])
            
            if info_type == 3:  # State update from EWneo receiver
                await self._process_ewneo_raw_state_update(serial_number, raw_data)
            else:
                # Für alle anderen info_types: Konvertiere zu normalem Telegramm-Format 
                # und leite an _handle_telegram weiter für normale Verarbeitung
                _LOGGER.debug("📡 Converting EWB telegram (info_type %d) to normal telegram format", info_type)
                
                # Extrahiere Rohdaten für normale Telegram-Verarbeitung
                if len(serial_number) >= 32:  # 16 bytes = 32 hex chars
                    device_id_bytes = bytes.fromhex(serial_number)
                    info_data_bytes = bytes(raw_data) if raw_data else b'\x00' * 8
                    
                    # Rufe den normalen Telegram-Callback auf
                    if hasattr(self.transceiver, '_telegram_callback') and self.transceiver._telegram_callback:
                        try:
                            self.transceiver._telegram_callback(info_type, device_id_bytes, info_data_bytes)
                        except Exception as cb_error:
                            _LOGGER.error("❌ Error calling telegram callback: %s", cb_error)
                    else:
                        _LOGGER.debug("📡 No telegram callback available for info_type %d", info_type)
                else:
                    _LOGGER.warning("⚠️ Invalid serial number length for EWB telegram: %s", serial_number)
                
        except Exception as e:
            _LOGGER.error("❌ Error processing EWB telegram: %s", e)

    async def _process_ewneo_raw_state_update(self, serial_number: str, raw_data: list) -> None:
        """Process EWneo receiver state update from raw EWB telegram data."""
        try:
            if len(raw_data) < 4:
                _LOGGER.warning("⚠️ Invalid state data length for %s: %d bytes", serial_number[-8:], len(raw_data))
                return
                
            # Extract 4-byte state
            state_bytes = raw_data[:4]
            state_value = int.from_bytes(state_bytes, byteorder='big')  # EWneo uses big-endian
            
            _LOGGER.debug("📊 State update for %s: %s (0x%08X)", 
                         serial_number[-8:], [f'0x{b:02X}' for b in state_bytes], state_value)
            
            # Get device info to determine type from device_manager
            device = self.device_manager.get_device(serial_number)
            if not device:
                _LOGGER.warning("⚠️ Device %s not found in device_manager for state update", serial_number[-8:])
                return
                
            # Get device type from ManagedDevice
            device_type_str = device.device_type if isinstance(device.device_type, str) else str(device.device_type)
            device_type_code = device.extra_data.get('device_type_code', 0) if device.extra_data else 0
            device_type_name = device_type_str
            
            # Process state based on device type
            parsed_state = self._parse_ewneo_state(device_type_code, state_bytes, device_type_name, serial_number)
            
            if parsed_state:
                # Update device state in extra_data
                if device.extra_data is None:
                    device.extra_data = {}
                device.extra_data['last_state_update'] = time.time()
                device.extra_data['current_state'] = parsed_state
                device.extra_data['raw_state'] = list(state_bytes)  # Convert to list for JSON serialization
                device.mark_available()  # Mark device as available on state update
                
                # Fire state update event for entities
                self.hass.bus.async_fire(
                    f"{EVENT_DEVICE_STATE_UPDATE}",
                    {
                        "serial_number": serial_number,
                        "device_type_code": device_type_code,
                        "device_type_name": device_type_name,
                        "state": parsed_state,
                        "raw_state": state_bytes,
                        "timestamp": time.time()
                    }
                )
                
                _LOGGER.debug("State update processed for %s (%s): %s", 
                           serial_number[-8:], device_type_name, parsed_state)
            else:
                _LOGGER.warning("⚠️ Could not parse state for %s (type 0x%02X)", 
                               serial_number[-8:], device_type_code)
                
        except Exception as e:
            _LOGGER.error("❌ Error processing EWneo state update for %s: %s", serial_number[-8:], e)

    def _parse_ewneo_state(self, device_type_code: int, state_bytes: list, device_type_name: str, device_serial: str = None, mode: int = None) -> Optional[dict]:
        """Parse EWneo receiver state based on device type.
        
        Args:
            device_type_code: Device type code (0x05=Single, 0x08=Dual, 0x09=Quad motor, etc.)
            state_bytes: 4-byte state data
            device_type_name: Device type name string
            device_serial: Device serial number (optional, for logging)
            mode: EWB query/response mode (0=all motors, 2/10/18/26=individual motor)
        
        NOTE: EWneo devices send state responses in LITTLE-ENDIAN byte order (confirmed by device implementations),
        but we parse them here as BIG-ENDIAN to match the command format we send.
        This works because we're receiving the raw bytes and the device layer handles the conversion.
        """
        try:
            # EWneo uses BIG-ENDIAN byte order for state parsing (to match command format)
            state_value = int.from_bytes(state_bytes, byteorder='big')
            
            if device_type_code == 0x03:  # EWB_DT_SWITCH (Single Switch)
                # Switch state format (from EWB specification):
                # Bits 31-27: Counter (0-31) incremented on switch-on
                # Bits 26-24: State reason (1=off, 2=on, 5=on due to logic)
                # Bits 23-0:  Reserved
                
                # Extract state reason from bits 26-24
                state_reason = (state_value >> 24) & 0x07
                counter = (state_value >> 27) & 0x1F
                
                # State is ON if reason is 2 (on) or 5 (on due to logic)
                is_on = state_reason in [2, 5]
                
                device_id = device_serial[-8:] if device_serial else "Unknown"
                _LOGGER.debug("🔍 EWneo Switch %s: State=0x%08X, Reason=%d, Counter=%d, IsOn=%s", 
                             device_id, state_value, state_reason, counter, is_on)
                
                return {
                    'type': 'switch',
                    'state': is_on,
                    'on': is_on,
                    'counter': counter,
                    'reason': state_reason
                }
                
            elif device_type_code == 0x04:  # EWB_DT_DIMMER
                # Dimmer state: Bit 0 = on/off, Byte 2 = brightness level (0-255)
                on_state = bool(state_value & 0x01)
                brightness = state_bytes[2] if len(state_bytes) > 2 else 0  # Direct byte access
                return {
                    'type': 'light',
                    'state': on_state,
                    'on': on_state,
                    'brightness': brightness,
                    'brightness_pct': round(brightness / 255 * 100, 1)
                }
                
            elif device_type_code == 0x05:  # EWB_DT_MOTOR (Single Motor)
                # Motor state: Complex parsing according to EWB specification Mode 0
                if len(state_bytes) >= 4:
                    # Handle both 4-byte (EWB_CHANGE_STATE) and 5-byte (EWB_RCV) responses
                    if len(state_bytes) == 4:
                        # 4-byte response from EWB_CHANGE_STATE: direct state word
                        mode = 0  # Mode is implicit (Mode 0 for motor responses)
                        state_word = int.from_bytes(state_bytes, byteorder='big')
                    else:
                        # 5-byte response from EWB_RCV: 1 byte mode + 4 bytes state
                        mode = state_bytes[0]
                        state_word = int.from_bytes(state_bytes[1:5], byteorder='big')
                    
                    # Extract motor status code (bits 31-25)
                    motor_status_code = (state_word >> 25) & 0x7F
                    
                    # Bit 24: Reserved
                    
                    # Current position (bits 23-17, 0=open, 100=closed)
                    current_position_raw = (state_word >> 17) & 0x7F
                    
                    # Bit 16: Recent tilt to horizontal
                    recent_tilt = bool(state_word & (1 << 16))
                    
                    # Target position (bits 15-9, 0=open, 100=closed)
                    target_position_raw = (state_word >> 9) & 0x7F
                    
                    # Bit 8: Auto-tilt after positioning
                    auto_tilt = bool(state_word & (1 << 8))
                    
                    # Configuration flags
                    runtime_measured = bool(state_word & (1 << 7))  # Bit 7
                    tilt_measured = bool(state_word & (1 << 6))     # Bit 6
                    # Bits 5-3: Reserved
                    terrace_function = bool(state_word & (1 << 2))  # Bit 2
                    stored_position = state_word & 0x03             # Bits 1-0
                    
                    # Enhanced runtime measurement detection logging
                    bit7_value = (state_word >> 7) & 1
                    device_id = device_serial[-8:] if device_serial else "Unknown"
                    _LOGGER.debug("🔍 EWneo state parse: Device %s, State=0x%08X, Bit7=%d, Runtime=%s, Pos=%s", 
                                 device_id, state_word, bit7_value, runtime_measured, 
                                 current_position_raw if runtime_measured else "N/A")
                    
                    # Interpret motor status code
                    motor_status_map = {
                        126: "stopped",              # Motor has stopped
                        119: "stopped_terrace",      # Motor stopped, terrace function active
                        120: "opening_runtime",      # Moving open for runtime duration
                        121: "closing_runtime",      # Moving close for runtime duration
                        122: "opening_120s",         # Moving open for 120 seconds
                        123: "closing_120s",         # Moving close for 120 seconds
                        124: "opening_position",     # Moving open to specific position
                        125: "closing_position",     # Moving close to specific position
                        117: "calibrating_runtime",  # Runtime measurement in progress
                        118: "calibrating_tilt"      # Tilt measurement in progress
                    }
                    
                    motor_status = motor_status_map.get(motor_status_code, "unknown")
                    
                    # Convert positions to Home Assistant format (0=closed, 100=open)
                    # Only valid if runtime measurement is done AND position is not 126 (unknown)
                    current_position = None
                    target_position = None
                    position_available = False
                    
                    if runtime_measured:
                        # Runtime measurement done - positions are valid if not 126
                        if current_position_raw <= 100:
                            current_position = 100 - current_position_raw  # Convert to HA format
                            position_available = True
                            
                        if target_position_raw <= 100:
                            target_position = 100 - target_position_raw    # Convert to HA format
                            
                        # Special cases where position is 126 (unknown)
                        if motor_status_code in [117, 118]:  # During calibration
                            current_position = None
                            target_position = None
                            position_available = False
                        elif motor_status_code in [122, 123]:  # 120s movement without positioning
                            current_position = None
                            target_position = None
                            position_available = False
                    else:
                        # No runtime measurement - positions are always unknown
                        current_position = None
                        target_position = None
                        position_available = False
                    
                    # Determine movement state
                    is_opening = motor_status_code in [120, 122, 124]  # Opening movements
                    is_closing = motor_status_code in [121, 123, 125]  # Closing movements
                    is_stopped = motor_status_code in [126, 119]       # Stopped states
                    is_calibrating = motor_status_code in [117, 118]   # Calibration states
                    
                    return {
                        'type': 'cover',
                        'motor_status': motor_status,
                        'motor_status_code': motor_status_code,
                        'position': current_position,
                        'target_position': target_position,
                        'position_available': position_available,
                        'runtime_measured': runtime_measured,
                        'tilt_measured': tilt_measured,
                        'terrace_function': terrace_function,
                        'recent_tilt': recent_tilt,
                        'auto_tilt': auto_tilt,
                        'stored_position': stored_position,
                        'is_opening': is_opening,
                        'is_closing': is_closing,
                        'is_stopped': is_stopped,
                        'is_calibrating': is_calibrating,
                        'supports_position': runtime_measured,
                        'supports_tilt': tilt_measured
                    }
                else:
                    # Fallback for short state data (should not happen with proper EWB)
                    _LOGGER.warning("EWB Motor: Received incomplete state data (%d bytes, expected 4 or 5)", len(state_bytes))
                    position_state = state_value & 0x03
                    return {
                        'type': 'cover',
                        'motor_status': "opening" if position_state == 1 else "closing" if position_state == 2 else "stopped",
                        'position': None,
                        'position_available': False,
                        'runtime_measured': False,
                        'is_opening': position_state == 1,
                        'is_closing': position_state == 2,
                        'is_stopped': position_state == 0
                    }
                
            elif device_type_code in [0x06, 0x07]:  # EWB_DT_DUAL_SWITCH (0x06), EWB_DT_QUAD_SWITCH (0x07)
                # Multi-switch state format: All channels packed in ONE 32-bit word
                # Dual Switch (0x06):
                #   Bits 31-27: Counter for switch #1
                #   Bits 26-24: State for switch #1 (1=off, 2=on, 3=timer, 5=logic)
                #   Bits 23-19: Counter for switch #2
                #   Bits 18-16: State for switch #2 (1=off, 2=on, 3=timer, 5=logic)
                #   Bits 15-0: Reserved
                # Quad Switch (0x07): Similar but with 4 channels
                
                # Parse as big-endian to match command format
                state_value = int.from_bytes(state_bytes, byteorder='big')
                
                channels = 2 if device_type_code == 0x06 else 4
                channel_states = {}
                
                # Extract state for each channel from the packed 32-bit word
                if device_type_code == 0x06:  # Dual switch
                    # Channel 1: bits 31-24
                    ch1_counter = (state_value >> 27) & 0x1F
                    ch1_reason = (state_value >> 24) & 0x07
                    ch1_is_on = ch1_reason in [2, 3, 5]
                    
                    # Channel 2: bits 23-16
                    ch2_counter = (state_value >> 19) & 0x1F
                    ch2_reason = (state_value >> 16) & 0x07
                    ch2_is_on = ch2_reason in [2, 3, 5]
                    
                    channel_states['channel_1'] = {
                        'state': ch1_is_on,
                        'on': ch1_is_on,
                        'counter': ch1_counter,
                        'reason': ch1_reason
                    }
                    channel_states['channel_2'] = {
                        'state': ch2_is_on,
                        'on': ch2_is_on,
                        'counter': ch2_counter,
                        'reason': ch2_reason
                    }
                    
                    device_id = device_serial[-8:] if device_serial else "Unknown"
                    _LOGGER.debug("🔍 EWneo Dual Switch %s: State=0x%08X, CH1=%s (reason=%d, cnt=%d), CH2=%s (reason=%d, cnt=%d)", 
                                 device_id, state_value, 
                                 "ON" if ch1_is_on else "OFF", ch1_reason, ch1_counter,
                                 "ON" if ch2_is_on else "OFF", ch2_reason, ch2_counter)
                    
                else:  # Quad switch (0x07)
                    # Similar pattern for 4 channels
                    # Channel 1: bits 31-24
                    # Channel 2: bits 23-16
                    # Channel 3: bits 15-8
                    # Channel 4: bits 7-0
                    for i in range(4):
                        shift = (3 - i) * 8  # 24, 16, 8, 0
                        counter = (state_value >> (shift + 3)) & 0x1F
                        reason = (state_value >> shift) & 0x07
                        is_on = reason in [2, 3, 5]
                        
                        channel_states[f'channel_{i+1}'] = {
                            'state': is_on,
                            'on': is_on,
                            'counter': counter,
                            'reason': reason
                        }
                
                return {
                    'type': 'switch',  # Return 'switch' type for compatibility
                    'channels': channels,
                    **channel_states  # Unpack channel states at top level for easy access
                }
                
            elif device_type_code in [0x08, 0x09]:  # EWB_DT_DUAL_MOTOR (0x08), EWB_DT_QUAD_MOTOR (0x09)
                # Dual/Quad motor state parsing according to EWB specification
                # 
                # Mode 0: All motors in one 32-bit word (multi_cover)
                # Mode 2/10/18/26: Individual motor detail (cover)
                
                # CRITICAL: Check mode parameter to determine format
                # Mode 0 = Summary (all motors), Mode 2/10/18/26 = Individual motor
                if mode == 0 or mode is None:
                    # Mode 0 or unknown: Parse as multi-channel summary (all motors in 32-bit word)
                    # Skip to multi_cover parsing below
                    pass
                elif mode in [2, 10, 18, 26]:
                    # Mode 2/10/18/26: Individual motor response in full detail
                    # Parse using the same format as single motor (0x05)
                    motor_status_code = (state_value >> 25) & 0x7F
                    current_position_raw = (state_value >> 17) & 0x7F  # Bits 23-17
                    recent_tilt = bool(state_value & (1 << 16))
                    target_position_raw = (state_value >> 9) & 0x7F
                    auto_tilt = bool(state_value & (1 << 8))
                    runtime_measured = bool(state_value & (1 << 7))
                    tilt_measured = bool(state_value & (1 << 6))
                    terrace_function = bool(state_value & (1 << 2))
                    stored_position = state_value & 0x03
                    
                    # Interpret motor status code
                    motor_status_map = {
                        126: "stopped",
                        119: "stopped_terrace",
                        120: "opening_runtime",
                        121: "closing_runtime",
                        122: "opening_120s",
                        123: "closing_120s",
                        124: "opening_position",
                        125: "closing_position",
                        117: "calibrating_runtime",
                        118: "calibrating_tilt"
                    }
                    
                    motor_status = motor_status_map.get(motor_status_code, "unknown")
                    
                    # Convert positions
                    current_position = None
                    target_position = None
                    position_available = False
                    
                    if runtime_measured:
                        if current_position_raw <= 100:
                            current_position = 100 - current_position_raw
                            position_available = True
                        if target_position_raw <= 100:
                            target_position = 100 - target_position_raw
                        
                        if motor_status_code in [117, 118, 122, 123]:
                            current_position = None
                            target_position = None
                            position_available = False
                    
                    # Determine movement state
                    is_opening = motor_status_code in [120, 122, 124]
                    is_closing = motor_status_code in [121, 123, 125]
                    is_stopped = motor_status_code in [126, 119] or (0 <= motor_status_code <= 100)
                    is_calibrating = motor_status_code in [117, 118]
                    
                    device_id = device_serial[-8:] if device_serial else "Unknown"
                    _LOGGER.debug("🔍 EWneo Multi-Motor (individual mode) %s: State=0x%08X, Status=%s, Pos=%s, Runtime=%s",
                                 device_id, state_value, motor_status, current_position, runtime_measured)
                    
                    # Return in multi_cover format but with single channel
                    # The Cover entity will extract the correct channel based on which mode was used
                    return {
                        'type': 'cover',  # Single motor format for individual channel response
                        'motor_status': motor_status,
                        'motor_status_code': motor_status_code,
                        'position': current_position,
                        'target_position': target_position,
                        'position_available': position_available,
                        'runtime_measured': runtime_measured,
                        'tilt_measured': tilt_measured,
                        'terrace_function': terrace_function,
                        'recent_tilt': recent_tilt,
                        'auto_tilt': auto_tilt,
                        'stored_position': stored_position,
                        'is_opening': is_opening,
                        'is_closing': is_closing,
                        'is_stopped': is_stopped,
                        'is_calibrating': is_calibrating,
                        'supports_position': runtime_measured,
                        'supports_tilt': tilt_measured
                    }
                
                # Otherwise, parse as Mode 0 (Summary) - all motors packed together
                # CORRECT FORMAT per EWB specification:
                # Dual Motor:
                #   Bits 31-25 (7 bits): Motor #1 status/position (0-100 or 117-127)
                #   Bit 24: Motor #1 recent tilt
                #   Bits 23-17 (7 bits): Motor #2 status/position
                #   Bit 16: Motor #2 recent tilt
                #   Bits 15-0: Reserved
                # Quad Motor:
                #   Bits 31-25: Motor #1 status, Bit 24: Motor #1 tilt
                #   Bits 23-17: Motor #2 status, Bit 16: Motor #2 tilt
                #   Bits 15-9: Motor #3 status, Bit 8: Motor #3 tilt
                #   Bits 7-1: Motor #4 status, Bit 0: Motor #4 tilt
                
                channels = 2 if device_type_code == 0x08 else 4
                channel_states = {}
                
                # Bit positions for each motor: [status_shift, tilt_bit]
                motor_bit_positions = [
                    (25, 24),  # Motor #1: bits 31-25, bit 24
                    (17, 16),  # Motor #2: bits 23-17, bit 16
                    (9, 8),    # Motor #3: bits 15-9, bit 8
                    (1, 0),    # Motor #4: bits 7-1, bit 0
                ]
                
                for i in range(channels):
                    status_shift, tilt_bit = motor_bit_positions[i]
                    
                    # Extract 7 bits for motor status/position
                    motor_status_code = (state_value >> status_shift) & 0x7F
                    
                    # Extract tilt bit
                    recent_tilt = bool(state_value & (1 << tilt_bit))
                    
                    # Interpret motor status code
                    motor_status_map = {
                        127: "unchanged",            # Host wants motor to remain at state
                        126: "stopped",              # Motor has stopped at unknown position
                        119: "stopped_tilted",       # Motor stopped, tilted to horizontal
                        120: "opening_runtime",      # Opening for runtime duration
                        121: "closing_runtime",      # Closing for runtime duration
                        122: "opening_120s",         # Opening for 120 seconds
                        123: "closing_120s",         # Closing for 120 seconds
                        124: "opening_position",     # Moving to open position
                        125: "closing_position",     # Moving to close position
                        117: "calibrating_runtime",  # Runtime measurement in progress
                        118: "calibrating_tilt"      # Tilt measurement in progress
                    }
                    
                    motor_status = motor_status_map.get(motor_status_code, "unknown")
                    
                    # Position is valid only if 0-100 (motor stopped at measured position)
                    # This is the ONLY indicator of runtime measurement in Mode 0
                    # Motor status codes 120/121/124/125 do NOT indicate runtime_measured
                    # because a motor can be moving without prior runtime measurement
                    position = None
                    runtime_measured = False
                    
                    if 0 <= motor_status_code <= 100:
                        # Position available: 0=open, 100=closed (convert to HA format)
                        position = 100 - motor_status_code  # Convert to HA: 0=closed, 100=open
                        runtime_measured = True
                    # NOTE: Do NOT set runtime_measured=True for 120/121/124/125
                    # Only Mode 2/10/18/26 with explicit Bit 7 can determine runtime_measured while moving
                    
                    # Determine movement state
                    is_opening = motor_status_code in [120, 122, 124]
                    is_closing = motor_status_code in [121, 123, 125]
                    is_stopped = motor_status_code in [126, 119] or (0 <= motor_status_code <= 100)
                    is_calibrating = motor_status_code in [117, 118]
                    
                    channel_states[f'channel_{i+1}'] = {
                        'motor_status': motor_status,
                        'motor_status_code': motor_status_code,
                        'position': position,
                        'position_available': position is not None,
                        'runtime_measured': runtime_measured,
                        'recent_tilt': recent_tilt,
                        'is_opening': is_opening,
                        'is_closing': is_closing,
                        'is_stopped': is_stopped,
                        'is_calibrating': is_calibrating,
                        'supports_position': runtime_measured
                    }
                
                device_id = device_serial[-8:] if device_serial else "Unknown"
                _LOGGER.debug("🔍 EWneo %s Motor %s: State=0x%08X, Motors=%s", 
                             "Dual" if channels == 2 else "Quad", device_id, state_value,
                             {f"M{i+1}": f"status={channel_states[f'channel_{i+1}']['motor_status']}, pos={channel_states[f'channel_{i+1}']['position']}" 
                              for i in range(channels)})
                
                return {
                    'type': 'multi_cover',
                    'channels': channels,
                    'channel_states': channel_states
                }
                
            else:
                _LOGGER.warning("⚠️ Unknown EWneo device type for state parsing: 0x%02X", device_type_code)
                return {
                    'type': 'unknown',
                    'raw_state': state_value,
                    'device_type_code': device_type_code
                }
                
        except Exception as e:
            _LOGGER.error("❌ Error parsing EWneo state: %s", e)
            return None

    async def _query_ewneo_state(self, gateway_serial: str, device_serial: str) -> bool:
        """Query initial state of EWneo device using EwbQueryState.
        
        Returns True if command was sent successfully and state was processed, False otherwise.
        State will also be fired as an event for entities to receive.
        """
        return await self._query_ewneo_state_with_mode(gateway_serial, device_serial, mode=0)
    
    async def _query_ewneo_state_with_mode(self, gateway_serial: str, device_serial: str, mode: int = 0) -> bool:
        """Query state of EWneo device with specific mode using EwbQueryState.
        
        Args:
            gateway_serial: Gateway device serial number
            device_serial: Target device serial number  
            mode: Query mode (0=summary, 2=motor1 full, 10=motor2 full, etc.)
        
        Returns True if command was sent successfully and state was processed, False otherwise.
        State will also be fired as an event for entities to receive.
        """
        try:
            if not hasattr(self.transceiver, 'wrapper') or not self.transceiver.wrapper:
                _LOGGER.error("❌ No transceiver wrapper available for EwbQueryState")
                return False
            
            _LOGGER.debug("🔍 Querying EWneo device state: Gateway %s, Device %s, Mode %d", 
                         gateway_serial[-8:], device_serial[-8:], mode)
            
            # Call RX11 wrapper method (async) - returns (recent_mode, state_bytes) or None
            result = await self.transceiver.wrapper.rx11_ewb_query_state(gateway_serial, device_serial, mode=mode)
            
            if result:
                recent_mode, state_bytes = result
                _LOGGER.debug("✅ EwbQueryState successful (mode %d): state=%s", 
                            mode, [f"0x{b:02X}" for b in state_bytes] if state_bytes else "None")
                
                # Report success (resets failure counter, dismisses notification if any)
                await self.report_ewneo_communication_success(device_serial)
                
                # Get device info to determine device type
                device_info = self.devices.get(device_serial, {})
                device_type_code = device_info.get("device_type_code", 0)
                device_type_name = device_info.get("device_type_name", device_info.get("device_type", "ewneo_motor"))
                
                # Parse the state using our existing parser
                parsed_state = self._parse_ewneo_state(device_type_code, list(state_bytes), device_type_name, device_serial, mode=recent_mode)
                
                if parsed_state:
                    _LOGGER.debug("🎯 Parsed EWneo state for %s: %s", device_serial[-8:], parsed_state)
                    
                    # Fire event for entities to receive
                    self.hass.bus.async_fire(
                        "easywave_ewneo_state_update",
                        {
                            "serial_number": device_serial,
                            "device_id": device_serial,
                            "parsed_state": parsed_state,
                            "state_bytes": list(state_bytes),
                            "query_mode": recent_mode,
                            "source": "query_state",
                        }
                    )
                    _LOGGER.debug("📡 Fired EWneo state update event for %s (mode=%d)", device_serial[-8:], recent_mode)
                    return True
                else:
                    _LOGGER.warning("⚠️ Could not parse EWneo state for %s", device_serial[-8:])
                    return False
            else:
                _LOGGER.warning("⚠️ EwbQueryState command failed (mode %d)", mode)
                # Report failure (increments failure counter, may trigger notification after 2 failures)
                await self.report_ewneo_communication_failure(device_serial)
                return False
        except Exception as e:
            _LOGGER.error("❌ Error sending EwbQueryState command (mode %d): %s", mode, e)
            # Report failure on exception as well
            await self.report_ewneo_communication_failure(device_serial)
            return False
    
    # ═══════════════════════════════════════════════════════════════
    # CENTRAL EVENT DISPATCHER - Consolidated Platform Event Handling
    # ═══════════════════════════════════════════════════════════════
    
    def register_platform_handler(self, platform_name: str, handler: callable) -> None:
        """Register a platform handler for centralized event dispatching.
        
        Platforms call this during setup to register their event handler.
        This allows centralized event dispatch instead of each platform listening separately.
        
        Args:
            platform_name: Name of the platform (e.g., 'switch', 'cover', 'light')
            handler: Async function that handles (serial_number, device_info, entity_specs)
        """
        self._platform_handlers[platform_name] = handler
        _LOGGER.debug("✅ Registered platform handler for '%s'", platform_name)
    
    async def _dispatch_device_added_event(self, event_data: Dict[str, Any]) -> None:
        """Central dispatcher for EVENT_DEVICE_ADDED events.
        
        This is the SINGLE point of entry for device-added events.
        All platforms listen to events fired by this dispatcher instead of the raw event.
        
        Handles:
        - Duplicate device detection
        - Entity spec retrieval
        - Platform-specific dispatching
        - Centralized error handling
        
        Args:
            event_data: Event data containing serial_number and device_info
        """
        try:
            serial_number = event_data.get("serial_number")
            device_info = event_data.get("device_info", {})
            
            if not serial_number:
                _LOGGER.warning("⚠️ Device added event missing serial_number")
                return
            
            # Avoid duplicate dispatching (unless force_create is set for re-learned devices)
            force_create = event_data.get("force_create", False)
            if serial_number in self._dispatched_devices and not force_create:
                _LOGGER.debug("⏭️  Skipping already-dispatched device: %s", serial_number[-8:])
                return
            
            self._dispatched_devices.add(serial_number)
            
            # Get entity specs (this is the SINGLE place where we generate them)
            entity_specs = create_entity_specs_for_device(serial_number, device_info, coordinator=self)
            
            if not entity_specs:
                _LOGGER.warning("⚠️ No entity specs generated for device %s", serial_number[-8:])
                return
            
            # Dispatch to each registered platform (instead of each platform listening separately)
            dispatched_platforms = []
            for platform_name, handler in self._platform_handlers.items():
                platform_entities = entity_specs.get(platform_name, [])
                
                # Only dispatch if platform has entities for this device
                if not platform_entities:
                    continue
                
                try:
                    await handler(serial_number, device_info, platform_entities)
                    dispatched_platforms.append(platform_name)
                except Exception as e:
                    _LOGGER.error("❌ Error in %s handler: %s", platform_name, e)
            
            if dispatched_platforms:
                _LOGGER.info("✅ Dispatched device %s to %d platforms: %s", 
                           serial_number[-8:], len(dispatched_platforms), dispatched_platforms)
            else:
                _LOGGER.debug("⏭️  No platforms handling device %s", serial_number[-8:])
        
        except Exception as e:
            _LOGGER.error("❌ Central dispatcher error: %s", e)
    
    # Event handler registration (called from __init__.py after platform setup)
    def setup_event_dispatchers(self) -> callable:
        """Setup central event dispatchers and return cleanup function.
        
        Returns:
            Cleanup function to unregister the event listener
        """
        @callback
        def _on_device_added_event(event: Event) -> None:
            """Callback for EVENT_DEVICE_ADDED - dispatches to registered platforms."""
            self.hass.async_create_task(
                self._dispatch_device_added_event(event.data)
            )
        
        # Register single listener for the main event
        remove_listener = self.hass.bus.async_listen(EVENT_DEVICE_ADDED, _on_device_added_event)
        _LOGGER.info("✅ Central event dispatcher setup complete")
        
        return remove_listener
    
    # Entity Persistence Helper Methods
    
    def is_entity_created(self, unique_id: str) -> bool:
        """Check if entity was already created in a previous session.
        
        Args:
            unique_id: The entity's unique_id
            
        Returns:
            True if entity was created before, False otherwise
        """
        return self.state_manager.is_entity_created(unique_id)
    
    def mark_entity_created(self, unique_id: str, entity_name: str = None) -> None:
        """Mark an entity as created. Will be persisted on shutdown.
        
        Args:
            unique_id: The entity's unique_id
            entity_name: Optional entity name for metadata
        """
        self.state_manager.mark_entity_created(unique_id, entity_name)