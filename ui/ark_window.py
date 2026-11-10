"""Desktop "ARK World" window: cluster health and parity, creature spawns, loot
tables and server rules for an ARK cluster (or a single ARK server).
Engine: core/ark_cluster.py, core/ark_ini.py, core/ark_catalog.py."""

from __future__ import annotations

import os
import threading

import customtkinter as ctk
from ui.app_common import later

FIX = {"sync_maps": "Sync all maps", "cluster_dir": "Share one cluster folder",
       "copy_settings": "Copy settings", "long_uploads": "Keep uploads 30 days", "update_all": "Update all maps"}
DOT = {"problem": "danger", "warning": "warning", "tip": "text_muted"}


def ark_targets(mgr):
    """[(label, server_id, where)] - one entry per cluster, one per cluster map that has
    its own install folder (its own settings), plus each stand-alone ARK server."""
    from core.ark_cluster import is_ark, own_install
    out, seen = [], set()
    servers = mgr.load_servers()
    for s in servers:
        if not is_ark(s) and not s.get("clusterId"):
            continue
        cid = s.get("clusterId")
        if cid:
            if cid in seen:
                continue
            seen.add(cid)
            members = [x for x in servers if x.get("clusterId") == cid]
            out.append(("Cluster %s (%d maps)" % (cid, len(members)), s["id"], "all"))
            dirs = {os.path.normcase(os.path.normpath(x.get("directory") or "")) for x in members}
            if len(dirs) > 1:
                for m in members:
                    if own_install(members, m):
                        out.append(("    \u21b3 %s only (own settings)" % (m.get("name") or m["id"]), m["id"], "this"))
        else:
            out.append((s.get("name") or s["id"], s["id"], "all"))
    return out


def open_ark_window(app, theme, server_id=None):
    from core import ark_catalog
    t = theme
    mgr = app.manager
    targets = ark_targets(mgr)
    if not targets:
        app._show_status("No ARK servers yet - use ARK Cluster to create the maps first.")
        return
    labels = [x[0] for x in targets]
    pick = next((lbl for lbl, sid, w in targets if sid == server_id and w == "all"), None)
    if pick is None and server_id:
        srv = mgr.get_server(server_id) or {}
        pick = next((lbl for lbl, sid, w in targets
                     if srv.get("clusterId") and (mgr.get_server(sid) or {}).get("clusterId") == srv.get("clusterId")),
                    labels[0])
    by_label = {lbl: (sid, w) for lbl, sid, w in targets}
    first = by_label[pick or labels[0]]
    state = {"sid": first[0], "where": first[1], "view": "Health", "dirty": False}

    win = ctk.CTkToplevel(app)
    win.title("ARK World")
    win.transient(app)
    app.center_window(win, 1000, 760, fit=True)
    top = ctk.CTkFrame(win, fg_color="transparent")
    top.pack(fill="x", padx=16, pady=(14, 4))
    ctk.CTkLabel(top, text="ARK World", text_color=t["text_light"],
                 font=("Bahnschrift SemiBold", 18, "bold")).pack(side="left")
    target_var = ctk.StringVar(value=pick or labels[0])
    target_menu = ctk.CTkOptionMenu(top, values=labels, variable=target_var, width=300,
                                    command=lambda v: switch_target(v))
    target_menu.pack(side="left", padx=12)
    seg = ctk.CTkSegmentedButton(top, values=["Health", "Creatures", "Loot", "Rules"],
                                 command=lambda v: switch_view(v))
    seg.set("Health")
    seg.pack(side="right")
    scope_lbl = ctk.CTkLabel(win, text="", anchor="w", text_color=t["text_muted"], font=("Segoe UI", 12))
    scope_lbl.pack(fill="x", padx=18)
    bar = ctk.CTkFrame(win, fg_color="transparent")
    bar.pack(fill="x", padx=16, pady=(4, 0))
    body = ctk.CTkScrollableFrame(win, fg_color=t["bg_card"], corner_radius=10)
    body.pack(fill="both", expand=True, padx=16, pady=8)
    foot = ctk.CTkFrame(win, fg_color="transparent")
    foot.pack(fill="x", padx=16, pady=(0, 12))
    ctk.CTkButton(foot, text="Close", fg_color="transparent", border_width=1, border_color=t["border"],
                  command=lambda: close()).pack(side="right")
    ctk.CTkButton(foot, text="Passwords & names...", fg_color="transparent", border_width=1,
                  border_color=t["border"], text_color=t["text_muted"],
                  command=lambda: app.show_cluster_settings_dialog()).pack(side="left")

    def clear(frame):
        for w in frame.winfo_children():
            w.destroy()

    def close():
        if state["dirty"] and not app.ask_yes_no("Leave without saving?", "You have changes that aren't saved.",
                                                 parent=win):
            return
        win.destroy()

    def switch_target(v):
        state["sid"], state["where"] = by_label[v]
        load()

    def refresh_targets(select_sid=None, select_where="this"):
        nonlocal targets
        targets = ark_targets(mgr)
        by_label.clear()
        by_label.update({lbl: (sid, w) for lbl, sid, w in targets})
        target_menu.configure(values=[x[0] for x in targets])
        lbl = next((l for l, sid, w in targets if sid == select_sid and w == select_where), None)
        if lbl:
            target_var.set(lbl)
            switch_target(lbl)

    def switch_view(v):
        if state["dirty"] and not app.ask_yes_no("Leave without saving?", "You have changes that aren't saved.",
                                                 parent=win):
            seg.set(state["view"])
            return
        state["view"] = v
        load()

    def note(parent, text, color=None, wrap=900):
        lbl = ctk.CTkLabel(parent, text=text, anchor="w", justify="left", wraplength=wrap,
                           text_color=color or t["text_muted"], font=("Segoe UI", 12))
        lbl.pack(fill="x", padx=10, pady=(2, 4))
        return lbl

    def bg(fn, done, label=None):
        def work():
            try:
                res, err = fn(), None
            except Exception as e:
                res, err = None, str(e)
            later(app, lambda: done(res, err) if win.winfo_exists() else None)
        if label:
            app._show_status(label)
        threading.Thread(target=work, daemon=True).start()

    def saved(res, err):
        if err:
            app.show_error("ARK World", err, parent=win)
            return
        state["dirty"] = False
        app._show_status("Saved - " + (res or {}).get("note", ""))
        load()

    def set_scope(name, shared):
        if state["where"] == "this":
            scope_lbl.configure(text="%s - spawns, loot and rules for this map; transfer settings still go to every "
                                     "map" % name)
            return
        scope_lbl.configure(text=("%s - changes apply to every map in the cluster" % name) if shared else (name or ""))

    def separate(m):
        from core import ark_cluster
        from tkinter import filedialog

        def planned(plan, err):
            if err:
                app.show_error("ARK World", err, parent=win)
                return
            gb = plan["bytes"] / 1024 ** 3
            msg = ("Give %s its own install folder?\n\nBaoBun copies the install (%.1f GB) to:\n%s\n\n%s gets its "
                   "own Game.ini / GameUserSettings.ini, so its creatures, loot and rules can differ. Mods, the world and "
                   "transfers stay the same: every map is pointed at one shared cluster folder. Choose No to pick "
                   "another folder." % (m["name"], gb, plan["target"], m["name"]))
            target = plan["target"]
            if not app.ask_yes_no("Own install folder", msg, parent=win):
                target = filedialog.askdirectory(parent=win, title="Pick an EMPTY folder for %s's install" % m["name"])
                if not target:
                    return
            prog = {"pct": 0.0}

            def tick():
                if win.winfo_exists() and prog.get("pct") is not None:
                    scope_lbl.configure(text="Copying the install for %s... %.0f%%" % (m["name"], prog["pct"]))
                    win.after(700, tick)

            def done(res, err2):
                prog["pct"] = None
                if err2:
                    app.show_error("ARK World", err2, parent=win)
                    load()
                    return
                app._show_status(res["note"])
                refresh_targets(m["id"], "this")
            tick()
            bg(lambda: ark_cluster.separate(mgr, m["id"], target, user="desktop",
                                            progress=lambda p, _w: prog.__setitem__("pct", p) if prog.get("pct")
                                            is not None else None), done)
        bg(lambda: ark_cluster.separate_plan(mgr, m["id"]), planned, "Measuring the install...")

    def dirty(save_btn):
        state["dirty"] = True
        save_btn.configure(state="normal")

    # ------------------------------------------------------------------ health
    def show_health(r):
        from core import ark_cluster
        set_scope(r["name"], len(r["maps"]) > 1)
        if not r["issues"]:
            note(body, "✅  Every map matches - mods, cluster ID and save folders are in order.", t["status_online"])
        for it in r["issues"]:
            row = ctk.CTkFrame(body, fg_color=t["bg_dark"], corner_radius=8)
            row.pack(fill="x", padx=6, pady=3)
            ctk.CTkLabel(row, text="●", width=16, text_color=t[DOT.get(it["level"], "text_muted")]).pack(
                side="left", padx=(10, 4), anchor="n", pady=8)
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, pady=6)
            ctk.CTkLabel(left, text=it["title"], anchor="w", justify="left", wraplength=700,
                         text_color=t["text_light"], font=("Segoe UI", 13, "bold")).pack(fill="x")
            ctk.CTkLabel(left, text=it["fix"], anchor="w", justify="left", wraplength=700,
                         text_color=t["text_muted"], font=("Segoe UI", 11)).pack(fill="x")
            if it.get("action") in FIX:
                ctk.CTkButton(row, text=FIX[it["action"]], width=150, fg_color=t["border"],
                              command=lambda a=it["action"]: bg(
                                  lambda: ark_cluster.fix(mgr, state["sid"], a, user="desktop"), saved,
                                  "Fixing...")).pack(side="right", padx=10)
        ctk.CTkLabel(body, text=("Maps in cluster " + r["cluster"]) if r["cluster"] else "Server", anchor="w",
                     text_color=t["text_light"], font=("Bahnschrift SemiBold", 14, "bold")).pack(
            fill="x", padx=10, pady=(14, 2))
        grid = ctk.CTkFrame(body, fg_color="transparent")
        grid.pack(fill="x", padx=10)
        for n, m in enumerate(r["maps"]):
            ctk.CTkLabel(grid, text="●", text_color=t["status_online"] if m["running"] else t["status_inactive"],
                         width=14).grid(row=n, column=0)
            ctk.CTkLabel(grid, text=m["name"], anchor="w", width=260, text_color=t["text_light"]).grid(
                row=n, column=1, sticky="w")
            ctk.CTkLabel(grid, text="%d mod%s" % (len(m["mods"]), "" if len(m["mods"]) == 1 else "s"),
                         anchor="w", width=90, text_color=t["text_muted"]).grid(row=n, column=2, sticky="w")
            ctk.CTkLabel(grid, text="saves in " + (m["save_dir"] or "-"), anchor="w",
                         text_color=t["text_muted"]).grid(row=n, column=3, sticky="w")
            if r["cluster"] and m.get("own_install"):
                ctk.CTkLabel(grid, text="own settings", anchor="w", text_color=t["status_online"]).grid(
                    row=n, column=4, sticky="w", padx=8)
            elif r["cluster"] and len(r["maps"]) > 1:
                ctk.CTkButton(grid, text="Own settings...", width=120, height=24, fg_color=t["border"],
                              command=lambda m=m: separate(m)).grid(row=n, column=4, sticky="w", padx=8, pady=2)
        if r["cluster"] and len(r["maps"]) > 1:
            note(body, "Want different creatures, loot or rules on one map? ARK keeps one set of settings per install "
                       "folder. Own settings copies the install for that map (it must be stopped; needs as much free "
                       "space as the install), then pick it in the list at the top to change its settings alone.")
        if r["cluster"]:
            note(body, "Cluster mods: %s. Change them in the Mods tab of any map (it updates every map). Before a "
                       "map starts BaoBun puts this list back on it, so creatures can't vanish because one map is "
                       "missing a mod." % (", ".join(r["mods"] or []) or "none"))

    # --------------------------------------------------------------- creatures
    def show_creatures(r):
        from core import ark_cluster
        set_scope(r["scope"], r["shared"])
        rules = {x["from"]: x["to"] for x in r["rules"]}
        names = {c[1]: c[0] for c in ark_catalog.CREATURES}
        groups = []
        for c in ark_catalog.CREATURES:
            if c[2] not in groups:
                groups.append(c[2])
        extra = [k for k in rules if k not in names]
        if extra:
            groups.insert(0, "Other / mods")
        groups.insert(0, "Changed")
        group_var = ctk.StringVar(value="Changed" if rules else groups[1])
        q_var = ctk.StringVar()
        save_btn = ctk.CTkButton(bar, text="Save", width=90, fg_color=t["success"], state="disabled",
                                 command=lambda: bg(lambda: ark_cluster.save_spawns(
                                     mgr, state["sid"], [{"from": k, "to": v} for k, v in rules.items()],
                                     user="desktop", where=state["where"]), saved, "Saving creatures..."))
        ctk.CTkOptionMenu(bar, values=groups, variable=group_var, width=170,
                          command=lambda _v: render_rows()).pack(side="left")
        ctk.CTkEntry(bar, textvariable=q_var, placeholder_text="Find a creature...", width=200).pack(
            side="left", padx=8)
        q_var.trace_add("write", lambda *_: render_rows())
        save_btn.pack(side="right")
        ctk.CTkButton(bar, text="Wipe wild creatures...", width=150, fg_color="transparent", border_width=1,
                      border_color=t["border"], command=lambda: wipe()).pack(side="right", padx=6)
        ctk.CTkButton(bar, text="Add mod creature...", width=140, fg_color="transparent", border_width=1,
                      border_color=t["border"], command=lambda: add_custom()).pack(side="right")
        note(body, "Turned-off creatures stop spawning on every map that uses these settings (ARK keeps one creature "
                   "list per install folder). Tamed ones are never touched; wild ones already out there stay until "
                   "they die - or wipe them once the maps have restarted.")
        rows_box = ctk.CTkFrame(body, fg_color="transparent")
        rows_box.pack(fill="x")

        def label_for(cls):
            to = rules.get(cls)
            if cls not in rules:
                return "Spawns"
            return "Never spawns" if not to else "→ " + names.get(to, to)

        def choose(cls, value, menu):
            if value == "Spawns":
                rules.pop(cls, None)
            elif value == "Never spawns":
                rules[cls] = ""
            else:
                pick_replacement(cls, menu)
                return
            menu.set(label_for(cls))
            dirty(save_btn)

        def pick_replacement(cls, menu):
            d = ctk.CTkToplevel(win)
            d.title("Replace " + names.get(cls, cls))
            d.transient(win)
            app.center_window(d, 360, 480, fit=True)
            fv = ctk.StringVar()
            ctk.CTkEntry(d, textvariable=fv, placeholder_text="Find...").pack(fill="x", padx=12, pady=10)
            lst = ctk.CTkScrollableFrame(d, fg_color=t["bg_card"])
            lst.pack(fill="both", expand=True, padx=12, pady=(0, 12))

            def fill(*_):
                clear(lst)
                q = fv.get().strip().lower()
                for n, c, _g in ark_catalog.CREATURES:
                    if c != cls and (not q or q in n.lower()):
                        ctk.CTkButton(lst, text=n, anchor="w", fg_color="transparent", hover_color=t["border"],
                                      text_color=t["text_light"],
                                      command=lambda c=c: (rules.__setitem__(cls, c), menu.set(label_for(cls)),
                                                           dirty(save_btn), d.destroy())).pack(fill="x")
            fv.trace_add("write", fill)
            fill()
            d.protocol("WM_DELETE_WINDOW", lambda: (menu.set(label_for(cls)), d.destroy()))

        def render_rows():
            clear(rows_box)
            g = group_var.get()
            q = q_var.get().strip().lower()
            items = [(names.get(k, ark_catalog.pretty(k)), k) for k in extra] if g == "Other / mods" else \
                [(n, c) for n, c, gg in ark_catalog.CREATURES if g == "Changed" and c in rules or gg == g]
            if g == "Changed":
                items += [(ark_catalog.pretty(k), k) for k in extra]
            if q:
                items = [(n, c) for n, c, _g in ark_catalog.CREATURES if q in n.lower() or q in c.lower()]
            if not items:
                note(rows_box, "Nothing changed yet - every creature spawns normally." if g == "Changed"
                     else "No creature matches.")
                return
            for n, (name, cls) in enumerate(items[:80]):
                row = ctk.CTkFrame(rows_box, fg_color=t["bg_dark"] if cls in rules else "transparent",
                                   corner_radius=6)
                row.grid(row=n // 2, column=n % 2, sticky="ew", padx=6, pady=2)
                rows_box.grid_columnconfigure(n % 2, weight=1)
                ctk.CTkLabel(row, text=name, anchor="w", width=200, text_color=t["text_light"]).pack(
                    side="left", padx=(8, 4))
                menu = ctk.CTkOptionMenu(row, values=["Spawns", "Never spawns", "Replace with..."], width=170)
                menu.configure(command=lambda v, c=cls, mm=menu: choose(c, v, mm))
                menu.set(label_for(cls))
                menu.pack(side="right", padx=6, pady=3)

        def add_custom():
            d = ctk.CTkInputDialog(text="Class name of the mod creature (e.g. MyDino_Character_BP_C):",
                                   title="Turn off a mod creature")
            v = (d.get_input() or "").strip()
            if not v:
                return
            import re
            if not re.match(r"^[\w\-.]+$", v):
                app.show_error("ARK World", "That doesn't look like a class name.", parent=win)
                return
            rules[v] = ""
            if v not in extra:
                extra.append(v)
            group_var.set("Changed")
            render_rows()
            dirty(save_btn)

        def wipe():
            if not app.ask_yes_no("Wipe wild creatures", "Kill every WILD creature on the running maps now? Tamed "
                                  "creatures are safe. New ones spawn using the current rules (restart the maps "
                                  "first if you just changed them).", parent=win):
                return
            bg(lambda: ark_cluster.wipe_wild(mgr, state["sid"], user="desktop", where=state["where"]),
               lambda res, err: app.show_error("ARK World", err, parent=win) if err
               else app._show_status(res["note"]), "Wiping wild creatures...")
        render_rows()

    # -------------------------------------------------------------------- loot
    def show_loot(r):
        from core import ark_cluster
        set_scope(r["scope"], r.get("shared"))
        loot = r["loot"]
        crate_names = {c[0]: c[1] for c in ark_catalog.CRATES}
        item_names = [i[0] for i in ark_catalog.ITEMS]
        item_cls = {i[0]: i[1] for i in ark_catalog.ITEMS}
        tiers = [q[0] for q in ark_catalog.QUALITY_TIERS]
        tier_val = dict(ark_catalog.QUALITY_TIERS)

        def tier_of(v):
            best = min(ark_catalog.QUALITY_TIERS, key=lambda q: abs(q[1] - float(v or 0)))
            return best[0]

        def do_save():
            for c in loot:
                if not c.get("advanced") and not [i for i in c["items"] if i.get("cls")]:
                    app.show_error("ARK World", "Add an item to %s or remove it." % c.get("crate_name"), parent=win)
                    return
            clean = [c if c.get("advanced") else {k: v for k, v in c.items() if k != "crate_name"} for c in loot]
            for c in clean:
                if not c.get("advanced"):
                    c["items"] = [i for i in c["items"] if i.get("cls")]
            bg(lambda: ark_cluster.save_loot(mgr, state["sid"], clean, user="desktop", where=state["where"]), saved, "Saving loot...")
        save_btn = ctk.CTkButton(bar, text="Save", width=90, fg_color=t["success"], state="disabled", command=do_save)
        add_var = ctk.StringVar(value="Change what a drop or crate gives...")
        ctk.CTkOptionMenu(bar, values=list(crate_names) + ["Other (type its class name)..."], variable=add_var,
                          width=300, dynamic_resizing=False, command=lambda v: add_crate(v)).pack(side="left")
        save_btn.pack(side="right")
        note(body, "Supply drops, cave crates and boss / alpha drops. Add mixes your items into what the crate "
                   "normally gives; Replace means it only gives your items. Used after the maps restart.")
        box = ctk.CTkFrame(body, fg_color="transparent")
        box.pack(fill="x")

        def add_crate(v):
            add_var.set("Change what a drop or crate gives...")
            cls = crate_names.get(v)
            if cls is None:
                d = ctk.CTkInputDialog(text="Class name of the crate or creature drop:", title="Other crate")
                cls = (d.get_input() or "").strip()
                if not cls:
                    return
            if any(c.get("crate") == cls for c in loot):
                app._show_status("That crate is already in the list")
                return
            loot.append({"crate": cls, "crate_name": ark_catalog.crate_name(cls), "mode": "add", "picks_min": 1,
                         "picks_max": 1, "items": [{"cls": "", "qty_min": 1, "qty_max": 1, "quality_min": 0,
                                                     "quality_max": 1.25, "blueprint": 0, "weight": 1}]})
            render()
            dirty(save_btn)

        def num_entry(parent, value, width, on):
            v = ctk.StringVar(value=("%g" % value) if isinstance(value, (int, float)) else str(value))
            e = ctk.CTkEntry(parent, textvariable=v, width=width)

            def changed(*_):
                try:
                    on(float(v.get()))
                    dirty(save_btn)
                except ValueError:
                    pass
            v.trace_add("write", changed)
            return e

        def render():
            clear(box)
            if not loot:
                note(box, "No changes yet - every drop gives ARK's normal loot.")
            for c in loot:
                card = ctk.CTkFrame(box, fg_color=t["bg_dark"], corner_radius=10)
                card.pack(fill="x", padx=6, pady=5)
                hd = ctk.CTkFrame(card, fg_color="transparent")
                hd.pack(fill="x", padx=10, pady=(8, 4))
                ctk.CTkLabel(hd, text=c.get("crate_name") or c.get("crate"), anchor="w", text_color=t["text_light"],
                             font=("Segoe UI", 13, "bold")).pack(side="left")
                ctk.CTkButton(hd, text="Remove", width=70, fg_color="transparent", border_width=1,
                              border_color=t["border"],
                              command=lambda c=c: (loot.remove(c), render(), dirty(save_btn))).pack(side="right")
                if c.get("advanced"):
                    note(card, "Hand-written layout BaoBun can't show as a list - kept exactly as it is.")
                    continue
                mode = ctk.CTkOptionMenu(hd, values=["Add to its normal loot", "Replace its loot"], width=190,
                                         command=lambda v, c=c: (c.__setitem__("mode", "add" if v.startswith("Add")
                                                                               else "replace"), dirty(save_btn)))
                mode.set("Add to its normal loot" if c.get("mode") == "add" else "Replace its loot")
                mode.pack(side="right", padx=8)
                ctk.CTkLabel(hd, text="of these", text_color=t["text_muted"]).pack(side="right")
                num_entry(hd, c["picks_max"], 46, lambda v, c=c: c.__setitem__("picks_max", max(1, int(v)))).pack(
                    side="right", padx=4)
                ctk.CTkLabel(hd, text="to", text_color=t["text_muted"]).pack(side="right")
                num_entry(hd, c["picks_min"], 46, lambda v, c=c: c.__setitem__("picks_min", max(1, int(v)))).pack(
                    side="right", padx=4)
                ctk.CTkLabel(hd, text="gives", text_color=t["text_muted"]).pack(side="right", padx=(10, 0))
                tbl = ctk.CTkFrame(card, fg_color="transparent")
                tbl.pack(fill="x", padx=10, pady=(0, 4))
                for col, h in enumerate(("Item", "Amount", "up to", "Quality from", "to", "Blueprint %",
                                         "How likely", "")):
                    ctk.CTkLabel(tbl, text=h, text_color=t["text_muted"], font=("Segoe UI", 11)).grid(
                        row=0, column=col, sticky="w", padx=3)
                for n, it in enumerate(c["items"], start=1):
                    cb = ctk.CTkComboBox(tbl, values=item_names, width=230,
                                         command=lambda v, it=it: (it.__setitem__("cls", item_cls.get(v, v)),
                                                                   dirty(save_btn)))
                    cb.set(ark_catalog.item_name(it["cls"]) if it.get("cls") else "")
                    cb.bind("<FocusOut>", lambda e, it=it, cb=cb: (it.__setitem__(
                        "cls", item_cls.get(cb.get(), cb.get().strip())), dirty(save_btn)))
                    cb.grid(row=n, column=0, padx=3, pady=2)
                    num_entry(tbl, it["qty_min"], 60, lambda v, it=it: it.__setitem__("qty_min", v)).grid(
                        row=n, column=1, padx=3)
                    num_entry(tbl, it["qty_max"], 60, lambda v, it=it: it.__setitem__("qty_max", v)).grid(
                        row=n, column=2, padx=3)
                    for col, key in ((3, "quality_min"), (4, "quality_max")):
                        om = ctk.CTkOptionMenu(tbl, values=tiers, width=120,
                                               command=lambda v, it=it, key=key: (it.__setitem__(key, tier_val[v]),
                                                                                  dirty(save_btn)))
                        om.set(tier_of(it.get(key)))
                        om.grid(row=n, column=col, padx=3)
                    num_entry(tbl, round(float(it.get("blueprint") or 0) * 100), 60,
                              lambda v, it=it: it.__setitem__("blueprint", min(100.0, max(0.0, v)) / 100)).grid(
                        row=n, column=5, padx=3)
                    num_entry(tbl, it.get("weight") or 1, 60, lambda v, it=it: it.__setitem__("weight", v)).grid(
                        row=n, column=6, padx=3)
                    ctk.CTkButton(tbl, text="✕", width=30, fg_color="transparent", border_width=1,
                                  border_color=t["border"],
                                  command=lambda c=c, it=it: (c["items"].remove(it), render(), dirty(save_btn))).grid(
                        row=n, column=7, padx=3)
                ctk.CTkButton(card, text="+ Item", width=80, fg_color="transparent", border_width=1,
                              border_color=t["border"],
                              command=lambda c=c: (c["items"].append({"cls": "", "qty_min": 1, "qty_max": 1,
                                                                      "quality_min": 0, "quality_max": 1.25,
                                                                      "blueprint": 0, "weight": 1}),
                                                   render(), dirty(save_btn))).pack(anchor="w", padx=10, pady=(0, 8))
        render()

    # ------------------------------------------------------------------- rules
    def show_rules(r):
        from core import ark_cluster
        set_scope(r["scope"], r["shared"])
        values = {}
        save_btn = ctk.CTkButton(bar, text="Save", width=90, fg_color=t["success"], state="disabled",
                                 command=lambda: bg(lambda: ark_cluster.save_rules(mgr, state["sid"], values, user="desktop",
                                                                                   where=state["where"]),
                                                    saved, "Saving rules..."))
        ctk.CTkLabel(bar, text="Saved into GameUserSettings.ini / Game.ini. Blank = ARK's default.",
                     text_color=t["text_muted"]).pack(side="left")
        save_btn.pack(side="right")
        groups = []
        for x in r["rules"]:
            if x["group"] not in groups:
                groups.append(x["group"])
        for g in groups:
            ctk.CTkLabel(body, text=g, anchor="w", text_color=t["accent_gold"],
                         font=("Bahnschrift SemiBold", 14, "bold")).pack(fill="x", padx=10, pady=(12, 2))
            grid = ctk.CTkFrame(body, fg_color="transparent")
            grid.pack(fill="x", padx=10)
            grid.grid_columnconfigure((0, 1), weight=1)
            for n, x in enumerate([y for y in r["rules"] if y["group"] == g]):
                cell = ctk.CTkFrame(grid, fg_color="transparent")
                cell.grid(row=n // 2, column=n % 2, sticky="ew", padx=6, pady=4)
                ctk.CTkLabel(cell, text=x["label"], anchor="w", text_color=t["text_light"]).pack(fill="x")
                if x["kind"] == "bool":
                    om = ctk.CTkOptionMenu(cell, values=["Game default", "On", "Off"],
                                           command=lambda v, k=x["key"]: (values.__setitem__(
                                               k, {"On": "True", "Off": "False"}.get(v, "")), dirty(save_btn)))
                    cur = (x["value"] or "").lower()
                    om.set("On" if cur in ("true", "1") else "Off" if cur in ("false", "0") else "Game default")
                    om.pack(fill="x")
                else:
                    v = ctk.StringVar(value=x["value"] or "")
                    ctk.CTkEntry(cell, textvariable=v, placeholder_text="game default").pack(fill="x")
                    v.trace_add("write", lambda *_a, k=x["key"], v=v: (values.__setitem__(k, v.get().strip()),
                                                                       dirty(save_btn)))
                if x.get("help"):
                    ctk.CTkLabel(cell, text=x["help"], anchor="w", justify="left", wraplength=420,
                                 text_color=t["text_muted"], font=("Segoe UI", 11)).pack(fill="x")

    # ------------------------------------------------------------------- load
    def load():
        from core import ark_cluster
        clear(body)
        clear(bar)
        state["dirty"] = False
        view = state["view"]
        note(body, "Loading...")

        def fetch():
            if view == "Health":
                return ark_cluster.check(mgr, state["sid"])
            if view == "Creatures":
                return ark_cluster.read_spawns(mgr, state["sid"], state["where"])
            if view == "Loot":
                return ark_cluster.read_loot(mgr, state["sid"], state["where"])
            return ark_cluster.rules_info(mgr, state["sid"], state["where"])

        def done(res, err):
            if view != state["view"]:
                return
            clear(body)
            if err:
                note(body, "Couldn't load: " + err, t["danger"])
                return
            {"Health": show_health, "Creatures": show_creatures, "Loot": show_loot, "Rules": show_rules}[view](res)
        bg(fetch, done)

    win.protocol("WM_DELETE_WINDOW", close)
    load()
