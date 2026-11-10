"""Add Server wizard: pick a game -> name & folder -> review & install.

Three screens, sensible defaults everywhere (folder, free ports), and a live
SteamCMD progress bar. The old full form stays available via "Advanced form".
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk
from tkinter import filedialog

from core.hosting import parse_steamcmd_progress, pick_port


class AddServerWizard(ctk.CTkToplevel):
    def __init__(self, app, theme: dict, presets: dict, on_done: Callable[[str], None],
                 on_advanced: Callable[[], None]):
        super().__init__(app)
        self.app, self.theme, self.presets = app, theme, presets
        self.mgr = app.manager
        self.on_done, self.on_advanced = on_done, on_advanced
        self.title("Add Server")
        self.geometry("680x600")
        self.configure(fg_color=theme["bg_dark"])
        self.transient(app)
        self.grab_set()
        try:
            app.center_window(self, 680, 600, fit=False)
        except Exception:
            pass
        self.game: Optional[str] = None
        self.server_id: Optional[str] = None
        self._installing = False

        self.header = ctk.CTkLabel(self, text="", anchor="w", text_color=theme["text_light"],
                                   font=("Bahnschrift SemiBold", 20, "bold"))
        self.header.pack(fill="x", padx=24, pady=(20, 2))
        self.steps_lbl = ctk.CTkLabel(self, text="", anchor="w", text_color=theme["text_muted"])
        self.steps_lbl.pack(fill="x", padx=24, pady=(0, 10))
        self.body = ctk.CTkFrame(self, fg_color=theme["bg_card"], corner_radius=12)
        self.body.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.footer = ctk.CTkFrame(self, fg_color="transparent")
        self.footer.pack(fill="x", padx=20, pady=(0, 18))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.show_pick()

    # -- helpers ---------------------------------------------------------------
    def _reset(self, title: str, step: int):
        for w in list(self.body.winfo_children()) + list(self.footer.winfo_children()):
            w.destroy()
        self.header.configure(text=title)
        names = ["1  Choose a game", "2  Name & folder", "3  Review & install"]
        self.steps_lbl.configure(text="     ".join(("● " if i == step else "○ ") + n
                                                  for i, n in enumerate(names, 1)))

    def _btn(self, text, cmd, primary=False, side="right"):
        b = ctk.CTkButton(self.footer, text=text, command=cmd, height=36, corner_radius=10,
                          fg_color=self.theme["accent_blue"] if primary else "transparent",
                          border_width=0 if primary else 1, border_color=self.theme["border"],
                          text_color="#ffffff" if primary else self.theme["text_light"])
        b.pack(side=side, padx=(8, 0) if side == "right" else (0, 8))
        return b

    def _close(self):
        if self._installing:
            if not self.app.ask_yes_no("Install running",
                                       "The download keeps going in the background. Close this window?",
                                       parent=self):
                return
        self.destroy()

    # -- step 1 -------------------------------------------------------------------
    def show_pick(self):
        self._reset("What do you want to host?", 1)
        t = self.theme
        self.search_var = ctk.StringVar(value="")
        e = ctk.CTkEntry(self.body, textvariable=self.search_var, height=36,
                         placeholder_text="Search games (e.g. Valheim, ARK, Vintage)")
        e.pack(fill="x", padx=14, pady=(14, 6))
        from core.game_catalog import CATEGORIES
        self._cats = [("popular", "Popular")] + CATEGORIES + [("all", "All")]
        self.cat_var = ctk.StringVar(value="Popular")
        ctk.CTkSegmentedButton(self.body, values=[c[1] for c in self._cats], variable=self.cat_var,
                               command=lambda _v: self._render_games(), selected_color=t["accent_blue"],
                               selected_hover_color=t["accent_blue"]).pack(fill="x", padx=14, pady=(0, 8))
        self.list_frame = ctk.CTkScrollableFrame(self.body, fg_color=t["bg_card"])
        self.list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.search_var.trace_add("write", lambda *_: self._render_games())
        self._render_games()
        self.after(100, e.focus_set)
        self._btn("Cancel", self._close)
        self._btn("Advanced form…", lambda: (self.destroy(), self.on_advanced()), side="left")

    def _render_games(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        if not hasattr(self, "_support"):
            try:
                from core import game_support
                self._support = game_support.levels(self.presets)
            except Exception:
                self._support = {}
        q = self.search_var.get().strip().lower()
        names = sorted(self.presets, key=lambda n: (n == "Custom / Other", n.lower()))
        cat = next((c[0] for c in getattr(self, "_cats", []) if c[1] == self.cat_var.get()), "all") \
            if hasattr(self, "cat_var") else "all"
        if q:   # searching looks through every game
            hits = [n for n in names if q in n.lower() or q in (self.presets[n].get("description") or "").lower()]
        elif cat == "popular":
            hits = [n for n in names if self.presets[n].get("popular")] + ["Custom / Other"]
        elif cat == "all":
            hits = names
        else:
            hits = [n for n in names if (self.presets[n].get("category") or "other") == cat]
        hits = hits[:200]
        if not hits:
            ctk.CTkLabel(self.list_frame, text="No match. Try another name, or pick \"Custom / Other\".",
                         text_color=self.theme["text_muted"]).pack(pady=20)
        for n in hits:
            p = self.presets[n]
            kind = "Steam" if p.get("appId") else (
                "One-click download" if self._web_download(p) else
                "Files from the game's own site" if p.get("manual") else
                "Manual download" if n != "Custom / Other" else "Any program")
            if p.get("appId") and not p.get("anonymous_login", True):
                kind += " · needs Steam account"
            if n != "Custom / Other" and self._support.get(n) == "basic":
                kind += " · basic support"
            row = ctk.CTkButton(self.list_frame, text="%s\n%s" % (n, kind), anchor="w", height=48,
                                corner_radius=8, fg_color=self.theme["bg_input"],
                                hover_color=self.theme["border"], text_color=self.theme["text_light"],
                                command=lambda name=n: self.show_details(name))
            row.pack(fill="x", padx=6, pady=3)

    # -- step 2 ---------------------------------------------------------------------
    def show_details(self, game: str):
        self.game = game
        p = self.presets.get(game, {})
        self._reset(game, 2)
        t = self.theme
        port = None
        try:
            from core import port_plan
            tmp = {"appId": p.get("appId"), "type": p.get("type"), "port": p.get("port"), "launchArgs": "",
                   "executable": p.get("executable") or ""}
            if p.get("port") or port_plan.spec(tmp):
                port = port_plan.plan(self.mgr, tmp).get("game")
        except Exception:
            port = None
        if port is None and p.get("port"):
            taken = set()
            for entry in self.mgr.get_all_ports():
                taken.update(x["port"] for x in entry["ports"])
            taken.update(int(s["port"]) for s in self.mgr.load_servers() if s.get("port"))
            port = pick_port(p.get("port"), taken)

        base = self.mgr.get_setting("default_install_dir", "") or ""
        if not base:
            base = str(Path(self.app.base_dir_for_wizard()) / "GameServers")
        default_name = game if game != "Custom / Other" else "My Server"
        existing = {s.get("name") for s in self.mgr.load_servers()}
        name, i = default_name, 2
        while name in existing:
            name, i = "%s %d" % (default_name, i), i + 1

        self.name_var = ctk.StringVar(value=name)
        self.base_var = ctk.StringVar(value=base)
        self.port_var = ctk.StringVar(value=str(port or ""))
        self.auto_var = ctk.BooleanVar(value=False)
        self.restart_var = ctk.BooleanVar(value=True)

        def label(text):
            ctk.CTkLabel(self.body, text=text, anchor="w", text_color=t["text_light"]).pack(
                fill="x", padx=16, pady=(12, 0))
        if getattr(self, "_support", {}).get(game) == "basic":
            ctk.CTkLabel(self.body, text="Basic support: BaoBun installs and runs %s, but nothing in BaoBun was made "
                                         "for it yet - its settings are found by searching the server folder, and "
                                         "some may need setting by hand." % game, anchor="w", justify="left",
                         wraplength=600, text_color=t["warning"]).pack(fill="x", padx=16, pady=(10, 0))
        label("Server name (shown in BaoBun and on Discord)")
        ctk.CTkEntry(self.body, textvariable=self.name_var, height=34).pack(fill="x", padx=16)
        label("Install into this folder (BaoBun makes a subfolder named after the server)")
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", padx=16)
        ctk.CTkEntry(row, textvariable=self.base_var, height=34).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Browse…", width=90, fg_color=t["border"],
                      command=self._browse).pack(side="left", padx=(8, 0))
        self.final_lbl = ctk.CTkLabel(self.body, text="", anchor="w", text_color=t["text_muted"],
                                      font=("Segoe UI", 10))
        self.final_lbl.pack(fill="x", padx=16)
        for v in (self.name_var, self.base_var):
            v.trace_add("write", lambda *_: self._update_final())
        self._update_final()
        if p.get("port") or game == "Custom / Other":
            label("Game port (a free one was picked for you)")
            ctk.CTkEntry(self.body, textvariable=self.port_var, width=120, height=34).pack(anchor="w", padx=16)
        ctk.CTkCheckBox(self.body, text="Start this server automatically when the PC boots",
                        variable=self.auto_var, fg_color=t["accent_blue"]).pack(anchor="w", padx=16, pady=(14, 4))
        ctk.CTkCheckBox(self.body, text="Restart it automatically if it crashes",
                        variable=self.restart_var, fg_color=t["accent_blue"]).pack(anchor="w", padx=16)
        if p.get("appId") and not p.get("anonymous_login", True):
            ctk.CTkLabel(self.body, text="⚠ This game's server needs a Steam account that owns it. "
                         "Add your Steam login in Settings before installing.", wraplength=600,
                         justify="left", anchor="w", text_color=t["warning"]).pack(fill="x", padx=16, pady=(12, 0))
        self.err_lbl = ctk.CTkLabel(self.body, text="", anchor="w", text_color=t["danger"])
        self.err_lbl.pack(fill="x", padx=16, pady=(8, 0))
        self._btn("Next ›", self.show_review, primary=True)
        self._btn("‹ Back", self.show_pick, side="left")

    def _browse(self):
        d = filedialog.askdirectory(title="Choose where game servers go", parent=self)
        if d:
            self.base_var.set(d)

    def _target_dir(self) -> str:
        safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in self.name_var.get()).strip() or "server"
        return os.path.join(self.base_var.get().strip(), safe)

    def _update_final(self):
        try:
            self.final_lbl.configure(text="Folder: " + self._target_dir())
        except Exception:
            pass

    # -- step 3 -------------------------------------------------------------------
    def show_review(self):
        name = self.name_var.get().strip()
        if not name:
            self.err_lbl.configure(text="Give the server a name.")
            return
        if any(s.get("name") == name for s in self.mgr.load_servers()):
            self.err_lbl.configure(text="A server with that name already exists.")
            return
        if not self.base_var.get().strip():
            self.err_lbl.configure(text="Choose a folder.")
            return
        port = None
        if self.port_var.get().strip():
            try:
                port = int(self.port_var.get())
                assert 1 <= port <= 65535
            except Exception:
                self.err_lbl.configure(text="The port must be a number from 1 to 65535.")
                return
        self._pending = {"name": name, "dir": self._target_dir(), "port": port,
                         "auto": bool(self.auto_var.get()), "restart": bool(self.restart_var.get())}
        p = self.presets.get(self.game, {})
        self._reset("Ready to create " + name, 3)
        t = self.theme
        lines = [("Game", self.game), ("Folder", self._pending["dir"]),
                 ("Port", str(port) if port else "game default"),
                 ("Starts at boot", "yes" if self._pending["auto"] else "no (start it when you need it)"),
                 ("Download", "SteamCMD, App %s" % p["appId"] if p.get("appId")
                  else self._download_text(p))]
        for k, v in lines:
            r = ctk.CTkFrame(self.body, fg_color="transparent")
            r.pack(fill="x", padx=16, pady=(10 if k == "Game" else 4, 0))
            ctk.CTkLabel(r, text=k, width=120, anchor="w", text_color=t["text_muted"]).pack(side="left")
            ctk.CTkLabel(r, text=v, anchor="w", text_color=t["text_light"], wraplength=440,
                         justify="left").pack(side="left", fill="x", expand=True)
        if port and p.get("appId"):
            ctk.CTkLabel(self.body, text="Tip: some games also need this port set in their own config "
                         "file - the server's Settings tab has it.", wraplength=600, justify="left",
                         anchor="w", text_color=t["text_muted"]).pack(fill="x", padx=16, pady=(12, 0))
        self.progress = ctk.CTkProgressBar(self.body, progress_color=t["accent_blue"])
        self.progress.set(0)
        self.prog_lbl = ctk.CTkLabel(self.body, text="", anchor="w", text_color=t["text_muted"])
        self._btn_back = self._btn("‹ Back", lambda: self.show_details(self.game), side="left")
        if p.get("appId") or self._web_download(p):
            self._btn_go = self._btn("Create & install", self._create_and_install, primary=True)
        else:
            self._btn_go = self._btn("Create server", self._create_only, primary=True)

    @staticmethod
    def _web_download(p: dict):
        """"vintage-story" / "minecraft" when BaoBun can download this game itself."""
        try:
            from core import game_installers as gi
            k = gi.kind({"type": p.get("type"), "executable": p.get("executable") or ""})
        except Exception:
            return None
        return k if k in ("vintage-story", "minecraft", "hytale") else None

    def _download_text(self, p: dict) -> str:
        k = self._web_download(p)
        if k == "vintage-story":
            return "official server files from vintagestory.at"
        if k == "minecraft":
            return "Minecraft server (Vanilla, Paper, Fabric, Forge or NeoForge - pick next) and Java if needed"
        if k == "hytale":
            return ("Java 25 and the server via Hytale's own downloader - it shows a link to sign in with your "
                    "Hytale account the first time")
        if (p.get("manual") or {}).get("how"):
            return p["manual"]["how"]
        return "you copy the server files into the folder"

    def _entry(self) -> dict:
        p = self.presets.get(self.game, {})
        d = self._pending["dir"]
        exe = p.get("executable")
        return {
            "name": self._pending["name"],
            "type": p.get("type", "custom"),
            "directory": d,
            "port": self._pending["port"],
            "executable": os.path.join(d, exe) if exe else None,
            "launchArgs": p.get("args") or "",
            "appId": p.get("appId"),
            "requiresAccount": bool(p.get("appId")) and not p.get("anonymous_login", True),
            "updateBeforeStart": False,
            "autoRestart": self._pending["restart"],
            "manualStart": not self._pending["auto"],
            "folder": "",
            "id": "srv_" + str(int(time.time() * 1000)),
        }

    def _save_entry(self) -> Optional[dict]:
        entry = self._entry()
        try:
            os.makedirs(entry["directory"], exist_ok=True)
        except OSError as e:
            self.app.show_error("Folder", "Could not create the folder:\n" + str(e), parent=self)
            return None
        servers = self.mgr.load_servers()
        servers.append(entry)
        self.mgr.save_servers(servers)
        self.server_id = entry["id"]
        try:
            from core import port_plan
            port_plan.setup_new(self.mgr, entry["id"], pinned_game=int(entry["port"]) if entry.get("port") else None)
            entry = self.mgr.get_server(entry["id"]) or entry
        except Exception:
            pass
        self.app.refresh_servers()
        return entry

    def _create_only(self):
        entry = self._save_entry()
        if not entry:
            return
        self._finish(entry, installed=False)

    def _create_and_install(self):
        entry = self._save_entry()
        if not entry:
            return
        if not entry.get("appId"):
            sid = entry["id"]
            app, theme = self.app, self.theme
            self.destroy()
            self.on_done(sid)
            from ui.install_dialog import open_install_dialog
            open_install_dialog(app, theme, sid)
            return
        if entry["requiresAccount"] and not (self.mgr.get_setting("steam_username") and
                                             self.mgr.get_setting("steam_password")):
            self.app.show_error("Steam login needed",
                                "The server was added, but this game needs a Steam account to download. "
                                "Add your Steam login in Settings, then click Install on the server.",
                                parent=self)
            self._finish(entry, installed=False)
            return
        self._installing = True
        self._btn_go.configure(state="disabled", text="Installing…")
        self._btn_back.configure(state="disabled")
        self.progress.pack(fill="x", padx=16, pady=(18, 4))
        self.prog_lbl.pack(fill="x", padx=16)
        self.prog_lbl.configure(text="Starting SteamCMD… (the first run downloads SteamCMD itself)")
        result = {}

        def work():
            try:
                result["ok"] = self.mgr.install_steamcmd_game(entry["id"], str(entry["appId"]))
            except Exception as e:
                result["error"] = str(e)
        th = threading.Thread(target=work, daemon=True)
        th.start()
        log = os.path.join(entry["directory"], "steamcmd_install.log")

        def poll():
            if not self.winfo_exists():
                return
            try:
                with open(log, "rb") as f:
                    f.seek(max(0, os.path.getsize(log) - 8192))
                    tail = f.read().decode("utf-8", errors="replace")
                pct, phase = parse_steamcmd_progress(tail)
            except OSError:
                pct, phase = None, None
            if pct is not None:
                self.progress.set(pct / 100.0)
                self.prog_lbl.configure(text="%s… %.1f%%" % ((phase or "working").capitalize(), pct))
            if th.is_alive():
                self.after(1000, poll)
                return
            self._installing = False
            if "error" in result:
                self.progress.set(0)
                err = str(result["error"])
                head, _, details = err.partition("\n")
                self.prog_lbl.configure(text="Install failed: " + head, text_color=self.theme["danger"],
                                        wraplength=640, justify="left")
                if details.strip():
                    box = getattr(self, "_err_box", None)
                    if box is None or not box.winfo_exists():
                        box = self._err_box = ctk.CTkTextbox(self.prog_lbl.master, height=220, wrap="word",
                                                             fg_color=self.theme["bg_dark"],
                                                             font=("Consolas", 11))
                        box.pack(fill="both", expand=True, padx=16, pady=(8, 8))
                    box.configure(state="normal")
                    box.delete("1.0", "end")
                    box.insert("1.0", details.strip()[-3000:])
                    box.configure(state="disabled")
                self._btn_go.configure(state="normal", text="Try again", command=self._retry_install)
                return
            self.progress.set(1)
            self._finish(entry, installed=True)
        self.after(1000, poll)

    def _retry_install(self):
        # The server entry already exists; just run the install again.
        box = getattr(self, "_err_box", None)
        if box is not None:
            try:
                box.destroy()
            except Exception:
                pass
            self._err_box = None
        try:
            self.prog_lbl.configure(text_color=self.theme["text_muted"])
        except Exception:
            pass
        servers = [s for s in self.mgr.load_servers() if s.get("id") != self.server_id]
        self.mgr.save_servers(servers)
        self._create_and_install()

    def _finish(self, entry: dict, installed: bool):
        self._reset(entry["name"] + " is ready" if installed or not entry.get("appId")
                    else entry["name"] + " was added", 3)
        t = self.theme
        if installed:
            msg = "The game is installed. Start it now, or later from its card."
        elif entry.get("appId"):
            msg = "Click Install / Update on the server when you're ready to download it."
        else:
            msg = ("Copy the game's dedicated-server files into:\n\n%s\n\nthen start it from its card."
                   % entry["directory"])
        ctk.CTkLabel(self.body, text=msg, wraplength=600, justify="left", anchor="w",
                     text_color=t["text_light"]).pack(fill="x", padx=16, pady=(20, 8))
        if not entry.get("appId"):
            man = (self.presets.get(self.game, {}) or {}).get("manual") or {}
            if man.get("how"):
                ctk.CTkLabel(self.body, text=man["how"], wraplength=600, justify="left", anchor="w",
                             text_color=t["text_muted"]).pack(fill="x", padx=16, pady=(0, 8))
            row = ctk.CTkFrame(self.body, fg_color="transparent")
            row.pack(anchor="w", padx=16)
            if man.get("url"):
                import webbrowser
                ctk.CTkButton(row, text="Open download page", fg_color=t["accent_blue"], width=170,
                              command=lambda u=man["url"]: webbrowser.open(u)).pack(side="left", padx=(0, 8))
            ctk.CTkButton(row, text="Open folder", fg_color=t["border"], width=140,
                          command=lambda: self.app.open_folder(entry["directory"])).pack(side="left")
        sid = entry["id"]
        self._btn("Close", lambda: (self.destroy(), self.on_done(sid)))
        if installed or not entry.get("appId"):
            self._btn("Start now", lambda: (self.destroy(), self.on_done(sid), self.app.start_server(sid)),
                      primary=True)
