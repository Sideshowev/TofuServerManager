"""How players reach a server: addresses, ports, what's listening, router forwarding.

  connect_info(mgr, server_id)  -> everything the "How players connect" panel shows
  upnp_forward(ports, lan_ip)   -> ask the router to forward ports (UPnP IGD)

The public IP comes from api.ipify.org (cached for 15 minutes).
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

# Ports a game usually needs, relative to its game port (offset, protocol, label),
# plus fixed extras. Keyed by Steam app ID of the dedicated server.
PORT_HINTS = {
    "896660": [(0, "UDP", "Game"), (1, "UDP", "Game + 1"), (2, "UDP", "Game + 2")],            # Valheim
    "2430930": [(0, "UDP", "Game"), ("27015", "UDP", "Query")],                                  # ARK SA
    "376030": [(0, "UDP", "Game"), (1, "UDP", "Raw UDP"), ("27015", "UDP", "Query")],            # ARK SE
    "380870": [(0, "UDP", "Game"), (1, "UDP", "Direct")],                                        # Project Zomboid
    "2394010": [(0, "UDP", "Game")],                                                             # Palworld
    "2278520": [(0, "UDP", "Game"), (1, "UDP", "Query")],                                        # Enshrouded
    "294420": [(0, "TCP/UDP", "Game"), (1, "UDP", "Game + 1"), (2, "UDP", "Game + 2")],          # 7 Days to Die
    "258550": [(0, "UDP", "Game"), ("28017", "UDP", "Query")],                                   # Rust
    "1690800": [(0, "TCP/UDP", "Game"), ("8888", "TCP", "Reliable messaging")],                  # Satisfactory
    "233780": [(0, "UDP", "Game"), (1, "UDP", "Steam query"), (2, "UDP", "Steam"), (3, "UDP", "BattlEye")],  # Arma 3
    "223350": [(0, "UDP", "Game"), (1, "UDP", "Steam"), (2, "UDP", "Steam query"), ("27016", "UDP", "Query")],  # DayZ
    "1874900": [(0, "UDP", "Game"), ("17777", "UDP", "A2S query")],                              # Arma Reforger
    "443030": [(0, "UDP", "Game"), (1, "UDP", "Raw UDP"), ("27015", "UDP", "Query")],            # Conan
    "298740": [(0, "UDP", "Game")],                                                              # Space Engineers
    "1829350": [(0, "UDP", "Game"), (1, "UDP", "Query")],                                        # V Rising
    "2465200": [(0, "UDP", "Game"), ("27016", "UDP", "Query"), ("9700", "UDP", "Blob sync")],    # Sons of the Forest
    "1110390": [(0, "TCP/UDP", "Game"), (1, "TCP/UDP", "Game + 1")],                             # Unturned
    "343050": [(0, "UDP", "Game")],                                                              # DST
    "565060": [(0, "TCP/UDP", "Game"), (3, "UDP", "Query"), ("27020", "UDP", "Steam query"),     # Avorion
               ("27021", "UDP", "Steam")],
    "403240": [(0, "UDP", "Game"), ("27165", "UDP", "Query"), ("15000", "UDP", "Beacon")],       # Squad
    "232130": [(0, "UDP", "Game"), ("27015", "UDP", "Query"), ("20560", "UDP", "Steam")],        # KF2
    "4020": [(0, "TCP/UDP", "Game")], "730": [(0, "TCP/UDP", "Game")],                           # GMod, CS2
}
TYPE_HINTS = {"vintage-story": [(0, "TCP/UDP", "Game")], "minecraft": [(0, "TCP", "Game")]}

_public_cache: Tuple[float, Optional[str]] = (0.0, None)


def public_ip(timeout: float = 5.0) -> Optional[str]:
    global _public_cache
    if time.time() - _public_cache[0] < 900:
        return _public_cache[1]
    ip = None
    try:
        req = urllib.request.Request("https://api.ipify.org?format=json",
                                     headers={"User-Agent": "BaoBunControlPanel"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ip = json.loads(r.read().decode()).get("ip")
        ipaddress.ip_address(ip)
    except Exception:
        ip = None
    _public_cache = (time.time(), ip)
    return ip


def lan_ips() -> List[str]:
    out = []
    try:
        import psutil
        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family != socket.AF_INET:
                    continue
                try:
                    ip = ipaddress.ip_address(a.address)
                except ValueError:
                    continue
                if ip.is_private and not ip.is_loopback and not ip.is_link_local \
                        and ip not in ipaddress.ip_network("100.64.0.0/10"):
                    out.append(a.address)
    except Exception:
        pass
    if not out:   # fallback: the address used to reach the internet
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.0.2.1", 9))
            out.append(s.getsockname()[0])
            s.close()
        except OSError:
            pass
    # the interface with the default route first, roughly: 192.168 before 10./172.
    return sorted(dict.fromkeys(out), key=lambda ip: (not ip.startswith("192.168."), ip))


def expected_ports(srv: dict, mgr=None) -> List[dict]:
    """[{port, proto, label}] the server most likely needs reachable."""
    out: List[dict] = []
    seen = set()

    def add(port, proto, label):
        try:
            port = int(port)
        except (TypeError, ValueError):
            return
        if not 0 < port < 65536:
            return
        key = (port, proto)
        if key in seen:
            return
        seen.add(key)
        out.append({"port": port, "proto": proto, "label": label})

    plan_rows = None
    try:
        from core import port_plan
        if port_plan.spec(srv):
            plan_rows = port_plan.rows(mgr, srv)
    except Exception:
        plan_rows = None
    game = srv.get("port")
    if plan_rows:
        game = next((r["port"] for r in plan_rows if r["role"] == "game"), game)
    if mgr is not None and not game:
        for p in mgr.get_server_ports(srv["id"]):
            if p["name"] == "Game":
                game = p["port"]
    exe = os.path.basename(str(srv.get("executable") or "")).lower()
    from core.minecraft import looks_minecraft
    kind = "minecraft" if (exe.endswith(".jar") or (not srv.get("appId") and looks_minecraft(srv))) \
        else (srv.get("type") or "")
    hints = PORT_HINTS.get(str(srv.get("appId") or "")) or TYPE_HINTS.get(kind)
    if hints and game:
        for off, proto, label in hints:
            if plan_rows and isinstance(off, str):
                continue      # fixed default ports: the port plan knows the real ones
            add(int(off) if isinstance(off, str) else int(game) + off, proto, label)
    elif game:
        add(game, "TCP/UDP", "Game")
    if plan_rows:
        covered = {o["port"] for o in out}
        for r in plan_rows:
            if r["local"]:
                continue
            for i in range(max(1, r["span"])):
                if r["port"] + i not in covered:
                    add(r["port"] + i, "UDP" if r["role"] in ("game", "query", "udp") else "TCP/UDP", r["label"])
        local = {r["port"] for r in plan_rows if r["local"]}
        return [o for o in out if o["port"] not in local]
    if mgr is not None:
        for p in mgr.get_server_ports(srv["id"]):
            if p["name"] != "RCON":
                add(p["port"], "UDP", p["name"])
    if srv.get("queryPort"):
        add(srv["queryPort"], "UDP", "Query")
    return out


def listening_ports(pid: Optional[int]) -> Dict[int, set]:
    """{port: {"TCP","UDP"}} the server's process tree is listening on."""
    if not pid:
        return {}
    try:
        import psutil
        procs = [psutil.Process(pid)]
        procs += procs[0].children(recursive=True)
        pids = {p.pid for p in procs}
        out: Dict[int, set] = {}
        for c in psutil.net_connections(kind="inet"):
            if c.pid in pids and c.laddr:
                if c.type == socket.SOCK_STREAM and c.status != psutil.CONN_LISTEN:
                    continue
                out.setdefault(c.laddr.port, set()).add("TCP" if c.type == socket.SOCK_STREAM else "UDP")
        return out
    except Exception:
        return {}


def connect_info(mgr, server_id: str, lookup_public: bool = True) -> dict:
    srv = mgr.get_server(server_id)
    if not srv:
        raise ValueError("Server not found")
    st = next((s for s in mgr.get_all_statuses() if s["id"] == server_id), {})
    ports = expected_ports(srv, mgr)
    listening = listening_ports(st.get("pid")) if st.get("isRunning") else {}
    for p in ports:
        if st.get("isRunning") and listening:
            want = {"TCP", "UDP"} if p["proto"] == "TCP/UDP" else {p["proto"]}
            p["listening"] = bool(want & listening.get(p["port"], set()))
        else:
            p["listening"] = None
    pub = public_ip() if lookup_public else None
    lans = lan_ips()
    ts_ip = ts_dns = None
    try:
        from core.web_panel import tailscale_info
        ts_ip, ts_dns = tailscale_info()
    except Exception:
        pass
    fw = None
    try:
        from core.hosting import firewall_rule_exists
        fw = firewall_rule_exists(srv.get("name", server_id))
    except Exception:
        pass
    game = ports[0]["port"] if ports else None
    addr = {}
    if game:
        if pub:
            addr["internet"] = "%s:%d" % (pub, game)
        if lans:
            addr["lan"] = "%s:%d" % (lans[0], game)
        if ts_dns or ts_ip:
            addr["tailscale"] = "%s:%d" % (ts_dns or ts_ip, game)
    tips = []
    if not game:
        tips.append("BaoBun doesn't know this server's port. Set it in Settings (Game port).")
    if fw is False:
        tips.append("Windows Firewall has no rule for this server yet - use Open Firewall.")
    if st.get("isRunning") and listening and any(p["listening"] is False for p in ports[:1]):
        tips.append("The server isn't listening on its game port yet. It may still be starting, or its "
                    "own config uses a different port.")
    if pub and lans:
        tips.append("Friends outside your home connect with the internet address. Your router must forward "
                    "the ports below to %s (or try automatic forwarding)." % lans[0])
    return {"server_id": server_id, "name": srv.get("name"), "running": bool(st.get("isRunning")),
            "public_ip": pub, "lan_ips": lans, "tailscale": ts_dns or ts_ip, "ports": ports,
            "firewall": fw, "addresses": addr, "tips": tips}


# --------------------------------------------------------------------------- #
# UPnP port forwarding (Internet Gateway Device)
# --------------------------------------------------------------------------- #
SSDP_ADDR = ("239.255.255.250", 1900)
WAN_SERVICES = ("urn:schemas-upnp-org:service:WANIPConnection:2", "urn:schemas-upnp-org:service:WANIPConnection:1",
                "urn:schemas-upnp-org:service:WANPPPConnection:1")


def _ssdp_locations(timeout: float = 2.5) -> List[str]:
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n"
           "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n").encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.settimeout(timeout)
    locs = []
    try:
        s.sendto(msg, SSDP_ADDR)
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, _ = s.recvfrom(65535)
            except socket.timeout:
                break
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m:
                locs.append(m.group(1).decode())
    except OSError:
        pass
    finally:
        s.close()
    return list(dict.fromkeys(locs))


def find_gateway(locations: Optional[List[str]] = None) -> Optional[Tuple[str, str]]:
    """(control URL, service type) of the router's WAN connection service, or None."""
    for loc in locations if locations is not None else _ssdp_locations():
        try:
            with urllib.request.urlopen(loc, timeout=5) as r:
                xml = r.read()
            root = ET.fromstring(xml)
        except Exception:
            continue
        for svc in root.iter():
            if not svc.tag.endswith("service"):
                continue
            kids = {c.tag.split("}")[-1]: (c.text or "").strip() for c in svc}
            if kids.get("serviceType") in WAN_SERVICES and kids.get("controlURL"):
                return urllib.parse.urljoin(loc, kids["controlURL"]), kids["serviceType"]
    return None


def _soap(url, service, action, args: Dict[str, str]) -> str:
    body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:%s xmlns:u="%s">%s</u:%s>'
            '</s:Body></s:Envelope>') % (action, service, "".join("<%s>%s</%s>" % (k, xml_escape(str(v)), k)
                                                                  for k, v in args.items()),
                                          action)
    req = urllib.request.Request(url, data=body.encode(), headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": '"%s#%s"' % (service, action)})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read().decode("utf-8", "replace")


def upnp_forward(ports: List[dict], lan_ip: str, label: str = "BaoBun", remove: bool = False,
                 gateway: Optional[Tuple[str, str]] = None) -> dict:
    """Ask the router to forward (or stop forwarding) ports to lan_ip."""
    gw = gateway or find_gateway()
    if not gw:
        return {"ok": False, "error": "No router answered. UPnP may be off on your router - forward the "
                                      "ports by hand in its settings instead."}
    url, svc = gw
    done, failed = [], []
    for p in ports:
        for proto in (["TCP", "UDP"] if p["proto"] == "TCP/UDP" else [p["proto"]]):
            try:
                if remove:
                    _soap(url, svc, "DeletePortMapping", {"NewRemoteHost": "", "NewExternalPort": p["port"],
                                                         "NewProtocol": proto})
                else:
                    _soap(url, svc, "AddPortMapping", {
                        "NewRemoteHost": "", "NewExternalPort": p["port"], "NewProtocol": proto,
                        "NewInternalPort": p["port"], "NewInternalClient": lan_ip, "NewEnabled": "1",
                        "NewPortMappingDescription": ("%s %s" % (label, p.get("label", "")))[:60],
                        "NewLeaseDuration": "0"})
                done.append("%s %d" % (proto, p["port"]))
            except Exception as e:
                failed.append({"port": "%s %d" % (proto, p["port"]), "error": str(e)[:120]})
    return {"ok": not failed, "done": done, "failed": failed}
