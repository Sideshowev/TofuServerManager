"""Desktop "pick a backup to restore": the newest backups here and on the second
backup folder (another PC), with the result of the last restore check."""

from __future__ import annotations

import time

import customtkinter as ctk


def _when(path_or_name: str) -> str:
    import re
    m = re.match(r"(\d{8})_(\d{6})", path_or_name)
    if not m:
        return path_or_name
    try:
        t = time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        return time.strftime("%a %d %b %Y, %H:%M", t)
    except ValueError:
        return path_or_name


def pick_backup(app, theme, backups, check=None, initial_dir=""):
    """Shows a list; returns the chosen zip path or None."""
    t = theme
    result = {"path": None}
    dlg = ctk.CTkToplevel(app)
    dlg.title("Restore a backup")
    dlg.transient(app)
    app.center_window(dlg, 620, 520, fit=True)
    ctk.CTkLabel(dlg, text="Pick a backup to put back (newest first). The current world is saved first.",
                 anchor="w", justify="left", wraplength=560, text_color=t["text_light"]).pack(fill="x", padx=18,
                                                                                              pady=(16, 4))
    if check:
        ctk.CTkLabel(dlg, text=("✅ " if check["ok"] else "⚠ ") + check["text"], anchor="w",
                     justify="left", wraplength=560,
                     text_color=t["status_online"] if check["ok"] else t["warning"]).pack(fill="x", padx=18)
    body = ctk.CTkScrollableFrame(dlg, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=8)
    for b in backups[:30]:
        row = ctk.CTkFrame(body, fg_color=t["bg_dark"], corner_radius=8)
        row.pack(fill="x", padx=4, pady=3)
        where = "  ·  copy on the other PC" if b.get("location") == "copy" else ""
        ctk.CTkLabel(row, text="%s  ·  %.1f MB%s" % (_when(b["name"]), b["size"] / 1048576, where), anchor="w",
                     text_color=t["text_light"]).pack(side="left", padx=10, pady=6)
        ctk.CTkButton(row, text="Restore this", width=110, fg_color=t["accent_blue"],
                      command=lambda p=b["path"]: (result.update(path=p), dlg.destroy())).pack(side="right",
                                                                                              padx=8)
    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=16, pady=(0, 14))

    def other():
        from tkinter import filedialog
        p = filedialog.askopenfilename(parent=dlg, title="Pick a backup zip", initialdir=initial_dir,
                                       filetypes=[("Backup zip", "*.zip")])
        if p:
            result["path"] = p
            dlg.destroy()
    ctk.CTkButton(btns, text="Cancel", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=dlg.destroy).pack(side="right")
    ctk.CTkButton(btns, text="Another file...", fg_color=t["border"], command=other).pack(side="left")
    try:
        dlg.grab_set()
    except Exception:
        pass
    app.wait_window(dlg)
    return result["path"]
