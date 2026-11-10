"""Game Settings tab: server name, password, max players... as a simple form."""

from __future__ import annotations

import threading

import customtkinter as ctk


class QuickSettingsPanel(ctk.CTkFrame):
    def __init__(self, master, theme, app, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.t, self.app = theme, app
        self.sid = None
        self._loaded = None
        self.vars = {}
        self.note = ctk.CTkLabel(self, text="Select a server.", anchor="w", justify="left", wraplength=880,
                                 text_color=theme["text_muted"])
        self.note.pack(fill="x", pady=(0, 6))
        self.form = ctk.CTkScrollableFrame(self, fg_color=theme["bg_dark"], corner_radius=8)
        self.form.pack(fill="both", expand=True)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        self.btn = ctk.CTkButton(row, text="Save game settings", fg_color=theme["accent_blue"], command=self._save)
        self.btn.pack(side="left")
        self.status = ctk.CTkLabel(row, text="", anchor="w", text_color=theme["text_muted"])
        self.status.pack(side="left", padx=10)

    def set_server(self, sid, visible=False):
        self.sid = sid
        self._loaded = None
        if visible:
            self.load()

    def on_shown(self):
        if self.sid and self._loaded != self.sid:
            self.load()

    def load(self):
        sid = self.sid
        if not sid:
            return

        def work():
            from core.quick_settings import get
            try:
                d, err = get(self.app.manager, sid), None
            except Exception as e:
                d, err = None, str(e)
            self.app.after(0, lambda: self._render(sid, d, err))
        threading.Thread(target=work, daemon=True).start()

    def _render(self, sid, d, err):
        if sid != self.sid:
            return
        self._loaded = sid
        for w in self.form.winfo_children():
            w.destroy()
        self.vars = {}
        if err:
            self.note.configure(text="Couldn't read the game settings: " + err)
            return
        self.note.configure(text=(d.get("note") or "") + ("  Changes apply the next time the server starts."
                                                         if d.get("supported") else ""))
        self.btn.configure(state="normal" if d.get("supported") else "disabled")
        t = self.t
        for f in d.get("fields") or []:
            r = ctk.CTkFrame(self.form, fg_color="transparent")
            r.pack(fill="x", padx=8, pady=4)
            lab = f["label"] + ("  (%s)" % f["error"] if f.get("error") else "")
            ctk.CTkLabel(r, text=lab, width=300, anchor="w", text_color=t["warning"] if f.get("error") else t["text_light"]).pack(side="left")
            if f["type"] in ("select", "bool"):
                choices = f.get("choices") or [["true", "Yes"], ["false", "No"]]
                labels = [c[1] for c in choices]
                cur = next((c[1] for c in choices if str(c[0]) == str(f.get("value"))), labels[0])
                var = ctk.StringVar(value=cur)
                ctk.CTkOptionMenu(r, variable=var, values=labels, width=280).pack(side="left")
                self.vars[f["key"]] = (var, {c[1]: c[0] for c in choices})
            else:
                var = ctk.StringVar(value=f.get("value") or "")
                ph = ("set - type a new one to change it" if f.get("has_value") else "none") if f["type"] == "password" else ""
                ctk.CTkEntry(r, textvariable=var, width=280, placeholder_text=ph,
                             show="*" if f["type"] == "password" else None).pack(side="left")
                self.vars[f["key"]] = (var, None)

    def _save(self):
        sid = self.sid
        if not sid:
            return
        values = {}
        for k, (var, mapping) in self.vars.items():
            v = var.get()
            values[k] = mapping.get(v, v) if mapping else v
        self.status.configure(text="Saving...")

        def work():
            from core.quick_settings import save
            try:
                r, err = save(self.app.manager, sid, values, user="gui"), None
            except Exception as e:
                r, err = None, str(e)

            def done():
                if err:
                    self.status.configure(text="Not saved: " + err, text_color=self.t["warning"])
                    return
                self.status.configure(text_color=self.t["text_muted"],
                                      text=("Saved to " + ", ".join(r["where"]) + "." +
                                            (" Restart the server to use them." if r.get("restart_needed") else ""))
                                      if r["saved"] else "Nothing changed.")
                self.load()
            self.app.after(0, done)
        threading.Thread(target=work, daemon=True).start()
