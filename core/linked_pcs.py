"""One panel, several PCs ("Linked PCs").

Every PC that hosts servers runs its own BaoBun and Watchdog - servers always run
on the PC they're installed on. One BaoBun (the desktop window, its web panel and
its Discord bot) can also show and control the servers on other PCs:

* On the other PC: Settings > Linked PCs > "Let another BaoBun control this PC".
  Its Watchdog then answers on port 8792, from the home network and Tailscale
  only, and shows a pairing code like  baobun://192.168.1.50:8792/Xy3...  (address
  + a secret key). "Make a new code" changes the key and cuts off old links.
* On the main PC: Add a PC, paste the code. The key is kept encrypted.

Requests are signed with the key (HMAC-SHA256 over a timestamp, a one-time
number and the request), so the key never travels over the network and a
recorded request can't be replayed. A linked PC may do what a web-panel admin
may do on that PC (start / stop / restart, console, backups, players, updates,
settings forms ...), with the same restrictions (no program paths, no file
downloads). Each PC keeps its own audit log of what the link did.

Remote servers get ids like "@<pc id>/<server id>" in the combined lists.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple

DEFAULT_PORT = 8792
MAX_SKEW = 120                 # seconds between the two PCs' clocks (signed requests)
CODE_RX = re.compile(r"^\s*baobun://(?P<addr>[^/\s]+)/(?P<key>[A-Za-z0-9_-]{20,})\s*$")
BLOCKED_PARAMS = {"dest_dir", "keep", "user", "user_id", "directory", "executable"}
NOT_OVER_LINK = {"backup.download", "diagnostics.download", "servers.bring", "servers.bring_scan",
                 "servers.bring_job", "host.settings_save", "agent.update_now", "discord.reload", "fleet.status"}


# --------------------------------------------------------------------------- #
# signing
# --------------------------------------------------------------------------- #
def sign(key: str, ts: str, nonce: str, body: bytes) -> str:
    msg = ts.encode() + b"\n" + nonce.encode() + b"\n" + body
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def parse_code(code: str) -> Tuple[str, str]:
    """(address "host:port", key) from a pairing code; ValueError if it isn't one."""
    m = CODE_RX.match(code or "")
    if not m:
        raise ValueError("That isn't a BaoBun pairing code (it starts with baobun://).")
    addr = m.group("addr")
    if ":" not in addr.rsplit("]", 1)[-1]:
        addr += ":%d" % DEFAULT_PORT
    return addr, m.group("key")


def allowed_actions() -> set:
    from core.web_auth import ROLE_PERMS
    return (set(ROLE_PERMS["admin"]) | {"link.hello"}) - NOT_OVER_LINK


# --------------------------------------------------------------------------- #
# this PC: answer a linked BaoBun
# --------------------------------------------------------------------------- #
def pc_name(mgr) -> str:
    return (mgr.get_setting("pc_name", "") or "").strip() or socket.gethostname()


def host_key(mgr, create: bool = True) -> str:
    key = mgr.get_setting("link_key", "") or ""
    if not key and create:
        key = secrets.token_urlsafe(32)
        mgr.set_setting("link_key", key)
    return key


def new_key(mgr) -> str:
    key = secrets.token_urlsafe(32)
    mgr.set_setting("link_key", key)
    return key


def host_config(mgr) -> dict:
    try:
        port = int(mgr.get_setting("link_port", DEFAULT_PORT) or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return {"enabled": mgr.get_setting("link_enabled", False) is True, "port": port}


def addresses() -> List[Tuple[str, str]]:
    """[(label, address)] other PCs could use to reach this one."""
    out = []
    try:
        from core.web_panel import tailscale_info
        ts_ip, ts_name = tailscale_info()
        if ts_name:
            out.append(("Tailscale", ts_name))
        if ts_ip:
            out.append(("Tailscale", ts_ip))
    except Exception:
        pass
    try:
        from core.network import lan_ips
        for ip in lan_ips():
            out.append(("Home network", ip))
    except Exception:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            out.append(("Home network", s.getsockname()[0]))
            s.close()
        except Exception:
            pass
    out.append(("PC name", socket.gethostname()))
    seen, uniq = set(), []
    for lbl, a in out:
        if a and a not in seen:
            seen.add(a)
            uniq.append((lbl, a))
    return uniq


def pairing_code(mgr, address: Optional[str] = None) -> str:
    cfg = host_config(mgr)
    addr = address or (addresses() or [("", socket.gethostname())])[0][1]
    return "baobun://%s:%d/%s" % (addr, cfg["port"], host_key(mgr))


def hello(mgr) -> dict:
    """What a linked BaoBun sees about this PC."""
    from core.version import VERSION
    statuses = mgr.get_all_statuses()
    games = {}
    try:
        from core.game_catalog import GAME_PRESETS, game_label
        for s in mgr.load_servers():
            games[s["id"]] = game_label(s, GAME_PRESETS)
    except Exception:
        pass
    keep = ("id", "name", "state", "isRunning", "playerCount", "maxPlayers", "players", "cpuPercent",
            "memUsageMB", "uptimeSeconds", "updateAvailable", "crashHelp", "exitCode", "installed")
    out = []
    for st in statuses:
        d = {k: st.get(k) for k in keep if k in st}
        d["game"] = games.get(st.get("id"), "")
        out.append(d)
    servers = {}
    try:
        from core.web_panel import WebPanel
        from core.api_layer import redact
        from core.game_installers import kind
        for s in mgr.load_servers():
            if s.get("id"):
                d = {k: s.get(k) for k in WebPanel.SAFE_FIELDS if k in s}
                try:
                    d["kind"] = kind(s)
                except Exception:
                    d["kind"] = None
                servers[s["id"]] = redact(d)
    except Exception:
        pass
    return {"pc": pc_name(mgr), "version": VERSION, "statuses": out, "servers": servers, "time": time.time()}


class LinkServer:
    """The agent's listener for linked BaoBuns."""

    def __init__(self, mgr, api, port: int = DEFAULT_PORT, log=print):
        self.mgr, self.api, self.port, self.log = mgr, api, port, log
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._nonces: Dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, client_ip: str, headers, body: bytes, now: Optional[float] = None) -> Optional[str]:
        """None when the request may run, else why not."""
        from core.web_panel import client_allowed
        now = time.time() if now is None else now
        if not client_allowed(client_ip, allow_lan=True):
            return "not from the home network or Tailscale"
        key = host_key(self.mgr, create=False)
        if not key:
            return "no key"
        ts, nonce, sig = headers.get("X-BaoBun-Ts", ""), headers.get("X-BaoBun-Nonce", ""), \
            headers.get("X-BaoBun-Sig", "")
        try:
            if abs(now - float(ts)) > MAX_SKEW:
                return "the clocks of the two PCs differ too much (or an old request)"
        except ValueError:
            return "unsigned"
        if not nonce or len(nonce) > 64 or not hmac.compare_digest(sign(key, ts, nonce, body), sig):
            return "wrong key"
        with self._lock:
            for n in [n for n, t in self._nonces.items() if now - t > MAX_SKEW * 2]:
                del self._nonces[n]
            if nonce in self._nonces:
                return "replayed request"
            self._nonces[nonce] = now
        return None

    def handle(self, payload: dict, who: str) -> dict:
        action = str(payload.get("action") or "")
        params = {k: v for k, v in (payload.get("params") or {}).items() if k not in BLOCKED_PARAMS}
        if action not in allowed_actions():
            return {"success": False, "error": "A linked PC can't do %s" % action}
        if action == "link.hello":
            return {"success": True, "data": hello(self.mgr)}
        if str(params.get("server_id") or "").startswith("@"):
            return {"success": False, "error": "Links don't chain through another PC"}
        user = "link:%s" % re.sub(r"[^\w .-]", "", str(who or "?"))[:40]
        return self.api.process(action, dict(params, user=user), user_id=user).to_dict()

    def start(self):
        srv = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _reply(self, code, payload):
                body = json.dumps(payload, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path != "/link":
                    return self._reply(404, {"success": False, "error": "not found"})
                n = int(self.headers.get("Content-Length") or 0)
                if n > 1_000_000:
                    return self._reply(413, {"success": False, "error": "too big"})
                body = self.rfile.read(n)
                why = srv.check(self.client_address[0], self.headers, body)
                if why:
                    srv.log("[WARN] linked-PC request from %s refused: %s" % (self.client_address[0], why))
                    return self._reply(403, {"success": False, "error": "Refused: " + why})
                try:
                    payload = json.loads(body.decode() or "{}")
                except ValueError:
                    return self._reply(400, {"success": False, "error": "bad request"})
                self._reply(200, srv.handle(payload, self.headers.get("X-BaoBun-From", "")))

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True, name="linked-pcs").start()
        self.log("[INFO] linked PCs: listening on port %d (home network and Tailscale)" % self.port)

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def firewall_commands(port: int) -> List[str]:
    name = "BaoBun linked PCs"
    return ['netsh advfirewall firewall delete rule name="%s"' % name,
            'netsh advfirewall firewall add rule name="%s" dir=in action=allow protocol=TCP localport=%d '
            'remoteip=LocalSubnet,100.64.0.0/10 profile=private,domain' % (name, port)]


# --------------------------------------------------------------------------- #
# the main PC: talk to linked PCs
# --------------------------------------------------------------------------- #
class LinkClient:
    def __init__(self, address: str, key: str, me: str = ""):
        self.address, self.key, self.me = address, key, me

    def call(self, action: str, params: Optional[dict] = None, timeout: float = 15) -> dict:
        body = json.dumps({"action": action, "params": params or {}}).encode()
        ts, nonce = str(int(time.time())), secrets.token_hex(12)
        req = urllib.request.Request("http://%s/link" % self.address, data=body, method="POST", headers={
            "Content-Type": "application/json", "X-BaoBun-Ts": ts, "X-BaoBun-Nonce": nonce,
            "X-BaoBun-Sig": sign(self.key, ts, nonce, body), "X-BaoBun-From": self.me[:40]})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode() or "{}")
            except Exception:
                return {"success": False, "error": "HTTP %s" % e.code}
        except (urllib.error.URLError, OSError, socket.timeout) as e:
            reason = getattr(e, "reason", e)
            return {"success": False, "error": "Can't reach %s (%s) - is it on, with BaoBun's Watchdog running?"
                    % (self.address, reason), "offline": True}


def _safe_id(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:24] or "pc"


class Fleet:
    """The linked PCs this BaoBun shows (setting "linked_pcs": [{id, name, address, key_protected}])."""

    def __init__(self, mgr):
        self.mgr = mgr
        self._cache: Dict[str, dict] = {}
        self._lock = threading.Lock()

    # ---- the list ----
    def _raw(self) -> List[dict]:
        v = self.mgr.get_setting("linked_pcs", []) or []
        return [p for p in v if isinstance(p, dict) and p.get("id")]

    def pcs(self) -> List[dict]:
        """[{id, name, address}] - no keys."""
        return [{"id": p["id"], "name": p.get("name") or p["id"], "address": p.get("address")} for p in self._raw()]

    def _client(self, pcid: str) -> LinkClient:
        p = next((x for x in self._raw() if x["id"] == pcid), None)
        if p is None:
            raise KeyError("No linked PC %s" % pcid)
        from core.secrets import unprotect
        return LinkClient(p["address"], unprotect(p["key_protected"]), pc_name(self.mgr))

    def test(self, code: str) -> dict:
        addr, key = parse_code(code)
        r = LinkClient(addr, key, pc_name(self.mgr)).call("link.hello", timeout=8)
        if not r.get("success"):
            raise RuntimeError(r.get("error") or "No answer")
        return r["data"]

    def add(self, code: str, name: str = "") -> dict:
        from core.secrets import protect
        addr, key = parse_code(code)
        info = self.test(code)
        name = (name or info.get("pc") or addr.split(":")[0]).strip()[:40]
        raw = self._raw()
        if any(p.get("address") == addr for p in raw):
            raise ValueError("That PC is already linked.")
        pid, i = _safe_id(name), 2
        while any(p["id"] == pid for p in raw):
            pid, i = "%s-%d" % (_safe_id(name), i), i + 1
        raw.append({"id": pid, "name": name, "address": addr, "key_protected": protect(key)})
        self.mgr.set_setting("linked_pcs", raw)
        with self._lock:
            self._cache[pid] = {"at": time.time(), "ok": True, "data": info}
        return {"id": pid, "name": name, "address": addr, "servers": len(info.get("statuses") or [])}

    def remove(self, pcid: str) -> bool:
        raw = self._raw()
        left = [p for p in raw if p["id"] != pcid]
        self.mgr.set_setting("linked_pcs", left)
        with self._lock:
            self._cache.pop(pcid, None)
        return len(left) != len(raw)

    def rename(self, pcid: str, name: str):
        raw = self._raw()
        for p in raw:
            if p["id"] == pcid:
                p["name"] = str(name).strip()[:40] or p["name"]
        self.mgr.set_setting("linked_pcs", raw)

    # ---- what's on them ----
    def refresh(self, timeout: float = 5.0) -> Dict[str, dict]:
        pcs = self._raw()
        if not pcs:
            return {}

        def one(p):
            try:
                r = self._client(p["id"]).call("link.hello", timeout=timeout)
            except Exception as e:
                r = {"success": False, "error": str(e)}
            return p["id"], r
        with ThreadPoolExecutor(max_workers=min(8, len(pcs))) as ex:
            results = list(ex.map(one, pcs))
        now = time.time()
        with self._lock:
            for pid, r in results:
                if r.get("success"):
                    self._cache[pid] = {"at": now, "ok": True, "data": r["data"]}
                else:
                    old = self._cache.get(pid) or {}
                    self._cache[pid] = {"at": now, "ok": False, "error": r.get("error"), "data": old.get("data")}
            return dict(self._cache)

    def state(self, max_age: float = 15.0, refresh: bool = True) -> List[dict]:
        """[{id, name, ok, error, servers, version, checked}] for the PC list."""
        with self._lock:
            stale = any(now_minus(c.get("at")) > max_age for c in self._cache.values()) or \
                len(self._cache) < len(self._raw())
        if stale and refresh:
            self.refresh()
        out = []
        with self._lock:
            for p in self.pcs():
                c = self._cache.get(p["id"]) or {}
                d = c.get("data") or {}
                out.append(dict(p, ok=bool(c.get("ok")), error=c.get("error"), version=d.get("version"),
                                servers=len(d.get("statuses") or []), checked=c.get("at"),
                                running=sum(1 for s in d.get("statuses") or [] if s.get("isRunning"))))
        return out

    def statuses(self, max_age: float = 10.0, refresh: bool = True) -> List[dict]:
        """Every linked PC's servers, ids "@pc/server", with "pc" / "pcName" / "remote"."""
        if refresh:
            with self._lock:
                stale = len(self._cache) < len(self._raw()) or any(
                    now_minus(c.get("at")) > max_age for c in self._cache.values())
            if stale:
                self.refresh()
        names = {p["id"]: p["name"] for p in self.pcs()}
        out = []
        with self._lock:
            for pid, c in self._cache.items():
                if pid not in names:
                    continue
                for st in (c.get("data") or {}).get("statuses") or []:
                    d = dict(st)
                    d["id"] = "@%s/%s" % (pid, st.get("id"))
                    d["remoteId"], d["pc"], d["pcName"], d["remote"] = st.get("id"), pid, names[pid], True
                    if not c.get("ok"):
                        d.update(state="unreachable", isRunning=False, players=[], playerCount=None)
                    out.append(d)
        return out

    def servers(self) -> Dict[str, dict]:
        """Server settings (the safe ones the web panel shows) keyed by "@pc/server"."""
        names = {p["id"]: p["name"] for p in self.pcs()}
        out = {}
        with self._lock:
            for pid, c in self._cache.items():
                if pid not in names:
                    continue
                for rid, d in ((c.get("data") or {}).get("servers") or {}).items():
                    out["@%s/%s" % (pid, rid)] = dict(d, remote=True, pc=pid, pcName=names[pid])
        return out

    @staticmethod
    def split(sid: str) -> Optional[Tuple[str, str]]:
        m = re.match(r"^@([a-z0-9-]+)/(.+)$", str(sid or ""))
        return (m.group(1), m.group(2)) if m else None

    def call(self, sid: str, action: str, params: Optional[dict] = None, timeout: float = 30) -> dict:
        """Run an action for a remote server ("@pc/server"). Returns the data or raises."""
        parts = self.split(sid)
        if not parts:
            raise ValueError("Not a server on a linked PC")
        pid, rid = parts
        r = self._client(pid).call(action, dict(params or {}, server_id=rid), timeout=timeout)
        if not r.get("success"):
            raise RuntimeError(r.get("error") or "The linked PC said no")
        with self._lock:
            c = self._cache.get(pid)
            if c:
                c["at"] = 0          # look again soon
        return r.get("data") or {}


def now_minus(t) -> float:
    return time.time() - float(t or 0)


_FLEET: Dict[int, Fleet] = {}


def fleet(mgr) -> Fleet:
    """One Fleet per manager (shared by the window, web panel and Discord bot in a process)."""
    f = _FLEET.get(id(mgr))
    if f is None:
        f = _FLEET[id(mgr)] = Fleet(mgr)
    return f


class FleetManager:
    """The server manager as the Discord bot sees it when PCs are linked: the lists
    include the linked PCs' servers, and start / stop / restart / broadcast / backup
    for "@pc/server" ids go to that PC. Everything else is the normal manager."""

    def __init__(self, mgr):
        self._mgr = mgr
        self._fleet = fleet(mgr)

    def __getattr__(self, name):
        return getattr(self._mgr, name)

    def get_all_statuses(self):
        return list(self._mgr.get_all_statuses()) + self._fleet.statuses(refresh=False)

    def get_server(self, sid):
        if Fleet.split(sid):
            st = next((s for s in self._fleet.statuses(refresh=False) if s["id"] == sid), None)
            return None if st is None else {"id": sid, "name": st.get("name"), "remote": True,
                                            "pcName": st.get("pcName")}
        return self._mgr.get_server(sid)

    def _route(self, sid, action, local, *args, **params):
        if Fleet.split(sid):
            params.pop("user", None)
            return self._fleet.call(sid, action, params)
        return local(sid, *args)

    def start_server(self, sid, user="system"):
        return self._route(sid, "server.start", lambda s, *a: self._mgr.start_server(s, user))

    def stop_server(self, sid, user="system"):
        return self._route(sid, "server.stop", lambda s, *a: self._mgr.stop_server(s, user))

    def restart_server(self, sid, user="system"):
        return self._route(sid, "server.restart", lambda s, *a: self._mgr.restart_server(s, user))

    def broadcast(self, sid, message):
        if Fleet.split(sid):
            return bool(self._fleet.call(sid, "server.broadcast", {"message": message}).get("sent"))
        return self._mgr.broadcast(sid, message)


def manager_for_bots(mgr):
    """FleetManager when this PC has linked PCs, else the manager itself."""
    try:
        return FleetManager(mgr) if fleet(mgr).pcs() else mgr
    except Exception:
        return mgr
