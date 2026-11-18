"""Turn on RCON for a server: its own free port and a strong random password.

Games like ARK, Palworld, Conan, V Rising, Arma and DayZ ignore commands typed
into their console window, so Tofu needs RCON to warn players, save before a
backup and stop them cleanly. enable() switches it on in the game's own config:

* the port comes from the port plan (never one another server uses)
* an existing password is kept; a blank one gets a new random password, which
  is returned once so the panel can show it (in ARK and Palworld it is also the
  in-game admin password)

BattlEye's beserver_x64.cfg (Arma 3, DayZ) is handled here too (be_read / be_write).
"""

from __future__ import annotations

import os
import secrets
import string
from typing import Dict, Optional

from core.port_plan import PAL, CONAN_GAME, key_for

GUS = "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini"
SUPPORTED = {
    "2430930": "ARK: Survival Ascended", "376030": "ARK: Survival Evolved", "2394010": "Palworld",
    "443030": "Conan Exiles", "1829350": "V Rising", "minecraft": "Minecraft", "233780": "Arma 3",
    "223350": "DayZ", "1874900": "Arma Reforger", "403240": "Squad", "380870": "Project Zomboid",
}
ADMIN_NOTE = {
    "2430930": "This is also the in-game admin password (enablecheats).",
    "376030": "This is also the in-game admin password (enablecheats).",
    "2394010": "This is also the in-game admin password (/AdminPassword).",
}


def new_password(n: int = 20) -> str:
    """Letters and digits only - every game's config format accepts those unquoted."""
    abc = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(abc) for _ in range(n))
        if any(c.isdigit() for c in pw) and any(c.isupper() for c in pw) and any(c.islower() for c in pw):
            return pw


def supported(srv: dict) -> bool:
    return key_for(srv) in SUPPORTED


# --------------------------------------------------------------------------- #
# BattlEye cfg
# --------------------------------------------------------------------------- #
def _be_dirs(srv: dict):
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
    return dirs


def be_path(srv: dict, create: bool = False) -> Optional[str]:
    """The beserver_x64.cfg you edit (not BattlEye's *_active_* copy)."""
    for folder in _be_dirs(srv):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        cfgs = [n for n in names if n.lower().startswith("beserver") and n.lower().endswith(".cfg")
                and "_active_" not in n.lower()]
        if cfgs:
            pick = next((n for n in cfgs if n.lower() == "beserver_x64.cfg"), cfgs[0])
            return os.path.join(folder, pick)
    if not create:
        return None
    folder = _be_dirs(srv)[0]
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "beserver_x64.cfg")


def be_read(srv: dict) -> Dict[str, str]:
    """{lowercase key: value} from the BattlEye cfg."""
    p = be_path(srv)
    out: Dict[str, str] = {}
    if not p:
        return out
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    out[parts[0].lower()] = parts[1].strip()
    except OSError:
        pass
    return out


def be_write(srv: dict, values: Dict[str, str]):
    p = be_path(srv, create=True)
    lines = []
    if os.path.isfile(p):
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    for key, val in values.items():
        for i, line in enumerate(lines):
            if line.strip().split(None, 1)[:1] and line.strip().split(None, 1)[0].lower() == key.lower():
                lines[i] = "%s %s" % (key, val)
                break
        else:
            lines.append("%s %s" % (key, val))
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, p)


# --------------------------------------------------------------------------- #
def _ensure_file(srv: dict, rel: str):
    """Create an empty config file (and its folders) for games that are happy to
    fill in the rest themselves (Conan's Game.ini, Minecraft's server.properties)."""
    p = os.path.join(srv.get("directory") or "", rel)
    if not os.path.isfile(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "a", encoding="utf-8").close()


def enable(mgr, server_id: str, user: str = "system") -> dict:
    """Switch RCON on. Returns {"port", "password" (only when newly made), "kept_password",
    "note"}. Never logs the password."""
    from core import port_plan, rcon_config
    from core.quick_settings import read_value as rv, write_value as wv
    if mgr.is_running(server_id):
        raise RuntimeError("Stop the server first - the game only reads RCON settings when it starts.")
    servers = mgr.load_servers()
    srv = next((s for s in servers if s.get("id") == server_id), None)
    if srv is None:
        raise ValueError("Server not found")
    k = key_for(srv)
    if k not in SUPPORTED:
        raise ValueError("Tofu can't set up RCON for this game automatically.")

    def safe(where):
        try:
            return rv(srv, where)
        except Exception:
            return None

    # a free port for RCON (keeps the current one when nobody else uses it)
    port = None
    if any(r["role"] == "rcon" for r in port_plan.spec(srv) or []):
        port = port_plan.plan(mgr, srv).get("rcon")
    args = srv.get("launchArgs") or ""
    pw_existing = None
    try:
        if k in ("2430930", "376030"):
            pw_existing = rcon_config._url(args, "ServerAdminPassword") or safe(("ini", GUS, "ServerSettings",
                                                                                   "ServerAdminPassword"))
            pw = pw_existing or new_password()
            for key, val in (("RCONEnabled", "True"),) + (() if pw_existing else (("ServerAdminPassword", pw),)):
                srv["launchArgs"] = wv(srv, ("ark_url", key), val, "text")[1]
            port_plan.write_port(srv, ("ark", "RCONPort"), port)
        elif k == "2394010":
            pw_existing = safe(("ini_tuple",) + PAL + ("AdminPassword",))
            pw = pw_existing or new_password()
            wv(srv, ("ini_tuple",) + PAL + ("RCONEnabled",), "True", "bool")
            wv(srv, ("ini_tuple",) + PAL + ("RCONPort",), str(port), "int")
            if not pw_existing:
                wv(srv, ("ini_tuple",) + PAL + ("AdminPassword",), pw, "password")
        elif k == "443030":
            _ensure_file(srv, CONAN_GAME)
            pw_existing = safe(("ini", CONAN_GAME, "RconPlugin", "RconPassword"))
            pw = pw_existing or new_password()
            wv(srv, ("ini", CONAN_GAME, "RconPlugin", "RconEnabled"), "1", "int")
            wv(srv, ("ini", CONAN_GAME, "RconPlugin", "RconPort"), str(port), "int")
            if not pw_existing:
                wv(srv, ("ini", CONAN_GAME, "RconPlugin", "RconPassword"), pw, "text")
        elif k == "1829350":
            pw_existing = safe(("json", "{vrising}", "Rcon.Password"))
            pw = pw_existing or new_password()
            wv(srv, ("json", "{vrising}", "Rcon.Enabled"), "true", "bool")
            wv(srv, ("json", "{vrising}", "Rcon.Port"), str(port), "int")
            if not pw_existing:
                wv(srv, ("json", "{vrising}", "Rcon.Password"), pw, "text")
        elif k == "minecraft":
            _ensure_file(srv, "server.properties")
            pw_existing = safe(("ini", "server.properties", None, "rcon.password"))
            pw = pw_existing or new_password()
            wv(srv, ("ini", "server.properties", None, "enable-rcon"), "true", "text")
            wv(srv, ("ini", "server.properties", None, "rcon.port"), str(port), "int")
            if not pw_existing:
                wv(srv, ("ini", "server.properties", None, "rcon.password"), pw, "text")
        elif k in ("233780", "223350"):
            pw_existing = be_read(srv).get("rconpassword")
            pw = pw_existing or new_password()
            vals = {"RConPort": str(port)}
            if not pw_existing:
                vals["RConPassword"] = pw
            be_write(srv, vals)
        elif k == "1874900":
            pw_existing = safe(("json", "{config}", "rcon.password"))
            pw = pw_existing or new_password()
            if not safe(("json", "{config}", "rcon.address")):
                wv(srv, ("json", "{config}", "rcon.address"), "127.0.0.1", "text")
            wv(srv, ("json", "{config}", "rcon.port"), str(port), "int")
            if not safe(("json", "{config}", "rcon.permission")):
                wv(srv, ("json", "{config}", "rcon.permission"), "admin", "text")
            if not pw_existing:
                wv(srv, ("json", "{config}", "rcon.password"), pw, "text")
        elif k == "403240":
            cfg = "SquadGame/ServerConfig/Rcon.cfg"
            pw_existing = safe(("ini", cfg, None, "Password"))
            pw = pw_existing or new_password()
            wv(srv, ("ini", cfg, None, "Port"), str(port), "int")
            if not pw_existing:
                wv(srv, ("ini", cfg, None, "Password"), pw, "text")
        elif k == "380870":
            pw_existing = safe(("ini", "{pzini}", None, "RCONPassword"))
            pw = pw_existing or new_password()
            wv(srv, ("ini", "{pzini}", None, "RCONPort"), str(port), "int")
            if not pw_existing:
                wv(srv, ("ini", "{pzini}", None, "RCONPassword"), pw, "text")
    except FileNotFoundError as e:
        raise RuntimeError(str(e))
    ports = dict(srv.get("ports") or {})
    if port:
        ports["rcon"] = port
        srv["ports"] = ports
    mgr.save_servers(servers)
    rcon_config.forget(srv)
    mgr._audit("rcon_setup", server_id, user=user, port=port, new_password=not pw_existing)
    note = "RCON is on (port %s, only reachable from this PC)." % port
    if not pw_existing:
        note += " Tofu made a random password and saved it in the game's settings - you'll find it there " \
                "again in Settings."
        if ADMIN_NOTE.get(k):
            note += " " + ADMIN_NOTE[k]
    else:
        note += " Your existing password was kept."
    return {"port": port, "password": None if pw_existing else pw, "kept_password": bool(pw_existing),
            "note": note}
