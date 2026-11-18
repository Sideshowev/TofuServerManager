"""Players: who may join (allow list / whitelist), who is banned, who is an admin.

Each game keeps these differently, so Tofu has one small adapter per game and
shows them the same way everywhere (desktop Players window, web panel, Discord
/access):

* Minecraft - whitelist.json, ops.json, banned-players.json and white-list= in
  server.properties. While the server runs, changes go through its console (so
  the server writes its own files); while it's stopped Tofu edits the files
  and looks the player's id up (or works out the offline-mode id).
* Vintage Story - lists are read from the data folder's Playerdata files;
  changes go through the console (/player ... whitelist, /player ... role,
  /ban, /unban), so the server must be running.
* Valheim - adminlist.txt, bannedlist.txt, permittedlist.txt (Steam IDs) in the
  save folder; edited directly, the game picks them up while running.
* ARK - AllowedCheaterAccountIDs.txt (admins) and PlayersExclusiveJoinList.txt
  (allow list, used with -exclusivejoin) in ShooterGame/Saved; bans go over
  RCON (BanPlayer / UnbanPlayer) while running.
* Anything else whose console has a ban command (Rust, Project Zomboid, ...) gets
  ban / unban through the console while running.

Tofu also keeps its own record of every change (runtime/player_access.json:
what, who, when, by whom) so lists show names next to IDs and games that can't
list their bans still show what was done from Tofu.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid as uuidlib
from typing import Dict, List, Optional

LISTS = ("allow", "ban", "admin")
LIST_LABEL = {"allow": "Allowed to join", "ban": "Banned", "admin": "Admins"}
_lock = threading.Lock()


class AccessError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Tofu's own record
# --------------------------------------------------------------------------- #
def _ledger_path(mgr) -> str:
    return os.path.join(str(mgr.config_path.parent), "runtime", "player_access.json")


def _ledger(mgr) -> dict:
    try:
        with open(_ledger_path(mgr), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _record(mgr, sid: str, lst: str, action: str, who: dict, by: str, reason: str = ""):
    with _lock:
        data = _ledger(mgr)
        srv = data.setdefault(sid, {"entries": {}, "log": []})
        key = "%s:%s" % (lst, (who.get("id") or who.get("name") or "").lower())
        if action == "add":
            srv["entries"][key] = {"list": lst, "id": who.get("id"), "name": who.get("name"), "by": by,
                                   "at": time.time(), "reason": reason}
        else:
            srv["entries"].pop(key, None)
        srv["log"] = (srv["log"] + [{"at": time.time(), "list": lst, "action": action, "who": who.get("name") or
                                     who.get("id"), "by": by, "reason": reason}])[-200:]
        if who.get("id") and who.get("name"):
            data.setdefault("_names", {})[str(who["id"])] = who["name"]
        p = _ledger_path(mgr)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, p)


def known_names(mgr) -> Dict[str, str]:
    return dict(_ledger(mgr).get("_names") or {})


def history(mgr, sid: str, limit: int = 30) -> List[dict]:
    return list(reversed(((_ledger(mgr).get(sid) or {}).get("log") or [])[-limit:]))


# --------------------------------------------------------------------------- #
# small file helpers
# --------------------------------------------------------------------------- #
def _read_json(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _read_lines(path) -> List[str]:
    try:
        with open(path, encoding="utf-8-sig") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith(("//", "#"))]
    except OSError:
        return []


def _write_lines(path, lines: List[str], header: Optional[str] = None):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    keep_comments = []
    try:
        with open(path, encoding="utf-8-sig") as f:
            keep_comments = [ln.rstrip("\n") for ln in f if ln.strip().startswith("//")]
    except OSError:
        if header:
            keep_comments = [header]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(keep_comments + lines) + "\n")
    os.replace(tmp, path)


def _props(path) -> Dict[str, str]:
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                if "=" in ln and not ln.lstrip().startswith("#"):
                    k, v = ln.split("=", 1)
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _set_prop(path, key, value):
    lines = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        pass
    done = False
    for i, ln in enumerate(lines):
        if ln.split("=", 1)[0].strip() == key and not ln.lstrip().startswith("#"):
            lines[i] = "%s=%s" % (key, value)
            done = True
    if not done:
        lines.append("%s=%s" % (key, value))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _clean_name(name: str) -> str:
    name = str(name or "").strip()
    if not name or len(name) > 64 or re.search(r"[\x00-\x1f\"\\]", name):
        raise AccessError("That isn't a valid player name.")
    return name


# --------------------------------------------------------------------------- #
# adapters
# --------------------------------------------------------------------------- #
class Adapter:
    key = ""
    label = ""
    id_kind = "name"            # what you type: "name", "steamid" or "eosid"
    id_hint = "player name"
    lists = LISTS
    needs_running = ()          # lists that can only change while the server runs
    allow_toggle = False        # can the allow list be switched on/off

    def __init__(self, mgr, srv):
        self.mgr, self.srv = mgr, srv
        self.dir = srv.get("directory") or ""

    def running(self) -> bool:
        try:
            return bool(self.mgr.is_running(self.srv["id"]))
        except Exception:
            return False

    def console(self, cmd: str, by: str):
        return self.mgr.send_console_command(self.srv["id"], cmd, user=by)

    def read(self) -> dict:
        raise NotImplementedError

    def change(self, lst: str, action: str, who: str, reason: str, by: str) -> dict:
        raise NotImplementedError

    def set_allow_on(self, on: bool, by: str) -> dict:
        raise AccessError("This game has no on/off switch for its allow list.")

    def notes(self) -> List[str]:
        return []


class Minecraft(Adapter):
    key, label = "minecraft", "Minecraft"
    allow_toggle = True

    def _p(self, name):
        return os.path.join(self.dir, name)

    def read(self):
        props = _props(self._p("server.properties"))
        def names(f):
            return [{"id": e.get("uuid"), "name": e.get("name"), "reason": e.get("reason")}
                    for e in _read_json(self._p(f), []) if isinstance(e, dict)]
        return {"allow": names("whitelist.json"), "ban": names("banned-players.json"), "admin": names("ops.json"),
                "allow_on": props.get("white-list", "false").lower() == "true"}

    def player_uuid(self, name: str) -> str:
        props = _props(self._p("server.properties"))
        if props.get("online-mode", "true").lower() == "false":
            h = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
            h[6] = (h[6] & 0x0F) | 0x30
            h[8] = (h[8] & 0x3F) | 0x80
            return str(uuidlib.UUID(bytes=bytes(h)))
        return mojang_uuid(name)

    def change(self, lst, action, who, reason, by):
        name = _clean_name(who)
        if not re.fullmatch(r"[A-Za-z0-9_]{2,16}", name):
            raise AccessError("Minecraft names are 3-16 letters, numbers or _.")
        if self.running():
            cmd = {("allow", "add"): "whitelist add %s", ("allow", "remove"): "whitelist remove %s",
                   ("admin", "add"): "op %s", ("admin", "remove"): "deop %s",
                   ("ban", "add"): "ban %s" + (" " + reason if reason else ""), ("ban", "remove"): "pardon %s"
                   }[(lst, action)] % name
            self.console(cmd, by)
            return {"name": name, "how": "console"}
        f = {"allow": "whitelist.json", "admin": "ops.json", "ban": "banned-players.json"}[lst]
        data = [e for e in _read_json(self._p(f), []) if isinstance(e, dict)]
        rest = [e for e in data if str(e.get("name", "")).lower() != name.lower()]
        if action == "add":
            u = self.player_uuid(name)
            entry = {"uuid": u, "name": name}
            if lst == "admin":
                entry.update(level=4, bypassesPlayerLimit=False)
            if lst == "ban":
                entry.update(created=time.strftime("%Y-%m-%d %H:%M:%S +0000", time.gmtime()), source="Tofu",
                             expires="forever", reason=reason or "Banned by an operator.")
            rest.append(entry)
            _write_json(self._p(f), rest)
            return {"name": name, "id": u, "how": "file"}
        _write_json(self._p(f), rest)
        return {"name": name, "how": "file"}

    def set_allow_on(self, on, by):
        if self.running():
            self.console("whitelist %s" % ("on" if on else "off"), by)
            return {"how": "console"}
        _set_prop(self._p("server.properties"), "white-list", "true" if on else "false")
        return {"how": "file"}


def mojang_uuid(name: str, timeout: float = 8.0) -> str:
    """A Java Edition player's id from Mojang (needs internet)."""
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen("https://api.mojang.com/users/profiles/minecraft/%s" % name,
                                    timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        if e.code in (204, 404):
            raise AccessError("There's no Minecraft player called %s." % name)
        raise AccessError("Couldn't look the player up (%s) - start the server and try again." % e)
    except Exception as e:
        raise AccessError("Couldn't look the player up (%s) - start the server and try again." % e)
    raw = (data or {}).get("id")
    if not raw:
        raise AccessError("There's no Minecraft player called %s." % name)
    return str(uuidlib.UUID(raw))


class VintageStory(Adapter):
    key, label = "vintage-story", "Vintage Story"
    needs_running = LISTS

    def _pd(self):
        from server_manager import vs_data_path
        return os.path.join(vs_data_path(self.srv), "Playerdata")

    @staticmethod
    def _name(e):
        for k in e:
            if k.lower() in ("playername", "lastknownplayername", "name"):
                return e[k]
        return None

    @staticmethod
    def _uid(e):
        for k in e:
            if k.lower() in ("playeruid", "uid"):
                return e[k]
        return None

    def read(self):
        pd = self._pd()
        out = {"allow": [], "ban": [], "admin": [], "allow_on": None}
        try:
            files = os.listdir(pd)
        except OSError:
            return out
        for f in files:
            low = f.lower()
            if not low.endswith(".json"):
                continue
            data = _read_json(os.path.join(pd, f), [])
            if not isinstance(data, list):
                continue
            if "whitelist" in low:
                out["allow"] = [{"id": self._uid(e), "name": self._name(e)} for e in data if isinstance(e, dict)]
            elif "ban" in low:
                out["ban"] = [{"id": self._uid(e), "name": self._name(e), "reason": e.get("Reason")}
                              for e in data if isinstance(e, dict)]
            elif low == "playerdata.json":
                out["admin"] = [{"id": self._uid(e), "name": self._name(e)} for e in data if isinstance(e, dict)
                                and str(e.get("RoleCode") or "").lower() == "admin"]
        return out

    def change(self, lst, action, who, reason, by):
        name = _clean_name(who)
        if " " in name:
            raise AccessError("Vintage Story names have no spaces.")
        if not self.running():
            raise AccessError("Start the server first - Vintage Story changes these through its console.")
        cmd = {("allow", "add"): "/player %s whitelist on", ("allow", "remove"): "/player %s whitelist off",
               ("admin", "add"): "/player %s role admin", ("admin", "remove"): "/player %s role suplayer",
               ("ban", "add"): "/ban %s " + (reason or "Banned by an admin"), ("ban", "remove"): "/unban %s"
               }[(lst, action)] % name
        self.console(cmd, by)
        return {"name": name, "how": "console"}

    def notes(self):
        return ["Who may join without being on the list is set by WhitelistMode in serverconfig.json "
                "(Settings tab)."]


STEAM_ID = re.compile(r"^7656119\d{10}$")


class Valheim(Adapter):
    key, label = "valheim", "Valheim"
    id_kind, id_hint = "steamid", "Steam ID (17 digits, starts 7656119)"
    FILES = {"allow": "permittedlist.txt", "ban": "bannedlist.txt", "admin": "adminlist.txt"}

    def _savedir(self):
        from server_manager import split_args
        args = split_args(self.srv.get("launchArgs") or "")
        for i, a in enumerate(args):
            if a.lower().strip('"') == "-savedir" and i + 1 < len(args):
                p = args[i + 1].strip('"')
                return p if os.path.isabs(p) else os.path.join(self.dir, p)
        home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        return os.path.join(home, "AppData", "LocalLow", "IronGate", "Valheim")

    def read(self):
        names = known_names(self.mgr)
        out = {}
        for lst, f in self.FILES.items():
            out[lst] = [{"id": x, "name": names.get(x)} for x in _read_lines(os.path.join(self._savedir(), f))]
        out["allow_on"] = bool(out["allow"])
        return out

    def change(self, lst, action, who, reason, by):
        sid = str(who or "").strip()
        if not STEAM_ID.match(sid):
            raise AccessError("Valheim needs the player's Steam ID: 17 digits starting 7656119 (Steam profile "
                              "> Account details, or steamid.io).")
        path = os.path.join(self._savedir(), self.FILES[lst])
        cur = _read_lines(path)
        cur = [x for x in cur if x != sid] + ([sid] if action == "add" else [])
        _write_lines(path, cur, "// Managed by Tofu - one Steam ID per line")
        return {"id": sid, "how": "file"}

    def notes(self):
        return ["The allow list is on as soon as it has someone in it.",
                "Valheim notices changes to these files while running."]


class Ark(Adapter):
    key, label = "ark", "ARK"
    needs_running = ("ban",)
    allow_toggle = True

    def __init__(self, mgr, srv):
        super().__init__(mgr, srv)
        asa = str(srv.get("appId") or "") in ("2430930", "") or bool(srv.get("clusterId"))
        self.id_kind = "eosid" if asa else "steamid"
        self.id_hint = ("EOS ID (32 letters/numbers - players see it with the 'whoami' console command)" if asa
                        else "Steam ID (17 digits, starts 7656119)")

    def _saved(self, f):
        return os.path.join(self.dir, "ShooterGame", "Saved", f)

    def _valid(self, x):
        return bool(re.fullmatch(r"[0-9a-fA-F]{32}", x)) if self.id_kind == "eosid" else bool(STEAM_ID.match(x))

    def read(self):
        names = known_names(self.mgr)
        def rows(f):
            return [{"id": x, "name": names.get(x)} for x in _read_lines(self._saved(f))]
        led = (_ledger(self.mgr).get(self.srv["id"]) or {}).get("entries") or {}
        bans = [{"id": e.get("id"), "name": e.get("name"), "reason": e.get("reason")} for e in led.values()
                if e.get("list") == "ban"]
        return {"allow": rows("PlayersExclusiveJoinList.txt"), "admin": rows("AllowedCheaterAccountIDs.txt"),
                "ban": bans, "allow_on": "-exclusivejoin" in (self.srv.get("launchArgs") or "").lower()}

    def change(self, lst, action, who, reason, by):
        x = str(who or "").strip()
        if not self._valid(x):
            raise AccessError("ARK needs the player's %s." % self.id_hint)
        if lst == "ban":
            if not self.running():
                raise AccessError("Start the server first - ARK bans go through RCON.")
            self.console(("BanPlayer %s" if action == "add" else "UnbanPlayer %s") % x, by)
            return {"id": x, "how": "rcon"}
        f = {"allow": "PlayersExclusiveJoinList.txt", "admin": "AllowedCheaterAccountIDs.txt"}[lst]
        cur = [y for y in _read_lines(self._saved(f)) if y.lower() != x.lower()] + ([x] if action == "add" else [])
        _write_lines(self._saved(f), cur)
        return {"id": x, "how": "file", "restart_needed": self.running()}

    def set_allow_on(self, on, by):
        args = self.srv.get("launchArgs") or ""
        has = "-exclusivejoin" in args.lower()
        if on and not has:
            args = (args + " -exclusivejoin").strip()
        if not on and has:
            args = re.sub(r"(?i)\s*-exclusivejoin\b", "", args).strip()
        with self.mgr._lock:
            servers = self.mgr.load_servers()
            for s in servers:
                if s.get("id") == self.srv["id"]:
                    s["launchArgs"] = args
            self.mgr.save_servers(servers)
        return {"how": "launch", "restart_needed": self.running()}

    def notes(self):
        return ["Changes to the allow list and admins take effect when the server restarts.",
                "Admins listed here can use admin commands without the admin password."]


class ConsoleBans(Adapter):
    """Any game whose console has a ban command (from the Quick commands list)."""
    key, label = "console", "Console"
    lists = ("ban",)
    needs_running = ("ban",)

    def __init__(self, mgr, srv, ban_cmd, unban_cmd):
        super().__init__(mgr, srv)
        self.ban_cmd, self.unban_cmd = ban_cmd, unban_cmd

    def read(self):
        led = (_ledger(self.mgr).get(self.srv["id"]) or {}).get("entries") or {}
        return {"ban": [{"id": e.get("id"), "name": e.get("name"), "reason": e.get("reason")} for e in led.values()
                        if e.get("list") == "ban"], "allow_on": None}

    def change(self, lst, action, who, reason, by):
        name = _clean_name(who)
        if not self.running():
            raise AccessError("Start the server first - bans go through its console.")
        tmpl = self.ban_cmd if action == "add" else self.unban_cmd
        if not tmpl:
            raise AccessError("This game has no unban command - remove the ban in its own files.")
        self.console(tmpl.replace("{player}", name).replace("{text}", reason or "").strip(), by)
        return {"name": name, "how": "console"}


def adapter_for(mgr, srv: dict) -> Optional[Adapter]:
    from core import game_installers as gi
    k = gi.kind(srv)
    app = str(srv.get("appId") or "")
    if k == "minecraft":
        return Minecraft(mgr, srv)
    if k == "vintage-story":
        return VintageStory(mgr, srv)
    if app == "896660":
        return Valheim(mgr, srv)
    if app in ("2430930", "376030") or srv.get("clusterId"):
        return Ark(mgr, srv)
    try:
        from core import quick_commands
        cmds = {c["label"]: c["command"] for c in quick_commands.for_server(srv)}
    except Exception:
        cmds = {}
    ban = cmds.get("Ban a player")
    if ban:
        return ConsoleBans(mgr, srv, ban, cmds.get("Unban a player"))
    return None


# --------------------------------------------------------------------------- #
# the one interface used by desktop, web and Discord
# --------------------------------------------------------------------------- #
def overview(mgr, sid: str) -> dict:
    srv = mgr.get_server(sid)
    if not srv:
        raise AccessError("Server not found")
    a = adapter_for(mgr, srv)
    if a is None:
        return {"supported": False, "name": srv.get("name"),
                "why": "Tofu doesn't know how this game stores its allow list, bans or admins yet."}
    try:
        data = a.read()
    except Exception as e:
        data = {"error": str(e)}
    running = a.running()
    return {"supported": True, "name": srv.get("name"), "game": a.label, "lists": list(a.lists),
            "labels": {k: LIST_LABEL[k] for k in a.lists}, "id_kind": a.id_kind, "id_hint": a.id_hint,
            "allow_toggle": a.allow_toggle, "running": running,
            "needs_running": [x for x in a.needs_running if not running],
            "data": {k: data.get(k) or [] for k in a.lists}, "allow_on": data.get("allow_on"),
            "error": data.get("error"), "notes": a.notes(), "history": history(mgr, sid, 15)}


def change(mgr, sid: str, lst: str, action: str, who: str, reason: str = "", by: str = "system") -> dict:
    if lst not in LISTS or action not in ("add", "remove"):
        raise AccessError("Unknown list or action")
    srv = mgr.get_server(sid)
    if not srv:
        raise AccessError("Server not found")
    a = adapter_for(mgr, srv)
    if a is None or lst not in a.lists:
        raise AccessError("Tofu can't change that list for this game.")
    reason = re.sub(r"[\x00-\x1f]", " ", str(reason or ""))[:200].strip()
    try:
        res = a.change(lst, action, str(who or "").strip(), reason, by)
    except AccessError:
        raise
    except RuntimeError as e:
        raise AccessError(str(e))
    _record(mgr, sid, lst, action, {"id": res.get("id"), "name": res.get("name") or (None if res.get("id") else who)},
            by, reason)
    try:
        mgr._audit("player_access", sid, user=by, list=lst, action=action, who=res.get("name") or res.get("id"))
    except Exception:
        pass
    verb = {("allow", "add"): "can now join", ("allow", "remove"): "is no longer on the allow list",
            ("ban", "add"): "is banned", ("ban", "remove"): "is unbanned",
            ("admin", "add"): "is now an admin", ("admin", "remove"): "is no longer an admin"}[(lst, action)]
    res["text"] = "%s %s on %s%s" % (res.get("name") or res.get("id"), verb, srv.get("name"),
                                     " (takes effect after a restart)" if res.get("restart_needed") else "")
    return res


def change_many(mgr, sids: List[str], lst: str, action: str, who: str, reason: str = "",
                by: str = "system") -> Dict[str, dict]:
    """The same change on several servers (e.g. ban someone everywhere)."""
    out = {}
    for sid in sids:
        try:
            out[sid] = change(mgr, sid, lst, action, who, reason, by)
        except AccessError as e:
            out[sid] = {"error": str(e)}
    return out


def same_game(mgr, sid: str) -> List[str]:
    """Servers of the same kind as this one (for "on all my X servers")."""
    srv = mgr.get_server(sid) or {}
    a = adapter_for(mgr, srv)
    if a is None:
        return [sid]
    out = []
    for s in mgr.load_servers():
        b = adapter_for(mgr, s)
        if b is not None and b.key == a.key and getattr(b, "id_kind", None) == a.id_kind:
            out.append(s["id"])
    return out


def set_allow_on(mgr, sid: str, on: bool, by: str = "system") -> dict:
    srv = mgr.get_server(sid)
    if not srv:
        raise AccessError("Server not found")
    a = adapter_for(mgr, srv)
    if a is None:
        raise AccessError("Tofu can't change this for this game.")
    try:
        res = a.set_allow_on(bool(on), by)
    except RuntimeError as e:
        raise AccessError(str(e))
    try:
        mgr._audit("player_access", sid, user=by, list="allow", action="on" if on else "off")
    except Exception:
        pass
    return res


def discord_text(info: dict) -> str:
    """The lists as a Discord message (for /access ... show the lists)."""
    if not info.get("supported"):
        return info.get("why") or "Not supported."
    lines = ["**%s** (%s)" % (info["name"], info["game"])]
    if info.get("allow_on") is not None and "allow" in info["lists"]:
        lines.append("Allow list: %s" % ("**on** - only listed players can join" if info["allow_on"]
                                         else "off - anyone can join"))
    for k in info["lists"]:
        rows = info["data"].get(k) or []
        names = [re.sub(r"[*_`~|>@]", "", str(r.get("name") or r.get("id") or "?")) for r in rows[:40]]
        lines.append("**%s** (%d): %s%s" % (LIST_LABEL[k], len(rows), ", ".join(names) or "-",
                                           " ..." if len(rows) > 40 else ""))
    if info.get("needs_running"):
        lines.append("_Start the server to change: %s._" % ", ".join(LIST_LABEL[k].lower()
                                                                  for k in info["needs_running"]))
    return "\n".join(lines)[:1990]
