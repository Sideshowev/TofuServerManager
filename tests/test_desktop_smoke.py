"""Desktop smoke test: really opens BaoBun's windows with customtkinter and closes
them again. It catches the mistakes the engine tests can't see (a wrong option
name, mixing pack and grid, a typo in a button's callback setup) - it needs a
desktop, so it runs on Windows (selftest.bat / run_tests.bat) and is skipped on a
PC without a display. Everything uses fake servers in a temporary folder."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# One window for the whole run, made right away: Tk can fail to start a second time
# later in the same run (test_engine points the user folders at a sandbox, and
# Python 3.14's Tcl then can't find its library - "invalid command name
# tcl_findLibrary").
try:
    import customtkinter as ctk

    class FakeApp(ctk.CTk):
        """Just enough of BaoBunApp for the windows to open."""
        manager = None
        engine = None
        selected_server_id = "vs"

        def center_window(self, dlg, width, height, fit=True):
            dlg.geometry("%dx%d" % (width, height))

        def _show_status(self, msg):
            pass

        def _toast(self, title, msg):
            pass

        def show_error(self, title, msg, parent=None):
            raise AssertionError("error shown: %s - %s" % (title, msg))

        def ask_yes_no(self, title, msg, parent=None):
            return False

        def _startup_command(self):
            return [sys.executable, "app.py", "--agent"]

        def __getattr__(self, name):
            # anything else the windows call on the app (refresh_servers, show_manage...) does nothing;
            # tkinter's own private lookups must still fail normally
            if name.startswith("_") or name in ("tk", "children", "master"):
                raise AttributeError(name)
            return lambda *a, **k: None

    APP = FakeApp()
    APP.withdraw()
    HAVE_TK = True
except Exception:          # no tkinter / no display (e.g. a Linux server)
    APP = None
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "needs a desktop with tkinter")
class DesktopSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_engine as T
        cls.T = T
        cls.tmp = T.make_env([{"id": "vs", "name": "VS", "type": "vintage-story"},
                              {"id": "hy", "name": "Hy", "type": "hytale", "executable": "Server/HytaleServer.jar"}])
        inst = cls.tmp / "ASA"
        (inst / "ShooterGame" / "Saved" / "Config" / "WindowsServer").mkdir(parents=True)
        cls.engine = T.engine(cls.tmp)
        cls.mgr = cls.engine.manager
        cls.ark = cls.mgr.setup_ark_cluster(str(inst), "Bao", ["The Island", "Ragnarok"], settings={"mods": "1"})
        from ui.app_common import THEME
        cls.theme = dict(THEME)
        FakeApp.manager = cls.mgr
        FakeApp.engine = cls.engine
        cls.app = APP


    def _open(self, fn, *args, **kw):
        before = set(self.app.winfo_children())
        fn(*args, **kw)
        for _ in range(20):
            self.app.update()
        new = [w for w in self.app.winfo_children() if w not in before]
        for w in new:
            try:
                w.destroy()
            except Exception:
                pass
        self.app.update()
        return new

    def test_settings_style_windows(self):
        from ui.nightly_dialog import open_nightly_dialog
        from ui.watchdog_dialog import open_watchdog_dialog
        from ui.status_page_dialog import open_status_page_dialog
        from ui.alerts_settings import open_alerts_dialog
        self.assertTrue(self._open(open_nightly_dialog, self.app, self.theme))
        self.assertTrue(self._open(open_watchdog_dialog, self.app, self.theme))
        self.assertTrue(self._open(open_status_page_dialog, self.app, self.theme))
        self._open(open_alerts_dialog, self.app, self.theme)

    def test_checkup_ports_ark(self):
        from ui.checkup_dialog import open_checkup_dialog
        from ui.ports_dialog import open_ports_dialog
        from ui.ark_window import open_ark_window
        self._open(open_checkup_dialog, self.app, self.theme)
        self._open(open_ports_dialog, self.app, self.theme)
        self._open(open_ark_window, self.app, self.theme, self.ark[0]["id"])

    def test_install_and_hytale(self):
        from ui.install_dialog import open_install_dialog
        self._open(open_install_dialog, self.app, self.theme, "hy")
        self._open(open_install_dialog, self.app, self.theme, "vs")

    def test_mod_browser_and_mods_panel(self):
        from ui.mod_browser import ModBrowser
        from ui.mods_tab import ModsPanel

        class Mods:
            def search(self, sid, text, sort=None):
                return [{"id": "carryon", "name": "Carry On", "downloads": 5, "summary": "x"}]

            def details(self, sid, mid):
                return {"blocks": [{"t": "h", "text": "Hi"}, {"t": "p", "text": "Text"}], "versions": [],
                        "links": [("Wiki", "https://example.com")]}

        b = ModBrowser(self.app, self.theme, Mods(), "vs")
        for _ in range(30):
            self.app.update()
        b.open_mod({"id": "carryon", "name": "Carry On"})
        for _ in range(30):
            self.app.update()
        b.destroy()
        frame = ctk.CTkFrame(self.app)
        p = ModsPanel(frame, self.theme, self.app)
        p.sid = "vs"
        p._render("vs", p.mods.list("vs"), None, False)
        self.app.update()
        frame.destroy()

    def test_bring_here_and_restore_windows(self):
        import json, shutil, tempfile
        from ui.bring_here_dialog import BringHereWindow
        from ui.restore_dialog import pick_backup
        mirror = Path(tempfile.mkdtemp()) / "copies"
        (mirror / "old1").mkdir(parents=True)
        z = self.mgr.backup_server_world("vs")["zip_path"]
        shutil.copy(z, mirror / "old1" / Path(z).name)
        (mirror / "old1" / "server.json").write_text(json.dumps({"id": "old1", "name": "Old world",
                                                                 "type": "vintage-story"}))
        self.mgr.set_setting("backup_mirror_dir", str(mirror))
        try:
            w = BringHereWindow(self.app, self.theme)
            for _ in range(30):
                self.app.update()
            self.assertIn("old1", w.checks)
            w.destroy()
        finally:
            self.mgr.set_setting("backup_mirror_dir", "")
        self.app.after(300, lambda: [c.destroy() for c in self.app.winfo_children()
                                     if isinstance(c, ctk.CTkToplevel)])
        self.assertIsNone(pick_backup(self.app, self.theme, self.mgr.list_server_backups("vs"), None))

    def test_first_run_and_discord_bot(self):
        from ui.first_run import FirstRunWindow
        from ui.discord_bot_dialog import open_discord_bot_dialog
        done = self.mgr.get_setting("setup_done")
        w = FirstRunWindow(self.app, self.theme)
        try:
            for name in w.PAGES[:-1]:
                for _ in range(10):
                    self.app.update()
                w.page += 1
                w.show()
            for _ in range(10):
                self.app.update()
        finally:
            w.destroy()
            self.mgr.set_setting("setup_done", done)
        self._open(open_discord_bot_dialog, self.app, self.theme)

    def test_players_window(self):
        from ui.players_dialog import PlayersWindow
        w = PlayersWindow(self.app, self.theme, self.ark[0]["id"])
        for _ in range(40):
            self.app.update()
        w.tab = "admin"
        if w.info and w.info.get("supported"):
            w._render()
        self.app.update()
        w.destroy()

    def test_update_dialog(self):
        from ui.update_dialog import open_update_dialog
        info = {"current": "1.0.0", "latest": "1.1.0", "newer": True, "notes": "New things",
                "page": "https://github.com/x/y/releases/tag/v1.1.0"}
        self._open(open_update_dialog, self.app, self.theme, info)

    def test_linked_pcs_windows(self):
        from ui.linked_pcs_dialog import open_linked_pcs_dialog
        from ui.remote_server_window import RemoteServerWindow
        from ui.home import HomeView
        self._open(open_linked_pcs_dialog, self.app, self.theme)
        w = RemoteServerWindow(self.app, self.theme, "@gamebox/vh")      # not linked: shows errors, no crash
        for _ in range(10):
            self.app.update()
        w._close()
        frame = ctk.CTkFrame(self.app)
        h = HomeView(frame, self.theme, lambda *a: None, lambda *a: None, lambda: None, {}, lambda s: None)
        h.refresh([{"id": "@gamebox/vh", "name": "Valheim", "game": "Valheim", "remote": True, "pcName": "GAMEBOX",
                    "state": "unreachable", "isRunning": False}])
        self.app.update()
        frame.destroy()

    def test_settings_tab_big_file(self):
        """A file with hundreds of settings opens without freezing (fields are built in batches)."""
        import time as _t
        from ui.settings_tab import SettingsPanel
        frame = ctk.CTkFrame(self.app)
        tab = SettingsPanel(frame, self.theme, self.app)
        fields = [{"id": "f%d" % i, "key": "Setting%d" % i, "label": "Setting %d" % i, "type": "text",
                   "value": str(i)} for i in range(600)]
        tab.form = {"file": "x", "path": "x.ini", "groups": [{"name": "All", "fields": fields}]}
        tab.open_groups = {"All"}
        start = _t.time()
        tab._render()
        self.assertLess(_t.time() - start, 2.0)          # the window isn't blocked while it fills in
        for _ in range(60):
            self.app.update()
        self.assertTrue(tab.scroll.winfo_children())
        frame.destroy()

    def test_app_module_imports(self):
        import app
        for name in ("_open_server_form", "show_settings_dialog", "show_ark_cluster_dialog", "install_game",
                     "bring_servers_here", "show_first_run", "show_discord_bot_dialog",
                     "show_players_dialog", "show_update_dialog",
                     "show_linked_pcs_dialog"):
            self.assertTrue(hasattr(app.BaoBunApp, name), name)


if __name__ == "__main__":
    unittest.main()
