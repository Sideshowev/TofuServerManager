"""Mods the game server downloads itself: BaoBun keeps an ordered list of IDs per
server (``modList`` in servers.json) and writes it where each game reads it."""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

from core.mods_common import ModError, add_flag, appdata, dedupe, documents_dir, first_existing, get_arg, http_json, ini_get, ini_set, lenient_json, rel_or_abs, set_arg, steam_details, user_home, workshop_id, _read_lines, _write_lines

MARK = "-- managed by BaoBun (edit mods in BaoBun's Mods tab)"


# --------------------------------------------------------------------------- #
# per-game writers: read(srv) -> [ids]   write(srv, items) -> note or None
# --------------------------------------------------------------------------- #
class Writer:
    def __init__(self, mgr):
        self.mgr = mgr

    def read(self, srv) -> List[dict]:
        return []

    def write(self, srv, items: List[dict]) -> Optional[str]:
        return None

    def where(self, srv) -> str:
        return ""

    def _save_args(self, srv, args):
        servers = self.mgr.load_servers()
        for s in servers:
            if s.get("id") == srv["id"]:
                s["launchArgs"] = args
        self.mgr.save_servers(servers)
        srv["launchArgs"] = args


class AsaLaunch(Writer):
    """ARK: SA - -mods=1,2,3 (one cluster-wide list for a cluster)."""

    def read(self, srv):
        if srv.get("clusterId"):
            raw = self.mgr.get_cluster_settings(srv["clusterId"]).get("mods", "")
        else:
            raw = get_arg(srv.get("launchArgs") or "", "mods") or ""
            auto = str(srv.get("arkMapMod") or "")
            raw = ",".join(x for x in raw.split(",") if x and x != auto)
        return [{"id": x.strip()} for x in raw.split(",") if x.strip().isdigit()]

    def write(self, srv, items):
        ids = ",".join(i["id"] for i in items if i.get("enabled", True))
        if srv.get("clusterId"):
            st = dict(self.mgr.get_cluster_settings(srv["clusterId"]))
            st["mods"] = ids
            n = self.mgr.apply_cluster_settings(srv["clusterId"], st)
            return "Updated all %d maps in cluster %s." % (n, srv["clusterId"])
        self._save_args(srv, set_arg(srv.get("launchArgs") or "", "mods", ids))
        return None

    def where(self, srv):
        return "the cluster's mod list (-mods=)" if srv.get("clusterId") else "launch options (-mods=)"


class ArkSeIni(Writer):
    def _path(self, srv):
        return os.path.join(srv.get("directory") or "", "ShooterGame", "Saved", "Config", "WindowsServer",
                            "GameUserSettings.ini")

    def read(self, srv):
        v = (ini_get(self._path(srv), "ServerSettings", "ActiveMods") or [""])[-1]
        return [{"id": x.strip()} for x in v.split(",") if x.strip().isdigit()]

    def write(self, srv, items):
        ids = ",".join(i["id"] for i in items if i.get("enabled", True))
        ini_set(self._path(srv), "ServerSettings", "ActiveMods", ids or None)
        args = srv.get("launchArgs") or ""
        if ids and "-automanagedmods" not in args.lower():
            self._save_args(srv, add_flag(args, "-automanagedmods"))
        return None

    def where(self, srv):
        return "GameUserSettings.ini (ActiveMods)"


class ReforgerJson(Writer):
    def _path(self, srv):
        d = srv.get("directory") or ""
        cfg = get_arg(srv.get("launchArgs") or "", "config")
        if cfg:
            return rel_or_abs(d, cfg)
        return first_existing(os.path.join(d, "config.json"), os.path.join(d, "Configs", "config.json")) \
            or os.path.join(d, "config.json")

    def _load(self, srv):
        p = self._path(srv)
        if not os.path.isfile(p):
            return p, None
        with open(p, encoding="utf-8-sig") as f:
            return p, lenient_json(f.read())

    def read(self, srv):
        _p, cfg = self._load(srv)
        mods = ((cfg or {}).get("game") or {}).get("mods") or []
        return [{"id": str(m.get("modId")), "name": m.get("name") or "", "version": m.get("version") or ""}
                for m in mods if isinstance(m, dict) and m.get("modId")]

    def write(self, srv, items):
        p, cfg = self._load(srv)
        if cfg is None:
            raise ModError("The server's config.json wasn't found (%s). Create it first (Config files "
                           "tab), or point -config in the launch options at it." % p)
        old = {m.get("modId"): m for m in ((cfg.get("game") or {}).get("mods") or []) if isinstance(m, dict)}
        mods = []
        for i in items:
            if not i.get("enabled", True):
                continue
            m = dict(old.get(i["id"]) or {})
            m["modId"] = i["id"]
            m["name"] = i.get("name") or m.get("name") or i["id"]
            m.setdefault("version", "")
            mods.append(m)
        cfg.setdefault("game", {})["mods"] = mods
        _backup_write_json(p, cfg)
        return None

    def where(self, srv):
        return os.path.basename(self._path(srv)) + " (game.mods)"


class PzIni(Writer):
    def _path(self, srv):
        args = srv.get("launchArgs") or ""
        cache = get_arg(args, "cachedir") or os.path.join(user_home(), "Zomboid")
        name = get_arg(args, "servername") or "servertest"
        return os.path.join(rel_or_abs(srv.get("directory") or "", cache), "Server", name + ".ini")

    def read(self, srv):
        p = self._path(srv)
        ws = [x for x in (ini_get(p, None, "WorkshopItems") or [""])[-1].split(";") if x.strip()]
        mods = [x for x in (ini_get(p, None, "Mods") or [""])[-1].split(";") if x.strip()]
        items = [{"id": w.strip(), "mod_ids": []} for w in ws if w.strip().isdigit()]
        if items and mods:
            items[0]["mod_ids_unassigned"] = mods   # can't tell which item they belong to
        return items

    def found_mod_ids(self, srv, item_id) -> List[str]:
        base = os.path.join(srv.get("directory") or "", "steamapps", "workshop", "content", "108600", item_id, "mods")
        out = []
        if os.path.isdir(base):
            for d in sorted(os.listdir(base)):
                mi = os.path.join(base, d, "mod.info")
                for line in _read_lines(mi):
                    if line.lower().startswith("id="):
                        out.append(line.split("=", 1)[1].strip())
                        break
        return out

    def write(self, srv, items):
        p = self._path(srv)
        if not os.path.isfile(p):
            return ("The server's settings file (%s) doesn't exist yet. Start the server once so it "
                    "creates it, then save the mod list again." % p)
        on = [i for i in items if i.get("enabled", True)]
        mods = dedupe(m for i in on for m in (i.get("mod_ids") or []))
        ini_set(p, None, "WorkshopItems", ";".join(i["id"] for i in on))
        ini_set(p, None, "Mods", ";".join(mods))
        missing = [i["id"] for i in on if not i.get("mod_ids")]
        if missing:
            return ("No Mod ID yet for %s - the server downloads them on its next start, then BaoBun "
                    "fills them in (or type them in)." % ", ".join(missing))
        return None

    def where(self, srv):
        return os.path.basename(self._path(srv)) + " (WorkshopItems / Mods)"


class SeCfg(Writer):
    def _path(self, srv):
        args = srv.get("launchArgs") or ""
        inst = get_arg(args, "path")
        d = srv.get("directory") or ""
        if inst:
            return os.path.join(rel_or_abs(d, inst), "SpaceEngineers-Dedicated.cfg")
        return first_existing(os.path.join(appdata(), "SpaceEngineersDedicated", "SpaceEngineers-Dedicated.cfg"),
                              os.path.join(d, "SpaceEngineers-Dedicated.cfg")) \
            or os.path.join(appdata(), "SpaceEngineersDedicated", "SpaceEngineers-Dedicated.cfg")

    def read(self, srv):
        p = self._path(srv)
        if not os.path.isfile(p):
            return []
        with open(p, encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
        out = []
        for m in re.finditer(r"<ModItem([^>]*)>(.*?)</ModItem>", text, re.S):
            pid = re.search(r"<PublishedFileId>(\d+)</PublishedFileId>", m.group(2))
            fn = re.search(r'FriendlyName="([^"]*)"', m.group(1))
            if pid:
                out.append({"id": pid.group(1), "name": fn.group(1) if fn else ""})
        return out

    def write(self, srv, items):
        p = self._path(srv)
        if not os.path.isfile(p):
            return ("SpaceEngineers-Dedicated.cfg wasn't found (%s). Run the server once (or set -path) "
                    "so it creates it, then save the list again." % p)
        with open(p, encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
        from xml.sax.saxutils import quoteattr
        rows = "".join(
            "\n    <ModItem FriendlyName=%s>\n      <Name>%s.sbm</Name>\n      <PublishedFileId>%s</PublishedFileId>"
            "\n      <PublishedServiceName>Steam</PublishedServiceName>\n    </ModItem>"
            % (quoteattr(i.get("name") or i["id"]), i["id"], i["id"]) for i in items if i.get("enabled", True))
        block = "<Mods>%s\n  </Mods>" % rows if rows else "<Mods />"
        if re.search(r"<Mods\s*/>|<Mods>.*?</Mods>", text, re.S):
            text = re.sub(r"<Mods\s*/>|<Mods>.*?</Mods>", lambda _m: block, text, count=1, flags=re.S)
        else:
            text = re.sub(r"(</MyConfigDedicated[^>]*>)", "  " + block + "\n\\1", text, count=1)
        _backup_write_text(p, text)
        return ("Existing worlds keep their own mod list in Sandbox_config.sbc - BaoBun updated the "
                "server config; check the world's list too.")

    def where(self, srv):
        return "SpaceEngineers-Dedicated.cfg (<Mods>)"


class UnturnedJson(Writer):
    def _path(self, srv):
        d = srv.get("directory") or ""
        m = re.search(r"(?i)\+\w*server/(\S+)", srv.get("launchArgs") or "")
        name = m.group(1) if m else None
        if not name:
            sd = os.path.join(d, "Servers")
            dirs = [x for x in os.listdir(sd)] if os.path.isdir(sd) else []
            name = dirs[0] if len(dirs) == 1 else "BaoBun"
        return os.path.join(d, "Servers", name, "WorkshopDownloadConfig.json")

    def read(self, srv):
        p = self._path(srv)
        if not os.path.isfile(p):
            return []
        with open(p, encoding="utf-8-sig") as f:
            cfg = lenient_json(f.read())
        return [{"id": str(x)} for x in (cfg.get("File_IDs") or [])]

    def write(self, srv, items):
        p = self._path(srv)
        cfg = {}
        if os.path.isfile(p):
            with open(p, encoding="utf-8-sig") as f:
                cfg = lenient_json(f.read())
        cfg["File_IDs"] = [int(i["id"]) for i in items if i.get("enabled", True)]
        _backup_write_json(p, cfg)
        return None

    def where(self, srv):
        return "Servers/%s/WorkshopDownloadConfig.json" % os.path.basename(os.path.dirname(self._path(srv)))


class DstLua(Writer):
    def _setup(self, srv):
        return os.path.join(srv.get("directory") or "", "mods", "dedicated_server_mods_setup.lua")

    def _cluster(self, srv):
        args = srv.get("launchArgs") or ""
        root = get_arg(args, "persistent_storage_root") or os.path.join(documents_dir(), "Klei")
        conf = get_arg(args, "conf_dir") or "DoNotStarveTogether"
        cluster = get_arg(args, "cluster") or "Cluster_1"
        return os.path.join(root, conf, cluster)

    def read(self, srv):
        ids = re.findall(r'ServerModSetup\(\s*"(?:workshop-)?(\d+)"\s*\)', "\n".join(_read_lines(self._setup(srv))))
        return [{"id": x} for x in dedupe(ids)]

    def write(self, srv, items):
        on = [i["id"] for i in items if i.get("enabled", True)]
        p = self._setup(srv)
        keep = [l for l in _read_lines(p) if "ServerModSetup(" not in l and MARK not in l]
        while keep and not keep[-1].strip():
            keep.pop()
        lines = keep + ([""] if keep else []) + [MARK] + ['ServerModSetup("%s")' % x for x in on]
        _write_lines(p, lines)
        cluster = self._cluster(srv)
        shards = [s for s in ("Master", "Caves") if os.path.isdir(os.path.join(cluster, s))] or ["Master"]
        skipped = []
        for shard in shards:
            mo = os.path.join(cluster, shard, "modoverrides.lua")
            existing = "\n".join(_read_lines(mo))
            if existing.strip() and MARK not in existing:
                skipped.append(shard)
                continue
            body = [MARK, "return {"] + ['  ["workshop-%s"] = { enabled = true },' % x for x in on] + ["}"]
            _write_lines(mo, body)
        if skipped:
            return ("modoverrides.lua in %s was written by hand, so BaoBun left it alone - enable the "
                    "mods there (\"workshop-<id>\" = { enabled = true })." % " and ".join(skipped))
        return None

    def where(self, srv):
        return "dedicated_server_mods_setup.lua + modoverrides.lua"


class Kf2Ini(Writer):
    DM = "OnlineSubsystemSteamworks.SteamWorkshopDownload"

    def _path(self, srv):
        return os.path.join(srv.get("directory") or "", "KFGame", "Config", "PCServer-KFEngine.ini")

    def read(self, srv):
        return [{"id": x} for x in ini_get(self._path(srv), "OnlineSubsystemSteamworks.KFWorkshopSteamworks",
                                           "ServerSubscribedWorkshopItems") if x.isdigit()]

    def write(self, srv, items):
        p = self._path(srv)
        if not os.path.isfile(p):
            return ("PCServer-KFEngine.ini doesn't exist yet - start the server once so it creates its "
                    "config, then save the mod list again.")
        ini_set(p, "OnlineSubsystemSteamworks.KFWorkshopSteamworks", "ServerSubscribedWorkshopItems",
                [i["id"] for i in items if i.get("enabled", True)])
        dms = ini_get(p, "IpDrv.TcpNetDriver", "DownloadManagers")
        if not dms or dms[0] != self.DM:
            ini_set(p, "IpDrv.TcpNetDriver", "DownloadManagers",
                    [self.DM] + [d for d in dms if d != self.DM], first_in_section=True)
        return None

    def where(self, srv):
        return "PCServer-KFEngine.ini"


class GmodCollection(Writer):
    def read(self, srv):
        v = get_arg(srv.get("launchArgs") or "", "host_workshop_collection")
        return [{"id": v}] if v and v.isdigit() else []

    def write(self, srv, items):
        on = [i for i in items if i.get("enabled", True)]
        args = set_arg(srv.get("launchArgs") or "", "host_workshop_collection", on[0]["id"] if on else None,
                       sep=" ", prefix="+")
        self._save_args(srv, args)
        return None

    def where(self, srv):
        return "launch options (+host_workshop_collection)"


WRITERS = {"asa_launch": AsaLaunch, "arkse_ini": ArkSeIni, "reforger_json": ReforgerJson, "pz_ini": PzIni,
           "se_cfg": SeCfg, "unturned_json": UnturnedJson, "dst_lua": DstLua, "kf2_ini": Kf2Ini,
           "gmod_collection": GmodCollection}


def _backup_write_text(path, text):
    import shutil
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.isfile(path):
        shutil.copy2(path, path + ".bak")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _backup_write_json(path, data):
    _backup_write_text(path, json.dumps(data, indent=2))


# --------------------------------------------------------------------------- #
class IdListBackend:
    """Ordered list of mod IDs; the server downloads them itself."""

    def __init__(self, mm, profile):
        self.mm = mm
        self.mgr = mm.mgr
        self.p = profile
        self.writer = WRITERS[profile["writer"]](self.mgr)

    def caps(self):
        return {"toggle": True, "reorder": self.p.get("max_items", 0) != 1, "update": False,
                "search": self.p.get("site") == "curseforge", "add": True, "remove_keeps_files": True,
                "add_file": self.p.get("site") == "workshop" and self.p.get("max_items", 0) != 1,
                "add_hint": self.p.get("add_hint") or "Steam Workshop ID or link",
                "fields": ["mod_ids"] if self.p["writer"] == "pz_ini" else (
                    ["name"] if self.p["writer"] == "reforger_json" else [])}

    # -- the stored list --------------------------------------------------------
    def items(self, srv) -> List[dict]:
        if srv.get("clusterId") and self.p["writer"] == "asa_launch":
            st = self.mgr.get_cluster_settings(srv["clusterId"])
            stored = [dict(i) for i in st.get("modItems") or [] if isinstance(i, dict) and i.get("id")]
            ids_now = [x for x in (st.get("mods") or "").split(",") if x.strip()]
            if stored and [i["id"] for i in stored if i.get("enabled", True)] == ids_now:
                return stored
            on = [{"id": x.strip(), "enabled": True} for x in ids_now]   # edited in Cluster Settings
            names = {i["id"]: i.get("name") for i in stored}
            for i in on:
                if names.get(i["id"]):
                    i["name"] = names[i["id"]]
            return on + [i for i in stored if not i.get("enabled", True) and i["id"] not in ids_now]
        if isinstance(srv.get("modList"), list):
            return [dict(i) for i in srv["modList"] if isinstance(i, dict) and i.get("id")]
        found = []
        try:
            found = self.writer.read(srv)
        except Exception:
            pass
        for i in found:
            i.setdefault("enabled", True)
        return found

    def save(self, srv, items) -> Optional[str]:
        if srv.get("clusterId") and self.p["writer"] == "asa_launch":
            st = dict(self.mgr.get_cluster_settings(srv["clusterId"]))
            before = [x for x in (st.get("mods") or "").split(",") if x.strip()]
            st["modItems"] = items
            st["mods"] = ",".join(i["id"] for i in items if i.get("enabled", True))
            n = self.mgr.apply_cluster_settings(srv["clusterId"], st)
            for sid in [s["id"] for s in self.mgr.load_servers() if s.get("clusterId") == srv["clusterId"]]:
                self.mm._changed(sid)
            note = "Updated all %d maps in cluster %s." % (n, srv["clusterId"])
            gone = [x for x in before if x not in st["mods"].split(",")]
            if gone:
                note += (" Creatures, items and buildings from the removed mod are gone on every map once "
                         "they restart - turn it back on before restarting if that wasn't intended.")
            return note
        servers = self.mgr.load_servers()
        for s in servers:
            if s.get("id") == srv["id"]:
                s["modList"] = items
                srv.update(s)
        self.mgr.save_servers(servers)
        note = self.writer.write(srv, items)
        self.mm._changed(srv["id"])
        return note

    # -- operations ------------------------------------------------------------------
    def parse_ref(self, ref) -> dict:
        s = str(ref or "").strip()
        site = self.p.get("site")
        if site == "reforger":
            m = re.search(r"\b([0-9A-Fa-f]{16})\b", s)
            if not m:
                raise ModError("That isn't an Arma Reforger mod ID (16 letters/digits like 5AAAC70D754245DD).")
            return {"id": m.group(1).upper()}
        if site == "curseforge":
            m = re.search(r"\b(\d{5,9})\b", s)
            if not m:
                raise ModError("Enter the CurseForge Project ID (a number on the mod's page, e.g. 928793).")
            return {"id": m.group(1)}
        pm = re.match(r"^(.*?\d)\s*:\s*([A-Za-z][\w;, .\-]*)$", s) if self.p["writer"] == "pz_ini" else None
        if pm:
            wid, mods = pm.group(1), pm.group(2)
            w = workshop_id(wid)
            if not w:
                raise ModError("Enter a Workshop ID or link")
            return {"id": w, "mod_ids": [m.strip() for m in re.split(r"[;,]", mods) if m.strip()]}
        w = workshop_id(s)
        if not w:
            raise ModError("Enter a Steam Workshop ID or link")
        return {"id": w}

    def install(self, srv, ref):
        item = self.parse_ref(ref)
        items = self.items(srv)
        if any(i["id"] == item["id"] for i in items):
            raise ModError("That mod is already in the list.")
        if self.p.get("max_items") == 1:
            items = []
        item["enabled"] = True
        info = self.lookup([item["id"]]).get(item["id"]) or {}
        if info.get("name") and not item.get("name"):
            item["name"] = info["name"]
        items.append(item)
        note = self.save(srv, items)
        return {"installed": item.get("name") or item["id"], "note": note}

    def remove(self, srv, key):
        items = [i for i in self.items(srv) if i["id"] != key]
        note = self.save(srv, items)
        return {"removed": key, "note": note}

    def set_enabled(self, srv, key, on):
        items = self.items(srv)
        for i in items:
            if i["id"] == key:
                i["enabled"] = bool(on)
        return {"changed": True, "note": self.save(srv, items)}

    def move(self, srv, key, delta):
        items = self.items(srv)
        idx = next((n for n, i in enumerate(items) if i["id"] == key), None)
        if idx is None:
            raise ModError("Mod not found")
        j = max(0, min(len(items) - 1, idx + delta))
        items.insert(j, items.pop(idx))
        return {"changed": True, "note": self.save(srv, items)}

    def set_field(self, srv, key, field, value):
        if field not in self.caps()["fields"]:
            raise ModError("That can't be changed for this game")
        items = self.items(srv)
        for i in items:
            if i["id"] == key:
                if field == "mod_ids":
                    i["mod_ids"] = [m.strip() for m in re.split(r"[;,\s]+", str(value or "")) if m.strip()]
                else:
                    i[field] = str(value or "").strip()[:120]
        return {"changed": True, "note": self.save(srv, items)}

    def lookup(self, ids) -> Dict[str, dict]:
        site = self.p.get("site")
        out: Dict[str, dict] = {}
        if not ids:
            return out
        if site == "workshop":
            for k, d in self.mm.steam_details(list(ids)).items():
                out[k] = {"name": d.get("title"), "updated": d.get("time_updated"),
                          "url": "https://steamcommunity.com/sharedfiles/filedetails/?id=" + k,
                          "size": d.get("file_size")}
        elif site == "curseforge":
            key = self.mgr.get_setting("curseforge_api_key", "") or ""
            if key:
                try:
                    data = http_json("https://api.curseforge.com/v1/mods", {"modIds": [int(x) for x in ids]},
                                     headers={"x-api-key": key}, timeout=20)
                    for d in data.get("data") or []:
                        out[str(d.get("id"))] = {"name": d.get("name"),
                                                 "url": (d.get("links") or {}).get("websiteUrl"),
                                                 "summary": d.get("summary")}
                except Exception:
                    pass
        return out

    def list(self, srv, check):
        items = self.items(srv)
        info = self.lookup([i["id"] for i in items])
        pz = self.p["writer"] == "pz_ini"
        changed = False
        mods = []
        for n, i in enumerate(items):
            d = info.get(i["id"]) or {}
            if pz and not i.get("mod_ids"):
                found = self.writer.found_mod_ids(srv, i["id"])
                if found:
                    i["mod_ids"] = found
                    changed = True
            if d.get("name") and i.get("name") != d["name"] and self.p.get("site") != "reforger":
                i["name"] = d["name"]
                changed = True
            mods.append({"key": i["id"], "id": i["id"], "name": i.get("name") or d.get("name") or i["id"],
                         "version": i.get("version") or "", "enabled": i.get("enabled", True), "order": n + 1,
                         "url": d.get("url") or self._url(i["id"]), "size": d.get("size") or 0,
                         "mod_ids": i.get("mod_ids"), "summary": d.get("summary")})
        if changed and isinstance(srv.get("modList"), list):
            servers = self.mgr.load_servers()
            for s in servers:
                if s.get("id") == srv["id"]:
                    s["modList"] = items
            self.mgr.save_servers(servers)
        extra = {"where": self.writer.where(srv)}
        if self.p.get("site") == "curseforge" and not self.mgr.get_setting("curseforge_api_key", ""):
            extra["hint"] = ("Add a free CurseForge API key (console.curseforge.com) in the options below to "
                             "see mod names.")
        return mods, extra

    # -- CurseForge browsing (ARK: Survival Ascended) --------------------------------
    CF_API = "https://api.curseforge.com/v1"
    CF_SORT = {"popular": 2, "updated": 3, "downloads": 6, "newest": 11, "trending": 2, "follows": 6}
    _cf_game = {}

    def _cf_key(self):
        key = self.mgr.get_setting("curseforge_api_key", "") or ""
        if not key:
            raise ModError("Browsing ARK mods needs a free CurseForge API key: make one at console.curseforge.com "
                           "and paste it in the Mods tab options (CurseForge API key). You can still add mods by "
                           "their Project ID without it.")
        return key

    def _cf(self, path, params=None):
        import urllib.parse
        from core.mods_common import friendly_http
        url = self.CF_API + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        try:
            return http_json(url, headers={"x-api-key": self._cf_key()}, timeout=20)
        except ModError:
            raise
        except Exception as e:
            if getattr(e, "code", None) == 403:
                raise ModError("CurseForge refused the API key - check it in the Mods tab options.")
            raise friendly_http(e, "CurseForge")

    def _cf_game_id(self):
        """ARK: SA's CurseForge game id (looked up once; 83374 if the lookup fails)."""
        if "id" not in self._cf_game:
            gid = 83374
            try:
                for g in self._cf("/games", {"pageSize": 50}).get("data") or []:
                    if "ark" in (g.get("slug") or "") and "ascended" in (g.get("slug") or g.get("name") or "").lower():
                        gid = int(g["id"])
                        break
            except ModError:
                raise
            except Exception:
                pass
            type(self)._cf_game["id"] = gid
        return self._cf_game["id"]

    @staticmethod
    def _cf_hit(d):
        from core.mod_browse import safe_img, _ids
        logo = (d.get("logo") or {}).get("thumbnailUrl") or (d.get("logo") or {}).get("url")
        return {"id": str(d.get("id")), "name": d.get("name"), "summary": (d.get("summary") or "")[:200],
                "author": ", ".join(a.get("name") for a in (d.get("authors") or [])[:2] if a.get("name")) or None,
                "downloads": d.get("downloadCount"), "follows": None, "icon": safe_img(logo),
                "tags": [c.get("name") for c in (d.get("categories") or []) if c.get("name")][:5],
                "updated": d.get("dateModified"), "match_ids": _ids(d.get("id")),
                "url": (d.get("links") or {}).get("websiteUrl") or "https://www.curseforge.com/projects/%s" % d.get("id")}

    def search(self, srv, text, sort=None):
        if self.p.get("site") != "curseforge":
            raise ModError("Search isn't available here - paste the mod's ID or link instead.")
        params = {"gameId": self._cf_game_id(), "pageSize": 40, "sortOrder": "desc",
                  "sortField": self.CF_SORT.get(sort or "", 2)}
        if text:
            params["searchFilter"] = text
        return [self._cf_hit(d) for d in self._cf("/mods/search", params).get("data") or []]

    def details(self, srv, mod_id):
        from core.mod_browse import html_to_blocks, safe_img
        if self.p.get("site") != "curseforge":
            raise ModError("Mod details aren't available here.")
        if not str(mod_id).isdigit():
            raise ModError("That isn't a CurseForge Project ID")
        d = self._cf("/mods/%s" % mod_id).get("data") or {}
        try:
            desc = self._cf("/mods/%s/description" % mod_id).get("data") or ""
        except ModError:
            desc = d.get("summary") or ""
        hit = self._cf_hit(d)
        blocks = html_to_blocks(desc)
        shots = [safe_img(s.get("url")) for s in d.get("screenshots") or []]
        files = sorted(d.get("latestFiles") or [], key=lambda f: f.get("fileDate") or "", reverse=True)
        links = [(lbl, (d.get("links") or {}).get(k)) for lbl, k in (("Wiki", "wikiUrl"), ("Issues", "issuesUrl"),
                                                                   ("Source code", "sourceUrl"))
                 if ((d.get("links") or {}).get(k) or "").startswith("https://")]
        return {"id": hit["id"], "name": hit["name"], "author": hit["author"], "icon": hit["icon"],
                "downloads": hit["downloads"], "follows": None, "tags": hit["tags"], "side": "both",
                "updated": hit["updated"], "created": d.get("dateCreated"), "url": hit["url"],
                "summary": d.get("summary"), "blocks": blocks,
                "images": ([s for s in shots if s] + [b["src"] for b in blocks if b["t"] == "img"])[:12],
                "links": links, "game_version": None,
                "release": None,   # the server downloads the newest file itself
                "versions": [{"version": f.get("displayName"), "for": [], "date": f.get("fileDate")}
                             for f in files[:8]],
                "match_ids": hit["match_ids"],
                "cluster_note": ("Installing adds it to every map in cluster %s." % srv["clusterId"])
                if srv.get("clusterId") else None}

    def _url(self, mid):
        site = self.p.get("site")
        if site == "workshop":
            return "https://steamcommunity.com/sharedfiles/filedetails/?id=" + mid
        if site == "reforger":
            return "https://reforger.armaplatform.com/workshop/" + mid
        if site == "curseforge":
            return "https://www.curseforge.com/projects/" + mid
        return None
