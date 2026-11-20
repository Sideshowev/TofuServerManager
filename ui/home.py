"""Home screen: overview strip + one card per server.

Pure presentation. The app passes in callbacks (start/stop/manage/add) and
calls ``HomeView.update(statuses, summary)`` from its poll loop; cards are
updated in place, never rebuilt, so nothing flickers.
"""

from __future__ import annotations

import hashlib
import time
from typing import Callable, Dict, List, Optional

import tkinter as tk

import customtkinter as ctk

CARD_MIN_WIDTH = 360   # px at 100% scaling
MAX_COLUMNS = 4

STATE_TEXT = {
    "running": "Online", "starting": "Starting", "updating": "Updating",
    "stopping": "Stopping", "restarting": "Restarting", "crashed": "Crashed",
    "stopped": "Offline", "unreachable": "PC offline",
}
BUSY = ("starting", "updating", "stopping", "restarting")
BADGE_COLORS = ["#7a5b20", "#2f6e57", "#2f517f", "#574f88", "#7f414c", "#17607a", "#6b4f2a", "#3f5f2a"]


def fmt_mb(mb) -> str:
    if mb is None:
        return "-"
    return ("%.1f GB" % (mb / 1024)) if mb >= 1024 else ("%d MB" % mb)


def fmt_uptime(sec) -> str:
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


def fmt_ago(ts) -> str:
    if not ts:
        return "never"
    s = max(0, time.time() - ts)
    if s < 3600:
        return "%dm ago" % max(1, s // 60)
    if s < 86400:
        return "%dh ago" % (s // 3600)
    return "%dd ago" % (s // 86400)


from core.game_catalog import game_label  # noqa: E402,F401  (kept here for older imports)


def initials(text: str) -> str:
    words = [w for w in "".join(c if c.isalnum() else " " for c in text).split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def badge_color(text: str) -> str:
    h = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)
    return BADGE_COLORS[h % len(BADGE_COLORS)]


class ServerCard(ctk.CTkFrame):
    """One server: is it up, who's playing, and how friends join (or the one thing to fix)."""

    def __init__(self, master, theme: dict, server_id: str, on_toggle: Callable, on_manage: Callable,
                 on_fix: Optional[Callable] = None, on_copy: Optional[Callable] = None):
        super().__init__(master, fg_color=theme["bg_card"], corner_radius=16, border_width=0)
        t = self.theme = theme
        self.server_id = server_id
        self._on_toggle, self._on_manage = on_toggle, on_manage
        self._on_fix, self._on_copy = on_fix, on_copy
        self._state = None
        self._action = None          # what the box's button does now: ("copy", text) / ("fix", kind) / ...

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(20, 0))
        self.badge = ctk.CTkLabel(top, text="?", width=48, height=48, corner_radius=12,
                                  fg_color=BADGE_COLORS[0], text_color="#ffffff",
                                  font=("Bahnschrift SemiBold", 16, "bold"))
        self.badge.pack(side="left")
        names = ctk.CTkFrame(top, fg_color="transparent")
        names.pack(side="left", fill="x", expand=True, padx=(14, 0))
        self.name_lbl = ctk.CTkLabel(names, text="", anchor="w", text_color=t["text_light"],
                                     font=("Bahnschrift SemiBold", 17, "bold"))
        self.name_lbl.pack(fill="x")
        self.game_lbl = ctk.CTkLabel(names, text="", anchor="w", text_color=t["text_muted"],
                                     font=("Segoe UI", 12))
        self.game_lbl.pack(fill="x")

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(16, 0))
        self.dot = ctk.CTkLabel(row, text="\u25cf", width=16, text_color=t["status_inactive"],
                                font=("Segoe UI", 14))
        self.dot.pack(side="left")
        self.state_lbl = ctk.CTkLabel(row, text="Off", anchor="w", text_color=t["text_light"],
                                      font=("Bahnschrift SemiBold", 21, "bold"))
        self.state_lbl.pack(side="left", padx=(6, 0))
        self.players_lbl = ctk.CTkLabel(row, text="", anchor="e", text_color=t["text_muted"],
                                        font=("Segoe UI", 12))
        self.players_lbl.pack(side="right")
        self.who_lbl = ctk.CTkLabel(self, text="", anchor="w", justify="left", text_color=t["text_muted"],
                                    font=("Segoe UI", 12), wraplength=300)
        self.who_lbl.pack(fill="x", padx=20, pady=(4, 0))

        self.box = ctk.CTkFrame(self, fg_color=t["bg_dark"], corner_radius=12)
        self.box.pack(fill="x", padx=20, pady=(14, 0))
        left = ctk.CTkFrame(self.box, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=(14, 8), pady=10)
        self.box_cap = ctk.CTkLabel(left, text="", anchor="w", text_color=t["text_muted"], font=("Segoe UI", 11))
        self.box_cap.pack(fill="x")
        self.box_text = ctk.CTkLabel(left, text="", anchor="w", justify="left", text_color=t["text_light"],
                                     font=("Segoe UI Semibold", 13), wraplength=220)
        self.box_text.pack(fill="x")
        self.box_btn = ctk.CTkButton(self.box, text="Copy invite", width=110, height=40, corner_radius=10,
                                     fg_color=t["accent_blue"], font=("Segoe UI Semibold", 13),
                                     command=self._box_click)
        self._box_btn_on = False

        self.stats_lbl = ctk.CTkLabel(self, text="", anchor="w", text_color=t["text_muted"], font=("Segoe UI", 11))
        self.stats_lbl.pack(fill="x", padx=20, pady=(10, 0))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(12, 20))
        self.toggle_btn = ctk.CTkButton(btns, text="Start", height=42, corner_radius=10,
                                        fg_color=t["success"], font=("Segoe UI Semibold", 14),
                                        command=lambda: self._on_toggle(self.server_id, self._state))
        self.toggle_btn.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(btns, text="Manage", height=42, corner_radius=10, fg_color="transparent",
                      border_width=1, border_color=t["border"], text_color=t["text_light"],
                      font=("Segoe UI", 14),
                      command=lambda: on_manage(self.server_id)).pack(side="left", fill="x", expand=True,
                                                                      padx=(10, 0))
        self.bind("<Configure>", self._wrap)

    # ------------------------------------------------------------------ pieces
    def _wrap(self, _e=None):
        try:
            w = max(160, int(self.winfo_width() / max(float(self._get_widget_scaling()), 0.5)) - 60)
        except Exception:
            return
        self.who_lbl.configure(wraplength=w)
        self.box_text.configure(wraplength=max(120, w - (130 if self._box_btn_on else 10)))

    def _set_box(self, caption: str, text: str, button: Optional[str] = None, action=None,
                 tone: str = "normal", muted: bool = False):
        t = self.theme
        self.box_cap.configure(text=caption, text_color=t["warning"] if tone == "warn" else t["text_muted"])
        self.box_text.configure(text=text, text_color=t["text_muted"] if muted else t["text_light"])
        self._action = action
        if button:
            colors = {"copy": (t["accent_blue"], "#ffffff"), "fix": (t["accent_gold"], "#1a1305"),
                      "manage": (t["border"], t["text_light"])}
            fg, tc = colors.get((action or ("manage",))[0], colors["manage"])
            self.box_btn.configure(text=button, fg_color=fg, text_color=tc)
            if not self._box_btn_on:
                self.box_btn.pack(side="right", padx=(0, 10), pady=10)
                self._box_btn_on = True
        elif self._box_btn_on:
            self.box_btn.pack_forget()
            self._box_btn_on = False
        self.box.configure(fg_color="#2a2413" if tone == "warn" else t["bg_dark"])
        self._wrap()

    def _box_click(self):
        if not self._action:
            return
        kind = self._action[0]
        if kind == "copy" and self._on_copy:
            self._on_copy(self.server_id, self._action[1])
        elif kind == "fix" and self._on_fix:
            self._on_fix(self.server_id, self._action[1])
        else:
            self._on_manage(self.server_id)

    def set_spark(self, data):
        """Kept for the app's poll loop; the card no longer draws a graph (History has them)."""

    # ------------------------------------------------------------------ update
    def update_status(self, st: dict, srv: dict, game: str, last_backup=None, reach: Optional[dict] = None):
        t = self.theme
        state = st.get("state") or ("running" if st.get("isRunning") else "stopped")
        self._state = state
        name = st.get("name", "Server")
        self.name_lbl.configure(text=name if len(name) <= 30 else name[:29] + "\u2026")
        self.game_lbl.configure(text=game)
        g = game.split("  \u00b7  ")[0]
        self.badge.configure(text=initials(g if g != "Custom server" else name), fg_color=badge_color(g))
        running = bool(st.get("isRunning"))
        r = reach or {}
        verdict = r.get("verdict")
        problem = running and verdict in ("blocked", "cgnat")
        if state == "running" and problem:
            label, color = "Online, but closed", t["warning"]
        elif state == "crashed":
            label, color = "Stopped unexpectedly", t["danger"]
        elif state in BUSY:
            label, color = STATE_TEXT[state] + "\u2026", t["warning"]
        elif state == "unreachable":
            label, color = "PC can't be reached", t["warning"]
        else:
            label = {"running": "Online", "stopped": "Off"}.get(state, STATE_TEXT.get(state, state.title()))
            color = t["status_online"] if running else t["status_inactive"]
        self.dot.configure(text_color=color)
        self.state_lbl.configure(text=label)

        names = [p.get("name") for p in st.get("players") or [] if isinstance(p, dict) and p.get("name")]
        if running and st.get("playerCount") is not None:
            self.players_lbl.configure(text="%s of %s playing" % (st["playerCount"], st.get("maxPlayers") or "?"))
        else:
            self.players_lbl.configure(text="")
        if names:
            more = len(names) - 3
            who = ", ".join(names[:3]) if more <= 0 else "%s and %d more" % (", ".join(names[:3]), more)
        else:
            who = ""
        self.who_lbl.configure(text=who)

        # the box: how friends join, or the one thing to fix
        if st.get("remote"):
            self._set_box("On another PC", st.get("pcName") or "", muted=True)
        elif state == "crashed":
            why = (st.get("crashHelp") or [{}])[0].get("title") or "exit code %s" % st.get("exitCode")
            self._set_box("What happened", why, "See why", ("manage",), tone="warn")
        elif st.get("installed") is False:
            self._set_box("One thing to do", "Install the game files", "Install", ("manage",), tone="warn")
        elif st.get("exeMissing"):
            self._set_box("One thing to fix", "Can't find %s" % st["exeMissing"], "Fix", ("manage",), tone="warn")
        elif problem:
            self._set_box("One thing to fix", r.get("title") or "", "Show me how", ("fix", r.get("fix") or "connect"),
                          tone="warn")
        elif running and verdict == "relay":
            self._set_box("Game ID", r.get("address") or "", "Copy invite", ("copy", r.get("invite") or ""))
        elif running and r.get("address"):
            cap = {"ok": "Friends join at", "likely": "Friends join at (probably reachable)"}.get(
                verdict, "Friends join at")
            self._set_box(cap, r["address"], "Copy invite", ("copy", r.get("invite") or r["address"]))
        elif running:
            self._set_box("Friends join at", r.get("title") or "Checking\u2026", muted=True)
        elif state in BUSY:
            self._set_box("Friends join at", "Shown once it's running", muted=True)
        else:
            self._set_box("Friends join at", "Shown once it's running", muted=True)

        bits = []
        if running:
            if st.get("cpuPercent") is not None:
                bits.append("CPU %s%%" % st["cpuPercent"])
            if st.get("memUsageMB"):
                bits.append(fmt_mb(st["memUsageMB"]))
            if st.get("uptimeSeconds"):
                bits.append("up " + fmt_uptime(st["uptimeSeconds"]))
        if not st.get("remote"):
            bits.append("last backup " + fmt_ago(last_backup))
        if st.get("updateAvailable"):
            bits.append("game update available")
        self.stats_lbl.configure(text="  \u00b7  ".join(bits),
                                 text_color=t["warning"] if st.get("updateAvailable") else t["text_muted"])

        if state == "unreachable":
            self.toggle_btn.configure(text="PC offline", state="disabled", fg_color=t["border"],
                                      border_width=0)
        elif state in BUSY:
            self.toggle_btn.configure(text=STATE_TEXT[state] + "\u2026", state="disabled", fg_color=t["border"],
                                      border_width=0)
        elif running:
            self.toggle_btn.configure(text="Stop", state="normal", fg_color="transparent", border_width=1,
                                      border_color=t["border"], text_color=t["text_light"])
        else:
            self.toggle_btn.configure(text="Start", state="normal", fg_color=t["success"], border_width=0,
                                      text_color="#ffffff")


class Headline(ctk.CTkFrame):
    """"2 of 3 servers are up" and one line under it - replaces the four tiles."""

    def __init__(self, master, theme: dict):
        super().__init__(master, fg_color="transparent")
        self.theme = theme
        self.big = ctk.CTkLabel(self, text="", anchor="w", text_color=theme["text_light"],
                                font=("Bahnschrift SemiBold", 30, "bold"))
        self.big.pack(fill="x")
        self.small = ctk.CTkLabel(self, text="", anchor="w", text_color=theme["text_muted"],
                                  font=("Segoe UI", 14))
        self.small.pack(fill="x", pady=(2, 0))

    def show(self, summary: Optional[dict]):
        if not summary:
            return
        n, up = summary.get("servers") or 0, summary.get("online") or 0
        if not n:
            self.big.configure(text="No servers yet")
        elif up == n:
            self.big.configure(text="All %d servers are up" % n if n > 1 else "Your server is up")
        elif up == 0:
            self.big.configure(text="All servers are off" if n > 1 else "Your server is off")
        else:
            self.big.configure(text="%d of %d servers are up" % (up, n))
        bits = []
        if summary.get("players"):
            bits.append("%d %s playing right now." % (summary["players"],
                                                     "friend" if summary["players"] == 1 else "friends"))
        backs = [v for v in (summary.get("last_backups") or {}).values() if v]
        if backs:
            bits.append("Last backup %s." % fmt_ago(max(backs)))
        nt = summary.get("next_task")
        if nt:
            bits.append("Next: %s %s." % (str(nt.get("kind", "")).lower(), time.strftime("%a %H:%M",
                                                                                       time.localtime(nt["at"]))))
        self.small.configure(text="  ".join(bits))


class HomeView(ctk.CTkFrame):
    def __init__(self, master, theme: dict, on_toggle: Callable, on_manage: Callable,
                 on_add: Callable, presets: dict, get_server: Callable, on_import: Optional[Callable] = None,
                 on_checkup: Optional[Callable] = None, get_reach: Optional[Callable] = None,
                 on_fix: Optional[Callable] = None):
        super().__init__(master, fg_color=theme["bg_dark"])
        self.theme = theme
        self._on_toggle, self._on_manage = on_toggle, on_manage
        self._get_reach, self._on_fix = get_reach, on_fix
        self.presets = presets
        self.get_server = get_server
        self.cards: Dict[str, ServerCard] = {}
        self._cols = None
        self._order: List[str] = []

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(16, 10))
        self.overview = Headline(head, theme)
        self.overview.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(head, text="+  Add Server", height=38, corner_radius=10,
                      fg_color=theme["accent_blue"], font=("Segoe UI Semibold", 13),
                      command=on_add).pack(side="right")
        if on_import:
            ctk.CTkButton(head, text="Find existing servers", height=38, corner_radius=10,
                          fg_color="transparent", border_width=1, border_color=theme["border"],
                          text_color=theme["text_light"], command=on_import).pack(side="right", padx=(0, 8))
        if on_checkup:
            ctk.CTkButton(head, text="Checkup", height=38, width=96, corner_radius=10,
                          fg_color="transparent", border_width=1, border_color=theme["border"],
                          text_color=theme["text_light"], command=on_checkup).pack(side="right", padx=(0, 8))


        self.grid_frame = ctk.CTkScrollableFrame(self, fg_color=theme["bg_dark"])
        self.grid_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.empty = ctk.CTkFrame(self.grid_frame, fg_color=theme["bg_card"], corner_radius=14)
        ctk.CTkLabel(self.empty, text="No servers yet", text_color=theme["text_light"],
                     font=("Bahnschrift SemiBold", 18, "bold")).pack(pady=(30, 4))
        ctk.CTkLabel(self.empty, text="Pick a game, choose a folder, and Tofu installs it for you.",
                     text_color=theme["text_muted"]).pack()
        ctk.CTkButton(self.empty, text="+  Add your first server", height=40, corner_radius=10,
                      fg_color=theme["accent_blue"], command=on_add).pack(pady=(14, 6))
        if on_import:
            ctk.CTkButton(self.empty, text="I already have servers installed - find them", height=34,
                          corner_radius=10, fg_color="transparent", border_width=1, border_color=theme["border"],
                          text_color=theme["text_light"], command=on_import).pack(pady=(0, 30))
        self.bind("<Configure>", lambda e: self.after_idle(self._layout))
        try:   # the scroll area's own width is what the cards have to share
            self.grid_frame._parent_canvas.bind("<Configure>", lambda e: self.after_idle(self._layout), add="+")
        except Exception:
            pass

    def _columns(self) -> int:
        try:
            w = int(self.winfo_width())
            scale = float(self._get_widget_scaling()) if hasattr(self, "_get_widget_scaling") else 1.0
        except Exception:
            w, scale = 0, 1.0
        if w <= 1:
            return 3   # not drawn yet
        return max(1, min(MAX_COLUMNS, int(w // (CARD_MIN_WIDTH * max(scale, 0.5)))))

    def _avail_width(self) -> int:
        """Pixels the card grid can use (the scroll area, minus its scrollbar)."""
        try:
            w = int(self.grid_frame._parent_canvas.winfo_width())
        except Exception:
            w = 0
        if w <= 1:
            try:
                w = int(self.winfo_width()) - 40
            except Exception:
                w = 0
        return w

    def _layout(self, force: bool = False):
        cols = self._columns()
        avail = self._avail_width()
        # Explicit column widths: the scrollable frame doesn't always stretch its inner
        # frame to the window (high-DPI screens), which squeezed every card to a sliver.
        col_w = max(0, avail // cols - 16) if avail > 1 else 0
        key = (cols, col_w)
        if not force and key == self._cols:
            return
        self._cols = key
        if avail > 1:
            try:
                self.grid_frame._parent_canvas.itemconfigure(self.grid_frame._create_window_id, width=avail)
            except Exception:
                pass
        for i in range(6):
            used = i < cols
            self.grid_frame.grid_columnconfigure(i, weight=1 if used else 0, uniform="cards" if used else "",
                                                 minsize=col_w if used else 0)
        for idx, sid in enumerate(self._order):
            card = self.cards.get(sid)
            if card is not None:
                card.grid(row=idx // cols, column=idx % cols, sticky="nsew", padx=8, pady=8)

    def _copy(self, server_id: str, text: str):
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            top = self.winfo_toplevel()
            if hasattr(top, "_show_status"):
                top._show_status("Invite copied - paste it to your friends")
        except Exception:
            pass

    def set_sparklines(self, sparks: Dict[str, dict]):
        for sid, card in self.cards.items():
            card.set_spark((sparks or {}).get(sid))

    def refresh(self, statuses: List[dict], summary: Optional[dict] = None):
        self.overview.show(summary)
        ids = [s["id"] for s in statuses]
        for sid in [k for k in self.cards if k not in ids]:
            self.cards.pop(sid).destroy()
        changed = ids != self._order
        for st in statuses:
            card = self.cards.get(st["id"])
            if card is None:
                card = ServerCard(self.grid_frame, self.theme, st["id"], self._on_toggle, self._on_manage,
                                  on_fix=self._on_fix, on_copy=self._copy)
                self.cards[st["id"]] = card
                changed = True
            if st.get("remote"):          # a server on a linked PC
                card.update_status(st, {}, "%s  ·  on %s" % (st.get("game") or "Server", st.get("pcName")), None)
                continue
            srv = self.get_server(st["id"]) or {}
            last = (summary or {}).get("last_backups", {}).get(st["id"])
            reach = None
            if self._get_reach:
                try:
                    reach = self._get_reach(st["id"], bool(st.get("isRunning")))
                except Exception:
                    reach = None
            card.update_status(st, srv, game_label(srv, self.presets), last, reach=reach)
        self._order = ids
        if not ids:
            self.empty.grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=8)
        else:
            try:
                self.empty.grid_forget()
            except Exception:
                pass
        if changed:
            self._layout(force=True)
