"""
Tofu Server Manager — Background Agent

The agent is the single owner of server processes. It keeps servers running
when the window is closed, restarts them after crashes, runs scheduled
backups/restarts/updates, tracks player activity for smart boot, and serves a
localhost API that the desktop app (and Discord bot) use to control servers.

Run:  python tofusm_agent.py        (or TofuSM.exe --agent)
Only one agent runs at a time; a second copy exits immediately.
"""

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.paths import base_dir  # noqa: E402

BASE_DIR = base_dir()   # honours dist\data_dir.txt, like the app

CONFIG_PATH = BASE_DIR / "servers.json"
AGENT_STATE_PATH = BASE_DIR / "agent_state.json"          # schedules (edited by the app)
SCHEDULE_RUNS_PATH = BASE_DIR / "runtime" / "schedule_runs.json"  # last run times (agent only)
AGENT_LOG_PATH = BASE_DIR / "logs" / "agent.log"
AGENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
_log_lock = threading.RLock()
_in_log = threading.local()


def agent_log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    if getattr(_in_log, "busy", False):
        # Something printed (e.g. a Python warning) while we were already writing the
        # log on this thread: drop it rather than recurse or deadlock.
        return
    _in_log.busy = True
    try:
        _write_log_line(line)
    finally:
        _in_log.busy = False


def _write_log_line(line: str):
    with _log_lock:
        try:
            if AGENT_LOG_PATH.exists() and AGENT_LOG_PATH.stat().st_size > 5 * 1024 * 1024:
                old = AGENT_LOG_PATH.with_suffix(".log.1")
                if old.exists():
                    old.unlink()
                AGENT_LOG_PATH.rename(old)
            with open(AGENT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


class _LogWriter:
    """Routes print() output from the engine into the agent log."""

    def __init__(self):
        self._buf = ""

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                agent_log(line)

    def flush(self):
        pass


def _route_output_to_log():
    """Everything the engine prints goes to logs/agent.log (the agent normally runs
    with no console). Pass --console to see it in a terminal instead. Only done when
    the agent actually runs, so importing this module never hijacks someone's output."""
    if "--console" not in sys.argv and not isinstance(sys.stdout, _LogWriter):
        sys.stdout = _LogWriter()
        sys.stderr = sys.stdout


def load_agent_state() -> dict:
    try:
        return json.loads(AGENT_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"schedules": {}, "last_runs": {}}


def load_schedule_runs(state: dict) -> dict:
    try:
        return json.loads(SCHEDULE_RUNS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return dict(state.get("last_runs", {}) or {})  # migrate from the old location


def save_schedule_runs(runs: dict):
    from core.paths import atomic_write_json
    atomic_write_json(SCHEDULE_RUNS_PATH, runs)


class TofuSMAgent:
    """Background process guardian for managed servers."""

    def __init__(self):
        from core.engine import build_engine
        self.engine = build_engine(role="agent", base_dir=BASE_DIR)
        self.mgr = self.engine.manager
        self.manager = self.mgr  # the Discord bot looks for .manager
        self._stop = threading.Event()
        self._busy: dict[str, threading.Lock] = {}
        self.api_server = None
        self.web_panel = None
        self.tracker = None
        self.smart_boot = None
        self._nightly_running = False
        from core.self_update import Watcher
        self.updater = Watcher()
        try:
            from activity_tracker import ActivityTracker
            self.tracker = ActivityTracker(str(CONFIG_PATH), manager=self.mgr)
        except Exception as e:
            agent_log(f"[WARN] activity tracker unavailable: {e}")
        try:
            from smart_boot import SmartBootManager
            self.smart_boot = SmartBootManager(str(CONFIG_PATH), activity_tracker=self.tracker)
        except Exception as e:
            agent_log(f"[WARN] smart boot unavailable: {e}")
        self.mgr.add_listener(self._on_event)
        from core.alerts import AlertCenter
        self.alerts = AlertCenter(self.mgr, log=agent_log)
        self.mgr.add_listener(self.alerts.on_event)
        self.engine.api.alerts = self.alerts

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        agent_log(f"Tofu agent starting (PID {os.getpid()})")
        from core.local_api import LocalApiServer
        self.api_server = LocalApiServer(self.engine.api, BASE_DIR)
        self.engine.api.on_shutdown = self.shutdown
        self.engine.api.on_web_reload = self._reload_web_panel
        self.engine.api.on_update = self.apply_update
        self.engine.api.on_discord_reload = self.restart_discord
        self.engine.api.updater = self.updater
        port = self.api_server.start()
        agent_log(f"[INFO] control API listening on 127.0.0.1:{port}")

        adopted = self.mgr.adopt_running_servers()
        if adopted:
            agent_log(f"[INFO] took over {len(adopted)} server(s) that were already running")
        self._boot_autostart(skip=set(adopted))
        self.mgr.start_player_polling(30)
        if self.tracker is not None:
            self.tracker.start_polling(60)
        threading.Thread(target=self._schedule_loop, daemon=True, name="schedules").start()
        threading.Thread(target=self._metrics_loop, daemon=True, name="metrics").start()
        threading.Thread(target=self._update_loop, daemon=True, name="update-check").start()
        self._start_discord()
        self._start_web_panel()
        threading.Thread(target=self._self_update_loop, daemon=True, name="self-update").start()
        threading.Thread(target=self._mod_update_loop, daemon=True, name="mod-updates").start()
        threading.Thread(target=self._status_page_loop, daemon=True, name="status-page-cfg").start()
        threading.Thread(target=self._backup_check_loop, daemon=True, name="backup-check").start()
        threading.Thread(target=self._new_version_loop, daemon=True, name="new-version").start()
        threading.Thread(target=self._links_loop, daemon=True, name="linked-pcs").start()
        self._start_heartbeat()

    # -- "is the server PC alive" ------------------------------------------------
    def _start_heartbeat(self):
        from core import heartbeat, nightly
        self.heartbeat = None
        if self.mgr.get_setting("heartbeat_enabled", False) is True:
            try:
                port = int(self.mgr.get_setting("heartbeat_port", heartbeat.DEFAULT_PORT) or heartbeat.DEFAULT_PORT)
                self.heartbeat = heartbeat.HeartbeatServer(
                    self.mgr, port, extra=lambda: {"nightly_running": self._nightly_running,
                                                   "next_nightly": nightly.next_run(self.mgr)})
                self.heartbeat.start()
                agent_log(f"[INFO] watchdog page on port {port} (/healthz)")
            except Exception as e:
                agent_log(f"[WARN] watchdog page not started: {e}")
        def notify(level, title, text):
            self.alerts.notify("host_down", None, title, text, level="error" if level == "down" else "info",
                               force=True)
        threading.Thread(target=heartbeat.watch_loop,
                         args=(lambda: self.mgr.get_setting("watch_host", "") or "", notify, self._stop),
                         kwargs={"log": agent_log}, daemon=True, name="watch-host").start()

    # -- Tofu updating itself ------------------------------------------------
    def _self_update_loop(self):
        from core import self_update
        msg = self_update.note_version(BASE_DIR, self.updater.started)
        if msg:
            agent_log("[INFO] " + msg)
            if not self._stop.wait(timeout=90):    # let the Discord bot connect first
                self._notify("Tofu updated", msg)
        while not self._stop.wait(timeout=120):
            try:
                st = self.updater.check()
                if not st.get("ready"):
                    continue
                policy = self.mgr.get_setting("self_update", "when_idle") or "when_idle"
                if policy != "when_idle" or self._nightly_running:
                    continue
                if time.time() < (getattr(self.mgr, "maintenance_until", 0) or 0):
                    continue
                if self_update.idle_enough(self.mgr):
                    self.apply_update(reason="nobody is playing")
                    return
            except Exception as e:
                agent_log(f"[WARN] self-update check: {e}")

    def apply_update(self, reason="asked from the panel"):
        """Hand over to the newly deployed Tofu; game servers keep running."""
        from core import self_update
        st = self.updater.check()
        if not st.get("ready"):
            raise RuntimeError("There's no newer Tofu on this PC - deploy one first.")
        agent_log(f"[INFO] switching to the new Tofu build ({reason})")
        self._notify("Updating Tofu", "The server PC is switching to the new Tofu build (%s). Game servers keep "
                     "running; the panel and Discord buttons are back in about a minute." % reason)
        if not self_update.restart_agent(BASE_DIR):
            raise RuntimeError("Only the installed TofuSM.exe can update itself.")
        threading.Thread(target=lambda: (time.sleep(1.0), self.shutdown(stop_servers=False)), daemon=True).start()
        return {"restarting": True}

    def _start_web_panel(self):
        """Browser control panel for guild admins (only when switched on in Settings)."""
        try:
            from core.web_panel import WebPanel
            self.web_panel = WebPanel(self.engine, BASE_DIR, log=agent_log)
            if self.web_panel.start() is None:
                agent_log("[INFO] web panel is off (turn it on in Tofu > Web Panel)")
        except OSError as e:
            agent_log(f"[WARN] web panel could not start (port in use?): {e}")
        except Exception as e:
            agent_log(f"[WARN] web panel not started: {e}")

    def _reload_web_panel(self):
        """Apply new web panel settings without restarting the agent (servers keep their console)."""
        if getattr(self, "heartbeat", None) is not None:
            self.heartbeat.stop()
        try:
            import discord_bot
            discord_bot.stop_bot()
        except Exception:
            pass
        if self.web_panel is not None:
            try:
                self.web_panel.stop()
            except Exception:
                pass
            self.web_panel = None
        self._start_web_panel()
        return self.web_panel.url if self.web_panel is not None else None

    def restart_discord(self) -> bool:
        """Reconnect the bot (the token was pasted or changed on the desktop)."""
        try:
            import discord_bot
            ok = discord_bot.restart_bot(app_instance=self)
            self.alerts.bot_sender = discord_bot.post_alert
            return ok
        except Exception as e:
            agent_log(f"[WARN] Discord restart: {e}")
            return False

    def _start_discord(self):
        """Run the Discord control panel here so it works with the window closed."""
        try:
            from core.secrets import bot_token
            try:
                raw = json.loads((BASE_DIR / "discord_config.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                raw = {}
            if not bot_token(self.manager.config_path.parent, raw):   # discord_config.json or the Discord bot window
                agent_log("[INFO] Discord bot not configured (no token)")
                return
        except Exception as e:
            agent_log(f"[WARN] could not read discord_config.json: {e}")
            return
        try:
            import discord_bot
            discord_bot.start_bot(app_instance=self)
            self.alerts.bot_sender = discord_bot.post_alert
            agent_log("[INFO] Discord bot started")
        except Exception as e:
            agent_log(f"[WARN] Discord bot not started: {e}")

    def run(self):
        self.start()
        try:
            while not self._stop.wait(timeout=1.0):
                pass
        except KeyboardInterrupt:
            self.shutdown(stop_servers=True)

    def shutdown(self, stop_servers: bool = True):
        if self._stop.is_set():
            return
        agent_log("Tofu agent stopping" + (" (stopping servers)" if stop_servers else " (servers left running)"))
        if self.tracker is not None:
            self.tracker.stop()
        if stop_servers:
            self.mgr.stop_all(user="agent")
        if self.web_panel is not None:
            try:
                self.web_panel.stop()
            except Exception:
                pass
        if self.api_server is not None:
            self.api_server.stop()
        self._stop.set()

    stop = shutdown  # backwards compatible name

    def _backup_check_loop(self):
        """Once a week: open the newest backup of every server and check it would restore."""
        from core import backup_check
        if self._stop.wait(timeout=900):
            return
        while True:
            try:
                if backup_check.due(self.mgr) and not self._nightly_running:
                    def notify(title, text):
                        self.alerts.notify("backup_failed", None, title, text, level="warn", force=True)
                    backup_check.check_all(self.mgr, notify=notify, log=agent_log)
            except Exception as e:
                agent_log(f"[WARN] backup check: {e}")
            if self._stop.wait(timeout=6 * 3600):
                return

    def _new_version_loop(self):
        """Once a day: is a newer Tofu published? (core/updates.py; off when no
        update repository is set). Tells the alert channel once per version."""
        from core import updates
        if self._stop.wait(timeout=600):
            return
        while True:
            try:
                if updates.enabled(self.mgr):
                    info = updates.check(self.mgr)
                    if info.get("newer") and self.mgr.get_setting("update_told", "") != info["latest"]:
                        self.mgr.set_setting("update_told", info["latest"])
                        self.alerts.notify("new_version", None, "🆕 " + updates.banner_text(info),
                                           "Open Tofu on the server PC and press Update"
                                           + (" - what's new: %s" % info["page"] if info.get("page") else "."),
                                           level="info", force=True)
            except Exception as e:
                agent_log(f"[WARN] update check: {e}")
            if self._stop.wait(timeout=6 * 3600):
                return

    def _links_loop(self):
        """Linked PCs: answer a linked Tofu while that's turned on (following setting
        changes), and keep the list of servers on the PCs linked here fresh."""
        from core import linked_pcs
        server, current = None, None
        fleet = linked_pcs.fleet(self.mgr)
        while True:
            try:
                cfg = linked_pcs.host_config(self.mgr)
                want = cfg["port"] if cfg["enabled"] else None
                if want != current:
                    if server is not None:
                        server.stop()
                        server = None
                    if want is not None:
                        linked_pcs.host_key(self.mgr)
                        server = linked_pcs.LinkServer(self.mgr, self.engine.api, want, log=agent_log)
                        server.start()
                    current = want
                if fleet.pcs():
                    fleet.refresh()
            except Exception as e:
                agent_log(f"[WARN] linked PCs: {e}")
                current = None
            if self._stop.wait(timeout=10):
                if server is not None:
                    server.stop()
                return

    def _status_page_loop(self):
        """Run the public status page while it's turned on; follow setting changes."""
        from core import status_page
        server, current = None, None
        while True:
            try:
                cfg = status_page.config(self.mgr)
                want = (cfg["port"], cfg["public"]) if cfg["enabled"] else None
                if want != current:
                    if server is not None:
                        server.stop()
                        server = None
                    if want:
                        server = status_page.StatusServer(self.mgr, BASE_DIR, port=cfg["port"], public=cfg["public"])
                        server.start()
                        agent_log(f"[INFO] status page on port {cfg['port']}"
                                  + (" (open to the internet if forwarded)" if cfg["public"] else ""))
                    current = want
            except Exception as e:
                agent_log(f"[WARN] status page: {e}")
                current = None
            if self._stop.wait(timeout=30):
                if server is not None:
                    server.stop()
                return

    def _mod_update_loop(self):
        """Every few hours: look for mod updates and post them to Discord. With
        automatic updates and no nightly restart, stopped servers get them right away."""
        from core import mod_updates, nightly
        if self._stop.wait(timeout=300):
            return
        while True:
            try:
                if mod_updates.mode(self.mgr) != "off":
                    mod_updates.check_and_notify(self.mgr, self._notify, log=agent_log)
                    if not nightly.config(self.mgr)["enabled"] and not self._nightly_running:
                        done = mod_updates.apply_pending(self.mgr, log=agent_log)
                        if done:
                            self._notify("\U0001F9E9 Mods updated", "\n".join(done))
            except Exception as e:
                agent_log(f"[WARN] mod update check: {e}")
            try:
                hours = max(1.0, float(self.mgr.get_setting("mod_check_hours", 6) or 6))
            except (TypeError, ValueError):
                hours = 6.0
            if self._stop.wait(timeout=hours * 3600):
                return

    def _on_event(self, event, server_id, info):
        name = info.get("name", server_id)
        if event == "crashed":
            try:
                from core import mod_updates
                mod_updates.on_crash(self.mgr, server_id, notify=self._notify, log=agent_log)
            except Exception as e:
                agent_log(f"[WARN] mod rollback check: {e}")
            agent_log(f"[WARN] {name}: exited unexpectedly (code {info.get('exit_code')}, "
                      f"up {int(info.get('uptime') or 0)}s)")
        elif event == "gave_up":
            agent_log(f"[ERROR] {name}: crashed {info.get('restarts')} times in a row; auto-restart paused")
        elif event in ("started", "stopped", "restarting", "updating", "update_failed"):
            extra = {k: v for k, v in info.items() if k != "name"}
            agent_log(f"[INFO] {name}: {event} {extra if extra else ''}".rstrip())

    # -- smart boot ----------------------------------------------------------
    def _boot_autostart(self, skip):
        try:
            days = int(self.mgr.get_setting("smart_boot_days", 0) or 0)
            if days and self.smart_boot is not None:
                self.smart_boot.boot_config["inactive_days_threshold"] = days
        except (TypeError, ValueError):
            pass
        from core import nightly
        try:
            plan = nightly.take_boot_plan(self.mgr)
        except Exception as e:
            agent_log(f"[WARN] nightly boot plan unreadable: {e}")
            plan = None
        if plan is not None:
            self._boot_after_nightly(plan, skip)
            return
        for srv in self.mgr.load_servers():
            sid = srv.get("id")
            if not sid or sid in skip or self.mgr.is_running(sid):
                continue
            rec = self.mgr._read_registry(sid)
            was_running = rec.get("state") in ("running", "restarting", "starting", "updating")
            if self.smart_boot is not None:
                start, reason = self.smart_boot.decide(srv, was_running=was_running)
            else:
                start, reason = was_running, "was running before shutdown"
            agent_log(f"[BOOT] {srv.get('name')}: {'start' if start else 'skip'} ({reason})")
            if not start:
                if was_running:
                    self.mgr._set_state(sid, "stopped", pid=None)
                continue
            threading.Thread(target=self._safe_start, args=(sid,), daemon=True).start()

    def _boot_after_nightly(self, plan, skip):
        """Back from a nightly restart: start exactly the servers the plan says."""
        back = set(plan.get("bring_back") or [])
        names = {s["id"]: s.get("name") or s["id"] for s in self.mgr.load_servers() if s.get("id")}
        for sid in names:
            if sid in skip or self.mgr.is_running(sid):
                continue
            if sid in back:
                agent_log(f"[BOOT] {names[sid]}: start (back from the nightly restart)")
                threading.Thread(target=self._safe_start, args=(sid,), daemon=True).start()
            else:
                rec = self.mgr._read_registry(sid)
                if rec.get("state") in ("running", "restarting", "starting", "updating"):
                    self.mgr._set_state(sid, "stopped", pid=None)

        def announce():
            # give the Discord bot a moment to connect, and the servers a moment to start
            self._stop.wait(timeout=90)
            up = [names[i] for i in names if i in back]
            sleeping = [names.get(i, i) for i in plan.get("sleeping") or [] if i in names]
            text = "Back up: %s." % (", ".join(up) or "none")
            if sleeping:
                text += " Sleeping (press Start to wake one): %s." % ", ".join(sleeping)
            self._notify("Nightly restart finished", text)
        threading.Thread(target=announce, daemon=True, name="nightly-announce").start()

    def _notify(self, title, text):
        agent_log(f"[NIGHTLY] {title} - {text}")
        try:
            self.alerts._deliver(title, text, 0xE6C87E)
        except Exception as e:
            agent_log(f"[WARN] Discord notice failed: {e}")

    def _check_nightly(self):
        from core import nightly
        if self._nightly_running or not nightly.due(self.mgr):
            return
        self._nightly_running = True

        def go():
            rebooting = False
            try:
                agent_log("[NIGHTLY] starting the nightly restart")
                # no new starts (Discord, schedules) while servers stop and Windows restarts
                self.mgr.maintenance_until = time.time() + 3600
                res = nightly.run(self.mgr, tracker=self.tracker, notify=self._notify, log=agent_log,
                                  start=self._safe_start_now)
                rebooting = bool(res.get("rebooted"))
            except Exception as e:
                agent_log(f"[ERROR] nightly restart: {e}")
            finally:
                if not rebooting:
                    self.mgr.maintenance_until = 0
                self._nightly_running = False
        threading.Thread(target=go, daemon=True, name="nightly").start()

    def _safe_start_now(self, sid):
        self.mgr.maintenance_until = 0
        self._safe_start(sid)

    def _safe_start(self, sid):
        try:
            self.mgr.start_server(sid, user="smart-boot")
        except Exception as e:
            agent_log(f"[ERROR] auto-start {sid}: {e}")

    # -- schedules -------------------------------------------------------------
    def _schedule_loop(self):
        while not self._stop.wait(timeout=20):
            try:
                self._check_schedules()
            except Exception as e:
                agent_log(f"[ERROR] schedule tick: {e}")
            try:
                self._check_nightly()
            except Exception as e:
                agent_log(f"[ERROR] nightly tick: {e}")

    @staticmethod
    def _schedule_due(cfg, last, now):
        """Interval: every N hours. Daily: once per day at/after any listed HH:MM
        (catches up within 2 hours if the PC was asleep at that minute)."""
        mode = cfg.get("mode", "interval")
        if mode == "daily":
            today = datetime.fromtimestamp(now)
            for hm in cfg.get("times", []) or []:
                try:
                    h, m = (int(x) for x in str(hm).split(":", 1))
                except ValueError:
                    continue
                slot = today.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
                if slot <= now <= slot + 2 * 3600 and last < slot:
                    return True
            return False
        try:
            hours = float(cfg.get("hours", 24) or 24)
        except (TypeError, ValueError):
            hours = 24.0
        if last <= 0:
            return False  # first sighting: start the interval clock now
        return (now - last) >= hours * 3600

    def _check_schedules(self):
        state = load_agent_state()
        schedules = state.get("schedules", {}) or {}
        last_runs = load_schedule_runs(state)
        now = time.time()
        changed = False
        for sid, kinds in schedules.items():
            if not isinstance(kinds, dict) or self.mgr.get_server(sid) is None:
                continue
            for kind in ("backup", "restart", "update", "message"):
                cfg = kinds.get(kind)
                if not isinstance(cfg, dict) or not cfg.get("enabled"):
                    continue
                key = sid + "|" + kind
                last = float(last_runs.get(key, 0) or 0)
                if last <= 0 and cfg.get("mode", "interval") != "daily":
                    last_runs[key] = now
                    changed = True
                    continue
                if self._schedule_due(cfg, last, now):
                    last_runs[key] = now
                    changed = True
                    threading.Thread(target=self._run_scheduled, args=(sid, kind, cfg),
                                     daemon=True, name=f"sched-{kind}-{sid}").start()
        if changed:
            save_schedule_runs(last_runs)

    def _wait_for_empty(self, sid, name, cfg) -> bool:
        """For "skip if players online": wait up to postpone_minutes for the
        server to empty. Returns True if it is OK to go ahead."""
        if not cfg.get("skip_if_players"):
            return True
        limit = time.time() + float(cfg.get("postpone_minutes", 120) or 0) * 60
        while True:
            online = self.mgr.player_count(sid)
            if not online:
                return True
            if time.time() >= limit:
                agent_log(f"[INFO] {name}: still {online} player(s) online; skipping this scheduled run")
                return False
            agent_log(f"[INFO] {name}: {online} player(s) online; postponing")
            if self._stop.wait(timeout=300):
                return False

    def _run_scheduled(self, sid, kind, cfg=None):
        cfg = cfg or {}
        lock = self._busy.setdefault(sid, threading.Lock())
        if not lock.acquire(blocking=False):
            agent_log(f"[INFO] scheduled {kind} for {sid} skipped (another task is running)")
            return
        try:
            srv = self.mgr.get_server(sid) or {}
            name = srv.get("name", sid)
            if kind == "message":
                text = (cfg.get("text") or "").strip()
                if text and self.mgr.is_running(sid):
                    if self.mgr.broadcast(sid, text):
                        agent_log(f"[INFO] {name}: announced \"{text[:60]}\"")
                return
            if kind == "backup":
                r = self.mgr.backup_server_world(sid, reason="scheduled")
                agent_log(f"[INFO] scheduled backup {name}: {r['files']} files -> {r['zip_path']}"
                          + (f" ({r['pruned']} old removed)" if r.get("pruned") else ""))
                if r.get("warning"):
                    agent_log(f"[WARN] {name}: {r['warning']}")
                    self.alerts.backup_failed(sid, r["warning"])
                if r.get("mirror_error"):
                    agent_log(f"[WARN] {name}: copy to the second backup folder failed: {r['mirror_error']}")
                    self.alerts.backup_failed(sid, "The backup was made, but copying it to the second backup "
                                                   "folder failed: " + r["mirror_error"])
            elif kind == "restart":
                if self.mgr.is_running(sid) and self._wait_for_empty(sid, name, cfg):
                    agent_log(f"[INFO] scheduled restart: {name}")
                    self.mgr.warn_players([sid], "restarting", cfg.get("warn_minutes"))
                    self.mgr.restart_server(sid, user="schedule")
            elif kind == "update":
                if not self._wait_for_empty(sid, name, cfg):
                    return
                resp = self.engine.api.process(
                    "server.update", {"server_id": sid, "warn_minutes": cfg.get("warn_minutes")},
                    user_id="schedule")
                if not resp.success:
                    agent_log(f"[ERROR] scheduled update {name}: {resp.error}")
                    self.alerts.on_event("update_failed", sid, {"name": name, "error": resp.error})
                else:
                    agent_log(f"[INFO] scheduled update {name}: {resp.data.get('status')}")
        except Exception as e:
            agent_log(f"[ERROR] scheduled {kind} {sid}: {e}")
            if kind == "backup":
                self.alerts.backup_failed(sid, str(e))
            elif kind == "update":
                self.alerts.on_event("update_failed", sid, {"name": sid, "error": str(e)})
        finally:
            lock.release()

    # -- idle auto-stop ------------------------------------------------------------
    def _check_idle(self, idle):
        """Stop servers that have had zero players for their idleStopMinutes.
        Only servers whose player count is known (Steam query / RCON) qualify."""
        for srv in self.mgr.load_servers():
            sid = srv.get("id")
            minutes = _idle_minutes(srv)
            if not sid or not minutes or not self.mgr.is_running(sid):
                idle.forget(sid)
                continue
            if self.mgr._states.get(sid) in ("updating", "stopping", "starting", "restarting"):
                continue
            if idle.update(sid, self.mgr.player_count(sid), minutes):
                idle.forget(sid)
                agent_log(f"[INFO] {srv.get('name')}: no players for {int(minutes)} min; stopping (idle auto-stop)")
                threading.Thread(target=self.mgr.stop_server, args=(sid, "idle-stop"), daemon=True).start()

    # -- game updates ---------------------------------------------------------------
    def _update_loop(self):
        """Every few hours: ask Steam which servers have a newer build (badges on the
        cards); servers with "auto-update when empty" are updated right away if they
        are stopped or nobody is online."""
        if self._stop.wait(timeout=120):
            return
        while True:
            try:
                hours = float(self.mgr.get_setting("update_check_hours", 6) or 6)
            except (TypeError, ValueError):
                hours = 6.0
            try:
                flags = self.mgr.refresh_update_flags(max_age_hours=max(1.0, hours))
                self._auto_update(flags)
            except Exception as e:
                agent_log(f"[WARN] update check: {e}")
            if self._stop.wait(timeout=1800):
                return

    def _auto_update(self, flags):
        done_groups = set()
        for sid, avail in flags.items():
            srv = self.mgr.get_server(sid) or {}
            if not avail or not srv.get("autoUpdateWhenEmpty"):
                continue
            if self.mgr._read_registry(sid).get("update_major"):
                continue   # e.g. Vintage Story 1.21 -> 1.22: mods may break, update by hand
            group = tuple(sorted(self.mgr.update_group(sid)))   # whole ARK cluster updates together
            if group in done_groups:
                continue
            running = [g for g in group if self.mgr.is_running(g)]
            if any(self.mgr.player_count(g) is None for g in running):
                continue   # can't tell if anyone is playing - leave it for a schedule
            if any(self.mgr.player_count(g) for g in running):
                continue
            if any(self.mgr._states.get(g) in ("updating", "stopping", "starting", "restarting") for g in group):
                continue
            done_groups.add(group)
            agent_log(f"[INFO] {srv.get('name')}: game update available and nobody is online - updating")
            resp = self.engine.api.process("server.update", {"server_id": sid}, user_id="auto-update")
            if not resp.success:
                agent_log(f"[ERROR] auto-update {srv.get('name')}: {resp.error}")
                self.alerts.on_event("update_failed", sid, {"name": srv.get("name"), "error": resp.error})

    # -- metrics -----------------------------------------------------------------
    def _metrics_loop(self):
        from core.hosting import IdleTracker
        from core.mem_guard import MemGuard
        db = self.engine.db
        idle = IdleTracker()
        guard = MemGuard(self.mgr, notify=self._notify, log=agent_log, db=db)
        from core.player_stats import PlayerStats
        stats = PlayerStats(str(BASE_DIR / "runtime" / "player_stats.json"))
        last_cleanup = 0.0
        while not self._stop.wait(timeout=60):
            self._check_idle(idle)
            try:
                statuses = self.mgr.get_all_statuses()
                self.alerts.check_resources(statuses)
            except Exception as e:
                agent_log(f"[WARN] alerts: {e}")
                statuses = []
            try:
                guard.step(statuses)
            except Exception as e:
                agent_log(f"[WARN] memory guard: {e}")
            try:
                stats.record(statuses)
            except Exception as e:
                agent_log(f"[WARN] player stats: {e}")
            if db is None:
                continue
            try:
                from core.history import record, system_sample
                record(db, statuses, system_sample(statuses))
                if time.time() - last_cleanup > 6 * 3600:
                    db.cleanup_old_metrics(86400 * 14)
                    last_cleanup = time.time()
            except Exception as e:
                agent_log(f"[WARN] metrics: {e}")


def _idle_minutes(srv):
    try:
        return float(srv.get("idleStopMinutes") or 0)
    except (TypeError, ValueError):
        return 0.0


def main():
    _route_output_to_log()
    from core.local_api import AgentClient
    if AgentClient.discover(BASE_DIR) is not None:
        agent_log("[INFO] another Tofu agent is already running; exiting")
        return
    agent = TofuSMAgent()
    try:
        agent.run()
    finally:
        if not agent._stop.is_set():
            agent.shutdown(stop_servers=False)


if __name__ == "__main__":
    main()
