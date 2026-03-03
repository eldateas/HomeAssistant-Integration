"""Defensive State Manager - Robust loading with zero data loss.

Key principles:
1. Never delete existing state due to failed async load
2. Graceful fallback to previous state on errors
3. Automatic retry with exponential backoff
4. Transparent error handling with detailed logging
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Any, List

from homeassistant.core import HomeAssistant

from .device_manager import DeviceManager, ManagedDevice
from .index_allocator import IndexAllocator
from .persistence_integrity_checker import PersistenceIntegrityChecker

_LOGGER = logging.getLogger(__name__)


class LoadState:
    """Represents a loadable persistence state with retry logic."""
    
    def __init__(self, name: str, loader_func, max_retries: int = 3):
        """Initialize load state.
        
        Args:
            name: Name of this load operation
            loader_func: Async function that performs the load
            max_retries: Maximum retry attempts
        """
        self.name = name
        self.loader_func = loader_func
        self.max_retries = max_retries
        
        self.loaded = False
        self.failed = False
        self.retry_count = 0
        self.last_error: Optional[Exception] = None
        self.last_error_time: Optional[datetime] = None
    
    async def try_load(self) -> bool:
        """Attempt to load with retries.
        
        Returns:
            True if loaded successfully, False otherwise (but doesn't clear previous state)
        """
        if self.loaded:
            return True
        
        while self.retry_count < self.max_retries:
            try:
                _LOGGER.debug(f"🔄 Loading {self.name} (attempt {self.retry_count + 1}/{self.max_retries})")
                
                success = await self.loader_func()
                
                if success:
                    self.loaded = True
                    self.failed = False
                    self.retry_count = 0
                    _LOGGER.info(f"✅ Loaded {self.name}")
                    return True
                else:
                    raise RuntimeError(f"Loader returned False")
            
            except Exception as e:
                self.retry_count += 1
                self.last_error = e
                self.last_error_time = datetime.now()
                
                if self.retry_count < self.max_retries:
                    wait_seconds = 2 ** self.retry_count  # Exponential backoff
                    _LOGGER.warning(
                        f"⚠️ {self.name} load failed (attempt {self.retry_count}): {e}. "
                        f"Retrying in {wait_seconds}s..."
                    )
                    await asyncio.sleep(wait_seconds)
                else:
                    self.failed = True
                    _LOGGER.error(
                        f"❌ {self.name} load failed after {self.max_retries} attempts: {e}"
                    )
                    return False
        
        return False


class DefensiveStateManager:
    """Manages persistence state with zero data loss and graceful degradation.
    
    Key features:
    - Defensive loading: errors don't delete existing state
    - Automatic retries with exponential backoff
    - Fallback to previous state on failure
    - Simplified API with single load/save point
    - Automatic integrity checking and repair
    """
    
    def __init__(
        self,
        hass: HomeAssistant,
        device_manager: DeviceManager,
        index_allocator: IndexAllocator,
        integrity_checker: PersistenceIntegrityChecker,
        config_entry_id: str
    ):
        """Initialize the defensive state manager.
        
        Args:
            hass: Home Assistant instance
            device_manager: Device manager instance
            index_allocator: Index allocator instance
            integrity_checker: Integrity checker instance
            config_entry_id: Config entry ID
        """
        self.hass = hass
        self.device_manager = device_manager
        self.index_allocator = index_allocator
        self.integrity_checker = integrity_checker
        self.config_entry_id = config_entry_id
        self._lock = asyncio.Lock()
        
        # Define all load operations
        self._load_states: Dict[str, LoadState] = {
            'device_manager': LoadState(
                'DeviceManager',
                self.device_manager.load,
                max_retries=3
            ),
            'index_allocator': LoadState(
                'IndexAllocator',
                self.index_allocator.load,
                max_retries=3
            ),
        }
        
        # Define all save operations (order matters!)
        self._save_operations: List[tuple[str, callable]] = [
            ('devices', self.device_manager.save),
            ('indices', self.index_allocator.save),
        ]
        
        # Track last successful load
        self._last_successful_load: Optional[datetime] = None
        # Track load health
        self._is_degraded = False
        self._degradation_reason: Optional[str] = None

    async def load_all(self) -> bool:
        """Load all persistence data defensively (never deletes existing state).
        
        Returns:
            True if all loads succeeded, False if in degraded mode
        """
        async with self._lock:
            _LOGGER.info("📖 Starting defensive load of all persistence data")
            
            try:
                # Phase 1: Try to load everything
                load_results = {}
                for name, load_state in self._load_states.items():
                    load_results[name] = await load_state.try_load()
                
                # Phase 2: Check if all succeeded
                all_succeeded = all(load_results.values())
                
                if all_succeeded:
                    _LOGGER.info("✅ All persistence data loaded successfully")
                    self._is_degraded = False
                    self._degradation_reason = None
                    self._last_successful_load = datetime.now()
                    
                    # Phase 3: Run integrity check (non-blocking)
                    integrity_report = await self.integrity_checker.check_all()
                    if integrity_report.issues:
                        _LOGGER.warning(
                            f"⚠️ Integrity issues detected, attempting auto-repair"
                        )
                        repair_report = await self.integrity_checker.auto_repair()
                        if repair_report.repairs:
                            _LOGGER.info(f"🔧 Repaired {len(repair_report.repairs)} issue(s)")
                            # Save repaired state
                            await self.save_all()
                    
                    return True
                else:
                    # Some loads failed - enter degraded mode
                    failed = [name for name, success in load_results.items() if not success]
                    degradation_msg = f"Failed to load: {', '.join(failed)}"
                    
                    _LOGGER.warning(
                        f"⚠️ Entering degraded mode: {degradation_msg}"
                    )
                    
                    self._is_degraded = True
                    self._degradation_reason = degradation_msg
                    
                    # CRITICAL: Don't delete existing state!
                    # The loaders preserve previous state on failure
                    # So we're still functional with last known good state
                    
                    _LOGGER.info(
                        "ℹ️ Using last known good state (degraded mode). "
                        "Data is safe, will retry next cycle."
                    )
                    
                    return False
            
            except Exception as e:
                _LOGGER.error(f"❌ Unexpected error during load: {e}", exc_info=True)
                self._is_degraded = True
                self._degradation_reason = str(e)
                return False

    async def save_all(self) -> bool:
        """Save all persistence data defensively.
        
        Uses atomic transactions where available. On failure, preserves
        previous save state.
        
        Returns:
            True if all saves succeeded, False otherwise
        """
        async with self._lock:
            _LOGGER.info("💾 Starting defensive save of all persistence data")
            
            try:
                # Save in order - each should preserve state on failure
                save_results = {}
                for op_name, save_func in self._save_operations:
                    try:
                        _LOGGER.debug(f"💾 Saving {op_name}...")
                        success = await save_func()
                        save_results[op_name] = success
                        
                        if not success:
                            _LOGGER.warning(f"⚠️ Save of {op_name} returned False")
                    except Exception as e:
                        _LOGGER.error(f"❌ Error saving {op_name}: {e}")
                        save_results[op_name] = False
                
                # Check results
                all_succeeded = all(save_results.values())
                
                if all_succeeded:
                    _LOGGER.info("✅ All persistence data saved successfully")
                    self._last_successful_load = datetime.now()
                    return True
                else:
                    failed = [name for name, success in save_results.items() if not success]
                    _LOGGER.error(f"❌ Save failed for: {', '.join(failed)}")
                    return False
            
            except Exception as e:
                _LOGGER.error(f"❌ Unexpected error during save: {e}", exc_info=True)
                return False

    async def ensure_loaded(self) -> bool:
        """Ensure all persistence data is loaded (idempotent).
        
        Safe to call multiple times. If already loaded, returns quickly.
        If in degraded mode, attempts reload.
        
        Returns:
            True if loaded successfully, False if degraded
        """
        async with self._lock:
            # Check if already fully loaded
            if all(state.loaded for state in self._load_states.values()):
                return not self._is_degraded
            
            # If degraded, attempt recovery
            if self._is_degraded:
                _LOGGER.info("🔄 Attempting recovery from degraded mode...")
                
                # Reset all load states for retry
                for state in self._load_states.values():
                    state.retry_count = 0
                    state.last_error = None
            
            # Try to load
            return await self.load_all()

    async def auto_save_on_interval(self, interval_seconds: float = 60.0) -> None:
        """Background task for periodic auto-save.
        
        Args:
            interval_seconds: Save interval in seconds
        """
        _LOGGER.info(f"🔄 Starting auto-save task (interval: {interval_seconds}s)")
        
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                
                # Only save if not degraded
                if not self._is_degraded:
                    success = await self.save_all()
                    if not success:
                        _LOGGER.warning("⚠️ Auto-save failed")
                else:
                    # In degraded mode, just log
                    _LOGGER.debug(
                        f"⏭️ Skipping auto-save (degraded: {self._degradation_reason})"
                    )
        
        except asyncio.CancelledError:
            _LOGGER.info("🛑 Auto-save task cancelled")
        except Exception as e:
            _LOGGER.error(f"❌ Auto-save task error: {e}", exc_info=True)

    def is_healthy(self) -> bool:
        """Check if state manager is in healthy (full) mode.
        
        Returns:
            True if all systems operational, False if degraded
        """
        return not self._is_degraded

    def get_status(self) -> Dict[str, Any]:
        """Get current state manager status.
        
        Returns:
            Status dictionary with detailed information
        """
        return {
            'healthy': not self._is_degraded,
            'degraded': self._is_degraded,
            'degradation_reason': self._degradation_reason,
            'last_successful_load': self._last_successful_load.isoformat() if self._last_successful_load else None,
            'load_states': {
                name: {
                    'loaded': state.loaded,
                    'failed': state.failed,
                    'retry_count': state.retry_count,
                    'last_error': str(state.last_error) if state.last_error else None,
                }
                for name, state in self._load_states.items()
            }
        }

    def log_status(self) -> None:
        """Log current status for debugging."""
        status = self.get_status()
        
        if status['healthy']:
            _LOGGER.info("✅ State Manager: HEALTHY - all systems operational")
        else:
            _LOGGER.warning(
                f"⚠️ State Manager: DEGRADED - {status['degradation_reason']}"
            )
            
            for name, load_state_info in status.get('load_states', {}).items():
                if load_state_info['failed']:
                    _LOGGER.warning(
                        f"  - {name}: FAILED ({load_state_info.get('last_error')})"
                    )
                else:
                    _LOGGER.debug(f"  - {name}: OK")
