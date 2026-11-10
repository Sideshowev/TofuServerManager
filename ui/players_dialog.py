"""Desktop "Players" window: allow list, bans and admins for the selected server
(core/player_access.py), the same for every game."""

from __future__ import annotations

import threading

import customtkinter as ctk

from core import player_access as PA


class LocalPlayers:
    """This PC's servers."""

    def __init__(self, mgr):
        self.mgr = mgr

    def overview(self, sid):
        return PA.overview(self.mgr, sid)

    def change(self, sid, lst, action, who, reason, everywhere):
        if everywhere:
            res = PA.change_many(self.mgr, PA.same_game(self.mgr, sid), lst, action, who, reason, by="gui")
            return ("\n".join(r.get("text") or ("⚠ " + r.get("error", "")) for r in res.values()),
                    not any("error" in r for r in res.values()))
        return PA.change(self.mgr, sid, lst, action, who, reason, by="gui")["text"], True

    def set_allow_on(self, sid, on):
        return PA.set_allow_on(self.mgr, sid, on, by="gui")


class RemotePlayers:
    """A server on a linked PC (core/linked_pcs.py)."""

    def __init__(self, mgr):
        from core import linked_pcs
        self.fleet = linked_pcs.fleet(mgr)

    def overview(self, sid):
        return self.fleet.call(sid, "players.access")

    def change(self, sid, lst, action, who, reason, everywhere):
        r = self.fleet.call(sid, "players.access_change", {"list": lst, "action": action, "who": who,
                                                           "reason": reason, "everywhere": everywhere})
        return r.get("text") or "Done", True

    def set_allow_on(self, sid, on):
        return self.fleet.call(sid, "players.allow_on", {"on": on})


class PlayersWindow(ctk.CTkToplevel):
    def __init__(self, app, theme, server_id):
        super().__init__(app)
        self.app, self.t, self.mgr, self.sid = app, theme, app.manager, server_id
        self.backend = RemotePlayers(self.mgr) if str(server_id).startswith("@") else LocalPlayers(self.mgr)
        self.title("Players")
        self.transient(app)
        app.center_window(self, 680, 640, fit=True)
        self.tab = "allow"
        self.info = None
        self.head = ctk.CTkLabel(self, text="", anchor="w", font=("Bahnschrift SemiBold", 17, "bold"),
                                 text_color=theme["text_light"])
        self.head.pack(fill="x", padx=18, pady=(14, 2))
        self.sub = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=630,
                                text_color=theme["text_muted"])
        self.sub.pack(fill="x", padx=18)
        self.tabs = ctk.CTkSegmentedButton(self, values=[], command=self._pick_tab)
        self.tabs.pack(fill="x", padx=16, pady=(8, 4))
        self.top = ctk.CTkFrame(self, fg_color="transparent")
        self.top.pack(fill="x", padx=16)
        self.list = ctk.CTkScrollableFrame(self, fg_color=theme["bg_card"], corner_radius=10, height=220)
        self.list.pack(fill="both", expand=True, padx=16, pady=6)
        add = ctk.CTkFrame(self, fg_color="transparent")
        add.pack(fill="x", padx=16, pady=(2, 2))
        self.who = ctk.StringVar(value="")
        self.reason = ctk.StringVar(value="")
        self.who_entry = ctk.CTkEntry(add, textvariable=self.who, width=300)
        self.who_entry.pack(side="left")
        self.reason_entry = ctk.CTkEntry(add, textvariable=self.reason, width=170, placeholder_text="reason")
        self.add_btn = ctk.CTkButton(add, text="Add", width=90, fg_color=theme["accent_blue"], command=self._add)
        self.add_btn.pack(side="right")
        self.everywhere = ctk.BooleanVar(value=False)
        self.every_cb = ctk.CTkCheckBox(self, text="", variable=self.everywhere, fg_color=theme["accent_blue"])
        self.every_cb.pack(anchor="w", padx=18, pady=(2, 0))
        self.online = ctk.CTkFrame(self, fg_color="transparent")
        self.online.pack(fill="x", padx=16, pady=(2, 0))
        self.msg = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=630,
                                text_color=theme["text_muted"])
        self.msg.pack(fill="x", padx=18, pady=(4, 2))
        self.notes = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=630,
                                  text_color=theme["text_muted"], font=("Segoe UI", 11))
        self.notes.pack(fill="x", padx=18, pady=(0, 10))
        self.reload()

    # ------------------------------------------------------------------ data
    def reload(self):
        def work():
            try:
                info = self.backend.overview(self.sid)
            except Exception as e:
                info = {"supported": False, "why": str(e)}
            try:
                self.after(0, lambda: self._show(info))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _show(self, info):
        if not self.winfo_exists():
            return
        self.info = info
        t = self.t
        self.head.configure(text="Players - %s" % (info.get("name") or ""))
        if not info.get("supported"):
            self.sub.configure(text=info.get("why") or "")
            self.tabs.configure(values=[])
            self.add_btn.configure(state="disabled")
            return
        labels = info["labels"]
        self.tabs.configure(values=[labels[k] for k in info["lists"]])
        if self.tab not in info["lists"]:
            self.tab = info["lists"][0]
        self.tabs.set(labels[self.tab])
        self.sub.configure(text="%s · players are added by %s." % (info["game"], info["id_hint"]))
        self.every_cb.configure(text="Do it on all my %s servers" % info["game"])
        self.notes.configure(text="\n".join(info.get("notes") or []))
        self._render()
        self._online()

    def _pick_tab(self, label):
        if not self.info:
            return
        for k, v in self.info["labels"].items():
            if v == label:
                self.tab = k
        self._render()

    def _render(self):
        t, info = self.t, self.info
        for w in self.list.winfo_children():
            w.destroy()
        for w in self.top.winfo_children():
            w.destroy()
        if self.tab == "allow" and info.get("allow_toggle"):
            on = bool(info.get("allow_on"))
            var = ctk.BooleanVar(value=on)
            ctk.CTkSwitch(self.top, text="Only players on this list can join", variable=var,
                          command=lambda: self._toggle(var.get())).pack(anchor="w", pady=(0, 4))
        elif self.tab == "allow" and info.get("allow_on") is not None:
            ctk.CTkLabel(self.top, text="The list is %s." % ("on - only these players can join" if info["allow_on"]
                                                            else "empty, so anyone can join"),
                         text_color=t["text_muted"]).pack(anchor="w")
        if self.tab in info.get("needs_running", []):
            ctk.CTkLabel(self.top, text="Start the server to change this list - %s changes it through its console."
                         % info["game"], text_color=t["warning"]).pack(anchor="w")
        rows = info["data"].get(self.tab) or []
        if not rows:
            ctk.CTkLabel(self.list, text="Nobody yet.", text_color=t["text_muted"]).pack(anchor="w", padx=10, pady=8)
        for r in rows:
            row = ctk.CTkFrame(self.list, fg_color=t["bg_dark"], corner_radius=8)
            row.pack(fill="x", padx=4, pady=2)
            label = r.get("name") or r.get("id") or "?"
            if r.get("name") and r.get("id") and info["id_kind"] != "name":
                label += "  ·  %s" % r["id"]
            if r.get("reason"):
                label += "  ·  %s" % r["reason"]
            ctk.CTkLabel(row, text=label, anchor="w", text_color=t["text_light"]).pack(side="left", padx=10, pady=5)
            who = r.get("name") if info["id_kind"] == "name" else r.get("id")
            if who:
                ctk.CTkButton(row, text="Remove", width=80, fg_color="transparent", border_width=1,
                              border_color=t["border"],
                              command=lambda w=who: self._change("remove", w)).pack(side="right", padx=6)
        if self.tab == "ban":
            self.reason_entry.pack(side="left", padx=8)
        else:
            self.reason_entry.pack_forget()
        self.who_entry.configure(placeholder_text=info["id_hint"])
        self.add_btn.configure(text={"allow": "Allow", "ban": "Ban", "admin": "Make admin"}[self.tab],
                               state="disabled" if self.tab in info.get("needs_running", []) else "normal")

    def _online(self):
        for w in self.online.winfo_children():
            w.destroy()
        if self.info.get("id_kind") != "name":
            return
        try:
            pool = getattr(self.app, "_remote_statuses", []) if str(self.sid).startswith("@") else \
                self.mgr.get_all_statuses()
            st = next((s for s in pool if s.get("id") == self.sid), {}) or {}
        except Exception:
            st = {}
        names = [str(p.get("name") if isinstance(p, dict) else p) for p in st.get("players") or []][:12]
        if not names:
            return
        ctk.CTkLabel(self.online, text="Online now:", text_color=self.t["text_muted"]).pack(side="left")
        for n in names:
            ctk.CTkButton(self.online, text=n, width=40, height=24, fg_color=self.t["border"],
                          command=lambda x=n: self.who.set(x)).pack(side="left", padx=2)

    # ------------------------------------------------------------------ actions
    def _add(self):
        who = self.who.get().strip()
        if not who:
            self.msg.configure(text="Type who first.", text_color=self.t["warning"])
            return
        self._change("add", who)

    def _change(self, action, who):
        lst, reason, every = self.tab, self.reason.get().strip(), self.everywhere.get()
        self.msg.configure(text="Working…", text_color=self.t["text_muted"])

        def work():
            try:
                text, ok = self.backend.change(self.sid, lst, action, who, reason, every)
            except Exception as e:
                text, ok = str(e), False
            try:
                self.after(0, lambda: self._done(text, ok))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _done(self, text, ok):
        if not self.winfo_exists():
            return
        self.msg.configure(text=("✓ " if ok else "⚠ ") + text,
                           text_color=self.t["status_online"] if ok else self.t["warning"])
        if ok:
            self.who.set("")
            self.reason.set("")
        self.after(800, self.reload)      # consoles write their files a moment later

    def _toggle(self, on):
        try:
            res = self.backend.set_allow_on(self.sid, on)
            self.msg.configure(text="✓ Allow list %s%s" % ("on" if on else "off", " (restart the server to apply)"
                                                           if res.get("restart_needed") else ""),
                               text_color=self.t["status_online"])
        except Exception as e:
            self.msg.configure(text="⚠ %s" % e, text_color=self.t["warning"])
        self.after(800, self.reload)
