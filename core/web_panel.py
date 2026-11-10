"""BaoBun web panel: control servers from any authorised PC with a browser.

Runs inside the background agent. Safe defaults:
  * Only answers requests from this PC and from Tailscale addresses
    (100.64.0.0/10, fd7a:115c:a1e0::/48), plus - only if "Allow my home
    network" is on - private LAN addresses (192.168.x, 10.x, 172.16-31.x).
    The internet always gets "403".
  * Sign-in is "Log in with Discord" (core.web_auth); every action is checked
    against the user's role and written to the audit log as "web:<name>".
  * Server programs, folders and BaoBun's own settings (Steam login, tokens)
    can't be changed from the web: servers are added from game presets only.

Traffic inside Tailscale is already encrypted end to end.
"""

from __future__ import annotations

import html
import ipaddress
import json
import os
import shutil
import subprocess
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

from core.paths import bundle_dir
from core.web_auth import DiscordAuth

DEFAULT_PORT = 8765
ALLOWED_NETS = [ipaddress.ip_network(n) for n in
                ("127.0.0.0/8", "::1/128", "100.64.0.0/10", "fd7a:115c:a1e0::/48")]
# "Allow my home network": private LAN ranges (never the internet). Off by default.
LAN_NETS = [ipaddress.ip_network(n) for n in
            ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fe80::/10", "fc00::/7")]
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def client_allowed(addr: str, allow_lan: bool = False) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        nets = ALLOWED_NETS + (LAN_NETS if allow_lan else [])
        return any(ip in n for n in nets)
    except ValueError:
        return False


def tailscale_info() -> Tuple[Optional[str], Optional[str]]:
    """(tailscale IPv4, MagicDNS name) of this PC, or (None, None)."""
    exe = shutil.which("tailscale")
    if not exe and os.name == "nt":
        cand = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
        exe = str(cand) if cand.is_file() else None
    if exe:
        try:
            out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True,
                                 timeout=10, creationflags=_NO_WINDOW)
            data = json.loads(out.stdout or "{}")
            me = data.get("Self") or {}
            ips = [i for i in me.get("TailscaleIPs") or [] if ":" not in i]
            return (ips[0] if ips else None), ((me.get("DNSName") or "").rstrip(".") or None)
        except Exception:
            pass
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                try:
                    if ipaddress.ip_address(a.address) in ipaddress.ip_network("100.64.0.0/10"):
                        return a.address, None
                except ValueError:
                    continue
    except Exception:
        pass
    return None, None


def suggested_panel_url(port: int = DEFAULT_PORT, allow_lan: bool = False) -> Optional[str]:
    ip, dns = tailscale_info()
    host = dns or ip
    if not host and allow_lan:
        try:
            from core.network import lan_ips
            host = (lan_ips() or [None])[0]
        except Exception:
            host = None
    return "http://%s:%d" % (host, port) if host else None


def _mod_img_hosts():
    """Mod-site image hosts the mod browser may show (see core.mod_browse.safe_img)."""
    from core.mod_browse import IMG_HOSTS
    return IMG_HOSTS


def _page(title: str, body: str) -> bytes:
    return ("""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>%s</title><style>
body{margin:0;font-family:Segoe UI,system-ui,sans-serif;background:#0a0a0a;color:#e8e0d0;
display:flex;min-height:100vh;align-items:center;justify-content:center}
.box{background:#121212;border:1px solid #3d3d3d;border-radius:16px;padding:36px 40px;max-width:440px;text-align:center}
h1{color:#e6c87e;font-size:22px;margin:0 0 8px}p{color:#9a8a70;line-height:1.5}
a.btn{display:inline-block;margin-top:14px;background:#5865F2;color:#fff;text-decoration:none;
padding:12px 22px;border-radius:10px;font-weight:600}ul{text-align:left;color:#fbbf24}
</style></head><body><div class="box">%s</div></body></html>""" % (title, body)).encode("utf-8")


class WebPanel:
    def __init__(self, engine, base_dir, log=print):
        self.engine = engine
        self.mgr = engine.manager
        self.base_dir = Path(base_dir)
        self.auth = DiscordAuth(base_dir)
        self.log = log
        self._httpd = None
        self.port = DEFAULT_PORT
        self.url = None

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> Optional[str]:
        if self.mgr.get_setting("web_panel_enabled", False) is not True:
            return None
        try:
            self.port = int(self.mgr.get_setting("web_panel_port", DEFAULT_PORT) or DEFAULT_PORT)
        except (TypeError, ValueError):
            self.port = DEFAULT_PORT
        panel = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "BaoBun"

            def log_message(self, *a):
                pass

            def handle_one_request(self):
                try:
                    super().handle_one_request()
                except Exception as e:  # never let one request kill the panel
                    panel.log(f"[WARN] web panel request failed: {e}")

            def do_GET(self):
                panel._route(self, "GET")

            def do_POST(self):
                panel._route(self, "POST")

        bind = "::" if self._ipv6_ok() else "0.0.0.0"
        server_cls = ThreadingHTTPServer
        if bind == "::":
            import socket

            class DualStack(ThreadingHTTPServer):
                address_family = socket.AF_INET6

                def server_bind(self):
                    try:
                        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                    except Exception:
                        pass
                    super().server_bind()
            server_cls = DualStack
        self._httpd = server_cls((bind, self.port), Handler)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True, name="web-panel").start()
        cfg = self.auth.config()
        self.allow_lan = self.mgr.get_setting("web_panel_allow_lan", False) is True
        self.url = (cfg.get("panel_url") or suggested_panel_url(self.port, self.allow_lan)
                    or "http://localhost:%d" % self.port)
        self.log(f"[INFO] web panel on port {self.port} - open {self.url} ("
                 + ("home network + " if self.allow_lan else "") + "Tailscale + this PC only)")
        for p in self.auth.problems():
            self.log(f"[WARN] web panel: {p}")
        return self.url

    @staticmethod
    def _ipv6_ok() -> bool:
        import socket
        try:
            s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            s.close()
            return True
        except OSError:
            return False

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()

    # -- plumbing ----------------------------------------------------------------
    @staticmethod
    def _send(h, code, body: bytes, ctype="application/json", headers=None):
        h.send_response(code)
        h.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json")) else ""))
        h.send_header("Content-Length", str(len(body)))
        if "Cache-Control" not in (headers or {}):
            h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.send_header("X-Frame-Options", "DENY")
        h.send_header("Referrer-Policy", "same-origin")
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(body)

    def _json(self, h, code, obj, headers=None):
        self._send(h, code, json.dumps(obj, default=str).encode("utf-8"), headers=headers)

    def _redirect(self, h, where, headers=None):
        hdr = {"Location": where}
        hdr.update(headers or {})
        self._send(h, 302, b"", "text/plain", hdr)

    @staticmethod
    def _token(h) -> Optional[str]:
        raw = h.headers.get("Cookie")
        if not raw:
            return None
        c = cookies.SimpleCookie()
        try:
            c.load(raw)
        except cookies.CookieError:
            return None
        m = c.get("bb_session")
        return m.value if m else None

    # -- routing ------------------------------------------------------------------
    def _route(self, h, method):
        if not client_allowed(h.client_address[0], getattr(self, "allow_lan", False)):
            return self._send(h, 403, _page("Not allowed", "<h1>Not allowed</h1><p>This panel only "
                              "answers this PC and devices on your Tailscale network%s.</p>"
                              % ("" if getattr(self, "allow_lan", False) else
                                 " (the owner can also allow the home network in BaoBun \u2192 "
                                 "Web Panel)")), "text/html")
        url = urlparse(h.path)
        path = url.path
        sess = self.auth.session(self._token(h))

        if path == "/login":
            probs = self.auth.problems()
            if probs:
                return self._send(h, 200, _page("BaoBun", "<h1>Web panel isn't set up yet</h1><p>The owner "
                                  "needs to finish setup in BaoBun \u2192 Settings \u2192 Web Panel:</p><ul>"
                                  + "".join("<li>%s</li>" % p for p in probs) + "</ul>"), "text/html")
            return self._redirect(h, self.auth.login_url())
        if path == "/auth/callback":
            q = parse_qs(url.query)
            if "error" in q:
                return self._send(h, 200, _page("BaoBun", "<h1>Login cancelled</h1><a class='btn' href='/login'>"
                                  "Try again</a>"), "text/html")
            try:
                s = self.auth.complete_login(q.get("code", [""])[0], q.get("state", [""])[0])
            except PermissionError as e:
                return self._send(h, 403, _page("BaoBun", "<h1>No access</h1><p>%s</p><a class='btn' "
                                  "href='/login'>Try again</a>" % html.escape(str(e))), "text/html")
            except Exception as e:
                return self._send(h, 500, _page("BaoBun", "<h1>Login failed</h1><p>%s</p>" % html.escape(str(e)[:300])),
                                  "text/html")
            self.log(f"[INFO] web panel: {s['name']} signed in ({s['role']})")
            cookie = "bb_session=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" % (s["token"], 12 * 3600)
            return self._redirect(h, "/", {"Set-Cookie": cookie})
        if path == "/logout":
            self.auth.logout(self._token(h))
            return self._redirect(h, "/", {"Set-Cookie": "bb_session=; Path=/; Max-Age=0"})

        if path in ("/logo.png", "/favicon.png"):
            for d in (self.base_dir / "assets", bundle_dir() / "assets"):
                f = d / path.lstrip("/")
                if f.is_file():
                    return self._send(h, 200, f.read_bytes(), "image/png", {"Cache-Control": "max-age=86400"})
            return self._send(h, 404, b"", "text/plain")

        if path in ("/", "/index.html"):
            if not sess:
                return self._send(h, 200, _page("BaoBun", "<h1>BaoBun Control Panel</h1><p>Sign in with the "
                                  "Discord account you use in the server's guild.</p><a class='btn' "
                                  "href='/login'>Log in with Discord</a>"), "text/html")
            return self._send(h, 200, self._index(), "text/html",
                              {"Content-Security-Policy": "default-src 'self'; img-src 'self' https://cdn.discordapp.com data: "
                               + " ".join("https://%s https://*.%s" % (h, h) for h in _mod_img_hosts()) + "; "
                               "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"})

        if not path.startswith("/api/"):
            return self._send(h, 404, b'{"error":"not found"}')
        if not sess:
            return self._json(h, 401, {"error": "Please sign in again."})
        if method == "POST" and h.headers.get("X-CSRF") != sess["csrf"]:
            return self._json(h, 403, {"error": "Security check failed - reload the page."})
        try:
            if path == "/api/me":
                return self._json(h, 200, {"name": sess["name"], "role": sess["role"],
                                           "avatar": sess["avatar"], "csrf": sess["csrf"]})
            if path == "/api/status":
                return self._json(h, 200, self._status())
            if path == "/api/diagnostics":
                return self._diagnostics_download(h, sess)
            if path == "/api/backup_download":
                q = parse_qs(url.query)
                return self._backup_download(h, sess, q.get("server_id", [""])[0], q.get("name", [""])[0])
            if path == "/api/logs":
                q = parse_qs(url.query)
                return self._json(h, 200, self._logs(q.get("server_id", [""])[0],
                                                     int(q.get("offset", ["-1"])[0] or -1)))
            if path == "/api/action" and method == "POST":
                length = int(h.headers.get("Content-Length") or 0)
                if length > 2_000_000:
                    return self._json(h, 413, {"error": "Request too large"})
                req = json.loads(h.rfile.read(length) or b"{}")
                return self._json(h, 200, self._action(sess, req.get("action", ""), req.get("params") or {}))
        except Exception as e:
            return self._json(h, 500, {"error": str(e)})
        return self._json(h, 404, {"error": "not found"})

    # -- handlers ------------------------------------------------------------------
    def _diagnostics_download(self, h, sess):
        """Build a diagnostics zip (secrets removed) and send it (admins only)."""
        if not self.auth.allowed(sess, "diagnostics.download"):
            return self._json(h, 403, {"error": "Only admins can save diagnostics."})
        from core import diagnostics
        try:
            path = diagnostics.build(self.mgr)
        except Exception as e:
            return self._json(h, 500, {"error": "Couldn't collect diagnostics: %s" % e})
        size = os.path.getsize(path)
        h.send_response(200)
        h.send_header("Content-Type", "application/zip")
        h.send_header("Content-Length", str(size))
        h.send_header("Content-Disposition", 'attachment; filename="%s"' % os.path.basename(path))
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                h.wfile.write(chunk)
        try:
            self.mgr._audit("diagnostics_download", "host", user="web:" + sess["name"])
        except Exception:
            pass
        return None

    def _backup_download(self, h, sess, server_id, name):
        """Stream one of a server's backup zips to the browser (admins only)."""
        if not self.auth.allowed(sess, "backup.download"):
            return self._json(h, 403, {"error": "Only admins can download backups."})
        if self.mgr.get_server(server_id) is None:
            return self._json(h, 404, {"error": "Server not found"})
        match = next((b for b in self.mgr.list_server_backups(server_id, include_mirror=True)
                      if b["name"] == name), None)
        if not match:
            return self._json(h, 404, {"error": "Backup not found"})
        size = os.path.getsize(match["path"])
        h.send_response(200)
        h.send_header("Content-Type", "application/zip")
        h.send_header("Content-Length", str(size))
        safe = "".join(c for c in name if c.isalnum() or c in "._-") or "backup.zip"
        h.send_header("Content-Disposition", 'attachment; filename="%s"' % safe)
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.end_headers()
        with open(match["path"], "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                h.wfile.write(chunk)
        try:
            self.mgr._audit("backup_download", server_id, user="web:" + sess["name"], backup=name)
        except Exception:
            pass
        return None

    def _index(self) -> bytes:
        for d in (self.base_dir / "ui" / "web", bundle_dir() / "ui" / "web"):
            f = d / "index.html"
            if f.is_file():
                return f.read_bytes()
        return _page("BaoBun", "<h1>Panel files missing</h1><p>ui/web/index.html was not found.</p>")

    SAFE_FIELDS = ("name", "type", "appId", "port", "queryPort", "launchArgs", "stopCommand",
                   "stopTimeout", "idleStopMinutes", "manualStart", "autoRestart", "maxRestarts",
                   "cpuAffinity", "priority", "updateBeforeStart", "folder", "clusterId", "discordVisible", "ramAlertMB", "memLimitMB", "workshopAppId", "autoUpdateWhenEmpty",
                   "mcFlavor", "mcVersion", "javaMemoryMB")

    WEB_BLOCKED_PARAMS = {"dest_dir", "keep", "user", "user_id", "directory", "executable"}

    def _status(self) -> dict:
        from core.hosting import dashboard_summary
        from core.api_layer import redact
        statuses = self.mgr.get_all_statuses()
        servers = {}
        for s in self.mgr.load_servers():
            if s.get("id"):
                d = {k: s.get(k) for k in self.SAFE_FIELDS if k in s}
                try:
                    from core.game_installers import kind
                    d["kind"] = kind(s)
                except Exception:
                    d["kind"] = None
                servers[s["id"]] = redact(d)   # hides passwords inside launch arguments
        try:
            summary = dashboard_summary(self.mgr, statuses, self.base_dir)
        except Exception:
            summary = None
        pcs = []
        try:      # servers on linked PCs (kept fresh by the Watchdog)
            from core import linked_pcs
            f = linked_pcs.fleet(self.mgr)
            if f.pcs():
                statuses = list(statuses) + f.statuses(refresh=False)
                servers.update(f.servers())
                pcs = f.state(refresh=False)
        except Exception:
            pass
        return {"statuses": statuses, "servers": servers, "summary": summary, "pcs": pcs,
                "agent": {"pid": os.getpid(), "time": time.time()}}

    def _logs(self, server_id: str, offset: int) -> dict:
        from core.api_layer import console_tail
        if str(server_id).startswith("@"):        # a server on a linked PC
            from core import linked_pcs
            try:
                return linked_pcs.fleet(self.mgr).call(server_id, "logs.since", {"offset": offset}, timeout=10)
            except Exception as e:
                return {"error": str(e)}
        return console_tail(self.mgr, server_id, offset)

    def _action(self, sess: dict, action: str, params: dict) -> dict:
        if not self.auth.allowed(sess, action):
            return {"success": False, "error": "Your role (%s) can't do that." % sess["role"]}
        user = "web:" + sess["name"]
        # Parameters a browser must never choose (where backups go, who did it).
        params = {k: v for k, v in params.items() if k not in self.WEB_BLOCKED_PARAMS}
        if action == "activity.list":
            db = self.engine.db
            rows = db.get_audit_log(limit=int(params.get("limit", 30)), target_type="server") if db else []
            names = {s["id"]: s.get("name") for s in self.mgr.load_servers()}
            for r in rows:
                r["server"] = names.get(r.get("target_id"), r.get("target_id"))
                r.pop("details", None)
            return {"success": True, "data": {"activity": rows}}
        resp = self.engine.api.process(action, dict(params, user=user), user_id=user)
        return resp.to_dict()
