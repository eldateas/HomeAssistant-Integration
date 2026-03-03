"""Persistence Transaction - Atomic multi-file persistence operations.

This module ensures transactional consistency when writing multiple
persistence files (devices, indices, etc.) at once.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any, List

import aiofiles

_LOGGER = logging.getLogger(__name__)


class PersistenceTransaction:
    """Context manager for atomic persistence operations across multiple files.
    
    Usage:
        async with PersistenceTransaction() as txn:
            txn.queue_write('file1.json', data1)
            txn.queue_write('file2.json', data2)
            # On exit, all files are written atomically
    """

    def __init__(self, timeout: float = 30.0):
        """Initialize a persistence transaction.
        
        Args:
            timeout: Timeout in seconds for the entire transaction
        """
        self.timeout = timeout
        self._queued_writes: Dict[Path, Dict[str, Any]] = {}
        self._temp_files: Dict[Path, Path] = {}
        self._committed = False
        self._rolled_back = False
        self._lock = asyncio.Lock()

    def queue_write(self, file_path: Path, data: Dict[str, Any]) -> None:
        """Queue a file write operation.
        
        Args:
            file_path: Path to write to
            data: Data to write (will be JSON serialized)
            
        Raises:
            RuntimeError: If transaction already committed or rolled back
        """
        if self._committed or self._rolled_back:
            raise RuntimeError("Cannot queue writes after commit/rollback")
        
        self._queued_writes[file_path] = data
        _LOGGER.debug(f"📝 Queued write to {file_path.name}")

    async def __aenter__(self) -> PersistenceTransaction:
        """Enter transaction context."""
        await self._lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit transaction context - commits on success, rolls back on error."""
        try:
            if exc_type is not None:
                # Exception occurred in context, rollback
                _LOGGER.warning(f"❌ Transaction context raised exception, rolling back: {exc_type.__name__}")
                await self.rollback()
            else:
                # Success, commit
                await self.commit()
        finally:
            self._lock.release()

    async def commit(self) -> bool:
        """Commit all queued writes atomically.
        
        This writes all files to temporary paths first, then renames them atomically
        to ensure consistency.
        
        Returns:
            True if all writes succeeded, False otherwise
        """
        if self._committed:
            _LOGGER.warning("⚠️ Transaction already committed")
            return True
        
        if len(self._queued_writes) == 0:
            _LOGGER.debug("📋 No writes queued, skipping commit")
            self._committed = True
            return True
        
        try:
            # Phase 1: Write all files to temporary paths
            _LOGGER.debug(f"💾 Committing {len(self._queued_writes)} file(s)")
            
            for file_path, data in self._queued_writes.items():
                temp_path = file_path.with_suffix('.tmp')
                
                try:
                    # Ensure directory exists
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Write to temp file
                    async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                        await f.write(json.dumps(data, indent=2))
                    
                    self._temp_files[file_path] = temp_path
                    _LOGGER.debug(f"📝 Wrote temporary file: {temp_path.name}")
                
                except Exception as e:
                    _LOGGER.error(f"❌ Failed to write temporary file {temp_path}: {e}")
                    await self.rollback()
                    return False
            
            # Phase 2: Atomic rename all temp files to target paths
            try:
                for file_path, temp_path in self._temp_files.items():
                    try:
                        # Atomic rename
                        temp_path.replace(file_path)
                        _LOGGER.debug(f"✅ Committed: {file_path.name}")
                    except Exception as e:
                        _LOGGER.error(f"❌ Failed to rename {temp_path} to {file_path}: {e}")
                        # Clean up any partial writes
                        await self.rollback()
                        return False
                
                _LOGGER.info(f"✅ Transaction committed: {len(self._queued_writes)} file(s)")
                self._committed = True
                return True
                
            except Exception as e:
                _LOGGER.error(f"❌ Error during atomic rename phase: {e}")
                await self.rollback()
                return False
        
        except Exception as e:
            _LOGGER.error(f"❌ Unexpected error during commit: {e}")
            await self.rollback()
            return False

    async def rollback(self) -> None:
        """Rollback the transaction - clean up temporary files.
        
        Returns:
            None
        """
        if self._rolled_back:
            return
        
        _LOGGER.info(f"⏮️ Rollback transaction: cleaning up {len(self._temp_files)} temp file(s)")
        
        for file_path, temp_path in self._temp_files.items():
            try:
                if temp_path.exists():
                    temp_path.unlink()
                    _LOGGER.debug(f"🗑️ Deleted temporary file: {temp_path.name}")
            except Exception as e:
                _LOGGER.warning(f"⚠️ Failed to delete temp file {temp_path}: {e}")
        
        self._temp_files.clear()
        self._rolled_back = True

    def get_queued_files(self) -> List[Path]:
        """Get list of queued file paths."""
        return list(self._queued_writes.keys())


class TransactionalPersistenceManager:
    """Manager for transactional persistence of multiple data sources."""

    def __init__(self, timeout: float = 30.0):
        """Initialize the persistence manager.
        
        Args:
            timeout: Timeout for transactions
        """
        self.timeout = timeout
        self._in_transaction = False

    def create_transaction(self) -> PersistenceTransaction:
        """Create a new persistence transaction.
        
        Returns:
            New PersistenceTransaction instance
        """
        return PersistenceTransaction(self.timeout)

    async def atomic_save(
        self,
        writes: Dict[Path, Dict[str, Any]]
    ) -> bool:
        """
        Perform atomic save of multiple files in a single transaction.
        
        Args:
            writes: Dict mapping file paths to data to write
            
        Returns:
            True if all writes succeeded, False otherwise
        """
        async with PersistenceTransaction(self.timeout) as txn:
            for file_path, data in writes.items():
                txn.queue_write(file_path, data)
            
            return await txn.commit()
