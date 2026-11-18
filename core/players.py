"""Who is online, for games without a query protocol (Vintage Story, ...).

Follows the server's console log (Tofu's capture, which starts every run with a
"==== Tofu: starting" line) or else the game's own log, and replays join /
leave lines from the start of the current run. Reads only new bytes each time.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Dict, List, Optional

MARKER = b"==== Tofu: starting"
OLD_MARKER = b"==== BaoBun: starting"          # logs written before the rename
MAX_SCAN = 4 * 1024 * 1024


class LogPlayers:
    def __init__(self):
        self._state: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def forget(self, server_id: str):
        with self._lock:
            self._state.pop(server_id, None)

    def update(self, server_id: str, path: Optional[str], patterns: dict,
               run_started: Optional[float] = None) -> Optional[Dict[str, float]]:
        """{name: seconds_online_since} for the current run, or None if unknown."""
        if not path or not os.path.isfile(path) or not patterns:
            return None
        joins = [re.compile(p) for p in patterns.get("join") or []]
        leaves = [re.compile(p) for p in patterns.get("leave") or []]
        with self._lock:
            st = self._state.get(server_id)
            size = os.path.getsize(path)
            if (st is None or st["path"] != path or size < st["offset"]
                    or (run_started and st.get("run") != run_started)):
                st = {"path": path, "offset": 0, "names": {}, "run": run_started}
                start = max(0, size - MAX_SCAN)
                with open(path, "rb") as f:
                    f.seek(start)
                    data = f.read()
                cut = max(data.rfind(MARKER), data.rfind(OLD_MARKER))
                if cut >= 0:
                    data = data[cut:]
                elif start > 0:
                    data = data[data.find(b"\n") + 1:]
                st["offset"] = size
                self._feed(st, data, joins, leaves)
                self._state[server_id] = st
            elif size > st["offset"]:
                with open(path, "rb") as f:
                    f.seek(st["offset"])
                    data = f.read(min(size - st["offset"], MAX_SCAN))
                nl = data.rfind(b"\n")
                if nl < 0:
                    return dict(st["names"])
                st["offset"] += nl + 1
                self._feed(st, data[:nl + 1], joins, leaves)
            return dict(st["names"])

    @staticmethod
    def _feed(st, data: bytes, joins, leaves):
        now = time.time()
        for raw in data.decode("utf-8", errors="replace").splitlines():
            if MARKER.decode() in raw or OLD_MARKER.decode() in raw:
                st["names"] = {}
                continue
            hit = None
            for rx in leaves:
                m = rx.search(raw)
                if m:
                    hit = ("leave", m.group("name"))
                    break
            if hit is None:
                for rx in joins:
                    m = rx.search(raw)
                    if m:
                        hit = ("join", m.group("name"))
                        break
            if hit is None:
                continue
            name = hit[1].strip().rstrip(".,")
            if not name or len(name) > 40:
                continue
            if hit[0] == "join":
                st["names"].setdefault(name, now)
            else:
                st["names"].pop(name, None)


def merge_since(old: List[dict], names: List[dict]) -> List[dict]:
    """Keep each player's first-seen time across polls (for "online for 2h")."""
    prev = {p.get("name"): p.get("since") for p in old or [] if isinstance(p, dict)}
    now = time.time()
    out = []
    for p in names:
        n = p.get("name")
        since = prev.get(n) or (now - p["seconds"] if p.get("seconds") else now)
        out.append({"name": n, "since": since})
    return out
