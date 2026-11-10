"""ARK: Survival Ascended plugin: RCON SaveWorld/DoExit stop, save paths, cluster data.

Map lists, mod ids and gameplay settings live in server_manager.py (the GUI's
cluster tools use them); they are re-exported here for plugin consumers.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.plugin_bridge import ManagedServerPlugin  # noqa: E402
from server_manager import (  # noqa: E402,F401  (re-exports)
    ARK_APP_ID, ARK_MAPS, ARK_MAP_MODS, ARK_REFORGE_MODS, ARK_GAMEPLAY_SETTINGS,
    ARK_BASE_GAME_PORT, ARK_BASE_QUERY_PORT, ARK_BASE_RCON_PORT,
)

BOOST_PROFILES = [
    {"name": "Vanilla (1x)", "settings": {"XPMultiplier": "1.0", "TamingSpeedMultiplier": "1.0",
     "HarvestAmountMultiplier": "1.0", "MatingIntervalMultiplier": "1.0",
     "EggHatchSpeedMultiplier": "1.0", "BabyMatureSpeedMultiplier": "1.0",
     "BabyCuddleIntervalMultiplier": "1.0"}},
    {"name": "Relaxed (3x)", "settings": {"XPMultiplier": "3.0", "TamingSpeedMultiplier": "3.0",
     "HarvestAmountMultiplier": "3.0", "MatingIntervalMultiplier": "0.5",
     "EggHatchSpeedMultiplier": "3.0", "BabyMatureSpeedMultiplier": "3.0",
     "BabyCuddleIntervalMultiplier": "0.5"}},
    {"name": "Boosted (5x)", "settings": {"XPMultiplier": "5.0", "TamingSpeedMultiplier": "5.0",
     "HarvestAmountMultiplier": "5.0", "MatingIntervalMultiplier": "0.3",
     "EggHatchSpeedMultiplier": "5.0", "BabyMatureSpeedMultiplier": "5.0",
     "BabyCuddleIntervalMultiplier": "0.3"}},
    {"name": "High Rate (10x)", "settings": {"XPMultiplier": "10.0", "TamingSpeedMultiplier": "10.0",
     "HarvestAmountMultiplier": "10.0", "MatingIntervalMultiplier": "0.15",
     "EggHatchSpeedMultiplier": "10.0", "BabyMatureSpeedMultiplier": "10.0",
     "BabyCuddleIntervalMultiplier": "0.2"}},
]


class ArkPlugin(ManagedServerPlugin):

    def handles(self, srv: Dict[str, Any]) -> bool:
        return bool(srv.get("clusterId")) or str(srv.get("appId") or "") == ARK_APP_ID

    def _rcon_text(self, srv: Dict[str, Any], command: str, quiet: bool = False):
        """Run an RCON command; return its text, or None if RCON isn't set up / failed."""
        mgr = self.manager
        if not mgr:
            return None
        info = mgr.get_rcon_info(srv["id"])
        if not info.get("port") or not info.get("password"):
            return None
        try:
            import rcon
            # ARK doesn't echo the multi-packet end marker, so don't send one.
            return rcon.rcon_command(info["host"], info["port"], info["password"], command,
                                     timeout=10, use_marker=False)
        except Exception as e:
            if not quiet:
                print(f"[WARN] ARK RCON '{command.split()[0]}' failed for {srv.get('name')}: {e}")
            return None

    def _rcon(self, srv: Dict[str, Any], command: str) -> bool:
        return self._rcon_text(srv, command) is not None

    def broadcast(self, srv: Dict[str, Any], message: str) -> bool:
        return self._rcon(srv, "Broadcast " + message)

    def query_players(self, srv: Dict[str, Any]):
        text = self._rcon_text(srv, "ListPlayers", quiet=True)
        if text is None:
            return None
        import re
        names = []
        if "no players connected" not in text.lower():
            for line in text.splitlines():
                pm = re.match(r"^\s*\d+\.\s+(.*?)(?:,\s*[0-9a-fA-F]{16,})?\s*$", line)
                if pm:
                    names.append({"name": pm.group(1).strip()})
        args = srv.get("launchArgs") or ""
        m = re.search(r"(?i)(?:-WinLiveMaxPlayers=|[?&]MaxPlayers=)(\d+)", args)
        return {"count": len(names), "max": int(m.group(1)) if m else None, "names": names}

    def graceful_stop(self, srv: Dict[str, Any]) -> bool:
        if self._rcon(srv, "SaveWorld"):
            time.sleep(5)
            self._rcon(srv, "DoExit")
            return True
        return False

    def stop_timeout(self, srv: Dict[str, Any]) -> int:
        return 120

    def pre_backup(self, srv: Dict[str, Any]) -> None:
        mgr = self.manager
        if mgr and mgr.is_running(srv["id"]) and self._rcon(srv, "SaveWorld"):
            time.sleep(5)

    def save_paths(self, srv: Dict[str, Any]) -> List[str]:
        return ["ShooterGame/Saved/SavedArks", "ShooterGame/Saved/clusters",
                "ShooterGame/Saved/Config/WindowsServer"]

    def log_candidates(self, srv: Dict[str, Any]) -> List[str]:
        d = srv.get("directory") or ""
        return [os.path.join(d, "ShooterGame", "Saved", "Logs", "ShooterGame.log")]

    def player_join_patterns(self, srv: Dict[str, Any]) -> List[str]:
        return [r"joined this ARK", r"Join succeeded"]
