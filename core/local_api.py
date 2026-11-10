"""Localhost JSON API so the GUI (and anything else) can drive the background agent.

The agent listens on 127.0.0.1 only, on a random port, and requires a random
token. Both are written to runtime/agent.json, which only local users who can
read the BaoBun folder can see.

Request:  POST /api   {"action": "server.start", "params": {...}}
          header  X-BaoBun-Token: <token>
Response: the ApiResponse dict ({"success": bool, "data": ..., "error": ...})
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from core.paths import atomic_write_json

ENDPOINT_FILE = "agent.json"


def _endpoint_path(base_dir) -> Path:
    return Path(base_dir) / "runtime" / ENDPOINT_FILE


class LocalApiServer:
    def __init__(self, api_layer, base_dir, host: str = "127.0.0.1", port: int = 0):
        self.api = api_layer
        self.base_dir = Path(base_dir)
        self.token = secrets.token_urlsafe(24)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.host = host
        self.port = port

    def start(self) -> int:
        api = self.api
        token = self.token

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # keep the agent log quiet
                pass

            def _reply(self, code: int, payload: Dict[str, Any]):
                body = json.dumps(payload, default=str).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path != "/api":
                    return self._reply(404, {"success": False, "error": "not found"})
                if not secrets.compare_digest(self.headers.get("X-BaoBun-Token", ""), token):
                    return self._reply(403, {"success": False, "error": "bad token"})
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    req = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    return self._reply(400, {"success": False, "error": "bad request"})
                params = req.get("params") or {}
                resp = api.process(req.get("action", ""), params, user_id=params.get("user") or "gui")
                return self._reply(200, resp.to_dict())

            def do_GET(self):
                if self.path == "/health":
                    return self._reply(200, {"success": True, "data": {"pid": os.getpid()}})
                return self._reply(404, {"success": False, "error": "not found"})

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="local-api")
        self._thread.start()
        atomic_write_json(_endpoint_path(self.base_dir), {
            "pid": os.getpid(), "host": self.host, "port": self.port,
            "token": self.token, "started_at": time.time(),
        })
        return self.port

    def stop(self):
        try:
            p = _endpoint_path(self.base_dir)
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                p.unlink()
        except Exception:
            pass
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()


class AgentClient:
    def __init__(self, host: str, port: int, token: str, pid: Optional[int] = None):
        self.host, self.port, self.token, self.pid = host, int(port), token, pid

    @classmethod
    def discover(cls, base_dir) -> Optional["AgentClient"]:
        """Return a client if a live agent is listening, else None."""
        try:
            data = json.loads(_endpoint_path(base_dir).read_text(encoding="utf-8"))
        except Exception:
            return None
        from core.process_manager import pid_alive
        if not pid_alive(data.get("pid")):
            return None
        client = cls(data.get("host", "127.0.0.1"), data["port"], data["token"], data.get("pid"))
        return client if client.ping() else None

    def ping(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://{self.host}:{self.port}/health", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def call(self, action: str, params: Optional[Dict[str, Any]] = None, timeout: float = 30) -> Dict[str, Any]:
        body = json.dumps({"action": action, "params": params or {}}).encode("utf-8")
        req = urllib.request.Request(
            f"http://{self.host}:{self.port}/api", data=body, method="POST",
            headers={"Content-Type": "application/json", "X-BaoBun-Token": self.token},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"success": False, "error": f"agent HTTP {e.code}"}
        except Exception as e:
            return {"success": False, "error": f"Could not reach the Watchdog (BaoBun's background service): {e}"}
