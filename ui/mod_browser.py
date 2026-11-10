"""Desktop mod browser: search a server's mod site and see results as cards with
their icons, then open a mod for its pictures, description, versions and links,
and install it. Uses ModManager.search / details (same data as the web panel) and
core.img_cache for the pictures. Works without Pillow - just no pictures."""

from __future__ import annotations

import threading
import webbrowser
from datetime import datetime

import customtkinter as ctk

try:
    from PIL import Image
except Exception:       # pragma: no cover - Pillow ships with the .exe
    Image = None

SORT_LABELS = [("popular", "Most downloaded"), ("trending", "Trending"), ("updated", "Recently updated"),
               ("newest", "Newest"), ("follows", "Most followed")]
MATCH_TEXT = {"exact": ("Works with your server version", "status_online"),
              "compatible": ("Should work with your server version", "status_online"),
              "older": ("Made for an older game version - may still work", "warning"),
              "newer": ("Made for a newer game version", "warning"),
              "incompatible": ("Not made for your server's game version", "danger")}


def fmt_num(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "?"
    if n >= 1_000_000:
        return "%.1fM" % (n / 1e6)
    if n >= 10_000:
        return "%dk" % (n // 1000)
    return "{:,}".format(n)


def fmt_date(v) -> str:
    if not v:
        return ""
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v).strftime("%d %b %Y")
        s = str(v).replace("Z", "").replace("T", " ")
        for f, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
            try:
                return datetime.strptime(s[:n], f).strftime("%d %b %Y")
            except ValueError:
                pass
    except Exception:
        pass
    return str(v)[:10]


def installed_ids(data) -> set:
    out = set()
    for m in (data or {}).get("mods") or []:
        for v in (m.get("id"), m.get("key"), m.get("name")):
            if v is not None:
                out.add(str(v).lower())
    return out


def is_installed(mod: dict, ids: set) -> bool:
    keys = mod.get("match_ids") or [mod.get("id")]
    return any(str(k).lower() in ids for k in keys if k is not None)


class ModBrowser(ctk.CTkToplevel):
    CARD_ICON = 56

    def __init__(self, app, theme, mods, server_id, query="", installed=None, on_change=None):
        super().__init__(app)
        from core.img_cache import ImageCache
        self.app, self.t, self.mods, self.sid = app, theme, mods, server_id
        self.on_change = on_change
        self.ids = installed_ids(installed)
        self.cache = ImageCache(str(app.manager.config_path.parent / "runtime" / "img_cache"))
        self._imgs = []          # keep CTkImages alive
        self._gen = 0            # ignore late results after a new search
        self._gen_d = 0
        srv = app.manager.get_server(server_id) or {}
        self.title("Browse mods - %s" % srv.get("name", server_id))
        self.transient(app)
        app.center_window(self, 1180, 780, fit=True)
        t = theme

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(14, 6))
        self.q = ctk.StringVar(value=query)
        e = ctk.CTkEntry(top, textvariable=self.q, placeholder_text="Search mods (leave empty for the most popular)")
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda _e: self.search())
        self.sort = ctk.StringVar(value=SORT_LABELS[0][1])
        ctk.CTkOptionMenu(top, variable=self.sort, values=[s[1] for s in SORT_LABELS], width=170,
                          command=lambda _v: self.search()).pack(side="left", padx=6)
        ctk.CTkButton(top, text="Search", width=90, fg_color=t["accent_blue"], command=self.search).pack(side="left")
        self.status = ctk.CTkLabel(self, text="", anchor="w", text_color=t["text_muted"])
        self.status.pack(fill="x", padx=16)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=(4, 14))
        body.grid_columnconfigure(0, weight=5, uniform="b")
        body.grid_columnconfigure(1, weight=6, uniform="b")
        body.grid_rowconfigure(0, weight=1)
        self.results = ctk.CTkScrollableFrame(body, fg_color=t["bg_dark"], corner_radius=8)
        self.results.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.detail = ctk.CTkScrollableFrame(body, fg_color=t["bg_card"], corner_radius=8)
        self.detail.grid(row=0, column=1, sticky="nsew")
        self._placeholder("Pick a mod on the left to see its pictures and details.")
        if Image is None:
            self.status.configure(text="Pictures need Pillow (included in BaoBun.exe).")
        self.after(50, self.search)

    # -- pictures ---------------------------------------------------------------
    def _picture(self, url, label, max_w, max_h, square=False):
        """Fill label with the picture at url once it's downloaded."""
        if not url or Image is None:
            return

        def done(path):
            if not path:
                return
            try:
                img = Image.open(path)
                img.load()
                img = img.convert("RGBA")
                if square:
                    side = min(img.size)
                    l, tp = (img.width - side) // 2, (img.height - side) // 2
                    img = img.crop((l, tp, l + side, tp + side))
                img.thumbnail((max_w, max_h))
            except Exception:
                return
            self.after(0, lambda: self._set(label, img))
        self.cache.fetch(url, done)

    def _set(self, label, img):
        try:
            if not label.winfo_exists():
                return
            ci = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self._imgs.append(ci)
            label.configure(image=ci, text="", width=img.size[0], height=img.size[1])
        except Exception:
            pass

    # -- search -----------------------------------------------------------------
    def search(self):
        self._gen += 1
        gen = self._gen
        text = self.q.get().strip()
        sort = next((k for k, v in SORT_LABELS if v == self.sort.get()), None)
        self.status.configure(text="Searching...")

        def work():
            try:
                res, err = self.mods.search(self.sid, text, sort=sort), None
            except Exception as e:
                res, err = None, str(e)
            self.after(0, lambda: self._show_results(gen, res, err))
        threading.Thread(target=work, daemon=True).start()

    def _clear(self, frame):
        for w in frame.winfo_children():
            w.destroy()

    def _show_results(self, gen, res, err):
        if gen != self._gen or not self.winfo_exists():
            return
        self._clear(self.results)
        if err:
            self.status.configure(text=err)
            return
        res = res or []
        self.status.configure(text="%d mod%s" % (len(res), "" if len(res) == 1 else "s") +
                              (" - showing the first 40" if len(res) > 40 else ""))
        for m in res[:40]:
            self._card(m)
        if not res:
            ctk.CTkLabel(self.results, text="Nothing found - try another word.",
                         text_color=self.t["text_muted"]).pack(pady=20)

    def _card(self, m):
        t = self.t
        card = ctk.CTkFrame(self.results, fg_color=t["bg_card"], corner_radius=8, cursor="hand2")
        card.pack(fill="x", padx=4, pady=3)
        icon = ctk.CTkLabel(card, text="\U0001F9E9", width=self.CARD_ICON, height=self.CARD_ICON,
                            fg_color=t["bg_dark"], corner_radius=6, font=("Segoe UI Emoji", 22))
        icon.pack(side="left", padx=8, pady=8)
        self._picture(m.get("icon"), icon, self.CARD_ICON, self.CARD_ICON, square=True)
        mid = ctk.CTkFrame(card, fg_color="transparent")
        mid.pack(side="left", fill="both", expand=True, pady=6)
        ctk.CTkLabel(mid, text=m.get("name") or str(m.get("id")), anchor="w", font=("Segoe UI", 13, "bold"),
                     text_color=t["text_light"]).pack(fill="x")
        bits = [b for b in ("by " + m["author"] if m.get("author") else "",
                            "⬇ " + fmt_num(m.get("downloads")) if m.get("downloads") is not None else "",
                            "updated " + fmt_date(m.get("updated")) if m.get("updated") else "") if b]
        ctk.CTkLabel(mid, text="  ·  ".join(bits), anchor="w", text_color=t["text_muted"],
                     font=("Segoe UI", 11)).pack(fill="x")
        if m.get("summary"):
            ctk.CTkLabel(mid, text=m["summary"][:160], anchor="w", justify="left", wraplength=330,
                         text_color=t["text_muted"], font=("Segoe UI", 11)).pack(fill="x")
        right = ctk.CTkFrame(card, fg_color="transparent")
        right.pack(side="right", padx=8)
        self._install_button(right, m, width=80).pack()
        for w in (card, icon, mid) + tuple(mid.winfo_children()):
            w.bind("<Button-1>", lambda _e, m=m: self.open_mod(m))

    def _install_button(self, parent, m, width=110):
        t = self.t
        if is_installed(m, self.ids):
            return ctk.CTkButton(parent, text="Installed", width=width, state="disabled", fg_color=t["border"])
        b = ctk.CTkButton(parent, text="Install", width=width, fg_color=t["success"])
        b.configure(command=lambda: self.install(m, b))
        return b

    def install(self, m, btn):
        ref = str(m.get("id"))
        btn.configure(state="disabled", text="Installing...")

        def work():
            try:
                r, err = self.mods.install(self.sid, ref, user="gui"), None
            except Exception as e:
                r, err = None, str(e)
            self.after(0, lambda: self._installed(m, btn, r, err))
        threading.Thread(target=work, daemon=True).start()

    def _installed(self, m, btn, r, err):
        if err:
            if btn.winfo_exists():
                btn.configure(state="normal", text="Install")
            self.app._toast("Mods", "Couldn't install %s: %s" % (m.get("name") or m.get("id"), err))
            return
        for k in (m.get("match_ids") or [m.get("id")]):
            if k is not None:
                self.ids.add(str(k).lower())
        if btn.winfo_exists():
            btn.configure(text="Installed", fg_color=self.t["border"])
        if self.on_change:
            self.on_change(r)

    # -- details ----------------------------------------------------------------
    def _placeholder(self, text):
        self._clear(self.detail)
        ctk.CTkLabel(self.detail, text=text, text_color=self.t["text_muted"], wraplength=480).pack(pady=40)

    def open_mod(self, m):
        self._gen_d += 1
        gen = self._gen_d
        self._placeholder("Loading %s..." % (m.get("name") or m.get("id")))

        def work():
            try:
                d, err = self.mods.details(self.sid, str(m.get("id"))), None
            except Exception as e:
                d, err = None, str(e)
            self.after(0, lambda: self._show_detail(gen, m, d, err))
        threading.Thread(target=work, daemon=True).start()

    def _show_detail(self, gen, m, d, err):
        if gen != self._gen_d or not self.winfo_exists():
            return
        t = self.t
        self._clear(self.detail)
        d = dict(m, **(d or {}))
        d["match_ids"] = list(d.get("match_ids") or []) + list(m.get("match_ids") or [])
        head = ctk.CTkFrame(self.detail, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(10, 4))
        icon = ctk.CTkLabel(head, text="\U0001F9E9", width=96, height=96, fg_color=t["bg_dark"], corner_radius=8,
                            font=("Segoe UI Emoji", 36))
        icon.pack(side="left")
        self._picture(d.get("icon"), icon, 96, 96, square=True)
        info = ctk.CTkFrame(head, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=12)
        ctk.CTkLabel(info, text=d.get("name") or str(d.get("id")), anchor="w", justify="left", wraplength=420,
                     font=("Bahnschrift SemiBold", 18, "bold"), text_color=t["text_light"]).pack(fill="x")
        stats = [s for s in ("by " + d["author"] if d.get("author") else "",
                             "⬇ " + fmt_num(d.get("downloads")) + " downloads" if d.get("downloads") is not None else "",
                             "♥ " + fmt_num(d.get("follows")) if d.get("follows") else "",
                             "updated " + fmt_date(d.get("updated")) if d.get("updated") else "") if s]
        ctk.CTkLabel(info, text="  ·  ".join(stats), anchor="w", text_color=t["text_muted"]).pack(fill="x")
        side = {"server": "Server only - players don't need it", "client": "Client only - not needed on the server",
                "both": "Players need it too"}.get(str(d.get("side") or "").lower())
        if side:
            ctk.CTkLabel(info, text=side, anchor="w", text_color=t["text_muted"]).pack(fill="x")
        btns = ctk.CTkFrame(info, fg_color="transparent")
        btns.pack(fill="x", pady=(6, 0))
        self._install_button(btns, d).pack(side="left")
        if d.get("url"):
            ctk.CTkButton(btns, text="Open mod page", width=120, fg_color="transparent", border_width=1,
                          border_color=t["border"], text_color=t["text_light"],
                          command=lambda u=d["url"]: webbrowser.open(u)).pack(side="left", padx=6)
        if err:
            ctk.CTkLabel(self.detail, text="Couldn't load the full page: " + err, anchor="w", wraplength=520,
                         text_color=t["warning"]).pack(fill="x", padx=12, pady=4)

        rel = d.get("release") or {}
        mt = MATCH_TEXT.get(rel.get("match"))
        if mt or rel.get("version"):
            line = ("Latest: %s" % rel["version"]) if rel.get("version") else ""
            if rel.get("for"):
                line += ("  " if line else "") + "for " + ", ".join(str(x) for x in rel["for"][:6])
            if mt:
                line = mt[0] + (" (%s)" % line if line else "")
            ctk.CTkLabel(self.detail, text=line, anchor="w", wraplength=520,
                         text_color=t.get(mt[1] if mt else "text_muted", t["text_muted"])).pack(fill="x", padx=12)
        if d.get("tags"):
            ctk.CTkLabel(self.detail, text="  ".join("#" + str(x) for x in d["tags"][:10]), anchor="w",
                         wraplength=520, text_color=t["text_muted"], font=("Segoe UI", 11)).pack(fill="x", padx=12)

        imgs = [u for u in d.get("images") or [] if u][:6]
        if imgs:
            gal = ctk.CTkFrame(self.detail, fg_color="transparent")
            gal.pack(fill="x", padx=8, pady=(10, 4))
            for i, u in enumerate(imgs):
                lbl = ctk.CTkLabel(gal, text="", width=200, height=120, fg_color=t["bg_dark"], corner_radius=6,
                                   cursor="hand2")
                lbl.grid(row=i // 3, column=i % 3, padx=4, pady=4)
                self._picture(u, lbl, 200, 120)
                lbl.bind("<Button-1>", lambda _e, u=u: self._big(u))

        if d.get("summary") and not d.get("blocks"):
            ctk.CTkLabel(self.detail, text=d["summary"], anchor="w", justify="left", wraplength=520,
                         text_color=t["text_light"]).pack(fill="x", padx=12, pady=6)
        shown_imgs = set(imgs)
        n_img = 0
        for b in (d.get("blocks") or [])[:120]:
            k = b.get("t")
            if k == "img":
                if b.get("src") in shown_imgs or n_img >= 8:
                    continue
                n_img += 1
                lbl = ctk.CTkLabel(self.detail, text="", height=10)
                lbl.pack(anchor="w", padx=12, pady=4)
                self._picture(b.get("src"), lbl, 520, 400)
            elif k == "h":
                ctk.CTkLabel(self.detail, text=b.get("text", ""), anchor="w", justify="left", wraplength=520,
                             font=("Segoe UI", 14, "bold"), text_color=t["text_light"]).pack(fill="x", padx=12,
                                                                                            pady=(10, 2))
            elif k == "li":
                ctk.CTkLabel(self.detail, text="•  " + b.get("text", ""), anchor="w", justify="left",
                             wraplength=500, text_color=t["text_light"]).pack(fill="x", padx=22)
            else:
                ctk.CTkLabel(self.detail, text=b.get("text", ""), anchor="w", justify="left", wraplength=520,
                             text_color=t["text_light"]).pack(fill="x", padx=12, pady=2)

        vers = d.get("versions") or []
        if vers:
            ctk.CTkLabel(self.detail, text="Versions", anchor="w", font=("Segoe UI", 14, "bold"),
                         text_color=t["text_light"]).pack(fill="x", padx=12, pady=(12, 2))
            for v in vers[:8]:
                ctk.CTkLabel(self.detail, text="%s  -  %s  %s" % (v.get("version") or "?", fmt_date(v.get("date")),
                                                                 ", ".join(str(x) for x in v.get("for") or [])),
                             anchor="w", text_color=t["text_muted"], font=("Segoe UI", 11)).pack(fill="x", padx=12)
        links = d.get("links") or []
        if links:
            row = ctk.CTkFrame(self.detail, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=(12, 12))
            for lbl, url in links[:6]:
                ctk.CTkButton(row, text=lbl, width=90, fg_color="transparent", border_width=1,
                              border_color=t["border"], text_color=t["text_light"],
                              command=lambda u=url: webbrowser.open(u)).pack(side="left", padx=3)

    def _big(self, url):
        """A screenshot at full window size."""
        win = ctk.CTkToplevel(self)
        win.title("Screenshot")
        win.transient(self)
        self.app.center_window(win, 1000, 680, fit=True)
        lbl = ctk.CTkLabel(win, text="Loading...")
        lbl.pack(fill="both", expand=True, padx=10, pady=10)
        lbl.bind("<Button-1>", lambda _e: win.destroy())
        self._picture(url, lbl, 980, 640)


def open_mod_browser(app, theme, mods, server_id, query="", installed=None, on_change=None):
    return ModBrowser(app, theme, mods, server_id, query=query, installed=installed, on_change=on_change)
