"""Desktop "new BaoBun version" window (core/updates.py): what's new, then
Download and install - the installer replaces BaoBun and starts it again."""

from __future__ import annotations

import threading
import webbrowser

import customtkinter as ctk

from core import updates


def open_update_dialog(app, theme, info=None):
    t = theme
    mgr = app.manager
    info = info or updates.cached(mgr)
    dlg = ctk.CTkToplevel(app)
    dlg.title("Update BaoBun")
    dlg.transient(app)
    app.center_window(dlg, 560, 480, fit=True)
    ctk.CTkLabel(dlg, text="BaoBun %s" % (info.get("latest") or "?"), anchor="w",
                 font=("Bahnschrift SemiBold", 19, "bold"), text_color=t["text_light"]).pack(fill="x", padx=18,
                                                                                            pady=(16, 2))
    ctk.CTkLabel(dlg, text="You have %s. Your servers, settings and backups stay as they are." % info["current"],
                 anchor="w", text_color=t["text_muted"]).pack(fill="x", padx=18)
    notes = ctk.CTkTextbox(dlg, height=200, wrap="word", fg_color=t["bg_card"])
    notes.pack(fill="both", expand=True, padx=16, pady=10)
    notes.insert("1.0", info.get("notes") or "No notes for this version.")
    notes.configure(state="disabled")
    status = ctk.CTkLabel(dlg, text="Game servers keep running. BaoBun closes, updates and opens again; the "
                                    "Watchdog moves to the new version when nobody is playing.",
                          anchor="w", justify="left", wraplength=520, text_color=t["text_muted"])
    status.pack(fill="x", padx=18)
    bar = ctk.CTkProgressBar(dlg)
    bar.set(0)
    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=16, pady=(8, 14))

    def go():
        if not info.get("asset"):
            if info.get("page"):
                webbrowser.open(info["page"])
            return
        go_btn.configure(state="disabled")
        bar.pack(fill="x", padx=18, pady=(4, 0), before=btns)

        def prog(pct):
            try:
                app.after(0, lambda: bar.set(pct / 100.0))
            except Exception:
                pass

        def work():
            try:
                path = updates.download(mgr, info, prog)
            except Exception as e:
                msg = str(e)
                app.after(0, lambda: (status.configure(text="⚠ " + msg, text_color=t["warning"]),
                                      go_btn.configure(state="normal")))
                return
            app.after(0, lambda: launch(path))
        threading.Thread(target=work, daemon=True).start()

    def launch(path):
        status.configure(text="Starting the installer…", text_color=t["text_muted"])
        try:
            updates.run_installer(path, silent=True)
        except Exception as e:
            status.configure(text="⚠ Couldn't start the installer: %s" % e, text_color=t["warning"])
            go_btn.configure(state="normal")
            return
        app.after(1500, app.quit_for_update)

    go_btn = ctk.CTkButton(btns, text="Download and install" if info.get("asset") else "Open the download page",
                           fg_color=t["success"], command=go)
    go_btn.pack(side="right")
    if info.get("page"):
        ctk.CTkButton(btns, text="What's new (web)", fg_color="transparent", border_width=1,
                      border_color=t["border"], command=lambda: webbrowser.open(info["page"])).pack(side="left")
    ctk.CTkButton(btns, text="Later", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=dlg.destroy).pack(side="right", padx=8)
    return dlg
