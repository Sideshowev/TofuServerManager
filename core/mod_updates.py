"""Mod updates without surprises.

* Pins: a mod can be set to "Keep this version" (server entry ``modPins``). Update
  all and automatic updates skip it; updating it on its own still works.
* Every few hours the agent checks every server's mods (check()) and posts the
  list of updates to Discord when it changes.
* Setting ``mod_updates``: "off", "notify" (just tell me) or "auto". With "auto"
  the updates are applied during the nightly restart (servers stopped, worlds
  backed up), or - when the nightly restart is off - on stopped servers.
  A server can opt out with ``modAutoUpdate: false``.
* Before applying, the server's mod folder is copied (snapshot). If the server
  crashes within ROLLBACK_HOURS of its first start after the update, on_crash()
  puts the old mods back, pins the updated ones and says so on Discord.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from typing import Callable, Dict, List, Optional

MODES = ("off", "notify", "auto")
ROLLBACK_HOURS = 3
SNAPSHOT_MAX_BYTES = 5 * 1024 ** 3
KEEP_SNAPSHOTS = 3


# --------------------------------------------------------------------------- #
# pins
# --------------------------------------------------------------------------- #
def pins(srv: dict) -> List[str]:
    return [str(k) for k in (srv.get("modPins") or []) if k]


def pin_id(m: dict) -> str:
    """What a pin remembers: the mod's ID (file names change when a mod updates)."""
    return str(m.get("id") or m.get("key") or "")


def is_pinned(m: dict, pinned) -> bool:
    return pin_id(m) in pinned or str(m.get("key") or "") in pinned


def set_pin(mgr, server_id: str, key: str, pinned: bool, user: str = "system") -> List[str]:
    key = str(key)[:200]
    servers = mgr.load_servers()
    out: List[str] = []
    for s in servers:
        if s.get("id") == server_id:
            cur = pins(s)
            if pinned and key not in cur:
                cur.append(key)
            if not pinned:
                cur = [k for k in cur if k != key]
            s["modPins"] = cur
            out = cur
    mgr.save_servers(servers)
    try:
        mgr._audit("mods_pin" if pinned else "mods_unpin", server_id, user=user, mod=key[:80])
    except Exception:
        pass
    return out


# --------------------------------------------------------------------------- #
# settings and state
# --------------------------------------------------------------------------- #
def mode(mgr) -> str:
    m = mgr.get_setting("mod_updates", "notify") or "notify"
    return m if m in MODES else "notify"


def included(srv: dict) -> bool:
    return srv.get("modAutoUpdate") is not False


def _state_path(mgr) -> str:
    return os.path.join(str(mgr.config_path.parent), "runtime", "mod_updates.json")


def read_state(mgr) -> dict:
    try:
        with open(_state_path(mgr), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(mgr, **kw) -> dict:
    st = read_state(mgr)
    st.update(kw)
    p = _state_path(mgr)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, p)
    return st


def pending(mgr) -> Dict[str, dict]:
    return read_state(mgr).get("servers") or {}


# --------------------------------------------------------------------------- #
# checking
# --------------------------------------------------------------------------- #
def check(mgr, mm=None, log: Callable[[str], None] = print) -> Dict[str, dict]:
    """Look for updates on every server that BaoBun updates mods for.
    {server_id: {"name", "mods": [{"key", "name", "from", "to"}], "pinned": [names]}}"""
    from core.mods import ModManager
    mm = mm or ModManager(mgr)
    found: Dict[str, dict] = {}
    for srv in mgr.load_servers():
        sid = srv.get("id")
        if not sid or not included(srv):
            continue
        try:
            be, _prof = mm.backend(dict(srv))
            if not be.caps().get("update"):
                continue
            data = mm.list(sid, check_updates=True)
        except Exception as e:
            log("[WARN] mod update check for %s: %s" % (srv.get("name") or sid, e))
            continue
        pinned = set(pins(srv))
        ups = [m for m in data.get("mods") or [] if m.get("update_available") and m.get("enabled", True)]
        todo = [{"key": m["key"], "id": pin_id(m), "name": m.get("name") or m["key"], "from": m.get("version"),
                 "to": m.get("latest")} for m in ups if not is_pinned(m, pinned)]
        held = [m.get("name") or m["key"] for m in ups if is_pinned(m, pinned)]
        if todo or held:
            found[sid] = {"name": srv.get("name") or sid, "mods": todo, "pinned": held}
    write_state(mgr, servers=found, checked=time.time())
    return found


def summary(found: Dict[str, dict], how: str) -> Optional[tuple]:
    """(title, text) for Discord, or None when there's nothing to update."""
    lines, n = [], 0
    for sid, x in found.items():
        if not x["mods"]:
            continue
        n += len(x["mods"])
        bits = ["%s%s" % (m["name"], (" → %s" % m["to"]) if m.get("to") else "") for m in x["mods"][:12]]
        more = len(x["mods"]) - 12
        lines.append("**%s**: %s%s" % (x["name"], ", ".join(bits), (" and %d more" % more) if more > 0 else ""))
        if x.get("pinned"):
            lines.append("  (kept at their version: %s)" % ", ".join(x["pinned"][:8]))
    if not n:
        return None
    when = {"auto": "They'll be updated at the next nightly restart (worlds are backed up first).",
            "notify": "Update them from the Mods tab when it suits you."}.get(how, "")
    return ("\U0001F9E9 %d mod update%s available" % (n, "" if n == 1 else "s"), "\n".join(lines) + "\n" + when)


def check_and_notify(mgr, notify: Callable[[str, str], None], log: Callable[[str], None] = print) -> dict:
    how = mode(mgr)
    if how == "off":
        return {}
    found = check(mgr, log=log)
    msg = summary(found, how)
    sig = hashlib.sha1(json.dumps({k: [m["key"] + str(m.get("to")) for m in v["mods"]]
                                   for k, v in sorted(found.items())}, sort_keys=True).encode()).hexdigest()
    if msg and read_state(mgr).get("notified") != sig:
        try:
            notify(*msg)
            write_state(mgr, notified=sig)
        except Exception as e:
            log("[WARN] mod update notice failed: %s" % e)
    return found


# --------------------------------------------------------------------------- #
# applying, with a way back
# --------------------------------------------------------------------------- #
def _folder_size(path: str, limit: int) -> int:
    total = 0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            if total > limit:
                return total
    return total


def snapshot(mgr, server_id: str, folder: str) -> Optional[str]:
    """Copy the mod folder aside. None when it's missing or too big."""
    if not folder or not os.path.isdir(folder) or _folder_size(folder, SNAPSHOT_MAX_BYTES) > SNAPSHOT_MAX_BYTES:
        return None
    base = os.path.join(str(mgr.config_path.parent), "backups", server_id, "mods-before-update")
    dest = os.path.join(base, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copytree(folder, dest)
    try:
        old = sorted(os.listdir(base))[:-KEEP_SNAPSHOTS]
        for d in old:
            shutil.rmtree(os.path.join(base, d), ignore_errors=True)
    except OSError:
        pass
    return dest


def apply(mgr, server_id: str, keys: Optional[List[str]] = None, user: str = "auto-update",
          mm=None, backup: bool = True) -> dict:
    """Update a stopped server's mods (the pending ones, never pinned ones),
    keeping a snapshot so a crash afterwards can roll back."""
    from core.mods import ModManager
    mm = mm or ModManager(mgr)
    if mgr.is_running(server_id):
        raise RuntimeError("The server is running")
    srv = mgr.get_server(server_id) or {}
    pinned = set(pins(srv))
    data = mm.list(server_id) or {}
    by_key = {m["key"]: m for m in data.get("mods") or []}
    if keys is None:
        keys = [m["key"] for m in (pending(mgr).get(server_id) or {}).get("mods") or []]
    keys = [k for k in keys if k in by_key and not is_pinned(by_key[k], pinned)]
    if not keys:
        return {"updated": [], "failed": []}
    ids = [pin_id(by_key[k]) for k in keys]
    folder = data.get("folder")
    snap = None
    try:
        snap = snapshot(mgr, server_id, folder)
    except Exception:
        snap = None
    r = mm.update(server_id, keys, user=user, backup=backup)
    updated = r.get("updated") or []
    if updated:
        mgr._write_registry(server_id, mod_rollback={"at": time.time(), "snapshot": snap, "folder": folder,
                                                     "keys": keys, "ids": ids,
                                                     "names": [u.get("mod") for u in updated],
                                                     "done": False})
    st = read_state(mgr)
    servers = st.get("servers") or {}
    if server_id in servers:
        left = [m for m in servers[server_id]["mods"] if m["key"] not in keys]
        servers[server_id]["mods"] = left
        if not left and not servers[server_id].get("pinned"):
            servers.pop(server_id)
        write_state(mgr, servers=servers)
    return r


def apply_pending(mgr, log: Callable[[str], None] = print, backup: bool = True,
                  only_stopped: bool = True) -> List[str]:
    """Apply every pending update (mode "auto"). Returns lines for a summary."""
    if mode(mgr) != "auto":
        return []
    out = []
    for sid, x in list(pending(mgr).items()):
        srv = mgr.get_server(sid)
        if not srv or not included(srv) or not x.get("mods"):
            continue
        if only_stopped and mgr.is_running(sid):
            continue
        try:
            r = apply(mgr, sid, backup=backup)
            names = [u.get("mod") for u in r.get("updated") or []]
            if names:
                out.append("%s: %s" % (x["name"], ", ".join(n for n in names if n)))
            for f in r.get("failed") or []:
                out.append("%s: %s failed (%s)" % (x["name"], f.get("mod"), f.get("error")))
        except Exception as e:
            log("[WARN] mod updates for %s: %s" % (x.get("name"), e))
            out.append("%s: couldn't update (%s)" % (x.get("name"), e))
    return out


def on_crash(mgr, server_id: str, notify: Optional[Callable[[str, str], None]] = None,
             log: Callable[[str], None] = print) -> bool:
    """Called (synchronously) when a server crashes. Roll back a recent automatic
    mod update. Returns True if it rolled back."""
    rec = (mgr._read_registry(server_id) or {}).get("mod_rollback") or {}
    if not rec or rec.get("done") or time.time() - float(rec.get("at") or 0) > ROLLBACK_HOURS * 3600:
        return False
    srv = mgr.get_server(server_id) or {}
    name = srv.get("name") or server_id
    snap, folder = rec.get("snapshot"), rec.get("folder")
    rec["done"] = True
    if not snap or not folder or not os.path.isdir(snap):
        mgr._write_registry(server_id, mod_rollback=rec)
        if notify:
            notify("⚠️ %s crashed after its mods updated" % name,
                   "Updated: %s. BaoBun had no copy of the old mods to put back (the folder was too big) - "
                   "check the console, and pin or remove the mod that broke it." % ", ".join(rec.get("names") or []))
        return False
    aside = os.path.join(str(mgr.config_path.parent), "backups", server_id,
                         "rolled-back-mods-" + time.strftime("%Y%m%d-%H%M%S"))
    try:
        if os.path.isdir(folder):
            shutil.move(folder, aside)
        shutil.copytree(snap, folder)
    except Exception as e:
        log("[ERROR] mod rollback for %s failed: %s" % (name, e))
        mgr._write_registry(server_id, mod_rollback=rec)
        return False
    for k in rec.get("ids") or rec.get("keys") or []:
        set_pin(mgr, server_id, k, True, user="rollback")
    rec["rolled_back_at"] = time.time()
    mgr._write_registry(server_id, mod_rollback=rec)
    try:
        mgr._audit("mods_rollback", server_id, user="auto-update", mods=rec.get("names"))
    except Exception:
        pass
    log("[INFO] %s crashed after a mod update - old mods put back" % name)
    if notify:
        notify("↩️ Rolled back mods on %s" % name,
               "It crashed after these updated: %s. The previous versions are back and those mods are now kept at "
               "their version (unpin them in the Mods tab once a fix is out). The new files are in %s."
               % (", ".join(n for n in rec.get("names") or [] if n), aside))
    return True
