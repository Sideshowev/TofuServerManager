"""BaoBun API layer: one action-based interface over the shared engine.

Every caller (the localhost agent API, the Discord bot, scripts) goes through
``ApiLayer.process(action, params)``. Handlers delegate to the single
ServerManager, so there is exactly one implementation of every operation and
one process table. Each request is recorded in the audit log with secrets
redacted.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

SECRET_KEYS = ("password", "token", "secret", "adminpassword", "serverpassword", "webhook")


def console_tail(mgr, server_id: str, offset: int) -> dict:
    """New console lines since ``offset`` (web panel console and linked PCs)."""
    if mgr.get_server(server_id) is None:
        return {"error": "Server not found"}
    path = mgr.get_console_log_path(server_id)
    if not path or not os.path.isfile(path):
        return {"lines": [], "offset": 0, "reset": True}
    size = os.path.getsize(path)
    reset = offset < 0 or offset > size
    start = max(0, size - 64 * 1024) if reset else offset
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(min(size - start, 512 * 1024))
    end = start + len(data)
    cut = data.rfind(b"\n")
    if cut >= 0 and not reset:
        data, end = data[:cut + 1], start + cut + 1
    elif cut < 0 and not reset:
        return {"lines": [], "offset": start, "reset": False}
    lines = data.decode("utf-8", errors="replace").splitlines()
    if reset and start > 0 and lines:
        lines = lines[1:]
    return {"lines": lines[-2000:], "offset": end, "reset": reset}


class ApiError(Exception):
    """Raised when an API operation fails."""
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"{code}: {message}")


@dataclass
class ApiRequest:
    id: str
    action: str
    params: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    user_id: str = "system"

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "action": self.action, "params": redact(self.params),
                "timestamp": self.timestamp, "user_id": self.user_id}


@dataclass
class ApiResponse:
    id: str
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "success": self.success, "data": self.data,
                "error": self.error, "error_code": self.error_code, "timestamp": self.timestamp}


def redact(value: Any) -> Any:
    """Copy of ``value`` with anything that looks like a secret masked."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if any(s in str(k).lower() for s in SECRET_KEYS):
                out[k] = "***" if v else v
            elif str(k) == "value" and any(s in str(value.get("key", "")).lower() for s in SECRET_KEYS):
                out[k] = "***" if v else v
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and "assword=" in value:
        import re
        return re.sub(r"(?i)(password=)[^\s?&]+", r"\1***", value)
    return value


# Actions that only read state; they are not written to the audit log.
READ_ONLY = {
    "agent.ping", "server.list", "server.get", "server.status", "logs.tail", "ports.list",
    "ports.conflicts", "backup.list", "settings.get", "audit.list", "plugins.list",
    "presets.list", "metrics.history", "health.check_all", "ai.health", "ai.log_insight",
    "server.check_update", "server.players", "games.list", "schedules.get",
    "mods.list", "mods.check", "mods.updates", "mods.search", "metrics.series", "metrics.sparklines", "server.connect_info", "server.game_settings", "server.quick_commands",
    "server.install_status", "server.install_options", "server.install_versions", "health.checkup",
    "config.form_files", "config.form", "mods.details", "ports.plan",
    "ark.cluster", "ark.rules", "ark.spawns", "ark.loot", "ark.catalog", "agent.update_status", "host.settings",
}


class ApiLayer:
    def __init__(self, manager, database_layer=None, plugin_manager=None,
                 preset_engine=None, health_check=None):
        self.mgr = manager
        self.db = database_layer
        self.pm = plugin_manager
        self.preset_engine = preset_engine
        self.health = health_check
        self.on_shutdown: Optional[Callable[[bool], None]] = None
        self.on_web_reload: Optional[Callable[[], Optional[str]]] = None
        self.alerts = None   # core.alerts.AlertCenter (agent only)
        self._lock = threading.RLock()
        self._action_handlers: Dict[str, Callable] = {}
        self._request_history: List[ApiRequest] = []
        self._max_history = 2000
        self._register_builtin_handlers()

    # ------------------------------------------------------------------
    def _register_builtin_handlers(self) -> None:
        self._action_handlers.update({
            "agent.ping": lambda p: {"pid": __import__("os").getpid(), "role": self.mgr.role},
            "agent.shutdown": self._handle_agent_shutdown,
            "agent.update_status": self._handle_update_status,
            "host.settings": self._handle_host_settings,
            "host.settings_save": self._handle_host_settings_save,
            "host.mirror_test": lambda p: self.mgr.test_mirror(str(p.get("path") or "") or None),
            "agent.update_now": self._handle_update_now,
            "app.version": lambda p: {k: v for k, v in __import__("core.updates", fromlist=["cached"]).cached(
                self.mgr).items() if k in ("current", "latest", "newer", "page", "notes", "enabled", "checked")},
            "app.update_check": lambda p: {k: v for k, v in __import__("core.updates", fromlist=["check"]).check(
                self.mgr, force=True).items() if k in ("current", "latest", "newer", "page", "notes", "enabled",
                                                     "checked", "error")},
            "discord.reload": self._handle_discord_reload,
            "agent.web_reload": self._handle_web_reload,
            "alerts.test": self._handle_alerts_test,

            "server.connect_info": lambda p: __import__("core.network", fromlist=["x"]).connect_info(self.mgr, self._sid(p)),
            "server.upnp": self._handle_upnp,
            "server.quick_commands": lambda p: {"commands": __import__("core.quick_commands", fromlist=["x"]).for_server(
                self.mgr.get_server(self._sid(p)))},
            "server.game_settings": lambda p: __import__("core.quick_settings", fromlist=["x"]).get(self.mgr, self._sid(p)),
            "server.save_game_settings": lambda p: __import__("core.quick_settings", fromlist=["x"]).save(
                self.mgr, self._sid(p), p.get("values") or {}, user=p.get("user", "api")),
            "metrics.series": self._handle_metrics_series,
            "metrics.sparklines": self._handle_metrics_sparklines,

            "mods.list": lambda p: self._mods().list(self._sid(p)),
            "mods.check": lambda p: self._mods().list(self._sid(p), check_updates=True),
            "mods.search": lambda p: {"results": self._mods().search(self._sid(p), str(p.get("text") or "")[:100],
                                                                     sort=p.get("sort") if p.get("sort") in (
                                                                         "popular", "trending", "updated", "newest",
                                                                         "follows") else None)},
            "mods.details": lambda p: self._mods().details(self._sid(p), str(self._req(p, "mod"))[:120]),
            "mods.install": lambda p: self._mods().install(self._sid(p), self._req(p, "mod"),
                                                           user=p.get("user", "api")),
            "mods.update": lambda p: self._mods().update(self._sid(p), p.get("mods") or None,
                                                         user=p.get("user", "api")),
            "mods.set_enabled": lambda p: self._mods().set_enabled(self._sid(p), self._req(p, "mod"),
                                                                   bool(p.get("enabled")), user=p.get("user", "api")),
            "mods.remove": lambda p: self._mods().remove(self._sid(p), self._req(p, "mod"),
                                                         user=p.get("user", "api")),
            "mods.install_deps": lambda p: self._mods().install_missing_deps(self._sid(p),
                                                                             user=p.get("user", "api")),
            "mods.move": lambda p: self._mods().move(self._sid(p), self._req(p, "mod"), int(p.get("delta") or 1),
                                                     user=p.get("user", "api")),
            "mods.set_field": lambda p: self._mods().set_field(self._sid(p), self._req(p, "mod"),
                                                               self._req(p, "field"), p.get("value"),
                                                               user=p.get("user", "api")),
            "mods.set_option": lambda p: self._mods().set_option(self._sid(p), self._req(p, "key"), p.get("value"),
                                                                 user=p.get("user", "api")),
            "mods.pin": lambda p: self._mods().set_pin(self._sid(p), str(self._req(p, "mod"))[:200],
                                                       bool(p.get("pinned", True)), user=p.get("user", "api")),
            "mods.auto_update": lambda p: self._mods().set_auto_update(self._sid(p), bool(p.get("included", True)),
                                                                       user=p.get("user", "api")),
            "mods.updates": lambda p: self._mod_updates_status(),
            "mods.setup": lambda p: self._mods().setup(self._sid(p), self._req(p, "setup_action"),
                                                       user=p.get("user", "api")),

            "server.list": lambda p: {"servers": redact(self.mgr.load_servers())},
            "server.get": self._handle_server_get,
            "server.add": self._handle_server_add,
            "server.remove": self._handle_server_remove,
            "server.start": self._handle_server_start,
            "server.stop": self._handle_server_stop,
            "server.restart": self._handle_server_restart,
            "server.status": self._handle_server_status,
            "server.start_all": self._handle_start_all,
            "server.stop_all": lambda p: {"results": self.mgr.stop_all(user=p.get("user", "api"))},
            "server.update": self._handle_server_update,
            "server.check_update": lambda p: self.mgr.check_update(self._sid(p)),
            "server.broadcast": lambda p: {"sent": self.mgr.broadcast(self._sid(p), self._req(p, "message"))},
            "server.players": lambda p: {"players": self.mgr.poll_players(self._sid(p))},
            "games.list": self._handle_games_list,
            "server.update_settings": self._handle_update_settings,
            "server.add_preset": self._handle_add_preset,
            "server.install": self._handle_install,
            "server.hytale_signin": self._handle_hytale_signin,
            "server.install_status": self._handle_install_status,
            "server.install_options": lambda p: self.mgr.install_options(self._sid(p)),
            "server.install_versions": self._handle_install_versions,
            "schedules.get": self._handle_schedules_get,
            "schedules.set": self._handle_schedules_set,

            "console.send": lambda p: self.mgr.send_console_command(
                self._sid(p), self._req(p, "command"), user=p.get("user", "api")),
            "logs.tail": self._handle_logs_tail,

            "config.list": lambda p: {"config_files": self.mgr.list_configs(self._sid(p))},
            "config.read": lambda p: self.mgr.load_config(self._sid(p), self._config_file(p)),
            "config.write": self._handle_config_write,
            "config.form_files": lambda p: {"files": self._forms().files(self.mgr, self._sid(p))},
            "config.form": lambda p: self._forms_call("form", p),
            "config.form_save": lambda p: self._forms_call("save", p),

            "ports.list": lambda p: {"ports": self.mgr.get_all_ports()},
            "ports.conflicts": lambda p: {"conflicts": self.mgr.detect_port_conflicts()},
            "ports.plan": lambda p: __import__("core.port_plan", fromlist=["overview"]).overview(self.mgr),
            "ports.fix": self._handle_ports_fix,
            "ports.set": self._handle_ports_set,
            "rcon.setup": self._handle_rcon_setup,
            "ark.cluster": lambda p: self._ark("check", p),
            "ark.fix": lambda p: self._ark("fix", p, str(p.get("action") or "")),
            "ark.rules": lambda p: self._ark_rules(p),
            "ark.rules_save": lambda p: self._ark("save_rules", p, dict(p.get("values") or {})),
            "ark.spawns": lambda p: self._ark("read_spawns", p),
            "ark.spawns_save": lambda p: self._ark("save_spawns", p, list(p.get("rules") or [])),
            "ark.loot": lambda p: self._ark("read_loot", p),
            "ark.loot_save": lambda p: self._ark("save_loot", p, list(p.get("loot") or [])),
            "ark.wipe_wild": lambda p: self._ark("wipe_wild", p),
            "ark.separate_plan": lambda p: self._ark("separate_plan", p, self._ark_target(p)),
            "ark.separate": lambda p: self._ark("start_separate", p, self._ark_target(p)),
            "ark.catalog": lambda p: __import__("core.ark_catalog", fromlist=["as_json"]).as_json(),

            "backup.create": lambda p: self.mgr.backup_server_world(
                self._sid(p), p.get("dest_dir") or None, reason=p.get("reason", "manual")),
            "backup.list": lambda p: self._backup_list(p),
            "backup.restore": self._handle_backup_restore,
            "fleet.status": self._fleet_status,
            "logs.since": lambda p: console_tail(self.mgr, self._sid(p), int(p.get("offset", -1))),
            "players.access": lambda p: self._access("overview", p),
            "players.access_change": self._handle_access_change,
            "players.allow_on": lambda p: self._access("set_allow_on", p, bool(p.get("on")),
                                                       by=p.get("user", "api")),
            "servers.bring_scan": lambda p: self._bring_scan(),
            "servers.bring": self._handle_bring,
            "servers.bring_job": lambda p: {"job": __import__("core.bring_here", fromlist=["job"]).job(
                str(p.get("job") or ""))},

            "settings.get": self._handle_settings_get,
            "settings.set": self._handle_settings_set,

            "audit.list": lambda p: {"audit": self.db.get_audit_log(limit=int(p.get("limit", 100)))
                                     if self.db else []},
            "plugins.list": self._handle_plugins_list,
            "metrics.history": self._handle_metrics_history,
            "health.check_all": self._handle_health_check_all,
            "health.checkup": lambda p: __import__("core.checkup", fromlist=["run"]).run(
                self.mgr, force=bool(p.get("refresh"))),

            "presets.list": self._handle_presets_list,
            "presets.save": self._handle_presets_save,
            "presets.update": self._handle_presets_update,
            "presets.delete": self._handle_presets_delete,
            "presets.apply": self._handle_presets_apply,

            "ai.health": self._handle_ai_health,
            "ai.suggest_preset": self._handle_ai_suggest_preset,
            "ai.log_insight": self._handle_ai_log_insight,
        })

    def register_handler(self, action: str, handler: Callable) -> None:
        with self._lock:
            self._action_handlers[action] = handler

    def get_request_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            return [r.to_dict() for r in self._request_history[-limit:]]

    # ------------------------------------------------------------------
    def process(self, action: str, params: Optional[Dict[str, Any]] = None,
                user_id: str = "system") -> ApiResponse:
        params = dict(params or {})
        params.setdefault("user", user_id)
        req = ApiRequest(id=str(uuid.uuid4()), action=action, params=params, user_id=user_id)
        with self._lock:
            self._request_history.append(req)
            del self._request_history[:-self._max_history]
        if self.db is not None and action not in READ_ONLY:
            try:
                self.db.audit(action=action, target_type="api",
                              target_id=str(params.get("server_id") or req.id),
                              user_id=user_id, details={"params": redact(params)})
            except Exception:
                pass
        try:
            sid = params.get("server_id")
            if isinstance(sid, str) and sid.startswith("@"):
                data = self._remote(action, params)       # a server on a linked PC
                return ApiResponse(id=req.id, success=True, data=data)
            handler = self._action_handlers.get(action)
            if handler is None:
                raise ApiError("action_not_found", f"No handler for action: {action}")
            data = handler(params)
            return ApiResponse(id=req.id, success=True, data=data)
        except ApiError as e:
            return ApiResponse(id=req.id, success=False, error=e.message, error_code=e.code)
        except Exception as e:
            return ApiResponse(id=req.id, success=False, error=str(e), error_code="internal_error")

    # ------------------------------------------------------------------
    @staticmethod
    def _req(params: Dict[str, Any], key: str):
        v = params.get(key)
        if v in (None, ""):
            raise ApiError("missing_param", f"{key} is required")
        return v

    def _sid(self, params: Dict[str, Any]) -> str:
        sid = self._req(params, "server_id")
        if self.mgr.get_server(sid) is None:
            raise ApiError("server_not_found", f"Server {sid} not found")
        return sid

    def _status(self, sid: str) -> Dict[str, Any]:
        return next((s for s in self.mgr.get_all_statuses() if s["id"] == sid), {})

    # -- agent -----------------------------------------------------------
    def _handle_agent_shutdown(self, params):
        if self.on_shutdown is None:
            raise ApiError("not_supported", "This process cannot be shut down remotely")
        stop = bool(params.get("stop_servers", True))
        threading.Thread(target=lambda: (time.sleep(0.3), self.on_shutdown(stop)), daemon=True).start()
        return {"shutting_down": True, "stop_servers": stop}

    def _handle_host_settings(self, params):
        """Game-PC settings the web panel can show/change: nightly restart, self-update, watchdog."""
        from core import nightly, boot_task
        try:
            boot = boot_task.installed()
        except Exception:
            boot = None
        prev = nightly.preview(self.mgr)
        return {"nightly": prev["config"], "preview": {"bring_back": prev["bring_back"], "sleeping": prev["sleeping"],
                                                       "next_run": prev["next_run"], "last": prev["last"]},
                "boot_task": boot,
                "self_update": self.mgr.get_setting("self_update", "when_idle") or "when_idle",
                "heartbeat_enabled": self.mgr.get_setting("heartbeat_enabled", False) is True,
                "backup_mirror_dir": self.mgr.get_setting("backup_mirror_dir", "") or "",
                "mirror_status": self.mgr.mirror_status(),
                "boot_mode": boot_task.mode() if boot else None,
                "mod_updates": self._mod_updates_status(),
                "status_page": __import__("core.status_page", fromlist=["config"]).config(self.mgr)}

    HOST_KEYS = {"nightly_restart_enabled": bool, "nightly_restart_time": str, "nightly_backup": bool,
                 "smart_boot_days": int, "self_update": str, "heartbeat_enabled": bool, "backup_mirror_dir": str,
                 "mod_updates": str, "status_page_enabled": bool, "status_page_public": bool,
                 "status_page_port": int, "status_address": str, "status_show_players": bool,
                 "status_discord_invite": str, "status_title": str}

    def _handle_host_settings_save(self, params):
        from core import nightly
        values = params.get("values") or {}
        if not isinstance(values, dict):
            raise ApiError("bad_value", "values must be an object")
        clean = {}
        for k, v in values.items():
            typ = self.HOST_KEYS.get(k)
            if typ is None:
                raise ApiError("bad_value", "That setting can't be changed here: %s" % k)
            if typ is bool:
                v = v is True or str(v).lower() in ("1", "true", "on", "yes")
            elif typ is int:
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    raise ApiError("bad_value", "%s must be a number" % k)
                if k == "status_page_port":
                    if not 1024 <= v <= 65535:
                        raise ApiError("bad_value", "Port must be 1024-65535")
                elif not 1 <= v <= 60:
                    raise ApiError("bad_value", "Days must be 1-60")
            else:
                v = str(v).strip()
            if k == "nightly_restart_time":
                if not nightly.valid_time(v):
                    raise ApiError("bad_value", "Use a time like 05:00 or 5am")
                h, m = nightly.parse_time(v)
                v = "%02d:%02d" % (h, m)
            if k == "self_update" and v not in ("when_idle", "manual"):
                raise ApiError("bad_value", "self_update is when_idle or manual")
            if k == "mod_updates" and v not in ("off", "notify", "auto"):
                raise ApiError("bad_value", "mod_updates is off, notify or auto")
            if k == "status_address":
                from core.status_page import clean_address
                if v and not clean_address(v):
                    raise ApiError("bad_value", "Use a host name or IP like play.example.com")
                v = clean_address(v)
            if k == "status_discord_invite" and v and not v.startswith(("https://discord.gg/",
                                                                         "https://discord.com/invite/")):
                raise ApiError("bad_value", "Use a Discord invite link (https://discord.gg/...)")
            if k == "status_title":
                v = v[:60]
            clean[k] = v
        for k, v in clean.items():
            self.mgr.set_setting(k, v)
        self.mgr._audit("host_settings", "host", user=params.get("user", "api"), keys=sorted(clean))
        return self._handle_host_settings(params)

    def _handle_update_status(self, params):
        from core import self_update
        up = getattr(self, "updater", None)
        st = up.check() if up is not None else {"ready": False, "supported": False}
        st["policy"] = self.mgr.get_setting("self_update", "when_idle") or "when_idle"
        if st.get("ready"):
            st["console_servers"] = self_update.console_servers(self.mgr)
            st["playing"] = self_update.anyone_playing(self.mgr)
            st["built_text"] = self_update.fmt_time(st.get("built"))
        if st.get("running_built"):
            st["running_text"] = self_update.fmt_time(st["running_built"])
        return st

    def _handle_discord_reload(self, params):
        fn = getattr(self, "on_discord_reload", None)
        if fn is None:
            raise ApiError("not_supported", "Only the Watchdog runs the Discord bot")
        return {"running": bool(fn())}

    def _handle_update_now(self, params):
        fn = getattr(self, "on_update", None)
        if fn is None:
            raise ApiError("not_supported", "Only the Watchdog (BaoBun's background service) can update BaoBun")
        try:
            return fn(reason="asked by %s" % params.get("user", "an admin"))
        except RuntimeError as e:
            raise ApiError("bad_value", str(e))

    def _handle_upnp(self, params):
        from core.network import connect_info, upnp_forward
        sid = self._sid(params)
        info = connect_info(self.mgr, sid, lookup_public=False)
        if not info["lan_ips"] or not info["ports"]:
            raise ApiError("no_ports", "BaoBun doesn't know this server's ports or this PC's address")
        r = upnp_forward(info["ports"], info["lan_ips"][0], label="BaoBun " + (info["name"] or sid),
                         remove=bool(params.get("remove")))
        self.mgr._audit("port_forward_remove" if params.get("remove") else "port_forward", sid,
                        user=params.get("user", "api"), ok=r.get("ok"))
        return r

    def _handle_ports_fix(self, params):
        from core import port_plan
        return port_plan.fix_all(self.mgr, user=params.get("user", "api"))

    def _handle_ports_set(self, params):
        from core import port_plan
        sid = self._sid(params)
        wanted = params.get("ports") or {}
        if not isinstance(wanted, dict) or not wanted:
            raise ApiError("missing_param", "ports is required")
        try:
            wanted = {str(k): int(v) for k, v in wanted.items()}
        except (TypeError, ValueError):
            raise ApiError("bad_value", "Ports must be numbers")
        try:
            res = port_plan.apply(self.mgr, sid, wanted=wanted, user=params.get("user", "api"))
        except (RuntimeError, ValueError) as e:
            raise ApiError("bad_value", str(e))
        return res

    def _backup_list(self, params):
        from core import backup_check
        sid = self._sid(params)
        out = {"backups": self.mgr.list_server_backups(sid, include_mirror=True)}
        s = backup_check.summary(self.mgr, sid)
        if s:
            out["check"] = s
        return out

    def _mod_updates_status(self):
        from core import mod_updates
        st = mod_updates.read_state(self.mgr)
        return {"mode": mod_updates.mode(self.mgr), "checked": st.get("checked"), "servers": st.get("servers") or {}}

    def _ark_rules(self, params):
        return self._ark("rules_info", params)

    @staticmethod
    def _ark_target(params):
        t = params.get("target")
        if t is None or t == "":
            return None
        if not isinstance(t, str) or len(t) > 260 or any(c in t for c in '<>|?*"\r\n'):
            raise ApiError("bad_value", "Bad folder")
        return t

    def _ark(self, fn, params, *args):
        from core import ark_cluster
        sid = self._sid(params)
        call = getattr(ark_cluster, fn)
        kw = {}
        if fn in ("save_rules", "save_spawns", "save_loot", "wipe_wild", "read_spawns", "read_loot", "rules_info"):
            where = params.get("where") or "all"
            if where not in ("all", "this"):
                raise ApiError("bad_value", "Bad scope")
            kw["where"] = where
        if fn in ("fix", "save_rules", "save_spawns", "save_loot", "wipe_wild", "start_separate"):
            kw["user"] = params.get("user", "api")
        try:
            return call(self.mgr, sid, *args, **kw)
        except (ValueError, RuntimeError, FileNotFoundError) as e:
            raise ApiError("bad_value", str(e))

    def _handle_rcon_setup(self, params):
        from core import rcon_setup
        sid = self._sid(params)
        try:
            return rcon_setup.enable(self.mgr, sid, user=params.get("user", "api"))
        except (RuntimeError, ValueError) as e:
            raise ApiError("bad_value", str(e))

    def _handle_metrics_series(self, params):
        from core.history import series, SYSTEM_ID
        sid = params.get("server_id")
        if sid != SYSTEM_ID:
            sid = self._sid(params)
        return series(self.db, sid, str(params.get("range") or "24h"))

    def _handle_metrics_sparklines(self, params):
        from core.history import sparklines
        ids = [s["id"] for s in self.mgr.load_servers() if s.get("id")]
        return {"sparklines": sparklines(self.db, ids)}

    def _mods(self):
        if getattr(self, "_mod_manager", None) is None:
            from core.mods import ModManager
            self._mod_manager = ModManager(self.mgr)
        return self._mod_manager

    def _handle_alerts_test(self, params):
        if self.alerts is None:
            raise ApiError("not_supported", "Alerts run in the Watchdog (BaoBun's background service)")
        return self.alerts.test()

    def _handle_web_reload(self, params):
        if self.on_web_reload is None:
            raise ApiError("not_supported", "This process has no web panel")
        return {"url": self.on_web_reload()}

    # -- servers ---------------------------------------------------------
    def _handle_server_get(self, params):
        sid = self._sid(params)
        return {"server": redact(self.mgr.get_server(sid)), "status": self._status(sid)}

    def _handle_server_status(self, params):
        if params.get("server_id"):
            return {"status": self._status(self._sid(params))}
        return {"statuses": self.mgr.get_all_statuses()}

    def _handle_server_add(self, params):
        """Add a server using the same camelCase schema the app uses."""
        name = (params.get("name") or "").strip()
        if not name:
            raise ApiError("missing_param", "name is required")
        servers = self.mgr.load_servers()
        if any(s.get("name") == name for s in servers):
            raise ApiError("duplicate", f"A server named '{name}' already exists")
        allowed = ("name", "type", "directory", "executable", "launchArgs", "appId", "port",
                   "autoRestart", "maxRestarts", "folder", "requiresAccount",
                   "updateBeforeStart", "manualStart", "stopCommand", "stopTimeout", "savePaths")
        entry = {k: params[k] for k in allowed if k in params}
        entry.setdefault("type", "custom")
        entry.setdefault("autoRestart", True)
        entry.setdefault("maxRestarts", 5)
        entry["id"] = "srv_" + str(int(time.time() * 1000))
        servers.append(entry)
        self.mgr.save_servers(servers)
        return {"server": entry}

    def _handle_server_remove(self, params):
        sid = self._sid(params)
        if self.mgr.is_running(sid):
            raise ApiError("server_running", "Stop the server before removing it")
        self.mgr.save_servers([s for s in self.mgr.load_servers() if s.get("id") != sid])
        return {"removed": sid}

    def _handle_server_start(self, params):
        sid = self._sid(params)
        user = params.get("user", "api")
        srv = self.mgr.get_server(sid) or {}
        if srv.get("updateBeforeStart") and srv.get("appId"):
            # SteamCMD validate can take a long time: run it in the background
            # and let callers watch the "updating" state.
            def run():
                try:
                    self.mgr.start_server(sid, user=user)
                except Exception as e:
                    print(f"[WARN] background start of {sid} failed: {e}")
            threading.Thread(target=run, daemon=True).start()
            return {"status": "updating", "server_id": sid}
        return self.mgr.start_server(sid, user=user)

    def _handle_server_stop(self, params):
        return self.mgr.stop_server(self._sid(params), user=params.get("user", "api"))

    def _handle_server_restart(self, params):
        return self.mgr.restart_server(self._sid(params), user=params.get("user", "api"))

    def _handle_start_all(self, params):
        out = {}
        for s in self.mgr.load_servers():
            if params.get("skip_manual", True) and s.get("manualStart"):
                continue
            try:
                out[s["id"]] = self._handle_server_start({"server_id": s["id"], "user": params.get("user", "api")})
            except Exception as e:
                out[s["id"]] = {"status": "error", "error": str(e)}
        return {"results": out}

    def _handle_server_update(self, params):
        sid = self._sid(params)
        return self.mgr.update_server(
            sid, user=params.get("user", "api"), force=bool(params.get("force")),
            warn_minutes=params.get("warn_minutes"),
            skip_if_players=bool(params.get("skip_if_players")))

    # Linked PCs (core/linked_pcs.py): servers on other PCs have ids "@pc/server"
    def _remote(self, action, params):
        from core import linked_pcs
        if action not in linked_pcs.allowed_actions() or action == "link.hello":
            raise ApiError("not_supported", "That can't be done for a server on another PC")
        fwd = {k: v for k, v in params.items() if k not in linked_pcs.BLOCKED_PARAMS and k != "server_id"}
        try:
            return linked_pcs.fleet(self.mgr).call(params["server_id"], action, fwd)
        except (KeyError, ValueError, RuntimeError) as e:
            raise ApiError("remote", str(e))

    def _fleet_status(self, params):
        from core import linked_pcs
        f = linked_pcs.fleet(self.mgr)
        if not f.pcs():
            return {"pcs": [], "statuses": [], "servers": {}}
        return {"pcs": f.state(refresh=False), "statuses": f.statuses(refresh=False),
                "servers": f.servers()}

    # Players: allow list, bans, admins (core/player_access.py)
    def _access(self, fn, params, *args, **kw):
        from core import player_access as PA
        try:
            return getattr(PA, fn)(self.mgr, self._sid(params), *args, **kw)
        except PA.AccessError as e:
            raise ApiError("bad_request", str(e))

    def _handle_access_change(self, params):
        from core import player_access as PA
        sid = self._sid(params)
        args = (str(params.get("list") or ""), str(params.get("action") or ""), str(params.get("who") or ""),
                str(params.get("reason") or ""))
        by = params.get("user", "api")
        try:
            if params.get("everywhere"):
                res = PA.change_many(self.mgr, PA.same_game(self.mgr, sid), *args, by=by)
                return {"results": res, "text": "; ".join(r.get("text") or r.get("error") for r in res.values())}
            return PA.change(self.mgr, sid, *args, by=by)
        except PA.AccessError as e:
            raise ApiError("bad_request", str(e))

    # Restore on another PC. From the web only the second backup folder set on the
    # server PC is looked in, and server settings that can run programs stay behind.
    def _bring_scan(self):
        from core import bring_here
        res = bring_here.scan(self.mgr)
        servers = [{k: e[k] for k in ("key", "name", "game", "newest", "newest_time", "here", "can_bring", "why_not")}
                   for e in res.get("servers") or []]
        for out, e in zip(servers, res.get("servers") or []):
            out["backups"] = [{"name": b["name"], "size": b["size"], "mtime": b["mtime"]} for b in e["backups"]]
        return {"folder": res.get("folder"), "error": res.get("error"), "servers": servers,
                "base": bring_here.default_base(self.mgr), "note": bring_here.SAFETY_NOTE}

    def _handle_bring(self, params):
        from core import bring_here
        picks = [{"key": str(p.get("key") or ""), "backup": (str(p["backup"]) if p.get("backup") else None)}
                 for p in (params.get("picks") or []) if isinstance(p, dict)]
        try:
            jid = bring_here.start(self.mgr, picks, bring_here.default_base(self.mgr), trusted=False)
        except (ValueError, RuntimeError) as e:
            raise ApiError("bad_request", str(e))
        return {"job": jid}

    def _handle_backup_restore(self, params):
        sid = self._sid(params)
        import os
        wanted = os.path.realpath(self._req(params, "backup_path"))
        known = {os.path.realpath(b["path"]) for b in self.mgr.list_server_backups(sid, include_mirror=True)}
        if wanted not in known:
            raise ApiError("bad_backup", "That backup doesn't belong to this server")
        return self.mgr.restore_server_world(sid, wanted)

    # Fields that may be edited remotely. Program path and folder are deliberately
    # not here: changing them would let a remote account run any program.
    EDITABLE = {
        "name": str, "launchArgs": str, "stopCommand": str, "broadcastCommand": str,
        "port": int, "queryPort": int, "stopTimeout": int, "idleStopMinutes": int, "maxRestarts": int,
        "manualStart": bool, "autoRestart": bool, "updateBeforeStart": bool, "discordVisible": bool,
        "cpuAffinity": str, "priority": str, "folder": str, "ramAlertMB": int, "memLimitMB": int, "workshopAppId": str, "autoUpdateWhenEmpty": bool,
        "javaMemoryMB": int,
    }
    # javaPath / javaArgs stay desktop-only: JVM flags can run programs.
    INT_LIMITS = {"ramAlertMB": 4_000_000, "memLimitMB": 4_000_000, "javaMemoryMB": 262_144}

    def _handle_update_settings(self, params):
        sid = self._sid(params)
        changes = params.get("changes") or {}
        servers = self.mgr.load_servers()
        srv = next(s for s in servers if s.get("id") == sid)
        applied = {}
        for k, v in changes.items():
            typ = self.EDITABLE.get(k)
            if typ is None:
                raise ApiError("not_editable", f"'{k}' can't be changed remotely")
            if v in (None, "") and typ is not bool:
                srv.pop(k, None)
                applied[k] = None
                continue
            try:
                v = typ(v) if typ is not bool else bool(v)
            except (TypeError, ValueError):
                raise ApiError("bad_value", f"Bad value for {k}")
            if k == "name":
                v = v.strip()
                if not v or any(s.get("name") == v and s.get("id") != sid for s in servers):
                    raise ApiError("bad_value", "That name is empty or already used")
            if k == "cpuAffinity":
                from core.hosting import parse_core_list, format_core_list
                v = format_core_list(parse_core_list(v))
            if k == "workshopAppId" and not str(v).strip().isdigit():
                raise ApiError("bad_value", "Workshop App ID must be a number")
            if k == "priority":
                from core.hosting import PRIORITIES
                if v not in PRIORITIES:
                    raise ApiError("bad_value", "Unknown priority")
            if typ is int and not (0 <= v <= self.INT_LIMITS.get(k, 65535)):
                raise ApiError("bad_value", f"{k} out of range")
            if k == "memLimitMB" and 0 < v < 512:
                raise ApiError("bad_value", "The RAM limit must be at least 512 MB (or blank for off)")
            srv[k] = v
            applied[k] = v
        self.mgr.save_servers(servers)
        self.mgr._audit("settings_change", sid, user=params.get("user", "api"), fields=sorted(applied))
        return {"applied": applied}

    def _handle_add_preset(self, params):
        """Add a server from a game preset. Its folder is always <default install
        folder>/<name>, and the program comes from the preset."""
        import os
        from pathlib import Path
        from core.game_catalog import GAME_PRESETS
        game = self._req(params, "game")
        p = GAME_PRESETS.get(game)
        if not p or game == "Custom / Other":
            raise ApiError("bad_game", "Pick a game from the list")
        name = (params.get("name") or game).strip()
        servers = self.mgr.load_servers()
        if any(s.get("name") == name for s in servers):
            raise ApiError("duplicate", f"A server named '{name}' already exists")
        first = params.get("settings") if isinstance(params.get("settings"), dict) else None
        if first:      # name / password / max players for the game itself: check before anything is made
            from core import first_settings
            try:
                first_settings.check(p, first)
            except ValueError as e:
                raise ApiError("bad_value", str(e))
        root = self.mgr.get_setting("default_install_dir", "") or str(
            Path(Path(self.mgr.config_path).anchor or "C:/") / "GameServers")
        safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in name).strip() or "server"
        directory = os.path.join(root, safe)
        os.makedirs(directory, exist_ok=True)
        exe = p.get("executable")
        entry = {"id": "srv_" + str(int(time.time() * 1000)), "name": name,
                 "type": p.get("type", "custom"), "directory": directory,
                 "executable": os.path.join(directory, exe) if exe else None, "launchArgs": p.get("args") or "",
                 "appId": p.get("appId"), "requiresAccount": bool(p.get("appId")) and not p.get("anonymous_login", True),
                 "port": int(params["port"]) if params.get("port") else p.get("port"),
                 "autoRestart": True, "manualStart": not bool(params.get("auto_start")),
                 "updateBeforeStart": False, "folder": ""}
        from core import game_defaults
        entry["launchArgs"] = game_defaults.new_server_args(entry, servers)
        servers.append(entry)
        self.mgr.save_servers(servers)
        self.mgr._audit("server_add", entry["id"], user=params.get("user", "api"), game=game)
        from core import port_plan
        port_plan.setup_new(self.mgr, entry["id"], pinned_game=int(params["port"]) if params.get("port") else None)
        if first:
            try:
                from core import first_settings
                first_settings.remember(self.mgr, entry["id"], first, user=params.get("user", "api"))
            except Exception as e:
                print(f"[WARN] first settings for {name}: {e}")
        return {"server": redact(self.mgr.get_server(entry["id"]) or entry)}

    def _handle_install(self, params):
        """Install / update the game files: SteamCMD, or the Vintage Story / Minecraft
        downloaders. Runs in the background; progress is in the server's install.log."""
        sid = self._sid(params)
        srv = self.mgr.get_server(sid) or {}
        from core import game_installers as gi
        k = gi.kind(srv)
        if k is None:
            raise ApiError("not_supported", "BaoBun can't download this game - copy its files in by hand")
        if self.mgr.is_running(sid):
            raise ApiError("server_running", "Stop the server first")
        if self.mgr._states.get(sid) == "updating":
            raise ApiError("busy", "An install is already running for this server")
        opts = {}
        for key in ("flavor", "version", "channel"):
            v = params.get(key)
            if v is not None:
                if not isinstance(v, str) or len(v) > 40 or not all(c.isalnum() or c in ".-_" for c in v):
                    raise ApiError("bad_value", f"Bad {key}")
                opts[key] = v
        opts["accept_eula"] = bool(params.get("accept_eula"))
        user = params.get("user", "api")
        self.mgr._set_state(sid, "updating", pid=None)

        def run():
            try:
                res = self.mgr.install_game(sid, opts, user=user)
                self.mgr._write_registry(sid, install_result={"ok": True, "at": time.time(),
                                                              "version": res.get("version"),
                                                              "notes": res.get("notes") or []})
            except Exception as e:
                print(f"[WARN] install of {srv.get('name')} failed: {e}")
                self.mgr._write_registry(sid, install_result={"ok": False, "at": time.time(), "error": str(e)})
            finally:
                self.mgr._states.pop(sid, None)
                self.mgr._set_state(sid, "stopped", pid=None)
        threading.Thread(target=run, daemon=True).start()
        return {"status": "installing", "kind": k}

    def _handle_install_status(self, params):
        """Progress of a running/finished install: {"running", "percent", "phase", "result"}."""
        import os
        from core.hosting import parse_steamcmd_progress
        sid = self._sid(params)
        srv = self.mgr.get_server(sid) or {}
        d = srv.get("directory") or ""
        text, newest = "", 0.0
        for name in ("install.log", "steamcmd_install.log"):
            p = os.path.join(d, name)
            try:
                if os.path.isfile(p) and os.path.getmtime(p) > newest:
                    with open(p, "rb") as f:
                        f.seek(max(0, os.path.getsize(p) - 8192))
                        text = f.read().decode("utf-8", errors="replace")
                    newest = os.path.getmtime(p)
            except OSError:
                pass
        pct, phase = parse_steamcmd_progress(text)
        rec = self.mgr._read_registry(sid)
        out = {"running": self.mgr._states.get(sid) == "updating", "percent": pct, "phase": phase,
               "result": rec.get("install_result")}
        if out["running"]:
            from core.hytale_install import last_signin
            si = last_signin(text)
            if si and si["url"].startswith("https://"):
                out["signin"] = si
        return out

    def _handle_hytale_signin(self, params):
        """Ask a running Hytale server for its device sign-in link and code."""
        from core.hytale import is_hytale
        sid = self._sid(params)
        srv = self.mgr.get_server(sid) or {}
        if not is_hytale(srv):
            raise ApiError("bad_value", "Not a Hytale server")
        try:
            r = self.mgr.hytale_sign_in(sid, user=params.get("user", "api"))
        except RuntimeError as e:
            raise ApiError("not_running", str(e))
        if r.get("url") and not str(r["url"]).startswith("https://"):
            r["url"] = None
        return r

    def _state_path(self):
        return self.mgr.config_path.parent / "agent_state.json"

    def _handle_schedules_get(self, params):
        import json
        sid = self._sid(params)
        try:
            state = json.loads(self._state_path().read_text(encoding="utf-8"))
        except Exception:
            state = {}
        return {"schedules": (state.get("schedules") or {}).get(sid, {})}

    def _handle_schedules_set(self, params):
        import json
        from core.paths import atomic_write_json
        sid = self._sid(params)
        kind = self._req(params, "kind")
        if kind not in ("backup", "restart", "update", "message"):
            raise ApiError("bad_value", "kind must be backup, restart, update or message")
        cfg = params.get("config") or {}
        clean = {"enabled": bool(cfg.get("enabled")),
                 "mode": "daily" if cfg.get("mode") == "daily" else "interval"}
        try:
            clean["hours"] = max(0.25, float(cfg.get("hours", 24) or 24))
        except (TypeError, ValueError):
            raise ApiError("bad_value", "hours must be a number")
        times = []
        for t in cfg.get("times") or []:
            try:
                h, m = (int(x) for x in str(t).split(":"))
                assert 0 <= h < 24 and 0 <= m < 60
                times.append("%02d:%02d" % (h, m))
            except Exception:
                raise ApiError("bad_value", f"Bad time '{t}' (use HH:MM)")
        clean["times"] = times
        if kind in ("restart", "update"):
            try:
                clean["warn_minutes"] = [int(x) for x in cfg.get("warn_minutes") or []]
            except (TypeError, ValueError):
                raise ApiError("bad_value", "warn_minutes must be numbers")
            clean["skip_if_players"] = bool(cfg.get("skip_if_players"))
        if kind == "message":
            text = str(cfg.get("text") or "").strip()
            if clean["enabled"] and not text:
                raise ApiError("bad_value", "Write the message to announce")
            if len(text) > 200 or any(c in text for c in "\r\n"):
                raise ApiError("bad_value", "Keep the message to one line (200 characters)")
            clean["text"] = text
        try:
            state = json.loads(self._state_path().read_text(encoding="utf-8"))
        except Exception:
            state = {}
        state.setdefault("schedules", {}).setdefault(sid, {})[kind] = clean
        atomic_write_json(self._state_path(), state)
        self.mgr._audit("schedule_change", sid, user=params.get("user", "api"), kind=kind)
        return {"schedule": clean}

    def _handle_logs_tail(self, params):
        sid = self._sid(params)
        try:
            n = int(params.get("tail_n") or params.get("last_n") or 200)
        except (TypeError, ValueError):
            n = 200
        lines = self.mgr.get_logs(sid, max(1, min(n, 5000)))
        return {"server_id": sid, "log_lines": lines, "count": len(lines)}

    def _config_file(self, params) -> str:
        """Only files the config editor lists (config extensions, inside the
        server folder) - never launchers or programs."""
        sid = self._sid(params)
        rel = str(self._req(params, "file")).replace("\\", "/")
        allowed = {c["relative"] for c in self.mgr.list_configs(sid)}
        if rel not in allowed or rel.lower().endswith((".bat", ".cmd", ".ps1", ".exe", ".dll", ".vbs")):
            raise ApiError("not_allowed", "That file can't be edited here")
        return rel

    @staticmethod
    def _forms():
        from core import config_forms
        return config_forms

    def _forms_call(self, what, params):
        cf = self._forms()
        sid = self._sid(params)
        fid = str(self._req(params, "file"))
        try:
            if what == "form":
                return cf.form(self.mgr, sid, fid)
            return cf.save(self.mgr, sid, fid, params.get("changes") or {}, user=params.get("user", "api"))
        except cf.FormError as e:
            raise ApiError("bad_value", str(e))

    def _handle_config_write(self, params):
        sid = self._sid(params)
        self.mgr.save_config(sid, self._config_file(params), params.get("payload") or {})
        return {"saved": params.get("file")}

    # -- settings (settings.json is the single settings store) -------------
    def _handle_settings_get(self, params):
        key = params.get("key")
        if key:
            return {"key": key, "value": redact({key: self.mgr.get_setting(key)})[key]}
        try:
            import json
            data = json.loads(self.mgr._settings_path().read_text(encoding="utf-8"))
        except Exception:
            data = {}
        return {"settings": redact(data)}

    def _handle_settings_set(self, params):
        key = self._req(params, "key")
        if "value" not in params:
            raise ApiError("missing_param", "value is required")
        self.mgr.set_setting(key, params["value"])
        return {"key": key}

    # -- plugins / metrics / health ------------------------------------------
    def _handle_plugins_list(self, params):
        out = []
        if self.pm is not None:
            for plugin in list(self.pm._plugins.values()):
                out.append({**plugin.metadata.to_dict(), "enabled": plugin.enabled})
        return {"plugins": out}

    def _handle_metrics_history(self, params):
        sid = self._sid(params)
        if self.db is None:
            return {"metrics": []}
        return {"metrics": self.db.get_metrics_history(sid, since=params.get("since"),
                                                       limit=int(params.get("limit", 500)))}

    def _handle_health_check_all(self, params):
        if self.health is None:
            raise ApiError("not_supported", "Health checks are not available")
        return {"servers": self.health.check_all_servers()}

    # -- game templates (Add Server presets) ------------------------------------
    def _handle_games_list(self, params):
        from core.game_catalog import GAME_PRESETS
        from core import game_installers as gi
        from core import game_support
        from core import first_settings
        out = []
        for k, v in GAME_PRESETS.items():
            kind = gi.kind({"type": v.get("type"), "appId": v.get("appId"), "executable": v.get("executable") or ""})
            try:      # what the "add a server" form asks for this game (generated passwords are not sent)
                ask = [dict(f, value="") for f in first_settings.fields(v)] if k != "Custom / Other" else []
            except Exception:
                ask = []
            out.append(dict(v, name=k, download=kind if k != "Custom / Other" else None, first_settings=ask,
                            support=game_support.level(dict(v, name=k)) if k != "Custom / Other" else None))
        return {"games": out, "mcFlavors": gi.MC_FLAVORS}

    _versions_cache: Dict[str, Any] = {}

    def _handle_install_versions(self, params):
        """Versions the Vintage Story / Minecraft installers can download (cached 30 min)."""
        from core import game_installers as gi
        kind = params.get("kind")
        flavor = params.get("flavor") or "vanilla"
        if kind not in ("vintage-story", "minecraft") or flavor not in {f["id"] for f in gi.MC_FLAVORS}:
            raise ApiError("bad_value", "Unknown game")
        key = kind + ":" + flavor
        hit = self._versions_cache.get(key)
        if hit and time.time() - hit[0] < 1800:
            return {"versions": hit[1]}
        try:
            vs = ([r["version"] for r in gi.vs_releases("stable")][:40] if kind == "vintage-story"
                  else gi.mc_versions(flavor, limit=40))
        except Exception as e:
            raise ApiError("unavailable", str(e))
        self._versions_cache[key] = (time.time(), vs)
        return {"versions": vs}

    # -- setting presets -----------------------------------------------------------
    def _presets(self):
        if self.preset_engine is None:
            raise ApiError("not_supported", "Preset engine is not available")
        return self.preset_engine

    def _handle_presets_list(self, params):
        if self.preset_engine is None:
            return {"presets": []}
        return {"presets": [p.to_dict() for p in self.preset_engine.list_presets(game_id=params.get("game_id"))]}

    def _handle_presets_save(self, params):
        from core.preset_engine import Preset
        preset = self._presets().create_preset(Preset.from_dict(params.get("preset") or {}))
        return {"preset": preset.to_dict()}

    def _handle_presets_update(self, params):
        preset = self._presets().update_preset(params.get("preset_id"), params.get("updates", {}))
        if not preset:
            raise ApiError("preset_not_found", f"Preset {params.get('preset_id')} not found")
        return {"preset": preset.to_dict()}

    def _handle_presets_delete(self, params):
        if not self._presets().delete_preset(params.get("preset_id")):
            raise ApiError("preset_not_found", f"Preset {params.get('preset_id')} not found")
        return {"deleted": params.get("preset_id")}

    def _handle_presets_apply(self, params):
        sid = self._sid(params)
        preset_id = self._req(params, "preset_id")
        engine = self._presets()
        if not engine.get_preset(preset_id):
            raise ApiError("preset_not_found", f"Preset {preset_id} not found")
        applied = engine.apply_preset_to_server(preset_id, dict(self.mgr.get_server(sid)))
        applied["preset_applied"] = preset_id
        return {"server": redact(applied)}

    # -- "AI" helpers: rule-based summaries for the UI / Discord ----------------
    def _handle_ai_health(self, params):
        statuses = self.mgr.get_all_statuses()
        servers = [{
            "server_id": s["id"], "name": s["name"], "type": s.get("type"),
            "is_running": s.get("isRunning"), "status": s.get("state"),
            "pid": s.get("pid"), "cpu_percent": s.get("cpuPercent"),
            "memory_mb": s.get("memUsageMB"),
        } for s in statuses]
        unhealthy = any(s.get("state") == "crashed" for s in statuses)
        return {"servers": servers, "count": len(servers),
                "running_count": sum(1 for s in statuses if s.get("isRunning")),
                "any_unhealthy": unhealthy, "overall": "unhealthy" if unhealthy else "ok"}

    def _handle_ai_suggest_preset(self, params):
        engine = self._presets()
        game_id = params.get("game_id") or "vintage_story"
        try:
            players = int(params.get("player_count") or 10)
        except (TypeError, ValueError):
            players = 10
        presets = engine.list_presets(game_id=game_id)
        if not presets:
            raise ApiError("preset_not_found", f"No presets found for game {game_id}")

        def score(p):
            s = 2.0 if p.port else 0.0
            if p.max_players:
                s += 2.0 if players <= p.max_players else (-2.0 if players > p.max_players * 2 else 0)
            return s + min(1.0, (p.max_players or 10) / max(1, players))
        best = max(presets, key=score)
        return {"game_id": game_id, "player_count": players, "suggested_preset": best.to_dict()}

    def _handle_ai_log_insight(self, params):
        sid = self._sid(params)
        lines = self.mgr.get_logs(sid, int(params.get("tail_n") or 120))
        low = [l.lower() for l in lines]
        flags = []
        if any(k in l for l in low for k in ("crash", "exception", "fatal")):
            flags.append("possible_crash_or_exception")
        if any("port" in l and ("in use" in l or "bind" in l or "fail" in l) for l in low):
            flags.append("possible_port_or_bind_issue")
        if any("vcruntime" in l or "msvcp" in l for l in low):
            flags.append("missing_visual_cpp_redistributable")
        if any(".net" in l and ("not found" in l or "required" in l) for l in low):
            flags.append("missing_dotnet_runtime")
        return {"server_id": sid, "log_lines": lines, "count": len(lines), "flags": flags,
                "server_type": (self.mgr.get_server(sid) or {}).get("type", "custom")}
