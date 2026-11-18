"""The Add / Edit Server form. Part of the desktop app (TofuApp in app.py)."""
# flake8: noqa
import os
import time

import customtkinter as ctk
import tkinter.messagebox as messagebox
from tkinter import ttk
from tkinter import filedialog

from ui.app_common import THEME
from core.hosting import format_core_list, parse_core_list, suggest_core_block, PRIORITIES
from core.game_catalog import GAME_PRESETS


class ServerFormMixin:
    # ------------------------------------------------------------------ #
    # Add / edit server
    # ------------------------------------------------------------------ #
    def _open_server_form(self, title, existing, submit_text):
        dlg = ctk.CTkToplevel(self)
        dlg.title(title)
        dlg.geometry("560x720")
        dlg.grab_set()
        dlg.transient(self)
        self.center_window(dlg, 560, 720)
        ctk.CTkLabel(dlg, text=title, font=("Bahnschrift SemiBold", 18, "bold")).pack(pady=(20, 10))
        form_frame = ctk.CTkFrame(dlg, fg_color=THEME["bg_card"], corner_radius=10)
        form_frame.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        existing = existing or {}

        name_var = ctk.StringVar(value=existing.get("name", ""))
        folder_var = ctk.StringVar(value=existing.get("folder", ""))
        type_var = ctk.StringVar(value=existing.get("type", "steamcmd"))
        dir_var = ctk.StringVar(
            value=existing.get("directory")
            or self.manager.get_setting("default_install_dir", "")
            or "",
        )
        exec_var = ctk.StringVar(value=existing.get("executable") or "")
        args_var = ctk.StringVar(value=existing.get("launchArgs", ""))
        appid_var = ctk.StringVar(value=existing.get("appId") or "")
        update_var = ctk.BooleanVar(value=bool(existing.get("updateBeforeStart", True)))
        auto_restart_var = ctk.BooleanVar(value=bool(existing.get("autoRestart", True)))
        requires_account_var = ctk.BooleanVar(
            value=bool(existing.get("requiresAccount", False))
        )
        port_var = ctk.StringVar(value=str(existing.get("port", "") or ""))
        preset_var = ctk.StringVar(value="Custom / Other")
        manual_var = ctk.BooleanVar(value=bool(
            existing.get("manualStart", bool(existing.get("clusterId")))))
        stopcmd_var = ctk.StringVar(value=existing.get("stopCommand", "") or "")
        try:
            _cores0 = format_core_list(parse_core_list(existing.get("cpuAffinity")))
        except ValueError:
            _cores0 = str(existing.get("cpuAffinity") or "")
        cores_var = ctk.StringVar(value=_cores0)
        prio_var = ctk.StringVar(value=existing.get("priority") or "normal")
        idle_var = ctk.StringVar(value=str(existing.get("idleStopMinutes") or ""))
        ram_alert_var = ctk.StringVar(value=str(existing.get("ramAlertMB") or ""))
        mem_limit_var = ctk.StringVar(value=str(existing.get("memLimitMB") or ""))

        def make_field(label_text, var, placeholder=""):
            ctk.CTkLabel(
                form_frame, text=label_text, anchor="w", text_color=THEME["text_light"],
            ).pack(fill="x", pady=(8, 0))
            ctk.CTkEntry(form_frame, textvariable=var, placeholder_text=placeholder).pack(
                fill="x", pady=(0, 6),
            )

        make_field("Name *", name_var)
        make_field("Folder / Group (optional)", folder_var, "e.g. ARK, Friends, Public")
        ctk.CTkLabel(form_frame, text="Type *", anchor="w", text_color=THEME["text_light"]).pack(
            fill="x", pady=(8, 0),
        )
        combo_type = ttk.Combobox(
            form_frame, textvariable=type_var,
            values=["steamcmd", "custom", "vintage-story"], state="readonly",
        )
        combo_type.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(
            form_frame, text="Game Preset (auto-fills defaults)",
            anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", pady=(8, 0))
        combo_preset = ttk.Combobox(
            form_frame, textvariable=preset_var,
            values=list(GAME_PRESETS.keys()), state="readonly",
        )
        combo_preset.pack(fill="x", pady=(0, 6))
        preset_desc_label = ctk.CTkLabel(
            form_frame, text="", anchor="w", text_color=THEME["text_muted"], wraplength=500,
        )
        preset_desc_label.pack(fill="x", padx=10, pady=(0, 6))

        def on_preset_change(*_args):
            preset = GAME_PRESETS.get(preset_var.get(), {})
            desc = preset.get("description", "")
            if preset_var.get() != "Custom / Other":
                type_var.set(preset.get("type", "custom"))
                p = preset.get("port")
                port_var.set(str(p) if p else "")
                a = preset.get("appId")
                appid_var.set(str(a) if a else "")
                exec_var.set(preset.get("executable") or "")
                requires_account_var.set(
                    not bool(preset.get("anonymous_login", True)))
                on_type_change()
                if requires_account_var.get():
                    desc = desc + "  [Needs Steam account login]"
            preset_desc_label.configure(text=desc)

        preset_var.trace("w", on_preset_change)

        ctk.CTkLabel(
            form_frame, text="Server Port", anchor="w", text_color=THEME["text_light"],
        ).pack(fill="x", pady=(8, 0))
        ctk.CTkEntry(
            form_frame, textvariable=port_var, placeholder_text="e.g. 42420 for Vintage Story",
        ).pack(fill="x", pady=(0, 6))

        dir_row = ctk.CTkFrame(form_frame, fg_color="transparent")
        dir_row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(dir_row, text="Directory *", anchor="w", text_color=THEME["text_light"]).pack(
            fill="x",
        )
        dir_entry = ctk.CTkEntry(dir_row, textvariable=dir_var)
        dir_entry.pack(side="left", fill="x", expand=True, pady=(0, 6))

        def browse_dir():
            chosen = filedialog.askdirectory(initialdir=dir_var.get() or os.path.expanduser("~"))
            if chosen:
                dir_var.set(chosen)

        ctk.CTkButton(
            dir_row, text="Browse...", width=70, fg_color=THEME["border"], command=browse_dir,
        ).pack(side="left", padx=(6, 0), pady=(0, 6))

        make_field("Executable (optional)", exec_var, "blank = auto-detect")
        make_field("Launch Args (optional)", args_var)
        from core.minecraft import looks_minecraft as _looks_mc
        is_mc = bool(existing) and not existing.get("appId") and _looks_mc(existing)
        java_mem_var = ctk.StringVar(value=str(existing.get("javaMemoryMB") or "") if existing else "")
        java_path_var = ctk.StringVar(value=(existing or {}).get("javaPath") or "")
        java_args_var = ctk.StringVar(value=(existing or {}).get("javaArgs") or "")
        if is_mc:
            make_field("Minecraft memory in MB (blank = 4096)", java_mem_var, "e.g. 6144")
            make_field("Java to use (blank = Tofu picks the right one)", java_path_var,
                       "e.g. C:\\Program Files\\Eclipse Adoptium\\jdk-21\\bin\\java.exe")
            make_field("Extra Java flags (optional)", java_args_var, "e.g. -XX:+UseG1GC")

        steam_frame = ctk.CTkFrame(form_frame, fg_color=THEME["bg_input"], corner_radius=8)
        ctk.CTkLabel(steam_frame, text="Steam App ID", anchor="w", text_color=THEME["text_light"]).pack(
            fill="x", pady=(8, 0),
        )
        ctk.CTkEntry(steam_frame, textvariable=appid_var, placeholder_text="e.g. 730 for CS2").pack(
            fill="x", pady=(0, 6),
        )
        ws_appid_var = ctk.StringVar(value=str(existing.get("workshopAppId") or ""))
        ctk.CTkLabel(steam_frame, text="Workshop App ID (the game's own ID, for mods; blank = same as above)",
                     anchor="w", text_color=THEME["text_light"]).pack(fill="x", pady=(4, 0))
        ctk.CTkEntry(steam_frame, textvariable=ws_appid_var, placeholder_text="e.g. 108600 for Project Zomboid").pack(
            fill="x", pady=(0, 6),
        )
        ctk.CTkCheckBox(
            steam_frame, text="Update before start (validate files every launch)",
            variable=update_var, fg_color=THEME["accent_blue"],
        ).pack(anchor="w", pady=(0, 4))
        auto_update_var = ctk.BooleanVar(value=bool(existing.get("autoUpdateWhenEmpty")))
        ctk.CTkCheckBox(
            steam_frame, text="Update the game automatically when nobody is online",
            variable=auto_update_var, fg_color=THEME["accent_blue"],
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkCheckBox(
            steam_frame, text="Auto-restart on crash",
            variable=auto_restart_var, fg_color=THEME["accent_blue"],
        ).pack(anchor="w", pady=(0, 8))

        adv = ctk.CTkFrame(form_frame, fg_color="transparent")
        adv.pack(fill="x", pady=(4, 4))
        ctk.CTkCheckBox(
            adv, text="Manual start only (never auto-start at boot)",
            variable=manual_var, fg_color=THEME["accent_blue"],
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(adv, text="Stop command (optional, e.g. /stop or quit; separate several with ;)",
                     anchor="w", text_color=THEME["text_light"]).pack(fill="x")
        ctk.CTkEntry(adv, textvariable=stopcmd_var,
                     placeholder_text="Leave empty to use the game's default").pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(adv, text="CPU cores (e.g. 0-15; empty = all cores)  \u00b7  priority",
                     anchor="w", text_color=THEME["text_light"]).pack(fill="x")
        cpu_row = ctk.CTkFrame(adv, fg_color="transparent")
        cpu_row.pack(fill="x", pady=(0, 4))
        ctk.CTkEntry(cpu_row, textvariable=cores_var, placeholder_text="all cores").pack(
            side="left", fill="x", expand=True)

        def auto_cores():
            used = []
            for s in self.manager.load_servers():
                if s.get("id") == existing.get("id"):
                    continue
                try:
                    used.append(parse_core_list(s.get("cpuAffinity")))
                except ValueError:
                    pass
            cores_var.set(format_core_list(suggest_core_block(used)))
        ctk.CTkButton(cpu_row, text="Auto", width=60, fg_color=THEME["border"],
                      command=auto_cores).pack(side="left", padx=(6, 0))
        ctk.CTkOptionMenu(cpu_row, variable=prio_var, values=list(PRIORITIES), width=130).pack(
            side="left", padx=(6, 0))
        ctk.CTkLabel(adv, text="Auto-stop after this many minutes with no players (empty = never; "
                     "needs a game that reports players)", anchor="w", wraplength=480, justify="left",
                     text_color=THEME["text_light"]).pack(fill="x")
        ctk.CTkEntry(adv, textvariable=idle_var, width=120, placeholder_text="e.g. 30").pack(
            anchor="w", pady=(0, 4))
        ctk.CTkLabel(adv, text="Discord alert when this server uses more RAM than (MB, empty = off)",
                     anchor="w", wraplength=480, justify="left",
                     text_color=THEME["text_light"]).pack(fill="x")
        ctk.CTkEntry(adv, textvariable=ram_alert_var, width=120, placeholder_text="e.g. 16000").pack(
            anchor="w", pady=(0, 4))
        ctk.CTkLabel(adv, text="Restart it cleanly when it stays above this much RAM for 3 minutes (MB, empty = "
                     "off). Players get a 5 and 1 minute warning.", anchor="w", wraplength=480, justify="left",
                     text_color=THEME["text_light"]).pack(fill="x")
        ctk.CTkEntry(adv, textvariable=mem_limit_var, width=120, placeholder_text="e.g. 24000").pack(
            anchor="w", pady=(0, 4))

        def on_type_change(*_args):
            if type_var.get() == "steamcmd":
                steam_frame.pack(fill="x", padx=10, pady=(0, 8))
            else:
                steam_frame.pack_forget()

        type_var.trace("w", on_type_change)
        if type_var.get() == "steamcmd":
            steam_frame.pack(fill="x", padx=10, pady=(0, 8))
        else:
            steam_frame.pack_forget()

        def save():
            name_val = name_var.get().strip()
            dir_val = dir_var.get().strip()
            appid_val = appid_var.get().strip()
            if not name_val:
                messagebox.showwarning("Name required", "Server name cannot be empty.")
                return
            if not dir_val:
                messagebox.showwarning("Directory required", "Choose the server install directory.")
                return
            if type_var.get() == "steamcmd" and not appid_val:
                messagebox.showwarning("App ID required", "Steam App ID is required for SteamCMD servers.")
                return

            if not existing.get("id"):
                safe_name = "".join(
                    c if c.isalnum() or c in " -_." else "_" for c in name_val
                ).strip()
                if not safe_name:
                    safe_name = "server"
                target_dir = os.path.join(dir_val, safe_name)
                try:
                    os.makedirs(target_dir, exist_ok=True)
                except Exception as e:
                    messagebox.showerror(
                        "Folder creation failed", "Could not create server folder: " + str(e),
                    )
                    return
                dir_val = target_dir

            port_int = None
            port_val = port_var.get().strip()
            if port_val:
                try:
                    port_int = int(port_val)
                    if port_int < 1 or port_int > 65535:
                        raise ValueError()
                except ValueError:
                    messagebox.showwarning("Invalid port", "Port must be a number between 1 and 65535.")
                    return
                for s in self.manager.load_servers():
                    if s.get("id") == existing.get("id"):
                        continue
                    if s.get("port") == port_int:
                        messagebox.showwarning(
                            "Port conflict",
                            "Another server ('" + s.get("name", "unknown") + "') already uses port "
                            + str(port_int) + ". Please choose a different port.",
                        )
                        return

            entry = {
                "name": name_val,
                "type": type_var.get(),
                "directory": dir_val,
                "port": port_int,
                "executable": exec_var.get().strip() or None,
                "launchArgs": args_var.get().strip(),
                "appId": (appid_val if type_var.get() == "steamcmd"
                          else (appid_val or existing.get("appId"))),
                "requiresAccount": bool(requires_account_var.get()),
                "updateBeforeStart": bool(update_var.get()),
                "autoRestart": bool(auto_restart_var.get()),
                "maxRestarts": existing.get("maxRestarts"),
                "folder": folder_var.get().strip(),
                "manualStart": bool(manual_var.get()),
                "stopCommand": stopcmd_var.get().strip(),
            }
            try:
                cores = parse_core_list(cores_var.get())
            except ValueError:
                messagebox.showwarning("CPU cores", "Use core numbers like 0-15 or 0-7, 16-23.")
                return
            entry["cpuAffinity"] = format_core_list(cores) if cores else ""
            entry["priority"] = prio_var.get() if prio_var.get() in PRIORITIES else "normal"
            try:
                entry["idleStopMinutes"] = max(0, int(idle_var.get())) if idle_var.get().strip() else 0
            except ValueError:
                messagebox.showwarning("Auto-stop", "Minutes must be a whole number.")
                return
            try:
                ram_cap = int(ram_alert_var.get()) if ram_alert_var.get().strip() else 0
            except ValueError:
                messagebox.showwarning("RAM alert", "Use a whole number of MB, e.g. 16000.")
                return
            entry["ramAlertMB"] = max(0, ram_cap)   # 0 = no alert
            try:
                mem_cap = int(mem_limit_var.get()) if mem_limit_var.get().strip() else 0
            except ValueError:
                messagebox.showwarning("RAM limit", "Use a whole number of MB, e.g. 24000.")
                return
            if 0 < mem_cap < 512:
                messagebox.showwarning("RAM limit", "That's too low for a game server - use at least 512 MB.")
                return
            entry["memLimitMB"] = max(0, mem_cap)   # 0 = no restarts
            entry["workshopAppId"] = ws_appid_var.get().strip()
            entry["autoUpdateWhenEmpty"] = bool(auto_update_var.get())
            if is_mc:
                try:
                    mem = int(java_mem_var.get()) if java_mem_var.get().strip() else None
                    if mem is not None and not 512 <= mem <= 262144:
                        raise ValueError
                except ValueError:
                    messagebox.showwarning("Minecraft memory", "Use a number of MB between 512 and 262144.")
                    return
                entry["javaMemoryMB"] = mem
                entry["javaPath"] = java_path_var.get().strip().strip('"') or None
                entry["javaArgs"] = java_args_var.get().strip() or None
            if not entry["stopCommand"]:
                entry.pop("stopCommand")
            if entry.get("maxRestarts") is None:
                entry.pop("maxRestarts")
            try:
                servers = self.manager.load_servers()
                if existing.get("id"):
                    entry["id"] = existing["id"]
                    merged = dict(existing)   # keep fields this form doesn't show
                    merged.update(entry)
                    for k in ("javaMemoryMB", "javaPath", "javaArgs"):
                        if merged.get(k) is None:
                            merged.pop(k, None)
                    if "stopCommand" not in entry:
                        merged.pop("stopCommand", None)
                    entry = merged
                    for i, s in enumerate(servers):
                        if s["id"] == existing["id"]:
                            servers[i] = entry
                            break
                else:
                    entry["id"] = "srv_" + str(int(time.time() * 1000))
                    servers.append(entry)
                self.manager.save_servers(servers)
            except Exception as e:
                messagebox.showerror("Save failed", "Could not save server definition: " + str(e))
                return
            dlg.destroy()
            self.refresh_servers()

        btn_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 20))
        ctk.CTkButton(btn_frame, text=submit_text, fg_color=THEME["success"], command=save).pack(
            side="left", padx=6,
        )
        ctk.CTkButton(btn_frame, text="Cancel", fg_color=THEME["border"], command=dlg.destroy).pack(
            side="right", padx=6,
        )
