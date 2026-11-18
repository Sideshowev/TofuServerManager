"""The app used to be called "BaoBun Control Panel". Tofu Server Manager still reads
what that name left on a PC, and moves it over where it can:

* the data folder %LOCALAPPDATA%\\BaoBun (used when the program sits in a folder it
  can't write to, and for SteamCMD)      -> %LOCALAPPDATA%\\TofuSM   (paths.py)
* settings\\baobun.db                     -> settings\\tofusm.db       (move_db)
* each server's logs\\baobun-<id>.log      -> logs\\tofusm-<id>.log     (move_logs)
* backups made before keep "baobun_backup.json" inside      (OLD_MANIFEST)
* pairing codes baobun://..., linked-PC and agent request headers, exported
  server profiles, plugin.json "min_baobun_version" are all still accepted
* the "start with Windows" entry and the start-at-boot task point at the old
  program file, so they are made again for the new one (app.py, boot_task.py)

Nothing here deletes data; a folder or file that can't be moved (in use) is simply
used where it is.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

OLD_NAME = "BaoBun"
OLD_DATA_DIR = "BaoBun"                 # under %LOCALAPPDATA%
OLD_HOME_ENV = "BAOBUN_HOME"
OLD_MANIFEST = "baobun_backup.json"
OLD_PROFILE_KEY = "_baoBunProfile"
OLD_RUN_KEY = "BaoBunAgent"
OLD_TASK_NAME = "BaoBun Agent"
OLD_CODE_PREFIX = "baobun://"
OLD_HEADER = "X-BaoBun-"
OLD_LOG_MARK = "==== BaoBun: starting"
MANIFESTS = ("tofusm_backup.json", OLD_MANIFEST)


def local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))


def appdata_folder(new: str = "TofuSM") -> Path:
    """%LOCALAPPDATA%\\TofuSM, moved over from %LOCALAPPDATA%\\BaoBun the first time.
    If the old folder can't be moved (something still has a file open), it is used as it is."""
    base = local_appdata()
    target, old = base / new, base / OLD_DATA_DIR
    if not target.exists() and old.is_dir():
        try:
            os.rename(old, target)
        except OSError:
            return old
    return target


def move_db(settings_dir: Path) -> Path:
    new, old = Path(settings_dir) / "tofusm.db", Path(settings_dir) / "baobun.db"
    if not new.exists() and old.exists():
        try:
            for suffix in ("", "-wal", "-shm"):
                if Path(str(old) + suffix).exists():
                    os.rename(str(old) + suffix, str(new) + suffix)
        except OSError:
            return old
    return new


def move_logs(servers) -> int:
    """logs\\baobun-<id>.log (and .1 .2 .3) -> logs\\tofusm-<id>.log, where the new one isn't there."""
    moved = 0
    for srv in servers or []:
        d, sid = (srv or {}).get("directory"), (srv or {}).get("id")
        if not d or not sid:
            continue
        logs = Path(d) / "logs"
        for suffix in ("", ".1", ".2", ".3"):
            old, new = logs / ("baobun-%s.log%s" % (sid, suffix)), logs / ("tofusm-%s.log%s" % (sid, suffix))
            if old.is_file() and not new.exists():
                try:
                    os.rename(old, new)
                    moved += 1
                except OSError:
                    pass
    return moved


def header(headers, name: str, default: str = "") -> str:
    """X-TofuSM-<name>, else the old X-BaoBun-<name>."""
    return headers.get("X-TofuSM-" + name) or headers.get(OLD_HEADER + name) or default


def both_headers(values: dict) -> dict:
    """{"Ts": ..} -> both header spellings, so a PC still on the old version understands too."""
    out = {}
    for k, v in values.items():
        out["X-TofuSM-" + k] = v
        out[OLD_HEADER + k] = v
    return out


LOG_MARKS = ("==== Tofu: starting", OLD_LOG_MARK)
LOG_MARK_RX = re.compile(r"==== (?:Tofu|BaoBun): starting")
