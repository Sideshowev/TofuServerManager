from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PresetParameter:
    """A single parameter within a preset."""
    name: str
    value: Any
    description: str = ""
    category: str = "General"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "description": self.description,
            "category": self.category,
        }


@dataclass
class Preset:
    """A server preset - a collection of pre-configured settings."""
    id: str
    name: str
    game_id: str
    category: str
    description: str
    parameters: List[PresetParameter] = field(default_factory=list)
    config_overrides: Dict[str, Any] = field(default_factory=dict)
    launch_args: List[str] = field(default_factory=list)
    port: Optional[int] = None
    max_players: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    is_builtin: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "game_id": self.game_id,
            "category": self.category,
            "description": self.description,
            "parameters": [p.to_dict() for p in self.parameters],
            "config_overrides": self.config_overrides,
            "launch_args": self.launch_args,
            "port": self.port,
            "max_players": self.max_players,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_builtin": self.is_builtin,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Preset":
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", "Unnamed"),
            game_id=data.get("game_id", ""),
            category=data.get("category", "custom"),
            description=data.get("description", ""),
            parameters=[PresetParameter(**p) for p in data.get("parameters", [])],
            config_overrides=data.get("config_overrides", {}),
            launch_args=data.get("launch_args", []),
            port=data.get("port"),
            max_players=data.get("max_players"),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            is_builtin=data.get("is_builtin", False),
        )


class PresetEngine:
    """Manages server presets - built-in and user-defined."""

    BUILTIN_CATEGORIES = [
        "vanilla", "small_community", "medium_community", "large_community",
        "performance_optimized", "pvp", "pve", "roleplay", "modded",
        "high_population", "low_resource", "custom",
    ]

    CATEGORY_LABELS = {
        "vanilla": "Vanilla",
        "small_community": "Small Community",
        "medium_community": "Medium Community",
        "large_community": "Large Community",
        "performance_optimized": "Performance Optimized",
        "pvp": "PvP",
        "pve": "PvE",
        "roleplay": "Roleplay",
        "modded": "Modded",
        "high_population": "High Population",
        "low_resource": "Low Resource",
        "custom": "Custom",
    }

    def __init__(self, presets_dir: str | Path):
        self.presets_dir = Path(presets_dir)
        self.presets_dir.mkdir(parents=True, exist_ok=True)
        self._presets: Dict[str, Preset] = {}
        self._lock = __import__("threading").RLock()
        self._load_builtin_presets()
        self._load_user_presets()

    # ------------------------------------------------------------------
    # User Presets
    # ------------------------------------------------------------------

    def _load_user_presets(self) -> None:
        presets_file = self.presets_dir / "user_presets.json"
        if not presets_file.exists():
            return
        try:
            data = json.loads(presets_file.read_text(encoding="utf-8"))
            for p in data.get("presets", []):
                preset = Preset.from_dict(p)
                preset.is_builtin = False
                self._presets[preset.id] = preset
        except Exception:
            pass

    def _save_user_presets(self) -> None:
        presets_file = self.presets_dir / "user_presets.json"
        user_presets = [p for p in self._presets.values() if not p.is_builtin]
        data = {"presets": [p.to_dict() for p in user_presets]}
        presets_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # CRUD Operations
    # ------------------------------------------------------------------

    def list_presets(self, game_id: Optional[str] = None,
                     category: Optional[str] = None) -> List[Preset]:
        with self._lock:
            result = list(self._presets.values())
        if game_id:
            result = [p for p in result if p.game_id == game_id]
        if category:
            result = [p for p in result if p.category == category]
        return sorted(result, key=lambda p: (p.category, p.name))

    def get_preset(self, preset_id: str) -> Optional[Preset]:
        with self._lock:
            return self._presets.get(preset_id)

    def create_preset(self, preset: Preset) -> Preset:
        preset.id = str(uuid.uuid4())
        preset.created_at = time.time()
        preset.updated_at = time.time()
        preset.is_builtin = False
        with self._lock:
            self._presets[preset.id] = preset
        self._save_user_presets()
        return preset

    def update_preset(self, preset_id: str, updates: Dict[str, Any]) -> Optional[Preset]:
        with self._lock:
            preset = self._presets.get(preset_id)
            if not preset or preset.is_builtin:
                return None
            for key, value in updates.items():
                if hasattr(preset, key) and key not in ("id", "created_at", "is_builtin"):
                    setattr(preset, key, value)
            preset.updated_at = time.time()
        self._save_user_presets()
        return preset

    def delete_preset(self, preset_id: str) -> bool:
        with self._lock:
            preset = self._presets.get(preset_id)
            if not preset or preset.is_builtin:
                return False
            del self._presets[preset_id]
        self._save_user_presets()
        return True

    def apply_preset_to_server(self, preset_id: str,
                               server_def: Dict[str, Any]) -> Dict[str, Any]:
        preset = self.get_preset(preset_id)
        if not preset:
            raise ValueError(f"Preset {preset_id} not found")
        result = dict(server_def)
        result["name"] = preset.name
        if preset.port:
            result["port"] = preset.port
        if preset.max_players:
            result["max_players"] = preset.max_players
        if preset.launch_args:
            result["launch_args"] = " ".join(preset.launch_args)
        extra = result.get("extra", {})
        extra["preset_id"] = preset.id
        extra["preset_category"] = preset.category
        if preset.config_overrides:
            extra["config_overrides"] = preset.config_overrides
        result["extra"] = extra
        for param in preset.parameters:
            extra[param.name] = param.value
        result["extra"] = extra
        return result

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    def get_categories(self) -> List[Dict[str, str]]:
        return [
            {"id": cat, "label": self.CATEGORY_LABELS.get(cat, cat.title())}
            for cat in self.BUILTIN_CATEGORIES
        ]

    def get_builtin_categories(self) -> List[str]:
        return list(self.BUILTIN_CATEGORIES)

    def _load_builtin_presets(self) -> None:
        """Load built-in presets for supported games."""
        for preset in self._builtin_preset_factory():
            self._add_builtin(preset)

    def _builtin_preset_factory(self) -> list:
        """Build the collection of built-in presets (kept separate for clarity)."""
        presets: list = []

        # Vintage Story
        presets.append(Preset(
            id="vs_vanilla", name="Vanilla", game_id="vintage_story",
            category="vanilla", description="Default Vintage Story server settings with no modifications.",
            parameters=[
                PresetParameter("port", 42420, "Server port", "Network"),
                PresetParameter("max_players", 10, "Maximum players", "Gameplay"),
                PresetParameter("difficulty", 1.0, "World difficulty", "Gameplay"),
            ],
            port=42420, max_players=10, is_builtin=True,
        ))
        presets.append(Preset(
            id="vs_small_community", name="Small Community", game_id="vintage_story",
            category="small_community", description="Optimized for 5-15 players with moderate resource usage.",
            parameters=[
                PresetParameter("port", 42420, "Server port", "Network"),
                PresetParameter("max_players", 15, "Maximum players", "Gameplay"),
                PresetParameter("difficulty", 1.0, "World difficulty", "Gameplay"),
                PresetParameter("view_distance", 1000, "View distance in blocks", "Performance"),
            ],
            port=42420, max_players=15, is_builtin=True,
        ))
        presets.append(Preset(
            id="vs_medium_community", name="Medium Community", game_id="vintage_story",
            category="medium_community", description="Optimized for 15-30 players.",
            parameters=[
                PresetParameter("port", 42420, "Server port", "Network"),
                PresetParameter("max_players", 30, "Maximum players", "Gameplay"),
                PresetParameter("difficulty", 1.0, "World difficulty", "Gameplay"),
                PresetParameter("view_distance", 1500, "View distance in blocks", "Performance"),
            ],
            port=42420, max_players=30, is_builtin=True,
        ))
        presets.append(Preset(
            id="vs_high_rate", name="High Rate (2x)", game_id="vintage_story",
            category="high_population", description="2x rates for faster progression, small world.",
            parameters=[
                PresetParameter("port", 42420, "Server port", "Network"),
                PresetParameter("max_players", 20, "Maximum players", "Gameplay"),
                PresetParameter("difficulty", 2.0, "World difficulty", "Gameplay"),
                PresetParameter("xprate", 2.0, "Experience rate", "Rates"),
                PresetParameter("maturerate", 2.0, "Mature speed rate", "Rates"),
            ],
            port=42420, max_players=20, is_builtin=True,
        ))

        # ARK
        presets.append(Preset(
            id="ark_vanilla", name="Vanilla", game_id="ark",
            category="vanilla", description="Default ARK: Survival Ascended settings.",
            parameters=[
                PresetParameter("port", 7777, "Server port", "Network"),
                PresetParameter("max_players", 70, "Maximum players", "Gameplay"),
                PresetParameter("ServerPVE", False, "PvE mode enabled", "Gameplay"),
                PresetParameter("XPMultiplier", 1.0, "Experience multiplier", "Rates"),
            ],
            port=7777, max_players=70, is_builtin=True,
        ))
        presets.append(Preset(
            id="ark_pve", name="PvE", game_id="ark",
            category="pve", description="PvE-focused settings.",
            parameters=[
                PresetParameter("port", 7777, "Server port", "Network"),
                PresetParameter("max_players", 50, "Maximum players", "Gameplay"),
                PresetParameter("ServerPVE", True, "PvE mode enabled", "Gameplay"),
                PresetParameter("XPMultiplier", 1.5, "Experience multiplier", "Rates"),
                PresetParameter("TamingSpeedMultiplier", 1.5, "Taming speed", "Rates"),
            ],
            port=7777, max_players=50, is_builtin=True,
        ))
        presets.append(Preset(
            id="ark_high_rate", name="High Rate (10x)", game_id="ark",
            category="high_population", description="10x rates for fast progression.",
            parameters=[
                PresetParameter("port", 7777, "Server port", "Network"),
                PresetParameter("max_players", 100, "Maximum players", "Gameplay"),
                PresetParameter("XPMultiplier", 10.0, "Experience multiplier", "Rates"),
                PresetParameter("TamingSpeedMultiplier", 10.0, "Taming speed", "Rates"),
                PresetParameter("HarvestAmountMultiplier", 10.0, "Harvest amount", "Rates"),
                PresetParameter("BabyMatureSpeedMultiplier", 10.0, "Baby mature speed", "Rates"),
                PresetParameter("EggHatchSpeedMultiplier", 10.0, "Egg hatch speed", "Rates"),
            ],
            port=7777, max_players=100, is_builtin=True,
        ))

        # Minecraft Java
        presets.append(Preset(
            id="mc_vanilla", name="Vanilla", game_id="minecraft_java",
            category="vanilla", description="Default Minecraft Java server settings.",
            parameters=[
                PresetParameter("port", 25565, "Server port", "Network"),
                PresetParameter("max_players", 20, "Maximum players", "Gameplay"),
            ],
            port=25565, max_players=20, is_builtin=True,
        ))
        presets.append(Preset(
            id="mc_high_population", name="High Population", game_id="minecraft_java",
            category="high_population", description="Optimized for larger groups.",
            parameters=[
                PresetParameter("port", 25565, "Server port", "Network"),
                PresetParameter("max_players", 60, "Maximum players", "Gameplay"),
                PresetParameter("view_distance", 10, "View distance", "Performance"),
                PresetParameter("spawn_monsters", True, "Spawn monsters", "Gameplay"),
            ],
            port=25565, max_players=60, is_builtin=True,
        ))

        # Minecraft Bedrock
        presets.append(Preset(
            id="mcb_vanilla", name="Vanilla", game_id="minecraft_bedrock",
            category="vanilla", description="Default Minecraft Bedrock server settings.",
            parameters=[
                PresetParameter("port", 27015, "Server port", "Network"),
                PresetParameter("max_players", 50, "Maximum players", "Gameplay"),
            ],
            port=27015, max_players=50, is_builtin=True,
        ))

        # Rust
        presets.append(Preset(
            id="rust_vanilla", name="Vanilla", game_id="rust",
            category="vanilla", description="Default Rust server settings.",
            parameters=[
                PresetParameter("port", 27015, "Server port", "Network"),
                PresetParameter("max_players", 100, "Maximum players", "Gameplay"),
                PresetParameter("tick_rate", 30.0, "Tick rate", "Performance"),
            ],
            port=27015, max_players=100, is_builtin=True,
        ))

        # Terraria
        presets.append(Preset(
            id="terraria_vanilla", name="Vanilla", game_id="terraria",
            category="vanilla", description="Default Terraria server settings.",
            parameters=[
                PresetParameter("port", 7123, "Server port", "Network"),
                PresetParameter("max_players", 8, "Maximum players", "Gameplay"),
            ],
            port=7123, max_players=8, is_builtin=True,
        ))

        # Factorio
        presets.append(Preset(
            id="factorio_vanilla", name="Vanilla", game_id="factorio",
            category="vanilla", description="Default Factorio server settings.",
            parameters=[
                PresetParameter("port", 27015, "Server port", "Network"),
                PresetParameter("max_players", 1, "Maximum players", "Gameplay"),
            ],
            port=27015, max_players=1, is_builtin=True,
        ))

        # 7 Days To Die
        presets.append(Preset(
            id="rpg7_vanilla", name="Vanilla", game_id="rpg7",
            category="vanilla", description="Default 7 Days To Die server settings.",
            parameters=[
                PresetParameter("port", 27016, "Server port", "Network"),
                PresetParameter("max_players", 4, "Maximum players", "Gameplay"),
            ],
            port=27016, max_players=4, is_builtin=True,
        ))

        # DayZ
        presets.append(Preset(
            id="dayz_vanilla", name="Vanilla", game_id="dayz",
            category="vanilla", description="Default DayZ server settings.",
            parameters=[
                PresetParameter("port", 27017, "Server port", "Network"),
                PresetParameter("max_players", 64, "Maximum players", "Gameplay"),
            ],
            port=27017, max_players=64, is_builtin=True,
        ))

        return presets

    def _add_builtin(self, preset: Preset) -> None:
        """Register a built-in preset."""
        self._presets[preset.id] = preset
