"""Where each game gets its mods, and how a server turns them on.

Every game in BaoBun's catalog maps to one *mod source*. The Mods tab (desktop
and web) adapts to it:

  vs_moddb     Vintage Story ModDB - BaoBun downloads into the data Mods folder
  id_list      the game server downloads mods itself; BaoBun keeps the list of
               IDs and writes it where the game reads it (launch options or a
               config file). ARK: SA (CurseForge), ARK: SE, Arma Reforger,
               Project Zomboid, Space Engineers, Unturned, Don't Starve
               Together, Killing Floor 2, Garry's Mod (collection)
  workshop     BaoBun downloads Steam Workshop items with SteamCMD and installs
               them the way the game needs: Arma 3 / DayZ (@folders, keys,
               -mod=), Conan Exiles (.pak + modlist.txt), Squad (Plugins/Mods)
  thunderstore Valheim and V Rising (BepInEx mods from thunderstore.io)
  modrinth     Minecraft Java (Fabric / Forge / NeoForge / Quilt mods, or
               Paper / Spigot plugins) from modrinth.com
  umod         Rust (Oxide / Carbon plugins from umod.org)
  folder       a plain mods folder you manage (7 Days to Die, Palworld, ...)
  none         the game has no server mods

A server can override the choice with ``modSource`` ("none", "folder",
"workshop") plus ``modFolder`` for folder mode.
"""

from __future__ import annotations

import os
from typing import Optional

# Steam app IDs of dedicated servers (and a few client IDs used by old entries).
PROFILES = {
    # --- servers that download mods themselves ------------------------------
    "2430930": {"name": "ARK: Survival Ascended", "source": "id_list", "site": "curseforge",
                "writer": "asa_launch", "label": "CurseForge",
                "add_hint": "CurseForge Project ID (shown on the mod's page on curseforge.com)",
                "note": "The server downloads and updates these mods itself when it starts (-mods=). "
                        "The order is the load order."},
    "376030": {"name": "ARK: Survival Evolved", "source": "id_list", "site": "workshop",
               "workshop_appid": "346110", "writer": "arkse_ini", "label": "Steam Workshop",
               "note": "Written to ActiveMods= in GameUserSettings.ini; -automanagedmods makes the server "
                       "download them. First in the list loads first."},
    "1874900": {"name": "Arma Reforger", "source": "id_list", "site": "reforger", "writer": "reforger_json",
                "label": "Arma Reforger Workshop",
                "add_hint": "Mod ID (16 characters, e.g. 5AAAC70D754245DD) or its reforger.armaplatform.com link",
                "note": "Written to the mods list in the server's config.json. The server downloads them "
                        "and their dependencies itself."},
    "1890870": {"alias": "1874900"},
    "380870": {"name": "Project Zomboid", "source": "id_list", "site": "workshop", "workshop_appid": "108600",
               "writer": "pz_ini", "label": "Steam Workshop",
               "add_hint": "Workshop ID or link. Add the Mod ID too if you know it: 2392709985:tsarslib",
               "note": "Written to WorkshopItems= and Mods= in the server's .ini. The server downloads them "
                       "when it starts; BaoBun fills in Mod IDs it finds after that."},
    "108600": {"alias": "380870"},
    "298740": {"name": "Space Engineers", "source": "id_list", "site": "workshop", "workshop_appid": "244850",
               "writer": "se_cfg", "label": "Steam Workshop",
               "note": "Written to <Mods> in SpaceEngineers-Dedicated.cfg; the server downloads them. "
                       "An existing world keeps its own list in Sandbox_config.sbc."},
    "1110390": {"name": "Unturned", "source": "id_list", "site": "workshop", "workshop_appid": "304930",
                "writer": "unturned_json", "label": "Steam Workshop",
                "note": "Written to WorkshopDownloadConfig.json; the server downloads them when it starts."},
    "343050": {"name": "Don't Starve Together", "source": "id_list", "site": "workshop",
               "workshop_appid": "322330", "writer": "dst_lua", "label": "Steam Workshop",
               "note": "Written to mods/dedicated_server_mods_setup.lua and enabled in modoverrides.lua "
                       "(Master and Caves); the server downloads them."},
    "232130": {"name": "Killing Floor 2", "source": "id_list", "site": "workshop", "workshop_appid": "232090",
               "writer": "kf2_ini", "label": "Steam Workshop",
               "note": "Written to PCServer-KFEngine.ini (ServerSubscribedWorkshopItems); the server "
                       "downloads them."},
    "4020": {"name": "Garry's Mod", "source": "id_list", "site": "workshop", "workshop_appid": "4000",
             "writer": "gmod_collection", "label": "Steam Workshop collection", "max_items": 1,
             "add_hint": "Workshop collection ID or link (make a collection with all your addons)",
             "note": "Garry's Mod servers download a Workshop collection (+host_workshop_collection)."},
    # --- BaoBun downloads with SteamCMD ----------------------------------------
    "233780": {"name": "Arma 3", "source": "workshop", "workshop_appid": "107410", "installer": "bohemia",
               "label": "Steam Workshop", "needs_account": True,
               "note": "BaoBun downloads each mod, links it into the server as @Name, copies its keys and "
                       "writes -mod= / -serverMod= into the launch options. Workshop downloads need the "
                       "Steam login in Settings."},
    "223350": {"name": "DayZ", "source": "workshop", "workshop_appid": "221100", "installer": "bohemia",
               "label": "Steam Workshop", "needs_account": True,
               "note": "BaoBun downloads each mod, links it into the server as @Name, copies its keys and "
                       "writes -mod= / -serverMod= into the launch options. Workshop downloads need the "
                       "Steam login in Settings (an account that owns DayZ)."},
    "443030": {"name": "Conan Exiles", "source": "workshop", "workshop_appid": "440900", "installer": "conan",
               "label": "Steam Workshop",
               "note": "BaoBun downloads each mod, copies its .pak into ConanSandbox/Mods and writes "
                       "modlist.txt (load order) and ServerModList (so players get the same mods)."},
    "403240": {"name": "Squad", "source": "workshop", "workshop_appid": "393380", "installer": "squad",
               "label": "Steam Workshop",
               "note": "BaoBun downloads each mod into SquadGame/Plugins/Mods."},
    # --- other mod sites ---------------------------------------------------------
    "896660": {"name": "Valheim", "source": "thunderstore", "community": "valheim",
               "bepinex": "denikson-BepInExPack_Valheim", "label": "Thunderstore",
               "note": "Mods run on BepInEx, which BaoBun installs with the first mod. Players need the "
                       "same client mods (r2modman / Thunderstore Mod Manager)."},
    "1829350": {"name": "V Rising", "source": "thunderstore", "community": "v-rising",
                "bepinex": "BepInEx-BepInExPack_V_Rising", "label": "Thunderstore",
                "note": "Server mods run on BepInEx, which BaoBun installs with the first mod. Game "
                        "updates can break BepInEx until it's updated."},
    "258550": {"name": "Rust", "source": "umod", "label": "uMod (Oxide plugins)",
               "note": "Plugins need Oxide (umod.org) or Carbon installed on the server. They load "
                       "without a restart."},
    "405100": {"name": "Hurtworld", "source": "umod", "label": "uMod (Oxide plugins)",
               "note": "Plugins need Oxide (umod.org) installed on the server."},
    "minecraft": {"name": "Minecraft (Java)", "source": "modrinth", "label": "Modrinth",
                  "note": "Mods must match the server's loader and Minecraft version (set below if "
                          "BaoBun can't tell). Players need the same client mods (not for plugins)."},
    # --- plain folders ---------------------------------------------------------------
    "294420": {"name": "7 Days to Die", "source": "folder", "folder": "Mods", "label": "Mods folder",
               "note": "Install modlets by URL (zip) or copy them into the Mods folder. Server-side-only "
                       "modlets don't need players to install anything."},
    "2394010": {"name": "Palworld", "source": "folder", "folder": "Pal/Content/Paks/~mods", "label": "Pak mods folder",
                "note": "Pak mods go in Pal/Content/Paks/~mods. Palworld has no official server mod support."},
    "1690800": {"name": "Satisfactory", "source": "folder", "folder": "FactoryGame/Mods", "label": "Mods folder",
                "note": "Satisfactory server mods need the Satisfactory Mod Loader; ficsit.app / Satisfactory "
                        "Mod Manager is the easiest way to install them."},
    # --- no server mods --------------------------------------------------------------
    "2278520": {"name": "Enshrouded", "source": "none"},
    "2465200": {"name": "Sons of the Forest", "source": "none"},
    "728470": {"name": "ASTRONEER", "source": "none"},
}

SOURCES = ("vs_moddb", "id_list", "workshop", "thunderstore", "modrinth", "umod", "folder", "none")


def _get(app_id: str) -> Optional[dict]:
    p = PROFILES.get(str(app_id or ""))
    if p and p.get("alias"):
        p = PROFILES.get(p["alias"])
    return p


def profile_for(srv: dict) -> dict:
    """The mod profile for a server entry (a copy, safe to change)."""
    override = (srv.get("modSource") or "").strip().lower()
    if override == "none":
        return {"source": "none", "label": "No mods"}
    if override == "folder":
        return {"source": "folder", "folder": srv.get("modFolder") or "mods", "label": "Mods folder"}
    if override == "workshop":
        return {"source": "workshop", "installer": "plain", "label": "Steam Workshop",
                "workshop_appid": str(srv.get("workshopAppId") or srv.get("appId") or "")}
    if (srv.get("type") or "").lower() == "vintage-story":
        return {"source": "vs_moddb", "label": "Vintage Story ModDB"}
    if srv.get("clusterId") and str(srv.get("appId") or "") in ("", "2430930"):
        return dict(PROFILES["2430930"], cluster=srv.get("clusterId"))
    p = _get(srv.get("appId"))
    if p:
        p = dict(p)
        if srv.get("workshopAppId"):
            p["workshop_appid"] = str(srv["workshopAppId"])
        return p
    exe = os.path.basename(str(srv.get("executable") or "")).lower()
    if exe.endswith(".jar") or exe in ("run.bat", "start.bat") and _looks_minecraft(srv):
        return dict(PROFILES["minecraft"])
    if exe == "bedrock_server.exe":
        return {"source": "folder", "folder": "behavior_packs", "label": "Behavior packs",
                "note": "Bedrock add-ons need their packs added to the world's world_behavior_packs.json."}
    if srv.get("appId"):
        return {"source": "none", "label": "No mods",
                "note": "BaoBun doesn't know how this game loads mods yet. Pick a mod source below "
                        "(Steam Workshop download or a mods folder) if it has them."}
    return {"source": "none", "label": "No mods",
            "note": "Pick a mod source below if this server uses mods."}


def _looks_minecraft(srv: dict) -> bool:
    d = srv.get("directory") or ""
    return any(os.path.exists(os.path.join(d, n)) for n in ("server.properties", "eula.txt"))
