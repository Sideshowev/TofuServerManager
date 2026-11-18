from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class PluginCapability(Enum):
    SERVER_LIFECYCLE = "server_lifecycle"
    CONFIG_EDITING = "config_editing"
    MOD_MANAGEMENT = "mod_management"
    BACKUP_RESTORE = "backup_restore"
    UPDATING = "updating"
    MONITORING = "monitoring"
    RCON = "rcon"
    WORKSHOP = "workshop"
    CLUSTER = "cluster"


@dataclass
class PluginMetadata:
    id: str
    name: str
    version: str
    description: str
    author: str = "Tofu"
    game_name: str = ""
    game_id: str = ""
    capabilities: List[PluginCapability] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    min_app_version: str = "1.0.0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "version": self.version,
            "description": self.description, "author": self.author,
            "game_name": self.game_name, "game_id": self.game_id,
            "capabilities": [c.value for c in self.capabilities],
            "dependencies": self.dependencies,
            "min_app_version": self.min_app_version,
        }


def infer_plugin_id(srv: Dict[str, Any]) -> str:
    """Map a servers.json entry (which uses "type") to a plugin id."""
    if srv.get("plugin_id"):
        return srv["plugin_id"]
    if srv.get("clusterId") or str(srv.get("appId") or "") == "2430930":
        return "ark"
    t = (srv.get("type") or "custom").lower()
    if t == "vintage-story":
        return "vintage_story"
    if t == "steamcmd" or srv.get("appId"):
        return "steamcmd"
    return "custom"


@dataclass
class ServerDefinition:
    id: str
    name: str
    plugin_id: str
    directory: str
    executable: Optional[str] = None
    launch_args: str = ""
    app_id: Optional[str] = None
    port: Optional[int] = None
    auto_restart: bool = True
    max_restarts: int = 5
    folder: str = ""
    requires_account: bool = False
    update_before_start: bool = False
    cluster_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "plugin_id": self.plugin_id,
            "directory": self.directory, "executable": self.executable,
            "launch_args": self.launch_args, "app_id": self.app_id,
            "port": self.port, "auto_restart": self.auto_restart,
            "max_restarts": self.max_restarts, "folder": self.folder,
            "requires_account": self.requires_account,
            "update_before_start": self.update_before_start,
            "cluster_id": self.cluster_id, "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServerDefinition":
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", "Unknown"),
            plugin_id=data.get("plugin_id") or infer_plugin_id(data),
            directory=data.get("directory", ""),
            executable=data.get("executable"),
            launch_args=data.get("launchArgs", "") or data.get("launch_args", ""),
            app_id=data.get("appId") or data.get("app_id"),
            port=data.get("port"),
            auto_restart=data.get("autoRestart", data.get("auto_restart", True)),
            max_restarts=data.get("maxRestarts", data.get("max_restarts", 5)),
            folder=data.get("folder", ""),
            requires_account=data.get("requiresAccount", data.get("requires_account", False)),
            update_before_start=data.get("updateBeforeStart", data.get("update_before_start", False)),
            cluster_id=data.get("clusterId") or data.get("cluster_id"),
            extra={k: v for k, v in data.items() if k not in (
                "id", "name", "plugin_id", "directory", "executable",
                "launchArgs", "launch_args", "appId", "app_id", "port",
                "autoRestart", "auto_restart", "maxRestarts", "max_restarts",
                "folder", "requiresAccount", "requires_account",
                "updateBeforeStart", "update_before_start",
                "clusterId", "cluster_id",
            )},
        )


@dataclass
class ServerStatus:
    id: str
    name: str
    plugin_id: str
    is_running: bool = False
    pid: Optional[int] = None
    restart_count: int = 0
    exit_code: Optional[int] = None
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    player_count: Optional[int] = None
    max_players: Optional[int] = None
    uptime_seconds: float = 0.0
    last_heartbeat: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "plugin_id": self.plugin_id,
            "isRunning": self.is_running, "pid": self.pid,
            "restartCount": self.restart_count, "exitCode": self.exit_code,
            "cpuPercent": self.cpu_percent, "memoryMB": self.memory_mb,
            "playerCount": self.player_count, "maxPlayers": self.max_players,
            "uptimeSeconds": self.uptime_seconds,
            "lastHeartbeat": self.last_heartbeat, "extra": self.extra,
        }


class PluginBase:
    def __init__(self, metadata: PluginMetadata, plugin_manager: "PluginManager"):
        self.metadata = metadata
        self.pm = plugin_manager
        self._enabled = False
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        with self._lock:
            self._enabled = True
            self.on_enable()

    def disable(self) -> None:
        with self._lock:
            self._enabled = False
            self.on_disable()

    def on_enable(self) -> None:
        pass

    def on_disable(self) -> None:
        pass

    def get_metadata(self) -> PluginMetadata:
        ...

    def health_check(self) -> Dict[str, Any]:
        return {"status": "healthy", "plugin": self.metadata.id}

    # ------------------------------------------------------------------
    # Game behaviour hooks (used by ServerManager for every server).
    # Plugins override what their game needs; defaults are generic.
    # ------------------------------------------------------------------
    @property
    def manager(self):
        """The one shared ServerManager (never create a private one)."""
        return self.pm.context.get("manager") if self.pm else None

    def handles(self, srv: Dict[str, Any]) -> bool:
        """Return True if this plugin is responsible for the server dict."""
        return False

    def stop_commands(self, srv: Dict[str, Any]) -> List[str]:
        """Console (stdin) commands that make the server save and exit."""
        return []

    def stop_timeout(self, srv: Dict[str, Any]) -> int:
        """Seconds to wait for a graceful exit before force-killing."""
        return 60

    def graceful_stop(self, srv: Dict[str, Any]) -> bool:
        """Ask the server to save and exit. Return True if a request was sent."""
        sent = False
        for cmd in self.stop_commands(srv):
            try:
                if self.manager and self.manager.send_console_command(srv["id"], cmd).get("status") == "sent":
                    sent = True
            except Exception:
                pass
        return sent

    def pre_backup(self, srv: Dict[str, Any]) -> None:
        """Flush world data to disk before a backup (e.g. an autosave command)."""
        return None

    def save_paths(self, srv: Dict[str, Any]) -> List[str]:
        """Folders (absolute, or relative to the server directory) holding world/save data."""
        return []

    def log_candidates(self, srv: Dict[str, Any]) -> List[str]:
        """Game log files to show in the Logs tab, most preferred first."""
        return []

    def player_join_patterns(self, srv: Dict[str, Any]) -> List[str]:
        """Regexes that mark a player joining (feeds smart boot activity)."""
        return []

    def broadcast(self, srv: Dict[str, Any], message: str) -> bool:
        """Show a message to everyone on the server. Return True if sent."""
        return False

    def query_players(self, srv: Dict[str, Any]):
        """Return (online, max) players - or {"count", "max", "names"} - or None if
        the game can't be asked."""
        return None

    def player_log_patterns(self, srv: Dict[str, Any]) -> Dict[str, List[str]]:
        """For games with no query protocol: {"join": [...], "leave": [...]} regexes
        with a (?P<name>...) group, matched against the console/game log."""
        return {}


class ServerPlugin(PluginBase):
    def install(self, server_def: ServerDefinition, directory: str) -> Dict[str, Any]:
        ...

    def update(self, server_def: ServerDefinition) -> Dict[str, Any]:
        ...

    def start(self, server_def: ServerDefinition) -> Dict[str, Any]:
        ...

    def stop(self, server_id: str) -> Dict[str, Any]:
        ...

    def restart(self, server_def: ServerDefinition) -> Dict[str, Any]:
        ...

    def get_status(self, server_id: str) -> ServerStatus:
        ...

    def generate_launch_args(self, server_def: ServerDefinition) -> List[str]:
        ...

    def validate_install(self, directory: str) -> bool:
        ...


class ConfigPlugin(PluginBase):
    def get_config_schema(self, server_def: ServerDefinition) -> List[ConfigSchema]:
        ...

    def read_config(self, server_def: ServerDefinition, schema: ConfigSchema) -> Dict[str, Any]:
        ...

    def write_config(self, server_def: ServerDefinition, schema: ConfigSchema,
                     values: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def validate_config(self, schema: ConfigSchema, values: Dict[str, Any]) -> List[str]:
        ...

    def list_config_files(self, server_def: ServerDefinition) -> List[str]:
        ...


class ProcessPlugin(PluginBase):
    def collect_metrics(self, server_id: str, pid: int) -> Dict[str, Any]:
        ...

    def get_player_count(self, server_id: str) -> Optional[int]:
        ...

    def get_max_players(self, server_id: str) -> Optional[int]:
        ...


class BackupPlugin(PluginBase):
    def backup(self, server_def: ServerDefinition, dest_dir: str) -> Dict[str, Any]:
        ...

    def restore(self, server_def: ServerDefinition, backup_path: str) -> Dict[str, Any]:
        ...

    def list_backups(self, server_id: str) -> List[Dict[str, Any]]:
        ...


class UpdatePlugin(PluginBase):
    def check_for_updates(self, server_def: ServerDefinition) -> Dict[str, Any]:
        ...

    def apply_update(self, server_def: ServerDefinition) -> Dict[str, Any]:
        ...


class MonitoringPlugin(PluginBase):
    def get_server_metrics(self, server_id: str) -> Dict[str, Any]:
        ...

    def predict_resource_exhaustion(self, server_id: str,
                                     history: List[Dict[str, Any]]) -> Dict[str, Any]:
        ...


class PluginError(Exception):
    pass

class PluginManager:
    def __init__(self, plugins_dir: "str | Path | List[str | Path]"):
        dirs = plugins_dir if isinstance(plugins_dir, (list, tuple)) else [plugins_dir]
        self.plugins_dirs: List[Path] = [Path(d) for d in dirs]
        self.plugins_dir = self.plugins_dirs[0] if self.plugins_dirs else Path("plugins")
        self.context: Dict[str, Any] = {}
        self._plugin_paths: Dict[str, Path] = {}
        self._plugins: Dict[str, PluginBase] = {}
        self._plugin_classes: Dict[str, type] = {}
        self._lock = threading.RLock()
        self._discovery_callbacks: List[Callable] = []

    def discover(self) -> List[PluginMetadata]:
        found: List[PluginMetadata] = []
        seen_ids = set()
        candidates = []
        for root in self.plugins_dirs:
            if root.is_dir():
                candidates.extend(sorted(p for p in root.iterdir() if p.is_dir()))
        for plugin_dir in candidates:
            manifest = plugin_dir / "plugin.json"
            if not manifest.exists():
                continue
            try:
                raw = json.loads(manifest.read_text(encoding="utf-8"))
                if raw.get("id") in seen_ids:
                    continue  # a user copy next to the exe overrides the bundled one
                seen_ids.add(raw.get("id"))
                self._plugin_paths[raw["id"]] = plugin_dir
                caps = [PluginCapability(c) for c in raw.get("capabilities", [])]
                meta = PluginMetadata(
                    id=raw["id"], name=raw.get("name", raw["id"]),
                    version=raw.get("version", "1.0.0"),
                    description=raw.get("description", ""),
                    author=raw.get("author", "Tofu"),
                    game_name=raw.get("game_name", ""),
                    game_id=raw.get("game_id", ""),
                    capabilities=caps,
                    dependencies=raw.get("dependencies", []),
                    min_app_version=raw.get("min_app_version") or raw.get("min_baobun_version", "1.0.0"),
                )
                found.append(meta)
            except Exception as e:
                print(f"[WARN] Plugin manifest error in {plugin_dir}: {e}")
        return found

    def load(self, metadata: PluginMetadata) -> Optional[PluginBase]:
        with self._lock:
            if metadata.id in self._plugins:
                return self._plugins[metadata.id]
        plugin_dir = self._plugin_paths.get(metadata.id, self.plugins_dir / metadata.id)
        main_py = plugin_dir / "main.py"
        if not main_py.exists():
            print(f"[WARN] Plugin {metadata.id} has no main.py")
            return None
        try:
            spec = importlib.util.spec_from_file_location(
                f"tofu_plugin_{metadata.id}", main_py)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            if str(plugin_dir.parent) not in sys.path:
                sys.path.insert(0, str(plugin_dir.parent))
            spec.loader.exec_module(module)
            plugin_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and
                        issubclass(attr, PluginBase) and
                        attr.__module__ == module.__name__):
                    plugin_class = attr
                    break
            if plugin_class is None:
                print(f"[WARN] Plugin {metadata.id} has no PluginBase subclass")
                return None
            instance = plugin_class(metadata, self)
            with self._lock:
                self._plugins[metadata.id] = instance
                self._plugin_classes[metadata.id] = plugin_class
            return instance
        except Exception as e:
            print(f"[ERROR] Failed to load plugin {metadata.id}: {e}")
            return None

    def enable(self, plugin_id: str) -> bool:
        with self._lock:
            plugin = self._plugins.get(plugin_id)
            if plugin is None:
                return False
            if plugin.enabled:
                return True
            plugin.enable()
            return True

    def disable(self, plugin_id: str) -> bool:
        with self._lock:
            plugin = self._plugins.get(plugin_id)
            if plugin is None:
                return False
            if not plugin.enabled:
                return True
            plugin.disable()
            return True

    def get_plugin(self, plugin_id: str) -> Optional[PluginBase]:
        with self._lock:
            return self._plugins.get(plugin_id)

    def get_plugins_by_capability(self, capability: PluginCapability) -> List[PluginBase]:
        result = []
        with self._lock:
            for plugin in self._plugins.values():
                if plugin.enabled and capability in plugin.metadata.capabilities:
                    result.append(plugin)
        return result

    def get_server_plugin(self, plugin_id: str) -> Optional[ServerPlugin]:
        plugin = self.get_plugin(plugin_id)
        if plugin and isinstance(plugin, ServerPlugin):
            return plugin
        return None

    def get_config_plugin(self, plugin_id: str) -> Optional[ConfigPlugin]:
        plugin = self.get_plugin(plugin_id)
        if plugin and isinstance(plugin, ConfigPlugin):
            return plugin
        return None

    def get_process_plugin(self, plugin_id: str) -> Optional[ProcessPlugin]:
        plugin = self.get_plugin(plugin_id)
        if plugin and isinstance(plugin, ProcessPlugin):
            return plugin
        return None

    def get_backup_plugin(self, plugin_id: str) -> Optional[BackupPlugin]:
        plugin = self.get_plugin(plugin_id)
        if plugin and isinstance(plugin, BackupPlugin):
            return plugin
        return None

    def get_update_plugin(self, plugin_id: str) -> Optional[UpdatePlugin]:
        plugin = self.get_plugin(plugin_id)
        if plugin and isinstance(plugin, UpdatePlugin):
            return plugin
        return None

    def get_monitoring_plugin(self, plugin_id: str) -> Optional[MonitoringPlugin]:
        plugin = self.get_plugin(plugin_id)
        if plugin and isinstance(plugin, MonitoringPlugin):
            return plugin
        return None

    def initialize_all(self) -> None:
        # Load dependencies first (ark depends on steamcmd, etc.).
        metas = self.discover()
        order = sorted(metas, key=lambda m: len(m.dependencies))
        for meta in order:
            plugin = self.load(meta)
            if plugin:
                self.enable(plugin.metadata.id)
                print(f"[INFO] Plugin enabled: {plugin.metadata.name} v{plugin.metadata.version}")

    def for_server(self, srv: Dict[str, Any]) -> Optional[PluginBase]:
        """Pick the plugin responsible for a server dict.

        An explicit ``plugin_id`` wins; otherwise the most specific plugin whose
        ``handles()`` says yes (plugins with more dependencies are more specific,
        e.g. ark before steamcmd).
        """
        with self._lock:
            plugins = [p for p in self._plugins.values() if p.enabled]
        pid = srv.get("plugin_id")
        if pid:
            for p in plugins:
                if p.metadata.id == pid:
                    return p
        for p in sorted(plugins, key=lambda p: -len(p.metadata.dependencies)):
            try:
                if p.handles(srv):
                    return p
            except Exception:
                continue
        return None

    def shutdown_all(self) -> None:
        with self._lock:
            for plugin_id in list(self._plugins.keys()):
                self.disable(plugin_id)

    def on_discovery(self, callback: Callable) -> None:
        self._discovery_callbacks.append(callback)

    def notify_discovery(self) -> None:
        for cb in self._discovery_callbacks:
            try:
                cb(self.discover())
            except Exception as e:
                print(f"[WARN] Discovery callback error: {e}")

    def health_check_all(self) -> Dict[str, Dict[str, Any]]:
        """Run health checks on all enabled plugins."""
        results = {}
        for plugin_id, plugin in self._plugins.items():
            if not plugin.enabled:
                continue
            try:
                results[plugin_id] = plugin.health_check()
            except Exception as e:
                results[plugin_id] = {"status": "error", "error": str(e)}
        return results

    def get_server_health(self, server_id: str) -> Dict[str, Any]:
        """Live status for one server, via the shared ServerManager."""
        mgr = self.context.get("manager")
        if mgr is None:
            return {"status": "unknown", "reason": "Server manager not attached"}
        st = next((s for s in mgr.get_all_statuses() if s["id"] == server_id), None)
        if not st:
            return {"status": "unknown", "reason": "Server not found"}
        return {
            "status": "healthy" if st.get("isRunning") else "stopped",
            "server_id": server_id,
            "server_name": st.get("name", ""),
            "is_running": bool(st.get("isRunning")),
            "pid": st.get("pid"),
            "exit_code": st.get("exitCode"),
            "cpu_percent": st.get("cpuPercent"),
            "memory_mb": st.get("memUsageMB"),
            "player_count": st.get("playerCount"),
            "max_players": st.get("maxPlayers"),
            "uptime_seconds": st.get("uptimeSeconds"),
        }
