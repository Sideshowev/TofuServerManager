"""Game templates for "+ Add Server" (port, Steam App ID, executable, config files).

One catalog for the whole app: built-in entries below, plus presets.json next to
the exe (user-editable), merged at import. Used by the GUI and the API
("games.list"). Setting profiles (rates, difficulty...) are a different thing and
live in core/preset_engine.py.
"""

import json

from core.paths import base_dir, bundle_dir

APP_DIR = base_dir()


def windows_exe(exe):
    """Fix Linux binary names in presets ("x.x86_64", "bin/x64/factorio") to the .exe."""
    if not exe:
        return exe
    low = exe.lower()
    if low.endswith((".exe", ".bat", ".cmd", ".jar", ".sh")):
        return exe
    if low.endswith(".x86_64"):
        return exe[: -len(".x86_64")] + ".exe"
    if "." not in exe.replace("\\", "/").split("/")[-1]:
        return exe + ".exe"
    return exe
BUNDLE_DIR = bundle_dir()

GAME_PRESETS = {
    "Vintage Story": {
        "type": "vintage-story", "appId": None, "port": 42420,
        "executable": "VintagestoryServer.exe",
        "config_files": ["serverconfig.json", "servermagicnumbers.json"],
        "description": "Vintage Story dedicated server", "anonymous_login": True,
    },
    "ARK: Survival Ascended": {
        "type": "steamcmd", "appId": "2430930", "port": 7777,
        "executable": "ShooterGame/Binaries/Win64/ArkAscendedServer.exe",
        "config_files": ["GameUserSettings.ini", "Engine.ini", "Game.ini"],
        "description": "ARK: Survival Ascended dedicated server", "anonymous_login": True,
    },
    "Counter-Strike 2": {
        "type": "steamcmd", "appId": "730", "port": 27015,
        "executable": "game/bin/win64/cs2.exe",
        "config_files": ["game/csgo/cfg/server.cfg", "game/csgo/gamemodes_server.txt"],
        "description": "CS2 dedicated server", "anonymous_login": True,
    },
    "Minecraft (Java)": {
        "type": "custom", "appId": None, "port": 25565,
        "executable": "server.jar",
        "config_files": ["server.properties", "bukkit.yml", "spigot.yml"],
        "description": "Minecraft Java Edition server", "anonymous_login": True,
    },
    "Valheim": {
        "type": "steamcmd", "appId": "896660", "port": 2456,
        "executable": "valheim_server.exe",
        "config_files": ["start_headless_server.bat"],
        "description": "Valheim dedicated server", "anonymous_login": True,
    },
    "Palworld": {
        "type": "steamcmd", "appId": "2394010", "port": 8211,
        "executable": "PalServer.exe",
        "config_files": ["PalWorldSettings.ini"],
        "description": "Palworld dedicated server", "anonymous_login": True,
    },
    "Sons of the Forest": {
        "type": "steamcmd", "appId": "2465200", "port": 8766,
        "executable": "SonsOfTheForestDS.exe",
        "config_files": ["serverconfig.json"],
        "description": "Sons of the Forest dedicated server", "anonymous_login": True,
    },
    "7 Days to Die": {
        "type": "steamcmd", "appId": "294420", "port": 26900,
        "executable": "7DaysToDieServer.exe",
        "config_files": ["serverconfig.xml"],
        "description": "Zombie survival sandbox", "anonymous_login": True,
    },
    "ASTRONEER": {
        "type": "steamcmd", "appId": "728470", "port": 8777,
        "executable": "AstroneerServer.exe",
        "config_files": ["GameUserSettings.ini"],
        "description": "Space exploration sandbox", "anonymous_login": True,
    },
    "Garry's Mod": {
        "type": "steamcmd", "appId": "4020", "port": 27015,
        "executable": "srcds.exe",
        "config_files": ["garrysmod/cfg/server.cfg"],
        "description": "Sandbox physics game", "anonymous_login": True,
    },
    "Left 4 Dead 2": {
        "type": "steamcmd", "appId": "222860", "port": 27015,
        "executable": "srcds.exe",
        "config_files": ["left4dead2/cfg/server.cfg"],
        "description": "Zombie co-op shooter sequel", "anonymous_login": True,
    },
    "DayZ": {
        "type": "steamcmd", "appId": "223350", "port": 27015,
        "executable": "DayZServer.exe",
        "config_files": ["serverDZ.cfg"],
        "description": "Zombie survival MMO", "anonymous_login": False,
    },
    "Killing Floor 2": {
        "type": "steamcmd", "appId": "232130", "port": 7777,
        "executable": "Binaries/Win64/KFServer.exe",
        "config_files": ["KFGame/Config/PCServer-KFGame.ini"],
        "description": "Zombie wave survival", "anonymous_login": True,
    },
    "Project Zomboid": {
        "type": "steamcmd", "appId": "380870", "port": 16261,
        "executable": "StartServer64.bat",
        "config_files": ["server.ini"],
        "description": "Zombie survival isometric", "anonymous_login": True,
    },
    "Satisfactory": {
        "type": "steamcmd", "appId": "1690800", "port": 7777,
        "executable": "FactoryServer.exe",
        "config_files": ["ServerSettings.json"],
        "description": "Factory-building game", "anonymous_login": True,
    },
    "Unturned": {
        "type": "steamcmd", "appId": "1110390", "port": 27015,
        "executable": "Unturned_Headless.exe",
        "config_files": ["Commands.dat", "ServerConfig.json"],
        "description": "Zombie survival blocky sandbox", "anonymous_login": True,
    },
    "Conan Exiles": {
        "type": "steamcmd", "appId": "443030", "port": 7777,
        "executable": "ConanSandboxServer.exe",
        "config_files": ["ServerSettings.ini"],
        "description": "Survival in Conan universe", "anonymous_login": True,
    },
    "Squad": {
        "type": "steamcmd", "appId": "403240", "port": 7777,
        "executable": "SquadServer.exe",
        "config_files": ["ServerConfig.json"],
        "description": "Tactical FPS", "anonymous_login": True,
    },
    "American Truck Simulator": {
        "type": "steamcmd", "appId": "2239530", "port": 25001,
        "executable": None,
        "config_files": ["server_config"],
        "description": "Trucking simulator dedicated server", "anonymous_login": True,
    },
    "Arma Reforger": {
        "type": "steamcmd", "appId": "1874900", "port": 2001,
        "executable": "ArmaReforgerServer.exe",
        "config_files": ["config.json"],
        "description": "Arma Reforger dedicated server (start with -config config.json)",
        "anonymous_login": True,
    },
    "Don't Starve Together": {
        "type": "steamcmd", "appId": "343050", "port": 10999,
        "executable": "bin64/dontstarve_dedicated_server_nullrenderer_x64.exe",
        "config_files": ["mods/dedicated_server_mods_setup.lua"],
        "description": "Don't Starve Together dedicated server", "anonymous_login": True,
    },
    "Custom / Other": {
        "type": "custom", "appId": None, "port": None,
        "executable": None, "config_files": [],
        "description": "Manually configured server", "anonymous_login": False,
    },
}

# How the built-in games are grouped in the "Add server" picker
_BUILTIN_TAGS = {
    "Vintage Story": ("survival", True), "ARK: Survival Ascended": ("survival", True),
    "Counter-Strike 2": ("shooter", True), "Minecraft (Java)": ("survival", True), "Valheim": ("survival", True),
    "Palworld": ("survival", True), "Sons of the Forest": ("survival", True), "7 Days to Die": ("survival", True),
    "ASTRONEER": ("survival", False), "Garry's Mod": ("sandbox", True), "Left 4 Dead 2": ("shooter", True),
    "DayZ": ("survival", True), "Killing Floor 2": ("shooter", True), "Project Zomboid": ("survival", True),
    "Satisfactory": ("survival", True), "Unturned": ("survival", True), "Conan Exiles": ("survival", True),
    "Squad": ("shooter", True), "American Truck Simulator": ("other", False), "Arma Reforger": ("shooter", True),
    "Don't Starve Together": ("survival", True),
}
for _n, (_cat, _pop) in _BUILTIN_TAGS.items():
    if _n in GAME_PRESETS:
        GAME_PRESETS[_n].setdefault("category", _cat)
        if _pop:
            GAME_PRESETS[_n].setdefault("popular", True)

CATEGORIES = [("survival", "Survival & crafting"), ("shooter", "Shooters & PvP"),
              ("sandbox", "Sandbox & building"), ("other", "Everything else")]


def load_extra_presets():
    """Merge optional presets from presets.json into GAME_PRESETS.

    Lets new SteamCMD games be added without editing code. presets.json is a
    dict {"presets": [ {name, type, appId, port, executable, config_files,
    description, anonymous_login}, ... ]}.

    Looks for presets.json next to the exe first (user-editable). On first run
    it materializes a bundled default there so new games can still be added
    without editing or rebuilding the app.
    """

    def _sha(path):
        import hashlib
        try:
            return hashlib.sha1(path.read_bytes()).hexdigest()
        except OSError:
            return None

    def _resolve_presets_path():
        local = APP_DIR / "presets.json"
        bundled = BUNDLE_DIR / "presets.json"
        marker = APP_DIR / "runtime" / "presets.bundled.sha1"
        if local.exists():
            # A copy BaoBun wrote itself and nobody edited since: refresh it when a newer
            # build ships a different list (so game fixes reach existing installs).
            try:
                if (bundled.exists() and bundled != local and marker.exists()
                        and marker.read_text(encoding="utf-8").strip() == _sha(local)
                        and _sha(bundled) != _sha(local)):
                    local.write_bytes(bundled.read_bytes())
                    marker.write_text(_sha(local) or "", encoding="utf-8")
            except Exception:
                pass
            return local
        if bundled.exists() and bundled != local:
            try:
                local.write_bytes(bundled.read_bytes())
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(_sha(local) or "", encoding="utf-8")
                return local
            except Exception:
                return bundled
        return local

    path = _resolve_presets_path()
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print("[WARN] presets.json:", e)
        return
    items = raw.get("presets", []) if isinstance(raw, dict) else raw
    merged = 0
    for p in items:
        if not isinstance(p, dict):
            continue
        name = p.get("name")
        if not name:
            continue
        old = GAME_PRESETS.get(name) or {}
        GAME_PRESETS[name] = {
            "type": p.get("type", "steamcmd"),
            "appId": p.get("appId"),
            "port": p.get("port"),
            "executable": windows_exe(p.get("executable")),
            "config_files": p.get("config_files", []) or [],
            "description": p.get("description", "") or "",
            "anonymous_login": bool(p.get("anonymous_login", False)),
            "category": p.get("category") or old.get("category") or "other",
            "popular": bool(p.get("popular", old.get("popular", False))),
        }
        # games BaoBun can't download: where to get the files and how
        man = p.get("manual") or old.get("manual")
        if isinstance(man, dict) and str(man.get("url") or "").startswith("https://"):
            GAME_PRESETS[name]["manual"] = {"url": man["url"], "how": str(man.get("how") or "")[:600]}
        if p.get("args") or old.get("args"):
            GAME_PRESETS[name]["args"] = str(p.get("args") or old.get("args"))[:300]
        merged += 1
    if merged:
        print("[INFO] Loaded " + str(merged) + " extra preset(s) from presets.json")


load_extra_presets()



# --------------------------------------------------------------------------- #
# finding the server program after an install
# --------------------------------------------------------------------------- #
_SKIP_EXE = ("crashreport", "unrealcefsubprocess", "steamerrorreporter", "steamcmd", "vcredist", "vc_redist",
             "dxsetup", "dotnet", "uninstall", "unins0", "easyanticheat", "eac_", "beservice", "battleye",
             "ue4prereq", "ueprereq", "redist", "setup", "launcherhelper", "7z", "crashpad", "bugsplat")


def find_server_exe(directory: str, hint: str = None, max_depth: int = 5):
    """Best guess at a game's dedicated-server program when the preset's path is
    wrong or missing (games move their exe between updates). None if unsure."""
    import os
    import re
    if not directory or not os.path.isdir(directory):
        return None
    hint_name = os.path.basename(hint or "").lower()
    best, best_score = None, 0
    base_depth = directory.rstrip("\\/").count(os.sep)
    for root, dirs, files in os.walk(directory):
        depth = root.count(os.sep) - base_depth
        low_root = root.lower()
        if depth >= max_depth or any(x in low_root for x in ("\\engine\\", "/engine/", "steamapps", "_commonredist",
                                                            "\\redist", "/redist")):
            dirs[:] = []
        for fn in files:
            low = fn.lower()
            if not low.endswith(".exe") or any(s in low for s in _SKIP_EXE):
                continue
            score = 0
            if hint_name and low == hint_name:
                score += 100
            if re.search(r"server-win64-(shipping|test)\.exe$", low):
                score += 60
            elif re.search(r"(dedicated|server|_ds|ds\.exe$|_srv|headless)", low):
                score += 40
            if "win64" in low_root:
                score += 5
            score -= depth
            if score > best_score:
                best, best_score = os.path.join(root, fn), score
    return best if best_score >= 35 else None


def game_label(srv: dict, presets: dict) -> str:
    """Human game name for a server entry (from the game catalog)."""
    app_id = str(srv.get("appId") or "")
    if srv.get("clusterId"):
        return "ARK: Survival Ascended"
    if app_id:
        for name, p in presets.items():
            if str(p.get("appId") or "") == app_id:
                return name
    if (srv.get("type") or "") == "vintage-story":
        return "Vintage Story"
    try:
        from core.minecraft import looks_minecraft
        if looks_minecraft(srv):
            return {"paper": "Minecraft (Paper)", "fabric": "Minecraft (Fabric)", "forge": "Minecraft (Forge)",
                    "neoforge": "Minecraft (NeoForge)"}.get(srv.get("mcFlavor") or "", "Minecraft (Java)")
    except Exception:
        pass
    return "Custom server"
