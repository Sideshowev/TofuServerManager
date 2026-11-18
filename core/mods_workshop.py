"""Steam Workshop mods that Tofu downloads with SteamCMD and installs itself.

Installers:
  bohemia  Arma 3 / DayZ - link the item into the server as @Name, copy its .bikey
           keys into keys/, write -mod= and -serverMod= (in list order)
  conan    Conan Exiles - copy the .pak into ConanSandbox/Mods, write modlist.txt
           (load order) and ServerModList in ServerSettings.ini
  squad    Squad - link the item into SquadGame/Plugins/Mods/<id>
  plain    just download (activate it in the game yourself)
"""

from __future__ import annotations

import glob
import os
import shutil
from typing import List, Optional

from core.mods_common import (ModError, ini_set, is_link, link_dir, move_to, parse_vdf, safe_name, set_arg,
                              size_of, unlink_dir, workshop_id, _write_lines)


class WorkshopBackend:
    def __init__(self, mm, profile):
        self.mm = mm
        self.mgr = mm.mgr
        self.p = profile
        self.kind = profile.get("installer", "plain")

    def caps(self):
        return {"toggle": True, "reorder": self.kind in ("bohemia", "conan"), "update": True, "search": False,
                "add": True, "add_file": True,
                "add_hint": "Steam Workshop IDs or links (several at once is fine), or Add file... for an Arma preset",
                "fields": ["server_only"] if self.kind == "bohemia" else []}

    # -- paths ----------------------------------------------------------------------
    def appid(self, srv):
        return str(self.p.get("workshop_appid") or srv.get("workshopAppId") or srv.get("appId") or "")

    def content(self, srv, item_id=None):
        base = os.path.join(srv.get("directory") or "", "steamapps", "workshop", "content", self.appid(srv))
        return os.path.join(base, item_id) if item_id else base

    def _acf(self, srv) -> dict:
        path = os.path.join(srv.get("directory") or "", "steamapps", "workshop", "appworkshop_%s.acf" % self.appid(srv))
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                data = parse_vdf(f.read())
            return (data.get("AppWorkshop") or {}).get("WorkshopItemsInstalled") or {}
        except Exception:
            return {}

    # -- the stored list (modList on the server entry) --------------------------------------
    def items(self, srv) -> List[dict]:
        if isinstance(srv.get("modList"), list):
            return [dict(i) for i in srv["modList"] if isinstance(i, dict) and i.get("id")]
        # first time: adopt what's already downloaded
        base = self.content(srv)
        out = []
        if os.path.isdir(base):
            for item in sorted(os.listdir(base)):
                if item.isdigit() and os.path.isdir(os.path.join(base, item)):
                    out.append({"id": item, "enabled": self.kind == "plain"})
        return out

    def _store(self, srv, items):
        servers = self.mgr.load_servers()
        for s in servers:
            if s.get("id") == srv["id"]:
                s["modList"] = items
                srv.update(s)
        self.mgr.save_servers(servers)

    def save(self, srv, items) -> Optional[str]:
        self._store(srv, items)
        note = self.activate(srv, items)
        self.mm._changed(srv["id"])
        return note

    # -- download ---------------------------------------------------------------------
    def download(self, srv, item_id):
        if self.p.get("needs_account") and not (self.mgr.get_setting("steam_username", "") or ""):
            raise ModError("Downloading %s Workshop mods needs a Steam login. Set one in Settings > Steam "
                           "login (an account that owns the game), then try again." % (self.p.get("name") or "these"))
        servers = self.mgr.load_servers()
        if self.p.get("needs_account"):
            for s in servers:   # SteamCMD uses the account login for this server's downloads
                if s.get("id") == srv["id"] and not s.get("requiresAccount"):
                    s["requiresAccount"] = True
                    self.mgr.save_servers(servers)
                    break
        r = self.mgr.download_workshop_mods(srv["id"], [item_id], self.appid(srv))
        if not r["downloaded"] or not os.path.isdir(self.content(srv, item_id)):
            raise ModError("SteamCMD couldn't download Workshop item %s (see workshop_%s.log in the server "
                           "folder). Check the ID%s." % (item_id, item_id,
                                                         " and the Steam login" if self.p.get("needs_account") else ""))

    # -- turning mods on in the game ---------------------------------------------------------
    def activate(self, srv, items) -> Optional[str]:
        d = srv.get("directory") or ""
        if self.kind == "bohemia":
            mods, server_mods = [], []
            keys_dir = os.path.join(d, "keys")
            for i in items:
                folder = i.get("folder") or ("@" + safe_name(i.get("name") or i["id"], i["id"]))
                i["folder"] = folder
                link = os.path.join(d, folder)
                src = self.content(srv, i["id"])
                if i.get("enabled", True) and os.path.isdir(src):
                    if not os.path.lexists(link):
                        link_dir(src, link)
                    i["keys"] = self._copy_keys(src, keys_dir)
                    (server_mods if i.get("server_only") else mods).append(folder)
            self._store(srv, items)
            args = srv.get("launchArgs") or ""
            args = set_arg(args, "mod", ";".join(mods) or None)
            args = set_arg(args, "serverMod", ";".join(server_mods) or None)
            self._save_args(srv, args)
            return None
        if self.kind == "conan":
            mods_dir = os.path.join(d, "ConanSandbox", "Mods")
            os.makedirs(mods_dir, exist_ok=True)
            lines, ids = [], []
            for i in items:
                src = self.content(srv, i["id"])
                paks = sorted(glob.glob(os.path.join(src, "*.pak")))
                names = []
                for pak in paks:
                    dest = os.path.join(mods_dir, os.path.basename(pak))
                    if (not os.path.isfile(dest) or os.path.getmtime(dest) < os.path.getmtime(pak)
                            or os.path.getsize(dest) != os.path.getsize(pak)):
                        shutil.copy2(pak, dest)
                    names.append(os.path.basename(pak))
                i["files"] = names
                if i.get("enabled", True) and names:
                    lines += ["*" + n for n in names]
                    ids.append(i["id"])
            self._store(srv, items)
            _write_lines(os.path.join(mods_dir, "modlist.txt"), lines)
            ini_set(os.path.join(d, "ConanSandbox", "Saved", "Config", "WindowsServer", "ServerSettings.ini"),
                    "ServerSettings", "ServerModList", ",".join(ids) or None)
            return None
        if self.kind == "squad":
            mods_dir = os.path.join(d, "SquadGame", "Plugins", "Mods")
            for i in items:
                link = os.path.join(mods_dir, i["id"])
                src = self.content(srv, i["id"])
                if i.get("enabled", True) and os.path.isdir(src):
                    if not os.path.lexists(link):
                        link_dir(src, link)
                elif os.path.lexists(link) and is_link(link):
                    unlink_dir(link)
            return None
        return None

    def _copy_keys(self, src, keys_dir) -> List[str]:
        out = []
        for k in glob.glob(os.path.join(src, "*", "*.bikey")) + glob.glob(os.path.join(src, "*.bikey")):
            os.makedirs(keys_dir, exist_ok=True)
            dest = os.path.join(keys_dir, os.path.basename(k))
            if not os.path.isfile(dest):
                shutil.copy2(k, dest)
            out.append(os.path.basename(k))
        return sorted(set(out))

    def _save_args(self, srv, args):
        servers = self.mgr.load_servers()
        for s in servers:
            if s.get("id") == srv["id"]:
                s["launchArgs"] = args
        self.mgr.save_servers(servers)
        srv["launchArgs"] = args

    def _deactivate(self, srv, item):
        d = srv.get("directory") or ""
        if self.kind == "bohemia" and item.get("folder"):
            link = os.path.join(d, item["folder"])
            if os.path.lexists(link):
                unlink_dir(link) if is_link(link) else move_to(link, self.mm.trash(srv["id"]))
            used = {k for i in self.items(srv) if i["id"] != item["id"] for k in i.get("keys") or []}
            for k in item.get("keys") or []:
                kp = os.path.join(d, "keys", k)
                if k not in used and os.path.isfile(kp):
                    move_to(kp, self.mm.trash(srv["id"]))
        if self.kind == "conan":
            for n in item.get("files") or []:
                fp = os.path.join(d, "ConanSandbox", "Mods", n)
                if os.path.isfile(fp):
                    move_to(fp, self.mm.trash(srv["id"]))
        if self.kind == "squad":
            link = os.path.join(d, "SquadGame", "Plugins", "Mods", item["id"])
            if os.path.lexists(link):
                unlink_dir(link) if is_link(link) else move_to(link, self.mm.trash(srv["id"]))

    # -- operations -------------------------------------------------------------------
    def install(self, srv, ref):
        item_id = workshop_id(ref)
        if not item_id:
            raise ModError("Enter a Steam Workshop ID or link")
        items = self.items(srv)
        if any(i["id"] == item_id for i in items):
            raise ModError("That mod is already installed - use Update to refresh it.")
        self.download(srv, item_id)
        name = (self.mm.steam_details([item_id]).get(item_id) or {}).get("title") or item_id
        items.append({"id": item_id, "name": name, "enabled": True})
        note = self.save(srv, items)
        return {"installed": name, "note": note}

    def remove(self, srv, key):
        items = self.items(srv)
        item = next((i for i in items if i["id"] == key), None)
        if item is None:
            raise ModError("Mod not found")
        self._deactivate(srv, item)
        src = self.content(srv, key)
        kept = move_to(src, self.mm.trash(srv["id"])) if os.path.isdir(src) else None
        note = self.save(srv, [i for i in items if i["id"] != key])
        return {"removed": item.get("name") or key, "kept_in": kept, "note": note}

    def set_enabled(self, srv, key, on):
        items = self.items(srv)
        for i in items:
            if i["id"] == key:
                if not on:
                    self._deactivate(srv, i)
                i["enabled"] = bool(on)
        return {"changed": True, "note": self.save(srv, items)}

    def move(self, srv, key, delta):
        items = self.items(srv)
        idx = next((n for n, i in enumerate(items) if i["id"] == key), None)
        if idx is None:
            raise ModError("Mod not found")
        items.insert(max(0, min(len(items) - 1, idx + delta)), items.pop(idx))
        return {"changed": True, "note": self.save(srv, items)}

    def set_field(self, srv, key, field, value):
        if field != "server_only" or self.kind != "bohemia":
            raise ModError("That can't be changed for this game")
        items = self.items(srv)
        for i in items:
            if i["id"] == key:
                i["server_only"] = bool(value)
        return {"changed": True, "note": self.save(srv, items)}

    def update(self, srv, keys=None):
        mods, _ = self.list(srv, True)
        todo = [m for m in mods if m.get("update_available") and (keys is None or m["key"] in keys)]
        done, failed = [], []
        for m in todo:
            try:
                self.download(srv, m["id"])
                done.append({"mod": m["name"]})
            except Exception as e:
                failed.append({"mod": m["name"], "error": str(e)[:200]})
        if done:
            self.save(srv, self.items(srv))   # recopy paks / keys
        return {"updated": done, "failed": failed,
                "message": "" if todo else "Everything is up to date."}

    def list(self, srv, check):
        items = self.items(srv)
        acf = self._acf(srv)
        details = self.mm.steam_details([i["id"] for i in items]) if items else {}
        mods = []
        for n, i in enumerate(items):
            d = details.get(i["id"]) or {}
            meta = acf.get(i["id"]) or {}
            path = self.content(srv, i["id"])
            downloaded = os.path.isdir(path)
            installed_at = int(meta.get("timeupdated") or (os.path.getmtime(path) if downloaded else 0))
            latest = int(d.get("time_updated") or 0)
            mods.append({"key": i["id"], "id": i["id"], "name": d.get("title") or i.get("name") or i["id"],
                         "enabled": i.get("enabled", True), "order": n + 1, "version": "",
                         "size": int(meta.get("size") or 0) or (size_of(path) if downloaded else 0),
                         "url": "https://steamcommunity.com/sharedfiles/filedetails/?id=" + i["id"],
                         "update_available": bool(latest and installed_at and latest > installed_at + 60),
                         "missing": not downloaded, "server_only": bool(i.get("server_only")),
                         "folder": i.get("folder")})
        extra = {"folder": self.content(srv)}
        if self.p.get("needs_account") and not (self.mgr.get_setting("steam_username", "") or ""):
            extra["hint"] = "Set a Steam login in Settings first - Workshop downloads for this game need one."
        return mods, extra
