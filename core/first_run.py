"""First-run setup: what the welcome walkthrough needs to know and save.

The desktop walkthrough (ui/first_run.py) shows once, on a fresh install: it
explains where BaoBun keeps its data, looks at this PC (cores, memory, free
space), and asks the few things that matter before the first server: where
servers get installed, how many backups to keep and where to copy them, and
whether BaoBun keeps the servers running in the background and starts with
Windows. Discord, the web panel and the status page are offered, never forced.

Nothing here assumes a particular PC: defaults come from this PC's drives and
the user's own folders. Existing installs (servers already set up) never see it.
"""

from __future__ import annotations

import os
import shutil
import time
from typing import Dict, List, Optional

DONE_KEY = "setup_done"


def needed(mgr) -> bool:
    """Show the walkthrough? Only on a fresh install that hasn't finished it."""
    if mgr.get_setting(DONE_KEY):
        return False
    try:
        if mgr.load_servers():
            mark_done(mgr, skipped=True)     # an existing install: don't bother them
            return False
    except Exception:
        pass
    return True


def mark_done(mgr, skipped: bool = False):
    mgr.set_setting(DONE_KEY, {"at": time.time(), "skipped": bool(skipped)})


def hardware() -> dict:
    """{"cores", "threads", "ram_gb", "cpu"} for the welcome page (best effort)."""
    out = {"threads": os.cpu_count() or 0, "cores": None, "ram_gb": None, "cpu": None}
    try:
        import psutil
        out["cores"] = psutil.cpu_count(logical=False)
        out["ram_gb"] = round(psutil.virtual_memory().total / 1024 ** 3)
    except Exception:
        pass
    try:
        import platform
        out["cpu"] = platform.processor() or None
    except Exception:
        pass
    return out


def hardware_text(hw: dict) -> str:
    bits = []
    if hw.get("cores") and hw.get("threads"):
        bits.append("%d cores / %d threads" % (hw["cores"], hw["threads"]))
    elif hw.get("threads"):
        bits.append("%d CPU threads" % hw["threads"])
    if hw.get("ram_gb"):
        bits.append("%d GB memory" % hw["ram_gb"])
    text = ", ".join(bits) or "couldn't read this PC's hardware"
    hint = rough_capacity(hw)
    return text + (" - " + hint if hint else "")


def rough_capacity(hw: dict) -> str:
    """A friendly sense of scale, not a promise (games differ a lot)."""
    ram = hw.get("ram_gb") or 0
    if not ram:
        return ""
    if ram < 12:
        return "enough for one or two light servers (Valheim, Terraria, small Minecraft)."
    if ram < 32:
        return "room for a few servers at once; big ones like ARK want 12+ GB each."
    if ram < 96:
        return "room for several servers at once, including a big one or two."
    return "room for lots of servers at once, even an ARK cluster."


def drives(min_free_gb: float = 0) -> List[dict]:
    """Fixed drives with free space, biggest free first: [{"path", "free_gb", "total_gb"}]."""
    out = []
    paths: List[str] = []
    try:
        import psutil
        for p in psutil.disk_partitions(all=False):
            if os.name == "nt" and ("cdrom" in (p.opts or "") or not p.fstype):
                continue
            paths.append(p.mountpoint)
    except Exception:
        paths = [os.path.splitdrive(os.getcwd())[0] + os.sep if os.name == "nt" else "/"]
    seen = set()
    for p in paths:
        try:
            u = shutil.disk_usage(p)
        except OSError:
            continue
        if p in seen:
            continue
        seen.add(p)
        free = u.free / 1024 ** 3
        if free >= min_free_gb:
            out.append({"path": p, "free_gb": round(free, 1), "total_gb": round(u.total / 1024 ** 3, 1)})
    out.sort(key=lambda d: -d["free_gb"])
    return out


def suggest_install_dir(mgr) -> str:
    """Current setting, or "GameServers" on the drive with the most free space."""
    cur = (mgr.get_setting("default_install_dir", "") or "").strip()
    if cur:
        return cur
    if os.name == "nt":
        ds = drives()
        if ds:
            return os.path.join(ds[0]["path"], "GameServers")
    return os.path.join(os.path.expanduser("~"), "GameServers")


def free_gb(path: str) -> Optional[float]:
    """Free space where ``path`` is (or would be)."""
    p = os.path.abspath(path or ".")
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    try:
        return round(shutil.disk_usage(p).free / 1024 ** 3, 1)
    except OSError:
        return None


def check_install_dir(path: str) -> Optional[str]:
    """None when fine, or what's wrong in plain words."""
    path = (path or "").strip()
    if not path:
        return "Pick a folder."
    if not os.path.isabs(path):
        return "Use a full folder path (like D:\\GameServers)."
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".baobun-write-test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        return "BaoBun can't write there (%s)." % (e.strerror or e)
    free = free_gb(path)
    if free is not None and free < 20:
        return "Only %.0f GB free there - game servers often need 10-100 GB each." % free
    return None


def defaults(mgr) -> Dict[str, object]:
    """What the walkthrough starts with."""
    try:
        keep = int(mgr.get_setting("backup_keep", 10) or 10)
    except (TypeError, ValueError):
        keep = 10
    return {"install_dir": suggest_install_dir(mgr), "backup_keep": keep,
            "backup_mirror_dir": mgr.get_setting("backup_mirror_dir", "") or "",
            "watchdog": mgr.get_setting("use_background_agent", True) is not False}


def apply(mgr, values: Dict[str, object]) -> List[str]:
    """Save the walkthrough's answers. Returns what changed (for the summary)."""
    done = []
    d = str(values.get("install_dir") or "").strip()
    if d:
        mgr.set_setting("default_install_dir", d)
        done.append("New servers install into %s" % d)
    if values.get("backup_keep") not in (None, ""):
        try:
            keep = max(1, min(100, int(values["backup_keep"])))
            mgr.set_setting("backup_keep", keep)
            done.append("Keeping the %d newest backups of each server" % keep)
        except (TypeError, ValueError):
            pass
    if "backup_mirror_dir" in values:
        m = str(values.get("backup_mirror_dir") or "").strip()
        mgr.set_setting("backup_mirror_dir", m)
        if m:
            done.append("Backups are also copied to %s" % m)
    if "watchdog" in values:
        on = bool(values["watchdog"])
        mgr.set_setting("use_background_agent", on)
        done.append("Watchdog %s" % ("on: servers keep running when the window closes" if on else
                                     "off: servers run from the BaoBun window"))
    return done
