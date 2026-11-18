"""Where each game keeps its RCON settings, and how to talk to it over RCON.

Several popular servers (ARK, Palworld, Conan Exiles, Squad, V Rising, and the
BattlEye games Arma 3, DayZ and Arma Reforger) ignore
commands typed into their console window, so Tofu's console, quick commands,
in-game warnings and clean stops go over RCON for them. Others (Minecraft,
Project Zomboid, Source games) read the console fine; RCON is then only used
when the console isn't attached (e.g. the server was started outside Tofu).

info(srv) reads the port/password from the game's own config (or the launch
options) - nothing is stored by Tofu. Profiles are keyed by Steam App ID,
plus "minecraft".
"""

from __future__ import annotations

import os
import re
import time
from typing import Dict, List, Optional

GUS = "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini"
PAL = ("{palini}", "/Script/Pal.PalGameWorldSettings", "OptionSettings")

PROFILES: Dict[str, dict] = {
    "2430930": {"name": "ARK: Survival Ascended", "stdin": False, "marker": False,
                "broadcast": "Broadcast {message}", "stop": ["SaveWorld", "DoExit"], "save": "SaveWorld",
                "players": "ListPlayers",
                "setup": "Turn on RCON: add ?RCONEnabled=True?RCONPort=27020 to the launch options and set "
                         "an admin password (Game Settings tab)."},
    "376030": {"name": "ARK: Survival Evolved", "stdin": False, "marker": False,
               "broadcast": "Broadcast {message}", "stop": ["SaveWorld", "DoExit"], "save": "SaveWorld",
               "players": "ListPlayers",
               "setup": "Turn on RCON: add ?RCONEnabled=True?RCONPort=27020 to the launch options and set an "
                        "admin password."},
    "2394010": {"name": "Palworld", "stdin": False, "marker": False,
                "broadcast": "Broadcast {message}", "stop": ["Save", "DoExit"], "save": "Save",
                "players": "ShowPlayers",
                "setup": "Turn on RCON in PalWorldSettings.ini: RCONEnabled=True, RCONPort=25575 and an "
                         "AdminPassword (the Game Settings tab sets the password)."},
    "443030": {"name": "Conan Exiles", "stdin": False, "marker": False,
               "broadcast": "broadcast {message}", "players": "ListPlayers",
               "setup": "Turn on RCON in ConanSandbox/Saved/Config/WindowsServer/Game.ini: "
                        "[RconPlugin] RconEnabled=1, RconPort=25575, RconPassword=..."},
    "403240": {"name": "Squad", "stdin": False, "marker": False,
               "broadcast": "AdminBroadcast {message}", "players": "ListPlayers",
               "setup": "Set Port and Password in SquadGame/ServerConfig/Rcon.cfg."},
    "1829350": {"name": "V Rising", "stdin": False, "marker": False,
                "broadcast": "announce {message}",
                "setup": "Turn on RCON in ServerHostSettings.json: \"Rcon\": {\"Enabled\": true, "
                         "\"Port\": 25575, \"Password\": \"...\"}."},
    # BattlEye RCon (UDP)
    "233780": {"name": "Arma 3", "stdin": False, "protocol": "battleye",
               "broadcast": "say -1 {message}", "stop": ["#shutdown"], "players": "players",
               "setup": "Set RConPassword and RConPort in BattlEye\\beserver_x64.cfg (in the server's "
                        "BattlEye folder or -bepath)."},
    "223350": {"name": "DayZ", "stdin": False, "protocol": "battleye",
               "broadcast": "say -1 {message}", "stop": ["#shutdown"], "players": "players",
               "setup": "Set RConPassword and RConPort in <profiles>\\BattlEye\\BEServer_x64.cfg."},
    "1874900": {"name": "Arma Reforger", "stdin": False, "protocol": "battleye",
                "setup": "Add an \"rcon\": {\"address\": \"127.0.0.1\", \"port\": 19999, \"password\": \"...\"} "
                         "block to the server's config.json."},
    "380870": {"name": "Project Zomboid", "stdin": True, "marker": False},
    "minecraft": {"name": "Minecraft", "stdin": True, "marker": False},
    "4020": {"name": "Garry's Mod", "stdin": True, "marker": True},
    "730": {"name": "Counter-Strike 2", "stdin": True, "marker": True},
    "740": {"name": "CS:GO", "stdin": True, "marker": True},
    "232250": {"name": "Team Fortress 2", "stdin": True, "marker": True},
    "222860": {"name": "Left 4 Dead 2", "stdin": True, "marker": True},
}
ALIASES = {"108600": "380870", "1890870": "1874900", "221100": "223350"}
_cache: Dict[str, tuple] = {}


def key_for(srv: dict) -> Optional[str]:
    a = str(srv.get("appId") or "")
    if srv.get("clusterId") and not a:
        a = "2430930"
    a = ALIASES.get(a, a)
    if a in PROFILES:
        return a
    if not a and (srv.get("type") or "").lower() != "vintage-story":
        from core.minecraft import looks_minecraft
        if looks_minecraft(srv):
            return "minecraft"
    return None


def profile(srv: dict) -> Optional[dict]:
    k = key_for(srv)
    return PROFILES.get(k) if k else None


def _truthy(v) -> bool:
    return str(v or "").strip().strip('"').lower() in ("1", "true", "yes", "on")


def _int(v) -> Optional[int]:
    try:
        n = int(str(v).strip().strip('"'))
        return n if 0 < n < 65536 else None
    except (TypeError, ValueError):
        return None


def _detect(srv: dict, k: str) -> Optional[dict]:
    from core.quick_settings import read_value as rv
    from core.mods_common import get_arg
    args = srv.get("launchArgs") or ""
    d = srv.get("directory") or ""

    def safe(where):
        try:
            return rv(srv, where)
        except Exception:
            return None

    if k in ("2430930", "376030"):
        port = _int(get_arg(args, "RCONPort")) or _int(_url(args, "RCONPort"))
        pw = _url(args, "ServerAdminPassword")
        enabled = _truthy(_url(args, "RCONEnabled")) or port is not None
        if not (port and pw):
            port = port or _int(safe(("ini", GUS, "ServerSettings", "RCONPort")))
            pw = pw or safe(("ini", GUS, "ServerSettings", "ServerAdminPassword"))
            enabled = enabled or _truthy(safe(("ini", GUS, "ServerSettings", "RCONEnabled")))
        if pw and enabled:
            return {"port": port or 27020, "password": pw}
        return None
    if k == "2394010":
        if not _truthy(safe(("ini_tuple",) + PAL + ("RCONEnabled",))):
            return None
        pw = safe(("ini_tuple",) + PAL + ("AdminPassword",))
        return {"port": _int(safe(("ini_tuple",) + PAL + ("RCONPort",))) or 25575, "password": pw} if pw else None
    if k == "443030":
        ini = "ConanSandbox/Saved/Config/WindowsServer/Game.ini"
        if not _truthy(safe(("ini", ini, "RconPlugin", "RconEnabled"))):
            return None
        pw = safe(("ini", ini, "RconPlugin", "RconPassword"))
        return {"port": _int(safe(("ini", ini, "RconPlugin", "RconPort"))) or 25575, "password": pw} if pw else None
    if k == "403240":
        cfg = "SquadGame/ServerConfig/Rcon.cfg"
        pw = safe(("ini", cfg, None, "Password"))
        port = _int(safe(("ini", cfg, None, "Port")))
        return {"port": port or 21114, "password": pw} if pw else None
    if k == "1829350":
        if not _truthy(safe(("json", "{vrising}", "Rcon.Enabled"))):
            return None
        pw = safe(("json", "{vrising}", "Rcon.Password"))
        return {"port": _int(safe(("json", "{vrising}", "Rcon.Port"))) or 25575, "password": pw} if pw else None
    if k == "380870":
        pw = safe(("ini", "{pzini}", None, "RCONPassword"))
        return {"port": _int(safe(("ini", "{pzini}", None, "RCONPort"))) or 27015, "password": pw} if pw else None
    if k in ("233780", "223350"):
        return _battleye_cfg(srv)
    if k == "1874900":
        pw = safe(("json", "{config}", "rcon.password"))
        port = _int(safe(("json", "{config}", "rcon.port")))
        return {"port": port or 19999, "password": pw} if pw else None
    if k == "minecraft":
        from core.minecraft import read_properties
        p = read_properties(d)
        if not _truthy(p.get("enable-rcon")) or not p.get("rcon.password"):
            return None
        return {"port": _int(p.get("rcon.port")) or 25575, "password": p["rcon.password"]}
    # Source engine: +rcon_password on the command line or in cfg/server.cfg
    pw = get_arg(args, "rcon_password")
    if not pw:
        for cfg in ("garrysmod/cfg/server.cfg", "game/csgo/cfg/server.cfg", "csgo/cfg/server.cfg",
                    "tf/cfg/server.cfg", "left4dead2/cfg/server.cfg"):
            pw = safe(("cfg", cfg, "rcon_password"))
            if pw:
                break
    if not pw:
        return None
    port = _int(get_arg(args, "port")) or _int(srv.get("port")) or 27015
    return {"port": port, "password": pw}


def _battleye_cfg(srv: dict) -> Optional[dict]:
    """RConPassword / RConPort from beserver_x64.cfg (Arma 3, DayZ)."""
    from core.mods_common import get_arg
    d = srv.get("directory") or ""
    args = srv.get("launchArgs") or ""

    def full(p):
        return p if os.path.isabs(p) else os.path.join(d, p)
    dirs = []
    if get_arg(args, "bepath"):
        dirs.append(full(get_arg(args, "bepath")))
    if get_arg(args, "profiles"):
        dirs.append(os.path.join(full(get_arg(args, "profiles")), "BattlEye"))
    dirs += [os.path.join(d, "BattlEye"), os.path.join(d, "battleye")]
    for folder in dirs:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        cfgs = [n for n in names if n.lower().startswith("beserver") and n.lower().endswith(".cfg")]
        cfgs.sort(key=lambda n: ("_active_" in n.lower(), n))   # the file you edit beats BattlEye's copy
        for n in cfgs:
            pw, port = None, None
            try:
                with open(os.path.join(folder, n), encoding="utf-8", errors="replace") as f:
                    for line in f:
                        parts = line.strip().split(None, 1)
                        if len(parts) == 2 and parts[0].lower() == "rconpassword":
                            pw = parts[1].strip()
                        elif len(parts) == 2 and parts[0].lower() == "rconport":
                            port = _int(parts[1])
            except OSError:
                continue
            if pw:
                game = _int(get_arg(args, "port")) or _int(srv.get("port")) or 2302
                return {"port": port or game, "password": pw}
    return None


def _url(args: str, name: str) -> Optional[str]:
    m = re.search(r"[?&]%s=([^?&\s\"]*)" % re.escape(name), args or "", re.I)
    return m.group(1) if m else None


def info(srv: dict, max_age: float = 20.0) -> Optional[dict]:
    """{"host", "port", "password", "marker"} when RCON is set up, else None (cached briefly)."""
    k = key_for(srv)
    if not k:
        return None
    sig = (srv.get("id"), srv.get("launchArgs"), srv.get("directory"))
    hit = _cache.get(str(sig))
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    try:
        found = _detect(srv, k)
    except Exception:
        found = None
    res = None
    if found and found.get("password"):
        res = {"host": "127.0.0.1", "port": int(found["port"]), "password": str(found["password"]),
               "marker": bool(PROFILES[k].get("marker")), "protocol": PROFILES[k].get("protocol") or "source"}
    _cache[str(sig)] = (time.time(), res)
    return res


def forget(srv: Optional[dict] = None):
    _cache.clear()


def send(srv: dict, command: str, timeout: float = 10.0) -> str:
    """Run one command over RCON and return the reply (raises on failure)."""
    i = info(srv)
    if not i:
        prof = profile(srv) or {}
        raise RuntimeError("RCON isn't set up for this server. " + (prof.get("setup") or ""))
    if i.get("protocol") == "battleye":
        from core import battleye
        return battleye.command(i["host"], i["port"], i["password"], command, timeout=timeout)
    import rcon
    return rcon.rcon_command(i["host"], i["port"], i["password"], command, timeout=timeout,
                             use_marker=i["marker"])


def parse_players(k: str, text: str) -> Optional[List[str]]:
    """Player names from a ListPlayers / ShowPlayers reply."""
    if text is None:
        return None
    lines = [l.strip() for l in str(text).splitlines() if l.strip()]
    if k == "2394010":   # name,playeruid,steamid
        return [l.split(",")[0] for l in lines[1:] if "," in l and l.split(",")[0]]
    if k in ("2430930", "376030"):   # "0. Name, 0002..." / "No Players Connected"
        out = []
        for l in lines:
            m = re.match(r"^\d+\.\s*(.+?),\s*\S+$", l)
            if m:
                out.append(m.group(1))
        return out
    if k == "443030":   # "Idx | Char name | Player name | ..."
        out = []
        for l in lines[1:]:
            cols = [c.strip() for c in l.split("|")]
            if len(cols) >= 3 and cols[2]:
                out.append(cols[2])
        return out
    if k in ("233780", "223350"):
        from core.battleye import parse_players as be
        return be(text)
    if k == "403240":   # "ID: 0 | Online IDs: ... | Name: Bob | Team ID: ..."
        out = []
        for l in lines:
            if l.startswith("----- Recently Disconnected"):
                break
            m = re.search(r"\|\s*Name:\s*(.*?)\s*\|", l)
            if m:
                out.append(m.group(1))
        return out
    return None
