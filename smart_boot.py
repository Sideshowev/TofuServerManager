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


    def should_auto_start_server(self, server_id: str) -> bool:
        """Determine if a server should be automatically started based on activity history."""
        
        # Check force-start list first (always start these servers regardless of history)  
        if self._is_in_list(server_id, "force_start_list"):
            return True

        # Never auto-start servers explicitly marked as manual-only  
        if self._is_in_list(server_id, "never_auto_start"):
            return False

        # If smart boot is disabled globally or activity tracking unavailable → default to starting
        if not self.boot_config.get("enabled", True):
            return True
            
        if not self.activity_tracker:
            print(f"[INFO] Activity tracker not available. Defaulting to auto-start for {server_id}")  
            return True

        # Check recent activity history via tracker module  
        try:
            threshold = self.boot_config.get("inactive_days_threshold", 2)
            is_inactive_too_long = self.activity_tracker.check_consecutive_inactive_days(
                server_id=server_id, 
                threshold=threshold
            )

            if is_inactive_too_long:
                print(f"[INFO] Server {server_id} inactive for >= {threshold} days. Skipping auto-start.")
                return False  
                
        except Exception as e:
            print(f"[WARN] Error checking activity history for {server_id}: {e}")

        # Default: if we got here without skipping, allow auto-start  
        return True


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


    def get_boot_summary(self) -> dict:
        """Get summary of smart boot decisions for logging/debugging."""
        servers = []

        # Load current server definitions to check against  
        if Path(self.config_path).exists():
            try:
                raw_servers = json.loads(Path(self.config_path).read_text(encoding="utf-8"))
                
                for srv in raw_servers:
                    sid = srv.get("id", "unknown")
                    name = srv.get("name", "Unnamed Server")

                    should_start = self.should_auto_start_server(sid)
                    
                    servers.append({
                        "id": sid,  
                        "name": name,
                        "will_auto_start": should_start,
                        "reason": self._get_decision_reason(sid)
                    })

            except Exception as e:
                print(f"[WARN] Could not generate boot summary: {e}")

        return {
            "smart_boot_enabled": self.boot_config.get("enabled", True),
            "inactive_threshold_days": self.boot_config.get("inactive_days_threshold", 2),  
            "servers": servers
        }


    def _get_decision_reason(self, server_id: str) -> str:
        """Get human-readable reason for a boot decision."""
        if self._is_in_list(server_id, "force_start_list"):
            return "Force-start list (always start)"
            
        if self._is_in_list(server_id, "never_auto_start"):  
            return "Manual-only server"

        if not self.boot_config.get("enabled", True):
            return "Smart boot disabled globally"

        if not self.activity_tracker:
            return "Activity tracking unavailable → defaulting to start"

        try:
            threshold = self.boot_config.get("inactive_days_threshold", 2)  
            is_inactive = self.activity_tracker.check_consecutive_inactive_days(server_id, threshold)

            if is_inactive:
                return f"Inactive >= {threshold} days (skip auto-start)"
                
            return "Recent activity detected → will auto-start"  

        except Exception:
            return "Unknown status → defaulting to start"


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


    def add_to_force_start(self, server_id: str):
        """Add a server to the force-start list (always auto-start regardless of history)."""  
        if server_id not in self.boot_config["force_start_list"]:
            self.boot_config["force_start_list"].append(server_id)
            self.save_config()


    def remove_from_force_start(self, server_id: str):
        """Remove a server from the force-start list."""
        try:  
            self.boot_config["force_start_list"].remove(server_id)
            self.save_config()
        except ValueError:
            pass  # Not in list, nothing to do


    def add_to_never_start(self, server_id: str):
        """Mark a server as manual-start only (never auto-start)."""
        if server_id not in self.boot_config["never_auto_start"]:
            self.boot_config["never_auto_start"].append(server_id)  
            self.save_config()


    def remove_from_never_start(self, server_id: str):
        """Remove manual-only restriction from a server."""
        try:
            self.boot_config["never_auto_start"].remove(server_id)  
            self.save_config()
        except ValueError:
            pass
