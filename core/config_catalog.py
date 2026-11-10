"""Friendly names, help text and limits for well-known game settings.

core/config_forms.py shows every setting in a file; this catalog makes the common
ones readable ("Max players" instead of MaxClients), groups them ("Basics",
"Gameplay"...), gives sensible limits and offers settings the file doesn't list
yet. Unknown keys still appear, just with an automatic label.

Entry: KEY: (label, help, type, extras). type None = guess from the value;
"select" needs extras["options"] = [(value, label), ...] and extras["valuetype"]
for how the value is written. extras["section"] is where a missing key is added.
"""

from __future__ import annotations

from typing import Dict, Optional

B, I, F, T, P, S = "bool", "int", "float", "text", "password", "select"


def _x(**kw):
    return kw


def _build(group: str, rows: dict, section: Optional[str] = None) -> Dict[str, dict]:
    out = {}
    for key, row in rows.items():
        label, help_, typ = row[0], row[1], row[2]
        extra = dict(row[3]) if len(row) > 3 else {}
        meta = {"label": label, "help": help_, "group": extra.pop("group", group)}
        if typ:
            meta["type"] = typ
        if section and "section" not in extra:
            meta["section"] = section
        meta.update(extra)
        out[key] = meta
    return out


def _merge(*parts: Dict[str, dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for p in parts:
        out.update(p)
    return out


def _mult(label: str, help_: str = "", group: str = "Rates"):
    return (label, help_ or "1 = normal. Higher numbers make it bigger/faster.", F,
            _x(min=0, max=100, step=0.1, default=1.0, group=group))


# --------------------------------------------------------------------------- #
# Vintage Story - serverconfig.json
# --------------------------------------------------------------------------- #
VS = _merge(
    _build("Basics", {
        "ServerName": ("Server name", "Shown in the server list and to players.", T),
        "ServerDescription": ("Description", "Shown in the public server list.", T),
        "WelcomeMessage": ("Welcome message", "Shown when someone joins. {0} is the player's name.", T),
        "Password": ("Server password", "Players need it to join. Leave blank to keep the current one.", P),
        "MaxClients": ("Max players", None, I, _x(min=1, max=512, default=16)),
        "AdvertiseServer": ("List on the public server list", None, B),
        "Port": ("Game port", "TCP and UDP. Forward it on your router for friends outside your home.", I,
                 _x(min=1, max=65535, default=42420)),
        "Upnp": ("Open the port on the router automatically (UPnP)", None, B),
        "ServerLanguage": ("Server language", "e.g. en", T),
    }),
    _build("Players & access", {
        "VerifyPlayerAuth": ("Check player accounts", "Only real Vintage Story accounts can join. Keep this on.", B),
        "OnlyWhitelisted": ("Only whitelisted players can join", None, B),
        "DefaultRoleCode": ("Role for new players", "suplayer = normal survival player, admin = full rights.", T),
        "ClientConnectionTimeout": ("Disconnect players after (seconds) without response", None, I,
                                    _x(min=10, max=3600, default=150)),
        "ChatRateLimitMs": ("Chat rate limit (ms between messages)", None, I, _x(min=0, max=60000)),
        "AntiAbuse": ("Anti-abuse level", "0 = off. Higher = stricter checks against cheating clients.", I,
                      _x(min=0, max=2)),
    }),
    _build("World", {
        "Seed": ("World seed", "Only used when a new world is created.", T),
        "WorldName": ("World name", None, T),
        "SaveFileLocation": ("World save file", "Path to the .vcdbs save.", T),
        "AllowCreativeMode": ("Allow creative mode", None, B),
        "PlayStyle": ("Play style", "surviveandbuild, exploration, wildernesssurvival, creativebuilding...", T),
        "WorldType": ("World type", None, T),
        "MapSizeX": ("Map width (blocks)", "Only for new worlds.", I, _x(min=1024, max=67108864)),
        "MapSizeY": ("World height (blocks)", "Only for new worlds. 256 is standard.", I, _x(min=128, max=1024)),
        "MapSizeZ": ("Map length (blocks)", "Only for new worlds.", I, _x(min=1024, max=67108864)),
        "PassTimeWhenEmpty": ("Keep time moving when nobody is online", None, B),
    }),
    _build("Performance", {
        "TickTime": ("Server tick time (ms)", "33.33 = 30 ticks a second. Don't change unless you know why.", F,
                     _x(min=5, max=200)),
        "MaxChunkRadius": ("Max view distance (chunks)", "Lower it if the server lags with many players.", I,
                           _x(min=4, max=64)),
        "SpawnCapPlayerScaling": ("Creature spawn scaling per player", None, F, _x(min=0, max=5)),
        "CompressPackets": ("Compress network traffic", None, B),
        "DieBelowDiskSpaceMb": ("Stop the server below this much free disk (MB)", None, I, _x(min=0)),
        "RandomBlockTicksPerChunk": ("Random block ticks per chunk", None, I, _x(min=0, max=1000)),
    }),
    _build("Safety", {
        "CorruptionProtection": ("Protect the world from corruption", "Keep this on.", B),
        "RegenerateCorruptChunks": ("Regenerate corrupt chunks", None, B),
        "RepairMode": ("Repair mode", "Starts the server in repair mode for a damaged world. Turn it off again after.", B),
    }),
)

# where these sit in serverconfig.json differs between versions: label them, never add them
for _k in ("Seed", "WorldName", "SaveFileLocation", "AllowCreativeMode", "PlayStyle", "WorldType",
           "MapSizeX", "MapSizeY", "MapSizeZ", "AntiAbuse", "ServerLanguage"):
    VS[_k]["hidden"] = True

# --------------------------------------------------------------------------- #
# Minecraft - server.properties
# --------------------------------------------------------------------------- #
MC = _merge(
    _build("Basics", {
        "motd": ("Server message (MOTD)", "Shown in the multiplayer server list.", T),
        "max-players": ("Max players", None, I, _x(min=1, max=1000, default=20)),
        "server-port": ("Game port", "TCP. Forward it on your router for friends outside your home.", I,
                        _x(min=1, max=65535, default=25565)),
        "white-list": ("Only whitelisted players can join", None, B),
        "enforce-whitelist": ("Kick players removed from the whitelist", None, B),
        "online-mode": ("Check Minecraft accounts", "Keep on, or anyone can join with any name.", B),
        "hide-online-players": ("Hide the player list in the server list", None, B),
        "server-ip": ("Listen only on this IP", "Leave blank.", T),
    }),
    _build("Gameplay", {
        "gamemode": ("Game mode", None, S, _x(options=[("survival", "Survival"), ("creative", "Creative"),
                                                     ("adventure", "Adventure"), ("spectator", "Spectator")])),
        "difficulty": ("Difficulty", None, S, _x(options=[("peaceful", "Peaceful"), ("easy", "Easy"),
                                                         ("normal", "Normal"), ("hard", "Hard")])),
        "hardcore": ("Hardcore (one life)", None, B),
        "pvp": ("Players can hurt each other (PvP)", None, B),
        "force-gamemode": ("Force the game mode on join", None, B),
        "allow-flight": ("Allow flying (needed by some mods)", None, B),
        "allow-nether": ("Allow the Nether", None, B),
        "spawn-monsters": ("Spawn monsters", None, B),
        "spawn-animals": ("Spawn animals", None, B),
        "spawn-npcs": ("Spawn villagers", None, B),
        "spawn-protection": ("Spawn protection radius (blocks)", "Non-ops can't build this close to spawn. 0 = off.",
                             I, _x(min=0, max=1000, default=16)),
        "enable-command-block": ("Allow command blocks", None, B),
        "player-idle-timeout": ("Kick idle players after (minutes)", "0 = never.", I, _x(min=0)),
        "op-permission-level": ("Operator permission level", "4 = full control.", I, _x(min=1, max=4)),
    }),
    _build("World", {
        "level-name": ("World folder name", None, T),
        "level-seed": ("World seed", "Only used for a new world.", T),
        "level-type": ("World type", "Only used for a new world.", T),
        "generate-structures": ("Generate villages and structures", None, B),
        "max-world-size": ("World border radius (blocks)", None, I, _x(min=1, max=29999984)),
    }),
    _build("Performance", {
        "view-distance": ("View distance (chunks)", "Lower it if the server lags.", I, _x(min=3, max=32, default=10)),
        "simulation-distance": ("Simulation distance (chunks)", "How far mobs and crops keep working.", I,
                                _x(min=3, max=32, default=10)),
        "max-tick-time": ("Watchdog: stop after a tick takes (ms)", "-1 = off.", I, _x(min=-1)),
        "network-compression-threshold": ("Compress packets bigger than (bytes)", None, I, _x(min=-1)),
        "sync-chunk-writes": ("Save chunks immediately", None, B),
        "entity-broadcast-range-percentage": ("Entity visibility range (%)", None, I, _x(min=10, max=1000)),
        "pause-when-empty-seconds": ("Pause the server when empty for (seconds)", "0 = never pause.", I, _x(min=0)),
    }),
    _build("Remote admin", {
        "enable-rcon": ("Turn on RCON", "Lets BaoBun send commands when the console isn't attached.", B),
        "rcon.port": ("RCON port", None, I, _x(min=1, max=65535, default=25575)),
        "rcon.password": ("RCON password", None, P),
        "enable-query": ("Turn on the query port", None, B),
        "query.port": ("Query port", None, I, _x(min=1, max=65535)),
    }),
)

# --------------------------------------------------------------------------- #
# Palworld - PalWorldSettings.ini OptionSettings
# --------------------------------------------------------------------------- #
PAL = _merge(
    _build("Basics", {
        "ServerName": ("Server name", None, T),
        "ServerDescription": ("Description", None, T),
        "ServerPassword": ("Server password", "Leave blank to keep the current one.", P),
        "AdminPassword": ("Admin password", "Also used for RCON.", P),
        "ServerPlayerMaxNum": ("Max players", None, I, _x(min=1, max=32, default=32)),
        "PublicPort": ("Game port", "UDP.", I, _x(min=1, max=65535, default=8211)),
        "Region": ("Region", None, T),
        "bShowPlayerList": ("Show the player list in game", None, B),
        "AllowConnectPlatform": ("Allowed platforms", "Steam or Xbox.", T),
    }),
    _build("Gameplay", {
        "Difficulty": ("Difficulty", "None = use the rates below.", T),
        "DeathPenalty": ("What you drop when you die", None, S,
                         _x(options=[("None", "Nothing"), ("Item", "Items"), ("ItemAndEquipment", "Items and gear"),
                                     ("All", "Everything incl. Pals")])),
        "bIsPvP": ("Player vs player (PvP)", None, B),
        "bEnablePlayerToPlayerDamage": ("Players can hurt each other", None, B),
        "bEnableFriendlyFire": ("Friendly fire", None, B),
        "bEnableInvaderEnemy": ("Raids on bases", None, B),
        "bEnableFastTravel": ("Fast travel", None, B),
        "bIsStartLocationSelectByMap": ("Choose start location on the map", None, B),
        "bExistPlayerAfterLogout": ("Player body stays after logout", None, B),
        "bEnableDefenseOtherGuildPlayer": ("Defend against other guilds", None, B),
        "bCanPickupOtherGuildDeathPenaltyDrop": ("Can loot other guilds' death drops", None, B),
        "GuildPlayerMaxNum": ("Max players per guild", None, I, _x(min=1, max=100)),
        "BaseCampMaxNum": ("Max bases (whole server)", None, I, _x(min=1, max=10000)),
        "BaseCampWorkerMaxNum": ("Max Pals working at a base", None, I, _x(min=1, max=50)),
        "PalEggDefaultHatchingTime": ("Egg hatching time (hours)", None, F, _x(min=0, max=240)),
        "CoopPlayerMaxNum": ("Max co-op players", None, I, _x(min=1, max=32)),
        "bAutoResetGuildNoOnlinePlayers": ("Reset guilds nobody has played in", None, B),
        "AutoResetGuildTimeNoOnlinePlayers": ("... after this many hours", None, F, _x(min=1)),
    }),
    _build("Rates", {
        "ExpRate": _mult("XP rate"),
        "PalCaptureRate": _mult("Capture rate"),
        "PalSpawnNumRate": _mult("Pal spawn amount", "Higher = more Pals in the world (more server load)."),
        "WorkSpeedRate": _mult("Work speed"),
        "DayTimeSpeedRate": _mult("Day speed"),
        "NightTimeSpeedRate": _mult("Night speed"),
        "CollectionDropRate": _mult("Gathering amount"),
        "CollectionObjectHpRate": _mult("Tree/rock health"),
        "CollectionObjectRespawnSpeedRate": _mult("Tree/rock respawn speed"),
        "EnemyDropItemRate": _mult("Enemy loot amount"),
    }),
    _build("Combat & survival", {
        "PalDamageRateAttack": _mult("Pal damage dealt", group="Combat & survival"),
        "PalDamageRateDefense": _mult("Pal damage taken", group="Combat & survival"),
        "PlayerDamageRateAttack": _mult("Player damage dealt", group="Combat & survival"),
        "PlayerDamageRateDefense": _mult("Player damage taken", group="Combat & survival"),
        "PlayerStomachDecreaceRate": _mult("Player hunger speed", group="Combat & survival"),
        "PlayerStaminaDecreaceRate": _mult("Player stamina use", group="Combat & survival"),
        "PlayerAutoHPRegeneRate": _mult("Player health regen", group="Combat & survival"),
        "PlayerAutoHpRegeneRateInSleep": _mult("Player regen while sleeping", group="Combat & survival"),
        "PalStomachDecreaceRate": _mult("Pal hunger speed", group="Combat & survival"),
        "PalStaminaDecreaceRate": _mult("Pal stamina use", group="Combat & survival"),
        "PalAutoHPRegeneRate": _mult("Pal health regen", group="Combat & survival"),
        "PalAutoHpRegeneRateInSleep": _mult("Pal regen in Palbox", group="Combat & survival"),
        "BuildObjectDamageRate": _mult("Damage to buildings", group="Combat & survival"),
        "BuildObjectDeteriorationDamageRate": _mult("Building decay speed", group="Combat & survival"),
    }),
    _build("Items & performance", {
        "DropItemMaxNum": ("Max dropped items in the world", None, I, _x(min=0, max=100000)),
        "DropItemAliveMaxHours": ("Dropped items vanish after (hours)", None, F, _x(min=0)),
        "bIsUseBackupSaveData": ("Keep backup saves", None, B),
    }),
    _build("Remote admin", {
        "RCONEnabled": ("Turn on RCON", "BaoBun needs this for warnings, saves and clean stops.", B),
        "RCONPort": ("RCON port", None, I, _x(min=1, max=65535, default=25575)),
        "RESTAPIEnabled": ("Turn on the REST API", None, B),
        "RESTAPIPort": ("REST API port", None, I, _x(min=1, max=65535, default=8212)),
        "bUseAuth": ("Check player accounts", None, B),
    }),
)

# --------------------------------------------------------------------------- #
# ARK - GameUserSettings.ini / Game.ini
# --------------------------------------------------------------------------- #
ARK_SS = "ServerSettings"
ARK_GUS = _merge(
    _build("Basics", {
        "SessionName": ("Server name", None, T, _x(section="SessionSettings")),
        "ServerPassword": ("Server password", None, P),
        "ServerAdminPassword": ("Admin password", "Also used for RCON.", P),
        "SpectatorPassword": ("Spectator password", None, P),
        "MaxPlayers": ("Max players", None, I, _x(min=1, max=200, section="/Script/Engine.GameSession")),
        "Message": ("Message of the day", None, T, _x(section="MessageOfTheDay")),
        "Duration": ("Message of the day shows for (seconds)", None, I, _x(min=0, section="MessageOfTheDay")),
    }, ARK_SS),
    _build("Rules", {
        "ServerPVE": ("PvE only (no player vs player)", None, B),
        "ServerHardcore": ("Hardcore (respawn as a new survivor)", None, B),
        "AllowThirdPersonPlayer": ("Allow third-person view", None, B),
        "ServerCrosshair": ("Show crosshair", None, B),
        "ShowMapPlayerLocation": ("Show your position on the map", None, B),
        "ShowFloatingDamageText": ("Show damage numbers", None, B),
        "AlwaysNotifyPlayerJoined": ("Announce players joining", None, B),
        "AlwaysNotifyPlayerLeft": ("Announce players leaving", None, B),
        "AllowFlyerCarryPvE": ("Flyers can carry in PvE", None, B),
        "AllowCaveBuildingPvE": ("Allow building in caves (PvE)", None, B),
        "DisableStructureDecayPvE": ("No building decay (PvE)", None, B),
        "PvEStructureDecayPeriodMultiplier": _mult("Building decay time (PvE)", group="Rules"),
        "PreventDownloadSurvivors": ("Block bringing survivors from other servers", None, B),
        "PreventDownloadItems": ("Block bringing items from other servers", None, B),
        "PreventDownloadDinos": ("Block bringing dinos from other servers", None, B),
        "KickIdlePlayersPeriod": ("Kick idle players after (seconds)", None, F, _x(min=0)),
        "MaxTamedDinos": ("Max tamed dinos (server)", None, I, _x(min=0, max=100000)),
        "TheMaxStructuresInRange": ("Max structures in an area", None, I, _x(min=0)),
        "AutoSavePeriodMinutes": ("Autosave every (minutes)", None, F, _x(min=1, max=600, default=15)),
    }, ARK_SS),
    _build("Difficulty", {
        "DifficultyOffset": ("Difficulty offset", "0-1. Together with the setting below it sets max wild levels.",
                             F, _x(min=0, max=1)),
        "OverrideOfficialDifficulty": ("Override difficulty", "5 = wild dinos up to level 150 (official servers).",
                                       F, _x(min=0, max=100)),
    }, ARK_SS),
    _build("Rates", {
        "XPMultiplier": _mult("XP"),
        "TamingSpeedMultiplier": _mult("Taming speed"),
        "HarvestAmountMultiplier": _mult("Harvest amount"),
        "HarvestHealthMultiplier": _mult("Resource node health"),
        "ResourcesRespawnPeriodMultiplier": _mult("Resource respawn time", "Lower = resources come back faster."),
        "ItemStackSizeMultiplier": _mult("Stack size"),
        "DayCycleSpeedScale": _mult("Day/night cycle speed"),
        "DayTimeSpeedScale": _mult("Day speed"),
        "NightTimeSpeedScale": _mult("Night speed"),
    }, ARK_SS),
    _build("Combat & survival", {
        "PlayerDamageMultiplier": _mult("Player damage dealt", group="Combat & survival"),
        "PlayerResistanceMultiplier": _mult("Player damage taken", group="Combat & survival"),
        "DinoDamageMultiplier": _mult("Dino damage dealt", group="Combat & survival"),
        "DinoResistanceMultiplier": _mult("Dino damage taken", group="Combat & survival"),
        "StructureDamageMultiplier": _mult("Structure damage dealt", group="Combat & survival"),
        "StructureResistanceMultiplier": _mult("Structure damage taken", group="Combat & survival"),
        "PlayerCharacterFoodDrainMultiplier": _mult("Player hunger", group="Combat & survival"),
        "PlayerCharacterWaterDrainMultiplier": _mult("Player thirst", group="Combat & survival"),
        "PlayerCharacterStaminaDrainMultiplier": _mult("Player stamina use", group="Combat & survival"),
        "PlayerCharacterHealthRecoveryMultiplier": _mult("Player health regen", group="Combat & survival"),
        "DinoCharacterFoodDrainMultiplier": _mult("Dino hunger", group="Combat & survival"),
        "DinoCountMultiplier": _mult("Wild dino amount", "Higher = more dinos (more server load).",
                                     group="Combat & survival"),
        "OxygenSwimSpeedStatMultiplier": _mult("Swim speed from oxygen", group="Combat & survival"),
    }, ARK_SS),
    _build("Remote admin", {
        "RCONEnabled": ("Turn on RCON", "BaoBun needs this for warnings, saves and clean stops.", B),
        "RCONPort": ("RCON port", None, I, _x(min=1, max=65535, default=27020)),
        "RCONServerGameLogBuffer": ("RCON log buffer (lines)", None, I, _x(min=0)),
    }, ARK_SS),
)
ARK_GAME_SEC = "/Script/ShooterGame.ShooterGameMode"
ARK_GAME = _merge(
    _build("Breeding", {
        "MatingIntervalMultiplier": _mult("Time between mating", "Lower = can mate again sooner.", "Breeding"),
        "EggHatchSpeedMultiplier": _mult("Egg hatch speed", group="Breeding"),
        "BabyMatureSpeedMultiplier": _mult("Baby growth speed", group="Breeding"),
        "BabyFoodConsumptionSpeedMultiplier": _mult("Baby food use", group="Breeding"),
        "BabyCuddleIntervalMultiplier": _mult("Time between imprint cuddles", "Lower = cuddle more often.", "Breeding"),
        "BabyImprintAmountMultiplier": _mult("Imprint per cuddle", group="Breeding"),
        "LayEggIntervalMultiplier": _mult("Time between laying eggs", group="Breeding"),
    }, ARK_GAME_SEC),
    _build("Rates", {
        "KillXPMultiplier": _mult("XP from kills"),
        "HarvestXPMultiplier": _mult("XP from harvesting"),
        "CraftXPMultiplier": _mult("XP from crafting"),
        "GenericXPMultiplier": _mult("XP over time"),
        "SpecialXPMultiplier": _mult("XP from special events"),
        "CropGrowthSpeedMultiplier": _mult("Crop growth speed"),
        "CropDecaySpeedMultiplier": _mult("Crop decay speed"),
        "PoopIntervalMultiplier": _mult("Time between poops"),
        "HairGrowthSpeedMultiplier": _mult("Hair growth speed"),
        "FuelConsumptionIntervalMultiplier": _mult("Fuel lasts", "Higher = fuel lasts longer."),
        "DinoHarvestingDamageMultiplier": _mult("Dino harvesting power"),
        "PlayerHarvestingDamageMultiplier": _mult("Player harvesting power"),
        "SupplyCrateLootQualityMultiplier": _mult("Supply crate loot quality"),
        "FishingLootQualityMultiplier": _mult("Fishing loot quality"),
        "CustomRecipeEffectivenessMultiplier": _mult("Custom recipe strength"),
    }, ARK_GAME_SEC),
    _build("Rules", {
        "bDisableStructurePlacementCollision": ("Build through rocks and terrain", None, B),
        "bAllowUnlimitedRespecs": ("Unlimited Mindwipe respecs", None, B),
        "bAllowPlatformSaddleMultiFloors": ("Multi-floor platform saddles", None, B),
        "bDisableFriendlyFire": ("No friendly fire", None, B),
        "bUseSingleplayerSettings": ("Use singleplayer balance", None, B),
        "MaxNumberOfPlayersInTribe": ("Max players per tribe", "0 = no limit.", I, _x(min=0, max=1000)),
    }, ARK_GAME_SEC),
)

# --------------------------------------------------------------------------- #
# 7 Days to Die - serverconfig.xml
# --------------------------------------------------------------------------- #
_ZMOVE = [("0", "Walk"), ("1", "Jog"), ("2", "Run"), ("3", "Sprint"), ("4", "Nightmare")]
SDTD = _merge(
    _build("Basics", {
        "ServerName": ("Server name", None, T),
        "ServerDescription": ("Description", None, T),
        "ServerPassword": ("Server password", None, P),
        "ServerMaxPlayerCount": ("Max players", None, I, _x(min=1, max=64, default=8)),
        "ServerPort": ("Game port", None, I, _x(min=1, max=65535, default=26900)),
        "ServerVisibility": ("Who can see it in the server list", None, S,
                             _x(options=[("2", "Everyone"), ("1", "Friends only"), ("0", "Nobody")], valuetype="int")),
        "ServerAdminSlots": ("Extra slots for admins", None, I, _x(min=0, max=10)),
        "ServerAllowCrossplay": ("Allow console players (crossplay)", None, B),
        "EACEnabled": ("Easy Anti-Cheat", None, B),
    }),
    _build("World", {
        "GameWorld": ("World", "Navezgane, or RWG for a random world.", T),
        "WorldGenSeed": ("Random world seed", None, T),
        "WorldGenSize": ("Random world size", "6144, 8192 or 10240.", I, _x(min=2048, max=16384)),
        "GameName": ("Save name", None, T),
    }),
    _build("Gameplay", {
        "GameDifficulty": ("Difficulty", None, S, _x(options=[("0", "Scavenger"), ("1", "Adventurer"), ("2", "Nomad"),
                                                         ("3", "Warrior"), ("4", "Survivalist"), ("5", "Insane")],
                                                valuetype="int")),
        "DayNightLength": ("Real minutes per game day", None, I, _x(min=10, max=240, default=60)),
        "DayLightLength": ("Daylight hours per day", None, I, _x(min=0, max=24, default=18)),
        "XPMultiplier": ("XP (%)", None, I, _x(min=0, max=1000, default=100)),
        "LootAbundance": ("Loot amount (%)", None, I, _x(min=0, max=1000, default=100)),
        "LootRespawnDays": ("Loot respawns after (days)", None, I, _x(min=0, max=365)),
        "AirDropFrequency": ("Air drop every (game hours)", "0 = off.", I, _x(min=0)),
        "DropOnDeath": ("Drop on death", None, S, _x(options=[("0", "Nothing"), ("1", "Everything"),
                                                           ("2", "Toolbelt only"), ("3", "Backpack only"),
                                                           ("4", "Delete all")], valuetype="int")),
        "DropOnQuit": ("Drop on quit", None, S, _x(options=[("0", "Nothing"), ("1", "Everything"),
                                                         ("2", "Toolbelt only"), ("3", "Backpack only")],
                                                valuetype="int")),
        "PlayerKillingMode": ("Player killing", None, S, _x(options=[("0", "No killing"), ("1", "Allies only"),
                                                                  ("2", "Strangers only"), ("3", "Everyone")],
                                                         valuetype="int")),
        "BuildCreate": ("Creative mode (cheats)", None, B),
        "LandClaimCount": ("Land claims per player", None, I, _x(min=0, max=100)),
        "LandClaimSize": ("Land claim size (blocks)", None, I, _x(min=1, max=200)),
        "PartySharedKillRange": ("Party XP sharing range", None, I, _x(min=0)),
        "BedrollDeadZoneSize": ("No-zombie zone around bedrolls", None, I, _x(min=0)),
    }),
    _build("Zombies", {
        "EnemySpawnMode": ("Spawn zombies", None, B),
        "ZombieMove": ("Zombie speed (day)", None, S, _x(options=_ZMOVE, valuetype="int")),
        "ZombieMoveNight": ("Zombie speed (night)", None, S, _x(options=_ZMOVE, valuetype="int")),
        "ZombieFeralMove": ("Feral zombie speed", None, S, _x(options=_ZMOVE, valuetype="int")),
        "ZombieBMMove": ("Blood moon zombie speed", None, S, _x(options=_ZMOVE, valuetype="int")),
        "BloodMoonFrequency": ("Blood moon every (days)", "0 = never.", I, _x(min=0, max=365, default=7)),
        "BloodMoonEnemyCount": ("Blood moon zombies per player", None, I, _x(min=0, max=64, default=8)),
        "MaxSpawnedZombies": ("Max zombies alive", "Lower it if the server lags.", I, _x(min=0, max=1000)),
        "MaxSpawnedAnimals": ("Max animals alive", None, I, _x(min=0, max=1000)),
    }),
    _build("Remote admin", {
        "TelnetEnabled": ("Turn on telnet", None, B),
        "TelnetPort": ("Telnet port", None, I, _x(min=1, max=65535)),
        "TelnetPassword": ("Telnet password", None, P),
        "ControlPanelEnabled": ("Built-in web control panel", None, B),
    }),
)

# --------------------------------------------------------------------------- #
# Project Zomboid - servertest.ini
# --------------------------------------------------------------------------- #
PZ = _merge(
    _build("Basics", {
        "PublicName": ("Server name", None, T),
        "PublicDescription": ("Description", None, T),
        "Password": ("Server password", None, P),
        "MaxPlayers": ("Max players", None, I, _x(min=1, max=100, default=32)),
        "Public": ("List on the public server list", None, B),
        "Open": ("Anyone can join (no whitelist)", None, B),
        "AutoCreateUserInWhiteList": ("Add new players to the whitelist", None, B),
        "ServerWelcomeMessage": ("Welcome message", None, T),
        "DefaultPort": ("Game port", None, I, _x(min=1, max=65535, default=16261)),
        "UPnP": ("Open ports on the router automatically (UPnP)", None, B),
    }),
    _build("Gameplay", {
        "PVP": ("Player vs player (PvP)", None, B),
        "PauseEmpty": ("Pause when nobody is online", None, B),
        "GlobalChat": ("Global chat", None, B),
        "SafetySystem": ("PvP safety toggle", None, B),
        "NoFire": ("No fire", None, B),
        "AnnounceDeath": ("Announce deaths", None, B),
        "SleepAllowed": ("Sleeping allowed", None, B),
        "SleepNeeded": ("Sleep needed", None, B),
        "PlayerRespawnWithSelf": ("Respawn at your death spot", None, B),
        "PlayerRespawnWithOther": ("Respawn at a friend", None, B),
        "Faction": ("Factions", None, B),
        "AllowDestructionBySledgehammer": ("Allow sledgehammer destruction", None, B),
        "HoursForLootRespawn": ("Loot respawns after (hours)", "0 = never.", I, _x(min=0)),
        "MaxItemsForLootRespawn": ("Don't respawn loot in containers with more than", None, I, _x(min=0)),
        "ConstructionPreventsLootRespawn": ("Building nearby stops loot respawn", None, B),
    }),
    _build("Safehouses", {
        "PlayerSafehouse": ("Players can claim safehouses", None, B),
        "AdminSafehouse": ("Only admins can claim safehouses", None, B),
        "SafehouseAllowTrepass": ("Others can enter safehouses", None, B),
        "SafehouseAllowFire": ("Fire can hurt safehouses", None, B),
        "SafehouseAllowLoot": ("Others can loot safehouses", None, B),
        "SafehouseAllowRespawn": ("Respawn in your safehouse", None, B),
        "SafehouseDaySurvivedToClaim": ("Days survived before claiming", None, I, _x(min=0)),
    }),
    _build("Saves & admin", {
        "SaveWorldEveryMinutes": ("Save the world every (minutes)", "0 = only on shutdown.", I, _x(min=0)),
        "BackupsCount": ("Backups to keep", None, I, _x(min=0, max=300)),
        "BackupsOnStart": ("Back up on every start", None, B),
        "BackupsPeriod": ("Back up every (minutes)", "0 = off.", I, _x(min=0)),
        "RCONPort": ("RCON port", None, I, _x(min=1, max=65535, default=27015)),
        "RCONPassword": ("RCON password", None, P),
        "SteamVAC": ("Valve anti-cheat", None, B),
        "PingLimit": ("Kick players with ping over (ms)", "0 = off.", I, _x(min=0)),
    }),
)

# --------------------------------------------------------------------------- #
# Enshrouded - enshrouded_server.json
# --------------------------------------------------------------------------- #
ENSH = _build("Basics", {
    "name": ("Server name", None, T),
    "password": ("Server password", None, P),
    "slotCount": ("Max players", None, I, _x(min=1, max=16, default=16)),
    "queryPort": ("Query port", None, I, _x(min=1, max=65535, default=15637)),
    "ip": ("Listen on IP", "0.0.0.0 = all.", T),
    "gameSettingsPreset": ("Difficulty preset", None, S,
                           _x(options=[("Default", "Default"), ("Relaxed", "Relaxed"), ("Hard", "Hard"),
                                       ("Survival", "Survival"), ("Custom", "Custom (use the values below)")])),
    "enableVoiceChat": ("Voice chat", None, B),
    "enableTextChat": ("Text chat", None, B),
    "saveDirectory": ("Save folder", None, T),
    "logDirectory": ("Log folder", None, T),
})

CATALOG = {
    ("vintage-story", "Server settings"): VS,
    ("minecraft", "Server settings"): MC,
    ("2394010", "World settings"): PAL,
    ("2430930", "Server settings"): ARK_GUS, ("376030", "Server settings"): ARK_GUS,
    ("2430930", "Game rules"): ARK_GAME, ("376030", "Game rules"): ARK_GAME,
    ("294420", "Server settings"): SDTD,
    ("380870", "Server settings"): PZ,
    ("2278520", "Server settings"): ENSH,
}
# raw files picked from the list that are really one of the above
BY_FILENAME = {"serverconfig.json": ("vintage-story", "Server settings"),
               "server.properties": ("minecraft", "Server settings"),
               "palworldsettings.ini": ("2394010", "World settings"),
               "gameusersettings.ini": ("2430930", "Server settings"),
               "game.ini": ("2430930", "Game rules"),
               "serverconfig.xml": ("294420", "Server settings"),
               "enshrouded_server.json": ("2278520", "Server settings")}


def lookup(srv: dict, file_id: str, label: Optional[str]) -> Optional[Dict[str, dict]]:
    from core.quick_settings import game_key
    key = game_key(srv) or str(srv.get("appId") or "")
    cat = CATALOG.get((key, label or ""))
    if cat is None and file_id.startswith("rel:"):
        name = file_id[4:].replace("\\", "/").rsplit("/", 1)[-1].lower()
        hit = BY_FILENAME.get(name)
        if hit and (hit[0] == key or name in ("serverconfig.json", "server.properties", "palworldsettings.ini")):
            cat = CATALOG.get(hit)
    return cat


def meta_for(cat: Optional[Dict[str, dict]], key: str) -> dict:
    if not cat:
        return {}
    if key in cat:
        return cat[key]
    low = key.lower()
    return next((v for k, v in cat.items() if k.lower() == low), {})
