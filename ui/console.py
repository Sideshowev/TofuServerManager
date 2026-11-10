"""Live console: follows a server's log as it is written, with a filter,
colour for errors/warnings, and a command box with history (Up/Down).
"""

from __future__ import annotations

import os
import re
import threading
import time
import tkinter as tk
from typing import Callable, Optional

import customtkinter as ctk

MAX_LINES = 5000
ERROR_RE = re.compile(r"\b(error|exception|fatal|crash(ed)?|failed)\b", re.IGNORECASE)
WARN_RE = re.compile(r"\b(warn(ing)?)\b", re.IGNORECASE)


class LogFollower:
    """Background reader: calls ``emit(lines, reset)`` with new log lines."""

    def __init__(self, get_path: Callable[[], Optional[str]], emit: Callable, interval: float = 0.7):
        self.get_path, self.emit, self.interval = get_path, emit, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="log-follow")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        path, pos, buf = None, 0, b""
        while not self._stop.is_set():
            try:
                cur = self.get_path()
            except Exception:
                cur = None
            if cur != path:
                path, buf = cur, b""
                lines = []
                if path and os.path.isfile(path):
                    size = os.path.getsize(path)
                    start = max(0, size - 64 * 1024)  # show the recent history
                    with open(path, "rb") as f:
                        f.seek(start)
                        data = f.read()
                    pos = start + len(data)
                    text = data.decode("utf-8", errors="replace").splitlines()
                    lines = text[1:] if start > 0 and text else text
                self.emit(lines[-400:], True)
            elif path and os.path.isfile(path):
                try:
                    size = os.path.getsize(path)
                    if size < pos:        # log was rotated / truncated
                        pos, buf = 0, b""
                    if size > pos:
                        with open(path, "rb") as f:
                            f.seek(pos)
                            data = f.read(min(size - pos, 1024 * 1024))
                        pos += len(data)
                        buf += data
                        *complete, buf = buf.split(b"\n")
                        if complete:
                            self.emit([c.decode("utf-8", errors="replace").rstrip("\r") for c in complete], False)
                except OSError:
                    pass
            self._stop.wait(self.interval)


class LiveConsole(ctk.CTkFrame):
    def __init__(self, master, theme: dict, get_log_path: Callable[[str], Optional[str]],
                 send_command: Callable[[str, str], None], schedule: Callable,
                 get_quick: Optional[Callable[[str], list]] = None,
                 get_players: Optional[Callable[[str], list]] = None):
        super().__init__(master, fg_color="transparent")
        self.theme = theme
        self.get_log_path = get_log_path
        self.send_command = send_command
        self.get_quick = get_quick
        self.get_players = get_players
        self._quick = []
        self.schedule = schedule          # app.after (thread-safe in BaoBunApp)
        self.server_id: Optional[str] = None
        self._follower: Optional[LogFollower] = None
        self._lines = []                  # every line received (for re-filtering)
        self._history, self._hpos = [], 0

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 6))
        self.filter_var = ctk.StringVar(value="")
        ent = ctk.CTkEntry(bar, textvariable=self.filter_var, width=260,
                           placeholder_text="Filter (text or regex)")
        ent.pack(side="left")
        self.filter_var.trace_add("write", lambda *_: self._rerender())
        self.follow_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(bar, text="Follow", variable=self.follow_var,
                        fg_color=theme["accent_blue"]).pack(side="left", padx=(12, 0))
        self.errors_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(bar, text="Errors & warnings only", variable=self.errors_var,
                        fg_color=theme["accent_blue"], command=self._rerender).pack(side="left", padx=(12, 0))
        ctk.CTkButton(bar, text="Clear", width=70, fg_color=theme["border"],
                      command=self._clear).pack(side="right")
        self.path_lbl = ctk.CTkLabel(bar, text="", text_color=theme["text_muted"], font=("Segoe UI", 10))
        self.path_lbl.pack(side="right", padx=8)

        self.text = tk.Text(self, wrap="none", bg=theme["code_bg"], fg=theme["code_fg"],
                            insertbackground=theme["code_fg"], relief="flat", borderwidth=0,
                            font=("Cascadia Mono", 10), state="disabled")
        sy = ctk.CTkScrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=sy.set)
        sy.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("error", foreground="#f87171")
        self.text.tag_configure("warn", foreground="#fbbf24")
        self.text.tag_configure("cmd", foreground=theme["accent_gold"])

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        self.quick_var = ctk.StringVar(value="Quick commands")
        self.quick_menu = ctk.CTkOptionMenu(row, variable=self.quick_var, values=["Quick commands"], width=180,
                                            command=self._pick_quick)
        self.cmd_var = ctk.StringVar(value="")
        self.cmd_entry = ctk.CTkEntry(row, textvariable=self.cmd_var,
                                      placeholder_text="Type a server command and press Enter "
                                      "(e.g. /announce Hello)")
        self.cmd_entry.pack(side="left", fill="x", expand=True)
        self.cmd_entry.bind("<Return>", lambda e: self._send())
        self.cmd_entry.bind("<Up>", lambda e: self._hist(-1))
        self.cmd_entry.bind("<Down>", lambda e: self._hist(1))
        self.send_btn = ctk.CTkButton(row, text="Send", width=80, fg_color=theme["accent_blue"],
                                      command=self._send)
        self.send_btn.pack(side="left", padx=(8, 0))

    # -- server binding ---------------------------------------------------------
    def set_server(self, server_id: Optional[str]):
        if server_id == self.server_id:
            return
        if self._follower:
            self._follower.stop()
            self._follower = None
        self.server_id = server_id
        self._clear()
        self._load_quick(server_id)
        if server_id:
            sid = server_id
            self._follower = LogFollower(lambda: self.get_log_path(sid),
                                         lambda lines, reset: self.schedule(0, self._receive, sid, lines, reset))

    def _load_quick(self, server_id):
        self._quick = []
        if server_id and self.get_quick:
            try:
                self._quick = self.get_quick(server_id) or []
            except Exception:
                self._quick = []
        try:
            if self._quick:
                self.quick_menu.configure(values=[q["label"] for q in self._quick])
                self.quick_var.set("Quick commands")
                if not self.quick_menu.winfo_manager():
                    self.quick_menu.pack(side="left", padx=(0, 8), before=self.cmd_entry)
            elif self.quick_menu.winfo_manager():
                self.quick_menu.pack_forget()
        except Exception:
            pass

    def _pick_quick(self, label):
        self.quick_var.set("Quick commands")
        q = next((x for x in self._quick if x["label"] == label), None)
        if not q:
            return
        cmd = q["command"]
        if q.get("player"):
            names = []
            try:
                names = self.get_players(self.server_id) if self.get_players else []
            except Exception:
                names = []
            who = ctk.CTkInputDialog(title=label, text="Which player?" + (
                "\nOnline now: " + ", ".join(names) if names else "")).get_input()
            if not who:
                return
            cmd = cmd.replace("{player}", who.replace('"', "").strip())
        if q.get("text"):
            t = ctk.CTkInputDialog(title=label, text=label + ":").get_input()
            if t is None:
                return
            cmd = cmd.replace("{text}", t.replace('"', "'").strip())
        self.cmd_var.set(cmd)
        self.cmd_entry.focus_set()

    def set_console_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        try:
            self.cmd_entry.configure(state=state)
            self.send_btn.configure(state=state)
        except Exception:
            pass

    # -- rendering ----------------------------------------------------------------
    def _matches(self, line: str) -> bool:
        if self.errors_var.get() and not (ERROR_RE.search(line) or WARN_RE.search(line)):
            return False
        f = self.filter_var.get().strip()
        if not f:
            return True
        try:
            return re.search(f, line, re.IGNORECASE) is not None
        except re.error:
            return f.lower() in line.lower()

    @staticmethod
    def _tag(line: str):
        if line.startswith("> "):
            return "cmd"
        if ERROR_RE.search(line):
            return "error"
        if WARN_RE.search(line):
            return "warn"
        return None

    def _append(self, lines):
        shown = [l for l in lines if self._matches(l)]
        if not shown:
            return
        self.text.configure(state="normal")
        for l in shown:
            self.text.insert("end", l + "\n", self._tag(l) or ())
        excess = int(self.text.index("end-1c").split(".")[0]) - MAX_LINES
        if excess > 0:
            self.text.delete("1.0", "%d.0" % (excess + 1))
        self.text.configure(state="disabled")
        if self.follow_var.get():
            self.text.see("end")

    def _receive(self, sid, lines, reset):
        if sid != self.server_id:
            return
        if reset:
            self._clear()
        self._lines.extend(lines)
        del self._lines[:-MAX_LINES]
        self._append(lines)
        try:
            path = self.get_log_path(sid)
            self.path_lbl.configure(text=os.path.basename(path) if path else "no log yet")
        except Exception:
            pass

    def _rerender(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._append(self._lines[-MAX_LINES:])

    def _clear(self):
        self._lines = []
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    # -- commands -------------------------------------------------------------------
    def _send(self):
        cmd = self.cmd_var.get().strip()
        if not cmd or not self.server_id:
            return
        self._history.append(cmd)
        self._hpos = len(self._history)
        self.cmd_var.set("")
        self._append(["> " + cmd])
        self.send_command(self.server_id, cmd)

    def _hist(self, step):
        if not self._history:
            return "break"
        self._hpos = max(0, min(len(self._history), self._hpos + step))
        self.cmd_var.set(self._history[self._hpos] if self._hpos < len(self._history) else "")
        self.cmd_entry.icursor("end")
        return "break"
