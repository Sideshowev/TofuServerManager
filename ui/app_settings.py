"""Settings, schedules and help windows. Part of the desktop app (BaoBunApp in app.py)."""
# flake8: noqa
import json
import time

import customtkinter as ctk
import tkinter.messagebox as messagebox
from tkinter import filedialog

from ui.app_common import BASE_DIR, ACCENTS, THEME


class SettingsDialogsMixin:
    def show_settings_dialog(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Settings")
        dlg.geometry("620x580")
        dlg.grab_set()
        dlg.transient(self)
        self.center_window(dlg, 620, 580, fit=False)
        form = ctk.CTkScrollableFrame(dlg, fg_color=THEME["bg_card"], corner_radius=10)
        form.pack(fill="both", expand=True, padx=20, pady=(20, 0))

        install_var = ctk.StringVar(
            value=self.manager.get_setting("default_install_dir", "") or "")
        steamcmd_var = ctk.StringVar(
            value=self.manager.get_setting("steamcmd_path", "") or "")
        user_var = ctk.StringVar(
            value=self.manager.get_setting("steam_username", "") or "")
        pass_var = ctk.StringVar(
            value=self.manager.get_setting("steam_password", "") or "")

        def field(label, var, ph=""):
            ctk.CTkLabel(
                form, text=label, anchor="w", text_color=THEME["text_light"],
            ).pack(fill="x", padx=10, pady=(10, 0))
            ctk.CTkEntry(form, textvariable=var, placeholder_text=ph).pack(
                fill="x", padx=10, pady=(0, 4),
            )

        ctk.CTkLabel(
            form, text="Settings", font=("Bahnschrift SemiBold", 18, "bold"),
        ).pack(pady=(15, 5))
        field("Default install parent folder", install_var, "e.g. D:/GameServers")
        field("SteamCMD path (blank = auto-detect)", steamcmd_var,
              "Full path to steamcmd.exe")

        ctk.CTkLabel(
            form, text="Steam login (for games that require a purchase)",
            anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", padx=10, pady=(16, 0))
        field("Steam username (blank = anonymous)", user_var)
        field("Steam password", pass_var)
        ctk.CTkLabel(
            form,
            text="Leave blank to use anonymous login (works for most games). "
            "For account login use a dedicated Steam account; Steam Guard "
            "may prompt once on first login.",
            anchor="w", justify="left", wraplength=560,
            text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10, pady=(4, 6))
        startup_var = ctk.BooleanVar(value=self.is_run_at_startup())
        ctk.CTkCheckBox(
            form, text="Start the Watchdog when I sign in to Windows",
            variable=startup_var, fg_color=THEME["accent_blue"],
        ).pack(fill="x", padx=10, pady=(14, 4))
        ctk.CTkButton(
            form, text="Nightly restart & start at boot...", fg_color=THEME["border"],
            command=lambda: (dlg.destroy(), self.show_nightly_dialog()),
        ).pack(anchor="w", padx=10, pady=(4, 4))
        ctk.CTkButton(
            form, text="PC-down alerts (Discord message when the server PC stops answering)...", fg_color=THEME["border"],
            command=lambda: (dlg.destroy(), self.show_watchdog_dialog()),
        ).pack(anchor="w", padx=10, pady=(0, 4))
        ctk.CTkButton(
            form, text="Save diagnostics (a zip to share when something goes wrong - passwords removed)...",
            fg_color=THEME["border"], command=self.save_diagnostics,
        ).pack(anchor="w", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            form, text="Restart the server PC every night, bring back the servers people use, and start "
            "BaoBun when the PC turns on (no sign-in needed).",
            anchor="w", justify="left", wraplength=560, text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10, pady=(0, 6))

        # ---- Appearance ----
        ctk.CTkLabel(
            form, text="Appearance", anchor="w", justify="left",
            text_color=THEME["text_light"], font=("Bahnschrift SemiBold", 13, "bold"),
        ).pack(fill="x", padx=10, pady=(16, 2))
        accent_var = ctk.StringVar(value=self.manager.get_setting("theme_accent", "gold") or "gold")
        mode_var = ctk.StringVar(value=self.manager.get_setting("appearance_mode", "dark") or "dark")

        ctk.CTkLabel(
            form, text="Accent color", anchor="w", text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10, pady=(4, 2))
        swatch_row = ctk.CTkFrame(form, fg_color="transparent")
        swatch_row.pack(fill="x", padx=10, pady=(0, 2))
        accent_name_var = ctk.StringVar(value=accent_var.get().capitalize())

        def pick_accent(name):
            accent_var.set(name)
            accent_name_var.set(name.capitalize())

        for name, cfg in ACCENTS.items():
            ctk.CTkButton(
                swatch_row, text="", width=30, height=30, corner_radius=6,
                fg_color=cfg["p"], hover_color=cfg["s"],
                border_width=2, border_color=THEME["border"],
                command=lambda n=name: pick_accent(n),
            ).pack(side="left", padx=4)
        ctk.CTkLabel(
            swatch_row, textvariable=accent_name_var, width=80, anchor="w",
            text_color=THEME["text_muted"],
        ).pack(side="left", padx=10)

        ctk.CTkLabel(
            form, text="Theme brightness", anchor="w", text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10, pady=(10, 2))
        mode_row = ctk.CTkFrame(form, fg_color="transparent")
        mode_row.pack(fill="x", padx=10, pady=(0, 2))
        ctk.CTkRadioButton(
            mode_row, text="Dark", variable=mode_var, value="dark",
            fg_color=THEME["accent_blue"],
        ).pack(side="left", padx=8)
        ctk.CTkRadioButton(
            mode_row, text="Light", variable=mode_var, value="light",
            fg_color=THEME["accent_blue"],
        ).pack(side="left", padx=8)
        ctk.CTkLabel(
            form, text="Theme changes apply immediately.",
            anchor="w", text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10, pady=(2, 0))

        ctk.CTkLabel(
            form, text="Crash prevention", anchor="w",
            text_color=THEME["text_light"],
            font=("Bahnschrift SemiBold", 13, "bold"),
        ).pack(fill="x", padx=10, pady=(8, 2))

        # Max restarts on crash
        ctk.CTkLabel(
            form, text="Max restarts on crash", anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", padx=10, pady=(4, 0))
        restart_var = ctk.StringVar(value=str(self.manager.get_setting("loop_max_restarts", "5")))
        ctk.CTkEntry(form, textvariable=restart_var).pack(fill="x", padx=10, pady=(0, 4))

        # Stability window in minutes
        ctk.CTkLabel(
            form, text="Stability window (minutes)", anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", padx=10, pady=(4, 0))
        stable_var = ctk.StringVar(value=str(self.manager.get_setting("loop_stability_minutes", "5")))
        ctk.CTkEntry(form, textvariable=stable_var).pack(fill="x", padx=10, pady=(0, 4))

        ctk.CTkLabel(
            form, text="Backups to keep per server (older ones are deleted)", anchor="w",
            text_color=THEME["text_light"],
        ).pack(fill="x", padx=10, pady=(4, 0))
        keep_var = ctk.StringVar(value=str(self.manager.get_setting("backup_keep", 10)))
        ctk.CTkEntry(form, textvariable=keep_var).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            form, text="Also copy every backup to (another drive or a network folder - blank = off)",
            anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", padx=10, pady=(4, 0))
        mirror_var = ctk.StringVar(value=str(self.manager.get_setting("backup_mirror_dir", "") or ""))
        mrow = ctk.CTkFrame(form, fg_color="transparent")
        mrow.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkEntry(mrow, textvariable=mirror_var, placeholder_text="e.g. E:/BaoBun backups or \\\\NAS\\backups").pack(
            side="left", fill="x", expand=True)

        def browse_mirror():
            chosen = filedialog.askdirectory(title="Second backup folder")
            if chosen:
                mirror_var.set(chosen)
        ctk.CTkButton(mrow, text="Browse", width=80, fg_color=THEME["border"], command=browse_mirror).pack(
            side="left", padx=(6, 0))
        ms = self.manager.mirror_status()
        mirror_lbl = ctk.CTkLabel(
            form, anchor="w", justify="left", wraplength=560, text_color=THEME["text_muted"],
            text=("Last copy: OK" if ms.get("ok") else ("Last copy FAILED: " + str(ms.get("error") or ""))
                  if ms.get("ok") is False else "") if ms else "")
        mirror_lbl.pack(fill="x", padx=10)

        def test_mirror():
            r = self.manager.test_mirror(mirror_var.get().strip())
            mirror_lbl.configure(text=("\u2705 BaoBun can write there (%.1f GB free)." % r["free_gb"]) if r.get("ok")
                                 else "\u274C Can't write there: " + str(r.get("error")),
                                 text_color=THEME["status_online"] if r.get("ok") else THEME["danger"])
        ctk.CTkButton(mrow, text="Test", width=60, fg_color=THEME["border"], command=test_mirror).pack(
            side="left", padx=(6, 0))
        agent_var = ctk.BooleanVar(value=self.manager.get_setting("use_background_agent", True) is not False)
        ctk.CTkCheckBox(
            form, text="Run servers through the Watchdog (keeps them running when this window closes)",
            variable=agent_var, fg_color=THEME["accent_blue"],
        ).pack(anchor="w", padx=10, pady=(6, 4))
        ctk.CTkLabel(
            form, text="Alerts", anchor="w", text_color=THEME["text_light"],
            font=("Bahnschrift SemiBold", 13, "bold"),
        ).pack(fill="x", padx=10, pady=(16, 2))
        ctk.CTkLabel(
            form, text="Discord messages for crashes, failed updates/backups, high RAM and low disk.",
            anchor="w", text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10, pady=(0, 4))

        def open_alerts():
            from ui.alerts_settings import open_alerts_dialog
            open_alerts_dialog(self, THEME)
        ctk.CTkButton(form, text="Set up Discord alerts...", command=open_alerts,
                      fg_color=THEME["border"]).pack(anchor="w", padx=10, pady=(0, 4))
        ctk.CTkButton(form, text="Discord bot (control panel, alerts, game nights)...",
                      command=self.show_discord_bot_dialog,
                      fg_color=THEME["border"]).pack(anchor="w", padx=10, pady=(0, 4))
        ctk.CTkButton(form, text="Linked PCs (control servers on other PCs from here)...",
                      command=self.show_linked_pcs_dialog,
                      fg_color=THEME["border"]).pack(anchor="w", padx=10, pady=(0, 4))
        ctk.CTkButton(form, text="Run the setup walkthrough again...", command=self.show_first_run,
                      fg_color=THEME["border"]).pack(anchor="w", padx=10, pady=(0, 4))
        from core.version import VERSION
        ctk.CTkButton(form, text="BaoBun %s - check for updates" % VERSION,
                      command=lambda: self._check_new_version(force=True),
                      fg_color=THEME["border"]).pack(anchor="w", padx=10, pady=(0, 4))
        # ---- UI scaling ----
        ctk.CTkLabel(
            form, text="UI Scale", anchor="w",
            text_color=THEME["text_light"],
            font=("Bahnschrift SemiBold", 13, "bold"),
        ).pack(fill="x", padx=10, pady=(16, 2))
        ctk.CTkLabel(
            form, text="Larger for high-DPI (4K) screens, smaller for 1080p.",
            anchor="w", text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10, pady=(0, 4))
        scale_var = ctk.StringVar(value=str(self.ui_scale))
        scale_row = ctk.CTkFrame(form, fg_color="transparent")
        scale_row.pack(fill="x", padx=10, pady=(0, 8))
        for _val in ("1.0 (100%)", "1.25 (125%)", "1.5 (150%)", "2.0 (200%)"):
            ctk.CTkRadioButton(
                scale_row, text=_val, variable=scale_var, value=_val,
                fg_color=THEME["accent_blue"],
            ).pack(side="left", padx=(0, 12))

        def browse_install():
            chosen = filedialog.askdirectory(title="Default install folder")
            if chosen:
                install_var.set(chosen)

        def browse_steamcmd():
            chosen = filedialog.askopenfilename(
                title="Select steamcmd.exe",
                filetypes=[("steamcmd.exe", "*.exe")],
            )
            if chosen:
                steamcmd_var.set(chosen)

        row = ctk.CTkFrame(form, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(10, 0))
        ctk.CTkButton(
            row, text="Browse install", fg_color=THEME["border"],
            command=browse_install,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            row, text="Browse steamcmd", fg_color=THEME["border"],
            command=browse_steamcmd,
        ).pack(side="left")

        def save():
            self.manager.set_setting(
                "default_install_dir", install_var.get().strip())
            self.manager.set_setting("steamcmd_path", steamcmd_var.get().strip())
            self.manager.set_setting("steam_username", user_var.get().strip())
            self.manager.set_setting("steam_password", pass_var.get().strip())
            self.set_run_at_startup(startup_var.get())
            try:
                self.manager.set_setting("loop_max_restarts", int(restart_var.get()))
            except Exception:
                pass
            try:
                self.manager.set_setting("loop_stability_minutes", float(stable_var.get()))
            except Exception:
                pass
            try:
                self.manager.set_setting("backup_keep", max(1, int(keep_var.get())))
            except Exception:
                pass
            self.manager.set_setting("backup_mirror_dir", mirror_var.get().strip())
            self.manager.set_setting("use_background_agent", bool(agent_var.get()))
            self.manager.set_setting("theme_accent", accent_var.get())
            self.manager.set_setting("appearance_mode", mode_var.get())
            try:
                _sel = scale_var.get()
                _num = float(_sel.split(" ")[0])
                self.manager.set_setting("ui_scale", _num)
                ctk.set_window_scaling(_num)
                ctk.set_widget_scaling(_num)
            except Exception:
                pass
            self._retheme(accent_var.get(), mode_var.get())
            dlg.destroy()
            self._show_status("Settings saved")

        btns = ctk.CTkFrame(dlg, fg_color=THEME["bg_card"], corner_radius=10)
        btns.pack(fill="x", padx=20, pady=(8, 20))
        ctk.CTkButton(
            btns, text="Save", fg_color=THEME["success"], command=save,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            btns, text="Cancel", fg_color=THEME["border"], command=dlg.destroy,
        ).pack(side="right", padx=6)

    # ------------------------------------------------------------------ #
    # Schedules
    # ------------------------------------------------------------------ #
    def _show_schedule_dialog(self, kind):
        if not self.selected_server_id:
            messagebox.showwarning("No server selected", "Select a server first.")
            return
        sid = self.selected_server_id
        titles = {"backup": "Scheduled Backups", "restart": "Scheduled Restarts",
                  "update": "Scheduled Updates", "message": "Scheduled Announcements"}
        defaults = {"backup": "03:00", "restart": "04:00", "update": "05:00", "message": "20:00"}
        title = titles.get(kind, "Scheduled Task")
        default_time = defaults.get(kind, "04:00")
        state_path = BASE_DIR / "agent_state.json"

        def read_state():
            try:
                return json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        cfg0 = (read_state().get("schedules", {}).get(sid, {}) or {}).get(kind, {}) or {}

        dlg = ctk.CTkToplevel(self)
        dlg.title(title)
        dlg.geometry("520x600")
        dlg.grab_set()
        dlg.transient(self)
        self.center_window(dlg, 520, 600)
        form = ctk.CTkFrame(dlg, fg_color=THEME["bg_card"], corner_radius=10)
        form.pack(fill="both", expand=True, padx=20, pady=20)
        ctk.CTkLabel(form, text=title + " - " + self._server_name(sid),
                     font=("Bahnschrift SemiBold", 16, "bold")).pack(pady=(0, 8))
        if not self.manager.agent_available():
            ctk.CTkLabel(
                form, text="\u26a0 Schedules run in the Watchdog, which is off. "
                "Turn it on (Watchdog button at the top) or this schedule will not run.",
                anchor="w", justify="left", wraplength=440, text_color=THEME["warning"],
            ).pack(fill="x", padx=10, pady=(0, 6))
        if kind == "update":
            ctk.CTkLabel(
                form, text="Checks Steam for a newer build; only then warns players, stops the "
                "server (and any others sharing its install), updates, and restarts.",
                anchor="w", justify="left", wraplength=440, text_color=THEME["text_muted"],
            ).pack(fill="x", padx=10, pady=(0, 6))
        enabled_var = ctk.BooleanVar(value=bool(cfg0.get("enabled", False)))
        mode_var = ctk.StringVar(value=cfg0.get("mode", "daily" if kind != "backup" else "interval"))
        hours_var = ctk.StringVar(value=str(cfg0.get("hours", 24)))
        times = cfg0.get("times") or [default_time]
        time_var = ctk.StringVar(value=", ".join(times))
        warn_var = ctk.StringVar(value=", ".join(str(m) for m in cfg0.get("warn_minutes", [10, 5, 1])))
        skip_var = ctk.BooleanVar(value=bool(cfg0.get("skip_if_players", False)))
        text_var = ctk.StringVar(value=cfg0.get("text", ""))
        if kind == "message":
            ctk.CTkLabel(form, text="Message shown in game (one line)", anchor="w",
                         text_color=THEME["text_light"]).pack(anchor="w", padx=10, pady=(4, 0))
            ctk.CTkEntry(form, textvariable=text_var, placeholder_text="e.g. Join our Discord: discord.gg/...").pack(
                fill="x", padx=10, pady=(0, 5))

        ctk.CTkCheckBox(form, text="Enable", variable=enabled_var,
                        fg_color=THEME["accent_blue"]).pack(anchor="w", padx=10, pady=(6, 5))
        mode_frame = ctk.CTkFrame(form, fg_color="transparent")
        mode_frame.pack(fill="x", padx=10, pady=(4, 5))
        ctk.CTkRadioButton(mode_frame, text="Every N hours", variable=mode_var, value="interval",
                           fg_color=THEME["accent_blue"]).pack(side="left", padx=(0, 10))
        ctk.CTkRadioButton(mode_frame, text="Daily at time(s)", variable=mode_var, value="daily",
                           fg_color=THEME["accent_blue"]).pack(side="left")
        ctk.CTkLabel(form, text="Hours (interval)", anchor="w",
                     text_color=THEME["text_light"]).pack(anchor="w", padx=10, pady=(8, 0))
        ctk.CTkEntry(form, textvariable=hours_var).pack(fill="x", padx=10, pady=(0, 5))
        ctk.CTkLabel(form, text="Times (daily, HH:MM - separate several with commas)", anchor="w",
                     text_color=THEME["text_light"]).pack(anchor="w", padx=10, pady=(8, 0))
        ctk.CTkEntry(form, textvariable=time_var).pack(fill="x", padx=10, pady=(0, 5))
        if kind in ("restart", "update"):
            ctk.CTkLabel(form, text="Warn players this many minutes before (blank = no warning)",
                         anchor="w", text_color=THEME["text_light"]).pack(anchor="w", padx=10, pady=(8, 0))
            ctk.CTkEntry(form, textvariable=warn_var).pack(fill="x", padx=10, pady=(0, 5))
            ctk.CTkCheckBox(form, text="Wait while players are online (up to 2 hours, then skip)",
                            variable=skip_var, fg_color=THEME["accent_blue"]).pack(anchor="w", padx=10, pady=(4, 4))

        def save():
            try:
                hours = float(hours_var.get() or 24)
                if hours <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("Invalid hours", "Hours must be a positive number.", parent=dlg)
                return
            tlist = [t.strip() for t in time_var.get().split(",") if t.strip()]
            for t in tlist:
                try:
                    h, m = (int(x) for x in t.split(":"))
                    assert 0 <= h < 24 and 0 <= m < 60
                except Exception:
                    messagebox.showwarning("Invalid time", "Use HH:MM, e.g. 04:30 (got '%s')." % t, parent=dlg)
                    return
            if mode_var.get() == "daily" and not tlist:
                messagebox.showwarning("Time required", "Enter at least one time.", parent=dlg)
                return
            try:
                warn = [int(x) for x in warn_var.get().replace(" ", "").split(",") if x]
            except ValueError:
                messagebox.showwarning("Invalid warnings", "Use minutes like: 10, 5, 1", parent=dlg)
                return
            cfg = {"enabled": bool(enabled_var.get()), "mode": mode_var.get(), "hours": hours,
                   "times": tlist if mode_var.get() == "daily" else []}
            if kind in ("restart", "update"):
                cfg["warn_minutes"] = warn
                cfg["skip_if_players"] = bool(skip_var.get())
            if kind == "message":
                cfg["text"] = text_var.get().strip()[:200]
                if cfg["enabled"] and not cfg["text"]:
                    messagebox.showwarning("Message", "Write the message to announce.", parent=dlg)
                    return
            # Re-read right before writing so concurrent edits aren't lost.
            state = read_state()
            state.setdefault("schedules", {}).setdefault(sid, {})[kind] = cfg
            from core.paths import atomic_write_json
            atomic_write_json(state_path, state)
            dlg.destroy()
            self._show_status(title + " saved for " + self._server_name(sid))

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(0, 20))
        ctk.CTkButton(btns, text="Save", command=save, fg_color=THEME["success"]).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Cancel", fg_color=THEME["border"], command=dlg.destroy).pack(side="right", padx=6)

    # ------------------------------------------------------------------ #
    # Help
    # ------------------------------------------------------------------ #
    def show_help_dialog(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("BaoBun Control Panel - Getting Started")
        dlg.geometry("700x600")
        dlg.grab_set()
        dlg.transient(self)
        self.center_window(dlg, 700, 600, fit=False)
        scroll = ctk.CTkScrollableFrame(dlg, fg_color=THEME["bg_card"], corner_radius=10)
        scroll.pack(fill="both", expand=True, padx=20, pady=20)
        def para(text):
            return ("p", text)
        def bullet(text):
            return ("b", text)
        def note(text):
            return ("n", text)

        sections = [
            ("Welcome", [
                para("BaoBun Control Panel manages many dedicated game servers from one window."),
                para("Each server gets its own install subfolder, so settings and configs never mix between games."),
                note("New to hosting? Work through the sections below top to bottom."),
            ]),
            ("Adding a server", [
                para("Follow these steps:"),
                bullet("Click + Add Server in the top bar."),
                bullet("Give it a name, then pick a Game Preset."),
                note("The preset auto-fills the port, Steam app ID, and executable for you."),
                bullet("Choose a parent folder."),
                note("BaoBun creates a subfolder named after your server inside it."),
                bullet("Click Add Server, then Install Game to download the files."),
            ]),
            ("Game presets & installing", [
                para("Presets carry all the per-game defaults so you never look them up by hand."),
                bullet("Each preset sets the correct port, app ID, and launch executable."),
                bullet("Free SteamCMD games install using anonymous login."),
                bullet("Games that require a purchase need a real Steam account."),
                note("Enter your Steam username/password in Settings before installing those."),
            ]),
            ("Running servers", [
                bullet("Start / Stop / Restart act on the selected server."),
                bullet("Start All / Stop All control every server at once."),
                bullet("Refresh re-reads live status; Update All runs SteamCMD updates."),
                bullet("Install Game fetches missing files for the selected server."),
                bullet("Remove deletes the entry while keeping its subfolder on disk."),
            ]),
            ("Watchdog", [
                para("The Watchdog is BaoBun's background service: it keeps working when this window is closed."),
                bullet("Keeps servers alive after you close the BaoBun window."),
                bullet("Auto-restarts a server if it crashes."),
                bullet("Runs your scheduled tasks on a 30-second tick."),
                note("The Watchdog button at the top shows whether it's on (green) and turns it on or off."),
            ]),
            ("Schedules", [
                para("Each server supports three schedulers:"),
                bullet("Backup Schedule - zips the server's config files."),
                bullet("Restart Schedule - restarts the server on a timer."),
                bullet("Update Schedule - runs SteamCMD updates on a timer."),
                para("Two scheduling modes:"),
                bullet("Interval - every N hours."),
                bullet("Daily - at a fixed HH:MM time."),
                note("Schedules only run while the Watchdog is on."),
            ]),
            ("Backups & profiles", [
                bullet("Backup Now - snapshot a server's config files immediately."),
                bullet("Restore - recover configs from a previous backup."),
                bullet("Export Profile - save a server's settings to share or move."),
                bullet("Import Profile - load a saved profile."),
            ]),
            ("Workshop mods", [
                para("Subscribe to Steam Workshop content and copy it into your server."),
            ]),
            ("ARK cluster", [
                para("Set up multiple ARK maps that share progression."),
                bullet("ARK Cluster - spin up maps in one go."),
                bullet("ARK World - cluster health, creature spawns, loot tables and rules for every map at once."),
                bullet("Apply to all maps - push one setting to every map."),
                bullet("Gameplay Settings - tweak rates, tames, and rules."),
                note("ASA and ASE are handled separately - their maps and behavior differ."),
            ]),
            ("Detail tabs", [
                bullet("Logs - live server output with a line-count selector."),
                bullet("Config - edit the selected server's config in place."),
                bullet("Settings - every game setting as a form (Essentials, All settings, Raw file)."),
                bullet("Console - send commands to the server's console."),
                bullet("RCON - remote admin commands where supported."),
            ]),
            ("Discord bot", [
                para("An optional bot lets people start servers from chat."),
                bullet("Settings > Discord bot: paste the bot token and press Invite the bot."),
                bullet("In Discord, type /baobun-setup to pick channels and who may start servers."),
                bullet("Anyone allowed can START; only admins (server owner, Manage Server) can STOP."),
                note("This prevents griefing from non-admin users."),
            ]),
            ("Port forwarding", [
                para("To let friends join over the internet, open your ports."),
                bullet("Click Ports to see each server's port and protocol."),
                bullet("Forward those ports to this machine's LAN IP on your router."),
                note("Vintage Story uses 42420 by default."),
                note("ARK: Game + Query ports use UDP; RCON uses TCP."),
                note("If the router shows a different LAN IP (e.g. .100 vs .45), forward to the address this machine actually uses."),
            ]),
            ("Settings", [
                bullet("Accent color - 6 swatches, applied live."),
                bullet("Theme brightness - Dark or Light."),
                bullet("Default install folder - where new servers go."),
                bullet("steamcmd.exe path - location of your SteamCMD binary."),
                bullet("Steam login - for purchase-required games."),
                bullet("Run at startup - launch BaoBun when you sign in."),
            ]),
        ]
        for title, items in sections:
            ctk.CTkLabel(
                scroll, text=title, font=("Bahnschrift SemiBold", 15, "bold"),
                text_color=THEME["accent_gold"],
            ).pack(anchor="w", padx=10, pady=(14, 4))
            for kind, text in items:
                if kind == "p":
                    ctk.CTkLabel(
                        scroll, text=text, anchor="w",
                        text_color=THEME["text_light"], wraplength=620,
                    ).pack(anchor="w", padx=22, pady=(0, 2))
                elif kind == "b":
                    ctk.CTkLabel(
                        scroll, text="- " + text, anchor="w",
                        text_color=THEME["text_light"], wraplength=600,
                    ).pack(anchor="w", padx=34, pady=(0, 2))
                else:
                    ctk.CTkLabel(
                        scroll, text=text, anchor="w",
                        text_color=THEME["text_muted"], wraplength=580,
                    ).pack(anchor="w", padx=46, pady=(1, 3))
        ctk.CTkButton(dlg, text="Close", command=dlg.destroy, fg_color=THEME["success"]).pack(
            pady=(0, 20),
        )
