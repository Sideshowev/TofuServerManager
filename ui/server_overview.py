"""The Overview tab on a server's page: status, the setup checklist, who's playing, shortcuts.

Pure presentation, like ui/home.py. The app gives it callbacks and calls
``show(status, items, game)`` whenever it has fresh data; rows are reused, never rebuilt.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

import customtkinter as ctk

from core.checklist import DONE, NEEDS, progress

ICONS = {DONE: ("✓", "#1F4A31", "#7FE8A6"),     # tick on dark green
         NEEDS: ("!", "#F2B544", "#1A1305"),          # exclamation on gold
         "todo": ("", "#2A2E38", "#8C95A6")}          # empty ring


class ChecklistRow(ctk.CTkFrame):
    def __init__(self, master, theme: dict, on_action: Callable[[str], None]):
        super().__init__(master, fg_color="transparent")
        t = self.theme = theme
        self._on_action = on_action
        self._action = None
        holder = ctk.CTkFrame(self, width=30, height=30, fg_color="transparent")   # same width for every icon
        holder.pack(side="left", anchor="n", pady=(2, 0))
        holder.pack_propagate(False)
        self.icon = ctk.CTkLabel(holder, text="", width=28, height=28, corner_radius=14,
                                 font=("Segoe UI Semibold", 14, "bold"))
        self.icon.place(relx=0.5, rely=0.5, anchor="center")
        mid = ctk.CTkFrame(self, fg_color="transparent")
        mid.pack(side="left", fill="x", expand=True, padx=(14, 10))
        self.title = ctk.CTkLabel(mid, text="", anchor="w", text_color=t["text_light"],
                                  font=("Segoe UI Semibold", 14))
        self.title.pack(fill="x")
        self.detail = ctk.CTkLabel(mid, text="", anchor="w", justify="left", text_color=t["text_muted"],
                                   font=("Segoe UI", 12), wraplength=420)
        self.detail.pack(fill="x")
        self.btn = ctk.CTkButton(self, text="", width=120, height=38, corner_radius=10,
                                 font=("Segoe UI Semibold", 13), command=self._click)
        self._btn_on = False
        self.bind("<Configure>", self._wrap)

    def _wrap(self, _e=None):
        try:
            w = int(self.winfo_width() / max(float(self._get_widget_scaling()), 0.5))
        except Exception:
            return
        self.detail.configure(wraplength=max(160, w - (190 if self._btn_on else 60)))

    def _click(self):
        if self._action:
            self._on_action(self._action)

    def show(self, item: dict):
        t = self.theme
        glyph, bg, fg = ICONS.get(item["state"], ICONS["todo"])
        self.icon.configure(text=glyph, fg_color=bg, text_color=fg)
        self.title.configure(text=item["title"])
        self.detail.configure(text=item.get("detail") or "",
                              text_color=t["warning"] if item["state"] == NEEDS else t["text_muted"])
        self._action = item.get("action") if item.get("enabled") else None
        if item.get("button"):
            if item["state"] == NEEDS:
                fg_c, tc, bw = t["accent_gold"], "#1a1305", 0
            elif (item.get("action") or "").startswith("copy:"):
                fg_c, tc, bw = t["accent_blue"], "#ffffff", 0
            else:
                fg_c, tc, bw = "transparent", t["text_light"], 1
            self.btn.configure(text=item["button"], fg_color=fg_c, text_color=tc, border_width=bw,
                               border_color=t["border"], state="normal" if item.get("enabled") else "disabled")
            if not self._btn_on:
                self.btn.pack(side="right", anchor="n")
                self._btn_on = True
        elif self._btn_on:
            self.btn.pack_forget()
            self._btn_on = False
        self._wrap()


class ServerOverview(ctk.CTkScrollableFrame):
    """`on_action(action)`: the checklist buttons. `on_power(kind)`: "start" / "stop" / "restart".
    `shortcuts`: [(label, callback)] for the More box."""

    def __init__(self, master, theme: dict, on_action: Callable[[str], None], on_power: Callable[[str], None],
                 shortcuts: List[tuple]):
        super().__init__(master, fg_color="transparent")
        t = self.theme = theme
        self._on_power = on_power

        # status line + power buttons
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=6, pady=(4, 14))
        self.dot = ctk.CTkLabel(top, text="●", width=16, text_color=t["status_inactive"], font=("Segoe UI", 14))
        self.dot.pack(side="left")
        self.state_lbl = ctk.CTkLabel(top, text="", anchor="w", text_color=t["text_light"],
                                      font=("Bahnschrift SemiBold", 21, "bold"))
        self.state_lbl.pack(side="left", padx=(6, 12))
        self.sub_lbl = ctk.CTkLabel(top, text="", anchor="w", text_color=t["text_muted"], font=("Segoe UI", 13))
        self.sub_lbl.pack(side="left")
        self.btn_restart = ctk.CTkButton(top, text="Restart", width=100, height=40, corner_radius=10,
                                         fg_color="transparent", border_width=1, border_color=t["border"],
                                         text_color=t["text_light"], command=lambda: on_power("restart"))
        self.btn_toggle = ctk.CTkButton(top, text="Start", width=100, height=40, corner_radius=10,
                                        fg_color=t["success"], font=("Segoe UI Semibold", 14),
                                        command=lambda: on_power(self._toggle_kind))
        self.btn_toggle.pack(side="right")
        self._toggle_kind = "start"
        self._restart_on = False

        cols = ctk.CTkFrame(self, fg_color="transparent")
        cols.pack(fill="both", expand=True, padx=6)
        cols.grid_columnconfigure(0, weight=3, uniform="c")
        cols.grid_columnconfigure(1, weight=2, uniform="c")

        # the checklist
        ready = ctk.CTkFrame(cols, fg_color=t["bg_dark"], corner_radius=14)
        ready.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        head = ctk.CTkFrame(ready, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(head, text="Getting it ready", anchor="w", text_color=t["text_light"],
                     font=("Bahnschrift SemiBold", 18, "bold")).pack(side="left")
        self.progress_lbl = ctk.CTkLabel(head, text="", anchor="e", text_color=t["text_muted"], font=("Segoe UI", 12))
        self.progress_lbl.pack(side="right")
        self.rows: List[ChecklistRow] = []
        self._rows_box = ready
        self._on_action = on_action

        # right column: playing now + more
        right = ctk.CTkFrame(cols, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        play = ctk.CTkFrame(right, fg_color=t["bg_dark"], corner_radius=14)
        play.pack(fill="x")
        ctk.CTkLabel(play, text="Playing now", anchor="w", text_color=t["text_light"],
                     font=("Bahnschrift SemiBold", 18, "bold")).pack(fill="x", padx=20, pady=(18, 4))
        self.play_lbl = ctk.CTkLabel(play, text="", anchor="w", justify="left", text_color=t["text_muted"],
                                     font=("Segoe UI", 13), wraplength=260)
        self.play_lbl.pack(fill="x", padx=20, pady=(0, 18))
        more = ctk.CTkFrame(right, fg_color=t["bg_dark"], corner_radius=14)
        more.pack(fill="x", pady=(14, 0))
        ctk.CTkLabel(more, text="More", anchor="w", text_color=t["text_light"],
                     font=("Bahnschrift SemiBold", 18, "bold")).pack(fill="x", padx=20, pady=(18, 8))
        grid = ctk.CTkFrame(more, fg_color="transparent")
        grid.pack(fill="x", padx=16, pady=(0, 16))
        grid.grid_columnconfigure((0, 1), weight=1, uniform="m")
        for i, (label, cb) in enumerate(shortcuts):
            ctk.CTkButton(grid, text=label, height=40, corner_radius=10, fg_color="transparent", border_width=1,
                          border_color=t["border"], text_color=t["text_light"], font=("Segoe UI", 13),
                          command=cb).grid(row=i // 2, column=i % 2, sticky="ew", padx=4, pady=4)
        self.bind("<Configure>", self._wrap)

    def _wrap(self, _e=None):
        try:
            w = int(self.play_lbl.master.winfo_width() / max(float(self._get_widget_scaling()), 0.5))
            self.play_lbl.configure(wraplength=max(140, w - 44))
        except Exception:
            pass

    # ------------------------------------------------------------------ data in
    def show_status(self, st: dict, reach: Optional[dict] = None):
        t = self.theme
        st = st or {}
        state = st.get("state") or ("running" if st.get("isRunning") else "stopped")
        running = bool(st.get("isRunning"))
        verdict = (reach or {}).get("verdict")
        busy = state in ("starting", "updating", "stopping", "restarting")
        if running and verdict in ("blocked", "cgnat"):
            label, color = "Online, but closed", t["warning"]
        elif state == "crashed":
            label, color = "Stopped unexpectedly", t["danger"]
        elif busy:
            label, color = state.title() + "…", t["warning"]
        elif running:
            label, color = "Online", t["status_online"]
        else:
            label, color = "Off", t["status_inactive"]
        self.dot.configure(text_color=color)
        self.state_lbl.configure(text=label)
        if running and st.get("playerCount") is not None:
            self.sub_lbl.configure(text="%s of %s playing" % (st["playerCount"], st.get("maxPlayers") or "?"))
        else:
            self.sub_lbl.configure(text="")

        names = [p.get("name") for p in st.get("players") or [] if isinstance(p, dict) and p.get("name")]
        if names:
            self.play_lbl.configure(text="\n".join(names[:12]) + ("\n+ %d more" % (len(names) - 12)
                                                                   if len(names) > 12 else ""),
                                    text_color=t["text_light"])
        elif running and st.get("playerCount"):
            self.play_lbl.configure(text="%d playing (the game doesn't share names)" % st["playerCount"],
                                    text_color=t["text_light"])
        elif running:
            self.play_lbl.configure(text="Nobody yet. You can join from this PC to test it.",
                                    text_color=t["text_muted"])
        else:
            self.play_lbl.configure(text="The server is off.", text_color=t["text_muted"])

        if busy:
            self.btn_toggle.configure(text=state.title() + "…", state="disabled", fg_color=t["border"])
        elif running:
            self._toggle_kind = "stop"
            self.btn_toggle.configure(text="Stop", state="normal", fg_color=t["danger"])
        else:
            self._toggle_kind = "start"
            self.btn_toggle.configure(text="Start", state="normal", fg_color=t["success"])
        if running and not busy and not self._restart_on:
            self.btn_restart.pack(side="right", padx=(0, 10))
            self._restart_on = True
        elif (not running or busy) and self._restart_on:
            self.btn_restart.pack_forget()
            self._restart_on = False

    def show_items(self, items: List[dict]):
        while len(self.rows) < len(items):
            row = ChecklistRow(self._rows_box, self.theme, self._on_action)
            row.pack(fill="x", padx=20, pady=(12, 0 if len(self.rows) < len(items) - 1 else 18))
            self.rows.append(row)
        for row, item in zip(self.rows, items):
            row.show(item)
        self.progress_lbl.configure(text=progress(items) if items else "")
