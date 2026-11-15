"""Launch options some games refuse to start without.

Most dedicated servers start with no options at all. A few exit straight away
unless they're told certain things - Valheim needs a server name, a world name
and a password of at least 5 characters (that isn't part of the server name).
BaoBun fills in whatever is missing when the server is added and again before
each start (so servers added another way are fixed too), and says what it chose
in the console log; everything can be changed in the server's Settings tab.
"""

from __future__ import annotations

import re
import secrets
from typing import List, Tuple

WORDS = ("amber", "birch", "cedar", "delta", "ember", "fjord", "grove", "harbor", "iris", "juniper",
         "kestrel", "lumen", "maple", "north", "otter", "pine", "quill", "raven", "stone", "tundra",
         "umber", "valley", "willow", "yarrow", "zephyr")


def make_password() -> str:
    """Easy to read out to friends: two words and two digits, e.g. "cedar-otter42"."""
    return "%s-%s%02d" % (secrets.choice(WORDS), secrets.choice(WORDS), secrets.randbelow(100))


def _has(args: str, flag: str) -> bool:
    return re.search(r'(?i)(^|\s)%s(\s|$)' % re.escape(flag), args or "") is not None


def _value(args: str, flag: str) -> str:
    m = re.search(r'(?i)(?:^|\s)%s\s+("([^"]*)"|(\S+))' % re.escape(flag), args or "")
    return (m.group(2) if m.group(2) is not None else m.group(3)) if m else ""


def _set(args: str, flag: str, value: str) -> str:
    quoted = '"%s"' % value if re.search(r"\s", value) else value
    if _has(args, flag):
        return re.sub(r'(?i)((?:^|\s)%s\s+)("[^"]*"|\S+)' % re.escape(flag), lambda m: m.group(1) + quoted, args,
                      count=1)
    return ("%s %s %s" % (args or "", flag, quoted)).strip()


def required(srv: dict) -> Tuple[str, List[str]]:
    """(launch options with anything missing filled in, notes on what was added)."""
    app = str(srv.get("appId") or "")
    args = srv.get("launchArgs") or ""
    notes: List[str] = []
    if app == "896660":                                   # Valheim
        name = _value(args, "-name") or (srv.get("name") or "BaoBun server")
        if not _value(args, "-name"):
            args = _set(args, "-name", name)
            notes.append('server name "%s"' % name)
        if not _value(args, "-port"):
            args = _set(args, "-port", str(srv.get("port") or 2456))
        if not _value(args, "-world"):
            args = _set(args, "-world", "Dedicated")
            notes.append('world "Dedicated"')
        pw = _value(args, "-password")
        if len(pw) < 5 or pw.lower() in name.lower():
            pw = make_password()
            while pw.lower() in name.lower():
                pw = make_password()
            args = _set(args, "-password", pw)
            notes.append("a password (Valheim needs one with at least 5 characters - it's shown in the Settings "
                         "tab; give it to your friends)")
        if not _has(args, "-nographics"):
            args = ("-nographics -batchmode " + args).strip()
        if not _value(args, "-public"):
            args = _set(args, "-public", "0")
    elif app not in SOURCE and _is_srcds(srv) and not _value(args, "-game"):
        # Any Source engine server (CS:S, HL2DM, DoD:S, mods ...) needs -game <its folder> and a map
        mod, mapname = _source_mod(srv.get("directory") or "")
        if mod:
            args = ("-game %s %s" % (mod, args)).strip()
            notes.append("game folder %s" % mod)
            if mapname and not _value(args, "+map"):
                args = _set(args, "+map", mapname)
                notes.append("start map %s" % mapname)
            if not _value(args, "-port"):
                args = _set(args, "-port", str(srv.get("port") or 27015))
            if not _has(args, "-console"):
                args = (args + " -console").strip()
    elif app in SOURCE:                                   # CS2 / TF2 / L4D2: no map, no server
        game, mapname = SOURCE[app]
        if app == "730" and not _has(args, "-dedicated"):
            args = ("-dedicated " + args).strip()
        if game and not _value(args, "-game"):
            args = ("-game %s " % game + args).strip()
        if not _value(args, "+map"):
            args = _set(args, "+map", mapname)
            notes.append("start map %s" % mapname)
        if not _value(args, "-port"):
            args = _set(args, "-port", str(srv.get("port") or 27015))
        if app != "730" and not _has(args, "-console"):
            args = (args + " -console").strip()
    elif app == "105600":                                 # Terraria asks which world to load
        if not _value(args, "-config") and not _value(args, "-world"):
            d = srv.get("directory") or "."
            world = d.rstrip("\\/") + ("\\" if "\\" in d or ":" in d else "/") + "Worlds" + \
                ("\\" if "\\" in d or ":" in d else "/") + "BaoBun.wld"
            args = _set(args, "-autocreate", "2")
            args = _set(args, "-worldname", "BaoBun")
            args = _set(args, "-world", world)
            notes.append('a world called "BaoBun" (made on the first start)')
        if not _value(args, "-port"):
            args = _set(args, "-port", str(srv.get("port") or 7777))
        if not _value(args, "-players"):
            args = _set(args, "-players", "8")
    elif app == "2465200":                                # Sons of the Forest
        # Its data folder defaults to the Windows user's AppData, shared by every server on the
        # PC. A server that has no data there yet keeps its settings and saves in its own folder.
        import os
        if not _either(args, "-userdatapath"):
            shared = os.path.join(os.path.expanduser("~"), "AppData", "LocalLow", "Endnight", "SonsOfTheForestDS")
            if not os.path.isdir(os.path.join(shared, "Saves")):       # no world there to keep using
                args = (args + " -userdatapath userdata").strip()
                notes.append("its own data folder (userdata, inside the server folder)")
    elif app in ("1874900", "1890870"):                   # Arma Reforger can't start without a config
        if not _either(args, "-config"):
            import os
            d = srv.get("directory") or ""        # full paths: the game doesn't find "config.json" by itself
            cfg, prof = (os.path.join(d, "config.json"), os.path.join(d, "profile")) if d else ("config.json", "profile")
            args = ('%s -config "%s" -profile "%s" -maxFPS 60' % (args, cfg, prof)).strip()
            notes.append("a settings file (config.json) - BaoBun makes a basic one; choose the scenario and "
                         "passwords in the Settings tab")
    elif app == "232130":                                 # Killing Floor 2 needs a map first
        if "kf-" not in args.lower():
            args = ("KF-BioticsLab " + args).strip()
            notes.append("start map KF-BioticsLab")
        if "-port=" not in args.lower():
            args = (args + " -Port=%s" % (srv.get("port") or 7777)).strip()
    elif app in ("380870", "108600"):                     # Project Zomboid asks for an admin password on
        # its first start and waits for someone to type it; -adminpassword answers it. It goes first
        # because the game's StartServer64.bat may only pass its first two options on.
        if not _either(args, "-adminpassword") and not _zomboid_has_admin(args):
            args = ("-adminpassword %s %s" % (make_password(), args)).strip()
            notes.append("an admin password (Project Zomboid asks for one the first time it starts - it's "
                         "shown in the Settings tab; log in as \"admin\" with it)")
    elif app in PREFIX:                                   # start options checked against WindowsGSM
        port = str(srv.get("port") or "")
        lead, rules = PREFIX[app]
        if lead and not args.lower().startswith(lead.split()[0].lower()):
            args = (lead + " " + args).strip()
            notes.append("the start options this game needs (%s)" % lead.split("?")[0])
        for flag, value in rules:
            if "{port}" in value and not port:
                continue
            value = value.replace("{port}", port)
            if flag.lower() not in args.lower():
                args = (args + " " + (flag + value)).strip()
    if app == "298740" and not _either(args, "-path"):
        # Space Engineers keeps its settings and worlds in the Windows user's AppData unless told
        # otherwise. A server with no settings there yet keeps them in its own folder.
        import os
        shared = os.path.join(os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"),
                              "SpaceEngineersDedicated", "SpaceEngineers-Dedicated.cfg")
        d = srv.get("directory") or ""
        if d and not os.path.isfile(shared):
            args = ('%s -path "%s"' % (args, os.path.join(d, "Instance"))).strip()
            notes.append("its own settings and worlds folder (Instance, inside the server folder) and a first "
                         "world (Star System)")
    if app == "600760" and not re.search(r"(?i)(^|\s)-(load|new|loadlatest|file)\b", args):
        # Stationeers sits at a "Ready" prompt and hosts nothing until it is told which world to
        # load; -load makes that world the first time. -settings must come last on the line.
        save = re.sub(r"[^A-Za-z0-9]", "", str(srv.get("name") or "")) or "BaoBun"
        port = int(srv.get("port") or 27500)
        args = ("%s -batchmode -nographics -load %s Lunar -settings StartLocalHost true ServerVisible true "
                "GamePort %d UpdatePort %d" % (args, save, port, port + 1)).strip()
        notes.append('a world to host ("%s", on the Moon)' % save)
    return args, notes


# app id -> (what must come first, [(flag, value) added when missing])
PREFIX = {
    "581330": ("Oilfield?Scenario=Scenario_Refinery_Push_Security?MaxPlayers=28",   # Insurgency: Sandstorm
               [("-Port=", "{port}"), ("-log", "")]),
    "530870": ("-batchmode -nographics -dedicated dedicated.yaml", []),              # Empyrion
    "298740": ("-console", []),                                                       # Space Engineers (no setup window)
    "1180760": ("-batchmode -nographics", []),                                        # Risk of Rain 2
    "739590": ("-batchmode -nographics", []),                                         # Eco
    "2857200": ("-log -useperfthreads -NoAsyncLoadingThread", [("-PORT=", "{port}")]),  # Abiotic Factor
    "3017310": ("Level01_Main -server -log -UTF8Output -forcepassthrough",           # Soulmask
                [("-Port=", "{port}"), ("-EchoPort=", "18888")]),
    "565060": ("--galaxy-name avorion_galaxy", []),                                   # Avorion
    "294420": ("-quit -batchmode -nographics -configfile=serverconfig.xml -dedicated", []),  # 7 Days to Die
    "2089300": ("-log", [("-Port=", "{port}")]),                                       # Icarus
    # Necesse: its own .bat opens the game in a second window and ends, so BaoBun thought it had
    # crashed. BaoBun runs the game's Java itself; without -world it stops to ask which world.
    "1169370": ("-jar Server.jar -nogui", [("-world ", "BaoBun"), ("-port ", "{port}")]),
}


SOURCE = {"730": ("", "de_dust2"), "232250": ("tf", "ctf_2fort"), "222860": ("left4dead2", "c1m1_hotel")}

# Games that only find their own files when started from the folder their program is in.
# Don't Starve Together looks for ..\data from there ("Could not load lua file scripts/main.lua").
STARTS_IN_OWN_FOLDER = {"343050"}


def working_dir(srv: dict, exe: str, directory: str) -> str:
    """The folder to start a game in: its install folder, except for the few games
    that have to be started from the folder their program is in."""
    import os
    if str(srv.get("appId") or "") in STARTS_IN_OWN_FOLDER:
        own = os.path.dirname(exe or "")
        if own and os.path.isdir(own):
            return own
    return directory


def new_server_args(srv: dict, others: list) -> str:
    """Launch options for a server that is being added right now (never for one that exists).
    Project Zomboid keeps every server's settings and world in one folder per Windows user,
    told apart only by -servername - and without it every server is "servertest", so a
    second server would share (and overwrite) the first one's settings and world. A new
    server gets a name of its own."""
    args = srv.get("launchArgs") or ""
    zomboid = ("380870", "108600")
    if str(srv.get("appId") or "") in zomboid and not _either(args, "-servername"):
        base = re.sub(r"[^A-Za-z0-9]", "", str(srv.get("name") or "")) or "server"
        used = {"servertest"} | {(_either(o.get("launchArgs") or "", "-servername") or "servertest").lower()
                                 for o in others or [] if str(o.get("appId") or "") in zomboid}
        name, i = base, 2
        while name.lower() in used:
            name, i = "%s%d" % (base, i), i + 1
        args = ("%s -servername %s" % (args, name)).strip()
    return args


def start_blocker(srv: dict) -> str:
    """Why this server can't work yet, in plain words ("" when nothing is in the way).
    Don't Starve Together runs but lets nobody in without a server token from Klei."""
    import os
    if str(srv.get("appId") or "") != "343050":
        return ""
    try:
        from core.quick_settings import _resolve
        token = _resolve(srv, "{dst}/cluster_token.txt")
    except Exception:
        return ""
    try:
        if os.path.isfile(token) and os.path.getsize(token) > 0:
            return ""
    except OSError:
        return ""
    return ("Don't Starve Together needs a server token from Klei before it can start. Sign in at "
            "https://accounts.klei.com/account/game/servers?game=DontStarveTogether , add a server, and "
            "either download its settings and put them in %s , or copy the token into a file called "
            "cluster_token.txt in that folder." % os.path.dirname(token))


def _either(args: str, flag: str) -> str:
    """Value of "-flag value" or "-flag=value"."""
    m = re.search(r'(?i)(?:^|\s)%s(?:=|\s+)("([^"]*)"|(\S+))' % re.escape(flag), args or "")
    return (m.group(2) if m.group(2) is not None else m.group(3)) if m else ""


def _zomboid_has_admin(args: str) -> bool:
    """True once Project Zomboid's accounts file has its "admin" account (which then has a password).
    The file alone isn't enough: the game makes it, empty, before it asks for the password."""
    import os
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    cache = _either(args, "-cachedir") or os.path.join(home, "Zomboid")
    name = _either(args, "-servername") or "servertest"
    db = os.path.join(cache, "db", name + ".db")
    if not os.path.isfile(db):
        return False
    try:
        import sqlite3
        from pathlib import Path
        con = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            return con.execute("SELECT 1 FROM whitelist WHERE username = 'admin' LIMIT 1").fetchone() is not None
        finally:
            con.close()
    except Exception:
        return True       # can't read it: leave a server that may already be set up alone


def full_launcher(srv: dict, exe: str) -> str:
    """The program to really start. Project Zomboid's StartServer64.bat hands only its first
    two options to the game ("%1 %2") and then waits for a key press, so later options (port,
    server name...) were lost and BaoBun couldn't see the server stop. This writes a copy next
    to it that passes every option on and doesn't wait - redone whenever the game's own file
    changes (game updates) - and returns it. Any other program is returned as it is."""
    import os
    if str(srv.get("appId") or "") not in ("380870", "108600") or not (exe or "").lower().endswith(".bat"):
        return exe
    try:
        with open(exe, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        if not re.search(r"(?i)zombie\.network\.GameServer", text):
            return exe
        lines = text.splitlines()
        while lines and lines[-1].strip().lower() in ("", "pause"):
            lines.pop()
        new = "\r\n".join(re.sub(r"\s%1(?:\s+%[2-9])*\s*$", " %*", ln) if "GameServer" in ln else ln
                          for ln in lines) + "\r\n"
        if "%*" not in new:
            return exe
        base, ext = os.path.splitext(exe)
        copy = base + "-BaoBun" + ext
        try:
            with open(copy, "r", encoding="utf-8", errors="replace", newline="") as f:
                same = f.read() == new
        except OSError:
            same = False
        if not same:
            with open(copy, "w", encoding="utf-8", newline="") as f:
                f.write(new)
        return copy
    except OSError:
        return exe


def _is_srcds(srv: dict) -> bool:
    exe = str(srv.get("executable") or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return exe in ("srcds.exe", "srcds_win64.exe")


MAP_PREFERENCE = ("de_dust2", "cs_office", "dm_lockdown", "dod_anzio", "ctf_2fort", "c1m1_hotel")


def _source_mod(directory: str):
    """(game folder with gameinfo.txt, a map from its maps folder) inside a Source install."""
    import os
    if not directory or not os.path.isdir(directory):
        return None, None
    skip = {"hl2", "platform", "bin", "steamapps", "sourcetest", "logs"}
    mods = []
    try:
        for d in sorted(os.listdir(directory)):
            full = os.path.join(directory, d)
            if d.lower() not in skip and os.path.isfile(os.path.join(full, "gameinfo.txt")):
                mods.append(d)
    except OSError:
        return None, None
    if not mods:                                   # some mods sit one folder deeper (Dystopia)
        try:
            for d in sorted(os.listdir(directory)):
                top = os.path.join(directory, d)
                if d.lower() in skip or not os.path.isdir(top):
                    continue
                for e in sorted(os.listdir(top)):
                    if e.lower() not in skip and os.path.isfile(os.path.join(top, e, "gameinfo.txt")):
                        mods.append(d + "/" + e)
        except OSError:
            pass
    if not mods:
        return None, None
    mod = mods[0]
    maps = []
    try:
        maps = sorted(f[:-4] for f in os.listdir(os.path.join(directory, mod, "maps")) if f.lower().endswith(".bsp"))
    except OSError:
        pass
    pick = next((m for m in MAP_PREFERENCE if m in maps), maps[0] if maps else None)
    return mod, pick
