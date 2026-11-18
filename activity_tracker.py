"""
Tofu Server Manager — Activity Tracker

Watches each server's log for player joins and records which days had players.
Smart boot uses this to skip auto-starting servers nobody has played for days.

How it works: every poll it reads only the *new* part of each server's log
(it remembers the file offset), counts lines matching the game's join patterns
(from its plugin), and marks today active when there is at least one join.
History is stored in servers_activity.json next to servers.json.
"""

import json
import os
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    from core.paths import atomic_write_json
except Exception:  # pragma: no cover
    atomic_write_json = None


class ActivityRecord:
    """Daily activity record for a single server."""

    def __init__(self, server_id: str):
        self.server_id = server_id
        self.dates: dict[str, dict] = {}  # YYYY-MM-DD -> {"active", "players_seen", "last_activity"}


class ActivityTracker:
    """Records player activity per server per day."""

    DEFAULT_PATTERNS = {
        "vintage-story": [r"\[Server Event\].*\bjoins\b", r"Player .+ joined", r"New connection from"],
        "steamcmd": [r"player_connect", r"User .+ connected", r"Player .+ (joined|connected)"],
        "custom": [r"\bplayer\b.*\b(joined|connected)\b", r"\b(joined|connected)\b.*\bplayer\b"],
    }

    def __init__(self, config_path: str, manager=None):
        self.config_path = Path(config_path)
        self.activity_log_path = Path(str(config_path).replace(".json", "_activity.json"))
        self.manager = manager
        self.records: dict[str, ActivityRecord] = {}
        self._offsets: dict[str, tuple[str, int]] = {}
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._poll_interval = 60
        self._load_activity_data()

    # ── persistence ───────────────────────────────────────
    def _load_activity_data(self):
        if self.activity_log_path.exists():
            try:
                data = json.loads(self.activity_log_path.read_text(encoding="utf-8"))
                for sid, dates in data.get("servers", {}).items():
                    rec = ActivityRecord(sid)
                    rec.dates = dates
                    self.records[sid] = rec
                self._offsets = {k: tuple(v) for k, v in (data.get("_offsets") or {}).items()}
            except Exception as e:
                print(f"[WARN] Failed to load activity data: {e}")

    def _save_activity_data(self):
        try:
            data = {"servers": {sid: r.dates for sid, r in self.records.items()},
                    "_offsets": {k: list(v) for k, v in self._offsets.items()}}
            if atomic_write_json:
                atomic_write_json(self.activity_log_path, data)
            else:
                self.activity_log_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[WARN] Failed to save activity data: {e}")

    def get_server_record(self, server_id: str) -> ActivityRecord:
        if server_id not in self.records:
            self.records[server_id] = ActivityRecord(server_id)
        return self.records[server_id]

    def has_history(self, server_id: str) -> bool:
        rec = self.records.get(server_id)
        return bool(rec and any(d.get("active") for d in rec.dates.values()))

    # ── scanning ──────────────────────────────────────────
    def _load_servers_config(self) -> list[dict]:
        if self.manager is not None:
            return self.manager.load_servers()
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _patterns(self, srv: dict):
        pats = []
        if self.manager is not None:
            plugin = self.manager.plugin_for(srv)
            if plugin is not None:
                try:
                    pats = list(plugin.player_join_patterns(srv) or [])
                except Exception:
                    pats = []
        if srv.get("joinPattern"):
            pats.append(srv["joinPattern"])
        if not pats:
            pats = self.DEFAULT_PATTERNS.get(srv.get("type", "custom"), self.DEFAULT_PATTERNS["custom"])
        out = []
        for p in pats:
            try:
                out.append(re.compile(p, re.IGNORECASE))
            except re.error:
                pass
        return out

    def _log_paths(self, srv: dict) -> list:
        if self.manager is not None:
            return self.manager.get_log_candidates(srv["id"])
        d = srv.get("directory", "")
        cand = os.path.join(d, "logs", f"tofusm-{srv['id']}.log")
        return [cand] if os.path.isfile(cand) else []

    def scan_server(self, srv: dict) -> int:
        """Read new lines from every log of one server; return the number of joins found."""
        sid = srv["id"]
        patterns = self._patterns(srv)
        joins = 0
        for path in self._log_paths(srv):
            key = sid + "|" + path
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            _p, prev_off = self._offsets.get(key, (path, None))
            if prev_off is None:
                # First time we see this file: only look at recent history.
                prev_off = max(0, size - 256 * 1024)
            if size < prev_off:      # rotated / truncated
                prev_off = 0
            if size == prev_off:
                self._offsets[key] = (path, size)
                continue
            try:
                with open(path, "rb") as f:
                    f.seek(prev_off)
                    chunk = f.read(min(size - prev_off, 8 * 1024 * 1024))
            except OSError:
                continue
            self._offsets[key] = (path, prev_off + len(chunk))
            text = chunk.decode("utf-8", errors="replace")
            joins += sum(1 for line in text.splitlines() if any(p.search(line) for p in patterns))
        if joins:
            self._mark_active(sid, joins)
        return joins

    def _mark_active(self, server_id: str, joins: int):
        rec = self.get_server_record(server_id)
        today = date.today().isoformat()
        day = rec.dates.setdefault(today, {"active": False, "players_seen": 0})
        day["active"] = True
        day["players_seen"] = int(day.get("players_seen", 0)) + joins
        day["last_activity"] = datetime.now().isoformat(timespec="seconds")

    def poll_once(self):
        with self._lock:
            changed = False
            for srv in self._load_servers_config():
                if not srv.get("id"):
                    continue
                try:
                    if self.scan_server(srv):
                        changed = True
                    # Live player counts (Steam query / RCON) also count as activity.
                    if self.manager is not None:
                        online = self.manager.player_count(srv["id"])
                        if online:
                            self._mark_active(srv["id"], 0)
                            day = self.get_server_record(srv["id"]).dates[date.today().isoformat()]
                            day["peak_players"] = max(int(day.get("peak_players", 0)), int(online))
                            changed = True
                except Exception as e:
                    print(f"[WARN] activity scan failed for {srv.get('name')}: {e}")
            # Offsets change every poll; persist them along with any new activity.
            self._save_activity_data()
            return changed

    def start_polling(self, interval: int = 60):
        self._poll_interval = max(15, min(int(interval), 3600))
        if self._running:
            return
        self._running = True

        def loop():
            import time as _t
            while self._running:
                try:
                    self.poll_once()
                except Exception as e:
                    print(f"[WARN] Activity polling error: {e}")
                waited = 0
                while self._running and waited < self._poll_interval:
                    _t.sleep(1)
                    waited += 1

        self._thread = threading.Thread(target=loop, daemon=True, name="activity")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    # ── queries ───────────────────────────────────────────
    def check_consecutive_inactive_days(self, server_id: str, threshold: int = 2) -> bool:
        """True if there were no players on any of the last ``threshold`` days (today included)."""
        rec = self.get_server_record(server_id)
        days = [(date.today() - timedelta(days=i)).isoformat() for i in range(max(1, threshold))]
        return not any(rec.dates.get(d, {}).get("active") for d in days)

    def should_auto_start(self, server_id: str) -> bool:
        return not self.check_consecutive_inactive_days(server_id, 2)

    def get_activity_summary(self, server_id: str) -> dict:
        rec = self.get_server_record(server_id)
        week = []
        for i in range(6, -1, -1):
            d = date.today() - timedelta(days=i)
            info = rec.dates.get(d.isoformat(), {})
            week.append({"day": d.strftime("%a %m/%d"), "active": bool(info.get("active")),
                         "players_seen": info.get("players_seen", 0)})
        last = None
        for d in sorted(rec.dates):
            if rec.dates[d].get("active"):
                last = rec.dates[d].get("last_activity") or d
        return {
            "server_id": server_id,
            "week_activity": week,
            "has_history": self.has_history(server_id),
            "last_activity": last,
            "should_auto_start": self.should_auto_start(server_id),
            "consecutive_inactive_days": self._count_consecutive_inactive(rec.dates),
        }

    def _count_consecutive_inactive(self, dates: dict) -> int:
        count = 0
        d = date.today()
        for _ in range(30):
            if dates.get(d.isoformat(), {}).get("active"):
                break
            count += 1
            d -= timedelta(days=1)
        return count
