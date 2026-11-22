"""Open the home screen (or a server's Overview tab) with made-up servers and save a screenshot.

  py -3.14 tools/ui_preview.py [out.png]
  py -3.14 tools/ui_preview.py server [out.png]

Nothing real is touched: no servers start, no settings are read or written.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import customtkinter as ctk  # noqa: E402

from ui.app_common import build_theme, THEME  # noqa: E402,F401
from ui.home import HomeView  # noqa: E402
from core.game_catalog import GAME_PRESETS  # noqa: E402

SERVERS = {
    "v": {"id": "v", "name": "Friday Crew", "appId": "896660"},
    "p": {"id": "p", "name": "Pal Island", "appId": "2394010"},
    "h": {"id": "h", "name": "Homestead", "type": "vintage-story"},
    "c": {"id": "c", "name": "Cave Diggers", "appId": "1963720"},
}
STATUSES = [
    {"id": "v", "name": "Friday Crew", "isRunning": True, "state": "running", "playerCount": 4, "maxPlayers": 10,
     "players": [{"name": n} for n in ("Mira", "Jonas", "Tess", "Bo")], "cpuPercent": 12, "memUsageMB": 3100,
     "uptimeSeconds": 7300},
    {"id": "p", "name": "Pal Island", "isRunning": True, "state": "running", "playerCount": 0, "maxPlayers": 16,
     "cpuPercent": 8, "memUsageMB": 9800, "uptimeSeconds": 600},
    {"id": "h", "name": "Homestead", "isRunning": False, "state": "stopped"},
    {"id": "c", "name": "Cave Diggers", "isRunning": False, "state": "crashed", "exitCode": 1,
     "crashHelp": [{"title": "The port is already in use"}]},
]
REACH = {
    "v": {"verdict": "ok", "address": "203.0.113.24:2456", "invite": "Join Friday Crew: 203.0.113.24:2456"},
    "p": {"verdict": "blocked", "title": "Your router is blocking port 8211", "fix": "forward"},
}
SUMMARY = {"servers": 4, "online": 2, "players": 4, "last_backups": {"v": time.time() - 2400}}


class FakeManager:
    def get_server(self, sid):
        return SERVERS.get(sid)


def server_page(root):
    """Pal Island's Overview tab: running, router blocking the port, backups on."""
    from core import checklist
    from ui.server_overview import ServerOverview
    frame = ctk.CTkFrame(root, fg_color=THEME["bg_card"], corner_radius=10)
    frame.pack(fill="both", expand=True, padx=15, pady=15)
    ctk.CTkLabel(frame, text="Pal Island", anchor="w", font=("Bahnschrift SemiBold", 18, "bold"),
                 text_color=THEME["text_light"]).pack(fill="x", padx=20, pady=(14, 6))
    view = ServerOverview(frame, THEME, on_action=lambda a: None, on_power=lambda k: None,
                          shortcuts=[("Game settings", None), ("Mods", None), ("Players and bans", None),
                                     ("Console", None), ("Restore a backup", None), ("Edit server", None)])
    view.pack(fill="both", expand=True, padx=6, pady=6)
    st = dict(STATUSES[1], players=[])
    known = {"installed": True, "settings": {"name": "Pal Island", "password": True, "players": 16},
             "schedules": {"p": {"backup": {"enabled": True, "mode": "daily", "times": ["04:00"]}}},
             "nightly": {}, "last_backup": time.time() - 5 * 3600}
    items = checklist.items(FakeManager(), "p", st, REACH["p"], known=known)

    def fill():
        view.show_status(st, REACH["p"])
        view.show_items(items)
    root.after(300, fill)


def main():
    args = sys.argv[1:]
    page = "server" if args[:1] == ["server"] else "home"
    args = args[1:] if page == "server" else args
    out = Path(args[0] if args else ROOT / "logs" / ("ui_preview_server.png" if page == "server" else
                                                     "ui_preview.png"))
    ctk.set_appearance_mode("Dark")
    root = ctk.CTk()
    root.title("Tofu Server Manager - preview")
    root.geometry("1400x860+40+40")
    root.configure(fg_color=THEME["bg_dark"])
    if page == "server":
        server_page(root)
    else:
        home_page(root)

    def shoot():
        root.update()
        try:
            from PIL import ImageGrab
            x, y = root.winfo_rootx(), root.winfo_rooty()
            img = ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height()))
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out)
            print("saved", out, img.size)
        finally:
            root.destroy()
    root.after(2500, shoot)
    root.attributes("-topmost", True)
    root.mainloop()


def home_page(root):
    view = HomeView(root, THEME, on_toggle=lambda *a: None, on_manage=lambda *a: None, on_add=lambda: None,
                    presets=GAME_PRESETS, get_server=lambda sid: SERVERS.get(sid), on_import=lambda: None,
                    on_checkup=lambda: None, get_reach=lambda sid, running: REACH.get(sid),
                    on_fix=lambda *a: None)
    view.pack(fill="both", expand=True)
    root.after(300, lambda: view.refresh(STATUSES, SUMMARY))


if __name__ == "__main__":
    main()
