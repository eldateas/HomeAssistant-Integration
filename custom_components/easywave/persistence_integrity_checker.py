"""Persistence Integrity Checker - Validates and repairs persisted data.

This module checks consistency of all persisted data and provides
automatic repair capabilities.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Set

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .device_manager import DeviceManager, ManagedDevice, DeviceAvailability
from .index_allocator import IndexAllocator

_LOGGER = logging.getLogger(__name__)


class IntegrityReport:
    """Report from integrity check."""

    def __init__(self):
        """Initialize report."""
        self.timestamp = datetime.now().isoformat()
        self.status = "OK"
        self.issues: List[str] = []
        self.warnings: List[str] = []
        self.repairs: List[str] = []
        self.statistics: Dict[str, Any] = {}

    def add_issue(self, message: str) -> None:
        """Add an integrity issue."""
        self.issues.append(message)
        self.status = "ISSUES_FOUND"

    def add_warning(self, message: str) -> None:
        """Add a warning."""
        self.warnings.append(message)

    def add_repair(self, message: str) -> None:
        """Log a repair action."""
        self.repairs.append(message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to dictionary."""
        return {
            'timestamp': self.timestamp,
            'status': self.status,
            'issues': self.issues,
            'warnings': self.warnings,
            'repairs': self.repairs,
            'statistics': self.statistics,
        }


class PersistenceIntegrityChecker:
    """Validates and repairs persistence data consistency."""

    def __init__(
        self,
        hass: HomeAssistant,
        device_manager: DeviceManager,
        index_allocator: IndexAllocator,
        config_entry_id: str
    ):
        """Initialize the integrity checker.
        
        Args:
            hass: Home Assistant instance
            device_manager: Device manager instance
            index_allocator: Index allocator instance
            config_entry_id: Config entry ID
        """
        self.hass = hass
        self.device_manager = device_manager
        self.index_allocator = index_allocator
        self.config_entry_id = config_entry_id
        self._lock = asyncio.Lock()

    async def check_all(self) -> IntegrityReport:
        """Perform comprehensive integrity check.
        
        Returns:
            IntegrityReport with findings
        """
        async with self._lock:
            report = IntegrityReport()
            
            _LOGGER.info("🔍 Starting comprehensive persistence integrity check")
            
            # Check device manager
            await self._check_device_manager(report)
            
            # Check index allocator
            await self._check_index_allocator(report)
            
            # Check cross-consistency
            await self._check_cross_consistency(report)
            
            # Check HA registries
            await self._check_ha_registries(report)
            
            if not report.issues:
                report.status = "OK"
                _LOGGER.info("✅ Integrity check passed")
            else:
                _LOGGER.warning(f"⚠️ Integrity check found {len(report.issues)} issue(s)")
            
            report.statistics = {
                'devices_checked': len(self.device_manager.get_all_devices()),
                'index_allocations_checked': {
                    idx_type: len(indices)
                    for idx_type, indices in self.index_allocator._allocations.items()
                },
            }
            
            return report

    async def auto_repair(self) -> IntegrityReport:
        """Automatically repair common integrity issues.
        
        Returns:
            IntegrityReport with repairs performed
        """
        async with self._lock:
            # Run integrity check first
            check_report = await self.check_all()
            
            if not check_report.issues:
                _LOGGER.info("✅ No issues to repair")
                check_report.add_repair("No issues found")
                return check_report
            
            _LOGGER.warning(f"🔧 Auto-repairing {len(check_report.issues)} issue(s)")
            
            # Try to repair each issue
            repair_report = IntegrityReport()
            
            # Repair index allocations
            alloc_repair = await self.index_allocator.auto_repair()
            if alloc_repair.get('repairs'):
                for repair in alloc_repair['repairs']:
                    msg = f"Index repair: {repair['action']}"
                    repair_report.add_repair(msg)
                    _LOGGER.info(f"🔧 {msg}")
            
            # Repair device data
            await self._repair_device_data(repair_report)
            
            # Save repaired data
            await self.device_manager.save()
            await self.index_allocator.save()
            
            _LOGGER.info(f"✅ Auto-repair complete: {len(repair_report.repairs)} action(s)")
            
            return repair_report

    async def _check_device_manager(self, report: IntegrityReport) -> None:
        """Check device manager consistency."""
        _LOGGER.debug("🔍 Checking device manager...")
        
        devices = self.device_manager.get_all_devices()
        
        # Check for serial number uniqueness
        serials = [d.serial_number for d in devices]
        if len(serials) != len(set(serials)):
            report.add_issue("⚠️ Duplicate serial numbers detected in device list")
        
        # Check for data inconsistencies
        for device in devices:
            # Check serial number format
            if not device.serial_number or len(device.serial_number) != 32:
                report.add_warning(
                    f"Device {device.name} has invalid serial format: {device.serial_number}"
                )
            
            # Check for empty name
            if not device.name or device.name.strip() == "":
                report.add_issue(f"Device {device.serial_number} has empty name")
            
            # Check indices consistency
            if device.indices:
                for idx_type, idx_value in device.indices.items():
                    if idx_value is not None and not isinstance(idx_value, int):
                        report.add_issue(
                            f"Device {device.name}: {idx_type} index is not integer: {idx_value}"
                        )
            
            # Check availability state
            if device.availability not in [DeviceAvailability.AVAILABLE, 
                                          DeviceAvailability.UNAVAILABLE,
                                          DeviceAvailability.UNKNOWN]:
                report.add_issue(f"Device {device.name} has invalid availability state")

    async def _check_index_allocator(self, report: IntegrityReport) -> None:
        """Check index allocator consistency."""
        _LOGGER.debug("🔍 Checking index allocator...")
        
        alloc_report = await self.index_allocator.check_integrity()
        
        if alloc_report.get('issues'):
            for issue in alloc_report['issues']:
                report.add_issue(f"Index allocator: {issue}")
        
        # Check for orphaned allocations (allocated but not in device list)
        devices = self.device_manager.get_all_devices()
        allocated_serials = set()
        
        for idx_type, allocations in self.index_allocator._allocations.items():
            for idx, info in allocations.items():
                device_serial = info.get('device_serial')
                if device_serial:
                    allocated_serials.add(device_serial)
        
        device_serials = {d.serial_number for d in devices}
        orphaned = allocated_serials - device_serials
        
        if orphaned:
            report.add_warning(
                f"Found {len(orphaned)} orphaned index allocations for non-existent devices"
            )

    async def _check_cross_consistency(self, report: IntegrityReport) -> None:
        """Check consistency between devices and indices."""
        _LOGGER.debug("🔍 Checking cross-consistency...")
        
        devices = self.device_manager.get_all_devices()
        
        for device in devices:
            if device.indices:
                # Verify each allocated index exists in allocator
                for idx_type, idx_value in device.indices.items():
                    if idx_value is not None:
                        alloc_info = await self.index_allocator.get_allocation_info(
                            idx_type, idx_value
                        )
                        if not alloc_info:
                            report.add_issue(
                                f"Device {device.name}: {idx_type}:{idx_value} not found in allocator"
                            )
                        elif alloc_info.get('device_serial') != device.serial_number:
                            report.add_issue(
                                f"Device {device.name}: {idx_type}:{idx_value} allocated to different device"
                            )

    async def _check_ha_registries(self, report: IntegrityReport) -> None:
        """Check consistency with Home Assistant registries."""
        _LOGGER.debug("🔍 Checking HA registries...")
        
        try:
            device_reg = dr.async_get(self.hass)
            entity_reg = er.async_get(self.hass)
            
            # Get devices for this integration
            integration_devices = [
                d for d in device_reg.devices.values()
                if any(entry[0] == self.config_entry_id for entry in d.config_entries)
            ]
            
            # Check for devices in HA but not in our manager
            for ha_device in integration_devices:
                # Find corresponding managed device (by name or other attributes)
                managed_found = False
                for managed_device in self.device_manager.get_all_devices():
                    if managed_device.name == ha_device.name:
                        managed_found = True
                        break
                
                if not managed_found:
                    report.add_warning(
                        f"HA device '{ha_device.name}' found but not in device manager"
                    )
        
        except Exception as e:
            _LOGGER.warning(f"⚠️ Could not check HA registries: {e}")

    async def _repair_device_data(self, report: IntegrityReport) -> None:
        """Repair device data issues."""
        devices = self.device_manager.get_all_devices()
        
        for device in devices:
            # Fix invalid availability
            if device.availability not in [DeviceAvailability.AVAILABLE,
                                           DeviceAvailability.UNAVAILABLE,
                                           DeviceAvailability.UNKNOWN]:
                device.availability = DeviceAvailability.UNKNOWN
                report.add_repair(f"Fixed invalid availability for {device.name}")
            
            # Fix empty names
            if not device.name or device.name.strip() == "":
                device.name = f"{device.device_type}_{device.serial_number[-8:]}"
                report.add_repair(f"Generated name for device {device.serial_number[-8:]}")
