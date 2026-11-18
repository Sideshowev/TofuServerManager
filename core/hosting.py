"""Hosting helpers: CPU pinning, Windows Firewall rules, port picking,
SteamCMD progress parsing, idle auto-stop and the dashboard summary.

Pure functions where possible so they can be unit-tested without a GUI.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


# --------------------------------------------------------------------------- #
# CPU cores / priority
# --------------------------------------------------------------------------- #
PRIORITIES = ("normal", "above_normal", "high", "below_normal")


def logical_cpu_count() -> int:
    try:
        return (psutil.cpu_count(logical=True) if psutil else os.cpu_count()) or 1
    except Exception:
        return os.cpu_count() or 1


def parse_core_list(text) -> List[int]:
    """"0-7, 16, 18-19" -> [0..7, 16, 18, 19]. Lists pass through. Raises ValueError."""
    if text is None or text == "":
        return []
    if isinstance(text, (list, tuple)):
        return sorted({int(x) for x in text})
    cores = set()
    for part in str(text).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a, b = int(a), int(b)
            if b < a:
                raise ValueError(f"bad range {part}")
            cores.update(range(a, b + 1))
        else:
            cores.add(int(part))
    return sorted(cores)


def format_core_list(cores: Iterable[int]) -> str:
    """[0,1,2,3,8] -> "0-3, 8"."""
    cores = sorted(set(cores))
    out, start, prev = [], None, None
    for c in cores + [None]:
        if start is None:
            start = prev = c
            continue
        if c is not None and c == prev + 1:
            prev = c
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = c
    return ", ".join(out)


def suggest_core_block(used: Iterable[Iterable[int]], total: Optional[int] = None,
                       block: Optional[int] = None) -> List[int]:
    """Pick the least-used contiguous block of logical cores for a new server.

    On big CPUs (e.g. a 32-core Threadripper = 64 logical CPUs) a block of 16
    logical CPUs is one 8-core CCD with SMT, so a server's threads share cache
    and don't fight other servers. Smaller CPUs use quarters of the machine.
    """
    total = total or logical_cpu_count()
    if block is None:
        block = 16 if total >= 32 else max(2, total // 4)
    block = max(1, min(block, total))
    load = [0] * total
    for cores in used:
        for c in cores:
            if 0 <= c < total:
                load[c] += 1
    best, best_score = 0, None
    for start in range(0, total - block + 1, block):
        score = sum(load[start:start + block])
        if best_score is None or score < best_score:
            best, best_score = start, score
    return list(range(best, best + block))


def apply_cpu_settings(pid: int, cores: List[int], priority: str = "normal") -> bool:
    """Pin a server's whole process tree to ``cores`` and set its priority."""
    if not pid or psutil is None:
        return False
    try:
        root = psutil.Process(pid)
        procs = [root] + root.children(recursive=True)
    except Exception:
        return False
    prio_map = {}
    if os.name == "nt":
        prio_map = {"normal": psutil.NORMAL_PRIORITY_CLASS,
                    "above_normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
                    "high": psutil.HIGH_PRIORITY_CLASS,
                    "below_normal": psutil.BELOW_NORMAL_PRIORITY_CLASS}
    else:
        prio_map = {"normal": 0, "above_normal": -5, "high": -10, "below_normal": 5}
    ok = True
    total = logical_cpu_count()
    valid = [c for c in cores if 0 <= c < total]
    for p in procs:
        try:
            if valid:
                p.cpu_affinity(valid)
            if priority in prio_map and priority != "normal":
                p.nice(prio_map[priority])
        except Exception:
            ok = False
    return ok


# --------------------------------------------------------------------------- #
# Ports
# --------------------------------------------------------------------------- #
def port_in_use(port: int) -> bool:
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
        s = socket.socket(socket.AF_INET, kind)
        try:
            s.bind(("0.0.0.0", int(port)))
        except OSError:
            return True
        finally:
            s.close()
    return False


def pick_port(preferred: Optional[int], taken: Iterable[int], step: int = 2,
              check_os: bool = True, limit: int = 200) -> Optional[int]:
    """The preferred port if free, else the next free one (stepping by ``step``
    so a game's port+1 query port stays free too)."""
    taken = set(int(p) for p in taken if p)
    port = int(preferred or 27015)
    for _ in range(limit):
        if port not in taken and (port + 1) not in taken and not (check_os and port_in_use(port)):
            return port
        port += step
        if port > 65000:
            port = 20000
    return None


# --------------------------------------------------------------------------- #
# Windows Firewall
# --------------------------------------------------------------------------- #
def firewall_rule_name(server_name: str) -> str:
    safe = re.sub(r'["\r\n]', "", server_name or "server").strip()
    return "Tofu - " + safe


def _old_rule_name(server_name: str) -> str:
    return "BaoBun - " + re.sub(r'["\r\n]', "", server_name or "server").strip()


def firewall_commands(server_name: str, exe: Optional[str], ports: Iterable[int]) -> List[str]:
    """netsh commands that (re)create inbound allow rules for one server:
    one for the server program itself, and TCP+UDP rules for its ports."""
    name = firewall_rule_name(server_name)
    old = _old_rule_name(server_name)
    cmds = [f'netsh advfirewall firewall delete rule name="{name}"',
            f'netsh advfirewall firewall delete rule name="{name} (ports)"',
            f'netsh advfirewall firewall delete rule name="{old}"',          # made before the rename
            f'netsh advfirewall firewall delete rule name="{old} (ports)"']
    if exe:
        cmds.append(f'netsh advfirewall firewall add rule name="{name}" dir=in action=allow '
                    f'program="{exe}" enable=yes profile=any')
    plist = sorted({int(p) for p in ports if p})
    if plist:
        joined = ",".join(str(p) for p in plist)
        for proto in ("TCP", "UDP"):
            cmds.append(f'netsh advfirewall firewall add rule name="{name} (ports)" dir=in '
                        f'action=allow protocol={proto} localport={joined} enable=yes profile=any')
    return cmds


def firewall_rule_exists(server_name: str) -> Optional[bool]:
    """True/False on Windows (no admin needed); None if it can't be checked."""
    if os.name != "nt":
        return None
    for name in (firewall_rule_name(server_name), _old_rule_name(server_name)):     # old rules still count
        try:
            out = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                                 capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW)
        except Exception:
            return None
        if out.returncode == 0 and name.lower() in (out.stdout or "").lower():
            return True
    return False


def run_elevated(commands: List[str]) -> bool:
    """Run commands as administrator with ONE Windows UAC prompt.
    Returns False if the user declined or this isn't Windows."""
    if os.name != "nt" or not commands:
        return False
    import ctypes
    fd, script = tempfile.mkstemp(prefix="tofusm_fw_", suffix=".cmd")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("@echo off\r\n")
        for c in commands:
            f.write(c + " >nul 2>&1\r\n")
        f.write('del "%~f0"\r\n')  # the script removes itself
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f'/c "{script}"', None, 0)
    return rc > 32


# --------------------------------------------------------------------------- #
# SteamCMD progress
# --------------------------------------------------------------------------- #
_PROGRESS_RE = re.compile(r"(?:Update state \(0x[0-9a-fA-F]+\)|Tofu:|BaoBun:) ([A-Za-z0-9 ,.\-]+?), progress: ([0-9.]+)")


def parse_steamcmd_progress(text: str) -> Tuple[Optional[float], Optional[str]]:
    """Last (percent, phase) in SteamCMD output, e.g. (42.3, "downloading")."""
    last = None
    for m in _PROGRESS_RE.finditer(text or ""):
        last = m
    if not last:
        if "Success! App" in (text or ""):
            return 100.0, "done"
        return None, None
    return float(last.group(2)), last.group(1).strip()


# --------------------------------------------------------------------------- #
# Idle auto-stop
# --------------------------------------------------------------------------- #
class IdleTracker:
    """Decides when an empty server should be stopped.

    A server counts as idle only while its player count is known and zero;
    unknown counts (e.g. Vintage Story) never trigger an automatic stop.
    """

    def __init__(self):
        self._empty_since: Dict[str, float] = {}

    def update(self, server_id: str, players: Optional[int], idle_minutes: float,
               now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        if not idle_minutes or idle_minutes <= 0 or players is None or players > 0:
            self._empty_since.pop(server_id, None)
            return False
        since = self._empty_since.setdefault(server_id, now)
        return (now - since) >= idle_minutes * 60

    def forget(self, server_id: str):
        self._empty_since.pop(server_id, None)

# --------------------------------------------------------------------------- #
# Dashboard summary
# --------------------------------------------------------------------------- #
def _next_run(cfg: dict, last: float, now: float) -> Optional[float]:
    if not cfg or not cfg.get("enabled"):
        return None
    if cfg.get("mode") == "daily":
        best = None
        base = time.localtime(now)
        for hm in cfg.get("times") or []:
            try:
                h, m = (int(x) for x in str(hm).split(":", 1))
            except ValueError:
                continue
            t = time.mktime((base.tm_year, base.tm_mon, base.tm_mday, h, m, 0, 0, 0, -1))
            if t <= now:
                t += 86400
            best = t if best is None or t < best else best
        return best
    try:
        hours = float(cfg.get("hours", 24) or 24)
    except (TypeError, ValueError):
        hours = 24.0
    return (last or now) + hours * 3600


def dashboard_summary(manager, statuses: List[dict], base_dir) -> dict:
    """Numbers for the overview strip: online, players, CPU/RAM, next task, last backups."""
    base = Path(base_dir)
    running = [s for s in statuses if s.get("isRunning")]
    players_known = [s.get("playerCount") for s in running if s.get("playerCount") is not None]
    summary = {
        "servers": len(statuses),
        "online": len(running),
        "players": sum(players_known) if players_known else None,
        "server_cpu": round(sum((s.get("cpuPercent") or 0) for s in running), 1),
        "server_ram_mb": round(sum((s.get("memUsageMB") or 0) for s in running)),
        "system_cpu": None, "system_ram_used_mb": None, "system_ram_total_mb": None,
        "next_task": None, "last_backups": {},
    }
    if psutil is not None:
        try:
            vm = psutil.virtual_memory()
            summary["system_cpu"] = psutil.cpu_percent(None)
            summary["system_ram_used_mb"] = round((vm.total - vm.available) / 1048576)
            summary["system_ram_total_mb"] = round(vm.total / 1048576)
        except Exception:
            pass
    try:
        sched = json.loads((base / "agent_state.json").read_text(encoding="utf-8")).get("schedules", {})
    except Exception:
        sched = {}
    try:
        runs = json.loads((base / "runtime" / "schedule_runs.json").read_text(encoding="utf-8"))
    except Exception:
        runs = {}
    names = {s["id"]: s.get("name", s["id"]) for s in statuses}
    now = time.time()
    best = None
    for sid, kinds in (sched or {}).items():
        if sid not in names or not isinstance(kinds, dict):
            continue
        for kind, cfg in kinds.items():
            if not isinstance(cfg, dict) or kind == "message":
                continue
            t = _next_run(cfg, float(runs.get(sid + "|" + kind, 0) or 0), now)
            if t and (best is None or t < best[0]):
                best = (t, kind, names[sid])
    if best:
        summary["next_task"] = {"at": best[0], "kind": best[1], "server": best[2]}
    for s in statuses:
        try:
            b = manager.list_server_backups(s["id"])
        except Exception:
            b = []
        if b:
            summary["last_backups"][s["id"]] = os.path.getmtime(b[0]["path"])
    return summary
