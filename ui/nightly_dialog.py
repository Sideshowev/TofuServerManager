"""Desktop "Nightly restart" settings: restart the server PC every night, bring back
the servers people use, and start BaoBun at boot (core/nightly.py, core/boot_task.py)."""

from __future__ import annotations

import threading
import time
from datetime import datetime

import customtkinter as ctk
from ui.app_common import later


def _when(ts):
    if not ts:
        return "-"
    d = datetime.fromtimestamp(ts)
    return d.strftime("%a %d %b, %H:%M")


def open_nightly_dialog(app, theme):
    from core import nightly
    t = theme
    mgr = app.manager
    cfg = nightly.config(mgr)
    dlg = ctk.CTkToplevel(app)
    dlg.title("Nightly restart")
    dlg.transient(app)
    app.center_window(dlg, 640, 700, fit=True)
    body = ctk.CTkScrollableFrame(dlg, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=(16, 6))

    def heading(text, top=14):
        ctk.CTkLabel(body, text=text, anchor="w", text_color=t["text_light"],
                     font=("Bahnschrift SemiBold", 15, "bold")).pack(fill="x", padx=12, pady=(top, 2))

    def note(text, color=None):
        lbl = ctk.CTkLabel(body, text=text, anchor="w", justify="left", wraplength=570,
                           text_color=color or t["text_muted"], font=("Segoe UI", 12))
        lbl.pack(fill="x", padx=12, pady=(0, 4))
        return lbl

    heading("Nightly restart", top=6)
    note("Every night BaoBun warns players (%s minutes before), stops every server so it saves, backs up "
         "the worlds and restarts Windows - that clears memory leaks in the games and Windows itself. "
         "When the PC is back, only the servers people played on lately start again; the rest sleep until "
         "someone presses Start on Discord or here." % ", ".join(str(m) for m in cfg["warn"]))

    on_var = ctk.BooleanVar(value=cfg["enabled"])
    row = ctk.CTkFrame(body, fg_color="transparent")
    row.pack(fill="x", padx=12, pady=(6, 2))
    ctk.CTkCheckBox(row, text="Restart the server PC every night at", variable=on_var,
                    fg_color=t["accent_blue"]).pack(side="left")
    time_var = ctk.StringVar(value=cfg["time"])
    ctk.CTkEntry(row, textvariable=time_var, width=70).pack(side="left", padx=8)
    ctk.CTkLabel(row, text="(e.g. 05:00 or 5am - this PC's clock)", text_color=t["text_muted"]).pack(side="left")

    backup_var = ctk.BooleanVar(value=cfg["backup"])
    ctk.CTkCheckBox(body, text="Back up the worlds before restarting", variable=backup_var,
                    fg_color=t["accent_blue"]).pack(fill="x", padx=12, pady=(6, 2))
    row2 = ctk.CTkFrame(body, fg_color="transparent")
    row2.pack(fill="x", padx=12, pady=(6, 2))
    ctk.CTkLabel(row2, text="Bring back servers that had players in the last", text_color=t["text_light"]).pack(
        side="left")
    days_var = ctk.StringVar(value=str(cfg["days"]))
    ctk.CTkEntry(row2, textvariable=days_var, width=50).pack(side="left", padx=8)
    ctk.CTkLabel(row2, text="days", text_color=t["text_light"]).pack(side="left")
    note("The same rule decides which servers start whenever BaoBun starts. Servers set to Manual start "
         "never start on their own.")
    err = note("", t["danger"])

    heading("If it ran now")
    preview_lbl = note("Checking...")

    heading("Start BaoBun when the PC turns on")
    note("Needed for the nightly restart: without it the servers only come back once someone signs in to "
         "Windows. BaoBun adds a Windows scheduled task that starts it at boot as your Windows user (one Windows "
         "admin prompt).\n\u2022 Simple: no password stored, but it can't open network folders (\\\\PC\\folder).\n"
         "\u2022 With network access: Windows asks for your password once in its own window (BaoBun never sees "
         "it) - needed when backups are copied to another PC or a server lives on a network folder.")
    boot_row = ctk.CTkFrame(body, fg_color="transparent")
    boot_row.pack(fill="x", padx=12, pady=(4, 2))
    boot_lbl = ctk.CTkLabel(boot_row, text="Checking...", anchor="w", text_color=t["text_light"])
    boot_lbl.pack(side="left")
    boot_row2 = ctk.CTkFrame(body, fg_color="transparent")
    boot_row2.pack(fill="x", padx=12, pady=(2, 8))
    boot_btn = ctk.CTkButton(boot_row2, text="Set up (simple)", width=150, fg_color=t["accent_blue"],
                             state="disabled")
    boot_btn.pack(side="left")
    net_btn = ctk.CTkButton(boot_row2, text="Set up with network access", width=210, fg_color=t["border"],
                            state="disabled")
    net_btn.pack(side="left", padx=8)
    rm_btn = ctk.CTkButton(boot_row2, text="Remove", width=90, fg_color="transparent", border_width=1,
                           border_color=t["border"], state="disabled")
    rm_btn.pack(side="left")
    state = {"boot": None}

    def show_boot(mode, ok):
        if not dlg.winfo_exists():
            return
        state["boot"] = ok
        if ok is None:
            boot_lbl.configure(text="Only available on Windows.")
            return
        if ok:
            boot_lbl.configure(text="\u2705  On - BaoBun starts at boot" + (" (with network access)." if mode == "password"
                                                                     else " (no network folders)."),
                               text_color=t["status_online"])
        else:
            boot_lbl.configure(text="Off - BaoBun starts when you sign in.", text_color=t["text_light"])
        boot_btn.configure(state="normal", command=lambda: setup(False))
        net_btn.configure(state="normal", command=lambda: setup(True))
        rm_btn.configure(state="normal" if ok else "disabled", command=remove)

    def check_boot(delay=0.0):
        def work():
            if delay:
                time.sleep(delay)
            from core import boot_task
            ok = boot_task.installed()
            mode = boot_task.mode() if ok else None
            later(app, lambda: show_boot(mode, ok))
        threading.Thread(target=work, daemon=True).start()

    def setup(network):
        from core import boot_task
        base = app.manager.config_path.parent
        try:
            fn = boot_task.install_with_password if network else boot_task.install
            started = fn(app._startup_command(), str(base), folder=str(base / "runtime"))
        except Exception as e:
            started = False
            err.configure(text=str(e))
        if not started:
            err.configure(text="Windows didn't run it (the admin prompt was declined?).")
        check_boot(delay=15.0 if network else 4.0)

    def remove():
        from core import boot_task
        try:
            boot_task.remove()
        except Exception as e:
            err.configure(text=str(e))
        check_boot(delay=4.0)

    def show_preview(p, e):
        if not dlg.winfo_exists():
            return
        if e:
            preview_lbl.configure(text="Couldn't work it out: " + e)
            return
        back = ", ".join("%s (%s)" % (x["name"], x["why"]) for x in p["bring_back"]) or "nothing is running"
        sleep = ", ".join("%s (%s)" % (x["name"], x["why"]) for x in p["sleeping"]) or "none"
        text = "Comes back: %s.\nStays asleep: %s." % (back, sleep)
        if p.get("next_run"):
            text += "\nNext restart: %s." % _when(p["next_run"])
        last = p.get("last") or {}
        if last.get("at"):
            text += "\nLast one: %s%s." % (_when(last["at"]), "" if last.get("rebooted")
                                           else " (servers only - Windows didn't restart)")
        preview_lbl.configure(text=text)

    def load_preview():
        def work():
            try:
                res = (nightly.preview(mgr), None)
            except Exception as e:
                res = (None, str(e))
            later(app, lambda: show_preview(*res))
        threading.Thread(target=work, daemon=True).start()

    def save():
        h_m = time_var.get().strip()
        if not nightly.valid_time(h_m):
            err.configure(text="Use a time like 05:00, 23:30 or 5am.")
            return
        try:
            days = int(days_var.get().strip())
            if not 1 <= days <= 60:
                raise ValueError
        except ValueError:
            err.configure(text="Days must be a number from 1 to 60.")
            return
        h, m = nightly.parse_time(h_m)
        mgr.set_setting("nightly_restart_enabled", bool(on_var.get()))
        mgr.set_setting("nightly_restart_time", "%02d:%02d" % (h, m))
        mgr.set_setting("nightly_backup", bool(backup_var.get()))
        mgr.set_setting("smart_boot_days", days)
        msg = "Nightly restart %s" % (("on at %02d:%02d" % (h, m)) if on_var.get() else "off")
        if on_var.get() and state["boot"] is False:
            msg += " - also set up 'Start BaoBun when the PC turns on', or servers wait for a sign-in"
        app._show_status(msg)
        err.configure(text="")
        load_preview()

    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=16, pady=(4, 14))
    ctk.CTkButton(btns, text="Close", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=dlg.destroy).pack(side="right")
    ctk.CTkButton(btns, text="Save", fg_color=t["success"], command=save).pack(side="right", padx=6)
    check_boot()
    load_preview()
