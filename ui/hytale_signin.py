"""Desktop "Sign the Hytale server in" window: asks the running server for a
device sign-in link (ServerManager.hytale_sign_in) and shows the link and code
big, with buttons to open the page and copy the code. Optionally starts the
server first and waits for it to boot."""

from __future__ import annotations

import threading
import time
import webbrowser

import customtkinter as ctk


def open_hytale_signin(app, theme, server_id, start_first=False):
    t = theme
    mgr = app.manager
    srv = mgr.get_server(server_id) or {}
    dlg = ctk.CTkToplevel(app)
    dlg.title("Sign in %s to Hytale" % srv.get("name", "the server"))
    dlg.transient(app)
    app.center_window(dlg, 520, 340, fit=True)
    body = ctk.CTkFrame(dlg, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=16)
    ctk.CTkLabel(body, text="Hytale servers have to be linked to a Hytale account once before players can join. "
                 "BaoBun asks the server for a sign-in link; open it, sign in with your Hytale account and enter "
                 "the code. The sign-in is kept, encrypted, in the server's folder.",
                 anchor="w", justify="left", wraplength=460, text_color=t["text_muted"]).pack(fill="x", padx=14,
                                                                                              pady=(12, 8))
    msg = ctk.CTkLabel(body, text="Working...", anchor="w", justify="left", wraplength=460,
                       text_color=t["text_light"])
    msg.pack(fill="x", padx=14)
    code_lbl = ctk.CTkLabel(body, text="", font=("Consolas", 26, "bold"), text_color=t["accent_blue"])
    code_lbl.pack(pady=8)
    row = ctk.CTkFrame(body, fg_color="transparent")
    row.pack(pady=(0, 10))
    open_btn = ctk.CTkButton(row, text="Open sign-in page", width=160, fg_color=t["success"], state="disabled")
    open_btn.pack(side="left", padx=4)
    copy_btn = ctk.CTkButton(row, text="Copy code", width=110, fg_color=t["border"], state="disabled")
    copy_btn.pack(side="left", padx=4)
    again_btn = ctk.CTkButton(row, text="Try again", width=100, fg_color="transparent", border_width=1,
                              border_color=t["border"], command=lambda: go(False))
    again_btn.pack(side="left", padx=4)

    def show(res, err):
        if not dlg.winfo_exists():
            return
        if err:
            msg.configure(text=err, text_color=t["danger"])
            return
        if res.get("url"):
            msg.configure(text="Open %s and enter this code:" % res["url"] if res.get("code") else
                          "Open %s and sign in." % res["url"], text_color=t["text_light"])
            code_lbl.configure(text=res.get("code") or "")
            open_btn.configure(state="normal", command=lambda: webbrowser.open(res["url"]))
            if res.get("code"):
                copy_btn.configure(state="normal", command=lambda: (app.clipboard_clear(),
                                                                    app.clipboard_append(res["code"])))
        else:
            msg.configure(text="Sent /auth login device to the server, but no link showed up in its log yet. "
                          "Look at the console for the link and code, or press Try again.",
                          text_color=t["warning"])

    def go(start):
        msg.configure(text="Starting the server..." if start else "Asking the server for a sign-in link...",
                      text_color=t["text_light"])

        def work():
            try:
                if start and not mgr.is_running(server_id):
                    app.after(0, lambda: app.start_server(server_id))
                    end = time.time() + 120
                    while time.time() < end and not mgr.is_running(server_id):
                        time.sleep(2)
                    if not mgr.is_running(server_id):
                        raise RuntimeError("The server didn't start - check its console.")
                    app.after(0, lambda: msg.configure(text="Waiting for the server to finish loading..."))
                    time.sleep(20)
                res, err = mgr.hytale_sign_in(server_id, user="gui"), None
            except Exception as e:
                res, err = None, str(e)
            app.after(0, lambda: show(res, err))
        threading.Thread(target=work, daemon=True).start()

    go(start_first)
    return dlg
