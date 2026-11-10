"""Minecraft (Java Edition) plugin: "stop" to save and quit, save-all before
backups, world folders, Server List Ping for players, join/leave log lines."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.plugin_bridge import ManagedServerPlugin  # noqa: E402
from core import minecraft as mc  # noqa: E402


class MinecraftPlugin(ManagedServerPlugin):

    def handles(self, srv: Dict[str, Any]) -> bool:
        if srv.get("appId") or (srv.get("type") or "").lower() == "vintage-story":
            return False
        return mc.looks_minecraft(srv)

    def stop_commands(self, srv: Dict[str, Any]) -> List[str]:
        return ["stop"]

    def stop_timeout(self, srv: Dict[str, Any]) -> int:
        return 90

    def _send(self, srv, command) -> bool:
        mgr = self.manager
        try:
            return bool(mgr) and mgr.send_console_command(srv["id"], command).get("status") == "sent"
        except Exception:
            return False

    def pre_backup(self, srv: Dict[str, Any]) -> None:
        mgr = self.manager
        if mgr and mgr.is_running(srv["id"]) and self._send(srv, "save-all flush"):
            time.sleep(5)

    def save_paths(self, srv: Dict[str, Any]) -> List[str]:
        d = srv.get("directory") or ""
        level = mc.read_properties(d).get("level-name") or "world"
        out = [os.path.join(d, level), os.path.join(d, level + "_nether"), os.path.join(d, level + "_the_end")]
        out += [os.path.join(d, n) for n in ("server.properties", "ops.json", "whitelist.json",
                                             "banned-players.json", "banned-ips.json")]
        return out

    def log_candidates(self, srv: Dict[str, Any]) -> List[str]:
        return [os.path.join(srv.get("directory") or "", "logs", "latest.log")]

    def broadcast(self, srv: Dict[str, Any], message: str) -> bool:
        return self._send(srv, "say " + message.replace("\n", " "))

    def query_players(self, srv: Dict[str, Any]):
        res = mc.ping("127.0.0.1", mc.game_port(srv), timeout=1.5)
        if not res or res.get("count") is None:
            return None
        return {"count": res["count"], "max": res.get("max"), "names": res.get("names")}

    def max_players(self, srv: Dict[str, Any]):
        try:
            return int(mc.read_properties(srv.get("directory") or "").get("max-players"))
        except (TypeError, ValueError):
            return None

    def player_log_patterns(self, srv: Dict[str, Any]):
        return {"join": [r"\]: (?P<name>[A-Za-z0-9_]{2,16}) joined the game"],
                "leave": [r"\]: (?P<name>[A-Za-z0-9_]{2,16}) left the game"]}

    def player_join_patterns(self, srv: Dict[str, Any]) -> List[str]:
        return [r"\]: [A-Za-z0-9_]{2,16} joined the game"]
