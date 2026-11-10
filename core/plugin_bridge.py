"""Shared base for game plugins.

Every game plugin used to carry ~100 identical lines that each created a
*private* ServerManager (so each plugin had its own process table). This base
class implements the plugin interfaces once, delegating to the single shared
ServerManager that the engine attaches as ``plugin_manager.context["manager"]``.

A game plugin subclasses ``ManagedServerPlugin`` and only overrides the game
behaviour hooks it needs (handles, stop_commands, save_paths, ...).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from core.plugin_system import (
    ServerPlugin, ConfigPlugin, ProcessPlugin, BackupPlugin, UpdatePlugin,
    MonitoringPlugin, PluginMetadata, ServerDefinition, ServerStatus,
)


def _detect_format(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
        ".ini": "ini", ".cfg": "ini", ".properties": "ini", ".xml": "xml",
    }.get(ext, "text")


class ManagedServerPlugin(ServerPlugin, ConfigPlugin, ProcessPlugin,
                          BackupPlugin, UpdatePlugin, MonitoringPlugin):

    def get_metadata(self) -> PluginMetadata:
        return self.metadata

    # -- helpers ---------------------------------------------------------
    def _mgr(self):
        mgr = self.manager
        if mgr is None:
            raise RuntimeError("Plugin is not attached to a ServerManager")
        return mgr

    def _status(self, server_id: str) -> Dict[str, Any]:
        return next((s for s in self._mgr().get_all_statuses() if s["id"] == server_id), {}) or {}

    # -- ServerPlugin ------------------------------------------------------
    def install(self, server_def: ServerDefinition, directory: str) -> Dict[str, Any]:
        if server_def.app_id:
            return self._mgr().install_steamcmd_game(server_def.id, str(server_def.app_id))
        Path(directory).mkdir(parents=True, exist_ok=True)
        return {"status": "manual_install", "directory": directory}

    def update(self, server_def: ServerDefinition) -> Dict[str, Any]:
        if not server_def.app_id:
            return {"status": "not_supported"}
        return self._mgr().update_steamcmd_game(server_def.id, str(server_def.app_id))

    def start(self, server_def: ServerDefinition) -> Dict[str, Any]:
        return self._mgr().start_server(server_def.id)

    def stop(self, server_id: str) -> Dict[str, Any]:
        return self._mgr().stop_server(server_id)

    def restart(self, server_def: ServerDefinition) -> Dict[str, Any]:
        return self._mgr().restart_server(server_def.id)

    def get_status(self, server_id: str) -> ServerStatus:
        st = self._status(server_id)
        if not st:
            return ServerStatus(id=server_id, name="Unknown", plugin_id=self.metadata.id)
        return ServerStatus(
            id=st["id"], name=st.get("name", ""), plugin_id=self.metadata.id,
            is_running=bool(st.get("isRunning")), pid=st.get("pid"),
            restart_count=st.get("restartCount", 0), exit_code=st.get("exitCode"),
            cpu_percent=st.get("cpuPercent") or 0.0, memory_mb=st.get("memUsageMB") or 0.0,
            uptime_seconds=st.get("uptimeSeconds") or 0.0,
        )

    def generate_launch_args(self, server_def: ServerDefinition) -> List[str]:
        from server_manager import split_args
        return split_args(server_def.launch_args)

    def validate_install(self, directory: str) -> bool:
        return bool(directory) and Path(directory).is_dir()

    # -- ConfigPlugin (raw editing goes through ServerManager's editor) ----
    def list_config_files(self, server_def: ServerDefinition) -> List[str]:
        return [c["relative"] for c in self._mgr().list_configs(server_def.id)]

    def get_config_schema(self, server_def: ServerDefinition):
        from core.config_engine import ConfigSchema, ConfigSection
        return [ConfigSchema(file_path=f, file_format=_detect_format(f), label=f,
                             sections=[ConfigSection(name="root", label="Settings", parameters=[])])
                for f in self.list_config_files(server_def)]

    def read_config(self, server_def: ServerDefinition, schema) -> Dict[str, Any]:
        return self._mgr().load_config(server_def.id, schema.file_path)

    def write_config(self, server_def: ServerDefinition, schema, values: Dict[str, Any]) -> Dict[str, Any]:
        self._mgr().save_config(server_def.id, schema.file_path, values)
        return {"status": "saved", "file": schema.file_path}

    def validate_config(self, schema, values: Dict[str, Any]) -> List[str]:
        return []

    # -- ProcessPlugin / MonitoringPlugin ---------------------------------
    def collect_metrics(self, server_id: str, pid: int) -> Dict[str, Any]:
        st = self._status(server_id)
        return {"cpu_percent": st.get("cpuPercent") or 0.0, "memory_mb": st.get("memUsageMB") or 0.0}

    def get_player_count(self, server_id: str):
        return self._status(server_id).get("playerCount")

    def get_max_players(self, server_id: str):
        return self._status(server_id).get("maxPlayers")

    def get_server_metrics(self, server_id: str) -> Dict[str, Any]:
        return self.collect_metrics(server_id, 0)

    def predict_resource_exhaustion(self, server_id: str, history):
        from core.process_manager import predict_resource_exhaustion
        return predict_resource_exhaustion(list(reversed(history or [])))

    # -- BackupPlugin --------------------------------------------------------
    def backup(self, server_def: ServerDefinition, dest_dir: str) -> Dict[str, Any]:
        return self._mgr().backup_server_world(server_def.id, dest_dir or None)

    def restore(self, server_def: ServerDefinition, backup_path: str) -> Dict[str, Any]:
        return self._mgr().restore_server_world(server_def.id, backup_path)

    def list_backups(self, server_id: str) -> List[Dict[str, Any]]:
        return self._mgr().list_server_backups(server_id)

    # -- UpdatePlugin ----------------------------------------------------------
    def check_for_updates(self, server_def: ServerDefinition) -> Dict[str, Any]:
        return {"updates_available": None, "method": "steamcmd_validate"}

    def apply_update(self, server_def: ServerDefinition) -> Dict[str, Any]:
        return self.update(server_def)
