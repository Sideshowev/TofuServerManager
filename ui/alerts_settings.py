"""Desktop dialog: which Discord alerts to send, and where."""

from __future__ import annotations

import threading

import customtkinter as ctk
import tkinter.messagebox as messagebox


def open_alerts_dialog(app, theme):
    from core.alerts import ALL_EVENTS, EVENT_LABELS, AlertCenter, valid_webhook
    mgr = app.manager
    dlg = ctk.CTkToplevel(app)
    dlg.title("Discord alerts")
    dlg.grab_set()
    dlg.transient(app)
    app.center_window(dlg, 600, 640, fit=True)
    form = ctk.CTkScrollableFrame(dlg, fg_color=theme["bg_card"], corner_radius=10)
    form.pack(fill="both", expand=True, padx=20, pady=(20, 0))

    def label(text, muted=False, bold=False, pady=(10, 0)):
        ctk.CTkLabel(form, text=text, anchor="w", justify="left", wraplength=520,
                     text_color=theme["text_muted"] if muted else theme["text_light"],
                     font=("Bahnschrift SemiBold", 14, "bold") if bold else None).pack(
            fill="x", padx=10, pady=pady)

    ctk.CTkLabel(form, text="Discord alerts", font=("Bahnschrift SemiBold", 18, "bold")).pack(pady=(12, 0))
    label("Get a Discord message when something goes wrong, even when nobody is looking at Tofu. "
          "Alerts are sent by the Watchdog (Tofu's background service).", muted=True)

    on_var = ctk.BooleanVar(value=mgr.get_setting("alerts_enabled", True) is not False)
    ctk.CTkCheckBox(form, text="Send alerts", variable=on_var,
                    fg_color=theme["accent_blue"]).pack(anchor="w", padx=10, pady=(12, 0))

    label("Where to send them", bold=True, pady=(16, 0))
    label("If the Tofu Discord bot is running, alerts go to its channel (set alert_channel_id in "
          "discord_config.json for a separate channel). Without the bot, paste a webhook: Discord "
          "channel > Edit Channel > Integrations > Webhooks > New Webhook > Copy Webhook URL.", muted=True)
    has_hook = bool(mgr.get_setting("alert_webhook_url", ""))
    label("Webhook URL" + ("  (saved - leave blank to keep it)" if has_hook else ""))
    hook_var = ctk.StringVar()
    ctk.CTkEntry(form, textvariable=hook_var, show="*").pack(fill="x", padx=10)
    clear_var = ctk.BooleanVar(value=False)
    if has_hook:
        ctk.CTkCheckBox(form, text="Remove the saved webhook", variable=clear_var,
                        fg_color=theme["accent_blue"]).pack(anchor="w", padx=10, pady=(4, 0))

    label("What to alert on", bold=True, pady=(16, 0))
    chosen = set(mgr.get_setting("alert_events", list(ALL_EVENTS)) or [])
    ev_vars = {}
    for ev in ALL_EVENTS:
        v = ctk.BooleanVar(value=ev in chosen)
        ev_vars[ev] = v
        ctk.CTkCheckBox(form, text=EVENT_LABELS[ev], variable=v,
                        fg_color=theme["accent_blue"]).pack(anchor="w", padx=10, pady=2)

    label("Alert when the whole PC's RAM use reaches (%)")
    ram_var = ctk.StringVar(value=str(mgr.get_setting("alert_ram_percent", 90)))
    ctk.CTkEntry(form, textvariable=ram_var, width=100).pack(anchor="w", padx=10)
    label("Alert when a server drive has less free space than (GB)")
    disk_var = ctk.StringVar(value=str(mgr.get_setting("alert_disk_gb", 5)))
    ctk.CTkEntry(form, textvariable=disk_var, width=100).pack(anchor="w", padx=10)
    label("Per-server RAM limits are in Edit Server > Advanced.", muted=True)

    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=20, pady=12)

    def collect():
        try:
            ram = max(10, min(100, int(float(ram_var.get()))))
            disk = max(0.0, float(disk_var.get()))
        except ValueError:
            messagebox.showerror("Alerts", "RAM % and free GB must be numbers.", parent=dlg)
            return None
        url = hook_var.get().strip()
        if url and not valid_webhook(url):
            messagebox.showerror("Alerts", "That doesn't look like a Discord webhook URL. It should "
                                 "start with https://discord.com/api/webhooks/", parent=dlg)
            return None
        return ram, disk, url

    def save(close=True):
        vals = collect()
        if vals is None:
            return False
        ram, disk, url = vals
        mgr.set_setting("alerts_enabled", bool(on_var.get()))
        mgr.set_setting("alert_events", [e for e, v in ev_vars.items() if v.get()])
        mgr.set_setting("alert_ram_percent", ram)
        mgr.set_setting("alert_disk_gb", disk)
        if url:
            mgr.set_setting("alert_webhook_url", url)
        elif clear_var.get():
            mgr.set_setting("alert_webhook_url", "")
        if close:
            dlg.destroy()
            app._show_status("Alert settings saved")
        return True

    def test():
        if not save(close=False):
            return
        test_btn.configure(state="disabled", text="Sending...")

        def work():
            client = mgr.agent_client()
            result = None
            if client is not None:
                resp = client.call("alerts.test", {}, timeout=30)
                result = resp.get("data") if resp.get("success") else {"sent": False, "reason": resp.get("error")}
            else:  # no agent: try the webhook directly from here
                result = AlertCenter(mgr).test()

            def show():
                try:
                    test_btn.configure(state="normal", text="Send test alert")
                except Exception:
                    pass
                if result and result.get("sent"):
                    messagebox.showinfo("Alerts", "Test alert sent - check Discord.", parent=dlg)
                else:
                    messagebox.showwarning("Alerts", "The test alert wasn't sent.\n\n"
                                           + str((result or {}).get("reason") or "Check the webhook URL "
                                                 "or that the bot is online (logs\\agent.log)."), parent=dlg)
            app.after(0, show)
        threading.Thread(target=work, daemon=True).start()

    ctk.CTkButton(btns, text="Save", command=save, fg_color=theme["accent_blue"]).pack(side="right", padx=4)
    ctk.CTkButton(btns, text="Cancel", command=dlg.destroy, fg_color="transparent", border_width=1,
                  border_color=theme["border"]).pack(side="right", padx=4)
    test_btn = ctk.CTkButton(btns, text="Send test alert", command=test, fg_color="transparent",
                             border_width=1, border_color=theme["border"])
    test_btn.pack(side="left", padx=4)
