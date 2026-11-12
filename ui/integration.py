"""UI-facing facade over the shared engine.

Earlier this class built its own private copies of every core service (its own
plugin manager, process manager, database connection ...). It now wraps the
one engine from ``core.engine`` so UI code, the agent and the Discord bot all
operate on the same state.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.api_layer import ApiResponse
from core.engine import Engine, build_engine


class UIIntegration:
    def __init__(self, engine: Optional[Engine] = None):
        self.engine = engine or build_engine(role="gui")
        self.manager = self.engine.manager
        self.api_layer = self.engine.api
        self.plugin_manager = self.engine.plugins
        self.database = self.engine.db

    def get(self) -> bool:
        return True

    def call(self, action: str, **params) -> ApiResponse:
        return self.api_layer.process(action, params, user_id=params.get("user", "gui"))

    def start_server(self, server_id: str) -> Dict[str, Any]:
        return self.manager.start_server(server_id, user="gui")

    def stop_server(self, server_id: str) -> Dict[str, Any]:
        return self.manager.stop_server(server_id, user="gui")

    def restart_server(self, server_id: str) -> Dict[str, Any]:
        return self.manager.restart_server(server_id, user="gui")

    def health(self, server_id: str) -> Dict[str, Any]:
        if self.engine.health is None:
            return {"status": "unknown", "checks": []}
        return self.engine.health.check_server(server_id)

    def audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.database.get_audit_log(limit=limit) if self.database else []
