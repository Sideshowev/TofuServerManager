"""BaoBun engine tests: real processes (a fake server), no GUI.

Run from the BaoBun folder:   python -m unittest discover -s tests -v
(or double-click selftest.bat). Nothing here touches your real servers: every
test works in its own temporary folder.
"""
import json
import os
import socket
import sqlite3
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FAKE = str(Path(__file__).with_name("fake_server.py"))

# Safety: point every per-user data location at a throwaway folder so no test
# can ever read or write your real game data (e.g. %APPDATA%\VintagestoryData).
_SANDBOX = Path(tempfile.mkdtemp(prefix="baobun_test_home_"))
for _var in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
    os.environ[_var] = str(_SANDBOX)


def make_env(servers):
    """Temp BaoBun data folder with the given server entries (directories created)."""
    tmp = Path(tempfile.mkdtemp(prefix="baobun_test_"))
    out = []
    for s in servers:
        d = tmp / s.pop("_dir", s["id"])
        d.mkdir(parents=True, exist_ok=True)
        args = '"%s" %s' % (FAKE, s.pop("_mode", "normal"))
        if s.get("type") == "vintage-story":
            args += " --dataPath Data"   # keep VS data inside the test folder
        entry = {"type": "custom", "directory": str(d), "executable": sys.executable,
                 "launchArgs": args, "autoRestart": False}
        entry.update(s)
        out.append(entry)
    (tmp / "servers.json").write_text(json.dumps(out, indent=2))
    return tmp


def engine(tmp, role="agent"):
    from core.engine import build_engine
    return build_engine(role=role, base_dir=tmp)


def wait_for(pred, timeout=15.0, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


class LifecycleTests(unittest.TestCase):

    def test_start_console_graceful_stop(self):
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story"}])
        m = engine(tmp).manager
        self.assertEqual(m.start_server("vs")["status"], "started")
        self.assertEqual(m.start_server("vs")["status"], "already_running")
        self.assertEqual(m.send_console_command("vs", "join")["status"], "sent")
        st = {s["id"]: s for s in m.get_all_statuses()}["vs"]
        self.assertTrue(st["isRunning"])
        self.assertTrue(st["consoleAvailable"])
        r = m.stop_server("vs")
        self.assertEqual(r, {"status": "stopped", "graceful": True})
        self.assertTrue(any("got: /stop" in l for l in m.get_logs("vs", 50)))

    def test_forced_stop_kills_whole_tree(self):
        import psutil
        tmp = make_env([{"id": "x", "name": "Stubborn", "_mode": "ignore", "stopTimeout": 2,
                         "stopCommand": "quit"}])
        m = engine(tmp).manager
        m.start_server("x")
        m.send_console_command("x", "spawn")
        pidfile = tmp / "x" / "child.pid"
        self.assertTrue(wait_for(pidfile.exists))
        child = int(pidfile.read_text())
        self.assertTrue(psutil.pid_exists(child))
        r = m.stop_server("x")
        self.assertFalse(r["graceful"])
        self.assertTrue(wait_for(lambda: not psutil.pid_exists(child) or
                                 psutil.Process(child).status() == psutil.STATUS_ZOMBIE, 10))

    def test_crash_restarts_then_gives_up(self):
        tmp = make_env([{"id": "c", "name": "Crashy", "_mode": "crash",
                         "autoRestart": True, "maxRestarts": 1}])
        m = engine(tmp).manager
        events = []
        m.add_listener(lambda e, sid, info: events.append(e))
        m.start_server("c")
        self.assertTrue(wait_for(lambda: "gave_up" in events, 40))
        self.assertEqual(events.count("started"), 2)

    def test_paths_with_spaces(self):
        tmp = make_env([{"id": "sp", "name": "Spaces", "_dir": "folder with spaces"}])
        m = engine(tmp).manager
        self.assertEqual(m.start_server("sp")["status"], "started")
        self.assertTrue(wait_for(lambda: any("fake server up" in l for l in m.get_logs("sp", 20))))
        m.stop_server("sp")

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_bat_launcher_with_spaces(self):
        tmp = make_env([{"id": "bat", "name": "Bat", "_dir": "bat dir"}])
        bat = tmp / "bat dir" / "start server.bat"
        bat.write_text('@echo off\r\n"%s" "%s" normal\r\n' % (sys.executable, FAKE))
        servers = json.loads((tmp / "servers.json").read_text())
        servers[0]["executable"] = str(bat)
        servers[0]["launchArgs"] = ""
        (tmp / "servers.json").write_text(json.dumps(servers))
        m = engine(tmp).manager
        m.start_server("bat")
        self.assertTrue(wait_for(lambda: any("fake server up" in l for l in m.get_logs("bat", 20))))
        self.assertEqual(m.send_console_command("bat", "join")["status"], "sent")
        self.assertTrue(m.stop_server("bat")["status"] == "stopped")


class AgentTests(unittest.TestCase):

    def test_gui_forwards_to_agent_and_no_duplicates(self):
        from core.local_api import LocalApiServer
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story"}])
        agent = engine(tmp, "agent")
        api = LocalApiServer(agent.api, tmp)
        api.start()
        try:
            gui = engine(tmp, "gui").manager
            self.assertTrue(gui.agent_available())
            self.assertEqual(gui.start_server("vs")["status"], "started")
            self.assertEqual(gui.processes, {})          # the agent owns it
            st = {s["id"]: s for s in gui.get_all_statuses()}["vs"]
            self.assertEqual(st["managedBy"], "agent")
            self.assertEqual(gui.send_console_command("vs", "join")["status"], "sent")
            # A second, independent manager must not start a duplicate.
            other = engine(tmp, "agent").manager
            self.assertEqual(other.start_server("vs")["status"], "already_running")
            self.assertEqual(gui.stop_server("vs")["status"], "stopped")
        finally:
            api.stop()


class BackupTests(unittest.TestCase):

    def test_sqlite_snapshot_and_retention(self):
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story"}])
        eng = engine(tmp)
        m = eng.manager
        saves = tmp / "vs" / "Data" / "Saves"
        saves.mkdir(parents=True)
        db = sqlite3.connect(str(saves / "world.vcdbs"))
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("create table chunks(id integer primary key, data blob)")
        db.executemany("insert into chunks(data) values (?)", [(os.urandom(512),) for _ in range(200)])
        db.commit()   # leave the connection open, like a running game
        m.set_setting("backup_keep", 2)
        results = [m.backup_server_world("vs") for _ in range(3)]
        db.close()
        zips = list((tmp / "backups" / "vs").glob("*.zip"))
        self.assertEqual(len([z for z in zips if not z.name.startswith("pre_restore_")]), 2)
        with zipfile.ZipFile(results[-1]["zip_path"]) as z:
            names = z.namelist()
            self.assertIn("Data/Saves/world.vcdbs", names)
            self.assertFalse(any(n.endswith("-wal") for n in names))
            out = tmp / "restored.db"
            out.write_bytes(z.read("Data/Saves/world.vcdbs"))
        con = sqlite3.connect(str(out))
        self.assertEqual(con.execute("select count(*) from chunks").fetchone()[0], 200)
        self.assertEqual(con.execute("pragma integrity_check").fetchone()[0], "ok")
        con.close()

    def test_backup_stays_inside_server_folder(self):
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story"}])
        m = engine(tmp).manager
        for _prefix, full in m._backup_sources(m.get_server("vs")):
            self.assertTrue(str(Path(full).resolve()).startswith(str(tmp.resolve())), full)

    def test_restore_refused_while_running(self):
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story"}])
        m = engine(tmp).manager
        b = m.backup_server_world("vs")
        m.start_server("vs")
        try:
            with self.assertRaises(RuntimeError):
                m.restore_server_world("vs", b["zip_path"])
        finally:
            m.stop_server("vs")
        self.assertGreaterEqual(m.restore_server_world("vs", b["zip_path"])["restored"], 0)


class ProtocolTests(unittest.TestCase):

    def _rcon_server(self, echo_marker, port_holder):
        import rcon
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port_holder.append(s.getsockname()[1])
        c, _ = s.accept()

        def rd():
            n = struct.unpack("<i", c.recv(4))[0]
            d = b""
            while len(d) < n:
                d += c.recv(n - len(d))
            return struct.unpack("<ii", d[:8])
        pid, _t = rd()
        c.sendall(rcon._packet(pid, 2, ""))
        pid, _t = rd()
        c.sendall(rcon._packet(pid, 0, "Players: 3"))
        if echo_marker:
            pid2, _t = rd()
            c.sendall(rcon._packet(pid2, 0, ""))
        time.sleep(3)
        c.close()
        s.close()

    def test_rcon_with_and_without_marker(self):
        import rcon
        for echo in (True, False):
            ph = []
            threading.Thread(target=self._rcon_server, args=(echo, ph), daemon=True).start()
            self.assertTrue(wait_for(lambda: ph, 5, 0.05))
            t0 = time.time()
            r = rcon.rcon_command("127.0.0.1", ph[0], "pw", "listplayers", use_marker=echo)
            self.assertEqual(r, "Players: 3")
            self.assertLess(time.time() - t0, 3)

    def test_a2s_with_challenge(self):
        from core.a2s import query_info
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

        def serve():
            data, addr = sock.recvfrom(1024)
            sock.sendto(b"\xFF\xFF\xFF\xFFA" + b"\x01\x02\x03\x04", addr)
            data, addr = sock.recvfrom(1024)
            assert data.endswith(b"\x01\x02\x03\x04")
            body = (b"\xFF\xFF\xFF\xFFI\x11" + b"My Server\x00" + b"map\x00" + b"folder\x00"
                    + b"game\x00" + struct.pack("<H", 0) + bytes([3, 16, 0]) + b"dw\x01")
            sock.sendto(body, addr)
        threading.Thread(target=serve, daemon=True).start()
        info = query_info("127.0.0.1", port, timeout=2)
        sock.close()
        self.assertEqual((info["players"], info["max_players"], info["name"]), (3, 16, "My Server"))
        self.assertIs(info["password"], True)           # dedicated, Windows, needs a password


class LogicTests(unittest.TestCase):

    def test_schedule_due(self):
        from baobun_agent import BaoBunAgent
        due = BaoBunAgent._schedule_due
        now = time.time()
        self.assertTrue(due({"mode": "interval", "hours": 1}, now - 3700, now))
        self.assertFalse(due({"mode": "interval", "hours": 1}, now - 100, now))
        hm = time.strftime("%H:%M", time.localtime(now - 60))
        self.assertTrue(due({"mode": "daily", "times": [hm]}, now - 86400, now))
        self.assertFalse(due({"mode": "daily", "times": [hm]}, now - 10, now))

    def test_smart_boot(self):
        from smart_boot import SmartBootManager
        tmp = make_env([{"id": "a", "name": "A"}])
        sb = SmartBootManager(str(tmp / "servers.json"), activity_tracker=None)
        self.assertEqual(sb.decide({"id": "a", "clusterId": "C"}, True)[0], False)
        self.assertEqual(sb.decide({"id": "a", "manualStart": True}, True)[0], False)
        self.assertEqual(sb.decide({"id": "a"}, was_running=True)[0], True)
        self.assertEqual(sb.decide({"id": "a"}, was_running=False)[0], False)

    def test_secrets_roundtrip_and_settings(self):
        from core.secrets import protect, unprotect, discord_token
        enc = protect("hunter2")
        self.assertNotIn("hunter2", enc)
        self.assertEqual(unprotect(enc), "hunter2")
        self.assertEqual(discord_token({"token": "", "token_protected": enc}), "hunter2")
        tmp = make_env([])
        m = engine(tmp).manager
        m.set_setting("steam_password", "s3cret")
        raw = (tmp / "settings.json").read_text()
        self.assertNotIn("s3cret", raw)
        self.assertEqual(m.get_setting("steam_password"), "s3cret")

    def test_install_group_and_update_check(self):
        tmp = make_env([{"id": "m1", "name": "Map 1", "_dir": "ark", "appId": "2430930"},
                        {"id": "m2", "name": "Map 2", "_dir": "ark", "appId": "2430930"},
                        {"id": "o", "name": "Other"}])
        m = engine(tmp).manager
        self.assertEqual(sorted(m.install_group("m1")), ["m1", "m2"])
        apps = tmp / "ark" / "steamapps"
        apps.mkdir()
        (apps / "appmanifest_2430930.acf").write_text('"AppState"\n{\n\t"buildid"\t\t"100"\n}\n')
        m.latest_buildid = lambda app_id, branch="public": "100"
        self.assertFalse(m.check_steam_update("m1")["update_available"])
        self.assertEqual(m.update_server("m1")["status"], "up_to_date")
        m.latest_buildid = lambda app_id, branch="public": "101"
        self.assertTrue(m.check_steam_update("m1")["update_available"])

    def test_vs_release_picking(self):
        from server_manager import ServerManager
        rels = [{"modversion": "2.0", "tags": ["1.21.0"]},
                {"modversion": "1.5", "tags": ["1.20.3", "1.20.4"]},
                {"modversion": "1.0", "tags": ["1.19.8"]}]
        pick = ServerManager._vs_pick_release
        self.assertEqual(pick(rels, "1.20.4")[0]["modversion"], "1.5")
        self.assertEqual(pick(rels, "1.20.9")[0]["modversion"], "1.5")   # same minor
        self.assertEqual(pick(rels, "1.18.0")[1], "incompatible")
        self.assertEqual(pick(rels, None)[0]["modversion"], "2.0")


class HostingTests(unittest.TestCase):

    def test_core_lists(self):
        from core.hosting import parse_core_list, format_core_list, suggest_core_block
        self.assertEqual(parse_core_list("0-3, 8, 10-11"), [0, 1, 2, 3, 8, 10, 11])
        self.assertEqual(format_core_list([0, 1, 2, 3, 8, 10, 11]), "0-3, 8, 10-11")
        self.assertEqual(parse_core_list(""), [])
        with self.assertRaises(ValueError):
            parse_core_list("5-2")
        # 64 logical CPUs (32-core Threadripper): blocks of 16, least-used first
        self.assertEqual(suggest_core_block([], total=64), list(range(0, 16)))
        self.assertEqual(suggest_core_block([range(0, 16)], total=64), list(range(16, 32)))
        self.assertEqual(suggest_core_block([range(0, 16), range(16, 32)], total=64)[0], 32)

    def test_cpu_pinning_applies_to_process(self):
        import psutil
        from core.hosting import apply_cpu_settings
        if not hasattr(psutil.Process(), "cpu_affinity"):
            self.skipTest("cpu_affinity not supported here")
        tmp = make_env([{"id": "p", "name": "Pinned", "cpuAffinity": "0"}])
        m = engine(tmp).manager
        m.start_server("p")
        pid = m.processes["p"].pid
        self.assertTrue(wait_for(lambda: psutil.Process(pid).cpu_affinity() == [0], 10))
        m.stop_server("p")
        self.assertTrue(apply_cpu_settings(os.getpid(), psutil.Process().cpu_affinity()))

    def test_pick_port_and_firewall_commands(self):
        from core.hosting import pick_port, firewall_commands
        self.assertEqual(pick_port(2456, [], check_os=False), 2456)
        self.assertEqual(pick_port(2456, [2456], check_os=False), 2458)
        self.assertEqual(pick_port(2456, [2457], check_os=False), 2458)   # keeps port+1 free
        cmds = firewall_commands('My "Server"', r"C:\games\srv.exe", [2456, 2457])
        self.assertTrue(any('program="C:\\games\\srv.exe"' in c for c in cmds))
        self.assertTrue(any("protocol=UDP localport=2456,2457" in c for c in cmds))
        self.assertTrue(all('"BaoBun - My Server' in c for c in cmds))

    def test_steamcmd_progress(self):
        from core.hosting import parse_steamcmd_progress
        txt = (" Update state (0x61) downloading, progress: 12.50 (1 / 8)\n"
               " Update state (0x81) verifying update, progress: 97.03 (7 / 8)\n")
        self.assertEqual(parse_steamcmd_progress(txt), (97.03, "verifying update"))
        self.assertEqual(parse_steamcmd_progress("Success! App '896660' fully installed."), (100.0, "done"))
        self.assertEqual(parse_steamcmd_progress("nothing yet"), (None, None))

    def test_idle_tracker(self):
        from core.hosting import IdleTracker
        t = IdleTracker()
        self.assertFalse(t.update("s", 0, 30, now=0))
        self.assertFalse(t.update("s", 0, 30, now=29 * 60))
        self.assertTrue(t.update("s", 0, 30, now=30 * 60))
        self.assertFalse(t.update("s", 2, 30, now=31 * 60))      # someone joined: reset
        self.assertFalse(t.update("s", 0, 30, now=40 * 60))
        self.assertFalse(t.update("v", None, 30, now=0))          # unknown count never stops
        self.assertFalse(t.update("v", None, 30, now=10 ** 6))

    def test_agent_idle_stop(self):
        from baobun_agent import BaoBunAgent
        from core.hosting import IdleTracker
        tmp = make_env([{"id": "i", "name": "Idle", "idleStopMinutes": 1}])
        agent = BaoBunAgent.__new__(BaoBunAgent)       # no API server / threads
        agent.mgr = engine(tmp).manager
        agent.mgr.start_server("i")
        agent.mgr.player_count = lambda sid: 0
        idle = IdleTracker()
        agent._check_idle(idle)                          # starts the empty timer
        idle._empty_since["i"] -= 61                     # pretend a minute passed
        agent._check_idle(idle)
        self.assertTrue(wait_for(lambda: not agent.mgr.is_running("i"), 15))

    def test_dashboard_next_task(self):
        from core.hosting import dashboard_summary
        tmp = make_env([{"id": "a", "name": "A"}])
        m = engine(tmp).manager
        hm = time.strftime("%H:%M", time.localtime(time.time() + 3600))
        (tmp / "agent_state.json").write_text(json.dumps(
            {"schedules": {"a": {"restart": {"enabled": True, "mode": "daily", "times": [hm]}}}}))
        m.backup_server_world("a")
        summ = dashboard_summary(m, m.get_all_statuses(), tmp)
        self.assertEqual(summ["next_task"]["kind"], "restart")
        self.assertIn("a", summ["last_backups"])
        self.assertEqual((summ["servers"], summ["online"]), (1, 0))

    def test_preset_executables_are_windows_names(self):
        from core.game_catalog import GAME_PRESETS, windows_exe
        self.assertEqual(windows_exe("valheim_server.x86_64"), "valheim_server.exe")
        self.assertEqual(windows_exe("bin/x64/factorio"), "bin/x64/factorio.exe")
        bad = [n for n, p in GAME_PRESETS.items() if p.get("executable")
               and not p["executable"].lower().endswith((".exe", ".bat", ".cmd", ".jar"))]
        self.assertEqual(bad, [])


class WebPanelTests(unittest.TestCase):
    """Remote access: roles, network filter, and what a browser may change."""

    def test_roles_and_permissions(self):
        from core.web_auth import DiscordAuth
        cfg = {"web_admin_ids": ["111"], "web_admin_role_ids": ["900"], "web_operator_role_ids": ["800", "bad"]}
        self.assertEqual(DiscordAuth.role_for("111", set(), cfg), "admin")
        self.assertEqual(DiscordAuth.role_for("5", {"900"}, cfg), "admin")
        self.assertEqual(DiscordAuth.role_for("5", {"800"}, cfg), "operator")
        self.assertIsNone(DiscordAuth.role_for("5", {"700"}, cfg))
        op, ad = {"role": "operator"}, {"role": "admin"}
        self.assertTrue(DiscordAuth.allowed(op, "server.start"))
        self.assertTrue(DiscordAuth.allowed(op, "console.send"))
        self.assertFalse(DiscordAuth.allowed(op, "config.write"))
        self.assertTrue(DiscordAuth.allowed(ad, "server.add_preset"))
        for never in ("settings.set", "server.add", "agent.shutdown", "agent.web_reload", "presets.save"):
            self.assertFalse(DiscordAuth.allowed(ad, never), never)

    def test_only_local_and_tailscale_clients(self):
        from core.web_panel import client_allowed
        for ok in ("127.0.0.1", "::1", "100.101.102.103", "::ffff:100.64.0.1", "fd7a:115c:a1e0::5"):
            self.assertTrue(client_allowed(ok), ok)
        for bad in ("192.168.1.20", "10.0.0.5", "8.8.8.8", "100.128.0.1", "fe80::1", "junk"):
            self.assertFalse(client_allowed(bad), bad)
        # "allow my home network": private ranges only, never the internet
        for ok in ("192.168.1.20", "10.0.0.5", "172.20.1.1", "::ffff:192.168.0.9", "127.0.0.1"):
            self.assertTrue(client_allowed(ok, allow_lan=True), ok)
        for bad in ("8.8.8.8", "172.32.0.1", "100.128.0.1", "2001:4860::1", "junk"):
            self.assertFalse(client_allowed(bad, allow_lan=True), bad)

    def test_safe_server_path(self):
        tmp = make_env([{"id": "a", "name": "A"}])
        m = engine(tmp).manager
        d = Path(m.get_server("a")["directory"])
        (d / "cfg.ini").write_text("x=1")
        (tmp / "secret.txt").write_text("no")
        self.assertEqual(m.load_config("a", "cfg.ini")["raw_text"], "x=1")
        for bad in ("../secret.txt", str(tmp / "secret.txt"), "missing.ini"):
            with self.assertRaises(Exception):
                m.load_config("a", bad)

    def test_remote_safe_api(self):
        tmp = make_env([{"id": "a", "name": "A"}])
        e = engine(tmp)
        d = Path(e.manager.get_server("a")["directory"])
        (d / "serverconfig.json").write_text('{"x": 1}')
        (d / "start.bat").write_text("echo hi")
        api = e.api
        for key in ("executable", "directory"):
            r = api.process("server.update_settings", {"server_id": "a", "changes": {key: "C:/evil.exe"}})
            self.assertFalse(r.success, key)
        r = api.process("server.update_settings", {"server_id": "a", "changes": {
            "name": "Renamed", "port": 1234, "queryPort": ""}})
        self.assertTrue(r.success, r.error)
        srv = e.manager.get_server("a")
        self.assertEqual((srv["name"], srv["port"]), ("Renamed", 1234))
        self.assertEqual(srv["executable"], sys.executable)
        self.assertEqual(srv["directory"], str(d))
        self.assertFalse(api.process("config.read", {"server_id": "a", "file": "start.bat"}).success)
        self.assertFalse(api.process("config.read", {"server_id": "a", "file": "../servers.json"}).success)
        files = {c["relative"] for c in e.manager.list_configs("a")}
        if "serverconfig.json" in files:
            self.assertTrue(api.process("config.write", {"server_id": "a", "file": "serverconfig.json",
                                                         "payload": {"raw_text": '{"x": 2}'}}).success)
            self.assertIn('"x": 2', (d / "serverconfig.json").read_text())
        outside = tmp / "elsewhere.zip"
        with zipfile.ZipFile(outside, "w") as z:
            z.writestr("x.txt", "x")
        r = api.process("backup.restore", {"server_id": "a", "backup_path": str(outside)})
        self.assertFalse(r.success)
        bad = api.process("schedules.set", {"server_id": "a", "kind": "restart",
                                            "config": {"enabled": True, "mode": "daily", "times": ["25:99"]}})
        self.assertFalse(bad.success)
        good = api.process("schedules.set", {"server_id": "a", "kind": "restart",
                                             "config": {"enabled": True, "mode": "daily", "times": ["04:00"]}})
        self.assertTrue(good.success, good.error)
        got = api.process("schedules.get", {"server_id": "a"}).data["schedules"]
        self.assertEqual(got["restart"]["times"], ["04:00"])

    def test_http_panel_end_to_end(self):
        import urllib.request
        import urllib.error
        tmp = make_env([{"id": "a", "name": "A"}])
        e = engine(tmp)
        e.manager.set_setting("web_panel_enabled", True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        e.manager.set_setting("web_panel_port", port)
        (tmp / "discord_config.json").write_text(json.dumps({"panel_url": "http://127.0.0.1:%d" % port}))
        from core.web_panel import WebPanel
        wp = WebPanel(e, ROOT, log=lambda *a: None)   # ROOT has ui/web/index.html
        wp.auth.base_dir = tmp
        self.assertIsNotNone(wp.start())
        try:
            base = "http://127.0.0.1:%d" % port
            page = urllib.request.urlopen(base + "/").read().decode()
            self.assertIn("Log in with Discord", page)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/api/status")
            self.assertEqual(cm.exception.code, 401)
            op = wp.auth._new_session("5", "Helper", "operator", None)

            def call(path, body=None, csrf=op["csrf"], tok=op["token"]):
                req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body else None,
                                             headers={"Cookie": "bb_session=" + tok, "X-CSRF": csrf,
                                                      "Content-Type": "application/json"})
                return json.loads(urllib.request.urlopen(req, timeout=15).read())

            self.assertEqual(call("/api/me")["role"], "operator")
            st = call("/api/status")
            self.assertEqual(st["statuses"][0]["id"], "a")
            self.assertNotIn("executable", st["servers"]["a"])
            r = call("/api/action", {"action": "config.write", "params": {"server_id": "a", "file": "x"}})
            self.assertFalse(r["success"])
            self.assertIn("role", r["error"])
            with self.assertRaises(urllib.error.HTTPError) as cm:
                call("/api/action", {"action": "server.start", "params": {"server_id": "a"}}, csrf="wrong")
            self.assertEqual(cm.exception.code, 403)
            r = call("/api/action", {"action": "server.start", "params": {"server_id": "a"}})
            self.assertTrue(r["success"], r)
            self.assertTrue(wait_for(lambda: e.manager.is_running("a")))
            self.assertTrue(wait_for(lambda: call("/api/logs?server_id=a&offset=-1")["lines"], 10))
            acts = call("/api/action", {"action": "activity.list", "params": {}})["data"]["activity"]
            self.assertTrue(any(a["action"] == "server_start" and a["user_id"] == "web:Helper" for a in acts))
            ad = wp.auth._new_session("1", "Boss", "admin", None)
            r = call("/api/action", {"action": "backup.create", "params": {"server_id": "a",
                     "dest_dir": str(tmp / "stolen")}}, csrf=ad["csrf"], tok=ad["token"])
            self.assertTrue(r["success"], r)
            self.assertFalse((tmp / "stolen").exists())
            call("/api/action", {"action": "server.stop", "params": {"server_id": "a"}})
            name = e.manager.list_server_backups("a")[0]["name"]
            req = urllib.request.Request(base + "/api/backup_download?server_id=a&name=" + name,
                                         headers={"Cookie": "bb_session=" + ad["token"]})
            with urllib.request.urlopen(req, timeout=15) as resp:
                self.assertEqual(resp.headers["Content-Type"], "application/zip")
                self.assertTrue(zipfile.is_zipfile(__import__("io").BytesIO(resp.read())))
            for bad_tok, bad_name in ((op["token"], name), (ad["token"], "../servers.json")):
                req = urllib.request.Request(base + "/api/backup_download?server_id=a&name=" + bad_name,
                                             headers={"Cookie": "bb_session=" + bad_tok})
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(req, timeout=15)
        finally:
            wp.stop()
            e.manager.stop_all()

    def test_oauth_secret_is_encrypted_at_rest(self):
        from core.secrets import migrate_discord_config
        from core.web_auth import DiscordAuth
        tmp = Path(tempfile.mkdtemp(prefix="baobun_test_"))
        (tmp / "discord_config.json").write_text(json.dumps({"oauth_client_secret": "s3cret",
                                                             "token": ""}))
        self.assertTrue(migrate_discord_config(tmp / "discord_config.json"))
        raw = (tmp / "discord_config.json").read_text()
        self.assertNotIn("s3cret", raw)
        auth = DiscordAuth(tmp)
        self.assertEqual(auth.client_secret(auth.config()), "s3cret")


class CrashHelpAndAlertTests(unittest.TestCase):

    def test_explain_patterns_and_exit_codes(self):
        from core.diagnose import explain
        f = explain(1, ["loading", "Failed to bind port 27015: address already in use"])
        self.assertEqual(f[0]["key"], "port_taken")
        self.assertIn("27015", f[0]["title"])
        f = explain(-1073741515, [])            # 0xC0000135 as a signed int
        self.assertEqual(f[0]["key"], "missing_dll")
        self.assertTrue(f[0]["link"].startswith("https://"))
        f = explain(0xE0434352, ["You must install or update .NET to run this application."])
        self.assertEqual([x["key"] for x in f[:2]], ["dotnet_missing", "dotnet_crash"])
        f = explain(1, ["Unhandled exception. System.OutOfMemoryException"])
        self.assertEqual(f[0]["key"], "out_of_memory")
        self.assertEqual(explain(0, ["all fine"], uptime=3)[0]["key"], "quick_exit")
        self.assertEqual(explain(7, ["all fine"], uptime=600)[0]["key"], "unknown")
        self.assertEqual(explain(0, ["all fine"], uptime=600), [])

    def test_crash_reason_reaches_status_and_alert(self):
        from core.alerts import AlertCenter
        tmp = make_env([{"id": "p", "name": "Porty", "_mode": "portcrash"}])
        m = engine(tmp).manager
        alerts = AlertCenter(m, log=lambda *a: None)
        delivered = []
        alerts._deliver = lambda title, text, color: delivered.append((title, text)) or True
        m.add_listener(alerts.on_event)
        m.start_server("p")
        self.assertTrue(wait_for(lambda: m.get_all_statuses()[0]["state"] == "crashed", 15))
        st = m.get_all_statuses()[0]
        self.assertEqual(st["crashHelp"][0]["key"], "port_taken")
        self.assertIn("42420", st["crashHelp"][0]["title"])
        self.assertTrue(wait_for(lambda: delivered, 5))
        self.assertIn("Porty crashed", delivered[0][0])
        self.assertIn("42420", delivered[0][1])
        # a second crash right away is not sent again (cooldown)
        self.assertFalse(alerts.notify("crash", "p", "again"))
        m.start_server("p")
        self.assertTrue(wait_for(lambda: m.get_all_statuses()[0]["state"] == "crashed", 15))
        self.assertEqual(len(delivered), 1)

    def test_alert_settings_and_ram(self):
        from core.alerts import AlertCenter, valid_webhook
        tmp = make_env([{"id": "a", "name": "A", "ramAlertMB": 1}])
        m = engine(tmp).manager
        a = AlertCenter(m, log=lambda *x: None)
        got = []
        a._deliver = lambda t, x, c: got.append(t) or True
        m.set_setting("alert_events", ["crash"])
        self.assertFalse(a.enabled("ram"))
        a.check_resources([{"id": "a", "name": "A", "isRunning": True, "memUsageMB": 50}])
        time.sleep(0.2)
        self.assertEqual(got, [])
        m.set_setting("alert_events", ["ram"])
        a.check_resources([{"id": "a", "name": "A", "isRunning": True, "memUsageMB": 50}])
        self.assertTrue(wait_for(lambda: any("A is using" in t for t in got), 3))
        m.set_setting("alerts_enabled", False)
        self.assertFalse(a.notify("ram", "zz", "x"))
        self.assertTrue(valid_webhook("https://discord.com/api/webhooks/1/abc"))
        self.assertFalse(valid_webhook("https://evil.example/api/webhooks/1/abc"))
        m.set_setting("alert_webhook_url", "https://discord.com/api/webhooks/1/abc")
        self.assertNotIn("webhooks", (tmp / "settings.json").read_text())
        self.assertEqual(m.get_setting("alert_webhook_url"), "https://discord.com/api/webhooks/1/abc")


class ModTests(unittest.TestCase):
    """Every mod source: Vintage Story, server-downloaded lists, Workshop installs, sites, folders."""

    @staticmethod
    def _zip(path, modid, version, deps=None, key="modid"):
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("modinfo.json", '{"type": "code", "%s": "%s", "name": "%s", "version": "%s", '
                       '"dependencies": %s, }' % (key, modid, modid.title(), version, json.dumps(deps or {})))

    @staticmethod
    def _zip_bytes(files):
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n, data in files.items():
                z.writestr(n, data)
        return buf.getvalue()

    def _mm(self, servers):
        from core.mods import ModManager
        tmp = make_env(servers)
        m = engine(tmp).manager
        mm = ModManager(m)
        mm.steam_details = lambda ids: {}
        return tmp, m, mm

    # -- sources --------------------------------------------------------------------
    def test_profiles(self):
        from core.mod_sources import profile_for
        self.assertEqual(profile_for({"type": "vintage-story"})["source"], "vs_moddb")
        self.assertEqual(profile_for({"type": "custom", "appId": "2430930", "clusterId": "c"})["writer"], "asa_launch")
        self.assertEqual(profile_for({"appId": "108600"})["writer"], "pz_ini")
        self.assertEqual(profile_for({"appId": "233780"})["installer"], "bohemia")
        self.assertEqual(profile_for({"appId": "896660"})["source"], "thunderstore")
        self.assertEqual(profile_for({"executable": "C:/mc/server.jar"})["source"], "modrinth")
        self.assertEqual(profile_for({"appId": "2278520"})["source"], "none")
        self.assertEqual(profile_for({"appId": "999999"})["source"], "none")
        self.assertEqual(profile_for({"appId": "999999", "modSource": "folder", "modFolder": "x"})["folder"], "x")
        self.assertEqual(profile_for({"appId": "4020", "modSource": "workshop"})["installer"], "plain")

    def test_common_helpers(self):
        from core.mods_common import set_arg, get_arg, ini_set, ini_get, extract_zip
        a = set_arg("Map?listen -Port=7777 -mods=1,2", "mods", "3,4")
        self.assertEqual(get_arg(a, "mods"), "3,4")
        self.assertNotIn("1,2", a)
        self.assertNotIn("-mods", set_arg(a, "mods", None))
        b = set_arg('-config "my cfg.json"', "config", "x.json")
        self.assertEqual(get_arg(b, "config"), "x.json")
        tmp = Path(tempfile.mkdtemp())
        ini = tmp / "a.ini"
        ini.write_text("[A]\nx=1\n\n[B]\ny=2\n")
        ini_set(str(ini), "A", "z", ["5", "6"])
        ini_set(str(ini), "C", "w", "7")
        ini_set(str(ini), "B", "y", "3")
        self.assertEqual(ini_get(str(ini), "A", "z"), ["5", "6"])
        self.assertEqual(ini_get(str(ini), "B", "y"), ["3"])
        self.assertEqual(ini_get(str(ini), "C", "w"), ["7"])
        self.assertEqual(ini_get(str(ini), "A", "x"), ["1"])
        evil = self._zip_bytes({"../../evil.txt": "x", "ok/a.txt": "y"})
        extract_zip(evil, str(tmp / "out"))
        self.assertFalse((tmp / "evil.txt").exists())
        self.assertTrue((tmp / "out" / "ok" / "a.txt").exists())

    def test_vs_mods_toggle_update_remove(self):
        from core.mods import ModError
        tmp, m, mm = self._mm([{"id": "vs", "name": "VS", "type": "vintage-story"}])
        d = Path(m.get_server("vs")["directory"])
        (d / "Mods").mkdir()
        (d / "Mods" / "VSSurvivalMod.dll").write_text("base game")
        mods_dir = d / "Data" / "Mods"
        mods_dir.mkdir(parents=True)
        self._zip(mods_dir / "moda_1.0.0.zip", "ModA", "1.0.0", {"game": "", "modb": "1.0"}, key="modID")
        (mods_dir / "folder").mkdir()
        (mods_dir / "folder" / "modinfo.json").write_text('{"modid": "foldermod", "name": "Folder", "version": "2"}')
        lst = mm.list("vs")
        self.assertEqual(lst["source"], "vs_moddb")
        self.assertTrue(lst["caps"]["search"])
        self.assertEqual(sorted(x["id"] for x in lst["mods"]), ["foldermod", "moda"])
        self.assertEqual(next(x for x in lst["mods"] if x["id"] == "moda")["missing_deps"], ["modb"])
        mm.set_enabled("vs", "moda_1.0.0.zip", False)
        self.assertTrue((d / "Data" / "Mods-disabled" / "moda_1.0.0.zip").is_file())
        mm.set_enabled("vs", "moda_1.0.0.zip", True)
        with self.assertRaises(ModError):
            mm.set_enabled("vs", "../../servers.json", False)
        new = tmp / "moda_1.1.0.zip"
        self._zip(new, "moda", "1.1.0")
        m.vs_server_version = lambda sid: "1.20.3"
        m.vs_get_mod = lambda mid: {"modid": 7, "releases": [
            {"modversion": "1.1.0", "tags": ["1.20.0"], "mainfile": new.as_uri(),
             "filename": "moda_1.1.0.zip", "modidstr": "moda"}]}
        self.assertTrue(next(x for x in mm.list("vs", True)["mods"] if x["id"] == "moda")["update_available"])
        r = mm.update("vs")
        self.assertEqual(r["updated"][0]["to"], "1.1.0")
        self.assertIn("backed up", r.get("backup", ""))
        self.assertEqual([x["version"] for x in mm.list("vs")["mods"] if x["id"] == "moda"], ["1.1.0"])
        self.assertTrue((d / "Data" / "Mods-old-versions" / "moda_1.0.0.zip").is_file())
        r = mm.remove("vs", "folder")
        self.assertTrue(Path(r["kept_in"]).exists())
        self.assertTrue((d / "Mods" / "VSSurvivalMod.dll").exists())

    # -- servers that download mods themselves -------------------------------------------
    def test_asa_single_and_cluster(self):
        from core.mods import ModError
        tmp, m, mm = self._mm([
            {"id": "asa", "name": "ASA", "appId": "2430930", "launchArgs": "TheIsland_WP?listen -Port=7777 -mods=111111"},
            {"id": "c1", "name": "ARK - The Island", "appId": "2430930", "clusterId": "Fam", "arkMap": "TheIsland_WP",
             "arkFriendly": "The Island", "arkIndex": 0, "launchArgs": ""},
            {"id": "c2", "name": "ARK - Scorched", "appId": "2430930", "clusterId": "Fam", "arkMap": "ScorchedEarth_WP",
             "arkFriendly": "Scorched Earth", "arkIndex": 1, "launchArgs": ""}])
        lst = mm.list("asa")
        self.assertEqual((lst["label"], [x["id"] for x in lst["mods"]]), ("CurseForge", ["111111"]))
        mm.install("asa", "https://www.curseforge.com/ark-survival-ascended/mods/x  Project ID 928793")
        self.assertIn("-mods=111111,928793", m.get_server("asa")["launchArgs"])
        mm.move("asa", "928793", -1)
        self.assertIn("-mods=928793,111111", m.get_server("asa")["launchArgs"])
        mm.set_enabled("asa", "111111", False)
        self.assertIn("-mods=928793", m.get_server("asa")["launchArgs"])
        self.assertNotIn("111111", m.get_server("asa")["launchArgs"])
        self.assertFalse(next(x for x in mm.list("asa")["mods"] if x["id"] == "111111")["enabled"])
        with self.assertRaises(ModError):
            mm.install("asa", "not a number")
        m._save_cluster_settings("Fam", {"serverPassword": "pw", "mods": ""})
        mm.install("c1", "900062")
        for sid in ("c1", "c2"):
            self.assertIn("-mods=900062", m.get_server(sid)["launchArgs"])
            self.assertIn("ServerPassword=pw", m.get_server(sid)["launchArgs"])
        self.assertEqual([x["id"] for x in mm.list("c2")["mods"]], ["900062"])
        self.assertEqual(m.get_cluster_settings("Fam")["modItems"][0]["id"], "900062")

    def test_project_zomboid_ini(self):
        tmp, m, mm = self._mm([{"id": "pz", "name": "PZ", "appId": "380870", "launchArgs": "-servername fam"}])
        ini = Path(os.environ["USERPROFILE"]) / "Zomboid" / "Server" / "fam.ini"
        ini.parent.mkdir(parents=True, exist_ok=True)
        ini.write_text("PVP=true\nMods=\nWorkshopItems=\nPublic=false\n")
        mm.install("pz", "2392709985:tsarslib")
        r = mm.install("pz", "https://steamcommunity.com/sharedfiles/filedetails/?id=2169435993")
        self.assertIn("No Mod ID yet", r["note"])
        text = ini.read_text()
        self.assertIn("WorkshopItems=2392709985;2169435993", text)
        self.assertIn("Mods=tsarslib", text)
        self.assertIn("PVP=true", text)
        d = Path(m.get_server("pz")["directory"])
        info = d / "steamapps" / "workshop" / "content" / "108600" / "2169435993" / "mods" / "ModOptions" / "mod.info"
        info.parent.mkdir(parents=True)
        info.write_text("name=Mod Options\nid=modoptions\n")
        lst = mm.list("pz")
        self.assertEqual(lst["mods"][1]["mod_ids"], ["modoptions"])
        mm.set_field("pz", "2392709985", "mod_ids", "tsarslib, tsarscommon")
        self.assertIn("Mods=tsarslib;tsarscommon;modoptions", ini.read_text())

    def test_reforger_kf2_dst_se_unturned_gmod_arkse(self):
        tmp, m, mm = self._mm([
            {"id": "rf", "name": "RF", "appId": "1874900", "launchArgs": '-config "cfg/main.json" -maxFPS 60'},
            {"id": "kf", "name": "KF", "appId": "232130"},
            {"id": "dst", "name": "DST", "appId": "343050"},
            {"id": "se", "name": "SE", "appId": "298740"},
            {"id": "un", "name": "UN", "appId": "1110390", "launchArgs": "-batchmode +SecureServer/Fam"},
            {"id": "gm", "name": "GM", "appId": "4020", "launchArgs": "-console +map gm_construct"},
            {"id": "se1", "name": "ARKSE", "appId": "376030", "launchArgs": "TheIsland?listen"}])
        dirs = {s["id"]: Path(s["directory"]) for s in m.load_servers()}
        # Arma Reforger
        cfg = dirs["rf"] / "cfg" / "main.json"
        cfg.parent.mkdir()
        cfg.write_text(json.dumps({"bindPort": 2001, "game": {"name": "x", "mods": [
            {"modId": "591AF5BDA9F7CE8B", "name": "Old", "version": "1.0.2"}]}}))
        self.assertEqual(mm.list("rf")["mods"][0]["name"], "Old")
        mm.install("rf", "https://reforger.armaplatform.com/workshop/5AAAC70D754245DD-ServerAdminTools")
        mods = json.loads(cfg.read_text())["game"]["mods"]
        self.assertEqual([x["modId"] for x in mods], ["591AF5BDA9F7CE8B", "5AAAC70D754245DD"])
        self.assertEqual(mods[0]["version"], "1.0.2")            # pinned version kept
        self.assertEqual(json.loads(cfg.read_text())["bindPort"], 2001)
        mm.set_field("rf", "5AAAC70D754245DD", "name", "Server Admin Tools")
        self.assertEqual(json.loads(cfg.read_text())["game"]["mods"][1]["name"], "Server Admin Tools")
        # Killing Floor 2
        kini = dirs["kf"] / "KFGame" / "Config" / "PCServer-KFEngine.ini"
        kini.parent.mkdir(parents=True)
        kini.write_text("[IpDrv.TcpNetDriver]\nDownloadManagers=IpDrv.HTTPDownload\nMaxClientRate=15000\n")
        mm.install("kf", "605633924")
        mm.install("kf", "605551918")
        from core.mods_common import ini_get
        self.assertEqual(ini_get(str(kini), "OnlineSubsystemSteamworks.KFWorkshopSteamworks",
                                 "ServerSubscribedWorkshopItems"), ["605633924", "605551918"])
        self.assertEqual(ini_get(str(kini), "IpDrv.TcpNetDriver", "DownloadManagers"),
                         ["OnlineSubsystemSteamworks.SteamWorkshopDownload", "IpDrv.HTTPDownload"])
        # Don't Starve Together
        (dirs["dst"] / "mods").mkdir()
        (dirs["dst"] / "mods" / "dedicated_server_mods_setup.lua").write_text('--comment\nServerModSetup("1")\n')
        cluster = Path(os.environ["USERPROFILE"]) / "Documents" / "Klei" / "DoNotStarveTogether" / "Cluster_1"
        (cluster / "Master").mkdir(parents=True, exist_ok=True)
        (cluster / "Caves").mkdir(parents=True, exist_ok=True)
        (cluster / "Caves" / "modoverrides.lua").write_text('return { ["workshop-1"]={enabled=true} }')
        self.assertEqual([x["id"] for x in mm.list("dst")["mods"]], ["1"])
        r = mm.install("dst", "378160973")
        setup = (dirs["dst"] / "mods" / "dedicated_server_mods_setup.lua").read_text()
        self.assertIn("--comment", setup)
        self.assertIn('ServerModSetup("378160973")', setup)
        self.assertIn('["workshop-378160973"] = { enabled = true }', (cluster / "Master" / "modoverrides.lua").read_text())
        self.assertIn("Caves", r["note"])
        # Space Engineers
        se_dir = Path(os.environ["APPDATA"]) / "SpaceEngineersDedicated"
        se_dir.mkdir(parents=True, exist_ok=True)
        (se_dir / "SpaceEngineers-Dedicated.cfg").write_text(
            '<?xml version="1.0"?>\n<MyConfigDedicated>\n  <SessionSettings />\n  <Mods />\n</MyConfigDedicated>\n')
        mm.install("se", "1135484377")
        text = (se_dir / "SpaceEngineers-Dedicated.cfg").read_text()
        self.assertIn("<Name>1135484377.sbm</Name>", text)
        self.assertIn("<PublishedFileId>1135484377</PublishedFileId>", text)
        import xml.dom.minidom
        xml.dom.minidom.parseString(text)    # still valid XML
        # Unturned
        mm.install("un", "2876376295")
        uj = json.loads((dirs["un"] / "Servers" / "Fam" / "WorkshopDownloadConfig.json").read_text())
        self.assertEqual(uj["File_IDs"], [2876376295])
        # Garry's Mod (one collection)
        mm.install("gm", "111111111")
        mm.install("gm", "222222222")
        args = m.get_server("gm")["launchArgs"]
        self.assertIn("+host_workshop_collection 222222222", args)
        self.assertNotIn("111111111", args)
        self.assertIn("+map gm_construct", args)
        # ARK: Survival Evolved
        mm.install("se1", "731604991")
        gus = dirs["se1"] / "ShooterGame" / "Saved" / "Config" / "WindowsServer" / "GameUserSettings.ini"
        self.assertIn("ActiveMods=731604991", gus.read_text())
        self.assertIn("-automanagedmods", m.get_server("se1")["launchArgs"])

    # -- Steam Workshop installs ---------------------------------------------------------
    def _fake_steamcmd(self, m, files_for):
        def dl(sid, ids, appid=None):
            srv = m.get_server(sid)
            for i in ids:
                base = Path(srv["directory"]) / "steamapps" / "workshop" / "content" / str(appid) / str(i)
                for rel, data in files_for(i).items():
                    (base / rel).parent.mkdir(parents=True, exist_ok=True)
                    (base / rel).write_text(data)
            return {"downloaded": len(ids), "results": [{"mod_id": i, "ok": True} for i in ids]}
        m.download_workshop_mods = dl

    def test_arma_dayz_workshop(self):
        from core.mods import ModError
        from core.mods_common import is_link
        tmp, m, mm = self._mm([{"id": "a3", "name": "A3", "appId": "233780", "launchArgs": "-port=2302 -config=server.cfg"}])
        self._fake_steamcmd(m, lambda i: {"addons/x.pbo": "pbo", "keys/cba_%s.bikey" % i: "key"})
        mm.steam_details = lambda ids: {i: {"title": "CBA A3" if i == "450814997" else "ACE", "time_updated": 1} for i in ids}
        with self.assertRaises(ModError):          # Arma Workshop needs a Steam login
            mm.install("a3", "450814997")
        m.set_setting("steam_username", "bob")
        mm.install("a3", "450814997")
        mm.install("a3", "463939057")
        d = Path(m.get_server("a3")["directory"])
        self.assertTrue(is_link(str(d / "@CBA_A3")))
        self.assertTrue((d / "@CBA_A3" / "addons" / "x.pbo").exists())
        self.assertTrue((d / "keys" / "cba_450814997.bikey").exists())
        self.assertIn("-mod=@CBA_A3;@ACE", m.get_server("a3")["launchArgs"])
        self.assertIn("-config=server.cfg", m.get_server("a3")["launchArgs"])
        self.assertTrue(m.get_server("a3")["requiresAccount"])
        mm.set_field("a3", "463939057", "server_only", True)
        args = m.get_server("a3")["launchArgs"]
        self.assertIn("-mod=@CBA_A3", args)
        self.assertIn("-serverMod=@ACE", args)
        mm.set_enabled("a3", "450814997", False)
        self.assertFalse(os.path.lexists(d / "@CBA_A3"))
        self.assertFalse((d / "keys" / "cba_450814997.bikey").exists())
        self.assertNotIn("-mod=", m.get_server("a3")["launchArgs"])
        content = d / "steamapps" / "workshop" / "content" / "107410" / "450814997"
        self.assertTrue(content.is_dir())          # turning off keeps the download
        mm.remove("a3", "463939057")
        self.assertFalse((d / "steamapps" / "workshop" / "content" / "107410" / "463939057").exists())
        self.assertNotIn("serverMod", m.get_server("a3")["launchArgs"])

    def test_conan_workshop(self):
        tmp, m, mm = self._mm([{"id": "ce", "name": "CE", "appId": "443030"}])
        self._fake_steamcmd(m, lambda i: {"Mod%s.pak" % i: "pak"})
        mm.install("ce", "880454836")
        mm.install("ce", "1159180273")
        d = Path(m.get_server("ce")["directory"])
        self.assertEqual((d / "ConanSandbox" / "Mods" / "modlist.txt").read_text().split(),
                         ["*Mod880454836.pak", "*Mod1159180273.pak"])
        self.assertIn("ServerModList=880454836,1159180273",
                      (d / "ConanSandbox" / "Saved" / "Config" / "WindowsServer" / "ServerSettings.ini").read_text())
        mm.move("ce", "1159180273", -1)
        self.assertEqual((d / "ConanSandbox" / "Mods" / "modlist.txt").read_text().split()[0], "*Mod1159180273.pak")

    # -- mod sites ----------------------------------------------------------------------------
    def test_thunderstore_valheim(self):
        import core.mods_sites as ms
        tmp, m, mm = self._mm([{"id": "vh", "name": "VH", "appId": "896660"}])
        pack = self._zip_bytes({"BepInExPack_Valheim/BepInEx/core/BepInEx.dll": "x",
                                "BepInExPack_Valheim/winhttp.dll": "x", "icon.png": "x", "manifest.json": "{}"})
        jot = self._zip_bytes({"manifest.json": json.dumps({"name": "Jotunn", "version_number": "2.20.0"}),
                               "plugins/Jotunn.dll": "x"})
        mod = self._zip_bytes({"manifest.json": json.dumps({"name": "PlantEverything", "version_number": "1.0.0",
                                                            "dependencies": ["ValheimModding-Jotunn-2.20.0"]}),
                               "PlantEverything.dll": "x"})
        pkgs = {("denikson", "BepInExPack_Valheim"): ("5.4.2202", "u:pack", []),
                ("ValheimModding", "Jotunn"): ("2.20.0", "u:jot", []),
                ("Advize", "PlantEverything"): ("1.0.0", "u:mod", ["denikson-BepInExPack_Valheim-5.4.2202",
                                                                   "ValheimModding-Jotunn-2.20.0"])}
        blobs = {"u:pack": pack, "u:jot": jot, "u:mod": mod}
        old_pkg, old_bytes = ms.ThunderstoreBackend.package, ms.http_bytes
        ms.ThunderstoreBackend.package = lambda self, ns, n: {"name": n, "latest": {
            "version_number": pkgs[(ns, n)][0], "download_url": pkgs[(ns, n)][1], "dependencies": pkgs[(ns, n)][2]}}
        ms.http_bytes = lambda url, **k: blobs[url]
        try:
            self.assertIn("BepInEx", mm.list("vh")["hint"])
            r = mm.install("vh", "https://thunderstore.io/c/valheim/p/Advize/PlantEverything/")
            d = Path(m.get_server("vh")["directory"])
            self.assertTrue((d / "BepInEx" / "core" / "BepInEx.dll").exists())
            self.assertTrue((d / "winhttp.dll").exists())
            self.assertFalse((d / "icon.png").exists())
            self.assertTrue((d / "BepInEx" / "plugins" / "ValheimModding-Jotunn" / "plugins" / "Jotunn.dll").exists())
            self.assertIn("Jotunn", r["note"])
            names = sorted(x["name"] for x in mm.list("vh")["mods"])
            self.assertEqual(names, ["Jotunn", "PlantEverything"])
            pkgs[("Advize", "PlantEverything")] = ("1.1.0", "u:mod", [])
            self.assertTrue(next(x for x in mm.list("vh", True)["mods"] if x["name"] == "PlantEverything")["update_available"])
            mm.set_enabled("vh", "Advize-PlantEverything", False)
            self.assertTrue((d / "BepInEx" / "plugins-disabled" / "Advize-PlantEverything").is_dir())
        finally:
            ms.ThunderstoreBackend.package, ms.http_bytes = old_pkg, old_bytes

    def test_modrinth_minecraft(self):
        import core.mods_sites as ms
        from core.mods_common import sha1_file
        tmp, m, mm = self._mm([{"id": "mc", "name": "MC", "executable": "fabric-server-launch.jar"}])
        d = Path(m.get_server("mc")["directory"])
        (d / "fabric-server-launch.jar").write_text("x")
        (d / "versions" / "1.21.1").mkdir(parents=True)
        lst = mm.list("mc")
        self.assertEqual((lst["source"], lst["loader"], lst["game_version"]), ("modrinth", "fabric", "1.21.1"))
        jar = b"sodium-jar"
        api_jar = b"fabric-api-jar"
        calls = []

        def fake_json(url, data=None, **k):
            calls.append(url)
            if "/project/sodium/version" in url:
                self.assertIn("1.21.1", urllib.parse.unquote(url))
                return [{"id": "v1", "project_id": "AANobbMI", "name": "Sodium", "version_number": "0.6.0",
                         "files": [{"primary": True, "url": "u:sodium", "filename": "sodium-0.6.0.jar"}],
                         "dependencies": [{"project_id": "P7dR8mSH", "dependency_type": "required"}]}]
            if "/project/P7dR8mSH/version" in url:
                return [{"id": "v2", "project_id": "P7dR8mSH", "name": "Fabric API", "version_number": "0.100",
                         "files": [{"primary": True, "url": "u:api", "filename": "fabric-api.jar"}], "dependencies": []}]
            if url.endswith("/version_files"):
                out = {}
                for h in data["hashes"]:
                    if h == hashlib.sha1(jar).hexdigest():
                        out[h] = {"id": "v1", "project_id": "AANobbMI", "version_number": "0.6.0"}
                    if h == hashlib.sha1(api_jar).hexdigest():
                        out[h] = {"id": "v2", "project_id": "P7dR8mSH", "version_number": "0.100"}
                return out
            if "/projects?ids=" in url:
                return [{"id": "AANobbMI", "title": "Sodium", "slug": "sodium", "project_type": "mod"},
                        {"id": "P7dR8mSH", "title": "Fabric API", "slug": "fabric-api", "project_type": "mod"}]
            if url.endswith("/version_files/update"):
                return {hashlib.sha1(jar).hexdigest(): {"id": "v9", "version_number": "0.7.0", "files": [
                    {"primary": True, "url": "u:sodium2", "filename": "sodium-0.7.0.jar"}]}}
            raise AssertionError(url)
        blobs = {"u:sodium": jar, "u:api": api_jar, "u:sodium2": b"new"}
        old = ms.http_json, ms.http_bytes
        ms.http_json, ms.http_bytes = fake_json, (lambda url, **k: blobs[url])
        try:
            import hashlib
            r = mm.install("mc", "https://modrinth.com/mod/sodium")
            self.assertTrue((d / "mods" / "sodium-0.6.0.jar").exists())
            self.assertTrue((d / "mods" / "fabric-api.jar").exists())
            self.assertIn("required", r["note"])
            lst = mm.list("mc", True)
            sod = next(x for x in lst["mods"] if x["name"] == "Sodium")
            self.assertTrue(sod["update_available"])
            mm.set_enabled("mc", "fabric-api.jar", False)
            self.assertTrue((d / "mods" / "fabric-api.jar.disabled").exists())
            r = mm.update("mc")
            self.assertTrue((d / "mods" / "sodium-0.7.0.jar").exists())
            self.assertFalse((d / "mods" / "sodium-0.6.0.jar").exists())
        finally:
            ms.http_json, ms.http_bytes = old

    def test_umod_rust_and_folder(self):
        import core.mods_sites as ms
        tmp, m, mm = self._mm([{"id": "ru", "name": "RU", "appId": "258550"},
                               {"id": "sd", "name": "7D", "appId": "294420"}])
        d = Path(m.get_server("ru")["directory"])
        src = '[Info("Kits", "k1lly0u", "4.4.1")]\nclass Kits : RustPlugin {}'
        old = ms.http_json, ms.http_bytes
        ms.http_json = lambda url, data=None, **k: {"data": [
            {"name": "Kits", "title": "Kits", "download_url": "u:kits", "latest_release_version": "4.4.2",
             "tags_all": "rust,kits", "games_detail": [{"slug": "rust"}]}]}
        ms.http_bytes = lambda url, **k: src.encode() if url == "u:kits" else self._zip_bytes(
            {"MyModlet/ModInfo.xml": "<xml/>", "MyModlet/Config/items.xml": "<items/>"})
        try:
            r = mm.install("ru", "https://umod.org/plugins/kits")
            self.assertIn("Oxide", r["note"])
            self.assertTrue((d / "oxide" / "plugins" / "Kits.cs").exists())
            lst = mm.list("ru", True)
            self.assertEqual((lst["mods"][0]["version"], lst["mods"][0]["update_available"]), ("4.4.1", True))
            self.assertFalse(mm.set_enabled("ru", "Kits.cs", False)["restart_needed"])
            self.assertTrue((d / "oxide" / "plugins_disabled" / "Kits.cs").exists())
            # 7 Days to Die: a plain Mods folder
            mm.install("sd", "https://example.com/files/MyModlet.zip")
            d7 = Path(m.get_server("sd")["directory"])
            self.assertTrue((d7 / "Mods" / "MyModlet" / "ModInfo.xml").exists())
            mm.set_enabled("sd", "MyModlet", False)
            self.assertFalse((d7 / "Mods" / "MyModlet").exists())
            self.assertEqual(mm.list("sd")["mods"][0]["enabled"], False)
        finally:
            ms.http_json, ms.http_bytes = old

    def test_bulk_add_and_arma_preset(self):
        tmp, m, mm = self._mm([{"id": "kf", "name": "KF", "appId": "1110390"},
                               {"id": "a3", "name": "A3", "appId": "233780"}])
        r = mm.install("kf", "111111 https://steamcommunity.com/sharedfiles/filedetails/?id=222222\n333333,444444 junk")
        self.assertEqual(sorted(r["installed"]), ["111111", "222222", "333333", "444444"])
        self.assertEqual(r["failed"][0]["mod"], "junk")
        self._fake_steamcmd(m, lambda i: {"addons/a.pbo": "x"})
        m.set_setting("steam_username", "bob")
        preset = Path(tempfile.mkdtemp()) / "preset.html"
        preset.write_text('<html><a href="https://steamcommunity.com/sharedfiles/filedetails/?id=450814997">CBA</a>'
                          '<a href="http://steamcommunity.com/sharedfiles/filedetails/?id=463939057">ACE</a></html>')
        r = mm.install_file("a3", str(preset))
        self.assertEqual(len(r["installed"]), 2)
        self.assertIn("-mod=@450814997;@463939057", m.get_server("a3")["launchArgs"])

    def test_options_and_web_limits(self):
        from core.mods import ModError
        from core.web_auth import DiscordAuth
        tmp, m, mm = self._mm([{"id": "x", "name": "X", "appId": "999999"}])
        self.assertEqual(mm.list("x")["source"], "none")
        mm.set_option("x", "modSource", "folder")
        mm.set_option("x", "modFolder", "addons/mods")
        self.assertEqual(mm.list("x")["source"], "folder")
        for bad in (("modFolder", "../../etc"), ("modFolder", "C:/Windows"), ("modSource", "evil"),
                    ("mcVersion", "abc"), ("launchArgs", "x")):
            with self.assertRaises(ModError):
                mm.set_option("x", *bad)
        with self.assertRaises(ModError):
            mm.set_option("x", "curseforge_api_key", "k", user="web:Bob")
        mm.set_option("x", "curseforge_api_key", "k", user="gui")
        self.assertNotIn('"k"', (tmp / "settings.json").read_text())
        self.assertTrue(DiscordAuth.allowed({"role": "admin"}, "mods.set_option"))
        self.assertFalse(DiscordAuth.allowed({"role": "operator"}, "mods.move"))
        self.assertTrue(DiscordAuth.allowed({"role": "operator"}, "mods.list"))


class HistoryTests(unittest.TestCase):

    def test_series_buckets_gaps_and_players(self):
        from core.history import series, sparklines, record, SYSTEM_ID
        tmp = make_env([{"id": "a", "name": "A"}, {"id": "b", "name": "B"}])
        e = engine(tmp)
        db = e.db
        now = time.time()
        conn = db._get_conn()
        for i in range(30):                       # last 30 minutes, players known
            conn.execute("INSERT INTO server_metrics (server_id, timestamp, cpu_percent, memory_mb, player_count,"
                         " max_players, uptime_seconds) VALUES (?,?,?,?,?,?,?)",
                         ("a", now - 1800 + i * 60, 10 + i, 1000 + i, i % 4, 8, 0))
        for i in range(5):                        # B: no player counts (NULL)
            conn.execute("INSERT INTO server_metrics (server_id, timestamp, cpu_percent, memory_mb, player_count,"
                         " max_players, uptime_seconds) VALUES (?,?,?,?,?,?,?)",
                         ("b", now - 300 + i * 60, 50, 2000, None, 0, 0))
        conn.commit()
        s1 = series(db, "a", "1h", now=now)
        self.assertEqual(s1["bucket"], 60)
        self.assertTrue(28 <= len(s1["points"]) <= 31)
        self.assertTrue(s1["has_players"])
        self.assertEqual(s1["peak_players"], 3)
        self.assertEqual(s1["peak_ram"], 1029)
        s7 = series(db, "a", "7d", now=now)
        self.assertLessEqual(len(s7["points"]), 2)
        sb = series(db, "b", "24h", now=now)
        self.assertFalse(sb["has_players"])
        sp = sparklines(db, ["a", "b"], now=now)
        self.assertEqual(len(sp["a"]["cpu"]), 30)
        self.assertIsNone(sp["b"]["players"])
        self.assertIsNotNone(sp["a"]["players"])
        record(db, [{"id": "a", "isRunning": True, "cpuPercent": 5, "memUsageMB": 10, "playerCount": None}],
               {"cpu": 12, "ram_used_mb": 9000, "players": None})
        r = e.api.process("metrics.series", {"server_id": SYSTEM_ID, "range": "1h"})
        self.assertTrue(r.success, r.error)
        self.assertEqual(r.data["points"][-1]["ram"], 9000)
        self.assertFalse(e.api.process("metrics.series", {"server_id": "nope"}).success)
        self.assertTrue(e.api.process("metrics.sparklines", {}).success)


class LogRotationTests(unittest.TestCase):
    def test_rotate_keeps_three(self):
        from server_manager import rotate_log
        d = Path(tempfile.mkdtemp())
        p = d / "baobun-x.log"
        for n in range(5):
            p.write_bytes(b"x" * 200 + str(n).encode())
            self.assertTrue(rotate_log(str(p), max_bytes=100, keep=3))
        self.assertFalse(p.exists())
        self.assertEqual(sorted(x.name for x in d.iterdir()), ["baobun-x.log.1", "baobun-x.log.2", "baobun-x.log.3"])
        self.assertTrue((d / "baobun-x.log.1").read_bytes().endswith(b"4"))
        p.write_bytes(b"small")
        self.assertFalse(rotate_log(str(p), max_bytes=100))


class PlayerTests(unittest.TestCase):
    def test_vs_players_from_log(self):
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story"}])
        m = engine(tmp).manager
        m.start_server("vs")
        try:
            self.assertEqual(m.poll_players("vs"), 0)
            m.send_console_command("vs", "join")
            self.assertTrue(wait_for(lambda: m.poll_players("vs") == 1, 10))
            st = next(s for s in m.get_all_statuses() if s["id"] == "vs")
            self.assertEqual((st["playerCount"], [p["name"] for p in st["players"]]), (1, ["Bob"]))
            m.send_console_command("vs", "leave")
            self.assertTrue(wait_for(lambda: m.poll_players("vs") == 0, 10))
        finally:
            m.stop_server("vs")

    def test_log_tracker_and_markers(self):
        from core.players import LogPlayers
        pats = {"join": [r"\[Server Event\]\s+(?P<name>\S+)\s+[0-9.]+:\d+\s+joins",
                         r"\[Server Event\]\s+(?P<name>\S+)\s+joins"],
                "leave": [r"\[Server Event\]\s+(?:Player\s+)?(?P<name>\S+)\s+left"]}
        p = Path(tempfile.mkdtemp()) / "c.log"
        p.write_text("[Server Event] Old 1.1.1.1:5 joins.\n==== BaoBun: starting VS ====\n"
                     "[Server Event] Ann 10.0.0.2:55 joins.\n[Server Event] Cid joins.\n")
        lp = LogPlayers()
        self.assertEqual(sorted(lp.update("x", str(p), pats)), ["Ann", "Cid"])
        with open(p, "a") as f:
            f.write("[Server Event] Player Ann left.\n[Server Event] Dee 1.2.3.4:9")
        self.assertEqual(sorted(lp.update("x", str(p), pats)), ["Cid"])
        with open(p, "a") as f:
            f.write(" joins.\n")
        self.assertEqual(sorted(lp.update("x", str(p), pats)), ["Cid", "Dee"])

    def test_a2s_players(self):
        import struct as st
        from core.a2s import query_players
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]

        def serve():
            for _ in range(2):
                data, addr = srv.recvfrom(1024)
                if data[5:9] == b"\xFF\xFF\xFF\xFF":
                    srv.sendto(b"\xFF\xFF\xFF\xFFA" + b"\x01\x02\x03\x04", addr)
                else:
                    body = b"\xFF\xFF\xFF\xFFD" + bytes([2])
                    for n, sc, t in ((b"Ann", 5, 61.0), (b"", 0, 3.0)):
                        body += b"\x00" + n + b"\x00" + st.pack("<lf", sc, t)
                    srv.sendto(body, addr)
        threading.Thread(target=serve, daemon=True).start()
        self.assertEqual(query_players("127.0.0.1", port), [{"name": "Ann", "score": 5, "seconds": 61}])
        srv.close()


class NetworkTests(unittest.TestCase):
    def test_ports_and_connect_info(self):
        import core.network as net
        self.assertEqual([(p["port"], p["proto"]) for p in net.expected_ports({"id": "v", "appId": "896660", "port": 2456})],
                         [(2456, "UDP"), (2457, "UDP"), (2458, "UDP")])
        self.assertEqual(net.expected_ports({"id": "m", "executable": "x/server.jar", "port": 25565})[0]["proto"], "TCP")
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story", "port": 42420}])
        m = engine(tmp).manager
        net._public_cache = (time.time(), "203.0.113.5")
        d = net.connect_info(m, "vs")
        self.assertEqual(d["addresses"]["internet"], "203.0.113.5:42420")
        self.assertEqual(d["ports"][0], {"port": 42420, "proto": "TCP/UDP", "label": "Game", "listening": None})

    def test_upnp_with_fake_router(self):
        import core.network as net
        from http.server import BaseHTTPRequestHandler, HTTPServer
        calls = []
        desc = (b'<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device><deviceList><device>'
                b'<serviceList><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>'
                b'<controlURL>/ctl/IPConn</controlURL></service></serviceList></device></deviceList></device></root>')

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(desc)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                calls.append((self.path, self.headers["SOAPAction"], body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<ok/>")
        httpd = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            loc = "http://127.0.0.1:%d/desc.xml" % httpd.server_port
            gw = net.find_gateway([loc])
            self.assertEqual(gw, ("http://127.0.0.1:%d/ctl/IPConn" % httpd.server_port,
                                  "urn:schemas-upnp-org:service:WANIPConnection:1"))
            r = net.upnp_forward([{"port": 2456, "proto": "UDP", "label": "Game"},
                                  {"port": 42420, "proto": "TCP/UDP", "label": "Game"}], "192.168.1.20",
                                 label="BaoBun A&B", gateway=gw)
            self.assertTrue(r["ok"])
            self.assertEqual(r["done"], ["UDP 2456", "TCP 42420", "UDP 42420"])
            self.assertIn("AddPortMapping", calls[0][1])
            self.assertIn("<NewInternalClient>192.168.1.20</NewInternalClient>", calls[0][2])
            self.assertIn("A&amp;B", calls[0][2])
        finally:
            httpd.shutdown()


class UpdateFlagTests(unittest.TestCase):
    def test_flags_shared_install_and_clear(self):
        tmp = make_env([{"id": "a", "name": "A", "appId": "896660", "_dir": "shared"},
                        {"id": "b", "name": "B", "appId": "896660", "_dir": "shared"},
                        {"id": "c", "name": "C", "type": "vintage-story"}])
        m = engine(tmp).manager
        calls = []
        m.check_steam_update = lambda sid: calls.append(sid) or {"installed": "1", "latest": "2", "update_available": True}
        flags = m.refresh_update_flags()
        self.assertEqual(flags, {"a": True, "b": True})
        self.assertEqual(len(calls), 1)                       # one Steam check per shared install
        st = {s["id"]: s for s in m.get_all_statuses()}
        self.assertTrue(st["a"]["updateAvailable"])
        self.assertNotIn("updateAvailable", st["c"])
        self.assertEqual(m.refresh_update_flags(), {"a": True, "b": True})
        self.assertEqual(len(calls), 1)                       # cached for 6 hours
        m.check_steam_update = lambda sid: {"installed": "2", "latest": "2", "update_available": False}
        m._steamcmd_app_update = lambda *a, **k: None
        self.assertEqual(m.update_server("a")["status"], "up_to_date")
        self.assertNotIn("updateAvailable", {s["id"]: s for s in m.get_all_statuses()}["b"])


    def test_agent_auto_update_only_when_empty(self):
        from baobun_agent import BaoBunAgent
        tmp = make_env([{"id": "a", "name": "A", "appId": "896660", "autoUpdateWhenEmpty": True},
                        {"id": "b", "name": "B", "appId": "896660", "_dir": "other"}])
        agent = BaoBunAgent.__new__(BaoBunAgent)
        eng = engine(tmp)
        agent.engine, agent.mgr = eng, eng.manager
        calls = []

        class Api:
            def process(self, action, params, user_id=None):
                calls.append((action, params["server_id"]))
                return type("R", (), {"success": True, "error": None})()
        agent.engine = type("E", (), {"api": Api()})()
        agent.mgr.is_running = lambda sid: True
        agent.mgr.player_count = lambda sid: 2
        agent._auto_update({"a": True, "b": True})
        self.assertEqual(calls, [])                           # players online
        agent.mgr.player_count = lambda sid: None
        agent._auto_update({"a": True})
        self.assertEqual(calls, [])                           # unknown: leave it
        agent.mgr.player_count = lambda sid: 0
        agent._auto_update({"a": True, "b": True})
        self.assertEqual(calls, [("server.update", "a")])     # b didn't opt in


class QuickSettingsTests(unittest.TestCase):
    def test_many_games(self):
        import core.quick_settings as qs
        tmp = make_env([
            {"id": "vs", "name": "VS", "type": "vintage-story"},
            {"id": "mc", "name": "MC", "executable": "x/server.jar"},
            {"id": "vh", "name": "VH", "appId": "896660", "launchArgs": "-nographics -batchmode -name \"Old Name\" -port 2456"},
            {"id": "pw", "name": "PW", "appId": "2394010"},
            {"id": "sd", "name": "7D", "appId": "294420"},
            {"id": "a3", "name": "A3", "appId": "233780", "launchArgs": "-port=2302 -config=srv.cfg"},
            {"id": "asa", "name": "ASA", "appId": "2430930", "launchArgs": "TheIsland_WP?listen?SessionName=Old -Port=7777"},
            {"id": "rf", "name": "RF", "appId": "1874900"},
            {"id": "rs", "name": "RS", "appId": "258550", "launchArgs": "-batchmode +server.port 28015"},
            {"id": "en", "name": "EN", "appId": "2278520"}])
        m = engine(tmp).manager
        d = {s["id"]: Path(s["directory"]) for s in m.load_servers()}
        (d["vs"] / "Data").mkdir()
        (d["vs"] / "Data" / "serverconfig.json").write_text('{"ServerName": "A", "MaxClients": 16, "AdvertiseServer": false, "Password": "x"}')
        (d["mc"] / "server.properties").write_text("#Minecraft server properties\nmotd=A Minecraft Server\nmax-players=20\npvp=true\n")
        (d["pw"] / "DefaultPalWorldSettings.ini").write_text(
            '[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(Difficulty=None,ServerPlayerMaxNum=32,ServerName="Default Palworld Server",ServerDescription="",AdminPassword="",ServerPassword="",bIsMultiplay=False)\n')
        (d["sd"] / "serverconfig.xml").write_text('<?xml version="1.0"?>\n<ServerSettings>\n\t<property name="ServerName" value="My Game Host" />\n'
                                                  '\t<property name="ServerMaxPlayerCount" value="8" />\n</ServerSettings>\n')
        (d["a3"] / "srv.cfg").write_text('hostname = "Old";\npasswordAdmin = "adm";\npassword = "";\nmaxPlayers = 40;\n')
        (d["rf"] / "config.json").write_text(json.dumps({"bindPort": 2001, "game": {"name": "x", "maxPlayers": 64, "visible": True}}))
        (d["en"] / "enshrouded_server.json").write_text('{"name": "Enshrouded Server", "slotCount": 16, "gamePort": 15636}')

        g = qs.get(m, "vs")
        self.assertTrue(g["supported"])
        f = {x["key"]: x for x in g["fields"]}
        self.assertEqual((f["ServerName"]["value"], f["MaxClients"]["value"], f["Password"]["value"], f["Password"]["has_value"]),
                         ("A", "16", "", True))
        qs.save(m, "vs", {"ServerName": "BaoBun Survival", "MaxClients": "24", "AdvertiseServer": "true", "Password": ""})
        cfg = json.loads((d["vs"] / "Data" / "serverconfig.json").read_text())
        self.assertEqual((cfg["ServerName"], cfg["MaxClients"], cfg["AdvertiseServer"], cfg["Password"]),
                         ("BaoBun Survival", 24, True, "x"))        # blank password = unchanged
        qs.save(m, "mc", {"motd": "Fam", "max-players": "10", "difficulty": "hard"})
        props = (d["mc"] / "server.properties").read_text()
        self.assertIn("motd=Fam", props)
        self.assertIn("difficulty=hard", props)
        self.assertIn("#Minecraft server properties", props)
        with self.assertRaises(ValueError):
            qs.save(m, "mc", {"difficulty": "nightmare"})
        with self.assertRaises(ValueError):
            qs.save(m, "mc", {"max-players": "ten"})
        qs.save(m, "vh", {"name": "Bao Valheim", "world": "Dedicated", "password": "secret1", "public": "1"})
        args = m.get_server("vh")["launchArgs"]
        self.assertIn('-name "Bao Valheim"', args)
        self.assertIn("-password secret1", args)
        self.assertIn("-port 2456", args)
        self.assertNotIn("Old Name", args)
        with self.assertRaises(ValueError):
            qs.save(m, "vh", {"password": "Bao"})
        qs.save(m, "pw", {"ServerName": "Pal Fam", "ServerPlayerMaxNum": "8", "ServerPassword": "pw123"})
        ini = (d["pw"] / "Pal" / "Saved" / "Config" / "WindowsServer" / "PalWorldSettings.ini").read_text()
        self.assertIn('ServerName="Pal Fam"', ini)
        self.assertIn("ServerPlayerMaxNum=8", ini)
        self.assertIn('ServerPassword="pw123"', ini)
        self.assertIn("bIsMultiplay=False", ini)
        self.assertEqual({x["key"]: x for x in qs.get(m, "pw")["fields"]}["ServerName"]["value"], "Pal Fam")
        qs.save(m, "sd", {"ServerName": "Zed & Co", "ServerMaxPlayerCount": "12", "ServerPassword": "z"})
        xtext = (d["sd"] / "serverconfig.xml").read_text()
        self.assertIn('name="ServerName" value="Zed &amp; Co"', xtext)
        self.assertIn('name="ServerPassword" value="z"', xtext)
        import xml.dom.minidom
        xml.dom.minidom.parseString(xtext)
        qs.save(m, "a3", {"hostname": "Ops", "password": "join", "maxPlayers": "60"})
        cfg = (d["a3"] / "srv.cfg").read_text()
        self.assertIn('hostname = "Ops";', cfg)
        self.assertIn('password = "join";', cfg)
        self.assertIn('passwordAdmin = "adm";', cfg)
        self.assertIn("maxPlayers = 60;", cfg)
        self.assertEqual({x["key"]: x for x in qs.get(m, "a3")["fields"]}["hostname"]["value"], "Ops")
        qs.save(m, "asa", {"SessionName": "BaoArk", "ServerPassword": "pw", "WinLiveMaxPlayers": "20"})
        args = m.get_server("asa")["launchArgs"]
        self.assertTrue(args.startswith("TheIsland_WP?listen?SessionName=BaoArk?ServerPassword=pw"))
        self.assertIn("-WinLiveMaxPlayers=20", args)
        with self.assertRaises(ValueError):
            qs.save(m, "asa", {"SessionName": "has space"})
        qs.save(m, "rf", {"name": "Reforger Fam", "maxPlayers": "32", "visible": "false"})
        rc = json.loads((d["rf"] / "config.json").read_text())
        self.assertEqual((rc["game"]["name"], rc["game"]["maxPlayers"], rc["game"]["visible"], rc["bindPort"]),
                         ("Reforger Fam", 32, False, 2001))
        qs.save(m, "rs", {"hostname": "Bao Rust", "maxplayers": "50"})
        self.assertIn('+server.hostname "Bao Rust"', m.get_server("rs")["launchArgs"])
        self.assertIn("+server.port 28015", m.get_server("rs")["launchArgs"])
        qs.save(m, "en", {"name": "Bao Embervale", "slotCount": "6"})
        en = json.loads((d["en"] / "enshrouded_server.json").read_text())
        self.assertEqual((en["name"], en["slotCount"], en["gamePort"]), ("Bao Embervale", 6, 15636))
        self.assertFalse(qs.get(m, "en")["fields"][0].get("error"))
        e = engine(tmp)
        r = e.api.process("server.game_settings", {"server_id": "en"})
        self.assertTrue(r.success and r.data["supported"])


class ImportTests(unittest.TestCase):
    def test_scan_and_import(self):
        from core.importer import scan, import_servers
        root = Path(tempfile.mkdtemp())
        def touch(rel):
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x")
        touch("Valheim Fam/valheim_server.exe")
        touch("games/ASA/ShooterGame/Binaries/Win64/ArkAscendedServer.exe")
        touch("MC/server.properties"); touch("MC/eula.txt"); touch("MC/paper-1.21.1.jar")
        touch("gmod/srcds.exe"); touch("gmod/steamapps/appmanifest_4020.acf")
        touch("VS/VintagestoryServer.exe")
        touch("notes/readme.txt")
        tmp = make_env([{"id": "a", "name": "Existing", "_dir": "x"}])
        m = engine(tmp).manager
        found = scan(str(root), known_dirs=[str(root / "VS")])
        games = {Path(f["directory"]).name: f for f in found}
        self.assertEqual(sorted(games), ["ASA", "MC", "VS", "Valheim Fam", "gmod"])
        self.assertEqual(games["Valheim Fam"]["appId"], "896660")
        self.assertEqual(games["gmod"]["game"], "Garry's Mod")
        self.assertEqual(games["MC"]["executable"], str(root / "MC" / "paper-1.21.1.jar"))
        self.assertEqual(games["VS"]["type"], "vintage-story")
        self.assertTrue(games["VS"]["already_added"])
        added = import_servers(m, [f for f in found if not f["already_added"]])
        self.assertEqual(len(added), 4)
        self.assertEqual(len(import_servers(m, found)), 1)       # VS folder wasn't in this BaoBun yet
        self.assertEqual(import_servers(m, found), [])           # nothing twice
        names = [s["name"] for s in m.load_servers()]
        self.assertIn("Valheim Fam", names)
        self.assertTrue(all(s.get("manualStart") for s in m.load_servers() if s["id"] != "a"))


class BackupMirrorTests(unittest.TestCase):
    def test_mirror_copy_and_prune(self):
        tmp = make_env([{"id": "a", "name": "A"}])
        m = engine(tmp).manager
        d = Path(m.get_server("a")["directory"])
        (d / "Saves").mkdir()
        (d / "Saves" / "w.dat").write_text("world")
        mirror = Path(tempfile.mkdtemp()) / "nas"
        m.set_setting("backup_mirror_dir", str(mirror))
        for _ in range(3):
            r = m.backup_server_world("a", keep=2)
            self.assertTrue(Path(r["mirror"]).is_file())
        self.assertEqual(len(list((mirror / "a").glob("*.zip"))), 2)
        self.assertEqual((mirror / "a" / "server-name.txt").read_text(), "A")
        m.set_setting("backup_mirror_dir", _unwritable())
        r = m.backup_server_world("a")
        self.assertIn("mirror_error", r)
        self.assertTrue(Path(r["zip_path"]).is_file())


class AnnouncementTests(unittest.TestCase):
    def test_scheduled_message(self):
        from baobun_agent import BaoBunAgent
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story"}])
        eng = engine(tmp)
        r = eng.api.process("schedules.set", {"server_id": "vs", "kind": "message",
                                              "config": {"enabled": True, "mode": "interval", "hours": 1, "text": ""}})
        self.assertFalse(r.success)
        r = eng.api.process("schedules.set", {"server_id": "vs", "kind": "message",
                                              "config": {"enabled": True, "mode": "interval", "hours": 1,
                                                         "text": "Join our Discord!"}})
        self.assertTrue(r.success, r.error)
        agent = BaoBunAgent.__new__(BaoBunAgent)
        agent.engine, agent.mgr, agent._busy = eng, eng.manager, {}
        agent.mgr.start_server("vs")
        try:
            agent._run_scheduled("vs", "message", r.data["schedule"])
            self.assertTrue(wait_for(lambda: any("/announce Join our Discord!" in l
                                                 for l in agent.mgr.get_logs("vs", 50) + open(
                                                     agent.mgr.get_console_log_path("vs")).read().splitlines()), 10))
        finally:
            agent.mgr.stop_server("vs")


class DiscordStatusTests(unittest.TestCase):
    def test_status_lines(self):
        from core import discord_status as discord_bot
        import core.network as net
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story", "port": 42420}])
        m = engine(tmp).manager
        net._public_cache = (time.time(), "203.0.113.7")
        st = [{"id": "vs", "name": "VS", "state": "running", "isRunning": True, "playerCount": 2, "maxPlayers": 16,
               "players": [{"name": "Bob"}, {"name": "Ann"}], "updateAvailable": True},
              {"id": "x", "name": "Valheim", "state": "crashed"}]
        lines = discord_bot.status_lines(m, st, show_address=True)
        self.assertIn("2/16 online (Bob, Ann)", lines[0])
        self.assertIn("`203.0.113.7:42420`", lines[0])
        self.assertIn("update available", lines[0])
        self.assertIn("crashed", lines[1])
        self.assertNotIn("203.0.113.7", discord_bot.status_lines(m, st)[0])


class QuickCommandTests(unittest.TestCase):
    def test_lists_and_fill(self):
        from core.quick_commands import for_server, fill
        vs = for_server({"type": "vintage-story"})
        self.assertTrue(any(q["command"] == "/kick {player}" and q["player"] for q in vs))
        self.assertTrue(for_server({"executable": "x/server.jar"}))
        self.assertEqual(for_server({"appId": "896660"}), [])
        self.assertEqual(fill('kickuser "{player}"', 'Bo"b\n'), 'kickuser "Bob"')


def _unwritable(base=None):
    """A folder path that can't be created on any OS (a "folder" inside a file)."""
    blocker = Path(base or tempfile.mkdtemp()) / "not-a-folder"
    blocker.write_text("x")
    return str(blocker / "sub")


def _fake_java(tmp):
    """A 'java' that is really the fake server (records its argv)."""
    home = Path(tmp) / "jdk" / "bin"
    home.mkdir(parents=True, exist_ok=True)
    (home.parent / "release").write_text('JAVA_VERSION="21.0.5"\n')
    exe = home / ("java.exe" if os.name == "nt" else "java")
    exe.write_text("#!%s\nimport sys, runpy\nopen('java_argv.txt', 'w').write(repr(sys.argv[1:]))\n"
                   "sys.argv = [%r, 'normal']\nrunpy.run_path(%r, run_name='__main__')\n"
                   % (sys.executable, FAKE, FAKE))
    exe.chmod(0o755)
    return str(exe)


class MinecraftTests(unittest.TestCase):

    def test_java_versions_and_pick(self):
        from core import minecraft as mc
        self.assertEqual(mc.java_for_version("1.21.4"), 21)
        self.assertEqual(mc.java_for_version("1.20.4"), 17)
        self.assertEqual(mc.java_for_version("1.20.5"), 21)
        self.assertEqual(mc.java_for_version("1.16.5"), 8)
        self.assertEqual(mc.java_for_version("26.1"), 25)
        javas = [{"path": "a", "major": 17}, {"path": "b", "major": 25}, {"path": "c", "major": 21}]
        self.assertEqual(mc.pick_java(21, javas), "c")
        self.assertEqual(mc.pick_java(20, javas), "c")
        self.assertIsNone(mc.pick_java(8, javas))
        self.assertIsNone(mc.pick_java(26, javas))
        self.assertEqual(mc._major("1.8.0_402"), 8)

    @unittest.skipIf(os.name == "nt", "the stand-in java is a script, which Windows can't run as java.exe")
    def test_jar_server_runs_through_java(self):
        tmp = make_env([{"id": "mc", "name": "MC"}])
        d = tmp / "mc"
        (d / "server.jar").write_text("jar")
        java = _fake_java(tmp)
        servers = json.loads((tmp / "servers.json").read_text())
        servers[0].update({"executable": str(d / "server.jar"), "launchArgs": "", "javaPath": java,
                           "javaMemoryMB": 2048})
        (tmp / "servers.json").write_text(json.dumps(servers))
        eng = engine(tmp)
        m = eng.manager
        self.assertEqual(type(m.plugin_for(m.get_server("mc"))).__name__, "MinecraftPlugin")
        self.assertEqual(m.start_server("mc")["status"], "started")
        self.assertTrue(wait_for(lambda: (d / "java_argv.txt").exists()))
        argv = eval((d / "java_argv.txt").read_text())
        self.assertEqual(argv[:2], ["-Xms1024M", "-Xmx2048M"])
        self.assertIn("-jar", argv)
        self.assertEqual(argv[-1], "nogui")
        r = m.stop_server("mc")
        self.assertTrue(r["graceful"])
        self.assertTrue(any("got: stop" in l for l in m.get_logs("mc", 50)))

    def test_missing_java_message_and_forge_args(self):
        from core import minecraft as mc
        tmp = Path(tempfile.mkdtemp())
        (tmp / "run.bat").write_text("@echo off\r\nREM\r\njava @user_jvm_args.txt "
                                     "@libraries/net/neoforged/neoforge/21.1.77/win_args.txt %*\r\npause\r\n")
        (tmp / "eula.txt").write_text("eula=true")
        srv = {"directory": str(tmp), "executable": str(tmp / "run.bat"), "javaPath": sys.executable}
        self.assertTrue(mc.looks_minecraft(srv))
        toks = mc._argfile_tokens(str(tmp / "run.bat"))
        self.assertEqual(toks, ["@user_jvm_args.txt", "@libraries/net/neoforged/neoforge/21.1.77/win_args.txt"])
        cmd = mc.launch_command(srv, srv["executable"], "")
        self.assertIn("@user_jvm_args.txt", cmd)
        if isinstance(cmd, str):          # Windows builds one command line
            self.assertTrue(cmd.endswith(" nogui"), cmd)
        else:
            self.assertEqual(cmd[-1], "nogui")
        orig = mc.find_javas
        mc.find_javas = lambda extra=None: []
        try:
            with self.assertRaises(RuntimeError) as cm:
                mc.resolve_java({"mcVersion": "1.21.1"})
            self.assertIn("Java 21", str(cm.exception))
        finally:
            mc.find_javas = orig

    def test_server_list_ping(self):
        from core import minecraft as mc
        lst = socket.socket()
        lst.bind(("127.0.0.1", 0))
        lst.listen(1)
        port = lst.getsockname()[1]
        payload = json.dumps({"version": {"name": "1.21.4"}, "description": {"text": "hi"},
                              "players": {"online": 2, "max": 20,
                                          "sample": [{"name": "Steve", "id": "x"}, {"name": "Alex", "id": "y"}]}})

        def serve():
            c, _ = lst.accept()
            c.settimeout(3)
            c.recv(1024)
            body = mc._varint(0) + mc._varint(len(payload)) + payload.encode()
            c.sendall(mc._varint(len(body)) + body)
            time.sleep(0.2)
            c.close()
        threading.Thread(target=serve, daemon=True).start()
        res = mc.ping("127.0.0.1", port)
        lst.close()
        self.assertEqual(res["count"], 2)
        self.assertEqual(res["max"], 20)
        self.assertEqual(res["names"], ["Steve", "Alex"])
        self.assertEqual(res["version"], "1.21.4")
        self.assertIsNone(mc.ping("127.0.0.1", port, timeout=0.5))


class InstallerTests(unittest.TestCase):

    def _zip(self, names):
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n in names:
                z.writestr(n, "x")
        return buf.getvalue()

    def _patch(self, gi, routes, files):
        """Replace HTTP with dicts: routes url->json, files url->bytes."""
        def http_json(url, *a, **k):
            for key, val in routes.items():
                if url.startswith(key):
                    return val
            raise AssertionError("unexpected url " + url)

        def download(url, dest, progress=None, **k):
            data = files[url]
            with open(dest, "wb") as f:
                f.write(data)
            if progress:
                progress("downloading", 100)
            return dest
        old = (gi.http_json, gi.download)
        gi.http_json, gi.download = http_json, download
        self.addCleanup(lambda: setattr(gi, "http_json", old[0]) or setattr(gi, "download", old[1]))

    def test_progress_lines_parse(self):
        from core.game_installers import Progress
        from core.hosting import parse_steamcmd_progress
        d = tempfile.mkdtemp()
        p = Progress(d)
        p("downloading Vintage Story 1.21.5", 42.0, "300 MB")
        text = Path(d, "install.log").read_text()
        self.assertEqual(parse_steamcmd_progress(text), (42.0, "downloading Vintage Story 1.21.5"))
        p("done", 100)
        self.assertEqual(parse_steamcmd_progress(Path(d, "install.log").read_text())[0], 100.0)

    def test_vintage_story_install_and_update_flag(self):
        from core import game_installers as gi
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story",
                         "executable": "StratumServer.exe"}])
        m = engine(tmp).manager
        url = "https://cdn.example/vs_server_win-x64_1.21.5.zip"
        self._patch(gi, {gi.VS_API % "stable": {
            "1.21.5": {"windowsserver": {"filename": "x.zip", "md5": None, "urls": {"cdn": url}}},
            "1.21.10": {"windowsserver": {"urls": {"cdn": url.replace("1.21.5", "1.21.10")}}},
            "1.20.12": {"windowsserver": {"urls": {"cdn": "u3"}}}}},
            {url.replace("1.21.5", "1.21.10"): self._zip(["VintagestoryServer.exe", "Lib/a.dll"])})
        rel = gi.vs_releases()
        self.assertEqual([r["version"] for r in rel], ["1.21.10", "1.21.5", "1.20.12"])
        self.assertEqual(m.install_options("vs")["kind"], "vintage-story")
        res = m.install_game("vs")
        self.assertEqual(res["version"], "1.21.10")
        srv = m.get_server("vs")
        self.assertTrue(srv["executable"].endswith("VintagestoryServer.exe"))
        self.assertTrue((tmp / "vs" / "Lib" / "a.dll").exists())
        self.assertTrue(gi.vs_is_vanilla(srv))
        # a custom Stratum build is never overwritten
        (tmp / "vs" / "StratumServer.exe").write_text("x")
        self.assertEqual(gi.vs_install_state(dict(srv, executable="StratumServer.exe")), "custom")
        # update check: installed 1.21.5 vs newest 1.21.10 -> patch update
        m.vs_server_version = lambda sid: "1.21.5"
        info = gi.check_update(m, srv)
        self.assertTrue(info["update_available"])
        self.assertTrue(info["patch"])
        m.vs_server_version = lambda sid: "1.20.12"
        self.assertFalse(gi.check_update(m, srv)["patch"])
        self.assertFalse(m.refresh_update_flags()["vs"])   # just installed: cached "up to date"
        m._write_registry("vs", update_checked_at=0)
        flags = m.refresh_update_flags()
        self.assertTrue(flags["vs"])
        self.assertTrue(m._read_registry("vs")["update_major"])

    def test_minecraft_vanilla_and_fabric(self):
        from core import game_installers as gi
        from core import minecraft as mc
        tmp = make_env([{"id": "mc", "name": "MC", "executable": "server.jar"}])
        m = engine(tmp).manager
        self._patch(gi, {
            gi.MOJANG_MANIFEST: {"latest": {"release": "1.21.4"},
                                 "versions": [{"id": "1.21.4", "type": "release", "url": "https://v/1.21.4.json"}]},
            "https://v/1.21.4.json": {"downloads": {"server": {"url": "https://jar", "sha1": None}},
                                      "javaVersion": {"majorVersion": 21}},
            gi.FABRIC_META + "/versions/loader/1.21.4": [{"loader": {"version": "0.16.9", "stable": True}}],
            gi.FABRIC_META + "/versions/installer": [{"version": "1.0.1", "stable": True}],
        }, {"https://jar": b"jar",
            gi.FABRIC_META + "/versions/loader/1.21.4/0.16.9/1.0.1/server/jar": b"fab"})
        orig = mc.pick_java
        mc.pick_java = lambda req, javas=None: "/fake/java"
        self.addCleanup(lambda: setattr(mc, "pick_java", orig))
        self.assertEqual(m.install_options("mc")["kind"], "minecraft")
        res = m.install_game("mc", {"accept_eula": True})
        self.assertEqual(res["version"], "1.21.4")
        srv = m.get_server("mc")
        self.assertEqual((srv["mcFlavor"], srv["mcVersion"], srv["mcJava"]), ("vanilla", "1.21.4", 21))
        self.assertNotIn("mcLoader", srv)
        self.assertIn("eula=true", (tmp / "mc" / "eula.txt").read_text())
        res = m.install_game("mc", {"flavor": "fabric", "version": "1.21.4"})
        srv = m.get_server("mc")
        self.assertTrue(srv["executable"].endswith("fabric-server-launch.jar"))
        self.assertEqual(srv["mcLoader"], "fabric")
        self.assertEqual(gi._neo_mc("21.1.77"), "1.21.1")
        self.assertEqual(gi._neo_mc("21.0.3"), "1.21")
        self.assertIsNone(gi._neo_mc("21.2.0-beta"))

    def test_api_install_runs_in_background(self):
        from core import game_installers as gi
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story", "executable": ""}])
        eng = engine(tmp)
        url = "https://cdn.example/a.zip"
        self._patch(gi, {gi.VS_API % "stable": {"1.21.5": {"windowsserver": {"urls": {"cdn": url}}}}},
                    {url: self._zip(["VintagestoryServer.exe"])})
        r = eng.api.process("server.install", {"server_id": "vs", "version": "1.21.5"})
        self.assertTrue(r.success, r.error)
        self.assertTrue(wait_for(lambda: (eng.api.process("server.install_status", {"server_id": "vs"}).data
                                          .get("result") or {}).get("ok")))
        st = eng.api.process("server.install_status", {"server_id": "vs"}).data
        self.assertEqual(st["percent"], 100.0)
        bad = eng.api.process("server.install", {"server_id": "vs", "version": "1;rm"})
        self.assertFalse(bad.success)
        r = eng.api.process("server.update_settings", {"server_id": "vs", "changes": {"javaPath": "x"}})
        self.assertFalse(r.success)
        r = eng.api.process("server.update_settings", {"server_id": "vs", "changes": {"javaMemoryMB": 6144}})
        self.assertTrue(r.success, r.error)


class FakeRcon:
    """Minimal Source RCON server: password "pw", answers "OK <command>"."""
    def __init__(self, replies=None):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.commands, self.replies = [], replies or {}
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(c,), daemon=True).start()

    def _client(self, c):
        def pkt(i, t, body):
            b = struct.pack("<ii", i, t) + body.encode() + b"\x00\x00"
            c.sendall(struct.pack("<i", len(b)) + b)
        try:
            while True:
                hdr = c.recv(4)
                if len(hdr) < 4:
                    return
                n = struct.unpack("<i", hdr)[0]
                data = b""
                while len(data) < n:
                    data += c.recv(n - len(data))
                i, t = struct.unpack("<ii", data[:8])
                body = data[8:].split(b"\x00")[0].decode()
                if t == 3:
                    pkt(i if body == "pw" else -1, 2, "")
                elif t == 2:
                    self.commands.append(body)
                    pkt(i, 0, self.replies.get(body, "OK " + body))
        except OSError:
            pass
        finally:
            c.close()

    def close(self):
        self.sock.close()


class RconRoutingTests(unittest.TestCase):

    def test_palworld_goes_over_rcon(self):
        from core import rcon_config
        rc = FakeRcon({"ShowPlayers": "name,playeruid,steamid\nBob,1,765\nAnn,2,766"})
        self.addCleanup(rc.close)
        tmp = make_env([{"id": "pal", "name": "Pal", "appId": "2394010", "stopTimeout": 2}])
        ini = tmp / "pal" / "Pal" / "Saved" / "Config" / "WindowsServer"
        ini.mkdir(parents=True)
        (ini / "PalWorldSettings.ini").write_text(
            "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(ServerName=\"x\",AdminPassword=\"pw\","
            "RCONEnabled=True,RCONPort=%d,ServerPlayerMaxNum=24)\n" % rc.port)
        rcon_config.forget()
        m = engine(tmp).manager
        self.assertEqual(m.get_rcon_info("pal")["port"], rc.port)
        m.start_server("pal")
        st = {s["id"]: s for s in m.get_all_statuses()}["pal"]
        self.assertTrue(st["consoleAvailable"])
        self.assertEqual(st["consoleVia"], "rcon")
        r = m.send_console_command("pal", "Info")
        self.assertEqual((r["via"], r["reply"], r["echoed"]), ("rcon", "OK Info", True))
        self.assertTrue(wait_for(lambda: any("[RCON] OK Info" in l for l in m.get_logs("pal", 50))))
        self.assertTrue(m.broadcast("pal", "Restart soon"))
        self.assertIn("Broadcast Restart soon", rc.commands)
        plugin = m.plugin_for(m.get_server("pal"))
        res = plugin._rcon_players(m.get_server("pal"))
        self.assertEqual((res["count"], res["max"]), (2, 24))
        m.stop_server("pal")
        self.assertIn("Save", rc.commands)
        self.assertIn("DoExit", rc.commands)

    def test_no_rcon_gives_setup_hint(self):
        from core import rcon_config
        rcon_config.forget()
        tmp = make_env([{"id": "ark", "name": "ARK", "appId": "2430930", "stopTimeout": 2}])
        m = engine(tmp).manager
        m.start_server("ark")
        try:
            st = {s["id"]: s for s in m.get_all_statuses()}["ark"]
            self.assertFalse(st["consoleAvailable"])
            self.assertIn("RCONEnabled", st["consoleHint"])
            with self.assertRaises(RuntimeError) as cm:
                m.send_console_command("ark", "ListPlayers")
            self.assertIn("RCON", str(cm.exception))
        finally:
            m.stop_server("ark")

    def test_detection_per_game(self):
        from core import rcon_config as rc
        rc.forget()
        d = Path(tempfile.mkdtemp())
        srv = {"id": "a", "appId": "2430930", "directory": str(d),
               "launchArgs": "TheIsland_WP?listen?RCONEnabled=True?ServerAdminPassword=sekrit -RCONPort=27021"}
        self.assertEqual(rc.info(srv)["port"], 27021)
        self.assertEqual(rc.info(srv)["password"], "sekrit")
        (d / "server.properties").write_text("enable-rcon=true\nrcon.port=25580\nrcon.password=mc\n")
        rc.forget()
        mcs = {"id": "m", "directory": str(d), "executable": str(d / "server.jar")}
        self.assertEqual(rc.info(mcs)["port"], 25580)
        sq = d / "SquadGame" / "ServerConfig"
        sq.mkdir(parents=True)
        (sq / "Rcon.cfg").write_text("Port=21115\nPassword=sq\n")
        self.assertEqual(rc.info({"id": "s", "appId": "403240", "directory": str(d)})["port"], 21115)
        self.assertIsNone(rc.info({"id": "v", "appId": "896660", "directory": str(d)}))
        self.assertEqual(rc.parse_players("2430930", "0. Bob Smith, 0002abc\n1. Ann, 0003def"), ["Bob Smith", "Ann"])
        self.assertEqual(rc.parse_players("2430930", "No Players Connected"), [])
        self.assertEqual(rc.parse_players("403240", "----- Active Players -----\nID: 0 | Online IDs: EOS: x | "
                                                     "Name: Zed | Team ID: 1 | Squad ID: N/A"), ["Zed"])


class FakeBattlEye:
    """BattlEye RCon over UDP: password "pw"; "players" gets a 2-part reply."""
    def __init__(self):
        from core import battleye as be
        self.be = be
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.commands = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        be = self.be
        while True:
            try:
                data, addr = self.sock.recvfrom(4096)
            except OSError:
                return
            p = be._parse(data)
            if not p:
                continue
            typ, rest = p
            if typ == 0:
                self.sock.sendto(be._packet(b"\x00" + (b"\x01" if rest == b"pw" else b"\x00")), addr)
                self.sock.sendto(be._packet(b"\x02\x00RCon admin #0 logged in"), addr)   # must be acked
            elif typ == 1:
                seq, cmd = rest[:1], rest[1:].decode()
                self.commands.append(cmd)
                if cmd == "players":
                    a = ("Players on server:\n[#] [IP Address]:[Port] [Ping] [GUID] [Name]\n"
                         "--------------------------------------------------\n"
                         "0   1.2.3.4:2304   32   0123456789abcdef(OK) Bob\n").encode()
                    b = "1   5.6.7.8:2304   40   fedcba9876543210(OK) Ann Lee (Lobby)\n(2 players in total)".encode()
                    self.sock.sendto(be._packet(b"\x01" + seq + b"\x00\x02\x01" + b), addr)
                    self.sock.sendto(be._packet(b"\x01" + seq + b"\x00\x02\x00" + a), addr)
                else:
                    self.sock.sendto(be._packet(b"\x01" + seq), addr)

    def close(self):
        self.sock.close()


class BattlEyeTests(unittest.TestCase):
    def test_arma3_over_battleye(self):
        from core import rcon_config, battleye
        be = FakeBattlEye()
        self.addCleanup(be.close)
        self.assertEqual(battleye.parse_players(battleye.command("127.0.0.1", be.port, "pw", "players")),
                         ["Bob", "Ann Lee"])
        with self.assertRaises(battleye.BattlEyeError):
            battleye.command("127.0.0.1", be.port, "nope", "players", timeout=2)
        tmp = make_env([{"id": "a3", "name": "Arma", "appId": "233780", "stopTimeout": 2}])
        bed = tmp / "a3" / "BattlEye"
        bed.mkdir()
        (bed / "beserver_x64.cfg").write_text("RConPassword pw\nRConPort %d\n" % be.port)
        rcon_config.forget()
        m = engine(tmp).manager
        m.start_server("a3")
        self.assertEqual(m.send_console_command("a3", "#lock")["via"], "rcon")
        self.assertTrue(m.broadcast("a3", "Restart in 5"))
        res = m.plugin_for(m.get_server("a3"))._rcon_players(m.get_server("a3"))
        self.assertEqual(res["count"], 2)
        m.stop_server("a3")
        self.assertEqual(be.commands[:3], ["players", "#lock", "say -1 Restart in 5"])
        self.assertIn("#shutdown", be.commands)


class CheckupTests(unittest.TestCase):
    def test_checkup_items(self):
        from core import checkup, minecraft as mc
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story", "port": 42420},
                        {"id": "mc", "name": "MC", "port": 25565},
                        {"id": "pal", "name": "Pal", "appId": "2394010", "port": 8211},
                        {"id": "gone", "name": "Gone", "port": 42420, "executable": "nothere.exe"}])
        servers = json.loads((tmp / "servers.json").read_text())
        (tmp / "mc" / "server.jar").write_text("j")
        servers[1]["executable"] = str(tmp / "mc" / "server.jar")
        (tmp / "servers.json").write_text(json.dumps(servers))
        m = engine(tmp).manager
        orig = mc.find_javas
        mc.find_javas = lambda extra=None: []
        self.addCleanup(lambda: setattr(mc, "find_javas", orig))
        res = checkup.run(m, firewall=False, force=True)
        titles = {(i["server"], i["title"]) for i in res["items"]}
        self.assertIn(("MC", "Java 21 isn't installed"), titles)
        self.assertIn(("MC", "Minecraft EULA not accepted"), titles)
        self.assertIn(("Pal", "RCON is off"), titles)
        self.assertIn(("VS", "No backups yet"), titles)
        self.assertIn(("Gone", "Server program not found"), titles)
        self.assertTrue(any("42420" in t and "share port" in t for _, t in titles))
        self.assertEqual(res["items"][0]["level"], "problem")
        m.backup_server_world("vs")
        res = checkup.run(m, firewall=False, force=True)
        self.assertNotIn(("VS", "No backups yet"), {(i["server"], i["title"]) for i in res["items"]})


class InstallSafetyTests(unittest.TestCase):
    def test_never_unpacks_over_other_files(self):
        from core import game_installers as gi
        d = Path(tempfile.mkdtemp())
        (d / "BaoLauncher.exe").write_text("x")
        srv = {"id": "v", "type": "vintage-story", "directory": str(d), "executable": None}
        self.assertEqual(gi.vs_install_state(srv), "custom")
        with self.assertRaises(gi.InstallError):
            gi.install_vintage_story(srv)
        with self.assertRaises(gi.InstallError):
            gi.install_minecraft({"directory": str(d)})
        self.assertFalse(gi.folder_is_empty(str(d)))
        e = Path(tempfile.mkdtemp())
        (e / "logs").mkdir()
        self.assertTrue(gi.folder_is_empty(str(e)))
        self.assertTrue(gi.folder_is_empty(str(e / "new")))
        self.assertEqual(gi.vs_install_state({"directory": str(e)}), "empty")

    def test_status_unreachable_and_wrong_exe(self):
        from core import checkup
        tmp = make_env([{"id": "a", "name": "A", "type": "vintage-story", "executable": None},
                        {"id": "b", "name": "B", "type": "vintage-story", "executable": None}])
        (tmp / "a" / "BaoLauncher.exe").write_text("x")
        servers = json.loads((tmp / "servers.json").read_text())
        servers[1]["directory"] = "\\\\NOPE-PC\\d\\vs" if os.name == "nt" else "/nonexistent/share/vs"
        (tmp / "servers.json").write_text(json.dumps(servers))
        m = engine(tmp).manager
        st = {s["id"]: s for s in m.get_all_statuses()}
        self.assertIsNone(st["a"]["installed"])
        self.assertEqual(st["a"]["exeMissing"], "VintagestoryServer.exe")
        self.assertIsNone(st["b"]["installed"])
        titles = {(i["server"], i["title"]) for i in checkup.run(m, firewall=False, force=True)["items"]}
        self.assertIn(("B", "Can't reach its folder"), titles)
        self.assertIn(("A", "Server program not found"), titles)
        self.assertNotIn(("A", "Game files aren't installed"), titles)


class PresetRefreshTests(unittest.TestCase):
    def test_unedited_copy_follows_new_builds(self):
        import core.game_catalog as gc
        app, bundle = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        old = (gc.APP_DIR, gc.BUNDLE_DIR)
        gc.APP_DIR, gc.BUNDLE_DIR = app, bundle
        self.addCleanup(lambda: (setattr(gc, "APP_DIR", old[0]), setattr(gc, "BUNDLE_DIR", old[1])))
        v1 = {"presets": [{"name": "ZZ Test A", "appId": "1"}]}
        v2 = {"presets": [{"name": "ZZ Test A", "appId": "2"}]}
        (bundle / "presets.json").write_text(json.dumps(v1))
        gc.load_extra_presets()
        self.assertEqual(json.loads((app / "presets.json").read_text()), v1)
        (bundle / "presets.json").write_text(json.dumps(v2))       # new build
        gc.load_extra_presets()
        self.assertEqual(gc.GAME_PRESETS["ZZ Test A"]["appId"], "2")
        mine = {"presets": [{"name": "ZZ Test A", "appId": "9"}]}   # the user edits it
        (app / "presets.json").write_text(json.dumps(mine))
        (bundle / "presets.json").write_text(json.dumps(v1))
        gc.load_extra_presets()
        self.assertEqual(gc.GAME_PRESETS["ZZ Test A"]["appId"], "9")
        gc.GAME_PRESETS.pop("ZZ Test A", None)


class ConfigFormTests(unittest.TestCase):
    """The full settings editor: every format keeps comments/order and only changes what changed."""

    def _env(self, srv, files):
        tmp = make_env([dict({"id": "s", "name": "S"}, **srv)])
        d = tmp / "s"
        for rel, text in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text.encode("utf-8"))
        return engine(tmp).manager, d

    def _fields(self, form):
        return {f["key"]: f for g in form["groups"] for f in g["fields"]}

    def test_minecraft_properties(self):
        from core import config_forms as cf
        props = "#Minecraft server properties\r\n#comment\r\nmotd=A Minecraft Server\r\nmax-players=20\r\npvp=true\r\ndifficulty=easy\r\nrcon.password=hunter2\r\n"
        m, d = self._env({"executable": "server.jar"}, {"server.jar": "j", "server.properties": props})
        files = cf.files(m, "s")
        self.assertEqual(files[0]["id"], "known:0")
        form = cf.form(m, "s", "known:0")
        f = self._fields(form)
        self.assertEqual((f["max-players"]["type"], f["max-players"]["value"], f["max-players"]["label"]), ("int", 20, "Max players"))
        self.assertEqual(f["pvp"]["type"], "bool")
        self.assertEqual(f["difficulty"]["type"], "select")
        self.assertEqual((f["rcon.password"]["type"], f["rcon.password"]["value"], f["rcon.password"]["set"]), ("password", "", True))
        self.assertIn("Basics", [g["name"] for g in form["groups"]])
        self.assertTrue(any(s["key"] == "view-distance" for s in form["suggestions"]))
        vd = next(s for s in form["suggestions"] if s["key"] == "view-distance")
        res = cf.save(m, "s", "known:0", {f["max-players"]["id"]: "32", f["pvp"]["id"]: False,
                                         f["difficulty"]["id"]: "hard", f["rcon.password"]["id"]: "",
                                         vd["id"]: 8})
        self.assertEqual(sorted(res["saved"]), ["difficulty", "max-players", "pvp", "view-distance"])
        out = (d / "server.properties").read_bytes().decode()
        self.assertIn("#comment\r\nmotd=A Minecraft Server\r\nmax-players=32\r\npvp=false\r\ndifficulty=hard\r\nrcon.password=hunter2\r\n", out)
        self.assertIn("view-distance=8", out)
        self.assertTrue((d / "server.properties.bak").exists())
        with self.assertRaises(cf.FormError):
            cf.save(m, "s", "known:0", {f["max-players"]["id"]: "5000"})
        with self.assertRaises(cf.FormError):
            cf.save(m, "s", "known:0", {f["difficulty"]["id"]: "ultra"})

    def test_vintage_story_json(self):
        from core import config_forms as cf
        cfg = {"ServerName": "Bao", "Port": 42420, "MaxClients": 16, "Password": None, "AdvertiseServer": False,
               "WorldConfig": {"Seed": "123", "WorldName": "W", "WorldConfiguration": {"temporalStorms": "sometimes"}},
               "ModPaths": ["Mods", "Data/Mods"], "Roles": [{"Code": "admin"}]}
        m, d = self._env({"type": "vintage-story", "launchArgs": "--dataPath Data"},
                         {"Data/serverconfig.json": json.dumps(cfg, indent=2)})
        form = cf.form(m, "s", "known:0")
        f = self._fields(form)
        self.assertEqual(f["MaxClients"]["label"], "Max players")
        self.assertEqual(f["Password"]["type"], "password")
        self.assertFalse(f["Password"]["set"])
        self.assertTrue(f["Roles"]["readonly"])
        self.assertEqual(f["ModPaths"]["value"], "Mods, Data/Mods")
        self.assertEqual(f["temporalStorms"]["section"], "WorldConfig.WorldConfiguration")
        cf.save(m, "s", "known:0", {f["MaxClients"]["id"]: 24, f["Password"]["id"]: "secret",
                                   f["temporalStorms"]["id"]: "often", f["ModPaths"]["id"]: "Mods",
                                   f["AdvertiseServer"]["id"]: True})
        new = json.loads((d / "Data" / "serverconfig.json").read_text())
        self.assertEqual(new["MaxClients"], 24)
        self.assertEqual(new["Password"], "secret")
        self.assertIs(new["AdvertiseServer"], True)
        self.assertEqual(new["WorldConfig"]["WorldConfiguration"]["temporalStorms"], "often")
        self.assertEqual(new["ModPaths"], ["Mods"])
        self.assertEqual(new["Roles"], [{"Code": "admin"}])
        self.assertEqual(list(new)[:3], ["ServerName", "Port", "MaxClients"])   # order kept

    def test_palworld_tuple_and_ark_ini(self):
        from core import config_forms as cf
        pal = ('[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(Difficulty=None,ExpRate=1.000000,'
               'ServerName="Default Palworld Server",AdminPassword="",bIsPvP=False,RCONEnabled=False,'
               'CrossplayPlatforms=(Steam,Xbox),ServerPlayerMaxNum=32)\n')
        m, d = self._env({"appId": "2394010"}, {"Pal/Saved/Config/WindowsServer/PalWorldSettings.ini": pal})
        f = self._fields(cf.form(m, "s", "known:0"))
        self.assertEqual(f["ExpRate"]["label"], "XP rate")
        self.assertTrue(f["CrossplayPlatforms"]["readonly"])
        cf.save(m, "s", "known:0", {f["ExpRate"]["id"]: 2.5, f["ServerName"]["id"]: "Bao, Pals",
                                   f["bIsPvP"]["id"]: True, f["RCONEnabled"]["id"]: True,
                                   f["AdminPassword"]["id"]: "pw"})
        out = (d / "Pal/Saved/Config/WindowsServer/PalWorldSettings.ini").read_text()
        self.assertIn('ExpRate=2.500000,ServerName="Bao, Pals",AdminPassword="pw",bIsPvP=True,RCONEnabled=True,'
                      'CrossplayPlatforms=(Steam,Xbox),ServerPlayerMaxNum=32)', out)
        from core import rcon_config
        rcon_config.forget()
        self.assertEqual(rcon_config.info(m.get_server("s"))["password"], "pw")

        gus = "[ServerSettings]\nXPMultiplier=1.000000\nServerPVE=False\n\n[SessionSettings]\nSessionName=My ARK\n"
        m2, d2 = self._env({"appId": "2430930"}, {"ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini": gus,
                                                  "ShooterGame/Saved/Config/WindowsServer/Game.ini":
                                                      "[/Script/ShooterGame.ShooterGameMode]\n+ConfigOverrideItemMaxQuantity=(x)\n"})
        form = cf.form(m2, "s", "known:0")
        f = self._fields(form)
        tm = next(s for s in form["suggestions"] if s["key"] == "TamingSpeedMultiplier")
        cf.save(m2, "s", "known:0", {f["XPMultiplier"]["id"]: 3, f["SessionName"]["id"]: "Bao ARK", tm["id"]: 5})
        out = (d2 / "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini").read_text()
        self.assertEqual(out, "[ServerSettings]\nXPMultiplier=3.000000\nServerPVE=False\nTamingSpeedMultiplier=5.0\n\n"
                              "[SessionSettings]\nSessionName=Bao ARK\n")
        g = self._fields(cf.form(m2, "s", "known:1"))
        self.assertTrue(g["+ConfigOverrideItemMaxQuantity"]["readonly"])

    def test_xml_cfg_yaml_and_safety(self):
        from core import config_forms as cf
        xml = ('<?xml version="1.0"?>\n<ServerSettings>\n\t<property name="ServerName" value="My Game Host" />\n'
               '\t<property name="GameDifficulty" value="1" />\n</ServerSettings>\n')
        m, d = self._env({"appId": "294420"}, {"serverconfig.xml": xml,
                                               "plugins/config.yml": "# top\nsettings:\n  max: 10 # cap\n  name: 'x'\nlist:\n  - a\n",
                                               "cfg/server.cfg": '// c\nhostname "Bao GMod"\nsv_password ""\nsv_lan 0\n'})
        form = cf.form(m, "s", "known:0")
        f = self._fields(form)
        self.assertEqual(f["GameDifficulty"]["type"], "select")
        bm = next(s for s in form["suggestions"] if s["key"] == "BloodMoonFrequency")
        cf.save(m, "s", "known:0", {f["ServerName"]["id"]: 'Bao "7D"', f["GameDifficulty"]["id"]: "3", bm["id"]: 14})
        out = (d / "serverconfig.xml").read_text()
        self.assertIn('value="Bao &quot;7D&quot;"', out)
        self.assertIn('<property name="GameDifficulty" value="3" />', out)
        self.assertIn('\t<property name="BloodMoonFrequency" value="14" />\n</ServerSettings>', out)
        ids = {x["id"]: x for x in cf.files(m, "s")}
        self.assertIn("rel:plugins/config.yml", ids)
        y = self._fields(cf.form(m, "s", "rel:plugins/config.yml"))
        self.assertEqual(y["max"]["section"], "settings")
        cf.save(m, "s", "rel:plugins/config.yml", {y["max"]["id"]: 20, y["name"]["id"]: "it's: new"})
        self.assertEqual((d / "plugins/config.yml").read_text(),
                         "# top\nsettings:\n  max: 20 # cap\n  name: 'it''s: new'\nlist:\n  - a\n")
        import yaml as _y
        self.assertEqual(_y.safe_load((d / "plugins/config.yml").read_text())["settings"]["name"], "it's: new")
        c = self._fields(cf.form(m, "s", "rel:cfg/server.cfg"))
        cf.save(m, "s", "rel:cfg/server.cfg", {c["hostname"]["id"]: "Bao Box", c["sv_lan"]["id"]: 1})
        self.assertEqual((d / "cfg/server.cfg").read_text(), '// c\nhostname "Bao Box"\nsv_password ""\nsv_lan 1\n')
        for bad in ("rel:../../etc/passwd", "rel:start.bat", "known:9", "nope"):
            with self.assertRaises(cf.FormError):
                cf.form(m, "s", bad)

    def test_web_permissions(self):
        from core.web_auth import ROLE_PERMS
        for a in ("config.read", "config.list", "config.form", "config.form_save"):
            self.assertNotIn(a, ROLE_PERMS["operator"])
            self.assertIn(a, ROLE_PERMS["admin"])


class ModBrowseTests(unittest.TestCase):
    def test_descriptions_are_made_safe(self):
        from core.mod_browse import html_to_blocks, md_to_blocks, safe_img
        b = html_to_blocks("<h2>Hi</h2><p>One <b>two</b></p><script>alert(1)</script><ul><li>a</li></ul>"
                           "<img src='https://moddbcdn.vintagestory.at/x.png'><img src='http://moddbcdn.vintagestory.at/y.png'>"
                           "<img src='https://evil.example/z.png' onerror='x'>")
        self.assertEqual(b, [{"t": "h", "text": "Hi"}, {"t": "p", "text": "One two"}, {"t": "li", "text": "a"},
                             {"t": "img", "src": "https://moddbcdn.vintagestory.at/x.png"}])
        m = md_to_blocks("# Title\n\nSome **bold** [link](https://x.y) text\nmore\n\n- item\n![s](https://cdn.modrinth.com/a.png)\n<img src=\"https://evil.example/b.png\">")
        self.assertEqual(m[0], {"t": "h", "text": "Title"})
        self.assertEqual(m[1], {"t": "p", "text": "Some bold link text more"})
        self.assertEqual(m[2], {"t": "li", "text": "item"})
        self.assertEqual(m[3], {"t": "img", "src": "https://cdn.modrinth.com/a.png"})
        self.assertEqual(len(m), 4)
        self.assertIsNone(safe_img("javascript:alert(1)"))
        self.assertEqual(safe_img("https://a.cdn.modrinth.com/i.png"), "https://a.cdn.modrinth.com/i.png")

    def test_vs_search_and_details(self):
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story"}])
        eng = engine(tmp)
        m = eng.manager
        seen = {}
        def fake_search(text="", orderby="downloads", orderdirection="desc", limit=50):
            seen["order"] = orderby
            return [{"modid": 890, "name": "Carry On", "summary": "Carry", "author": "N", "downloads": 5,
                     "logo": "https://moddbcdn.vintagestory.at/l.png", "tags": ["QoL"], "modidstrs": ["carryon"],
                     "urlalias": "carryon"}]
        m.vs_search_mods = fake_search
        m.vs_server_version = lambda sid: "1.21.5"
        m.vs_get_mod = lambda mid: {"modid": 890, "name": "Carry On", "text": "<p>Hi</p>", "urlalias": "carryon",
                                    "releases": [{"modversion": "2.0", "tags": ["1.21.5"], "modidstr": "carryon"}]}
        r = eng.api.process("mods.search", {"server_id": "vs", "text": "carry", "sort": "trending"})
        self.assertTrue(r.success, r.error)
        hit = r.data["results"][0]
        self.assertEqual(seen["order"], "trendingpoints")
        self.assertEqual(hit["icon"], "https://moddbcdn.vintagestory.at/l.png")
        self.assertIn("carryon", hit["match_ids"])
        d = eng.api.process("mods.details", {"server_id": "vs", "mod": "890"})
        self.assertTrue(d.success, d.error)
        self.assertEqual(d.data["release"]["match"], "exact")
        self.assertEqual(d.data["blocks"], [{"t": "p", "text": "Hi"}])


class PortPlanTests(unittest.TestCase):
    def test_clash_fix_and_write(self):
        from core import port_plan
        tmp = make_env([
            {"id": "a", "name": "ASE 1", "appId": "376030", "launchArgs": "TheIsland?listen"},
            {"id": "b", "name": "ASE 2", "appId": "376030", "launchArgs": "Ragnarok?listen"},
            {"id": "c", "name": "Conan", "appId": "443030", "launchArgs": "-log"},
            {"id": "v1", "name": "VH1", "appId": "896660", "launchArgs": "-port 2456"},
            {"id": "v2", "name": "VH2", "appId": "896660", "launchArgs": "-port 2457"},
            {"id": "mc", "name": "MC", "executable": "x/server.jar", "launchArgs": "nogui"}])
        m = engine(tmp).manager
        d = {s["id"]: Path(s["directory"]) for s in m.load_servers()}
        (d["mc"] / "server.properties").write_text("server-port=7777\n")
        pairs = port_plan.clash_pairs(m)
        names = sorted(tuple(p["names"]) for p in pairs)
        self.assertIn(("ASE 1", "ASE 2"), names)       # 7777/27015/27020 all shared
        self.assertIn(("VH1", "VH2"), names)           # 2456-2458 overlaps 2457-2459
        self.assertTrue(any("Conan" in n for n in names))
        res = port_plan.fix_all(m, check_os=False)
        self.assertEqual(port_plan.clash_pairs(m), [])
        self.assertEqual(res["skipped"], [])
        b = m.get_server("b")
        self.assertRegex(b["launchArgs"], r"\?Port=\d+")
        self.assertNotIn("Port=7777", b["launchArgs"])
        self.assertEqual(m.get_server("a")["launchArgs"], "TheIsland?listen")   # first one keeps its defaults
        v2 = port_plan.rows(m, m.get_server("v2"))[0]
        self.assertGreaterEqual(v2["port"], 2459)
        # RCON is never opened in the firewall
        rc = next(r for r in port_plan.rows(m, b) if r["role"] == "rcon")
        self.assertTrue(rc["local"])
        self.assertNotIn(rc["port"], m.firewall_ports("b"))
        self.assertIn(b["port"], m.firewall_ports("b"))

    def test_pending_and_start_check(self):
        from core import port_plan
        tmp = make_env([
            {"id": "vs1", "name": "VS1", "type": "vintage-story"},
            {"id": "vs2", "name": "VS2", "type": "vintage-story"}])
        m = engine(tmp).manager
        res = port_plan.apply(m, "vs2", check_os=False)
        self.assertEqual(res["pending"], ["Game"])                # serverconfig.json not made yet
        self.assertEqual(m.get_server("vs2")["portsPending"], {"game": 42422})
        self.assertEqual(port_plan.clash_pairs(m), [])
        data = Path(m.get_server("vs2")["directory"]) / "Data"
        data.mkdir()
        (data / "serverconfig.json").write_text('{"Port": 42420, "ServerName": "x"}')
        self.assertEqual(port_plan.apply_pending(m, m.get_server("vs2")), ["Game"])
        self.assertEqual(json.loads((data / "serverconfig.json").read_text())["Port"], 42422)
        self.assertNotIn("portsPending", m.get_server("vs2"))
        # start check: blocked only by a *running* BaoBun server on the same port
        m.is_running = lambda sid: sid == "vs1"
        (data / "serverconfig.json").write_text('{"Port": 42420}')
        chk = port_plan.start_check(m, m.get_server("vs2"), check_os=False)
        self.assertTrue(chk["block"] and "VS1" in chk["block"][0])
        m.is_running = lambda sid: False
        self.assertEqual(port_plan.start_check(m, m.get_server("vs2"), check_os=False)["block"], [])

    def test_rcon_setup_and_target_check(self):
        from core import rcon_setup, rcon_config
        tmp = make_env([
            {"id": "pw", "name": "PW", "appId": "2394010"},
            {"id": "ark", "name": "ARK", "appId": "2430930",
             "launchArgs": "TheIsland_WP?listen?ServerAdminPassword=keepme"},
            {"id": "a3", "name": "A3", "appId": "233780", "launchArgs": "-port=2302"}])
        m = engine(tmp).manager
        d = {s["id"]: Path(s["directory"]) for s in m.load_servers()}
        (d["pw"] / "DefaultPalWorldSettings.ini").write_text(
            '[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(Difficulty=None,AdminPassword="",RCONEnabled=False,RCONPort=25575)\n')
        r = rcon_setup.enable(m, "pw")
        self.assertEqual(len(r["password"]), 20)
        info = rcon_config.info(m.get_server("pw"), max_age=0)
        self.assertEqual(info["password"], r["password"])
        self.assertEqual(info["port"], 25575)
        again = rcon_setup.enable(m, "pw")
        self.assertIsNone(again["password"])          # never replaces a password
        self.assertTrue(again["kept_password"])
        k = rcon_setup.enable(m, "ark")
        self.assertIsNone(k["password"])
        args = m.get_server("ark")["launchArgs"]
        self.assertIn("RCONEnabled=True", args)
        self.assertIn("ServerAdminPassword=keepme", args)
        self.assertEqual(rcon_config.info(m.get_server("ark"), max_age=0)["password"], "keepme")
        b = rcon_setup.enable(m, "a3")
        cfg = (d["a3"] / "BattlEye" / "beserver_x64.cfg").read_text()
        self.assertIn("RConPassword " + b["password"], cfg)
        self.assertIn("RConPort 2310", cfg)
        # RCON refuses to talk to a port another BaoBun server owns
        m.port_owner = lambda port: ("ark", {123})
        with self.assertRaises(RuntimeError):
            m._rcon_target_ok("pw", 25575)
        m.port_owner = lambda port: ("pw", {123})
        m._rcon_target_ok("pw", 25575)
        m.port_owner = lambda port: (None, set())
        m._rcon_target_ok("pw", 25575)
        m.port_owner = lambda port: (None, {999})
        m._rcon_target_ok("pw", 25575)               # some other program: allowed (only BaoBun clashes refuse)
        m._server_pid = lambda sid: 4242
        m._rcon_target_ok("pw", 25575)               # helper processes / detached .bat launches are allowed

    def test_ports_api_perms(self):
        from core.web_auth import DiscordAuth, ROLE_PERMS
        self.assertIn("ports.plan", ROLE_PERMS["operator"])
        for a in ("ports.fix", "ports.set", "rcon.setup"):
            self.assertNotIn(a, ROLE_PERMS["operator"])
            self.assertIn(a, ROLE_PERMS["admin"])
        tmp = make_env([{"id": "v1", "name": "VH1", "appId": "896660", "launchArgs": "-port 2456"},
                        {"id": "v2", "name": "VH2", "appId": "896660", "launchArgs": "-port 2456"}])
        eng = engine(tmp)
        r = eng.api.process("ports.plan", {})
        self.assertTrue(r.success, r.error)
        self.assertEqual(len(r.data["clashes"]), 1)
        r = eng.api.process("ports.set", {"server_id": "v2", "ports": {"game": 2470}})
        self.assertTrue(r.success, r.error)
        self.assertIn("-port 2470", eng.manager.get_server("v2")["launchArgs"])
        self.assertEqual(eng.api.process("ports.plan", {}).data["clashes"], [])
        bad = eng.api.process("ports.set", {"server_id": "v2", "ports": {"game": 80}})
        self.assertFalse(bad.success)


class NightlyTests(unittest.TestCase):
    class Tracker:
        def __init__(self, recent, known):
            self.recent, self.known = recent, known
        def has_history(self, sid):
            return sid in self.known
        def check_consecutive_inactive_days(self, sid, threshold=2):
            return sid not in self.recent

    def test_due_window_and_once_a_day(self):
        from core import nightly
        from datetime import datetime
        m = engine(make_env([{"id": "a", "name": "A"}])).manager
        at = lambda h, mi: datetime(2026, 11, 4, h, mi).timestamp()
        self.assertFalse(nightly.due(m, at(5, 1)))                 # off by default
        m.set_setting("nightly_restart_enabled", True)
        m.set_setting("nightly_restart_time", "05:00")
        self.assertFalse(nightly.due(m, at(4, 59)))
        self.assertTrue(nightly.due(m, at(5, 1)))
        self.assertFalse(nightly.due(m, at(8, 30)))                # PC was off at 5: don't reboot at 8:30
        nightly.write_state(m, last_date="2026-11-04")
        self.assertFalse(nightly.due(m, at(5, 30)))                # once a day
        self.assertEqual(datetime.fromtimestamp(nightly.next_run(m, at(5, 30))).day, 5)

    def test_run_plan_and_boot(self):
        from core import nightly
        tmp = make_env([{"id": "busy", "name": "Busy"}, {"id": "dead", "name": "Dead"},
                        {"id": "new", "name": "New"}, {"id": "off", "name": "Off"}])
        m = engine(tmp).manager
        running = {"busy", "dead", "new"}
        events = []
        m.is_running = lambda sid: sid in running
        m.player_count = lambda sid: 0
        m.warn_players = lambda ids, action="", minutes=None: events.append(("warn", sorted(ids), minutes))
        m.stop_all = lambda user="": (events.append(("stop", user)), running.clear())
        m.backup_server_world = lambda sid, reason="": events.append(("backup", sid))
        tracker = self.Tracker(recent={"busy"}, known={"busy", "dead"})
        notes = []
        res = nightly.run(m, tracker=tracker, notify=lambda t, x: notes.append(t), log=lambda *_: None,
                          reboot=lambda: True)
        self.assertTrue(res["rebooted"])
        self.assertEqual(sorted(res["bring_back"]), ["busy", "new"])  # 'new' has no history yet
        self.assertEqual(res["sleeping"], ["dead"])
        self.assertEqual(events[0], ("warn", ["busy", "dead", "new"], [15, 5, 1]))
        self.assertIn(("stop", "nightly"), events)
        self.assertEqual(sorted(e[1] for e in events if e[0] == "backup"), ["busy", "dead", "new"])
        self.assertTrue(notes and "15 minutes" in notes[0])
        plan = nightly.take_boot_plan(m)
        self.assertEqual(sorted(plan["bring_back"]), ["busy", "new"])
        self.assertIsNone(nightly.take_boot_plan(m))                  # only once
        # Windows refused to restart: servers come straight back
        running.update({"busy", "dead"})
        started = []
        nightly.write_state(m, last_date=None)
        res = nightly.run(m, tracker=tracker, log=lambda *_: None, reboot=lambda: False, start=started.append)
        self.assertFalse(res["rebooted"])
        self.assertEqual(started, ["busy"])
        self.assertIsNone(nightly.take_boot_plan(m))

    def test_maintenance_blocks_starts(self):
        m = engine(make_env([{"id": "a", "name": "A"}])).manager
        m.maintenance_until = time.time() + 60
        with self.assertRaises(RuntimeError):
            m.start_server("a")
        m.maintenance_until = 0

    def test_boot_task_xml(self):
        from core import boot_task
        import xml.dom.minidom
        x = boot_task.task_xml(r"C:\BaoBun & Co\BaoBunCP.exe", "--agent", r"C:\BaoBun & Co", user="PC\\me")
        doc = xml.dom.minidom.parseString(x.replace('encoding="UTF-16"', 'encoding="UTF-8"').encode("utf-8"))
        self.assertEqual(doc.getElementsByTagName("LogonType")[0].firstChild.data, "S4U")
        self.assertEqual(doc.getElementsByTagName("ExecutionTimeLimit")[0].firstChild.data, "PT0S")
        self.assertEqual(doc.getElementsByTagName("Priority")[0].firstChild.data, "4")
        self.assertEqual(doc.getElementsByTagName("Command")[0].firstChild.data, r"C:\BaoBun & Co\BaoBunCP.exe")
        p = boot_task.write_xml(["C:/x/BaoBunCP.exe", "--agent"], "C:/x")
        self.assertTrue(open(p, "rb").read().startswith(b"\xff\xfe"))   # UTF-16 for schtasks
        os.remove(p)
        self.assertIn("/XML", boot_task.install_commands("t.xml")[0])


class ArkClusterTests(unittest.TestCase):
    def _cluster(self):
        tmp = make_env([])
        inst = tmp / "ASA"
        (inst / "ShooterGame" / "Saved" / "Config" / "WindowsServer").mkdir(parents=True)
        m = engine(tmp).manager
        made = m.setup_ark_cluster(str(inst), "Bao", ["The Island", "Ragnarok", "Svartalfheim"],
                                   settings={"mods": "111,222", "adminPassword": "adm"})
        return m, inst, {e["arkFriendly"]: e["id"] for e in made}

    def test_parity_check_sync_and_preflight(self):
        from core import ark_cluster
        m, inst, ids = self._cluster()
        res = ark_cluster.check(m, ids["Ragnarok"])
        self.assertFalse([i for i in res["issues"] if i["level"] == "problem"], res["issues"])
        self.assertEqual(ark_cluster.map_mods(m.get_server(ids["Svartalfheim"])), ["111", "222"])  # map mod hidden
        # someone edits one map by hand: drops a mod and adds a flag
        servers = m.load_servers()
        for s in servers:
            if s["id"] == ids["Ragnarok"]:
                s["launchArgs"] = s["launchArgs"].replace("-mods=111,222", "-mods=111") + " -NoBattlEye"
        m.save_servers(servers)
        res = ark_cluster.check(m, ids["Ragnarok"])
        prob = [i for i in res["issues"] if i["level"] == "problem"]
        self.assertTrue(prob and "missing 222" in prob[0]["title"])
        self.assertEqual(prob[0]["action"], "sync_maps")
        # starting the map puts the mods back (and keeps the extra flag)
        changed = ark_cluster.preflight(m, m.get_server(ids["Ragnarok"]))
        self.assertTrue(changed)
        args = m.get_server(ids["Ragnarok"])["launchArgs"]
        self.assertIn("-mods=111,222", args)
        self.assertIn("-NoBattlEye", args)
        self.assertEqual(ark_cluster.preflight(m, m.get_server(ids["Ragnarok"])), [])

    def test_apply_keeps_ports_flags_and_save_folder(self):
        m, inst, ids = self._cluster()
        servers = m.load_servers()
        for s in servers:
            if s["id"] == ids["The Island"]:
                s["launchArgs"] = s["launchArgs"].replace("-Port=7777", "-Port=7801") + " -ClusterDirOverride=D:/c"
                s["launchArgs"] = s["launchArgs"].replace("?listen", "?listen?RCONEnabled=True")
        m.save_servers(servers)
        before = m.get_server(ids["The Island"])["launchArgs"]
        import re
        save = re.search(r"-AltSaveDirectoryName=(\S+)", before).group(1)
        st = dict(m.get_cluster_settings("Bao"))
        st["serverNames"] = {"The Island": "Totally New Name"}
        m.apply_cluster_settings("Bao", st)
        after = m.get_server(ids["The Island"])["launchArgs"]
        self.assertIn("-Port=7801", after)
        self.assertIn("-ClusterDirOverride=D:/c", after)
        self.assertIn("RCONEnabled=True", after)
        self.assertIn("SessionName=TotallyNewName", after)
        self.assertIn("-AltSaveDirectoryName=" + save, after)     # renaming never moves the world
        self.assertEqual(m.get_cluster_settings("Bao")["adminPassword"], "adm")
        # port plan reads -Port= flags on ASA cluster maps
        from core import port_plan
        rows = {r["role"]: r["port"] for r in port_plan.rows(m, m.get_server(ids["The Island"]))}
        self.assertEqual(rows["game"], 7801)
        self.assertEqual(port_plan.clash_pairs(m), [])

    def test_spawns_loot_rules_roundtrip(self):
        from core import ark_cluster, ark_ini
        m, inst, ids = self._cluster()
        gi = inst / ark_ini.REL
        gi.write_text("[/script/shootergame.shootergamemode]\nbAllowFlyerSpeedLeveling=True\n"
                      "OverrideMaxExperiencePointsPlayer=10000\n\n[SomethingElse]\nKeep=1\n")
        sid = ids["Ragnarok"]
        ark_cluster.save_spawns(m, sid, [{"from": "Raptor_Character_BP_C", "to": ""},
                                         {"from": "Leech_Character_C", "to": "Dodo_Character_BP_C"}])
        r = ark_cluster.read_spawns(m, sid)
        self.assertEqual([(x["from_name"], x["to"]) for x in r["rules"]],
                         [("Raptor", ""), ("Leech", "Dodo_Character_BP_C")])
        loot = [{"crate": "SupplyCrate_Level03_C", "mode": "add", "picks_min": 1, "picks_max": 2,
                 "items": [{"cls": "PrimalItemResource_MetalIngot_C", "qty_min": 10, "qty_max": 50,
                            "quality_min": 0, "quality_max": 1, "blueprint": 0}]}]
        ark_cluster.save_loot(m, sid, loot)
        back = ark_cluster.read_loot(m, sid)["loot"][0]
        self.assertEqual(back["mode"], "add")
        self.assertEqual(back["items"][0]["name"], "Metal Ingot")
        self.assertEqual(back["items"][0]["qty_max"], 50)
        ark_cluster.save_rules(m, sid, {"TributeDinoExpirationSeconds": 2592000, "ServerPVE": True,
                                        "SupplyCrateLootQualityMultiplier": 2})
        text = gi.read_text()
        self.assertIn("bAllowFlyerSpeedLeveling=True", text)          # hand edits kept
        self.assertIn("[SomethingElse]\nKeep=1", text.replace("\r", ""))
        self.assertIn("SupplyCrateLootQualityMultiplier=2.0", text)
        self.assertEqual(text.count("[/script/shootergame.shootergamemode]"), 1)
        rules = {r["key"]: r["value"] for r in ark_cluster.read_rules(m, sid)}
        self.assertEqual(rules["TributeDinoExpirationSeconds"], "2592000")
        self.assertEqual(rules["ServerPVE"], "True")
        with self.assertRaises(ValueError):
            ark_cluster.save_loot(m, sid, [{"crate": "SupplyCrate_Level03_C", "items": []}])

    def test_two_installs_need_shared_cluster_dir(self):
        from core import ark_cluster
        m, inst, ids = self._cluster()
        other = inst.parent / "ASA2"
        (other / "ShooterGame" / "Saved" / "Config" / "WindowsServer").mkdir(parents=True)
        servers = m.load_servers()
        for s in servers:
            if s["id"] == ids["Ragnarok"]:
                s["directory"] = str(other)
        m.save_servers(servers)
        res = ark_cluster.check(m, ids["Ragnarok"])
        acts = [i["action"] for i in res["issues"]]
        self.assertIn("cluster_dir", acts)
        ark_cluster.fix(m, ids["Ragnarok"], "cluster_dir")
        self.assertNotIn("cluster_dir", [i["action"] for i in ark_cluster.check(m, ids["Ragnarok"])["issues"]])
        self.assertTrue(all("-ClusterDirOverride=" in s["launchArgs"] for s in m.load_servers()))
        # settings written once go to both installs
        ark_cluster.save_rules(m, ids["Ragnarok"], {"DestroyTamesOverLevelClamp": 450})
        self.assertIn("DestroyTamesOverLevelClamp=450", (other / "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini").read_text())
        self.assertNotIn("copy_settings", [i["action"] for i in ark_cluster.check(m, ids["Ragnarok"])["issues"]])

    def test_curseforge_browse(self):
        import core.mods_ids as mi
        m, inst, ids = self._cluster()
        eng_api = None
        from core.mods import ModManager
        mm = ModManager(m)
        sid = ids["Ragnarok"]
        with self.assertRaises(Exception) as cm:
            mm.search(sid, "dino")
        self.assertIn("API key", str(cm.exception))
        m.set_setting("curseforge_api_key", "test-key")
        calls = []
        def fake(url, data=None, headers=None, timeout=30, form=False):
            calls.append((url, headers))
            if "/games" in url:
                return {"data": [{"id": 83374, "name": "ARK: Survival Ascended", "slug": "ark-survival-ascended"}]}
            if "/mods/search" in url:
                return {"data": [{"id": 929420, "name": "Dino Storage", "summary": "Store dinos",
                                  "downloadCount": 1000, "authors": [{"name": "A"}],
                                  "logo": {"thumbnailUrl": "https://media.forgecdn.net/x.png"},
                                  "categories": [{"name": "QoL"}], "links": {"websiteUrl": "https://www.curseforge.com/ark-survival-ascended/mods/ds"}}]}
            if url.endswith("/description"):
                return {"data": "<p>Hello <img src=\"https://media.forgecdn.net/a.png\"></p>"}
            return {"data": {"id": 929420, "name": "Dino Storage", "summary": "Store dinos", "latestFiles": []}}
        mi.http_json, old = fake, mi.http_json
        try:
            hits = mm.search(sid, "dino", sort="updated")
            self.assertEqual(hits[0]["id"], "929420")
            self.assertEqual(hits[0]["icon"], "https://media.forgecdn.net/x.png")
            self.assertIn("gameId=83374", calls[-1][0])
            self.assertIn("sortField=3", calls[-1][0])
            self.assertEqual(calls[-1][1]["x-api-key"], "test-key")
            d = mm.details(sid, "929420")
            self.assertEqual(d["blocks"][0], {"t": "p", "text": "Hello"})
            self.assertIn("every map", d["cluster_note"])
        finally:
            mi.http_json = old

    def test_backup_includes_shared_cluster_folder(self):
        import zipfile
        from core import ark_cluster
        m, inst, ids = self._cluster()
        shared = inst.parent / "SharedCluster"
        st = dict(m.get_cluster_settings("Bao")); st["clusterDir"] = str(shared)
        m._save_cluster_settings("Bao", st)
        ark_cluster.fix(m, ids["Ragnarok"], "cluster_dir")
        (shared / "clusterid").mkdir(parents=True, exist_ok=True)
        (shared / "clusterid" / "123.arktribute").write_text("upload")
        r = m.backup_server_world(ids["Ragnarok"])
        names = zipfile.ZipFile(r["zip_path"]).namelist()
        self.assertTrue(any(n.endswith("123.arktribute") for n in names), names[:20])

    def test_api_permissions(self):
        from core.web_auth import ROLE_PERMS
        for a in ("ark.cluster", "ark.loot", "ark.spawns", "ark.rules", "ark.catalog"):
            self.assertIn(a, ROLE_PERMS["operator"])
        for a in ("ark.fix", "ark.loot_save", "ark.spawns_save", "ark.rules_save", "ark.wipe_wild"):
            self.assertNotIn(a, ROLE_PERMS["operator"])
            self.assertIn(a, ROLE_PERMS["admin"])


class CatalogTests(unittest.TestCase):
    def test_presets_complete(self):
        from core.game_catalog import GAME_PRESETS, CATEGORIES
        cats = {c[0] for c in CATEGORIES}
        self.assertGreater(len(GAME_PRESETS), 190)
        for name, p in GAME_PRESETS.items():
            self.assertIn(p.get("category", "other"), cats, name)
            if p.get("appId"):
                self.assertTrue(str(p["appId"]).isdigit(), name)
            if p.get("manual"):
                self.assertTrue(p["manual"]["url"].startswith("https://"), name)
        for n in ("SCUM", "HumanitZ", "The Isle (Evrima)", "Hytale", "FiveM (GTA V roleplay)", "Path of Titans"):
            self.assertIn(n, GAME_PRESETS)
        self.assertEqual(GAME_PRESETS["FiveM (GTA V roleplay)"]["args"], "+exec server.cfg")

    def test_find_server_exe(self):
        from core.game_catalog import find_server_exe
        d = Path(tempfile.mkdtemp())
        for rel in ("Engine/Binaries/Win64/CrashReportClient.exe", "TSSGame/Binaries/Win64/TSSGameServer-Win64-Shipping.exe",
                    "TSSGameServer.exe", "steamcmd/steamcmd.exe", "_CommonRedist/vcredist_x64.exe"):
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_bytes(b"MZ")
        self.assertTrue(find_server_exe(str(d)).endswith("TSSGameServer-Win64-Shipping.exe"))
        self.assertTrue(find_server_exe(str(d), "x/TSSGameServer.exe").endswith("TSSGameServer.exe"))
        e = Path(tempfile.mkdtemp()); (e / "readme.txt").write_text("x")
        self.assertIsNone(find_server_exe(str(e)))
        # a SteamCMD server whose preset path is wrong finds (and remembers) the real program
        tmp = make_env([{"id": "h", "name": "H", "type": "steamcmd", "appId": "2728330",
                         "executable": "nope/Server.exe"}])
        m = engine(tmp).manager
        dd = Path(m.get_server("h")["directory"])
        (dd / "TSSGame/Binaries/Win64").mkdir(parents=True)
        (dd / "TSSGame/Binaries/Win64/TSSGameServer-Win64-Shipping.exe").write_bytes(b"MZ")
        exe, _ = m._resolve_executable(m.get_server("h"))
        self.assertTrue(exe.endswith("TSSGameServer-Win64-Shipping.exe"))
        self.assertEqual(m.get_server("h")["executable"], exe)

    def test_hytale_launch(self):
        from core import hytale, minecraft
        d = Path(tempfile.mkdtemp()); (d / "Server").mkdir(); (d / "Server/HytaleServer.jar").write_bytes(b"PK")
        (d / "Assets.zip").write_bytes(b"PK")
        srv = {"type": "hytale", "directory": str(d), "executable": str(d / "Server/HytaleServer.jar"),
               "port": 5521, "javaPath": sys.executable}
        self.assertFalse(minecraft.looks_minecraft(srv))
        cmd = hytale.launch_command(srv, srv["executable"], "")
        text = cmd if isinstance(cmd, str) else " ".join(cmd)
        self.assertIn("--assets", text)
        self.assertIn("Assets.zip", text)
        self.assertIn("0.0.0.0:5521", text)
        (d / "Assets.zip").unlink()
        with self.assertRaises(FileNotFoundError):
            hytale.launch_command(srv, srv["executable"], "")


class DiscordBotTests(unittest.TestCase):
    def test_logic(self):
        from core import discord_logic as L
        self.assertEqual(L.button_spec({"name": "V", "state": "starting"})[1:], ("secondary", True))
        lbl, style, dis = L.button_spec({"name": "V", "state": "running", "isRunning": True, "playerCount": 2,
                                         "maxPlayers": 10})
        self.assertIn("(2/10)", lbl)
        self.assertEqual(L.button_spec({"name": "V", "state": "crashed"})[1], "danger")
        self.assertLessEqual(len(L.button_spec({"name": "X" * 200})[0]), 80)
        servers = [{"id": "a", "name": "ARK - Ragnarok"}, {"id": "b", "name": "ARK - The Island"},
                   {"id": "c", "name": "Valheim"}]
        self.assertEqual(L.find_server(servers, "valheim")[0]["id"], "c")
        self.assertEqual(L.find_server(servers, "rag")[0]["id"], "a")
        srv, cands = L.find_server(servers, "ark")
        self.assertIsNone(srv)
        self.assertEqual(len(cands), 2)
        self.assertEqual(L.autocomplete(servers, "isl")[0], ("ARK - The Island", "b"))
        cd = L.Cooldowns(20)
        cd.hit(1, now=100)
        self.assertEqual(cd.remaining(1, now=110), 10)
        self.assertEqual(cd.remaining(2, now=110), 0)
        prev = {"a": {"Bob"}}
        now = {"a": {"Bob", "Ann"}, "c": {"Zed"}}
        self.assertEqual(L.player_changes(prev, now, {"a": "Ragnarok"}), ["\U0001F3AE Ann joined **Ragnarok**"])
        self.assertEqual(L.player_changes({"a": {"Bob", "Ann"}}, {"a": {"Ann"}}, {"a": "R"}), ["\U0001F44B Bob left **R**"])
        msg = L.event_message("stopped", {"name": "Valheim", "user": "idle-stop"}, {"idleStopMinutes": 30})
        self.assertIn("nobody played for 30 minutes", msg)
        self.assertIsNone(L.event_message("started", {"name": "V", "user": "discord:bob"}, {}))
        self.assertIn("after the PC booted", L.event_message("started", {"name": "V", "user": "smart-boot"}, {}))
        self.assertTrue(L.too_many_running([{"isRunning": True}, {"state": "starting"}], 2))
        self.assertFalse(L.too_many_running([{"isRunning": True}], 0))

    def test_ready_state(self):
        from core import discord_logic as L

        class M:
            st = {"id": "a", "state": "starting", "isRunning": True}
            def get_all_statuses(self):
                return [dict(self.st)]
            def get_server(self, sid):
                return {"id": "a"}
        m = M()
        self.assertEqual(L.ready_state(m, "a", 0, now=10), "waiting")
        M.st = {"id": "a", "state": "running", "isRunning": True, "playerCount": 0}
        self.assertEqual(L.ready_state(m, "a", 0, now=10), "ready")
        M.st = {"id": "a", "state": "crashed", "isRunning": False}
        self.assertEqual(L.ready_state(m, "a", 0, now=30), "failed")
        M.st = {"id": "a", "state": "running", "isRunning": True}
        self.assertEqual(L.ready_state(m, "a", 0, now=30), "waiting")
        self.assertEqual(L.ready_state(m, "a", 0, now=90), "ready")

    def test_bot_actions(self):
        import asyncio, importlib, types
        import fake_discord
        fake_discord.install()
        import discord_bot
        importlib.reload(discord_bot)
        calls = []

        class Mgr:
            def __init__(self):
                self.sts = [{"id": "v", "name": "Valheim", "state": "stopped", "isRunning": False},
                            {"id": "r", "name": "Rust", "state": "running", "isRunning": True, "playerCount": 3,
                             "maxPlayers": 50, "players": [{"name": "Bob"}]}]
            def get_all_statuses(self):
                return [dict(s) for s in self.sts]
            def get_server(self, sid):
                return {"id": sid}
            def start_server(self, sid, user):
                calls.append(("start", sid, user))
                return {"status": "started", "pid": 1}
            def stop_server(self, sid, user):
                calls.append(("stop", sid, user))
                return {"status": "stopped", "graceful": True}
            def restart_server(self, sid, user):
                calls.append(("restart", sid, user))
        mgr = Mgr()
        discord_bot._app_instance = types.SimpleNamespace(manager=mgr)
        discord_bot._apply_config({"admin_ids": [1], "start_cooldown_seconds": 20, "max_running": 2,
                                   "notify_ready": False})
        discord_bot.L.join_text = lambda *a: ""

        def inter(uid):
            user = types.SimpleNamespace(id=uid, roles=[], mention="<@%d>" % uid)
            user.__str__ = lambda self=None: "u%d" % uid
            return types.SimpleNamespace(user=user, client=types.SimpleNamespace(), channel=None)
        run = lambda c: asyncio.run(c)
        txt = run(discord_bot._start(inter(5), "v"))
        self.assertIn("Starting **Valheim**", txt)
        self.assertEqual(calls[-1][:2], ("start", "v"))
        self.assertIn("more seconds", run(discord_bot._start(inter(5), "v")))      # cooldown
        mgr.sts[0]["isRunning"] = True
        self.assertIn("already running", run(discord_bot._start(inter(6), "v")))
        mgr.sts[0]["isRunning"] = False
        mgr.sts.append({"id": "x", "name": "X", "state": "running", "isRunning": True})
        self.assertIn("limit", run(discord_bot._start(inter(7), "v")))             # max_running
        n = len(calls)
        self.assertIn("Only admins", run(discord_bot._stop_or_restart(inter(5), "r")))
        self.assertEqual(len(calls), n)
        self.assertIn("Stopped **Rust**", run(discord_bot._stop_or_restart(inter(1), "r")))
        self.assertEqual(calls[-1][:2], ("stop", "r"))
        view = discord_bot._build_grid_view(mgr.get_all_statuses())
        self.assertEqual(len(view.children), 3)
        self.assertTrue(view.children[0].label.startswith("\u25B6 Start"))
        self.assertEqual(view.children[0].custom_id, "baobun:toggle:v")
        self.assertIn("players", discord_bot._players_text(mgr.sts[1]) + "players")
        tree = discord_bot._setup_commands(types.SimpleNamespace())
        self.assertEqual(sorted(tree.commands), sorted([
            "help", "join", "players", "restart", "say", "servers", "start", "stop", "status", "mods", "notify",
            "stats", "event", "events", "event-cancel", "backup", "restore", "logs", "update", "checkup",
            "baobun-setup", "access"]))
        # server owner / Manage Server count as admins on a fresh install
        owner = types.SimpleNamespace(id=99, roles=[], guild=types.SimpleNamespace(owner_id=99))
        manager = types.SimpleNamespace(id=98, roles=[], guild=types.SimpleNamespace(owner_id=1),
                                        guild_permissions=types.SimpleNamespace(manage_guild=True, administrator=False))
        self.assertTrue(discord_bot._is_admin(owner))
        self.assertTrue(discord_bot._is_admin(manager))
        self.assertFalse(discord_bot._is_admin(types.SimpleNamespace(id=5, roles=[])))
        ev = {"id": "abc", "title": "Boss night", "when": 2000000000, "going": [5], "not_going": [],
              "creator_name": "Bo", "server_name": "Rust", "auto_start": True}
        v = discord_bot._event_view(ev)
        self.assertEqual([b.custom_id for b in v.children], ["baobun:ev:in:abc", "baobun:ev:out:abc"])
        self.assertIn("<t:2000000000:F>", discord_bot._event_text(ev))
        self.assertIn("<@5>", discord_bot._event_text(ev))


class SelfUpdateTests(unittest.TestCase):
    def test_watcher_and_api(self):
        from core import self_update
        d = Path(tempfile.mkdtemp())
        exe = d / "BaoBunCP.exe"
        exe.write_bytes(b"old build")
        w = self_update.Watcher(str(exe))
        self.assertFalse(w.check()["ready"])
        w.started["mtime"] -= 1000                  # pretend it started a while ago
        exe.write_bytes(b"new build!!")
        self.assertTrue(w.check().get("copying"))   # still being copied: wait
        os.utime(exe, (time.time() - 30, time.time() - 30))
        r = w.check()
        self.assertTrue(r["ready"], r)
        self.assertIn("built", r)
        self.assertEqual(self_update.note_version(d, w.started), None)
        w2 = self_update.Watcher(str(exe))
        self.assertIn("updated", self_update.note_version(d, w2.started))
        tmp = make_env([{"id": "v", "name": "V"}])
        eng = engine(tmp)
        eng.api.updater = w
        done = []
        eng.api.on_update = lambda reason="": done.append(reason) or {"restarting": True}
        r = eng.api.process("agent.update_status", {})
        self.assertTrue(r.success and r.data["ready"] and r.data["policy"] == "when_idle", r.error)
        self.assertTrue(eng.api.process("agent.update_now", {}, user_id="web:boss").success)
        self.assertTrue(done)
        from core.web_auth import ROLE_PERMS
        self.assertIn("agent.update_status", ROLE_PERMS["operator"])
        self.assertNotIn("agent.update_now", ROLE_PERMS["operator"])


class WatchdogTests(unittest.TestCase):
    def test_heartbeat_page_and_watch(self):
        from core import heartbeat
        tmp = make_env([{"id": "v", "name": "V"}])
        m = engine(tmp).manager
        import socket as _s
        sock = _s.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
        hb = heartbeat.HeartbeatServer(m, port, extra=lambda: {"next_nightly": None})
        hb.start()
        try:
            r = heartbeat.probe("127.0.0.1:%d" % port)
            self.assertTrue(r and r["ok"] and r["running"] == 0, r)
        finally:
            hb.stop()
        self.assertEqual(heartbeat.target_url("gamehost"), "http://gamehost:8791/healthz")
        self.assertEqual(heartbeat.target_url("10.0.0.5:9000"), "http://10.0.0.5:9000/healthz")
        w = heartbeat.HostWatch("gamehost", fails_before_alert=3)
        self.assertIsNone(w.step({"ok": True, "running": 2}, now=0))
        self.assertIsNone(w.step(None, pings=False, now=60))
        self.assertIsNone(w.step(None, pings=False, now=120))
        down = w.step(None, pings=False, now=180)
        self.assertEqual(down[0], "down")
        self.assertIn("doesn't answer at all", down[2])
        self.assertIsNone(w.step(None, pings=False, now=240))           # alert only once
        back = w.step({"ok": True, "running": 2}, now=600)
        self.assertEqual(back[0], "ok")
        self.assertIn("9 minutes", back[2])
        # nightly restart window: no alert
        n = heartbeat.HostWatch("gamehost", fails_before_alert=1)
        n.step({"ok": True, "next_nightly": 1000}, now=900)
        self.assertIsNone(n.step(None, pings=False, now=1100))
        self.assertEqual(n.step(None, pings=True, now=1000 + 25 * 60)[0], "down")
        self.assertIn("BaoBun on it doesn't", n.step({"ok": True}, now=3000) and "BaoBun on it doesn't")


class HostSettingsApiTests(unittest.TestCase):
    def test_host_settings(self):
        eng = engine(make_env([{"id": "v", "name": "V"}]))
        r = eng.api.process("host.settings", {})
        self.assertTrue(r.success, r.error)
        self.assertEqual(r.data["self_update"], "when_idle")
        ok = eng.api.process("host.settings_save", {"values": {"nightly_restart_enabled": True,
                                                              "nightly_restart_time": "4:30", "smart_boot_days": 3}})
        self.assertTrue(ok.success, ok.error)
        self.assertEqual(eng.manager.get_setting("nightly_restart_time"), "04:30")
        self.assertFalse(eng.api.process("host.settings_save", {"values": {"steam_password": "x"}}).success)
        self.assertFalse(eng.api.process("host.settings_save", {"values": {"nightly_restart_time": "25:00"}}).success)
        from core.web_auth import ROLE_PERMS
        self.assertNotIn("host.settings_save", ROLE_PERMS["operator"])


class OffsiteBackupTests(unittest.TestCase):
    def test_mirror_status_test_and_checkup(self):
        from core import checkup, boot_task
        tmp = make_env([{"id": "a", "name": "A"}])
        m = engine(tmp).manager
        (Path(m.get_server("a")["directory"]) / "Saves").mkdir()
        (Path(m.get_server("a")["directory"]) / "Saves" / "w.dat").write_text("x")
        titles = [i["title"] for i in checkup.run(m, firewall=False, force=True)["items"]]
        self.assertIn("Backups only live on this PC", titles)
        good = Path(tempfile.mkdtemp()) / "nas"
        self.assertTrue(m.test_mirror(str(good))["ok"])
        bad = _unwritable()
        self.assertFalse(m.test_mirror(bad)["ok"])
        m.set_setting("backup_mirror_dir", bad)
        m.backup_server_world("a")
        self.assertFalse(m.mirror_status()["ok"])
        titles = [i["title"] for i in checkup.run(m, firewall=False, force=True)["items"]]
        self.assertTrue(any(t.startswith("Copying backups to") for t in titles), titles)
        m.set_setting("backup_mirror_dir", str(good))
        m.backup_server_world("a")
        self.assertTrue(m.mirror_status()["ok"])
        x = boot_task.task_xml("C:/b.exe", "--agent", "C:/", user="PC\\me", logon="Password")
        self.assertIn("<LogonType>Password</LogonType>", x)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ImageCacheTests(unittest.TestCase):
    def test_only_mod_site_images_are_fetched_and_cached(self):
        import io
        from core.img_cache import ImageCache, MAX_BYTES
        calls = []

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        def opener(req, timeout=0):
            calls.append(req.full_url)
            return R(b"x" * (MAX_BYTES + 10) if "big" in req.full_url else b"PNGDATA")
        with tempfile.TemporaryDirectory() as d:
            c = ImageCache(os.path.join(d, "img"))
            self.assertIsNone(c.get("https://evil.example/x.png", opener=opener))
            self.assertIsNone(c.get("http://cdn.modrinth.com/x.png", opener=opener))
            self.assertEqual(calls, [])
            p = c.get("https://cdn.modrinth.com/a/icon.png", opener=opener)
            self.assertTrue(p and p.endswith(".png") and open(p, "rb").read() == b"PNGDATA")
            self.assertEqual(c.get("https://cdn.modrinth.com/a/icon.png", opener=opener), p)
            self.assertEqual(len(calls), 1)
            self.assertIsNone(c.get("https://cdn.modrinth.com/big.png", opener=opener))


class HytaleInstallTests(unittest.TestCase):
    def _game_zip(self, path):
        import zipfile
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("Server/HytaleServer.jar", b"JAR2")
            z.writestr("Server/HytaleServer.aot", b"AOT")
            z.writestr("Assets.zip", b"ASSETS")
            z.writestr("start.bat", b"@echo off")
            z.writestr("config.json", b"{\"new\": true}")
            z.writestr("../evil.txt", b"x")

    def test_signin_parsing(self):
        from core import hytale_install as hi
        si = hi.parse_signin("Please visit https://oauth.accounts.hytale.com/oauth2/device/verify?user_code=ABCD-EFGH to sign in")
        self.assertEqual(si["code"], "ABCD-EFGH")
        si = hi.parse_signin("Visit: https://accounts.hytale.com/device\nEnter code: WXYZ1234\nExpires in 900 seconds")
        self.assertEqual(si, {"url": "https://accounts.hytale.com/device", "code": "WXYZ1234"})
        self.assertIsNone(hi.parse_signin("see https://example.com/device code ABCD"))
        log = "x\n%s https://accounts.hytale.com/device AAAA\n%s https://accounts.hytale.com/device BBBB\n" % (
            hi.SIGNIN_TAG, hi.SIGNIN_TAG)
        self.assertEqual(hi.last_signin(log)["code"], "BBBB")

    def test_install_runs_downloader_and_keeps_server_files(self):
        from core import hytale_install as hi
        from core import game_installers as gi
        tmp = make_env([{"id": "hy", "name": "Hy", "type": "hytale", "executable": "Server/HytaleServer.jar",
                         "launchArgs": ""}])
        e = engine(tmp)
        srv = e.manager.get_server("hy")
        self.assertEqual(gi.kind(srv), "hytale")
        d = srv["directory"]
        os.makedirs(os.path.join(d, "Server"))
        open(os.path.join(d, "config.json"), "w").write("{\"mine\": true}")
        open(os.path.join(d, "Server", "auth.enc"), "w").write("SECRET")
        tool = hi.tool_dir(str(tmp))
        os.makedirs(tool)
        exe = os.path.join(tool, "hytale-downloader-windows-amd64.exe" if os.name == "nt" else "hytale-downloader-linux-amd64")
        open(exe, "w").write("")
        test = self

        class FakeProc:
            def __init__(self, cmd, cwd=None, **kw):
                test.assertEqual(cmd[1:2], ["-download-path"])
                test._game_zip(cmd[2])
                import io
                self.stdout = io.BytesIO(b"Visit https://accounts.hytale.com/device\r\nEnter code: QQQQ-1234\n"
                                         b"Downloading 2026.03.01-abcdef12\n 45%\r 100%\n")

            def wait(self):
                return 0

            def kill(self):
                pass
        prog = gi.Progress(d)
        res = hi.install(srv, str(tmp), prog, popen=FakeProc, java=lambda v, progress=None: "java")
        log = open(os.path.join(d, gi.INSTALL_LOG), encoding="utf-8").read()
        self.assertEqual(hi.last_signin(log), {"url": "https://accounts.hytale.com/device", "code": "QQQQ-1234"})
        self.assertEqual(res["version"], "2026.03.01-abcdef12")
        self.assertEqual(open(os.path.join(d, "Server", "HytaleServer.jar"), "rb").read(), b"JAR2")
        self.assertTrue(os.path.isfile(os.path.join(d, "Assets.zip")))
        self.assertEqual(open(os.path.join(d, "config.json")).read(), "{\"mine\": true}")
        self.assertEqual(open(os.path.join(d, "Server", "auth.enc")).read(), "SECRET")
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(d), "evil.txt")))
        self.assertTrue(hi.server_signed_in(srv))
        self.assertFalse(os.path.exists(os.path.join(tool, hi.GAME_ZIP)))

    def test_failed_download_mentions_signin(self):
        from core import hytale_install as hi
        from core import game_installers as gi
        import io
        with tempfile.TemporaryDirectory() as d:
            class P:
                def __init__(self, *a, **k):
                    self.stdout = io.BytesIO(b"Go to https://accounts.hytale.com/device code ZZZZ\nauth timed out\n")

                def wait(self):
                    return 1
            with self.assertRaises(RuntimeError) as cm:
                hi.run_downloader(os.path.join(d, "dl"), os.path.join(d, "g.zip"), gi.Progress(d), popen=P)
            self.assertIn("sign-in", str(cm.exception))

    def test_sign_in_sends_commands_and_reads_link(self):
        tmp = make_env([{"id": "hy", "name": "Hy", "type": "hytale", "executable": "Server/HytaleServer.jar"}])
        m = engine(tmp).manager
        log = os.path.join(str(tmp), "hy.log")
        open(log, "w").write("old https://accounts.hytale.com/device code OLD1\n")
        sent = []
        m.is_running = lambda sid: True
        m.get_log_path = lambda sid: log

        def send(sid, cmd, user="x"):
            sent.append(cmd)
            if cmd == "/auth login device":
                with open(log, "a") as f:
                    f.write("Visit https://accounts.hytale.com/device\nEnter code: NEW2-CODE\n")
            return {"status": "sent"}
        m.send_console_command = send
        r = m.hytale_sign_in("hy", wait=3)
        self.assertEqual(sent, ["/auth persistence Encrypted", "/auth login device"])
        self.assertEqual(r["code"], "NEW2-CODE")
        from core import quick_commands
        cmds = [c["command"] for c in quick_commands.for_server(m.get_server("hy"))]
        self.assertIn("/auth login device", cmds)


class ArkOwnInstallTests(unittest.TestCase):
    def test_one_map_gets_its_own_settings_and_transfers_keep_working(self):
        from core import ark_cluster, ark_ini
        tmp = make_env([])
        inst = tmp / "ASA"
        cfg = inst / "ShooterGame" / "Saved" / "Config" / "WindowsServer"
        cfg.mkdir(parents=True)
        (inst / "ShooterGame" / "Saved" / "Logs").mkdir(parents=True)
        (inst / "ShooterGame" / "Saved" / "Logs" / "big.log").write_text("x" * 1000)
        (inst / "ShooterGame" / "Saved" / "SavedArks" / "Ragnarok_WP").mkdir(parents=True)
        (inst / "ShooterGame" / "Saved" / "SavedArks" / "Ragnarok_WP" / "Ragnarok_WP.ark").write_text("WORLD")
        (inst / "ShooterGame" / "Binaries" / "Win64").mkdir(parents=True)
        (inst / "ShooterGame" / "Binaries" / "Win64" / "ArkAscendedServer.exe").write_text("EXE")
        m = engine(tmp).manager
        made = m.setup_ark_cluster(str(inst), "Bao", ["The Island", "Ragnarok"], settings={"mods": "111"})
        ids = {e["arkFriendly"]: e["id"] for e in made}
        rag, isl = ids["Ragnarok"], ids["The Island"]
        # shared install: "this map only" is refused, the cluster-wide scope works
        with self.assertRaises(ValueError):
            ark_cluster.read_spawns(m, rag, "this")
        ark_cluster.save_spawns(m, isl, [{"from": "Rex_Character_BP_C", "to": ""}])
        plan = ark_cluster.separate_plan(m, rag)
        self.assertTrue(plan["target"].endswith("ASA-Ragnarok"))
        res = ark_cluster.separate(m, rag)
        new = res["target"]
        self.assertEqual(m.get_server(rag)["directory"], new)
        self.assertEqual(m.get_server(isl)["directory"], str(inst))
        self.assertTrue(os.path.isfile(os.path.join(new, "ShooterGame", "Saved", "SavedArks", "Ragnarok_WP",
                                                    "Ragnarok_WP.ark")))
        self.assertFalse(os.path.exists(os.path.join(new, "ShooterGame", "Saved", "Logs")))
        # every map uses one shared cluster folder
        dirs = {ark_cluster.flag(m.get_server(i)["launchArgs"], "ClusterDirOverride") for i in (rag, isl)}
        self.assertEqual(len(dirs), 1)
        self.assertTrue(list(dirs)[0])
        with self.assertRaises(ValueError):
            ark_cluster.separate_plan(m, rag)       # already has its own
        # Ragnarok alone: spawns / loot / rules differ, transfer settings still shared
        ark_cluster.save_spawns(m, rag, [{"from": "Raptor_Character_BP_C", "to": ""}], where="this")
        ark_cluster.save_rules(m, rag, {"DinoCountMultiplier": "2", "DestroyTamesOverLevelClamp": "450"},
                               where="this")
        isl_sp = [r["from"] for r in ark_cluster.read_spawns(m, isl)["rules"]]
        rag_sp = [r["from"] for r in ark_cluster.read_spawns(m, rag, "this")["rules"]]
        self.assertEqual(isl_sp, ["Rex_Character_BP_C"])
        self.assertEqual(rag_sp, ["Raptor_Character_BP_C"])
        self.assertEqual(ark_cluster._gus(new, "DinoCountMultiplier"), "2.0")
        self.assertIsNone(ark_cluster._gus(str(inst), "DinoCountMultiplier"))
        self.assertEqual(ark_cluster._gus(str(inst), "DestroyTamesOverLevelClamp"), "450")
        info = ark_cluster.read_loot(m, rag, "this")
        self.assertTrue(info["can_split"] and info["where"] == "this")
        # health: no complaint about different spawns, maps marked
        chk = ark_cluster.check(m, isl)
        self.assertFalse([i for i in chk["issues"] if "differ" in i["title"]], chk["issues"])
        self.assertTrue({r["id"]: r["own_install"] for r in chk["maps"]}[rag])
        # transfer setting drifts by hand -> flagged, Copy fixes only that
        from core.mods_common import ini_set
        ini_set(ark_ini.gus_ini(new), "ServerSettings", "PreventDownloadDinos", "True")
        chk = ark_cluster.check(m, isl)
        self.assertTrue([i for i in chk["issues"] if i["action"] == "copy_settings"])
        ark_cluster.copy_settings(m, isl)
        self.assertIsNone(ark_cluster._gus(new, "PreventDownloadDinos"))
        self.assertEqual(ark_cluster._gus(new, "DinoCountMultiplier"), "2.0")
        self.assertEqual([r["from"] for r in ark_cluster.read_spawns(m, rag, "this")["rules"]],
                         ["Raptor_Character_BP_C"])


class ModAutoUpdateTests(unittest.TestCase):
    def _setup(self):
        from core.mods import ModManager
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story"}])
        m = engine(tmp).manager
        mm = ModManager(m)
        d = Path(m.get_server("vs")["directory"])
        mods_dir = d / "Data" / "Mods"
        mods_dir.mkdir(parents=True)
        ModTests._zip(mods_dir / "moda_1.0.0.zip", "moda", "1.0.0")
        ModTests._zip(mods_dir / "modb_1.0.0.zip", "modb", "1.0.0")
        rel = {}
        for mid in ("moda", "modb"):
            new = tmp / ("%s_1.1.0.zip" % mid)
            ModTests._zip(new, mid, "1.1.0")
            rel[mid] = {"modid": mid, "releases": [{"modversion": "1.1.0", "tags": ["1.20.0"], "mainfile": new.as_uri(),
                                                     "filename": new.name, "modidstr": mid}]}
        m.vs_server_version = lambda sid: "1.20.3"
        m.vs_get_mod = lambda mid: rel[str(mid)]
        return tmp, m, mm, mods_dir

    def test_pins_skip_update_all_but_allow_manual(self):
        tmp, m, mm, mods_dir = self._setup()
        mm.set_pin("vs", "modb_1.0.0.zip", True)
        self.assertEqual(m.get_server("vs")["modPins"], ["modb"])       # pinned by mod ID, not file name
        lst = mm.list("vs", True)
        self.assertTrue(next(x for x in lst["mods"] if x["id"] == "modb")["pinned"])
        r = mm.update("vs")
        self.assertEqual([u["mod"] for u in r["updated"]], ["Moda"])
        self.assertEqual(r["pinned_skipped"], ["Modb"])
        self.assertTrue((mods_dir / "modb_1.0.0.zip").is_file())
        r = mm.update("vs", ["modb_1.0.0.zip"])                         # by hand: allowed
        self.assertEqual([u["mod"] for u in r["updated"]], ["Modb"])
        self.assertTrue(next(x for x in mm.list("vs")["mods"] if x["id"] == "modb")["pinned"])   # still pinned
        mm.set_pin("vs", "modb", False)
        self.assertEqual(m.get_server("vs")["modPins"], [])

    def test_check_notify_apply_and_rollback_on_crash(self):
        from core import mod_updates
        tmp, m, mm, mods_dir = self._setup()
        mm.set_pin("vs", "modb_1.0.0.zip", True)
        sent = []
        m.set_setting("mod_updates", "auto")
        found = mod_updates.check_and_notify(m, lambda t, x: sent.append((t, x)))
        self.assertEqual([x["key"] for x in found["vs"]["mods"]], ["moda_1.0.0.zip"])
        self.assertEqual(found["vs"]["pinned"], ["Modb"])
        self.assertEqual(len(sent), 1)
        self.assertIn("Moda", sent[0][1])
        mod_updates.check_and_notify(m, lambda t, x: sent.append((t, x)))
        self.assertEqual(len(sent), 1)                                   # same list -> no repeat
        done = mod_updates.apply_pending(m)
        self.assertEqual(done, ["VS: Moda"])
        self.assertTrue((mods_dir / "moda_1.1.0.zip").is_file())
        self.assertEqual(mod_updates.pending(m), {"vs": {"name": "VS", "mods": [], "pinned": ["Modb"]}})
        rec = m._read_registry("vs")["mod_rollback"]
        self.assertTrue(os.path.isdir(rec["snapshot"]))
        # the server crashes after the update -> old mods back, moda pinned, message
        msgs = []
        self.assertTrue(mod_updates.on_crash(m, "vs", notify=lambda t, x: msgs.append(t)))
        self.assertTrue((mods_dir / "moda_1.0.0.zip").is_file())
        self.assertFalse((mods_dir / "moda_1.1.0.zip").exists())
        self.assertEqual(sorted(m.get_server("vs")["modPins"]), ["moda", "modb"])
        self.assertIn("Rolled back", msgs[0])
        self.assertFalse(mod_updates.on_crash(m, "vs"))                   # only once
        # opted-out servers and "off" are left alone
        mm.set_auto_update("vs", False)
        self.assertEqual(mod_updates.check(m), {})
        m.set_setting("mod_updates", "off")
        self.assertEqual(mod_updates.check_and_notify(m, lambda *a: None), {})

    def test_pin_refused_for_server_downloaded_mods(self):
        from core.mods import ModManager, ModError
        tmp = make_env([{"id": "asa", "name": "ASA", "appId": "2430930", "launchArgs": "TheIsland_WP?listen -mods=111"}])
        m = engine(tmp).manager
        with self.assertRaises(ModError):
            ModManager(m).set_pin("asa", "111", True)


class MemGuardTests(unittest.TestCase):
    def test_restarts_after_three_minutes_over_with_cooldown(self):
        from core.mem_guard import MemGuard, OVER_SAMPLES
        tmp = make_env([{"id": "a", "name": "A", "memLimitMB": 1000}, {"id": "b", "name": "B"}])
        m = engine(tmp).manager
        calls, notes = [], []
        m.warn_players = lambda ids, action="", minutes=None: calls.append(("warn", ids, minutes))
        m.restart_server = lambda sid, user="": calls.append(("restart", sid, user))
        m.is_running = lambda sid: True
        g = MemGuard(m, notify=lambda t, x: notes.append(t), log=lambda *a: None, spawn=lambda fn: fn())
        over = [{"id": "a", "name": "A", "isRunning": True, "state": "running", "memUsageMB": 1500},
                {"id": "b", "name": "B", "isRunning": True, "state": "running", "memUsageMB": 99999}]
        for i in range(OVER_SAMPLES - 1):
            self.assertEqual(g.step(over, now=1000 + i * 60), [])
        g.step([dict(over[0], memUsageMB=500)], now=1100)             # dips below -> count restarts
        acted = [g.step(over, now=1200 + i * 60) for i in range(OVER_SAMPLES)]
        self.assertEqual(acted[-1], ["a"])
        self.assertEqual(calls, [("warn", ["a"], [5, 1]), ("restart", "a", "memory-guard")])
        self.assertIn("Restarted A", notes[0])
        for i in range(10):                                           # cooldown
            self.assertEqual(g.step(over, now=1500 + i * 60), [])
        m.maintenance_until = 10 ** 10
        self.assertEqual(g.step(over, now=1200 + 3600), [])

    def test_trend(self):
        from core.mem_guard import trend, trend_text
        pts = [{"ts": i * 600, "ram": 9000 + (i % 6) * 10} for i in range(6)]      # before restart
        pts += [{"ts": 3600 + i * 600, "ram": 2000 + i * 100} for i in range(13)]   # +600 MB/h after
        self.assertAlmostEqual(trend(pts), 600.0, delta=1)
        self.assertIn("600 MB per hour", trend_text(trend(pts)))
        self.assertIsNone(trend(pts[:3]))
        self.assertEqual(trend_text(5.0), "memory is steady")


class StatusPageTests(unittest.TestCase):
    def test_page_and_json_show_only_public_bits(self):
        import urllib.request
        from core import status_page
        tmp = make_env([{"id": "vs", "name": "VS <World>", "type": "vintage-story", "port": 42420},
                        {"id": "hid", "name": "Hidden", "discordVisible": False}])
        m = engine(tmp).manager
        m.set_setting("status_address", "https://play.example.com/x")
        m.set_setting("status_discord_invite", "https://evil.example/")
        cfg = status_page.config(m)
        self.assertEqual(cfg["address"], "play.example.com")
        self.assertEqual(cfg["invite"], "")
        self.assertEqual(status_page.clean_address("a b'c"), "")
        data = status_page.StatusData(m, public_ip=lambda: "203.0.113.5",
                                      mod_lister=lambda sid: {"mods": [{"name": "Carry On", "version": "1.2",
                                                                        "url": "https://mods.vintagestory.at/carryon"},
                                                                       {"name": "Off", "enabled": False}]})
        snap = data.snapshot()
        self.assertEqual([s["name"] for s in snap["servers"]], ["VS <World>"])
        s = snap["servers"][0]
        self.assertEqual(s["address"], "play.example.com:42420")
        self.assertEqual([x["name"] for x in s["mods"]], ["Carry On"])
        self.assertNotIn("directory", json.dumps(snap))
        page = status_page.render(snap).decode()
        self.assertIn("VS &lt;World&gt;", page)
        self.assertNotIn("<World>", page)
        self.assertIn("Asleep", page)
        srv = status_page.StatusServer(m, tmp, port=0, data=data)
        srv._httpd = None
        # start on a free port
        import socket
        sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
        srv.port = port
        srv.start()
        try:
            body = urllib.request.urlopen("http://127.0.0.1:%d/status.json" % port, timeout=5).read()
            self.assertEqual(json.loads(body)["servers"][0]["name"], "VS <World>")
            html_ = urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=5).read().decode()
            self.assertIn("Carry On", html_)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen("http://127.0.0.1:%d/api/action" % port, timeout=5)
            self.assertEqual(cm.exception.code, 404)
            req = urllib.request.Request("http://127.0.0.1:%d/" % port, data=b"x", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 405)
        finally:
            srv.stop()


class DiscordAssistantTests(unittest.TestCase):
    def test_parse_when(self):
        from core.discord_events import parse_when
        from datetime import datetime
        now = datetime(2026, 11, 6, 15, 0).timestamp()        # a Friday, 3pm
        dt = lambda ts: datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(dt(parse_when("8pm", now)), "2026-11-06 20:00")
        self.assertEqual(dt(parse_when("2pm", now)), "2026-11-07 14:00")          # passed today -> tomorrow
        self.assertEqual(dt(parse_when("tonight", now)), "2026-11-06 20:00")
        self.assertEqual(dt(parse_when("tomorrow 7:30pm", now)), "2026-11-07 19:30")
        self.assertEqual(dt(parse_when("sat 8pm", now)), "2026-11-07 20:00")
        self.assertEqual(dt(parse_when("Saturday at 20:00", now)), "2026-11-07 20:00")
        self.assertEqual(dt(parse_when("fri 1pm", now)), "2026-11-13 13:00")       # Friday already passed
        self.assertEqual(dt(parse_when("next friday 9pm", now)), "2026-11-13 21:00")
        self.assertEqual(dt(parse_when("11/14 21:00", now)), "2026-11-14 21:00")
        self.assertEqual(dt(parse_when("2026-12-01 18:15", now)), "2026-12-01 18:15")
        self.assertEqual(parse_when("in 2h", now), now + 7200)
        self.assertEqual(parse_when("in 1h30m", now), now + 5400)
        self.assertEqual(parse_when("in 45 minutes", now), now + 2700)
        for bad in ("someday", "", "13/45 8pm", "25:00", "in 0m"):
            self.assertIsNone(parse_when(bad, now), bad)

    def test_event_store_flow(self):
        from core.discord_events import EventStore, can_manage, describe
        d = tempfile.mkdtemp()
        s = EventStore(os.path.join(d, "ev.json"))
        now = 1_800_000_000
        with self.assertRaises(ValueError):
            s.create("x", now - 10, 1, "A", now=now)
        ev = s.create("Boss @everyone night", now + 3 * 3600, 1, "Ann", 55, "r", "Rust", True, now=now)
        self.assertNotIn("@", ev["title"])
        self.assertEqual(ev["going"], [1])
        s.rsvp(ev["id"], 2, True)
        s.rsvp(ev["id"], 3, False)
        s.rsvp(ev["id"], 3, True)                        # changed their mind
        e = s.get(ev["id"])
        self.assertEqual((e["going"], e["not_going"]), ([1, 2, 3], []))
        self.assertEqual(s.due(now)["remind"], [])
        due = s.due(now + 3 * 3600 - 29 * 60)
        self.assertEqual([x["id"] for x in due["remind"]], [ev["id"]])
        self.assertEqual(due["wake"], [])
        s.update(ev["id"], reminded=True)
        due = s.due(now + 3 * 3600 - 9 * 60)
        self.assertEqual((due["remind"], [x["id"] for x in due["wake"]]), ([], [ev["id"]]))
        s.update(ev["id"], woke=True)
        self.assertEqual([x["id"] for x in s.due(now + 3 * 3600 + 5)["announce"]], [ev["id"]])
        self.assertTrue(can_manage(e, 1, False) and not can_manage(e, 2, False) and can_manage(e, 2, True))
        self.assertIn("Going (3)", describe(e))
        self.assertEqual(s.prune(now + 3 * 3600 + 13 * 3600), 1)
        ev2 = s.create("Later", now + 86400, 1, "Ann", now=now)
        self.assertFalse(ev2["auto_start"])               # no server -> nothing to start
        s.cancel(ev2["id"])
        self.assertEqual(s.upcoming(now), [])

    def test_player_stats(self):
        from core.player_stats import PlayerStats, format_summary
        from datetime import datetime
        d = tempfile.mkdtemp()
        ps = PlayerStats(os.path.join(d, "s.json"))
        t0 = datetime(2026, 11, 6, 20, 0).timestamp()
        for i in range(90):
            ps.record([{"id": "v", "name": "VS", "isRunning": True, "players": [{"name": "Bob"}, {"name": "Ann"}]},
                       {"id": "r", "name": "Rust", "isRunning": True, "players": [{"name": "Bob"}] if i < 30 else []},
                       {"id": "x", "isRunning": False, "players": [{"name": "Ghost"}]}], now=t0 + i * 60)
        ps.save()
        s = PlayerStats(os.path.join(d, "s.json")).summary(7, now=t0 + 3600)
        self.assertEqual(s["players"][0], {"name": "Bob", "hours": 2.0})
        self.assertEqual([x["name"] for x in s["servers"]], ["VS", "Rust"])
        self.assertEqual(s["peak"]["weekday"], 4)
        txt = format_summary(s)
        self.assertIn("Bob - 2.0 h", txt)
        self.assertIn("Fridays", txt)
        self.assertNotIn("Ghost", txt)
        self.assertIn("No one has played", format_summary(PlayerStats(os.path.join(d, "none.json")).summary()))

    def test_extras(self):
        from core import discord_extras as X
        from core import discord_logic as L
        d = tempfile.mkdtemp()
        subs = X.Subscriptions(os.path.join(d, "n.json"))
        subs.set("v", 5, "up")
        subs.set("v", 6, "join")
        self.assertEqual(subs.mine(5), {"v": ["up"]})
        prev = X.up_map([{"id": "v", "isRunning": False}])
        self.assertEqual(X.came_up(prev, [{"id": "v", "isRunning": True, "state": "running"}]), ["v"])
        self.assertEqual(X.came_up(prev, [{"id": "v", "isRunning": True, "state": "starting"}]), [])
        self.assertEqual(subs.take_up("v"), [5])
        self.assertEqual(subs.take_up("v"), [])
        self.assertEqual(subs.who("v", "join"), [6])
        self.assertEqual(X.presence_text([{"isRunning": True, "playerCount": 3}, {"isRunning": False}]),
                         "1 server · 3 players")
        cfg = X.merged_config({"token": "t", "channel_id": 1}, {"channel_id": 2, "log_channel_id": 0, "x": 1})
        self.assertEqual((cfg["channel_id"], cfg["token"], "x" in cfg), (2, "t", False))
        self.assertTrue(X.is_admin(7, {3}, set(), {3}))
        self.assertFalse(X.is_admin(7, set(), set(), set(), owner_id=8))
        msg = L.event_message("warning", {"name": "VS", "action": "restarting", "minutes": 10}, {})
        self.assertIn("in 10 minutes", msg)
        self.assertIsNone(L.event_message("warning", {"name": "VS", "action": "restarting for maintenance",
                                                      "minutes": 15}, {}))

    def test_mods_modpack_logs_setup(self):
        from core import discord_extras as X
        tmp = make_env([{"id": "vs", "name": "VS", "type": "vintage-story"}])
        m = engine(tmp).manager
        d = Path(m.get_server("vs")["directory"]) / "Data" / "Mods"
        d.mkdir(parents=True)
        ModTests._zip(d / "moda_1.0.0.zip", "moda", "1.0.0")
        info = X.mods_info(m, "vs")
        self.assertTrue(info["pack_possible"])
        self.assertIn("Moda", X.mods_text(info, "VS"))
        r = X.make_modpack(m, "vs", str(tmp / "packs"))
        self.assertEqual(r["count"], 1)
        with zipfile.ZipFile(r["path"]) as z:
            self.assertEqual(z.namelist(), ["moda_1.0.0.zip"])
        log = tmp / "vs.log"
        log.write_text("\n".join("line %d" % i for i in range(100)) + "\nrcon password = hunter2\n")
        m.get_log_path = lambda sid: str(log)
        tail = X.log_tail(m, "vs", 5)
        self.assertEqual(len(tail.splitlines()), 5)
        self.assertNotIn("hunter2", tail)
        X.save_setup(m, channel_id=123, admin_role_ids=[9], bogus=1)
        self.assertEqual(X.setup_values(m), {"channel_id": 123, "admin_role_ids": [9]})
        self.assertIn("<#123>", X.setup_summary(X.merged_config({}, X.setup_values(m))))
        self.assertNotIn("bot_token", json.dumps(X.setup_values(m)))


class DiagnosticsTests(unittest.TestCase):
    def test_zip_has_logs_and_no_secrets(self):
        from core import diagnostics
        tmp = make_env([{"id": "ark", "name": "ARK", "appId": "2430930",
                         "launchArgs": "TheIsland_WP?listen?ServerAdminPassword=adminpw123?SessionName=X -Port=7777"
                                       " -password hunter2x"}])
        m = engine(tmp).manager
        m.set_setting("steam_password", "steampw999")
        m.set_setting("alert_webhook_url", "https://discord.com/api/webhooks/1/abcdefSECRET")
        (tmp / "discord_config.json").write_text(json.dumps({
            "token": "MTIzNDU2Nzg5MDEyMzQ1Njc4" + ".GabcDE." + "abcdefghijklmnopqrstuvwxyz0123456", "channel_id": 42,
            "oauth_client_secret": "oauthsecret777"}))
        (tmp / "logs").mkdir(exist_ok=True)
        (tmp / "logs" / "agent.log").write_text("started\nrcon password = rconpw555\nfrom 203.0.113.9 and "
                                               "192.168.1.5 and 100.101.102.103 version 1.20.3.4\n")
        path = diagnostics.build(m)
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            blob = "\n".join(z.read(n).decode("utf-8", "replace") for n in names)
        for need in ("system.json", "servers.json", "settings.json", "checkup.json", "logs/agent.log",
                     "discord_config (no secrets).json", "statuses.json"):
            self.assertIn(need, names)
        for secret in ("adminpw123", "hunter2x", "steampw999", "abcdefSECRET", "oauthsecret777", "rconpw555",
                       "abcdefghijklmnopqrstuvwxyz0123456", "203.0.113.9"):
            self.assertNotIn(secret, blob, secret)
        for kept in ("192.168.1.5", "100.101.102.103", "SessionName=X", "-Port=7777", '"channel_id": 42',
                     "1.20.3.4"):
            self.assertIn(kept, blob, kept)


class ClusterUpdateTests(unittest.TestCase):
    def test_every_install_updates_together(self):
        tmp = make_env([])
        inst = tmp / "ASA"
        (inst / "ShooterGame" / "Saved" / "Config" / "WindowsServer").mkdir(parents=True)
        m = engine(tmp).manager
        made = m.setup_ark_cluster(str(inst), "Bao", ["The Island", "Ragnarok"], settings={"mods": "1"})
        other = tmp / "ASA-Ragnarok"
        other.mkdir()
        servers = m.load_servers()
        for s in servers:
            if s["id"] == made[1]["id"]:
                s["directory"] = str(other)
        m.save_servers(servers)
        rag, isl = made[1]["id"], made[0]["id"]
        self.assertEqual(sorted(m.install_group(isl)), [isl])
        self.assertEqual(sorted(m.update_group(isl)), sorted([isl, rag]))
        updated = []
        m.check_steam_update = lambda sid: {"update_available": sid == rag, "installed": "1"}
        m._steamcmd_app_update = lambda app, d, acct, branch=None: updated.append(d)
        builds = {str(inst): "100", str(other): "100"}
        m.installed_buildid = lambda d, app: builds.get(d)
        r = m.update_server(isl)
        self.assertEqual(r["status"], "updated")
        self.assertEqual(sorted(updated), sorted([str(inst), str(other)]))
        self.assertNotIn("warning", r)
        builds[str(other)] = "99"
        r = m.update_server(isl, force=True)
        self.assertIn("different game builds", r["warning"])
        m.check_steam_update = lambda sid: {"update_available": False, "installed": "100"}
        self.assertEqual(m.update_server(rag)["status"], "up_to_date")
        # nightly installs waiting updates for servers that allow it
        from core import nightly
        for sid in (isl, rag):
            m._write_registry(sid, update_available=True)
        servers = m.load_servers()
        for s in servers:
            if s["id"] == isl:
                s["autoUpdateWhenEmpty"] = True
        m.save_servers(servers)
        m.check_steam_update = lambda sid: {"update_available": True, "installed": "100"}
        updated.clear()
        builds[str(other)] = "100"
        done = nightly.update_games(m, log=lambda *a: None)
        self.assertEqual(done, ["Cluster Bao (2 maps)"])
        self.assertEqual(len(updated), 2)


class BackupCheckTests(unittest.TestCase):
    def _server(self):
        tmp = make_env([{"id": "a", "name": "A"}])
        m = engine(tmp).manager
        d = Path(m.get_server("a")["directory"]) / "Saves"
        d.mkdir()
        con = sqlite3.connect(str(d / "world.vcdbs"))
        con.execute("create table chunks (id integer primary key, data blob)")
        con.executemany("insert into chunks (data) values (?)", [(os.urandom(2000),) for _ in range(200)])
        con.commit()
        con.close()
        (d / "w.dat").write_text("x" * 5000)
        return tmp, m, d

    def test_good_bad_and_shrunk_backups(self):
        from core import backup_check, checkup
        tmp, m, d = self._server()
        mirror = Path(tempfile.mkdtemp()) / "other-pc"
        m.set_setting("backup_mirror_dir", str(mirror))
        m.backup_server_world("a")
        r = backup_check.check_server(m, "a")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["mirror"]["ok"])
        self.assertIn("it restores", backup_check.summary(m, "a")["text"])
        # a damaged world database inside the newest backup
        time.sleep(1.1)
        with open(d / "world.vcdbs", "r+b") as f:
            f.seek(100)
            f.write(b"\x00" * 3000)
        m.backup_server_world("a")
        r = backup_check.check_server(m, "a")
        self.assertFalse(r["ok"])
        self.assertTrue(any("database" in p for p in r["problems"]), r["problems"])
        self.assertIn("Last backup known to restore", backup_check.summary(m, "a")["text"])
        titles = [i["title"] for i in checkup.run(m, firewall=False, force=True)["items"]]
        self.assertIn("Newest backup failed its restore check", titles)
        # a zip cut short
        newest = m.list_server_backups("a")[0]["path"]
        with open(newest, "r+b") as f:
            f.truncate(os.path.getsize(newest) // 2)
        self.assertFalse(backup_check.verify_zip(newest)["ok"])
        # the world went missing -> much smaller backup
        time.sleep(1.1)
        os.remove(d / "world.vcdbs")
        m.backup_server_world("a")
        r = backup_check.check_server(m, "a")
        self.assertTrue(any("smaller" in p for p in r["problems"]), r["problems"])
        # copies on the other PC show up for restoring, and the server is described there
        for b in m.list_server_backups("a"):
            os.remove(b["path"])
        lst = m.list_server_backups("a", include_mirror=True)
        self.assertTrue(lst and all(b["location"] == "copy" for b in lst))
        self.assertEqual(json.loads((mirror / "a" / "server.json").read_text())["name"], "A")
        self.assertEqual(m.list_server_backups("a"), [])

    def test_due_and_check_all(self):
        from core import backup_check
        tmp, m, d = self._server()
        self.assertTrue(backup_check.due(m))
        m.backup_server_world("a")
        sent = []
        out = backup_check.check_all(m, notify=lambda t, x: sent.append(t), log=lambda *a: None)
        self.assertTrue(out["a"]["ok"])
        self.assertEqual(sent, [])
        self.assertFalse(backup_check.due(m))
        m.set_setting("backup_check_days", 0)
        self.assertFalse(backup_check.due(m, now=time.time() + 30 * 86400))


class BringHereTests(unittest.TestCase):
    """Restore on another PC: servers set up again from a backup-copy folder."""

    def test_paths_from_another_pc(self):
        from core import bring_here as bh
        self.assertEqual(bh.home_of(r"C:\Users\Sam\AppData\Roaming\VintagestoryData"), r"C:\Users\Sam")
        self.assertIsNone(bh.home_of(r"D:\Games\Saves"))
        self.assertEqual(bh._under(r"d:\servers\vs\Data\x", r"D:\Servers\VS"), r"Data\x")
        self.assertIsNone(bh._under(r"D:\Servers\VS2\Data", r"D:\Servers\VS"))
        out = bh.rewrite_text(r'--dataPath "D:\Servers\VS\Data" --x D:/Servers/VS/mods -y D:\Servers\VS2',
                              [(r"D:\Servers\VS", "/new/VS")])
        self.assertEqual(out, r'--dataPath "/new/VS\Data" --x /new/VS/mods -y D:\Servers\VS2')
        self.assertEqual(bh.translate(r"D:\Servers\VS\Data\a", [(r"D:\Servers\VS", "/n")]),
                         os.path.join("/n", "Data", "a"))

    def _old_pc(self, servers):
        tmp = make_env(servers)
        m = engine(tmp).manager
        mirror = Path(tempfile.mkdtemp()) / "copies"
        m.set_setting("backup_mirror_dir", str(mirror))
        return tmp, m, mirror

    def test_bring_a_server_with_an_outside_save_folder(self):
        from core import bring_here as bh
        outside = Path(tempfile.mkdtemp()) / "WorldData"
        (outside / "Saves").mkdir(parents=True)
        (outside / "Saves" / "world.dat").write_text("the world")
        tmp, old, mirror = self._old_pc([{"id": "a", "name": "Valley", "port": 4242, "cpuAffinity": "0-3",
                                          "savePaths": [str(outside)]}])
        d = Path(old.get_server("a")["directory"])
        (d / "serverconfig.json").write_text('{"x": 1}')
        srv = old.get_server("a")
        srv["launchArgs"] += ' --dataPath "%s"' % outside
        old.save_servers([srv])
        old.backup_server_world("a")
        self.assertTrue((mirror / "a" / "server.json").exists())

        new_pc = make_env([{"id": "other", "name": "Valley", "port": 4242}])
        m = engine(new_pc).manager
        found = bh.scan(m, str(mirror))
        self.assertEqual(len(found["servers"]), 1, found)
        e = found["servers"][0]
        self.assertTrue(e["can_bring"])
        self.assertTrue(e["here"])          # same name exists here
        base = Path(tempfile.mkdtemp()) / "GameServers"
        res = bh.bring(m, e, str(base), progress=lambda s: None)
        got = m.get_server(res["server_id"])
        self.assertEqual(got["name"], "Valley (restored)")
        self.assertTrue(got["manualStart"])
        self.assertNotIn("cpuAffinity", got)
        newdir = Path(got["directory"])
        self.assertTrue(str(newdir).startswith(str(base)))
        self.assertEqual(json.loads((newdir / "serverconfig.json").read_text()), {"x": 1})
        moved = Path(got["savePaths"][0])
        self.assertEqual((moved / "Saves" / "world.dat").read_text(), "the world")
        self.assertNotEqual(moved, outside)
        self.assertIn(str(moved), got["launchArgs"])
        self.assertNotIn(str(outside), got["launchArgs"])
        self.assertTrue(any("4242" in w for w in res["warnings"]))
        self.assertEqual(len(m.list_server_backups(res["server_id"])), 1)   # the backup is kept here too

    def test_cluster_maps_share_one_install_and_job_runs(self):
        from core import bring_here as bh
        tmp, old, mirror = self._old_pc([{"id": "isl", "name": "Island", "appId": "2430930", "_dir": "ark"},
                                         {"id": "cen", "name": "Center", "appId": "2430930", "_dir": "ark"}])
        d = Path(old.get_server("isl")["directory"])
        (d / "Saved").mkdir()
        (d / "Saved" / "map.ark").write_text("save")
        for sid in ("isl", "cen"):
            s = old.get_server(sid)
            s["savePaths"] = ["Saved"]
            servers = [x for x in old.load_servers() if x["id"] != sid] + [s]
            old.save_servers(servers)
            old.backup_server_world(sid)
        m = engine(make_env([])).manager
        calls = []
        m.install_game = lambda sid, opts=None, user="system": calls.append(sid) or {"notes": []}
        base = Path(tempfile.mkdtemp())
        jid = bh.start(m, [{"key": "isl"}, {"key": "cen"}], str(base), folder=str(mirror))
        self.assertTrue(wait_for(lambda: bh.job(jid)["state"] != "working", timeout=30), bh.job(jid))
        j = bh.job(jid)
        self.assertEqual(j["state"], "done", j)
        self.assertEqual(len(calls), 1)             # one install for both maps
        dirs = {m.get_server(x["server_id"])["directory"] for x in j["done"]}
        self.assertEqual(len(dirs), 1)
        self.assertTrue((Path(dirs.pop()) / "Saved" / "map.ark").exists())

    def test_from_the_web(self):
        from core import bring_here as bh
        from core.web_auth import MANAGE
        tmp, old, mirror = self._old_pc([{"id": "a", "name": "Camp", "javaArgs": "-Xmx2G"}])
        (Path(old.get_server("a")["directory"]) / "server.properties").write_text("motd=hi")
        old.backup_server_world("a")
        e = engine(make_env([]))
        e.manager.set_setting("backup_mirror_dir", str(mirror))
        e.manager.set_setting("default_install_dir", str(Path(tempfile.mkdtemp()) / "GS"))
        r = e.api.process("servers.bring_scan", {})
        self.assertTrue(r.success, r.error)
        self.assertEqual([s["name"] for s in r.data["servers"]], ["Camp"])
        self.assertNotIn("server", r.data["servers"][0])          # launch arguments stay on the PC
        self.assertFalse(e.api.process("servers.bring", {"picks": [{"key": "nope"}]}).success)
        jid = e.api.process("servers.bring", {"picks": [{"key": "a"}]}).data["job"]
        self.assertTrue(wait_for(lambda: bh.job(jid)["state"] != "working", timeout=30))
        j = e.api.process("servers.bring_job", {"job": jid}).data["job"]
        self.assertEqual(j["state"], "done", j)
        got = e.manager.get_server(j["done"][0]["server_id"])
        self.assertNotIn("javaArgs", got)
        self.assertTrue(got["directory"].startswith(e.manager.get_setting("default_install_dir")))
        self.assertTrue({"servers.bring_scan", "servers.bring", "servers.bring_job"} <= MANAGE)


class FirstRunTests(unittest.TestCase):
    def test_walkthrough_once_and_saves(self):
        from core import first_run as F
        m = engine(make_env([])).manager
        self.assertTrue(F.needed(m))
        d = F.defaults(m)
        self.assertTrue(os.path.isabs(d["install_dir"]))
        target = Path(tempfile.mkdtemp()) / "GS"
        self.assertIsNone(F.check_install_dir(str(target)) if (F.free_gb(str(target)) or 99) >= 20 else None)
        self.assertEqual(F.check_install_dir("relative/path")[:3], "Use")
        done = F.apply(m, {"install_dir": str(target), "backup_keep": "500", "backup_mirror_dir": "",
                           "watchdog": True})
        self.assertEqual(m.get_setting("default_install_dir"), str(target))
        self.assertEqual(m.get_setting("backup_keep"), 100)
        self.assertTrue(any("Watchdog on" in x for x in done))
        F.mark_done(m)
        self.assertFalse(F.needed(m))
        self.assertIn("GB memory", F.hardware_text({"cores": 8, "threads": 16, "ram_gb": 32}))
        # an existing install with servers never sees it
        m2 = engine(make_env([{"id": "a", "name": "A"}])).manager
        self.assertFalse(F.needed(m2))
        self.assertTrue(m2.get_setting("setup_done")["skipped"])

    def test_discord_token_from_settings(self):
        import base64
        from core import secrets as S
        tmp = make_env([])
        m = engine(tmp).manager
        app_id = "123456789012345678"
        tok = base64.urlsafe_b64encode(app_id.encode()).decode().rstrip("=") + ".AbCdEf.x" + "y" * 30
        self.assertTrue(S.looks_like_bot_token(tok))
        self.assertFalse(S.looks_like_bot_token("not-a-token"))
        self.assertEqual(S.token_app_id(tok), int(app_id))
        self.assertEqual(S.bot_token(tmp, {}), "")
        m.set_setting(S.SETTINGS_TOKEN_KEY, tok)
        raw = json.loads((tmp / "settings.json").read_text())
        self.assertNotIn(S.SETTINGS_TOKEN_KEY, raw)                 # stored encrypted only
        self.assertNotIn(tok, json.dumps(raw))
        self.assertEqual(S.bot_token(tmp, {}), tok)
        self.assertEqual(S.bot_token(tmp, {"token": "from-config"}), "from-config")   # older setups win
        r = engine(tmp).api.process("settings.get", {"key": S.SETTINGS_TOKEN_KEY})
        self.assertNotIn(tok, json.dumps(r.data))


from unittest import mock  # noqa: E402


class PlayerAccessTests(unittest.TestCase):
    def test_minecraft_files_when_stopped(self):
        from core import player_access as PA
        tmp = make_env([{"id": "mc", "name": "MC", "preset": "Minecraft Java", "executable": "server.jar"}])
        m = engine(tmp).manager
        d = Path(m.get_server("mc")["directory"])
        (d / "server.properties").write_text("online-mode=false\nwhite-list=false\nmotd=x\n")
        o = PA.overview(m, "mc")
        self.assertTrue(o["supported"], o)
        self.assertEqual(o["game"], "Minecraft")
        r = PA.change(m, "mc", "allow", "add", "Steve", by="tester")
        self.assertEqual(r["how"], "file")
        wl = json.loads((d / "whitelist.json").read_text())
        self.assertEqual(wl[0]["name"], "Steve")
        self.assertEqual(wl[0]["uuid"], "5627dd98-e6be-3c21-b8a8-e92344183641")   # offline-mode id for Steve
        PA.change(m, "mc", "ban", "add", "Griefer", reason="tnt", by="tester")
        self.assertEqual(json.loads((d / "banned-players.json").read_text())[0]["reason"], "tnt")
        PA.change(m, "mc", "admin", "add", "Alex", by="tester")
        self.assertEqual(json.loads((d / "ops.json").read_text())[0]["level"], 4)
        PA.set_allow_on(m, "mc", True)
        self.assertIn("white-list=true", (d / "server.properties").read_text())
        self.assertIn("motd=x", (d / "server.properties").read_text())
        PA.change(m, "mc", "allow", "remove", "steve")
        o = PA.overview(m, "mc")
        self.assertEqual(o["data"]["allow"], [])
        self.assertTrue(o["allow_on"])
        self.assertEqual(o["history"][0]["action"], "remove")
        with self.assertRaises(PA.AccessError):
            PA.change(m, "mc", "allow", "add", "bad name!")
        # online mode looks the id up at Mojang
        (d / "server.properties").write_text("online-mode=true\n")
        with mock.patch.object(PA, "mojang_uuid", return_value="11111111-2222-3333-4444-555555555555"):
            r = PA.change(m, "mc", "allow", "add", "Notch")
        self.assertEqual(r["id"], "11111111-2222-3333-4444-555555555555")

    def test_valheim_ark_vs_and_console_games(self):
        from core import player_access as PA
        save = Path(tempfile.mkdtemp()) / "vh save"
        tmp = make_env([{"id": "vh", "name": "VH", "appId": "896660"},
                        {"id": "vh2", "name": "VH2", "appId": "896660"},
                        {"id": "ark", "name": "Island", "appId": "2430930"},
                        {"id": "vs", "name": "VS", "type": "vintage-story"},
                        {"id": "rust", "name": "Rust", "appId": "258550"},
                        {"id": "x", "name": "Other"}])
        m = engine(tmp).manager
        s = m.get_server("vh")
        s["launchArgs"] += ' -savedir "%s"' % save
        m.save_servers([x if x["id"] != "vh" else s for x in m.load_servers()])
        sid = "76561198000000001"
        with self.assertRaises(PA.AccessError):
            PA.change(m, "vh", "ban", "add", "Bob")
        PA.change(m, "vh", "admin", "add", sid)
        self.assertIn(sid, (save / "adminlist.txt").read_text())
        self.assertEqual(PA.overview(m, "vh")["data"]["admin"][0]["id"], sid)
        self.assertEqual(sorted(PA.same_game(m, "vh")), ["vh", "vh2"])
        # ARK: allow list + admins in files, the switch is a launch flag, bans need RCON
        eos = "0123456789abcdef0123456789abcdef"
        PA.change(m, "ark", "admin", "add", eos)
        d = Path(m.get_server("ark")["directory"])
        self.assertIn(eos, (d / "ShooterGame" / "Saved" / "AllowedCheaterAccountIDs.txt").read_text())
        PA.set_allow_on(m, "ark", True)
        self.assertIn("-exclusivejoin", m.get_server("ark")["launchArgs"])
        PA.set_allow_on(m, "ark", False)
        self.assertNotIn("-exclusivejoin", m.get_server("ark")["launchArgs"])
        with self.assertRaises(PA.AccessError):
            PA.change(m, "ark", "ban", "add", eos)
        # Vintage Story reads its Playerdata files; changes need the server running
        pd = Path(m.get_server("vs")["directory"]) / "Data" / "Playerdata"
        pd.mkdir(parents=True)
        (pd / "playersbanned.json").write_text(json.dumps([{"PlayerName": "Troll", "PlayerUID": "u1",
                                                             "Reason": "griefing"}]))
        (pd / "playerdata.json").write_text(json.dumps([{"PlayerUID": "u2", "RoleCode": "admin",
                                                          "LastKnownPlayername": "Boss"}]))
        o = PA.overview(m, "vs")
        self.assertEqual(o["data"]["ban"][0]["name"], "Troll")
        self.assertEqual(o["data"]["admin"][0]["name"], "Boss")
        self.assertEqual(sorted(o["needs_running"]), ["admin", "allow", "ban"])
        with self.assertRaises(PA.AccessError):
            PA.change(m, "vs", "allow", "add", "Friend")
        sent = []
        with mock.patch.object(m, "is_running", return_value=True), \
                mock.patch.object(m, "send_console_command", side_effect=lambda s, c, user="": sent.append(c)):
            PA.change(m, "vs", "admin", "add", "Friend")
            PA.change(m, "rust", "ban", "add", "Cheater", reason="aimbot")
            res = PA.change_many(m, ["vh", "vh2"], "ban", "add", sid)
        self.assertEqual(sent, ["/player Friend role admin", 'ban "Cheater"'])
        self.assertTrue(all("error" not in r for r in res.values()))
        self.assertEqual(PA.overview(m, "rust")["data"]["ban"][0]["name"], "Cheater")    # BaoBun's own record
        self.assertFalse(PA.overview(m, "x")["supported"])
        txt = PA.discord_text(PA.overview(m, "vs"))
        self.assertIn("Banned** (1): Troll", txt)
        self.assertIn("Start the server to change", txt)
        # the web/API: admins only, and "everywhere" covers every server of the game
        api = engine(tmp).api
        r = api.process("players.access_change", {"server_id": "vh", "list": "allow", "action": "add",
                                                   "who": sid, "everywhere": True})
        self.assertTrue(r.success, r.error)
        self.assertEqual(sorted(r.data["results"]), ["vh", "vh2"])
        self.assertFalse(api.process("players.access_change", {"server_id": "vh", "list": "allow",
                                                                "action": "add", "who": "Bob"}).success)
        from core.web_auth import ROLE_PERMS
        self.assertNotIn("players.access_change", ROLE_PERMS["operator"])


class UpdateCheckTests(unittest.TestCase):
    def test_versions_and_check(self):
        from core import updates as U
        self.assertTrue(U.newer("1.2.0", "1.1.9"))
        self.assertTrue(U.newer("v1.10", "1.9.3"))
        self.assertFalse(U.newer("1.0", "1.0.0"))
        self.assertFalse(U.newer("1.0.0-beta", "1.0.0"))
        tmp = make_env([])
        m = engine(tmp).manager
        self.assertFalse(U.enabled(m))                      # no update source: off
        self.assertFalse(U.check(m, force=True)["newer"])
        m.set_setting("update_repo", "https://github.com/someone/baobun/")
        self.assertEqual(U.repo(m), "someone/baobun")
        m.set_setting("update_repo", "bad repo; rm -rf")
        self.assertEqual(U.repo(m), "")
        m.set_setting("update_repo", "someone/baobun")
        asked = []
        rel = {"tag_name": "v99.0.0", "html_url": "https://github.com/someone/baobun/releases/tag/v99.0.0",
               "body": "Lots of new things", "assets": [
                   {"name": "BaoBun-Setup-99.0.0.exe", "size": 10,
                    "browser_download_url": "https://github.com/someone/baobun/releases/download/v99.0.0/BaoBun-Setup-99.0.0.exe"},
                   {"name": "BaoBun-Setup-99.0.0.exe.sha256", "size": 80,
                    "browser_download_url": "https://github.com/someone/baobun/releases/download/v99.0.0/BaoBun-Setup-99.0.0.exe.sha256"}]}
        info = U.check(m, force=True, fetch=lambda url: asked.append(url) or rel)
        self.assertEqual(asked, ["https://api.github.com/repos/someone/baobun/releases/latest"])
        self.assertTrue(info["newer"])
        self.assertEqual(info["latest"], "99.0.0")
        self.assertTrue(info["asset"]["sha256_url"].endswith(".sha256"))
        self.assertIn("99.0.0 is available", U.banner_text(info))
        U.check(m, fetch=lambda url: asked.append(url) or rel)          # once a day
        self.assertEqual(len(asked), 1)
        with self.assertRaises(RuntimeError):
            U._download("https://evil.example.com/x.exe", str(tmp / "x.exe"))
        r = engine(tmp).api.process("app.version", {})
        self.assertTrue(r.success and r.data["newer"] and "asset" not in r.data, r.data)
        from core.web_auth import ROLE_PERMS
        self.assertIn("app.version", ROLE_PERMS["operator"])
        self.assertNotIn("app.update_check", ROLE_PERMS["operator"])


class LinkedPcsTests(unittest.TestCase):
    def test_link_two_pcs(self):
        from core import linked_pcs as LP
        import socket as _s
        host_tmp = make_env([{"id": "vh", "name": "Valheim", "appId": "896660"}])
        host = engine(host_tmp)
        host.manager.set_setting("pc_name", "GAMEBOX")
        sock = _s.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
        host.manager.set_setting("link_port", port)
        srv = LP.LinkServer(host.manager, host.api, port, log=lambda *a: None)
        srv.start()
        try:
            code = LP.pairing_code(host.manager, "127.0.0.1")
            self.assertTrue(code.startswith("baobun://127.0.0.1:%d/" % port))
            main = engine(make_env([{"id": "mine", "name": "Mine"}]))
            f = LP.fleet(main.manager)
            added = f.add(code)
            self.assertEqual((added["name"], added["servers"]), ("GAMEBOX", 1))
            raw = json.loads((main.manager.config_path.parent / "settings.json").read_text())
            self.assertNotIn(code.rsplit("/", 1)[1], json.dumps(raw))        # key kept encrypted
            sts = f.statuses()
            self.assertEqual(sts[0]["id"], "@gamebox/vh")
            self.assertEqual(sts[0]["pcName"], "GAMEBOX")
            self.assertEqual(sts[0]["game"], "Valheim")
            self.assertIn("@gamebox/vh", f.servers())
            # any API action for "@pc/server" goes to that PC (and its own audit log)
            r = main.api.process("players.access", {"server_id": "@gamebox/vh"})
            self.assertTrue(r.success, r.error)
            self.assertEqual(r.data["game"], "Valheim")
            r = main.api.process("logs.since", {"server_id": "@gamebox/vh", "offset": -1})
            self.assertTrue(r.success, r.error)
            self.assertFalse(main.api.process("backup.download", {"server_id": "@gamebox/vh"}).success)
            st = main.api.process("fleet.status", {}).data
            self.assertTrue(st["pcs"][0]["ok"])
            # the bot's view: lists include the other PC; start goes there
            fm = LP.manager_for_bots(main.manager)
            self.assertIn("@gamebox/vh", [s["id"] for s in fm.get_all_statuses()])
            self.assertEqual(fm.get_server("@gamebox/vh")["name"], "Valheim")
            with mock.patch.object(host.manager, "start_server", return_value={"status": "started"}) as m:
                self.assertEqual(fm.start_server("@gamebox/vh", "discord:x")["status"], "started")
            self.assertEqual(m.call_args[0][0], "vh")
            # wrong key, replays, chains and blocked actions are refused
            bad = LP.LinkClient("127.0.0.1:%d" % port, "x" * 40).call("link.hello")
            self.assertFalse(bad["success"])
            self.assertIn("wrong key", bad["error"])
            body = b'{"action":"link.hello"}'
            ts, nonce = str(int(time.time())), "n1"
            hdr = {"X-BaoBun-Ts": ts, "X-BaoBun-Nonce": nonce,
                   "X-BaoBun-Sig": LP.sign(LP.host_key(host.manager), ts, nonce, body)}
            self.assertIsNone(srv.check("127.0.0.1", hdr, body))
            self.assertEqual(srv.check("127.0.0.1", hdr, body), "replayed request")
            self.assertIn("home network", srv.check("8.8.8.8", hdr, body))
            old = dict(hdr, **{"X-BaoBun-Ts": str(int(time.time()) - 3600)})
            self.assertIn("clocks", srv.check("127.0.0.1", old, body))
            self.assertIn("chain", srv.handle({"action": "server.status", "params": {"server_id": "@x/y"}}, "t")["error"])
            self.assertFalse(srv.handle({"action": "host.settings_save", "params": {}}, "t")["success"])
            # a new code cuts the old link off
            LP.new_key(host.manager)
            f.refresh()
            self.assertFalse(f.state(refresh=False)[0]["ok"])
            self.assertEqual(f.statuses(refresh=False)[0]["state"], "unreachable")
            self.assertTrue(f.remove("gamebox"))
            self.assertEqual(f.statuses(), [])
            with self.assertRaises(ValueError):
                LP.parse_code("http://nope")
        finally:
            srv.stop()


class BroadFolderTests(unittest.TestCase):
    def test_scans_stay_inside_the_server(self):
        import server_manager as SM
        home = os.path.expanduser("~")
        self.assertEqual(SM.broad_folder(home), "your user folder")
        self.assertEqual(SM.broad_folder(os.path.abspath(os.sep)), "a whole drive")
        self.assertIsNone(SM.broad_folder(os.path.join(home, "GameServers", "Valheim")))
        tmp = make_env([{"id": "a", "name": "A", "_dir": "games"}, {"id": "b", "name": "B", "_dir": "games/inner"}])
        m = engine(tmp).manager
        d = Path(m.get_server("a")["directory"])
        (d / "a.ini").write_text("x=1")
        (d / "inner" / "b.ini").write_text("y=2")
        (d / "deep" / "x").mkdir(parents=True)
        (d / "deep" / "x" / "c.ini").write_text("z=3")
        names = [c["relative"] for c in m.list_configs("a")]
        self.assertIn("a.ini", names)
        self.assertIn("deep/x/c.ini", names)
        self.assertNotIn("inner/b.ini", names)          # another server's folder
        self.assertIn("b.ini", [c["relative"] for c in m.list_configs("b")])
        # a Valheim-style install: the engine's own files aren't server settings
        mono = d / "MonoBleedingEdge" / "etc" / "mono"
        mono.mkdir(parents=True)
        (mono / "browscap.ini").write_text("[GJK_Browscap_Version]\nVersion=1")
        (d / "valheim_server_Data").mkdir()
        (d / "valheim_server_Data" / "x.cfg").write_text("a=1")
        names = [c["relative"] for c in m.list_configs("a")]
        self.assertFalse([n for n in names if "browscap" in n or "_Data" in n], names)
        with mock.patch.object(SM, "broad_folder", return_value="a whole drive"):
            names = [c["relative"] for c in m.list_configs("a")]
        self.assertEqual(names, ["a.ini"])               # top level only


class GameDefaultsTests(unittest.TestCase):
    def test_zomboid_gets_an_admin_password_once(self):
        from core import game_defaults as G
        home = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"USERPROFILE": home}):
            args, notes = G.required({"appId": "380870", "launchArgs": "-port 16261"})
            self.assertTrue(args.startswith("-adminpassword "), "should add admin password")
            self.assertIn("-port 16261", args, "original args preserved")
            pw = G._either(args, "-adminpassword")
            self.assertGreaterEqual(len(pw), 5, "password length >= 5")
            self.assertEqual(len(notes), 1, "one note generated")
            self.assertNotIn(pw, notes[0], "password not in note text")

            args2, notes2 = G.required({"appId": "380870", "launchArgs": args})
            self.assertEqual((args2, notes2), (args, []), "nothing added twice")

            args3, _ = G.required({"appId": "108600", "launchArgs": ""})
            self.assertIn("-adminpassword", args3, "other appId also gets password")

            def accounts(path, admin):
                path.parent.mkdir(parents=True, exist_ok=True)
                con = sqlite3.connect(str(path))
                con.execute("CREATE TABLE whitelist (id INTEGER PRIMARY KEY, username TEXT, password TEXT)")
                if admin:
                    con.execute("INSERT INTO whitelist (username, password) VALUES ('admin', 'x')")
                con.commit()
                con.close()

            # the game makes the accounts file, empty, before it asks: still needs the password
            db_path = Path(home) / "Zomboid" / "db" / "servertest.db"
            accounts(db_path, admin=False)
            self.assertIn("-adminpassword", G.required({"appId": "380870", "launchArgs": ""})[0])
            db_path.unlink()
            accounts(db_path, admin=True)
            args4, notes4 = G.required({"appId": "380870", "launchArgs": ""})
            self.assertEqual((args4, notes4), ("", []), "no password when admin exists")

            args5, _ = G.required({"appId": "380870", "launchArgs": "-servername other"})
            self.assertIn("-adminpassword", args5, "other server gets password")

            cache = tempfile.mkdtemp()
            accounts(Path(cache) / "db" / "servertest.db", admin=True)
            args6 = "-cachedir=" + cache
            args7, notes7 = G.required({"appId": "380870", "launchArgs": args6})
            self.assertEqual((args7, notes7), (args6, []), "unchanged with cachedir")

            odd = Path(home) / "Zomboid" / "db" / "odd.db"       # a file BaoBun can't read: left alone
            odd.write_bytes(b"not a database at all" * 10)
            self.assertEqual(G.required({"appId": "380870", "launchArgs": "-servername odd"})[1], [])

    def test_zomboid_launcher_passes_every_option(self):
        from core import game_defaults as G
        BAT = ('@setlocal enableextensions\n@cd /d "%~dp0"\nSET PZ_CLASSPATH=java/;java/projectzomboid.jar\n'
               '".\\jre64\\bin\\java.exe" -Xms16g -cp %PZ_CLASSPATH% zombie.network.GameServer -statistic 0 %1 %2\n'
               'PAUSE\n')
        d = Path(tempfile.mkdtemp())
        bat = d / "StartServer64.bat"
        bat.write_text(BAT)

        out = G.full_launcher({"appId": "380870"}, str(bat))
        self.assertEqual(out, str(d / "StartServer64-BaoBun.bat"))
        self.assertTrue(Path(out).exists())

        text = Path(out).read_text()
        self.assertIn("GameServer -statistic 0 %*", text)
        self.assertNotIn("%1", text)
        self.assertNotIn("pause", text.lower())
        self.assertIn("SET PZ_CLASSPATH", text)
        self.assertIn("%1 %2", bat.read_text())            # the game's own file is untouched

        before = Path(out).stat().st_mtime_ns
        self.assertEqual(G.full_launcher({"appId": "380870"}, str(bat)), out)
        self.assertEqual(Path(out).stat().st_mtime_ns, before)   # not rewritten when nothing changed

        bat.write_text(BAT.replace("-Xms16g", "-Xms8g"))    # a game update changes its file
        G.full_launcher({"appId": "380870"}, str(bat))
        self.assertIn("-Xms8g", Path(out).read_text())

        self.assertEqual(G.full_launcher({"appId": "896660"}, str(bat)), str(bat))
        self.assertEqual(G.full_launcher({"appId": "380870"}, str(d / "x.exe")), str(d / "x.exe"))
        self.assertEqual(G.full_launcher({"appId": "380870"}, str(d / "nope.bat")), str(d / "nope.bat"))

    def test_dont_starve_starts_in_its_program_folder(self):
        from core import game_defaults as G
        d = Path(tempfile.mkdtemp())
        bin_dir = d / "bin64"
        bin_dir.mkdir()
        exe = str(d / "bin64" / "server.exe")

        self.assertEqual(G.working_dir({"appId": "343050"}, exe, str(d)), str(bin_dir),
                         "Don't Starve starts in program folder")
        self.assertEqual(G.working_dir({"appId": "896660"}, exe, str(d)), str(d),
                         "other games start in install folder")
        self.assertEqual(G.working_dir({"appId": "343050"}, str(d / "missing" / "server.exe"), str(d)),
                         str(d), "fallback when folder doesn't exist")
        self.assertEqual(G.working_dir({"appId": "343050"}, "", str(d)), str(d),
                         "fallback with empty exe path")

    def test_valheim_gets_what_it_needs(self):
        from core import game_defaults as G
        args, notes = G.required({"appId": "896660", "name": "Viking Fun", "port": 2470, "launchArgs": ""})
        self.assertIn('-name "Viking Fun"', args)
        self.assertIn("-port 2470", args)
        self.assertIn("-world Dedicated", args)
        pw = G._value(args, "-password")
        self.assertGreaterEqual(len(pw), 5)
        self.assertEqual(len(notes), 3)
        self.assertNotIn(pw, " ".join(notes))           # never written to the console log
        again, notes2 = G.required({"appId": "896660", "name": "Viking Fun", "launchArgs": args})
        self.assertEqual((again, notes2), (args, []))   # nothing to add the second time
        fixed, _ = G.required({"appId": "896660", "name": "X", "launchArgs": "-name Viking -password abc"})
        self.assertNotEqual(G._value(fixed, "-password"), "abc")
        self.assertEqual(G._value(fixed, "-name"), "Viking")
        self.assertEqual(G.required({"appId": "258550", "launchArgs": "+x"}), ("+x", []))
        cs2, n = G.required({"appId": "730", "port": 27020, "launchArgs": ""})
        self.assertTrue(cs2.startswith("-dedicated") and "+map de_dust2" in cs2 and "-port 27020" in cs2, cs2)
        tf2, _ = G.required({"appId": "232250", "launchArgs": "+map pl_upward"})
        self.assertIn("-game tf", tf2)
        self.assertIn("+map pl_upward", tf2)
        terr, n = G.required({"appId": "105600", "directory": "C:\\GameServers\\Terraria", "port": 7777,
                              "launchArgs": ""})
        self.assertIn("-autocreate 2", terr)
        self.assertIn("C:\\GameServers\\Terraria\\Worlds\\BaoBun.wld", terr)
        self.assertEqual(G.required({"appId": "105600", "launchArgs": "-config serverconfig.txt -port 7777 "
                                                                  "-players 8"})[1], [])
        sm, n = G.required({"appId": "3017310", "port": 8777, "launchArgs": ""})
        self.assertTrue(sm.startswith("Level01_Main -server") and "-Port=8777" in sm, sm)
        self.assertEqual(G.required({"appId": "3017310", "port": 8777, "launchArgs": sm}), (sm, []))
        self.assertNotIn("-PORT=", G.required({"appId": "2857200", "launchArgs": ""})[0])   # no port known
        kf, _ = G.required({"appId": "232130", "launchArgs": ""})
        self.assertTrue(kf.startswith("KF-BioticsLab"))
        # saved to the server before it starts
        tmp = make_env([{"id": "vh", "name": "VH", "appId": "896660"}])
        m = engine(tmp).manager
        srv = m.get_server("vh")
        notes = m._fill_required_args(srv)
        self.assertTrue(notes)
        self.assertIn("-password", m.get_server("vh")["launchArgs"])


class SteamRetryTests(unittest.TestCase):
    def test_missing_configuration_is_retried(self):
        import types as _ty
        tmp = make_env([{"id": "pal", "name": "Pal", "appId": "2394010"}])
        m = engine(tmp).manager
        d = m.get_server("pal")["directory"]
        calls = []

        def fake(pre, post, acct, log_path=None, **k):
            calls.append(list(post))
            ok = len(calls) >= 2
            Path(log_path).write_text("Success! App fully installed." if ok else
                                      "Connecting anonymously to Steam Public...OK\nERROR! Failed to install "
                                      "app '2394010' (Missing configuration)")
            return _ty.SimpleNamespace(returncode=0 if ok else 8)
        with mock.patch.object(m, "_steamcmd_run", side_effect=fake), mock.patch("time.sleep"):
            m._steamcmd_app_update("2394010", d)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][:2], ["+app_info_update", "1"])
        calls.clear()

        def always(pre, post, acct, log_path=None, **k):
            calls.append(1)
            Path(log_path).write_text("ERROR! Failed to install app '2394010' (Missing configuration)")
            return _ty.SimpleNamespace(returncode=8)
        with mock.patch.object(m, "_steamcmd_run", side_effect=always), mock.patch("time.sleep"):
            with self.assertRaises(RuntimeError) as cm:
                m._steamcmd_app_update("2394010", d)
        self.assertEqual(len(calls), 3)
        self.assertIn("Try again", str(cm.exception))
        self.assertNotIn("login failed", str(cm.exception).lower())


class SmokeToolTests(unittest.TestCase):
    def test_flow_and_report(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        import game_smoke_test as GS
        from core.game_catalog import GAME_PRESETS
        from core import game_support
        names = GS.pick_games(GAME_PRESETS)
        self.assertIn("Valheim", names)
        self.assertNotIn("Hytale", names)
        self.assertEqual(game_support.level(dict(GAME_PRESETS["Valheim"], name="Valheim")), "full")
        self.assertEqual(game_support.level(dict(GAME_PRESETS["Core Keeper"], name="Core Keeper")), "basic")
        tmp = make_env([])
        e = engine(tmp)
        e.manager.set_setting("default_install_dir", str(tmp / "games"))
        with mock.patch.object(e.manager, "install_game", return_value={"notes": []}):
            r = GS.test_game(e.manager, e.api, "Valheim", 5, False, lambda m: None)
        self.assertEqual(r["install"], "ok")
        self.assertEqual(r["result"], "didn't start")             # no game files in the fake install
        self.assertTrue(r["launch_defaults"])
        self.assertEqual(e.manager.load_servers(), [])            # cleaned up
        md = tmp / "r.md"
        GS.write_report(md, [r])
        self.assertIn("| Valheim | full | ok |", md.read_text())


class AutoSettingsTests(unittest.TestCase):
    def test_first_start_files_and_auto_essentials(self):
        from core import quick_settings
        tmp = make_env([{"id": "g", "name": "G", "appId": "999999"}])
        m = engine(tmp).manager
        d = Path(m.get_server("g")["directory"])
        (d / "data").mkdir()
        (d / "data" / "items.json").write_text('{"sword": 1}')            # shipped data, not settings
        m._note_config_baseline(m.get_server("g"))
        time.sleep(1.2)
        (d / "Config").mkdir()
        (d / "Config" / "ServerSettings.json").write_text(json.dumps(
            {"ServerName": "My World", "MaxPlayers": 8, "ServerPassword": "secret1", "PvP": False}))
        names = [c["relative"] for c in m.list_configs("g")]
        self.assertEqual(names[0], "Config/ServerSettings.json")         # written by the game: first
        self.assertNotIn("data/items.json", names)
        q = quick_settings.get(m, "g")
        self.assertTrue(q["supported"] and q.get("auto"), q)
        by = {f["label"]: f for f in q["fields"]}
        self.assertEqual(by["Server name"]["value"], "My World")
        self.assertEqual(by["Max players"]["value"], "8")
        self.assertEqual(by["Password to join"]["value"], "")            # never shown
        self.assertTrue(by["Password to join"]["has_value"])
        quick_settings.save(m, "g", {by["Server name"]["key"]: "Renamed", by["Max players"]["key"]: "12",
                                     by["Password to join"]["key"]: ""})
        data = json.loads((d / "Config" / "ServerSettings.json").read_text())
        self.assertEqual((data["ServerName"], data["MaxPlayers"], data["ServerPassword"]), ("Renamed", 12, "secret1"))


class SettingsSafetyTests(unittest.TestCase):
    def test_verify_and_queue_while_running(self):
        from core import config_forms as cf
        tmp = make_env([{"id": "a", "name": "A", "appId": "999998"}])
        m = engine(tmp).manager
        d = Path(m.get_server("a")["directory"])
        cfgdir = d / "Game" / "Saved" / "Config" / "WindowsServer"
        cfgdir.mkdir(parents=True)
        ini = cfgdir / "Game.ini"
        ini.write_text("[/Script/Game]\nMaxPlayers=10\nServerName=Old\n")
        fid = "rel:Game/Saved/Config/WindowsServer/Game.ini"
        form = cf.form(m, "a", fid)
        ids = {f["key"]: f["id"] for g in form["groups"] for f in g["fields"]}
        # a write that doesn't read back is undone
        with mock.patch.object(cf, "_save_lines", side_effect=lambda path, fmt, plan:
                               Path(path).write_text("[/Script/Game]\nMaxPlayers=oops\n")):
            with self.assertRaises(cf.FormError):
                cf.save(m, "a", fid, {ids["MaxPlayers"]: 20})
        self.assertIn("MaxPlayers=10", ini.read_text())
        # while running, files the game rewrites on exit get the change at the next start
        with mock.patch.object(m, "is_running", return_value=True):
            r = cf.save(m, "a", fid, {ids["MaxPlayers"]: 24})
        self.assertTrue(r["pending"])
        self.assertIn("MaxPlayers=10", ini.read_text())
        notes = cf.apply_pending(m, "a")
        self.assertTrue(notes)
        self.assertIn("MaxPlayers=24", ini.read_text())
        self.assertEqual(cf.pending(m, "a"), {})


class ExeGuessTests(unittest.TestCase):
    def test_known_program_and_java_runtime(self):
        tmp = make_env([{"id": "pz", "name": "PZ", "appId": "380870", "type": "steamcmd",
                         "executable": "StartServer64.bat"}])
        m = engine(tmp).manager
        d = Path(m.get_server("pz")["directory"])
        (d / "StartServer64.bat").write_text("@echo off")
        (d / "jre64" / "bin").mkdir(parents=True)
        (d / "jre64" / "bin" / "jwebserver.exe").write_bytes(b"MZ")
        (d / "ProjectZomboid64.exe").write_bytes(b"MZ")
        with mock.patch.object(m, "generate_start_bat", return_value=str(d / "start.bat")):
            m.create_game_launcher("pz", "380870")
        self.assertTrue(m.get_server("pz")["executable"].endswith("StartServer64.bat"))
        self.assertNotIn("jwebserver", m._guess_game_executable(str(d), "380870"))
        # big asset trees don't hide the program
        se = tmp / "se"
        (se / "Content" / "x").mkdir(parents=True)
        for i in range(300):
            (se / "Content" / "x" / ("f%d.dat" % i)).write_text("")
        (se / "DedicatedServer64").mkdir()
        (se / "DedicatedServer64" / "SpaceEngineersDedicated.exe").write_bytes(b"MZ")
        self.assertTrue(m._guess_game_executable(str(se), "298740").endswith("SpaceEngineersDedicated.exe"))


class SourceDefaultsTests(unittest.TestCase):
    def test_any_source_server_gets_its_game_folder(self):
        from core import game_defaults as G
        d = Path(tempfile.mkdtemp())
        (d / "hl2").mkdir()
        (d / "hl2" / "gameinfo.txt").write_text("x")
        (d / "cstrike" / "maps").mkdir(parents=True)
        (d / "cstrike" / "gameinfo.txt").write_text("x")
        for m in ("cs_assault.bsp", "de_dust2.bsp"):
            (d / "cstrike" / "maps" / m).write_text("")
        args, notes = G.required({"appId": "232330", "executable": str(d / "srcds.exe"), "directory": str(d),
                                  "port": 27016, "launchArgs": ""})
        self.assertTrue(args.startswith("-game cstrike"), args)
        self.assertIn("+map de_dust2", args)
        self.assertIn("-port 27016", args)
        self.assertEqual(G.required({"appId": "232330", "executable": str(d / "srcds.exe"), "directory": str(d),
                                     "launchArgs": args})[1], [])
