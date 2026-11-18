"""Desktop "Settings" tab: one place for everything a server can be configured with.

Three views (switch at the top):
  Essentials   - the quick fields (name, password, max players...) per game
  All settings - every setting in the game's config files as switches, number
                 boxes and dropdowns, with search, help text and "add a setting"
                 (core/config_forms.py, the same engine as the web panel)
  Raw file     - the file as plain text, for anyone who wants it

Replaces the old Config / Batch Files / Console / RCON tabs.
"""

from __future__ import annotations

import threading
from tkinter import messagebox
import tkinter.scrolledtext as tkst

import customtkinter as ctk

VIEWS = ("Essentials", "All settings", "Raw file")


class SettingsPanel(ctk.CTkFrame):
    def __init__(self, master, theme, app, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.t, self.app = theme, app
        self.sid = None
        self.view = "Essentials"
        self.files = []
        self.form = None          # last form from config_forms.form()
        self.changes = {}         # field id -> new value
        self.open_groups = set()
        self._loaded_for = None
        self._render_job = None
        self._gen = 0             # bumps on every render; stale batches stop
        self.group_limit = {}     # group name -> how many fields to show
        t = theme

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x")
        self.seg = ctk.CTkSegmentedButton(top, values=list(VIEWS), command=self._switch,
                                          selected_color=t["accent_blue"], selected_hover_color=t["accent_blue"])
        self.seg.set(self.view)
        self.seg.pack(side="left")
        self.hint = ctk.CTkLabel(top, text="", anchor="w", text_color=t["text_muted"])
        self.hint.pack(side="left", padx=12)

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, pady=(8, 0))

        # Essentials (existing quick settings)
        from ui.quick_settings_tab import QuickSettingsPanel
        self.quick = QuickSettingsPanel(self.body, theme, app)

        # All settings
        self.all = ctk.CTkFrame(self.body, fg_color="transparent")
        bar = ctk.CTkFrame(self.all, fg_color=t["bg_card"], corner_radius=10)
        bar.pack(fill="x")
        self.file_var = ctk.StringVar(value="")
        self.file_menu = ctk.CTkOptionMenu(bar, variable=self.file_var, values=["-"], width=300,
                                           command=lambda _v: self._pick_file(), fg_color=t["border"],
                                           button_color=t["border"])
        self.file_menu.pack(side="left", padx=(10, 8), pady=8)
        self.search_var = ctk.StringVar(value="")
        self.search = ctk.CTkEntry(bar, textvariable=self.search_var,
                                   placeholder_text="Search settings (e.g. pvp, password, rate)")
        self.search.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.search_var.trace_add("write", lambda *_: self._schedule_render())
        self.save_btn = ctk.CTkButton(bar, text="Save changes", width=120, fg_color=t["accent_blue"],
                                      state="disabled", command=self._save)
        self.save_btn.pack(side="right", padx=(0, 10))
        self.discard_btn = ctk.CTkButton(bar, text="Discard", width=80, fg_color="transparent", border_width=1,
                                         border_color=t["border"], command=self._discard)
        self.count = ctk.CTkLabel(bar, text="", text_color=t["accent_gold"])
        self.count.pack(side="right", padx=8)
        self.all_note = ctk.CTkLabel(self.all, text="", anchor="w", justify="left", wraplength=900,
                                     text_color=t["text_muted"])
        self.all_note.pack(fill="x", pady=(6, 2))
        self.scroll = ctk.CTkScrollableFrame(self.all, fg_color=t["bg_dark"], corner_radius=8)
        self.scroll.pack(fill="both", expand=True)

        # Raw file
        self.raw = ctk.CTkFrame(self.body, fg_color="transparent")
        rbar = ctk.CTkFrame(self.raw, fg_color=t["bg_card"], corner_radius=10)
        rbar.pack(fill="x")
        self.raw_var = ctk.StringVar(value="")
        self.raw_menu = ctk.CTkOptionMenu(rbar, variable=self.raw_var, values=["-"], width=360,
                                          command=lambda _v: self._load_raw(), fg_color=t["border"],
                                          button_color=t["border"])
        self.raw_menu.pack(side="left", padx=(10, 8), pady=8)
        ctk.CTkButton(rbar, text="Save file", width=100, fg_color=t["accent_blue"],
                      command=self._save_raw).pack(side="right", padx=10)
        ctk.CTkButton(rbar, text="Reload", width=80, fg_color="transparent", border_width=1,
                      border_color=t["border"], command=self._load_raw).pack(side="right")
        self.raw_note = ctk.CTkLabel(self.raw, text="", anchor="w", text_color=t["text_muted"])
        self.raw_note.pack(fill="x", pady=(6, 2))
        self.raw_text = tkst.ScrolledText(self.raw, wrap="none", bg=t["code_bg"], fg=t["code_fg"],
                                          insertbackground=t["code_fg"], font=("Cascadia Mono", 10),
                                          undo=True, borderwidth=0)
        self.raw_text.pack(fill="both", expand=True)
        self._show_view()

    # -- server / view switching ---------------------------------------------------
    def set_server(self, sid, visible=False):
        if sid != self.sid and self.changes and not self._confirm_discard():
            return
        self.sid = sid
        self._loaded_for = None
        self.changes = {}
        self.form = None
        self._raw_id = None
        self.quick.set_server(sid, visible=visible and self.view == "Essentials")
        if visible:
            self.on_shown()

    def on_shown(self):
        if self.view == "Essentials":
            self.quick.on_shown()
        elif self.sid and self._loaded_for != self.sid:
            self._load_files()
        elif self.view == "Raw file" and self.files and not getattr(self, "_raw_id", None):
            self._load_raw()

    def _switch(self, view):
        if view == self.view:
            return
        try:
            self.seg.set(view)
        except Exception:
            pass
        self.view = view
        self._show_view()
        self.on_shown()

    def _show_view(self):
        for w in (self.quick, self.all, self.raw):
            w.pack_forget()
        target = {"Essentials": self.quick, "All settings": self.all, "Raw file": self.raw}[self.view]
        target.pack(fill="both", expand=True)
        self.hint.configure(text={"Essentials": "The settings most people change.",
                                  "All settings": "Every setting in the game's files - nothing to type by hand.",
                                  "Raw file": "The file as text. A .bak copy is kept on every save."}[self.view])

    def _confirm_discard(self) -> bool:
        return messagebox.askyesno("Unsaved changes", "Discard your unsaved setting changes?", parent=self)

    # -- files -----------------------------------------------------------------------
    def _load_files(self):
        sid = self.sid

        def work():
            from core import config_forms as cf
            try:
                res, err = cf.files(self.app.manager, sid, include_all=True), None
            except Exception as e:
                res, err = [], str(e)
            self.app.after(0, lambda: self._got_files(sid, res, err))
        threading.Thread(target=work, daemon=True).start()

    def _file_title(self, f):
        name = f["path"].replace("\\", "/").rsplit("/", 1)[-1]
        lab = ("%s (%s)" % (f["label"], name)) if f.get("known") else f["path"]
        if f.get("fresh") and not f.get("known"):
            lab += "  · written by the game"
        return lab + ("" if f.get("exists") else "  - not created yet")

    def _got_files(self, sid, files, err):
        if sid != self.sid:
            return
        self._loaded_for = sid
        self.files = files
        form_files = [f for f in files if f.get("format")]
        labels = [self._file_title(f) for f in form_files] or ["(no settings files found)"]
        self.file_menu.configure(values=labels)
        raw_labels = [self._file_title(f) for f in files] or ["(no files found)"]
        self.raw_menu.configure(values=raw_labels)
        if err:
            self.all_note.configure(text="Couldn't list the settings files: " + err)
        if not form_files:
            self.file_var.set(labels[0])
            self._clear_scroll()
            self.all_note.configure(text="Tofu didn't find any settings files for this server. Some games (like "
                                         "Valheim) keep their settings in the launch options - see Essentials. Others "
                                         "write them on their first start - start it once, stop it, then come back.")
        else:
            self.file_var.set(labels[0])
            self._pick_file(force=True)
        self.raw_var.set(raw_labels[0])
        if self.view == "Raw file" and files:
            self._load_raw()

    def _file_by_label(self, label, raw=False):
        pool = self.files if raw else [f for f in self.files if f.get("format")]
        return next((f for f in pool if self._file_title(f) == label), None)

    # -- all settings -------------------------------------------------------------------
    def _pick_file(self, force=False):
        f = self._file_by_label(self.file_var.get())
        if not f:
            return
        if self.changes and not force and not self._confirm_discard():
            if self.form:
                cur = next((x for x in self.files if x["id"] == self.form["file"]), None)
                if cur:
                    self.file_var.set(self._file_title(cur))
            return
        sid = self.sid
        self.all_note.configure(text="Loading...")

        def work():
            from core import config_forms as cf
            try:
                d, err = cf.form(self.app.manager, sid, f["id"]), None
            except Exception as e:
                d, err = None, str(e)
            self.app.after(0, lambda: self._got_form(sid, d, err))
        threading.Thread(target=work, daemon=True).start()

    def _got_form(self, sid, d, err):
        if sid != self.sid:
            return
        self.changes = {}
        self._update_count()
        if err:
            self.form = None
            self._clear_scroll()
            self.all_note.configure(text="Couldn't open it: " + err)
            return
        self.form = d
        self.open_groups = {g["name"] for g in d["groups"][:1]}
        self.group_limit = {}
        note = (d.get("note") + "  " if d.get("note") else "") + "File: " + d["path"]
        if d.get("running"):
            note += "  ·  the server is running, so changes apply after a restart"
        self.all_note.configure(text=note)
        self._render()

    def _clear_scroll(self):
        for w in self.scroll.winfo_children():
            w.destroy()

    def _schedule_render(self):
        if self._render_job:
            try:
                self.after_cancel(self._render_job)
            except Exception:
                pass
        self._render_job = self.after(400, self._render)

    def _groups(self):
        d = self.form or {}
        groups = list(d.get("groups") or [])
        if d.get("suggestions"):
            groups.append({"name": "More settings this game understands", "fields": d["suggestions"],
                           "missing": True})
        return groups

    PAGE = 30          # fields shown per group before "Show more"
    BATCH = 8          # fields built per step, so the window keeps responding

    def _render(self):
        """Group headers at once; the fields themselves are built a few at a time in
        the background (customtkinter widgets are slow to make), and each group shows
        PAGE fields until "Show more" is pressed."""
        self._render_job = None
        self._gen += 1
        gen = self._gen
        self._clear_scroll()
        if not self.form:
            return
        q = self.search_var.get().strip().lower()
        t = self.t
        todo = []
        any_shown = False
        for g in self._groups():
            fields = [f for f in g["fields"] if not q or q in (f["label"] + " " + f["key"] + " " +
                                                               (f.get("help") or "")).lower()]
            if not fields:
                continue
            any_shown = True
            is_open = bool(q) or g["name"] in self.open_groups
            head = ctk.CTkButton(self.scroll, anchor="w", height=34, corner_radius=8, fg_color=t["bg_card"],
                                 hover_color=t["border"], text_color=t["text_light"],
                                 font=("Segoe UI Semibold", 13),
                                 text=("▾  " if is_open else "▸  ") + g["name"] + "   (%d)" % len(fields)
                                      + ("  - change one to add it to the file" if g.get("missing") else ""),
                                 command=lambda n=g["name"]: self._toggle_group(n))
            head.pack(fill="x", padx=6, pady=(8, 2))
            if not is_open:
                continue
            grid = ctk.CTkFrame(self.scroll, fg_color="transparent")
            grid.pack(fill="x", padx=10)
            grid.grid_columnconfigure(0, weight=1, uniform="af")
            grid.grid_columnconfigure(1, weight=1, uniform="af")
            limit = self.group_limit.get(g["name"], self.PAGE)
            for i, f in enumerate(fields[:limit]):
                todo.append((grid, i, f))
            if len(fields) > limit:
                ctk.CTkButton(self.scroll, text="Show %d more" % min(self.PAGE * 2, len(fields) - limit),
                              height=28, fg_color="transparent", border_width=1, border_color=t["border"],
                              text_color=t["text_light"],
                              command=lambda n=g["name"], lim=limit: self._more(n, lim)).pack(pady=(2, 6))
        if not any_shown:
            ctk.CTkLabel(self.scroll, text="No settings match." if q else "No settings in this file.",
                         text_color=t["text_muted"]).pack(pady=20)
            return
        self._build(gen, todo, 0)

    def _build(self, gen, todo, start):
        if gen != self._gen:
            return                    # a newer render took over
        end = min(len(todo), start + self.BATCH)
        for grid, i, f in todo[start:end]:
            try:
                cell = self._field(grid, f)
                cell.grid(row=i // 2, column=i % 2, sticky="new", padx=8, pady=6)
            except Exception:
                return                # the tab was closed or re-rendered
        if end < len(todo):
            self.after(1, lambda: self._build(gen, todo, end))

    def _more(self, name, limit):
        self.group_limit[name] = limit + self.PAGE * 2
        self._render()

    def _toggle_group(self, name):
        if name in self.open_groups:
            self.open_groups.discard(name)
        else:
            self.open_groups.add(name)
        self._render()

    def _field(self, parent, f):
        t = self.t
        changed = f["id"] in self.changes
        cell = ctk.CTkFrame(parent, fg_color=t["bg_card"] if not changed else t["bg_input"], corner_radius=8,
                            border_width=1, border_color=t["accent_gold"] if changed else t["bg_card"])
        head = ctk.CTkFrame(cell, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(head, text=f["label"], anchor="w", text_color=t["text_light"],
                     font=("Segoe UI Semibold", 12)).pack(side="left")
        if f["key"].replace("_", "").replace("-", "").replace(".", "").lower() != \
                f["label"].replace(" ", "").lower():
            ctk.CTkLabel(head, text=f["key"], anchor="w", text_color=t["text_muted"],
                         font=("Cascadia Mono", 9)).pack(side="left", padx=(6, 0))
        if f.get("default") is not None and not f.get("readonly") and not f.get("missing"):
            ctk.CTkButton(head, text="reset", width=44, height=20, fg_color="transparent",
                          text_color=t["accent_gold"], hover_color=t["border"], font=("Segoe UI", 10),
                          command=lambda: self._set(f, f["default"], rerender=True)).pack(side="right")
        cur = self.changes.get(f["id"], f.get("value"))
        state = "disabled" if f.get("readonly") else "normal"
        typ = f["type"]
        if typ == "bool":
            var = ctk.BooleanVar(value=bool(cur) if not isinstance(cur, str) else cur.lower() in ("1", "true", "yes", "on"))
            sw = ctk.CTkSwitch(cell, text="On" if var.get() else "Off", variable=var, state=state,
                               progress_color=t["accent_blue"], text_color=t["text_muted"])
            sw.configure(command=lambda: (sw.configure(text="On" if var.get() else "Off"),
                                          self._set(f, var.get(), cell=cell)))
            sw.pack(anchor="w", padx=10, pady=(2, 4))
        elif typ == "select":
            opts = [(o[0], o[1]) if isinstance(o, (list, tuple)) else (o, o) for o in f.get("options") or []]
            labels = [str(o[1]) for o in opts]
            by_label = {str(o[1]): o[0] for o in opts}
            cur_label = next((str(o[1]) for o in opts if str(o[0]) == str(cur)), str(cur))
            if cur_label not in labels:
                labels = [cur_label] + labels
                by_label[cur_label] = cur
            var = ctk.StringVar(value=cur_label)
            ctk.CTkOptionMenu(cell, variable=var, values=labels, state=state, fg_color=t["bg_input"],
                              button_color=t["border"],
                              command=lambda v: self._set(f, by_label.get(v, v), cell=cell)).pack(fill="x", padx=10, pady=(2, 4))
        else:
            var = ctk.StringVar(value="" if typ == "password" and f["id"] not in self.changes else
                                ("" if cur is None else str(cur)))
            ph = ""
            if typ == "password":
                ph = "set - type a new one to change it" if f.get("set") else "not set"
            elif typ == "list":
                ph = "comma-separated"
            e = ctk.CTkEntry(cell, textvariable=var, placeholder_text=ph, state=state,
                             show="•" if typ == "password" else None)
            e.pack(fill="x", padx=10, pady=(2, 4))
            var.trace_add("write", lambda *_: self._set(f, var.get(), cell=cell))
        help_ = f.get("help") or ("Edit this one in the Raw file view." if f.get("readonly") else "")
        rng = []
        if f.get("min") is not None:
            rng.append("min %s" % f["min"])
        if f.get("max") is not None:
            rng.append("max %s" % f["max"])
        if rng and typ in ("int", "float"):
            help_ = (help_ + "  " if help_ else "") + "(" + ", ".join(rng) + ")"
        if help_:
            ctk.CTkLabel(cell, text=help_, anchor="w", justify="left", wraplength=380, text_color=t["text_muted"],
                         font=("Segoe UI", 10)).pack(fill="x", padx=10, pady=(0, 8))
        else:
            ctk.CTkFrame(cell, height=4, fg_color="transparent").pack()
        return cell

    def _same(self, f, v) -> bool:
        cur = f.get("value")
        typ = f["type"]
        if typ == "password":
            return v in ("", None)
        if typ == "bool":
            return bool(v) == bool(cur)
        if typ in ("int", "float"):
            try:
                return float(v) == float(cur)
            except (TypeError, ValueError):
                return False
        return str(v) == str("" if cur is None else cur)

    def _set(self, f, value, cell=None, rerender=False):
        if self._same(f, value):
            self.changes.pop(f["id"], None)
        else:
            self.changes[f["id"]] = value
        self._update_count()
        if cell is not None:
            on = f["id"] in self.changes
            try:
                cell.configure(border_color=self.t["accent_gold"] if on else self.t["bg_card"],
                               fg_color=self.t["bg_input"] if on else self.t["bg_card"])
            except Exception:
                pass
        if rerender:
            self._schedule_render()

    def _update_count(self):
        n = len(self.changes)
        self.count.configure(text=("%d unsaved change%s" % (n, "s" if n != 1 else "")) if n else "")
        self.save_btn.configure(state="normal" if n else "disabled")
        if n and not self.discard_btn.winfo_ismapped():
            self.discard_btn.pack(side="right", padx=(0, 6), before=self.count)
        elif not n and self.discard_btn.winfo_ismapped():
            self.discard_btn.pack_forget()

    def _discard(self):
        self.changes = {}
        self._update_count()
        self._render()

    def _save(self):
        if not self.form or not self.changes:
            return
        sid, fid, changes = self.sid, self.form["file"], dict(self.changes)
        self.save_btn.configure(state="disabled", text="Saving...")

        def work():
            from core import config_forms as cf
            try:
                r, err = cf.save(self.app.manager, sid, fid, changes, user="gui"), None
            except Exception as e:
                r, err = None, str(e)

            def done():
                self.save_btn.configure(text="Save changes")
                if err:
                    self._update_count()
                    messagebox.showerror("Not saved", err, parent=self)
                    return
                n = len(r.get("saved") or [])
                if r.get("pending"):
                    messagebox.showinfo("Saved for the next start", r.get("note") or "", parent=self)
                self.app._show_status(("Saved %d setting%s" % (n, "s" if n != 1 else "")) +
                                      (" - restart the server to use them" if r.get("restart_needed") else "")
                                      if n else "Nothing changed")
                self.changes = {}
                self._pick_file(force=True)
                self.quick._loaded = None
            self.app.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    # -- raw file -------------------------------------------------------------------------
    def _load_raw(self):
        f = self._file_by_label(self.raw_var.get(), raw=True)
        if not f:
            return
        sid = self.sid
        self.raw_note.configure(text="Loading...")

        def work():
            from core import config_forms as cf
            try:
                d, err = cf.raw_read(self.app.manager, sid, f["id"]), None
            except Exception as e:
                d, err = None, str(e)

            def done():
                if sid != self.sid:
                    return
                self.raw_text.delete("1.0", "end")
                if err:
                    self.raw_note.configure(text="Couldn't open it: " + err)
                    return
                self.raw_text.insert("1.0", d["text"])
                self.raw_text.edit_reset()
                self.raw_text.edit_modified(False)
                self.raw_note.configure(text="File: " + d["path"] + ("" if d["exists"] else "  (doesn't exist yet - saving creates it)"))
                self._raw_id = f["id"]
            self.app.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def _save_raw(self, force=False):
        fid = getattr(self, "_raw_id", None)
        if not fid or not self.sid:
            return
        text = self.raw_text.get("1.0", "end-1c")
        sid = self.sid
        from core import config_forms as cf
        try:
            r = cf.raw_write(self.app.manager, sid, fid, text, user="gui", force=force)
        except cf.FormError as e:
            if not force and str(e).endswith("Save anyway?"):
                if messagebox.askyesno("Check the file", str(e), parent=self):
                    self._save_raw(force=True)
                return
            messagebox.showerror("Not saved", str(e), parent=self)
            return
        except Exception as e:
            messagebox.showerror("Not saved", str(e), parent=self)
            return
        self.raw_text.edit_modified(False)
        self.app._show_status("Saved" + (" - restart the server to use it" if r.get("restart_needed") else ""))
        self._loaded_for = None   # forms re-read the file next time
