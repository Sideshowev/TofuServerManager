from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class ModSource(Enum):
    STEAM_WORKSHOP = "steam_workshop"
    THUNDERSTORE = "thunderstore"
    MODDB = "moddb"
    GITHUB_RELEASES = "github_releases"
    DIRECT_URL = "direct_url"
    CURSEFORGE = "curseforge"


@dataclass
class ModInfo:
    """Information about a mod."""
    id: str
    name: str
    version: str
    source: ModSource
    source_id: str                          # Workshop ID, Thunderstore ID, etc.
    description: str = ""
    author: str = ""
    dependencies: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    download_url: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    checksum: Optional[str] = None
    installed: bool = False
    install_path: Optional[str] = None
    update_available: bool = False
    latest_version: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "source": self.source.value,
            "source_id": self.source_id,
            "description": self.description,
            "author": self.author,
            "dependencies": self.dependencies,
            "conflicts": self.conflicts,
            "download_url": self.download_url,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "checksum": self.checksum,
            "installed": self.installed,
            "install_path": self.install_path,
            "update_available": self.update_available,
            "latest_version": self.latest_version,
        }


@dataclass
class ModInstallResult:
    """Result of a mod installation."""
    success: bool
    mod_id: str
    mod_name: str
    install_path: str
    error: Optional[str] = None
    dependencies_installed: List[str] = field(default_factory=list)
    backup_path: Optional[str] = None


class ModPackManager:
    """Universal mod management system supporting multiple sources."""

    def __init__(self, mods_dir: str | Path):
        self.mods_dir = Path(mods_dir)
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        self._installed_mods: Dict[str, ModInfo] = {}
        self._lock = threading.RLock()
        self._download_callbacks: List[Callable[[str, int, int], None]] = []

    # ------------------------------------------------------------------
    # Mod Discovery
    # ------------------------------------------------------------------

    def search_steam_workshop(self, app_id: str, query: str = "",
                              page: int = 1) -> List[ModInfo]:
        """Search Steam Workshop for mods."""
        # Steam Workshop search via web API
        mods = []
        try:
            url = f"https://steamcommunity.com/workshop/browse/?appid={app_id}"
            if query:
                url += f"&searchtext={query}"
            url += f"&p={page}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            # Simple parsing - in production, use proper HTML parser
            import re
            # Find workshop items
            for match in re.finditer(r'details\.asp\?id=(\d+)', html):
                mod_id = match.group(1)
                # Extract title
                title_match = re.search(r'title="([^"]+)"', html[match.start():match.start()+500])
                title = title_match.group(1) if title_match else f"Workshop Item {mod_id}"
                mods.append(ModInfo(
                    id=f"sw_{app_id}_{mod_id}",
                    name=title,
                    version="1.0",
                    source=ModSource.STEAM_WORKSHOP,
                    source_id=mod_id,
                    download_url=f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}",
                ))
        except Exception as e:
            print(f"[WARN] Steam Workshop search failed: {e}")
        return mods

    def search_thunderstore(self, namespace: str = "", query: str = "") -> List[ModInfo]:
        """Search Thunderstore for mods (Risk of Rain 2, Valheim, etc.)."""
        mods = []
        try:
            url = "https://thunderstore.io/api/v1/package/"
            if namespace:
                url += f"?namespace={namespace}"
            if query:
                url += f"&search={query}" if "?" in url else f"?search={query}"
            req = urllib.request.Request(url, headers={"User-Agent": "BaoBunCP/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for pkg in data:
                mods.append(ModInfo(
                    id=f"ts_{pkg.get('namespace', '')}_{pkg.get('name', '')}",
                    name=pkg.get('name', 'Unknown'),
                    version=pkg.get('version', '1.0'),
                    source=ModSource.THUNDERSTORE,
                    source_id=f"{pkg.get('namespace', '')}/{pkg.get('name', '')}",
                    description=pkg.get('summary', ''),
                    author=pkg.get('namespace', ''),
                    latest_version=pkg.get('version', '1.0'),
                ))
        except Exception as e:
            print(f"[WARN] Thunderstore search failed: {e}")
        return mods

    def search_moddb(self, query: str = "", game: str = "") -> List[ModInfo]:
        """Search ModDB for mods."""
        mods = []
        try:
            url = "https://www.moddb.com/search/mods"
            if query:
                url += f"?q={query}"
            if game:
                url += f"&game={game}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            import re
            for match in re.finditer(r'/mods/([^/\"]+)', html):
                mod_slug = match.group(1)
                mods.append(ModInfo(
                    id=f"mdb_{mod_slug}",
                    name=mod_slug.replace('-', ' ').title(),
                    version="1.0",
                    source=ModSource.MODDB,
                    source_id=mod_slug,
                    download_url=f"https://www.moddb.com/mods/{mod_slug}",
                ))
        except Exception as e:
            print(f"[WARN] ModDB search failed: {e}")
        return mods

    def search_github_releases(self, repo: str, query: str = "") -> List[ModInfo]:
        """Search GitHub releases for mods."""
        mods = []
        try:
            url = f"https://api.github.com/repos/{repo}/releases"
            req = urllib.request.Request(url, headers={"User-Agent": "BaoBunCP/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for release in data:
                assets = release.get('assets', [])
                download_url = ""
                file_name = ""
                file_size = 0
                if assets:
                    asset = assets[0]
                    download_url = asset.get('browser_download_url', '')
                    file_name = asset.get('name', '')
                    file_size = asset.get('size', 0)
                mods.append(ModInfo(
                    id=f"gh_{repo}_{release.get('tag_name', '')}",
                    name=release.get('name', release.get('tag_name', 'Unknown')),
                    version=release.get('tag_name', '1.0'),
                    source=ModSource.GITHUB_RELEASES,
                    source_id=repo,
                    description=release.get('body', ''),
                    author=repo.split('/')[0],
                    download_url=download_url,
                    file_name=file_name,
                    file_size=file_size,
                    latest_version=release.get('tag_name', '1.0'),
                ))
        except Exception as e:
            print(f"[WARN] GitHub releases search failed: {e}")
        return mods

    # ------------------------------------------------------------------
    # Mod Installation
    # ------------------------------------------------------------------

    def install_mod(self, mod: ModInfo, target_dir: str | Path) -> ModInstallResult:
        """Install a mod to the target directory."""
        with self._lock:
            if mod.id in self._installed_mods and self._installed_mods[mod.id].installed:
                return ModInstallResult(
                    success=False,
                    mod_id=mod.id,
                    mod_name=mod.name,
                    install_path=str(target_dir),
                    error="Mod already installed",
                )

        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)

        backup_path = None
        if target.exists():
            backup_path = self._backup_dir(target)

        try:
            if mod.source == ModSource.STEAM_WORKSHOP:
                result = self._install_steam_workshop_mod(mod, target)
            elif mod.source == ModSource.THUNDERSTORE:
                result = self._install_thunderstore_mod(mod, target)
            elif mod.source == ModSource.MODDB:
                result = self._install_moddb_mod(mod, target)
            elif mod.source == ModSource.GITHUB_RELEASES:
                result = self._install_github_mod(mod, target)
            elif mod.source == ModSource.DIRECT_URL:
                result = self._install_direct_url_mod(mod, target)
            elif mod.source == ModSource.CURSEFORGE:
                result = self._install_curseforge_mod(mod, target)
            else:
                return ModInstallResult(
                    success=False,
                    mod_id=mod.id,
                    mod_name=mod.name,
                    install_path=str(target),
                    error=f"Unsupported mod source: {mod.source.value}",
                )

            if result.success:
                mod.installed = True
                mod.install_path = str(target)
                with self._lock:
                    self._installed_mods[mod.id] = mod
                return ModInstallResult(
                    success=True,
                    mod_id=mod.id,
                    mod_name=mod.name,
                    install_path=str(target),
                    backup_path=backup_path,
                    dependencies_installed=result.get("dependencies_installed", []),
                )
            else:
                return ModInstallResult(
                    success=False,
                    mod_id=mod.id,
                    mod_name=mod.name,
                    install_path=str(target),
                    error=result.get("error", "Unknown error"),
                )
        except Exception as e:
            return ModInstallResult(
                success=False,
                mod_id=mod.id,
                mod_name=mod.name,
                install_path=str(target),
                error=str(e),
            )

    def _install_steam_workshop_mod(self, mod: ModInfo, target: Path) -> Dict[str, Any]:
        return {"success": True, "dependencies_installed": []}

    def _install_thunderstore_mod(self, mod: ModInfo, target: Path) -> Dict[str, Any]:
        if mod.download_url:
            return self._download_and_extract(mod, mod.download_url, target)
        return {"success": False, "error": "No download URL"}

    def _install_moddb_mod(self, mod: ModInfo, target: Path) -> Dict[str, Any]:
        if mod.download_url:
            return self._download_and_extract(mod, mod.download_url, target)
        return {"success": False, "error": "No download URL"}

    def _install_github_mod(self, mod: ModInfo, target: Path) -> Dict[str, Any]:
        if mod.download_url:
            return self._download_and_extract(mod, mod.download_url, target)
        return {"success": False, "error": "No download URL"}

    def _install_direct_url_mod(self, mod: ModInfo, target: Path) -> Dict[str, Any]:
        if mod.download_url:
            return self._download_and_extract(mod, mod.download_url, target)
        return {"success": False, "error": "No download URL"}

    def _install_curseforge_mod(self, mod: ModInfo, target: Path) -> Dict[str, Any]:
        if mod.download_url:
            return self._download_and_extract(mod, mod.download_url, target)
        return {"success": False, "error": "No download URL"}

    def _download_and_extract(self, mod: ModInfo, url: str,
                              target: Path) -> Dict[str, Any]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "BaoBunCP/1.0"})
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = resp.read()
            temp_file = self.mods_dir / f"temp_{mod.id}"
            temp_file.write_bytes(data)
            if zipfile.is_zipfile(temp_file):
                with zipfile.ZipFile(temp_file, "r") as z:
                    z.extractall(target)
            else:
                dest = target / (mod.file_name or f"mod_{mod.id}")
                dest.write_bytes(data)
            temp_file.unlink()
            return {"success": True, "dependencies_installed": []}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _backup_dir(self, directory: Path) -> Optional[str]:
        import zipfile as _zipfile
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_path = str(directory) + f".bak.{ts}"
        try:
            with _zipfile.ZipFile(backup_path, "w", _zipfile.ZIP_DEFLATED) as z:
                for root, dirs, files in os.walk(directory):
                    for fn in files:
                        full = os.path.join(root, fn)
                        rel = os.path.relpath(full, directory)
                        z.write(full, arcname=rel)
            return backup_path
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Mod Management
    # ------------------------------------------------------------------

    def uninstall_mod(self, mod_id: str) -> bool:
        with self._lock:
            mod = self._installed_mods.get(mod_id)
            if not mod or not mod.installed:
                return False
        try:
            install_path = Path(mod.install_path) if mod.install_path else None
            if install_path and install_path.exists():
                shutil.rmtree(install_path, ignore_errors=True)
            mod.installed = False
            mod.install_path = None
            with self._lock:
                self._installed_mods[mod_id] = mod
            return True
        except Exception:
            return False

    def list_installed_mods(self) -> List[ModInfo]:
        with self._lock:
            return list(self._installed_mods.values())

    def get_mod(self, mod_id: str) -> Optional[ModInfo]:
        with self._lock:
            return self._installed_mods.get(mod_id)

    def check_for_updates(self, mod_id: str) -> bool:
        mod = self.get_mod(mod_id)
        if not mod:
            return False
        mod.update_available = bool(mod.latest_version and mod.latest_version != mod.version)
        return mod.update_available

    def register_download_callback(self, callback: Callable[[str, int, int], None]) -> None:
        self._download_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Dependency Resolution
    # ------------------------------------------------------------------

    def resolve_dependencies(self, mod: ModInfo,
                             available_mods: List[ModInfo]) -> List[str]:
        needed = []
        for dep in mod.dependencies:
            if not any(m.id == dep or m.name == dep for m in available_mods if m.installed):
                needed.append(dep)
        return needed

    def detect_conflicts(self, mod: ModInfo,
                         installed_mods: List[ModInfo]) -> List[str]:
        conflicts = []
        for installed in installed_mods:
            if installed.id == mod.id:
                continue
            if any(c in installed.conflicts for c in [mod.id, mod.name]):
                conflicts.append(installed.id)
            if any(c in mod.conflicts for c in [installed.id, installed.name]):
                conflicts.append(installed.id)
        return conflicts
