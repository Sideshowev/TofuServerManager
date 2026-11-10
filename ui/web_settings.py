"""Desktop dialog that sets up the browser web panel (Discord login over Tailscale)."""

from __future__ import annotations

import json
import re
import threading
import time
import webbrowser
from pathlib import Path

import customtkinter as ctk
import tkinter.messagebox as messagebox

STEPS = (
    "1. Install Tailscale on this PC and sign in (tailscale.com/download).\n"
    "2. In the Tailscale admin console, share this PC with each helper (Machines > ... > Share), "
    "or invite them to your tailnet. They install Tailscale and accept.\n"
    "3. discord.com/developers/applications > your bot's app > OAuth2: copy the Client ID, "
    "reset/copy the Client Secret, and add the Redirect URL shown below.\n"
    "4. In Discord turn on Developer Mode (Settings > Advanced), then right-click your server "
    "and roles to Copy ID.\n"
    "5. Save. Helpers open the Panel URL in any browser and press Log in with Discord."
)


def _ids(text: str) -> list:
    return [t for t in re.split(r"[\s,;]+", text or "") if t.isdigit()]


def _read_cfg(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def open_web_panel_dialog(app, theme, base_dir):
    from core.web_panel import DEFAULT_PORT, tailscale_info
    mgr = app.manager
    cfg_path = Path(base_dir) / "discord_config.json"
    cfg = _read_cfg(cfg_path)
    has_secret = bool(cfg.get("oauth_client_secret") or cfg.get("oauth_client_secret_protected"))

    dlg = ctk.CTkToplevel(app)
    dlg.title("Web Panel")
    dlg.grab_set()
    dlg.transient(app)
    app.center_window(dlg, 680, 720, fit=True)
    form = ctk.CTkScrollableFrame(dlg, fg_color=theme["bg_card"], corner_radius=10)
    form.pack(fill="both", expand=True, padx=20, pady=(20, 0))

    def heading(text):
        ctk.CTkLabel(form, text=text, anchor="w", text_color=theme["text_light"],
                     font=("Bahnschrift SemiBold", 14, "bold")).pack(fill="x", padx=10, pady=(16, 2))

    def note(text):
        ctk.CTkLabel(form, text=text, anchor="w", justify="left", wraplength=600,
                     text_color=theme["text_muted"]).pack(fill="x", padx=10, pady=(2, 4))

    def field(label, var, ph="", show=None):
        ctk.CTkLabel(form, text=label, anchor="w", text_color=theme["text_light"]).pack(
            fill="x", padx=10, pady=(8, 0))
        e = ctk.CTkEntry(form, textvariable=var, placeholder_text=ph, show=show)
        e.pack(fill="x", padx=10, pady=(0, 2))
        return e

    ctk.CTkLabel(form, text="Web Panel", font=("Bahnschrift SemiBold", 18, "bold")).pack(pady=(12, 0))
    note("Let other admins in your Discord guild manage servers from any PC with a browser. "
         "Only devices on your Tailscale network can reach it, and everyone signs in with Discord.")

    enabled_var = ctk.BooleanVar(value=mgr.get_setting("web_panel_enabled", False) is True)
    ctk.CTkCheckBox(form, text="Turn on the web panel", variable=enabled_var,
                    fg_color=theme["accent_blue"]).pack(fill="x", padx=10, pady=(12, 2))
    port_var = ctk.StringVar(value=str(mgr.get_setting("web_panel_port", DEFAULT_PORT) or DEFAULT_PORT))
    field("Port", port_var, str(DEFAULT_PORT))
    lan_var = ctk.BooleanVar(value=mgr.get_setting("web_panel_allow_lan", False) is True)
    ctk.CTkCheckBox(form, text="Also allow PCs on my home network (no Tailscale needed at home)",
                    variable=lan_var, fg_color=theme["accent_blue"]).pack(fill="x", padx=10, pady=(8, 0))
    note("Only private addresses (192.168.x, 10.x) get in - never the internet - and everyone still "
         "signs in with Discord. Use this PC's home address as the Panel URL (Detect fills it in).")

    heading("Address")
    url_var = ctk.StringVar(value=cfg.get("panel_url", ""))
    row = ctk.CTkFrame(form, fg_color="transparent")
    ctk.CTkLabel(form, text="Panel URL (what helpers type in their browser)", anchor="w",
                 text_color=theme["text_light"]).pack(fill="x", padx=10, pady=(8, 0))
    row.pack(fill="x", padx=10)
    ctk.CTkEntry(row, textvariable=url_var, placeholder_text="http://my-pc.tail1234.ts.net:8765").pack(
        side="left", fill="x", expand=True)
    detect_btn = ctk.CTkButton(row, text="Detect", width=80)
    detect_btn.pack(side="left", padx=(8, 0))
    ts_label = ctk.CTkLabel(form, text="", anchor="w", text_color=theme["text_muted"])
    ts_label.pack(fill="x", padx=10)

    def detect():
        detect_btn.configure(state="disabled", text="...")

        def work():
            ip, dns = tailscale_info()
            lan = None
            if not (ip or dns) and lan_var.get():
                try:
                    from core.network import lan_ips
                    lan = (lan_ips() or [None])[0]
                except Exception:
                    lan = None

            def show():
                detect_btn.configure(state="normal", text="Detect")
                if not (ip or dns) and lan:
                    try:
                        port = int(port_var.get())
                    except ValueError:
                        port = DEFAULT_PORT
                    url_var.set("http://%s:%d" % (lan, port))
                    ts_label.configure(text="Home network address: %s (Tailscale isn't running)" % lan)
                    return
                if not (ip or dns):
                    ts_label.configure(text="Tailscale isn't running on this PC. Install it and sign in first, "
                                            "or tick \"Also allow PCs on my home network\".")
                    return
                try:
                    port = int(port_var.get())
                except ValueError:
                    port = DEFAULT_PORT
                url_var.set("http://%s:%d" % (dns or ip, port))
                ts_label.configure(text="Tailscale found: %s" % (dns or ip)
                                   + ("" if dns else "  (turn on MagicDNS for a friendlier name)"))
            app.after(0, show)
        threading.Thread(target=work, daemon=True).start()
    detect_btn.configure(command=detect)

    redirect_var = ctk.StringVar()
    ctk.CTkLabel(form, text="Redirect URL (paste into Discord Developer Portal > OAuth2 > Redirects)",
                 anchor="w", text_color=theme["text_light"]).pack(fill="x", padx=10, pady=(8, 0))
    rrow = ctk.CTkFrame(form, fg_color="transparent")
    rrow.pack(fill="x", padx=10)
    ctk.CTkEntry(rrow, textvariable=redirect_var, state="readonly").pack(side="left", fill="x", expand=True)

    def copy_redirect():
        dlg.clipboard_clear()
        dlg.clipboard_append(redirect_var.get())
        app._show_status("Redirect URL copied")
    ctk.CTkButton(rrow, text="Copy", width=80, command=copy_redirect).pack(side="left", padx=(8, 0))

    def sync_redirect(*_):
        u = url_var.get().strip().rstrip("/")
        redirect_var.set(u + "/auth/callback" if u else "")
    url_var.trace_add("write", sync_redirect)
    sync_redirect()

    heading("Discord login")
    id_var = ctk.StringVar(value=str(cfg.get("oauth_client_id", "") or ""))
    field("Client ID (OAuth2 page of your Discord application)", id_var)
    secret_var = ctk.StringVar()
    field("Client Secret" + ("  (saved - leave blank to keep it)" if has_secret else ""), secret_var, show="*")
    guild_var = ctk.StringVar(value=str(cfg.get("guild_id", "") or ""))
    field("Discord server (guild) ID", guild_var)

    heading("Who can get in")
    # First time: suggest the bot's admins (nothing is granted until you press Save).
    admin_ids_var = ctk.StringVar(value=", ".join(map(str, cfg.get("web_admin_ids")
                                                   or ([] if "web_admin_ids" in cfg else cfg.get("admin_ids") or []))))
    field("Admin user IDs (full control, comma separated)", admin_ids_var, "e.g. your own Discord user ID")
    admin_roles_var = ctk.StringVar(value=", ".join(map(str, cfg.get("web_admin_role_ids") or [])))
    field("Admin role IDs (members with these roles get full control)", admin_roles_var)
    op_roles_var = ctk.StringVar(value=", ".join(map(str, cfg.get("web_operator_role_ids") or [])))
    field("Operator role IDs (start/stop/restart and console only)", op_roles_var)
    note("Admins can do everything the panel offers: start/stop, console, configs, schedules, "
         "backups and restores, updates, adding and removing servers. Server programs, folders, "
         "Steam login and tokens can only be changed here on this PC.")

    heading("Setup steps")
    note(STEPS)

    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=20, pady=12)

    def save():
        try:
            port = int(port_var.get().strip() or DEFAULT_PORT)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("Web Panel", "The port must be a number between 1 and 65535.", parent=dlg)
            return
        url = url_var.get().strip().rstrip("/")
        if enabled_var.get() and not url.startswith(("http://", "https://")):
            messagebox.showerror("Web Panel", "Set the Panel URL first (press Detect).", parent=dlg)
            return
        new = _read_cfg(cfg_path)
        new.update({
            "oauth_client_id": id_var.get().strip(),
            "guild_id": guild_var.get().strip(),
            "panel_url": url,
            "web_admin_ids": _ids(admin_ids_var.get()),
            "web_admin_role_ids": _ids(admin_roles_var.get()),
            "web_operator_role_ids": _ids(op_roles_var.get()),
        })
        sec = secret_var.get().strip()
        if sec:
            try:
                from core.secrets import protect
                new["oauth_client_secret_protected"] = protect(sec)
                new["oauth_client_secret"] = ""
            except Exception as e:
                messagebox.showerror("Web Panel", "Could not encrypt the secret: %s" % e, parent=dlg)
                return
        try:
            from core.paths import atomic_write_json
            atomic_write_json(cfg_path, new)
            mgr.set_setting("web_panel_enabled", bool(enabled_var.get()))
            mgr.set_setting("web_panel_port", port)
            mgr.set_setting("web_panel_allow_lan", bool(lan_var.get()))
        except Exception as e:
            messagebox.showerror("Web Panel", "Could not save: %s" % e, parent=dlg)
            return
        dlg.destroy()
        if enabled_var.get():
            from core.web_auth import DiscordAuth
            probs = DiscordAuth(base_dir).problems()
            if probs:
                messagebox.showwarning("Web Panel", "Saved, but login won't work until these are set:\n\n- "
                                       + "\n- ".join(probs), parent=app)
        restart_agent(app)

    ctk.CTkButton(btns, text="Save & apply", command=save,
                  fg_color=theme["accent_blue"]).pack(side="right", padx=4)
    ctk.CTkButton(btns, text="Cancel", command=dlg.destroy, fg_color="transparent",
                  border_width=1, border_color=theme["border"]).pack(side="right", padx=4)

    def open_panel():
        u = url_var.get().strip()
        if u:
            webbrowser.open(u)
    ctk.CTkButton(btns, text="Open panel", command=open_panel, fg_color="transparent",
                  border_width=1, border_color=theme["border"]).pack(side="left", padx=4)

    def status_page():
        from ui.status_page_dialog import open_status_page_dialog
        try:
            dlg.grab_release()
        except Exception:
            pass
        open_status_page_dialog(app, theme)
    ctk.CTkButton(btns, text="Public status page...", command=status_page, fg_color="transparent",
                  border_width=1, border_color=theme["border"]).pack(side="left", padx=4)


def restart_agent(app):
    """Apply new web panel settings in the background agent.

    A running agent reloads just the web panel (servers and their consoles are
    untouched). If no agent is running, one is started."""
    mgr = app.manager

    def wait_for(up, secs):
        deadline = time.time() + secs
        while time.time() < deadline:
            mgr._agent_checked = 0
            if mgr.agent_available() == up:
                return True
            time.sleep(0.5)
        return False

    def run():
        client = mgr.agent_client()
        if client is not None:
            resp = client.call("agent.web_reload", {}, timeout=20)
            if resp.get("success"):
                return (resp.get("data") or {}).get("url") or "off"
            # Older agent without live reload: restart it (servers keep running).
            client.call("agent.shutdown", {"stop_servers": False}, timeout=15)
            wait_for(False, 20)
        if not app._spawn_agent():
            raise RuntimeError("Could not start the Watchdog.")
        if not wait_for(True, 20):
            raise RuntimeError("The background agent did not respond. See logs\\agent.log.")
        return "restarted"

    def done(r):
        if r == "off":
            app._show_status("Web panel is off")
        elif r == "restarted":
            app._show_status("Watchdog restarted - web panel settings applied")
        else:
            app._show_status("Web panel running at " + r)

    app._run_bg("Applying web panel settings...", run, on_done=done,
                on_error=lambda e: messagebox.showerror("Web Panel", str(e)))
