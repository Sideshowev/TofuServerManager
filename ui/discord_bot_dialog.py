"""Desktop "Discord bot" window: set the bot up without editing files.

Paste the bot token (kept encrypted in settings.json, never shown again), invite
the bot to your Discord server with one button, then pick channels and roles in
Discord with /baobun-setup. An older discord_config.json token keeps working
and is never changed here.
"""

from __future__ import annotations

import json
import threading
import webbrowser

import customtkinter as ctk

from core import secrets as S

PERMISSIONS = 126016     # view, send, embed, attach, read history, react, manage messages (tidy the panel)
PORTAL = "https://discord.com/developers/applications"


def invite_url(app_id) -> str:
    return ("https://discord.com/oauth2/authorize?client_id=%s&scope=bot+applications.commands&permissions=%d"
            % (app_id, PERMISSIONS))


def token_source(mgr) -> str:
    """"config" (discord_config.json), "settings" (pasted here) or ""."""
    base = mgr.config_path.parent
    try:
        raw = json.loads((base / "discord_config.json").read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if S.discord_token(raw):
        return "config"
    return "settings" if S.bot_token(base, {}) else ""


def save_token(mgr, token: str) -> str:
    """Store a pasted token (encrypted). Returns an error message or ''."""
    token = (token or "").strip().strip('"')
    if not S.looks_like_bot_token(token):
        return ("That doesn't look like a bot token. In the Discord developer portal open your app > Bot > "
                "Reset Token, then copy the long text with two dots in it.")
    mgr.set_setting(S.SETTINGS_TOKEN_KEY, token)
    return ""


def reconnect(app) -> str:
    """Start / restart the bot wherever it runs. Returns a status line."""
    mgr = app.manager
    try:
        if mgr.agent_available():
            r = mgr._forward("discord.reload", timeout=40) or {}
            return "The Watchdog is connecting the bot." if r.get("running") else \
                "The Watchdog couldn't start the bot - see logs\\agent.log."
        import discord_bot
        discord_bot.restart_bot(app_instance=app)
        return "Connecting the bot from this window (turn the Watchdog on so it keeps running)."
    except Exception as e:
        return "Couldn't restart the bot: %s" % e


def open_discord_bot_dialog(app, theme):
    t = theme
    mgr = app.manager
    dlg = ctk.CTkToplevel(app)
    dlg.title("Discord bot")
    dlg.transient(app)
    app.center_window(dlg, 640, 600, fit=True)
    body = ctk.CTkScrollableFrame(dlg, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=(16, 6))

    def heading(text, top=12):
        ctk.CTkLabel(body, text=text, anchor="w", text_color=t["text_light"],
                     font=("Bahnschrift SemiBold", 15, "bold")).pack(fill="x", padx=12, pady=(top, 2))

    def note(text, color=None):
        lbl = ctk.CTkLabel(body, text=text, anchor="w", justify="left", wraplength=570,
                           text_color=color or t["text_muted"], font=("Segoe UI", 12))
        lbl.pack(fill="x", padx=12, pady=(0, 4))
        return lbl

    heading("Run your servers from Discord", top=4)
    note("The bot posts a control panel where friends can see and start servers, sends crash and backup alerts, "
         "and handles game-night events, mod lists and stats. It runs inside BaoBun - nothing to host.")

    heading("1. Make a bot")
    note("In the Discord developer portal: New Application > give it a name > Bot > Reset Token > Copy. "
         "Keep the token secret - it is the bot's password.")
    ctk.CTkButton(body, text="Open the developer portal", width=220, fg_color=t["border"],
                  command=lambda: webbrowser.open(PORTAL)).pack(anchor="w", padx=12, pady=(0, 6))

    heading("2. Paste the token")
    src = token_source(mgr)
    state = note({"config": "✅ A token is set in discord_config.json (older setup) - it keeps working.",
                  "settings": "✅ A token is saved (encrypted). Paste a new one only to replace it.",
                  "": "No token yet."}[src], t["status_online"] if src else t["text_muted"])
    row = ctk.CTkFrame(body, fg_color="transparent")
    row.pack(fill="x", padx=12, pady=(2, 4))
    tok_var = ctk.StringVar(value="")
    ctk.CTkEntry(row, textvariable=tok_var, show="•", width=380,
                 placeholder_text="paste the bot token here").pack(side="left")
    msg = note("")

    def save():
        err = save_token(mgr, tok_var.get())
        if err:
            msg.configure(text=err, text_color=t["warning"])
            return
        tok_var.set("")
        msg.configure(text="Saved. Connecting…", text_color=t["text_muted"])
        state.configure(text="✅ A token is saved (encrypted).", text_color=t["status_online"])
        show_invite()

        def work():
            text = reconnect(app)
            try:
                app.after(0, lambda: msg.configure(text="Saved. " + text))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()
    ctk.CTkButton(row, text="Save", width=80, fg_color=t["accent_blue"], command=save).pack(side="left", padx=8)
    if src == "settings":
        def remove():
            mgr.set_setting(S.SETTINGS_TOKEN_KEY, "")
            state.configure(text="Token removed. The bot stops when BaoBun or the Watchdog restarts.",
                            text_color=t["text_muted"])
        ctk.CTkButton(row, text="Remove", width=80, fg_color="transparent", border_width=1,
                      border_color=t["border"], command=remove).pack(side="left")

    heading("3. Invite it to your Discord server")
    invite_box = ctk.CTkFrame(body, fg_color="transparent")
    invite_box.pack(fill="x", padx=0)

    def show_invite():
        for w in invite_box.winfo_children():
            w.destroy()
        app_id = S.token_app_id(S.bot_token(mgr.config_path.parent, _raw(mgr)))
        if not app_id:
            ctk.CTkLabel(invite_box, text="Save a token first.", anchor="w", text_color=t["text_muted"]).pack(
                fill="x", padx=12)
            return
        ctk.CTkButton(invite_box, text="Invite the bot", width=220, fg_color=t["success"],
                      command=lambda: webbrowser.open(invite_url(app_id))).pack(anchor="w", padx=12, pady=(2, 2))
        ctk.CTkLabel(invite_box, text="Pick your Discord server and press Authorize (you need Manage Server there).",
                     anchor="w", justify="left", wraplength=570, text_color=t["text_muted"]).pack(fill="x", padx=12)
    show_invite()

    heading("4. Pick channels in Discord")
    note("In your Discord server type /baobun-setup and choose the control panel channel, where alerts and the "
         "activity feed go, and who may start servers. The Discord server's owner and anyone with Manage Server "
         "count as BaoBun admins. Everything else can be changed there later.")
    ctk.CTkButton(dlg, text="Close", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=dlg.destroy).pack(side="right", padx=16, pady=(0, 14))
    return dlg


def _raw(mgr) -> dict:
    try:
        return json.loads((mgr.config_path.parent / "discord_config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
