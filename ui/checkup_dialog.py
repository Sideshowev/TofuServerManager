"""Desktop "Checkup": everything that needs attention across all servers, with a
button to fix each one where BaoBun can (see core/checkup.py)."""

from __future__ import annotations

import threading

import customtkinter as ctk
from ui.app_common import later

DOT = {"problem": "danger", "warning": "warning", "tip": "text_muted"}
BTN = {"install": "Install", "backup": "Back up now", "firewall": "Open firewall",
       "schedule": "Set up schedule", "game_settings": "Game settings", "rcon": "Turn on RCON",
       "ports": "Fix ports", "nightly": "Set up", "ark": "Open ARK World",
       "hytale_signin": "Sign in"}


def open_checkup_dialog(app, theme):
    t = theme
    dlg = ctk.CTkToplevel(app)
    dlg.title("Checkup")
    dlg.transient(app)
    app.center_window(dlg, 680, 620, fit=True)
    head = ctk.CTkLabel(dlg, text="Checking your servers...", anchor="w", text_color=t["text_light"],
                        font=("Bahnschrift SemiBold", 15, "bold"))
    head.pack(fill="x", padx=18, pady=(16, 4))
    body = ctk.CTkScrollableFrame(dlg, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=6)
    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=16, pady=(4, 14))

    def clear():
        for w in body.winfo_children():
            w.destroy()

    def act(item):
        sid, a = item.get("server_id"), item.get("action")
        if a == "ark":
            dlg.destroy()
            from ui.ark_window import open_ark_window
            open_ark_window(app, t, sid)
            return
        if a == "hytale_signin":
            from ui.hytale_signin import open_hytale_signin
            open_hytale_signin(app, t, sid, start_first=True)
            return
        if a == "nightly":
            dlg.destroy()
            app.show_nightly_dialog()
            return
        if a == "ports":
            from ui.ports_dialog import open_ports_dialog
            dlg.destroy()
            open_ports_dialog(app, t)
            return
        if a == "rcon":
            from ui.ports_dialog import turn_on_rcon
            turn_on_rcon(app, t, sid, on_done=lambda: load(True))
            return
        if a == "backup":
            app._run_bg("Backing up %s..." % item.get("server"), lambda: app.manager.backup_server_world(sid),
                        on_done=lambda _r: (app._show_status("Backed up %s" % item.get("server")), load(True)))
            return
        dlg.destroy()
        app.show_manage(sid)
        if a == "install":
            app.install_game()
        elif a == "firewall":
            app.open_firewall_for_selected()
        elif a == "schedule":
            app._show_status("Use Backup Schedule in the tools on the right to back it up automatically.")
        elif a == "game_settings":
            try:
                app.tabview.set("Settings")
                app.game_panel._switch("All settings")
            except Exception:
                pass

    def show(res, err):
        if not dlg.winfo_exists():
            return
        clear()
        if err:
            head.configure(text="Checkup failed: " + err)
            return
        items = res.get("items") or []
        c = res.get("counts") or {}
        if not items:
            head.configure(text="All good - nothing needs attention.")
            ctk.CTkLabel(body, text="✅  Every server has its files, backups and settings in order.",
                         text_color=t["text_muted"]).pack(pady=24)
            return
        parts = ["%d %s%s" % (c[k], k, "s" if c[k] != 1 else "") for k in ("problem", "warning", "tip") if c.get(k)]
        head.configure(text="Needs attention: " + ", ".join(parts))
        for it in items:
            row = ctk.CTkFrame(body, fg_color=t["bg_dark"], corner_radius=8)
            row.pack(fill="x", padx=6, pady=3)
            ctk.CTkLabel(row, text="●", width=18, text_color=t[DOT.get(it["level"], "text_muted")]).pack(
                side="left", padx=(10, 2), anchor="n", pady=8)
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, pady=6)
            title = ((it.get("server") + " - ") if it.get("server") else "") + it["title"]
            ctk.CTkLabel(left, text=title, anchor="w", justify="left", wraplength=470, text_color=t["text_light"],
                         font=("Segoe UI", 13, "bold")).pack(fill="x")
            if it.get("fix"):
                ctk.CTkLabel(left, text=it["fix"], anchor="w", justify="left", wraplength=470,
                             text_color=t["text_muted"], font=("Segoe UI", 11)).pack(fill="x")
            if it.get("action") in BTN and (it.get("server_id") or it.get("action") in ("ports", "nightly")):
                ctk.CTkButton(row, text=BTN[it["action"]], width=110, fg_color=t["border"],
                              command=lambda i=it: act(i)).pack(side="right", padx=10)

    def load(refresh=False):
        head.configure(text="Checking your servers...")

        def work():
            from core import checkup
            try:
                res, err = checkup.run(app.manager, force=refresh), None
            except Exception as e:
                res, err = None, str(e)
            later(app, lambda: show(res, err))
        threading.Thread(target=work, daemon=True).start()

    ctk.CTkButton(btns, text="Close", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=dlg.destroy).pack(side="right")
    ctk.CTkButton(btns, text="Save diagnostics", fg_color="transparent", border_width=1, border_color=t["border"],
                  text_color=t["text_muted"], command=app.save_diagnostics).pack(side="left")
    ctk.CTkButton(btns, text="Check again", fg_color=t["accent_blue"], command=lambda: load(True)).pack(
        side="right", padx=6)
    load(True)
