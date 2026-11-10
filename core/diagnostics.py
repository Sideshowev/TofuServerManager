"""Save diagnostics: one zip with what's needed to understand a problem, safe to
share. It holds BaoBun's logs, the end of each server's console log, its server
list and settings, what Checkup says, the server statuses and some facts about
the PC (Windows version, CPU, RAM, free disk).

Everything goes through redact() first: passwords, tokens, keys, webhooks and
the Discord bot token are replaced with "****", and internet IP addresses
become "x.x.x.x" (home-network and Tailscale addresses are kept - they help and
say nothing about you to the outside). discord_config.json is included only as
its non-secret settings (channels, roles, options).
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

LOG_TAIL_BYTES = 2 * 1024 * 1024
SERVER_LOG_LINES = 400
SECRET_KEY = re.compile(r"(?i)(pass(word|wd)?|token|secret|api[_-]?key|webhook|credential|auth|cookie|session|"
                        r"private|_protected)")
_ARG_SECRETS = [
    re.compile(r"(?i)((?:Server)?(?:Admin)?Password=)([^?\s\"]+)"),                 # ARK ?ServerAdminPassword=x
    re.compile(r"(?i)((?:password|passwd|token|secret|apikey|api_key|webhook)\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s?&\"',;]+)"),
    re.compile(r"(?i)((?:^|\s)[-+](?:password|pass|adminpassword|rconpassword|serverpassword)\s+)(\"[^\"]*\"|\S+)"),
    re.compile(r"(https://(?:discord|discordapp)\.com/api/webhooks/)\S+"),
    re.compile(r"((?:mfa\.)?[A-Za-z\d_-]{23,28}\.[A-Za-z\d_-]{6,7}\.)[A-Za-z\d_-]{27,}"),   # Discord bot tokens
]
_IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
CONFIG_SAFE_KEYS = ("channel_id", "log_channel_id", "alert_channel_id", "event_channel_id", "admin_ids",
                    "admin_role_ids", "start_role_ids", "web_admin_ids", "web_admin_role_ids",
                    "web_operator_role_ids", "guild_id", "panel_url", "show_join_address", "log_max_age_hours",
                    "start_cooldown_seconds", "max_running", "announce_players", "announce_activity",
                    "notify_ready", "oauth_client_id")


def _public_ip(a: int, b: int, c: int, d: int) -> bool:
    if any(x > 255 for x in (a, b, c, d)):
        return False          # a version number, not an address
    if a in (0, 10, 127) or a >= 224:
        return False
    if (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168) or (a == 169 and b == 254):
        return False
    if a == 100 and 64 <= b <= 127:
        return False          # Tailscale
    return True


def redact_text(text: str) -> str:
    s = str(text)
    for rx in _ARG_SECRETS:
        s = rx.sub(lambda m: m.group(1) + "****", s)
    def ip(m):
        before = s[max(0, m.start() - 12):m.start()].lower()
        if re.search(r"(version|ver\.?|build|v)\s*$", before):
            return m.group(0)             # "version 1.20.3.4" is not an address
        return "x.x.x.x" if _public_ip(*(int(g) for g in m.groups())) else m.group(0)
    return _IPV4.sub(ip, s)


def redact(obj: Any, key: str = "") -> Any:
    """A copy with secrets blanked (by key name, and inside any text)."""
    if isinstance(obj, dict):
        return {k: ("****" if SECRET_KEY.search(str(k)) and v not in (None, "", [], {}, False, 0)
                    else redact(v, str(k))) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, key) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def _tail(path: str, max_bytes: int) -> str:
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read().decode("utf-8", "replace")
    if size > max_bytes:
        data = "[... first %d KB left out ...]\n" % ((size - max_bytes) // 1024) + data.split("\n", 1)[-1]
    return data


def _read_json(path) -> Optional[Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _system() -> dict:
    out = {"os": platform.platform(), "python": sys.version.split()[0], "frozen": bool(getattr(sys, "frozen", False)),
           "cpus": os.cpu_count(), "time": time.strftime("%Y-%m-%d %H:%M:%S %z")}
    try:
        import psutil
        vm = psutil.virtual_memory()
        out["ram_total_gb"] = round(vm.total / 1024 ** 3, 1)
        out["ram_used_percent"] = vm.percent
        out["cpu_percent"] = psutil.cpu_percent(interval=0.3)
        out["uptime_hours"] = round((time.time() - psutil.boot_time()) / 3600, 1)
    except Exception:
        pass
    return out


def build(mgr, dest_dir: Optional[str] = None, include_server_logs: bool = True) -> str:
    """Write the zip and return its path."""
    base = Path(mgr.config_path).parent
    dest = Path(dest_dir) if dest_dir else base / "diagnostics"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / ("BaoBun-diagnostics-%s.zip" % time.strftime("%Y%m%d-%H%M%S"))
    problems = []

    def add_json(z, name, data):
        z.writestr(name, json.dumps(redact(data), indent=2, default=str))

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", "BaoBun diagnostics, made %s.\nPasswords, tokens, keys and internet IP addresses "
                   "have been replaced with **** / x.x.x.x.\n" % time.strftime("%Y-%m-%d %H:%M"))
        sysinfo = _system()
        try:
            from core.self_update import Watcher
            sysinfo["build"] = Watcher().started
        except Exception:
            pass
        try:
            from core import boot_task
            sysinfo["boot_task"] = boot_task.mode()
        except Exception:
            pass
        try:
            sysinfo["watchdog_running"] = bool(mgr.agent_available())
        except Exception:
            pass
        sysinfo["data_folder"] = str(base)
        try:
            from core.version import VERSION
            sysinfo["version"] = VERSION
        except Exception:
            pass
        add_json(z, "system.json", sysinfo)
        servers = []
        try:
            servers = mgr.load_servers()
            add_json(z, "servers.json", servers)
        except Exception as e:
            problems.append("servers: %s" % e)
        settings = _read_json(base / "settings.json")
        if settings is not None:
            add_json(z, "settings.json", settings)
        cfg = _read_json(base / "discord_config.json")
        if isinstance(cfg, dict):
            safe = {k: cfg[k] for k in CONFIG_SAFE_KEYS if k in cfg}
            safe["bot_configured"] = bool(cfg.get("token_protected") or (cfg.get("token") and
                                                                    "YOUR_" not in str(cfg.get("token"))))
            add_json(z, "discord_config (no secrets).json", safe)
        try:
            add_json(z, "statuses.json", mgr.get_all_statuses())
        except Exception as e:
            problems.append("statuses: %s" % e)
        try:
            from core import checkup
            add_json(z, "checkup.json", checkup.run(mgr, firewall=False, force=True))
        except Exception as e:
            problems.append("checkup: %s" % e)
        for name in ("nightly.json", "mod_updates.json", "mirror_status.json", "version.json"):
            data = _read_json(base / "runtime" / name)
            if data is not None:
                add_json(z, "runtime/" + name, data)
        logs = base / "logs"
        if logs.is_dir():
            for f in sorted(logs.glob("*.log")):
                try:
                    z.writestr("logs/" + f.name, redact_text(_tail(str(f), LOG_TAIL_BYTES)))
                except Exception as e:
                    problems.append("%s: %s" % (f.name, e))
        if include_server_logs:
            for srv in servers:
                sid = srv.get("id")
                if not sid:
                    continue
                safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(sid))[:60]
                try:
                    lp = mgr.get_log_path(sid)
                    if lp and os.path.isfile(lp):
                        text = _tail(lp, 512 * 1024)
                        text = "\n".join(text.splitlines()[-SERVER_LOG_LINES:])
                        z.writestr("servers/%s/console.log" % safe_id, redact_text(text))
                except Exception as e:
                    problems.append("%s log: %s" % (sid, e))
                d = srv.get("directory") or ""
                for extra in ("install.log", "steamcmd_install.log"):
                    p = os.path.join(d, extra)
                    if os.path.isfile(p):
                        try:
                            z.writestr("servers/%s/%s" % (safe_id, extra), redact_text(_tail(p, 128 * 1024)))
                        except Exception:
                            pass
        if problems:
            z.writestr("problems-collecting.txt", "\n".join(problems))
    return str(path)
