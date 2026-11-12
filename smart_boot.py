"""
BaoBun Control Panel — Smart Boot Logic Module

Intelligent server startup decisions based on:
- Recent activity history (from ActivityTracker)  
- Configurable inactivity thresholds
- Graceful degradation if Discord/activity tracking unavailable

Boot Decision Rules:
1. If server was active within last 2 days → Auto-start ✓
2. If server inactive for >= 2 consecutive days → Skip auto-start ⏭️
3. Admin can override via Discord buttons or manual start in GUI

This module is called during app initialization to determine which servers 
should be automatically started when the BaoBun Control Panel launches.
"""

import json
from pathlib import Path


class SmartBootManager:
    """Manages intelligent server startup decisions based on activity history."""

    def __init__(self, config_path: str, activity_tracker=None):
        self.config_path = Path(config_path)
        self.activity_tracker = activity_tracker  # ActivityTracker instance

        # Load boot configuration with sensible defaults  
        self.boot_config = {
            "enabled": True,                        # Master toggle for smart boot logic
            "inactive_days_threshold": 2,           # Days inactive before skipping auto-start  
            "force_start_list": [],                 # Server IDs that should ALWAYS start regardless of activity
            "never_auto_start": []                  # Server IDs that should NEVER auto-start (manual only)
        }

        self._load_config()


    def _load_config(self):
        """Load smart boot configuration from disk."""
        config_file = Path(str(self.config_path).replace(".json", "_boot.json"))

        if config_file.exists():
            try:
                raw = json.loads(config_file.read_text(encoding="utf-8"))
                # Merge user settings with defaults (preserving any new keys we add later)  
                for key in self.boot_config.keys():
                    if key in raw:
                        self.boot_config[key] = raw[key]
            except Exception as e:
                print(f"[WARN] Failed to load smart boot config: {e}")


    def decide(self, srv: dict, was_running: bool = False) -> tuple:
        """Boot decision for one server dict: (start: bool, reason: str).

        Order: never-list / manualStart  ->  force-list  ->  smart boot.
        Smart boot starts a server that had players in the last N days. A server
        with no activity history yet starts only if it was running when the
        machine/agent last shut down, so brand-new or rarely used servers
        don't all spin up after every reboot.
        """
        sid = srv.get("id", "")
        manual = srv.get("manualStart")
        if manual is None and srv.get("clusterId"):
            manual = True  # ARK cluster maps are on-demand by design
        if self._is_in_list(sid, "never_auto_start") or manual:
            return False, "Manual-start server"
        if self._is_in_list(sid, "force_start_list"):
            return True, "Force-start list"
        if not self.boot_config.get("enabled", True):
            return bool(was_running), ("Was running before shutdown" if was_running
                                       else "Smart boot off; was not running")
        threshold = int(self.boot_config.get("inactive_days_threshold", 2) or 2)
        tracker = self.activity_tracker
        if tracker is None or not getattr(tracker, "has_history", lambda _s: False)(sid):
            return bool(was_running), ("No activity history yet; was running before shutdown"
                                       if was_running else "No activity history yet")
        if tracker.check_consecutive_inactive_days(sid, threshold):
            return False, f"No players for {threshold}+ days"
        return True, "Players in the last %d days" % threshold

    def _is_in_list(self, server_id: str, list_name: str) -> bool:
        """Check if a server ID is in a configured allow/block list."""
        return server_id in self.boot_config.get(list_name, [])


    def save_config(self):
        """Persist current boot configuration settings."""
        config_file = Path(str(self.config_path).replace(".json", "_boot.json"))

        try:
            from core.paths import atomic_write_json
            atomic_write_json(config_file, self.boot_config)
        except Exception as e:
            print(f"[WARN] Could not save smart boot config: {e}")


    # ── Public API for GUI Integration ───────────────

    def set_enabled(self, enabled: bool):
        """Enable/disable smart boot logic globally."""
        self.boot_config["enabled"] = enabled
        self.save_config()

