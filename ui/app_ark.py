"""ARK cluster setup, gameplay and cluster settings windows. Part of the desktop app (BaoBunApp in app.py)."""
# flake8: noqa
import threading

import customtkinter as ctk
import tkinter.messagebox as messagebox
from tkinter import ttk
from tkinter import filedialog

from ui.app_common import THEME
from server_manager import ARK_MAPS, ARK_MAP_MODS, ARK_REFORGE_MODS


class ArkDialogsMixin:
    def show_ark_cluster_dialog(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Setup ARK Cluster")
        dlg.geometry("560x660")
        dlg.grab_set()
        dlg.transient(self)
        self.center_window(dlg, 560, 660, fit=False)
        ctk.CTkLabel(dlg, text="ARK: Survival Ascended Cluster", font=("Bahnschrift SemiBold", 18, "bold")).pack(
            pady=(20, 10),
        )
        form = ctk.CTkScrollableFrame(dlg, fg_color=THEME["bg_card"], corner_radius=10)
        form.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        dir_var = ctk.StringVar()
        cid_var = ctk.StringVar(value="BaoBunCluster")
        pw_var = ctk.StringVar()
        apw_var = ctk.StringVar()
        mods_var = ctk.StringVar()
        server_name_var = ctk.StringVar()
        map_vars = {}

        ctk.CTkLabel(
            form, text="Install Directory *", anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", pady=(12, 0), padx=10)
        dir_row = ctk.CTkFrame(form, fg_color="transparent")
        dir_row.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkEntry(dir_row, textvariable=dir_var).pack(side="left", fill="x", expand=True)

        def browse_dir():
            chosen = filedialog.askdirectory(title="Select ARK install folder")
            if chosen:
                dir_var.set(chosen)

        ctk.CTkButton(
            dir_row, text="Browse", width=70, fg_color=THEME["border"], command=browse_dir,
        ).pack(side="left", padx=(6, 0))
        ctk.CTkLabel(
            form, text="Point at the folder holding 'ShooterGame'. Install first via SteamCMD App ID 2430930.",
            anchor="w", text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10)

        inst_row = ctk.CTkFrame(form, fg_color="transparent")
        inst_row.pack(fill="x", padx=10, pady=(8, 0))

        def do_install():
            directory = dir_var.get().strip()
            if not directory:
                messagebox.showwarning("Directory required", "Select the ARK install directory first.")
                return
            if not messagebox.askyesno(
                "Install ARK",
                "Download/update the ARK ASA dedicated server (App 2430930) into:\n"
                + directory + "\n\nThis is ~30 GB and may take a while. Continue?",
            ):
                return

            def run():
                try:
                    self.after(0, lambda: self._show_status("Installing ARK dedicated server..."))
                    self.manager.install_ark_server(directory)
                    self.after(0, lambda: self._show_status("ARK server installed"))
                    self.after(0, lambda: messagebox.showinfo(
                        "Install complete", "ARK server installed to:\n" + directory,
                    ))
                except Exception as e:
                    msg = str(e)
                    self.after(0, lambda: messagebox.showerror("Install failed", msg))

            threading.Thread(target=run, daemon=True).start()

        ctk.CTkButton(
            inst_row, text="Download / Update ASA (SteamCMD)",
            fg_color=THEME["accent_blue"], command=do_install,
        ).pack(side="left")
        ctk.CTkLabel(
            form, text="Installs game files all maps share. Run again to update.",
            anchor="w", text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10, pady=(4, 0))

        ctk.CTkLabel(form, text="Cluster ID", anchor="w", text_color=THEME["text_light"]).pack(
            fill="x", pady=(12, 0), padx=10,
        )
        ctk.CTkEntry(form, textvariable=cid_var).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            form, text="Server Name (shared)", anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", pady=(12, 0), padx=10)
        ctk.CTkEntry(form, textvariable=server_name_var).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            form, text="Server Password (shared)", anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", pady=(12, 0), padx=10)
        ctk.CTkEntry(form, textvariable=pw_var).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            form, text="Admin Password (shared)", anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", pady=(6, 0), padx=10)
        ctk.CTkEntry(form, textvariable=apw_var).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            form, text="Mod IDs (comma-separated)", anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", pady=(6, 0), padx=10)
        ctk.CTkEntry(form, textvariable=mods_var).pack(fill="x", padx=10, pady=(0, 4))

        ctk.CTkLabel(
            form, text="Maps to create", anchor="w",
            text_color=THEME["text_light"], font=("Bahnschrift SemiBold", 13, "bold"),
        ).pack(fill="x", pady=(16, 0), padx=10)
        for friendly, map_id in ARK_MAPS:
            modded = map_id in ARK_MAP_MODS or friendly in ARK_REFORGE_MODS
            var = ctk.BooleanVar(value=not modded)
            map_vars[friendly] = var
            label = friendly + "  (" + map_id + ")"
            if modded:
                label += "  [mod]"
            ctk.CTkCheckBox(
                form, text=label, variable=var, fg_color=THEME["accent_blue"],
            ).pack(fill="x", padx=10, pady=(2, 0))
        ctk.CTkLabel(
            form, text="Each map gets unique ports and its own save folder.",
            anchor="w", text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10, pady=(4, 10))

        custom_maps = []

        ctk.CTkLabel(
            form, text="Custom maps", anchor="w",
            text_color=THEME["text_light"], font=("Bahnschrift SemiBold", 13, "bold"),
        ).pack(fill="x", pady=(14, 0), padx=10)
        ctk.CTkLabel(
            form, text="Add a map that isn't listed above (new or community maps).",
            anchor="w", text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10)

        custom_frame = ctk.CTkFrame(form, fg_color="transparent")
        custom_frame.pack(fill="x", padx=10, pady=(4, 0))

        def redraw_custom():
            for w in custom_frame.winfo_children():
                w.destroy()
            if not custom_maps:
                ctk.CTkLabel(
                    custom_frame, text="No custom maps added.",
                    anchor="w", text_color=THEME["text_muted"],
                ).pack(anchor="w", pady=2)
                return
            for cm in custom_maps:
                row = ctk.CTkFrame(custom_frame, fg_color="transparent")
                row.pack(fill="x", pady=1)
                label = cm["name"] + "  (" + cm["map_id"] + ")"
                if cm.get("mod"):
                    label += "  [mod " + cm["mod"] + "]"
                ctk.CTkLabel(
                    row, text=label, anchor="w", text_color=THEME["text_light"],
                ).pack(side="left", fill="x", expand=True)
                ctk.CTkButton(
                    row, text="Remove", width=60, fg_color=THEME["danger"],
                    command=lambda c=cm: remove_custom(c),
                ).pack(side="right", padx=(8, 0))

        def remove_custom(cm):
            if cm in custom_maps:
                custom_maps.remove(cm)
            redraw_custom()

        ctk.CTkLabel(
            form, text="Map name", anchor="w", text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10, pady=(10, 0))
        custom_name_var = ctk.StringVar()
        ctk.CTkEntry(form, textvariable=custom_name_var).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            form, text="Map identifier (usually ends in _WP, e.g. MyMap_WP)", anchor="w",
            text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10)
        custom_id_var = ctk.StringVar()
        ctk.CTkEntry(form, textvariable=custom_id_var).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            form, text="Mod ID (optional CurseForge ID)", anchor="w",
            text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10)
        custom_mod_var = ctk.StringVar()
        ctk.CTkEntry(form, textvariable=custom_mod_var).pack(fill="x", padx=10, pady=(0, 4))

        def add_custom():
            name = custom_name_var.get().strip()
            map_id = custom_id_var.get().strip()
            mod = custom_mod_var.get().strip()
            if not name or not map_id:
                messagebox.showwarning(
                    "Custom map", "Enter both a map name and a map identifier.")
                return
            if any(c["map_id"] == map_id for c in custom_maps):
                messagebox.showwarning(
                    "Duplicate map", "That map identifier is already added.")
                return
            custom_maps.append({"name": name, "map_id": map_id, "mod": mod})
            custom_name_var.set("")
            custom_id_var.set("")
            custom_mod_var.set("")
            redraw_custom()

        ctk.CTkButton(
            form, text="+ Add custom map", fg_color=THEME["accent_blue"],
            command=add_custom,
        ).pack(fill="x", padx=10, pady=(4, 8))

        redraw_custom()

        def do_setup():
            directory = dir_var.get().strip()
            if not directory:
                messagebox.showwarning(
                    "Directory required", "Select the ARK install directory.")
                return
            selected = [f for f, v in map_vars.items() if v.get()]
            if not selected and not custom_maps:
                messagebox.showwarning(
                    "No maps", "Select at least one map or add a custom map.")
                return
            cluster_id = cid_var.get().strip() or "BaoBunCluster"
            server_name_val = server_name_var.get().strip()
            server_names = {}
            for f in map_vars:
                server_names[f] = server_name_val
            settings = {
                "serverPassword": pw_var.get().strip(),
                "adminPassword": apw_var.get().strip(),
                "mods": mods_var.get().strip(),
                "serverNames": server_names,
            }
            try:
                created = self.manager.setup_ark_cluster(
                    directory, cluster_id, selected, settings, custom_maps)
            except Exception as e:
                messagebox.showerror("Setup failed", str(e))
                return
            dlg.destroy()
            self.refresh_servers()
            if created:
                messagebox.showinfo(
                    "ARK Cluster", "Created " + str(len(created)) +
                    " ARK server(s).")
            else:
                messagebox.showinfo(
                    "ARK Cluster",
                    "No new servers created (they may already exist).")

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(0, 20))
        ctk.CTkButton(
            btns, text="Create Cluster", fg_color=THEME["success"],
            command=do_setup).pack(side="left", padx=6)
        ctk.CTkButton(
            btns, text="Cancel", fg_color=THEME["border"],
            command=dlg.destroy).pack(side="right", padx=6)

    def show_ark_gameplay_dialog(self, cluster_id):
        """In-depth, beginner-friendly editor for ARK gameplay settings.

        Edits the shared GameUserSettings.ini (all maps in a cluster share one
        install), giving labeled fields for the most important rates and toggles.
        """
        try:
            entries = self.manager.get_ark_gameplay_settings(cluster_id)
        except Exception as e:
            messagebox.showerror("Gameplay Settings", str(e))
            return

        dlg = ctk.CTkToplevel(self)
        dlg.title("ARK Gameplay Settings")
        dlg.geometry("640x720")
        dlg.grab_set()
        dlg.transient(self)
        self.center_window(dlg, 640, 720)
        ctk.CTkLabel(
            dlg, text="ARK Gameplay Settings", font=("Bahnschrift SemiBold", 18, "bold"),
        ).pack(pady=(20, 4))
        ctk.CTkLabel(
            dlg, text="Applied to every map in the cluster (shared config).",
            text_color=THEME["text_muted"],
        ).pack(pady=(0, 10))

        form = ctk.CTkScrollableFrame(dlg, fg_color=THEME["bg_card"], corner_radius=10)
        form.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        widgets = {}
        for e in entries:
            key = e["key"]
            kind = e["kind"]
            label = e["label"]
            help_text = e.get("help", "")
            row = ctk.CTkFrame(form, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=4)
            ctk.CTkLabel(
                row, text=label, width=160, anchor="w", text_color=THEME["text_light"],
            ).pack(side="left")
            if kind == "bool":
                var = ctk.BooleanVar(value=(str(e["value"]).strip().lower() in ("true", "1", "yes", "on")))
                ctk.CTkCheckBox(row, text="", variable=var, width=40, fg_color=THEME["accent_blue"]).pack(side="left")
            else:
                var = ctk.StringVar(value=str(e["value"]))
                ctk.CTkEntry(row, textvariable=var, width=110).pack(side="left")
            widgets[key] = (kind, var)
            if help_text:
                ctk.CTkLabel(
                    row, text=help_text, text_color=THEME["text_muted"], anchor="w",
                ).pack(side="left", padx=(10, 0))

        def apply():
            settings = {}
            for key, (kind, var) in widgets.items():
                if kind == "bool":
                    settings[key] = "True" if var.get() else "False"
                    continue
                raw = var.get().strip()
                try:
                    if kind == "int":
                        int(raw)
                    else:
                        float(raw)
                except ValueError:
                    messagebox.showerror("Invalid value", "Enter a valid number for " + key + ".")
                    return
                settings[key] = raw
            dlg.destroy()
            try:
                written = self.manager.set_ark_gameplay_settings(cluster_id, settings)
            except Exception as e:
                messagebox.showerror("Apply failed", str(e))
                return
            self.refresh_servers()
            self._toast("Gameplay Settings", "Updated " + str(written) + " setting(s).")

        # ---- Boost / rate profiles ----
        profile_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        profile_frame.pack(fill="x", padx=20, pady=(4, 0))
        profile_names = [p["name"] for p in self.manager.list_boost_profiles()]
        profile_var = ctk.StringVar(value="")
        profile_row = ctk.CTkFrame(profile_frame, fg_color="transparent")
        profile_row.pack(fill="x")
        ctk.CTkLabel(profile_row, text="Boost profile:", text_color=THEME["text_muted"]).pack(side="left", padx=(0, 6))
        profile_combo = ctk.CTkComboBox(profile_row, variable=profile_var, values=["(saved profiles)"] + profile_names, width=220)
        profile_combo.pack(side="left")

        def _apply_profile_values(settings_map):
            for key, (kind, var) in widgets.items():
                if key not in settings_map:
                    continue
                raw = str(settings_map[key])
                if kind == "bool":
                    var.set(raw.strip().lower() in ("true", "1", "yes", "on"))
                else:
                    var.set(raw)

        def load_profile():
            name = profile_var.get()
            if not name or name.startswith("("):
                messagebox.showwarning("Boost profile", "Choose a saved profile first.")
                return
            for p in self.manager.list_boost_profiles():
                if p["name"] == name:
                    _apply_profile_values(p.get("settings", {}))
                    return

        def save_profile():
            name = profile_var.get().strip()
            if not name or name.startswith("("):
                messagebox.showwarning("Boost profile", "Type a name (or pick an existing one) to save the current rates.")
                return
            settings = {}
            for key, (kind, var) in widgets.items():
                if kind == "bool":
                    settings[key] = "True" if var.get() else "False"
                else:
                    settings[key] = str(var.get()).strip()
            self.manager.save_boost_profile(name, settings)
            profile_combo.configure(values=["(saved profiles)"] + [p["name"] for p in self.manager.list_boost_profiles()])
            self._toast("Boost profile", "Saved profile " + name + ".")

        def delete_profile():
            name = profile_var.get()
            if not name or name.startswith("("):
                messagebox.showwarning("Boost profile", "Choose a saved profile to delete.")
                return
            self.manager.delete_boost_profile(name)
            profile_var.set("")
            profile_combo.configure(values=["(saved profiles)"] + [p["name"] for p in self.manager.list_boost_profiles()])
            self._toast("Boost profile", "Deleted " + name + ".")

        profile_btns = ctk.CTkFrame(profile_frame, fg_color="transparent")
        profile_btns.pack(fill="x", pady=(4, 0))
        ctk.CTkButton(profile_btns, text="Load", width=70, fg_color=THEME["border"], command=load_profile).pack(side="left", padx=(0, 6))
        ctk.CTkButton(profile_btns, text="Save", width=70, fg_color=THEME["border"], command=save_profile).pack(side="left", padx=(0, 6))
        ctk.CTkButton(profile_btns, text="Delete", width=70, fg_color=THEME["border"], command=delete_profile).pack(side="left")

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(0, 20))
        ctk.CTkButton(
            btns, text="Apply to cluster", fg_color=THEME["success"], command=apply,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            btns, text="Cancel", fg_color=THEME["border"], command=dlg.destroy,
        ).pack(side="right", padx=6)

    # ------------------------------------------------------------------ #
    # ARK cluster
    # ------------------------------------------------------------------ #
    def show_cluster_settings_dialog(self):
        cluster_ids = self.manager.get_cluster_ids()
        if not cluster_ids:
            messagebox.showinfo("ARK Cluster Settings", "No ARK cluster servers found yet.")
            return
        dlg = ctk.CTkToplevel(self)
        dlg.title("ARK Cluster Settings")
        dlg.geometry("500x500")
        dlg.grab_set()
        dlg.transient(self)
        self.center_window(dlg, 500, 500)
        ctk.CTkLabel(dlg, text="ARK Cluster Settings", font=("Bahnschrift SemiBold", 18, "bold")).pack(
            pady=(20, 10),
        )
        form = ctk.CTkFrame(dlg, fg_color=THEME["bg_card"], corner_radius=10)
        form.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        ctk.CTkLabel(form, text="Cluster", anchor="w", text_color=THEME["text_light"]).pack(
            fill="x", pady=(12, 0), padx=10,
        )
        cluster_var = ctk.StringVar(value=cluster_ids[0])
        cluster_combo = ttk.Combobox(
            form, textvariable=cluster_var, values=cluster_ids, state="readonly",
        )
        cluster_combo.pack(fill="x", padx=10, pady=(0, 6))
        pw_var = ctk.StringVar()
        apw_var = ctk.StringVar()
        mods_var = ctk.StringVar()
        server_name_var = ctk.StringVar()

        def load_settings(*_args):
            s = self.manager.get_cluster_settings(cluster_var.get())
            pw_var.set(s.get("serverPassword", ""))
            apw_var.set(s.get("adminPassword", ""))
            mods_var.set(s.get("mods", ""))
            names = s.get("serverNames") or {}
            server_name_var.set(next(iter(names.values()), ""))

        cluster_combo.bind("<<ComboboxSelected>>", load_settings)
        load_settings()
        ctk.CTkLabel(
            form, text="Server Password (shared)", anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", pady=(12, 0), padx=10)
        ctk.CTkEntry(form, textvariable=pw_var).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            form, text="Admin Password (shared)", anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", pady=(6, 0), padx=10)
        ctk.CTkEntry(form, textvariable=apw_var).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            form, text="Server Name (shared)", anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", pady=(12, 0), padx=10)
        ctk.CTkEntry(form, textvariable=server_name_var).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            form, text="Mod IDs (comma-separated)", anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", pady=(6, 0), padx=10)
        ctk.CTkEntry(form, textvariable=mods_var).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(
            form, text="Applied identically to every map in this cluster.",
            anchor="w", text_color=THEME["text_muted"],
        ).pack(fill="x", padx=10)

        def apply():
            cid = cluster_var.get().strip()
            if not cid:
                return
            server_name_val = server_name_var.get().strip()
            settings = {
                "serverPassword": pw_var.get().strip(),
                "adminPassword": apw_var.get().strip(),
                "mods": mods_var.get().strip(),
                "serverNames": {},
            }
            servers = self.manager.load_servers()
            for s in servers:
                if s.get("clusterId") == cid:
                    friendly = s.get("arkFriendly", "")
                    settings["serverNames"][friendly] = server_name_val
            dlg.destroy()
            try:
                changed = self.manager.apply_cluster_settings(cid, settings)
            except Exception as e:
                messagebox.showerror("Apply failed", str(e))
                return
            self.refresh_servers()
            self._toast("Cluster Settings", "Updated " + str(changed) + " servers.")

        buttons = ctk.CTkFrame(dlg, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(0, 20))
        ctk.CTkButton(
            buttons, text="Apply to all maps", fg_color=THEME["success"], command=apply,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            buttons, text="Gameplay Settings...", fg_color=THEME["accent_blue"],
            command=lambda: self.show_ark_gameplay_dialog(cluster_var.get().strip()),
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            buttons, text="Cancel", fg_color=THEME["border"], command=dlg.destroy,
        ).pack(side="right", padx=6)
