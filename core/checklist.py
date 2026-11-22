""""Getting it ready": the short setup checklist on a server's page.

Five steps a new host actually needs, each worked out from what Tofu already knows:

1. Game installed            (the server program is on disk)
2. Name and password chosen  (read from the game's own settings, when Tofu knows where they are)
3. Let friends in            (core.reach's verdict)
4. Backups on                (a backup schedule, or the nightly restart's backup)
5. Invite friends            (the join address or Core Keeper's Game ID, once friends can get in)

Each item: {"key", "title", "detail", "state": "done" | "needs" | "todo",
            "button": label or None, "action": what the button does, "enabled": bool}
Actions the page knows: "install", "settings", "fix:<kind>" (core.reach fix kinds),
"connect", "schedule", "backup", "copy:<text>", "start".
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import List, Optional

DONE, NEEDS, TODO = "done", "needs", "todo"


def _item(key, title, detail, state, button=None, action=None, enabled=True) -> dict:
    return {"key": key, "title": title, "detail": detail, "state": state,
            "button": button, "action": action, "enabled": bool(enabled and button)}


def ago(ts: Optional[float], now: Optional[float] = None) -> str:
    if not ts:
        return "never"
    s = max(0.0, (time.time() if now is None else now) - ts)
    if s < 3600:
        return "%d min ago" % max(1, s // 60)
    if s < 86400:
        n = int(s // 3600)
        return "%d hour%s ago" % (n, "" if n == 1 else "s")
    n = int(s // 86400)
    return "%d day%s ago" % (n, "" if n == 1 else "s")


# --------------------------------------------------------------------------- #
# what Tofu knows, gathered (each piece can be passed in by tests)
# --------------------------------------------------------------------------- #
def _installed(mgr, srv) -> Optional[bool]:
    try:
        return os.path.isfile(mgr._resolve_executable(srv)[0])
    except Exception:
        return None


def _settings(mgr, server_id) -> dict:
    """{"name", "password": True/False/None, "players"} from the game's own settings, or {} if unknown."""
    try:
        from core import quick_settings
        g = quick_settings.get(mgr, server_id)
    except Exception:
        return {}
    if not g.get("supported"):
        return {}
    out = {"name": None, "password": None, "players": None}
    for f in g.get("fields") or []:
        label = (f.get("label") or "").lower()
        if label in ("server name", "server message (motd)") and out["name"] is None:
            out["name"] = str(f.get("value") or "").strip().strip('"') or None
        elif label in ("password", "password to join"):
            out["password"] = bool(f.get("has_value"))
        elif label == "max players":
            v = str(f.get("value") or "").strip().strip('"')
            out["players"] = int(v) if v.isdigit() else None
    return out


def _schedules(mgr) -> dict:
    try:
        base = Path(mgr.config_path).parent
        return json.loads((base / "agent_state.json").read_text(encoding="utf-8")).get("schedules") or {}
    except Exception:
        return {}


def _nightly(mgr) -> dict:
    try:
        from core import nightly
        return nightly.config(mgr)
    except Exception:
        return {}


def _last_backup(mgr, server_id) -> Optional[float]:
    try:
        b = mgr.list_server_backups(server_id)
        return os.path.getmtime(b[0]["path"]) if b else None
    except Exception:
        return None


def schedule_text(cfg: dict) -> str:
    if cfg.get("mode") == "daily":
        times = [str(t) for t in cfg.get("times") or []]
        return "Every day at " + ", ".join(times) if times else "Every day"
    try:
        h = float(cfg.get("hours", 24) or 24)
    except (TypeError, ValueError):
        h = 24.0
    if h >= 24 and h % 24 == 0:
        d = int(h // 24)
        return "Every day" if d == 1 else "Every %d days" % d
    return "Every hour" if h == 1 else "Every %g hours" % h


# --------------------------------------------------------------------------- #
# the list
# --------------------------------------------------------------------------- #
def items(mgr, server_id: str, status: Optional[dict] = None, reach: Optional[dict] = None,
          known: Optional[dict] = None, now: Optional[float] = None) -> List[dict]:
    """The checklist for one server. `known` replaces the lookups in tests:
    {"installed", "settings", "schedules", "nightly", "last_backup"}."""
    K = known or {}
    srv = mgr.get_server(server_id) or {}
    st = status or {}
    r = reach or {}
    running = bool(st.get("isRunning"))
    out: List[dict] = []

    # 1. installed
    inst = K["installed"] if "installed" in K else _installed(mgr, srv)
    if inst is False:
        out.append(_item("install", "Install the game", "The server files aren't on this PC yet.", NEEDS,
                         "Install", "install"))
    else:
        detail = "A game update is waiting" if st.get("updateAvailable") else "Ready to run"
        out.append(_item("install", "Game installed", detail, DONE,
                         "Update" if st.get("updateAvailable") else None, "install"))

    # 2. name and password
    s = K["settings"] if "settings" in K else _settings(mgr, server_id)
    if not s:
        out.append(_item("settings", "Name and password",
                         "Tofu doesn't know where this game keeps them - set them in the game's files.",
                         TODO, "Open settings", "settings"))
    elif s.get("name"):
        bits = [s["name"]]
        if s.get("password") is True:
            bits.append("password set")
        elif s.get("password") is False:
            bits.append("no password")
        if s.get("players"):
            bits.append("%d players" % s["players"])
        out.append(_item("settings", "Name and password chosen", ", ".join(bits), DONE, "Change", "settings"))
    else:
        out.append(_item("settings", "Choose a name and password",
                         "So friends can find it and strangers can't join.", NEEDS, "Choose", "settings"))

    # 3. friends can get in
    verdict = r.get("verdict")
    fix = r.get("fix")
    fix_buttons = {"forward": "Show me how", "tailscale": "Show me how", "firewall": "Allow it",
                   "edit": "Edit server", "connect": "Details"}
    if verdict in ("ok", "relay"):
        out.append(_item("reach", "Friends can get in", r.get("detail") or r.get("title") or "", DONE,
                         "Details", "connect"))
    elif verdict == "likely":
        out.append(_item("reach", "Friends can probably get in", r.get("detail") or "", DONE, "Details", "connect"))
    elif verdict in ("blocked", "cgnat"):
        out.append(_item("reach", "Let friends in", r.get("title") or "", NEEDS,
                         fix_buttons.get(fix, "Show me how"), "fix:%s" % (fix or "connect")))
    elif not running:
        out.append(_item("reach", "Let friends in", "Start the server and Tofu checks whether friends can get in.",
                         TODO, "Start", "start"))
    elif r:
        out.append(_item("reach", "Let friends in", "%s. %s" % (r.get("title") or "Can't tell yet",
                                                                 r.get("detail") or ""), TODO,
                         fix_buttons.get(fix, "Details"), "fix:%s" % fix if fix else "connect"))
    else:
        out.append(_item("reach", "Let friends in", "Checking…", TODO))

    # 4. backups
    sched = (K["schedules"] if "schedules" in K else _schedules(mgr)).get(server_id) or {}
    bcfg = sched.get("backup") if isinstance(sched, dict) else None
    night = K["nightly"] if "nightly" in K else _nightly(mgr)
    last = K["last_backup"] if "last_backup" in K else _last_backup(mgr, server_id)
    tail = " Last one %s." % ago(last, now) if last else ""
    if isinstance(bcfg, dict) and bcfg.get("enabled"):
        out.append(_item("backup", "Backups on", schedule_text(bcfg) + "." + tail, DONE, "Back up now", "backup"))
    elif night.get("enabled") and night.get("backup"):
        out.append(_item("backup", "Backups on", "With the nightly restart at %s, while it's running.%s" % (
            night.get("time") or "night", tail), DONE, "Back up now", "backup"))
    elif last:
        out.append(_item("backup", "Turn on backups", "Last backup %s, and none are planned." % ago(last, now),
                         TODO, "Turn on", "schedule"))
    else:
        out.append(_item("backup", "Turn on backups", "No backups yet - one crash could cost you the world.",
                         NEEDS, "Turn on", "schedule"))

    # 5. invite
    invite = r.get("invite") or r.get("address")
    if running and verdict in ("ok", "likely", "relay") and invite:
        cap = "Game ID %s" % r["address"] if verdict == "relay" else "Friends join at %s" % r.get("address")
        out.append(_item("invite", "Invite friends", cap, DONE, "Copy invite", "copy:" + invite))
    else:
        why = ("Ready once friends can get in" if running else "Ready once it's running")
        out.append(_item("invite", "Invite friends", why, TODO, "Copy invite", None, enabled=False))
    return out


def progress(lst: List[dict]) -> str:
    done = sum(1 for i in lst if i["state"] == DONE)
    return "All set" if done == len(lst) else "%d of %d done" % (done, len(lst))
