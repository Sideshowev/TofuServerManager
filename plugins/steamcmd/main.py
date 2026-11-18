"""Generic SteamCMD game plugin (CS2, Rust, Valheim, Palworld, Enshrouded, ...).

Games differ a lot in how they shut down cleanly, so this plugin only knows a
few safe defaults by Steam App ID. Any server can override with the
"stopCommand" field (sent to the console) in servers.json / Edit Server.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.plugin_bridge import ManagedServerPlugin  # noqa: E402

# App ID -> console command that saves and exits (sent to stdin).
# Unsupported commands are harmless: the server is force-stopped after the
# grace period. Add more here, or set "stopCommand" on a single server.
KNOWN_STOP_COMMANDS = {
    "740": ["quit"],       # CS:GO dedicated (srcds)
    "730": ["quit"],       # CS2
    "232250": ["quit"],    # TF2 (srcds)
    "222860": ["quit"],    # Left 4 Dead 2 (srcds)
    "258550": ["quit"],    # Rust
}

# App ID -> save folders (relative to the install directory). Servers not listed
# fall back to Tofu's folder-name heuristics; set "savePaths" on a server to override.
KNOWN_SAVE_PATHS = {
    "2394010": ["Pal/Saved"],   # Palworld
    "2278520": ["savegame"],    # Enshrouded
    "258550": ["server"],       # Rust (server/<identity>)
}


# App ID -> console command template for an in-game message.
KNOWN_BROADCAST = {
    "740": "say {message}", "730": "say {message}", "232250": "say {message}",
    "222860": "say {message}", "258550": "say {message}",
}


def _ports_from_args(args: str):
    """(game_port, query_port) found in a launch-argument string."""
    import re
    def find(*pats):
        for pat in pats:
            m = re.search(pat, args or "", re.IGNORECASE)
            if m:
                return int(m.group(1))
        return None
    query = find(r"-QueryPort[= ](\d+)", r"\+sv_queryport[= ](\d+)", r"\+server\.queryport[= ](\d+)",
                 r"[?&]QueryPort=(\d+)")
    game = find(r"-Port[= ](\d+)", r"\+port[= ](\d+)", r"\+server\.port[= ](\d+)",
                r"-port[= ](\d+)", r"[?&]Port=(\d+)")
    return game, query


def _rcon_profile(srv: Dict[str, Any]):
    try:
        from core import rcon_config
        return rcon_config.profile(srv)
    except Exception:
        return None


class SteamCmdPlugin(ManagedServerPlugin):

    def handles(self, srv: Dict[str, Any]) -> bool:
        return (srv.get("type") or "").lower() == "steamcmd" or bool(srv.get("appId"))

    def stop_commands(self, srv: Dict[str, Any]) -> List[str]:
        known = KNOWN_STOP_COMMANDS.get(str(srv.get("appId") or ""))
        if known:
            return list(known)
        prof = _rcon_profile(srv)   # e.g. Palworld: Save + DoExit over RCON
        return list(prof.get("stop") or []) if prof else []

    def pre_backup(self, srv: Dict[str, Any]) -> None:
        prof = _rcon_profile(srv)
        mgr = self.manager
        if prof and prof.get("save") and mgr and mgr.is_running(srv["id"]):
            try:
                if mgr.send_console_command(srv["id"], prof["save"]).get("status") == "sent":
                    import time
                    time.sleep(5)
            except Exception:
                pass

    def stop_timeout(self, srv: Dict[str, Any]) -> int:
        return 60

    def save_paths(self, srv: Dict[str, Any]) -> List[str]:
        return list(KNOWN_SAVE_PATHS.get(str(srv.get("appId") or ""), []))

    def log_candidates(self, srv: Dict[str, Any]) -> List[str]:
        d = srv.get("directory") or ""
        return [os.path.join(d, n) for n in ("server.log", "console-output.log", "output_log.txt")]

    def broadcast(self, srv: Dict[str, Any], message: str) -> bool:
        tmpl = KNOWN_BROADCAST.get(str(srv.get("appId") or ""))
        if not tmpl:
            prof = _rcon_profile(srv)
            tmpl = prof.get("broadcast") if prof else None
        mgr = self.manager
        if not tmpl or not mgr:
            return False
        try:
            return mgr.send_console_command(srv["id"], tmpl.replace("{message}", message)).get("status") == "sent"
        except Exception:
            return False

    def query_players(self, srv: Dict[str, Any]):
        """Steam A2S query on the server's query port (tries the usual candidates)."""
        from core.a2s import query_info
        game, query = _ports_from_args(srv.get("launchArgs") or "")
        game = srv.get("port") or game
        candidates = []
        for p in (srv.get("queryPort"), query, (game + 1) if game else None, game, 27015):
            if p and p not in candidates:
                candidates.append(int(p))
        known = (self._query_port_cache or {}).get(srv["id"])
        if known in candidates:
            candidates.remove(known)
            candidates.insert(0, known)
        # Most games answer on 127.0.0.1. Source engine servers only listen on this PC's
        # network address, so that is asked too - but only after an answer was found there
        # once, or for a Source server, so other games don't wait twice as long for nothing.
        from core.a2s import local_hosts
        hosts = local_hosts(srv.get("launchArgs") or "")
        if not hasattr(self, "_query_host_cache"):
            self._query_host_cache = {}
        host0 = self._query_host_cache.get(srv["id"])
        exe = str(srv.get("executable") or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
        if host0 in hosts:
            hosts = [host0]
        elif exe not in ("srcds.exe", "srcds_win64.exe", "hlds.exe"):
            hosts = hosts[:1] if hosts[0] != "127.0.0.1" else ["127.0.0.1"]
        for host in hosts:
            for port in candidates[:4]:
                info = query_info(host, port, timeout=1.0)
                if info:
                    self._query_port_cache[srv["id"]] = port
                    self._query_host_cache[srv["id"]] = host
                    names = None
                    if info["players"]:
                        from core.a2s import query_players as a2s_players
                        lst = a2s_players(host, port, timeout=1.0)
                        if lst is not None:
                            names = [{"name": p["name"], "seconds": p["seconds"]} for p in lst]
                    return {"count": info["players"], "max": info["max_players"], "names": names}
        return self._rcon_players(srv)

    def _rcon_players(self, srv: Dict[str, Any]):
        """No Steam query answer: ask over RCON (ListPlayers / ShowPlayers) if it's set up."""
        from core import rcon_config
        prof = rcon_config.profile(srv)
        if not prof or not prof.get("players") or not rcon_config.info(srv):
            return None
        try:
            names = rcon_config.parse_players(rcon_config.key_for(srv), rcon_config.send(srv, prof["players"], timeout=5))
        except Exception:
            return None
        if names is None:
            return None
        mx = srv.get("maxPlayers")
        if mx is None:
            from core.quick_settings import max_players
            mx = max_players(srv)
        return {"count": len(names), "max": mx, "names": [{"name": n} for n in names]}

    _query_port_cache: Dict[str, int] = {}

    def player_join_patterns(self, srv: Dict[str, Any]) -> List[str]:
        return [r"player_connect", r"User .+ connected", r"Player .+ (joined|connected)"]
