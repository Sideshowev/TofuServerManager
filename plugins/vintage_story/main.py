"""Vintage Story plugin: graceful /stop, autosave before backups, save/log locations.

ModDB browsing/installing lives in ServerManager (vs_search_mods, vs_download_mod,
...) so the GUI and the API share one implementation.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.plugin_bridge import ManagedServerPlugin  # noqa: E402


def _data_path(srv: Dict[str, Any]) -> str:
    """The VS data folder (shared with ServerManager.vs_data_path)."""
    from server_manager import vs_data_path
    return vs_data_path(srv)


class VintageStoryPlugin(ManagedServerPlugin):

    def handles(self, srv: Dict[str, Any]) -> bool:
        return (srv.get("type") or "").lower() == "vintage-story"

    def stop_commands(self, srv: Dict[str, Any]) -> List[str]:
        return ["/stop"]

    def stop_timeout(self, srv: Dict[str, Any]) -> int:
        return 120  # large worlds can take a while to flush

    def pre_backup(self, srv: Dict[str, Any]) -> None:
        mgr = self.manager
        if mgr and mgr.is_running(srv["id"]):
            try:
                if mgr.send_console_command(srv["id"], "/autosavenow").get("status") == "sent":
                    time.sleep(8)
            except Exception:
                pass

    def save_paths(self, srv: Dict[str, Any]) -> List[str]:
        data = _data_path(srv)
        return [os.path.join(data, "Saves"), os.path.join(data, "Playerdata"),
                os.path.join(data, "serverconfig.json")]

    def log_candidates(self, srv: Dict[str, Any]) -> List[str]:
        data = _data_path(srv)
        logs = os.path.join(data, "Logs")
        return [os.path.join(logs, n) for n in
                ("server-main.log", "stratum-server.log", "server.log")]

    def broadcast(self, srv: Dict[str, Any], message: str) -> bool:
        mgr = self.manager
        try:
            return bool(mgr) and mgr.send_console_command(srv["id"], "/announce " + message).get("status") == "sent"
        except Exception:
            return False

    # Vintage Story has no server-query protocol, so query_players stays None
    # (unknown); player activity for smart boot comes from the join log lines.

    def max_players(self, srv: Dict[str, Any]):
        """MaxClients from serverconfig.json (None if unknown)."""
        import json
        try:
            with open(os.path.join(_data_path(srv), "serverconfig.json"), encoding="utf-8-sig") as f:
                return int(json.load(f).get("MaxClients"))
        except Exception:
            return None

    def player_log_patterns(self, srv: Dict[str, Any]):
        """Vintage Story has no query protocol: follow joins/leaves in the log."""
        return {
            "join": [r"\[Server Event\]\s+(?P<name>\S+)\s+\[?[0-9a-fA-F.:]+\]?:\d+\s+joins",
                     r"\[Server Event\]\s+(?:Player\s+)?(?P<name>\S+)\s+joins",
                     r"\[Server Event\]\s+(?:Player\s+)?(?P<name>\S+)\s+(?:has\s+)?joined"],
            "leave": [r"\[Server Event\]\s+(?:Player\s+)?(?P<name>\S+)\s+(?:has\s+)?left",
                      r"\[Server (?:Event|Notification)\]\s+(?:Player|Client)\s+(?P<name>\S+)\s+(?:disconnected|left)",
                      r"[Cc]lient\s+(?P<name>\S+?)\s+disconnected",
                      r"(?P<name>\S+)\s+(?:got\s+)?(?:kicked|banned)\b"],
        }

    def player_join_patterns(self, srv: Dict[str, Any]) -> List[str]:
        return [r"\[Server Event\].*\bjoins\b", r"Player .+ joined", r"New connection from"]
