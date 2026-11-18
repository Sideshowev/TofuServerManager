"""Public status page: a read-only page friends can open without logging in.

    http://<game-pc or your address>:8790/          the page (refreshes itself)
    http://<game-pc or your address>:8790/status.json

It runs on its own port, separate from the web panel, and only answers those two
addresses - there are no buttons, no logins and nothing secret on it (no
passwords, no folders, no console). Safe to forward on the router if you want it
reachable from the internet; otherwise it answers this PC, the home network and
Tailscale.

Settings: status_page_enabled (off), status_page_port (8790), status_page_public
(answer the internet too), status_address (what players type, e.g.
play.example.com - default: this PC's public IP), status_show_players (list player
names, off), status_discord_invite (a https://discord.gg/... link). A server is left
off the page when its "Show on the Discord panel" is off.
"""

from __future__ import annotations

import html
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional

DEFAULT_PORT = 8790
CACHE_SECONDS = 15
MOD_CACHE_SECONDS = 300
STATE_TEXT = {"running": "Online", "starting": "Starting", "restarting": "Restarting", "updating": "Updating",
              "stopping": "Stopping", "crashed": "Down", "stopped": "Asleep"}


def config(mgr) -> dict:
    def g(k, d=None):
        v = mgr.get_setting(k, d)
        return d if v is None else v
    try:
        port = int(g("status_page_port", DEFAULT_PORT) or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    invite = str(g("status_discord_invite", "") or "").strip()
    if not invite.startswith(("https://discord.gg/", "https://discord.com/invite/")):
        invite = ""
    return {"enabled": g("status_page_enabled", False) is True, "port": port,
            "public": g("status_page_public", False) is True,
            "address": clean_address(g("status_address", "")),
            "show_players": g("status_show_players", False) is True, "invite": invite,
            "title": str(g("status_title", "") or "").strip()[:60] or "Our game servers"}


def clean_address(v) -> str:
    """Host name or IP players type (no scheme, no spaces, nothing that could break the page)."""
    import re
    s = str(v or "").strip()
    s = re.sub(r"^[a-z]+://", "", s, flags=re.I).split("/")[0]
    return s[:120] if re.fullmatch(r"[A-Za-z0-9.\-\[\]:]+", s or "-") and s else ""


def game_label(srv: dict) -> str:
    if srv.get("arkFriendly"):
        return "ARK: %s" % srv["arkFriendly"]
    if srv.get("preset"):
        return str(srv["preset"])
    try:
        from core.game_catalog import GAME_PRESETS
        for name, p in GAME_PRESETS.items():
            if srv.get("appId") and str(p.get("appId") or "") == str(srv["appId"]):
                return name
        for name, p in GAME_PRESETS.items():
            if srv.get("type") and p.get("type") == srv["type"] and not p.get("appId"):
                return name
    except Exception:
        pass
    return str(srv.get("type") or "Game").replace("-", " ").title()


class StatusData:
    """Builds the page data, cached so a busy page can't load the server PC."""

    def __init__(self, mgr, public_ip=None, mod_lister=None):
        self.mgr = mgr
        self._public_ip = public_ip
        self._mods = mod_lister
        self._cache = (0.0, None)
        self._mod_cache: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def _host(self, cfg) -> Optional[str]:
        if cfg["address"]:
            return cfg["address"]
        try:
            if self._public_ip:
                return self._public_ip()
            from core.network import public_ip
            return public_ip()
        except Exception:
            return None

    def _mod_list(self, sid: str) -> Optional[List[dict]]:
        hit = self._mod_cache.get(sid)
        if hit and time.time() - hit[0] < MOD_CACHE_SECONDS:
            return hit[1]
        out = None
        try:
            if self._mods:
                data = self._mods(sid)
            else:
                from core.mods import ModManager
                data = ModManager(self.mgr).list(sid)
            mods = [m for m in data.get("mods") or [] if m.get("enabled", True)]
            out = [{"name": str(m.get("name") or m.get("id") or "")[:80], "version": str(m.get("version") or "")[:30],
                    "url": m.get("url") if str(m.get("url") or "").startswith("https://") else None}
                   for m in mods][:200]
        except Exception:
            out = None
        self._mod_cache[sid] = (time.time(), out)
        return out

    def snapshot(self) -> dict:
        with self._lock:
            if self._cache[1] is not None and time.time() - self._cache[0] < CACHE_SECONDS:
                return self._cache[1]
            cfg = config(self.mgr)
            host = self._host(cfg)
            try:
                statuses = {s["id"]: s for s in self.mgr.get_all_statuses()}
            except Exception:
                statuses = {}
            servers = []
            for srv in self.mgr.load_servers():
                sid = srv.get("id")
                if not sid or srv.get("discordVisible") is False:
                    continue
                st = statuses.get(sid, {})
                state = st.get("state") or ("running" if st.get("isRunning") else "stopped")
                port = None
                try:
                    from core.network import expected_ports
                    ports = expected_ports(srv, self.mgr)
                    port = ports[0]["port"] if ports else srv.get("port")
                except Exception:
                    port = srv.get("port")
                row = {"name": srv.get("name") or sid, "game": game_label(srv),
                       "state": state, "state_text": STATE_TEXT.get(state, state.title()),
                       "online": bool(st.get("isRunning")) and state == "running",
                       "players": st.get("playerCount"), "max_players": st.get("maxPlayers"),
                       "address": ("%s:%s" % (host, port)) if host and port else None,
                       "cluster": srv.get("clusterId") or None, "mods": self._mod_list(sid)}
                if cfg["show_players"] and isinstance(st.get("players"), list):
                    row["player_names"] = [str(p)[:40] for p in st["players"][:100]]
                servers.append(row)
            servers.sort(key=lambda r: (not r["online"], r["name"].lower()))
            snap = {"title": cfg["title"], "updated": int(time.time()), "servers": servers,
                    "invite": cfg["invite"] or None,
                    "online": sum(1 for s in servers if s["online"]),
                    "players": sum(int(s["players"] or 0) for s in servers if s["online"])}
            self._cache = (time.time(), snap)
            return snap


def render(snap: dict) -> bytes:
    e = html.escape
    cards = []
    for s in snap["servers"]:
        players = ""
        if s["online"] and s.get("players") is not None:
            players = "%s%s player%s" % (s["players"], ("/%s" % s["max_players"]) if s.get("max_players") else "",
                                         "" if s["players"] == 1 else "s")
        names = ""
        if s.get("player_names"):
            names = '<div class="names">%s</div>' % ", ".join(e(n) for n in s["player_names"])
        mods = ""
        if s.get("mods"):
            items = "".join('<li>%s%s</li>' % (
                ('<a href="%s" rel="noopener" target="_blank">%s</a>' % (e(m["url"]), e(m["name"]))) if m.get("url")
                else e(m["name"]), (' <span>%s</span>' % e(m["version"])) if m.get("version") else "")
                for m in s["mods"])
            mods = '<details><summary>%d mod%s</summary><ul>%s</ul></details>' % (
                len(s["mods"]), "" if len(s["mods"]) == 1 else "s", items)
        asleep = "" if s["online"] else (
            '<div class="hint">Asleep - ask on Discord to start it.</div>' if s["state"] == "stopped" else "")
        addr = ('<div class="addr"><code>%s</code><button onclick="cp(this)">Copy</button></div>' % e(s["address"])) \
            if s.get("address") else ""
        cards.append('<div class="card %s"><div class="top"><span class="dot"></span><b>%s</b><span class="st">%s'
                     '</span></div><div class="game">%s%s</div>%s%s%s%s%s</div>' % (
                         "on" if s["online"] else "off", e(s["name"]), e(s["state_text"]), e(s["game"]),
                         (" &middot; cluster %s" % e(s["cluster"])) if s.get("cluster") else "",
                         ('<div class="pl">%s</div>' % e(players)) if players else "", names, addr, asleep, mods))
    invite = ('<a class="discord" href="%s" rel="noopener" target="_blank">Join our Discord</a>' % e(snap["invite"])
              if snap.get("invite") else "")
    when = time.strftime("%H:%M", time.localtime(snap["updated"]))
    page = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>%(title)s</title><link rel="icon" href="/logo.png"><style>
:root{color-scheme:dark}body{margin:0;font-family:Segoe UI,system-ui,sans-serif;background:#0a0a0a;color:#e8e0d0}
.wrap{max-width:980px;margin:0 auto;padding:28px 16px 40px}header{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header img{width:44px;height:44px;border-radius:50%%}h1{color:#e6c87e;font-size:24px;margin:0;flex:1}
.sum{color:#9a8a70;margin:6px 0 22px}.discord{background:#5865F2;color:#fff;text-decoration:none;padding:9px 16px;border-radius:10px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.card{background:#121212;border:1px solid #2e2e2e;border-left:4px solid #64748b;border-radius:12px;padding:14px 16px}
.card.on{border-left-color:#4ade80}.top{display:flex;align-items:center;gap:8px}.top b{flex:1;font-size:16px}
.dot{width:9px;height:9px;border-radius:50%%;background:#64748b}.on .dot{background:#4ade80}.st{font-size:12px;color:#9a8a70}
.game{color:#9a8a70;font-size:13px;margin:3px 0 8px}.pl{font-weight:600;margin-bottom:6px}.names{font-size:13px;color:#c9bda6;margin-bottom:8px}
.addr{display:flex;gap:8px;align-items:center;margin:6px 0}.addr code{background:#1c1c1c;padding:5px 8px;border-radius:6px;flex:1;overflow:auto;white-space:nowrap}
.addr button{background:#2a2a2a;color:#e8e0d0;border:1px solid #3d3d3d;border-radius:6px;padding:5px 10px;cursor:pointer}
.hint{font-size:12px;color:#9a8a70}details{margin-top:8px;font-size:13px}summary{cursor:pointer;color:#e6c87e}
ul{margin:6px 0 0;padding-left:18px;max-height:220px;overflow:auto}li span{color:#7d7060;font-size:12px}a{color:#e6c87e}
footer{color:#5d5448;font-size:12px;margin-top:22px}</style></head><body><div class="wrap">
<header><img src="/logo.png" alt="" onerror="this.remove()"><h1>%(title)s</h1>%(invite)s</header>
<div class="sum">%(online)d of %(total)d online &middot; %(players)d playing now</div>
<div class="grid">%(cards)s</div><script>
function cp(b){var c=b.previousSibling,t=c.textContent;
if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t);}
else{var r=document.createRange();r.selectNodeContents(c);var s=getSelection();s.removeAllRanges();s.addRange(r);try{document.execCommand("copy");}catch(e){}}
b.textContent="Copied";}</script><footer>Updated %(when)s &middot; refreshes every minute &middot; Tofu</footer></div></body></html>""" % {
        "title": e(snap["title"]), "invite": invite, "online": snap["online"], "total": len(snap["servers"]),
        "players": snap["players"], "cards": "".join(cards) or "<p>No servers to show.</p>", "when": when}
    return page.encode("utf-8")


class StatusServer:
    def __init__(self, mgr, base_dir, port: int = DEFAULT_PORT, public: bool = False, data: Optional[StatusData] = None):
        self.mgr = mgr
        self.base_dir = str(base_dir)
        self.port = int(port)
        self.public = bool(public)
        self.data = data or StatusData(mgr)
        self._httpd = None

    def _logo(self) -> Optional[bytes]:
        import os
        from core.paths import bundle_dir
        for d in (os.path.join(self.base_dir, "assets"), os.path.join(str(bundle_dir()), "assets")):
            p = os.path.join(d, "logo.png")
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return f.read()
        return None

    def start(self) -> int:
        from core.web_panel import client_allowed
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype, extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                if not srv.public and not client_allowed(self.client_address[0], allow_lan=True):
                    return self._send(403, b"Not available", "text/plain")
                path = self.path.split("?")[0]
                try:
                    if path in ("/", "/index.html"):
                        return self._send(200, render(srv.data.snapshot()), "text/html; charset=utf-8",
                                          {"Content-Security-Policy": "default-src 'none'; img-src 'self'; "
                                           "style-src 'unsafe-inline'; script-src 'unsafe-inline'",
                                           "Cache-Control": "no-cache"})
                    if path == "/status.json":
                        return self._send(200, json.dumps(srv.data.snapshot()).encode(), "application/json",
                                          {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"})
                    if path in ("/logo.png", "/favicon.ico"):
                        logo = srv._logo()
                        if logo:
                            return self._send(200, logo, "image/png", {"Cache-Control": "max-age=86400"})
                except Exception:
                    return self._send(500, b"Something went wrong", "text/plain")
                return self._send(404, b"Not found", "text/plain")

            def do_POST(self):
                self._send(405, b"Read only", "text/plain")

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), H)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True, name="status-page").start()
        return self.port

    def stop(self):
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None


def firewall_commands(port: int = DEFAULT_PORT, public: bool = False) -> List[str]:
    name = "Tofu status page"
    remote = "any" if public else "LocalSubnet,100.64.0.0/10"
    return ['netsh advfirewall firewall delete rule name="%s"' % name,
            'netsh advfirewall firewall add rule name="%s" dir=in action=allow protocol=TCP localport=%d '
            'remoteip=%s profile=any' % (name, int(port), remote)]
