"""Desktop dialog: find game servers already installed in a folder and add them."""

from __future__ import annotations

import threading
from tkinter import filedialog

import customtkinter as ctk


def open_import_dialog(app, theme):
    t = theme
    folder = filedialog.askdirectory(title="Where are your game servers? (e.g. D:\\GameServers)")
    if not folder:
        return
    dlg = ctk.CTkToplevel(app)
    dlg.title("Find existing servers")
    dlg.transient(app)
    app.center_window(dlg, 680, 560, fit=True)
    head = ctk.CTkLabel(dlg, text="Looking for game servers in %s ..." % folder, anchor="w", justify="left",
                        wraplength=620, text_color=t["text_light"])
    head.pack(fill="x", padx=18, pady=(16, 6))
    body = ctk.CTkScrollableFrame(dlg, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=6)
    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=16, pady=(4, 14))
    picks = []

    def show(found, err):
        if err:
            head.configure(text="Couldn't scan that folder: " + err)
            return
        new = [f for f in found if not f.get("already_added")]
        head.configure(text=("Found %d server(s)" % len(found)) + (
            " - %d already in BaoBun" % (len(found) - len(new)) if len(found) != len(new) else "")
            + (". Tick the ones to add." if new else "."))
        if not found:
            ctk.CTkLabel(body, text="No known game servers found (BaoBun looks 3 folders deep).",
                         text_color=t["text_muted"]).pack(pady=20)
        for f in found:
            row = ctk.CTkFrame(body, fg_color=t["bg_dark"], corner_radius=8)
            row.pack(fill="x", padx=6, pady=3)
            var = ctk.BooleanVar(value=not f.get("already_added"))
            cb = ctk.CTkCheckBox(row, text="", variable=var, width=24, fg_color=t["accent_blue"])
            cb.pack(side="left", padx=(10, 4))
            if f.get("already_added"):
                cb.configure(state="disabled")
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, pady=6)
            ctk.CTkLabel(left, text=f["game"] + ("   (already added)" if f.get("already_added") else ""),
                         anchor="w", font=("Segoe UI", 13, "bold"), text_color=t["text_light"]).pack(fill="x")
            ctk.CTkLabel(left, text=f["directory"], anchor="w", text_color=t["text_muted"],
                         font=("Segoe UI", 11)).pack(fill="x")
            if not f.get("already_added"):
                picks.append((var, f))

        def do_import():
            chosen = [f for var, f in picks if var.get()]
            if not chosen:
                dlg.destroy()
                return
            from core.importer import import_servers
            try:
                added = import_servers(app.manager, chosen)
            except Exception as e:
                app.show_error("Import", str(e))
                return
            dlg.destroy()
            app.refresh_servers()
            app._toast("Servers added", "Added %d server(s). They start only when you press Start (change "
                       "that in Edit Server)." % len(added))
        if picks:
            ctk.CTkButton(btns, text="Add selected", fg_color=t["accent_blue"], command=do_import).pack(side="right")

    ctk.CTkButton(btns, text="Close", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=dlg.destroy).pack(side="right", padx=6)

    def work():
        from core.importer import scan
        try:
            known = [s.get("directory") for s in app.manager.load_servers()]
            found, err = scan(folder, known_dirs=known), None
        except Exception as e:
            found, err = None, str(e)
        app.after(0, lambda: show(found, err))
    threading.Thread(target=work, daemon=True).start()
