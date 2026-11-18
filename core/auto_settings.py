"""Essentials for games Tofu has no hand-made settings page for.

Most games name their important settings alike - server name, password, admin
password, max players, port, PvP, public listing, description - just spelled
differently (ServerName, server_name, hostname, MaxPlayers, max-players ...).
This finds them in the server's settings files (the files Tofu knows for the
game first, then the ones found in its folder, newest from the game's first
start first) and offers them as a simple Essentials page. Saving writes them back
through the normal settings editor (core/config_forms.py), which keeps the file's
own formatting and a .bak copy.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List

# essential -> (label, accepted key spellings after lowercasing and removing _ - . and spaces)
ESSENTIALS = [
    ("name", "Server name", {"servername", "hostname", "srvname", "publicname", "sessionname", "steamservername", "gamename", "worldname"}),
    ("description", "Description / message", {"serverdescription", "description", "motd", "welcomemessage",
                                              "servermessage"}),
    ("password", "Password to join", {"serverpassword", "password", "srvpassword", "joinpassword", "gamepassword",
                                      "serverpass"}),
    ("admin", "Admin password", {"adminpassword", "serveradminpassword", "adminpass", "rconpassword"}),
    ("players", "Max players", {"maxplayers", "maxplayer", "srvmaxplayers", "playerlimit", "slotcount", "maxconnectedplayers",
                                "maxusers", "maxclients", "players"}),
    ("port", "Game port", {"port", "gameport", "serverport", "srvport"}),
    ("pvp", "PvP", {"pvp", "enablepvp", "ispvp", "serverpvp", "pvpenabled", "allowpvp", "bpvp"}),
    ("public", "Listed publicly", {"public", "ispublic", "publiclobby", "listed", "visibleinserverbrowser"}),
]
MAX_FILES = 6


def _norm(key: str) -> str:
    return re.sub(r"[\s_\-.]", "", str(key)).lower()


def find(mgr, server_id: str) -> List[dict]:
    """[{"essential", "label", "file", "field", "type", "value", "set"}] - one per essential found."""
    from core import config_forms as cf
    files = [f for f in cf.files(mgr, server_id) if f.get("format") and f.get("exists")]
    files.sort(key=lambda f: (not f.get("known"), not f.get("fresh")))
    found: Dict[str, dict] = {}
    for f in files[:MAX_FILES]:
        try:
            form = cf.form(mgr, server_id, f["id"])
        except Exception:
            continue
        for g in form.get("groups") or []:
            for fld in g.get("fields") or []:
                if fld.get("readonly") or fld.get("missing"):
                    continue
                k = _norm(fld.get("key") or "")
                for ess, label, names in ESSENTIALS:
                    if ess in found or k not in names:
                        continue
                    found[ess] = {"essential": ess, "label": label, "file": f["id"], "file_path": f["path"],
                                  "field": fld["id"], "type": fld.get("type") or "text",
                                  "value": fld.get("value"), "set": fld.get("set")}
        if len(found) == len(ESSENTIALS):
            break
    return [found[e[0]] for e in ESSENTIALS if e[0] in found]


def get(mgr, server_id: str) -> dict:
    items = find(mgr, server_id)
    if not items:
        return {"supported": False, "fields": [],
                "note": "Tofu doesn't have quick settings for this game, and didn't spot the usual ones (server "
                        "name, password, max players...) in its settings files - use All settings. Many games "
                        "write their settings files on their first start."}
    fields = []
    for it in items:
        typ = it["type"]
        f = {"key": "auto" + json.dumps([it["file"], it["field"]]), "label": it["label"],
             "type": "password" if typ == "password" or it["essential"] in ("password", "admin") else
             ("bool" if typ == "bool" else ("int" if typ == "int" and it["essential"] in ("players", "port")
                                            else "text"))}
        if f["type"] == "password":
            f["value"], f["has_value"] = "", bool(it.get("set") or it.get("value"))
        elif f["type"] == "bool":
            v = it.get("value")
            f["value"] = "true" if (v is True or str(v).lower() in ("1", "true", "yes", "on")) else "false"
        else:
            f["value"] = "" if it.get("value") is None else str(it["value"])
        fields.append(f)
    files = sorted({it["file_path"] for it in items})
    return {"supported": True, "auto": True, "fields": fields,
            "note": "Found in %s. Tofu picked these out automatically - check them in All settings if a value "
                    "looks off." % ", ".join(files)}


def save(mgr, server_id: str, values: dict, user: str = "system") -> dict:
    from core import config_forms as cf
    by_file: Dict[str, dict] = {}
    for key, value in values.items():
        if not str(key).startswith("auto[") or str(key).endswith("__clear"):
            continue
        try:
            file_id, field_id = json.loads(key[4:])
        except (ValueError, TypeError):
            continue
        if value in (None, "") and not values.get(key + "__clear"):
            continue          # blank = keep (passwords)
        by_file.setdefault(file_id, {})[field_id] = value
    saved, restart = [], False
    for file_id, changes in by_file.items():
        r = cf.save(mgr, server_id, file_id, changes, user=user)
        saved += r.get("saved") or []
        restart = restart or bool(r.get("restart_needed"))
    return {"saved": saved, "restart_needed": restart}
