from __future__ import annotations

import hashlib
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional


class FileManager:
    """File operations, backups, rollback, and integrity verification."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # File Operations
    # ------------------------------------------------------------------

    def read_file(self, path: str | Path, encoding: str = "utf-8") -> str:
        full = self.base_dir / path if not Path(path).is_absolute() else Path(path)
        return full.read_text(encoding=encoding, errors="replace")

    def write_file(self, path: str | Path, content: str, encoding: str = "utf-8",
                   backup: bool = True) -> Optional[str]:
        full = self.base_dir / path if not Path(path).is_absolute() else Path(path)
        backup_path = None
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_path = str(full) + f".bak.{ts}"
        shutil.copy2(full, backup_path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding=encoding)
        return backup_path

    def delete_file(self, path: str | Path, backup: bool = True) -> bool:
        full = self.base_dir / path if not Path(path).is_absolute() else Path(path)
        if not full.exists():
            return False
        if backup:
            ts = time.strftime("%Y%m%d_%H%M%S")
            backup_path = str(full) + f".bak.{ts}"
            shutil.move(str(full), backup_path)
        else:
            full.unlink()
        return True

    def list_files(self, directory: str | Path, pattern: str = "*",
                   recursive: bool = True) -> List[Dict[str, Any]]:
        full = self.base_dir / directory if not Path(directory).is_absolute() else Path(directory)
        if not full.exists() or not full.is_dir():
            return []
        results = []
        if recursive:
            for f in full.rglob(pattern):
                if f.is_file():
                    stat = f.stat()
                    results.append({
                        "path": str(f.relative_to(self.base_dir)),
                        "name": f.name,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                    })
        else:
            for f in full.iterdir():
                if f.is_file() and f.match(pattern):
                    stat = f.stat()
                    results.append({
                        "path": str(f.relative_to(self.base_dir)),
                        "name": f.name,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                    })
        return sorted(results, key=lambda x: x["name"])

    def directory_exists(self, directory: str | Path) -> bool:
        full = self.base_dir / directory if not Path(directory).is_absolute() else Path(directory)
        return full.exists() and full.is_dir()

    def create_directory(self, directory: str | Path) -> bool:
        full = self.base_dir / directory if not Path(directory).is_absolute() else Path(directory)
        try:
            full.mkdir(parents=True, exist_ok=True)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Checksums & Integrity
    # ------------------------------------------------------------------

    def checksum_file(self, path: str | Path, algorithm: str = "sha256") -> str:
        full = self.base_dir / path if not Path(path).is_absolute() else Path(path)
        h = hashlib.new(algorithm)
        with open(full, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def verify_checksum(self, path: str | Path, expected: str,
                        algorithm: str = "sha256") -> bool:
        try:
            return self.checksum_file(path, algorithm) == expected
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Backup & Rollback
    # ------------------------------------------------------------------

    def backup_file(self, path: str | Path) -> Optional[str]:
        """Create a timestamped backup of a file. Returns backup path or None."""
        full = self.base_dir / path if not Path(path).is_absolute() else Path(path)
        if not full.exists():
            return None
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_path = str(full) + f".bak.{ts}"
        shutil.copy2(full, backup_path)
        return backup_path

    def backup_directory(self, directory: str | Path,
                         dest_dir: str | Path,
                         exclude: Optional[List[str]] = None) -> Dict[str, Any]:
        """Create a zip backup of a directory."""
        full = self.base_dir / directory if not Path(directory).is_absolute() else Path(directory)
        if not full.exists() or not full.is_dir():
            raise FileNotFoundError(f"Directory not found: {directory}")
        dest = self.base_dir / dest_dir if not Path(dest_dir).is_absolute() else Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")

        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(directory))
        zip_path = dest / f"{ts}_{safe_name}.zip"
        exclude = exclude or []
        exclude_set = {e.lower() for e in exclude}
        count = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for root, dirs, files in os.walk(full):
                dirs[:] = [d for d in dirs if d.lower() not in exclude_set]
                for fn in files:
                    full_path = os.path.join(root, fn)
                    rel = os.path.relpath(full_path, full)
                    try:
                        if os.path.getsize(full_path) > 300 * 1024 * 1024:
                            continue
                    except OSError:
                        continue
                    z.write(full_path, arcname=rel)
                    count += 1
        return {
            "zip_path": str(zip_path),
            "name": zip_path.name,
            "files": count,
            "size": zip_path.stat().st_size,
        }

    def rollback_file(self, path: str | Path, backup_name: str) -> Dict[str, Any]:
        full = self.base_dir / path if not Path(path).is_absolute() else Path(path)
        backup = Path(backup_name)
        if not backup.is_absolute():
            backup = full.parent / backup_name
        if not backup.exists():
            return {"success": False, "error": "Backup not found"}
        shutil.copy2(backup, full)
        return {"success": True, "restored_from": str(backup)}

    def list_backups(self, path: str | Path) -> List[Dict[str, Any]]:
        full = self.base_dir / path if not Path(path).is_absolute() else Path(path)
        if not full.exists():
            return []
        backups = []
        for bak in sorted(full.parent.glob(f"{full.name}.bak.*")):
            stat = bak.stat()
            backups.append({
                "path": str(bak),
                "name": bak.name,
                "created": stat.st_mtime,
                "size": stat.st_size,
            })
        return sorted(backups, key=lambda b: b["created"], reverse=True)

    def get_disk_usage(self, path: str | Path = ".") -> Dict[str, Any]:
        full = self.base_dir / path if not Path(path).is_absolute() else Path(path)
        try:
            usage = shutil.disk_usage(full)
            return {
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": round(usage.used / usage.total * 100, 2),
            }
        except Exception:
            return {"total": 0, "used": 0, "free": 0, "percent": 0}
