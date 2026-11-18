"""Desktop "Linked PCs" window (core/linked_pcs.py).

Top: let another Tofu control this PC (switch, pairing code to copy, firewall).
Bottom: the other PCs this Tofu controls (add with a pairing code, test, remove).
"""

from __future__ import annotations

import threading

import customtkinter as ctk

from core import linked_pcs as LP


def open_linked_pcs_dialog(app, theme):
    t = theme
    mgr = app.manager
    fleet = LP.fleet(mgr)
    dlg = ctk.CTkToplevel(app)
    dlg.title("Linked PCs")
    dlg.transient(app)
    app.center_window(dlg, 680, 680, fit=True)
    body = ctk.CTkScrollableFrame(dlg, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=(16, 6))

    def heading(text, top=12):
        ctk.CTkLabel(body, text=text, anchor="w", text_color=t["text_light"],
                     font=("Bahnschrift SemiBold", 15, "bold")).pack(fill="x", padx=12, pady=(top, 2))

    def note(text, color=None, parent=None):
        lbl = ctk.CTkLabel(parent or body, text=text, anchor="w", justify="left", wraplength=600,
                           text_color=color or t["text_muted"], font=("Segoe UI", 12))
        lbl.pack(fill="x", padx=12, pady=(0, 4))
        return lbl

    heading("One Tofu for all your PCs", top=4)
    note("Servers always run on the PC they're installed on, with its own Tofu. Link the PCs and this Tofu - "
         "its window, web panel and Discord bot - shows and controls the servers on all of them.")

    # ------------------------------------------------------------ this PC
    heading("Let another Tofu control this PC")
    cfg = LP.host_config(mgr)
    on_var = ctk.BooleanVar(value=cfg["enabled"])
    ctk.CTkSwitch(body, text="Allow it (home network and Tailscale only, port %d)" % cfg["port"], variable=on_var,
                  command=lambda: toggle()).pack(anchor="w", padx=12, pady=(2, 4))
    host_box = ctk.CTkFrame(body, fg_color="transparent")
    host_box.pack(fill="x")

    def show_host():
        for w in host_box.winfo_children():
            w.destroy()
        if not on_var.get():
            note("Off. Turn it on, on each PC that hosts servers, then add it on your main PC.", parent=host_box)
            return
        addrs = LP.addresses()
        note("This PC's name in lists:  %s" % LP.pc_name(mgr), t["text_light"], parent=host_box)
        note("On your main PC: Settings > Linked PCs > Add a PC, and paste one of these codes "
             "(use the Tailscale one if the PCs aren't on the same network):", parent=host_box)
        for label, addr in addrs[:4]:
            row = ctk.CTkFrame(host_box, fg_color=t["bg_dark"], corner_radius=8)
            row.pack(fill="x", padx=12, pady=2)
            code = LP.pairing_code(mgr, addr)
            shown = code.rsplit("/", 1)[0] + "/" + "•" * 10
            ctk.CTkLabel(row, text="%s:  %s" % (label, shown), anchor="w", text_color=t["text_light"]).pack(
                side="left", padx=10, pady=6)
            ctk.CTkButton(row, text="Copy code", width=96, fg_color=t["accent_blue"],
                          command=lambda c=code: copy(c)).pack(side="right", padx=6)
        note("The code is a password for this PC - share it only with your own Tofu.", t["warning"],
             parent=host_box)
        row = ctk.CTkFrame(host_box, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(2, 6))
        ctk.CTkButton(row, text="Allow through Windows Firewall", width=230, fg_color=t["border"],
                      command=fw).pack(side="left")
        ctk.CTkButton(row, text="Make a new code (unlinks old ones)", width=250, fg_color="transparent",
                      border_width=1, border_color=t["border"], command=rotate).pack(side="left", padx=8)

    def copy(code):
        try:
            dlg.clipboard_clear()
            dlg.clipboard_append(code)
            app._show_status("Pairing code copied - paste it on your main PC")
        except Exception:
            pass

    def toggle():
        mgr.set_setting("link_enabled", bool(on_var.get()))
        if on_var.get():
            LP.host_key(mgr)
        show_host()
        app._show_status("Linked PCs: the Watchdog %s within 10 seconds" % (
            "starts answering" if on_var.get() else "stops answering"))

    def fw():
        from core.hosting import run_elevated
        ok = run_elevated(LP.firewall_commands(LP.host_config(mgr)["port"]))
        app._show_status("Firewall rule added" if ok else "Windows didn't add the firewall rule (prompt declined?)")

    def rotate():
        LP.new_key(mgr)
        show_host()
        app._show_status("New code made - PCs linked with the old one have to be added again")

    show_host()

    # ------------------------------------------------------------ other PCs
    heading("PCs this Tofu controls")
    pcs_box = ctk.CTkFrame(body, fg_color="transparent")
    pcs_box.pack(fill="x")

    def show_pcs(state=None):
        for w in pcs_box.winfo_children():
            w.destroy()
        rows = state if state is not None else fleet.pcs()
        if not rows:
            note("None yet.", parent=pcs_box)
            return
        for p in rows:
            row = ctk.CTkFrame(pcs_box, fg_color=t["bg_dark"], corner_radius=8)
            row.pack(fill="x", padx=12, pady=2)
            if state is None:
                info = "checking…"
                col = t["text_muted"]
            elif p.get("ok"):
                info = "online · %d server%s, %d running · Tofu %s" % (
                    p["servers"], "" if p["servers"] == 1 else "s", p["running"], p.get("version") or "?")
                col = t["status_online"]
            else:
                info = p.get("error") or "can't be reached"
                col = t["warning"]
            box = ctk.CTkFrame(row, fg_color="transparent")
            box.pack(side="left", fill="x", expand=True, padx=10, pady=6)
            ctk.CTkLabel(box, text="%s  (%s)" % (p["name"], p.get("address")), anchor="w",
                         text_color=t["text_light"]).pack(fill="x")
            ctk.CTkLabel(box, text=info, anchor="w", justify="left", wraplength=420, text_color=col).pack(fill="x")
            ctk.CTkButton(row, text="Remove", width=80, fg_color="transparent", border_width=1,
                          border_color=t["border"], command=lambda pid=p["id"]: remove(pid)).pack(side="right", padx=6)

    def check():
        def work():
            try:
                fleet.refresh()
                st = fleet.state(refresh=False)
            except Exception:
                st = None
            try:
                dlg.after(0, lambda: dlg.winfo_exists() and show_pcs(st))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def remove(pid):
        fleet.remove(pid)
        show_pcs()
        check()
        app._poll_linked_pcs()

    show_pcs()
    check()

    heading("Add a PC", top=14)
    note("Paste the pairing code from Settings > Linked PCs on the other PC.")
    row = ctk.CTkFrame(body, fg_color="transparent")
    row.pack(fill="x", padx=12, pady=(2, 2))
    code_var = ctk.StringVar(value="")
    name_var = ctk.StringVar(value="")
    ctk.CTkEntry(row, textvariable=code_var, show="•", width=330, placeholder_text="tofu://…").pack(side="left")
    ctk.CTkEntry(row, textvariable=name_var, width=140, placeholder_text="name (optional)").pack(side="left", padx=6)
    add_msg = note("")

    def add():
        code, name = code_var.get().strip(), name_var.get().strip()
        add_msg.configure(text="Connecting…", text_color=t["text_muted"])

        def work():
            try:
                r = fleet.add(code, name)
                text, ok = "✓ Linked %s - %d server%s." % (r["name"], r["servers"], "" if r["servers"] == 1 else "s"), True
            except Exception as e:
                text, ok = "⚠ %s" % e, False
            try:
                dlg.after(0, lambda: done(text, ok))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def done(text, ok):
        if not dlg.winfo_exists():
            return
        add_msg.configure(text=text, text_color=t["status_online"] if ok else t["warning"])
        if ok:
            code_var.set("")
            name_var.set("")
            check()
            app._poll_linked_pcs()
    ctk.CTkButton(row, text="Add", width=70, fg_color=t["accent_blue"], command=add).pack(side="left")

    ctk.CTkButton(dlg, text="Close", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=dlg.destroy).pack(side="right", padx=16, pady=(0, 14))
    return dlg
