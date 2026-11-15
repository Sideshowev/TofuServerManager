"""Game smoke test: does each game really install, start and show its settings?

Run on a PC with plenty of free disk space (run_game_smoke_test.bat). For each
game it, in a separate test copy of BaoBun (your real servers, settings and
backups are never touched):

  1. adds the server and installs it (SteamCMD / the game's own download),
  2. starts it and waits up to a few minutes for it to be ready (its port opens
     or it answers a player query) - or notes how it failed, with the end of its
     log,
  3. lists what the Settings tab would show (known settings files, found files,
     Essentials),
  4. stops it and (unless --keep) deletes the install to free the space again.

Results go to logs/game_smoke_report.md (to read) and .json (to resume - games
already tested are skipped unless --redo). Windows Firewall may ask about each
game's program; answering isn't needed for the test.

  python tools/game_smoke_test.py                      popular games without a Steam account
  python tools/game_smoke_test.py --games "Valheim,Terraria"
  python tools/game_smoke_test.py --dir D:\\SmokeTest --keep --wait 300
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MIN_FREE_GB = 30
SETTINGS_CHECK = True
SKIP = {"Hytale": "needs a Hytale account sign-in", "Custom / Other": "not a game"}


def pick_games(presets, wanted=None, include_account=False, everything=False):
    if wanted:
        names = [w.strip() for w in wanted.split(",") if w.strip()]
        missing = [n for n in names if n not in presets]
        if missing:
            sys.exit("Unknown game(s): %s" % ", ".join(missing))
        return names
    out = []
    for name, p in sorted(presets.items()):
        if (not p.get("popular") and not everything) or name in SKIP:
            continue
        if everything and not p.get("appId") and not p.get("popular"):
            continue          # not on SteamCMD: BaoBun can't download it to test
        if p.get("appId") and p.get("anonymous_login") is False and not include_account:
            continue
        out.append(name)
    return out


def default_dir():
    from core.first_run import drives
    ds = drives()
    base = ds[0]["path"] if ds else str(Path.home())
    return os.path.join(base, "BaoBunSmokeTest")


def free_gb(path):
    try:
        return shutil.disk_usage(path).free / 1024 ** 3
    except OSError:
        return 0


def tail_log(mgr, sid, lines=30):
    try:
        p = mgr.get_log_path(sid)
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 32000))
            text = f.read().decode("utf-8", "replace")
        return "\n".join(text.splitlines()[-lines:])
    except Exception:
        return ""


NAME_KEYS = ("servername", "server_name", "hostname", "sessionname", "session_name", "servertitle",
             "server_title", "serverdisplayname", "name")
OUTSIDE_SKIP = {"temp", "microsoft", "google", "mozilla", "packages", "nvidia", "nvidia corporation", "pip",
                "baobun", "claude", "anthropic", "discord", "steam", "crashdumps", "d3dscache", "npm-cache",
                "programs", "code", "spotify", "comms", "connecteddevicesplatform", "intel", "amd", "nuget"}
CONFIG_EXT = {".ini", ".json", ".cfg", ".xml", ".yaml", ".yml", ".toml", ".properties", ".txt", ".conf", ".lua",
              ".sav", ".db"}


def outside_roots():
    home = Path.home()
    roots = [os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA"), str(home / "AppData" / "LocalLow"),
             str(home / "Documents"), str(home / "Saved Games")]
    return [r for r in roots if r and os.path.isdir(r)]


def files_written_outside(since, install_dir, limit=4000, seconds=20):
    """Folders outside the install where files were written since `since` (where a
    game keeps settings / saves it doesn't put in its own folder)."""
    t0, seen, hits = time.time(), 0, {}
    inst = os.path.normcase(os.path.abspath(install_dir or ""))
    for root in outside_roots():
        for cur, dirs, files in os.walk(root):
            depth = cur[len(root):].count(os.sep)
            dirs[:] = [d for d in dirs if d.lower() not in OUTSIDE_SKIP and not d.startswith(".")] if depth < 5 else []
            if os.path.normcase(os.path.abspath(cur)).startswith(inst):
                dirs[:] = []
                continue
            for f in files:
                seen += 1
                if os.path.splitext(f)[1].lower() not in CONFIG_EXT:
                    continue
                try:
                    if os.path.getmtime(os.path.join(cur, f)) >= since:
                        hits.setdefault(cur, []).append(f)
                except OSError:
                    pass
            if seen > limit * 10 or time.time() - t0 > seconds:
                break
    return {d: sorted(fs)[:8] for d, fs in list(hits.items())[:15]}


def find_name_field(mgr, sid):
    """(file id, field id, key, current value) of the setting that names the server."""
    from core import config_forms
    files = [f for f in config_forms.files(mgr, sid) if f.get("exists") and f.get("format")]
    files.sort(key=lambda f: (not f.get("known"), not f.get("fresh")))
    for f in files[:12]:
        try:
            frm = config_forms.form(mgr, sid, f["id"])
        except Exception:
            continue
        fields = [x for g in frm.get("groups") or [] for x in g["fields"]
                  if x.get("type") == "text" and not x.get("readonly")]
        for want in NAME_KEYS:
            for x in fields:
                if x["key"].lower().replace(" ", "") == want and (want != "name" or "server" in
                                                                   str(x.get("section") or f["path"]).lower()):
                    return f["id"], x["id"], x["key"], x.get("value"), f["path"]
    return None


def query_name(pid):
    """The server name a player would see (Steam query on the ports it listens on)."""
    from core.network import listening_ports
    from core.a2s import query_info
    for port, protos in sorted((listening_ports(pid) or {}).items()):
        if "UDP" in protos:
            try:
                info = _ask(port)
            except Exception:
                info = None
            if info and info.get("name"):
                return info["name"], port
    return None, None


def query_any(pid):
    """What a player's server browser would get (Steam query on the ports it listens on)."""
    from core.network import listening_ports
    from core.a2s import query_info
    for port, protos in sorted((listening_ports(pid) or {}).items()):
        if "UDP" in protos:
            try:
                info = _ask(port)
            except Exception:
                info = None
            if info and info.get("name"):
                return info
    return None


def _kind(label):
    low = str(label or "").strip().lower()
    if low.startswith("server name"):
        return "name"
    if low in ("password", "password to join", "server password"):
        return "password"
    if low.startswith("max players"):
        return "players"
    return None


def essentials_check(mgr, sid, wait, log):
    """Set the server name, join password and max players through Essentials (what an
    "Add a server" form would fill in), restart, and see what the game kept and what
    players would see. The test password itself is never written to the report."""
    from core import quick_settings
    ess = quick_settings.get(mgr, sid)
    out = {"supported": bool(ess.get("supported")), "auto": bool(ess.get("auto")),
           "fields_found": [f.get("label") for f in ess.get("fields") or []], "fields": {}}
    targets = {}
    for f in ess.get("fields") or []:
        k = _kind(f.get("label"))
        if k and k not in targets and not f.get("error"):
            targets[k] = f
    if not targets:
        out["result"] = "no server name, password or max players in Essentials"
        return out
    stamp = int(time.time()) % 10000
    old_players = str(targets["players"].get("value") or "").strip() if "players" in targets else ""
    want = {"name": "BaoBun Check %d" % stamp, "password": "check%04dpw" % stamp,
            "players": "5" if old_players != "5" else "4"}     # small: some games cap at 6 or 8
    log("  essentials check: " + ", ".join(sorted(targets)))
    try:
        mgr.stop_server(sid, user="smoke-test")
    except Exception:
        pass
    for k, f in targets.items():
        entry = {"label": f.get("label"), "old": None if k == "password" else f.get("value")}
        try:
            quick_settings.save(mgr, sid, {f["key"]: want[k]}, user="smoke-test")
            entry["saved"] = True
        except Exception as e:
            entry.update(saved=False, result="save failed", error=str(e)[:300])
        out["fields"][k] = entry
    mgr.start_server(sid, user="smoke-test")
    out["restart"] = wait_ready(mgr, sid, wait)
    time.sleep(10)
    st = next((s for s in mgr.get_all_statuses() if s["id"] == sid), {}) or {}
    info = query_any(st.get("pid")) if st.get("isRunning") else None
    for _ in range(4):          # Source servers open their port early and only answer once the map is in
        if info or not st.get("isRunning"):
            break
        time.sleep(10)
        st = next((s for s in mgr.get_all_statuses() if s["id"] == sid), {}) or {}
        info = query_any(st.get("pid")) if st.get("isRunning") else None
    out["answers_query"] = bool(info)
    try:
        mgr.stop_server(sid, user="smoke-test")
    except Exception:
        pass
    time.sleep(2)
    try:
        after = {f["key"]: f for f in quick_settings.get(mgr, sid).get("fields") or []}
    except Exception:
        after = {}
    for k, f in targets.items():
        e = out["fields"][k]
        if not e.get("saved"):
            continue
        now = after.get(f["key"])
        if now is None:
            e["result"] = "setting not found afterwards"
            continue
        if k == "password":
            kept = bool(now.get("has_value"))
            seen = info.get("password") if info else None
            ok = seen
        elif k == "name":
            kept = str(now.get("value")) == want[k]
            seen = info.get("name") if info else None
            ok = None if seen is None else want[k] in str(seen)
        else:
            kept = str(now.get("value")).strip() == want[k]
            seen = info.get("max_players") if info else None
            ok = None if seen is None else str(seen) == want[k]
        e["kept"], e["players_see"] = kept, seen
        if ok is None:
            e["result"] = "kept (not checkable)" if kept else "game rewrote it"
        else:
            e["result"] = "works" if ok else ("game ignores it" if kept else "game rewrote it")
            if k == "password" and kept and seen is False and str((mgr.get_server(sid) or {}).get("appId") or "") in PASSWORD_FLAG_UNRELIABLE:
                e["result"] = "kept (not checkable)"     # the game doesn't tell the browser it has a password
    return out


def wait_ready(mgr, sid, wait):
    from core import discord_logic
    started, state = time.time(), "waiting"
    while time.time() - started < wait:
        time.sleep(5)
        state = discord_logic.ready_state(mgr, sid, started)
        if state != "waiting":
            break
    return state


def settings_check(mgr, sid, wait, log):
    """Change the server name, restart, and see whether the file kept it and the
    game shows it. Returns a dict for the report."""
    from core import config_forms
    found = find_name_field(mgr, sid)
    if not found:
        return {"result": "no server-name setting found"}
    file_id, field_id, key, old, path = found
    out = {"file": path, "key": key, "old": old}
    new = "BaoBun Check %d" % (int(time.time()) % 10000)
    log("  settings check: %s in %s -> %r" % (key, path, new))
    try:
        try:
            mgr.stop_server(sid, user="smoke-test")
        except Exception:
            pass          # already stopped by the essentials check
        r = config_forms.save(mgr, sid, file_id, {field_id: new}, user="smoke-test")
        out["saved"] = bool(r.get("saved"))
    except Exception as e:
        out.update(result="save failed", error=str(e)[:400])
        return out
    mgr.start_server(sid, user="smoke-test")
    state = wait_ready(mgr, sid, wait)
    out["restart"] = state
    time.sleep(10)
    st = next((s for s in mgr.get_all_statuses() if s["id"] == sid), {}) or {}
    shown, qport = query_name(st.get("pid"))
    out["players_see"] = shown
    try:
        mgr.stop_server(sid, user="smoke-test")
    except Exception:
        pass
    time.sleep(2)
    try:
        frm = config_forms.form(mgr, sid, file_id)
        now = next((x.get("value") for g in frm["groups"] for x in g["fields"] if x["id"] == field_id), None)
    except Exception:
        now = None
    out["after"] = now
    kept = str(now) == new
    if shown is not None:
        out["result"] = "works" if new in str(shown) else ("game ignores this file" if kept else
                                                           "game rewrote the file")
    else:
        out["result"] = "kept (game name not checkable)" if kept else "game rewrote the file"
    return out

# Games known not to report "needs a password" to the server browser even when one is set.
PASSWORD_FLAG_UNRELIABLE = {"730"}          # Counter-Strike 2


def _ask(port):
    """A2S answer from a server on this PC, on whichever local address it listens on."""
    from core.a2s import query_info, local_hosts
    for host in local_hosts():
        info = query_info(host, port, timeout=1.0)
        if info:
            return info
    return None


def test_game(mgr, api, name, wait, keep, log):
    from core.game_catalog import GAME_PRESETS
    from core.hosting import pick_port
    from core import game_support, discord_logic, config_forms, quick_settings
    p = GAME_PRESETS[name]
    res = {"game": name, "app_id": p.get("appId"), "support": game_support.level(dict(p, name=name)),
           "started_at": time.time()}
    taken = [s.get("port") for s in mgr.load_servers()]
    port = pick_port(p.get("port") or 27015, taken) if p.get("port") else None
    left = next((s for s in mgr.load_servers() if s.get("name") == "Smoke " + name[:40]), None)
    if left:                     # still there from an earlier run with --keep: test that one again
        sid = left["id"]
        res["reused_install"] = True
    else:
        r = api.process("server.add_preset", {"game": name, "name": "Smoke " + name[:40], "port": port})
        if not r.success:
            res.update(result="add failed", error=r.error)
            return res
        sid = r.data.get("server_id") or r.data.get("id") or (r.data.get("server") or {}).get("id")
        if not sid:
            sid = next(s["id"] for s in mgr.load_servers() if s.get("name") == "Smoke " + name[:40])
    srv = mgr.get_server(sid)
    res["directory"] = srv.get("directory")
    if str(p.get("appId")) == "380870" and srv.get("directory"):
        # Project Zomboid keeps worlds and accounts in the Windows user's Zomboid folder,
        # shared with a real server or the game itself - keep the test's inside its own folder
        servers = mgr.load_servers()
        for s in servers:
            if s.get("id") == sid:
                s["launchArgs"] = ('-cachedir="%s" %s' % (os.path.join(srv["directory"], "ZomboidData"),
                                                          s.get("launchArgs") or "")).strip()
        mgr.save_servers(servers)
    try:
        # 1. install
        t0 = time.time()
        log("  installing...")
        try:
            inst = mgr.install_game(sid, {"accept_eula": True})
            res["install"] = "ok"
            res["install_notes"] = inst.get("notes") or []
        except Exception as e:
            res.update(install="failed", result="install failed", error=str(e)[:1500])
            return res
        finally:
            res["install_minutes"] = round((time.time() - t0) / 60, 1)
        try:
            from core.game_defaults import required
            res["launch_defaults"] = required(mgr.get_server(sid))[1]
        except Exception:
            pass
        # 2. start and wait for ready
        log("  starting...")
        started = time.time()
        res["first_start_at"] = started
        try:
            mgr.start_server(sid, user="smoke-test")
        except Exception as e:
            res.update(start="failed", result="didn't start", error=str(e)[:1500], log_tail=tail_log(mgr, sid))
            return res
        state = "waiting"
        while time.time() - started < wait:
            time.sleep(5)
            state = discord_logic.ready_state(mgr, sid, started)
            if state != "waiting":
                break
        st = next((s for s in mgr.get_all_statuses() if s["id"] == sid), {}) or {}
        res["start"] = state
        res["seconds_to_ready"] = round(time.time() - started) if state == "ready" else None
        res["ready_by"] = ("player query" if st.get("playerCount") is not None else "port/time") \
            if state == "ready" else None
        res["still_running"] = bool(st.get("isRunning"))
        try:
            from core.network import listening_ports, expected_ports
            res["listening_ports"] = sorted("%s/%s" % (p, "+".join(sorted(v)))
                                            for p, v in (listening_ports(st.get("pid")) or {}).items())
            res["expected_ports"] = [e["port"] for e in expected_ports(mgr.get_server(sid), mgr)]
            if state == "ready" and st.get("playerCount") is None:
                opened = {int(str(x).split("/")[0]) for x in res["listening_ports"]}
                res["ready_by"] = "port" if opened & set(res["expected_ports"]) else \
                    "time only (none of the ports BaoBun expects is open)"
        except Exception:
            pass
        if state != "ready" or not st.get("isRunning"):
            res["exit_code"] = st.get("exitCode")
            res["crash_help"] = [c.get("title") for c in st.get("crashHelp") or []]
            res["log_tail"] = tail_log(mgr, sid)
        # 3. settings
        try:
            files = config_forms.files(mgr, sid)
            res["settings_known"] = [f["path"] for f in files if f.get("known")]
            res["settings_known_existing"] = [f["path"] for f in files if f.get("known") and f.get("exists")]
            res["settings_found"] = [f["path"] for f in files if not f.get("known")][:25]
            res["essentials"] = bool(quick_settings.game_key(mgr.get_server(sid)))
        except Exception as e:
            res["settings_error"] = str(e)
        if SETTINGS_CHECK and state == "ready" and st.get("isRunning"):
            try:
                res["essentials_check"] = essentials_check(mgr, sid, wait, log)
            except Exception as e:
                res["essentials_check"] = {"result": "check error", "error": str(e)[:400]}
            if "name" not in (res["essentials_check"].get("fields") or {}):
                try:      # Essentials has no server name: look for one in any settings file
                    res["settings_check"] = settings_check(mgr, sid, wait, log)
                except Exception as e:
                    res["settings_check"] = {"result": "check error", "error": str(e)[:400]}
        try:
            res["written_outside"] = files_written_outside(started, res.get("directory"))
        except Exception:
            pass
        res["result"] = "ok" if state == "ready" and st.get("isRunning") else (
            "crashed" if state == "failed" else "not ready in time")
        return res
    finally:
        try:
            if mgr.is_running(sid):
                log("  stopping...")
                mgr.stop_server(sid, user="smoke-test")
        except Exception as e:
            res["stop_error"] = str(e)
        if not keep:
            try:
                api.process("server.remove", {"server_id": sid, "delete_files": True})
            except Exception:
                pass
            d = res.get("directory")
            if d and os.path.isdir(d) and "Smoke" in os.path.basename(d):
                shutil.rmtree(d, ignore_errors=True)
        res["finished_at"] = time.time()


def write_report(path_md, results):
    ok = [r for r in results if r.get("result") == "ok"]
    lines = ["# BaoBun game smoke test", "",
             "%d games tested, %d started and were ready. Generated %s." % (
                 len(results), len(ok), time.strftime("%Y-%m-%d %H:%M")), "",
             "| Game | Support | Install | Start | Ready after | Settings BaoBun knows (exist) | Files found | Essentials |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append("| %s | %s | %s | %s | %s | %s | %d | %s |" % (
            r["game"], r.get("support", ""), r.get("install", "-"), r.get("start", r.get("result", "-")),
            ("%ss" % r["seconds_to_ready"]) if r.get("seconds_to_ready") is not None else "-",
            "%d (%d)" % (len(r.get("settings_known") or []), len(r.get("settings_known_existing") or [])),
            len(r.get("settings_found") or []), "yes" if r.get("essentials") else "no"))
    checks = [r for r in results if r.get("settings_check")]
    if checks:
        lines += ["", "## Settings check (change the server name, restart, look again)", "",
                  "| Game | Result | Setting | Players see |", "|---|---|---|---|"]
        for r in checks:
            c = r["settings_check"]
            lines.append("| %s | %s | %s | %s |" % (r["game"], c.get("result"),
                         ("%s in %s" % (c.get("key"), c.get("file"))) if c.get("key") else "-",
                         c.get("players_see") or "-"))
    ess = [r for r in results if (r.get("essentials_check") or {}).get("fields")]
    if ess:
        lines += ["", "## Essentials check (set name, password and max players, restart, look again)", "",
                  "| Game | Essentials | Server name | Password | Max players | Answers Steam query |",
                  "|---|---|---|---|---|---|"]
        for r in ess:
            c = r["essentials_check"]
            cell = lambda k: (c["fields"].get(k) or {}).get("result") or "not found"
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                r["game"], "found automatically" if c.get("auto") else "made for this game",
                cell("name"), cell("password"), cell("players"), "yes" if c.get("answers_query") else "no"))
    for r in results:
        if r.get("result") == "ok" and r.get("support") == "full" and \
                str((r.get("settings_check") or {}).get("result", "works")).startswith(("works", "kept")) and \
                not r.get("written_outside"):
            continue
        lines += ["", "## %s - %s" % (r["game"], r.get("result"))]
        for k in ("error", "launch_defaults", "install_notes", "crash_help", "settings_found", "settings_known",
                  "listening_ports", "expected_ports", "settings_check", "written_outside"):
            if r.get(k):
                lines.append("- %s: %s" % (k.replace("_", " "), r[k] if not isinstance(r[k], list)
                                          else "; ".join(map(str, r[k]))))
        if r.get("log_tail"):
            lines += ["", "```", r["log_tail"][-3000:], "```"]
    Path(path_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", help="comma-separated game names (default: popular games)")
    ap.add_argument("--with-account", action="store_true", help="also games that need a Steam account")
    ap.add_argument("--all", action="store_true", help="every game in the list BaoBun can download, not just "
                                                        "the popular ones (many hours)")
    ap.add_argument("--dir", help="where to install the test servers")
    ap.add_argument("--wait", type=int, default=240, help="seconds to wait for each server to be ready")
    ap.add_argument("--keep", action="store_true", help="keep the installs afterwards")
    ap.add_argument("--redo", action="store_true", help="test games again that were already tested")
    ap.add_argument("--failed-with", help="with --failed: only games whose log or error mentions this text")
    ap.add_argument("--no-settings-check", action="store_true", help="skip changing the server name and "
                                                                       "restarting to check settings stick")
    ap.add_argument("--failed", action="store_true", help="test again only the games that didn't pass last time "
                                                           "(not ones BaoBun can't download)")
    a = ap.parse_args()
    global SETTINGS_CHECK
    SETTINGS_CHECK = not a.no_settings_check

    home = ROOT / "smoke_test_data"
    home.mkdir(exist_ok=True)
    os.environ["BAOBUN_HOME"] = str(home)      # a separate BaoBun: real servers are never touched
    from core.engine import build_engine
    from core.game_catalog import GAME_PRESETS
    eng = build_engine(role="agent", base_dir=home)
    mgr, api = eng.manager, eng.api
    install_dir = a.dir or default_dir()
    os.makedirs(install_dir, exist_ok=True)
    mgr.set_setting("default_install_dir", install_dir)
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    js, md = logs / "game_smoke_report.json", logs / "game_smoke_report.md"
    results = []
    if js.exists():          # keep earlier results; --redo only re-tests the chosen games
        try:
            results = json.loads(js.read_text(encoding="utf-8"))
        except Exception:
            results = []
    done = {r["game"] for r in results}
    if a.failed:
        games = [r["game"] for r in results if r.get("result") != "ok"
                 and "can't download" not in str(r.get("error") or "")
                 and (not a.failed_with or a.failed_with.lower() in
                      (str(r.get("log_tail")) + str(r.get("error")) + str(r.get("directory")) +
                       str(GAME_PRESETS.get(r["game"], {}).get("executable"))).lower())]
    else:
        games = [g for g in pick_games(GAME_PRESETS, a.games, a.with_account, a.all) if a.redo or g not in done]
    print("Testing %d game(s), installing into %s" % (len(games), install_dir))
    for i, name in enumerate(games, 1):
        if free_gb(install_dir) < MIN_FREE_GB:
            print("Stopping: less than %d GB free in %s" % (MIN_FREE_GB, install_dir))
            break
        print("[%d/%d] %s" % (i, len(games), name))
        try:
            r = test_game(mgr, api, name, a.wait, a.keep, lambda m: print(m, flush=True))
        except Exception as e:
            r = {"game": name, "result": "test error", "error": "%s\n%s" % (e, traceback.format_exc()[-1500:])}
        print("  -> %s" % r.get("result"))
        results = [x for x in results if x["game"] != name] + [r]
        js.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
        write_report(md, results)
    write_report(md, results)
    print("\nReport: %s" % md)


if __name__ == "__main__":
    main()
