"""Desktop "How players connect" dialog: addresses to share, ports, router forwarding."""

from __future__ import annotations

import threading

import customtkinter as ctk


def open_connect_dialog(app, theme, server_id):
    t = theme
    dlg = ctk.CTkToplevel(app)
    dlg.title("How players connect")
    dlg.transient(app)
    app.center_window(dlg, 640, 600, fit=True)
    body = ctk.CTkScrollableFrame(dlg, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=16)
    status = ctk.CTkLabel(body, text="Checking addresses and ports...", anchor="w", text_color=t["text_muted"])
    status.pack(fill="x", padx=8, pady=8)

    def heading(text):
        ctk.CTkLabel(body, text=text, anchor="w", text_color=t["text_light"],
                     font=("Bahnschrift SemiBold", 14, "bold")).pack(fill="x", padx=8, pady=(12, 2))

    def copy(text):
        dlg.clipboard_clear()
        dlg.clipboard_append(text)
        app._show_status("Copied " + text)

    def show(d, err):
        try:
            status.destroy()
        except Exception:
            pass
        if err:
            ctk.CTkLabel(body, text="Couldn't check: " + err, text_color=t["warning"]).pack(fill="x", padx=8)
            return
        r = d.get("reach") or {}
        if r:
            good = r.get("verdict") in ("ok", "likely", "relay")
            bad = r.get("verdict") in ("blocked", "cgnat")
            box = ctk.CTkFrame(body, fg_color="#2a2413" if bad else t["bg_dark"], corner_radius=10)
            box.pack(fill="x", padx=8, pady=(4, 2))
            ctk.CTkLabel(box, text="Can friends connect?", anchor="w", text_color=t["text_muted"],
                         font=("Segoe UI", 11)).pack(fill="x", padx=12, pady=(10, 0))
            ctk.CTkLabel(box, text=r.get("title") or "", anchor="w",
                         text_color=t["status_online"] if good else (t["warning"] if bad else t["text_light"]),
                         font=("Bahnschrift SemiBold", 18, "bold")).pack(fill="x", padx=12)
            ctk.CTkLabel(box, text=r.get("detail") or "", anchor="w", justify="left", wraplength=540,
                         text_color=t["text_light"]).pack(fill="x", padx=12, pady=(0, 10))
            if r.get("verdict") == "cgnat":
                ctk.CTkLabel(body, text="Tailscale: install it on this PC and on each friend's PC, sign in, and share "
                             "this PC with them from the Tailscale admin page. They then join at the Tailscale "
                             "address below.", anchor="w", justify="left", wraplength=560,
                             text_color=t["text_muted"]).pack(fill="x", padx=12, pady=(2, 0))
        heading("Addresses to share")
        rows = [("From the internet (friends)", d["addresses"].get("internet"), "Needs the ports below forwarded on your router."),
                ("Same home network", d["addresses"].get("lan"), ""),
                ("Over Tailscale", d["addresses"].get("tailscale"), "")]
        for label, val, hint in rows:
            if not val:
                continue
            r = ctk.CTkFrame(body, fg_color=t["bg_dark"], corner_radius=8)
            r.pack(fill="x", padx=8, pady=3)
            left = ctk.CTkFrame(r, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=10, pady=6)
            ctk.CTkLabel(left, text=label, anchor="w", text_color=t["text_muted"], font=("Segoe UI", 11)).pack(fill="x")
            ctk.CTkLabel(left, text=val, anchor="w", text_color=t["accent_gold"],
                         font=("Consolas", 15, "bold")).pack(fill="x")
            if hint:
                ctk.CTkLabel(left, text=hint, anchor="w", text_color=t["text_muted"], font=("Segoe UI", 10)).pack(fill="x")
            ctk.CTkButton(r, text="Copy", width=70, fg_color=t["border"], command=lambda v=val: copy(v)).pack(
                side="right", padx=10)
        heading("Checklist")
        fw = d.get("firewall")
        lines = ["✅ Windows Firewall allows this server" if fw else
                 ("⚠ No Windows Firewall rule yet - use Open Firewall" if fw is False else
                  "Windows Firewall: couldn't check")]
        if not d["running"]:
            lines.append("⏸ Start the server to check its ports")
        elif d["ports"] and d["ports"][0].get("listening"):
            lines.append("✅ The server is listening on its game port")
        elif d["ports"] and d["ports"][0].get("listening") is False:
            lines.append("⚠ Not listening on the game port yet")
        lines += d.get("tips") or []
        for ln in lines:
            ctk.CTkLabel(body, text=ln, anchor="w", justify="left", wraplength=560,
                         text_color=t["text_light"]).pack(fill="x", padx=12, pady=1)
        heading("Ports to forward on your router")
        if not d["ports"]:
            ctk.CTkLabel(body, text="Unknown - set the game port in Edit Server.", anchor="w",
                         text_color=t["text_muted"]).pack(fill="x", padx=12)
        for p in d["ports"]:
            state = {True: "listening", False: "not listening"}.get(p.get("listening"), "")
            ctk.CTkLabel(body, text="%-6s %-8s %-18s %s" % (p["port"], p["proto"], p["label"], state), anchor="w",
                         font=("Consolas", 12), text_color=t["text_light"]).pack(fill="x", padx=12)
        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(fill="x", padx=8, pady=(12, 4))

        def upnp(remove):
            if not app.ask_yes_no("Router forwarding", "Remove these port forwards from the router?" if remove else
                                  "Ask your router to forward these ports to this PC? Anyone on the internet "
                                  "can then reach them.", parent=dlg):
                return
            app._show_status("Talking to the router...")

            def work():
                try:
                    r = app.engine.api.process("server.upnp", {"server_id": server_id, "remove": remove},
                                               user_id="gui")
                    msg = (r.data or {}) if r.success else {"error": r.error}
                except Exception as e:
                    msg = {"error": str(e)}

                def done():
                    if msg.get("ok"):
                        app._toast("Router", ("Removed: " if remove else "Forwarded: ") + ", ".join(msg.get("done") or []))
                    else:
                        app.show_error("Router", msg.get("error") or "Some ports failed: " + ", ".join(
                            f["port"] for f in msg.get("failed") or []))
                app.after(0, done)
            threading.Thread(target=work, daemon=True).start()
        ctk.CTkButton(btns, text="Forward automatically (UPnP)", fg_color=t["accent_blue"],
                      command=lambda: upnp(False)).pack(side="left")
        ctk.CTkButton(btns, text="Remove forwarding", fg_color="transparent", border_width=1,
                      border_color=t["border"], command=lambda: upnp(True)).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Open Firewall", fg_color=t["border"],
                      command=app.open_firewall_for_selected).pack(side="left", padx=6)
        ctk.CTkLabel(body, text="UPnP works if your router has it turned on. Otherwise add the ports above in "
                     "your router's settings, pointing at %s." % ((d.get("lan_ips") or ["this PC"])[0]),
                     anchor="w", justify="left", wraplength=560, text_color=t["text_muted"]).pack(fill="x", padx=12)

    def work():
        try:
            from core.network import connect_info
            from core import reach
            d, err = connect_info(app.manager, server_id), None
            try:
                d["reach"] = reach.check(app.manager, server_id)      # fresh: the user asked
            except Exception as e:
                d["reach"] = {"title": "Couldn't check", "detail": str(e)[:200]}
        except Exception as e:
            d, err = None, str(e)
        app.after(0, lambda: show(d, err))
    threading.Thread(target=work, daemon=True).start()
    def again():
        dlg.destroy()
        open_connect_dialog(app, theme, server_id)
    row = ctk.CTkFrame(dlg, fg_color="transparent")
    row.pack(pady=(0, 12))
    ctk.CTkButton(row, text="Check again", width=110, fg_color=t["border"], command=again).pack(side="left", padx=4)
    ctk.CTkButton(row, text="Close", width=90, command=dlg.destroy).pack(side="left", padx=4)
