import sys
import json
import os
import time
import threading
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.paths import base_dir as _base_dir, bundle_dir as _bundle_dir  # noqa: E402
from core import perf  # noqa: E402  (first, so startup timing covers the imports below)

# Data files live in BASE_DIR. For the exe that is its folder, unless a
# data_dir.txt next to it points elsewhere (dist\data_dir.txt -> "..").
BASE_DIR = _base_dir()
BUNDLE_DIR = _bundle_dir()

APP_DIR = BASE_DIR
CONFIG_PATH = APP_DIR / "servers.json"
ASSETS_DIR = BUNDLE_DIR / "assets"

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import customtkinter as ctk
import tkinter.messagebox as messagebox
from tkinter import ttk
import tkinter.scrolledtext as tkst
from tkinter import filedialog


sys.path.insert(0, str(APP_DIR))
import rcon
from core.engine import build_engine
from core.hosting import dashboard_summary, firewall_rule_exists
from ui.home import HomeView
from ui.console import LiveConsole
from ui.wizard import AddServerWizard

from ui.app_common import ACCENTS, build_theme, THEME, load_image  # noqa: E402,F401

from core.game_catalog import GAME_PRESETS  # noqa: E402

from ui.app_server_form import ServerFormMixin  # noqa: E402
from ui.app_settings import SettingsDialogsMixin  # noqa: E402
from ui.app_ark import ArkDialogsMixin  # noqa: E402
from ui.app_legacy_mods import LegacyModsMixin  # noqa: E402

try:
    perf.set_log(BASE_DIR / "logs" / "perf.log")
    perf.mark("loading the app's code")
    perf.quiet_widgets()
except Exception:
    pass


class TofuApp(ServerFormMixin, SettingsDialogsMixin, ArkDialogsMixin, LegacyModsMixin, ctk.CTk):
    def after(self, ms, func=None, *args):
        """Tk isn't thread-safe: calls from worker threads are queued and run on
        the UI thread by _pump_ui instead of touching Tk directly."""
        if func is not None and threading.current_thread() is not threading.main_thread():
            q = self.__dict__.get("_ui_queue")
            if q is not None:
                q.put((func, args))
                return None
        return super().after(ms, func, *args)

    def _pump_ui(self):
        import queue as _queue
        try:
            for _ in range(200):
                func, args = self._ui_queue.get_nowait()
                try:
                    func(*args)
                except Exception as e:
                    print("[WARN] UI callback:", e)
        except _queue.Empty:
            pass
        super().after(50, self._pump_ui)

    def __init__(self):
        import queue as _queue
        self._ui_queue = _queue.Queue()
        self._session_start = time.time()
        super().__init__()
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        # ---- UI scaling (DPI awareness) ----
        # Read saved scale; fall back to auto-detect from screen DPI.
        self.ui_scale = 0.0
        try:
            import json as _json
            _sp = BASE_DIR / "settings.json"
            if _sp.exists():
                _saved = json.loads(_sp.read_text(encoding="utf-8"))
                _sv = float(_saved.get("ui_scale", 0) or 0)
                if _sv > 0:
                    self.ui_scale = _sv
        except Exception:
            pass
        if self.ui_scale <= 0:
            try:
                import ctypes
                _dpi = ctypes.windll.shcore.GetDpiForMonitor(
                    ctypes.windll.user32.MonitorFromWindow(
                        int(self.winfo_id()), 2), 0)
                self.ui_scale = max(1.0, round(_dpi / 96.0, 2))
            except Exception:
                self.ui_scale = 1.0
        ctk.set_window_scaling(self.ui_scale)
        ctk.set_widget_scaling(self.ui_scale)

        self.title("Tofu Server Manager")
        _s = self.ui_scale
        self.geometry("%dx%d" % (int(1300 * _s), int(800 * _s)))
        self.minsize(int(900 * _s), int(600 * _s))
        ico_path = ASSETS_DIR / "icon.ico"
        if ico_path.exists():
            try:
                self.iconbitmap(str(ico_path))
            except Exception:
                pass
        self.configure(fg_color=THEME["bg_dark"])
        perf.mark("window created")
        self.engine = build_engine(role="gui", base_dir=APP_DIR)
        self.manager = self.engine.manager
        perf.mark("engine and plugins")
        self._statuses = []
        self._poll_inflight = False
        self._local_mode = False
        self._discord_started = False
        self._apply_theme()
        self._init_optional_modules()
        self.selected_server_id = None
        self._polling_active = False
        self._poll_interval = 3.0
        self._current_config_data = None
        self._build_ui()
        perf.mark("building the window")
        self._pump_ui()
        self._start_polling()
        self.refresh_servers()
        perf.mark("first server list")
        perf.watch(self)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        threading.Thread(target=self._startup_engine, daemon=True).start()
        self._move_old_startup_entry()
        self.after(400, self.maybe_show_onboarding)

    def _apply_theme(self):
        accent = (self.manager.get_setting("theme_accent", "gold") or "gold").lower()
        mode = (self.manager.get_setting("appearance_mode", "dark") or "dark").lower()
        if accent not in ACCENTS:
            accent = "cyan"
        if mode not in ("dark", "light"):
            mode = "dark"
        self._retheme(accent, mode)

    def _apply_ttk_style(self):
        try:
            style = ttk.Style(self)
            style.theme_use("clam")
            style.configure("Treeview",
                            background=THEME["bg_input"],
                            fieldbackground=THEME["bg_input"],
                            foreground=THEME["text_light"],
                            rowheight=26)
            style.configure("Treeview.Heading",
                            background=THEME["bg_card"],
                            foreground=THEME["text_light"],
                            font=("Bahnschrift SemiBold", 11, "bold"),
                            relief="flat")
            style.map("Treeview",
                      background=[("selected", THEME["accent_blue"])],
                      foreground=[("selected", "#ffffff")])
            style.configure("TCombobox",
                            fieldbackground=THEME["bg_input"],
                            background=THEME["bg_input"],
                            foreground=THEME["text_light"],
                            arrowcolor=THEME["text_light"],
                            bordercolor=THEME["border"],
                            lightcolor=THEME["bg_input"],
                            darkcolor=THEME["bg_input"])
            style.map("TCombobox",
                      fieldbackground=[("readonly", THEME["bg_input"])],
                      foreground=[("readonly", THEME["text_light"])],
                      selectbackground=[("readonly", THEME["bg_input"])],
                      selectforeground=[("readonly", THEME["text_light"])])
        except Exception as e:
            print("[WARN] ttk style:", e)

    def _retheme_widget(self, w, color_map):
        for opt in ("fg_color", "text_color", "border_color", "hover_color",
                    "text_color_disabled", "placeholder_text_color",
                    "dropdown_fg_color", "dropdown_hover_color",
                    "button_color", "button_hover_color", "checkbox_border_color"):
            try:
                cur = w.cget(opt)
            except Exception:
                continue
            if isinstance(cur, str) and cur in color_map:
                try:
                    w.configure(**{opt: color_map[cur]})
                except Exception:
                    pass
        for opt in ("bg", "background", "fg", "foreground",
                    "insertbackground", "selectbackground", "selectforeground"):
            try:
                cur = w.cget(opt)
            except Exception:
                continue
            if isinstance(cur, str) and cur in color_map:
                try:
                    w.configure(**{opt: color_map[cur]})
                except Exception:
                    pass

    def _retheme_tree(self, w, color_map):
        self._retheme_widget(w, color_map)
        try:
            children = w.winfo_children()
        except Exception:
            children = []
        for child in children:
            self._retheme_tree(child, color_map)

    def _retheme(self, accent, mode):
        old = dict(THEME)
        new = build_theme(accent, mode)
        color_map = {}
        for k, v in new.items():
            ov = old.get(k)
            if ov and ov != v:
                color_map[ov] = v
        THEME.clear()
        THEME.update(new)
        self._accent = accent
        self._mode = mode
        ctk.set_appearance_mode(mode)
        self._apply_ttk_style()
        try:
            self.configure(fg_color=THEME["bg_dark"])
        except Exception:
            pass
        self._retheme_tree(self, color_map)

    def _init_optional_modules(self):
        self.activity_tracker = None
        self.smart_boot = None
        self.agent_thread = None
        try:
            from activity_tracker import ActivityTracker
            self.activity_tracker = ActivityTracker(str(CONFIG_PATH), manager=self.manager)
        except Exception:
            self.activity_tracker = None
        try:
            from smart_boot import SmartBootManager
            self.smart_boot = SmartBootManager(
                str(CONFIG_PATH), activity_tracker=self.activity_tracker,
            )
        except Exception:
            self.smart_boot = None
        # The Discord bot runs inside the background agent (so it works with the
        # window closed). It only runs here when the agent is unavailable; see
        # _startup_engine.

    def _discord_configured(self):
        try:
            from core.secrets import bot_token
            try:
                raw = json.loads((APP_DIR / "discord_config.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                raw = {}
            return bool(bot_token(self.manager.config_path.parent, raw))
        except Exception:
            return False

    def _start_discord_here(self):
        if self._discord_started or not self._discord_configured():
            return
        try:
            import discord_bot as discord_module
            discord_module.start_bot(app_instance=self)
            self._discord_started = True
        except Exception as e:
            print("[WARN] Discord:", e)

    # ------------------------------------------------------------------ #
    # Engine startup / background agent
    # ------------------------------------------------------------------ #
    def _startup_engine(self):
        """Make sure the background agent is running and hand server control to it.

        Falls back to running servers from this window if the agent can't start.
        """
        use_agent = self.manager.get_setting("use_background_agent", True)
        if use_agent is not False and not self.manager.agent_available():
            self._show_status("Starting the Watchdog...")
            self._spawn_agent()
            deadline = time.time() + 15
            while time.time() < deadline and not self.manager.agent_available():
                time.sleep(0.5)
                self.manager._agent_checked = 0
        if self.manager.agent_available():
            self._local_mode = False
            self._show_status("Watchdog on - servers keep running when this window closes")
            return
        # Local mode: this window owns server processes.
        self._local_mode = True
        try:
            adopted = self.manager.adopt_running_servers()
        except Exception:
            adopted = []
        if self.activity_tracker is not None:
            try:
                self.activity_tracker.start_polling(60)
            except Exception:
                pass
        self._auto_start_eligible_servers(skip=set(adopted))
        self._start_discord_here()
        self._show_status("Running servers from this window (Watchdog is off)"
                          if use_agent is not False else "Running servers from this window")

    def _spawn_agent(self):
        import subprocess as _sp
        cmd = self._startup_command()
        try:
            flags = 0
            if os.name == "nt":
                flags = _sp.CREATE_NO_WINDOW | _sp.DETACHED_PROCESS | _sp.CREATE_NEW_PROCESS_GROUP
            _sp.Popen(cmd, cwd=str(APP_DIR), creationflags=flags, stdin=_sp.DEVNULL,
                      stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, close_fds=True)
            return True
        except Exception as e:
            print("[WARN] could not start agent:", e)
            return False

    def _poll_linked_pcs(self, again=False):
        """Servers on linked PCs, for the home screen (every 10 s while any are linked)."""
        def work():
            try:
                from core import linked_pcs
                f = linked_pcs.fleet(self.manager)
                sts = f.statuses(max_age=8) if f.pcs() else []
            except Exception as e:
                print("[WARN] linked PCs:", e)
                sts = list(getattr(self, "_remote_statuses", []))
            self.after(0, lambda: self._set_remote(sts))
        threading.Thread(target=work, daemon=True).start()
        if again:
            self.after(10000, lambda: self._poll_linked_pcs(again=True))

    def _set_remote(self, sts):
        changed = sts != getattr(self, "_remote_statuses", [])
        self._remote_statuses = sts
        if changed and getattr(self, "_statuses", None) is not None:
            self._apply_statuses(self._statuses)

    def show_linked_pcs_dialog(self):
        from ui.linked_pcs_dialog import open_linked_pcs_dialog
        open_linked_pcs_dialog(self, THEME)

    def _check_new_version(self, force=False):
        """Is a newer Tofu published? Asks at most once a day (the Watchdog asks too)."""
        def work():
            try:
                from core import updates
                info = updates.check(self.manager, force=force)
            except Exception as e:
                info = {"error": str(e)}
            self.after(0, lambda: self._show_new_version(info, force))
        threading.Thread(target=work, daemon=True).start()
        if not force:
            self.after(6 * 3600 * 1000, self._check_new_version)

    def _show_new_version(self, info, told=False):
        if info.get("newer"):
            self.btn_newver.configure(text="\u2B07 Tofu %s" % info["latest"])
            if not self.btn_newver.winfo_ismapped():
                self.btn_newver.pack(side="right", padx=8, pady=15)
            if told:
                self.show_update_dialog(info)
        elif told:
            from core.version import VERSION
            if not info.get("enabled"):
                msg = "This copy of Tofu has no update source set."
            elif info.get("error"):
                msg = "Couldn't check: %s" % info["error"]
            else:
                msg = "Tofu %s is the newest version." % VERSION
            messagebox.showinfo("Updates", msg)

    def show_update_dialog(self, info=None):
        from ui.update_dialog import open_update_dialog
        open_update_dialog(self, THEME, info)

    def quit_for_update(self):
        """Close so the installer can start the new version (asks first if servers run from this window)."""
        self.on_close()

    def on_close(self):
        local = [sid for sid, info in list(self.manager.processes.items()) if info.is_alive]
        if local:
            ans = messagebox.askyesnocancel(
                "Servers are running",
                str(len(local)) + " server(s) are running from this window.\n\n"
                "Yes - stop them, then close\n"
                "No - leave them running (the Watchdog takes them over when it starts; "
                "their console is unavailable until they are restarted)\n"
                "Cancel - keep Tofu open")
            if ans is None:
                return
            if ans:
                self._show_status("Stopping servers...")
                self.update_idletasks()
                self.manager.shutdown()
        self._polling_active = False
        try:
            if self.activity_tracker is not None:
                self.activity_tracker.stop()
        except Exception:
            pass
        self.destroy()

    def _run_bg(self, label, fn, on_done=None, on_error=None):
        """Run ``fn`` off the UI thread; report through the status bar."""
        if label:
            self._show_status(label)

        def worker():
            try:
                result = fn()
            except Exception as e:
                err = e
                self.after(0, lambda: (on_error(err) if on_error else
                                       messagebox.showerror("Tofu", str(err))))
                self.after(0, self.refresh_servers)
                return
            if on_done:
                self.after(0, lambda: on_done(result))
            self.after(0, self.refresh_servers)
        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        topbar = ctk.CTkFrame(self, height=70, fg_color=THEME["bg_card"])
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        logo_img = load_image("logo.png", size=(48, 48))
        if logo_img:
            ctk.CTkLabel(topbar, image=logo_img, text="").pack(
                side="left", padx=(20, 8), pady=10,
            )
        ctk.CTkLabel(
            topbar, text="Tofu Server Manager",
            font=("Bahnschrift SemiBold", 20, "bold"), text_color=THEME["accent_gold"],
        ).pack(side="left", padx=(0, 20))

        # Visible action buttons (no hidden menus)
        self.btn_settings = ctk.CTkButton(
            topbar, text="Settings", command=self.show_settings_dialog,
            fg_color="transparent", border_color=THEME["border"],
            text_color=THEME["text_light"], width=70,
        )
        self.btn_settings.pack(side="right", padx=8, pady=15)
        self.btn_web = ctk.CTkButton(
            topbar, text="Web Panel", command=self.show_web_panel_dialog,
            fg_color="transparent", border_color=THEME["border"],
            text_color=THEME["text_light"], width=80,
        )
        self.btn_web.pack(side="right", padx=8, pady=15)
        # the background service that keeps servers running: a coloured pill so it's obvious
        self.btn_agent = ctk.CTkButton(
            topbar, text="\u25CB  Watchdog", command=self.toggle_agent,
            fg_color="transparent", border_width=2, border_color=THEME["border"],
            text_color=THEME["text_muted"], width=130, corner_radius=14,
        )
        self.btn_agent.pack(side="right", padx=8, pady=15)
        # shown when a newer Tofu has been deployed onto this PC
        self.btn_update = ctk.CTkButton(
            topbar, text="\u2B06 Update ready", command=self.apply_self_update,
            fg_color=THEME["accent_green"], width=110,
        )
        # shown when a newer Tofu has been published (core/updates.py)
        self.btn_newver = ctk.CTkButton(
            topbar, text="\u2B07 New version", command=self.show_update_dialog,
            fg_color=THEME["accent_blue"], width=120,
        )
        self.after(8000, self._check_new_version)
        self._remote_statuses = []
        self.after(3000, lambda: self._poll_linked_pcs(again=True))
        from core.self_update import Watcher
        self._self_watcher = Watcher()
        self.after(60000, self._check_self_update)
        self.btn_ports = ctk.CTkButton(
            topbar, text="Ports", command=self.show_ports_dialog,
            fg_color="transparent", border_color=THEME["border"],
            text_color=THEME["text_light"], width=70,
        )
        self.btn_ports.pack(side="right", padx=8, pady=15)
        self.btn_ark_settings = ctk.CTkButton(
            topbar, text="ARK World",
            command=self.show_ark_window,
            fg_color="transparent", border_color=THEME["border"],
            text_color=THEME["text_light"], width=70,
        )
        self.btn_ark_settings.pack(side="right", padx=8, pady=15)
        self.btn_ark = ctk.CTkButton(
            topbar, text="ARK Cluster",
            command=self.show_ark_cluster_dialog,
            fg_color=THEME["accent_green"], width=70,
        )
        self.btn_ark.pack(side="right", padx=8, pady=15)
        self.btn_add_server = ctk.CTkButton(
            topbar, text="+ Add Server", command=self.show_add_wizard,
            fg_color=THEME["accent_blue"], width=90,
        )
        self.btn_add_server.pack(side="right", padx=8, pady=15)
        self.btn_home = ctk.CTkButton(
            topbar, text="⌂  Home", command=self.show_home, width=80,
            fg_color="transparent", border_width=1, border_color=THEME["border"],
            text_color=THEME["text_light"],
        )
        self.btn_home.pack(side="left", padx=(0, 8), pady=15)

        statusbar = ctk.CTkFrame(self, height=28, fg_color=THEME["bg_card"], corner_radius=0)
        statusbar.pack(side="bottom", fill="x")
        self.status_bar_label = ctk.CTkLabel(
            statusbar, text="Ready", anchor="w", font=("Segoe UI", 11),
            text_color=THEME["text_muted"],
        )
        self.status_bar_label.pack(side="left", fill="x", expand=True, padx=12)
        self.agent_status_label = ctk.CTkLabel(
            statusbar, text="", anchor="e", font=("Segoe UI", 11),
            text_color=THEME["text_muted"],
        )
        self.agent_status_label.pack(side="right", padx=12)

        main_frame = ctk.CTkFrame(self, fg_color=THEME["bg_dark"])
        self.detail_view = main_frame   # shown by show_manage(); Home is the default view
        self.home_view = HomeView(
            self, THEME, on_toggle=self._card_toggle, on_manage=self.show_manage,
            on_add=self.show_add_wizard, presets=GAME_PRESETS, get_server=self.manager.get_server,
            on_import=self.show_import_dialog, on_checkup=self.show_checkup_dialog,
            get_reach=self._reach_for, on_fix=self._reach_fix)
        self.home_view.pack(fill="both", expand=True)
        self._summary = None
        self._summary_at = 0.0
        self._sparks_at = 0.0

        sidebar = ctk.CTkFrame(
            main_frame, width=300, fg_color=THEME["bg_card"], corner_radius=12,
        )
        sidebar.pack(side="left", fill="y", padx=(0, 12))
        sidebar.pack_propagate(False)
        ctk.CTkLabel(
            sidebar, text="SERVERS", font=("Bahnschrift SemiBold", 13, "bold"),
            text_color=THEME["text_muted"],
        ).pack(pady=(20, 10))
        self.server_tree = ttk.Treeview(
            sidebar, columns=("status",), show="tree headings", height=24,
        )
        self.server_tree.heading("#0", text="Server")
        self.server_tree.heading("status", text="Status")
        self.server_tree.column("#0", width=160)
        self.server_tree.column("status", width=110)
        self.server_tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.server_tree.bind("<<TreeviewSelect>>", self.on_server_selected)

        detail_frame = ctk.CTkFrame(main_frame, fg_color=THEME["bg_dark"])
        detail_frame.pack(side="left", fill="both", expand=True)

        header = ctk.CTkFrame(detail_frame, fg_color=THEME["bg_card"], corner_radius=12)
        header.pack(fill="x", pady=(0, 15))
        self.detail_name_label = ctk.CTkLabel(
            header, text="-", font=("Bahnschrift SemiBold", 18, "bold"),
            text_color=THEME["text_light"],
        )
        self.detail_name_label.pack(side="left", padx=20, pady=15)
        self.status_indicator = ctk.CTkLabel(
            header, text="No server selected",
            font=("Bahnschrift SemiBold", 12), text_color=THEME["status_inactive"],
        )
        self.status_indicator.pack(side="right", padx=20, pady=15)
        self.activity_label = ctk.CTkLabel(
            header, text="", font=("Bahnschrift SemiBold", 11), text_color=THEME["text_muted"],
        )
        self.activity_label.pack(side="left", padx=20, pady=15)

        # Server controls: all visible in clean horizontal columns
        controls = ctk.CTkFrame(detail_frame, fg_color="transparent")
        controls.pack(fill="x", pady=(0, 15))

        col_power = ctk.CTkFrame(controls, fg_color="transparent")
        col_power.pack(side="left", fill="y", padx=(0, 12))
        ctk.CTkLabel(col_power, text="POWER", font=("Bahnschrift SemiBold", 11, "bold"),
                     text_color=THEME["text_muted"]).pack(anchor="w", pady=(0, 6))
        self.btn_start = ctk.CTkButton(col_power, text="Start", fg_color=THEME["success"],
            command=self.start_server)
        self.btn_start.pack(fill="x", pady=(0, 4))
        self.btn_stop = ctk.CTkButton(col_power, text="Stop", fg_color=THEME["danger"],
            command=self.stop_server)
        self.btn_stop.pack(fill="x", pady=(0, 4))
        self.btn_restart = ctk.CTkButton(col_power, text="Restart", fg_color=THEME["warning"],
            command=self.restart_server)
        self.btn_restart.pack(fill="x")

        col_game = ctk.CTkFrame(controls, fg_color="transparent")
        col_game.pack(side="left", fill="y", padx=(0, 12))
        ctk.CTkLabel(col_game, text="GAME", font=("Bahnschrift SemiBold", 11, "bold"),
                     text_color=THEME["text_muted"]).pack(anchor="w", pady=(0, 6))
        self.btn_install = ctk.CTkButton(col_game, text="Install Game", fg_color=THEME["info"],
            command=self.install_game)
        self.btn_install.pack(fill="x", pady=(0, 4))
        self.btn_backup_sched = ctk.CTkButton(col_game, text="Backup Schedule",
            fg_color="transparent", border_color=THEME["border"],
            text_color=THEME["text_light"], command=self.show_backup_schedule_dialog)
        self.btn_backup_sched.pack(fill="x", pady=(0, 4))
        self.btn_restart_sched = ctk.CTkButton(col_game, text="Restart Schedule",
            fg_color="transparent", border_color=THEME["border"],
            text_color=THEME["text_light"], command=self.show_restart_schedule_dialog)
        self.btn_restart_sched.pack(fill="x", pady=(0, 4))
        self.btn_update_sched = ctk.CTkButton(col_game, text="Update Schedule",
            fg_color="transparent", border_color=THEME["border"],
            text_color=THEME["text_light"], command=self.show_update_schedule_dialog)
        self.btn_update_sched.pack(fill="x", pady=(0, 4))
        self.btn_msg_sched = ctk.CTkButton(col_game, text="Announcements",
            fg_color="transparent", border_color=THEME["border"],
            text_color=THEME["text_light"], command=self.show_message_schedule_dialog)
        self.btn_msg_sched.pack(fill="x", pady=(0, 4))
        self.btn_remove = ctk.CTkButton(col_game, text="Remove Server",
            fg_color="transparent", border_color=THEME["danger"],
            text_color=THEME["text_light"], command=self.remove_selected)
        self.btn_remove.pack(fill="x")

        col_bulk = ctk.CTkFrame(controls, fg_color="transparent")
        col_bulk.pack(side="left", fill="y", padx=(0, 12))
        ctk.CTkLabel(col_bulk, text="BULK", font=("Bahnschrift SemiBold", 11, "bold"),
                     text_color=THEME["text_muted"]).pack(anchor="w", pady=(0, 6))
        self.btn_start_all = ctk.CTkButton(
            col_bulk, text="Start All", fg_color=THEME["success"],
            command=self.start_all_servers,
        )
        self.btn_start_all.pack(fill="x", pady=(0, 4))
        self.btn_stop_all = ctk.CTkButton(
            col_bulk, text="Stop All", fg_color=THEME["danger"],
            command=self.stop_all_servers,
        )
        self.btn_stop_all.pack(fill="x", pady=(0, 4))
        self.btn_refresh_all = ctk.CTkButton(
            col_bulk, text="Refresh All", fg_color=THEME["info"],
            command=self.refresh_servers,
        )
        self.btn_refresh_all.pack(fill="x", pady=(0, 4))
        self.btn_update_all = ctk.CTkButton(
            col_bulk, text="Update All", fg_color=THEME["info"],
            command=self.update_all_servers,
        )
        self.btn_update_all.pack(fill="x")

        col_tools = ctk.CTkFrame(controls, fg_color="transparent")
        col_tools.pack(side="left", fill="y", padx=(0, 12))
        ctk.CTkLabel(col_tools, text="TOOLS", font=("Bahnschrift SemiBold", 11, "bold"),
                     text_color=THEME["text_muted"]).pack(anchor="w", pady=(0, 6))
        self.btn_backup_now = ctk.CTkButton(
            col_tools, text="Backup Now", fg_color="transparent",
            border_color=THEME["border"], text_color=THEME["text_light"],
            command=self.backup_now,
        )
        self.btn_backup_now.pack(fill="x", pady=(0, 4))
        self.btn_restore = ctk.CTkButton(
            col_tools, text="Restore", fg_color="transparent",
            border_color=THEME["border"], text_color=THEME["text_light"],
            command=self.restore_backup,
        )
        self.btn_restore.pack(fill="x", pady=(0, 4))
        self.btn_workshop = ctk.CTkButton(
            col_tools, text="Workshop Mods", fg_color="transparent",
            border_color=THEME["border"], text_color=THEME["text_light"],
            command=self.workshop_mods_dialog,
        )   # not shown: the Mods tab replaces it
        self.btn_vs_mods = ctk.CTkButton(
            col_tools, text="VS Mods", fg_color=THEME["accent_gold"],
            text_color=THEME["bg_dark"], command=self.vs_mods_dialog,
        )
        # not shown: the Mods tab replaces it
        self.btn_export = ctk.CTkButton(
            col_tools, text="Export Profile", fg_color="transparent",
            border_color=THEME["border"], text_color=THEME["text_light"],
            command=self.export_profile,
        )
        self.btn_export.pack(fill="x", pady=(0, 4))
        self.btn_import = ctk.CTkButton(
            col_tools, text="Import Profile", fg_color="transparent",
            border_color=THEME["border"], text_color=THEME["text_light"],
            command=self.import_profile,
        )
        self.btn_import.pack(fill="x", pady=(0, 4))
        self.btn_bring = ctk.CTkButton(
            col_tools, text="Bring Servers Here", fg_color="transparent",
            border_color=THEME["border"], text_color=THEME["text_light"],
            command=self.bring_servers_here,
        )
        self.btn_bring.pack(fill="x", pady=(0, 4))
        self.btn_firewall = ctk.CTkButton(
            col_tools, text="Open Firewall", fg_color="transparent",
            border_color=THEME["border"], text_color=THEME["text_light"],
            command=self.open_firewall_for_selected,
        )
        self.btn_firewall.pack(fill="x", pady=(0, 4))
        self.btn_players = ctk.CTkButton(
            col_tools, text="Players (allow list, bans, admins)", fg_color="transparent",
            border_color=THEME["border"], text_color=THEME["text_light"],
            command=self.show_players_dialog,
        )
        self.btn_players.pack(fill="x", pady=(0, 4))
        self.btn_ark_world = ctk.CTkButton(
            col_tools, text="ARK World (creatures, loot)", fg_color="transparent",
            border_color=THEME["border"], text_color=THEME["text_light"],
            command=self.show_ark_window,
        )
        self.btn_ark_world.pack(fill="x", pady=(0, 4))
        self.btn_connect = ctk.CTkButton(
            col_tools, text="How to Connect", fg_color="transparent",
            border_color=THEME["border"], text_color=THEME["text_light"],
            command=self.show_connect_dialog,
        )
        self.btn_connect.pack(fill="x")
        self.tabview = ctk.CTkTabview(
            detail_frame, fg_color=THEME["bg_card"], corner_radius=10,
        )
        self.tabview.pack(fill="both", expand=True)
        tab_live = self.tabview.add("Live Console")
        tab_game = self.tabview.add("Settings")
        tab_mods = self.tabview.add("Mods")
        tab_hist = self.tabview.add("History")
        tab_logs = self.tabview.add("Logs")
        self._build_logs_tab(tab_logs)
        self.live_console = LiveConsole(
            tab_live, THEME, get_log_path=self.manager.get_console_log_path,
            send_command=self._console_send, schedule=self.after,
            get_quick=lambda sid: __import__("core.quick_commands", fromlist=["x"]).for_server(
                self.manager.get_server(sid) or {}),
            get_players=lambda sid: [p.get("name") for st in self._statuses if st.get("id") == sid
                                     for p in st.get("players") or [] if p.get("name")])
        self.live_console.pack(fill="both", expand=True, padx=10, pady=10)
        from ui.mods_tab import ModsPanel
        self.mods_panel = ModsPanel(tab_mods, THEME, self)
        self.mods_panel.pack(fill="both", expand=True, padx=10, pady=10)
        from ui.settings_tab import SettingsPanel
        self.game_panel = SettingsPanel(tab_game, THEME, self)
        self.game_panel.pack(fill="both", expand=True, padx=10, pady=10)
        from ui.history import HistoryPanel
        self.history_panel = HistoryPanel(tab_hist, THEME, get_db=lambda: self.engine.db,
                                          schedule=self.after)
        self.history_panel.pack(fill="both", expand=True, padx=10, pady=10)
        self.tabview.set("Live Console")
        try:
            self.tabview.configure(command=self._on_tab_changed)
        except Exception:
            pass
        self.btn_add_server.pack_forget()   # starting on Home

    # ------------------------------------------------------------------ #
    # Views: Home (cards) and Manage (detail)
    # ------------------------------------------------------------------ #
    def _on_tab_changed(self):
        try:
            if self.tabview.get() == "Mods":
                self.mods_panel.on_shown()
            elif self.tabview.get() == "History":
                self.history_panel.on_shown()
            elif self.tabview.get() == "Settings":
                self.game_panel.on_shown()
        except Exception:
            pass

    def show_home(self):
        self.detail_view.pack_forget()
        self.btn_add_server.pack_forget()   # Home has its own big Add Server button
        self.home_view.pack(fill="both", expand=True)
        self.home_view.refresh(self._statuses, self._summary)

    def show_manage(self, server_id=None):
        if server_id and str(server_id).startswith("@"):
            from ui.remote_server_window import RemoteServerWindow
            RemoteServerWindow(self, THEME, server_id)
            return
        self.home_view.pack_forget()
        if not self.btn_add_server.winfo_ismapped():
            self.btn_add_server.pack(side="right", padx=8, pady=15, after=self.btn_ark)
        self.detail_view.pack(fill="both", expand=True, padx=15, pady=15)
        if server_id and self.server_tree.exists(server_id):
            self.server_tree.selection_set(server_id)
            self.server_tree.see(server_id)
            self.on_server_selected()

    def _card_toggle(self, server_id, state):
        if str(server_id).startswith("@"):        # a server on a linked PC
            action = "server.stop" if state == "running" else "server.start"
            if state not in ("running", "stopped", "crashed", None):
                return
            from core import linked_pcs
            self._run_bg("%s on the other PC..." % ("Stopping" if state == "running" else "Starting"),
                         lambda: linked_pcs.fleet(self.manager).call(server_id, action),
                         on_done=lambda r: self.after(1500, self._poll_linked_pcs))
            return
        if state in ("running",):
            self.stop_server(server_id)
        elif state in ("stopped", "crashed", None):
            self.start_server(server_id)

    def show_add_wizard(self):
        def done(sid):
            self.refresh_servers()
        AddServerWizard(self, THEME, GAME_PRESETS, on_done=done,
                        on_advanced=self.show_add_dialog)

    def _console_send(self, server_id, cmd):
        def done(r):
            if isinstance(r, dict) and r.get("via") == "rcon" and not r.get("echoed"):
                self._show_status("RCON: " + ((r.get("reply") or "sent").splitlines() or ["sent"])[0][:200])
        self._run_bg(None, lambda: self.manager.send_console_command(server_id, cmd, user="gui"),
                     on_done=done, on_error=lambda e: messagebox.showerror("Console error", str(e)))

    # Small helpers used by the wizard and dialogs
    def ask_yes_no(self, title, msg, parent=None):
        return messagebox.askyesno(title, msg, parent=parent or self)

    def show_error(self, title, msg, parent=None):
        messagebox.showerror(title, msg, parent=parent or self)

    def open_folder(self, path):
        try:
            os.startfile(path)
        except Exception as e:
            self._show_status("Could not open folder: " + str(e))

    def save_diagnostics(self):
        """Zip logs, settings and status (secrets removed) and show the file."""
        from core import diagnostics

        def done(path):
            self._show_status("Diagnostics saved: " + path)
            try:
                import subprocess
                subprocess.Popen(["explorer", "/select,", path])
            except Exception:
                self.open_folder(os.path.dirname(path))
        self._run_bg("Collecting diagnostics...", lambda: diagnostics.build(self.manager), on_done=done)

    def base_dir_for_wizard(self):
        """Default parent for new servers: the drive Tofu is on (e.g. F:/GameServers)."""
        return Path(APP_DIR).anchor or str(Path.home())

    def _build_logs_tab(self, parent):
        toolbar = ctk.CTkFrame(parent, fg_color=THEME["bg_card"], corner_radius=8)
        toolbar.pack(fill="x", padx=10, pady=(10, 5))
        ctk.CTkLabel(toolbar, text="Lines:", text_color=THEME["text_light"]).pack(
            side="left", padx=(10, 5),
        )
        self.log_lines_var = ctk.IntVar(value=100)
        ttk.Combobox(
            toolbar, textvariable=self.log_lines_var,
            values=[50, 100, 250, 500, 1000, 5000], state="readonly", width=8,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            toolbar, text="Refresh", command=self.load_logs,
            fg_color=THEME["accent_blue"], width=70,
        ).pack(side="left")
        self.log_text = tkst.ScrolledText(
            parent, wrap="word", bg=THEME["code_bg"], fg=THEME["code_fg"], font=("Cascadia Mono", 10),
        )
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ------------------------------------------------------------------ #
    # Polling
    # ------------------------------------------------------------------ #
    def _start_polling(self):
        self._polling_active = True
        self._poll()

    def _check_self_update(self):
        def work():
            mine = self._self_watcher.check()
            agent = None
            try:
                agent = self.manager._forward("agent.update_status", timeout=10)
            except Exception:
                agent = None
            ready = bool(mine.get("ready") or (agent or {}).get("ready"))
            self.after(0, lambda: self._show_update_button(ready, agent))
        threading.Thread(target=work, daemon=True).start()
        self.after(300000, self._check_self_update)

    def _show_update_button(self, ready, agent):
        self._agent_update = agent or {}
        try:
            if ready and not self.btn_update.winfo_ismapped():
                self.btn_update.pack(side="right", padx=8, pady=15)
                self._show_status("A newer Tofu was deployed to this PC - click Update ready to switch "
                                  "(game servers keep running).")
            elif not ready and self.btn_update.winfo_ismapped():
                self.btn_update.pack_forget()
        except Exception:
            pass

    def apply_self_update(self):
        info = getattr(self, "_agent_update", {}) or {}
        lost = info.get("console_servers") or []
        msg = "Switch to the new Tofu now? Game servers keep running."
        if lost:
            msg += ("\n\nThese servers lose their console window until they're restarted (you can still stop "
                    "them): " + ", ".join(lost))
        if info.get("playing"):
            msg += "\n\nPeople are playing right now - they won't notice, the game servers aren't touched."
        if not self.ask_yes_no("Update Tofu", msg):
            return
        try:
            if info.get("ready"):
                self.manager._forward("agent.update_now", timeout=15)
        except Exception as e:
            self.show_error("Update Tofu", str(e))
            return
        if getattr(sys, "frozen", False):
            import subprocess as _sp
            flags = _sp.DETACHED_PROCESS | _sp.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            try:
                cmd = 'ping -n 9 127.0.0.1 >nul & start "" "%s"' % sys.executable
                _sp.Popen(["cmd", "/c", cmd] if os.name == "nt" else [sys.executable], cwd=str(APP_DIR),
                          creationflags=flags | (_sp.CREATE_NO_WINDOW if os.name == "nt" else 0))
            except Exception as e:
                print("[WARN] could not reopen Tofu:", e)
        self._show_status("Updating - Tofu reopens in a few seconds.")
        self.after(1500, self.destroy)

    def _poll(self):
        if not self._polling_active:
            return
        if not self._poll_inflight:
            self._poll_inflight = True
            threading.Thread(target=self._poll_worker, daemon=True).start()
        self.after(int(self._poll_interval * 1000), self._poll)

    def _poll_worker(self):
        try:
            statuses = self.manager.get_all_statuses()
        except Exception as e:
            print("[WARN] poll:", e)
            statuses = None
        try:
            agent = self.manager.agent_available()
        except Exception:
            agent = False
        summary = None
        if statuses is not None and time.time() - self._summary_at > 10:
            try:
                summary = dashboard_summary(self.manager, statuses, APP_DIR)
                self._summary_at = time.time()
            except Exception as e:
                print("[WARN] dashboard:", e)
        sparks = None
        db = getattr(self.engine, "db", None)
        if statuses is not None and db is not None and time.time() - self._sparks_at > 60:
            self._sparks_at = time.time()
            try:
                from core.history import record, sparklines, system_sample
                if not agent:   # no agent: this window records the history itself
                    record(db, statuses, system_sample(statuses))
                sparks = sparklines(db, [s["id"] for s in statuses])
            except Exception as e:
                print("[WARN] history:", e)

        def apply():
            self._poll_inflight = False
            if statuses is not None:
                self._apply_statuses(statuses, summary)
            if sparks is not None:
                try:
                    self.home_view.set_sparklines(sparks)
                except Exception:
                    pass
            self._update_agent_indicator(agent)
        try:
            self.after(0, apply)
        except Exception:
            self._poll_inflight = False

    def _update_agent_indicator(self, agent):
        try:
            if agent:
                self.agent_status_label.configure(
                    text="\u25CF Watchdog on - keeps servers running when this window closes",
                    text_color=THEME["status_online"])
                self.btn_agent.configure(text="\u25CF  Watchdog on", text_color=THEME["status_online"],
                                         border_color=THEME["status_online"])
            else:
                self.agent_status_label.configure(
                    text="\u25CB Watchdog off - servers run from this window and stop when it closes"
                    if self._local_mode else "\u25CB Watchdog off - no crash restarts or schedules",
                    text_color=THEME["warning"])
                self.btn_agent.configure(text="\u25CB  Watchdog off", text_color=THEME["warning"],
                                         border_color=THEME["warning"])
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Server list / selection
    # ------------------------------------------------------------------ #
    STATE_LABELS = {
        "running": "Online", "starting": "Starting", "updating": "Updating",
        "stopping": "Stopping", "restarting": "Restarting", "crashed": "Crashed",
        "stopped": "Offline",
    }

    def refresh_servers(self):
        if not hasattr(self, "server_tree"):
            return
        try:
            statuses = self.manager.get_all_statuses()
        except Exception as e:
            print("[WARN] refresh_servers:", e)
            return
        self._apply_statuses(statuses)

    def _apply_statuses(self, statuses, summary=None):
        self._statuses = statuses
        if summary is not None:
            self._summary = summary
        if self.home_view.winfo_ismapped() or not self.detail_view.winfo_ismapped():
            self.home_view.refresh(list(statuses) + list(getattr(self, "_remote_statuses", [])), self._summary)
        self._render_tree(statuses)
        self._refresh_selected_status()
        self._update_cluster_button(statuses)

    def _render_tree(self, statuses):
        tree = self.server_tree
        groups = {}
        for st in statuses:
            groups.setdefault((st.get("folder") or "").strip(), []).append(st)
        layout = []
        for folder in sorted(groups.keys(), key=lambda f: (f == "", f.lower())):
            fid = "folder::" + (folder if folder else "__ungrouped__")
            layout.append((fid, folder or "Ungrouped", [st["id"] for st in groups[folder]]))
        current = [(fid, list(tree.get_children(fid))) for fid in tree.get_children("")]
        wanted = [(fid, ids) for fid, _label, ids in layout]
        if current != wanted:
            opened = {fid: bool(tree.item(fid, "open")) for fid in tree.get_children("")}
            selected = self.selected_server_id
            for item in tree.get_children(""):
                tree.delete(item)
            for fid, label, ids in layout:
                tree.insert("", "end", iid=fid, text=label, values=("",), open=opened.get(fid, True))
                for sid in ids:
                    tree.insert(fid, "end", iid=sid, text="", values=("",))
            if selected and tree.exists(selected):
                tree.selection_set(selected)
        for st in statuses:
            state = st.get("state") or ("running" if st.get("isRunning") else "stopped")
            label = self.STATE_LABELS.get(state, state.title())
            if state == "running" and st.get("playerCount") is not None:
                label += " %s/%s" % (st["playerCount"], st.get("maxPlayers") or "?")
            try:
                tree.item(st["id"], text=st.get("name", "Unknown"), values=(label,), tags=(state,))
            except Exception:
                pass
        try:
            tree.tag_configure("running", foreground=THEME["status_online"])
            tree.tag_configure("crashed", foreground=THEME["danger"])
            for t in ("starting", "updating", "stopping", "restarting"):
                tree.tag_configure(t, foreground=THEME["warning"])
        except Exception:
            pass

    def on_server_selected(self, event=None):
        selection = self.server_tree.selection()
        if not selection:
            self.selected_server_id = None
            self._clear_per_server_ui()
            return
        sel = selection[0]
        if sel.startswith("folder::"):
            self.selected_server_id = None
            self._clear_per_server_ui()
            return
        self.selected_server_id = sel
        self.live_console.set_server(sel)
        try:
            self.mods_panel.set_server(sel, visible=self.tabview.get() == "Mods")
            self.history_panel.set_server(sel, visible=self.tabview.get() == "History")
            self.game_panel.set_server(sel, visible=self.tabview.get() == "Settings")
        except Exception:
            pass
        st = next((s for s in self._statuses if s["id"] == self.selected_server_id), None)
        if st:
            self._update_detail_status(st)

    def _clear_per_server_ui(self):
        try:
            self.live_console.set_server(None)
        except Exception:
            pass
        self.detail_name_label.configure(text="-")
        self.status_indicator.configure(text="No server selected")
        self.activity_label.configure(text="")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="disabled")
        self.btn_restart.configure(state="disabled")
        self.btn_install.configure(state="disabled")
        self.btn_backup_sched.configure(state="disabled")
        self.btn_restart_sched.configure(state="disabled")
        self.btn_update_sched.configure(state="disabled")
        self.btn_remove.configure(state="disabled")
        try:
            self.game_panel.set_server(None)
        except Exception:
            pass

    @staticmethod
    def _fmt_uptime(sec):
        if not sec:
            return ""
        sec = int(sec)
        d, rem = divmod(sec, 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        if d:
            return "%dd %dh" % (d, h)
        if h:
            return "%dh %dm" % (h, m)
        return "%dm" % max(m, 1)

    def _update_detail_status(self, status):
        name = status.get("name", "-")
        is_running = bool(status.get("isRunning", False))
        state = status.get("state") or ("running" if is_running else "stopped")
        self.detail_name_label.configure(text=name)
        extra = []
        names = [p.get("name") for p in status.get("players") or [] if p.get("name")]
        if names:
            extra.append("Online: " + ", ".join(names[:6]) + (" +%d" % (len(names) - 6) if len(names) > 6 else ""))
        if status.get("updateAvailable"):
            extra.append("\u2b06 game update available")
        why = (status.get("crashHelp") or [{}])[0].get("title") if state == "crashed" else None
        if why:
            extra.append(why)
        self.activity_label.configure(text="   \u00b7   ".join([x for x in [self._activity_text(status.get("id"))] + extra if x]))
        busy = state in ("starting", "updating", "stopping", "restarting")
        if is_running and not busy:
            parts = ["Online"]
            if status.get("playerCount") is not None:
                parts.append("%s/%s players" % (status["playerCount"], status.get("maxPlayers") or "?"))
            if status.get("cpuPercent") is not None:
                parts.append("CPU %s%%" % status["cpuPercent"])
            if status.get("memUsageMB"):
                mb = status["memUsageMB"]
                parts.append(("%.1f GB" % (mb / 1024)) if mb >= 1024 else ("%d MB" % mb))
            if status.get("uptimeSeconds"):
                parts.append("up " + self._fmt_uptime(status["uptimeSeconds"]))
            self.status_indicator.configure(text="  ·  ".join(parts), text_color=THEME["status_online"])
        elif busy:
            self.status_indicator.configure(text=self.STATE_LABELS.get(state, state) + "...",
                                            text_color=THEME["warning"])
        elif state == "crashed":
            code = status.get("exitCode")
            self.status_indicator.configure(
                text="Crashed" + (" (exit code %s)" % code if code is not None else ""),
                text_color=THEME["danger"])
        else:
            self.status_indicator.configure(text="Offline", text_color=THEME["status_inactive"])
        self.btn_start.configure(state="disabled" if (is_running or busy) else "normal")
        self.btn_stop.configure(state="normal" if (is_running or state in ("updating", "restarting")) else "disabled")
        self.btn_restart.configure(state="normal" if is_running and not busy else "disabled")
        self.live_console.set_console_enabled(bool(status.get("consoleAvailable")))
        srv = self.manager.get_server(status.get("id")) or {}
        from core import game_installers as _gi
        self.btn_install.configure(state="normal" if _gi.kind(srv) in ("steam", "vintage-story", "minecraft", "hytale")
                                   and not is_running else "disabled")
        self.btn_backup_sched.configure(state="normal")
        self.btn_restart_sched.configure(state="normal")
        self.btn_update_sched.configure(state="normal" if srv.get("appId") else "disabled")
        self.btn_remove.configure(state="normal" if not is_running else "disabled")
        self._update_context_buttons(srv)

    def _update_context_buttons(self, srv):
        """Show game-specific tools only when they apply (keeps the window simple)."""
        def show(btn, visible, before):
            try:
                mapped = bool(btn.winfo_manager())
            except Exception:
                mapped = True
            if visible and not mapped:
                btn.pack(fill="x", pady=(0, 4), before=before)
            elif not visible and mapped:
                btn.pack_forget()
        try:
            # mods now live in the Mods tab; the old tool buttons stay hidden
            show(self.btn_vs_mods, False, self.btn_export)
            show(self.btn_workshop, False, self.btn_export)
            from core import game_installers as _gi
            self.btn_install.configure(text="Install / Update" if _gi.kind(srv or {}) in (
                "steam", "vintage-story", "minecraft", "hytale") else "Install Game")
        except Exception:
            pass

    def _update_cluster_button(self, statuses):
        has_cluster = any((self.manager.get_server(st["id"]) or {}).get("clusterId") for st in statuses)
        try:
            mapped = bool(self.btn_ark_settings.winfo_manager())
            if has_cluster and not mapped:
                self.btn_ark_settings.pack(side="right", padx=8, pady=15, before=self.btn_ark)
            elif not has_cluster and mapped:
                self.btn_ark_settings.pack_forget()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Logs
    # ------------------------------------------------------------------ #
    def load_logs(self):
        if not self.selected_server_id:
            return
        try:
            lines_count = max(10, min(5000, self.log_lines_var.get()))
        except Exception:
            lines_count = 200
        sid = self.selected_server_id

        def show(entries):
            if sid != self.selected_server_id:
                return
            self.log_text.config(state="normal")
            self.log_text.delete(1.0, "end")
            self.log_text.insert("end", "\n".join(entries) + ("\n" if entries else ""))   # one insert, not thousands
            self.log_text.config(state="disabled")
            self.log_text.see("end")

        def work():                       # reading a big log off the disk must not freeze the window
            try:
                entries = self.manager.get_logs(sid, last_n=lines_count)
                self.after(0, lambda: show(entries))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: messagebox.showerror("Load Logs Failed", msg))
        threading.Thread(target=work, daemon=True).start()

    def show_add_dialog(self):
        self._open_server_form("Add New Server", None, "Add Server")

    # ------------------------------------------------------------------ #
    # Bulk operations
    # ------------------------------------------------------------------ #
    def start_all_servers(self):
        servers = self.manager.load_servers()
        running = {st["id"] for st in self._statuses if st.get("isRunning")}
        manual = [x for x in servers if x.get("manualStart") or
                  (x.get("clusterId") and x.get("manualStart") is not False)]
        todo = [x for x in servers if x["id"] not in running and x not in manual]
        if not todo:
            messagebox.showinfo("Start All", "Every auto-start server is already running."
                                + ("\n(%d manual-start server(s) skipped.)" % len(manual) if manual else ""))
            return
        if not messagebox.askyesno("Start All", "Start %d server(s)?" % len(todo)
                                   + ("\n\n%d manual-start server(s) (e.g. ARK cluster maps) are skipped."
                                      % len(manual) if manual else "")):
            return

        def run():
            ok, failed = 0, []
            for x in todo:
                try:
                    self.manager.start_server(x["id"], user="gui")
                    ok += 1
                except Exception as e:
                    failed.append(x.get("name", "?") + ": " + str(e))
            return ok, failed

        def done(r):
            ok, failed = r
            self._show_status("Started %d server(s)" % ok)
            if failed:
                messagebox.showwarning("Start All", "Some servers failed to start:\n\n" + "\n".join(failed))
        self._run_bg("Starting %d server(s)..." % len(todo), run, on_done=done)

    def stop_all_servers(self):
        running = [st for st in self._statuses if st.get("isRunning")]
        if not running:
            messagebox.showinfo("Stop All", "No servers are running.")
            return
        if not messagebox.askyesno("Stop All", "Stop %d running server(s)?" % len(running)):
            return

        def run():
            out = {}
            threads = []
            for st in running:
                t = threading.Thread(target=lambda sid=st["id"]: out.__setitem__(
                    sid, self.manager.stop_server(sid, user="gui")), daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join(timeout=360)
            return out
        self._run_bg("Stopping %d server(s) (saving worlds)..." % len(running), run,
                     on_done=lambda r: self._show_status("Stopped %d server(s)" % len(r)))

    # ------------------------------------------------------------------ #
    # Agent
    # ------------------------------------------------------------------ #
    def toggle_agent(self):
        if self.manager.agent_available():
            self.stop_agent()
        else:
            self.start_agent()

    @property
    def agent_running(self):
        return self.manager.agent_available()

    def start_agent(self):
        local = [sid for sid, info in list(self.manager.processes.items()) if info.is_alive]

        def run():
            if not self._spawn_agent():
                raise RuntimeError("Could not start the Watchdog.")
            deadline = time.time() + 15
            while time.time() < deadline:
                self.manager._agent_checked = 0
                if self.manager.agent_available():
                    return True
                time.sleep(0.5)
            raise RuntimeError("The background agent did not respond. See logs\\agent.log.")

        def done(_r):
            self._local_mode = False
            msg = "The Watchdog is on. Servers keep running when this window closes, crashed servers restart and schedules run."
            if local:
                msg += ("\n\n%d server(s) started from this window keep running here until "
                        "you restart them; after that the Watchdog looks after them." % len(local))
            messagebox.showinfo("Watchdog on", msg)
            self._show_status("Watchdog on")
        self._run_bg("Starting the Watchdog...", run, on_done=done,
                     on_error=lambda e: messagebox.showerror("Watchdog didn't start", str(e)))

    def stop_agent(self):
        ans = messagebox.askyesnocancel(
            "Turn off the Watchdog",
            "Turn off the Watchdog?\n\n"
            "Yes - stop all servers too\n"
            "No - leave servers running (no crash restarts, schedules, nightly restart or Discord bot until it's on again)")
        if ans is None:
            return
        client = self.manager.agent_client()
        if client is None:
            self._show_status("The Watchdog is already off")
            return

        def run():
            resp = client.call("agent.shutdown", {"stop_servers": bool(ans)}, timeout=15)
            if not resp.get("success"):
                raise RuntimeError(resp.get("error") or "The Watchdog didn't accept the request")
            deadline = time.time() + (360 if ans else 15)
            while time.time() < deadline:
                self.manager._agent_checked = 0
                if not self.manager.agent_available():
                    return True
                time.sleep(1)
            return False
        self._run_bg("Turning off the Watchdog" + (" and servers..." if ans else "..."), run,
                     on_done=lambda r: self._show_status("Watchdog off"
                                                         if r else "The Watchdog is still shutting down"))

    def show_backup_schedule_dialog(self):
        self._show_schedule_dialog("backup")

    def show_restart_schedule_dialog(self):
        self._show_schedule_dialog("restart")

    def show_update_schedule_dialog(self):
        self._show_schedule_dialog("update")

    def show_message_schedule_dialog(self):
        self._show_schedule_dialog("message")

    def show_ark_window(self):
        from ui.ark_window import open_ark_window
        open_ark_window(self, THEME, getattr(self, "selected_server_id", None))

    def show_watchdog_dialog(self):
        from ui.watchdog_dialog import open_watchdog_dialog
        open_watchdog_dialog(self, THEME)

    def show_players_dialog(self):
        if not self.selected_server_id:
            messagebox.showwarning("No server selected", "Select a server first.")
            return
        from ui.players_dialog import PlayersWindow
        PlayersWindow(self, THEME, self.selected_server_id)

    def show_discord_bot_dialog(self):
        from ui.discord_bot_dialog import open_discord_bot_dialog
        open_discord_bot_dialog(self, THEME)

    def show_first_run(self):
        from ui.first_run import FirstRunWindow
        FirstRunWindow(self, THEME)

    def show_nightly_dialog(self):
        from ui.nightly_dialog import open_nightly_dialog
        open_nightly_dialog(self, THEME)

    def show_checkup_dialog(self):
        from ui.checkup_dialog import open_checkup_dialog
        open_checkup_dialog(self, THEME)

    def show_import_dialog(self):
        from ui.import_dialog import open_import_dialog
        open_import_dialog(self, THEME)

    def _reach_for(self, server_id, running):
        """Last "can friends connect?" answer for a home card; starts a fresh check in the
        background when it's missing or old (the card shows it on the next refresh)."""
        if str(server_id).startswith("@"):
            return None
        from core import reach
        if reach.stale(server_id, running):
            reach.check_async(self.manager, server_id)
        return reach.cached(server_id)

    def _reach_fix(self, server_id, kind=None):
        from core import reach
        from ui.connect_dialog import open_connect_dialog
        reach.forget(server_id)          # check again once they've fixed it
        open_connect_dialog(self, THEME, server_id)

    def show_connect_dialog(self):
        if not self.selected_server_id:
            messagebox.showwarning("No server selected", "Select a server first.")
            return
        from ui.connect_dialog import open_connect_dialog
        open_connect_dialog(self, THEME, self.selected_server_id)

    def show_web_panel_dialog(self):
        from ui.web_settings import open_web_panel_dialog
        open_web_panel_dialog(self, THEME, BASE_DIR)

    def show_ports_dialog(self):
        from ui.ports_dialog import open_ports_dialog
        open_ports_dialog(self, THEME)

    # ------------------------------------------------------------------ #
    # Single-server actions
    # ------------------------------------------------------------------ #
    def open_firewall_for_selected(self):
        sid = self.selected_server_id
        if not sid:
            messagebox.showwarning("No server selected", "Select a server first.")
            return
        name = self._server_name(sid)
        ports = self.manager.firewall_ports(sid)
        if not messagebox.askyesno(
                "Open Windows Firewall",
                "Allow incoming connections for '%s'?\n\nRules for the server program%s will be "
                "added. Windows asks for administrator permission once."
                % (name, (" and ports " + ", ".join(map(str, ports))) if ports else "")):
            return
        try:
            ok = self.manager.open_firewall(sid)
        except Exception as e:
            messagebox.showerror("Firewall", str(e))
            return
        if not ok:
            self._show_status("Firewall change cancelled (administrator permission was not given)")
            return

        def check():
            time.sleep(4)
            return firewall_rule_exists(name)
        self._run_bg("Adding firewall rules for " + name + "...", check,
                     on_done=lambda r: self._show_status(
                         "Firewall rules added for " + name if r else
                         "Could not confirm the firewall rules - check Windows Firewall settings"))

    def remove_selected(self):
        if not self.selected_server_id:
            messagebox.showwarning("No server selected", "Select a server first.")
            return
        srv = next(
            (s for s in self.manager.load_servers()
             if s["id"] == self.selected_server_id), None)
        if not srv:
            return
        if not messagebox.askyesno(
            "Remove Server",
            "Remove '" + srv.get("name", "server") +
            "' from this panel?\n\nServer FILES are NOT deleted."):
            return
        if self.manager.is_running(self.selected_server_id):
            messagebox.showwarning("Server is running",
                                   "Stop '" + srv.get("name", "server") + "' before removing it.")
            return
        servers = [s for s in self.manager.load_servers()
                   if s["id"] != self.selected_server_id]
        self.manager.save_servers(servers)
        self.selected_server_id = None
        self._clear_per_server_ui()
        self.refresh_servers()
        self._show_status("Server removed")

    def _toast(self, title, msg):
        """Non-blocking confirmation in the status bar (instead of an OK popup)."""
        self._show_status(str(title) + " - " + " ".join(str(msg).split()))

    def _show_status(self, msg):
        print("[STATUS]", msg)

        def apply():
            try:
                self.status_bar_label.configure(text=time.strftime("%H:%M  ") + str(msg))
            except Exception:
                pass
        try:
            if threading.current_thread() is threading.main_thread():
                apply()
            else:
                self.after(0, apply)
        except Exception:
            pass

    def _activity_text(self, server_id):
        try:
            if not getattr(self, "activity_tracker", None):
                return ""
            summary = self.activity_tracker.get_activity_summary(server_id)
            inactive = summary.get("consecutive_inactive_days", 0)
            if inactive <= 0:
                return "Active recently"
            return "Idle " + str(inactive) + " day" + ("s" if inactive != 1 else "")
        except Exception:
            return ""

    def update_all_servers(self):
        servers = self.manager.load_servers()
        updatable = [s for s in servers if s.get("appId")]
        if not updatable:
            messagebox.showinfo("Update All", "No SteamCMD servers to update.")
            return
        # One update per install folder (all maps of an ARK cluster share one).
        seen, groups = set(), []
        for s in updatable:
            key = os.path.normcase(os.path.normpath(s.get("directory") or ""))
            if key not in seen:
                seen.add(key)
                groups.append(s)
        if not messagebox.askyesno(
            "Update All",
            "Check %d install(s) for Steam updates?\n\n"
            "Only installs with a newer build are downloaded. Running servers get an in-game "
            "warning, are stopped for the update, and restarted afterwards." % len(groups)):
            return

        def run():
            updated, current, failed = [], [], []
            for s in groups:
                name = s.get("name", "?")
                self.after(0, lambda n=name: self._show_status("Checking/updating " + n + "..."))
                resp = self.engine.api.process("server.update", {"server_id": s["id"]}, user_id="gui")
                if not resp.success:
                    failed.append(name + ": " + str(resp.error)[:200])
                elif (resp.data or {}).get("status") == "up_to_date":
                    current.append(name)
                else:
                    updated.append(name)
            lines = ["Updated: %d   Already up to date: %d   Failed: %d"
                     % (len(updated), len(current), len(failed))]
            if failed:
                lines += [""] + failed
            self.after(0, lambda: (messagebox.showwarning if failed else messagebox.showinfo)(
                "Update All", "\n".join(lines)))
            self.after(0, self.refresh_servers)

        threading.Thread(target=run, daemon=True).start()

    def _monitor_startup(self, server_id):
        def check():
            time.sleep(6)
            for _ in range(600):  # wait out a SteamCMD update (up to ~1h)
                try:
                    st = next((x for x in self.manager.get_all_statuses()
                               if x["id"] == server_id), None)
                except Exception:
                    return
                if st is None or st.get("state") not in ("updating", "starting"):
                    break
                time.sleep(6)
            if st is None or st.get("isRunning"):
                return
            if st.get("state") not in ("crashed", "restarting"):
                return  # stopped on purpose, or still busy
            path = self.manager.get_console_log_path(server_id)
            from server_manager import tail_lines
            logs = tail_lines(path, 40) if path else []
            tail = "\n".join(logs[-12:]) if logs else "(no log output captured)"
            problems = []
            if self.engine.health is not None:
                try:
                    problems = self.engine.health.check_server(server_id).get("problems", [])
                except Exception:
                    problems = []
            from core.diagnose import explain
            help_ = list(st.get("crashHelp") or [])
            seen = {h["title"] for h in help_}
            for h in explain(None, [], {}, health_problems=problems):
                if h["title"] not in seen:
                    help_.append(h)
            self.after(0, lambda: self._show_crash_dialog(
                st.get("name", "server"), st.get("exitCode"), tail, help_))
        threading.Thread(target=check, daemon=True).start()

    def _show_crash_dialog(self, name, exit_code, tail, help_=None):
        """Explain why a server stopped, in plain words, with what to do about it."""
        help_ = [h if isinstance(h, dict) else {"title": str(h), "fix": ""} for h in (help_ or [])]
        parts = []
        for h in help_[:3]:
            parts.append("\u2022 " + h["title"] + ("\n   " + h["fix"] if h.get("fix") else ""))
        diag = ("What went wrong:\n" + "\n".join(parts) + "\n\n") if parts else ""
        body = (name + " stopped by itself"
                + (" (exit code " + str(exit_code) + ")" if exit_code not in (None, 0) else "")
                + ".\n\n" + diag + "Last lines of the console:\n" + tail)
        link = next((h.get("link") for h in help_ if h.get("link")), None)
        if link:
            if messagebox.askyesno(name + " stopped", body + "\n\nOpen the download page now?"):
                import webbrowser
                webbrowser.open(link)
        else:
            messagebox.showwarning(name + " stopped", body + "\n\nThe Live Console tab has the full output.")

    def _startup_command(self):
        frozen = getattr(sys, "frozen", False)
        if frozen:
            return [sys.executable, "--agent"]
        return [sys.executable, str(BASE_DIR / "tofusm_agent.py")]

    def is_run_at_startup(self):
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_READ) as k:
                try:
                    winreg.QueryValueEx(k, "TofuSMAgent")
                except FileNotFoundError:
                    winreg.QueryValueEx(k, "BaoBunAgent")     # set up before the rename
            return True
        except Exception:
            return False

    def _move_old_startup_entry(self):
        """The "start with Windows" entry made before the rename starts the old program file
        (BaoBunCP.exe), which the installer removes: make it again for this one."""
        if os.name != "nt":
            return
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                                0, winreg.KEY_READ) as k:
                winreg.QueryValueEx(k, "BaoBunAgent")
        except Exception:
            return
        try:
            self.set_run_at_startup(True)
        except Exception as e:
            print("[WARN] moving the start-with-Windows entry:", e)

    def set_run_at_startup(self, enabled):
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        parts = self._startup_command()
        cmd = '"' + parts[0] + '"'
        for a in parts[1:]:
            cmd += " " + ('"' + a + '"' if " " in a else a)
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, "TofuSMAgent", 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, "TofuSMAgent")
                except FileNotFoundError:
                    pass
            try:
                winreg.DeleteValue(k, "BaoBunAgent")          # the old name's entry, either way
            except FileNotFoundError:
                pass

    def center_window(self, dlg, width, height, fit=True):
        dlg.update_idletasks()
        try:
            x = self.winfo_rootx() + (self.winfo_width() - width) // 2
            y = self.winfo_rooty() + (self.winfo_height() - height) // 2
            dlg.geometry(
                str(width) + "x" + str(height) + "+" +
                str(max(x, 0)) + "+" + str(max(y, 0)))
        except Exception:
            dlg.geometry(str(width) + "x" + str(height))
        if fit:
            self.after(0, lambda: self._fit_dialog(dlg, width))

    def _fit_dialog(self, dlg, min_width):
        try:
            if not dlg.winfo_exists():
                return
            dlg.update_idletasks()
            req_w = dlg.winfo_reqwidth()
            req_h = dlg.winfo_reqheight()
            w = max(req_w + 44, int(min_width))
            h = max(req_h + 28, 220)
            w = min(w, dlg.winfo_screenwidth() - 40)
            h = min(h, dlg.winfo_screenheight() - 100)
            x = max(self.winfo_rootx() + (self.winfo_width() - w) // 2, 0)
            y = max(self.winfo_rooty() + (self.winfo_height() - h) // 2, 0)
            dlg.geometry(str(w) + "x" + str(h) + "+" + str(x) + "+" + str(y))
        except Exception:
            pass

    def _refresh_selected_status(self):
        if not self.selected_server_id:
            return
        st = next((s for s in self._statuses
                   if s["id"] == self.selected_server_id), None)
        if st:
            self._update_detail_status(st)

    def _server_name(self, sid):
        return (self.manager.get_server(sid) or {}).get("name", "server")

    def start_server(self, sid=None):
        sid = sid or self.selected_server_id
        if not sid:
            return
        name = self._server_name(sid)
        self.btn_start.configure(state="disabled")

        def done(result):
            status = result.get("status")
            if status == "updating":
                self._show_status("Updating " + name + " (SteamCMD) - it starts when the update finishes")
            elif status in ("already_running", "already_starting"):
                self._show_status(name + " is already running")
            else:
                self._show_status("Started " + name + " (PID " + str(result.get("pid")) + ")")
            self._monitor_startup(sid)
        self._run_bg("Starting " + name + "...",
                     lambda: self.manager.start_server(sid, user="gui"), on_done=done,
                     on_error=lambda e: messagebox.showerror("Start Failed", str(e)))

    def stop_server(self, sid=None):
        sid = sid or self.selected_server_id
        if not sid:
            return
        name = self._server_name(sid)
        self.btn_stop.configure(state="disabled")
        self._run_bg("Stopping " + name + " (saving world)...",
                     lambda: self.manager.stop_server(sid, user="gui"),
                     on_done=lambda r: self._show_status(
                         "Stopped " + name + ("" if r.get("graceful", True) else " (forced - it did not exit in time)")),
                     on_error=lambda e: messagebox.showerror("Stop Failed", str(e)))

    def restart_server(self, sid=None):
        sid = sid or self.selected_server_id
        if not sid:
            return
        name = self._server_name(sid)
        self.btn_restart.configure(state="disabled")
        self._run_bg("Restarting " + name + "...",
                     lambda: self.manager.restart_server(sid, user="gui"),
                     on_done=lambda r: (self._show_status("Restarted " + name), self._monitor_startup(sid)),
                     on_error=lambda e: messagebox.showerror("Restart Failed", str(e)))

    def maybe_show_onboarding(self):
        """The first-run walkthrough, once, on a fresh install (core/first_run.py)."""
        try:
            from core import first_run
            if first_run.needed(self.manager):
                self.show_first_run()
        except Exception as e:
            print("[WARN] first-run walkthrough:", e)

    def backup_now(self):
        if not self.selected_server_id:
            messagebox.showwarning("No server selected", "Select a server first.")
            return
        sid = self.selected_server_id

        def done(r):
            size = r.get("bytes", 0) / (1024 * 1024)
            self._show_status("Backed up %d files (%.1f MB)" % (r["files"], size))
            msg = "Backed up %d file(s) (%.1f MB) to:\n%s" % (r["files"], size, r["zip_path"])
            if r.get("pruned"):
                msg += "\n\nRemoved %d old backup(s) (keeping the newest %s)." % (
                    r["pruned"], self.manager.get_setting("backup_keep", 10))
            if r.get("warning"):
                messagebox.showwarning("Backup Complete - check this", msg + "\n\n" + r["warning"])
            else:
                self._toast("Backup complete", msg.split("\n")[0])
        self._run_bg("Backing up " + self._server_name(sid) + "...",
                     lambda: self.manager.backup_server_world(sid), on_done=done,
                     on_error=lambda e: messagebox.showerror("Backup Failed", str(e)))

    def restore_backup(self):
        if not self.selected_server_id:
            messagebox.showwarning("No server selected", "Select a server first.")
            return
        backups = self.manager.list_server_backups(self.selected_server_id, include_mirror=True)
        initial_dir = str(Path(backups[0]["path"]).parent) if backups else ""
        if not backups:
            messagebox.showinfo("No Backups", "No backups found for this server yet.")
            return
        from ui.restore_dialog import pick_backup
        from core import backup_check
        chk = backup_check.summary(self.manager, self.selected_server_id)
        choice = pick_backup(self, THEME, backups, chk, initial_dir)
        if not choice:
            return
        if not messagebox.askyesno(
            "Restore Backup",
            "Restore this backup into the server directory?\n\n"
            "This overwrites save/config files (a safety backup is made first)."):
            return
        try:
            r = self.manager.restore_server_world(self.selected_server_id, choice)
            self.refresh_servers()
            msg = "Restored " + str(r["restored"]) + " file(s)."
            if r.get("pre_backup"):
                msg += "\nSafety backup: " + r["pre_backup"]
            messagebox.showinfo("Restore Complete", msg)
        except Exception as e:
            messagebox.showerror("Restore Failed", str(e))

    def export_profile(self):
        if not self.selected_server_id:
            messagebox.showwarning("No server selected", "Select a server first.")
            return
        srv = next((s for s in self.manager.load_servers()
                    if s["id"] == self.selected_server_id), None)
        if not srv:
            return
        default = "".join(c if c.isalnum() or c in "-_." else "_"
                          for c in (srv.get("name") or "server")) + ".json"
        path = filedialog.asksaveasfilename(
            title="Export server profile", defaultextension=".json",
            initialfile=default, filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            self.manager.export_server_profile(self.selected_server_id, path)
            self._show_status("Exported profile")
            self._toast("Exported", "Server profile saved to:\n" + path)
        except Exception as e:
            messagebox.showerror("Export Failed", str(e))

    def import_profile(self):
        path = filedialog.askopenfilename(
            title="Import server profile", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            imp = self.manager.import_server_profile(path)
            self.refresh_servers()
            self._show_status("Imported '" + imp["name"] + "'")
            self._toast("Imported", "Added server '" + imp["name"] + "'.")
        except Exception as e:
            messagebox.showerror("Import Failed", str(e))

    def bring_servers_here(self):
        """Set servers up again on this PC from their backup copies (core/bring_here.py)."""
        from ui.bring_here_dialog import BringHereWindow
        BringHereWindow(self, THEME)

    def install_game(self):
        if not self.selected_server_id:
            return
        servers = self.manager.load_servers()
        srv = next((s for s in servers
                    if s["id"] == self.selected_server_id), None)
        if not srv:
            return
        app_id = srv.get("appId") or ""
        directory = srv.get("directory", "")
        srv_type = srv.get("type", "custom")
        if not app_id:
            try:
                from ui.install_dialog import open_install_dialog
                if open_install_dialog(self, THEME, srv["id"]):
                    return
            except Exception as e:
                print("[WARN] install dialog:", e)
            self._show_manual_install_help(srv_type, directory)
            return
        busy = [self._server_name(sid) for sid in self.manager.install_group(srv["id"])
                if self.manager.is_running(sid)]
        if busy:
            messagebox.showwarning(
                "Servers are using these files",
                "Stop these servers first (they share this install folder):\n\n" + "\n".join(busy)
                + "\n\nOr use Update All / scheduled updates, which stop and restart them for you.")
            return
        if srv.get("requiresAccount"):
            u = self.manager.get_setting("steam_username", "") or ""
            pw = self.manager.get_setting("steam_password", "") or ""
            if not (u and pw):
                messagebox.showwarning(
                    "Steam login required",
                    "This game requires a Steam account login (it cannot be "
                    "downloaded anonymously). Open Settings (gear icon), enter "
                    "your Steam username and password, then try again.")
                return
        if not messagebox.askyesno(
            "Install Game",
            "Download/update App ID " + app_id + " into:\n" + directory +
            "\n\nThis may download many GB and take a while. Continue?"):
            return

        def run():
            try:
                self.after(0, lambda: self._show_status(
                    "Installing App " + app_id + " ..."))
                self.manager.install_steamcmd_game(
                    self.selected_server_id, app_id)
                self.after(0, lambda: self._show_status(
                    "Install completed for App " + app_id))
                self.after(0, lambda: messagebox.showinfo(
                    "Install complete",
                    "Game installed to:\n" + directory))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: messagebox.showerror(
                    "Install failed", msg))

        threading.Thread(target=run, daemon=True).start()

    def _show_manual_install_help(self, srv_type, directory):
        nl = chr(10)
        if srv_type == "vintage-story":
            hint = (
                "Vintage Story's dedicated server comes from vintagestory.at, not Steam. "
                "Use Install to download it, or unzip it into:"
                + nl + nl + directory + nl + nl
                + "The program is VintagestoryServer.exe (custom builds such as StratumServer.exe work too)."
            )
        else:
            hint = (
                "This server is manually installed (no Steam app to download). "
                "Place the game's dedicated server files into:"
                + nl + nl + directory + nl + nl
                + "Then confirm the executable in Edit Server."
            )
        if directory and os.path.isdir(directory):
            if messagebox.askyesno(
                "Manual install", hint + nl + nl + "Open this folder now?"):
                try:
                    os.startfile(directory)
                except Exception:
                    pass
        else:
            messagebox.showinfo("Manual install", hint)

    def _auto_start_eligible_servers(self, skip=()):
        """Local-mode boot: same rules the agent uses (smart boot + manual-start flags)."""
        for srv in self.manager.load_servers():
            sid = srv.get("id")
            if not sid or sid in skip or self.manager.is_running(sid):
                continue
            rec = self.manager._read_registry(sid)
            if float(rec.get("updated") or 0) >= self._session_start:
                continue  # touched since this window opened: the user is in charge
            was_running = rec.get("state") in ("running", "restarting", "starting", "updating")
            if self.smart_boot is not None:
                start, reason = self.smart_boot.decide(srv, was_running=was_running)
            else:
                start, reason = was_running, ""
            if not start:
                continue
            try:
                self.manager.start_server(sid, user="smart-boot")
                self._show_status("Auto-started " + srv.get("name", "server") + " (" + reason + ")")
            except Exception as e:
                print("[WARN] auto-start:", srv.get("name"), e)


if __name__ == "__main__":
    if "--agent" in sys.argv:
        import tofusm_agent
        tofusm_agent.main()
    else:
        try:
            app = TofuApp()
            app.mainloop()
        except Exception:
            # With no console (start.bat uses pythonw), make startup errors visible.
            import traceback
            err = traceback.format_exc()
            try:
                (BASE_DIR / "logs").mkdir(exist_ok=True)
                (BASE_DIR / "logs" / "app_error.log").write_text(err, encoding="utf-8")
            except Exception:
                pass
            try:
                messagebox.showerror("Tofu could not start",
                                     err[-1500:] + "\n\n(saved to logs\\app_error.log)")
            except Exception:
                pass
            raise
