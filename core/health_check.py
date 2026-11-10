"""Server health checks ("why won't it start?" diagnostics).

Checks run against the shared ServerManager so results match what the GUI and
agent see. Each check returns {"check", "passed", "status", "message"}.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _result(check: str, passed: bool, status: str, message: str = "", **extra) -> Dict[str, Any]:
    r = {"check": check, "passed": passed, "status": status, "message": message}
    r.update(extra)
    return r


class HealthCheck:
    def __init__(self, manager=None, servers_path=None, plugins_dir=None):
        # servers_path/plugins_dir are accepted for backwards compatibility.
        self.mgr = manager
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    def check_executable(self, srv: Dict[str, Any]) -> Dict[str, Any]:
        directory = srv.get("directory") or ""
        if not directory:
            return _result("executable", False, "fail", "No install folder is set.")
        if directory.startswith("\\\\") and not os.path.isdir(directory):
            return _result("executable", False, "fail",
                           f"The network folder {directory} is not reachable from this PC.")
        if not os.path.isdir(directory):
            return _result("executable", False, "fail", f"Install folder does not exist: {directory}")
        try:
            exe, _ = self.mgr._resolve_executable(srv)
        except Exception as e:
            return _result("executable", False, "fail", str(e))
        if not os.path.isfile(exe):
            hint = " Click Install Game first." if srv.get("appId") else ""
            return _result("executable", False, "fail", f"Server executable not found: {exe}.{hint}")
        return _result("executable", True, "ok", exe, path=exe)

    def check_ports(self, srv: Dict[str, Any], running: bool) -> List[Dict[str, Any]]:
        """For a stopped server, a port that is already in use means another program has it."""
        out = []
        ports = self.mgr.get_server_ports(srv["id"])
        if srv.get("port") and not any(p["port"] == srv["port"] for p in ports):
            ports.append({"name": "Game", "port": int(srv["port"])})
        conflicts = {port: names for port, names in self.mgr.detect_port_conflicts()}
        for p in ports:
            port = p["port"]
            if port in conflicts:
                out.append(_result("port", False, "conflict",
                                   f"{p['name']} port {port} is also used by: "
                                   + ", ".join(n for n in conflicts[port] if n != srv.get("name")),
                                   port=port))
                continue
            if running:
                continue
            in_use = self._port_in_use(port)
            out.append(_result("port", not in_use, "in_use" if in_use else "free",
                               (f"{p['name']} port {port} is already in use by another program."
                                if in_use else f"{p['name']} port {port} is free."), port=port))
        return out

    @staticmethod
    def _port_in_use(port: int) -> bool:
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            s = socket.socket(socket.AF_INET, kind)
            try:
                s.bind(("0.0.0.0", int(port)))
            except OSError:
                return True
            finally:
                s.close()
        return False

    def check_disk_space(self, srv: Dict[str, Any], min_free_gb: float = 5.0) -> Dict[str, Any]:
        directory = srv.get("directory") or ""
        target = directory if os.path.isdir(directory) else (Path(directory).anchor or ".")
        try:
            usage = shutil.disk_usage(target)
        except Exception as e:
            return _result("disk_space", True, "unknown", f"Could not read disk space: {e}")
        free_gb = usage.free / (1024 ** 3)
        ok = free_gb >= min_free_gb
        return _result("disk_space", ok, "ok" if ok else "low",
                       f"{free_gb:.1f} GB free" + ("" if ok else " - updates and backups may fail."),
                       free_gb=round(free_gb, 1))

    def check_firewall(self) -> Dict[str, Any]:
        if os.name != "nt":
            return _result("firewall", True, "unknown", "Not Windows")
        try:
            out = subprocess.run(["netsh", "advfirewall", "show", "allprofiles", "state"],
                                 capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
            active = "on" in (out.stdout or "").lower()
            return _result("firewall", True, "active" if active else "inactive",
                           "Windows Firewall is on - make sure the server's ports are allowed."
                           if active else "Windows Firewall is off.")
        except Exception as e:
            return _result("firewall", True, "unknown", str(e))

    # ------------------------------------------------------------------
    def check_server(self, server_id: str, *_legacy_args) -> Dict[str, Any]:
        srv = self.mgr.get_server(server_id) if self.mgr else None
        if not srv:
            return {"server_id": server_id, "status": "fail", "checks": [],
                    "error": f"Server {server_id} not found"}
        st = next((s for s in self.mgr.get_all_statuses() if s["id"] == server_id), {})
        running = bool(st.get("isRunning"))
        checks = [self.check_executable(srv)]
        checks += self.check_ports(srv, running)
        checks.append(self.check_disk_space(srv))
        if st.get("state") == "crashed":
            checks.append(_result("process", False, "crashed",
                                  f"Last exit code {st.get('exitCode')}. Check the Logs tab."))
        else:
            checks.append(_result("process", True, st.get("state", "stopped"),
                                  f"State: {st.get('state', 'stopped')}"))
        failed = [c for c in checks if not c["passed"]]
        return {"server_id": server_id, "server_name": srv.get("name", ""),
                "status": "fail" if failed else "ok", "checks": checks,
                "problems": [c["message"] for c in failed]}

    def check_all_servers(self, *_legacy_args, **_legacy_kwargs) -> List[Dict[str, Any]]:
        if not self.mgr:
            return []
        return [self.check_server(s["id"]) for s in self.mgr.load_servers() if s.get("id")]
