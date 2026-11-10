"""Port plan: which ports each server uses, clashes between servers, and fixing them.

Lots of games default to the same ports (27015, 7777, 25575 ...). Each game's
ports are listed in SPECS with where the game reads them (the same "where"
tuples as core/quick_settings: launch options, ARK map URL, ini/json/xml/cfg
files) plus how many ports in a row the game uses ("span": Valheim uses
2456-2458, Arma 2302-2306).

* rows(mgr, srv)      - the ports a server uses now (set, pending or the game's default)
* conflicts(mgr)      - ports used by more than one server
* plan(mgr, srv)      - free ports for a server, keeping the ones that don't clash
* apply(mgr, sid)     - write a plan into the game's own settings
* fix_all(mgr)        - move servers off each other's ports (running ones stay put)
* start_check(mgr, srv) - blockers / warnings before a server starts

Ports marked "local" (RCON, telnet, REST API) are only used by BaoBun on this PC,
so they are never opened in the firewall or forwarded.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

PAL = ("{palini}", "/Script/Pal.PalGameWorldSettings", "OptionSettings")
CONAN_GAME = "ConanSandbox/Saved/Config/WindowsServer/Game.ini"


def _r(role, label, default, where, span=1, local=False, step=None, also=()):
    return {"role": role, "label": label, "default": default, "where": where, "span": span,
            "local": local, "step": step, "also": list(also)}


_SRCDS = [_r("game", "Game", 27015, ("launch", "-port", " "), span=1, step=2)]
_ARMA = [_r("game", "Game", 2302, ("launch", "-port", "="), span=5, step=10),
         _r("rcon", "RCON (BattlEye)", 2310, ("be", "RConPort"), local=True)]

SPECS: Dict[str, List[dict]] = {
    "2430930": [_r("game", "Game", 7777, ("ark", "Port"), step=2),
                _r("rcon", "RCON", 27020, ("ark", "RCONPort"), local=True)],
    "376030": [_r("game", "Game", 7777, ("ark", "Port"), span=2),
               _r("query", "Query", 27015, ("ark", "QueryPort"), step=2),
               _r("rcon", "RCON", 27020, ("ark", "RCONPort"), local=True)],
    "2394010": [_r("game", "Game", 8211, ("launch", "-port", "="), step=2,
                   also=[("ini_tuple",) + PAL + ("PublicPort",)]),
                _r("query", "Query", 27015, ("launch", "-QueryPort", "="), step=2),
                _r("rcon", "RCON", 25575, ("ini_tuple",) + PAL + ("RCONPort",), local=True),
                _r("rest", "REST API", 8212, ("ini_tuple",) + PAL + ("RESTAPIPort",), local=True)],
    "896660": [_r("game", "Game", 2456, ("launch", "-port", " "), span=3)],
    "443030": [_r("game", "Game", 7777, ("launch", "-Port", "="), span=2),
               _r("query", "Query", 27015, ("launch", "-QueryPort", "="), step=2),
               _r("rcon", "RCON", 25575, ("ini", CONAN_GAME, "RconPlugin", "RconPort"), local=True)],
    "1690800": [_r("game", "Game", 7777, ("launch", "-Port", "="), step=2),
                _r("reliable", "Reliable", 8888, ("launch", "-ReliablePort", "="))],
    "3017310": [_r("game", "Game", 8777, ("launch", "-Port", "="), step=2),
                _r("query", "Query", 27015, ("launch", "-QueryPort", "="), step=2),
                _r("echo", "Echo (local)", 18888, ("launch", "-EchoPort", "="), local=True)],
    "2089300": [_r("game", "Game", 17777, ("launch", "-Port", "="), step=2),
                _r("query", "Query", 27015, ("launch", "-QueryPort", "="), step=2)],
    "2857200": [_r("game", "Game", 7777, ("launch", "-PORT", "="), step=2),
                _r("query", "Query", 27015, ("launch", "-QueryPort", "="), step=2)],
    "581330": [_r("game", "Game", 27102, ("launch", "-Port", "="), step=2),
               _r("query", "Query", 27131, ("launch", "-QueryPort", "="), step=2)],
    "629800": [_r("game", "Game", 7777, ("launch", "-Port", "="), step=2),
               _r("query", "Query", 27015, ("launch", "-QueryPort", "="), step=2),
               _r("beacon", "Beacon", 15000, ("launch", "-BeaconPort", "="))],
    "233780": _ARMA,
    "223350": [_ARMA[0], _r("query", "Steam query", 27016, ("cfg", "{config}", "steamQueryPort"), step=2), _ARMA[1]],
    "1874900": [_r("game", "Game", 2001, ("json", "{config}", "bindPort"), also=[("json", "{config}", "publicPort")]),
                _r("query", "Query (A2S)", 17777, ("json", "{config}", "a2s.port")),
                _r("rcon", "RCON", 19999, ("json", "{config}", "rcon.port"), local=True)],
    "258550": [_r("game", "Game", 28015, ("launch", "+server.port", " "), step=10),
               _r("query", "Query", 28017, ("launch", "+server.queryport", " ")),
               _r("rcon", "RCON", 28016, ("launch", "+rcon.port", " "), local=True),
               _r("app", "Rust+ app", 28082, ("launch", "+app.port", " "))],
    "294420": [_r("game", "Game", 26900, ("xml_prop", "serverconfig.xml", "ServerPort"), span=4, step=10),
               _r("telnet", "Telnet", 8081, ("xml_prop", "serverconfig.xml", "TelnetPort"), local=True)],
    "380870": [_r("game", "Game", 16261, ("launch", "-port", " ")),
               _r("udp", "Direct UDP", 16262, ("launch", "-udpport", " ")),
               _r("rcon", "RCON", 27015, ("ini", "{pzini}", None, "RCONPort"), local=True)],
    "vintage-story": [_r("game", "Game", 42420, ("json", "{data}/serverconfig.json", "Port"))],
    "minecraft": [_r("game", "Game", 25565, ("ini", "server.properties", None, "server-port")),
                  _r("rcon", "RCON", 25575, ("ini", "server.properties", None, "rcon.port"), local=True)],
    "2278520": [_r("game", "Game", 15637, ("json", "enshrouded_server.json", "queryPort"))],
    "1829350": [_r("game", "Game", 9876, ("json", "{vrising}", "Port")),
                _r("query", "Query", 9877, ("json", "{vrising}", "QueryPort")),
                _r("rcon", "RCON", 25575, ("json", "{vrising}", "Rcon.Port"), local=True)],
    "1110390": [_r("game", "Game", 27015, ("dat", "{unturned}/Server/Commands.dat", "Port"), span=3, step=4)],
    "403240": [_r("game", "Game", 7787, ("bare", "Port"), span=2),
               _r("query", "Query", 27165, ("bare", "QueryPort"), step=2),
               _r("beacon", "Beacon", 15000, ("bare", "beaconport")),
               _r("rcon", "RCON", 21114, ("ini", "SquadGame/ServerConfig/Rcon.cfg", None, "Port"), local=True)],
    "4020": _SRCDS, "730": _SRCDS, "740": _SRCDS, "232250": _SRCDS, "222860": _SRCDS,
}
ALIASES = {"108600": "380870", "1890870": "1874900", "346110": "376030"}
LOW, HIGH = 1024, 65000


def key_for(srv: dict) -> Optional[str]:
    if (srv.get("type") or "").lower() == "vintage-story":
        return "vintage-story"
    if not srv.get("appId"):
        try:
            from core.minecraft import looks_minecraft
            if looks_minecraft(srv):
                return "minecraft"
        except Exception:
            pass
        return None
    a = str(srv.get("appId"))
    return ALIASES.get(a, a)


def spec(srv: dict) -> Optional[List[dict]]:
    k = key_for(srv)
    return SPECS.get(k) if k else None


def _int(v) -> Optional[int]:
    try:
        n = int(str(v).strip().strip('"'))
        return n if 0 < n < 65536 else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# reading / writing one port
# --------------------------------------------------------------------------- #
def _bare_get(args: str, key: str) -> Optional[str]:
    m = re.search(r"(?i)(?:^|\s)%s=(\S+)" % re.escape(key), args or "")
    return m.group(1) if m else None


def _bare_set(args: str, key: str, value) -> str:
    rx = re.compile(r"(?i)(^|\s)%s=\S+" % re.escape(key))
    if rx.search(args or ""):
        return rx.sub(lambda m: m.group(1) + "%s=%s" % (key, value), args, count=1)
    return ((args or "") + " %s=%s" % (key, value)).strip()


def _ark_get(args: str, key: str) -> Optional[str]:
    """ARK reads ports as -Port=7777 or ?Port=7777 on the map - BaoBun's cluster maps use -flags."""
    m = re.search(r"(?i)(?:^|\s)-%s=(\S+)" % re.escape(key), args or "")
    if m:
        return m.group(1)
    m = re.search(r"(?i)[?&]%s=([^?&\s]*)" % re.escape(key), args or "")
    return m.group(1) if m else None


def _ark_set(srv: dict, key: str, value) -> str:
    args = srv.get("launchArgs") or ""
    flag = re.compile(r"(?i)((?:^|\s)-%s=)\S+" % re.escape(key))
    if flag.search(args):
        return flag.sub(lambda m: m.group(1) + str(value), args, count=1)
    url = re.compile(r"(?i)([?&]%s=)[^?&\s]*" % re.escape(key))
    if url.search(args):
        return url.sub(lambda m: m.group(1) + str(value), args, count=1)
    if str(srv.get("appId")) == "2430930" or not args or args.startswith("-"):
        return (args + " -%s=%s" % (key, value)).strip()
    parts = args.split(" ", 1)
    parts[0] += "?%s=%s" % (key, value)
    return " ".join(parts)


def read_port(srv: dict, where) -> Optional[int]:
    kind = where[0]
    try:
        if kind == "ark":
            return _int(_ark_get(srv.get("launchArgs") or "", where[1]))
        if kind == "bare":
            return _int(_bare_get(srv.get("launchArgs") or "", where[1]))
        if kind == "be":
            from core import rcon_setup
            return _int(rcon_setup.be_read(srv).get(where[1].lower()))
        from core.quick_settings import read_value
        return _int(read_value(srv, where))
    except Exception:
        return None


def write_port(srv: dict, where, port: int):
    """Write one port. Launch-option kinds update srv["launchArgs"] in place.
    Raises FileNotFoundError when the game hasn't made its config file yet."""
    kind = where[0]
    if kind == "ark":
        srv["launchArgs"] = _ark_set(srv, where[1], int(port))
        return
    if kind == "bare":
        srv["launchArgs"] = _bare_set(srv.get("launchArgs") or "", where[1], port)
        return
    if kind == "be":
        from core import rcon_setup
        rcon_setup.be_write(srv, {where[1]: str(port)})
        return
    from core.quick_settings import write_value
    res = write_value(srv, where, str(int(port)), "int")
    if res and res[0] == "args":
        srv["launchArgs"] = res[1]


# --------------------------------------------------------------------------- #
# what a server uses now
# --------------------------------------------------------------------------- #
def rows(mgr, srv: dict) -> List[dict]:
    """[{role, label, port, span, local, source, writable}] - source is "set" (in the
    game's settings), "pending" (BaoBun will write it once the config file exists),
    "default" (the game's default) or "baobun" (only known from BaoBun's own fields)."""
    sp = spec(srv)
    pending = srv.get("portsPending") or {}
    out = []
    if sp:
        for r in sp:
            port, source = read_port(srv, r["where"]), "set"
            if port is None and _int(pending.get(r["role"])):
                port, source = _int(pending.get(r["role"])), "pending"
            if port is None and r["role"] == "game" and _int(srv.get("port")) and r["where"][0] not in (
                    "launch", "ark_url", "ark", "bare"):
                # file-based setting we couldn't read (file missing / share offline): trust BaoBun's field
                port, source = _int(srv.get("port")), "baobun"
            if port is None:
                port, source = r["default"], "default"
            out.append({"role": r["role"], "label": r["label"], "port": port, "span": r["span"],
                        "local": r["local"], "source": source, "writable": True})
        return out
    # games without a spec: what BaoBun knows (Edit Server port, -Port= style options)
    seen = set()
    if _int(srv.get("port")):
        p = _int(srv.get("port"))
        out.append({"role": "game", "label": "Game", "port": p, "span": 2 if srv.get("appId") else 1,
                    "local": False, "source": "baobun", "writable": False})
        seen.add(p)
    if _int(srv.get("queryPort")) and _int(srv.get("queryPort")) not in seen:
        out.append({"role": "query", "label": "Query", "port": _int(srv.get("queryPort")), "span": 1,
                    "local": False, "source": "baobun", "writable": False})
        seen.add(_int(srv.get("queryPort")))
    args = srv.get("launchArgs") or ""
    for label, rx in (("Game", r"(?i)[-?&]Port=(\d+)"), ("Query", r"(?i)[-?&]QueryPort=(\d+)"),
                      ("RCON", r"(?i)[-?&]RCONPort=(\d+)")):
        m = re.search(rx, args)
        if m and _int(m.group(1)) not in seen:
            p = _int(m.group(1))
            seen.add(p)
            out.append({"role": label.lower(), "label": label, "port": p, "span": 1, "local": label == "RCON",
                        "source": "set", "writable": False})
    return out


current = rows


def _ports_of(row) -> range:
    return range(int(row["port"]), int(row["port"]) + max(1, int(row.get("span") or 1)))


def usage(mgr, exclude: Optional[str] = None, only_running: bool = False) -> Dict[int, List[dict]]:
    """{port: [{"id", "name", "role"}]} for every server (except ``exclude``)."""
    out: Dict[int, List[dict]] = {}
    for s in mgr.load_servers():
        if not s.get("id") or s["id"] == exclude:
            continue
        if only_running and not mgr.is_running(s["id"]):
            continue
        for row in rows(mgr, s):
            for p in _ports_of(row):
                out.setdefault(p, []).append({"id": s["id"], "name": s.get("name") or s["id"],
                                              "role": row["label"]})
    return out


def conflicts(mgr) -> List[dict]:
    """[{"port", "servers": [{"id","name","role"}]}] for ports more than one server uses."""
    res = []
    for port, users in sorted(usage(mgr).items()):
        if len({u["id"] for u in users}) > 1:
            res.append({"port": port, "servers": users})
    return res


def clash_pairs(mgr) -> List[dict]:
    """Conflicts grouped per pair of servers: [{"ids", "names", "ports": [..]}]."""
    pairs: Dict[tuple, dict] = {}
    for c in conflicts(mgr):
        ids = sorted({u["id"] for u in c["servers"]})
        names = {u["id"]: u["name"] for u in c["servers"]}
        k = tuple(ids)
        e = pairs.setdefault(k, {"ids": ids, "names": [names[i] for i in ids], "ports": []})
        e["ports"].append(c["port"])
    return list(pairs.values())


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #
def _os_busy(port: int) -> bool:
    try:
        from core.hosting import port_in_use
        return port_in_use(port)
    except Exception:
        return False


def _free_block(start: int, span: int, step: int, taken: set, check_os: bool) -> Optional[int]:
    p = max(LOW, int(start))
    for _ in range(400):
        block = range(p, p + span)
        if p + span <= HIGH and not any(x in taken for x in block) and \
                not (check_os and any(_os_busy(x) for x in block)):
            return p
        p += step
        if p + span > HIGH:
            p = 20000
    return None


def plan(mgr, srv: dict, taken: Optional[set] = None, check_os: bool = True,
         wanted: Optional[dict] = None) -> Dict[str, int]:
    """{role: port} - keeps each current port whose range is free, otherwise the
    next free block from there. ``wanted`` pins roles to given ports."""
    if taken is None:
        taken = set(usage(mgr, exclude=srv.get("id")).keys())
    taken = set(taken)
    sp = {r["role"]: r for r in (spec(srv) or [])}
    out: Dict[str, int] = {}
    running = srv.get("id") and mgr is not None and mgr.is_running(srv["id"])
    for row in rows(mgr, srv):
        r = sp.get(row["role"], {})
        span = max(1, int(row.get("span") or 1))
        step = r.get("step") or (max(span, 2) if row["role"] == "game" else span)
        if wanted and _int(wanted.get(row["role"])):
            p = _int(wanted[row["role"]])
        else:
            # our own running server holds its ports - don't count that as "in use"
            p = _free_block(row["port"], span, step, taken, check_os and not running)
            if p is None:
                raise RuntimeError("No free ports found for %s" % row["label"])
        out[row["role"]] = p
        taken.update(range(p, p + span))
    return out


def apply(mgr, server_id: str, wanted: Optional[dict] = None, user: str = "system",
          check_os: bool = True) -> dict:
    """Give a server free ports and write them into the game's settings.

    Returns {"ports": {role: port}, "written": [labels], "pending": [labels],
    "errors": [text], "changed": bool}."""
    if mgr.is_running(server_id):
        raise RuntimeError("Stop the server first - games only read their ports when they start.")
    servers = mgr.load_servers()
    srv = next((s for s in servers if s.get("id") == server_id), None)
    if srv is None:
        raise ValueError("Server not found")
    if wanted:
        for role, p in wanted.items():
            if not _int(p) or not (LOW <= int(p) <= HIGH):
                raise ValueError("Ports must be between %d and %d" % (LOW, HIGH))
    sp = {r["role"]: r for r in (spec(srv) or [])}
    before = {r["role"]: r for r in rows(mgr, srv)}
    target = plan(mgr, srv, check_os=check_os, wanted=wanted)
    written, pending_l, errors = [], [], []
    pending = dict(srv.get("portsPending") or {})
    for role, port in target.items():
        old = before.get(role) or {}
        r = sp.get(role)
        if r is None:
            if role == "game":
                srv["port"] = port
            elif role == "query":
                srv["queryPort"] = port
            continue
        if old.get("port") == port and old.get("source") in ("set",):
            pending.pop(role, None)
            continue
        if old.get("source") == "default" and port == r["default"] and role not in pending:
            continue          # the game uses this port anyway
        try:
            write_port(srv, r["where"], port)
            for extra in r.get("also") or []:
                try:
                    write_port(srv, extra, port)
                except Exception:
                    pass
            pending.pop(role, None)
            written.append(r["label"])
        except FileNotFoundError:
            pending[role] = port
            pending_l.append(r["label"])
        except Exception as e:
            errors.append("%s: %s" % (r["label"], e))
    if pending:
        srv["portsPending"] = pending
    else:
        srv.pop("portsPending", None)
    # store what the game will really use (a port we couldn't write stays as it was)
    eff = {r["role"]: r["port"] for r in rows(mgr, srv)} if sp else dict(target)
    if "game" in eff:
        srv["port"] = eff["game"]
    if "query" in eff:
        srv["queryPort"] = eff["query"]
    srv["ports"] = eff
    mgr.save_servers(servers)
    try:
        from core import rcon_config
        rcon_config.forget(srv)
    except Exception:
        pass
    changed = any((before.get(k) or {}).get("port") != v for k, v in eff.items())
    if changed or written:
        mgr._audit("ports_set", server_id, user=user, ports=eff)
    return {"ports": eff, "written": written, "pending": pending_l, "errors": errors, "changed": changed}


def setup_new(mgr, server_id: str, pinned_game: Optional[int] = None) -> Optional[dict]:
    """Right after a server is added: move it off ports other servers already use."""
    try:
        srv = mgr.get_server(server_id) or {}
        wanted = {"game": pinned_game} if pinned_game and spec(srv) else None
        return apply(mgr, server_id, wanted=wanted, user="system")
    except Exception as e:
        print("[WARN] port plan for new server skipped: %s" % e)
        return None


def apply_pending(mgr, srv: dict) -> List[str]:
    """Write ports that were waiting for the game to create its config file."""
    pending = srv.get("portsPending") or {}
    if not pending:
        return []
    sp = {r["role"]: r for r in (spec(srv) or [])}
    done = []
    servers = mgr.load_servers()
    live = next((s for s in servers if s.get("id") == srv.get("id")), None)
    if live is None:
        return []
    left = dict(pending)
    for role, port in pending.items():
        r = sp.get(role)
        if not r:
            left.pop(role, None)
            continue
        try:
            write_port(live, r["where"], int(port))
            for extra in r.get("also") or []:
                try:
                    write_port(live, extra, int(port))
                except Exception:
                    pass
            left.pop(role, None)
            done.append(r["label"])
        except Exception:
            pass
    if done:
        if left:
            live["portsPending"] = left
        else:
            live.pop("portsPending", None)
        mgr.save_servers(servers)
        srv.update(live)
    return done


def fix_all(mgr, user: str = "system", check_os: bool = True) -> dict:
    """Move servers off each other's ports. Running servers keep theirs; otherwise
    the first server in the list keeps its ports and later ones move."""
    servers = [s for s in mgr.load_servers() if s.get("id")]
    clashing = {i for pair in clash_pairs(mgr) for i in pair["ids"]}
    order = sorted(servers, key=lambda s: (not mgr.is_running(s["id"]),))
    taken: set = set()
    moved, skipped = [], []
    for s in order:
        my = rows(mgr, s)
        mine = {p for r in my for p in _ports_of(r)}
        overlaps_self = len(mine) != sum(len(_ports_of(r)) for r in my)
        if s["id"] not in clashing or (not (mine & taken) and not overlaps_self):
            taken |= mine
            continue
        if mgr.is_running(s["id"]):
            skipped.append({"id": s["id"], "name": s.get("name"), "reason": "running"})
            taken |= mine
            continue
        if not spec(s):
            skipped.append({"id": s["id"], "name": s.get("name"),
                            "reason": "BaoBun doesn't know where this game keeps its ports - change them by hand"})
            taken |= mine
            continue
        try:
            target = plan(mgr, s, taken=taken, check_os=check_os)
            res = apply(mgr, s["id"], wanted=target, user=user, check_os=check_os)
            moved.append({"id": s["id"], "name": s.get("name"), "ports": res["ports"],
                          "pending": res["pending"], "errors": res["errors"]})
            fresh = mgr.get_server(s["id"]) or s
            taken |= {p for r in rows(mgr, fresh) for p in _ports_of(r)}
        except Exception as e:
            skipped.append({"id": s["id"], "name": s.get("name"), "reason": str(e)})
            taken |= mine
    return {"moved": moved, "skipped": skipped, "left": clash_pairs(mgr)}


# --------------------------------------------------------------------------- #
# start-time check
# --------------------------------------------------------------------------- #
def start_check(mgr, srv: dict, check_os: bool = True) -> dict:
    """{"block": [text], "warn": [text]} - block when a running BaoBun server owns
    one of this server's ports; warn when some other program does."""
    block, warn = [], []
    my = rows(mgr, srv)
    others = usage(mgr, exclude=srv.get("id"), only_running=True)
    reported = set()
    for r in my:
        for p in _ports_of(r):
            for u in others.get(p, []):
                if u["id"] not in reported:
                    reported.add(u["id"])
                    block.append("%s port %d is already used by %s, which is running. Give this server other "
                                 "ports first (Ports → Fix automatically)." % (r["label"], p, u["name"]))
    if block or not check_os:
        return {"block": block, "warn": warn}
    for r in my:
        if r["source"] == "pending":
            warn.append("%s port %d is set the first time the game writes its config file - this start may "
                        "still use the game's default port." % (r["label"], r["port"]))
        p = r["port"]
        if _os_busy(p):
            owner = None
            try:
                owner, _pids = mgr.port_owner(p)
            except Exception:
                pass
            if owner and owner != srv.get("id"):
                name = (mgr.get_server(owner) or {}).get("name", owner)
                block.append("%s port %d is in use by %s." % (r["label"], p, name))
            elif not owner:
                warn.append("%s port %d is already in use by another program on this PC - the server may "
                            "fail to start or players may not reach it." % (r["label"], p))
    return {"block": block, "warn": warn}


def _rcon_state(srv: dict) -> Optional[str]:
    """"on" / "off" for games that need RCON for commands (and BaoBun can set it up), else None."""
    try:
        from core import rcon_config, rcon_setup
        prof = rcon_config.profile(srv)
        if not prof or prof.get("stdin") or not rcon_setup.supported(srv):
            return None
        return "on" if rcon_config.info(srv) else "off"
    except Exception:
        return None


def overview(mgr) -> dict:
    """Everything the Ports page shows."""
    use = usage(mgr)
    out = []
    for s in mgr.load_servers():
        if not s.get("id"):
            continue
        rs = rows(mgr, s)
        for r in rs:
            names = sorted({u["name"] for p in _ports_of(r) for u in use.get(p, []) if u["id"] != s["id"]})
            r["clash"] = names
            r["range"] = ("%d" % r["port"]) if r["span"] <= 1 else "%d-%d" % (r["port"], r["port"] + r["span"] - 1)
        out.append({"id": s["id"], "name": s.get("name") or s["id"], "running": mgr.is_running(s["id"]),
                    "known": bool(spec(s)), "rows": rs, "pending": bool(s.get("portsPending")),
                    "rcon": _rcon_state(s)})
    pairs = clash_pairs(mgr)
    return {"servers": out, "clashes": pairs}
