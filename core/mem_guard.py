"""Memory caps per server: a clean restart when a game keeps using too much RAM.

Per server ``memLimitMB`` (0 / blank = off). The agent calls MemGuard.step() once a
minute with the server statuses. When a running server stays above its limit for
OVER_SAMPLES minutes in a row, BaoBun warns the players (5 and 1 minutes, skipped
when nobody is on), restarts it cleanly and posts to Discord with how fast its
memory was growing. At most one restart per server every COOLDOWN seconds, and
never during the nightly restart.

trend() turns the history samples into "grows about X MB per hour" for the graphs.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional

OVER_SAMPLES = 3
COOLDOWN = 30 * 60
WARN_MINUTES = [5, 1]


def limit_mb(srv: dict) -> float:
    try:
        return max(0.0, float(srv.get("memLimitMB") or 0))
    except (TypeError, ValueError):
        return 0.0


def _mb(v: float) -> str:
    return "%.1f GB" % (v / 1024) if v >= 1024 else "%d MB" % v


def trend(points: List[dict], min_minutes: float = 60) -> Optional[float]:
    """MB per hour from [{"ts", "ram"}] (least squares over the run since the last
    big drop, i.e. since the last restart). None if there isn't enough data."""
    pts = [(float(p["ts"]), float(p["ram"])) for p in points if p.get("ram")]
    if len(pts) < 4:
        return None
    start = 0
    for i in range(1, len(pts)):
        if pts[i][1] < pts[i - 1][1] * 0.5:      # restarted
            start = i
    pts = pts[start:]
    if len(pts) < 4 or pts[-1][0] - pts[0][0] < min_minutes * 60:
        return None
    n = len(pts)
    mx = sum(t for t, _ in pts) / n
    my = sum(r for _, r in pts) / n
    den = sum((t - mx) ** 2 for t, _ in pts)
    if not den:
        return None
    slope = sum((t - mx) * (r - my) for t, r in pts) / den      # MB per second
    return round(slope * 3600, 1)


def trend_text(mb_per_hour: Optional[float]) -> str:
    if mb_per_hour is None:
        return ""
    if mb_per_hour > 50:
        return "memory grows about %s per hour" % _mb(mb_per_hour)
    if mb_per_hour < -50:
        return "memory is going down"
    return "memory is steady"


class MemGuard:
    def __init__(self, mgr, notify: Optional[Callable[[str, str], None]] = None,
                 log: Callable[[str], None] = print, db=None, spawn=None):
        self.mgr = mgr
        self.notify = notify
        self.log = log
        self.db = db
        self.over: Dict[str, int] = {}
        self.last_action: Dict[str, float] = {}
        self.busy: set = set()
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True, name="mem-guard").start())

    def step(self, statuses: List[dict], now: Optional[float] = None) -> List[str]:
        """Returns the ids of servers it decided to restart."""
        now = time.time() if now is None else now
        acted = []
        if float(getattr(self.mgr, "maintenance_until", 0) or 0) > now:
            return acted
        for st in statuses:
            sid = st.get("id")
            if not sid:
                continue
            srv = self.mgr.get_server(sid) or {}
            cap = limit_mb(srv)
            used = float(st.get("memUsageMB") or 0)
            if not cap or not st.get("isRunning") or st.get("state") not in (None, "running"):
                self.over.pop(sid, None)
                continue
            if used < cap:
                self.over[sid] = 0
                continue
            self.over[sid] = self.over.get(sid, 0) + 1
            if self.over[sid] < OVER_SAMPLES or sid in self.busy or \
                    (sid in self.last_action and now - self.last_action[sid] < COOLDOWN):
                continue
            self.over[sid] = 0
            self.last_action[sid] = now
            self.busy.add(sid)
            acted.append(sid)
            name = st.get("name") or srv.get("name") or sid
            self.spawn(lambda sid=sid, name=name, used=used, cap=cap: self._restart(sid, name, used, cap))
        return acted

    def _growth(self, sid) -> str:
        if self.db is None:
            return ""
        try:
            from core.history import series
            return trend_text(trend(series(self.db, sid, "24h")["points"]))
        except Exception:
            return ""

    def _restart(self, sid, name, used, cap):
        try:
            grow = self._growth(sid)
            self.log("[MEMORY] %s uses %s (limit %s) - restarting it" % (name, _mb(used), _mb(cap)))
            try:
                self.mgr.warn_players([sid], action="restarting to free up memory", minutes=WARN_MINUTES)
            except Exception as e:
                self.log("[WARN] memory restart warning: %s" % e)
            if not self.mgr.is_running(sid):
                return
            self.mgr.restart_server(sid, user="memory-guard")
            try:
                self.mgr._audit("memory_restart", sid, user="memory-guard", used_mb=round(used), limit_mb=round(cap))
            except Exception:
                pass
            if self.notify:
                self.notify("\U0001F9F9 Restarted %s to free memory" % name,
                            "It was using %s, over its limit of %s for %d minutes.%s Players got a warning first."
                            % (_mb(used), _mb(cap), OVER_SAMPLES, (" Its " + grow + ".") if grow else ""))
        except Exception as e:
            self.log("[ERROR] memory restart of %s failed: %s" % (name, e))
        finally:
            self.busy.discard(sid)
