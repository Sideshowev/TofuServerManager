"""Desktop "Bring servers here": set servers up again on this PC from the copies
in a backup-copy folder (another drive, a NAS, a shared folder on another PC).
See core/bring_here.py."""

from __future__ import annotations

import time
from tkinter import filedialog, messagebox

import customtkinter as ctk

from core import bring_here


class BringHereWindow(ctk.CTkToplevel):
    def __init__(self, app, theme):
        super().__init__(app)
        self.app, self.t, self.mgr = app, theme, app.manager
        t = theme
        self.title("Bring servers here")
        self.transient(app)
        app.center_window(self, 700, 640, fit=True)
        self.folder = ctk.StringVar(value=self.mgr.get_setting("backup_mirror_dir", "") or "")
        self.base = ctk.StringVar(value=bring_here.default_base(self.mgr))
        self.checks = {}
        self.job = None

        ctk.CTkLabel(self, text="Set servers up again on this PC from their backup copies - for when the old PC "
                                "died, or you're moving to a new one. Tofu installs each game and puts the "
                                "newest world back.", anchor="w", justify="left", wraplength=640,
                     text_color=t["text_light"]).pack(fill="x", padx=18, pady=(16, 6))
        self._path_row("Backup copies are in", self.folder, self._pick_folder)
        self._path_row("Install into", self.base, self._pick_base)
        ctk.CTkLabel(self, text="⚠ " + bring_here.SAFETY_NOTE, anchor="w", justify="left", wraplength=640,
                     text_color=t["warning"]).pack(fill="x", padx=18, pady=(8, 2))
        self.list = ctk.CTkScrollableFrame(self, fg_color=t["bg_card"], corner_radius=10)
        self.list.pack(fill="both", expand=True, padx=16, pady=8)
        self.status = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=640,
                                   text_color=t["text_muted"])
        self.status.pack(fill="x", padx=18)
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(4, 14))
        self.go = ctk.CTkButton(btns, text="Bring here", fg_color=t["accent_blue"], command=self._start)
        self.go.pack(side="right")
        ctk.CTkButton(btns, text="Close", fg_color="transparent", border_width=1, border_color=t["border"],
                      command=self.destroy).pack(side="right", padx=8)
        self.after(50, self._scan)

    def _path_row(self, label, var, pick):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=2)
        ctk.CTkLabel(row, text=label, width=150, anchor="w", text_color=self.t["text_muted"]).pack(side="left")
        ctk.CTkLabel(row, textvariable=var, anchor="w", text_color=self.t["text_light"]).pack(
            side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Change…", width=90, fg_color="transparent", border_width=1,
                      border_color=self.t["border"], command=pick).pack(side="right")

    def _pick_folder(self):
        p = filedialog.askdirectory(parent=self, title="Folder the backups were copied to",
                                    initialdir=self.folder.get() or None)
        if p:
            self.folder.set(p)
            self._scan()

    def _pick_base(self):
        p = filedialog.askdirectory(parent=self, title="Install brought-over servers into",
                                    initialdir=self.base.get() or None)
        if p:
            self.base.set(p)

    def _scan(self):
        for w in self.list.winfo_children():
            w.destroy()
        self.checks.clear()
        if not self.folder.get():
            self.status.configure(text="Pick the folder the backups were copied to (the old PC's "
                                       "\"Also copy backups to\" folder).")
            return
        self.status.configure(text="Looking in %s…" % self.folder.get())
        self.update_idletasks()
        res = bring_here.scan(self.mgr, self.folder.get())
        if res.get("error"):
            self.status.configure(text=res["error"])
            return
        t = self.t
        if not res["servers"]:
            self.status.configure(text="No server backups in that folder.")
            return
        self.status.configure(text="Tick the servers to set up here.")
        for e in res["servers"]:
            row = ctk.CTkFrame(self.list, fg_color=t["bg_dark"], corner_radius=8)
            row.pack(fill="x", padx=4, pady=3)
            var = ctk.BooleanVar(value=e["can_bring"] and not e["here"])
            cb = ctk.CTkCheckBox(row, text=e["name"], variable=var, text_color=t["text_light"])
            cb.pack(side="left", padx=10, pady=8)
            if not e["can_bring"]:
                cb.configure(state="disabled")
            info = "%s  ·  newest backup %s" % (e["game"] or "game", time.strftime(
                "%d %b %Y %H:%M", time.localtime(e["newest_time"])))
            if e["here"]:
                info += "  ·  already on this PC"
            if not e["can_bring"]:
                info = e["why_not"]
            ctk.CTkLabel(row, text=info, anchor="e", justify="right", wraplength=360,
                         text_color=t["text_muted"]).pack(side="right", padx=10)
            self.checks[e["key"]] = var

    def _start(self):
        picks = [{"key": k} for k, v in self.checks.items() if v.get()]
        if not picks:
            messagebox.showinfo("Bring servers here", "Tick at least one server.", parent=self)
            return
        if not messagebox.askyesno("Bring servers here", bring_here.SAFETY_NOTE + "\n\nGo ahead?", parent=self):
            return
        try:
            self.job = bring_here.start(self.mgr, picks, self.base.get(), folder=self.folder.get(), trusted=True)
        except Exception as e:
            messagebox.showerror("Bring servers here", str(e), parent=self)
            return
        self.go.configure(state="disabled")
        self._poll()

    def _poll(self):
        if not self.winfo_exists():
            return
        j = bring_here.job(self.job) or {}
        if j.get("state") == "working":
            self.status.configure(text="%d of %d done · %s" % (len(j["done"]) + len(j["failed"]), j["total"],
                                                               j.get("step") or ""))
            self.after(1000, self._poll)
            return
        self.go.configure(state="normal")
        lines = []
        for d in j.get("done") or []:
            lines.append("✅ %s - %s" % (d["name"], " ".join(d["notes"])))
            lines += ["   ⚠ " + w for w in d["warnings"]]
        for f in j.get("failed") or []:
            lines.append("❌ %s - %s" % (f["name"], f["error"]))
        self.status.configure(text="\n".join(lines) or "Done.",
                              text_color=self.t["success"] if j.get("state") == "done" else self.t["warning"])
        try:
            self.app.refresh_servers()
        except Exception:
            pass
        self._scan_keep_status()

    def _scan_keep_status(self):
        text = self.status.cget("text")
        self._scan()
        self.status.configure(text=text)
