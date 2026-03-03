"""Central Index Allocator - Unified management of all device indices.

This module provides centralized allocation and tracking of device indices
(EWB, EW Receiver, etc.) with full recovery capabilities.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class IndexAllocationConflict(Exception):
    """Raised when an index allocation conflict is detected."""
    pass


class IndexAllocator:
    """Central allocator for device indices with integrity checking."""

    # Define index ranges for different types
    INDEX_RANGES = {
        'ewb': (0, 255),           # EWB Gateway indices 0-255
        'ew_receiver': (0, 255),   # Easywave Receiver indices 0-255
        'rx11': (0, 255),          # RX11 Gateway indices 0-255
    }

    def __init__(self, hass: HomeAssistant, config_entry_id: str):
        """Initialize the index allocator.
        
        Args:
            hass: Home Assistant instance
            config_entry_id: Config entry ID
        """
        self.hass = hass
        self.config_entry_id = config_entry_id
        self._lock = asyncio.Lock()
        
        # Persistent storage
        config_dir = Path(hass.config.config_dir) / DOMAIN
        self.allocations_file = config_dir / f"index_allocations_{config_entry_id}.json"
        
        # In-memory allocations: {index_type: {index_value: {device_serial, name, allocated_at}}}
        self._allocations: Dict[str, Dict[int, Dict[str, str]]] = {}
        # Backup of previous state (for defensive loading)
        self._allocations_backup: Dict[str, Dict[int, Dict[str, str]]] = {}
        
        # Next free index cache: {index_type: next_free_index}
        self._next_free_cache: Dict[str, int] = {}
        # Backup cache
        self._next_free_cache_backup: Dict[str, int] = {}

    async def load(self) -> bool:
        """Load allocations from persistent storage (defensive - preserves state on errors).
        
        Returns:
            True if loaded successfully, False otherwise (but preserves previous state)
        """
        async with self._lock:
            try:
                # STEP 1: Backup current state (for defensive recovery)
                self._allocations_backup = {
                    k: {idx: info.copy() for idx, info in v.items()} 
                    for k, v in self._allocations.items()
                }
                self._next_free_cache_backup = dict(self._next_free_cache)
                
                if not self.allocations_file.exists():
                    _LOGGER.info("📋 No index allocations file found, starting fresh")
                    await self._initialize_all_indices()
                    return True
                
                _LOGGER.info("📖 Loading index allocations from %s", self.allocations_file)
                
                # STEP 2: Read file
                data = await self._read_json_file(self.allocations_file)
                
                # STEP 3: Load into temporary state
                temp_allocations: Dict[str, Dict[int, Dict[str, str]]] = {}
                
                for index_type, allocations in data.get('allocations', {}).items():
                    temp_allocations[index_type] = {}
                    # Convert string keys back to integers
                    for idx_str, info in allocations.items():
                        try:
                            idx = int(idx_str)
                            temp_allocations[index_type][idx] = info
                        except (ValueError, TypeError):
                            _LOGGER.warning("⚠️ Skipping invalid index %s for type %s", 
                                          idx_str, index_type)
                
                # Initialize missing index types
                for index_type in self.INDEX_RANGES:
                    if index_type not in temp_allocations:
                        temp_allocations[index_type] = {}
                
                # STEP 4: Only now replace state if all succeeded
                self._allocations = temp_allocations
                
                _LOGGER.info("✅ Loaded index allocations: %s", 
                           {k: len(v) for k, v in self._allocations.items()})
                
                # Rebuild cache
                await self._rebuild_next_free_cache()
                
                return True
                
            except Exception as e:
                _LOGGER.error("❌ Failed to load index allocations: %s", e)
                
                # RECOVERY: Restore previous state from backup
                if self._allocations_backup:
                    _LOGGER.warning("🔄 Restoring previous allocation state from backup")
                    self._allocations = {
                        k: {idx: info.copy() for idx, info in v.items()} 
                        for k, v in self._allocations_backup.items()
                    }
                    self._next_free_cache = dict(self._next_free_cache_backup)
                    return False
                else:
                    # No backup, initialize fresh
                    _LOGGER.warning("⚠️ No backup available, initializing fresh state")
                    await self._initialize_all_indices()
                    return False

    async def save(self) -> bool:
        """Save allocations to persistent storage.
        
        Returns:
            True if saved successfully, False otherwise
        """
        async with self._lock:
            try:
                # Prepare data structure (convert int keys to strings for JSON)
                data = {
                    'version': '1.0',
                    'created_at': datetime.now().isoformat(),
                    'config_entry_id': self.config_entry_id,
                    'allocations': {
                        index_type: {
                            str(idx): info
                            for idx, info in allocations.items()
                        }
                        for index_type, allocations in self._allocations.items()
                    }
                }
                
                # Write to file
                await self._write_json_file(self.allocations_file, data)
                
                _LOGGER.debug("💾 Saved index allocations for %d types", 
                            len(self._allocations))
                return True
                
            except Exception as e:
                _LOGGER.error("❌ Failed to save index allocations: %s", e)
                return False

    async def allocate_index(
        self,
        index_type: str,
        device_serial: str,
        device_name: str,
        allocation_gateway: Optional[str] = None,
        preferred_index: Optional[int] = None
    ) -> int:
        """Allocate an index for a device.
        
        Args:
            index_type: Type of index ('ewb', 'ew_receiver', 'rx11', etc.)
            device_serial: Serial number of the device
            device_name: Human-readable name of the device
            allocation_gateway: Serial of gateway that allocated the index
            preferred_index: Preferred index value (will use if available)
            
        Returns:
            Allocated index value
            
        Raises:
            IndexAllocationConflict: If index cannot be allocated
        """
        async with self._lock:
            if index_type not in self.INDEX_RANGES:
                raise ValueError(f"Unknown index type: {index_type}")
            
            min_idx, max_idx = self.INDEX_RANGES[index_type]
            
            # Try preferred index first
            if preferred_index is not None:
                if min_idx <= preferred_index <= max_idx:
                    if preferred_index not in self._allocations[index_type]:
                        # Allocate preferred index
                        self._allocations[index_type][preferred_index] = {
                            'device_serial': device_serial,
                            'device_name': device_name,
                            'allocated_at': datetime.now().isoformat(),
                            'allocation_gateway': allocation_gateway,
                        }
                        _LOGGER.info(
                            "✅ Allocated %s index %d to %s (preferred)",
                            index_type, preferred_index, device_name
                        )
                        await self._rebuild_next_free_cache()
                        return preferred_index
                    else:
                        _LOGGER.warning(
                            "⚠️ Preferred %s index %d already allocated",
                            index_type, preferred_index
                        )
            
            # Find next free index
            next_free = self._next_free_cache.get(index_type, min_idx)
            
            for idx in range(next_free, max_idx + 1):
                if idx not in self._allocations[index_type]:
                    # Found free index
                    self._allocations[index_type][idx] = {
                        'device_serial': device_serial,
                        'device_name': device_name,
                        'allocated_at': datetime.now().isoformat(),
                        'allocation_gateway': allocation_gateway,
                    }
                    _LOGGER.info(
                        "✅ Allocated %s index %d to %s",
                        index_type, idx, device_name
                    )
                    await self._rebuild_next_free_cache()
                    return idx
            
            # No free index found
            raise IndexAllocationConflict(
                f"No free {index_type} index available (range {min_idx}-{max_idx}, "
                f"{len(self._allocations[index_type])} allocated)"
            )

    async def deallocate_index(
        self,
        index_type: str,
        index: int,
        device_serial: Optional[str] = None
    ) -> bool:
        """Deallocate an index.
        
        Args:
            index_type: Type of index
            index: Index value to deallocate
            device_serial: Optional serial to verify ownership
            
        Returns:
            True if deallocated, False if not found
        """
        async with self._lock:
            if index_type not in self._allocations:
                return False
            
            if index not in self._allocations[index_type]:
                _LOGGER.debug("Index %s:%d not allocated", index_type, index)
                return False
            
            # Verify ownership if requested
            if device_serial:
                info = self._allocations[index_type][index]
                if info.get('device_serial') != device_serial:
                    _LOGGER.warning(
                        "⚠️ Ownership mismatch: tried to deallocate %s:%d but owned by %s",
                        index_type, index, info.get('device_serial')
                    )
                    return False
            
            # Deallocate
            info = self._allocations[index_type].pop(index)
            _LOGGER.info(
                "🗑️ Deallocated %s index %d (was: %s)",
                index_type, index, info.get('device_name', 'unknown')
            )
            
            await self._rebuild_next_free_cache()
            return True

    async def get_allocation_info(
        self,
        index_type: str,
        index: int
    ) -> Optional[Dict[str, str]]:
        """Get information about an allocated index.
        
        Args:
            index_type: Type of index
            index: Index value
            
        Returns:
            Allocation info dict or None if not allocated
        """
        async with self._lock:
            if index_type in self._allocations:
                return self._allocations[index_type].get(index)
            return None

    async def get_device_indices(self, device_serial: str) -> Dict[str, int]:
        """Get all indices allocated to a device.
        
        Args:
            device_serial: Device serial number
            
        Returns:
            Dict mapping index_type to index value
        """
        async with self._lock:
            result = {}
            for index_type, allocations in self._allocations.items():
                for idx, info in allocations.items():
                    if info.get('device_serial') == device_serial:
                        result[index_type] = idx
            return result

    async def get_allocated_indices(self, index_type: str) -> List[int]:
        """Get list of allocated indices for a type.
        
        Args:
            index_type: Type of index
            
        Returns:
            Sorted list of allocated indices
        """
        async with self._lock:
            if index_type in self._allocations:
                return sorted(self._allocations[index_type].keys())
            return []

    async def check_integrity(self) -> Dict[str, any]:
        """Check integrity of allocations.
        
        Returns:
            Report with integrity check results
        """
        async with self._lock:
            report = {
                'timestamp': datetime.now().isoformat(),
                'index_types': {},
                'issues': [],
            }
            
            for index_type, min_idx, max_idx in [
                (k, v[0], v[1]) for k, v in self.INDEX_RANGES.items()
            ]:
                allocations = self._allocations.get(index_type, {})
                allocated_indices = sorted(allocations.keys())
                
                type_report = {
                    'range': (min_idx, max_idx),
                    'allocated': len(allocated_indices),
                    'free': (max_idx - min_idx + 1) - len(allocated_indices),
                    'utilization': len(allocated_indices) / (max_idx - min_idx + 1) * 100,
                }
                
                # Check for out-of-range indices
                out_of_range = [idx for idx in allocated_indices if idx < min_idx or idx > max_idx]
                if out_of_range:
                    report['issues'].append(
                        f"Out-of-range {index_type} indices: {out_of_range}"
                    )
                    type_report['out_of_range'] = out_of_range
                
                # Check for duplicate allocations (shouldn't happen, but validate)
                serials = [info.get('device_serial') for info in allocations.values()]
                if len(serials) != len(set(serials)):
                    report['issues'].append(f"Duplicate device serials in {index_type}")
                
                report['index_types'][index_type] = type_report
            
            if not report['issues']:
                report['status'] = 'OK'
            else:
                report['status'] = 'ISSUES_FOUND'
            
            return report

    async def auto_repair(self) -> Dict[str, any]:
        """Automatically repair common allocation issues.
        
        Returns:
            Report with repair actions taken
        """
        async with self._lock:
            report = {
                'timestamp': datetime.now().isoformat(),
                'repairs': [],
            }
            
            # Check for out-of-range indices and remove them
            for index_type, min_idx, max_idx in [
                (k, v[0], v[1]) for k, v in self.INDEX_RANGES.items()
            ]:
                allocations = self._allocations.get(index_type, {})
                out_of_range = [idx for idx in allocations.keys() if idx < min_idx or idx > max_idx]
                
                for idx in out_of_range:
                    info = allocations.pop(idx)
                    report['repairs'].append({
                        'action': 'removed_out_of_range_index',
                        'index_type': index_type,
                        'index': idx,
                        'device_name': info.get('device_name'),
                    })
                    _LOGGER.warning(
                        "🔧 Removed out-of-range %s index %d (%s)",
                        index_type, idx, info.get('device_name')
                    )
            
            await self._rebuild_next_free_cache()
            
            return report

    async def export_allocation_map(self) -> Dict[str, any]:
        """Export complete allocation map for backup/debugging.
        
        Returns:
            Complete allocation map
        """
        async with self._lock:
            return {
                'timestamp': datetime.now().isoformat(),
                'allocations': self._allocations,
                'next_free_cache': self._next_free_cache,
            }

    async def _initialize_all_indices(self) -> None:
        """Initialize all index types with empty allocations."""
        for index_type in self.INDEX_RANGES:
            self._allocations[index_type] = {}
        await self._rebuild_next_free_cache()

    async def _rebuild_next_free_cache(self) -> None:
        """Rebuild the next free index cache."""
        for index_type, (min_idx, max_idx) in self.INDEX_RANGES.items():
            allocations = self._allocations.get(index_type, {})
            allocated_set = set(allocations.keys())
            
            # Find first free index
            for idx in range(min_idx, max_idx + 1):
                if idx not in allocated_set:
                    self._next_free_cache[index_type] = idx
                    break
            else:
                # No free index found
                self._next_free_cache[index_type] = max_idx + 1

    async def _read_json_file(self, file_path: Path) -> Dict:
        """Read JSON file asynchronously."""
        # Use async file reading
        import aiofiles
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        return json.loads(content)

    async def _write_json_file(self, file_path: Path, data: Dict) -> None:
        """Write JSON file asynchronously."""
        import aiofiles
        
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write to temp file first
        temp_path = file_path.with_suffix('.tmp')
        async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, indent=2))
        
        # Atomic rename
        temp_path.replace(file_path)
