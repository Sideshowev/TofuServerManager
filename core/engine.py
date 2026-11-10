"""Builds BaoBun's single engine: ServerManager + plugins + database + API.

Both the desktop app and the background agent call ``build_engine`` so they
are wired exactly the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from core import paths


@dataclass
class Engine:
    manager: Any
    plugins: Any = None
    db: Any = None
    api: Any = None
    health: Any = None
    presets: Any = None


def build_engine(role: str = "gui", base_dir: Optional[Path] = None) -> Engine:
    from server_manager import ServerManager

    base = Path(base_dir) if base_dir else paths.base_dir()
    manager = ServerManager(str(base / "servers.json"), role=role)
    engine = Engine(manager=manager)

    try:
        moved = manager.migrate_secrets()
        if moved:
            print("[INFO] Encrypted stored secrets: " + ", ".join(moved))
    except Exception as e:
        print(f"[WARN] secret migration: {e}")

    try:
        from core.database_layer import DatabaseLayer
        engine.db = DatabaseLayer(base / "settings" / "baobun.db")
        manager.db = engine.db
    except Exception as e:
        print(f"[WARN] database unavailable: {e}")

    try:
        from core.plugin_system import PluginManager
        dirs = [base / "plugins"]
        bundled = paths.bundle_dir() / "plugins"
        if bundled not in dirs:
            dirs.append(bundled)
        pm = PluginManager(dirs)
        pm.context["manager"] = manager
        pm.initialize_all()
        engine.plugins = pm
        manager.plugins = pm
    except Exception as e:
        print(f"[WARN] plugins unavailable: {e}")

    try:
        from core.health_check import HealthCheck
        engine.health = HealthCheck(manager)
    except Exception as e:
        print(f"[WARN] health checks unavailable: {e}")

    try:
        from core.preset_engine import PresetEngine
        engine.presets = PresetEngine(base / "presets")
    except Exception as e:
        print(f"[WARN] preset engine unavailable: {e}")

    from core.api_layer import ApiLayer
    engine.api = ApiLayer(manager, database_layer=engine.db, plugin_manager=engine.plugins,
                          preset_engine=engine.presets, health_check=engine.health)
    return engine
