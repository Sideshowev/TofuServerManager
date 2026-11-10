"""Backups you know will restore.

A backup nobody has opened isn't proven. Once a week (setting
``backup_check_days``, 0 = off) the Watchdog opens the newest backup of every
server and checks it the way a restore would use it:

* every file in the zip reads back with the right checksum (zip CRC);
* BaoBun's manifest is there and the zip holds world/config files;
* SQLite worlds and databases inside (Vintage Story's .vcdbs, .db, .sqlite) are
  unpacked into a scratch folder and pass SQLite's own ``quick_check``;
* it didn't shrink to less than half the size of the last good one (a sign the
  world folder moved or went missing).

The copy on the second backup folder (another PC / NAS) gets the checksum check
too. Results live in runtime/backup_checks.json; Checkup, the desktop and web
backup lists and Discord show "last backup known to restore".
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from typing import Callable, Dict, List, Optional

SQLITE_EXT = (".vcdbs", ".db", ".sqlite", ".sqlite3")
SHRINK_RATIO = 0.5
MAX_DB_CHECK_BYTES = 8 * 1024 ** 3


def _state_path(mgr) -> str:
    return os.path.join(str(mgr.config_path.parent), "runtime", "backup_checks.json")


def results(mgr) -> Dict[str, dict]:
    try:
        with open(_state_path(mgr), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(mgr, data: dict):
    p = _state_path(mgr)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, p)


def verify_zip(path: str, deep: bool = True, scratch: Optional[str] = None) -> dict:
    """Check one backup zip. {"ok", "files", "bytes", "problems": [...]}"""
    problems: List[str] = []
    files = total = 0
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad:
                problems.append("%s is damaged inside the zip" % bad)
            infos = [i for i in z.infolist() if not i.is_dir()]
            names = {i.filename for i in infos}
            files = len([i for i in infos if i.filename != "baobun_backup.json"])
            total = sum(i.file_size for i in infos)
            if "baobun_backup.json" not in names:
                problems.append("it has no BaoBun manifest (made by something else, or cut short)")
            if files == 0:
                problems.append("it has no world or config files in it")
            if deep and not bad:
                dbs = [i for i in infos if i.filename.lower().endswith(SQLITE_EXT) and i.file_size > 0
                       and i.file_size < MAX_DB_CHECK_BYTES]
                if dbs:
                    work = tempfile.mkdtemp(prefix="baobun_check_", dir=scratch)
                    try:
                        for i in dbs:
                            out = os.path.join(work, "db_%d" % len(os.listdir(work)))
                            with z.open(i) as src, open(out, "wb") as dst:
                                shutil.copyfileobj(src, dst, 1 << 20)
                            res = _sqlite_ok(out)
                            if res is not True:
                                problems.append("world database %s won't open cleanly (%s)" % (i.filename, res))
                            os.remove(out)
                    finally:
                        shutil.rmtree(work, ignore_errors=True)
    except zipfile.BadZipFile:
        problems.append("it isn't a readable zip file")
    except OSError as e:
        problems.append("couldn't read it (%s)" % e)
    return {"ok": not problems, "files": files, "bytes": total, "problems": problems}


def _sqlite_ok(path: str):
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
        finally:
            con.close()
        return True if row and row[0] == "ok" else (row[0] if row else "no answer")
    except sqlite3.DatabaseError as e:
        return str(e)


def check_server(mgr, sid: str, scratch: Optional[str] = None, now: Optional[float] = None) -> Optional[dict]:
    """Check the newest backup of one server (and its copy on the second backup folder)."""
    now = time.time() if now is None else now
    backups = mgr.list_server_backups(sid)
    if not backups:
        return None
    newest = backups[0]
    res = verify_zip(newest["path"], deep=True, scratch=scratch)
    prev = results(mgr).get(sid) or {}
    last_good = prev.get("last_good") or {}
    if res["ok"] and last_good.get("bytes") and res["bytes"] < last_good["bytes"] * SHRINK_RATIO:
        res["problems"].append("it's much smaller than the last good backup (%.0f MB vs %.0f MB) - the world may "
                               "have moved" % (res["bytes"] / 1048576, last_good["bytes"] / 1048576))
        res["ok"] = False
    entry = {"name": newest["name"], "checked": now, "ok": res["ok"], "problems": res["problems"],
             "files": res["files"], "bytes": res["bytes"]}
    if res["ok"]:
        entry["last_good"] = {"name": newest["name"], "checked": now, "bytes": res["bytes"]}
    elif last_good:
        entry["last_good"] = last_good
    mirror = _mirror_copy(mgr, sid, newest["name"])
    if mirror:
        m = verify_zip(mirror, deep=False)
        entry["mirror"] = {"ok": m["ok"], "problems": m["problems"]}
    data = results(mgr)
    data[sid] = entry
    _save(mgr, data)
    return entry


def _mirror_copy(mgr, sid: str, name: str) -> Optional[str]:
    target = (mgr.get_setting("backup_mirror_dir", "") or "").strip()
    if not target:
        return None
    found: List[Optional[str]] = [None]

    def look():
        p = os.path.join(target, sid, name)
        if os.path.isfile(p):
            found[0] = p
    t = threading.Thread(target=look, daemon=True)
    t.start()
    t.join(10)          # an offline PC can make network paths hang
    return found[0]


def check_all(mgr, notify: Optional[Callable[[str, str], None]] = None,
              log: Callable[[str], None] = print) -> Dict[str, dict]:
    out = {}
    bad = []
    for srv in mgr.load_servers():
        sid = srv.get("id")
        if not sid:
            continue
        try:
            r = check_server(mgr, sid)
        except Exception as e:
            log("[WARN] backup check for %s: %s" % (srv.get("name") or sid, e))
            continue
        if r is None:
            continue
        out[sid] = r
        if not r["ok"]:
            bad.append("**%s**: %s" % (srv.get("name") or sid, "; ".join(r["problems"])))
        elif r.get("mirror") and not r["mirror"]["ok"]:
            bad.append("**%s** (copy on the other PC): %s" % (srv.get("name") or sid,
                                                              "; ".join(r["mirror"]["problems"])))
    data = results(mgr)
    data["_last_run"] = time.time()
    _save(mgr, data)
    if bad and notify:
        notify("⚠️ A backup failed its restore check",
               "\n".join(bad) + "\nMake a new backup (and look at why) before you need it.")
    log("[INFO] backup check: %d checked, %d with problems" % (len(out), len(bad)))
    return out


def due(mgr, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    try:
        days = float(mgr.get_setting("backup_check_days", 7) or 0)
    except (TypeError, ValueError):
        days = 7.0
    if days <= 0:
        return False
    last = results(mgr).get("_last_run") or 0
    return now - float(last) >= days * 86400


def summary(mgr, sid: str) -> Optional[dict]:
    """{"text", "ok"} for the backup lists, or None when never checked."""
    r = results(mgr).get(sid)
    if not r:
        return None
    good = r.get("last_good")
    if r.get("ok"):
        text = "Newest backup checked %s - it restores." % _ago(r["checked"])
    elif good:
        text = "Newest backup failed its check (%s). Last backup known to restore: %s." % (
            "; ".join(r.get("problems") or []), good.get("name"))
    else:
        text = "Newest backup failed its check: %s." % "; ".join(r.get("problems") or [])
    if r.get("mirror") and not r["mirror"]["ok"]:
        text += " The copy on the other PC is damaged."
    return {"text": text, "ok": bool(r.get("ok"))}


def _ago(ts: float) -> str:
    d = time.time() - float(ts or 0)
    if d < 3600:
        return "just now"
    if d < 86400:
        return "%d hours ago" % (d // 3600)
    return "%d days ago" % (d // 86400)
