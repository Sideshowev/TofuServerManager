"""Discord alerts: crashes, crash loops, failed updates/backups, high RAM, low disk.

Runs in the background agent. Alerts go to Discord in one of two ways:
  * through the Tofu bot, if it is running (channel ``alert_channel_id`` in
    discord_config.json, else its log channel, else its panel channel), or
  * through a Discord webhook URL (Settings > Alerts), which needs no bot.

Settings (settings.json):
  alerts_enabled      True/False (default True)
  alert_webhook_url   Discord webhook (stored encrypted)
  alert_events        list of: crash, gave_up, update_failed, backup_failed, ram, disk
  alert_ram_percent   whole-PC RAM use that triggers an alert (default 90)
  alert_disk_gb       free space on a server's drive that triggers an alert (default 5)
Per server: ``ramAlertMB`` alerts when that server alone uses more.

The same alert for the same server is sent at most once per cooldown, so a
crash loop sends one "crashed" and one "gave up", not ten messages.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import urllib.request
from typing import Callable, Dict, Optional

ALL_EVENTS = ("crash", "gave_up", "update_failed", "backup_failed", "ram", "disk")
EVENT_LABELS = {
    "crash": "A server crashed",
    "gave_up": "A server keeps crashing (auto-restart gave up)",
    "update_failed": "A game update failed",
    "backup_failed": "A backup failed",
    "ram": "RAM is running high",
    "disk": "Disk space is low",
}
COOLDOWN = {"crash": 600, "gave_up": 60, "update_failed": 1800, "backup_failed": 3600,
            "ram": 3600, "disk": 6 * 3600, "test": 0}
COLORS = {"error": 0xEF4444, "warn": 0xF59E0B, "info": 0xE6C87E}
WEBHOOK_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/",
                    "https://ptb.discord.com/api/webhooks/", "https://canary.discord.com/api/webhooks/")


def valid_webhook(url: str) -> bool:
    return bool(url) and url.startswith(WEBHOOK_PREFIXES)


class AlertCenter:
    def __init__(self, manager, log: Callable[[str], None] = print,
                 bot_sender: Optional[Callable[[str, str, int], bool]] = None):
        self.mgr = manager
        self.log = log
        self.bot_sender = bot_sender      # returns True if the bot posted it
        self._last: Dict[tuple, float] = {}
        self._lock = threading.Lock()
        self.sent = []                    # recent alerts (for tests and the UI)

    # -- settings -------------------------------------------------------------
    def _setting(self, key, default=None):
        try:
            v = self.mgr.get_setting(key, default)
        except Exception:
            return default
        return default if v is None or v == "" else v

    def enabled(self, kind: str) -> bool:
        if self._setting("alerts_enabled", True) is False:
            return False
        events = self._setting("alert_events", list(ALL_EVENTS))
        return kind == "test" or kind in (events or [])

    # -- sending ----------------------------------------------------------------
    def notify(self, kind: str, server_id: Optional[str], title: str, text: str = "",
               level: str = "error", force: bool = False) -> bool:
        if not force and not self.enabled(kind):
            return False
        key = (kind, server_id)
        now = time.time()
        with self._lock:
            if not force and now - self._last.get(key, 0) < COOLDOWN.get(kind, 600):
                return False
            self._last[key] = now
        self.sent.append({"kind": kind, "server_id": server_id, "title": title, "text": text,
                          "at": now})
        del self.sent[:-50]
        color = COLORS.get(level, COLORS["info"])

        def go():
            try:
                self._deliver(title, text, color, server_id=server_id, kind=kind)
            except TypeError:       # a replacement _deliver that only takes the basics
                self._deliver(title, text, color)
        threading.Thread(target=go, daemon=True, name="alert").start()
        return True

    def _deliver(self, title: str, text: str, color: int, server_id=None, kind=None) -> bool:
        ok = False
        if self.bot_sender is not None:
            try:
                try:
                    ok = bool(self.bot_sender(title, text, color, server_id=server_id, kind=kind))
                except TypeError:
                    ok = bool(self.bot_sender(title, text, color))
            except Exception as e:
                self.log(f"[WARN] alert via bot failed: {e}")
        if not ok:
            url = self._webhook_url()
            if url:
                ok = self.post_webhook(url, title, text, color)
        if not ok:
            self.log(f"[ALERT] {title} - {text}" if text else f"[ALERT] {title}")
        return ok

    def _webhook_url(self) -> str:
        url = self._setting("alert_webhook_url", "") or ""
        return url if valid_webhook(url) else ""

    def post_webhook(self, url: str, title: str, text: str, color: int) -> bool:
        body = json.dumps({"username": "Tofu", "allowed_mentions": {"parse": []},
                           "embeds": [{"title": title[:250], "description": text[:3900],
                                       "color": color, "timestamp": time.strftime(
                                           "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}]}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "User-Agent": "TofuServerManager"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return 200 <= r.status < 300
        except Exception as e:
            self.log(f"[WARN] Discord webhook alert failed: {e}")
            return False

    def test(self) -> dict:
        has_bot = self.bot_sender is not None
        has_hook = bool(self._webhook_url())
        if not (has_bot or has_hook):
            return {"sent": False, "reason": "No Discord bot is running and no webhook URL is set."}
        ok = self._deliver("Tofu test alert", "Alerts are working. You'll get a message like this "
                           "when a server crashes, an update or backup fails, or RAM/disk runs low.",
                           COLORS["info"])
        return {"sent": ok, "via": "bot" if has_bot else "webhook"}

    # -- events from ServerManager ---------------------------------------------------
    def on_event(self, event: str, server_id: str, info: dict):
        name = info.get("name", server_id)
        reason = info.get("reason") or ""
        fix = ""
        if info.get("help"):
            fix = info["help"][0].get("fix") or ""
        if event == "crashed":
            up = int(info.get("uptime") or 0)
            text = (f"**Why:** {reason}\n{fix}\n" if reason else "") + \
                   f"Exit code {info.get('exit_code')}, ran for {_dur(up)}."
            srv = self.mgr.get_server(server_id) or {}
            if srv.get("autoRestart"):
                text += " Tofu will restart it."
            self.notify("crash", server_id, f"⚠️ {name} crashed", text)
        elif event == "gave_up":
            text = (f"**Why:** {reason}\n{fix}\n" if reason else "") + \
                   f"It crashed {info.get('restarts')} times in a row, so auto-restart is paused. " \
                   "Start it again from Tofu once it's fixed."
            self.notify("gave_up", server_id, f"\U0001F6D1 {name} keeps crashing", text)
        elif event == "update_failed":
            self.notify("update_failed", server_id, f"⚠️ Update failed: {name}",
                        str(info.get("error") or "")[:1500], level="warn")

    def backup_failed(self, server_id: str, error: str):
        name = (self.mgr.get_server(server_id) or {}).get("name", server_id)
        self.notify("backup_failed", server_id, f"⚠️ Backup failed: {name}", str(error)[:1500],
                    level="warn")

    # -- periodic checks (agent metrics loop, every minute) --------------------------
    def check_resources(self, statuses):
        try:
            import psutil
            vm = psutil.virtual_memory()
            limit = float(self._setting("alert_ram_percent", 90) or 90)
            if vm.percent >= limit:
                top = sorted((s for s in statuses if s.get("memUsageMB")),
                             key=lambda s: -s["memUsageMB"])[:3]
                who = ", ".join("%s %s" % (s["name"], _mb(s["memUsageMB"])) for s in top)
                self.notify("ram", None, f"\U0001F4C8 RAM is at {vm.percent:.0f}%",
                            f"{_mb(vm.used / 1048576)} of {_mb(vm.total / 1048576)} in use."
                            + (f" Biggest servers: {who}." if who else ""), level="warn")
        except Exception:
            pass
        for st in statuses:
            if not st.get("isRunning"):
                continue
            srv = self.mgr.get_server(st["id"]) or {}
            try:
                cap = float(srv.get("ramAlertMB") or 0)
            except (TypeError, ValueError):
                cap = 0
            if cap and (st.get("memUsageMB") or 0) >= cap:
                self.notify("ram", st["id"], f"\U0001F4C8 {st['name']} is using {_mb(st['memUsageMB'])}",
                            f"Its alert limit is {_mb(cap)}. A restart usually frees memory.", level="warn")
        try:
            min_gb = float(self._setting("alert_disk_gb", 5) or 5)
        except (TypeError, ValueError):
            min_gb = 5.0
        seen = set()
        for srv in self.mgr.load_servers():
            d = srv.get("directory") or ""
            drive = os.path.splitdrive(d)[0] or (d[:2] if d.startswith("\\\\") else "/")
            if not d or not os.path.isdir(d) or drive in seen:
                continue
            seen.add(drive)
            try:
                free = shutil.disk_usage(d).free / (1024 ** 3)
            except Exception:
                continue
            if free < min_gb:
                self.notify("disk", "drive:" + drive, f"\U0001F4BE Only {free:.1f} GB free on {drive or d}",
                            "Updates and backups can fail when the disk is full. Delete old backups "
                            "or move a server to another drive.", level="warn")


def _mb(v) -> str:
    v = float(v or 0)
    return "%.1f GB" % (v / 1024) if v >= 1024 else "%d MB" % v


def _dur(s: int) -> str:
    return "%dh %dm" % (s // 3600, s % 3600 // 60) if s >= 3600 else ("%dm" % (s // 60) if s >= 60 else "%ds" % s)
