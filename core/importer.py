"""Find game servers that are already installed and add them to Tofu.

scan(root) walks a few folder levels looking for known server programs (from the
game catalog), Steam app manifests, Vintage Story and Minecraft servers.
Desktop-only: the web panel can't scan or add arbitrary folders.
"""

from __future__ import annotations

import os
import re
import time
from typing import Dict, List, Optional

SKIP_DIRS = {"windows", "program files", "program files (x86)", "$recycle.bin", "system volume information",
             "programdata", "appdata", "node_modules", ".git", "steamapps", "logs", "saves", "backups",
             "__pycache__", "mods", "plugins", "bin", "engine", "content", "binaries"}
EXTRA = {
    "vintagestoryserver.exe": ("Vintage Story", "vintage-story"),
    "stratumserver.exe": ("Vintage Story", "vintage-story"),
}


def _catalog():
    from core.game_catalog import GAME_PRESETS
    by_first: Dict[str, List[tuple]] = {}
    for name, p in GAME_PRESETS.items():
        exe = (p.get("executable") or "").replace("\\", "/")
        if not exe or name == "Custom / Other":
            continue
        first = exe.split("/")[0].lower()
        by_first.setdefault(first, []).append((name, p, exe))
    return GAME_PRESETS, by_first


def _manifest_app_ids(d: str) -> List[str]:
    sa = os.path.join(d, "steamapps")
    try:
        return [m.group(1) for f in os.listdir(sa) for m in [re.match(r"appmanifest_(\d+)\.acf$", f)] if m]
    except OSError:
        return []


def _match_dir(d: str, entries: Dict[str, str], presets, by_first) -> Optional[dict]:
    app_ids = _manifest_app_ids(d)
    hits = []
    for low, real in entries.items():
        for name, p, exe in by_first.get(low, []):
            full = os.path.join(d, *exe.split("/"))
            if os.path.isfile(full):
                hits.append((name, p, full))
    for low, (name, typ) in EXTRA.items():
        if low in entries:
            hits.append((name, presets.get(name) or {"type": typ, "port": 42420}, os.path.join(d, entries[low])))
    if not hits:
        if "server.properties" in entries and ("eula.txt" in entries or any(e.endswith(".jar") for e in entries)):
            jar = next((entries[e] for e in sorted(entries) if e.endswith(".jar")), "server.jar")
            p = presets.get("Minecraft (Java)") or {"type": "custom", "port": 25565}
            return {"game": "Minecraft (Java)", "type": p.get("type", "custom"), "appId": None,
                    "executable": os.path.join(d, jar), "port": p.get("port"), "directory": d}
        return None
    # several games share an exe name (srcds.exe): prefer the one the Steam manifest names
    if app_ids:
        for name, p, full in hits:
            if str(p.get("appId") or "") in app_ids:
                hits = [(name, p, full)]
                break
    name, p, full = hits[0]
    ambiguous = len({h[0] for h in hits}) > 1
    return {"game": name if not ambiguous else name + " (or similar)", "type": p.get("type", "steamcmd"),
            "appId": p.get("appId") or (app_ids[0] if app_ids else None), "executable": full,
            "port": p.get("port"), "directory": d, "ambiguous": ambiguous}


def scan(root: str, depth: int = 3, known_dirs=(), limit_seconds: float = 60.0) -> List[dict]:
    presets, by_first = _catalog()
    known = {os.path.normcase(os.path.normpath(k)) for k in known_dirs if k}
    found, end = [], time.time() + limit_seconds

    def walk(d, level):
        if time.time() > end:
            return
        try:
            names = os.listdir(d)
        except OSError:
            return
        entries = {n.lower(): n for n in names}
        hit = _match_dir(d, entries, presets, by_first)
        if hit:
            hit["already_added"] = os.path.normcase(os.path.normpath(d)) in known
            found.append(hit)
            return   # don't look inside a server for more servers
        if level >= depth:
            return
        for n in sorted(names):
            p = os.path.join(d, n)
            if n.lower() in SKIP_DIRS or n.startswith(".") or not os.path.isdir(p) or os.path.islink(p):
                continue
            walk(p, level + 1)
    walk(root, 0)
    return found


def import_servers(mgr, items: List[dict], user: str = "gui") -> List[dict]:
    """Add scanned servers (skips folders that are already in Tofu)."""
    servers = mgr.load_servers()
    known = {os.path.normcase(os.path.normpath(s.get("directory") or "")) for s in servers}
    names = {s.get("name") for s in servers}
    added = []
    for i, it in enumerate(items):
        d = it["directory"]
        if os.path.normcase(os.path.normpath(d)) in known or not os.path.isdir(d):
            continue
        base = (it.get("name") or os.path.basename(os.path.normpath(d)) or it.get("game") or "Server").strip()
        name, n = base, 2
        while name in names:
            name, n = "%s (%d)" % (base, n), n + 1
        entry = {"id": "srv_%d_%d" % (int(time.time() * 1000), i), "name": name, "type": it.get("type") or "custom",
                 "directory": d, "executable": it.get("executable"), "launchArgs": "",
                 "appId": it.get("appId"), "port": it.get("port"), "autoRestart": True, "manualStart": True,
                 "updateBeforeStart": False, "folder": ""}
        servers.append(entry)
        names.add(name)
        known.add(os.path.normcase(os.path.normpath(d)))
        added.append(entry)
    if added:
        mgr.save_servers(servers)
        for e in added:
            mgr._audit("server_add", e["id"], user=user, imported=True)
    return added
