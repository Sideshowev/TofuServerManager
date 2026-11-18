"""History graphs: small sparklines for the Home cards and a History tab with
CPU / RAM / players charts (one measure per chart, shared time axis)."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from typing import List, Optional

import customtkinter as ctk

LINE = "#c98500"        # validated dark-surface line color (one series per chart)
RANGES = (("1h", "1 hour"), ("6h", "6 hours"), ("24h", "24 hours"), ("7d", "7 days"))


def _fmt_mb(v) -> str:
    v = float(v or 0)
    return "%.1f GB" % (v / 1024) if v >= 1024 else "%d MB" % v


def draw_sparkline(cv: tk.Canvas, values: List[Optional[float]], color: str, muted: str,
                   caption: str = "", vmax: Optional[float] = None):
    """Line over the canvas width; None values leave gaps."""
    cv.delete("all")
    w = max(int(cv.winfo_width()), 60)
    h = max(int(cv.winfo_height()), 20)
    pad = 3
    nums = [v for v in values if v is not None]
    cv.create_line(0, h - 1, w, h - 1, fill=muted, width=1, dash=(1, 3))
    if caption:
        cv.create_text(w - 2, 1, text=caption, anchor="ne", fill=muted, font=("Segoe UI", 8))
    if not nums:
        return
    top = vmax if vmax else max(max(nums), 1e-9)
    n = len(values)
    step = (w - 2 * pad) / max(n - 1, 1)
    seg = []
    for i, v in enumerate(values):
        if v is None:
            if len(seg) >= 4:
                cv.create_line(*seg, fill=color, width=2, capstyle="round", joinstyle="round")
            elif len(seg) == 2:
                cv.create_oval(seg[0] - 1.5, seg[1] - 1.5, seg[0] + 1.5, seg[1] + 1.5, fill=color, outline="")
            seg = []
            continue
        x = pad + i * step
        y = h - pad - (min(v, top) / top) * (h - 2 * pad - 8)
        seg += [x, y]
    if len(seg) >= 4:
        cv.create_line(*seg, fill=color, width=2, capstyle="round", joinstyle="round")
    elif len(seg) == 2:
        cv.create_oval(seg[0] - 1.5, seg[1] - 1.5, seg[0] + 1.5, seg[1] + 1.5, fill=color, outline="")


class Chart(tk.Canvas):
    """One measure over time: recessive grid, 2px line, crosshair + readout on hover."""

    def __init__(self, master, theme, title, fmt, height=150):
        super().__init__(master, height=height, bg=theme["bg_card"], highlightthickness=0)
        self.t = theme
        self.title = title
        self.fmt = fmt
        self.points = []     # (ts, value or None)
        self.since = self.until = 0
        self.bucket = 60
        self.vmax = None
        self.note = ""
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self._hover(None))

    def set_data(self, points, since, until, bucket, vmax=None, note=""):
        self.points, self.since, self.until, self.bucket = points, since, until, bucket
        self.vmax = vmax
        self.note = note
        self.redraw()

    def _geom(self):
        w, h = max(self.winfo_width(), 200), max(self.winfo_height(), 80)
        return 52, 24, w - 12, h - 22   # left, top, right, bottom

    def _x(self, ts):
        l, _t, r, _b = self._geom()
        span = max(self.until - self.since, 1)
        return l + (ts - self.since) / span * (r - l)

    def _y(self, v, top):
        _l, t, _r, b = self._geom()
        return b - (v / top) * (b - t) if top else b

    def _top(self):
        vals = [v for _ts, v in self.points if v is not None]
        top = self.vmax or (max(vals) if vals else 0)
        if not self.vmax:
            top = _nice(top * 1.1) if top > 0 else 1
        return top

    def redraw(self):
        self.delete("all")
        t = self.t
        l, tp, r, b = self._geom()
        muted, grid, ink = t["text_muted"], t["border"], t["text_light"]
        self.create_text(l - 44, 4, text=self.title, anchor="nw", fill=ink, font=("Segoe UI Semibold", 11))
        top = self._top()
        for frac in (0, 0.5, 1):
            y = b - frac * (b - tp)
            self.create_line(l, y, r, y, fill=grid, width=1)
            self.create_text(l - 6, y, text=self.fmt(top * frac), anchor="e", fill=muted, font=("Segoe UI", 9))
        for frac, anchor in ((0, "nw"), (0.5, "n"), (1, "ne")):
            ts = self.since + frac * (self.until - self.since)
            fmt = "%a %H:%M" if self.until - self.since > 86400 else "%H:%M"
            self.create_text(l + frac * (r - l), b + 5, text=time.strftime(fmt, time.localtime(ts)),
                             anchor=anchor, fill=muted, font=("Segoe UI", 9))
        vals = [p for p in self.points if p[1] is not None]
        if not vals:
            self.create_text((l + r) / 2, (tp + b) / 2, text=getattr(self, "note", "") or "No data yet",
                             fill=muted, font=("Segoe UI", 10))
            return
        seg, last_ts = [], None
        gap = self.bucket * 2.5
        for ts, v in self.points:
            if v is None or (last_ts is not None and ts - last_ts > gap):
                self._flush(seg)
                seg = []
            if v is not None:
                seg += [self._x(ts), self._y(v, top)]
                last_ts = ts
        self._flush(seg)

    def _flush(self, seg):
        if len(seg) >= 4:
            self.create_line(*seg, fill=LINE, width=2, capstyle="round", joinstyle="round")
        elif len(seg) == 2:
            self.create_oval(seg[0] - 2.5, seg[1] - 2.5, seg[0] + 2.5, seg[1] + 2.5, fill=LINE, outline="")

    def _hover(self, e):
        self.delete("hover")
        if e is None or not self.points:
            return
        l, tp, r, b = self._geom()
        if not (l <= e.x <= r):
            return
        ts_at = self.since + (e.x - l) / max(r - l, 1) * (self.until - self.since)
        pts = [p for p in self.points if p[1] is not None]
        if not pts:
            return
        ts, v = min(pts, key=lambda p: abs(p[0] - ts_at))
        if abs(ts - ts_at) > self.bucket * 3:
            return
        x, y = self._x(ts), self._y(v, self._top())
        self.create_line(x, tp, x, b, fill=self.t["text_muted"], width=1, tags="hover")
        self.create_oval(x - 4, y - 4, x + 4, y + 4, fill=LINE, outline=self.t["bg_card"], width=2, tags="hover")
        fmt = "%a %H:%M" if self.until - self.since > 86400 else "%H:%M"
        self.create_text(r, 4, text="%s   %s" % (self.fmt(v), time.strftime(fmt, time.localtime(ts))),
                         anchor="ne", fill=self.t["text_light"], font=("Segoe UI Semibold", 10), tags="hover")


def _nice(v: float) -> float:
    import math
    if v <= 0:
        return 1
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * e:
            return m * e
    return 10 * e


class HistoryPanel(ctk.CTkFrame):
    def __init__(self, master, theme, get_db, schedule=None, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.t = theme
        self.schedule = schedule or self.after   # must be thread-safe (the app's after)
        self.get_db = get_db
        self.sid = None
        self.rng = "24h"
        self._loaded = None
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", pady=(0, 6))
        self.seg = ctk.CTkSegmentedButton(top, values=[label for _k, label in RANGES],
                                          command=self._pick)
        self.seg.set("24 hours")
        self.seg.pack(side="left")
        self.stats = ctk.CTkLabel(top, text="", anchor="e", text_color=theme["text_muted"])
        self.stats.pack(side="right")
        self.cpu = Chart(self, theme, "CPU", lambda v: "%d%%" % round(v))
        self.ram = Chart(self, theme, "RAM", _fmt_mb)
        self.players = Chart(self, theme, "Players", lambda v: "%d" % round(v))
        for c in (self.cpu, self.ram, self.players):
            c.pack(fill="both", expand=True, pady=4)

    def _pick(self, label):
        self.rng = next(k for k, l in RANGES if l == label)
        self.load()

    def set_server(self, sid, visible=False):
        self.sid = sid
        self._loaded = None
        if visible:
            self.load()

    def on_shown(self):
        if self.sid and self._loaded != (self.sid, self.rng):
            self.load()

    def load(self):
        sid, rng = self.sid, self.rng
        if not sid:
            return

        def work():
            from core.history import series
            try:
                data = series(self.get_db(), sid, rng)
            except Exception as e:
                data = {"error": str(e)}
            self.schedule(0, lambda: self._show(sid, rng, data))
        threading.Thread(target=work, daemon=True).start()

    def _show(self, sid, rng, d):
        if (sid, rng) != (self.sid, self.rng):
            return
        self._loaded = (sid, rng)
        if d.get("error"):
            self.stats.configure(text="History unavailable: " + d["error"])
            return
        pts = d["points"]
        args = (d["since"], d["until"], d["bucket"])
        self.cpu.set_data([(p["ts"], p["cpu"]) for p in pts], *args,
                          note="No data yet - Tofu records usage once a minute while the server runs.")
        self.ram.set_data([(p["ts"], p["ram"]) for p in pts], *args)
        self.players.set_data([(p["ts"], p["players"]) for p in pts], *args,
                              note="This game doesn't report players (or the server wasn't running).")
        bits = []
        if d.get("avg_cpu") is not None:
            bits.append("avg CPU %s%%" % d["avg_cpu"])
        if d.get("peak_ram"):
            bits.append("peak RAM " + _fmt_mb(d["peak_ram"]))
        if d.get("ram_trend"):
            bits.append(d["ram_trend"])
        if d.get("peak_players") is not None:
            bits.append("peak %d players" % d["peak_players"])
        self.stats.configure(text="   ·   ".join(bits))
