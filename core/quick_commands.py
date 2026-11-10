"""Common console commands per game, for the "Quick commands" menu next to the
console. {player} is filled with a chosen online player, {text} with typed text.
Games that ignore their console window (ARK, Palworld, ...) get these over RCON."""

from __future__ import annotations

import os
from typing import Dict, List

COMMANDS: Dict[str, List[tuple]] = {
    "vintage-story": [
        ("Announce a message", "/announce {text}"),
        ("Save the world now", "/autosavenow"),
        ("List online players", "/list clients"),
        ("Kick a player", "/kick {player}"),
        ("Ban a player", "/ban {player} {text}"),
        ("Set time to day", "/time set day"),
        ("Server stats", "/stats"),
    ],
    "minecraft": [
        ("Say a message", "say {text}"),
        ("List online players", "list"),
        ("Save the world now", "save-all"),
        ("Kick a player", "kick {player}"),
        ("Ban a player", "ban {player}"),
        ("Unban a player", "pardon {player}"),
        ("Make a player operator", "op {player}"),
        ("Remove operator", "deop {player}"),
        ("Add to whitelist", "whitelist add {player}"),
        ("Remove from whitelist", "whitelist remove {player}"),
        ("Set time to day", "time set day"),
        ("Clear the weather", "weather clear"),
    ],
    "258550": [   # Rust
        ("Say a message", "say \"{text}\""),
        ("Server status / players", "status"),
        ("Save the world now", "server.save"),
        ("Kick a player", "kick \"{player}\""),
        ("Ban a player", "ban \"{player}\""),
        ("Set time to noon", "env.time 12"),
    ],
    "380870": [   # Project Zomboid
        ("Message everyone", "servermsg \"{text}\""),
        ("List online players", "players"),
        ("Save the world now", "save"),
        ("Kick a player", "kickuser \"{player}\""),
        ("Ban a player", "banuser \"{player}\""),
        ("Make a player admin", "setaccesslevel \"{player}\" admin"),
    ],
    "294420": [   # 7 Days to Die
        ("Say a message", "say \"{text}\""),
        ("List online players", "listplayers"),
        ("Save the world now", "saveworld"),
        ("Kick a player", "kick \"{player}\""),
    ],
    # these go over RCON (BaoBun routes them automatically once RCON is on)
    "2430930": [   # ARK: Survival Ascended
        ("Broadcast a message", "Broadcast {text}"),
        ("List online players", "ListPlayers"),
        ("Save the world now", "SaveWorld"),
        ("Set time to noon", "SetTimeOfDay 12:00"),
        ("Wipe wild dinos", "DestroyWildDinos"),
    ],
    "2394010": [   # Palworld
        ("Broadcast a message", "Broadcast {text}"),
        ("List online players", "ShowPlayers"),
        ("Save the world now", "Save"),
        ("Server info", "Info"),
    ],
    "443030": [   # Conan Exiles
        ("List online players", "ListPlayers"),
    ],
    "403240": [   # Squad
        ("Broadcast a message", "AdminBroadcast {text}"),
        ("List online players", "ListPlayers"),
        ("Kick a player", "AdminKick \"{player}\" {text}"),
        ("Restart the match", "AdminRestartMatch"),
        ("End the match", "AdminEndMatch"),
    ],
    "233780": [   # Arma 3 (BattlEye RCon)
        ("Message everyone", "say -1 {text}"),
        ("List online players", "players"),
        ("Lock the server", "#lock"),
        ("Unlock the server", "#unlock"),
        ("Restart the mission", "#restart"),
    ],
    "223350": [   # DayZ (BattlEye RCon)
        ("Message everyone", "say -1 {text}"),
        ("List online players", "players"),
        ("Lock the server", "#lock"),
        ("Unlock the server", "#unlock"),
    ],
    "hytale": [
        ("Sign the server in (shows a link)", "/auth login device"),
        ("Keep the sign-in after restarts", "/auth persistence Encrypted"),
    ],
    "1829350": [   # V Rising
        ("Announce a message", "announce {text}"),
    ],
}
ALIASES = {"108600": "380870", "376030": "2430930", "221100": "223350"}


def _is_mc(srv: dict) -> bool:
    from core.minecraft import looks_minecraft
    return looks_minecraft(srv)


def for_server(srv: dict) -> List[dict]:
    from core.hytale import is_hytale
    if (srv.get("type") or "").lower() == "vintage-story":
        key = "vintage-story"
    elif is_hytale(srv):
        key = "hytale"
    elif not srv.get("appId") and _is_mc(srv):
        key = "minecraft"
    else:
        a = str(srv.get("appId") or "") or ("2430930" if srv.get("clusterId") else "")
        key = ALIASES.get(a, a)
    return [{"label": label, "command": cmd, "player": "{player}" in cmd, "text": "{text}" in cmd}
            for label, cmd in COMMANDS.get(key, [])]


def fill(command: str, player: str = "", text: str = "") -> str:
    for bad in ("\r", "\n"):
        player, text = player.replace(bad, " "), text.replace(bad, " ")
    return command.replace("{player}", player.replace('"', "").strip()).replace(
        "{text}", text.replace('"', "'").strip()).strip()
