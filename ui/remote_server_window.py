"""Desktop window for a server on a linked PC (core/linked_pcs.py): status, the
live console, start / stop / restart, back up, update and players. Everything is
done by Tofu on that PC (and recorded in its own activity log)."""

from __future__ import annotations

import threading

import customtkinter as ctk

from core import linked_pcs


class RemoteServerWindow(ctk.CTkToplevel):
    def __init__(self, app, theme, server_id):
        super().__init__(app)
        self.app, self.t, self.sid = app, theme, server_id
        self.fleet = linked_pcs.fleet(app.manager)
        self.offset = -1
        self.alive = True
        t = theme
        self.title("Server on another PC")
        self.transient(app)
        app.center_window(self, 760, 600, fit=True)
        self.head = ctk.CTkLabel(self, text="", anchor="w", font=("Bahnschrift SemiBold", 17, "bold"),
                                 text_color=t["text_light"])
        self.head.pack(fill="x", padx=18, pady=(14, 0))
        self.sub = ctk.CTkLabel(self, text="", anchor="w", text_color=t["text_muted"])
        self.sub.pack(fill="x", padx=18)
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(8, 4))
        self.power = ctk.CTkButton(btns, text="Start", width=90, command=self._power)
        self.power.pack(side="left")
        for text, action in (("Restart", "server.restart"), ("Back up now", "backup.create"),
                             ("Update game", "server.update")):
            ctk.CTkButton(btns, text=text, width=110, fg_color=t["border"],
                          command=lambda a=action, x=text: self._do(a, x)).pack(side="left", padx=(8, 0))
        ctk.CTkButton(btns, text="Players…", width=90, fg_color=t["border"], command=self._players).pack(
            side="left", padx=(8, 0))
        self.console = ctk.CTkTextbox(self, wrap="none", fg_color=t["bg_card"], font=("Consolas", 11))
        self.console.pack(fill="both", expand=True, padx=16, pady=6)
        self.console.configure(state="disabled")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 4))
        self.cmd = ctk.StringVar(value="")
        ent = ctk.CTkEntry(row, textvariable=self.cmd, placeholder_text="Type a server command and press Enter")
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda _e: self._send())
        ctk.CTkButton(row, text="Send", width=80, fg_color=t["accent_blue"], command=self._send).pack(
            side="left", padx=(8, 0))
        self.msg = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=700,
                                text_color=t["text_muted"])
        self.msg.pack(fill="x", padx=18, pady=(0, 10))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._status()
        self._logs()

    def _close(self):
        self.alive = False
        self.destroy()

    def _bg(self, fn, done):
        def work():
            try:
                res, err = fn(), None
            except Exception as e:
                res, err = None, e
            try:
                if self.alive:
                    self.after(0, lambda: done(res, err))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _st(self):
        return next((s for s in getattr(self.app, "_remote_statuses", []) if s.get("id") == self.sid), None)

    def _status(self):
        if not self.alive:
            return
        st = self._st() or {}
        name = st.get("name") or self.sid
        self.head.configure(text=name)
        state = st.get("state") or ("running" if st.get("isRunning") else "stopped")
        bits = ["%s on %s" % (st.get("game") or "Server", st.get("pcName") or "another PC"),
                {"running": "online", "unreachable": "PC can't be reached"}.get(state, state)]
        if st.get("isRunning") and st.get("playerCount") is not None:
            bits.append("%s/%s players" % (st["playerCount"], st.get("maxPlayers") or "?"))
        self.sub.configure(text="  ·  ".join(bits))
        running = bool(st.get("isRunning"))
        self.power.configure(text="Stop" if running else "Start",
                             fg_color=self.t["danger"] if running else self.t["success"],
                             state="disabled" if state == "unreachable" else "normal")
        self.after(3000, self._status)

    def _logs(self):
        if not self.alive:
            return

        def got(r, err):
            if r and r.get("lines") is not None:
                self.console.configure(state="normal")
                if r.get("reset"):
                    self.console.delete("1.0", "end")
                if r["lines"]:
                    self.console.insert("end", "\n".join(r["lines"]) + "\n")
                    self.console.see("end")
                self.console.configure(state="disabled")
                self.offset = r.get("offset", self.offset)
            self.after(2000, self._logs)
        self._bg(lambda: self.fleet.call(self.sid, "logs.since", {"offset": self.offset}, timeout=10), got)

    def _report(self, label):
        def done(r, err):
            if err:
                self.msg.configure(text="⚠ %s: %s" % (label, err), text_color=self.t["warning"])
            else:
                self.msg.configure(text="✓ %s" % label, text_color=self.t["status_online"])
                self.app.after(1200, self.app._poll_linked_pcs)
        return done

    def _power(self):
        st = self._st() or {}
        action = "server.stop" if st.get("isRunning") else "server.start"
        label = "Stopping" if st.get("isRunning") else "Starting"
        self.msg.configure(text=label + "…", text_color=self.t["text_muted"])
        self._bg(lambda: self.fleet.call(self.sid, action, timeout=120), self._report(label))

    def _do(self, action, label):
        self.msg.configure(text=label + "…", text_color=self.t["text_muted"])
        self._bg(lambda: self.fleet.call(self.sid, action, timeout=600), self._report(label))

    def _send(self):
        cmd = self.cmd.get().strip()
        if not cmd:
            return
        self.cmd.set("")
        self._bg(lambda: self.fleet.call(self.sid, "console.send", {"command": cmd}), self._report("Sent: " + cmd))

    def _players(self):
        from ui.players_dialog import PlayersWindow
        PlayersWindow(self.app, self.t, self.sid)
