"""Mods from community sites (Thunderstore, Modrinth, uMod) and plain mods folders."""

from __future__ import annotations

import glob
import json
import os
import re
import urllib.parse
from typing import Dict, List, Optional

from core.mods_common import (ModError, extract_zip, friendly_http, http_bytes, http_json, move_to,
                              safe_name, sha1_file, size_of)


def _folder_list(path: str, enabled: bool, skip=()) -> List[dict]:
    out = []
    if not os.path.isdir(path):
        return out
    for fn in sorted(os.listdir(path), key=str.lower):
        if fn.startswith(".") or fn in skip:
            continue
        p = os.path.join(path, fn)
        out.append({"key": fn, "id": fn, "name": fn, "enabled": enabled, "size": size_of(p), "version": ""})
    return out


# --------------------------------------------------------------------------- #
# Thunderstore (Valheim, V Rising) - BepInEx mods
# --------------------------------------------------------------------------- #
class ThunderstoreBackend:
    API = "https://thunderstore.io/api/experimental/package/%s/%s/"

    def __init__(self, mm, profile):
        self.mm, self.mgr, self.p = mm, mm.mgr, profile

    def caps(self):
        return {"toggle": True, "reorder": False, "update": True, "search": False, "add": True, "fields": [],
                "add_hint": "Thunderstore mod: Author-ModName, or its thunderstore.io link"}

    def _dirs(self, srv):
        d = srv.get("directory") or ""
        return (os.path.join(d, "BepInEx", "plugins"), os.path.join(d, "BepInEx", "plugins-disabled"))

    @staticmethod
    def parse_ref(ref):
        s = str(ref or "").strip()
        m = re.search(r"/p(?:ackage)?/([\w.]+)/([\w.]+)", s)
        if m:
            return m.group(1), m.group(2)
        m = re.match(r"^([\w.]+)[-/]([\w.]+?)(?:-\d+\.\d+\.\d+)?$", s)
        if m:
            return m.group(1), m.group(2)
        raise ModError("Enter the mod as Author-ModName (e.g. ValheimModding-Jotunn) or paste its "
                       "thunderstore.io link.")

    def package(self, ns, name) -> dict:
        try:
            return http_json(self.API % (urllib.parse.quote(ns), urllib.parse.quote(name)), timeout=30)
        except Exception as e:
            raise friendly_http(e, "Thunderstore package %s-%s" % (ns, name))

    def bepinex_installed(self, srv) -> bool:
        return os.path.isdir(os.path.join(srv.get("directory") or "", "BepInEx", "core"))

    def ensure_bepinex(self, srv) -> Optional[str]:
        if self.bepinex_installed(srv):
            return None
        ns, name = self.p["bepinex"].split("-", 1)
        pkg = self.package(ns, name)
        data = http_bytes(pkg["latest"]["download_url"])
        import io
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(data))
        # the pack's files sit in one folder that contains BepInEx/ (e.g. BepInExPack_Valheim/)
        roots = sorted({n.split("/")[0] for n in z.namelist() if "/BepInEx/" in "/" + n})
        sub = next((r for r in roots if any(n.startswith(r + "/BepInEx/") for n in z.namelist())), None)
        extract_zip(data, srv.get("directory") or "", subdir=sub)
        os.makedirs(self._dirs(srv)[0], exist_ok=True)
        return "Installed BepInEx %s." % pkg["latest"].get("version_number", "")

    def installed(self, srv) -> Dict[str, dict]:
        """{"Namespace-Name": manifest} for installed packages (on and off)."""
        out = {}
        for base, on in ((self._dirs(srv)[0], True), (self._dirs(srv)[1], False)):
            for mf in glob.glob(os.path.join(base, "*", "manifest.json")):
                try:
                    with open(mf, encoding="utf-8-sig") as fh:
                        m = json.load(fh)
                except Exception:
                    continue
                folder = os.path.basename(os.path.dirname(mf))
                m["_folder"], m["_on"] = folder, on
                out[folder] = m
        return out

    def install(self, srv, ref, _seen=None):
        ns, name = self.parse_ref(ref)
        note = self.ensure_bepinex(srv)
        seen = _seen if _seen is not None else set()
        full = "%s-%s" % (ns, name)
        if full in seen:
            return {"installed": full}
        seen.add(full)
        pkg = self.package(ns, name)
        latest = pkg["latest"]
        plugins = self._dirs(srv)[0]
        dest = os.path.join(plugins, full)
        if os.path.isdir(dest):
            move_to(dest, self.mm.trash(srv["id"]))
        extract_zip(http_bytes(latest["download_url"]), dest)
        if not os.path.isfile(os.path.join(dest, "manifest.json")):
            with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"name": name, "version_number": latest.get("version_number", ""),
                           "dependencies": latest.get("dependencies", [])}, f)
        deps_done = []
        have = self.installed(srv)
        for dep in latest.get("dependencies") or []:
            m = re.match(r"^([\w.]+)-([\w.]+)-\d", dep)
            if not m or "BepInExPack" in m.group(2):
                continue
            key = "%s-%s" % (m.group(1), m.group(2))
            if key in have or key in seen:
                continue
            self.install(srv, key, seen)
            deps_done.append(key)
        self.mm._changed(srv["id"])
        r = {"installed": "%s %s" % (pkg.get("name", name), latest.get("version_number", ""))}
        notes = [n for n in (note, ("Also installed: " + ", ".join(deps_done)) if deps_done else None) if n]
        if notes:
            r["note"] = " ".join(notes)
        return r

    def remove(self, srv, key):
        for base in self._dirs(srv):
            p = os.path.join(base, os.path.basename(key))
            if os.path.exists(p):
                kept = move_to(p, self.mm.trash(srv["id"]))
                self.mm._changed(srv["id"])
                return {"removed": key, "kept_in": kept}
        raise ModError("Mod not found")

    def set_enabled(self, srv, key, on):
        on_dir, off_dir = self._dirs(srv)
        src = os.path.join(off_dir if on else on_dir, os.path.basename(key))
        if not os.path.exists(src):
            return {"changed": False}
        move_to(src, on_dir if on else off_dir)
        self.mm._changed(srv["id"])
        return {"changed": True}

    def list(self, srv, check):
        mods = []
        have = self.installed(srv)
        for folder, m in sorted(have.items(), key=lambda kv: kv[0].lower()):
            path = os.path.join(self._dirs(srv)[0 if m["_on"] else 1], folder)
            ns_name = folder.split("-", 1)
            mods.append({"key": folder, "id": folder, "name": m.get("name") or folder,
                         "version": m.get("version_number") or "", "enabled": m["_on"],
                         "size": size_of(path), "summary": m.get("description"),
                         "url": "https://thunderstore.io/c/%s/p/%s/%s/" % (self.p.get("community", ""), *ns_name)
                         if len(ns_name) == 2 else None})
        # loose plugins without a manifest (copied in by hand)
        for base, on in ((self._dirs(srv)[0], True), (self._dirs(srv)[1], False)):
            for x in _folder_list(base, on, skip=set(have)):
                x["manual"] = True
                mods.append(x)
        if check:
            for m in mods:
                if m.get("manual") or "-" not in m["key"]:
                    continue
                try:
                    ns, name = m["key"].split("-", 1)
                    latest = self.package(ns, name)["latest"]
                    m["latest"] = latest.get("version_number")
                    m["update_available"] = bool(m["latest"] and m["latest"] != m["version"])
                except Exception as e:
                    m["check_error"] = str(e)[:150]
        extra = {"folder": self._dirs(srv)[0]}
        if not self.bepinex_installed(srv):
            extra["hint"] = "BepInEx isn't installed yet - Tofu installs it with the first mod."
            extra["setup"] = [{"action": "install_bepinex", "label": "Install BepInEx now"}]
        return mods, extra

    def update(self, srv, keys=None):
        mods, _ = self.list(srv, True)
        todo = [m for m in mods if m.get("update_available") and (keys is None or m["key"] in keys)]
        done, failed = [], []
        for m in todo:
            try:
                was_on = m["enabled"]
                self.install(srv, m["key"])
                if not was_on:
                    self.set_enabled(srv, m["key"], False)
                done.append({"mod": m["name"], "from": m["version"], "to": m.get("latest")})
            except Exception as e:
                failed.append({"mod": m["name"], "error": str(e)[:200]})
        return {"updated": done, "failed": failed, "message": "" if todo else "Everything is up to date."}

    def setup(self, srv, action):
        if action != "install_bepinex":
            raise ModError("Unknown action")
        return {"note": self.ensure_bepinex(srv) or "BepInEx is already installed."}


# --------------------------------------------------------------------------- #
# Modrinth (Minecraft Java)
# --------------------------------------------------------------------------- #
PLUGIN_LOADERS = ("paper", "purpur", "spigot", "bukkit", "folia")
MOD_LOADERS = ("fabric", "forge", "neoforge", "quilt")


class ModrinthBackend:
    API = "https://api.modrinth.com/v2"

    def __init__(self, mm, profile):
        self.mm, self.mgr, self.p = mm, mm.mgr, profile

    def caps(self):
        return {"toggle": True, "reorder": False, "update": True, "search": True, "add": True, "fields": [],
                "add_hint": "Modrinth project (e.g. sodium or lithium) or its modrinth.com link"}

    # -- what the server runs ----------------------------------------------------------
    def detect(self, srv):
        d = srv.get("directory") or ""
        loader = (srv.get("mcLoader") or "").lower() or None
        version = srv.get("mcVersion") or None
        names = [n.lower() for n in os.listdir(d)] if os.path.isdir(d) else []
        if not loader:
            if any(n.startswith("fabric-server") for n in names) or ".fabric" in names:
                loader = "fabric"
            elif any(n.startswith("quilt-server") for n in names):
                loader = "quilt"
            elif os.path.isdir(os.path.join(d, "libraries", "net", "neoforged")):
                loader = "neoforge"
            elif os.path.isdir(os.path.join(d, "libraries", "net", "minecraftforge")) or \
                    any(n.startswith("forge-") for n in names):
                loader = "forge"
            elif any(n.startswith("purpur") for n in names):
                loader = "purpur"
            elif any(n.startswith(("paper", "folia")) for n in names) or os.path.isdir(os.path.join(d, "plugins")):
                loader = "paper"
            elif any(n.startswith("spigot") for n in names):
                loader = "spigot"
        if not version:
            cands = names[:]
            vdir = os.path.join(d, "versions")
            if os.path.isdir(vdir):
                cands += os.listdir(vdir)
            mcdir = os.path.join(d, "libraries", "net", "minecraft", "server")
            if os.path.isdir(mcdir):
                cands += os.listdir(mcdir)
            for n in cands:
                m = re.search(r"(?<![\d.])(1\.\d{1,2}(?:\.\d{1,2})?)(?![\d.])", n)
                if m:
                    version = m.group(1)
                    break
        return loader, version

    def _folder(self, srv, loader):
        d = srv.get("directory") or ""
        name = "plugins" if loader in PLUGIN_LOADERS else "mods"
        return os.path.join(d, name)

    def _loaders(self, loader):
        if loader == "paper":
            return ["paper", "spigot", "bukkit"]
        if loader == "purpur":
            return ["purpur", "paper", "spigot", "bukkit"]
        if loader == "folia":
            return ["folia"]
        if loader == "spigot":
            return ["spigot", "bukkit"]
        if loader == "quilt":
            return ["quilt", "fabric"]
        return [loader]

    def _need(self, srv):
        loader, version = self.detect(srv)
        if not loader or not version:
            raise ModError("Tofu can't tell this server's %s. Set it in the options below the mod list."
                           % ("loader and Minecraft version" if not (loader or version) else
                              "loader" if not loader else "Minecraft version"))
        return loader, version

    @staticmethod
    def parse_ref(ref):
        s = str(ref or "").strip()
        m = re.search(r"modrinth\.com/(?:mod|plugin|datapack|project)/([\w.-]+)", s)
        if m:
            return m.group(1)
        if re.match(r"^[\w.-]+$", s):
            return s
        raise ModError("Enter a Modrinth project name (from its modrinth.com address) or paste the link.")

    def _versions(self, project, loader, version):
        q = urllib.parse.urlencode({"loaders": json.dumps(self._loaders(loader)),
                                    "game_versions": json.dumps([version])})
        try:
            return http_json("%s/project/%s/version?%s" % (self.API, urllib.parse.quote(project), q))
        except Exception as e:
            raise friendly_http(e, "Modrinth project '%s'" % project)

    def _hash_map(self, folder):
        files = glob.glob(os.path.join(folder, "*.jar")) + glob.glob(os.path.join(folder, "*.jar.disabled"))
        return {sha1_file(f): f for f in files}

    def install(self, srv, ref, _seen=None):
        loader, version = self._need(srv)
        project = self.parse_ref(ref)
        seen = _seen if _seen is not None else set()
        if project in seen:
            return {"installed": project}
        seen.add(project)
        vers = self._versions(project, loader, version)
        if not vers:
            raise ModError("'%s' has no release for %s %s." % (project, loader, version))
        v = vers[0]
        f = next((x for x in v.get("files") or [] if x.get("primary")), (v.get("files") or [None])[0])
        if not f:
            raise ModError("That release has no file to download.")
        folder = self._folder(srv, loader)
        os.makedirs(folder, exist_ok=True)
        # replace an older copy of the same project
        have = self.installed_versions(folder)
        for h, info in have.items():
            if info.get("project_id") == v.get("project_id"):
                move_to(info["_path"], self.mm.trash(srv["id"]))
        data = http_bytes(f["url"])
        dest = os.path.join(folder, os.path.basename(f["filename"]))
        with open(dest + ".partial", "wb") as out:
            out.write(data)
        os.replace(dest + ".partial", dest)
        deps = []
        have_projects = {i.get("project_id") for i in self.installed_versions(folder).values()}
        for dep in v.get("dependencies") or []:
            pid = dep.get("project_id")
            if dep.get("dependency_type") == "required" and pid and pid not in have_projects and pid not in seen:
                try:
                    self.install(srv, pid, seen)
                    deps.append(pid)
                except ModError as e:
                    deps.append("%s (failed: %s)" % (pid, e))
        self.mm._changed(srv["id"])
        r = {"installed": "%s %s" % (v.get("name") or project, v.get("version_number", ""))}
        if deps:
            r["note"] = "Also installed %d required mod(s)." % len(deps)
        return r

    def installed_versions(self, folder) -> Dict[str, dict]:
        hm = self._hash_map(folder)
        if not hm:
            return {}
        try:
            data = http_json(self.API + "/version_files", {"hashes": list(hm), "algorithm": "sha1"}, timeout=30)
        except Exception:
            data = {}
        out = {}
        for h, path in hm.items():
            info = dict(data.get(h) or {})
            info["_path"] = path
            out[h] = info
        return out

    def list(self, srv, check):
        loader, version = self.detect(srv)
        folder = self._folder(srv, loader or "fabric")
        inst = self.installed_versions(folder)
        titles = {}
        pids = sorted({i.get("project_id") for i in inst.values() if i.get("project_id")})
        if pids:
            try:
                for p in http_json(self.API + "/projects?ids=" + urllib.parse.quote(json.dumps(pids))):
                    titles[p["id"]] = (p.get("title"), p.get("slug"), p.get("project_type"))
            except Exception:
                pass
        latest = {}
        if check and inst and loader and version:
            try:
                latest = http_json(self.API + "/version_files/update",
                                   {"hashes": list(inst), "algorithm": "sha1", "loaders": self._loaders(loader),
                                    "game_versions": [version]}, timeout=30)
            except Exception:
                latest = {}
        mods = []
        for h, info in sorted(inst.items(), key=lambda kv: os.path.basename(kv[1]["_path"]).lower()):
            fn = os.path.basename(info["_path"])
            t = titles.get(info.get("project_id")) or (None, None, None)
            m = {"key": fn, "id": info.get("project_id") or fn, "name": t[0] or fn,
                 "version": info.get("version_number") or "", "enabled": not fn.endswith(".disabled"),
                 "size": os.path.getsize(info["_path"]), "file": fn,
                 "url": "https://modrinth.com/%s/%s" % (t[2] or "mod", t[1]) if t[1] else None}
            new = latest.get(h)
            if new:
                m["latest"] = new.get("version_number")
                m["update_available"] = new.get("id") != info.get("id")
                m["_new"] = new
            mods.append(m)
        extra = {"folder": folder, "loader": loader, "game_version": version}
        if not loader or not version:
            extra["hint"] = "Set the server's loader and Minecraft version in the options below."
        if loader is None and not os.path.isdir(folder):
            extra["hint"] = ("Vanilla servers can't load mods. Use a Fabric, Forge or NeoForge server for "
                             "mods, or Paper for plugins, then set the loader below.")
        return mods, extra

    def update(self, srv, keys=None):
        mods, _ = self.list(srv, True)
        todo = [m for m in mods if m.get("update_available") and (keys is None or m["key"] in keys)]
        done, failed = [], []
        loader, _v = self.detect(srv)
        folder = self._folder(srv, loader)
        for m in todo:
            try:
                new = m["_new"]
                f = next((x for x in new.get("files") or [] if x.get("primary")), (new.get("files") or [None])[0])
                data = http_bytes(f["url"])
                old = os.path.join(folder, m["file"])
                dest = os.path.join(folder, os.path.basename(f["filename"]) + ("" if m["enabled"] else ".disabled"))
                move_to(old, self.mm.trash(srv["id"]))
                with open(dest, "wb") as out:
                    out.write(data)
                done.append({"mod": m["name"], "from": m["version"], "to": m.get("latest")})
            except Exception as e:
                failed.append({"mod": m["name"], "error": str(e)[:200]})
        if done:
            self.mm._changed(srv["id"])
        return {"updated": done, "failed": failed, "message": "" if todo else "Everything is up to date."}

    def set_enabled(self, srv, key, on):
        loader, _v = self.detect(srv)
        folder = self._folder(srv, loader)
        base = os.path.basename(key)
        cur = os.path.join(folder, base)
        if not os.path.isfile(cur):
            raise ModError("Mod not found")
        if on and base.endswith(".disabled"):
            os.replace(cur, cur[:-len(".disabled")])
        elif not on and not base.endswith(".disabled"):
            os.replace(cur, cur + ".disabled")
        self.mm._changed(srv["id"])
        return {"changed": True}

    def remove(self, srv, key):
        loader, _v = self.detect(srv)
        p = os.path.join(self._folder(srv, loader), os.path.basename(key))
        if not os.path.isfile(p):
            raise ModError("Mod not found")
        kept = move_to(p, self.mm.trash(srv["id"]))
        self.mm._changed(srv["id"])
        return {"removed": key, "kept_in": kept}

    SORTS = {"popular": "downloads", "trending": "follows", "updated": "updated", "newest": "newest",
             "follows": "follows"}

    def details(self, srv, slug):
        from core.mod_browse import modrinth_details
        return modrinth_details(self, srv, slug)

    def search(self, srv, text, sort=None):
        loader, version = self._need(srv)
        ptype = "plugin" if loader in PLUGIN_LOADERS else "mod"
        facets = [["project_type:%s" % ptype], ["versions:%s" % version],
                  ["categories:%s" % l for l in self._loaders(loader)]]
        if ptype == "mod":
            facets.append(["server_side:required", "server_side:optional"])
        index = self.SORTS.get(sort or "") or ("relevance" if text else "downloads")
        q = urllib.parse.urlencode({"query": text or "", "limit": 40, "index": index, "facets": json.dumps(facets)})
        try:
            hits = http_json("%s/search?%s" % (self.API, q)).get("hits") or []
        except Exception as e:
            raise friendly_http(e, "Modrinth search")
        from core.mod_browse import safe_img, _ids
        return [{"id": h.get("slug") or h.get("project_id"), "name": h.get("title"),
                 "summary": (h.get("description") or "")[:200], "author": h.get("author"),
                 "downloads": h.get("downloads"), "follows": h.get("follows"), "icon": safe_img(h.get("icon_url")),
                 "tags": [c for c in h.get("display_categories") or h.get("categories") or []
                          if c not in ("fabric", "forge", "neoforge", "quilt", "paper", "bukkit", "spigot")][:5],
                 "updated": h.get("date_modified"), "match_ids": _ids(h.get("slug"), h.get("project_id")),
                 "url": "https://modrinth.com/%s/%s" % (ptype, h.get("slug"))}
                for h in hits]


# --------------------------------------------------------------------------- #
# uMod (Rust / Hurtworld Oxide plugins)
# --------------------------------------------------------------------------- #
INFO_RX = re.compile(r'\[Info\(\s*"([^"]+)"\s*,\s*"([^"]*)"\s*,\s*"?([\d.]+)"?')


class UModBackend:
    SEARCH = "https://umod.org/plugins/search.json?query=%s&page=1&sort=title&sortdir=asc"

    def __init__(self, mm, profile):
        self.mm, self.mgr, self.p = mm, mm.mgr, profile

    def caps(self):
        return {"toggle": True, "reorder": False, "update": True, "search": True, "add": True, "fields": [],
                "add_hint": "uMod plugin name (e.g. Kits) or its umod.org link", "no_restart": True}

    def _base(self, srv):
        d = srv.get("directory") or ""
        for fw in ("carbon", "oxide"):
            if os.path.isdir(os.path.join(d, fw)):
                return os.path.join(d, fw)
        return os.path.join(d, "oxide")

    def _dirs(self, srv):
        b = self._base(srv)
        return os.path.join(b, "plugins"), os.path.join(b, "plugins_disabled")

    def framework_installed(self, srv):
        d = srv.get("directory") or ""
        return (os.path.isdir(os.path.join(d, "carbon")) or
                bool(glob.glob(os.path.join(d, "*_Data", "Managed", "Oxide.*.dll"))))

    def _game(self):
        return "hurtworld" if self.p.get("name") == "Hurtworld" else "rust"

    def _find(self, name) -> dict:
        try:
            data = http_json(self.SEARCH % urllib.parse.quote(name))
        except Exception as e:
            raise friendly_http(e, "uMod plugin '%s'" % name)
        game = self._game()
        hits = [h for h in data.get("data") or []
                if game in (h.get("tags_all") or "") or any(g.get("slug") == game for g in h.get("games_detail") or [])]
        low = name.lower()
        exact = [h for h in hits if (h.get("name") or "").lower() == low or (h.get("slug") or "").lower() == low]
        return (exact or hits or [None])[0] or {}

    def search(self, srv, text, sort=None):
        try:
            data = http_json(self.SEARCH % urllib.parse.quote(text or ""))
        except Exception as e:
            raise friendly_http(e, "uMod search")
        game = self._game()
        out = []
        for h in data.get("data") or []:
            if game not in (h.get("tags_all") or "") and not any(g.get("slug") == game for g in h.get("games_detail") or []):
                continue
            from core.mod_browse import safe_img, _ids
            out.append({"id": h.get("name"), "name": h.get("title"), "summary": (h.get("description") or "")[:200],
                        "author": h.get("author"), "downloads": h.get("downloads"), "url": h.get("url"),
                        "icon": safe_img(h.get("icon_url")), "updated": h.get("updated_at"),
                        "match_ids": _ids(h.get("name"), h.get("slug"))})
        return out

    def install(self, srv, ref):
        s = str(ref or "").strip()
        m = re.search(r"umod\.org/plugins/([\w-]+)", s)
        name = m.group(1) if m else s
        if not re.match(r"^[\w-]+$", name or ""):
            raise ModError("Enter a uMod plugin name (e.g. Kits) or its umod.org link.")
        hit = self._find(name)
        if not hit.get("download_url"):
            raise ModError("Couldn't find the uMod plugin '%s' for %s." % (name, self._game().title()))
        data = http_bytes(hit["download_url"])
        on_dir, off_dir = self._dirs(srv)
        os.makedirs(on_dir, exist_ok=True)
        fn = (hit.get("name") or name) + ".cs"
        for d in (on_dir, off_dir):
            if os.path.isfile(os.path.join(d, fn)):
                move_to(os.path.join(d, fn), self.mm.trash(srv["id"]))
        with open(os.path.join(on_dir, fn), "wb") as f:
            f.write(data)
        r = {"installed": "%s %s" % (hit.get("title") or name, hit.get("latest_release_version") or "")}
        if not self.framework_installed(srv):
            r["note"] = "Oxide (or Carbon) isn't installed on this server yet, so the plugin won't load until it is."
        return r

    def list(self, srv, check):
        mods = []
        for base, on in zip(self._dirs(srv), (True, False)):
            for p in sorted(glob.glob(os.path.join(base, "*.cs")), key=str.lower):
                with open(p, encoding="utf-8", errors="replace") as fh:
                    head = fh.read(20000)
                m = INFO_RX.search(head)
                fn = os.path.basename(p)
                mods.append({"key": fn, "id": fn[:-3], "name": m.group(1) if m else fn[:-3],
                             "version": m.group(3) if m else "", "author": m.group(2) if m else "",
                             "enabled": on, "size": os.path.getsize(p),
                             "url": "https://umod.org/plugins/" + fn[:-3].lower()})
        if check:
            for m in mods:
                try:
                    hit = self._find(m["id"])
                    m["latest"] = hit.get("latest_release_version")
                    m["update_available"] = bool(m["latest"] and m["version"] and m["latest"] != m["version"])
                except Exception as e:
                    m["check_error"] = str(e)[:150]
        extra = {"folder": self._dirs(srv)[0]}
        if not self.framework_installed(srv):
            extra["hint"] = ("Oxide isn't installed on this server. Download Oxide for %s from umod.org and "
                             "extract it into the server folder (or install Carbon), then restart."
                             % self._game().title())
        return mods, extra

    def update(self, srv, keys=None):
        mods, _ = self.list(srv, True)
        todo = [m for m in mods if m.get("update_available") and (keys is None or m["key"] in keys)]
        done, failed = [], []
        for m in todo:
            try:
                self.install(srv, m["id"])
                if not m["enabled"]:
                    self.set_enabled(srv, m["key"], False)
                done.append({"mod": m["name"], "from": m["version"], "to": m.get("latest")})
            except Exception as e:
                failed.append({"mod": m["name"], "error": str(e)[:200]})
        return {"updated": done, "failed": failed, "message": "" if todo else "Everything is up to date."}

    def set_enabled(self, srv, key, on):
        on_dir, off_dir = self._dirs(srv)
        src = os.path.join(off_dir if on else on_dir, os.path.basename(key))
        if not os.path.isfile(src):
            return {"changed": False}
        move_to(src, on_dir if on else off_dir)
        return {"changed": True}

    def remove(self, srv, key):
        for d in self._dirs(srv):
            p = os.path.join(d, os.path.basename(key))
            if os.path.isfile(p):
                return {"removed": key, "kept_in": move_to(p, self.mm.trash(srv["id"]))}
        raise ModError("Plugin not found")


# --------------------------------------------------------------------------- #
# a plain mods folder
# --------------------------------------------------------------------------- #
class FolderBackend:
    def __init__(self, mm, profile):
        self.mm, self.mgr, self.p = mm, mm.mgr, profile

    def caps(self):
        return {"toggle": True, "reorder": False, "update": False, "search": False, "add": True, "fields": [],
                "add_file": True, "add_hint": "Download link to a mod (.zip or a single file)"}

    def _dirs(self, srv):
        d = srv.get("directory") or ""
        rel = (self.p.get("folder") or "mods").replace("\\", "/").strip("/")
        on = os.path.realpath(os.path.join(d, rel))
        if os.path.commonpath([on, os.path.realpath(d)]) != os.path.realpath(d):
            raise ModError("The mods folder must be inside the server folder.")
        off = os.path.join(d, "TofuSM-disabled-mods", safe_name(rel, "mods"))
        return on, off

    def list(self, srv, check):
        on, off = self._dirs(srv)
        mods = _folder_list(on, True, skip={"modlist.txt"}) + _folder_list(off, False)
        return mods, {"folder": on}

    def _place(self, srv, name, data):
        on, _off = self._dirs(srv)
        os.makedirs(on, exist_ok=True)
        name = os.path.basename(name) or "mod"
        if name.lower().endswith(".zip"):
            import io
            import zipfile
            roots = {n.split("/", 1)[0] for n in zipfile.ZipFile(io.BytesIO(data)).namelist() if n}
            if len(roots) == 1 and not list(roots)[0].lower().endswith((".pak", ".dll", ".jar")):
                tops = extract_zip(data, on)          # zip holds one mod folder
            else:
                extract_zip(data, os.path.join(on, name[:-4]))
                tops = [name[:-4]]
        else:
            with open(os.path.join(on, name), "wb") as f:
                f.write(data)
            tops = [name]
        self.mm._changed(srv["id"])
        return {"installed": ", ".join(tops)}

    def install(self, srv, ref):
        url = str(ref or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ModError("Paste a download link (http/https) to the mod's file.")
        try:
            data = http_bytes(url)
        except Exception as e:
            raise friendly_http(e, "That download")
        name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(url).path)) or "mod.zip"
        return self._place(srv, name, data)

    def install_file(self, srv, path):
        with open(path, "rb") as f:
            return self._place(srv, os.path.basename(path), f.read())

    def set_enabled(self, srv, key, on):
        on_dir, off_dir = self._dirs(srv)
        src = os.path.join(off_dir if on else on_dir, os.path.basename(key))
        if not os.path.exists(src):
            return {"changed": False}
        move_to(src, on_dir if on else off_dir)
        self.mm._changed(srv["id"])
        return {"changed": True}

    def remove(self, srv, key):
        for d in self._dirs(srv):
            p = os.path.join(d, os.path.basename(key))
            if os.path.exists(p):
                kept = move_to(p, self.mm.trash(srv["id"]))
                self.mm._changed(srv["id"])
                return {"removed": key, "kept_in": kept}
        raise ModError("Mod not found")
