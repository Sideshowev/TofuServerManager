"""More things the Discord bot works out without discord.py (so they can be tested):
"ping me" subscriptions, the /mods list and mod pack, console log tails, the
/checkup summary, the bot's status line, the in-Discord setup (/baobun setup)
and who counts as an admin.
"""

from __future__ import annotations

import json
import os
import re
import threading
import zipfile
from typing import Dict, List, Optional

UPLOAD_LIMIT = int(9.5 * 1024 * 1024)       # Discord's default upload limit is 10 MB
PACK_SOURCES = ("vs_moddb", "modrinth", "folder", "thunderstore")
SETUP_KEYS = {"channel_id": int, "log_channel_id": int, "alert_channel_id": int, "event_channel_id": int,
              "admin_role_ids": list, "start_role_ids": list, "show_join_address": bool}


# --------------------------------------------------------------------------- #
# "ping me when it's up / when someone joins"
# --------------------------------------------------------------------------- #
class Subscriptions:
    """{server_id: {"up": [user ids], "join": [user ids]}} in runtime/discord_notify.json.
    "up" is one-shot (cleared once the server is up); "join" stays until turned off."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> Dict[str, dict]:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, d):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, self.path)

    def set(self, sid: str, user_id: int, kind: str, on: bool = True) -> bool:
        if kind not in ("up", "join"):
            raise ValueError("kind is up or join")
        with self._lock:
            d = self._load()
            lst = d.setdefault(sid, {}).setdefault(kind, [])
            uid = int(user_id)
            if on and uid not in lst:
                lst.append(uid)
            if not on and uid in lst:
                lst.remove(uid)
            self._save(d)
            return on

    def who(self, sid: str, kind: str) -> List[int]:
        return list((self._load().get(sid) or {}).get(kind) or [])

    def take_up(self, sid: str) -> List[int]:
        """Everyone waiting for this server to come up (and forget them)."""
        with self._lock:
            d = self._load()
            users = list((d.get(sid) or {}).get("up") or [])
            if users:
                d[sid]["up"] = []
                self._save(d)
            return users

    def mine(self, user_id: int) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for sid, kinds in self._load().items():
            for kind, users in kinds.items():
                if int(user_id) in users:
                    out.setdefault(sid, []).append(kind)
        return out


def came_up(prev: Dict[str, bool], statuses: List[dict]) -> List[str]:
    """Server ids that are running now and weren't at the last look."""
    out = []
    for s in statuses:
        up = bool(s.get("isRunning")) and (s.get("state") in (None, "running"))
        if up and prev.get(s["id"]) is False:
            out.append(s["id"])
    return out


def up_map(statuses: List[dict]) -> Dict[str, bool]:
    return {s["id"]: bool(s.get("isRunning")) and (s.get("state") in (None, "running")) for s in statuses}


# --------------------------------------------------------------------------- #
# /mods
# --------------------------------------------------------------------------- #
def mods_info(mgr, sid: str) -> dict:
    """{"label", "mods": [{"name", "version", "url", "server_only"}], "pack_possible"}."""
    from core.mods import ModManager
    data = ModManager(mgr).list(sid)
    mods = []
    for m in data.get("mods") or []:
        if not m.get("enabled", True):
            continue
        side = str(m.get("side") or "").lower()
        mods.append({"name": str(m.get("name") or m.get("id") or "?")[:80], "version": str(m.get("version") or ""),
                     "url": m.get("url") if str(m.get("url") or "").startswith("https://") else None,
                     "server_only": bool(m.get("server_only")) or side == "server",
                     "file": m.get("file"), "id": m.get("id")})
    return {"label": data.get("label") or "Mods", "source": data.get("source"), "folder": data.get("folder"),
            "mods": mods, "pack_possible": data.get("source") in PACK_SOURCES and bool(data.get("folder"))}


def mods_text(info: dict, server_name: str) -> str:
    mods = info["mods"]
    if not mods:
        return "**%s** runs without mods - nothing to install." % server_name
    client = [m for m in mods if not m["server_only"]]
    lines = ["**%s** - %d mod%s (%s)" % (server_name, len(mods), "" if len(mods) == 1 else "s", info["label"])]
    for m in mods[:40]:
        name = ("[%s](<%s>)" % (_esc(m["name"]), m["url"])) if m["url"] else _esc(m["name"])
        lines.append("• %s%s%s" % (name, (" `%s`" % m["version"]) if m["version"] else "",
                                   " *(server only)*" if m["server_only"] else ""))
    if len(mods) > 40:
        lines.append("…and %d more" % (len(mods) - 40))
    if client and info.get("pack_possible"):
        lines.append("\nPress **Download mod pack** for a zip of the ones you need.")
    return "\n".join(lines)[:1990]


def make_modpack(mgr, sid: str, dest_dir: str, info: Optional[dict] = None) -> dict:
    """Zip the mods players need (not server-only ones). {"path"} or {"error"}."""
    info = info or mods_info(mgr, sid)
    if not info.get("pack_possible"):
        return {"error": "This game downloads its mods by itself, or they come from Steam - use the links."}
    folder = info["folder"]
    files = []
    for m in info["mods"]:
        if m["server_only"] or not m.get("file"):
            continue
        p = os.path.join(folder, os.path.basename(str(m["file"])))
        if os.path.exists(p):
            files.append(p)
    if not files:
        return {"error": "There are no mod files players need to install."}
    size = 0
    for p in files:
        if os.path.isdir(p):
            for root, _d, fs in os.walk(p):
                size += sum(os.path.getsize(os.path.join(root, f)) for f in fs)
        else:
            size += os.path.getsize(p)
    if size > UPLOAD_LIMIT * 1.3:
        return {"error": "The mods are %.0f MB together - too big for Discord. Use the links instead."
                % (size / 1048576)}
    os.makedirs(dest_dir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(sid))[:40]
    out = os.path.join(dest_dir, "modpack_%s.zip" % safe)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            if os.path.isdir(p):
                for root, _d, fs in os.walk(p):
                    for f in fs:
                        full = os.path.join(root, f)
                        z.write(full, os.path.relpath(full, os.path.dirname(p)))
            else:
                z.write(p, os.path.basename(p))
    if os.path.getsize(out) > UPLOAD_LIMIT:
        os.remove(out)
        return {"error": "The mod pack is too big for Discord (over 10 MB). Use the links instead."}
    return {"path": out, "count": len(files)}


# --------------------------------------------------------------------------- #
# /logs, /checkup, status line
# --------------------------------------------------------------------------- #
_SECRETISH = re.compile(r"(?i)(password|passwd|token|secret|apikey|api_key)(\s*[=:]\s*)(\S+)")


def log_tail(mgr, sid: str, lines: int = 30) -> str:
    path = mgr.get_log_path(sid)
    if not path or not os.path.isfile(path):
        return ""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 64 * 1024))
        text = f.read().decode("utf-8", "replace")
    out = text.splitlines()[-max(1, min(int(lines), 80)):]
    return _SECRETISH.sub(lambda m: m.group(1) + m.group(2) + "****", "\n".join(out)).replace("```", "'''")


def checkup_text(mgr, visible_ids: Optional[set] = None) -> str:
    from core import checkup
    res = checkup.run(mgr, firewall=False)
    icon = {"problem": "\U0001F534", "warning": "\U0001F7E0", "tip": "⚪"}
    items = [i for i in res["items"] if i["level"] != "tip"
             and (visible_ids is None or not i.get("server_id") or i["server_id"] in visible_ids)]
    if not items:
        return "✅ Nothing needs attention."
    lines = []
    for i in items[:15]:
        lines.append("%s %s%s\n   %s" % (icon.get(i["level"], ""), ("**%s** - " % i["server"]) if i.get("server") else "",
                                         i["title"], i.get("fix") or ""))
    if len(items) > 15:
        lines.append("…and %d more - open Checkup in BaoBun." % (len(items) - 15))
    return "\n".join(lines)[:1990]


def presence_text(statuses: List[dict]) -> str:
    running = [s for s in statuses if s.get("isRunning")]
    players = sum(int(s.get("playerCount") or 0) for s in running)
    if not running:
        return "all servers asleep"
    return "%d server%s · %d player%s" % (len(running), "" if len(running) == 1 else "s", players,
                                         "" if players == 1 else "s")


# --------------------------------------------------------------------------- #
# setup from Discord (stored in settings, never in discord_config.json)
# --------------------------------------------------------------------------- #
def setup_values(mgr) -> dict:
    v = mgr.get_setting("discord_setup", {}) or {}
    return v if isinstance(v, dict) else {}


def save_setup(mgr, **values) -> dict:
    cur = dict(setup_values(mgr))
    for k, v in values.items():
        if k not in SETUP_KEYS or v is None:
            continue
        typ = SETUP_KEYS[k]
        if typ is int:
            cur[k] = int(v)
        elif typ is list:
            cur[k] = [int(x) for x in (v if isinstance(v, (list, tuple)) else [v]) if x]
        else:
            cur[k] = bool(v)
    mgr.set_setting("discord_setup", cur)
    return cur


def merged_config(raw: dict, setup: dict) -> dict:
    """discord_config.json with anything set from Discord (/baobun setup) on top."""
    out = dict(raw or {})
    for k, v in (setup or {}).items():
        if k in SETUP_KEYS and v not in (None, 0, []):
            out[k] = v
    return out


def is_admin(user_id: int, role_ids: set, admin_ids: set, admin_role_ids: set, owner_id: int = 0,
             manage_guild: bool = False) -> bool:
    """Admins: listed users/roles, the Discord server's owner, and anyone allowed to
    Manage Server - so a fresh install works without editing files."""
    return (int(user_id) in admin_ids or bool(set(role_ids) & admin_role_ids) or
            (owner_id and int(user_id) == int(owner_id)) or bool(manage_guild))


def setup_summary(cfg: dict) -> str:
    def ch(k):
        return "<#%d>" % cfg[k] if cfg.get(k) else "not set"

    def roles(k):
        return ", ".join("<@&%d>" % r for r in cfg.get(k) or []) or "-"
    return ("**BaoBun bot setup**\n"
            "Control panel: %s\nActivity feed: %s\nAlerts: %s\nGame-night events: %s\n"
            "Admin roles: %s (the server owner and anyone with Manage Server are admins too)\n"
            "Who may start servers: %s\nShow join address: %s"
            % (ch("channel_id"), ch("log_channel_id"), ch("alert_channel_id"), ch("event_channel_id"),
               roles("admin_role_ids"), roles("start_role_ids") if cfg.get("start_role_ids") else "everyone",
               "yes" if cfg.get("show_join_address") else "no"))


def _esc(s: str) -> str:
    return re.sub(r"([*_`~|>\[\]])", r"\\\1", str(s))
