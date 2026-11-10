"""BaoBun updating itself on the server PC.

deploy_to_host.bat copies a new BaoBunCP.exe over the running one (Windows lets it
rename the running file out of the way). The agent keeps running the old program
until it restarts, so it watches its own exe:

* status() says whether a newer build is sitting on disk
* restart_agent() starts the new agent and lets this one go - game servers keep
  running and the new agent takes them over. Servers that are controlled through
  their console window (Vintage Story, Minecraft, Project Zomboid, Source games...)
  lose that console until they are restarted; RCON games keep it.
* the agent applies an update by itself when nobody is playing and no
  console-controlled server is running (setting self_update: "when_idle", the
  default), or only by hand ("manual"). A nightly PC restart always picks it up.

Outside the frozen exe (running from source) there's nothing to watch.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional


def exe_path() -> Optional[str]:
    return sys.executable if getattr(sys, "frozen", False) else None


def _fingerprint(path: str) -> Optional[dict]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return {"size": st.st_size, "mtime": st.st_mtime, "sha1": h.hexdigest()}


class Watcher:
    """Remembers the exe this process started from and notices a newer one."""

    def __init__(self, path: Optional[str] = None):
        self.path = path if path is not None else exe_path()
        self.started = _fingerprint(self.path) if self.path else None
        self._seen = None
        self._result = {"ready": False}

    def check(self) -> dict:
        if not self.path or not self.started:
            return {"ready": False, "supported": False}
        try:
            st = os.stat(self.path)
        except OSError:
            return {"ready": False, "supported": True}
        key = (st.st_size, st.st_mtime)
        if key == (self.started["size"], self.started["mtime"]):
            self._result = {"ready": False, "supported": True}
        elif key != self._seen:
            # only hash when the file changed, and wait until the copy has settled
            if time.time() - st.st_mtime < 20 and st.st_mtime > self.started["mtime"]:
                return {"ready": False, "supported": True, "copying": True}
            fp = _fingerprint(self.path)
            self._seen = key
            ready = bool(fp and fp["sha1"] != self.started["sha1"])
            self._result = {"ready": ready, "supported": True}
            if ready:
                self._result["built"] = fp["mtime"]
        out = dict(self._result)
        out["running_built"] = self.started["mtime"]
        return out


def console_servers(mgr) -> List[str]:
    """Running servers that would lose their console if the agent restarted."""
    out = []
    try:
        for st in mgr.get_all_statuses():
            if st.get("isRunning") and st.get("consoleVia") == "stdin":
                out.append(st.get("name") or st["id"])
    except Exception:
        pass
    return out


def anyone_playing(mgr) -> bool:
    try:
        return any(st.get("isRunning") and (st.get("playerCount") or 0) > 0 for st in mgr.get_all_statuses())
    except Exception:
        return True


def idle_enough(mgr) -> bool:
    return not anyone_playing(mgr) and not console_servers(mgr)


def fmt_time(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%a %d %b %H:%M")
    except Exception:
        return "?"


def restart_agent(base_dir) -> bool:
    """Launch a fresh agent from the (new) exe a few seconds from now, detached, so
    it starts after this one has let go of its ports."""
    exe = exe_path()
    if not exe:
        return False
    if os.name == "nt":
        cmd = 'ping -n 6 127.0.0.1 >nul & start "" "%s" --agent' % exe
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(["cmd", "/c", cmd], cwd=str(base_dir), creationflags=flags, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    else:
        subprocess.Popen(["sh", "-c", 'sleep 5; "%s" --agent' % exe], cwd=str(base_dir),
                         start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    return True


def note_version(base_dir, started: Optional[dict]) -> Optional[str]:
    """Remember which build ran last; returns a "BaoBun was updated" line when it changed."""
    if not started:
        return None
    p = Path(base_dir) / "runtime" / "version.json"
    try:
        old = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        old = {}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"sha1": started["sha1"], "built": started["mtime"]}), encoding="utf-8")
    except Exception:
        pass
    if old.get("sha1") and old["sha1"] != started["sha1"]:
        return "BaoBun on the server PC was updated (build from %s)." % fmt_time(started["mtime"])
    return None
