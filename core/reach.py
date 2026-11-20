"""Can friends connect? A plain-words answer per server, for the home screen.

There is no server outside the home to test from, so this puts together what can be
known from inside, strongest evidence first, and says how sure it is:

1. Is the server running, and listening on its game port?
2. Does Windows Firewall let it in?
3. Is the internet address shared with other homes (carrier-grade NAT)? Then no
   router setting can let friends in - the fix is Tailscale or a relay.
4. Does Steam's public server list show the server at this home's internet address?
   (Valve's list is built from the outside, so that is good evidence it's reachable.)
5. Does the server answer on the home's own internet address? Many routers can loop
   that back; when it works it is proof, when it doesn't it proves nothing.
6. Did the router accept a forwarding rule for the port (UPnP)?

Result: {"verdict": "ok" | "likely" | "blocked" | "cgnat" | "off" | "unknown" | "relay",
         "title", "detail", "address", "invite", "fix", "checked"}
Results are kept for a while (CACHE_SECONDS) so the home screen can ask every refresh.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
import time
import urllib.request
from typing import Dict, Optional

CACHE_SECONDS = 600
_cache: Dict[str, dict] = {}
_lock = threading.Lock()
_running: set = set()


def cached(server_id: str) -> Optional[dict]:
    with _lock:
        return _cache.get(server_id)


def forget(server_id: str) -> None:
    with _lock:
        _cache.pop(server_id, None)


def stale(server_id: str, running: bool, now: Optional[float] = None) -> bool:
    """True when a fresh check is worth doing (none yet, too old, or the server started/stopped)."""
    r = cached(server_id)
    now = time.time() if now is None else now
    if not r:
        return True
    if r.get("was_running") != bool(running):
        return True
    return now - float(r.get("checked") or 0) > CACHE_SECONDS


def check_async(mgr, server_id: str, done=None) -> bool:
    """Run check() in the background (one at a time per server). False if one is already running."""
    with _lock:
        if server_id in _running:
            return False
        _running.add(server_id)

    def work():
        try:
            r = check(mgr, server_id)
        except Exception as e:
            r = {"verdict": "unknown", "title": "Couldn't check", "detail": str(e)[:200], "checked": time.time()}
        finally:
            with _lock:
                _running.discard(server_id)
        if done:
            try:
                done(server_id, r)
            except Exception:
                pass
    threading.Thread(target=work, daemon=True, name="reach-" + str(server_id)).start()
    return True


# --------------------------------------------------------------------------- #
# the pieces
# --------------------------------------------------------------------------- #
def _shared_address(ip: str) -> bool:
    """100.64.0.0/10 (carrier-grade NAT) or a private range: not a real internet address."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a in ipaddress.ip_network("100.64.0.0/10") or a.is_private


def router_external_ip(gateway=None) -> Optional[str]:
    """The internet address the router itself has (UPnP), or None if it doesn't say."""
    from core import network
    gw = gateway or network.find_gateway()
    if not gw:
        return None
    try:
        xml = network._soap(gw[0], gw[1], "GetExternalIPAddress", {})
    except Exception:
        return None
    import re
    m = re.search(r"<NewExternalIPAddress>([^<]+)</NewExternalIPAddress>", xml)
    return m.group(1).strip() if m else None


def router_forwards(port: int, proto: str, lan_ip: str, gateway=None) -> Optional[bool]:
    """True when the router has a forwarding rule for port -> this PC (UPnP); None if it can't say."""
    from core import network
    gw = gateway or network.find_gateway()
    if not gw:
        return None
    try:
        xml = network._soap(gw[0], gw[1], "GetSpecificPortMappingEntry",
                            {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": proto})
    except Exception:
        return False          # routers answer "no such entry" with an error
    import re
    m = re.search(r"<NewInternalClient>([^<]+)</NewInternalClient>", xml)
    return bool(m) and (not lan_ip or m.group(1).strip() == lan_ip)


def steam_sees(public_ip: str, ports, fetch=None) -> Optional[bool]:
    """True when Steam's public server list has a server at public_ip on one of ports."""
    url = "https://api.steampowered.com/ISteamApps/GetServersAtAddress/v1/?addr=%s" % public_ip
    try:
        if fetch:
            data = fetch(url)
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "TofuServerManager"})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    servers = ((data or {}).get("response") or {}).get("servers") or []
    want = {int(p) for p in ports if p}
    for s in servers:
        try:
            gp = int(s.get("gameport") or 0)
            qp = int(str(s.get("addr", "")).rsplit(":", 1)[-1] or 0)
        except ValueError:
            continue
        if gp in want or qp in want:
            return True
    return False


def answers_at(host: str, port: int, proto: str) -> bool:
    """Does something answer at host:port? UDP: a Steam query. TCP: a connection."""
    if proto.startswith("TCP") or proto == "TCP/UDP":
        try:
            with socket.create_connection((host, int(port)), timeout=2):
                return True
        except OSError:
            if proto == "TCP":
                return False
    try:
        from core.a2s import query_info
        return bool(query_info(host, int(port), timeout=1.5))
    except Exception:
        return False


def _core_keeper_id(srv: dict) -> Optional[str]:
    exe = srv.get("executable") or ""
    d = srv.get("directory") or ""
    for folder in (os.path.dirname(os.path.join(d, exe)) if exe else d, d):
        p = os.path.join(folder, "GameID.txt")
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                gid = fh.read().strip()
            if gid:
                return gid
        except OSError:
            continue
    return None


# --------------------------------------------------------------------------- #
# the answer
# --------------------------------------------------------------------------- #
def check(mgr, server_id: str, lookups: Optional[dict] = None) -> dict:
    """Work out whether friends can connect. `lookups` replaces the network calls in tests:
    {"public_ip", "router_ip", "steam", "hairpin", "forwarded", "listening", "firewall"}."""
    from core import network
    L = lookups or {}
    srv = mgr.get_server(server_id) or {}
    st = next((s for s in mgr.get_all_statuses() if s["id"] == server_id), {}) or {}
    running = bool(st.get("isRunning"))
    out = {"checked": time.time(), "was_running": running, "address": None, "invite": None, "fix": None}
    game = srv.get("name") or "the server"

    # Core Keeper normally joins through Steam's relay with a Game ID: nothing to forward
    app = str(srv.get("appId") or "")
    if app == "1963720" and "-port" not in (srv.get("launchArgs") or "").lower():
        gid = _core_keeper_id(srv)
        out.update(verdict="relay" if gid else ("off" if not running else "unknown"),
                   title="Friends join with the Game ID" if gid else "Game ID not written yet",
                   detail=("No port forwarding needed - Core Keeper connects through Steam." if gid else
                           "Core Keeper writes its Game ID when the server has started."),
                   address=gid, invite=("Join %s in Core Keeper with Game ID: %s" % (game, gid)) if gid else None)
        return _keep(server_id, out)

    ports = network.expected_ports(srv, mgr)
    if not ports:
        out.update(verdict="unknown", title="Port unknown",
                   detail="Tofu doesn't know which port this server uses. Set it in Edit Server.", fix="edit")
        return _keep(server_id, out)
    main = ports[0]
    lans = network.lan_ips()
    lan = lans[0] if lans else None
    pub = L["public_ip"] if "public_ip" in L else network.public_ip()
    if pub:
        out["address"] = "%s:%d" % (pub, main["port"])
        out["invite"] = "Join %s: %s" % (game, out["address"])

    if not running:
        out.update(verdict="off", title="Off", detail="Start it to check whether friends can connect.")
        return _keep(server_id, out)

    listening = L.get("listening")
    if listening is None:
        lp = network.listening_ports(st.get("pid"))
        if lp:
            want = {"TCP", "UDP"} if main["proto"] == "TCP/UDP" else {main["proto"]}
            listening = bool(want & lp.get(main["port"], set()))
    if listening is False:
        out.update(verdict="unknown", title="Not listening on port %d yet" % main["port"],
                   detail="It may still be starting, or the game uses another port than Tofu expects.",
                   fix="connect")
        return _keep(server_id, out)

    if not pub:
        out.update(verdict="unknown", title="No internet address found",
                   detail="Tofu couldn't find this home's internet address - is the PC online?")
        return _keep(server_id, out)

    gateway = None if L else network.find_gateway()
    router_ip = L["router_ip"] if "router_ip" in L else (router_external_ip(gateway) if gateway else None)
    if router_ip and router_ip != pub and _shared_address(router_ip):
        out.update(verdict="cgnat", title="Your internet address is shared",
                   detail="Your internet provider shares one address between many homes, so no router "
                          "setting can let friends in. Use Tailscale (free) - friends join over it.",
                   fix="tailscale", address=None, invite=None)
        return _keep(server_id, out)

    seen = L["steam"] if "steam" in L else steam_sees(pub, [p["port"] for p in ports])
    loop = L["hairpin"] if "hairpin" in L else answers_at(pub, main["port"], main["proto"])
    if seen or loop:
        out.update(verdict="ok", title="Friends can join",
                   detail="Confirmed: %s." % ("Steam's server list sees it from the internet" if seen else
                                             "it answers on your internet address"))
        return _keep(server_id, out)

    fw = L["firewall"] if "firewall" in L else _firewall(srv)
    if fw is False:
        out.update(verdict="blocked", title="Windows Firewall is blocking it",
                   detail="Allow it through the firewall, then check again.", fix="firewall")
        return _keep(server_id, out)

    proto = "UDP" if main["proto"] in ("UDP", "TCP/UDP") else "TCP"
    fwd = L["forwarded"] if "forwarded" in L else (router_forwards(main["port"], proto, lan, gateway)
                                                    if gateway else None)
    if fwd:
        out.update(verdict="likely", title="Probably OK",
                   detail="Your router forwards port %d to this PC, but Tofu can't test from outside your "
                          "home. Ask a friend to try." % main["port"])
    elif fwd is False:
        out.update(verdict="blocked", title="Your router is blocking port %d" % main["port"],
                   detail="Forward port %d (%s) on your router to this PC%s." % (
                       main["port"], main["proto"], " (%s)" % lan if lan else ""), fix="forward")
    else:
        out.update(verdict="unknown", title="Can't tell from here",
                   detail="Tofu couldn't reach your router to check. If friends can't join, forward port %d "
                          "(%s) to this PC." % (main["port"], main["proto"]), fix="forward")
    return _keep(server_id, out)


def _firewall(srv: dict) -> Optional[bool]:
    try:
        from core.hosting import firewall_rule_exists
        return firewall_rule_exists(srv.get("name") or "")
    except Exception:
        return None


def _keep(server_id: str, result: dict) -> dict:
    with _lock:
        _cache[server_id] = result
    return result
