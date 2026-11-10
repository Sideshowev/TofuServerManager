"""Who plays what, and when: minutes per player per server per day, plus how many
people were on at each hour of the week. Recorded by the Watchdog once a minute
from the player lists games report; kept for KEEP_DAYS days in
runtime/player_stats.json. Used by the Discord /stats command.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

KEEP_DAYS = 90


class PlayerStats:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._data = None
        self._dirty = 0

    def _load(self) -> dict:
        if self._data is None:
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}
            self._data.setdefault("days", {})       # "YYYY-MM-DD" -> {sid: {player: minutes}}
            self._data.setdefault("hours", {})      # "weekday-hour" -> [player-minutes, samples]
            self._data.setdefault("names", {})      # sid -> server name (for old servers)
        return self._data

    def save(self):
        with self._lock:
            if self._data is None:
                return
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
            os.replace(tmp, self.path)
            self._dirty = 0

    def record(self, statuses: List[dict], now: Optional[float] = None, minutes: float = 1.0):
        """One sample: every running server's player list counts ``minutes`` for each name."""
        now = time.time() if now is None else now
        dt = datetime.fromtimestamp(now)
        day = dt.strftime("%Y-%m-%d")
        hkey = "%d-%d" % (dt.weekday(), dt.hour)
        with self._lock:
            d = self._load()
            total = 0
            for st in statuses:
                if not st.get("isRunning"):
                    continue
                names = [str(p.get("name") if isinstance(p, dict) else p) for p in st.get("players") or []]
                names = [re.sub(r"[\x00-\x1f]", "", n)[:40] for n in names if n and n != "None"]
                count = len(names) if names else int(st.get("playerCount") or 0)
                total += count
                if not names:
                    continue
                srv = d["days"].setdefault(day, {}).setdefault(st["id"], {})
                for n in names:
                    srv[n] = round(srv.get(n, 0) + minutes, 2)
                d["names"][st["id"]] = st.get("name") or st["id"]
            h = d["hours"].setdefault(hkey, [0, 0])
            h[0] += total * minutes
            h[1] += 1
            cutoff = (dt - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
            for k in [k for k in d["days"] if k < cutoff]:
                del d["days"][k]
            self._dirty += 1
        if self._dirty >= 10:
            self.save()

    def summary(self, days: int = 7, now: Optional[float] = None, server_id: Optional[str] = None) -> dict:
        now = time.time() if now is None else now
        start = (datetime.fromtimestamp(now) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        with self._lock:
            d = self._load()
            players: Dict[str, float] = {}
            servers: Dict[str, float] = {}
            for day, per in d["days"].items():
                if day < start:
                    continue
                for sid, who in per.items():
                    if server_id and sid != server_id:
                        continue
                    for n, mins in who.items():
                        players[n] = players.get(n, 0) + mins
                        servers[sid] = servers.get(sid, 0) + mins
            busiest = None
            best = 0.0
            for k, (pm, samples) in d["hours"].items():
                if samples and pm / samples > best:
                    best, busiest = pm / samples, k
            names = dict(d["names"])
        top = sorted(players.items(), key=lambda x: -x[1])
        srv = sorted(servers.items(), key=lambda x: -x[1])
        peak = None
        if busiest:
            wd, hr = (int(x) for x in busiest.split("-"))
            peak = {"weekday": wd, "hour": hr, "avg_players": round(best, 1)}
        return {"days": days, "players": [{"name": n, "hours": round(m / 60, 1)} for n, m in top],
                "servers": [{"id": s, "name": names.get(s, s), "hours": round(m / 60, 1)} for s, m in srv],
                "peak": peak, "total_hours": round(sum(players.values()) / 60, 1)}


def format_summary(s: dict, server_name: Optional[str] = None) -> str:
    """Discord text for /stats."""
    days = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"]
    head = "**Last %d days%s** - %s hours played" % (s["days"], (" on " + server_name) if server_name else "",
                                                      s["total_hours"])
    if not s["players"]:
        return head + "\nNo one has played yet (or the game doesn't report player names)."
    lines = [head, "", "**Top players**"]
    medals = ["\U0001F947", "\U0001F948", "\U0001F949"]
    for i, p in enumerate(s["players"][:10]):
        lines.append("%s %s - %s h" % (medals[i] if i < 3 else "%d." % (i + 1), _safe(p["name"]), p["hours"]))
    if not server_name and len(s["servers"]) > 1:
        lines += ["", "**Busiest servers**"]
        for x in s["servers"][:6]:
            lines.append("• %s - %s h" % (_safe(x["name"]), x["hours"]))
    if s.get("peak"):
        h = s["peak"]["hour"]
        lines += ["", "Busiest time: %s around %s (server PC time)" % (
            days[s["peak"]["weekday"]], datetime(2000, 1, 1, h).strftime("%I %p").lstrip("0"))]
    return "\n".join(lines)


def _safe(s: str) -> str:
    return re.sub(r"[*_`~|>@]", "", str(s))
