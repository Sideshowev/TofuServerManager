"""Settings asked for when a server is added: server name, join password, max players.

Only for games, and only for the settings, that the game smoke test showed the game
really keeps (game_profiles/SMOKE_SUMMARY.md) - TRUSTED below. Where each setting
lives comes from Essentials (core/quick_settings.py), so there is one place that
knows a game's settings.

Many games only make their settings file on their first start. What can't be
written yet is remembered on the server ("firstSettings", passwords encrypted)
and written as soon as the file is there:

* remember(mgr, sid, values)   - when the server is added: write what can be written
* apply(mgr, sid)              - before every start: write what's still waiting
* watch_first_start(mgr, sid)  - after a start that left something waiting: once the
                                 game has made its settings and nobody is playing,
                                 restart it once so the choices take effect

Servers added without anyone to ask (Discord, "Bring servers here") skip all this;
core/game_defaults.py still fills in what a game can't start without.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Dict, List, Optional

# game key (as core/quick_settings.game_key) -> the kinds proven to stick
TRUSTED: Dict[str, tuple] = {
    "380870": ("name", "password", "players"),      # Project Zomboid - players see all three
    "1110390": ("name", "password", "players"),     # Unturned - players see all three
    "2278520": ("name", "players"),                 # Enshrouded - players see both
    "258550": ("name", "players"),                  # Rust - players see both
    "896660": ("name", "password"),                 # Valheim - kept (launch options)
    "2394010": ("name", "password", "players"),     # Palworld - kept
    "1829350": ("name", "password", "players"),     # V Rising - kept
    "294420": ("name", "password", "players"),      # 7 Days to Die - kept
    "4020": ("name", "password", "players"),        # Garry's Mod - kept
    "vintage-story": ("name", "players"),           # kept
    "minecraft": ("players",),                      # kept
}
WAIT_MINUTES = 20


def kind_of(label: str) -> Optional[str]:
    low = str(label or "").strip().lower()
    if low.startswith("server name"):
        return "name"
    if low.startswith("password"):
        return "password"
    if low.startswith("max players"):
        return "players"
    return None


def _spec(game: dict):
    """(game key, [(field key, kind, label, type, where)]) for a preset or a server."""
    from core import quick_settings as qs
    probe = {"appId": game.get("appId"), "type": game.get("type"), "executable": game.get("executable") or ""}
    try:
        key = qs.game_key(probe)
    except Exception:
        key = None
    kinds = TRUSTED.get(key or "")
    if not kinds:
        return None, []
    out = []
    for fkey, label, typ, where in qs.GAMES[key]["fields"]:
        k = kind_of(label)
        if k in kinds and isinstance(typ, str) and not any(o[1] == k for o in out):
            out.append((fkey, k, label, typ, where))
    return key, out


def fields(game: dict, server_name: str = "") -> List[dict]:
    """What to ask for this game: [{"key", "kind", "label", "type", "value", "required", "help"}].
    Empty when BaoBun has nothing proven to offer for it."""
    key, spec = _spec(game)
    out = []
    for fkey, kind, _label, typ, _where in spec:
        f = {"key": fkey, "kind": kind, "type": typ, "value": "", "required": False, "help": ""}
        if kind == "name":
            f.update(label="Name players see in the game's server list", value=server_name or "")
        elif kind == "password":
            f.update(label="Password to join", help="Leave empty for no password.")
            if key == "896660":       # Valheim won't start without one
                from core.game_defaults import make_password
                f.update(required=True, value=make_password(),
                         help="Valheim needs one: at least 5 characters, and not part of the server name.")
        else:
            f.update(label="Max players", help="Leave empty for the game's own limit.")
        out.append(f)
    return out


def check(game: dict, values: dict) -> Dict[str, str]:
    """Cleaned {field key: value} for the fields this game is asked for; blank ones are
    left out. Raises ValueError with a plain message for a value that can't work."""
    key, spec = _spec(game)
    out: Dict[str, str] = {}
    name = ""
    for fkey, kind, _label, _typ, _where in spec:
        if kind == "name":
            name = str((values or {}).get(fkey) or "").strip()
    for fkey, kind, _label, _typ, where in spec:
        v = str((values or {}).get(fkey) or "").strip()
        if v == "":
            if kind == "password" and key == "896660":
                raise ValueError("Valheim needs a password of at least 5 characters.")
            continue
        what = {"name": "The server name", "password": "The password", "players": "Max players"}[kind]
        if any(ch in v for ch in '\r\n"') or len(v) > 120:
            raise ValueError("%s can't contain quotes or line breaks, or be longer than 120 characters." % what)
        if kind == "players":
            if not re.match(r"^\d{1,4}$", v) or not 1 <= int(v) <= 1000:
                raise ValueError("Max players must be a whole number from 1 to 1000.")
        if kind == "password" and where[0] == "launch" and re.search(r"\s", v):
            raise ValueError("The password can't contain spaces for this game.")
        if kind == "password" and key == "896660" and (len(v) < 5 or (name and v.lower() in name.lower())):
            raise ValueError("Valheim passwords need at least 5 characters and can't be part of the server name.")
        out[fkey] = v
    return out


# --------------------------------------------------------------------------- #
def _waiting(srv: dict) -> Dict[str, str]:
    """{field key: plain value} still to be written for this server."""
    from core import secrets
    out = {}
    for k, v in (srv.get("firstSettings") or {}).items():
        try:
            out[k] = secrets.unprotect(str(v))
        except Exception:
            continue          # encrypted for another Windows account (copied servers.json): drop it
    return out


def _store(mgr, server_id: str, waiting: Dict[str, str], kinds: Dict[str, str]):
    from core import secrets
    servers = mgr.load_servers()
    for s in servers:
        if s.get("id") == server_id:
            if waiting:
                s["firstSettings"] = {k: (secrets.protect(v) if kinds.get(k) == "password" else v)
                                      for k, v in waiting.items()}
            else:
                s.pop("firstSettings", None)
    mgr.save_servers(servers)


def remember(mgr, server_id: str, values: dict, user: str = "system") -> dict:
    """Keep the choices made when the server was added and write what can be written now."""
    srv = mgr.get_server(server_id)
    if not srv:
        raise ValueError("Server not found")
    clean = check(srv, values)
    _key, spec = _spec(srv)
    _store(mgr, server_id, clean, {f[0]: f[1] for f in spec})
    return apply(mgr, server_id, user=user)


def apply(mgr, server_id: str, user: str = "system") -> dict:
    """Write the choices that are still waiting. {"applied": [labels], "waiting": [labels]}"""
    srv = mgr.get_server(server_id) or {}
    waiting = _waiting(srv)
    if not waiting:
        if srv.get("firstSettings"):
            _store(mgr, server_id, {}, {})
        return {"applied": [], "waiting": []}
    from core import quick_settings as qs
    _key, spec = _spec(srv)
    by_key = {f[0]: f for f in spec}
    applied, still, left = [], [], {}
    for fkey, value in waiting.items():
        f = by_key.get(fkey)
        if not f:
            continue                       # the game changed (edited server): nothing to write to
        label = {"name": "server name", "password": "password", "players": "max players"}[f[1]]
        try:
            qs.save(mgr, server_id, {fkey: value}, user=user)
            applied.append(label)
        except FileNotFoundError:
            left[fkey] = value             # the game hasn't made its settings file yet
            still.append(label)
        except Exception as e:
            print("[WARN] %s: couldn't set the %s chosen when it was added: %s" % (srv.get("name"), label, e))
    _store(mgr, server_id, left, {f[0]: f[1] for f in spec})
    return {"applied": applied, "waiting": still}


def files_ready(srv: dict) -> bool:
    """True when every settings file the waiting choices go into exists."""
    from core import quick_settings as qs
    waiting = _waiting(srv)
    _key, spec = _spec(srv)
    for fkey, _kind, _label, _typ, where in spec:
        if fkey in waiting and where[0] not in ("launch", "ark_url"):
            try:
                if not os.path.isfile(qs._resolve(srv, where[1])):
                    return False
            except Exception:
                return False
    return True


_watching = set()
_watch_lock = threading.Lock()


def watch_first_start(mgr, server_id: str, started_at: Optional[float] = None,
                      poll: float = 5.0, settle: float = 20.0) -> bool:
    """After a start that left choices waiting: when the game has made its settings
    files and nobody is playing, restart it once so the choices take effect. Runs in
    the background; returns False when there is nothing to wait for."""
    srv = mgr.get_server(server_id) or {}
    if not _waiting(srv):
        return False
    with _watch_lock:
        if server_id in _watching:
            return True
        _watching.add(server_id)
    started_at = started_at or time.time()

    def run():
        try:
            seen = None
            while time.time() - started_at < WAIT_MINUTES * 60:
                time.sleep(poll)
                if not mgr.is_running(server_id):
                    return                               # stopped or crashed: the next start applies them
                srv_now = mgr.get_server(server_id) or {}
                if not _waiting(srv_now):
                    return
                if not files_ready(srv_now):
                    seen = None
                    continue
                seen = seen or time.time()
                if time.time() - seen < settle:
                    continue                             # let the game finish writing its files
                st = next((s for s in mgr.get_all_statuses() if s.get("id") == server_id), {}) or {}
                if st.get("playerCount"):
                    continue                             # someone is already playing: wait
                print("[INFO] %s: restarting once to use the settings chosen when it was added" %
                      srv_now.get("name"))
                mgr.restart_server(server_id, user="BaoBun (first settings)")
                return
        except Exception as e:
            print("[WARN] first settings for %s: %s" % (server_id, e))
        finally:
            with _watch_lock:
                _watching.discard(server_id)
    threading.Thread(target=run, daemon=True, name="first-settings-" + server_id).start()
    return True
