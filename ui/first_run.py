"""First-run walkthrough (core/first_run.py has the logic).

Six short pages: welcome (what BaoBun is, this PC), where servers go, backups,
keeping servers running, optional extras (Discord, web panel, nightly restart,
status page) and a finish page that adds the first server or brings servers
over from another PC. Every page can be skipped; everything is in Settings later.
"""

from __future__ import annotations

import os
import threading
from tkinter import filedialog

import customtkinter as ctk

from core import first_run as F

KEEP_CHOICES = ["3", "5", "10", "20", "30"]


class FirstRunWindow(ctk.CTkToplevel):
    PAGES = ("welcome", "folder", "backups", "running", "extras", "finish")

    def __init__(self, app, theme):
        super().__init__(app)
        self.app, self.t, self.mgr = app, theme, app.manager
        self.title("Welcome to BaoBun")
        self.transient(app)
        app.center_window(self, 680, 560, fit=True)
        d = F.defaults(self.mgr)
        self.install_var = ctk.StringVar(value=d["install_dir"])
        self.keep_var = ctk.StringVar(value=str(d["backup_keep"]) if str(d["backup_keep"]) in KEEP_CHOICES else "10")
        self.mirror_var = ctk.StringVar(value=d["backup_mirror_dir"])
        self.watchdog_var = ctk.BooleanVar(value=bool(d["watchdog"]))
        self.boot_var = ctk.BooleanVar(value=False)
        self.page = 0
        self.summary = []
        t = theme
        self.steps = ctk.CTkLabel(self, text="", anchor="w", text_color=t["text_muted"])
        self.steps.pack(fill="x", padx=22, pady=(14, 0))
        self.body = ctk.CTkFrame(self, fg_color=t["bg_card"], corner_radius=12)
        self.body.pack(fill="both", expand=True, padx=16, pady=8)
        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.pack(fill="x", padx=16, pady=(0, 14))
        self.back_btn = ctk.CTkButton(nav, text="Back", width=90, fg_color="transparent", border_width=1,
                                      border_color=t["border"], command=self.back)
        self.back_btn.pack(side="left")
        self.skip_btn = ctk.CTkButton(nav, text="Skip setup", width=100, fg_color="transparent",
                                      text_color=t["text_muted"], command=self.skip)
        self.skip_btn.pack(side="left", padx=8)
        self.next_btn = ctk.CTkButton(nav, text="Next", width=120, fg_color=t["accent_blue"], command=self.next)
        self.next_btn.pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.skip)
        self.show()

    # ------------------------------------------------------------------ helpers
    def _clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def h(self, text):
        ctk.CTkLabel(self.body, text=text, anchor="w", text_color=self.t["text_light"],
                     font=("Bahnschrift SemiBold", 19, "bold")).pack(fill="x", padx=18, pady=(18, 6))

    def p(self, text, color=None, top=0):
        lbl = ctk.CTkLabel(self.body, text=text, anchor="w", justify="left", wraplength=600,
                           text_color=color or self.t["text_muted"], font=("Segoe UI", 13))
        lbl.pack(fill="x", padx=18, pady=(top, 6))
        return lbl

    def folder_row(self, var, title, on_change=None):
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(2, 4))
        ent = ctk.CTkEntry(row, textvariable=var)
        ent.pack(side="left", fill="x", expand=True)

        def pick():
            p = filedialog.askdirectory(parent=self, title=title, initialdir=var.get() or None)
            if p:
                var.set(os.path.normpath(p))
                if on_change:
                    on_change()
        ctk.CTkButton(row, text="Change…", width=90, fg_color=self.t["border"], command=pick).pack(
            side="left", padx=(8, 0))
        if on_change:
            ent.bind("<FocusOut>", lambda _e: on_change())
        return ent

    # ------------------------------------------------------------------ pages
    def show(self):
        self._clear()
        name = self.PAGES[self.page]
        self.steps.configure(text="Step %d of %d" % (self.page + 1, len(self.PAGES)))
        self.back_btn.configure(state="normal" if self.page else "disabled")
        self.skip_btn.configure(state="normal" if name != "finish" else "disabled")
        self.next_btn.configure(text="Finish" if name == "finish" else ("Let's go" if name == "welcome" else "Next"))
        getattr(self, "page_" + name)()

    def page_welcome(self):
        t = self.t
        self.h("Welcome to BaoBun")
        self.p("BaoBun runs your game servers for you: it installs them, keeps them running, backs up the worlds, "
               "updates games and mods, and lets your friends start servers from Discord or a web page.")
        self.p("This takes about a minute. Everything here can be changed later in Settings.")
        self.p("This PC", t["text_light"], top=10)
        self.hw = self.p("Looking at this PC…")
        self.p("BaoBun keeps its settings, backups and logs in:\n%s" % self.mgr.config_path.parent, top=6)

        def work():
            text = F.hardware_text(F.hardware())
            try:
                self.after(0, lambda: self.hw.winfo_exists() and self.hw.configure(text=text))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def page_folder(self):
        self.h("Where should game servers go?")
        self.p("Each server gets its own folder in here. Game servers can be big (10-100 GB each), so pick a drive "
               "with plenty of room - an SSD makes worlds load faster.")
        self.folder_row(self.install_var, "Install game servers into", self._check_folder)
        self.folder_msg = self.p("")
        ds = F.drives()
        if len(ds) > 1:
            self.p("Free space: " + ",  ".join("%s %.0f GB" % (d["path"], d["free_gb"]) for d in ds[:6]))
        self._check_folder()

    def _check_folder(self):
        if not getattr(self, "folder_msg", None) or not self.folder_msg.winfo_exists():
            return
        path = self.install_var.get().strip()
        err = F.check_install_dir(path) if path else "Pick a folder."
        if err:
            self.folder_msg.configure(text="⚠ " + err, text_color=self.t["warning"])
        else:
            free = F.free_gb(path)
            self.folder_msg.configure(text="✓ Good%s." % (" - %.0f GB free" % free if free is not None else ""),
                                      text_color=self.t["status_online"])

    def page_backups(self):
        t = self.t
        self.h("Backups")
        self.p("BaoBun backs up each server's world before updates and restores, on a schedule you choose, and "
               "checks every week that the newest backup would really restore.")
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(4, 8))
        ctk.CTkLabel(row, text="Keep the newest", text_color=t["text_light"]).pack(side="left")
        ctk.CTkOptionMenu(row, values=KEEP_CHOICES, variable=self.keep_var, width=70).pack(side="left", padx=8)
        ctk.CTkLabel(row, text="backups of each server", text_color=t["text_light"]).pack(side="left")
        self.p("Also copy backups to (optional)", t["text_light"], top=8)
        self.p("Another drive, a NAS or a shared folder on another PC, so one dead disk can't take your worlds. "
               "It also lets another PC take your servers over if this one dies.")
        self.folder_row(self.mirror_var, "Also copy backups to")
        row2 = ctk.CTkFrame(self.body, fg_color="transparent")
        row2.pack(fill="x", padx=18)
        res = ctk.CTkLabel(row2, text="", anchor="w", text_color=t["text_muted"])

        def test():
            target = self.mirror_var.get().strip()
            if not target:
                res.configure(text="Leave it blank to skip.", text_color=t["text_muted"])
                return
            r = self.mgr.test_mirror(target)
            res.configure(text=("✓ BaoBun can write there (%s GB free)." % r.get("free_gb")) if r.get("ok") else
                          "✕ Can't write there: %s" % r.get("error"),
                          text_color=t["status_online"] if r.get("ok") else t["warning"])
        ctk.CTkButton(row2, text="Test", width=70, fg_color=t["border"], command=test).pack(side="left")
        res.pack(side="left", padx=8)

    def page_running(self):
        t = self.t
        self.h("Keep servers running")
        ctk.CTkCheckBox(self.body, text="Turn on the Watchdog (recommended)", variable=self.watchdog_var,
                        fg_color=t["accent_blue"]).pack(anchor="w", padx=18, pady=(4, 2))
        self.p("The Watchdog is BaoBun's background service. With it, servers keep running when you close this "
               "window, crashed servers restart by themselves, and schedules, backups, Discord and the web panel "
               "keep working. Without it, servers stop when BaoBun closes.")
        if os.name == "nt":
            ctk.CTkCheckBox(self.body, text="Start BaoBun when Windows starts, before anyone signs in",
                            variable=self.boot_var, fg_color=t["accent_blue"]).pack(anchor="w", padx=18,
                                                                                   pady=(12, 2))
            self.p("Best for a PC that hosts servers: after a power cut or update restart, the servers come back on "
                   "their own. Windows asks for permission once. (Backup copies to another PC need the "
                   "\"with network access\" option in Settings > Nightly restart instead.)")

    def page_extras(self):
        t = self.t
        self.h("Extras (optional)")
        self.p("Set these up now or any time later in Settings.")
        items = [
            ("Discord bot", "A control panel in your Discord: friends see and start servers, get alerts, game-night "
                            "events and mod lists.", "show_discord_bot_dialog"),
            ("Web panel", "Run BaoBun from a browser or phone, with sign-in for your admins.",
             "show_web_panel_dialog"),
            ("Nightly restart", "Restart the PC at night, back up first, and bring back the servers people use.",
             "show_nightly_dialog"),
        ]
        for title, text, method in items:
            row = ctk.CTkFrame(self.body, fg_color=t["bg_dark"], corner_radius=8)
            row.pack(fill="x", padx=16, pady=4)
            col = ctk.CTkFrame(row, fg_color="transparent")
            col.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            ctk.CTkLabel(col, text=title, anchor="w", text_color=t["text_light"],
                         font=("Bahnschrift SemiBold", 14, "bold")).pack(fill="x")
            ctk.CTkLabel(col, text=text, anchor="w", justify="left", wraplength=430,
                         text_color=t["text_muted"]).pack(fill="x")
            ctk.CTkButton(row, text="Set up…", width=90, fg_color=t["border"],
                          command=lambda m=method: getattr(self.app, m)()).pack(side="right", padx=10)

    def page_finish(self):
        t = self.t
        self.h("All set")
        for line in self.summary:
            self.p("✓ " + line, t["text_light"])
        self.p("Next: add your first server - pick a game and BaoBun downloads and sets it up.", top=10)
        btns = ctk.CTkFrame(self.body, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(4, 6))
        ctk.CTkButton(btns, text="Add a server", fg_color=t["success"],
                      command=lambda: self._finish_then("show_add_dialog")).pack(side="left")
        ctk.CTkButton(btns, text="Bring servers from another PC", fg_color=t["border"],
                      command=lambda: self._finish_then("bring_servers_here")).pack(side="left", padx=8)
        self.p("Moving from another PC? \"Bring servers\" sets them up here from their backup copies.")

    # ------------------------------------------------------------------ navigation
    def back(self):
        if self.page:
            self.page -= 1
            self.show()

    def next(self):
        name = self.PAGES[self.page]
        if name == "folder":
            err = F.check_install_dir(self.install_var.get())
            if err and not err.startswith("Only"):
                self._check_folder()
                return
        if name == "extras":
            self._save()
        if name == "finish":
            self._finish_then(None)
            return
        self.page += 1
        self.show()

    def _save(self):
        self.summary = F.apply(self.mgr, {"install_dir": self.install_var.get(), "backup_keep": self.keep_var.get(),
                                          "backup_mirror_dir": self.mirror_var.get(),
                                          "watchdog": self.watchdog_var.get()})
        if self.boot_var.get():
            if not getattr(self, "_boot_line", None):
                self._boot_line = self._boot_task()
            self.summary.append(self._boot_line)
        if self.watchdog_var.get():
            try:
                if not self.mgr.agent_available():
                    self.app.start_agent()
            except Exception:
                pass

    def _boot_task(self) -> str:
        try:
            from core import boot_task
            base = self.mgr.config_path.parent
            ok = boot_task.install(self.app._startup_command(), str(base), folder=str(base / "runtime"))
            return "BaoBun starts with Windows" if ok else \
                "Start with Windows wasn't set up (permission declined) - try again in Settings > Nightly restart"
        except Exception as e:
            return "Start with Windows wasn't set up: %s" % e

    def _finish_then(self, method):
        F.mark_done(self.mgr)
        self.destroy()
        if method:
            try:
                getattr(self.app, method)()
            except Exception:
                pass

    def skip(self):
        F.mark_done(self.mgr, skipped=True)
        self.destroy()
