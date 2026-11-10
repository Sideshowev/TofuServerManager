"""Everything the Discord bot decides that doesn't need discord.py itself, so it
can be tested: button labels, finding a server from what someone typed, cooldowns,
"who joined" messages, activity messages and waiting until a server is joinable.
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Tuple

BUSY = ("starting", "updating", "stopping", "restarting")
ICON = {"running": "\U0001F7E2", "crashed": "\U0001F534", "stopped": "⚪"}


# --------------------------------------------------------------------------- #
# panel buttons
# --------------------------------------------------------------------------- #
def button_spec(st: dict) -> Tuple[str, str, bool]:
    """(label, style, disabled) for a server's panel button.
    style is one of "success" (start), "secondary" (running), "danger" (crashed)."""
    name = st.get("name") or "?"
    state = st.get("state") or ("running" if st.get("isRunning") else "stopped")
    if state in BUSY:
        word = {"starting": "Starting", "updating": "Updating", "stopping": "Stopping",
                "restarting": "Restarting"}[state]
        return _cut("⏳ %s: %s" % (word, name)), "secondary", True
    if st.get("isRunning"):
        label = "\U0001F7E2 " + name
        if st.get("playerCount") is not None:
            label += " (%s%s)" % (st["playerCount"], ("/" + str(st["maxPlayers"])) if st.get("maxPlayers") else "")
        return _cut(label), "secondary", False
    if state == "crashed":
        return _cut("▶ Start: %s (crashed)" % name), "danger", False
    return _cut("▶ Start: " + name), "success", False


def _cut(s: str, n: int = 80) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# --------------------------------------------------------------------------- #
# finding servers by name
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def find_server(servers: List[dict], text: str) -> Tuple[Optional[dict], List[dict]]:
    """(server, candidates). Exact id/name first, then unique prefix/contains match."""
    t = (text or "").strip()
    if not t:
        return None, []
    for s in servers:
        if s.get("id") == t or (s.get("name") or "").lower() == t.lower():
            return s, [s]
    n = _norm(t)
    pref = [s for s in servers if _norm(s.get("name")).startswith(n)]
    if len(pref) == 1:
        return pref[0], pref
    cont = [s for s in servers if n and n in _norm(s.get("name"))]
    if len(cont) == 1:
        return cont[0], cont
    return None, pref or cont


def autocomplete(servers: List[dict], current: str, limit: int = 25) -> List[Tuple[str, str]]:
    """[(label, value)] choices for a slash-command server option."""
    n = _norm(current)
    hits = [s for s in servers if not n or n in _norm(s.get("name"))]
    hits.sort(key=lambda s: (not _norm(s.get("name")).startswith(n), (s.get("name") or "").lower()))
    return [((s.get("name") or s["id"])[:100], s["id"]) for s in hits[:limit]]


# --------------------------------------------------------------------------- #
# cooldowns / limits
# --------------------------------------------------------------------------- #
class Cooldowns:
    """Stops one person from hammering Start: one action per user per ``seconds``."""

    def __init__(self, seconds: float = 20.0):
        self.seconds = float(seconds)
        self._last: Dict[tuple, float] = {}

    def remaining(self, user_id, key="any", now: Optional[float] = None) -> float:
        now = time.time() if now is None else now
        last = self._last.get((user_id, key), 0.0)
        return max(0.0, self.seconds - (now - last))

    def hit(self, user_id, key="any", now: Optional[float] = None):
        self._last[(user_id, key)] = time.time() if now is None else now


def too_many_running(statuses: List[dict], max_running: int) -> bool:
    if not max_running:
        return False
    return sum(1 for s in statuses if s.get("isRunning") or s.get("state") in BUSY) >= int(max_running)


# --------------------------------------------------------------------------- #
# who joined / left
# --------------------------------------------------------------------------- #
def player_snapshot(statuses: List[dict]) -> Dict[str, set]:
    out = {}
    for s in statuses:
        if s.get("isRunning") and s.get("players") is not None:
            out[s["id"]] = {p.get("name") for p in s.get("players") or [] if p.get("name")}
    return out


def player_changes(prev: Dict[str, set], now: Dict[str, set], names: Dict[str, str]) -> List[str]:
    """Lines like "Bob joined **Valheim**" for servers known in both snapshots."""
    lines = []
    for sid, cur in now.items():
        if sid not in prev:
            continue    # just started being tracked: don't announce everyone at once
        joined = sorted(cur - prev[sid])
        left = sorted(prev[sid] - cur)
        srv = names.get(sid, sid)
        if joined:
            lines.append("\U0001F3AE %s joined **%s**" % (_names(joined), srv))
        if left:
            lines.append("\U0001F44B %s left **%s**" % (_names(left), srv))
    return lines


def _names(lst: List[str]) -> str:
    lst = [re.sub(r"[*_`~|>@]", "", n) for n in lst]
    if len(lst) <= 3:
        return ", ".join(lst)
    return ", ".join(lst[:3]) + " and %d more" % (len(lst) - 3)


# --------------------------------------------------------------------------- #
# activity messages (things that happened outside Discord)
# --------------------------------------------------------------------------- #
def event_message(event: str, info: dict, srv: dict) -> Optional[str]:
    name = info.get("name") or srv.get("name") or "A server"
    user = str(info.get("user") or "")
    if user.startswith("discord:"):
        return None     # already logged when the button / command was used
    if event == "started":
        why = {"smart-boot": "BaoBun started it after the PC booted", "nightly": "back after the nightly restart",
               "schedule": "scheduled restart"}.get(user)
        if user.startswith("web:"):
            why = "started by %s from the web panel" % user[4:]
        elif user in ("gui", "desktop", "system") or not user:
            why = why or "started from the desktop app"
        return "▶ **%s** is starting%s." % (name, (" (" + why + ")") if why else "")
    if event == "stopped":
        if user == "idle-stop":
            mins = srv.get("idleStopMinutes")
            return ("\U0001F4A4 **%s** went to sleep - nobody played for %s minutes. Press Start to wake it."
                    % (name, int(float(mins)) if mins else "a while"))
        if user == "nightly":
            return None   # the nightly notice already lists everything
        if user.startswith("web:"):
            return "⏹ **%s** was stopped by %s from the web panel." % (name, user[4:])
        if user == "schedule":
            return None
        if user == "agent":
            return "⏹ **%s** stopped (BaoBun on the server PC is shutting down)." % name
        return "⏹ **%s** stopped." % name
    if event == "gave_up":
        return None       # alerts.py posts crash alerts
    if event == "warning":
        action = str(info.get("action") or "restarting")
        if "maintenance" in action:
            return None   # the nightly restart notice already says so
        mins = info.get("minutes")
        return "\u23F0 **%s** is %s in %s minute%s - players were warned in-game." % (
            name, action, mins, "" if mins == 1 else "s")
    return None


# --------------------------------------------------------------------------- #
# ready check / how to join
# --------------------------------------------------------------------------- #
def ready_state(mgr, server_id: str, started_at: float, now: Optional[float] = None) -> str:
    """"ready" | "waiting" | "failed" for a server someone just started."""
    now = time.time() if now is None else now
    st = next((s for s in mgr.get_all_statuses() if s["id"] == server_id), None)
    if st is None:
        return "failed"
    state = st.get("state") or ("running" if st.get("isRunning") else "stopped")
    if state in ("crashed", "stopped") and not st.get("isRunning"):
        return "failed" if now - started_at > 5 else "waiting"
    if not st.get("isRunning") or state in BUSY:
        return "waiting"
    if st.get("playerCount") is not None:
        return "ready"   # the game answered a player query
    srv = mgr.get_server(server_id) or {}
    try:
        from core.network import expected_ports, listening_ports
        ports = expected_ports(srv, mgr)
        if ports and st.get("pid"):
            open_ports = listening_ports(st.get("pid"))
            if open_ports and ports[0]["port"] in open_ports:
                return "ready"
            if not open_ports and now - started_at > 120:
                return "ready"   # can't tell - assume it's up after two minutes
            return "waiting"
    except Exception:
        pass
    return "ready" if now - started_at > 60 else "waiting"


def join_text(mgr, server_id: str, show_public: bool) -> str:
    """How to join: the address (public only when the owner allows it)."""
    try:
        from core.network import connect_info
        info = connect_info(mgr, server_id, lookup_public=show_public)
    except Exception:
        return ""
    addr = info.get("addresses") or info.get("addr") or {}
    if show_public and addr.get("internet"):
        return "Join at `%s`" % addr["internet"]
    if addr.get("tailscale"):
        return "Join over Tailscale at `%s`" % addr["tailscale"]
    return ""


HELP = ("**BaoBun bot**\n"
        "• Press a button on the panel to start a server (admins can also stop one).\n"
        "• `/servers` - what's online (just for you) · `/status` - the same, posted for everyone\n"
        "• `/start <server>` - start a server; I'll tell you when it's ready to join\n"
        "• `/players`, `/join` - who's on, how to connect\n"
        "• `/mods <server>` - the mods it uses, with links and a mod pack to download\n"
        "• `/notify <server>` - a DM when it's up, or whenever someone joins\n"
        "• `/event` - plan a game night: people sign up, get pinged 30 min before, and I start the server\n"
        "• `/events`, `/event-cancel` - see or cancel game nights · `/stats` - who played the most\n"
        "• Admins: `/stop`, `/restart`, `/say`, `/access` (allow list, bans, admins), `/backup`, `/restore`, `/logs`, `/update`, `/checkup`, "
        "`/baobun-setup` (channels and roles)")
