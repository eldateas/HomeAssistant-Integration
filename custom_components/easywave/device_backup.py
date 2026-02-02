"""Device backup management for ELDAT integration.

This module handles backup creation, restoration, and management
for device configurations.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class DeviceBackup:
    """Manages device configuration backups."""

    def __init__(self, hass: HomeAssistant, config_entry_id: str) -> None:
        """Initialize device backup manager."""
        self.hass = hass
        self.config_entry_id = config_entry_id
        self.config_dir = Path(hass.config.config_dir) / DOMAIN
        self.backup_dir = self.config_dir / "backups"
        self.config_file = self.config_dir / "eldat_devices.json"
        
        # Ensure directories exist
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    async def create_manual_backup(self) -> str:
        """Create a manual backup with timestamp.
        
        Returns:
            Path to created backup file
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = self.backup_dir / f"eldat_devices_manual_{timestamp}.json"
            
            if self.config_file.exists():
                await self._copy_file(self.config_file, backup_file)
                _LOGGER.info(f"📦 Created manual backup: {backup_file.name}")
                return str(backup_file)
            else:
                _LOGGER.warning("⚠️ No configuration file to backup")
                return ""
                
        except Exception as e:
            _LOGGER.error(f"❌ Failed to create manual backup: {e}")
            return ""

    async def create_automatic_backup(self) -> bool:
        """Create an automatic backup (overwrites previous auto backup).
        
        Returns:
            True if backup created successfully
        """
        try:
            backup_file = self.backup_dir / "eldat_devices_auto.json"
            
            if self.config_file.exists():
                await self._copy_file(self.config_file, backup_file)
                _LOGGER.debug("💾 Created automatic backup")
                return True
            return False
                
        except Exception as e:
            _LOGGER.error(f"❌ Failed to create automatic backup: {e}")
            return False

    async def list_backups(self) -> List[Dict[str, Any]]:
        """List all available backups with metadata.
        
        Returns:
            List of backup information dictionaries
        """
        try:
            backups = []
            
            if not self.backup_dir.exists():
                return backups
            
            for backup_file in self.backup_dir.glob("*.json"):
                try:
                    stat = backup_file.stat()
                    backup_info = {
                        "name": backup_file.name,
                        "path": str(backup_file),
                        "size": stat.st_size,
                        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "is_auto": "auto" in backup_file.name,
                    }
                    
                    # Try to read device count
                    try:
                        data = await self._read_json_file(backup_file)
                        backup_info["device_count"] = len(data.get("devices", {}))
                        backup_info["version"] = data.get("version", "unknown")
                    except:
                        backup_info["device_count"] = -1
                        backup_info["version"] = "unknown"
                    
                    backups.append(backup_info)
                    
                except Exception as e:
                    _LOGGER.warning(f"⚠️ Could not read backup {backup_file.name}: {e}")
                    continue
            
            # Sort by creation date, newest first
            backups.sort(key=lambda x: x["created"], reverse=True)
            
            _LOGGER.debug(f"📋 Found {len(backups)} backups")
            return backups
            
        except Exception as e:
            _LOGGER.error(f"❌ Failed to list backups: {e}")
            return []

    async def restore_backup(self, backup_path: str) -> bool:
        """Restore configuration from a backup file.
        
        Args:
            backup_path: Path to backup file to restore
            
        Returns:
            True if restored successfully
        """
        try:
            backup_file = Path(backup_path)
            
            if not backup_file.exists():
                _LOGGER.error(f"❌ Backup file not found: {backup_path}")
                return False
            
            # Create backup of current config before restoring
            await self.create_automatic_backup()
            
            # Restore backup
            await self._copy_file(backup_file, self.config_file)
            _LOGGER.info(f"✅ Restored configuration from {backup_file.name}")
            return True
            
        except Exception as e:
            _LOGGER.error(f"❌ Failed to restore backup: {e}")
            return False

    async def delete_backup(self, backup_path: str) -> bool:
        """Delete a specific backup file.
        
        Args:
            backup_path: Path to backup file to delete
            
        Returns:
            True if deleted successfully
        """
        try:
            backup_file = Path(backup_path)
            
            if not backup_file.exists():
                _LOGGER.warning(f"⚠️ Backup file not found: {backup_path}")
                return False
            
            # Prevent deletion of auto backup
            if "auto" in backup_file.name:
                _LOGGER.warning("⚠️ Cannot delete automatic backup")
                return False
            
            backup_file.unlink()
            _LOGGER.info(f"🗑️ Deleted backup: {backup_file.name}")
            return True
            
        except Exception as e:
            _LOGGER.error(f"❌ Failed to delete backup: {e}")
            return False

    async def cleanup_old_backups(self, keep_count: int = 10) -> int:
        """Clean up old manual backups, keeping only the most recent ones.
        
        Args:
            keep_count: Number of recent backups to keep
            
        Returns:
            Number of backups deleted
        """
        try:
            backups = await self.list_backups()
            
            # Filter only manual backups
            manual_backups = [b for b in backups if not b["is_auto"]]
            
            if len(manual_backups) <= keep_count:
                _LOGGER.debug(f"Only {len(manual_backups)} backups, no cleanup needed")
                return 0
            
            # Delete oldest backups
            to_delete = manual_backups[keep_count:]
            deleted_count = 0
            
            for backup in to_delete:
                if await self.delete_backup(backup["path"]):
                    deleted_count += 1
            
            _LOGGER.info(f"🧹 Cleaned up {deleted_count} old backups")
            return deleted_count
            
        except Exception as e:
            _LOGGER.error(f"❌ Failed to cleanup old backups: {e}")
            return 0

    async def get_backup_info(self, backup_path: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific backup.
        
        Args:
            backup_path: Path to backup file
            
        Returns:
            Backup information dictionary or None if not found
        """
        try:
            backup_file = Path(backup_path)
            
            if not backup_file.exists():
                return None
            
            data = await self._read_json_file(backup_file)
            stat = backup_file.stat()
            
            return {
                "name": backup_file.name,
                "path": str(backup_file),
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "version": data.get("version", "unknown"),
                "device_count": len(data.get("devices", {})),
                "devices": list(data.get("devices", {}).keys()),
            }
            
        except Exception as e:
            _LOGGER.error(f"❌ Failed to get backup info: {e}")
            return None

    async def _copy_file(self, src: Path, dst: Path) -> None:
        """Copy file asynchronously."""
        def _copy():
            shutil.copy2(src, dst)
        await asyncio.get_event_loop().run_in_executor(None, _copy)

    async def _read_json_file(self, file_path: Path) -> Dict[str, Any]:
        """Read JSON file asynchronously."""
        def _read_file():
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return await asyncio.get_event_loop().run_in_executor(None, _read_file)
