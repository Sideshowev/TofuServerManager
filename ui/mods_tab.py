"""Mods tab on the Manage view. It adapts to the server's mod source (Vintage Story
ModDB, CurseForge / Workshop ID lists, Steam Workshop installs, Thunderstore,
Modrinth, uMod or a plain folder) using the capabilities ModManager reports."""

from __future__ import annotations

import threading

import customtkinter as ctk


def _mb(n) -> str:
    n = float(n or 0) / 1048576
    return "%.1f GB" % (n / 1024) if n >= 1024 else ("%.1f MB" % n if n >= 0.1 else "<0.1 MB")


class ModsPanel(ctk.CTkFrame):
    def __init__(self, master, theme, app, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.t = theme
        self.app = app
        self.sid = None
        self.data = None
        self._busy = False
        self._loaded_for = None
        from core.mods import ModManager
        self.mods = ModManager(app.manager)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", pady=(0, 6))
        self.title = ctk.CTkLabel(top, text="Mods", anchor="w", font=("Bahnschrift SemiBold", 15, "bold"),
                                  text_color=theme["text_light"])
        self.title.pack(side="left")
        self.btn_folder = ctk.CTkButton(top, text="Open folder", width=100, fg_color="transparent",
                                        border_width=1, border_color=theme["border"],
                                        text_color=theme["text_light"], command=self._open_folder)
        self.btn_folder.pack(side="right", padx=(6, 0))
        self.btn_update_all = ctk.CTkButton(top, text="Update all", width=100, fg_color=theme["accent_blue"],
                                            command=lambda: self._run("Updating mods...", self._do_update_all))
        self.btn_update_all.pack(side="right", padx=(6, 0))
        self.btn_check = ctk.CTkButton(top, text="Check for updates", width=140, fg_color=theme["border"],
                                       command=lambda: self.load(check=True))
        self.btn_check.pack(side="right", padx=(6, 0))

        self.add_row = ctk.CTkFrame(self, fg_color="transparent")
        self.add_row.pack(fill="x", pady=(0, 6))
        self.add_var = ctk.StringVar()
        self.add_entry = ctk.CTkEntry(self.add_row, textvariable=self.add_var)
        self.add_entry.pack(side="left", fill="x", expand=True)
        self.add_entry.bind("<Return>", lambda _e: self._install())
        self.btn_install = ctk.CTkButton(self.add_row, text="Install", width=90, fg_color=theme["success"],
                                         command=self._install)
        self.btn_install.pack(side="left", padx=(6, 0))
        self.btn_search = ctk.CTkButton(self.add_row, text="Browse mods", width=110, fg_color=theme["border"],
                                        command=self._search)
        self.btn_search.pack(side="left", padx=(6, 0))
        self.btn_file = ctk.CTkButton(self.add_row, text="Add file...", width=100, fg_color=theme["border"],
                                      command=self._add_file)
        self.btn_file.pack(side="left", padx=(6, 0))

        self.banner = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=900,
                                   text_color=theme["warning"])
        self.banner.pack(fill="x")
        self.info = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=900,
                                 text_color=theme["text_muted"], font=("Segoe UI", 11))
        self.info.pack(fill="x")
        self.status = ctk.CTkLabel(self, text="Select a server to see its mods.", anchor="w",
                                   text_color=theme["text_muted"])
        self.status.pack(fill="x", pady=(0, 4))
        self.list = ctk.CTkScrollableFrame(self, fg_color=theme["bg_dark"], corner_radius=8)
        self.list.pack(fill="both", expand=True)
        self.opts = ctk.CTkFrame(self, fg_color="transparent")
        self.opts.pack(fill="x", pady=(6, 0))

    # -- loading ----------------------------------------------------------------
    def set_server(self, server_id, visible=False):
        self.sid = server_id
        if visible:
            self.load()
        else:
            self._loaded_for = None

    def on_shown(self):
        if self.sid and self._loaded_for != self.sid:
            self.load()

    def load(self, check=False):
        sid = self.sid
        if not sid:
            return
        self.status.configure(text="Checking for updates..." if check else "Loading mods...")

        def work():
            try:
                data, err = self.mods.list(sid, check_updates=check), None
            except Exception as e:
                data, err = None, str(e)
            self.app.after(0, lambda: self._render(sid, data, err, check))
        threading.Thread(target=work, daemon=True).start()

    def _clear(self, frame):
        for w in frame.winfo_children():
            w.destroy()

    def _render(self, sid, data, err, checked):
        if sid != self.sid:
            return
        self._loaded_for = sid
        self._clear(self.list)
        self._clear(self.opts)
        if err:
            self.status.configure(text="Couldn't read mods: " + err)
            return
        self.data = data
        t = self.t
        caps = data.get("caps") or {}
        title = data.get("label") or "Mods"
        if data.get("game_version"):
            title += "  ·  %s%s" % ((data.get("loader") or "game") + " ", data["game_version"])
        self.title.configure(text=title)
        self.add_entry.configure(placeholder_text=caps.get("add_hint") or "")
        self._show(self.add_row, bool(caps.get("add")))
        self._show_btn(self.btn_search, caps.get("search"))
        self._show_btn(self.btn_file, caps.get("add_file"))
        for b in (self.btn_check, self.btn_update_all):
            mapped = bool(b.winfo_manager())
            if caps.get("update") and not mapped:
                b.pack(side="right", padx=(6, 0), before=self.btn_folder)
            elif not caps.get("update") and mapped:
                b.pack_forget()
            b.configure(state="normal")
        self.btn_folder.configure(state="normal" if data.get("folder") else "disabled")

        notes = []
        if data.get("restart_needed"):
            notes.append("Mods changed while the server is running - restart it to load them.")
        missing = sorted({d for m in data["mods"] if m.get("enabled") for d in m.get("missing_deps", [])})
        if missing:
            notes.append("Missing required mods: " + ", ".join(missing) + ".")
        dup = sorted({m["name"] for m in data["mods"] if m.get("duplicate")})
        if dup:
            notes.append("Two copies enabled (the server may refuse to start): " + ", ".join(dup) + ".")
        if data.get("hint"):
            notes.append(data["hint"])
        self.banner.configure(text="\n".join(notes))
        info = [x for x in (data.get("note"), ("Saved to: " + data["where"]) if data.get("where") else None) if x]
        self.info.configure(text="\n".join(info))

        for s in data.get("setup") or []:
            ctk.CTkButton(self.list, text=s["label"], fg_color=t["accent_blue"],
                          command=lambda a=s["action"]: self._run("Working...",
                                                                  lambda sid: self.mods.setup(sid, a, user="gui"))
                          ).pack(anchor="w", padx=8, pady=(8, 4))
        if missing and caps.get("deps"):
            ctk.CTkButton(self.list, text="Install missing mods (%d)" % len(missing), fg_color=t["accent_blue"],
                          command=lambda: self._run("Installing missing mods...", self._do_deps)).pack(
                anchor="w", padx=8, pady=(8, 4))

        mods = data["mods"]
        ups = sum(1 for m in mods if m.get("update_available"))
        self.btn_update_all.configure(text="Update all (%d)" % ups if ups else "Update all")
        if not mods:
            self.status.configure(text="No mods yet." + (" Add one above." if caps.get("add") else ""))
        else:
            on = sum(1 for m in mods if m.get("enabled"))
            self.status.configure(text="%d mod(s), %d on" % (len(mods), on)
                                  + (", %d update(s) available" % ups if ups else (", all up to date" if checked else ""))
                                  + (("   ·   " + data["folder"]) if data.get("folder") else ""))
        ordered = mods if caps.get("reorder") else sorted(mods, key=lambda m: (not m.get("enabled"), m["name"].lower()))
        for m in ordered:
            self._row(m, caps)
        self._render_auto(data, caps)
        self._render_options(data.get("options") or [])

    AUTO_LABELS = [("off", "Off"), ("notify", "Tell me on Discord"), ("auto", "Update automatically")]

    def _render_auto(self, data, caps):
        """Automatic mod updates: the setting (all servers) and this server's opt-out."""
        if not caps.get("update"):
            return
        t = self.t
        au = data.get("auto_update") or {}
        row = ctk.CTkFrame(self.opts, fg_color="transparent")
        row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(row, text="Mod updates (all servers)", width=260, anchor="w",
                     text_color=t["text_muted"]).pack(side="left")
        labels = [x[1] for x in self.AUTO_LABELS]
        var = ctk.StringVar(value=dict(self.AUTO_LABELS).get(au.get("mode") or "notify", labels[1]))

        def set_mode(lab):
            val = next(k for k, v in self.AUTO_LABELS if v == lab)
            self.app.manager.set_setting("mod_updates", val)
            self.app._show_status("Mod updates: " + lab)
        ctk.CTkOptionMenu(row, variable=var, values=labels, width=200, command=set_mode).pack(side="left")
        inc = ctk.BooleanVar(value=au.get("included", True))
        ctk.CTkCheckBox(row, text="Include this server", variable=inc, fg_color=t["accent_blue"],
                        command=lambda: self._run("Saving...", lambda sid: self.mods.set_auto_update(
                            sid, inc.get(), user="gui"))).pack(side="left", padx=10)
        ctk.CTkLabel(self.opts, text="Checked every few hours. 'Update automatically' updates during the nightly "
                     "restart (worlds backed up first, old mods kept) and puts the old mods back if the server "
                     "crashes afterwards. Mods set to 'Keep version' are never updated automatically or by Update "
                     "all.", anchor="w", justify="left", wraplength=900, text_color=t["text_muted"],
                     font=("Segoe UI", 11)).pack(fill="x", pady=(0, 6))

    def _show(self, w, on):
        """Show/hide the add row, keeping it above the banner."""
        mapped = bool(w.winfo_manager())
        if on and not mapped:
            w.pack(fill="x", pady=(0, 6), before=self.banner)
        elif not on and mapped:
            w.pack_forget()

    @staticmethod
    def _show_btn(b, on):
        mapped = bool(b.winfo_manager())
        if on and not mapped:
            b.pack(side="left", padx=(6, 0))
        elif not on and mapped:
            b.pack_forget()

    def _row(self, m, caps):
        t = self.t
        row = ctk.CTkFrame(self.list, fg_color=t["bg_card"], corner_radius=8)
        row.pack(fill="x", padx=6, pady=3)
        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=10, pady=6)
        name = ("%d. " % m["order"] if caps.get("reorder") and m.get("order") else "") + m["name"] \
            + ("  " + m["version"] if m.get("version") else "")
        ctk.CTkLabel(left, text=name, anchor="w", font=("Segoe UI", 13, "bold"),
                     text_color=t["text_light"] if m.get("enabled") else t["text_muted"]).pack(fill="x")
        bits = []
        if m.get("pinned"):
            bits.append("\U0001F512 kept at this version")
        if m.get("update_available"):
            bits.append("update: %s" % (m.get("latest") or "newer version available"))
        if m.get("missing_deps"):
            bits.append("needs " + ", ".join(m["missing_deps"]))
        if m.get("duplicate"):
            bits.append("duplicate")
        if m.get("missing"):
            bits.append("not downloaded yet")
        if m.get("check_error"):
            bits.append("couldn't check")
        if not m.get("enabled"):
            bits.append("off")
        if m.get("server_only"):
            bits.append("server-only")
        if m.get("mod_ids") is not None and "mod_ids" in (caps.get("fields") or []):
            bits.append("Mod IDs: " + (", ".join(m["mod_ids"]) if m["mod_ids"] else "not known yet"))
        if m.get("size"):
            bits.append(_mb(m["size"]))
        if m.get("id") and m.get("id") != m.get("name"):
            bits.append(str(m["id"]))
        warn = m.get("missing_deps") or m.get("duplicate") or m.get("missing")
        ctk.CTkLabel(left, text="  ·  ".join(bits), anchor="w", justify="left", wraplength=560,
                     text_color=t["warning"] if warn else t["text_muted"], font=("Segoe UI", 11)).pack(fill="x")
        key = m["key"]
        ctk.CTkButton(row, text="Remove", width=76, fg_color="transparent", border_width=1,
                      border_color=t["danger"], text_color=t["text_light"],
                      command=lambda: self._remove(m)).pack(side="right", padx=(4, 10))
        if m.get("update_available"):
            ctk.CTkButton(row, text="Update", width=76, fg_color=t["accent_blue"],
                          command=lambda: self._run("Updating %s..." % m["name"],
                                                    lambda sid: self.mods.update(sid, [key], user="gui"))
                          ).pack(side="right", padx=4)
        if caps.get("toggle"):
            var = ctk.BooleanVar(value=bool(m.get("enabled")))
            ctk.CTkSwitch(row, text="On", variable=var, width=60, progress_color=t["success"],
                          command=lambda: self._run(("Turning on " if var.get() else "Turning off ") + m["name"],
                                                    lambda sid: self.mods.set_enabled(sid, key, var.get(), user="gui"))
                          ).pack(side="right", padx=4)
        if caps.get("update"):
            pv = ctk.BooleanVar(value=bool(m.get("pinned")))
            ctk.CTkCheckBox(row, text="Keep version", variable=pv, width=110, fg_color=t["accent_blue"],
                            command=lambda: self._run(("Keeping %s at its version..." if pv.get() else
                                                       "Letting %s update...") % m["name"],
                                                      lambda sid: self.mods.set_pin(sid, key, pv.get(), user="gui"))
                            ).pack(side="right", padx=4)
        fields = caps.get("fields") or []
        if "server_only" in fields:
            so = ctk.BooleanVar(value=bool(m.get("server_only")))
            ctk.CTkCheckBox(row, text="Server only", variable=so, width=100, fg_color=t["accent_blue"],
                            command=lambda: self._run("Saving...", lambda sid: self.mods.set_field(
                                sid, key, "server_only", so.get(), user="gui"))).pack(side="right", padx=4)
        for f, label in (("mod_ids", "Mod IDs"), ("name", "Name")):
            if f in fields:
                ctk.CTkButton(row, text="Edit " + label, width=90, fg_color=t["border"],
                              command=lambda f=f, label=label: self._edit_field(m, f, label)).pack(side="right", padx=4)
        if caps.get("reorder"):
            ctk.CTkButton(row, text="↓", width=32, fg_color=t["border"],
                          command=lambda: self._run("Moving...", lambda sid: self.mods.move(sid, key, 1))
                          ).pack(side="right", padx=(2, 4))
            ctk.CTkButton(row, text="↑", width=32, fg_color=t["border"],
                          command=lambda: self._run("Moving...", lambda sid: self.mods.move(sid, key, -1))
                          ).pack(side="right", padx=2)

    def _render_options(self, opts):
        if not opts:
            return
        t = self.t
        ctk.CTkLabel(self.opts, text="Mod options", anchor="w", text_color=t["text_light"],
                     font=("Segoe UI Semibold", 12)).pack(fill="x")
        for o in opts:
            row = ctk.CTkFrame(self.opts, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=o["label"], width=260, anchor="w", text_color=t["text_muted"]).pack(side="left")
            if o["type"] == "select":
                labels = [c[1] for c in o["choices"]]
                cur = next((c[1] for c in o["choices"] if c[0] == (o.get("value") or "")), labels[0])
                var = ctk.StringVar(value=cur)
                ctk.CTkOptionMenu(row, variable=var, values=labels, width=260,
                                  command=lambda lab, o=o: self._set_option(
                                      o["key"], next(c[0] for c in o["choices"] if c[1] == lab))).pack(side="left")
            else:
                var = ctk.StringVar(value="" if o["type"] == "secret" else (o.get("value") or ""))
                ph = ("saved - type a new one to replace it" if o.get("value") else "") if o["type"] == "secret" \
                    else (o.get("placeholder") or "")
                e = ctk.CTkEntry(row, textvariable=var, width=260, placeholder_text=ph,
                                 show="*" if o["type"] == "secret" else None)
                e.pack(side="left")
                ctk.CTkButton(row, text="Save", width=60, fg_color=t["border"],
                              command=lambda o=o, var=var: self._set_option(o["key"], var.get())).pack(side="left", padx=6)

    def _set_option(self, key, value):
        self._run("Saving...", lambda sid: self.mods.set_option(sid, key, value, user="gui"))

    def _edit_field(self, m, field, label):
        dlg = ctk.CTkInputDialog(title=label, text="%s for %s%s" % (
            label, m["name"], " (separate several with commas)" if field == "mod_ids" else ""))
        val = dlg.get_input()
        if val is None:
            return
        self._run("Saving...", lambda sid: self.mods.set_field(sid, m["key"], field, val, user="gui"))

    # -- actions ---------------------------------------------------------------------
    def _run(self, label, fn):
        if self._busy or not self.sid:
            return
        self._busy = True
        sid = self.sid
        self.status.configure(text=label)
        for b in (self.btn_install, self.btn_update_all, self.btn_check):
            b.configure(state="disabled")

        def work():
            try:
                r, err = fn(sid), None
            except Exception as e:
                r, err = None, str(e)

            def done():
                self._busy = False
                self.btn_install.configure(state="normal")
                if err:
                    self.app.show_error("Mods", err)
                else:
                    self._report(r)
                self.load()
            self.app.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def _report(self, r):
        if not isinstance(r, dict):
            return
        lines = []
        if r.get("installed") and isinstance(r["installed"], str):
            lines.append("Installed " + r["installed"])
            if r.get("replaced"):
                lines.append("Older copy moved to Mods-old-versions: " + ", ".join(r["replaced"]))
        if isinstance(r.get("installed"), list) and r["installed"]:
            lines.append("Installed: " + ", ".join(r["installed"]))
        if r.get("updated"):
            lines.append("Updated: " + ", ".join("%s %s → %s" % (u["mod"], u.get("from") or "", u.get("to") or "")
                                                 for u in r["updated"]))
        if r.get("message"):
            lines.append(r["message"])
        for f in r.get("failed") or []:
            lines.append("Failed: %s (%s)" % (f["mod"], f["error"]))
        for k in ("note", "backup"):
            if r.get(k):
                lines.append(r[k])
        if r.get("pinned_skipped"):
            lines.append("Kept at their version: " + ", ".join(r["pinned_skipped"]))
        if r.get("removed"):
            lines.append("Removed %s (a copy is kept in Tofu's backups folder)" % r["removed"])
        if r.get("restart_needed") and (r.get("installed") or r.get("updated") or r.get("removed") or r.get("changed")):
            lines.append("Restart the server to load the change.")
        if lines:
            self.app._toast("Mods", "\n".join(lines))

    def _install(self):
        ref = self.add_var.get().strip()
        if not ref:
            return
        self.add_var.set("")
        self._run("Installing %s..." % ref, lambda sid: self.mods.install(sid, ref, user="gui"))

    def _add_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(title="Pick a mod file (.zip, .pak, .dll, .jar ...)")
        if path:
            self._run("Adding %s..." % path, lambda sid: self.mods.install_file(sid, path, user="gui"))

    def _remove(self, m):
        srv = self.app.manager.get_server(self.sid) or {}
        if srv.get("clusterId") or str(srv.get("appId")) in ("2430930", "376030"):
            msg = ("Remove %s?\n\nARK deletes every creature, item and building from a mod when the map starts "
                   "without it - on %s. Uploaded creatures and items from it are lost too.\n\nRemove it anyway?"
                   % (m["name"], "every map in cluster " + srv["clusterId"] if srv.get("clusterId") else "this server"))
            if not self.app.ask_yes_no("Remove ARK mod", msg):
                return
        elif not self.app.ask_yes_no("Remove mod", "Remove %s from this server?\n\nNothing is deleted: files go to "
                                     "Tofu's backups folder (backups\\%s\\removed-mods)." % (m["name"], self.sid)):
            return
        self._run("Removing %s..." % m["name"], lambda sid: self.mods.remove(sid, m["key"], user="gui"))

    def _do_update_all(self, sid):
        return self.mods.update(sid, None, user="gui")

    def _do_deps(self, sid):
        return self.mods.install_missing_deps(sid, user="gui")

    def _search(self):
        """Open the mod browser (cards with pictures, details, install)."""
        sid = self.sid
        text = self.add_var.get().strip()
        try:
            from ui.mod_browser import open_mod_browser
            open_mod_browser(self.app, self.t, self.mods, sid, query=text, installed=self.data,
                             on_change=lambda r: (self._report(r),
                                                  self.load()))
            return
        except Exception as e:
            self.status.configure(text="Couldn't open the mod browser (%s) - showing a list instead." % e)
        self.status.configure(text="Searching...")

        def work():
            try:
                res, err = self.mods.search(sid, text), None
            except Exception as e:
                res, err = None, str(e)
            self.app.after(0, lambda: self._show_results(res, err))
        threading.Thread(target=work, daemon=True).start()

    def _show_results(self, res, err):
        if err:
            self.status.configure(text=err)
            return
        t = self.t
        self._clear(self.list)
        self.status.configure(text="%d result(s) - click Install, or go back to the installed list." % len(res))
        ctk.CTkButton(self.list, text="← Back to installed mods", fg_color=t["border"],
                      command=lambda: self.load()).pack(anchor="w", padx=8, pady=6)
        for r in res[:30]:
            row = ctk.CTkFrame(self.list, fg_color=t["bg_card"], corner_radius=8)
            row.pack(fill="x", padx=6, pady=3)
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=10, pady=6)
            ctk.CTkLabel(left, text=r.get("name") or str(r.get("id")), anchor="w", font=("Segoe UI", 13, "bold"),
                         text_color=t["text_light"]).pack(fill="x")
            sub = "by %s  ·  %s downloads  ·  %s" % (r.get("author") or "?", "{:,}".format(r.get("downloads") or 0),
                                                         r.get("summary") or "")
            ctk.CTkLabel(left, text=sub, anchor="w", justify="left", wraplength=640, text_color=t["text_muted"],
                         font=("Segoe UI", 11)).pack(fill="x")
            ref = str(r.get("id"))
            ctk.CTkButton(row, text="Install", width=80, fg_color=t["success"],
                          command=lambda ref=ref: self._run("Installing...", lambda sid: self.mods.install(
                              sid, ref, user="gui"))).pack(side="right", padx=10)

    def _open_folder(self):
        if self.data and self.data.get("folder"):
            import os
            os.makedirs(self.data["folder"], exist_ok=True)
            self.app.open_folder(self.data["folder"])
