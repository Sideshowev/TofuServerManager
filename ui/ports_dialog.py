"""Desktop "Ports" page: every server's ports, clashes in red, Fix automatically,
per-server port editing and "Turn on RCON" (see core/port_plan.py, core/rcon_setup.py)."""

from __future__ import annotations

import threading

import customtkinter as ctk

SOURCE = {"default": "game default", "pending": "set on next start", "baobun": "from Edit Server", "set": ""}


def turn_on_rcon(app, theme, server_id, on_done=None, parent=None):
    """Switch RCON on for one server and show the new password once."""
    t = theme
    srv = app.manager.get_server(server_id) or {}

    def work():
        from core import rcon_setup
        return rcon_setup.enable(app.manager, server_id, user="desktop")

    def done(res):
        dlg = ctk.CTkToplevel(parent or app)
        dlg.title("RCON is on")
        dlg.transient(parent or app)
        app.center_window(dlg, 520, 300 if res.get("password") else 200, fit=True)
        ctk.CTkLabel(dlg, text="RCON is on for " + srv.get("name", "the server"), anchor="w",
                     text_color=t["text_light"], font=("Bahnschrift SemiBold", 15, "bold")).pack(
            fill="x", padx=18, pady=(16, 6))
        ctk.CTkLabel(dlg, text=res.get("note") or "", anchor="w", justify="left", wraplength=480,
                     text_color=t["text_muted"]).pack(fill="x", padx=18)
        if res.get("password"):
            row = ctk.CTkFrame(dlg, fg_color=t["bg_input"], corner_radius=8)
            row.pack(fill="x", padx=18, pady=12)
            box = ctk.CTkEntry(row, font=("Consolas", 14), border_width=0, fg_color=t["bg_input"])
            box.insert(0, res["password"])
            box.configure(state="readonly")
            box.pack(side="left", fill="x", expand=True, padx=8, pady=8)

            def copy():
                dlg.clipboard_clear()
                dlg.clipboard_append(res["password"])
                copy_btn.configure(text="Copied")
            copy_btn = ctk.CTkButton(row, text="Copy", width=80, fg_color=t["accent_blue"], command=copy)
            copy_btn.pack(side="right", padx=8)
        ctk.CTkButton(dlg, text="Done", fg_color=t["border"], width=100,
                      command=dlg.destroy).pack(side="bottom", anchor="e", padx=18, pady=14)
        if on_done:
            on_done()

    app._run_bg("Turning on RCON for %s..." % srv.get("name", "server"), work, on_done=done)


def open_ports_dialog(app, theme):
    t = theme
    mgr = app.manager
    dlg = ctk.CTkToplevel(app)
    dlg.title("Ports")
    dlg.transient(app)
    app.center_window(dlg, 860, 680, fit=True)
    ctk.CTkLabel(dlg, text="Ports", anchor="w", text_color=t["text_light"],
                 font=("Bahnschrift SemiBold", 18, "bold")).pack(fill="x", padx=18, pady=(16, 0))
    ctk.CTkLabel(dlg, anchor="w", justify="left", wraplength=800, text_color=t["text_muted"],
                 text="Every server needs its own ports - lots of games all default to 7777 or 27015. "
                      "RCON ports are only used by BaoBun on this PC: they are never opened in the firewall "
                      "or forwarded on your router.").pack(fill="x", padx=18, pady=(2, 8))
    banner = ctk.CTkFrame(dlg, fg_color="transparent")
    banner.pack(fill="x", padx=16)
    body = ctk.CTkScrollableFrame(dlg, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=6)
    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=16, pady=(4, 14))
    state = {"data": None}

    def clear(frame):
        for w in frame.winfo_children():
            w.destroy()

    def fix_all():
        def work():
            from core import port_plan
            return port_plan.fix_all(mgr, user="desktop")

        def done(res):
            moved = ", ".join(m["name"] for m in res["moved"]) or "nothing"
            msg = "Moved: %s." % moved
            if res["skipped"]:
                msg += " Left alone: " + "; ".join("%s (%s)" % (s["name"], s["reason"]) for s in res["skipped"])
            app._show_status(msg)
            load()
        app._run_bg("Finding free ports...", work, on_done=done)

    def edit(server):
        win = ctk.CTkToplevel(dlg)
        win.title("Ports - " + server["name"])
        win.transient(dlg)
        app.center_window(win, 440, 120 + 46 * len(server["rows"]), fit=True)
        ctk.CTkLabel(win, text=server["name"], anchor="w", text_color=t["text_light"],
                     font=("Bahnschrift SemiBold", 15, "bold")).pack(fill="x", padx=18, pady=(14, 6))
        entries = {}
        for r in server["rows"]:
            row = ctk.CTkFrame(win, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=3)
            label = r["label"] + ("  (uses %d in a row)" % r["span"] if r["span"] > 1 else "")
            ctk.CTkLabel(row, text=label, anchor="w", width=240, text_color=t["text_light"]).pack(side="left")
            e = ctk.CTkEntry(row, width=110)
            e.insert(0, str(r["port"]))
            e.pack(side="right")
            entries[r["role"]] = e
        err = ctk.CTkLabel(win, text="", text_color=t["danger"], anchor="w", wraplength=400)
        err.pack(fill="x", padx=18)

        def save():
            try:
                wanted = {k: int(e.get().strip()) for k, e in entries.items()}
            except ValueError:
                err.configure(text="Ports must be numbers.")
                return

            def work():
                from core import port_plan
                return port_plan.apply(mgr, server["id"], wanted=wanted, user="desktop")

            def done(res):
                note = "Ports saved for %s." % server["name"]
                if res["pending"]:
                    note += " %s will be written on its next start." % ", ".join(res["pending"])
                if res["errors"]:
                    note += " Problems: " + "; ".join(res["errors"])
                app._show_status(note)
                if win.winfo_exists():
                    win.destroy()
                load()
            app._run_bg("Saving ports...", work, on_done=done,
                        on_error=lambda e: err.configure(text=str(e)) if win.winfo_exists() else None)
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=12, side="bottom")
        ctk.CTkButton(bar, text="Save", width=90, fg_color=t["success"], command=save).pack(side="right")
        ctk.CTkButton(bar, text="Cancel", width=90, fg_color="transparent", border_width=1,
                      border_color=t["border"], command=win.destroy).pack(side="right", padx=6)

    def show(data, rcon_off, err):
        if not dlg.winfo_exists():
            return
        clear(body)
        clear(banner)
        if err:
            ctk.CTkLabel(banner, text="Couldn't read the ports: " + err, text_color=t["danger"]).pack(anchor="w")
            return
        state["data"] = data
        clashes = data.get("clashes") or []
        if clashes:
            box = ctk.CTkFrame(banner, fg_color=t["bg_input"], corner_radius=8, border_width=1,
                               border_color=t["danger"])
            box.pack(fill="x", pady=(0, 4))
            txt = "\n".join("%s share port%s %s" % (" and ".join(c["names"]), "s" if len(c["ports"]) > 1 else "",
                                                     ", ".join(str(p) for p in c["ports"][:6]))
                            for c in clashes)
            ctk.CTkLabel(box, text=txt, anchor="w", justify="left", text_color=t["text_light"],
                         wraplength=620).pack(side="left", padx=12, pady=10, fill="x", expand=True)
            ctk.CTkButton(box, text="Fix automatically", fg_color=t["accent_blue"], width=140,
                          command=fix_all).pack(side="right", padx=12)
        else:
            ctk.CTkLabel(banner, text="✅  No two servers share a port.", anchor="w",
                         text_color=t["status_online"]).pack(fill="x", pady=(0, 4))
        for s in data.get("servers") or []:
            card = ctk.CTkFrame(body, fg_color=t["bg_dark"], corner_radius=8)
            card.pack(fill="x", padx=6, pady=4)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=10, pady=(8, 2))
            ctk.CTkLabel(top, text="●", width=14, text_color=t["status_online"] if s["running"]
                         else t["status_inactive"]).pack(side="left")
            ctk.CTkLabel(top, text=s["name"], anchor="w", text_color=t["text_light"],
                         font=("Segoe UI", 13, "bold")).pack(side="left", padx=(4, 0))
            if s["known"] and not s["running"]:
                ctk.CTkButton(top, text="Change", width=80, height=26, fg_color=t["border"],
                              command=lambda sv=s: edit(sv)).pack(side="right")
            elif s["running"]:
                ctk.CTkLabel(top, text="running - stop it to change ports", text_color=t["text_muted"],
                             font=("Segoe UI", 11)).pack(side="right")
            if s["id"] in rcon_off and not s["running"]:
                ctk.CTkButton(top, text="Turn on RCON", width=110, height=26, fg_color=t["accent_blue"],
                              command=lambda sid=s["id"]: turn_on_rcon(app, t, sid, on_done=load,
                                                                       parent=dlg)).pack(side="right", padx=6)
            if not s["rows"]:
                ctk.CTkLabel(card, text="BaoBun doesn't know this game's ports - set the game port in Edit "
                                        "Server.", anchor="w", text_color=t["text_muted"],
                             font=("Segoe UI", 11)).pack(fill="x", padx=30, pady=(0, 8))
                continue
            grid = ctk.CTkFrame(card, fg_color="transparent")
            grid.pack(fill="x", padx=30, pady=(0, 8))
            for i, r in enumerate(s["rows"]):
                ctk.CTkLabel(grid, text=r["label"], anchor="w", width=150, text_color=t["text_muted"]).grid(
                    row=i, column=0, sticky="w")
                ctk.CTkLabel(grid, text=r["range"], anchor="w", width=110,
                             text_color=t["danger"] if r["clash"] else t["text_light"],
                             font=("Consolas", 13, "bold")).grid(row=i, column=1, sticky="w")
                bits = []
                if r["local"]:
                    bits.append("this PC only")
                if SOURCE.get(r["source"]):
                    bits.append(SOURCE[r["source"]])
                if r["clash"]:
                    bits.append("also used by " + ", ".join(r["clash"]))
                ctk.CTkLabel(grid, text="  ·  ".join(bits), anchor="w",
                             text_color=t["danger"] if r["clash"] else t["text_muted"],
                             font=("Segoe UI", 11)).grid(row=i, column=2, sticky="w")
            if not s["known"]:
                ctk.CTkLabel(card, text="BaoBun only knows the port from Edit Server for this game - change it "
                                        "in the game's own settings too.", anchor="w", wraplength=760,
                             text_color=t["text_muted"], font=("Segoe UI", 11)).pack(fill="x", padx=30,
                                                                                    pady=(0, 8))

    def load():
        def work():
            from core import port_plan
            try:
                data = port_plan.overview(mgr)
                res = (data, {s["id"] for s in data["servers"] if s.get("rcon") == "off"}, None)
            except Exception as e:
                res = (None, set(), str(e))
            app.after(0, lambda: show(*res))
        threading.Thread(target=work, daemon=True).start()

    ctk.CTkButton(btns, text="Close", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=dlg.destroy).pack(side="right")
    ctk.CTkButton(btns, text="Check again", fg_color=t["border"], command=load).pack(side="right", padx=6)
    load()
