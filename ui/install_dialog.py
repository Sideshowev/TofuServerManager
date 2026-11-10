"""Desktop dialog: download a Vintage Story or Minecraft server (and Java).

Steam games keep using SteamCMD; this covers the games that come from their
own websites. Progress is read from <server folder>/install.log.
"""

from __future__ import annotations

import os
import threading

import customtkinter as ctk
from ui.app_common import later


def open_install_dialog(app, theme, server_id, on_finished=None):
    from core import game_installers as gi
    from core.hosting import parse_steamcmd_progress
    t = theme
    mgr = app.manager
    srv = mgr.get_server(server_id) or {}
    opts = gi.options_for(srv)
    k = opts.get("kind")
    if k == "hytale":
        return open_hytale_install(app, theme, server_id, opts, on_finished)
    if k not in ("vintage-story", "minecraft"):
        return False
    if mgr.is_running(server_id):
        app.show_error("Install", "Stop %s first." % srv.get("name", "the server"))
        return True

    dlg = ctk.CTkToplevel(app)
    dlg.title("Install " + ("Vintage Story" if k == "vintage-story" else "Minecraft") + " server")
    dlg.transient(app)
    app.center_window(dlg, 560, 470, fit=True)
    body = ctk.CTkFrame(dlg, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=(16, 6))
    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=16, pady=(4, 14))

    def label(text, muted=False, pad=(12, 0)):
        lb = ctk.CTkLabel(body, text=text, anchor="w", justify="left", wraplength=500,
                          text_color=t["text_muted"] if muted else t["text_light"])
        lb.pack(fill="x", padx=16, pady=pad)
        return lb

    label("Folder: " + (srv.get("directory") or "?"), muted=True)
    flavor_var = ctk.StringVar(value="")
    version_var = ctk.StringVar(value="Newest")
    eula_var = ctk.BooleanVar(value=False)
    flavor_ids = {}
    if k == "minecraft":
        label("Server type")
        flavor_ids = {f["label"]: f["id"] for f in opts["flavors"]}
        cur = next((f["label"] for f in opts["flavors"] if f["id"] == opts.get("flavor")), opts["flavors"][0]["label"])
        flavor_var.set(cur)
        ctk.CTkOptionMenu(body, values=list(flavor_ids), variable=flavor_var, width=260,
                          fg_color=t["border"], command=lambda _v: load_versions()).pack(anchor="w", padx=16)
        label("Minecraft version")
    else:
        if opts.get("state") == "vanilla":
            label("Installed: %s. Installing again updates the game files; worlds, mods and "
                  "settings in the data folder are kept." % (mgr.vs_server_version(server_id) or "unknown"),
                  muted=True)
        label("Version")
    version_menu = ctk.CTkOptionMenu(body, values=["Newest"], variable=version_var, width=260, fg_color=t["border"])
    version_menu.pack(anchor="w", padx=16)
    ver_note = label("Loading versions...", muted=True, pad=(2, 0))
    if k == "minecraft" and opts.get("needsEula"):
        ctk.CTkCheckBox(body, text="I agree to the Minecraft EULA (aka.ms/MinecraftEULA)", variable=eula_var,
                        fg_color=t["accent_blue"]).pack(anchor="w", padx=16, pady=(14, 0))
    progress = ctk.CTkProgressBar(body, progress_color=t["accent_blue"])
    progress.set(0)
    prog_lbl = ctk.CTkLabel(body, text="", anchor="w", justify="left", wraplength=500, text_color=t["text_muted"])
    state = {"busy": False}

    def load_versions():
        flav = flavor_ids.get(flavor_var.get(), "vanilla")
        ver_note.configure(text="Loading versions...")

        def work():
            try:
                if k == "minecraft":
                    vs = gi.mc_versions(flav, limit=40)
                else:
                    vs = [r["version"] for r in gi.vs_releases("stable")][:40]
                err = None
            except Exception as e:
                vs, err = [], str(e)

            def show():
                if not dlg.winfo_exists():
                    return
                version_menu.configure(values=["Newest"] + vs)
                if version_var.get() not in ["Newest"] + vs:
                    version_var.set("Newest")
                ver_note.configure(text=("Newest is %s." % vs[0]) if vs else ("Couldn't load versions: %s" % err
                                                                              if err else ""))
            later(app, show)
        threading.Thread(target=work, daemon=True).start()

    def go():
        if state["busy"]:
            return
        if k == "minecraft" and opts.get("needsEula") and not eula_var.get():
            prog_lbl.pack(fill="x", padx=16, pady=(8, 0))
            prog_lbl.configure(text="Tick the EULA box first - Minecraft servers won't run without it.",
                               text_color=t["warning"])
            return
        state["busy"] = True
        go_btn.configure(state="disabled", text="Installing...")
        progress.pack(fill="x", padx=16, pady=(16, 4))
        prog_lbl.pack(fill="x", padx=16)
        prog_lbl.configure(text="Starting...", text_color=t["text_muted"])
        o = {"version": None if version_var.get() == "Newest" else version_var.get(),
             "accept_eula": bool(eula_var.get())}
        if k == "minecraft":
            o["flavor"] = flavor_ids.get(flavor_var.get(), "vanilla")
        result = {}

        def work():
            try:
                result["ok"] = mgr.install_game(server_id, o, user="gui")
            except Exception as e:
                result["error"] = str(e)
        th = threading.Thread(target=work, daemon=True)
        th.start()
        log = os.path.join(srv.get("directory") or "", gi.INSTALL_LOG)

        def poll():
            if not dlg.winfo_exists():
                return
            try:
                with open(log, "rb") as f:
                    f.seek(max(0, os.path.getsize(log) - 4096))
                    pct, phase = parse_steamcmd_progress(f.read().decode("utf-8", "replace"))
            except OSError:
                pct, phase = None, None
            if pct is not None:
                progress.set(pct / 100.0)
                prog_lbl.configure(text="%s... %.0f%%" % ((phase or "working").capitalize(), pct))
            if th.is_alive():
                dlg.after(700, poll)
                return
            state["busy"] = False
            if "error" in result:
                progress.set(0)
                prog_lbl.configure(text="Install failed: " + result["error"], text_color=t["danger"])
                go_btn.configure(state="normal", text="Try again")
                return
            res = result["ok"]
            progress.set(1)
            msg = "Installed %s." % (res.get("version") or "")
            if res.get("notes"):
                msg += "\n\n" + "\n".join("• " + n for n in res["notes"])
            prog_lbl.configure(text=msg, text_color=t["text_light"])
            go_btn.configure(state="normal", text="Start now",
                             command=lambda: (dlg.destroy(), app.start_server(server_id)))
            try:
                app.refresh_servers()
            except Exception:
                pass
            if on_finished:
                on_finished(res)
        dlg.after(700, poll)

    go_btn = ctk.CTkButton(btns, text="Install", fg_color=t["accent_blue"], command=go)
    go_btn.pack(side="right")
    ctk.CTkButton(btns, text="Close", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=lambda: None if state["busy"] else dlg.destroy()).pack(side="right", padx=6)
    load_versions()
    return True


def open_hytale_install(app, theme, server_id, opts, on_finished=None):
    """Hytale: Java 25 + Hytale's downloader (sign-in link shown here) + sign the server in."""
    import webbrowser
    from core import game_installers as gi
    from core.hosting import parse_steamcmd_progress
    from core.hytale_install import last_signin
    t = theme
    mgr = app.manager
    srv = mgr.get_server(server_id) or {}
    running = mgr.is_running(server_id)
    dlg = ctk.CTkToplevel(app)
    dlg.title("Hytale server - " + srv.get("name", ""))
    dlg.transient(app)
    app.center_window(dlg, 580, 470, fit=True)
    body = ctk.CTkFrame(dlg, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=(16, 6))
    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=16, pady=(4, 14))

    def label(text, color=None, pad=(10, 0), **kw):
        lb = ctk.CTkLabel(body, text=text, anchor="w", justify="left", wraplength=520,
                          text_color=color or t["text_muted"], **kw)
        lb.pack(fill="x", padx=16, pady=pad)
        return lb

    label("Folder: " + (srv.get("directory") or "?"))
    label("Install / Update gets Java 25 if needed and runs Hytale's own downloader. The first time it shows a "
          "link and a code: open the link, sign in with your Hytale account and type the code - BaoBun never "
          "sees your password. Updating keeps the server's settings, worlds, mods and sign-in.",
          color=t["text_light"])
    if opts.get("signedIn"):
        label("\u2705 The server is signed in to Hytale.", color=t["status_online"])
    else:
        label("After installing, the server itself must be signed in once (Sign the server in) before players can "
              "join.", color=t["warning"])
    progress = ctk.CTkProgressBar(body, progress_color=t["accent_blue"])
    progress.set(0)
    prog_lbl = label("Stop the server to install or update." if running else "")
    si_box = ctk.CTkFrame(body, fg_color=t["bg_dark"], corner_radius=8)
    si_lbl = ctk.CTkLabel(si_box, text="", anchor="w", justify="left", wraplength=500, text_color=t["text_light"])
    si_lbl.pack(fill="x", padx=12, pady=(10, 2))
    si_code = ctk.CTkLabel(si_box, text="", font=("Consolas", 24, "bold"), text_color=t["accent_blue"])
    si_code.pack(pady=2)
    si_btn = ctk.CTkButton(si_box, text="Open sign-in page", fg_color=t["success"], width=170)
    si_btn.pack(pady=(2, 10))
    state = {"busy": False, "shown": None}

    def show_signin(si):
        if not si or si == state["shown"] or not si["url"].startswith("https://"):
            return
        state["shown"] = si
        si_box.pack(fill="x", padx=16, pady=(12, 0))
        si_lbl.configure(text="Sign in to Hytale so the downloader can fetch the server: open the page and "
                         "enter this code.")
        si_code.configure(text=si.get("code") or "")
        si_btn.configure(command=lambda: webbrowser.open(si["url"]))
        try:
            webbrowser.open(si["url"])
        except Exception:
            pass

    def go():
        if state["busy"]:
            return
        if mgr.is_running(server_id):
            prog_lbl.configure(text="Stop the server first.", text_color=t["warning"])
            return
        state["busy"] = True
        go_btn.configure(state="disabled", text="Installing...")
        progress.pack(fill="x", padx=16, pady=(14, 4), before=prog_lbl)
        prog_lbl.configure(text="Starting...", text_color=t["text_muted"])
        result = {}

        def work():
            try:
                result["ok"] = mgr.install_game(server_id, {}, user="gui")
            except Exception as e:
                result["error"] = str(e)
        th = threading.Thread(target=work, daemon=True)
        th.start()
        log = os.path.join(srv.get("directory") or "", gi.INSTALL_LOG)

        def poll():
            if not dlg.winfo_exists():
                return
            try:
                with open(log, "rb") as f:
                    f.seek(max(0, os.path.getsize(log) - 16384))
                    text = f.read().decode("utf-8", "replace")
                pct, phase = parse_steamcmd_progress(text)
                if th.is_alive():
                    show_signin(last_signin(text))
            except OSError:
                pct, phase = None, None
            if pct is not None:
                progress.set(pct / 100.0)
                prog_lbl.configure(text="%s... %.0f%%" % ((phase or "working").capitalize(), pct))
            if th.is_alive():
                dlg.after(700, poll)
                return
            state["busy"] = False
            si_box.pack_forget()
            if "error" in result:
                progress.set(0)
                prog_lbl.configure(text="Install failed: " + result["error"], text_color=t["danger"])
                go_btn.configure(state="normal", text="Try again")
                return
            res = result["ok"]
            progress.set(1)
            msg = "Installed Hytale %s." % (res.get("version") or "")
            if res.get("notes"):
                msg += "\n\n" + "\n".join("\u2022 " + n for n in res["notes"])
            prog_lbl.configure(text=msg, text_color=t["text_light"])
            go_btn.configure(state="normal", text="Install / Update")
            try:
                app.refresh_servers()
            except Exception:
                pass
            if on_finished:
                on_finished(res)
        dlg.after(700, poll)

    def sign_in():
        from ui.hytale_signin import open_hytale_signin
        open_hytale_signin(app, t, server_id, start_first=not mgr.is_running(server_id))

    go_btn = ctk.CTkButton(btns, text="Install / Update", fg_color=t["accent_blue"], command=go,
                           state="disabled" if running else "normal")
    go_btn.pack(side="right")
    ctk.CTkButton(btns, text="Sign the server in", fg_color=t["border"], command=sign_in).pack(side="right", padx=6)
    ctk.CTkButton(btns, text="Close", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=lambda: None if state["busy"] else dlg.destroy()).pack(side="left")
    return True
