"""Quick settings: the handful of things everyone changes (server name, password,
max players, ...) as a simple form, written to wherever each game keeps them.

Each game has a list of fields. A field says where its value lives:
  ("launch", "-name", "sep")           launch option (-name "X" / -name=X / +x y)
  ("ark_url", "SessionName")           ARK ?Key=Value in the map URL
  ("json", path, "a.b.c")              JSON file, dotted key
  ("ini", path, section, key)          INI (section None = top of file / .properties)
  ("ini_tuple", path, section, key, sub)   Palworld's OptionSettings=(A=1,B="x",...)
  ("xml_prop", path, name)             7 Days to Die <property name=".." value=".."/>
  ("cfg", path, key)                   Arma / DayZ  key = value;  (and srcds  key "value")
  ("dat", path, key)                   Unturned Commands.dat  "Key value" lines
Paths may contain {data} (Vintage Story data folder), {zomboid}/{pzname}
(Project Zomboid), {unturned} (Unturned server folder), {dst} (DST cluster),
{config} (the -config file from the launch options).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Dict, List, Optional

from core.mods_common import (_read_lines, _write_lines, documents_dir, get_arg, ini_get, ini_set,
                              lenient_json, rel_or_abs, set_arg, user_home)

YESNO = [["true", "Yes"], ["false", "No"]]

GAMES: Dict[str, dict] = {
    "vintage-story": {"note": "Saved to serverconfig.json in the server's data folder.", "fields": [
        ("ServerName", "Server name", "text", ("json", "{data}/serverconfig.json", "ServerName")),
        ("ServerDescription", "Description", "text", ("json", "{data}/serverconfig.json", "ServerDescription")),
        ("WelcomeMessage", "Welcome message", "text", ("json", "{data}/serverconfig.json", "WelcomeMessage")),
        ("Password", "Password (blank = none)", "password", ("json", "{data}/serverconfig.json", "Password")),
        ("MaxClients", "Max players", "int", ("json", "{data}/serverconfig.json", "MaxClients")),
        ("AdvertiseServer", "List on the public server list", "bool", ("json", "{data}/serverconfig.json", "AdvertiseServer")),
    ]},
    "minecraft": {"note": "Saved to server.properties.", "fields": [
        ("motd", "Server message (MOTD)", "text", ("ini", "server.properties", None, "motd")),
        ("max-players", "Max players", "int", ("ini", "server.properties", None, "max-players")),
        ("difficulty", "Difficulty", ["peaceful", "easy", "normal", "hard"], ("ini", "server.properties", None, "difficulty")),
        ("gamemode", "Game mode", ["survival", "creative", "adventure", "spectator"], ("ini", "server.properties", None, "gamemode")),
        ("pvp", "PvP", YESNO, ("ini", "server.properties", None, "pvp")),
        ("white-list", "Whitelist only", YESNO, ("ini", "server.properties", None, "white-list")),
        ("view-distance", "View distance (chunks)", "int", ("ini", "server.properties", None, "view-distance")),
    ]},
    "896660": {"note": "Saved in the launch options. The password must be at least 5 characters and can't "
                       "contain the server name.", "fields": [
        ("name", "Server name", "text", ("launch", "-name", " ")),
        ("world", "World name", "text", ("launch", "-world", " ")),
        ("password", "Password", "password", ("launch", "-password", " ")),
        ("public", "List on the public server list", [["1", "Yes"], ["0", "No"]], ("launch", "-public", " ")),
    ]},
    "2394010": {"note": "Saved to PalWorldSettings.ini (OptionSettings).", "fields": [
        ("ServerName", "Server name", "text", ("ini_tuple", "{palini}", "/Script/Pal.PalGameWorldSettings", "OptionSettings", "ServerName")),
        ("ServerDescription", "Description", "text", ("ini_tuple", "{palini}", "/Script/Pal.PalGameWorldSettings", "OptionSettings", "ServerDescription")),
        ("ServerPassword", "Password", "password", ("ini_tuple", "{palini}", "/Script/Pal.PalGameWorldSettings", "OptionSettings", "ServerPassword")),
        ("AdminPassword", "Admin password", "password", ("ini_tuple", "{palini}", "/Script/Pal.PalGameWorldSettings", "OptionSettings", "AdminPassword")),
        ("ServerPlayerMaxNum", "Max players", "int", ("ini_tuple", "{palini}", "/Script/Pal.PalGameWorldSettings", "OptionSettings", "ServerPlayerMaxNum")),
    ]},
    "2278520": {"note": "Saved to enshrouded_server.json (passwords are set per user group in that file).", "fields": [
        ("name", "Server name", "text", ("json", "enshrouded_server.json", "name")),
        ("slotCount", "Max players", "int", ("json", "enshrouded_server.json", "slotCount")),
    ]},
    "294420": {"note": "Saved to serverconfig.xml.", "fields": [
        ("ServerName", "Server name", "text", ("xml_prop", "serverconfig.xml", "ServerName")),
        ("ServerDescription", "Description", "text", ("xml_prop", "serverconfig.xml", "ServerDescription")),
        ("ServerPassword", "Password", "password", ("xml_prop", "serverconfig.xml", "ServerPassword")),
        ("ServerMaxPlayerCount", "Max players", "int", ("xml_prop", "serverconfig.xml", "ServerMaxPlayerCount")),
        ("GameDifficulty", "Difficulty (0 easiest - 5 hardest)", [["0", "0"], ["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"], ["5", "5"]],
         ("xml_prop", "serverconfig.xml", "GameDifficulty")),
        ("ServerVisibility", "Visibility", [["2", "Public"], ["1", "Friends only"], ["0", "Not listed"]],
         ("xml_prop", "serverconfig.xml", "ServerVisibility")),
    ]},
    "380870": {"note": "Saved to the server's .ini in the Zomboid folder.", "fields": [
        ("PublicName", "Server name", "text", ("ini", "{pzini}", None, "PublicName")),
        ("PublicDescription", "Description", "text", ("ini", "{pzini}", None, "PublicDescription")),
        ("Password", "Password", "password", ("ini", "{pzini}", None, "Password")),
        ("MaxPlayers", "Max players", "int", ("ini", "{pzini}", None, "MaxPlayers")),
        ("Public", "List on the public server list", YESNO, ("ini", "{pzini}", None, "Public")),
        ("PVP", "PvP", YESNO, ("ini", "{pzini}", None, "PVP")),
    ]},
    "258550": {"note": "Saved in the launch options.", "fields": [
        ("hostname", "Server name", "text", ("launch", "+server.hostname", " ")),
        ("description", "Description", "text", ("launch", "+server.description", " ")),
        ("maxplayers", "Max players", "int", ("launch", "+server.maxplayers", " ")),
        ("worldsize", "Map size", "int", ("launch", "+server.worldsize", " ")),
        ("seed", "Map seed", "int", ("launch", "+server.seed", " ")),
    ]},
    "2430930": {"note": "Passwords and max players are saved in the launch options; a server name with spaces goes "
                       "to GameUserSettings.ini (ARK cuts a name at the first space on the launch line). Cluster "
                       "maps use ARK Cluster Settings instead.", "fields": [
        ("SessionName", "Server name", "text", ("ark_name", "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini")),
        ("ServerPassword", "Password", "password", ("ark_url", "ServerPassword")),
        ("ServerAdminPassword", "Admin password", "password", ("ark_url", "ServerAdminPassword")),
        ("WinLiveMaxPlayers", "Max players", "int", ("launch", "-WinLiveMaxPlayers", "=")),
    ]},
    "376030": {"note": "Saved in the launch options (map URL).", "fields": [
        ("SessionName", "Server name", "text", ("ark_name", "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini")),
        ("ServerPassword", "Password", "password", ("ark_url", "ServerPassword")),
        ("ServerAdminPassword", "Admin password", "password", ("ark_url", "ServerAdminPassword")),
        ("MaxPlayers", "Max players", "int", ("ark_url", "MaxPlayers")),
    ]},
    "1829350": {"note": "Saved to ServerHostSettings.json.", "fields": [
        ("Name", "Server name", "text", ("json", "{vrising}", "Name")),
        ("Description", "Description", "text", ("json", "{vrising}", "Description")),
        ("Password", "Password", "password", ("json", "{vrising}", "Password")),
        ("MaxConnectedUsers", "Max players", "int", ("json", "{vrising}", "MaxConnectedUsers")),
        ("ListOnSteam", "List on Steam", "bool", ("json", "{vrising}", "ListOnSteam")),
    ]},
    "233780": {"note": "Saved to the server's .cfg (-config=).", "fields": [
        ("hostname", "Server name", "text", ("cfg", "{config}", "hostname")),
        ("password", "Password", "password", ("cfg", "{config}", "password")),
        ("passwordAdmin", "Admin password", "password", ("cfg", "{config}", "passwordAdmin")),
        ("maxPlayers", "Max players", "int", ("cfg", "{config}", "maxPlayers")),
    ]},
    "223350": {"note": "Saved to serverDZ.cfg (-config=).", "fields": [
        ("hostname", "Server name", "text", ("cfg", "{config}", "hostname")),
        ("password", "Password", "password", ("cfg", "{config}", "password")),
        ("passwordAdmin", "Admin password", "password", ("cfg", "{config}", "passwordAdmin")),
        ("maxPlayers", "Max players", "int", ("cfg", "{config}", "maxPlayers")),
    ]},
    "1874900": {"note": "Saved to the server's config.json (-config).", "fields": [
        ("name", "Server name", "text", ("json", "{config}", "game.name")),
        ("password", "Password", "password", ("json", "{config}", "game.password")),
        ("passwordAdmin", "Admin password", "password", ("json", "{config}", "game.passwordAdmin")),
        ("maxPlayers", "Max players", "int", ("json", "{config}", "game.maxPlayers")),
        ("visible", "List on the server browser", "bool", ("json", "{config}", "game.visible")),
    ]},
    "1110390": {"note": "Saved to Commands.dat.", "fields": [
        ("Name", "Server name", "text", ("dat", "{unturned}/Server/Commands.dat", "Name")),
        ("Password", "Password", "password", ("dat", "{unturned}/Server/Commands.dat", "Password")),
        ("MaxPlayers", "Max players", "int", ("dat", "{unturned}/Server/Commands.dat", "MaxPlayers")),
        ("Map", "Map", "text", ("dat", "{unturned}/Server/Commands.dat", "Map")),
    ]},
    "343050": {"note": "Saved to cluster.ini.", "fields": [
        ("cluster_name", "Server name", "text", ("ini", "{dst}/cluster.ini", "NETWORK", "cluster_name")),
        ("cluster_description", "Description", "text", ("ini", "{dst}/cluster.ini", "NETWORK", "cluster_description")),
        ("cluster_password", "Password", "password", ("ini", "{dst}/cluster.ini", "NETWORK", "cluster_password")),
        ("max_players", "Max players", "int", ("ini", "{dst}/cluster.ini", "GAMEPLAY", "max_players")),
        ("pvp", "PvP", YESNO, ("ini", "{dst}/cluster.ini", "GAMEPLAY", "pvp")),
    ]},
    "443030": {"note": "Saved to Engine.ini / ServerSettings.ini.", "fields": [
        ("ServerName", "Server name", "text", ("ini", "ConanSandbox/Saved/Config/WindowsServer/Engine.ini", "OnlineSubsystem", "ServerName")),
        ("ServerPassword", "Password", "password", ("ini", "ConanSandbox/Saved/Config/WindowsServer/Engine.ini", "OnlineSubsystem", "ServerPassword")),
        ("AdminPassword", "Admin password", "password", ("ini", "ConanSandbox/Saved/Config/WindowsServer/ServerSettings.ini", "ServerSettings", "AdminPassword")),
    ]},
    "4020": {"note": "Saved to garrysmod/cfg/server.cfg and the launch options.", "fields": [
        ("hostname", "Server name", "text", ("cfg", "garrysmod/cfg/server.cfg", "hostname")),
        ("sv_password", "Password", "password", ("cfg", "garrysmod/cfg/server.cfg", "sv_password")),
        ("maxplayers", "Max players", "int", ("launch", "+maxplayers", " ")),
    ]},
    # --- start options / files confirmed by the settings smoke test's gaps (2026-11) ---
    "2857200": {"note": "Saved in the launch options.", "fields": [                       # Abiotic Factor
        ("SteamServerName", "Server name", "text", ("launch", "-SteamServerName", "=")),
        ("ServerPassword", "Password", "password", ("launch", "-ServerPassword", "=")),
        ("MaxServerPlayers", "Max players", "int", ("launch", "-MaxServerPlayers", "=")),
    ]},
    "3017310": {"note": "Saved in the launch options.", "fields": [                       # Soulmask
        ("SteamServerName", "Server name", "text", ("launch", "-SteamServerName", "=")),
        ("PSW", "Password", "password", ("launch", "-PSW", "=")),
        ("adminpsw", "Admin password", "password", ("launch", "-adminpsw", "=")),
        ("MaxPlayers", "Max players", "int", ("launch", "-MaxPlayers", "=")),
    ]},
    "1963720": {"note": "Saved in the launch options. Core Keeper has no server name or password: friends join "
                        "with the Game ID, which the server writes to GameID.txt next to its program.", "fields": [                                           # Core Keeper
        ("worldname", "World name", "text", ("launch", "-worldname", " ")),
        ("maxplayers", "Max players", "int", ("launch", "-maxplayers", " ")),
    ]},
    "581330": {"note": "Saved in the launch options.", "fields": [                        # Insurgency: Sandstorm
        ("hostname", "Server name", "text", ("launch", "-hostname", "=")),
    ]},
    "3792580": {"note": "Saved to ServerSettings.ini (the game writes it on its first start).", "fields": [  # SCUM
        ("scum.ServerName", "Server name", "text", ("ini", "SCUM/Saved/Config/WindowsServer/ServerSettings.ini", "General", "scum.ServerName")),
        ("scum.ServerPassword", "Password", "password", ("ini", "SCUM/Saved/Config/WindowsServer/ServerSettings.ini", "General", "scum.ServerPassword")),
        ("scum.MaxPlayers", "Max players", "int", ("ini", "SCUM/Saved/Config/WindowsServer/ServerSettings.ini", "General", "scum.MaxPlayers")),
    ]},
    "3349480": {"note": "Saved to MoriaServerConfig.ini (the game writes it on its first start).", "fields": [
        ("Name", "World name", "text", ("ini", "MoriaServerConfig.ini", "World", "Name")),
        ("OptionalPassword", "Password", "password", ("ini", "MoriaServerConfig.ini", "Main", "OptionalPassword")),
    ]},
    "2465200": {"note": "Saved to dedicatedserver.cfg in the server's data folder (BaoBun makes it if it isn't "
                        "there). Up to 8 players.", "fields": [
        ("ServerName", "Server name", "text", ("json", "{sotf}", "ServerName")),
        ("Password", "Password", "password", ("json", "{sotf}", "Password")),
        ("MaxPlayers", "Max players", "int", ("json", "{sotf}", "MaxPlayers")),
    ]},
    "232130": {"note": "Name and passwords are saved to PCServer-KFGame.ini, max players in the launch options "
                       "(the game ignores the file for that). Survival allows up to 6.", "fields": [   # Killing Floor 2
        ("ServerName", "Server name", "text", ("ini", "KFGame/Config/PCServer-KFGame.ini", "Engine.GameReplicationInfo", "ServerName")),
        ("GamePassword", "Password", "password", ("ini", "KFGame/Config/PCServer-KFGame.ini", "Engine.AccessControl", "GamePassword")),
        ("AdminPassword", "Admin password", "password", ("ini", "KFGame/Config/PCServer-KFGame.ini", "Engine.AccessControl", "AdminPassword")),
        ("MaxPlayers", "Max players", "int", ("ark_url", "MaxPlayers")),
    ]},
    "1690800": {"note": "Satisfactory has no setting for its server name or passwords: start the server, then in "
                        "the game open Server Manager, add this server and claim it - the game asks for a name "
                        "and an admin password there. Max players is saved to Game.ini.", "fields": [
        ("MaxPlayers", "Max players", "int", ("ini", "FactoryGame/Saved/Config/WindowsServer/Game.ini", "/Script/Engine.GameSession", "MaxPlayers")),
    ]},
    "2131400": {"note": "Saved to Game.ini. VEIN has no admin password - admins are listed by Steam ID in the "
                        "same file.", "fields": [                                              # VEIN
        ("ServerName", "Server name", "text", ("ini", "Vein/Saved/Config/WindowsServer/Game.ini", "/Script/Vein.VeinGameSession", "ServerName")),
        ("Password", "Password", "password", ("ini", "Vein/Saved/Config/WindowsServer/Game.ini", "/Script/Vein.VeinGameSession", "Password")),
        ("MaxPlayers", "Max players", "int", ("ini", "Vein/Saved/Config/WindowsServer/Game.ini", "/Script/Engine.GameSession", "MaxPlayers")),
    ]},
    "1026340": {"note": "Saved to serversettings.xml (the game writes it on its first start). Up to 16 players.",
                "fields": [                                                                    # Barotrauma
        ("ServerName", "Server name", "text", ("xml_attr", "serversettings.xml", "ServerName")),
        ("password", "Password", "password", ("xml_attr", "serversettings.xml", "password")),
        ("MaxPlayers", "Max players", "int", ("xml_attr", "serversettings.xml", "MaxPlayers")),
    ]},
    # any Source engine server (srcds: CS:S, TF2, L4D2, HL2DM, mods ...) and CS2
    "source": {"note": "Saved to the game's cfg/server.cfg (BaoBun makes the file if the game didn't ship one) "
                       "and the launch options.", "fields": [
        ("hostname", "Server name", "text", ("cfg", "{srccfg}", "hostname")),
        ("sv_password", "Password", "password", ("cfg", "{srccfg}", "sv_password")),
        ("rcon_password", "Admin (RCON) password", "password", ("cfg", "{srccfg}", "rcon_password")),
        ("maxplayers", "Max players", "int", ("launch", "{maxplayers}", " ")),
    ]},
}
ALIASES = {"108600": "380870", "1890870": "1874900"}


def source_cfg(srv: dict) -> Optional[str]:
    """Path of cfg/server.cfg for a Source engine server (srcds or CS2), else None."""
    from core import game_defaults as gd
    d = srv.get("directory") or ""
    app = str(srv.get("appId") or "")
    exe = str(srv.get("executable") or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if app == "730" or exe == "cs2.exe":
        return os.path.join(d, "game", "csgo", "cfg", "server.cfg")
    if not gd._is_srcds(srv):
        return None
    mod = get_arg(srv.get("launchArgs") or "", "game") or (gd.SOURCE.get(app) or ("",))[0] or \
        gd._source_mod(d)[0]
    return os.path.join(d, mod, "cfg", "server.cfg") if mod else None


def game_key(srv: dict) -> Optional[str]:
    if (srv.get("type") or "").lower() == "vintage-story":
        return "vintage-story"
    from core.minecraft import looks_minecraft
    if not srv.get("appId") and looks_minecraft(srv):
        return "minecraft"
    if srv.get("clusterId"):
        return None   # cluster-wide settings live in ARK Cluster Settings
    a = str(srv.get("appId") or "")
    a = ALIASES.get(a, a)
    if a in GAMES:
        return a
    return "source" if source_cfg(srv) else None


# --------------------------------------------------------------------------- #
# path placeholders
# --------------------------------------------------------------------------- #
def _resolve(srv: dict, path: str) -> str:
    d = srv.get("directory") or ""
    args = srv.get("launchArgs") or ""
    if "{data}" in path:
        from server_manager import vs_data_path
        path = path.replace("{data}", vs_data_path(srv))
    if "{pzini}" in path:
        cache = get_arg(args, "cachedir") or os.path.join(user_home(), "Zomboid")
        name = get_arg(args, "servername") or "servertest"
        path = os.path.join(rel_or_abs(d, cache), "Server", name + ".ini")
    if "{palini}" in path:
        path = os.path.join(d, "Pal", "Saved", "Config", "WindowsServer", "PalWorldSettings.ini")
    if "{vrising}" in path:
        root = get_arg(args, "persistentDataPath")
        cands = [os.path.join(rel_or_abs(d, root), "Settings", "ServerHostSettings.json")] if root else []
        cands.append(os.path.join(d, "save-data", "Settings", "ServerHostSettings.json"))
        cands.append(os.path.join(d, "VRisingServer_Data", "StreamingAssets", "Settings", "ServerHostSettings.json"))
        path = next((c for c in cands if os.path.isfile(c)), cands[-1])
    if "{unturned}" in path:
        m = re.search(r"(?i)\+\w*server/(\S+)", args)
        name = m.group(1) if m else None
        if not name:
            sd = os.path.join(d, "Servers")
            dirs = os.listdir(sd) if os.path.isdir(sd) else []
            name = dirs[0] if len(dirs) == 1 else "BaoBun"
        path = path.replace("{unturned}", os.path.join(d, "Servers", name))
    if "{dst}" in path:
        root = get_arg(args, "persistent_storage_root") or os.path.join(documents_dir(), "Klei")
        path = path.replace("{dst}", os.path.join(root, get_arg(args, "conf_dir") or "DoNotStarveTogether",
                                                  get_arg(args, "cluster") or "Cluster_1"))
    if "{srccfg}" in path:
        path = source_cfg(srv) or os.path.join(d, "cfg", "server.cfg")
    if "{sotf}" in path:
        root = get_arg(args, "userdatapath")
        path = os.path.join(rel_or_abs(d, root) if root else os.path.join(
            user_home(), "AppData", "LocalLow", "Endnight", "SonsOfTheForestDS"), "dedicatedserver.cfg")
    if "{config}" in path and str(srv.get("appId")) in ("1874900", "1890870") and not get_arg(args, "config"):
        path = path.replace("{config}", os.path.join(d, "config.json"))
    if "{config}" in path:
        cfg = get_arg(args, "config")
        default = "config.json" if str(srv.get("appId")) in ("1874900", "1890870") else (
            "serverDZ.cfg" if str(srv.get("appId")) == "223350" else "server.cfg")
        path = rel_or_abs(d, cfg or default)
    return rel_or_abs(d, path)


# --------------------------------------------------------------------------- #
# readers / writers per kind
# --------------------------------------------------------------------------- #
def _json_load(p):
    with open(p, encoding="utf-8-sig") as f:
        return lenient_json(f.read())


def _json_save(p, data):
    if os.path.isfile(p):
        shutil.copy2(p, p + ".bak")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def _tuple_parse(text: str) -> List[List[str]]:
    """Palworld (A=1,B="x,y",C=True) -> [[key, raw_value], ...] keeping order and quotes."""
    body = text.strip()
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1]
    out, cur, q = [], "", False
    for ch in body:
        if ch == '"':
            q = not q
        if ch == "," and not q:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    pairs = []
    for item in out:
        k, _, v = item.partition("=")
        pairs.append([k.strip(), v.strip()])
    return pairs


FIRST_MAP = {"2430930": "TheIsland_WP?listen", "376030": "TheIsland?listen", "232130": "KF-BioticsLab"}


def _make_unreal_ini(path: str) -> None:
    """Unreal games read Saved/Config/WindowsServer/*.ini at start but some never write
    one themselves (Conan's Engine.ini), or only after a first start. An empty file there
    is safe - the game adds the rest - so settings can be chosen before the first start."""
    folder = os.path.dirname(path)
    norm = folder.replace("\\", "/").lower()
    if os.path.isfile(path) or not norm.endswith("/saved/config/windowsserver"):
        return
    game_root = os.path.dirname(os.path.dirname(os.path.dirname(folder)))      # <Game>/ next to Saved
    if not os.path.isdir(game_root):
        return                                      # the game isn't installed here
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("")


def first_json(srv: dict):
    """The settings file a game needs before it can start at all, for the two games that
    don't make a usable one themselves. None for every other game."""
    app = ALIASES.get(str(srv.get("appId") or ""), str(srv.get("appId") or ""))
    name = str(srv.get("name") or "BaoBun server")
    if app == "2465200":             # Sons of the Forest stops after writing its own default
        port = int(srv.get("port") or 8766)
        return {"IpAddress": "0.0.0.0", "GamePort": port, "QueryPort": 27016, "BlobSyncPort": 9700,
                "ServerName": name, "MaxPlayers": 8, "Password": "", "LanOnly": False, "SaveSlot": 1,
                "SaveMode": "Continue", "GameMode": "Normal", "SaveInterval": 600, "IdleDayCycleSpeed": 0.0,
                "IdleTargetFramerate": 5, "ActiveTargetFramerate": 60, "LogFilesEnabled": False,
                "TimestampLogFilenames": True, "SkipNetworkAccessibilityTest": True}
    if app == "1874900":             # Arma Reforger ships no config and makes none
        port = int(srv.get("port") or 2001)
        return {"bindPort": port, "publicPort": port, "a2s": {"address": "0.0.0.0", "port": 17777},
                "game": {"name": name, "password": "", "passwordAdmin": "",
                         "scenarioId": "{ECC61978EDCC2B5A}Missions/23_Campaign.conf", "maxPlayers": 32,
                         "visible": True, "gameProperties": {}, "mods": []}}
    return None


def _make_first_json(srv: dict, path: str) -> bool:
    data = first_json(srv)
    if data is None or os.path.isfile(path) or not os.path.isdir(srv.get("directory") or ""):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return True


SOTF_OWNERS = ("# To manage this server from inside the game, add the Steam ID of each owner below,\n"
               "# one per line. Lines starting with # are ignored.\n")


def prepare(srv: dict) -> list:
    """Before a start: make the files the game can't start without. Returns notes.
    Sons of the Forest checks itself on every start and stops ("Please restart the server")
    each time one of three files is missing - its settings, its owners list and
    steam_appid.txt - so a new server needed three starts. All three are made here."""
    key = game_key(srv)
    notes = []
    if key in ("2465200", "1874900"):
        try:
            path = _resolve(srv, "{sotf}" if key == "2465200" else "{config}")
            if _make_first_json(srv, path):
                notes.append("its settings file (%s)" % os.path.basename(path))
            d = srv.get("directory") or ""
            if key == "2465200" and os.path.isdir(d):
                owners = os.path.join(os.path.dirname(path), "ownerswhitelist.txt")
                if os.path.isdir(os.path.dirname(path)) and not os.path.isfile(owners):
                    with open(owners, "w", encoding="utf-8") as fh:
                        fh.write(SOTF_OWNERS)
                appid = os.path.join(d, "steam_appid.txt")
                if not os.path.isfile(appid):
                    with open(appid, "w", encoding="ascii") as fh:
                        fh.write("1326470")              # the game's own id, as its start script writes it
        except OSError:
            pass
    return notes


def _root_attr(path: str, name: str):
    """Match of name="value" on the first element of an XML file (group 2 = the value)."""
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    start = text.find("?>") + 2 if "?>" in text else 0
    m = re.search(r"<[A-Za-z][\w.-]*([^>]*)>", text[start:], re.S)
    if not m:
        return None
    a = re.search(r'(\s%s=)"([^"]*)"' % re.escape(name), m.group(1))
    if not a:
        return None
    base = start + m.start(1)

    class _M:                                    # positions in the whole file
        def start(self, g):
            return base + a.start(g)

        def end(self, g):
            return base + a.end(g)

        def group(self, g):
            return a.group(g)
    return _M()


def _where(srv, where):
    if where[0] == "launch" and where[1] == "{maxplayers}":      # CS2 wants -maxplayers, srcds +maxplayers
        cs2 = str(srv.get("appId") or "") == "730"
        return ("launch", "-maxplayers" if cs2 else "+maxplayers", " ")
    return where


def read_value(srv, where):
    where = _where(srv, where)
    kind = where[0]
    args = srv.get("launchArgs") or ""
    if kind == "launch":
        return get_arg(args, where[1])
    if kind == "ark_url":
        m = re.search(r"[?&]%s=([^?&\s]*)" % re.escape(where[1]), args, re.I)
        return m.group(1) if m else None
    if kind == "ark_name":                  # the launch line wins when the name is there
        m = re.search(r"[?&]SessionName=([^?&\s]*)", args, re.I)
        if m:
            return m.group(1)
        path = _resolve(srv, where[1])
        vals = ini_get(path, "SessionSettings", "SessionName") if os.path.isfile(path) else None
        return vals[-1] if vals else None
    path = _resolve(srv, where[1])
    if not os.path.isfile(path):
        return None
    if kind == "xml_attr":
        m = _root_attr(path, where[2])
        return m.group(2) if m else None
    if kind == "json":
        cur = _json_load(path)
        for part in where[2].split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        if isinstance(cur, bool):
            return "true" if cur else "false"
        return None if cur is None else str(cur)
    if kind == "ini":
        vals = ini_get(path, where[2], where[3])
        return vals[-1] if vals else None
    if kind == "ini_tuple":
        vals = ini_get(path, where[2], where[3])
        for k, v in _tuple_parse(vals[-1] if vals else ""):
            if k == where[4]:
                return v.strip('"')
        return None
    if kind == "xml_prop":
        text = "\n".join(_read_lines(path))
        m = re.search(r'<property\s+name="%s"\s+value="([^"]*)"' % re.escape(where[2]), text)
        return m.group(1) if m else None
    if kind == "cfg":
        for line in _read_lines(path):
            m = re.match(r'^\s*%s\b\s*(?:=\s*)?"?([^";]*)"?\s*;?' % re.escape(where[2]), line, re.I)
            if m and not line.strip().startswith("//"):
                return m.group(1).strip()
        return None
    if kind == "dat":
        for line in _read_lines(path):
            k, _, v = line.strip().partition(" ")
            if k.lower() == where[2].lower():
                return v.strip()
        return None
    return None


def write_value(srv, where, value, typ):
    where = _where(srv, where)
    kind = where[0]
    args = srv.get("launchArgs") or ""
    if kind == "launch":
        sep = where[2]
        name = where[1]
        prefix = name[0] if name[0] in "+-" else "-"
        return ("args", set_arg(args, name, value if value != "" else None, sep=sep, prefix=prefix))
    if kind == "ark_url":
        key = where[1]
        if re.search(r"[?&]%s=" % re.escape(key), args, re.I):
            args = re.sub(r"([?&])%s=[^?&\s]*" % re.escape(key), lambda m: m.group(1) + key + "=" + value, args,
                          count=1, flags=re.I)
        else:
            if not args.strip() or args.lstrip().startswith(("-", "+")):   # no map yet: the game's usual one
                first = FIRST_MAP.get(ALIASES.get(str(srv.get("appId") or ""), str(srv.get("appId") or "")))
                if first:
                    args = (first + " " + args).strip()
            parts = args.split(" ", 1)
            if "?" in parts[0] or (parts[0] and not parts[0].startswith("-")):
                parts[0] += "?%s=%s" % (key, value)
                args = " ".join(parts)
            else:
                raise ValueError("The launch options don't start with a map (e.g. TheIsland_WP?listen), "
                                 "so BaoBun can't add %s. Add the map first in Settings." % key)
        return ("args", args)
    if kind == "ark_name":
        value = str(value).strip()
        if re.search(r'[?&"]', value):
            raise ValueError("The server name can't contain ? & or quotes")
        if not re.search(r"\s", value):
            return write_value(srv, ("ark_url", "SessionName"), value, typ)     # one word: the launch line
        # a name with spaces: the settings file, and off the launch line, which would win over
        # the file and cuts the name at the first space
        path = _resolve(srv, where[1])
        _make_unreal_ini(path)
        if not os.path.isfile(path):
            raise FileNotFoundError("GameUserSettings.ini doesn't exist yet. Install the game first.")
        ini_set(path, "SessionSettings", "SessionName", value)
        srv["_also_args"] = re.sub(r"\?SessionName=[^?&\s]*", "", args, flags=re.I)
        return ("file", path)
    path = _resolve(srv, where[1])
    if kind == "ini":
        _make_unreal_ini(path)
    if kind == "json" and not os.path.isfile(path):
        _make_first_json(srv, path)
    if kind == "ini_tuple" and not os.path.isfile(path):
        default = os.path.join(srv.get("directory") or "", "DefaultPalWorldSettings.ini")
        if os.path.isfile(default):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copy2(default, path)
    if kind == "cfg" and not os.path.isfile(path) and os.path.basename(os.path.dirname(path)).lower() == "cfg" \
            and os.path.isdir(os.path.dirname(path)):
        with open(path, "w", encoding="utf-8") as fh:      # Source games don't ship a server.cfg
            fh.write("// Server settings - the game runs this file every time a map starts.\n")
    if not os.path.isfile(path):
        raise FileNotFoundError("%s doesn't exist yet. Start the server once so it creates its config, then "
                                "try again." % os.path.basename(path))
    if kind == "json":
        data = _json_load(path)
        cur = data
        parts = where[2].split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        old = cur.get(parts[-1])
        if typ == "int":
            v = int(value)
        elif typ == "bool" or isinstance(old, bool):
            v = str(value).lower() in ("1", "true", "yes", "on")
        else:
            v = value
        cur[parts[-1]] = v
        _json_save(path, data)
    elif kind == "ini":
        ini_set(path, where[2], where[3], str(value))
    elif kind == "ini_tuple":
        vals = ini_get(path, where[2], where[3])
        pairs = _tuple_parse(vals[-1] if vals else "")
        quoted = typ in ("text", "password")
        newv = '"%s"' % str(value).replace('"', "") if quoted else str(value)
        for p in pairs:
            if p[0] == where[4]:
                p[1] = newv
                break
        else:
            pairs.append([where[4], newv])
        ini_set(path, where[2], where[3], "(" + ",".join("%s=%s" % (k, v) for k, v in pairs) + ")")
    elif kind == "xml_attr":
        m = _root_attr(path, where[2])
        from xml.sax.saxutils import quoteattr
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
        if m:
            text = text[:m.start(2) - 1] + quoteattr(str(value)) + text[m.end(2) + 1:]
        else:
            r = re.search(r"<[A-Za-z][\w.-]*", text[text.find("?>") + 2 if "?>" in text else 0:])
            if not r:
                raise ValueError("%s isn't in a form BaoBun understands" % os.path.basename(path))
            at = (text.find("?>") + 2 if "?>" in text else 0) + r.end()
            text = text[:at] + " %s=%s" % (where[2], quoteattr(str(value))) + text[at:]
        shutil.copy2(path, path + ".bak")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    elif kind == "xml_prop":
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
        from xml.sax.saxutils import quoteattr
        rx = re.compile(r'(<property\s+name="%s"\s+value=)"[^"]*"' % re.escape(where[2]))
        if rx.search(text):
            text = rx.sub(lambda m: m.group(1) + quoteattr(str(value)), text, count=1)
        else:
            text = re.sub(r"(</ServerSettings>)", '\t<property name="%s" value=%s />\n\\1'
                          % (where[2], quoteattr(str(value))), text, count=1)
        shutil.copy2(path, path + ".bak")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    elif kind == "cfg":
        lines = _read_lines(path)
        num = typ == "int"
        srcds = os.path.basename(os.path.dirname(path)).lower() == "cfg"
        if srcds:
            new = '%s "%s"' % (where[2], value)
        else:
            new = "%s = %s;" % (where[2], value if num else '"%s"' % str(value).replace('"', ""))
        rx = re.compile(r'^\s*%s\b' % re.escape(where[2]), re.I)
        for i, line in enumerate(lines):
            if rx.match(line) and not line.strip().startswith("//"):
                lines[i] = new
                break
        else:
            lines.append(new)
        _write_lines(path, lines)
    elif kind == "dat":
        lines = _read_lines(path)
        for i, line in enumerate(lines):
            if line.strip().partition(" ")[0].lower() == where[2].lower():
                lines[i] = "%s %s" % (where[2], value)
                break
        else:
            lines.append("%s %s" % (where[2], value))
        _write_lines(path, lines)
    return ("file", path)


# --------------------------------------------------------------------------- #
def max_players(srv: dict) -> Optional[int]:
    """The "Max players" value from the game's own settings, when BaoBun knows where it is."""
    key = game_key(srv)
    spec = GAMES.get(key) if key else None
    for f in (spec or {}).get("fields", []):
        if f[1] == "Max players":
            try:
                v = read_value(srv, f[3])
                return int(str(v).strip().strip('"')) if v not in (None, "") else None
            except Exception:
                return None
    return None


def get(mgr, server_id: str) -> dict:
    srv = mgr.get_server(server_id)
    if not srv:
        raise ValueError("Server not found")
    key = game_key(srv)
    if not key:
        if srv.get("clusterId"):
            return {"supported": False, "note": "ARK cluster maps share their settings - use ARK Cluster Settings.",
                    "fields": []}
        from core import auto_settings      # the usual settings, found in the game's own files
        return auto_settings.get(mgr, server_id)
    g = GAMES[key]
    fields = []
    for fkey, label, typ, where in g["fields"]:
        try:
            val = read_value(srv, where)
            err = None
        except Exception as e:
            val, err = None, str(e)[:120]
        f = {"key": fkey, "label": label, "type": typ if isinstance(typ, str) else "select"}
        if not isinstance(typ, str):
            f["choices"] = [c if isinstance(c, list) else [c, c.title()] for c in typ]
        if f["type"] == "password":
            f["value"] = ""
            f["has_value"] = bool(val)
        else:
            f["value"] = "" if val is None else val
        if err:
            f["error"] = err
        fields.append(f)
    return {"supported": True, "note": g.get("note"), "fields": fields}


def save(mgr, server_id: str, values: dict, user: str = "system") -> dict:
    srv = mgr.get_server(server_id)
    if not srv:
        raise ValueError("Server not found")
    key = game_key(srv)
    if not key:
        if not srv.get("clusterId") and any(str(k).startswith("auto[") for k in values):
            from core import auto_settings
            return auto_settings.save(mgr, server_id, values, user=user)
        raise ValueError("No quick settings for this server")
    g = GAMES[key]
    srv = dict(srv)
    saved, files = [], set()
    for fkey, label, typ, where in g["fields"]:
        if fkey not in values:
            continue
        value = values[fkey]
        value = "" if value is None else str(value).strip()
        t = typ if isinstance(typ, str) else "select"
        if t == "password" and value == "" and not values.get(fkey + "__clear"):
            continue   # blank password field = keep the current one
        if t == "int":
            if not re.match(r"^\d{1,7}$", value):
                raise ValueError("%s must be a whole number" % label)
        if t == "select" and value not in [c[0] if isinstance(c, list) else c for c in typ]:
            raise ValueError("Pick a value for %s from the list" % label)
        if t == "bool":
            value = "true" if value.lower() in ("1", "true", "yes", "on") else "false"
        if where[0] == "ark_url" and re.search(r'[\s?&"]', value):
            raise ValueError("%s can't contain spaces, ? or & (ARK reads it from the launch line)" % label)
        if where[0] == "launch" and '"' in value:
            raise ValueError("%s can't contain quotes" % label)
        if any(ch in value for ch in "\r\n") or len(value) > 200:
            raise ValueError("%s is too long or has line breaks" % label)
        if key == "896660" and fkey == "password" and value and (
                len(value) < 5 or value.lower() in (values.get("name") or read_value(srv, ("launch", "-name", " ")) or "").lower()):
            raise ValueError("Valheim passwords need at least 5 characters and can't be part of the server name")
        if where[0] in ("ini", "ini_tuple", "ark_name") and mgr.is_running(server_id):
            from core.config_forms import rewritten_on_exit
            if rewritten_on_exit(_resolve(srv, where[1])):
                raise ValueError("Stop the server first. %s writes its settings file again when it shuts down, "
                                 "which would undo this change." % (srv.get("game") or "This game"))
        kind, out = write_value(srv, where, value, t)
        if "_also_args" in srv:
            srv["launchArgs"] = srv.pop("_also_args")
        if kind == "args":
            srv["launchArgs"] = out
        else:
            files.add(os.path.basename(out))
        saved.append(fkey)
    if (srv.get("launchArgs") or "") != (mgr.get_server(server_id).get("launchArgs") or ""):
        servers = mgr.load_servers()
        for s in servers:
            if s.get("id") == server_id:
                s["launchArgs"] = srv["launchArgs"]
        mgr.save_servers(servers)
        files.add("launch options")
    if saved:
        mgr._audit("game_settings", server_id, user=user, fields=saved)
    return {"saved": saved, "where": sorted(files), "restart_needed": mgr.is_running(server_id)}
