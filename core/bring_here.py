"""Restore on another PC ("Bring servers here").

When the PC that runs a server dies (or you're moving to a new one), every
server whose backups were copied to the second backup folder can be set up
again on any PC running Tofu:

1. scan(folder)  - look in a backup-copy folder (the "Also copy backups to"
   folder: another drive, a NAS, a shared folder on another PC). Each server
   has a subfolder with its backup zips and server.json (what the server is).
2. plan(...)     - work out the server's new home here: a folder under the
   chosen base folder, paths in its settings moved along (the server folder,
   save folders outside it, the user's own AppData), settings tied to the old
   PC's hardware dropped (CPU cores, Java location).
3. start(...)    - in the background: copy the backup here, add the server,
   install the game (Steam / Vintage Story / Minecraft / Hytale; the same
   game version where the game lets us pick), restore the world.

It is manual on purpose: brought-over servers are set to start only when you
start them, because the same world running on two PCs at once splits into two
worlds. Nothing here knows about particular PCs, drives or user names.
"""

from __future__ import annotations

import json
import ntpath
import os
import posixpath
import re
import shutil
import threading
import time
import zipfile
from typing import Callable, Dict, List, Optional, Tuple

# Settings that describe the old PC rather than the server.
MACHINE_FIELDS = ("cpuAffinity", "javaPath")
# Desktop-only fields that can run programs: not taken over when started from the web.
UNTRUSTED_DROP = ("javaArgs",)

JOBS: Dict[str, dict] = {}
_jobs_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# paths from another PC (may be Windows paths read on any OS)
# --------------------------------------------------------------------------- #
def _flavor(p: str):
    return ntpath if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", p or "") else posixpath


def _norm(p: str) -> str:
    f = _flavor(p)
    return f.normcase(f.normpath(p))


def _under(path: str, root: str) -> Optional[str]:
    """The part of ``path`` below ``root`` ("" when equal), or None when it isn't inside."""
    if not path or not root or _flavor(path) is not _flavor(root):
        return None
    f = _flavor(root)
    a, b = _norm(path), _norm(root)
    if a == b:
        return ""
    if a.startswith(b.rstrip(f.sep) + f.sep):
        # keep the original letter case of the rest
        return f.normpath(path)[len(f.normpath(root).rstrip(f.sep)) + 1:]
    return None


def _join(base: str, rel: str) -> str:
    if not rel:
        return base
    return os.path.join(base, *[x for x in re.split(r"[\\/]+", rel) if x])


def translate(path: str, mapping: List[Tuple[str, str]]) -> Optional[str]:
    """Move one old path along with the (old root, new root) pairs; None if no pair fits."""
    for old, new in sorted(mapping, key=lambda m: -len(m[0])):
        rel = _under(path, old)
        if rel is not None:
            return _join(new, rel)
    return None


_HOME_RX = (re.compile(r"^[A-Za-z]:\\Users\\[^\\]+(?=\\|$)", re.I), re.compile(r"^/(?:home|Users)/[^/]+(?=/|$)"))


def home_of(path: str) -> Optional[str]:
    """The user-profile folder an old path sits in (C:\\Users\\name), if any."""
    p = (path or "").replace("/", "\\") if _flavor(path) is ntpath else (path or "")
    for rx in _HOME_RX:
        m = rx.match(p)
        if m:
            return m.group(0)
    return None


def rewrite_text(text: str, pairs: List[Tuple[str, str]]) -> str:
    """Replace old paths inside a setting (launch arguments, save folder lists ...)."""
    if not isinstance(text, str) or not text:
        return text
    for old, new in sorted(pairs, key=lambda m: -len(m[0])):
        if not old:
            continue
        flags = re.I if _flavor(old) is ntpath else 0
        variants = {old.rstrip("\\/")}
        if _flavor(old) is ntpath:
            variants.add(old.rstrip("\\/").replace("\\", "/"))
        for v in variants:
            rx = re.compile(re.escape(v) + r"(?=$|[\\/\"'\s;,?=)])", flags)
            text = rx.sub(lambda _m: new, text)
    return text


def _rewrite(obj, pairs):
    if isinstance(obj, str):
        return rewrite_text(obj, pairs)
    if isinstance(obj, list):
        return [_rewrite(x, pairs) for x in obj]
    if isinstance(obj, dict):
        return {k: _rewrite(v, pairs) for k, v in obj.items()}
    return obj


def _safe_name(name: str) -> str:
    s = "".join(c if c.isalnum() or c in " -_." else "_" for c in str(name or "")).strip(" .")
    return s[:60] or "server"


# --------------------------------------------------------------------------- #
# 1. what's in a backup-copy folder
# --------------------------------------------------------------------------- #
def _manifest(zip_path: str) -> dict:
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            return json.loads(z.read("tofusm_backup.json" if "tofusm_backup.json" in names else "baobun_backup.json"))
    except Exception:
        return {}


def _scan(folder: str, mine_ids: set, mine_names: set) -> List[dict]:
    out = []
    for sub in sorted(os.listdir(folder)):
        d = os.path.join(folder, sub)
        if not os.path.isdir(d):
            continue
        zips = []
        for f in os.listdir(d):
            if f.lower().endswith(".zip") and not f.startswith("pre_restore_"):
                p = os.path.join(d, f)
                st = os.stat(p)
                zips.append({"name": f, "path": p, "size": st.st_size, "mtime": st.st_mtime})
        if not zips:
            continue
        zips.sort(key=lambda b: b["mtime"], reverse=True)
        srv = None
        try:
            with open(os.path.join(d, "server.json"), encoding="utf-8") as f:
                srv = json.load(f)
        except Exception:
            srv = _manifest(zips[0]["path"]).get("server")
        if not isinstance(srv, dict):
            srv = None
        name = (srv or {}).get("name")
        if not name:
            try:
                with open(os.path.join(d, "server-name.txt"), encoding="utf-8") as f:
                    name = f.read().strip()
            except OSError:
                name = sub
        out.append({"key": sub, "name": name or sub, "server": srv, "backups": zips,
                    "newest": zips[0]["name"], "newest_time": zips[0]["mtime"],
                    "game": _game_label(srv), "here": sub in mine_ids or (name in mine_names),
                    "can_bring": srv is not None,
                    "why_not": None if srv else "made by an older Tofu - make one new backup on the old PC, "
                                                "or add the server here and use Restore"})
    return out


def _game_label(srv: Optional[dict]) -> str:
    if not srv:
        return ""
    try:
        from core import game_installers as gi
        k = gi.kind(srv)
    except Exception:
        k = None
    return {"vintage-story": "Vintage Story", "hytale": "Hytale", "minecraft": "Minecraft"}.get(
        k or "", srv.get("preset") or ("Custom game" if srv.get("type") in (None, "", "custom") and not srv.get("appId") else srv.get("type")) or ("Steam app %s" % srv["appId"] if srv.get("appId") else ""))


def scan(mgr, folder: Optional[str] = None, timeout: float = 15.0) -> dict:
    """{"folder", "servers": [...]} or {"folder", "error"}. Looked up with a time
    limit, so an offline PC or NAS can't freeze Tofu."""
    folder = (folder or mgr.get_setting("backup_mirror_dir", "") or "").strip()
    if not folder:
        return {"folder": "", "error": "Pick the folder the backups were copied to."}
    mine = mgr.load_servers()
    ids = {s.get("id") for s in mine}
    names = {s.get("name") for s in mine}
    box: dict = {}

    def look():
        try:
            if not os.path.isdir(folder):
                box["error"] = "Can't open %s (is that PC or drive on?)" % folder
                return
            box["servers"] = _scan(folder, ids, names)
        except OSError as e:
            box["error"] = "Can't read %s: %s" % (folder, e)
    t = threading.Thread(target=look, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return {"folder": folder, "error": "%s didn't answer in time (is that PC or drive on?)" % folder}
    if "error" in box:
        return {"folder": folder, "error": box["error"]}
    return {"folder": folder, "servers": box.get("servers") or []}


# --------------------------------------------------------------------------- #
# 2. the plan for one server
# --------------------------------------------------------------------------- #
def default_base(mgr) -> str:
    return (mgr.get_setting("default_install_dir", "") or "").strip() or os.path.join(
        os.path.splitdrive(str(mgr.config_path.parent))[0] + os.sep if os.name == "nt" else os.path.expanduser("~"),
        "GameServers")


def plan(mgr, entry: dict, base_dir: str, backup: Optional[str] = None, dir_memo: Optional[Dict[str, str]] = None,
         trusted: bool = True, taken_dirs: Optional[set] = None) -> dict:
    """{"server", "directory", "backup", "external", "warnings", "shared"} for one scanned entry."""
    if not entry.get("server"):
        raise ValueError("%s can't be brought over automatically: %s" % (entry.get("name"), entry.get("why_not")))
    old = dict(entry["server"])
    zips = {b["name"]: b for b in entry["backups"]}
    chosen = zips.get(backup) if backup else entry["backups"][0]
    if not chosen:
        raise ValueError("That backup isn't in the folder any more")
    man = _manifest(chosen["path"])
    dir_memo = dir_memo if dir_memo is not None else {}
    taken_dirs = taken_dirs if taken_dirs is not None else set()
    warnings: List[str] = []

    old_dir = (old.get("directory") or "").strip()
    shared = False
    key = _norm(old_dir) if old_dir else None
    if key and key in dir_memo:
        new_dir, shared = dir_memo[key], True          # cluster maps that shared one install
    else:
        leaf = _flavor(old_dir).basename(_flavor(old_dir).normpath(old_dir)) if old_dir else ""
        stem = _safe_name(leaf or old.get("name"))
        new_dir, i = os.path.join(base_dir, stem), 2
        while _norm(new_dir) in taken_dirs or (os.path.isdir(new_dir) and os.listdir(new_dir)):
            new_dir, i = os.path.join(base_dir, "%s %d" % (stem, i)), i + 1
        if key:
            dir_memo[key] = new_dir
    taken_dirs.add(_norm(new_dir))

    pairs: List[Tuple[str, str]] = []
    if old_dir:
        pairs.append((old_dir, new_dir))
    external: Dict[str, str] = {}
    here_home = os.path.expanduser("~")
    for i, (prefix, src) in enumerate(sorted((man.get("external") or {}).items())):
        moved = translate(src, pairs)
        if moved is None:
            home = home_of(src)
            if home:
                moved = _join(here_home, _under(src, home) or "")
                pairs.append((src, moved))
            else:
                leaf = _flavor(src).basename(_flavor(src).normpath(src)) or "data"
                moved = os.path.join(new_dir, "_external", "%d_%s" % (i, _safe_name(leaf)))
                pairs.append((src, moved))
                warnings.append("A save folder outside the server (%s) goes to %s." % (src, moved))
        external[prefix] = moved

    new = _rewrite(old, pairs)
    new["directory"] = new_dir
    for k in MACHINE_FIELDS + (() if trusted else UNTRUSTED_DROP):
        new.pop(k, None)
    exe = str(new.get("executable") or "")
    if exe and os.path.isabs(exe) and _under(exe, new_dir) is None:
        new.pop("executable", None)              # pointed outside the server: the installer sets it again
    mine = mgr.load_servers()
    names = {s.get("name") for s in mine}
    name, n = old.get("name") or entry["key"], 2
    base_name = name
    while name in names:
        name, n = ("%s (restored)" % base_name) if n == 2 else ("%s (restored %d)" % (base_name, n)), n + 1
    new["name"] = name
    new["manualStart"] = True                    # the same world must not run on two PCs
    new["broughtFrom"] = {"id": old.get("id"), "backup": chosen["name"], "at": time.time()}
    port = old.get("port")
    if port and any(str(s.get("port")) == str(port) for s in mine):
        warnings.append("Port %s is already used by another server here - change one before starting both."
                        % port)
    return {"server": new, "directory": new_dir, "backup": chosen, "external": external, "warnings": warnings,
            "shared": shared, "game_version": man.get("gameVersion")}


# --------------------------------------------------------------------------- #
# 3. do it
# --------------------------------------------------------------------------- #
def bring(mgr, entry: dict, base_dir: str, backup: Optional[str] = None, dir_memo=None, installed=None,
          trusted: bool = True, progress: Callable[[str], None] = print, taken_dirs=None) -> dict:
    """Set one server up here. {"server_id", "name", "notes", "warnings"}."""
    from core import backup_check
    p = plan(mgr, entry, base_dir, backup, dir_memo, trusted, taken_dirs)
    srv = p["server"]
    installed = installed if installed is not None else set()
    notes: List[str] = []
    progress("Checking the backup %s" % p["backup"]["name"])
    v = backup_check.verify_zip(p["backup"]["path"], deep=False)
    if not v["ok"]:
        raise RuntimeError("The backup is damaged: %s" % "; ".join(v["problems"]))
    os.makedirs(p["directory"], exist_ok=True)
    with mgr._lock:
        servers = mgr.load_servers()
        srv["id"] = "srv_" + str(int(time.time() * 1000))
        while any(s.get("id") == srv["id"] for s in servers):
            time.sleep(0.002)
            srv["id"] = "srv_" + str(int(time.time() * 1000))
        servers.append(srv)
        mgr.save_servers(servers)
    sid = srv["id"]
    mgr._audit("server_bring_here", sid, source=entry.get("key"), backup=p["backup"]["name"])
    progress("Copying the backup here")
    local = mgr.config_path.parent / "backups" / sid
    local.mkdir(parents=True, exist_ok=True)
    dest = local / p["backup"]["name"]
    shutil.copyfile(p["backup"]["path"], str(dest) + ".partial")
    os.replace(str(dest) + ".partial", dest)

    from core import game_installers as gi
    k = gi.kind(srv)
    if k is None:
        notes.append("Tofu can't download this game - copy its server files into %s." % p["directory"])
    elif _norm(p["directory"]) in installed:
        pass                                     # another map of the same install
    else:
        progress("Installing the game (this can take a while)")
        opts = {}
        if k == "vintage-story" and p.get("game_version"):
            opts["version"] = p["game_version"]
        if k == "minecraft":
            opts = {"flavor": srv.get("mcFlavor") or "vanilla", "version": srv.get("mcVersion")}
        try:
            res = mgr.install_game(sid, opts)
        except Exception as e:
            if opts.get("version") and k == "vintage-story":
                notes.append("Version %s couldn't be downloaded, so the newest was installed." % opts["version"])
                res = mgr.install_game(sid, {})
            else:
                raise RuntimeError("Installing the game failed: %s (the server was added - use Install "
                                   "to try again, then Restore)" % e)
        notes += [n for n in (res.get("notes") or []) if "EULA" not in n]
        installed.add(_norm(p["directory"]))
        if k == "hytale":
            notes.append("Sign the Hytale server in again on this PC (Checkup shows the button).")
    progress("Restoring the world")
    r = mgr.restore_server_world(sid, str(dest), external_map=p["external"], safety_backup=False)
    notes.append("Restored %d files from %s." % (r["restored"], p["backup"]["name"]))
    return {"server_id": sid, "name": srv["name"], "directory": p["directory"], "notes": notes,
            "warnings": p["warnings"]}


def start(mgr, picks: List[dict], base_dir: str, folder: Optional[str] = None, trusted: bool = True) -> str:
    """Bring several servers over in the background. picks: [{"key", "backup"?}].
    Progress is in JOBS[job id]."""
    found = scan(mgr, folder)
    if found.get("error"):
        raise RuntimeError(found["error"])
    by_key = {e["key"]: e for e in found["servers"]}
    todo = []
    for p in picks:
        e = by_key.get(p.get("key"))
        if not e:
            raise ValueError("%s isn't in that folder" % p.get("key"))
        if not e["can_bring"]:
            raise ValueError("%s: %s" % (e["name"], e["why_not"]))
        todo.append((e, p.get("backup")))
    if not todo:
        raise ValueError("Pick at least one server")
    base_dir = (base_dir or "").strip() or default_base(mgr)
    with _jobs_lock:
        if any(j.get("state") == "working" for j in JOBS.values()):
            raise RuntimeError("Servers are already being brought over - wait for that to finish.")
        jid = "bring_%d" % int(time.time() * 1000)
        JOBS[jid] = {"state": "working", "step": "Starting", "done": [], "failed": [], "total": len(todo),
                     "started": time.time()}
    job = JOBS[jid]

    def run():
        memo: Dict[str, str] = {}
        installed: set = set()
        taken: set = set()
        for e, b in todo:
            def prog(msg, _n=e["name"]):
                job["step"] = "%s: %s" % (_n, msg)
            try:
                job["done"].append(bring(mgr, e, base_dir, b, memo, installed, trusted, prog, taken))
            except Exception as ex:
                job["failed"].append({"name": e["name"], "error": str(ex)})
        job["state"] = "done" if not job["failed"] else ("failed" if not job["done"] else "partly")
        job["step"] = ""
        job["at"] = time.time()
    threading.Thread(target=run, daemon=True, name="bring-here").start()
    return jid


def job(jid: str) -> Optional[dict]:
    j = JOBS.get(jid)
    return json.loads(json.dumps(j)) if j else None


SAFETY_NOTE = ("Brought-over servers start only when you start them. Make sure the server isn't running on the "
               "old PC first - one world running on two PCs splits into two different worlds.")
