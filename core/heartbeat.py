"""Knowing when the server PC itself is down.

If the server PC freezes, loses power or Windows hangs, Tofu on it can't tell
anyone - its Discord bot and alerts go down with it. So:

* on the server PC the agent answers a tiny "are you alive" page:
  http://<game-pc>:8791/healthz  ->  {"ok": true, "running": 3, "players": 5, ...}
  (home network and Tailscale only, nothing secret in it)
* on another PC (for example your own PC) Tofu watches that page once a minute and posts
  to the Discord alert webhook when it stops answering for 3 minutes, and again
  when it's back. It also pings the PC so the message says whether the whole PC is
  gone or just Tofu.

Settings: heartbeat_enabled (off until turned on) and heartbeat_port (8791) on the server PC;
watch_host ("SERVER-PC" or an IP, optionally ":port") on the watching PC.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

DEFAULT_PORT = 8791
FAILS_BEFORE_ALERT = 3


class HeartbeatServer:
    def __init__(self, mgr, port: int = DEFAULT_PORT, extra: Optional[Callable[[], dict]] = None):
        self.mgr = mgr
        self.port = int(port)
        self.extra = extra
        self._httpd = None

    def snapshot(self) -> dict:
        running = players = 0
        try:
            for st in self.mgr.get_all_statuses():
                if st.get("isRunning"):
                    running += 1
                    players += int(st.get("playerCount") or 0)
        except Exception:
            pass
        out = {"ok": True, "app": "Tofu", "host": socket.gethostname(), "running": running,
               "players": players, "time": int(time.time())}
        if self.extra:
            try:
                out.update(self.extra() or {})
            except Exception:
                pass
        return out

    def start(self):
        from core.web_panel import client_allowed
        hb = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if not client_allowed(self.client_address[0], allow_lan=True):
                    self.send_response(403)
                    self.end_headers()
                    return
                if self.path.split("?")[0] not in ("/healthz", "/"):
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps(hb.snapshot()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), H)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True, name="heartbeat").start()
        return self.port

    def stop(self):
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# the watching side
# --------------------------------------------------------------------------- #
def target_url(host: str) -> str:
    h = (host or "").strip()
    if h.startswith("http://") or h.startswith("https://"):
        return h.rstrip("/") + ("" if h.rstrip("/").endswith("/healthz") else "/healthz")
    if ":" in h and not h.startswith("["):
        name, port = h.rsplit(":", 1)
    else:
        name, port = h, str(DEFAULT_PORT)
    return "http://%s:%s/healthz" % (name, port)


def host_name(host: str) -> str:
    from urllib.parse import urlparse
    return urlparse(target_url(host)).hostname or host


def probe(host: str, timeout: float = 5.0) -> Optional[dict]:
    try:
        req = urllib.request.Request(target_url(host), headers={"User-Agent": "TofuSM-watchdog"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
            return data if data.get("ok") else None
    except Exception:
        return None


def pc_pings(host: str) -> Optional[bool]:
    name = host_name(host)
    try:
        cmd = ["ping", "-n" if os.name == "nt" else "-c", "1", "-w" if os.name == "nt" else "-W",
               "2000" if os.name == "nt" else "2", name]
        r = subprocess.run(cmd, capture_output=True, timeout=8,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception:
        return None


QUIET_MINUTES = 20


class HostWatch:
    """Up/down state for one watched PC. step() returns a message to post, or None."""

    def _planned(self, now: float) -> bool:
        last = self.last or {}
        if last.get("nightly_running"):
            return now - (self.down_since or now) < QUIET_MINUTES * 60
        nxt = last.get("next_nightly")
        try:
            return bool(nxt) and float(nxt) - 120 <= now <= float(nxt) + QUIET_MINUTES * 60
        except (TypeError, ValueError):
            return False

    def __init__(self, host: str, fails_before_alert: int = FAILS_BEFORE_ALERT):
        self.host = host
        self.need = max(1, int(fails_before_alert))
        self.fails = 0
        self.down_since = None
        self.alerted = False
        self.last = None

    def step(self, result: Optional[dict], pings: Optional[bool] = None, now: Optional[float] = None):
        now = time.time() if now is None else now
        name = host_name(self.host)
        if result:
            self.last = result
            msg = None
            if self.alerted:
                mins = int((now - (self.down_since or now)) // 60)
                msg = ("ok", "\U0001F7E2 %s is back" % name,
                       "Tofu on %s answers again after about %d minute%s. %d server%s running."
                       % (name, mins, "" if mins == 1 else "s", result.get("running", 0),
                          "" if result.get("running", 0) == 1 else "s"))
            self.fails, self.down_since, self.alerted = 0, None, False
            return msg
        self.fails += 1
        if self.down_since is None:
            self.down_since = now
        if self._planned(now):
            return None     # the nightly restart: it's meant to be away for a few minutes
        if self.fails >= self.need and not self.alerted:
            self.alerted = True
            if pings:
                text = ("The PC answers pings, but Tofu on it doesn't. Tofu may have crashed or been closed - "
                        "the game servers may still be running. Check the PC or restart Tofu there.")
            elif pings is False:
                text = ("The PC doesn't answer at all - it may be off, frozen, restarting or off the network. "
                        "Game servers on it are down too.")
            else:
                text = "Tofu on it stopped answering."
            return ("down", "\U0001F534 %s is not answering" % name,
                    text + " (Checked from %s.)" % socket.gethostname())
        return None


def watch_loop(get_host, notify: Callable[[str, str, str], None], stop: threading.Event,
               interval: float = 60.0, log: Callable[[str], None] = print):
    """Run on the watching PC. get_host() -> host to watch ("" = nothing, re-read every
    minute so the setting can change). notify(level, title, text)."""
    w, current = None, None
    while not stop.wait(interval):
        host = (get_host() or "").strip()
        if host != current:
            current = host
            w = HostWatch(host) if host else None
            if host:
                log("[INFO] watching %s for outages" % target_url(host))
        if w is None:
            continue
        res = probe(host)
        pings = None if res else pc_pings(host)
        msg = w.step(res, pings)
        if msg:
            log("[WATCH] %s - %s" % (msg[1], msg[2]))
            try:
                notify(*msg)
            except Exception as e:
                log("[WARN] watchdog alert failed: %s" % e)


def firewall_commands(port: int = DEFAULT_PORT):
    name = "Tofu watchdog page"
    return ['netsh advfirewall firewall delete rule name="%s"' % name,
            'netsh advfirewall firewall add rule name="%s" dir=in action=allow protocol=TCP localport=%d '
            'remoteip=LocalSubnet,100.64.0.0/10 profile=any' % (name, int(port))]
