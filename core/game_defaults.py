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
    elif app == "232130":                                 # Killing Floor 2 needs a map first
        if "kf-" not in args.lower():
            args = ("KF-BioticsLab " + args).strip()
            notes.append("start map KF-BioticsLab")
        if "-port=" not in args.lower():
            args = (args + " -Port=%s" % (srv.get("port") or 7777)).strip()
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
}


SOURCE = {"730": ("", "de_dust2"), "232250": ("tf", "ctf_2fort"), "222860": ("left4dead2", "c1m1_hotel")}


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
