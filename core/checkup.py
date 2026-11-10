"""One "Checkup" list of things that will bite later, each with what to do.

Looks across every server: missing game files, Java / .NET for Minecraft and
Vintage Story, RCON for games that ignore their console, Windows Firewall rules,
port clashes, backups, low disk space. Items: {"server_id", "server", "level"
("problem" | "warning" | "tip"), "title", "fix", "action"} where action is a
button the panels can offer ("install", "firewall", "backup", "schedule",
"game_settings" or None).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional

ORDER = {"problem": 0, "warning": 1, "tip": 2}
_cache: Dict[str, tuple] = {}


def _item(srv, level, title, fix, action=None):
    return {"server_id": srv.get("id") if srv else None, "server": (srv or {}).get("name"),
            "level": level, "title": title, "fix": fix, "action": action}


def _schedules(base: Path) -> dict:
    try:
        return json.loads((base / "agent_state.json").read_text(encoding="utf-8")).get("schedules") or {}
    except Exception:
        return {}


def run(mgr, firewall: bool = True, max_age: float = 300.0, force: bool = False) -> dict:
    key = str(id(mgr))
    hit = _cache.get(key)
    if hit and not force and time.time() - hit[0] < max_age:
        return hit[1]
    res = _run(mgr, firewall)
    _cache[key] = (time.time(), res)
    return res


def _run(mgr, firewall: bool) -> dict:
    from core import game_installers as gi
    from core import rcon_config
    from core import minecraft as mc
    items: List[dict] = []
    servers = [s for s in mgr.load_servers() if s.get("id")]
    base = Path(mgr.config_path).parent
    sched = _schedules(base)
    try:
        statuses = {s["id"]: s for s in mgr.get_all_statuses()}
    except Exception:
        statuses = {}
    try:
        from core import port_plan
        pairs = port_plan.clash_pairs(mgr)
    except Exception:
        port_plan, pairs = None, []
    javas = None
    disks_seen = set()
    for srv in servers:
        sid = srv["id"]
        st = statuses.get(sid, {})
        kind = gi.kind(srv)
        d0 = srv.get("directory") or ""
        if d0 and not os.path.isdir(d0):
            share = d0.startswith("\\\\") or d0.startswith("//")
            items.append(_item(srv, "warning", "Can't reach its folder",
                               ("%s is on another PC or network share. Make sure that PC is on and the share is "
                                "available, or fix the folder in Edit Server." if share else
                                "%s doesn't exist. Fix the folder in Edit Server (or install the game).") % d0))
            continue
        try:
            from server_manager import broad_folder
            why = broad_folder(d0)
        except Exception:
            why = None
        if why:
            items.append(_item(srv, "warning", "Its folder is %s" % why,
                               "%s is %s, not a folder of its own, so BaoBun only looks at the top of it for "
                               "settings and backups. Move the server into its own folder (for example "
                               "GameServers\\%s) and update it in Edit Server." % (d0, why, srv.get("name") or "Server")))
        try:
            exe = mgr._resolve_executable(srv)[0]
            installed = os.path.isfile(exe)
        except Exception:
            installed = None
        if installed is False:
            if kind in ("steam", "vintage-story", "minecraft", "hytale") and (kind == "steam" or gi.folder_is_empty(d0)
                                                                               or kind == "hytale"):
                items.append(_item(srv, "problem", "Game files aren't installed",
                                   "Use Install to download them.", "install"))
            else:
                items.append(_item(srv, "problem", "Server program not found",
                                   "Copy the game's server files into %s, or fix the program path in "
                                   "Edit Server." % (srv.get("directory") or "its folder")))
            continue
        # Minecraft: Java + EULA
        if kind == "minecraft":
            need = mc.required_java(srv)
            if javas is None:
                try:
                    javas = mc.find_javas()
                except Exception:
                    javas = []
            if not (srv.get("javaPath") and os.path.isfile(srv["javaPath"])) and not mc.pick_java(need, javas):
                items.append(_item(srv, "problem", "Java %d isn't installed" % need,
                                   "Run Install on the server (BaoBun downloads Java), or install "
                                   "Temurin %d from adoptium.net." % need, "install"))
            if not gi._eula_ok(os.path.join(srv.get("directory") or "", "eula.txt")):
                items.append(_item(srv, "problem", "Minecraft EULA not accepted",
                                   "Run Install and tick the EULA box, or set eula=true in eula.txt.", "install"))
        if kind == "vintage-story" and os.name == "nt" and gi.vs_is_vanilla(srv):
            try:
                v = mgr.vs_server_version(sid)
            except Exception:
                v = None
            need = gi.vs_dotnet_major(v) if v else None
            if need and not gi.dotnet_has(need):
                items.append(_item(srv, "problem", ".NET %d runtime missing" % need,
                                   "Vintage Story %s needs it: install \".NET Desktop Runtime %d\" (x64) "
                                   "from dot.net." % (v, need)))
        if kind == "hytale":
            from core import hytale_install
            if not hytale_install.server_signed_in(srv):
                items.append(_item(srv, "warning", "Not signed in to Hytale yet",
                                   "Players can't join until the server is linked to a Hytale account once. "
                                   "Sign in starts it (if needed) and shows the link and code.", "hytale_signin"))
        # RCON for games that ignore the console
        prof = rcon_config.profile(srv)
        if prof and not prof.get("stdin"):
            try:
                ok = rcon_config.info(srv) is not None
            except Exception:
                ok = False
            if not ok:
                from core import rcon_setup
                auto = rcon_setup.supported(srv)
                items.append(_item(srv, "warning", "RCON is off",
                                   "%s ignores its console, so BaoBun can't warn players, save before "
                                   "backups or stop it cleanly (it gets force-stopped). %s"
                                   % (prof.get("name", "This game"),
                                      "Turn on RCON sets it up with its own port and a strong password."
                                      if auto else (prof.get("setup") or "")),
                                   "rcon" if auto else "game_settings"))
        if srv.get("portsPending"):
            items.append(_item(srv, "tip", "Ports will be set on the next start",
                               "The game hasn't made its config file yet, so its first start uses the default "
                               "port. Start it once, stop it, and BaoBun writes the planned ports in."))
        # backups
        try:
            b = mgr.list_server_backups(sid)
        except Exception:
            b = []
        try:
            from core import backup_check
            chk = backup_check.results(mgr).get(sid)
        except Exception:
            chk = None
        if chk and not chk.get("ok") and b and chk.get("name") == b[0]["name"]:
            good = (chk.get("last_good") or {}).get("name")
            items.append(_item(srv, "problem", "Newest backup failed its restore check",
                               "%s. Make a new backup now%s." % ("; ".join(chk.get("problems") or []).capitalize(),
                                                                (" - the last one known to restore is " + good)
                                                                if good else ""), "backup"))
        has_backup_schedule = isinstance(sched.get(sid), dict) and bool((sched.get(sid) or {}).get("backup"))
        newest = os.path.getmtime(b[0]["path"]) if b else None
        if not has_backup_schedule:
            if newest is None:
                items.append(_item(srv, "warning", "No backups yet",
                                   "Back it up now, and add a daily backup schedule.", "backup"))
            elif time.time() - newest > 7 * 86400:
                items.append(_item(srv, "warning", "Last backup is %d days old" % ((time.time() - newest) // 86400),
                                   "Add a backup schedule so it happens on its own.", "schedule"))
        # firewall (Windows): only for servers that have run, to keep this quick
        if firewall and os.name == "nt" and (st.get("isRunning") or newest):
            try:
                from core.hosting import firewall_rule_exists
                if firewall_rule_exists(srv.get("name", sid)) is False:
                    items.append(_item(srv, "tip", "No Windows Firewall rule",
                                       "Friends outside your PC may not be able to join. Use Open Firewall "
                                       "on the server.", "firewall"))
            except Exception:
                pass
        # disk
        d = srv.get("directory") or ""
        root = Path(d).anchor if d else ""
        if root and root not in disks_seen and os.path.isdir(d):
            disks_seen.add(root)
            try:
                free = shutil.disk_usage(d).free / 1024 ** 3
                if free < 10:
                    items.append(_item(None, "warning" if free >= 3 else "problem",
                                       "Only %.1f GB free on %s" % (free, root),
                                       "Updates and backups can fail. Delete old backups or move servers."))
            except Exception:
                pass
        if st.get("state") == "crashed":
            why = (st.get("crashHelp") or [{}])[0]
            items.append(_item(srv, "problem", "Crashed: " + (why.get("title") or "exit code %s" % st.get("exitCode")),
                               why.get("fix") or "Check the console for the last lines before it stopped."))
    # nightly restart needs BaoBun to start at boot; without a stored password the boot
    # task can't open network shares (server folders or the second backup folder)
    mirror = (mgr.get_setting("backup_mirror_dir", "") or "").strip()
    if os.name == "nt":
        try:
            from core import boot_task, nightly
            mode = boot_task.mode()
            if nightly.config(mgr)["enabled"] and mode is None and boot_task.installed() is False:
                items.append(_item(None, "warning", "Nightly restart is on, but BaoBun only starts when someone "
                                   "signs in", "Set up \"Start BaoBun when the PC turns on\" so the servers come "
                                   "back after the restart by themselves.", "nightly"))
            if mode == "s4u":
                for srv in servers:
                    d = srv.get("directory") or ""
                    if d.startswith("\\\\") or d.startswith("//"):
                        items.append(_item(srv, "warning", "Folder is on a network share",
                                           "When BaoBun starts at boot it can't open network shares. Move the "
                                           "server to a drive on this PC, or set up start-at-boot again with "
                                           "network access (Settings > Nightly restart).", "nightly"))
                if mirror.startswith("\\\\") or mirror.startswith("//"):
                    items.append(_item(None, "warning", "Backups to %s stop after a restart" % mirror,
                                       "BaoBun starts at boot without network access, so it can't copy backups to "
                                       "another PC. Set up start-at-boot with network access (Settings > Nightly "
                                       "restart) - Windows asks for your password once.", "nightly"))
        except Exception:
            pass
    ms = mgr.mirror_status() if hasattr(mgr, "mirror_status") else {}
    if mirror and ms.get("ok") is False and ms.get("target") == mirror:
        items.append(_item(None, "problem", "Copying backups to %s failed" % mirror,
                           "%s. Check that the other PC is on and the folder is shared and writable." %
                           (ms.get("error") or "Unknown error")))
    elif not mirror and servers:
        items.append(_item(None, "tip", "Backups only live on this PC",
                           "If this PC's drive dies, the worlds go with it. Set \"Also copy every backup to\" "
                           "in Settings to another drive or PC."))
    # ARK clusters: mods / cluster folder / save folders out of step
    try:
        from core import ark_cluster
        seen_c = set()
        for srv in servers:
            cid = srv.get("clusterId")
            if not cid or cid in seen_c:
                continue
            seen_c.add(cid)
            for it in ark_cluster.check(mgr, srv["id"])["issues"]:
                if it["level"] == "tip":
                    continue
                items.append({"server_id": srv["id"], "server": "Cluster " + cid, "level": it["level"],
                              "title": it["title"], "fix": it["fix"], "action": "ark"})
    except Exception:
        pass
    for pair in pairs:
        ports = pair["ports"]
        shown = ", ".join(str(p) for p in ports[:4]) + (" ..." if len(ports) > 4 else "")
        items.append(_item(None, "problem", "%s share port%s %s" % (" and ".join(pair["names"]),
                                                                   "s" if len(ports) > 1 else "", shown),
                           "They can't run at the same time like this. Fix automatically gives the server "
                           "that isn't running its own free ports.", "ports"))
    items.sort(key=lambda i: (ORDER.get(i["level"], 3), i.get("server") or ""))
    counts = {lvl: sum(1 for i in items if i["level"] == lvl) for lvl in ORDER}
    return {"items": items, "counts": counts, "checked_at": time.time()}
