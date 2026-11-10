"""Desktop "PC-down alerts" settings: let another PC notice when the server PC goes down
(core/heartbeat.py)."""

from __future__ import annotations

import threading

import customtkinter as ctk


def open_watchdog_dialog(app, theme):
    from core import heartbeat
    t = theme
    mgr = app.manager
    dlg = ctk.CTkToplevel(app)
    dlg.title("PC-down alerts")
    dlg.transient(app)
    app.center_window(dlg, 620, 600, fit=True)
    body = ctk.CTkScrollableFrame(dlg, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=(16, 6))

    def heading(text, top=12):
        ctk.CTkLabel(body, text=text, anchor="w", text_color=t["text_light"],
                     font=("Bahnschrift SemiBold", 15, "bold")).pack(fill="x", padx=12, pady=(top, 2))

    def note(text, color=None):
        lbl = ctk.CTkLabel(body, text=text, anchor="w", justify="left", wraplength=560,
                           text_color=color or t["text_muted"], font=("Segoe UI", 12))
        lbl.pack(fill="x", padx=12, pady=(0, 4))
        return lbl

    heading("Know when the server PC goes down", top=4)
    note("If the server PC freezes or loses power, BaoBun on it can't send alerts. So BaoBun on another PC (for "
         "example your own PC) checks it every minute and posts to your Discord alert webhook when it stops answering for 3 "
         "minutes - and again when it's back. The nightly restart doesn't count.")

    heading("On the server PC")
    hb_var = ctk.BooleanVar(value=mgr.get_setting("heartbeat_enabled", False) is True)
    port = int(mgr.get_setting("heartbeat_port", heartbeat.DEFAULT_PORT) or heartbeat.DEFAULT_PORT)
    ctk.CTkCheckBox(body, text="Let other PCs check that BaoBun is alive (port %d)" % port, variable=hb_var,
                    fg_color=t["accent_blue"]).pack(anchor="w", padx=12, pady=(4, 2))
    note("Home network and Tailscale only; it only shows how many servers and players are on.")
    ctk.CTkButton(body, text="Allow it through Windows Firewall", width=240, fg_color=t["border"],
                  command=lambda: fw()).pack(anchor="w", padx=12, pady=(2, 8))

    heading("On the PC that watches")
    host_var = ctk.StringVar(value=mgr.get_setting("watch_host", "") or "")
    row = ctk.CTkFrame(body, fg_color="transparent")
    row.pack(fill="x", padx=12, pady=(4, 2))
    ctk.CTkLabel(row, text="Server PC name or address", text_color=t["text_light"]).pack(side="left")
    ctk.CTkEntry(row, textvariable=host_var, width=220, placeholder_text="e.g. SERVER-PC or 192.168.1.50").pack(
        side="left", padx=8)
    ctk.CTkButton(row, text="Test", width=70, fg_color=t["border"], command=lambda: test()).pack(side="left")
    result = note("Leave blank on the server PC itself.")
    hook = bool(mgr.get_setting("alert_webhook_url", ""))
    note("Discord webhook on this PC: %s" % ("set ✅" if hook else
         "not set - add one in Settings > Discord alerts, or the warning only goes to the log"),
         t["text_muted"] if hook else t["warning"])

    def fw():
        from core.hosting import run_elevated
        if not run_elevated(heartbeat.firewall_commands(port)):
            app._show_status("Windows didn't add the firewall rule (prompt declined?)")
        else:
            app._show_status("Firewall rule for the alive-check page added")

    def test():
        h = host_var.get().strip()
        if not h:
            result.configure(text="Type the server PC's name or address first.", text_color=t["warning"])
            return
        result.configure(text="Checking %s..." % heartbeat.target_url(h), text_color=t["text_muted"])

        def work():
            r = heartbeat.probe(h)
            pings = None if r else heartbeat.pc_pings(h)
            if r:
                txt, col = ("✅ BaoBun on %s answers: %d server(s) running, %d player(s)."
                            % (r.get("host", h), r.get("running", 0), r.get("players", 0)), t["status_online"])
            elif pings:
                txt, col = ("The PC answers pings but not the alive-check page. On the server PC: tick the box above, "
                            "click 'Allow it through Windows Firewall', and make sure BaoBun is updated."), t["warning"]
            else:
                txt, col = "Can't reach %s at all - check the name/address." % h, t["danger"]
            app.after(0, lambda: result.configure(text=txt, text_color=col) if dlg.winfo_exists() else None)
        threading.Thread(target=work, daemon=True).start()

    def save():
        mgr.set_setting("heartbeat_enabled", bool(hb_var.get()))
        mgr.set_setting("watch_host", host_var.get().strip())
        app._show_status("PC-down alerts saved" + (" - watching %s (starts within a minute)" % host_var.get().strip()
                                             if host_var.get().strip() else ""))
        dlg.destroy()

    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=16, pady=(4, 14))
    ctk.CTkButton(btns, text="Close", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=dlg.destroy).pack(side="right")
    ctk.CTkButton(btns, text="Save", fg_color=t["success"], command=save).pack(side="right", padx=6)
