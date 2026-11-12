"""Central path resolution for BaoBun (works from source and from the PyInstaller exe).

Everything that used to do ``Path(__file__).resolve().parents[1]`` should use
these helpers instead: inside a onefile exe ``__file__`` points into a temporary
extraction folder, so data files (servers.json, settings.json, ...) would be
looked up in the wrong place.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def base_dir() -> Path:
    """Folder that holds the user's data files (servers.json, settings.json, ...).

    Source: the project folder. Exe: the exe's folder, unless a ``data_dir.txt``
    next to the exe names another folder (relative paths are relative to the
    exe), so dist\\BaoBunCP.exe and start.bat can share one set of files. If the
    exe's folder can't be written to (e.g. Program Files), %LOCALAPPDATA%\\BaoBun.
    The BAOBUN_HOME environment variable overrides both.
    """
    env = os.environ.get("BAOBUN_HOME")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        pointer = exe_dir / "data_dir.txt"
        if pointer.is_file():
            try:
                target = pointer.read_text(encoding="utf-8").strip().strip('"')
                if target:
                    cand = Path(target)
                    cand = (cand if cand.is_absolute() else exe_dir / cand).resolve()
                    if cand.is_dir():
                        return cand
            except OSError:
                pass
        if _writable(exe_dir):
            return exe_dir
        # e.g. installed under Program Files: keep the data in the user's own folder instead
        fallback = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "BaoBun"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback
        except OSError:
            return exe_dir
    return Path(__file__).resolve().parents[1]


_WRITABLE: dict = {}


def _writable(folder: Path) -> bool:
    key = str(folder)
    if key not in _WRITABLE:
        _WRITABLE[key] = _try_write(folder)
    return _WRITABLE[key]


def _try_write(folder: Path) -> bool:
    try:
        fd, name = tempfile.mkstemp(prefix=".baobun_write_test_", dir=str(folder))
        os.close(fd)
        os.remove(name)
        return True
    except OSError:
        return False


def bundle_dir() -> Path:
    """Folder that holds read-only bundled resources (assets, built-in plugins)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", str(Path(sys.executable).resolve().parent)))
    return Path(__file__).resolve().parents[1]


def servers_path() -> Path:
    return base_dir() / "servers.json"


def db_path() -> Path:
    return base_dir() / "settings" / "baobun.db"


def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
    """Write a file so a crash mid-write can never leave it half-written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, data) -> None:
    atomic_write_text(path, json.dumps(data, indent=2))
