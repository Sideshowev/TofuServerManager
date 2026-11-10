"""ARK clusters: keep every map in step so nobody loses creatures or items.

A cluster is every server with the same ``clusterId`` (BaoBun's ARK Cluster
setup). Players upload creatures, items and survivors on one map and download them
on another. Things go wrong when the maps don't match:

* a mod missing on the destination map -> its creatures/items vanish on download
* separate install folders without a shared -ClusterDirOverride -> uploads are
  stored per install and can't be downloaded on the other maps at all
* two maps saving into the same -AltSaveDirectoryName -> they overwrite each other
* different game builds / different tame-level caps on different installs ->
  downloads refused, or high-level tames destroyed (DestroyTamesOverLevelClamp)

check() lists these with a fix, fix() applies one, preflight() quietly puts a map's
mod list back in line right before it starts.

Games settings (Game.ini / GameUserSettings.ini) live in each install folder, so
maps sharing one install already share them; with several installs BaoBun writes
the same settings into every one of them.

Different rules per map: separate() gives one map its own copy of the install
(same game, mods and world), and points every map at one shared cluster folder so
transfers keep working. Its creature spawns, loot and most rules can then be set
for that map alone (where="this"); the settings transfers depend on (PARITY_KEYS)
are still written to every install.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
from typing import Callable, Dict, List, Optional

from core import ark_ini

ARK_IDS = ("2430930", "376030")
RULES = [
    # (key, label, kind, help, group)   GameUserSettings.ini [ServerSettings]
    ("DifficultyOffset", "Difficulty", "float", "Wild creature level and loot quality (1.0 = max level 150 on most maps).", "World"),
    ("DinoCountMultiplier", "Wild creature amount", "float", "How many wild creatures spawn (1.0 = normal).", "World"),
    ("XPMultiplier", "XP multiplier", "float", "Experience gain.", "Rates"),
    ("TamingSpeedMultiplier", "Taming speed", "float", "", "Rates"),
    ("HarvestAmountMultiplier", "Harvest amount", "float", "", "Rates"),
    ("MatingIntervalMultiplier", "Mating interval", "float", "Lower = breed more often.", "Rates"),
    ("EggHatchSpeedMultiplier", "Egg hatch speed", "float", "", "Rates"),
    ("BabyMatureSpeedMultiplier", "Baby mature speed", "float", "", "Rates"),
    ("BabyCuddleIntervalMultiplier", "Imprint interval", "float", "Lower = cuddle more often.", "Rates"),
    ("DayTimeSpeedScale", "Day length", "float", "Lower = longer days.", "World"),
    ("NightTimeSpeedScale", "Night length", "float", "Lower = longer nights.", "World"),
    ("MaxPlayers", "Max players", "int", "", "Players"),
    ("ServerPVE", "PvE (no player vs player damage)", "bool", "", "Players"),
    ("ServerHardcore", "Hardcore (start over on death)", "bool", "", "Players"),
    ("ShowMapPlayerLocation", "Show players on the map", "bool", "", "Players"),
    ("AllowThirdPersonPlayer", "Third-person camera", "bool", "", "Players"),
    ("MaxTamedDinos", "Max tamed creatures (whole server)", "int", "", "Tames"),
    ("MaxPersonalTamedDinos", "Max tamed creatures per tribe", "int", "0 = no limit.", "Tames"),
    ("DestroyTamesOverLevelClamp", "Destroy tames above level", "int",
     "0 = never. Must match on every map, or high-level creatures are destroyed when they arrive.", "Transfers"),
    ("PreventDownloadSurvivors", "Block downloading survivors", "bool", "", "Transfers"),
    ("PreventDownloadItems", "Block downloading items", "bool", "", "Transfers"),
    ("PreventDownloadDinos", "Block downloading creatures", "bool", "", "Transfers"),
    ("PreventUploadSurvivors", "Block uploading survivors", "bool", "", "Transfers"),
    ("PreventUploadItems", "Block uploading items", "bool", "", "Transfers"),
    ("PreventUploadDinos", "Block uploading creatures", "bool", "", "Transfers"),
    ("TributeItemExpirationSeconds", "Uploaded items kept for (seconds)", "int",
     "How long uploaded items wait to be downloaded before ARK deletes them.", "Transfers"),
    ("TributeDinoExpirationSeconds", "Uploaded creatures kept for (seconds)", "int", "", "Transfers"),
    ("TributeCharacterExpirationSeconds", "Uploaded survivors kept for (seconds)", "int", "", "Transfers"),
    ("CrossARKAllowForeignDinoDownloads", "Allow creatures from other maps' DLC", "bool",
     "Lets map-specific creatures (e.g. from Aberration) be downloaded on other maps.", "Transfers"),
]
RULE_KEYS = {r[0] for r in RULES}
# settings that must be identical on every install of a cluster
PARITY_KEYS = ("DestroyTamesOverLevelClamp", "PreventDownloadDinos", "PreventDownloadItems",
               "PreventDownloadSurvivors", "PreventUploadDinos", "PreventUploadItems", "PreventUploadSurvivors",
               "TributeItemExpirationSeconds", "TributeDinoExpirationSeconds", "TributeCharacterExpirationSeconds",
               "CrossARKAllowForeignDinoDownloads", "MaxPersonalTamedDinos")
MONTH = 30 * 86400


def is_ark(srv: dict) -> bool:
    return str(srv.get("appId") or "") in ARK_IDS or str(srv.get("appId") or "") == "346110"


def _norm(d: str) -> str:
    return os.path.normcase(os.path.normpath(d or ""))


def own_install(members: List[dict], srv: dict) -> bool:
    """True when no other map in the cluster runs from srv's install folder."""
    d = _norm(srv.get("directory") or "")
    return bool(d) and not any(_norm(s.get("directory") or "") == d for s in members if s.get("id") != srv.get("id"))


def scope(mgr, server_id: str, where: str = "all") -> dict:
    """The servers that share settings with this one: its whole cluster, or just itself.
    where="this": only this map's own install (it must have one - see separate())."""
    srv = mgr.get_server(server_id)
    if not srv:
        raise ValueError("Server not found")
    if not is_ark(srv):
        raise ValueError("This is for ARK servers")
    cid = srv.get("clusterId")
    members = [s for s in mgr.load_servers() if cid and s.get("clusterId") == cid] if cid else [srv]
    installs, seen = [], set()
    for s in members:
        d = s.get("directory") or ""
        if d and _norm(d) not in seen:
            seen.add(_norm(d))
            installs.append(d)
    out = {"cluster": cid, "members": members, "installs": installs, "server": srv, "all_installs": installs,
           "name": ("Cluster " + cid) if cid else srv.get("name"), "where": "all",
           "own": own_install(members, srv)}
    if where == "this" and cid and len(members) > 1:
        if not out["own"]:
            others = [s.get("name") for s in members if s["id"] != srv["id"] and
                      _norm(s.get("directory") or "") == _norm(srv.get("directory") or "")]
            raise ValueError("%s shares its install folder with %s, so its settings are theirs too. Give it its own "
                             "install folder first (ARK World > Health)." % (srv.get("name"), ", ".join(others)))
        out.update(members=[srv], installs=[srv["directory"]], where="this",
                   name="%s only" % (srv.get("name") or server_id))
    return out


# --------------------------------------------------------------------------- #
# launch-option helpers
# --------------------------------------------------------------------------- #
def flag(args: str, name: str) -> Optional[str]:
    m = re.search(r'(?i)(?:^|\s)-%s=("[^"]*"|\S+)' % re.escape(name), args or "")
    return m.group(1).strip('"') if m else None


def set_flag(args: str, name: str, value: Optional[str]) -> str:
    rx = re.compile(r'(?i)(\s|^)-%s=(?:"[^"]*"|\S+)' % re.escape(name))
    args = rx.sub("", args or "").strip()
    if value:
        v = '"%s"' % value if " " in value else value
        args = (args + " -%s=%s" % (name, v)).strip()
    return args


def map_mods(srv: dict) -> List[str]:
    """The map's -mods list without its own map mod (e.g. Svartalfheim's)."""
    from server_manager import ARK_REFORGE_MODS
    raw = flag(srv.get("launchArgs") or "", "mods") or ""
    own = {str(srv.get("arkMapMod") or ""), str(ARK_REFORGE_MODS.get(srv.get("arkFriendly") or "", ""))}
    try:
        from server_manager import ARK_MAP_MODS
        own.add(str(ARK_MAP_MODS.get(srv.get("arkMap") or "", "")))
    except Exception:
        pass
    return [m for m in (x.strip() for x in raw.split(",")) if m and m not in own]


def cluster_mods(mgr, cid: str) -> List[str]:
    return [m.strip() for m in (mgr.get_cluster_settings(cid).get("mods") or "").split(",") if m.strip()]


def _build(directory: str) -> Optional[str]:
    from server_manager import ServerManager
    for app in ARK_IDS:
        try:
            b = ServerManager.installed_buildid(directory, app)
        except Exception:
            b = None
        if b:
            return b
    return None


def _gus(directory: str, key: str) -> Optional[str]:
    from core.mods_common import ini_get
    v = ini_get(ark_ini.gus_ini(directory), "ServerSettings", key)
    return v[-1].strip() if v else None


# --------------------------------------------------------------------------- #
# the check
# --------------------------------------------------------------------------- #
def _issue(level, title, fix, action=None, servers=None):
    return {"level": level, "title": title, "fix": fix, "action": action, "servers": servers or []}


def check(mgr, server_id: str) -> dict:
    sc = scope(mgr, server_id)
    cid, members, installs = sc["cluster"], sc["members"], sc["installs"]
    issues: List[dict] = []
    names = {s["id"]: s.get("name") or s["id"] for s in members}
    maps = []
    want = cluster_mods(mgr, cid) if cid else None
    for s in members:
        args = s.get("launchArgs") or ""
        running = mgr.is_running(s["id"])
        mods = map_mods(s)
        row = {"id": s["id"], "name": names[s["id"]], "map": s.get("arkFriendly") or s.get("arkMap") or "",
               "running": running, "install": s.get("directory"), "mods": mods,
               "save_dir": flag(args, "AltSaveDirectoryName"), "cluster_dir": flag(args, "ClusterDirOverride"),
               "clusterid": flag(args, "clusterid"), "own_install": bool(cid) and len(installs) > 1 and
               own_install(members, s), "job": JOBS.get(s["id"])}
        maps.append(row)
        if cid:
            if (row["clusterid"] or "") != cid:
                issues.append(_issue("problem", "%s isn't joined to the cluster" % row["name"],
                                     "Its launch options are missing -clusterid=%s, so players can't transfer to "
                                     "or from it." % cid, "sync_maps", [s["id"]]))
            if want is not None and mods != want:
                missing = [m for m in want if m not in mods]
                extra = [m for m in mods if m not in want]
                bits = []
                if missing:
                    bits.append("missing " + ", ".join(missing))
                if extra:
                    bits.append("extra " + ", ".join(extra))
                if not bits:
                    bits.append("different load order")
                issues.append(_issue("problem", "%s has different mods (%s)" % (row["name"], "; ".join(bits)),
                                     "Creatures and items from a mod disappear when they're downloaded on a map "
                                     "without it. Sync puts the cluster's mod list on every map%s."
                                     % (" (restart it afterwards)" if running else ""), "sync_maps", [s["id"]]))
    # save folders
    by_save: Dict[tuple, List[str]] = {}
    for r in maps:
        key = (_norm(r["install"]), (r["save_dir"] or "").lower())
        by_save.setdefault(key, []).append(r["name"])
    for (inst, save), who in by_save.items():
        if len(who) > 1:
            issues.append(_issue("problem", "%s save into the same world folder" % " and ".join(who),
                                 "They'd overwrite each other's world. Give each map its own "
                                 "-AltSaveDirectoryName (Settings > Launch options) - ask before changing it on a "
                                 "map people play on, its current world is in the old folder."))
    # several installs
    if cid and len(installs) > 1:
        dirs = {(_norm(r["cluster_dir"]) if r["cluster_dir"] else None) for r in maps}
        if len(dirs) != 1 or None in dirs:
            issues.append(_issue("problem", "Maps use different install folders without a shared cluster folder",
                                 "Uploads are stored per install folder, so a creature uploaded on one install "
                                 "can't be downloaded on the other. Fix points every map at one shared cluster "
                                 "folder (-ClusterDirOverride).", "cluster_dir"))
        builds = {d: _build(d) for d in installs}
        if len({b for b in builds.values() if b}) > 1:
            issues.append(_issue("warning", "Installs are on different game versions",
                                 "Transfers between different versions can be refused. Update all maps stops "
                                 "every map (players get a warning), updates each install folder and starts "
                                 "them again.", "update_all"))
        diffs = []
        for k in PARITY_KEYS:
            vals = {_gus(d, k) for d in installs}
            if len(vals) > 1:
                diffs.append(k)
        if diffs:
            issues.append(_issue("warning", "Transfer settings differ between install folders",
                                 "Different: %s. These must match on every map or transfers fail / creatures are "
                                 "destroyed. Copy puts the values from %s on every install (each map keeps its own "
                                 "spawns, loot and other rules)." % (", ".join(diffs), installs[0]), "copy_settings"))
    # how long uploads wait
    if cid and installs:
        short = []
        for k, label in (("TributeDinoExpirationSeconds", "creatures"), ("TributeItemExpirationSeconds", "items"),
                         ("TributeCharacterExpirationSeconds", "survivors")):
            v = _gus(installs[0], k)
            try:
                if v is None or 0 < float(v) < 7 * 86400:
                    short.append(label)
            except ValueError:
                pass
        if short:
            issues.append(_issue("tip", "Uploaded %s expire quickly" % ", ".join(short),
                                 "ARK deletes uploads that sit too long (the default is short). Keep them for "
                                 "30 days so nothing is lost between play sessions.", "long_uploads"))
    order = {"problem": 0, "warning": 1, "tip": 2}
    issues.sort(key=lambda i: order.get(i["level"], 3))
    return {"cluster": cid, "name": sc["name"], "maps": maps, "installs": installs, "issues": issues,
            "mods": want if cid else None}


# --------------------------------------------------------------------------- #
# fixes
# --------------------------------------------------------------------------- #
def fix(mgr, server_id: str, action: str, user: str = "system") -> dict:
    sc = scope(mgr, server_id)
    cid = sc["cluster"]
    if action == "sync_maps":
        if not cid:
            raise ValueError("Not a cluster")
        n = mgr.apply_cluster_settings(cid, dict(mgr.get_cluster_settings(cid)))
        mgr._audit("ark_cluster_sync", server_id, user=user, cluster=cid)
        running = [s.get("name") for s in sc["members"] if mgr.is_running(s["id"])]
        note = "Put the cluster's mods and settings on all %d maps." % n
        if running:
            note += " Restart %s to use them." % ", ".join(running)
        return {"note": note}
    if action == "cluster_dir":
        if not cid:
            raise ValueError("Not a cluster")
        target = mgr.get_cluster_settings(cid).get("clusterDir") or os.path.join(
            sc["installs"][0], "ShooterGame", "Saved", "clusters")
        os.makedirs(target, exist_ok=True)
        servers = mgr.load_servers()
        for s in servers:
            if s.get("clusterId") == cid:
                s["launchArgs"] = set_flag(s.get("launchArgs") or "", "ClusterDirOverride", target)
        mgr.save_servers(servers)
        st = dict(mgr.get_cluster_settings(cid))
        st["clusterDir"] = target
        mgr._save_cluster_settings(cid, st)
        mgr._audit("ark_cluster_dir", server_id, user=user, cluster=cid, folder=target)
        return {"note": "Every map now keeps uploads in %s. Restart running maps to use it. Uploads made "
                        "before this are still in each install's own clusters folder." % target}
    if action == "copy_settings":
        return copy_settings(mgr, server_id, user=user)
    if action == "update_all":
        import threading

        def run():
            try:
                mgr.update_server(server_id, user=user, force=True)
            except Exception as e:
                print("[WARN] cluster update failed: %s" % e)
        threading.Thread(target=run, daemon=True, name="cluster-update").start()
        return {"note": "Updating every map of the cluster: running maps get a warning, stop, update and start "
                        "again. This can take a while."}
    if action == "long_uploads":
        save_rules(mgr, server_id, {"TributeDinoExpirationSeconds": MONTH, "TributeItemExpirationSeconds": MONTH,
                                    "TributeCharacterExpirationSeconds": MONTH}, user=user)
        return {"note": "Uploads are now kept for 30 days on every map. Restart running maps to use it."}
    raise ValueError("Unknown fix")


def copy_settings(mgr, server_id: str, user: str = "system") -> dict:
    """Put the transfer settings (PARITY_KEYS) from the first install on every other
    install. Spawns, loot and other rules stay as each install has them."""
    from core.mods_common import ini_set
    sc = scope(mgr, server_id)
    src = sc["installs"][0]
    vals = {k: _gus(src, k) for k in PARITY_KEYS}
    done = 0
    for d in sc["installs"][1:]:
        p = ark_ini.gus_ini(d)
        if os.path.isfile(p):
            shutil.copy2(p, p + ".bak")
        else:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "a").close()
        for k, v in vals.items():
            if v is not None or _gus(d, k) is not None:
                ini_set(p, "ServerSettings", k, v)
        done += 1
    mgr._audit("ark_copy_settings", server_id, user=user, source=src)
    return {"note": "Transfer settings from %s are now on %d other install(s)." % (src, done), "files": done}


def preflight(mgr, srv: dict) -> List[str]:
    """Right before a cluster map starts: put the cluster's mod list (and -clusterid)
    back on it if its launch options drifted. Returns what was changed."""
    cid = srv.get("clusterId")
    if not cid or not srv.get("arkMap") or not is_ark(srv):
        return []
    st = mgr.get_cluster_settings(cid)
    if not st:
        return []
    want = cluster_mods(mgr, cid)
    args = srv.get("launchArgs") or ""
    if map_mods(srv) == want and (flag(args, "clusterid") or "") == cid:
        return []
    mgr.apply_cluster_settings(cid, dict(st))
    fresh = mgr.get_server(srv["id"]) or srv
    srv.update(fresh)
    return ["mods / cluster ID put back in line with the cluster"]


# --------------------------------------------------------------------------- #
# rules, spawns, loot across every install
# --------------------------------------------------------------------------- #
def read_rules(mgr, server_id: str, where: str = "all") -> List[dict]:
    sc = scope(mgr, server_id, where)
    d = sc["installs"][0] if sc["installs"] else ""
    out = []
    for key, label, kind, help_text, group in RULES:
        out.append({"key": key, "label": label, "kind": kind, "help": help_text, "group": group,
                    "value": _gus(d, key)})
    nums = ark_ini.read_numbers(ark_ini.game_ini(d))
    out.append({"key": "SupplyCrateLootQualityMultiplier", "label": "Supply drop loot quality", "kind": "float",
                "help": "Higher = better items in drops and crates.", "group": "Loot",
                "value": None if nums["SupplyCrateLootQualityMultiplier"] is None
                else str(nums["SupplyCrateLootQualityMultiplier"])})
    out.append({"key": "FishingLootQualityMultiplier", "label": "Fishing loot quality", "kind": "float",
                "help": "", "group": "Loot", "value": None if nums["FishingLootQualityMultiplier"] is None
                else str(nums["FishingLootQualityMultiplier"])})
    return out


def _clean(kind: str, v):
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip()
    if kind == "bool":
        return "True" if s.lower() in ("1", "true", "yes", "on") else "False"
    if kind == "int":
        n = int(float(s))
        if n < 0:
            raise ValueError("Use 0 or more")
        return str(n)
    f = float(s)
    if f < 0:
        raise ValueError("Use 0 or more")
    return ("%.4f" % f).rstrip("0").rstrip(".") if f != int(f) else "%.1f" % f


def save_rules(mgr, server_id: str, values: Dict[str, object], user: str = "system", where: str = "all") -> dict:
    from core.mods_common import ini_set
    sc = scope(mgr, server_id, where)
    kinds = {r[0]: r[2] for r in RULES}
    gus, nums = {}, {}
    for k, v in values.items():
        if k in kinds:
            try:
                gus[k] = _clean(kinds[k], v)
            except ValueError as e:
                raise ValueError("%s: %s" % (k, e))
        elif k in ark_ini.NUM_KEYS:
            nums[k] = None if _clean("float", v) is None else float(_clean("float", v))
    shared = {k: v for k, v in gus.items() if k in PARITY_KEYS}
    for d in sc["all_installs"]:
        mine = d in sc["installs"]
        todo = gus if mine else shared
        p = ark_ini.gus_ini(d)
        if todo:
            if os.path.isfile(p):
                shutil.copy2(p, p + ".bak")
            else:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "a").close()
            for k, v in todo.items():
                ini_set(p, "ServerSettings", k, v)
        if nums and mine:
            ark_ini.save(ark_ini.game_ini(d), numbers=nums)
    mgr._audit("ark_rules", server_id, user=user, keys=sorted(list(gus) + list(nums)), where=sc["where"])
    note = _restart_note(mgr, sc)
    if sc["where"] == "this" and shared:
        note += " Transfer settings (%s) went to every map so transfers keep working." % ", ".join(sorted(shared))
    return {"saved": len(gus) + len(nums), "installs": len(sc["installs"]), "note": note}


def _restart_note(mgr, sc) -> str:
    running = [s.get("name") for s in sc["members"] if mgr.is_running(s["id"])]
    where = ("%s only" % sc["server"].get("name")) if sc.get("where") == "this" else \
        "every map in the cluster" if sc["cluster"] else "the server"
    if running:
        return "Saved for %s. Restart %s to use it." % (where, ", ".join(running))
    return "Saved for %s - used from the next start." % where


def _scope_info(sc) -> dict:
    return {"scope": sc["name"], "shared": len(sc["members"]) > 1, "where": sc["where"], "own": sc["own"],
            "can_split": bool(sc["cluster"]) and len(sc["all_installs"]) > 1 and sc["own"]}


def read_spawns(mgr, server_id: str, where: str = "all") -> dict:
    from core import ark_catalog
    sc = scope(mgr, server_id, where)
    rules = ark_ini.read_spawns(ark_ini.game_ini(sc["installs"][0])) if sc["installs"] else []
    for r in rules:
        r["from_name"] = ark_catalog.creature_name(r["from"])
        r["to_name"] = ark_catalog.creature_name(r["to"]) if r["to"] else ""
    return dict(_scope_info(sc), rules=rules)


def save_spawns(mgr, server_id: str, rules: List[dict], user: str = "system", where: str = "all") -> dict:
    sc = scope(mgr, server_id, where)
    for d in sc["installs"]:
        ark_ini.save(ark_ini.game_ini(d), spawns=rules)
    mgr._audit("ark_spawns", server_id, user=user, rules=len(rules))
    note = _restart_note(mgr, sc) + " Creatures already in the world stay until they die or you wipe wild creatures."
    return {"saved": len(rules), "note": note}


def read_loot(mgr, server_id: str, where: str = "all") -> dict:
    from core import ark_catalog
    sc = scope(mgr, server_id, where)
    loot = ark_ini.read_loot(ark_ini.game_ini(sc["installs"][0])) if sc["installs"] else []
    for r in loot:
        r["crate_name"] = ark_catalog.crate_name(r.get("crate"))
        for i in r.get("items") or []:
            i["name"] = ark_catalog.item_name(i["cls"])
    return dict(_scope_info(sc), loot=loot)


def save_loot(mgr, server_id: str, loot: List[dict], user: str = "system", where: str = "all") -> dict:
    sc = scope(mgr, server_id, where)
    for r in loot:          # validate before touching any file
        ark_ini.loot_to_struct(r)
    for d in sc["installs"]:
        ark_ini.save(ark_ini.game_ini(d), loot=loot)
    mgr._audit("ark_loot", server_id, user=user, crates=len(loot))
    return {"saved": len(loot), "note": _restart_note(mgr, sc)}


def rules_info(mgr, server_id: str, where: str = "all") -> dict:
    sc = scope(mgr, server_id, where)
    return dict(_scope_info(sc), rules=read_rules(mgr, server_id, where))


def wipe_wild(mgr, server_id: str, user: str = "system", where: str = "all") -> dict:
    """DestroyWildDinos on every running map (new spawn rules then take over)."""
    sc = scope(mgr, server_id, where)
    done, failed = [], []
    for s in sc["members"]:
        if not mgr.is_running(s["id"]):
            continue
        try:
            mgr.send_console_command(s["id"], "DestroyWildDinos", user=user)
            done.append(s.get("name"))
        except Exception as e:
            failed.append("%s (%s)" % (s.get("name"), e))
    mgr._audit("ark_wipe_wild", server_id, user=user, maps=done)
    if not done and not failed:
        return {"note": "No map is running - nothing to wipe. New spawn rules apply as they start."}
    note = "Wiped wild creatures on %s." % (", ".join(done) or "no map")
    if failed:
        note += " Failed: " + "; ".join(failed)
    return {"note": note, "done": done, "failed": failed}


# --------------------------------------------------------------------------- #
# one map, its own install
# --------------------------------------------------------------------------- #
JOBS: Dict[str, dict] = {}
SKIP_DIRS = {os.path.normcase(os.path.join("ShooterGame", "Saved", x)) for x in ("Logs", "clusters", "Crashes")}


def default_target(srv: dict) -> str:
    base = os.path.normpath(srv.get("directory") or "")
    tag = re.sub(r"[^A-Za-z0-9]+", "", str(srv.get("arkFriendly") or srv.get("arkMap") or srv.get("name") or "map"))
    cand = "%s-%s" % (base, tag or "map")
    n = 2
    while os.path.exists(cand) and os.listdir(cand):
        cand = "%s-%s%d" % (base, tag or "map", n)
        n += 1
    return cand


def _walk(src: str):
    """(path, size) for every file to copy, skipping logs / crash dumps / the old uploads folder."""
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs
                   if os.path.normcase(os.path.relpath(os.path.join(root, d), src)) not in SKIP_DIRS]
        for f in files:
            p = os.path.join(root, f)
            try:
                yield p, os.path.getsize(p)
            except OSError:
                pass


def separate_plan(mgr, server_id: str, target: Optional[str] = None) -> dict:
    """What separate() would do (and whether it can)."""
    sc = scope(mgr, server_id)
    srv = sc["server"]
    if not sc["cluster"]:
        raise ValueError("Only maps in a cluster share an install - this server already has its own.")
    if sc["own"]:
        raise ValueError("%s already has its own install folder." % srv.get("name"))
    target = os.path.normpath(target or default_target(srv))
    src = srv["directory"]
    if _norm(target).startswith(_norm(src) + os.sep) or _norm(target) == _norm(src):
        raise ValueError("Pick a folder outside the current install.")
    if os.path.exists(target) and os.listdir(target):
        raise ValueError("%s isn't empty - pick a new folder." % target)
    total = sum(sz for _p, sz in _walk(src))
    probe = target
    while probe and not os.path.exists(probe):
        probe = os.path.dirname(probe)
    try:
        free = shutil.disk_usage(probe or src).free
    except OSError:
        free = None
    return {"source": src, "target": target, "bytes": total, "free": free,
            "fits": free is None or free > total * 1.05 + 256 * 1024 ** 2,
            "running": mgr.is_running(server_id), "name": srv.get("name")}


def separate(mgr, server_id: str, target: Optional[str] = None, user: str = "system",
             progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """Copy the shared install to target and move this map onto it. The map must be
    stopped. Every map is pointed at one shared cluster folder so uploads still
    work both ways. The old install keeps its files (and this map's old world copy)."""
    plan = separate_plan(mgr, server_id, target)
    if plan["running"]:
        raise RuntimeError("Stop %s first." % plan["name"])
    if not plan["fits"]:
        raise RuntimeError("Not enough free space: the copy needs about %.0f GB." % (plan["bytes"] / 1024 ** 3))
    src, dst = plan["source"], plan["target"]
    sc = scope(mgr, server_id)
    cid = sc["cluster"]
    # 1. one shared cluster folder for every map (before the installs split)
    st = dict(mgr.get_cluster_settings(cid))
    shared = st.get("clusterDir") or next((r for r in (flag(s.get("launchArgs") or "", "ClusterDirOverride")
                                                       for s in sc["members"]) if r), None) \
        or os.path.join(src, "ShooterGame", "Saved", "clusters")
    os.makedirs(shared, exist_ok=True)
    # 2. copy
    done, total = 0, max(1, plan["bytes"])
    os.makedirs(dst, exist_ok=True)
    last = 0.0
    for p, sz in _walk(src):
        rel = os.path.relpath(p, src)
        out = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy2(p, out)
        done += sz
        if progress and time.time() - last > 0.5:
            last = time.time()
            progress(100.0 * done / total, rel)
    # 3. point the map at the copy, and every map at the shared cluster folder
    servers = mgr.load_servers()
    old = os.path.normpath(src)
    for s in servers:
        if s.get("clusterId") == cid:
            s["launchArgs"] = set_flag(s.get("launchArgs") or "", "ClusterDirOverride", shared)
        if s.get("id") == server_id:
            for k, v in list(s.items()):
                if isinstance(v, str) and _norm(v).startswith(_norm(old) + os.sep):
                    s[k] = os.path.join(dst, os.path.relpath(v, old))
            s["directory"] = dst
    mgr.save_servers(servers)
    st["clusterDir"] = shared
    mgr._save_cluster_settings(cid, st)
    mgr._audit("ark_separate", server_id, user=user, source=src, target=dst)
    if progress:
        progress(100.0, "done")
    return {"target": dst, "cluster_dir": shared,
            "note": "%s now runs from %s with its own settings. Uploads for every map are in %s. Its old world "
                    "copy stays in %s as a backup. Use Open Firewall on it again, and update both installs when "
                    "ARK updates." % (plan["name"], dst, shared, src)}


def start_separate(mgr, server_id: str, target: Optional[str] = None, user: str = "system") -> dict:
    """separate() in the background; progress in JOBS[server_id] (shown by check())."""
    plan = separate_plan(mgr, server_id, target)
    if plan["running"]:
        raise RuntimeError("Stop %s first." % plan["name"])
    if not plan["fits"]:
        raise RuntimeError("Not enough free space: the copy needs about %.0f GB." % (plan["bytes"] / 1024 ** 3))
    if (JOBS.get(server_id) or {}).get("state") == "copying":
        raise RuntimeError("Already copying.")
    JOBS[server_id] = {"state": "copying", "pct": 0.0, "target": plan["target"], "started": time.time()}

    def prog(pct, _what):
        JOBS[server_id]["pct"] = round(pct, 1)

    def work():
        try:
            r = separate(mgr, server_id, plan["target"], user=user, progress=prog)
            JOBS[server_id] = {"state": "done", "pct": 100.0, "note": r["note"], "at": time.time()}
        except Exception as e:
            JOBS[server_id] = {"state": "failed", "error": str(e), "at": time.time()}
    threading.Thread(target=work, daemon=True, name="ark-separate").start()
    return dict(plan, state="copying")
