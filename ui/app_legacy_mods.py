"""Older Vintage Story / Workshop mod windows (kept for their menu entries). Part of the desktop app (BaoBunApp in app.py)."""
# flake8: noqa
import sys
import json
import os
import time
import threading
from pathlib import Path

import customtkinter as ctk
import tkinter.messagebox as messagebox
from tkinter import ttk
import tkinter.scrolledtext as tkst
from tkinter import filedialog

from ui.app_common import (BASE_DIR, BUNDLE_DIR, APP_DIR, CONFIG_PATH, ASSETS_DIR, PIL_AVAILABLE, Image, ACCENTS,
                           build_theme, THEME, load_image)
from server_manager import ServerManager, ARK_MAPS, ARK_MAP_MODS, ARK_REFORGE_MODS
import rcon
from core.engine import build_engine
from core.hosting import (dashboard_summary, format_core_list, parse_core_list,
                          suggest_core_block, firewall_rule_exists, PRIORITIES)
from core.game_catalog import GAME_PRESETS, load_extra_presets


class LegacyModsMixin:
    def vs_mods_dialog(self, on_installed=None):
        """Browse and install Vintage Story mods; check installed mods for updates."""
        if not self.selected_server_id:
            messagebox.showwarning("No server selected", "Select a server first.")
            return
        srv = next((s for s in self.manager.load_servers()
                    if s["id"] == self.selected_server_id), None)
        if not srv:
            return

        dlg = ctk.CTkToplevel(self)
        dlg.title("Vintage Story Mods")
        dlg.geometry("720x680")
        dlg.grab_set()
        dlg.transient(self)
        self.center_window(dlg, 720, 680)

        search_row = ctk.CTkFrame(dlg, fg_color="transparent")
        search_row.pack(fill="x", padx=20, pady=(20, 0))
        search_var = ctk.StringVar()
        ctk.CTkEntry(
            search_row, textvariable=search_var,
            placeholder_text="Search Vintage Story mods...",
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        status_lbl = ctk.CTkLabel(
            dlg, text="Search or check installed mods.", anchor="w",
            text_color=THEME["text_muted"],
        )
        status_lbl.pack(fill="x", padx=20, pady=(8, 0))

        scroll = ctk.CTkScrollableFrame(dlg, fg_color=THEME["bg_card"], corner_radius=10)
        scroll.pack(fill="both", expand=True, padx=20, pady=10)

        def show_status(msg):
            status_lbl.configure(text=msg)

        def clear_scroll():
            for w in scroll.winfo_children():
                w.destroy()

        def install_mod(modid, name):
            show_status("Installing " + str(name) + "...")
            def run():
                try:
                    from core.mods import ModManager
                    r = ModManager(self.manager).install(self.selected_server_id, str(modid), user="gui")
                    self.after(0, lambda: show_status("Installed " + r["installed"]))
                    extra = ""
                    if r.get("replaced"):
                        extra += " Moved old version(s) to Mods-old-versions."
                    if r.get("note"):
                        extra += " " + r["note"]
                    if r.get("backup"):
                        extra += " " + r["backup"]
                    if r.get("restart_needed"):
                        extra += " Restart the server to load it."
                    self.after(0, lambda: self._toast(
                        "Mod Installed", r["installed"] + " installed." + extra))
                    if on_installed:
                        self.after(0, on_installed)
                    if r.get("note"):
                        self.after(0, lambda: messagebox.showwarning("Mod version", r["note"]))
                except Exception as e:
                    msg = str(e)
                    self.after(0, lambda: show_status("Install failed: " + msg))
                    self.after(0, lambda: messagebox.showerror("Install Failed", msg))
            threading.Thread(target=run, daemon=True).start()

        def render_results(mods):
            clear_scroll()
            if not mods:
                ctk.CTkLabel(scroll, text="No mods found.",
                             text_color=THEME["text_muted"]).pack(pady=20)
                show_status("No mods found.")
                return
            for m in mods:
                row = ctk.CTkFrame(scroll, fg_color="transparent")
                row.pack(fill="x", pady=4)
                info = ctk.CTkFrame(row, fg_color="transparent")
                info.pack(side="left", fill="x", expand=True)
                ctk.CTkLabel(
                    info, text=m.get("name", "?"), anchor="w",
                    font=("Bahnschrift SemiBold", 13, "bold"),
                    text_color=THEME["text_light"],
                ).pack(anchor="w")
                ctk.CTkLabel(
                    info, text="by " + str(m.get("author", "?")) + "  -  " +
                    str(m.get("downloads", 0)) + " downloads",
                    anchor="w", text_color=THEME["text_muted"],
                ).pack(anchor="w")
                if m.get("summary"):
                    ctk.CTkLabel(
                        info, text=m.get("summary", ""), anchor="w",
                        wraplength=430, justify="left", text_color=THEME["text_muted"],
                    ).pack(anchor="w")
                mid = m.get("modid")
                nm = m.get("name", "")
                ctk.CTkButton(
                    row, text="Install", width=70, fg_color=THEME["success"],
                    command=lambda mi=mid, n=nm: install_mod(mi, n),
                ).pack(side="right", padx=(8, 0))
            show_status(str(len(mods)) + " result(s). Click Install to download a mod.")

        def do_search():
            query = search_var.get().strip()
            show_status("Searching...")
            def run():
                try:
                    mods = self.manager.vs_search_mods(query, "downloads", "desc", 50)
                    self.after(0, lambda: render_results(mods))
                except Exception as e:
                    msg = str(e)
                    self.after(0, lambda: show_status("Search failed: " + msg))
            threading.Thread(target=run, daemon=True).start()

        def render_installed(mods):
            clear_scroll()
            if not mods:
                ctk.CTkLabel(
                    scroll, text="No installed mods found in the Mods folder.",
                    text_color=THEME["text_muted"],
                ).pack(pady=20)
                show_status("No installed mods found.")
                return
            updates = sum(1 for m in mods if m.get("update_available"))
            ctk.CTkLabel(
                scroll,
                text=str(len(mods)) + " installed, " + str(updates) + " update(s) available",
                font=("Bahnschrift SemiBold", 13, "bold"),
                text_color=THEME["accent_gold"],
            ).pack(anchor="w", pady=(4, 8))
            for m in mods:
                row = ctk.CTkFrame(scroll, fg_color="transparent")
                row.pack(fill="x", pady=4)
                info = ctk.CTkFrame(row, fg_color="transparent")
                info.pack(side="left", fill="x", expand=True)
                ctk.CTkLabel(
                    info, text=m.get("name") or m.get("filename", "?"), anchor="w",
                    font=("Bahnschrift SemiBold", 13, "bold"),
                    text_color=THEME["text_light"],
                ).pack(anchor="w")
                ver = "v" + str(m.get("version", "?"))
                if m.get("update_available"):
                    ver += "   (v" + str(m.get("latest", "?")) + " available)"
                color = THEME["warning"] if m.get("update_available") else THEME["text_muted"]
                ctk.CTkLabel(info, text=ver, anchor="w", text_color=color).pack(anchor="w")
                if m.get("update_available"):
                    mid = m.get("modid")
                    nm = m.get("name") or m.get("filename", "")
                    ctk.CTkButton(
                        row, text="Update", width=70, fg_color=THEME["warning"],
                        command=lambda mi=mid, n=nm: install_mod(mi, n),
                    ).pack(side="right", padx=(8, 0))
            show_status("Done. " + str(updates) + " update(s) available.")

        def do_check_updates():
            clear_scroll()
            show_status("Checking installed mods for updates...")
            def run():
                try:
                    mods = self.manager.vs_check_updates(self.selected_server_id)
                    self.after(0, lambda: render_installed(mods))
                except Exception as e:
                    msg = str(e)
                    self.after(0, lambda: show_status("Check failed: " + msg))
            threading.Thread(target=run, daemon=True).start()

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(4, 15))
        ctk.CTkButton(
            btn_row, text="Search", fg_color=THEME["accent_blue"],
            command=do_search,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            btn_row, text="Check Installed / Updates", fg_color=THEME["info"],
            command=do_check_updates,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            btn_row, text="Close", fg_color=THEME["border"], command=dlg.destroy,
        ).pack(side="right", padx=4)

        do_check_updates()

    def workshop_mods_dialog(self):

        if not self.selected_server_id:
            messagebox.showwarning("No server selected", "Select a server first.")
            return
        srv = next((s for s in self.manager.load_servers()
                    if s["id"] == self.selected_server_id), None)
        if not srv:
            return
        dlg = ctk.CTkToplevel(self)
        dlg.title("Workshop Mods")
        dlg.geometry("540x360")
        dlg.grab_set()
        dlg.transient(self)
        self.center_window(dlg, 540, 360)
        form = ctk.CTkFrame(dlg, fg_color=THEME["bg_card"], corner_radius=10)
        form.pack(fill="both", expand=True, padx=20, pady=20)
        ctk.CTkLabel(
            form, text="Download Steam Workshop Mods", font=("Bahnschrift SemiBold", 16, "bold"),
        ).pack(pady=(10, 4))
        ctk.CTkLabel(
            form,
            text="Items download into steamapps/workshop in the server folder. "
            "You may need to activate them in the game's mod config.",
            anchor="w", justify="left", wraplength=480, text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkLabel(
            form, text="Workshop item IDs (comma-separated)",
            anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", padx=10, pady=(4, 0))
        ids_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            form, textvariable=ids_var, placeholder_text="e.g. 123456789, 987654321",
        ).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            form, text="Workshop App ID (blank = use the game's app ID)",
            anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", padx=10, pady=(6, 0))
        appid_var = ctk.StringVar(value=str(srv.get("appId") or ""))
        ctk.CTkEntry(form, textvariable=appid_var).pack(fill="x", padx=10, pady=(0, 10))

        def download():
            raw = ids_var.get()
            ids = [x.strip() for x in raw.replace("\n", ",").replace(" ", ",").split(",") if x.strip()]
            if not ids:
                messagebox.showwarning("No IDs", "Enter at least one Workshop item ID.")
                return
            sid = self.selected_server_id
            appid = appid_var.get().strip() or None
            dlg.destroy()

            def run():
                try:
                    r = self.manager.download_workshop_mods(sid, ids, appid)
                    self.after(0, lambda: self._toast(
                        "Workshop Mods",
                        "Downloaded " + str(r["downloaded"]) + "/" + str(len(r["results"])) + " item(s)."))
                except Exception as e:
                    self.after(0, lambda: messagebox.showerror("Download failed", str(e)))
            threading.Thread(target=run, daemon=True).start()

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(0, 20))
        ctk.CTkButton(
            btns, text="Download", command=download, fg_color=THEME["success"],
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            btns, text="Cancel", command=dlg.destroy, fg_color=THEME["border"],
        ).pack(side="right", padx=6)
