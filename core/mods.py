"""One Mods tab for every game. Each server gets a backend for its mod source
(see core.mod_sources): Vintage Story ModDB, server-downloaded ID lists
(CurseForge / Workshop / Reforger), Steam Workshop via SteamCMD, Thunderstore,
Modrinth, uMod, or a plain folder.

Every backend answers the same questions (list / install / remove / on-off /
update ...) so the desktop and web Mods tabs work the same for all of them.

Safety for all sources:
  * nothing is deleted - removed mods go to BaoBun's backups/<server>/removed-mods
  * the world is backed up before installs and updates (unless a backup is
    less than 30 minutes old)
  * changes made while a server runs show "restart to apply"
"""

from __future__ import annotations

import os
import re
import time
import urllib.error
from typing import Dict, List, Optional

from core.mod_sources import profile_for
from core.mods_common import (ModError, ci as _ci, lenient_json as _lenient_json, move_to,
                              parse_vdf, size_of, steam_details as _steam_details, workshop_id)

BACKUP_FRESH_SECONDS = 30 * 60
VS_BASE_DEPS = {"game", "survival", "creative", "essentials", "vssurvivalmod", "vsessentials",
                "vscreativemod"}
OPTION_KEYS = {"modSource", "modFolder", "workshopAppId", "mcLoader", "mcVersion"}
GLOBAL_OPTION_KEYS = {"curseforge_api_key"}

__all__ = ["ModManager", "ModError", "read_vs_modinfo", "workshop_id", "parse_vdf"]


def read_vs_modinfo(path: str) -> Optional[dict]:
    """modinfo.json of a VS mod zip or folder as a dict, or None."""
    import zipfile
    try:
        if os.path.isdir(path):
            mi = os.path.join(path, "modinfo.json")
            if not os.path.isfile(mi):
                return None
            with open(mi, encoding="utf-8-sig", errors="replace") as f:
                return _lenient_json(f.read())
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith("modinfo.json")]
            if not names:
                return None
            names.sort(key=lambda n: n.count("/"))
            return _lenient_json(z.read(names[0]).decode("utf-8-sig", "replace"))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Vintage Story ModDB
# --------------------------------------------------------------------------- #
class VSBackend:
    def __init__(self, mm, profile):
        self.mm, self.mgr, self.p = mm, mm.mgr, profile

    def caps(self):
        return {"toggle": True, "reorder": False, "update": True, "search": True, "add": True, "fields": [],
                "deps": True, "add_hint": "Mod ID from the ModDB (e.g. carryon) - or search"}

    def folders(self, srv):
        mods = self.mgr._vs_mods_folder(srv)
        return mods, os.path.join(os.path.dirname(mods), "Mods-disabled")

    def list(self, srv, check):
        folder, disabled = self.folders(srv)
        mods = []
        for where, enabled in ((folder, True), (disabled, False)):
            if not os.path.isdir(where):
                continue
            for fn in sorted(os.listdir(where), key=str.lower):
                p = os.path.join(where, fn)
                low = fn.lower()
                if fn.startswith((".", "_")) or low.endswith((".partial", ".pdb", ".txt", ".md")):
                    continue
                if not (low.endswith((".zip", ".dll", ".cs")) or os.path.isdir(p)):
                    continue
                info = read_vs_modinfo(p) if (low.endswith(".zip") or os.path.isdir(p)) else None
                if os.path.isdir(p) and info is None:
                    continue
                info = info or {}
                deps = _ci(info, "dependencies", default={}) or {}
                mods.append({
                    "key": fn, "file": fn, "enabled": enabled,
                    "id": str(_ci(info, "modid", default="") or os.path.splitext(fn)[0]).lower(),
                    "name": _ci(info, "name", default="") or os.path.splitext(fn)[0],
                    "version": str(_ci(info, "version", default="") or ""),
                    "side": _ci(info, "side", default=""),
                    "deps": {str(d).lower(): str(v) for d, v in deps.items()} if isinstance(deps, dict) else {},
                    "size": size_of(p),
                })
        on_ids = {m["id"] for m in mods if m["enabled"]}
        count: Dict[str, int] = {}
        for m in mods:
            m["missing_deps"] = sorted(d for d in m["deps"] if d not in VS_BASE_DEPS and d not in on_ids)
            if m["enabled"]:
                count[m["id"]] = count.get(m["id"], 0) + 1
        for m in mods:
            m["duplicate"] = m["enabled"] and count.get(m["id"], 0) > 1
        gv = None
        try:
            gv = self.mgr.vs_server_version(srv["id"])
        except Exception:
            pass
        if check:
            for m in mods:
                m["update_available"] = False
                if not m["id"] or m["file"].lower().endswith((".dll", ".cs")):
                    continue
                try:
                    mod = self.mgr.vs_get_mod(m["id"])
                    rel, match = self.mgr._vs_pick_release(mod.get("releases", []) if isinstance(mod, dict) else [], gv)
                    m["latest"] = (rel or {}).get("modversion", "")
                    m["compatible"] = match in ("exact", "minor", "unknown_version")
                    m["update_available"] = bool(m["latest"] and m["latest"] != m["version"] and m["compatible"])
                    m["moddb_id"] = mod.get("modid") if isinstance(mod, dict) else None
                except Exception as e:
                    m["check_error"] = str(e)[:200]
        return mods, {"folder": folder, "game_version": gv}

    def _find(self, srv, key):
        folder, disabled = self.folders(srv)
        key = os.path.basename(str(key or ""))
        if not key or key in (".", ".."):
            raise ModError("No mod given")
        for where, enabled in ((folder, True), (disabled, False)):
            p = os.path.join(where, key)
            if os.path.exists(p):
                return p, enabled
        raise ModError("Mod %s not found" % key)

    def set_enabled(self, srv, key, on):
        path, is_on = self._find(srv, key)
        if is_on == bool(on):
            return {"changed": False}
        folder, disabled = self.folders(srv)
        move_to(path, folder if on else disabled)
        self.mm._changed(srv["id"])
        return {"changed": True}

    def remove(self, srv, key):
        path, _ = self._find(srv, key)
        kept = move_to(path, self.mm.trash(srv["id"]))
        self.mm._changed(srv["id"])
        return {"removed": os.path.basename(path), "kept_in": kept}

    def install(self, srv, ref):
        mod = str(ref or "").strip()
        m = re.search(r"mods\.vintagestory\.at/(?:show/mod/)?([\w-]+)", mod)
        if m:
            mod = m.group(1)
        if not mod:
            raise ModError("Enter a mod name or ModDB link")
        try:
            r = self.mgr.vs_download_mod(srv["id"], int(mod) if mod.isdigit() else mod)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise ModError("Mod '%s' wasn't found on the ModDB. Use the mod's ID (the modid in its "
                               "modinfo, e.g. carryon) or pick it from Search." % mod)
            raise ModError("The ModDB didn't answer (%s). Try again in a minute." % e.code)
        self.mm._changed(srv["id"])
        return {"installed": r["filename"], "replaced": r.get("replaced", []), "note": r.get("note")}

    def install_deps(self, srv):
        mods, _ = self.list(srv, False)
        missing = sorted({d for m in mods if m["enabled"] for d in m.get("missing_deps", [])})
        done, failed = [], []
        for dep in missing:
            try:
                self.install(srv, dep)
                done.append(dep)
            except Exception as e:
                failed.append({"mod": dep, "error": str(e)[:200]})
        return {"installed": done, "failed": failed}

    def update(self, srv, keys=None):
        mods, _ = self.list(srv, True)
        todo = [m for m in mods if m.get("update_available") and m["enabled"] and (keys is None or m["key"] in keys)]
        done, failed = [], []
        for m in todo:
            try:
                self.install(srv, m.get("moddb_id") or m["id"])
                done.append({"mod": m["name"], "from": m.get("version"), "to": m.get("latest")})
            except Exception as e:
                failed.append({"mod": m["name"], "error": str(e)[:200]})
        return {"updated": done, "failed": failed, "message": "" if todo else "Everything is up to date."}

    VS_SORT = {"popular": "downloads", "trending": "trendingpoints", "updated": "lastreleased",
               "newest": "asset.created", "follows": "follows"}

    def search(self, srv, text, sort=None):
        from core.mod_browse import safe_img, _ids
        out = []
        order = self.VS_SORT.get(sort or "", "downloads")
        for m in self.mgr.vs_search_mods(text, order, "desc", 60):
            out.append({"id": m.get("modid"), "name": m.get("name"), "summary": (m.get("summary") or "")[:200],
                        "author": m.get("author"), "downloads": m.get("downloads"), "follows": m.get("follows"),
                        "icon": safe_img(m.get("logo")), "tags": m.get("tags") or [], "side": m.get("side"),
                        "updated": m.get("lastreleased"),
                        "match_ids": _ids(m.get("modid"), m.get("urlalias"), *(m.get("modidstrs") or [])),
                        "url": ("https://mods.vintagestory.at/" + m["urlalias"]) if m.get("urlalias")
                        else "https://mods.vintagestory.at/show/mod/%s" % m.get("assetid", m.get("modid"))})
        return out

    def details(self, srv, mod_id):
        from core.mod_browse import vs_details
        return vs_details(self.mgr, srv, mod_id)


class NoneBackend:
    def __init__(self, mm, profile):
        self.mm, self.mgr, self.p = mm, mm.mgr, profile

    def caps(self):
        return {"toggle": False, "reorder": False, "update": False, "search": False, "add": False, "fields": []}

    def list(self, srv, check):
        return [], {}


def _backend_class(source):
    if source == "vs_moddb":
        return VSBackend
    if source == "id_list":
        from core.mods_ids import IdListBackend
        return IdListBackend
    if source == "workshop":
        from core.mods_workshop import WorkshopBackend
        return WorkshopBackend
    from core import mods_sites
    return {"thunderstore": mods_sites.ThunderstoreBackend, "modrinth": mods_sites.ModrinthBackend,
            "umod": mods_sites.UModBackend, "folder": mods_sites.FolderBackend}.get(source, NoneBackend)


# --------------------------------------------------------------------------- #
class ModManager:
    def __init__(self, manager):
        self.mgr = manager

    # -- plumbing ------------------------------------------------------------------
    def _srv(self, server_id: str) -> dict:
        srv = self.mgr.get_server(server_id)
        if not srv:
            raise ModError("Server not found")
        return dict(srv)

    def backend(self, srv):
        prof = profile_for(srv)
        return _backend_class(prof.get("source"))(self, prof), prof

    @staticmethod
    def steam_details(ids):
        return _steam_details(ids)

    def trash(self, server_id: str) -> str:
        return os.path.join(str(self.mgr.config_path.parent), "backups", server_id, "removed-mods")

    def _before_change(self, server_id: str, backup: bool) -> Optional[str]:
        if not backup:
            return None
        try:
            latest = (self.mgr.list_server_backups(server_id) or [None])[0]
            if latest and time.time() - os.path.getmtime(latest["path"]) < BACKUP_FRESH_SECONDS:
                return None
            r = self.mgr.backup_server_world(server_id, reason="before-mods")
            return "World backed up first (%s)." % os.path.basename(r.get("zip_path", ""))
        except Exception as e:
            return "Couldn't back up the world first (%s)." % e

    def _changed(self, server_id: str):
        try:
            self.mgr._write_registry(server_id, mods_changed_at=time.time())
        except Exception:
            pass

    def _restart_needed(self, server_id: str) -> bool:
        try:
            if not self.mgr.is_running(server_id):
                return False
            rec = self.mgr._read_registry(server_id)
            return float(rec.get("mods_changed_at") or 0) > float(rec.get("started_at") or 0)
        except Exception:
            return False

    def _audit(self, server_id, action, user, **details):
        try:
            self.mgr._audit("mods_" + action, server_id, user=user, **details)
        except Exception:
            pass

    def _call(self, server_id, op, *args):
        srv = self._srv(server_id)
        be, prof = self.backend(srv)
        fn = getattr(be, op, None)
        if fn is None:
            raise ModError("%s can't do that." % (prof.get("label") or "This mod source"))
        return srv, be, prof, fn(srv, *args)

    def _finish(self, server_id, be, r):
        if isinstance(r, dict):
            r["restart_needed"] = (not be.caps().get("no_restart")) and self.mgr.is_running(server_id)
            if not r.get("note"):
                r.pop("note", None)
        return r

    # -- public API -------------------------------------------------------------------
    def list(self, server_id: str, check_updates: bool = False) -> dict:
        srv = self._srv(server_id)
        be, prof = self.backend(srv)
        mods, extra = be.list(srv, check_updates)
        out = {"server_id": server_id, "source": prof.get("source"), "kind": prof.get("source"),
               "label": prof.get("label"), "note": prof.get("note"), "caps": be.caps(), "mods": mods,
               "restart_needed": self._restart_needed(server_id) and not be.caps().get("no_restart"),
               "options": self.options(srv, prof, extra)}
        out.update(extra)
        from core.mod_updates import pins, is_pinned, mode, included
        pinned = set(pins(srv))
        for m in mods:
            m.pop("_new", None)
            m["pinned"] = is_pinned(m, pinned)
        out["auto_update"] = {"mode": mode(self.mgr), "included": included(srv)}
        return out

    def options(self, srv, prof, extra) -> List[dict]:
        src = prof.get("source")
        opts = [{"key": "modSource", "label": "Mod source", "type": "select",
                 "value": srv.get("modSource") or "",
                 "choices": [["", "Automatic (%s)" % (profile_for(dict(srv, modSource="")).get("label") or "none")],
                             ["workshop", "Steam Workshop download"], ["folder", "A mods folder"],
                             ["none", "No mods"]]}]
        if (srv.get("modSource") or "") == "folder":
            opts.append({"key": "modFolder", "label": "Mods folder (inside the server folder)", "type": "text",
                         "value": srv.get("modFolder") or "mods"})
        if src in ("workshop",) or (src == "id_list" and prof.get("site") == "workshop"):
            opts.append({"key": "workshopAppId", "label": "Workshop App ID", "type": "text",
                         "value": srv.get("workshopAppId") or "",
                         "placeholder": prof.get("workshop_appid") or srv.get("appId") or ""})
        if src == "modrinth":
            opts.append({"key": "mcLoader", "label": "Loader", "type": "select", "value": srv.get("mcLoader") or "",
                         "choices": [["", "Detect (%s)" % (extra.get("loader") or "unknown")]] +
                                    [[x, x.title()] for x in ("fabric", "forge", "neoforge", "quilt", "paper",
                                                              "purpur", "spigot", "folia")]})
            opts.append({"key": "mcVersion", "label": "Minecraft version", "type": "text",
                         "value": srv.get("mcVersion") or "", "placeholder": extra.get("game_version") or "e.g. 1.21.1"})
        if src == "id_list" and prof.get("site") == "curseforge":
            opts.append({"key": "curseforge_api_key", "label": "CurseForge API key (optional, shows mod names)",
                         "type": "secret", "global": True,
                         "value": "saved" if self.mgr.get_setting("curseforge_api_key", "") else ""})
        return opts

    def set_option(self, server_id: str, key: str, value, user: str = "system") -> dict:
        value = "" if value is None else str(value).strip()
        if key in GLOBAL_OPTION_KEYS:
            if str(user).startswith("web:"):
                raise ModError("API keys can only be set in BaoBun on the server PC.")
            self.mgr.set_setting(key, value)
            return {"changed": True}
        if key not in OPTION_KEYS:
            raise ModError("Unknown option")
        if key == "modSource" and value not in ("", "workshop", "folder", "none"):
            raise ModError("Unknown mod source")
        if key == "workshopAppId" and value and not value.isdigit():
            raise ModError("Workshop App ID must be a number")
        if key == "mcLoader" and value and value not in ("fabric", "forge", "neoforge", "quilt", "paper",
                                                         "purpur", "spigot", "folia"):
            raise ModError("Unknown loader")
        if key == "mcVersion" and value and not re.match(r"^\d+\.\d+(\.\d+)?$", value):
            raise ModError("Minecraft version looks like 1.21.1")
        if key == "modFolder":
            value = value.replace("\\", "/").strip("/")
            if not value or ".." in value.split("/") or os.path.isabs(value) or ":" in value:
                raise ModError("Use a folder inside the server folder, e.g. Mods")
        servers = self.mgr.load_servers()
        for s in servers:
            if s.get("id") == server_id:
                if value:
                    s[key] = value
                else:
                    s.pop(key, None)
        self.mgr.save_servers(servers)
        self._audit(server_id, "option", user, option=key)
        return {"changed": True}

    @staticmethod
    def split_refs(ref: str, source: str, writer: str = "") -> List[str]:
        """Several mods pasted at once: one per line, or separated by spaces (or commas
        for plain number lists like 123,456)."""
        text = str(ref or "").strip()
        parts = [p for p in re.split(r"\s+", text) if p]
        out = []
        for p in parts:
            if writer != "pz_ini" and re.fullmatch(r"\d+(,\d+)+", p):
                out += p.split(",")
            else:
                out.append(p)
        return out or [text]

    def install(self, server_id: str, ref: str, user: str = "system", backup: bool = True) -> dict:
        srv = self._srv(server_id)
        be, prof = self.backend(srv)
        if not be.caps().get("add"):
            raise ModError(prof.get("note") or "This server has no mod source. Pick one in the options.")
        refs = self.split_refs(ref, prof.get("source"), prof.get("writer", ""))
        looks = [r for r in refs if re.fullmatch(r"\d{5,}(:[\w;,.-]+)?|https?://\S+|[0-9A-Fa-f]{16}|[\w.]+-[\w.]+", r)]
        if len(refs) > 1 and len(looks) >= 2:
            return self.install_many(server_id, refs, user=user, backup=backup)
        note = self._before_change(server_id, backup and prof.get("source") != "id_list")
        r = be.install(srv, ref)
        if note:
            r["backup"] = note
        self._audit(server_id, "install", user, mod=str(r.get("installed"))[:80])
        return self._finish(server_id, be, r)

    def install_many(self, server_id: str, refs: List[str], user: str = "system", backup: bool = True) -> dict:
        srv = self._srv(server_id)
        be, prof = self.backend(srv)
        note = self._before_change(server_id, backup and prof.get("source") != "id_list")
        done, failed, notes = [], [], []
        for ref in refs[:200]:
            try:
                r = be.install(self._srv(server_id), ref)
                done.append(str(r.get("installed") or ref))
                if r.get("note") and r["note"] not in notes:
                    notes.append(r["note"])
            except Exception as e:
                failed.append({"mod": ref, "error": str(e)[:160]})
        if done:
            self._audit(server_id, "install", user, mod="%d mods" % len(done))
        r = {"installed": done, "failed": failed}
        if notes:
            r["note"] = " ".join(notes[:3])
        if note:
            r["backup"] = note
        return self._finish(server_id, be, r)

    def install_file(self, server_id: str, path: str, user: str = "system") -> dict:
        srv = self._srv(server_id)
        be, prof = self.backend(srv)
        low = path.lower()
        if prof.get("source") in ("workshop", "id_list") and low.endswith((".html", ".htm", ".txt")):
            # an Arma 3 Launcher preset (or any list of Workshop links / IDs)
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            ids = list(dict.fromkeys(re.findall(r"[?&]id=(\d{5,})", text))) or \
                [x for x in re.findall(r"\b\d{6,}\b", text)]
            if not ids:
                raise ModError("No Workshop links or IDs were found in that file.")
            return self.install_many(server_id, ids, user=user)
        srv, be, _prof, r = self._call(server_id, "install_file", path)
        self._audit(server_id, "install", user, mod=os.path.basename(path))
        return self._finish(server_id, be, r)

    def remove(self, server_id: str, key: str, user: str = "system") -> dict:
        srv, be, _prof, r = self._call(server_id, "remove", key)
        self._audit(server_id, "remove", user, mod=str(key)[:80])
        return self._finish(server_id, be, r)

    def set_enabled(self, server_id: str, key: str, enabled: bool, user: str = "system") -> dict:
        srv, be, _prof, r = self._call(server_id, "set_enabled", key, bool(enabled))
        self._audit(server_id, "enable" if enabled else "disable", user, mod=str(key)[:80])
        return self._finish(server_id, be, r)

    def move(self, server_id: str, key: str, delta: int, user: str = "system") -> dict:
        srv, be, _prof, r = self._call(server_id, "move", key, -1 if int(delta) < 0 else 1)
        return self._finish(server_id, be, r)

    def set_field(self, server_id: str, key: str, field: str, value, user: str = "system") -> dict:
        srv, be, _prof, r = self._call(server_id, "set_field", key, field, value)
        return self._finish(server_id, be, r)

    def update(self, server_id: str, keys: Optional[List[str]] = None, user: str = "system",
               backup: bool = True) -> dict:
        srv = self._srv(server_id)
        be, prof = self.backend(srv)
        if not be.caps().get("update"):
            return {"updated": [], "failed": [], "message": "The server updates these mods itself when it starts."}
        skipped = []
        if keys is None:
            from core.mod_updates import pins, is_pinned
            pinned = set(pins(srv))
            if pinned:
                mods, _x = be.list(srv, False)
                keys = [x["key"] for x in mods if not is_pinned(x, pinned)]
                skipped = [x.get("name") or x["key"] for x in mods if is_pinned(x, pinned)]
                if not keys:
                    return {"updated": [], "failed": [], "message": "Every mod is kept at its version (pinned)."}
        note = self._before_change(server_id, backup)
        r = be.update(srv, keys)
        if skipped:
            r["pinned_skipped"] = skipped
        if note and r.get("updated"):
            r["backup"] = note
        if r.get("updated"):
            self._audit(server_id, "update", user, count=len(r["updated"]))
        return self._finish(server_id, be, r)

    def set_pin(self, server_id: str, key: str, pinned: bool, user: str = "system") -> dict:
        """Keep a mod at its version (skipped by Update all and automatic updates)."""
        from core import mod_updates
        srv = self._srv(server_id)
        be, prof = self.backend(srv)
        if not be.caps().get("update"):
            raise ModError("%s always downloads the newest version of its mods by itself, so they can't be kept at "
                           "a version here." % (prof.get("label") or "This game"))
        mods, _x = be.list(srv, False)
        m = next((x for x in mods if x["key"] == key or mod_updates.pin_id(x) == key), None)
        ident = mod_updates.pin_id(m) if m else str(key)
        mod_updates.set_pin(self.mgr, server_id, ident, bool(pinned), user=user)
        return {"pinned": bool(pinned), "mod": (m or {}).get("name") or key}

    def set_auto_update(self, server_id: str, included: bool, user: str = "system") -> dict:
        servers = self.mgr.load_servers()
        for s in servers:
            if s.get("id") == server_id:
                if included:
                    s.pop("modAutoUpdate", None)
                else:
                    s["modAutoUpdate"] = False
        self.mgr.save_servers(servers)
        self._audit(server_id, "auto_update", user, included=bool(included))
        return {"included": bool(included)}

    def install_missing_deps(self, server_id: str, user: str = "system") -> dict:
        srv = self._srv(server_id)
        be, _prof = self.backend(srv)
        if not hasattr(be, "install_deps"):
            return {"installed": [], "failed": []}
        self._before_change(server_id, True)
        return self._finish(server_id, be, be.install_deps(srv))

    def search(self, server_id: str, text: str, sort: Optional[str] = None) -> List[dict]:
        srv = self._srv(server_id)
        be, prof = self.backend(srv)
        if not be.caps().get("search"):
            raise ModError("Search isn't available for %s - paste the mod's ID or link instead."
                           % (prof.get("label") or "this game"))
        try:
            return be.search(srv, text, sort=sort)
        except TypeError:
            return be.search(srv, text)

    def details(self, server_id: str, mod_id: str) -> dict:
        """Everything about one mod for the browser's details view."""
        srv = self._srv(server_id)
        be, prof = self.backend(srv)
        if not hasattr(be, "details"):
            raise ModError("Mod details aren't available for %s." % (prof.get("label") or "this game"))
        try:
            return be.details(srv, str(mod_id))
        except ModError:
            raise
        except Exception as e:
            raise ModError("Couldn't load that mod's page (%s)." % e)

    def setup(self, server_id: str, action: str, user: str = "system") -> dict:
        srv, be, _prof, r = self._call(server_id, "setup", action)
        self._changed(server_id)
        return self._finish(server_id, be, r)
