"""Nightly restart: keep a server PC fresh without anyone having to look after it.

At the set time (Settings > Nightly restart) the agent:

1. tells Discord and the players (15 / 5 / 1 minute warnings - it goes ahead even
   if people are still on, so the PC stays on a reliable nightly cycle)
2. stops every server cleanly (they save), then backs up their worlds
3. remembers which servers to bring back: the ones that were running and had
   players in the last few days. Servers nobody played on lately stay asleep -
   anyone can wake them with Start on Discord or in the panel
4. restarts Windows (clears leaks in the games, drivers and Windows itself)

When the PC is back, the agent (started at boot, see core/boot_task.py) reads the
plan and starts exactly those servers, then posts what's up and what's asleep.
If Windows refuses to restart, the servers are started again right away.

Settings: nightly_restart_enabled (False), nightly_restart_time ("05:00", local
time), nightly_backup (True), nightly_warn_minutes ([15, 5, 1]), smart_boot_days (2).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

DEFAULT_TIME = "05:00"
WINDOW_HOURS = 2          # a PC that was off at 05:00 doesn't reboot at lunch time
PLAN_MAX_AGE = 3 * 3600   # a boot plan older than this is ignored (normal boot rules apply)
REBOOT_DELAY = 60


def _setting(mgr, key, default):
    try:
        v = mgr.get_setting(key, default)
    except Exception:
        return default
    return default if v is None or v == "" else v


def parse_clock(text):
    """(hour, minute) from "05:00", "5:30", "17:45", "5am", "5:30 PM" or "5", else None."""
    import re
    m = re.match(r"^\s*(\d{1,2})(?:[:.](\d{2}))?\s*([ap])?\.?\s*m?\.?\s*$", str(text or ""), re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ap:
        if not 1 <= h <= 12:
            return None
        h = (0 if h == 12 else h) + (12 if ap == "p" else 0)
    if 0 <= h < 24 and 0 <= mi < 60:
        return h, mi
    return None


def parse_time(text) -> tuple:
    return parse_clock(text) or parse_clock(DEFAULT_TIME) or (5, 0)


def valid_time(text) -> bool:
    return parse_clock(text) is not None


def config(mgr) -> dict:
    warn = _setting(mgr, "nightly_warn_minutes", [15, 5, 1])
    try:
        warn = sorted({int(x) for x in warn if int(x) > 0}, reverse=True)
    except (TypeError, ValueError):
        warn = [15, 5, 1]
    try:
        days = max(1, int(_setting(mgr, "smart_boot_days", 2)))
    except (TypeError, ValueError):
        days = 2
    h, m = parse_time(_setting(mgr, "nightly_restart_time", DEFAULT_TIME))
    return {"enabled": bool(_setting(mgr, "nightly_restart_enabled", False)), "time": "%02d:%02d" % (h, m),
            "backup": bool(_setting(mgr, "nightly_backup", True)), "warn": warn, "days": days}


def _state_path(mgr) -> Path:
    p = Path(mgr.config_path).parent / "runtime"
    p.mkdir(parents=True, exist_ok=True)
    return p / "nightly.json"


def read_state(mgr) -> dict:
    try:
        return json.loads(_state_path(mgr).read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(mgr, **fields) -> dict:
    st = read_state(mgr)
    st.update(fields)
    tmp = _state_path(mgr).with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
    os.replace(tmp, _state_path(mgr))
    return st


def next_run(mgr, now: Optional[float] = None) -> Optional[float]:
    cfg = config(mgr)
    if not cfg["enabled"]:
        return None
    now = time.time() if now is None else now
    h, m = parse_time(cfg["time"])
    d = datetime.fromtimestamp(now)
    t = d.replace(hour=h, minute=m, second=0, microsecond=0)
    if t.timestamp() <= now or read_state(mgr).get("last_date") == t.date().isoformat():
        t += timedelta(days=1)
    return t.timestamp()


def due(mgr, now: Optional[float] = None) -> bool:
    cfg = config(mgr)
    if not cfg["enabled"]:
        return False
    now = time.time() if now is None else now
    h, m = parse_time(cfg["time"])
    d = datetime.fromtimestamp(now)
    t = d.replace(hour=h, minute=m, second=0, microsecond=0)
    if now < t.timestamp() or now - t.timestamp() > WINDOW_HOURS * 3600:
        return False
    st = read_state(mgr)
    if st.get("last_date") == t.date().isoformat():
        return False
    # just rebooted by this very restart (the clock is still inside the window)
    if st.get("rebooting_at") and now - float(st["rebooting_at"]) < WINDOW_HOURS * 3600:
        return False
    return True


# --------------------------------------------------------------------------- #
# who comes back
# --------------------------------------------------------------------------- #
def _recent(tracker, sid: str, days: int) -> Optional[bool]:
    """True/False when activity is known, None when there's no history yet."""
    if tracker is None:
        return None
    try:
        if not tracker.has_history(sid):
            return None
        return not tracker.check_consecutive_inactive_days(sid, days)
    except Exception:
        return None


def plan(mgr, tracker=None, running: Optional[List[str]] = None, days: Optional[int] = None) -> dict:
    """{"bring_back": [ids], "sleeping": [ids], "reasons": {id: text}} for the running servers."""
    days = days or config(mgr)["days"]
    if running is None:
        running = [s["id"] for s in mgr.load_servers() if s.get("id") and mgr.is_running(s["id"])]
    back, sleep, why = [], [], {}
    for sid in running:
        try:
            online = mgr.player_count(sid)
        except Exception:
            online = None
        r = _recent(tracker, sid, days)
        if online:
            back.append(sid)
            why[sid] = "players online now"
        elif r is None:
            back.append(sid)
            why[sid] = "no player history yet"
        elif r:
            back.append(sid)
            why[sid] = "played in the last %d day%s" % (days, "s" if days != 1 else "")
        else:
            sleep.append(sid)
            why[sid] = "nobody played for %d+ days" % days
    return {"bring_back": back, "sleeping": sleep, "reasons": why}


def preview(mgr, tracker=None) -> dict:
    """What tonight's restart would do (for the settings screen)."""
    if tracker is None:
        try:
            from activity_tracker import ActivityTracker
            tracker = ActivityTracker(str(mgr.config_path), manager=None)
        except Exception:
            tracker = None
    p = plan(mgr, tracker)
    names = {s["id"]: s.get("name") or s["id"] for s in mgr.load_servers() if s.get("id")}
    nxt = next_run(mgr)
    return {"next_run": nxt, "config": config(mgr),
            "bring_back": [{"id": i, "name": names.get(i, i), "why": p["reasons"][i]} for i in p["bring_back"]],
            "sleeping": [{"id": i, "name": names.get(i, i), "why": p["reasons"][i]} for i in p["sleeping"]],
            "last": read_state(mgr).get("last_summary")}


def take_boot_plan(mgr, now: Optional[float] = None) -> Optional[dict]:
    """The plan left by a nightly restart, once (None when this boot wasn't one)."""
    st = read_state(mgr)
    p = st.get("boot_plan")
    if not p:
        return None
    now = time.time() if now is None else now
    write_state(mgr, boot_plan=None)
    if now - float(p.get("at") or 0) > PLAN_MAX_AGE:
        return None
    return p


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
def reboot_windows(delay: int = REBOOT_DELAY) -> bool:
    if os.name != "nt":
        return False
    try:
        r = subprocess.run(["shutdown", "/r", "/t", str(int(delay)), "/d", "p:0:0",
                            "/c", "BaoBun nightly restart - game servers come back by themselves."],
                           capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception:
        return False


def update_games(mgr, log: Callable[[str], None] = print) -> List[str]:
    """While everything is stopped for the nightly restart: install waiting game
    updates for servers set to "Update the game automatically when nobody is online".
    An ARK cluster is updated as one (every install folder, same build)."""
    done, seen = [], set()
    for srv in mgr.load_servers():
        sid = srv.get("id")
        if not sid or not srv.get("autoUpdateWhenEmpty") or not srv.get("appId"):
            continue
        rec = mgr._read_registry(sid) or {}
        if not rec.get("update_available") or rec.get("update_major"):
            continue
        group = tuple(sorted(mgr.update_group(sid)))
        if group in seen or any(mgr.is_running(g) for g in group):
            continue
        seen.add(group)
        try:
            r = mgr.update_server(sid, user="nightly")
            if r.get("status") == "updated":
                line = srv.get("name") or sid
                if srv.get("clusterId") and len(group) > 1:
                    line = "Cluster %s (%d maps)" % (srv["clusterId"], len(group))
                done.append(line + ((" - " + r["warning"]) if r.get("warning") else ""))
        except Exception as e:
            log("[WARN] nightly update of %s: %s" % (srv.get("name") or sid, e))
            done.append("%s: update failed (%s)" % (srv.get("name") or sid, e))
    return done


def run(mgr, tracker=None, notify: Optional[Callable[[str, str], None]] = None,
        log: Callable[[str], None] = print, reboot: Callable[[], bool] = reboot_windows,
        start: Optional[Callable[[str], None]] = None, now: Optional[float] = None) -> dict:
    """Do one nightly restart. ``notify(title, text)`` posts to Discord."""
    cfg = config(mgr)
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now).date().isoformat()
    write_state(mgr, last_date=today, phase="warning", started_at=now)
    names = {s["id"]: s.get("name") or s["id"] for s in mgr.load_servers() if s.get("id")}
    running = [sid for sid in names if mgr.is_running(sid)]
    p = plan(mgr, tracker, running=running, days=cfg["days"])

    def nm(ids):
        return ", ".join(names.get(i, i) for i in ids) or "none"

    def tell(title, text):
        log("[NIGHTLY] %s - %s" % (title, text))
        if notify:
            try:
                notify(title, text)
            except Exception as e:
                log("[WARN] nightly notice failed: %s" % e)

    if running:
        lead = cfg["warn"][0] if cfg["warn"] else 0
        tell("Nightly restart in %d minutes" % lead if lead else "Nightly restart now",
             "The server PC restarts for maintenance. Coming back: %s. Sleeping until someone starts them: %s."
             % (nm(p["bring_back"]), nm(p["sleeping"])))
        try:
            mgr.warn_players(running, action="restarting for maintenance", minutes=cfg["warn"])
        except Exception as e:
            log("[WARN] nightly warnings: %s" % e)
    write_state(mgr, phase="stopping")
    try:
        mgr.stop_all(user="nightly")
    except Exception as e:
        log("[WARN] nightly stop: %s" % e)
    still = [sid for sid in running if mgr.is_running(sid)]
    if still:
        log("[WARN] nightly: still running after stop: %s" % nm(still))
    backups = []
    if cfg["backup"]:
        write_state(mgr, phase="backing up")
        for sid in running:
            try:
                mgr.backup_server_world(sid, reason="nightly")
                backups.append(sid)
            except Exception as e:
                log("[WARN] nightly backup of %s failed: %s" % (names.get(sid, sid), e))
    mods_done = []
    try:
        from core import mod_updates
        if mod_updates.mode(mgr) == "auto":
            write_state(mgr, phase="updating mods")
            mods_done = mod_updates.apply_pending(mgr, log=log)
            if mods_done:
                tell("\U0001F9E9 Mods updated", "\n".join(mods_done))
    except Exception as e:
        log("[WARN] nightly mod updates: %s" % e)
    games_done = []
    try:
        write_state(mgr, phase="updating games")
        games_done = update_games(mgr, log=log)
        if games_done:
            tell("\u2B06 Game updates installed", "\n".join(games_done))
    except Exception as e:
        log("[WARN] nightly game updates: %s" % e)
    summary = {"at": now, "date": today, "bring_back": p["bring_back"], "sleeping": p["sleeping"],
               "backed_up": backups, "rebooted": False, "mods": mods_done, "games": games_done}
    write_state(mgr, phase="rebooting", boot_plan={"at": time.time(), "bring_back": p["bring_back"],
                                                    "sleeping": p["sleeping"]}, last_summary=summary)
    ok = False
    try:
        ok = bool(reboot())
    except Exception as e:
        log("[WARN] nightly reboot failed: %s" % e)
    if ok:
        summary["rebooted"] = True
        write_state(mgr, rebooting_at=time.time(), last_summary=summary)
        log("[NIGHTLY] Windows restarts in %d seconds" % REBOOT_DELAY)
        return summary
    # Windows wouldn't restart (or this isn't Windows): bring the servers back now.
    log("[WARN] nightly: Windows didn't restart - starting the servers again without a reboot")
    write_state(mgr, boot_plan=None, phase="done", last_summary=summary)
    for sid in p["bring_back"]:
        try:
            (start or (lambda s: mgr.start_server(s, user="nightly")))(sid)
        except Exception as e:
            log("[ERROR] nightly start %s: %s" % (names.get(sid, sid), e))
    tell("Nightly restart done (without a PC restart)",
         "Back up: %s. Sleeping: %s." % (nm(p["bring_back"]), nm(p["sleeping"])))
    return summary
