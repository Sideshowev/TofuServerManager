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

CARD_MIN_WIDTH = 340   # px at 100% scaling
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
    def __init__(self, master, theme: dict, server_id: str, on_toggle: Callable, on_manage: Callable):
        super().__init__(master, fg_color=theme["bg_card"], corner_radius=14,
                         border_width=1, border_color=theme["border"])
        self.theme = theme
        self.server_id = server_id
        self._on_toggle = on_toggle
        self._state = None

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(14, 6))
        self.badge = ctk.CTkLabel(top, text="?", width=42, height=42, corner_radius=10,
                                  fg_color=BADGE_COLORS[0], text_color="#ffffff",
                                  font=("Bahnschrift SemiBold", 15, "bold"))
        self.badge.pack(side="left")
        names = ctk.CTkFrame(top, fg_color="transparent")
        names.pack(side="left", fill="x", expand=True, padx=(10, 6))
        self.name_lbl = ctk.CTkLabel(names, text="", anchor="w", text_color=theme["text_light"],
                                     font=("Bahnschrift SemiBold", 15, "bold"))
        self.name_lbl.pack(fill="x")
        self.game_lbl = ctk.CTkLabel(names, text="", anchor="w", text_color=theme["text_muted"],
                                     font=("Segoe UI", 11))
        self.game_lbl.pack(fill="x")
        self.pill = ctk.CTkLabel(top, text="Offline", corner_radius=12, height=26, width=96,
                                 fg_color=theme["bg_input"], text_color=theme["status_inactive"],
                                 font=("Segoe UI Semibold", 11))
        self.pill.pack(side="right", anchor="n")
        names.pack_configure(side="left")  # names sit between badge and pill

        self.stats_lbl = ctk.CTkLabel(self, text="", anchor="w", justify="left",
                                      text_color=theme["text_light"], font=("Segoe UI", 12))
        self.stats_lbl.pack(fill="x", padx=14)
        self.cpu_bar = ctk.CTkProgressBar(self, height=6, progress_color=theme["accent_blue"],
                                          fg_color=theme["bg_input"])
        self.cpu_bar.set(0)
        self.cpu_bar.pack(fill="x", padx=14, pady=(6, 4))
        self._bar_on = True
        self._show_bar(False)
        self.spark = tk.Canvas(self, height=30, bg=theme["bg_card"], highlightthickness=0)
        self.spark.pack(fill="x", padx=14, pady=(0, 2))
        self._spark_data = None
        self.spark.bind("<Configure>", lambda e: self._draw_spark())
        self.foot_lbl = ctk.CTkLabel(self, text="", anchor="w", text_color=theme["text_muted"],
                                     font=("Segoe UI", 11))
        self.foot_lbl.pack(fill="x", padx=14)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=(8, 14))
        self.toggle_btn = ctk.CTkButton(btns, text="Start", height=36, corner_radius=10,
                                        fg_color=theme["success"], font=("Segoe UI Semibold", 13),
                                        command=lambda: self._on_toggle(self.server_id, self._state))
        self.toggle_btn.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(btns, text="Manage ›", width=96, height=36, corner_radius=10,
                      fg_color="transparent", border_width=1, border_color=theme["border"],
                      text_color=theme["text_light"],
                      command=lambda: on_manage(self.server_id)).pack(side="left", padx=(8, 0))

    def set_spark(self, data):
        """Last 6 hours: players if the game reports them, otherwise CPU."""
        if data == self._spark_data:
            return
        self._spark_data = data
        self._draw_spark()

    def _draw_spark(self):
        from ui.history import draw_sparkline, LINE
        d = self._spark_data or {}
        if d.get("players"):
            vals = d["players"]
            peak = max((v for v in vals if v is not None), default=0)
            draw_sparkline(self.spark, vals, LINE, self.theme["text_muted"],
                           "players, 6h (peak %d)" % peak, vmax=max(peak, 1))
        else:
            draw_sparkline(self.spark, d.get("cpu") or [], LINE, self.theme["text_muted"],
                           "CPU, 6h" if any(v is not None for v in d.get("cpu") or []) else "", vmax=100)

    def _show_bar(self, on: bool):
        """The CPU bar only appears while a server runs; the gap stays so cards don't jump."""
        if on == self._bar_on:
            return
        self._bar_on = on
        self.cpu_bar.configure(progress_color=self.theme["accent_blue"] if on else self.theme["bg_card"],
                               fg_color=self.theme["bg_input"] if on else self.theme["bg_card"])
        if not on:
            self.cpu_bar.set(0)

    def update_status(self, st: dict, srv: dict, game: str, last_backup=None):
        t = self.theme
        state = st.get("state") or ("running" if st.get("isRunning") else "stopped")
        self._state = state
        name = st.get("name", "Server")
        self.name_lbl.configure(text=name if len(name) <= 26 else name[:25] + "\u2026")
        self.game_lbl.configure(text=game)
        g = game.split("  ·  ")[0]
        self.badge.configure(text=initials(g if g != "Custom server" else name), fg_color=badge_color(g))
        color = {"running": t["status_online"], "crashed": t["danger"]}.get(
            state, t["warning"] if state in BUSY else t["status_inactive"])
        self.pill.configure(text=STATE_TEXT.get(state, state.title()), text_color=color)

        if st.get("isRunning"):
            parts = []
            if st.get("playerCount") is not None:
                who = ""
                names = [p.get("name") for p in st.get("players") or [] if p.get("name")]
                if names:
                    who = " (" + ", ".join(names[:3]) + (" +%d" % (len(names) - 3) if len(names) > 3 else "") + ")"
                parts.append("\U0001F464 %s/%s%s" % (st["playerCount"], st.get("maxPlayers") or "?", who))
            if st.get("cpuPercent") is not None:
                parts.append("CPU %s%%" % st["cpuPercent"])
            if st.get("memUsageMB"):
                parts.append(fmt_mb(st["memUsageMB"]))
            if st.get("uptimeSeconds"):
                parts.append("up " + fmt_uptime(st["uptimeSeconds"]))
            self.stats_lbl.configure(text="   ".join(parts) or "Running")
            self._show_bar(True)
            self.cpu_bar.set(min(1.0, (st.get("cpuPercent") or 0) / 100.0))
        elif state == "crashed":
            why = (st.get("crashHelp") or [{}])[0].get("title")
            text = ("Stopped: " + why) if why else "Stopped unexpectedly (exit code %s)" % st.get("exitCode")
            self.stats_lbl.configure(text=text if len(text) <= 60 else text[:59] + "\u2026")
            self._show_bar(False)
        elif state == "unreachable":
            self.stats_lbl.configure(text="Can't reach %s - is it on, with the Watchdog running?" % st.get("pcName"))
            self._show_bar(False)
        elif state in BUSY:
            self.stats_lbl.configure(text=STATE_TEXT[state] + "…")
            self._show_bar(False)
        elif st.get("installed") is False:
            self.stats_lbl.configure(text="Game files not installed yet - Manage › Install Game")
            self._show_bar(False)
        elif st.get("exeMissing"):
            self.stats_lbl.configure(text="Can't find %s - check the program in Edit Server" % st["exeMissing"])
            self._show_bar(False)
        else:
            self.stats_lbl.configure(text="Ready to start" if not srv.get("manualStart") else
                                     "Ready to start  ·  manual start")
            self._show_bar(False)
        foot = ("On another PC: %s" % st.get("pcName")) if st.get("remote") else "Last backup " + fmt_ago(last_backup)
        if srv.get("idleStopMinutes"):
            foot += "  ·  auto-stops after %s min empty" % srv["idleStopMinutes"]
        if st.get("updateAvailable"):
            foot += "  ·  \u2b06 game update available"
        self.foot_lbl.configure(text=foot, text_color=self.theme["warning"] if st.get("updateAvailable")
                                else self.theme["text_muted"])

        if state == "unreachable":
            self.toggle_btn.configure(text="PC offline", state="disabled", fg_color=t["border"])
        elif state in BUSY:
            self.toggle_btn.configure(text=STATE_TEXT[state] + "…", state="disabled",
                                      fg_color=t["border"])
        elif st.get("isRunning"):
            self.toggle_btn.configure(text="Stop", state="normal", fg_color=t["danger"])
        else:
            self.toggle_btn.configure(text="Start", state="normal", fg_color=t["success"])


class OverviewStrip(ctk.CTkFrame):
    def __init__(self, master, theme: dict):
        super().__init__(master, fg_color="transparent")
        self.theme = theme
        self.tiles = {}
        for i, key in enumerate(("online", "players", "load", "next")):
            tile = ctk.CTkFrame(self, fg_color=theme["bg_card"], corner_radius=12,
                                border_width=1, border_color=theme["border"])
            tile.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            self.grid_columnconfigure(i, weight=1 if key != "next" else 2, uniform="tiles")
            cap = ctk.CTkLabel(tile, text="", anchor="w", text_color=theme["text_muted"],
                               font=("Segoe UI", 11))
            cap.pack(fill="x", padx=14, pady=(10, 0))
            val = ctk.CTkLabel(tile, text="-", anchor="w", text_color=theme["text_light"],
                               font=("Bahnschrift SemiBold", 18, "bold"))
            val.pack(fill="x", padx=14)
            sub = ctk.CTkLabel(tile, text="", anchor="w", text_color=theme["text_muted"],
                               font=("Segoe UI", 11))
            sub.pack(fill="x", padx=14, pady=(0, 10))
            self.tiles[key] = (cap, val, sub)
        self.tiles["online"][0].configure(text="SERVERS ONLINE")
        self.tiles["players"][0].configure(text="PLAYERS ONLINE")
        self.tiles["load"][0].configure(text="SERVER LOAD")
        self.tiles["next"][0].configure(text="NEXT SCHEDULED TASK")

    def show(self, summary: Optional[dict]):
        if not summary:
            return
        cap, val, sub = self.tiles["online"]
        val.configure(text="%d / %d" % (summary["online"], summary["servers"]))
        sub.configure(text="running now")
        cap, val, sub = self.tiles["players"]
        val.configure(text="-" if summary["players"] is None else str(summary["players"]))
        sub.configure(text="on servers that report players")
        cap, val, sub = self.tiles["load"]
        val.configure(text="CPU %s%%  ·  %s" % (summary["server_cpu"], fmt_mb(summary["server_ram_mb"])))
        if summary.get("system_ram_total_mb"):
            sub.configure(text="PC: CPU %s%%, RAM %s of %s" % (
                summary.get("system_cpu"), fmt_mb(summary["system_ram_used_mb"]),
                fmt_mb(summary["system_ram_total_mb"])))
        cap, val, sub = self.tiles["next"]
        nt = summary.get("next_task")
        if nt:
            when = time.strftime("%a %H:%M", time.localtime(nt["at"]))
            val.configure(text="%s %s" % (nt["kind"].title(), when))
            sub.configure(text=nt["server"])
        else:
            val.configure(text="None")
            sub.configure(text="set one up from a server's Manage page")


class HomeView(ctk.CTkFrame):
    def __init__(self, master, theme: dict, on_toggle: Callable, on_manage: Callable,
                 on_add: Callable, presets: dict, get_server: Callable, on_import: Optional[Callable] = None,
                 on_checkup: Optional[Callable] = None):
        super().__init__(master, fg_color=theme["bg_dark"])
        self.theme = theme
        self._on_toggle, self._on_manage = on_toggle, on_manage
        self.presets = presets
        self.get_server = get_server
        self.cards: Dict[str, ServerCard] = {}
        self._cols = None
        self._order: List[str] = []

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(16, 10))
        ctk.CTkLabel(head, text="Your servers", text_color=theme["text_light"],
                     font=("Bahnschrift SemiBold", 22, "bold")).pack(side="left")
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

        self.overview = OverviewStrip(self, theme)
        self.overview.pack(fill="x", padx=18, pady=(0, 12))

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
                card = ServerCard(self.grid_frame, self.theme, st["id"], self._on_toggle, self._on_manage)
                self.cards[st["id"]] = card
                changed = True
            if st.get("remote"):          # a server on a linked PC
                card.update_status(st, {}, "%s  ·  on %s" % (st.get("game") or "Server", st.get("pcName")), None)
                continue
            srv = self.get_server(st["id"]) or {}
            last = (summary or {}).get("last_backups", {}).get(st["id"])
            card.update_status(st, srv, game_label(srv, self.presets), last)
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
