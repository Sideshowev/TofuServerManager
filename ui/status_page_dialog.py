"""Desktop "Public status page" settings (core/status_page.py). The agent picks up
changes within half a minute - no restart needed."""

from __future__ import annotations

import webbrowser

import customtkinter as ctk


def open_status_page_dialog(app, theme):
    from core import status_page
    t = theme
    mgr = app.manager
    cfg = status_page.config(mgr)
    dlg = ctk.CTkToplevel(app)
    dlg.title("Public status page")
    dlg.transient(app)
    app.center_window(dlg, 600, 600, fit=True)
    body = ctk.CTkScrollableFrame(dlg, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=(16, 6))

    def note(text, color=None):
        lbl = ctk.CTkLabel(body, text=text, anchor="w", justify="left", wraplength=520,
                           text_color=color or t["text_muted"], font=("Segoe UI", 12))
        lbl.pack(fill="x", padx=12, pady=(2, 4))
        return lbl

    def field(label, value, ph=""):
        ctk.CTkLabel(body, text=label, anchor="w", text_color=t["text_light"]).pack(fill="x", padx=12, pady=(8, 0))
        v = ctk.StringVar(value=value)
        ctk.CTkEntry(body, textvariable=v, placeholder_text=ph).pack(fill="x", padx=12)
        return v

    ctk.CTkLabel(body, text="Public status page", anchor="w", text_color=t["text_light"],
                 font=("Bahnschrift SemiBold", 16, "bold")).pack(fill="x", padx=12, pady=(8, 2))
    note("A read-only page friends can open without logging in: which servers are up, how many are playing, "
         "the address to join and each server's mod list. There are no buttons, passwords or folders on it, and "
         "it runs on its own port - separate from the web panel. Servers with 'Show on the Discord panel' off "
         "are left off.")
    on = ctk.BooleanVar(value=cfg["enabled"])
    ctk.CTkCheckBox(body, text="Turn on the status page", variable=on, fg_color=t["accent_blue"]).pack(
        fill="x", padx=12, pady=(8, 2))
    port = field("Port", str(cfg["port"]), str(status_page.DEFAULT_PORT))
    pub = ctk.BooleanVar(value=cfg["public"])
    ctk.CTkCheckBox(body, text="Answer the internet too (then forward the port on your router)", variable=pub,
                    fg_color=t["accent_blue"]).pack(fill="x", padx=12, pady=(10, 2))
    note("Off: only this PC, your home network and Tailscale can open it.")
    names = ctk.BooleanVar(value=cfg["show_players"])
    ctk.CTkCheckBox(body, text="List player names (not just how many)", variable=names,
                    fg_color=t["accent_blue"]).pack(fill="x", padx=12, pady=(6, 2))
    title = field("Page title", cfg["title"], "Our game servers")
    addr = field("Address players type", cfg["address"], "blank = this PC's public IP, e.g. play.example.com")
    invite = field("Discord invite link (optional)", cfg["invite"], "https://discord.gg/...")
    err = note("", t["danger"])
    link = note("")

    def show_link():
        c = status_page.config(mgr)
        link.configure(text=("Open it at http://localhost:%d/ (others use this PC's address)." % c["port"])
                       if c["enabled"] else "")

    def save():
        try:
            p = int(port.get().strip() or status_page.DEFAULT_PORT)
            if not 1024 <= p <= 65535:
                raise ValueError
        except ValueError:
            err.configure(text="The port must be a number from 1024 to 65535.")
            return
        a = addr.get().strip()
        if a and not status_page.clean_address(a):
            err.configure(text="Use a host name or IP for the address, like play.example.com.")
            return
        inv = invite.get().strip()
        if inv and not inv.startswith(("https://discord.gg/", "https://discord.com/invite/")):
            err.configure(text="The invite should look like https://discord.gg/abc123.")
            return
        if pub.get() and not cfg["public"] and not app.ask_yes_no(
                "Status page", "Let anyone on the internet see the status page once you forward the port? It "
                "shows server names, player counts, the join address and mods - nothing secret.", parent=dlg):
            return
        for k, v in (("status_page_enabled", bool(on.get())), ("status_page_port", p),
                     ("status_page_public", bool(pub.get())), ("status_show_players", bool(names.get())),
                     ("status_title", title.get().strip()[:60]), ("status_address", status_page.clean_address(a)),
                     ("status_discord_invite", inv)):
            mgr.set_setting(k, v)
        err.configure(text="")
        cfg.update(public=bool(pub.get()))
        app._show_status("Status page %s - applies within half a minute" % ("on" if on.get() else "off"))
        show_link()

    def firewall():
        from core.hosting import run_elevated
        try:
            p = int(port.get().strip() or status_page.DEFAULT_PORT)
        except ValueError:
            p = status_page.DEFAULT_PORT
        if run_elevated(status_page.firewall_commands(p, bool(pub.get()))):
            app._show_status("Windows Firewall now lets the status page through")
        else:
            err.configure(text="Windows didn't add the rule (the admin prompt was declined?).")

    show_link()
    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=16, pady=(4, 14))
    ctk.CTkButton(btns, text="Close", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=dlg.destroy).pack(side="right")
    ctk.CTkButton(btns, text="Save", fg_color=t["success"], command=save).pack(side="right", padx=6)
    ctk.CTkButton(btns, text="Open Windows Firewall for it", fg_color=t["border"], command=firewall).pack(side="left")
    ctk.CTkButton(btns, text="Open page", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=lambda: webbrowser.open("http://localhost:%d/" % status_page.config(mgr)["port"])
                  ).pack(side="left", padx=6)
    return dlg
