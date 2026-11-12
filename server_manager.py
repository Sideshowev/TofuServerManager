"""
BaoBun Control Panel — Server Management Backend
Handles process spawning, watchdog monitoring, config editing, log reading.
"""

import json
import os
import re
import shlex
import socket
import subprocess
import threading
import time
import shutil
import zipfile
import urllib.request
from pathlib import Path
import yaml

from core.paths import atomic_write_json
from core.process_manager import MetricsSampler, kill_tree, pid_alive, process_create_time


# Config/data files that live next to the exe (or this module when running
# from source). These are what Backup/Restore snapshots.
CONFIG_FILES = [
    "servers.json",
    "settings.json",
    "discord_config.json",
    "servers_activity.json",
    "servers_installs.json",
    "servers_boot.json",
    "ark_clusters.json",
]


def broad_folder(path: str) -> str | None:
    """Why ``path`` is too broad to search through as a server's folder (a whole
    drive, the user's own folder, Windows, Program Files...), or None when it's fine.
    Scans of such folders stay at the top level instead of reading the whole PC."""
    if not path:
        return None
    try:
        p = os.path.normcase(os.path.abspath(path)).rstrip("\\/")
    except (TypeError, ValueError):
        return None
    drive, rest = os.path.splitdrive(p)
    if not rest.strip("\\/") or p in ("", "/"):
        return "a whole drive"
    home = os.path.normcase(os.path.expanduser("~")).rstrip("\\/")
    env = os.environ
    named = {}
    for var, label in (("SystemRoot", "Windows"), ("ProgramFiles", "Program Files"),
                       ("ProgramFiles(x86)", "Program Files (x86)"), ("APPDATA", "AppData"),
                       ("LOCALAPPDATA", "AppData"), ("ProgramData", "ProgramData")):
        v = env.get(var)
        if v:
            named[os.path.normcase(v).rstrip("\\/")] = label
    named.update({home: "your user folder", os.path.dirname(home): "the folder of all users"})
    for sub, label in (("Desktop", "your Desktop"), ("Documents", "your Documents"),
                       ("Downloads", "your Downloads"), ("OneDrive", "your OneDrive")):
        named[os.path.normcase(os.path.join(home, sub))] = label
    return named.get(p)


def walk_server_dir(directory: str, max_depth: int, exclude=(), skip_paths=(), budget: int = 20000,
                    seconds: float = 4.0):
    """os.walk for a server's folder that can't wander off across the PC: it never
    goes below a too-broad folder's top level (see broad_folder), skips other
    servers' folders and links, and stops after ``budget`` entries / ``seconds``."""
    if not directory or not os.path.isdir(directory):
        return
    if broad_folder(directory):
        max_depth = 0
    skip = {os.path.normcase(os.path.abspath(x)) for x in skip_paths if x}
    skip.discard(os.path.normcase(os.path.abspath(directory)))
    excl = {e.lower() for e in exclude}
    seen = 0
    end = time.time() + seconds
    for root, dirs, files in os.walk(directory):
        rel_root = os.path.relpath(root, directory)
        depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
        keep = []
        if depth < max_depth:
            for d in dirs:
                full = os.path.join(root, d)
                if d.lower() in excl or os.path.islink(full) or os.path.normcase(full) in skip:
                    continue
                try:
                    if getattr(os.path, "isjunction", None) and os.path.isjunction(full):
                        continue
                except OSError:
                    continue
                keep.append(d)
        dirs[:] = keep
        seen += len(files) + len(keep)
        yield root, rel_root, depth, files
        if seen > budget or time.time() > end:
            return

def split_args(arg_string: str) -> list[str]:
    """Split a launch-argument string like a shell would, keeping quoted values together."""
    if not arg_string:
        return []
    try:
        return shlex.split(arg_string, posix=(os.name != "nt"))
    except ValueError:
        return arg_string.split()


def build_launch_command(exe: str, arg_string: str):
    """Command for Popen.

    On Windows the argument string is passed through verbatim (exactly as the
    old start.bat did), because many game servers (Unreal-based ones like ARK
    in particular) parse the raw command line themselves. .bat/.cmd launchers
    run through cmd.exe with the standard double-quote wrapping so paths with
    spaces work.
    """
    arg_string = (arg_string or "").strip()
    if os.name == "nt":
        if exe.lower().endswith((".bat", ".cmd")):
            return 'cmd.exe /d /c ""' + exe + '"' + ((" " + arg_string) if arg_string else "") + '"'
        return '"' + exe + '"' + ((" " + arg_string) if arg_string else "")
    return [exe] + split_args(arg_string)


class ServerProcess:
    """A server process owned (proc) or adopted (pid only) by this instance."""

    def __init__(self, server_id: str, name: str, proc: subprocess.Popen | None = None,
                 pid: int | None = None, create_time: float | None = None,
                 started_at: float | None = None, log_file=None):
        self.server_id = server_id
        self.name = name
        self.proc = proc
        self.pid = proc.pid if proc is not None else pid
        self.create_time = create_time if create_time is not None else process_create_time(self.pid)
        self.start_time = started_at or time.time()
        self.restart_count = 0
        self.exit_code: int | None = None
        self.adopted = proc is None
        self._log_file = log_file
        self._stdin_lock = threading.Lock()

    # Backwards-compatible attribute used by older callers.
    @property
    def running(self) -> bool:
        return self.is_alive

    @property
    def is_alive(self) -> bool:
        if self.proc is not None:
            rc = self.proc.poll()
            if rc is not None:
                self.exit_code = rc
                return False
            return True
        return pid_alive(self.pid, self.create_time)

    @property
    def can_send(self) -> bool:
        return self.proc is not None and self.proc.stdin is not None and self.is_alive

    def send(self, command: str) -> bool:
        """Write one console line to the server's stdin."""
        if not self.can_send:
            return False
        data = (command.rstrip("\r\n") + ("\r\n" if os.name == "nt" else "\n")).encode("utf-8")
        with self._stdin_lock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
                return True
            except (BrokenPipeError, OSError, ValueError):
                return False

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for exit. Returns True if the process has exited."""
        if self.proc is not None:
            try:
                self.exit_code = self.proc.wait(timeout=timeout)
                return True
            except subprocess.TimeoutExpired:
                return False
        deadline = None if timeout is None else time.time() + timeout
        while pid_alive(self.pid, self.create_time):
            if deadline is not None and time.time() >= deadline:
                return False
            time.sleep(1.0)
        return True

    @property
    def crashed_immediately(self) -> bool:
        return self.exit_code is not None and (time.time() - self.start_time) < 30

    def close_log(self):
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
        if self.proc is not None and self.proc.stdin is not None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass


def sqlite_snapshot(path: str) -> str | None:
    """If ``path`` is an SQLite database (VS .vcdbs saves, many games' .db files),
    return a consistent temporary copy made with SQLite's online backup API,
    which is safe while the game is writing. Returns None for other files."""
    try:
        with open(path, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                return None
    except OSError:
        return None
    import sqlite3
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix="baobun_snap_", suffix=".db")
    os.close(fd)
    try:
        src = sqlite3.connect("file:" + Path(path).as_posix() + "?mode=ro", uri=True, timeout=30)
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst, pages=4096, sleep=0.01)
        finally:
            dst.close()
            src.close()
        return tmp
    except Exception as e:
        print(f"[WARN] consistent copy of {path} failed ({e}); copying the file as-is")
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None


CONSOLE_LOG_MAX = 20 * 1024 * 1024
CONSOLE_LOG_KEEP = 3


def rotate_log(path: str, max_bytes: int = CONSOLE_LOG_MAX, keep: int = CONSOLE_LOG_KEEP) -> bool:
    """Before a server starts: if its console log is big, keep it as .1 (older
    ones shift to .2, .3; the oldest is dropped) and start a fresh one."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < max_bytes:
            return False
        for i in range(keep, 0, -1):
            src = path if i == 1 else "%s.%d" % (path, i - 1)
            dst = "%s.%d" % (path, i)
            if os.path.exists(src):
                if os.path.exists(dst):
                    os.remove(dst)
                os.replace(src, dst)
        return True
    except OSError as e:
        print(f"[WARN] could not rotate {path}: {e}")
        return False


def vs_data_path(srv: dict) -> str:
    """The Vintage Story data folder (Saves, Logs, Mods): --dataPath from the
    launch args, else a data folder inside the install, else %APPDATA%."""
    directory = srv.get("directory") or ""
    args = split_args(srv.get("launchArgs") or "")
    for i, a in enumerate(args):
        low = a.lower().strip('"')
        p = None
        if low == "--datapath" and i + 1 < len(args):
            p = args[i + 1]
        elif low.startswith("--datapath="):
            p = a.split("=", 1)[1]
        if p is not None:
            p = p.strip('"')
            return p if os.path.isabs(p) else os.path.join(directory, p)
    for name in ("Data", "VintageStoryData", "data"):
        cand = os.path.join(directory, name)
        if os.path.isdir(cand):
            return cand
    appdata = os.environ.get("APPDATA")
    if appdata and os.path.isdir(os.path.join(appdata, "VintagestoryData")):
        return os.path.join(appdata, "VintagestoryData")
    return os.path.join(directory, "Data")


def file_version(path: str) -> str | None:
    """Windows file version (e.g. of VintagestoryAPI.dll) as "1.20.3.0"."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        ln = ctypes.c_uint()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)):
            return None
        vals = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        ms, ls = vals[2], vals[3]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def tail_lines(path: str, last_n: int = 200, max_bytes: int = 4_000_000) -> list[str]:
    """Return the last ``last_n`` lines of a text file without reading all of it."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            block = 65536
            data = b""
            pos = size
            while pos > 0 and data.count(b"\n") <= last_n and len(data) < max_bytes:
                step = min(block, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    out = text.splitlines()
    if pos > 0 and out:
        out = out[1:]  # first line is probably partial
    return out[-last_n:]


ARK_APP_ID = "2430930"
# Official ARK: Survival Ascended maps: (friendly name, map identifier)
ARK_MAPS = [
    ("The Island", "TheIsland_WP"),
    ("Scorched Earth", "ScorchedEarth_WP"),
    ("Aberration", "Aberration_WP"),
    ("Extinction", "Extinction_WP"),
    ("The Center", "TheCenter_WP"),
    ("Ragnarok", "Ragnarok_WP"),
    ("Astraeos", "Astraeos_WP"),
    ("Valguero", "Valguero_WP"),
    ("Genesis Part 1", "Genesis_WP"),
    ("Lost Colony", "LostColony_WP"),
    ("Club ARK", "BobsMissions_WP"),
    ("Svartalfheim", "Svartalfheim_WP"),
    ("Forglar", "Forglar_WP"),
    ("Amissa", "Amissa_WP"),
    ("Bjarnheim", "Bjarnheim_WP"),
    ("Forglar Part II", "Forglar2_WP"),
    ("Lost Protocol", "PROTOCOL_WP"),
    ("The Island Reforged", "TheIsland_WP"),
    ("Dark's Event Map", "Darken_Map_WP"),
    ("Reverence", "Reverence_WP"),
    ("Prometeus", "PROMETEUS_WP"),
    ("TaeniaStella", "TaeniaStella_WP"),
]

# Required CurseForge mod IDs for premium / community maps. These are
# auto-appended to -mods= when the map is selected, so the ASA server
# downloads them automatically on first start.
ARK_MAP_MODS = {
    "BobsMissions_WP": "1005639",   # Club ARK (free social hub)
    "Svartalfheim_WP": "893657",    # Svartalfheim free PC-only test (premium crossplay = 962796, free crossplay = 942355)
    "Forglar_WP": "1009169",        # Forglar Part I (premium)
    "Amissa_WP": "965379",          # Amissa (free fan map)
    "Bjarnheim_WP": "1376189",      # Bjarnheim (Nekatus, free)
    "Forglar2_WP": "1406494",       # Forglar Part II (premium)
    "PROTOCOL_WP": "1240685",       # Lost Protocol (premium)
    "Darken_Map_WP": "1009599",     # Dark's Event Map (free)
    "Reverence_WP": "975626",       # Reverence (free)
    "PROMETEUS_WP": "1454964",      # Prometeus (premium, by Arkitecto)
    "TaeniaStella_WP": "984808",    # TaeniaStella (alpha, by TeamMIROKU)
}

# Maps that reuse an existing official level name and must be loaded via
# -MapModID=<id> instead of a unique _WP level. Keyed by friendly name.
ARK_REFORGE_MODS = {
    "The Island Reforged": "1460513",   # overrides TheIsland_WP
}
ARK_BASE_GAME_PORT = 7777
ARK_BASE_QUERY_PORT = 27015
ARK_BASE_RCON_PORT = 32330

BACKUP_FILE_EXTS = (
    ".json", ".ini", ".cfg", ".conf", ".properties", ".env", ".toml", ".yaml",
    ".yml", ".xml", ".txt", ".sav", ".db", ".sqlite", ".sqlite3", ".map",
    ".ark", ".arkprofile", ".arktribe", ".world", ".level", ".dat", ".vdf",
    ".profile", ".gz", ".bin",
)
BACKUP_SAVE_DIRS = {
    "saves", "save", "saved", "world", "worlds", "worldsave", "level", "levels",
    "storage", "cluster", "clusters", "profiles", "profile", "serverfiles",
    "players", "characters",
}
BACKUP_EXCLUDE_DIRS = {
    "logs", "log", "cache", "caches", "temp", "tmp", "backup", "backups",
    "node_modules", ".git", "steamcmd", "steamapps", "redist", "_commonredist",
    "bin", "lib", "mods", "assets", "content", "paks", "shadercache", "engine",
}
BACKUP_BINARY_EXTS = (".exe", ".dll", ".pak", ".so", ".dylib", ".uasset", ".umap", ".tfc", ".ress")


def iter_backup_files(directory):
    """Yield relative file paths worth backing up in a game install.

    Includes config files anywhere plus everything under save-like folders,
    skipping logs/cache/binaries/redist and oversized files.
    """
    broad = broad_folder(directory)
    for root, dirs, files in os.walk(directory):
        rel_root = os.path.relpath(root, directory)
        depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
        if depth > 6 or broad:
            dirs[:] = []          # never back up a whole drive / user folder
        if depth > 6:
            continue
        segments = rel_root.split(os.sep) if rel_root != "." else []
        in_save_dir = any(seg.lower() in BACKUP_SAVE_DIRS for seg in segments)
        dirs[:] = [d for d in dirs if d.lower() not in BACKUP_EXCLUDE_DIRS]
        for fn in files:
            low = fn.lower()
            full = os.path.join(root, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > 300 * 1024 * 1024:
                continue
            if low.endswith(BACKUP_BINARY_EXTS):
                continue
            if low.endswith(BACKUP_FILE_EXTS) or in_save_dir:
                yield os.path.relpath(full, directory)


# Curated ARK gameplay settings exposed by the in-depth cluster config tool.
# Each entry: (GameUserSettings.ini [ServerSettings] key, friendly label,
#              value kind, default, help text).
# Because all maps in a cluster share one install folder, these are inherently
# consistent cluster-wide -- this editor just gives them a friendly UI.
ARK_GAMEPLAY_SETTINGS = [
    ("DifficultyOffset", "Difficulty", "float", "1.0", "Wild dino level and loot quality (0=low, 1=high)"),
    ("XPMultiplier", "XP Multiplier", "float", "1.0", "Global experience gain rate"),
    ("TamingSpeedMultiplier", "Taming Speed", "float", "1.0", "Taming progress rate"),
    ("HarvestAmountMultiplier", "Harvest Multiplier", "float", "1.0", "Resources gathered per hit"),
    ("MatingIntervalMultiplier", "Mating Interval", "float", "1.0", "Cooldown between matings"),
    ("EggHatchSpeedMultiplier", "Egg Hatch Speed", "float", "1.0", "Egg incubation speed"),
    ("BabyMatureSpeedMultiplier", "Baby Mature Speed", "float", "1.0", "Baby growth speed"),
    ("BabyCuddleIntervalMultiplier", "Cuddle Interval", "float", "1.0", "Time between imprint cuddles"),
    ("DayTimeSpeedScale", "Day Length", "float", "1.0", "Length of daytime"),
    ("NightTimeSpeedScale", "Night Length", "float", "1.0", "Length of nighttime"),
    ("MaxPlayers", "Max Players", "int", "70", "Maximum simultaneous players"),
    ("ServerPVE", "PvE Mode", "bool", "False", "Disables player-vs-player structure damage"),
    ("ServerHardcore", "Hardcore", "bool", "False", "Permanent death on respawn"),
    ("ShowMapPlayerLocation", "Show Player on Map", "bool", "True", "Reveal players on the map"),
    ("AllowThirdPersonPlayer", "Third Person", "bool", "True", "Allow third-person camera"),
]


ARK_MANAGED_FLAGS = ("port", "queryport", "rconport", "clusterid", "altsavedirectoryname", "mods", "mapmodid")
ARK_MANAGED_URL = ("listen", "sessionname", "serverpassword", "serveradminpassword")


def _ark_tokens(args):
    return re.findall(r'(?:[^\s"]+|"[^"]*")+', args or "")


def merge_ark_args(old, new):
    """Rebuilt cluster launch options + everything else the old ones had: extra
    ?options on the map (e.g. ?RCONEnabled=True) and extra -flags
    (e.g. -ClusterDirOverride, -NoBattlEye) stay instead of being wiped."""
    old_t, new_t = _ark_tokens(old), _ark_tokens(new)
    if not old_t or not new_t:
        return new
    extra_url = []
    if not old_t[0].startswith("-"):
        for part in old_t[0].split("?")[1:]:
            key = part.split("=", 1)[0].lower()
            if key and key not in ARK_MANAGED_URL and not re.search(r"[?]%s(=|$|\?)" % re.escape(part.split("=", 1)[0]),
                                                                  new_t[0], re.I):
                extra_url.append(part)
        old_t = old_t[1:]
    new_keys = {t.lstrip("-").split("=", 1)[0].lower() for t in new_t[1:] if t.startswith("-")}
    extra_flags = [t for t in old_t if t.startswith("-")
                   and t.lstrip("-").split("=", 1)[0].lower() not in ARK_MANAGED_FLAGS
                   and t.lstrip("-").split("=", 1)[0].lower() not in new_keys]
    head = new_t[0] + "".join("?" + p for p in extra_url)
    # never move a map to a new save folder (renaming the server would look like a wipe)
    keep_save = next((t for t in old_t if t.lower().startswith("-altsavedirectoryname=")), None)
    body = [keep_save if (keep_save and t.lower().startswith("-altsavedirectoryname=")) else t for t in new_t[1:]]
    return " ".join([head] + body + extra_flags)


def _ark_flag_port(args, name):
    m = re.search(r"(?i)(?:^|\s)-%s=(\d+)" % name, args or "")
    return int(m.group(1)) if m else None


def build_ark_launch_args(map_id, friendly_name, cluster_id, index, server_password="", admin_password="", mods="",
                          map_mod="", server_name="", ports=None):
    """Build a single-token, space-free launch argument string for one map.
    ``ports`` (game, query, rcon) keeps ports a map already has (Ports page)."""
    cid = (cluster_id or "BaoBunCluster").strip().replace(" ", "")
    sess = (server_name or friendly_name).replace(" ", "").replace("'", "").replace('"', "")
    ports = ports or (None, None, None)
    game_port = ports[0] or ARK_BASE_GAME_PORT + index * 2
    query_port = ports[1] or ARK_BASE_QUERY_PORT + index * 2
    rcon_port = ports[2] or ARK_BASE_RCON_PORT + index * 2
    pw = (server_password or "").replace(" ", "")
    apw = (admin_password or "").replace(" ", "")
    modlist = (mods or "").strip().replace(" ", "")
    reforge_mod = ARK_REFORGE_MODS.get(friendly_name)
    auto_mod = (map_mod or "").strip() or reforge_mod or ARK_MAP_MODS.get(map_id, "")
    combined_mods = ",".join([m for m in (auto_mod, modlist) if m])
    args = (
        map_id + "?listen?SessionName=" + sess
        + "?ServerPassword=" + pw
        + "?ServerAdminPassword=" + apw
        + " -Port=" + str(game_port)
        + " -QueryPort=" + str(query_port)
        + " -RCONPort=" + str(rcon_port)
        + " -clusterid=" + cid
        + " -AltSaveDirectoryName=" + sess
    )
    if combined_mods:
        args = args + " -mods=" + combined_mods
    if reforge_mod:
        args = args + " -MapModID=" + reforge_mod
    return args


def _local_appdata() -> Path:
    """The user's local app-data folder (wherever Windows keeps it on this PC)."""
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))

class ServerManager:
    """Core server lifecycle management.

    One ServerManager is the single engine for a process:
      * role="agent": the background agent; owns server processes.
      * role="gui":   the desktop app. When the agent is running (it is started
        automatically), lifecycle calls are forwarded to it so there is only
        ever one owner. Without an agent the GUI owns processes itself.
    Every owner writes a small record per server to runtime/ so any instance
    can see what is running (and nothing gets started twice).
    """

    REGISTRY_PREFIX = "proc_"
    FORWARDED = ("start_server", "stop_server", "restart_server", "send_console_command")

    def __init__(self, config_path: str, role: str = "gui"):
        self.config_path = Path(config_path)
        self.role = role
        self.processes: dict[str, ServerProcess] = {}
        self.watchdog_timers: dict = {}          # kept for backwards compatibility
        self._lock = threading.RLock()
        self._servers_cache = (None, None)       # ((mtime_ns, size), text)
        self._restart_counts: dict[str, int] = {}
        self._stop_requested: set[str] = set()
        self._states: dict[str, str] = {}
        self._last_exit: dict[str, dict] = {}
        self._sampler = MetricsSampler()
        self._listeners: list = []
        self._agent_client = None
        self._agent_checked = 0.0
        self._update_cache: dict = {}
        self.plugins = None   # core.plugin_system.PluginManager (attached by core.engine)
        self.db = None        # core.database_layer.DatabaseLayer (attached by core.engine)

    # ── Events / audit ───────────────────────────────────

    def add_listener(self, callback):
        """callback(event, server_id, info) for started/stopped/crashed/restarting/..."""
        self._listeners.append(callback)

    def _emit(self, event, server_id, **info):
        for cb in list(self._listeners):
            try:
                cb(event, server_id, info)
            except Exception as e:
                print(f"[WARN] listener error: {e}")

    def _audit(self, action, server_id, user="system", **details):
        if self.db is None:
            return
        try:
            self.db.audit(action=action, target_type="server", target_id=server_id,
                          user_id=user, details=details)
        except Exception:
            pass

    def plugin_for(self, srv):
        if self.plugins is None or not srv:
            return None
        try:
            return self.plugins.for_server(srv)
        except Exception:
            return None

    # ── Config Persistence ───────────────────────────────

    def load_servers(self) -> list[dict]:
        p = self.config_path
        try:
            st = p.stat()
        except FileNotFoundError:
            return []
        key = (st.st_mtime_ns, st.st_size)
        if self._servers_cache[0] != key:
            self._servers_cache = (key, p.read_text(encoding="utf-8"))
        data = json.loads(self._servers_cache[1] or "[]")
        return data if isinstance(data, list) else []

    def save_servers(self, servers: list[dict]) -> None:
        atomic_write_json(self.config_path, servers)

    def get_server(self, server_id: str) -> dict | None:
        return next((s for s in self.load_servers() if s.get("id") == server_id), None)

    # ── Runtime registry (shared between GUI and agent) ──

    def _registry_dir(self) -> Path:
        d = self.config_path.parent / "runtime"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _registry_path(self, server_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", server_id)
        return self._registry_dir() / (self.REGISTRY_PREFIX + safe + ".json")

    def _read_registry(self, server_id: str) -> dict:
        try:
            return json.loads(self._registry_path(server_id).read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_registry(self, server_id: str, **fields):
        rec = self._read_registry(server_id)
        rec.update(fields)
        rec["updated"] = time.time()
        try:
            atomic_write_json(self._registry_path(server_id), rec)
        except Exception as e:
            print(f"[WARN] registry write failed for {server_id}: {e}")

    def _set_state(self, server_id: str, state: str, **extra):
        self._states[server_id] = state
        self._write_registry(server_id, state=state, owner_pid=os.getpid(),
                             owner_role=self.role, **extra)

    # ── Agent forwarding (GUI side) ──────────────────────

    def agent_client(self):
        """Return a live client for the background agent, or None."""
        if self.role == "agent":
            return None
        now = time.time()
        if self._agent_client is not None and now - self._agent_checked < 5:
            return self._agent_client
        self._agent_checked = now
        try:
            from core.local_api import AgentClient
            client = AgentClient.discover(self.config_path.parent)
        except Exception:
            client = None
        self._agent_client = client
        return client

    def agent_available(self) -> bool:
        return self.agent_client() is not None

    def _forward(self, action, timeout=30, **params):
        client = self.agent_client()
        if client is None:
            return None
        resp = client.call(action, params, timeout=timeout)
        if not resp.get("success"):
            raise RuntimeError(resp.get("error") or "Watchdog error")
        return resp.get("data") or {}

    def setup_ark_cluster(self, directory, cluster_id, selected_maps, settings=None, custom_maps=None):
        """Create one server entry per selected ARK map, sharing a single install.

        settings: optional dict of cluster-wide parity values (serverPassword,
        adminPassword, mods) baked into every map and persisted for later edits.
        Returns the list of created entries (each already has an "id").
        Existing entries with the same name are skipped (idempotent on re-run).
        """
        settings = settings or {}
        server_names = settings.get("serverNames") or {}
        directory = (directory or "").strip()
        if not directory or not Path(directory).exists():
            raise FileNotFoundError("ARK install directory not found: " + directory)

        cid = (cluster_id or "BaoBunCluster").strip().replace(" ", "")
        exe = os.path.join(directory, "ShooterGame", "Binaries", "Win64", "ArkAscendedServer.exe")
        pw = settings.get("serverPassword", "")
        apw = settings.get("adminPassword", "")
        mods = settings.get("mods", "")

        servers = self.load_servers()
        existing_names = {s.get("name") for s in servers}
        created = []

        # Build the ordered map plan: (friendly, map_id, map_mod, index).
        # Built-in maps keep their ARK_MAPS index so ports stay stable; custom
        # maps are offset past the built-in list to avoid port collisions.
        plan = []
        for idx, (friendly, map_id) in enumerate(ARK_MAPS):
            if selected_maps is not None and friendly not in selected_maps:
                continue
            plan.append((friendly, map_id, "", idx))
        custom_offset = len(ARK_MAPS)
        for j, cm in enumerate(custom_maps or []):
            friendly = (cm.get("name") or "").strip()
            map_id = (cm.get("map_id") or cm.get("id") or "").strip()
            map_mod = (cm.get("mod") or cm.get("mod_id") or "").strip()
            if not friendly or not map_id:
                continue
            plan.append((friendly, map_id, map_mod, custom_offset + j))

        with self._lock:
            for friendly, map_id, map_mod, idx in plan:
                name = "ARK - " + friendly
                if name in existing_names:
                    continue
                entry = {
                    "name": name,
                    "type": "custom",
                    "directory": directory,
                    "executable": exe,
                    "start_bat_name": "start_" + friendly.replace(" ", "_") + ".bat",
                    "launchArgs": build_ark_launch_args(
                        map_id, friendly, cid, idx, pw, apw, mods, map_mod,
                        server_name=server_names.get(friendly, ""),
                    ),
                    "appId": ARK_APP_ID,
                    "updateBeforeStart": False,
                    "autoRestart": True,
                    "maxRestarts": 5,
                    "manualStart": True,
                    "clusterId": cid,
                    "arkMap": map_id,
                    "arkFriendly": friendly,
                    "arkMapMod": map_mod,
                    "arkIndex": idx,
                    "id": "srv_" + str(int(time.time() * 1000)) + "_" + str(idx),
                }
                servers.append(entry)
                existing_names.add(name)
                created.append(entry)

            self.save_servers(servers)

        if created:
            self._save_cluster_settings(cid, settings)

        return created

    # ── ARK cluster parity settings ─────────────────────

    def _cluster_settings_path(self):
        return self.config_path.parent / "ark_clusters.json"

    def get_cluster_settings(self, cluster_id):
        cid = (cluster_id or "").strip().replace(" ", "")
        try:
            data = json.loads(self._cluster_settings_path().read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data.get(cid, {})

    def _save_cluster_settings(self, cluster_id, settings):
        sp = self._cluster_settings_path()
        data = {}
        if sp.exists():
            try:
                data = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        prev = data.get(cluster_id) or {}
        entry = dict(prev)
        entry.update({
            "serverPassword": settings.get("serverPassword", prev.get("serverPassword", "")),
            "adminPassword": settings.get("adminPassword", prev.get("adminPassword", "")),
            "mods": settings.get("mods", prev.get("mods", "")),
            # names/order/on-off from the Mods tab; kept when only passwords change
            "modItems": settings["modItems"] if "modItems" in settings else (prev.get("modItems") or []),
            "serverNames": settings.get("serverNames", prev.get("serverNames") or {}),
        })
        for k in ("clusterDir",):
            if k in settings:
                entry[k] = settings[k]
        data[cluster_id] = entry
        sp.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def apply_cluster_settings(self, cluster_id, settings):
        """Re-stamp every map in a cluster with the shared parity settings."""
        cid = (cluster_id or "").strip().replace(" ", "")
        if not cid:
            raise ValueError("Cluster ID is required")
        servers = self.load_servers()
        changed = 0
        with self._lock:
            for i, s in enumerate(servers):
                if s.get("clusterId") != cid:
                    continue
                m = s.get("arkMap")
                if not m:
                    continue
                f = s.get("arkFriendly", s.get("name", "ARK"))
                idx = int(s.get("arkIndex", i))
                old = s.get("launchArgs") or ""
                names = settings.get("serverNames") or {}
                new = build_ark_launch_args(
                    m, f, cid, idx,
                    settings.get("serverPassword", ""),
                    settings.get("adminPassword", ""),
                    settings.get("mods", ""),
                    s.get("arkMapMod", ""),
                    server_name=names.get(f) or s.get("arkServerName", ""),
                    ports=(_ark_flag_port(old, "Port"), _ark_flag_port(old, "QueryPort"),
                           _ark_flag_port(old, "RCONPort")),
                )
                servers[i]["launchArgs"] = merge_ark_args(old, new)
                changed += 1
            self.save_servers(servers)

        self._save_cluster_settings(cid, settings)
        return changed

    def get_cluster_ids(self):
        ids = []
        for s in self.load_servers():
            cid = s.get("clusterId")
            if cid and cid not in ids:
                ids.append(cid)
        return ids

    # ── Process Lifecycle ────────────────────────────────

    def install_ark_server(self, directory):
        """Download/update the ARK: Survival Ascended dedicated server via SteamCMD.

        Reuses the SteamCMD warm-up + anonymous app_update flow. Runs synchronously
        (can take a while); the GUI calls this from a background thread.
        """
        directory = (directory or "").strip()
        if not directory:
            raise ValueError("ARK install directory is required")
        Path(directory).mkdir(parents=True, exist_ok=True)
        requires_account = bool(
            (self.get_setting("steam_username", "") or "")
            and (self.get_setting("steam_password", "") or ""))
        self._steamcmd_app_update(ARK_APP_ID, directory, requires_account)
        exe = os.path.join(directory, "ShooterGame", "Binaries", "Win64", "ArkAscendedServer.exe")
        return {"status": "ok", "app_id": ARK_APP_ID, "directory": directory, "executable": exe}

    def get_server_ports(self, server_id):
        """Extract network ports from a server's launch args (ARK and generic flags)."""
        servers = self.load_servers()
        srv = next((s for s in servers if s["id"] == server_id), None)
        if not srv:
            return []
        args = srv.get("launchArgs", "") or ""
        ports = []
        seen = set()
        for label, flag in [("Game", "-Port="), ("Query", "-QueryPort="), ("RCON", "-RCONPort=")]:
            m = re.search(re.escape(flag) + "([0-9]+)", args, re.IGNORECASE)
            if m:
                port = int(m.group(1))
                if port not in seen:
                    seen.add(port)
                    ports.append({"name": label, "port": port})
        for label, key in [("Game", "Port"), ("Query", "QueryPort"), ("RCON", "RCONPort")]:
            m = re.search("[?&]" + key + "=([0-9]+)", args, re.IGNORECASE)
            if m:
                port = int(m.group(1))
                if port not in seen:
                    seen.add(port)
                    ports.append({"name": label, "port": port})
        return ports

    def get_all_ports(self):
        """Return [{name, ports:[{name, port}]}] for every server that uses ports."""
        result = []
        for s in self.load_servers():
            ports = self.get_server_ports(s["id"])
            if ports:
                result.append({"id": s["id"], "name": s["name"], "ports": ports})
        return result

    def detect_port_conflicts(self):
        """Return [(port, [server_names])] for ports used by more than one server
        (see core/port_plan.py - knows each game's real ports, not just -Port=)."""
        try:
            from core import port_plan
            return [(c["port"], sorted({u["name"] for u in c["servers"]})) for c in port_plan.conflicts(self)]
        except Exception as e:
            print(f"[WARN] port plan failed, using the simple check: {e}")
        usage = {}
        for s in self.load_servers():
            ports = {p["port"] for p in self.get_server_ports(s["id"])}
            try:
                if s.get("port"):
                    ports.add(int(s["port"]))
            except (TypeError, ValueError):
                pass
            for port in sorted(ports):
                usage.setdefault(port, []).append(s.get("name", "unknown"))
        return [(port, names) for port, names in usage.items() if len(names) > 1]

    def get_rcon_info(self, server_id):
        """RCON connection info (host, port, password): from the game's own config
        (core/rcon_config.py), else the -RCONPort / ServerAdminPassword launch options."""
        servers = self.load_servers()
        srv = next((s for s in servers if s["id"] == server_id), None)
        if not srv:
            return {"host": "127.0.0.1", "port": None, "password": ""}
        try:
            from core import rcon_config
            found = rcon_config.info(srv)
            if found:
                return {"host": found["host"], "port": found["port"], "password": found["password"]}
        except Exception:
            pass
        args = srv.get("launchArgs", "") or ""
        port = None
        password = ""
        m = re.search(r"(?i)-RCONPort=([0-9]+)", args)
        if m:
            port = int(m.group(1))
        m = re.search(r"(?i)ServerAdminPassword=([^\s?&]+)", args)
        if m:
            password = m.group(1)
        return {"host": "127.0.0.1", "port": port, "password": password}

    # ── Lifecycle ────────────────────────────────────────

    def is_running(self, server_id: str) -> bool:
        local = self.processes.get(server_id)
        if local is not None:
            return local.is_alive
        rec = self._read_registry(server_id)
        return bool(rec.get("pid")) and pid_alive(rec.get("pid"), rec.get("create_time"))

    def _owner_alive(self, rec: dict) -> bool:
        opid = rec.get("owner_pid")
        return bool(opid) and opid != os.getpid() and pid_alive(opid)

    def start_server(self, server_id: str, user: str = "system", _from_watchdog: bool = False,
                     _skip_update: bool = False) -> dict:
        """Start a server (forwarded to the agent when it is running)."""
        if not _from_watchdog and self.agent_available():
            return self._forward("server.start", timeout=30, server_id=server_id, user=user)

        srv_def = self.get_server(server_id)
        if not srv_def:
            raise ValueError(f"Server {server_id} not found")
        if time.time() < (getattr(self, "maintenance_until", 0) or 0):
            raise RuntimeError("The server PC is restarting for maintenance - try again in a few minutes.")

        with self._lock:
            existing = self.processes.get(server_id)
            if existing is not None and existing.is_alive:
                return {"status": "already_running", "pid": existing.pid, "server_id": server_id}
            rec = self._read_registry(server_id)
            if rec.get("pid") and pid_alive(rec.get("pid"), rec.get("create_time")):
                return {"status": "already_running", "pid": rec.get("pid"), "server_id": server_id,
                        "note": "started by another BaoBun instance"}
            if self._states.get(server_id) in ("starting", "updating"):
                return {"status": "already_starting", "server_id": server_id}
            self.processes.pop(server_id, None)
            self._states[server_id] = "starting"
            if not _from_watchdog:
                self._restart_counts[server_id] = 0
            self._stop_requested.discard(server_id)

        try:
            self._cluster_preflight(srv_def)
            self._port_check(srv_def)
            return self._launch(srv_def, user=user, skip_update=_skip_update or _from_watchdog)
        except Exception:
            self._states.pop(server_id, None)
            self._set_state(server_id, "stopped", pid=None)
            raise

    def _cluster_preflight(self, srv_def: dict):
        """ARK cluster maps: make sure the map has the cluster's mods before it starts,
        so nothing a player downloads there can vanish."""
        try:
            from core import ark_cluster
            for note in ark_cluster.preflight(self, srv_def):
                print(f"[INFO] {srv_def.get('name')}: {note}")
        except Exception as e:
            print(f"[WARN] cluster check skipped for {srv_def.get('name')}: {e}")

    def _port_check(self, srv_def: dict):
        """Refuse to start on top of another running BaoBun server's ports (the game
        would fail to bind, or worse, players would land on the wrong server)."""
        try:
            from core import port_plan
            port_plan.apply_pending(self, srv_def)
            res = port_plan.start_check(self, srv_def)
        except Exception as e:
            print(f"[WARN] port check skipped for {srv_def.get('name')}: {e}")
            return
        for w in res["warn"]:
            print(f"[WARN] {srv_def.get('name')}: {w}")
            self._emit("port_warning", srv_def["id"], name=srv_def.get("name"), message=w)
        if res["block"]:
            raise RuntimeError(res["block"][0])

    def _fill_required_args(self, srv_def: dict) -> list:
        """Add launch options the game can't start without (core/game_defaults.py);
        saved to the server. Returns notes on what was added."""
        from core import game_defaults
        args, notes = game_defaults.required(srv_def)
        if args != (srv_def.get("launchArgs") or ""):
            srv_def["launchArgs"] = args
            with self._lock:
                servers = self.load_servers()
                for s in servers:
                    if s.get("id") == srv_def["id"]:
                        s["launchArgs"] = args
                self.save_servers(servers)
            if notes:
                self._audit("launch_defaults", srv_def["id"], added=", ".join(n.split(" (")[0] for n in notes
                                                                             if "password" not in n))
        return notes

    def _launch(self, srv_def: dict, user: str = "system", skip_update: bool = False) -> dict:
        server_id = srv_def["id"]
        directory = srv_def.get("directory") or ""
        if not directory or not Path(directory).exists():
            raise ValueError(f"Server directory does not exist: {directory}")

        first = {"applied": [], "waiting": []}
        if srv_def.get("firstSettings"):
            try:      # name / password / max players chosen when the server was added
                from core import first_settings
                first = first_settings.apply(self, server_id, user=user)
                srv_def = self.get_server(server_id) or srv_def
            except Exception as e:
                print(f"[WARN] first settings: {e}")
        filled = self._fill_required_args(srv_def)
        try:
            self._note_config_baseline(srv_def)
        except Exception as e:
            print(f"[WARN] settings baseline: {e}")
        applied = []
        try:     # settings changed while it ran, for games that rewrite their files on exit
            from core import config_forms
            applied = config_forms.apply_pending(self, server_id)
        except Exception as e:
            print(f"[WARN] waiting settings changes: {e}")
        exe, _args = self._resolve_executable(srv_def)
        if not Path(exe).exists():
            raise FileNotFoundError(f"Executable not found: {exe}")
        from core import game_defaults as _gd0
        blocked = _gd0.start_blocker(srv_def)
        if blocked:
            raise RuntimeError(blocked)      # plain words: what's missing and how to get it
        from core import minecraft as _mc
        if _mc.looks_minecraft(srv_def) and exe.lower().endswith((".jar", "run.bat")):
            _mc.resolve_java(srv_def)   # clear "install Java" message instead of a crash

        # Refresh the double-clickable start.bat (manual use only; BaoBun itself
        # launches the executable directly).
        try:
            self.generate_start_bat(server_id)
        except Exception as e:
            print(f"[WARN] start.bat not written: {e}")

        app_id = srv_def.get("appId")
        if app_id and srv_def.get("updateBeforeStart") and not skip_update:
            busy = [sid for sid in self.install_group(server_id)
                    if sid != server_id and self.is_running(sid)]
            try:
                if busy:
                    print(f"[INFO] {srv_def.get('name')}: skipping pre-start update "
                          f"({len(busy)} other server(s) use the same install)")
                else:
                    self._set_state(server_id, "updating", pid=None)
                    info = self.check_steam_update(server_id)
                    if info.get("update_available") is False:
                        print(f"[INFO] {srv_def.get('name')}: already up to date (build {info.get('installed')})")
                    else:
                        self._emit("updating", server_id, name=srv_def.get("name"))
                        self._steamcmd_app_update(str(app_id), directory,
                                                  bool(srv_def.get("requiresAccount", False)),
                                                  srv_def.get("steamBranch"))
            except Exception as e:
                # A failed update should not keep the server offline.
                print(f"[WARN] pre-start update failed for {srv_def.get('name')}: {e}")
                self._emit("update_failed", server_id, name=srv_def.get("name"), error=str(e))

        log_dir = os.path.join(directory, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"baobun-{server_id}.log")
        rotate_log(log_path)
        log_file = open(log_path, "ab", buffering=0)
        try:
            log_file.write(("\n==== BaoBun: starting " + srv_def.get("name", server_id) + " at "
                            + time.strftime("%Y-%m-%d %H:%M:%S") + " ====\n").encode("utf-8"))
            if applied:
                log_file.write(("==== BaoBun: applied settings changed while the server was running: "
                                + "; ".join(applied) + " ====\n").encode("utf-8"))
            if first["applied"]:
                log_file.write(("==== BaoBun: set what you chose when adding the server: "
                                + ", ".join(first["applied"]) + " ====\n").encode("utf-8"))
            if first["waiting"]:
                log_file.write(("==== BaoBun: the game makes its settings file on this first start. When it has, "
                                "and nobody is playing, BaoBun restarts it once to set the "
                                + ", ".join(first["waiting"]) + " you chose. ====\n").encode("utf-8"))
            if filled:
                log_file.write(("==== BaoBun: this game won't start without some launch options, so BaoBun set "
                                + ", ".join(filled) + ". Change them in the server's Settings tab. ====\n"
                                ).encode("utf-8"))
        except Exception:
            pass

        from core import game_defaults as _gd
        start_in = _gd.working_dir(srv_def, exe, directory)
        exe = _gd.full_launcher(srv_def, exe)
        cmd = None
        from core import minecraft as _mc
        from core import hytale as _hy
        if _hy.is_hytale(srv_def):
            cmd = _hy.launch_command(srv_def, exe, srv_def.get("launchArgs") or "")
        elif _mc.looks_minecraft(srv_def):
            cmd = _mc.launch_command(srv_def, exe, srv_def.get("launchArgs") or "")
        if cmd is None:
            cmd = build_launch_command(exe, srv_def.get("launchArgs") or "")
        creation_flags = 0
        if os.name == "nt":
            # Hidden console (not DETACHED) so console-based servers behave normally;
            # own process group so a GUI Ctrl+C never reaches the game.
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        def popen(env):
            return subprocess.Popen(cmd, cwd=start_in, stdin=subprocess.PIPE, stdout=log_file,
                                    stderr=subprocess.STDOUT, env=env, creationflags=creation_flags)
        try:
            try:
                proc = popen(os.environ.copy())
            except OSError as e:
                if getattr(e, "winerror", None) != 740:
                    raise
                # The program asks Windows for administrator rights (SCUM ...). Game servers
                # don't need them - start it as a normal program instead.
                print(f"[INFO] {srv_def.get('name')}: the program asks for admin rights - starting it normally")
                proc = popen(dict(os.environ, __COMPAT_LAYER="RunAsInvoker"))
        except Exception as e:
            log_file.close()
            raise RuntimeError(f"Failed to start {srv_def.get('name')}: {e}")

        info = ServerProcess(server_id, srv_def.get("name", server_id), proc=proc, log_file=log_file)
        info.restart_count = self._restart_counts.get(server_id, 0)
        with self._lock:
            self.processes[server_id] = info
        self._set_state(server_id, "running", pid=proc.pid, create_time=info.create_time,
                        started_at=info.start_time, restart_count=info.restart_count,
                        exit_code=None, log=log_path, console=True, crash_help=None)
        self._audit("server_start", server_id, user=user, pid=proc.pid)
        self._emit("started", server_id, name=info.name, pid=proc.pid, user=user)
        self._start_monitor(server_id)
        self._schedule_cpu_settings(server_id, proc.pid)
        print(f"[INFO] Started '{info.name}' (PID {proc.pid}), logging to {log_path}")
        if first["waiting"]:
            try:
                from core import first_settings
                first_settings.watch_first_start(self, server_id)
            except Exception as e:
                print(f"[WARN] first settings watch: {e}")
        return {"status": "started", "pid": proc.pid, "log_file": log_path, "server_id": server_id}

    def cpu_settings(self, server_id: str):
        """(cores, priority) configured for a server; cores [] = all cores."""
        from core.hosting import parse_core_list
        srv = self.get_server(server_id) or {}
        try:
            cores = parse_core_list(srv.get("cpuAffinity"))
        except ValueError:
            cores = []
        return cores, (srv.get("priority") or "normal")

    def _schedule_cpu_settings(self, server_id: str, pid: int):
        """Apply core pinning/priority now and again shortly after, because
        launchers (cmd.exe, .bat files) start the real game a moment later."""
        cores, prio = self.cpu_settings(server_id)
        if not cores and prio == "normal":
            return
        from core.hosting import apply_cpu_settings

        def run():
            for delay in (0, 5, 30):
                time.sleep(delay)
                if not pid_alive(pid):
                    return
                apply_cpu_settings(pid, cores, prio)
        threading.Thread(target=run, daemon=True, name="cpu-" + server_id).start()

    def firewall_ports(self, server_id: str) -> list:
        """Ports to allow in Windows Firewall. RCON (and other "this PC only" ports) are
        left out on purpose: only BaoBun on this PC talks to them."""
        srv = self.get_server(server_id) or {}
        local = set()
        try:
            from core import port_plan
            if port_plan.spec(srv):
                rows = port_plan.rows(self, srv)
                return sorted({p for r in rows if not r["local"]
                               for p in range(r["port"], r["port"] + max(1, r["span"]))})
        except Exception:
            pass
        ports = {p["port"] for p in self.get_server_ports(server_id) if p.get("name") != "RCON"}
        for key in ("port", "queryPort"):
            if srv.get(key):
                ports.add(int(srv[key]))
        if srv.get("port") and srv.get("appId"):
            ports.add(int(srv["port"]) + 1)  # most Steam games use port+1 for queries
        try:
            from core import rcon_config
            rc = rcon_config.info(srv)
            if rc and rc["port"] != srv.get("port"):
                local.add(int(rc["port"]))
        except Exception:
            pass
        return sorted(ports - local)

    def open_firewall(self, server_id: str) -> bool:
        """Create inbound Windows Firewall rules for a server (one UAC prompt)."""
        from core.hosting import firewall_commands, run_elevated
        srv = self.get_server(server_id)
        if not srv:
            raise ValueError("Server not found")
        try:
            exe, _ = self._resolve_executable(srv)
        except Exception:
            exe = None
        if exe and exe.lower().endswith((".bat", ".cmd")):
            exe = None  # the rule must name the real program, not its launcher
        ok = run_elevated(firewall_commands(srv.get("name", server_id), exe, self.firewall_ports(server_id)))
        if ok:
            self._audit("firewall_open", server_id, ports=self.firewall_ports(server_id))
        return ok

    def crash_help(self, server_id: str, exit_code=None, uptime=None) -> list:
        """Plain-language reasons a server stopped (see core.diagnose)."""
        try:
            from core.diagnose import explain
            path = self.get_console_log_path(server_id)
            lines = tail_lines(path, 300) if path else []
            # only look at the latest run (BaoBun writes a header at every start)
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].startswith("==== BaoBun: starting"):
                    lines = lines[i + 1:]
                    break
            return explain(exit_code, lines, self.get_server(server_id) or {}, uptime=uptime)
        except Exception as e:
            print(f"[WARN] crash diagnosis failed: {e}")
            return []

    def _start_monitor(self, server_id: str):
        t = threading.Thread(target=self._monitor, args=(server_id,), daemon=True,
                             name="watch-" + server_id)
        t.start()
        self.watchdog_timers[server_id] = t

    def _monitor(self, server_id: str):
        """Wait for the process to exit, then decide whether to restart it."""
        info = self.processes.get(server_id)
        if info is None:
            return
        info.wait()
        exit_code = info.exit_code
        uptime = time.time() - (info.start_time or time.time())
        info.close_log()

        with self._lock:
            if self.processes.get(server_id) is not info:
                return  # replaced by a newer process
            requested = server_id in self._stop_requested
        self._last_exit[server_id] = {"code": exit_code, "uptime": uptime, "at": time.time()}

        if requested:
            return  # stop_server handles state/cleanup

        srv_def = self.get_server(server_id) or {}
        name = srv_def.get("name", info.name)
        stable_after = float(self.get_setting("loop_stability_minutes", 5) or 5) * 60
        if uptime >= stable_after:
            self._restart_counts[server_id] = 0
        count = self._restart_counts.get(server_id, 0)
        max_r = srv_def.get("maxRestarts")
        if max_r is None:
            max_r = self.get_setting("loop_max_restarts", 5)
        try:
            max_r = int(max_r)
        except (TypeError, ValueError):
            max_r = 5
        crashed_fast = uptime < 30

        help_ = self.crash_help(server_id, exit_code, uptime)
        self._set_state(server_id, "crashed", pid=None, exit_code=exit_code, crash_help=help_)
        self._audit("server_crash", server_id, exit_code=exit_code, uptime=round(uptime),
                    reason=help_[0]["title"] if help_ else None)
        self._emit("crashed", server_id, name=name, exit_code=exit_code, uptime=uptime,
                   reason=help_[0]["title"] if help_ else "", help=help_)

        if not srv_def.get("autoRestart"):
            print(f"[WATCHDOG] '{name}' exited (code {exit_code}); auto-restart is off")
            return
        if count >= max_r:
            print(f"[WATCHDOG] '{name}' exited (code {exit_code}); giving up after {max_r} restarts")
            self._emit("gave_up", server_id, name=name, exit_code=exit_code, restarts=count,
                       reason=help_[0]["title"] if help_ else "", help=help_)
            return

        self._restart_counts[server_id] = count + 1
        backoff = min(120, 10 * (count + 1)) if crashed_fast else 5
        print(f"[WATCHDOG] '{name}' exited (code {exit_code}) after {uptime:.0f}s; "
              f"restart {count + 1}/{max_r} in {backoff}s")
        self._set_state(server_id, "restarting", pid=None, exit_code=exit_code,
                        restart_count=count + 1, crash_help=help_)
        self._emit("restarting", server_id, name=name, attempt=count + 1, max=max_r)
        deadline = time.time() + backoff
        while time.time() < deadline:
            if server_id in self._stop_requested:
                self._set_state(server_id, "stopped", pid=None)
                return
            time.sleep(0.5)
        with self._lock:
            if self.processes.get(server_id) is info:
                self.processes.pop(server_id, None)
            self._states.pop(server_id, None)
        try:
            self.start_server(server_id, _from_watchdog=True)
        except Exception as e:
            print(f"[WARN] Watchdog restart failed for {server_id}: {e}")
            self._set_state(server_id, "crashed", pid=None)

    def adopt_running_servers(self) -> list[str]:
        """Track servers still running from an earlier session (or an exited
        GUI) so they are monitored and can be stopped. Their console is not
        available because the original stdin pipe belonged to that process."""
        adopted = []
        for srv in self.load_servers():
            sid = srv.get("id")
            if not sid or sid in self.processes:
                continue
            rec = self._read_registry(sid)
            pid = rec.get("pid")
            if not pid or not pid_alive(pid, rec.get("create_time")):
                continue
            if self._owner_alive(rec):
                continue  # someone else is actively managing it
            info = ServerProcess(sid, srv.get("name", sid), pid=pid,
                                 create_time=rec.get("create_time"),
                                 started_at=rec.get("started_at"))
            info.adopted = True
            info.restart_count = int(rec.get("restart_count") or 0)
            self._restart_counts[sid] = info.restart_count
            self.processes[sid] = info
            self._set_state(sid, "running", pid=pid, create_time=info.create_time,
                            started_at=info.start_time, console=False)
            self._start_monitor(sid)
            adopted.append(sid)
            print(f"[INFO] Adopted running server '{info.name}' (PID {pid})")
        return adopted

    def stop_server(self, server_id: str, user: str = "system", timeout: float | None = None) -> dict:
        """Ask the server to save and exit; force-stop the whole process tree after a grace period."""
        if server_id not in self.processes and self.agent_available():
            return self._forward("server.stop", timeout=330, server_id=server_id, user=user)

        srv_def = self.get_server(server_id) or {"id": server_id}
        with self._lock:
            info = self.processes.get(server_id)
            self._stop_requested.add(server_id)
        if info is None:
            rec = self._read_registry(server_id)
            if rec.get("pid") and pid_alive(rec.get("pid"), rec.get("create_time")):
                info = ServerProcess(server_id, srv_def.get("name", server_id), pid=rec["pid"],
                                     create_time=rec.get("create_time"),
                                     started_at=rec.get("started_at"))
                info.adopted = True
                with self._lock:
                    self.processes[server_id] = info
        if info is None or not info.is_alive:
            if self._states.get(server_id) in ("updating", "restarting", "starting"):
                self._set_state(server_id, "stopped", pid=None)
            with self._lock:
                self.processes.pop(server_id, None)
            return {"status": "not_running"}

        name = srv_def.get("name", info.name)
        self._set_state(server_id, "stopping", pid=info.pid)
        self._emit("stopping", server_id, name=name)

        sent = self._request_graceful_stop(srv_def, info)
        plugin = self.plugin_for(srv_def)
        grace = timeout
        if grace is None:
            grace = srv_def.get("stopTimeout")
        if grace is None:
            grace = plugin.stop_timeout(srv_def) if plugin else 30
        grace = float(grace) if sent else min(5.0, float(grace))

        exited = info.wait(timeout=grace)
        forced = False
        if not exited:
            forced = True
            print(f"[INFO] '{name}' did not exit within {grace:.0f}s; force-stopping process tree")
            kill_tree(info.pid, timeout=10)
            info.wait(timeout=5)
        info.close_log()

        with self._lock:
            if self.processes.get(server_id) is info:
                self.processes.pop(server_id, None)
            self._states.pop(server_id, None)
        self._set_state(server_id, "stopped", pid=None, exit_code=info.exit_code)
        self._audit("server_stop", server_id, user=user, graceful=not forced)
        self._emit("stopped", server_id, name=name, graceful=not forced, user=user)
        return {"status": "stopped", "graceful": not forced}

    def _request_graceful_stop(self, srv_def: dict, info) -> bool:
        """Send the game's save-and-quit command(s). Returns True if any request went out."""
        custom = (srv_def.get("stopCommand") or "").strip()
        if custom and info.can_send:
            ok = False
            for c in [x.strip() for x in custom.split(";") if x.strip()]:
                ok = info.send(c) or ok
            return ok
        plugin = self.plugin_for(srv_def)
        if plugin is not None:
            try:
                return bool(plugin.graceful_stop(srv_def))
            except Exception as e:
                print(f"[WARN] graceful stop failed for {srv_def.get('name')}: {e}")
        return False

    def restart_server(self, server_id: str, user: str = "system") -> dict:
        """Restart a managed server."""
        if server_id not in self.processes and self.agent_available():
            return self._forward("server.restart", timeout=360, server_id=server_id, user=user)
        self.stop_server(server_id, user=user)
        time.sleep(1)
        return self.start_server(server_id, user=user)

    def stop_all(self, user: str = "system") -> dict:
        results = {}
        threads = []
        for srv in self.load_servers():
            sid = srv["id"]
            if self.is_running(sid):
                t = threading.Thread(target=lambda s=sid: results.__setitem__(s, self.stop_server(s, user=user)),
                                     daemon=True)
                t.start()
                threads.append(t)
        for t in threads:
            t.join(timeout=360)
        return results

    def generate_start_bat(self, server_id: str) -> str:
        """Write a double-clickable start.bat next to the server (for manual use).

        BaoBun launches the executable directly, so this file is only a
        convenience. It never contains a Steam password: account games log in
        with the username only and SteamCMD reuses its cached login.
        """
        srv_def = self.get_server(server_id)
        if not srv_def:
            raise ValueError(f"Server {server_id} not found")

        directory = srv_def.get("directory", "")
        if not directory:
            raise ValueError(f"Server '{srv_def.get('name')}' has no directory")

        exe, _args = self._resolve_executable(srv_def)

        validate_line = ""
        app_id = srv_def.get("appId")
        if app_id and srv_def.get("updateBeforeStart"):
            try:
                steamcmd = self._steamcmd_path()
                if srv_def.get("requiresAccount"):
                    user = self.get_setting("steam_username", "") or ""
                    login = "+login " + user if user else "+login anonymous"
                else:
                    login = "+login anonymous"
                validate_line = (
                    '"' + steamcmd + '" ' + login
                    + ' +force_install_dir "' + directory
                    + '" +app_update ' + str(app_id) + ' validate +quit'
                )
            except Exception as e:
                print(f"[WARN] start.bat validate step skipped: {e}")

        lines = ["@echo off",
                 "REM Generated by BaoBun Control Panel for manual launching.",
                 'cd /d "' + directory + '"']
        if validate_line:
            lines.append("echo Updating game files (SteamCMD)...")
            lines.append(validate_line)
        mc_line = None
        try:
            from core import minecraft as _mc
            if _mc.looks_minecraft(srv_def):
                mc_line = _mc.start_bat_line(srv_def, exe, srv_def.get("launchArgs") or "")
        except Exception:
            mc_line = None
        if mc_line and os.path.basename(exe).lower() == "run.bat":
            return os.path.join(directory, "run.bat")   # Forge/NeoForge ship their own launcher
        from core import game_defaults as _gd
        start_in = _gd.working_dir(srv_def, exe, directory)
        if start_in != directory:
            lines.append('cd /d "' + start_in + '"')
        lines.append(mc_line or ('"' + exe + '" ' + (srv_def.get("launchArgs") or "")))
        lines.append("if errorlevel 1 pause")

        # Unique name per server so ARK cluster maps sharing one install folder
        # don't overwrite each other's launcher.
        bat_name = srv_def.get("start_bat_name") or "start.bat"
        bat_path = os.path.join(directory, bat_name)
        new_text = "\r\n".join(lines) + "\r\n"
        try:
            if Path(bat_path).read_text(encoding="utf-8") == new_text:
                return bat_path
        except Exception:
            pass
        Path(bat_path).write_text(new_text, encoding="utf-8")
        return bat_path

    def _resolve_executable(self, srv_def: dict) -> tuple[str, list[str]]:
        """Determine executable and arguments from server definition."""
        srv_type = srv_def.get("type", "custom")
        directory = srv_def.get("directory") or ""

        if not directory:
            raise ValueError(f"Server '{srv_def.get('name', 'unknown')}' has no directory configured")

        exe = srv_def.get("executable")
        if not exe:
            if srv_type == "vintage-story":
                for cand in ("VintagestoryServer.exe", "StratumServer.exe"):
                    if os.path.isfile(os.path.join(directory, cand)):
                        exe = os.path.join(directory, cand)
                        break
                else:
                    exe = os.path.join(directory, "VintagestoryServer.exe")
            elif srv_type == "steamcmd":
                exe = os.path.join(directory, "srcds.exe")
            else:
                exe = os.path.join(directory, "server.exe")
        elif not os.path.isabs(exe):
            exe = os.path.join(directory, exe)
        if srv_type == "vintage-story" and not os.path.isfile(exe):
            for cand in ("VintagestoryServer.exe", "StratumServer.exe"):
                if os.path.isfile(os.path.join(directory, cand)):
                    exe = os.path.join(directory, cand)
                    break
        if srv_type == "steamcmd" and not os.path.isfile(exe) and os.path.isdir(directory):
            # the preset's path can be out of date - look for the server program and remember it
            try:
                from core.game_catalog import find_server_exe
                found = find_server_exe(directory, exe)
            except Exception:
                found = None
            if found:
                exe = found
                self._remember_exe(srv_def.get("id"), found)
        return (exe, split_args(srv_def.get("launchArgs") or ""))

    def _remember_exe(self, server_id, exe):
        try:
            servers = self.load_servers()
            for s in servers:
                if s.get("id") == server_id and s.get("executable") != exe:
                    s["executable"] = exe
                    self.save_servers(servers)
                    print(f"[INFO] {s.get('name')}: found the server program at {exe}")
                    break
        except Exception:
            pass

    # ── Status Queries ───────────────────────────────────

    def get_all_statuses(self) -> list[dict]:
        """Current status of every server (fast: no subprocesses are spawned)."""
        statuses = []
        for srv in self.load_servers():
            sid = srv.get("id")
            if not sid:
                continue
            status = {
                "id": sid,
                "name": srv.get("name", sid),
                "type": srv.get("type", "custom"),
                "directory": srv.get("directory", ""),
                "folder": srv.get("folder", ""),
                "isRunning": False,
                "state": "stopped",
                "pid": None,
                "restartCount": 0,
                "exitCode": None,
                "managedBy": None,
                "consoleAvailable": False,
                "cpuPercent": None,
                "memUsageMB": None,
                "uptimeSeconds": None,
            }
            try:
                d = srv.get("directory") or ""
                # a folder we can't see (e.g. a share on another PC that is off) is "unknown", not missing
                exe_path = self._resolve_executable(srv)[0]
                status["installed"] = os.path.isfile(exe_path) if d and os.path.isdir(d) else None
                if status["installed"] is False:
                    from core.game_installers import folder_is_empty
                    if not folder_is_empty(d):   # files are there, just not the program BaoBun expects
                        status["installed"] = None
                        status["exeMissing"] = os.path.basename(exe_path)
            except Exception:
                status["installed"] = None
            info = self.processes.get(sid)
            rec = self._read_registry(sid)
            pid = None
            started = None
            if info is not None and info.is_alive:
                pid = info.pid
                started = info.start_time
                status["managedBy"] = self.role
                status["consoleAvailable"] = info.can_send
                status["restartCount"] = info.restart_count
            elif rec.get("pid") and pid_alive(rec.get("pid"), rec.get("create_time")):
                pid = rec["pid"]
                started = rec.get("started_at")
                owner_alive = rec.get("owner_pid") == os.getpid() or pid_alive(rec.get("owner_pid"))
                status["managedBy"] = (rec.get("owner_role") or "other") if owner_alive else "orphan"
                status["consoleAvailable"] = (owner_alive and rec.get("owner_role") == "agent"
                                              and rec.get("console", True))
                status["restartCount"] = rec.get("restart_count") or 0
            state = self._states.get(sid) or rec.get("state") or "stopped"
            if pid and rec.get("players_at") and time.time() - rec["players_at"] < 180:
                status["playerCount"] = rec.get("players")
                status["maxPlayers"] = rec.get("max_players")
                if rec.get("players_list") is not None:
                    status["players"] = rec.get("players_list")
            if rec.get("update_available"):
                status["updateAvailable"] = True
            if pid:
                status["consoleVia"] = "stdin" if status["consoleAvailable"] else None
                try:
                    from core import rcon_config
                    prof = rcon_config.profile(srv)
                    if prof:
                        rc = rcon_config.info(srv)
                        if not prof.get("stdin"):
                            status["consoleAvailable"] = bool(rc)
                            status["consoleVia"] = "rcon" if rc else None
                            if not rc:
                                status["consoleHint"] = prof.get("setup")
                        elif rc and not status["consoleAvailable"]:
                            status["consoleAvailable"], status["consoleVia"] = True, "rcon"
                except Exception:
                    pass
                status["isRunning"] = True
                status["pid"] = pid
                status["state"] = state if state in ("stopping",) else "running"
                m = self._sampler.sample(pid, started)
                if m is not None:
                    status["cpuPercent"] = round(m.cpu_percent, 1)
                    status["memUsageMB"] = round(m.memory_mb)
                    status["uptimeSeconds"] = round(m.uptime_seconds)
            else:
                transient = state in ("starting", "updating", "restarting")
                # A transient state written by an owner that has since died is stale.
                if transient and sid not in self._states and not pid_alive(rec.get("owner_pid")):
                    state = "stopped"
                status["state"] = state if state in ("starting", "updating", "restarting", "crashed") else "stopped"
                status["exitCode"] = rec.get("exit_code")
                if status["state"] in ("crashed", "restarting") and rec.get("crash_help"):
                    status["crashHelp"] = rec["crash_help"]
                if info is not None and info.exit_code is not None:
                    status["exitCode"] = info.exit_code
            statuses.append(status)
        return statuses

    # ── Config Editor ────────────────────────────────────

    def _other_server_dirs(self, server_id: str) -> list:
        """Other servers' folders (and BaoBun's own), skipped when searching one server's folder."""
        out = [str(self.config_path.parent)]
        for s in self.load_servers():
            if s.get("id") != server_id and s.get("directory"):
                out.append(s["directory"])
        return out

    def list_configs(self, server_id: str) -> list[dict]:
        """Find editable config files for a server (curated names + recursive scan)."""
        servers = self.load_servers()
        srv_def = next((s for s in servers if s["id"] == server_id), None)
        if not srv_def:
            return []

        directory = srv_def["directory"]
        if not directory or not os.path.isdir(directory):
            return []

        found = {}

        def add(rel):
            rel = rel.replace("\\", "/")
            if rel in found:
                return
            full = os.path.join(directory, rel)
            if not os.path.isfile(full):
                return
            found[rel] = {"relative": rel, "absolute": full, "format": self._detect_config_format(full)}

        # 1) Curated, well-known config files (checked at root and Data/).
        curated = [
            "stratum.json", "serverconfig.json", "servermagicnumbers.json",
            "server.json", "config.json", "settings.json",
            "server.ini", "server.cfg", "server.properties",
            "config.ini", "config.cfg", "GameUserSettings.ini",
            "Engine.ini", "Game.ini", ".env",
        ]
        for sub in ("", "Data"):
            for name in curated:
                rel = (sub + "/" + name) if sub else name
                add(rel)

        # 2) Recursive scan for non-JSON config extensions (low flood risk).
        scan_ext = (".ini", ".cfg", ".conf", ".properties", ".env", ".toml", ".yaml", ".yml")
        exclude = {"logs", "log", "saves", "save", "backup", "backups", "cache",
                   "node_modules", ".git", "mods", "assets", "redist", "_commonredist",
                   "steamapps", "bin", "lib"}
        # Files the game wrote itself on/after its first start (see _note_config_baseline) are
        # almost always its settings: those come first, and .json / .xml ones count too.
        base = self._read_registry(server_id)
        base_at = float(base.get("cfg_baseline_at") or 0)
        base_files = set(base.get("cfg_baseline") or [])
        for root, rel_root, depth, files in walk_server_dir(directory, 4, exclude, self._other_server_dirs(server_id)):
            segs = [x.lower() for x in rel_root.split(os.sep)] if rel_root != "." else []
            if any(x.endswith("_data") or x in ("monobleedingedge", "embedruntime", "engine", "jre", "jre64",
                                                 "jdk", "java") for x in segs):
                continue          # game engine / runtime files (Unity, Mono, Unreal), not server settings
            for name in files:
                low = name.lower()
                if low in ("browscap.ini", "desktop.ini", "unins000.ini"):
                    continue      # runtime / Windows files that happen to end in .ini
                if not low.endswith(scan_ext + (".json", ".xml")):
                    continue
                full = os.path.join(root, name)
                rel = os.path.relpath(full, directory).replace("\\", "/")
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if st.st_size > 1_000_000:
                    continue
                fresh = bool(base_at) and (rel not in base_files or st.st_mtime > base_at + 1)
                if low.endswith((".json", ".xml")) and not fresh:
                    continue      # data files are everywhere - only the ones the game wrote
                if len(found) >= 300:
                    break
                add(rel)
                if fresh and rel in found:
                    found[rel]["fresh"] = True
        return sorted(found.values(), key=lambda x: (not x.get("fresh"), x["relative"].lower()))

    def _note_config_baseline(self, srv_def: dict):
        """Before a server's first start, remember which settings-like files already
        exist, so files the game creates or changes afterwards can be told apart."""
        sid = srv_def["id"]
        if self._read_registry(sid).get("cfg_baseline_at"):
            return
        directory = srv_def.get("directory") or ""
        names = []
        exts = (".ini", ".cfg", ".conf", ".properties", ".env", ".toml", ".yaml", ".yml", ".json", ".xml")
        for root, rel_root, depth, files in walk_server_dir(directory, 4, ("logs", "steamapps", ".git"),
                                                             self._other_server_dirs(sid)):
            for name in files:
                if name.lower().endswith(exts):
                    names.append(os.path.relpath(os.path.join(root, name), directory).replace("\\", "/"))
                    if len(names) >= 5000:
                        break
        self._write_registry(sid, cfg_baseline_at=time.time(), cfg_baseline=names)
    def _detect_config_format(self, filepath: str) -> str:
        """Detect config file format by extension and content."""
        ext = Path(filepath).suffix.lower()
        if ext == ".json":
            return "json"
        if ext in (".yaml", ".yml"):
            return "yaml"
        if ext == ".toml":
            return "toml"
        if ext in (".ini", ".cfg", ".properties"):
            return "ini"
        if ext == ".xml":
            return "xml"
        # Heuristic: look for key=value or [section] patterns
        try:
            text = Path(filepath).read_text(encoding="utf-8", errors="replace")[:2000]
            if "[" in text and "]" in text and "=" in text:
                return "ini"
        except Exception:
            pass
        return "text"

    def _parse_ini_config(self, text: str) -> dict:
        """Parse INI-style config into sections/keys."""
        import configparser
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(text)
        result = {"sections": {}}
        for section in parser.sections():
            result["sections"][section] = dict(parser.items(section))
        if parser.defaults():
            result["defaults"] = dict(parser.defaults())
        return result

    def _write_ini_config(self, data: dict) -> str:
        """Write INI-style config from sections/keys."""
        import configparser
        from io import StringIO
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        for section, keys in data.get("sections", {}).items():
            parser[section] = keys
        if data.get("defaults"):
            for k, v in data["defaults"].items():
                parser['DEFAULT'][k] = v
        output = StringIO()
        parser.write(output)
        return output.getvalue()

    def _parse_kv_config(self, text: str) -> dict:
        """Parse generic key=value config (one per line, # comments)."""
        result = {"keys": {}}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                continue
            if "=" in stripped:
                key, _, val = stripped.partition("=")
                result["keys"][key.strip()] = val.strip()
        return result

    def _write_kv_config(self, data: dict) -> str:
        """Write generic key=value config."""
        lines = []
        for k, v in data.get("keys", {}).items():
            lines.append(f"{k}={v}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _safe_server_path(srv_def: dict, file_rel: str) -> str:
        """Resolve a config path and refuse anything outside the server's folder
        (e.g. "../../Windows/..."), or files that don't exist."""
        base = os.path.realpath(srv_def.get("directory") or "")
        full = os.path.realpath(os.path.join(base, file_rel or ""))
        if not base or os.path.commonpath([base, full]) != base:
            raise ValueError("That file is outside the server's folder")
        if not os.path.isfile(full):
            raise FileNotFoundError("Config file not found: " + str(file_rel))
        return full

    def load_config(self, server_id: str, file_rel: str) -> dict:
        """Load a config file for editing, with format detection."""
        servers = self.load_servers()
        srv_def = next((s for s in servers if s["id"] == server_id), None)
        if not srv_def:
            raise ValueError("Server not found")

        full_path = self._safe_server_path(srv_def, file_rel)
        fmt = self._detect_config_format(full_path)
        text = Path(full_path).read_text(encoding="utf-8", errors="replace")

        # A file that doesn't parse still opens as plain text, so a broken
        # config can be fixed in the editor instead of being impossible to open.
        try:
            if fmt == "json":
                import json as _json
                return {"path": full_path, "format": "json", "data": _json.loads(text), "raw_text": text}
            if fmt == "yaml":
                return {"path": full_path, "format": "yaml", "data": yaml.safe_load(text), "raw_text": text}
            if fmt == "toml":
                try:
                    import tomllib
                    data = tomllib.loads(text)
                except ImportError:
                    import toml as _toml
                    data = _toml.loads(text)
                return {"path": full_path, "format": "toml", "data": data, "raw_text": text}
            if fmt == "ini":
                # many game .ini files have no [section]; those fall through to key=value
                return {"path": full_path, "format": "ini", "data": self._parse_ini_config(text),
                        "raw_text": text}
        except Exception as e:
            if fmt in ("json", "yaml", "toml"):
                return {"path": full_path, "format": "text", "data": None, "raw_text": text,
                        "parse_error": str(e)}
        kv = self._parse_kv_config(text)
        if kv["keys"]:
            return {"path": full_path, "format": "kv", "data": kv, "raw_text": text}
        return {"path": full_path, "format": "text", "data": None, "raw_text": text}

    def save_config(self, server_id: str, file_rel: str, payload: dict) -> None:
        """Save config with automatic backup, preserving format."""
        servers = self.load_servers()
        srv_def = next((s for s in servers if s["id"] == server_id), None)
        if not srv_def:
            raise ValueError("Server not found")

        full_path = self._safe_server_path(srv_def, file_rel)
        fmt = payload.get("format") or self._detect_config_format(full_path)
        ts = time.strftime("%Y%m%d_%H%M%S")
        shutil.copy2(full_path, f"{full_path}.bak.{ts}")

        data = payload.get("data", payload)
        raw = payload.get("raw_text")

        # Raw-text mode: the caller has already validated JSON/YAML/TOML where
        # needed and supplied verbatim text. Write it unchanged (preserves the
        # user's hand-edited content and formatting for every format).
        if raw is not None:
            Path(full_path).write_text(raw, encoding="utf-8")
            return

        # Structured form mode: re-serialize the parsed data for the format.
        if fmt == "json":
            import json as _json
            Path(full_path).write_text(_json.dumps(data, indent=2), encoding="utf-8")
        elif fmt == "yaml":
            Path(full_path).write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
        elif fmt == "toml":
            import toml as _toml
            Path(full_path).write_text(_toml.dumps(data), encoding="utf-8")
        elif fmt == "ini":
            Path(full_path).write_text(self._write_ini_config(data), encoding="utf-8")
        elif fmt == "kv":
            Path(full_path).write_text(self._write_kv_config(data), encoding="utf-8")
        else:
            Path(full_path).write_text(str(data), encoding="utf-8")

    # ── Batch File Editor ─────────────────────────────────

    def _settings_path(self) -> Path:
        """Path to the machine-local settings file (next to servers.json / the exe)."""
        return self.config_path.parent / "settings.json"

    SECRET_SETTINGS = ("steam_password", "alert_webhook_url", "curseforge_api_key", "discord_bot_token")

    def _read_settings(self) -> dict:
        try:
            data = json.loads(self._settings_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def get_setting(self, key: str, default=None):
        """Read a persisted, machine-specific setting (secrets are decrypted)."""
        data = self._read_settings()
        if key in self.SECRET_SETTINGS:
            enc = data.get(key + "_protected")
            if enc:
                try:
                    from core.secrets import unprotect
                    return unprotect(enc)
                except Exception as e:
                    print(f"[WARN] could not decrypt {key}: {e}")
                    return default
        return data.get(key, default)

    def migrate_secrets(self) -> list:
        """Encrypt any plaintext secrets left in settings.json / discord_config.json."""
        changed = []
        data = self._read_settings()
        for key in self.SECRET_SETTINGS:
            if data.get(key):
                try:
                    self.set_setting(key, data[key])
                    changed.append(key)
                except Exception as e:
                    print(f"[WARN] could not encrypt {key}: {e}")
        try:
            from core.secrets import migrate_discord_config
            if migrate_discord_config(self.config_path.parent / "discord_config.json"):
                changed.append("discord token")
        except Exception as e:
            print(f"[WARN] discord token migration: {e}")
        return changed

    def set_setting(self, key: str, value) -> None:
        """Persist a machine-specific setting."""
        sp = self._settings_path()
        data = {}
        if sp.exists():
            try:
                data = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        if key in self.SECRET_SETTINGS:
            from core.secrets import protect
            data.pop(key, None)
            if value:
                data[key + "_protected"] = protect(str(value))
            else:
                data.pop(key + "_protected", None)
        else:
            data[key] = value
        atomic_write_json(sp, data)

    def _steamcmd_path(self) -> str:
        """
        Locate (or download) SteamCMD and return the path to steamcmd.exe.

        Search order:
          1) A user-configured override (Settings -> SteamCMD path).
          2) A bundled 'SteamCMD' folder next to the app / servers.json.
          3) The per-user BaoBun SteamCMD cache (where auto-download lands).
          4) A system-wide Steam installation.
          5) Auto-download from Valve's CDN.
        """
        # 1) Explicit override
        override = self.get_setting("steamcmd_path")
        if override:
            p = Path(str(override))
            if p.is_file():
                return str(p)

        # 2-4) Well-known locations (app-relative first so a shipped copy wins).
        candidates = [
            self.config_path.parent / "SteamCMD" / "steamcmd.exe",
            _local_appdata() / "BaoBun" / "SteamCMD" / "steamcmd.exe",
            Path("C:/Program Files (x86)/Steam/steamcmd.exe"),
        ]
        for c in candidates:
            if c.is_file():
                return str(c)

        # 5) Auto-download
        return self._download_steamcmd()

    def _download_steamcmd(self) -> str:
        """Download and extract SteamCMD, returning the exe path."""
        steamcmd_dir = _local_appdata() / "BaoBun" / "SteamCMD"
        steamcmd_dir.mkdir(parents=True, exist_ok=True)
        zip_path = steamcmd_dir / "steamcmd.zip"
        urls = [
            "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip",
            "https://media.steampowered.com/client/installer/steamcmd.zip",
        ]
        last_err = None
        for url in urls:
            try:
                print(f"[INFO] Downloading SteamCMD from {url} ...")
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (BaoBunCP steamcmd)"})
                with urllib.request.urlopen(req, timeout=120) as resp, open(zip_path, "wb") as f:
                    shutil.copyfileobj(resp, f)
                with zipfile.ZipFile(zip_path, "r") as z:
                    z.extractall(steamcmd_dir)
                exe = steamcmd_dir / "steamcmd.exe"
                if exe.is_file():
                    try:
                        zip_path.unlink()
                    except OSError:
                        pass
                    return str(exe)
            except Exception as e:
                last_err = e
                print(f"[WARN] SteamCMD download from {url} failed: {e}")

        nl = chr(10)
        raise FileNotFoundError(
            "SteamCMD is not installed and could not be auto-downloaded"
            + (f" ({last_err})." if last_err else ".")
            + nl + nl
            + "Place steamcmd.exe in a 'SteamCMD' folder next to the app, "
            + "or set its full path in Settings (gear icon)."
        )

    def _prime_steamcmd(self, steamcmd: str) -> None:
        """
        Force SteamCMD's one-time self-update to complete before a real install.

        A freshly extracted steamcmd.exe is a small bootstrap that downloads
        ~70 MB of updates and re-launches itself on first run. That relaunch
        breaks a single blocking subprocess call (the game never installs), so
        we warm it up once and mark it primed with a marker file next to it.
        """
        steamcmd_dir = Path(steamcmd).parent
        marker = steamcmd_dir / ".baobun_primed"
        if marker.exists():
            return
        print("[INFO] Warming up SteamCMD (one-time self-update)...")
        try:
            prime_log = steamcmd_dir / "prime.log"
            with open(prime_log, "wb") as f:
                subprocess.run(
                    [steamcmd, "+quit"],
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
        except Exception as e:
            # Best-effort; the real install call will still attempt it.
            print(f"[WARN] SteamCMD warm-up: {e}")
        try:
            marker.write_text("ok", encoding="utf-8")
        except OSError:
            pass

    def _guess_game_executable(self, directory: str, app_id: str) -> str:
        """
        Guess the main server executable for a SteamCMD-installed game.

        Instead of a fragile hand-maintained App-ID table (which is easy to
        get wrong), scan the install directory recursively and pick the
        executable whose name most looks like a dedicated server binary.
        """
        _ = app_id  # App ID is informative only; the scan does the real work.
        scored = []
        avoid = ("unins", "install", "setup", "launcher", "crashhandler", "crashreport", "crashpad",
                 "redist", "dotnet", "vcredist", "dxwebsetup", "oalinst", "jwebserver", "java", "jabswitch",
                 "keytool", "rmiregistry", "steamerrorreporter", "eac", "easyanticheat", "be_service")
        strong = ("server", "dedicated", "headless", "srcds")
        skip_dirs = (".git", "redist", "_commonredist", "steamapps", "jre", "jre64", "jdk", "java", "content",
                     "paks", "movies", "engine")
        # shallow folders first: big installs (Space Engineers ...) have huge asset trees
        walked = list(walk_server_dir(directory, 2, skip_dirs, budget=40000, seconds=6.0))
        walked += [w for w in walk_server_dir(directory, 6, skip_dirs, budget=60000, seconds=8.0) if w[2] > 2]
        for root, _rel, _depth, files in walked:
            for name in files:
                if not name.lower().endswith(".exe"):
                    continue
                low = name.lower()
                if any(bad in low for bad in avoid):
                    continue
                full = os.path.join(root, name)
                if not Path(full).is_file():
                    continue
                score = 1  # it is a real .exe
                for kw in strong:
                    if kw in low:
                        score += 5
                # Prefer binaries closer to the install root.
                depth = os.path.relpath(full, directory).count(os.sep)
                score -= depth
                scored.append((score, full))

        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored[0][1]

        # Fallback: some servers (e.g. Project Zomboid) launch via .bat.
        bat_scored = []
        for root, _rel, _depth, files in walk_server_dir(directory, 4):
            for name in files:
                if not name.lower().endswith(".bat"):
                    continue
                low = name.lower()
                if any(bad in low for bad in avoid):
                    continue
                full = os.path.join(root, name)
                score = 0
                if "server" in low or "start" in low or "dedicated" in low:
                    score += 5
                depth = os.path.relpath(full, directory).count(os.sep)
                score -= depth
                bat_scored.append((score, full))
        if bat_scored:
            bat_scored.sort(key=lambda x: x[0], reverse=True)
            return bat_scored[0][1]

        return ""

    def create_game_launcher(self, server_id: str, app_id: str) -> dict:
        """Create a .bat launcher file for an installed SteamCMD game."""
        with self._lock:
            servers = self.load_servers()
            srv_def = next((s for s in servers if s["id"] == server_id), None)
            if not srv_def:
                raise ValueError(f"Server {server_id} not found")

        directory = srv_def.get("directory", "")
        if not directory or not Path(directory).exists():
            raise FileNotFoundError(f"Server directory not found: {directory}")

        cur = srv_def.get("executable") or ""
        cur_full = cur if (cur and os.path.isabs(cur)) else os.path.join(directory, cur) if cur else ""
        if cur_full and os.path.isfile(cur_full):
            exe = cur_full                 # the game's known program is there - keep it
        else:
            exe = self._guess_game_executable(directory, app_id)
        if not exe:
            if os.path.isdir(os.path.join(directory, "EmptySteamDepot")):
                raise FileNotFoundError(
                    "Steam finished without giving any game files for this server. Either its server "
                    "isn't offered on Steam for Windows any more, or it needs a Steam account that owns "
                    "the game (set one in Settings and install again).")
            raise FileNotFoundError(f"Could not find game executable for app {app_id} in {directory}")

        launch_args = srv_def.get("launchArgs", "") or ""

        # Persist the discovered executable on the server definition.
        for i, s in enumerate(servers):
            if s["id"] == server_id:
                servers[i]["executable"] = exe
                servers[i]["launchArgs"] = launch_args
                break
        self.save_servers(servers)

        # Generate the start.bat (SteamCMD validate step baked in for this app).
        bat_path = self.generate_start_bat(server_id)

        return {
            "status": "launcher_created",
            "bat_path": bat_path,
            "executable": exe,
            "app_id": app_id
        }

    STEAMCMD_ERROR_HINTS = {
        2: "SteamCMD failed to start or invalid arguments.",
        3: "SteamCMD could not update itself (network issue).",
        5: "Steam login failed (wrong password, or Steam Guard needs a one-time code - run steamcmd.exe once by hand and log in).",
        7: "SteamCMD could not connect to Steam servers (firewall, proxy, or network issue).",
        8: "Steam couldn't install the game files. Check the output below: \"No subscription\" means the game needs a Steam account that owns it (set it in Settings); otherwise try again in a minute.",
        15: "SteamCMD is already running in another process.",
        16: "Steam servers are busy or rate-limiting.",
    }

    def _steamcmd_run(self, pre_args: list, post_args: list, requires_account: bool,
                      log_path: str | None = None, timeout: int = 3600, capture: bool = False):
        """Run SteamCMD. Account passwords go through a temporary +runscript file
        (deleted right after) so they never appear on the command line."""
        steamcmd = self._steamcmd_path()
        self._prime_steamcmd(steamcmd)
        script = None
        if requires_account:
            user = self.get_setting("steam_username", "") or ""
            pw = self.get_setting("steam_password", "") or ""
            if not (user and pw):
                raise ValueError(
                    "This game requires a Steam account login (it cannot be downloaded "
                    "anonymously). Add your Steam username and password in Settings, then retry.")
            import tempfile
            fd, script = tempfile.mkstemp(prefix="baobun_login_", suffix=".txt")
            q = (lambda v: '"' + v + '"' if (" " in v or not v) else v)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("login " + q(user) + " " + q(pw) + "\n")
            login = ["+runscript", script]
        else:
            login = ["+login", "anonymous"]
        # Valve requires force_install_dir before logging in.
        cmd = [steamcmd] + list(pre_args) + login + list(post_args) + ["+quit"]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            if capture:
                return subprocess.run(cmd, capture_output=True, timeout=timeout,
                                      stdin=subprocess.DEVNULL, creationflags=flags)
            with open(log_path or os.devnull, "wb") as logf:
                return subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, timeout=timeout,
                                      stdin=subprocess.DEVNULL, creationflags=flags)
        finally:
            if script:
                try:
                    os.unlink(script)
                except OSError:
                    pass

    def _steamcmd_app_update(self, app_id: str, directory: str, requires_account: bool = False,
                             branch: str | None = None) -> None:
        """Run SteamCMD 'app_update <id> validate' into a directory (with warm-up)."""
        print(f"[INFO] SteamCMD app_update {app_id} -> {directory}"
              + (" (account login)" if requires_account else ""))
        log_path = os.path.join(directory, "steamcmd_install.log")
        post = ["+app_update", str(app_id)]
        if branch and branch != "public":
            post += ["-beta", branch]
        post.append("validate")

        def tail():
            try:
                return Path(log_path).read_bytes()[-4000:].decode("utf-8", errors="replace")
            except Exception:
                return ""
        proc = self._steamcmd_run(["+force_install_dir", directory], post, requires_account,
                                  log_path=log_path)
        # A fresh SteamCMD often fails the first install with "Missing configuration"
        # until it has fetched the game's details - fetching them and retrying fixes it.
        for attempt in range(2):
            if proc.returncode == 0 or "missing configuration" not in tail().lower():
                break
            print(f"[INFO] SteamCMD: 'Missing configuration' for {app_id} - refreshing app info and retrying")
            time.sleep(3)
            proc = self._steamcmd_run(["+force_install_dir", directory], ["+app_info_update", "1"] + post,
                                      requires_account, log_path=log_path)
        self._update_cache.pop(str(app_id), None)
        if proc.returncode != 0:
            full_output = tail()
            hint = self.STEAMCMD_ERROR_HINTS.get(proc.returncode, "Unknown SteamCMD error.")
            low = full_output.lower()
            if "missing configuration" in low:
                hint = ("Steam didn't hand over this game's details (\"Missing configuration\") even after "
                        "retrying. This is usually a hiccup on Steam's side - wait a minute and press Try again.")
            elif "after update job" in low and "logged in ok" not in low and "connecting anonymously" in low:
                # Anonymous login worked, so it isn't a login problem.
                hint = ("Steam couldn't finish writing the game files. Usual causes: the server "
                        "is still running (files in use), the drive is full, or the folder isn't "
                        "writable. Stop the server, check free space, and try again.")
            elif "no subscription" in low:
                hint = "This game needs a Steam account that owns it (set it in Settings)."
            nl = chr(10)
            raise RuntimeError(
                f"SteamCMD update failed (code {proc.returncode}). {hint}" + nl + nl
                + "SteamCMD output:" + nl + full_output
            )

    # ── Update detection (only download when Steam has a new build) ──

    @staticmethod
    def installed_buildid(directory: str, app_id: str) -> str | None:
        acf = os.path.join(directory, "steamapps", f"appmanifest_{app_id}.acf")
        try:
            text = Path(acf).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        m = re.search(r'"buildid"\s+"(\d+)"', text)
        return m.group(1) if m else None

    def latest_buildid(self, app_id: str, branch: str = "public") -> str | None:
        key = str(app_id) + "|" + branch
        hit = self._update_cache.get(key)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        try:
            proc = self._steamcmd_run([], ["+app_info_update", "1", "+app_info_print", str(app_id)],
                                      False, timeout=180, capture=True)
            out = (proc.stdout or b"").decode("utf-8", errors="replace")
        except Exception as e:
            print(f"[WARN] could not ask Steam for the latest build of {app_id}: {e}")
            return None
        i = out.find('"branches"')
        j = out.find('"' + branch + '"', i) if i >= 0 else -1
        m = re.search(r'"buildid"\s+"(\d+)"', out[j:]) if j >= 0 else None
        build = m.group(1) if m else None
        self._update_cache[key] = (time.time(), build)
        return build

    def check_steam_update(self, server_id: str) -> dict:
        """{"installed", "latest", "update_available": True/False/None (unknown)}."""
        srv = self.get_server(server_id) or {}
        app_id = str(srv.get("appId") or "")
        if not app_id:
            return {"update_available": None, "reason": "no Steam App ID"}
        branch = srv.get("steamBranch") or "public"
        installed = self.installed_buildid(srv.get("directory") or "", app_id)
        latest = self.latest_buildid(app_id, branch)
        available = None
        if installed is None:
            available = True   # not installed (or no manifest) -> install
        elif latest is not None:
            available = latest != installed
        return {"installed": installed, "latest": latest, "update_available": available,
                "app_id": app_id, "branch": branch}

    def check_update(self, server_id: str) -> dict:
        """Steam build check, or the Vintage Story version check."""
        srv = self.get_server(server_id) or {}
        if not srv.get("appId"):
            from core import game_installers as gi
            if gi.kind(srv) == "vintage-story":
                return gi.check_update(self, srv)
        return self.check_steam_update(server_id)

    # ── Shared installs (ARK cluster maps etc.) ──────────

    def refresh_update_flags(self, max_age_hours: float = 6.0) -> dict:
        """Check Steam for newer builds (one check per shared install) and remember
        the answer per server ("Update available" badges). Returns {server_id: bool}."""
        done, out = {}, {}
        from core import game_installers as gi
        for srv in self.load_servers():
            sid, app_id = srv.get("id"), str(srv.get("appId") or "")
            if sid and not app_id and gi.kind(srv) == "vintage-story" and gi.vs_is_vanilla(srv):
                rec = self._read_registry(sid)
                if time.time() - float(rec.get("update_checked_at") or 0) < max_age_hours * 3600:
                    out[sid] = bool(rec.get("update_available"))
                    continue
                info = gi.check_update(self, srv)
                if info.get("update_available") is None:
                    continue
                avail = bool(info["update_available"])
                self._write_registry(sid, update_available=avail, update_checked_at=time.time(),
                                     latest_build=info.get("latest"), installed_build=info.get("installed"),
                                     update_major=avail and not info.get("patch"))
                out[sid] = avail
                continue
            if not sid or not app_id or (srv.get("type") or "") not in ("steamcmd", "custom", ""):
                continue
            rec = self._read_registry(sid)
            if time.time() - float(rec.get("update_checked_at") or 0) < max_age_hours * 3600:
                out[sid] = bool(rec.get("update_available"))
                continue
            key = (os.path.normcase(srv.get("directory") or ""), app_id, srv.get("steamBranch") or "public")
            if key not in done:
                try:
                    done[key] = self.check_steam_update(sid)
                except Exception as e:
                    print(f"[WARN] update check for {srv.get('name')}: {e}")
                    done[key] = {}
            info = done[key]
            if info.get("latest") is None or info.get("installed") is None:
                continue   # Steam didn't answer, or not installed through SteamCMD: can't tell
            avail = bool(info.get("update_available"))
            self._write_registry(sid, update_available=avail, update_checked_at=time.time(),
                                 latest_build=info.get("latest"), installed_build=info.get("installed"))
            out[sid] = avail
        return out

    def install_group(self, server_id: str) -> list[str]:
        """IDs of every server sharing this server's install folder (itself included)."""
        srv = self.get_server(server_id)
        if not srv:
            return []
        def norm(d):
            return os.path.normcase(os.path.normpath(d or ""))
        d = norm(srv.get("directory"))
        return [s["id"] for s in self.load_servers() if s.get("id") and norm(s.get("directory")) == d]

    def update_group(self, server_id: str) -> list[str]:
        """Servers that must be updated together: everything sharing the install
        folder, plus - for an ARK cluster - every map of the cluster, even ones
        with their own install folder (maps on different game versions refuse
        transfers between them)."""
        group = list(self.install_group(server_id))
        srv = self.get_server(server_id) or {}
        cid = srv.get("clusterId")
        if cid:
            for s in self.load_servers():
                if s.get("clusterId") == cid and s.get("id") and s["id"] not in group \
                        and str(s.get("appId") or "") == str(srv.get("appId") or ""):
                    group.append(s["id"])
        return group

    def _installs_of(self, group: list[str]) -> list[dict]:
        """One server entry per distinct install folder in the group."""
        seen, out = set(), []
        for sid in group:
            s = self.get_server(sid) or {}
            d = os.path.normcase(os.path.normpath(s.get("directory") or ""))
            if d and d not in seen:
                seen.add(d)
                out.append(s)
        return out

    def update_server(self, server_id: str, user: str = "system", force: bool = False,
                      warn_minutes=None, skip_if_players: bool = False) -> dict:
        """Safely update a SteamCMD server.

        Checks Steam for a newer build first (unless force), warns players, stops
        *every* server sharing the install folder - and for an ARK cluster every
        map, whatever folder it runs from - updates each install folder once,
        checks they all ended up on the same build, then restarts the ones that
        were running.
        """
        if self.agent_available():
            return self._forward("server.update", timeout=7200, server_id=server_id, user=user,
                                 force=force, warn_minutes=warn_minutes, skip_if_players=skip_if_players)
        srv = self.get_server(server_id)
        if not srv:
            raise ValueError(f"Server {server_id} not found")
        app_id = str(srv.get("appId") or "")
        from core import game_installers as gi
        if not app_id and gi.kind(srv) == "vintage-story":
            return self._update_vintage_story(server_id, srv, user, force, warn_minutes, skip_if_players)
        if not app_id:
            raise ValueError("This server has no Steam App ID")
        group = self.update_group(server_id)
        installs = self._installs_of(group)
        if not force:
            infos = []
            for inst in installs:
                try:
                    infos.append(self.check_steam_update(inst["id"]))
                except Exception as e:
                    infos.append({"update_available": None, "error": str(e)})
            if infos and all(i.get("update_available") is False for i in infos):
                for sid in group:
                    self._write_registry(sid, update_available=False, update_checked_at=time.time())
                return {"status": "up_to_date", "build": infos[0].get("installed")}
        running = [sid for sid in group if self.is_running(sid)]
        if running and skip_if_players and any(self.player_count(sid) for sid in running):
            return {"status": "skipped", "reason": "players online"}
        if running:
            self.warn_players(running, "updating", warn_minutes)
        threads = [threading.Thread(target=self.stop_server, args=(sid, user), daemon=True)
                   for sid in running]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=400)
        for sid in running:
            self._set_state(sid, "updating", pid=None)
        builds = {}
        try:
            for inst in installs:
                self._steamcmd_app_update(str(inst.get("appId") or app_id), inst.get("directory"),
                                          bool(inst.get("requiresAccount")), inst.get("steamBranch"))
                try:
                    builds[inst.get("directory")] = self.installed_buildid(inst.get("directory"),
                                                                            str(inst.get("appId") or app_id))
                except Exception:
                    builds[inst.get("directory")] = None
            self._audit("server_update", server_id, user=user, group=group, installs=len(installs))
            for sid in group:
                self._write_registry(sid, update_available=False, update_checked_at=time.time())
        finally:
            for sid in running:
                self._states.pop(sid, None)
                try:
                    self.start_server(sid, user=user, _from_watchdog=True, _skip_update=True)
                except Exception as e:
                    print(f"[WARN] restart after update failed for {sid}: {e}")
        known = {b for b in builds.values() if b}
        out = {"status": "updated", "restarted": running, "group": group, "installs": len(installs),
               "builds": builds}
        if len(known) > 1:
            out["warning"] = ("The install folders ended up on different game builds (%s) - transfers between "
                              "maps may be refused. Run Update again." % ", ".join(sorted(map(str, known))))
            print("[WARN] " + out["warning"])
        return out

    def _update_vintage_story(self, server_id, srv, user, force, warn_minutes, skip_if_players) -> dict:
        """Download the newest official Vintage Story server over this install."""
        from core import game_installers as gi
        if not gi.vs_is_vanilla(srv):
            raise ValueError("This Vintage Story server isn't an official install BaoBun can update.")
        info = gi.check_update(self, srv)
        if not force and info.get("update_available") is False:
            self._write_registry(server_id, update_available=False, update_checked_at=time.time())
            return {"status": "up_to_date", "build": info.get("installed")}
        group = self.install_group(server_id)
        running = [sid for sid in group if self.is_running(sid)]
        if running and skip_if_players and any(self.player_count(sid) for sid in running):
            return {"status": "skipped", "reason": "players online"}
        if running:
            self.warn_players(running, "updating", warn_minutes)
        for sid in running:
            self.stop_server(sid, user)
        for sid in running:
            self._set_state(sid, "updating", pid=None)
        try:
            try:
                self.backup_server_world(server_id, reason="before-update")
            except Exception as e:
                print(f"[WARN] backup before the Vintage Story update failed: {e}")
            res = gi.install_vintage_story(srv, progress=gi.Progress(srv.get("directory")))
            self._audit("server_update", server_id, user=user, group=group, version=res.get("version"))
            for sid in group:
                self._write_registry(sid, update_available=False, update_checked_at=time.time())
        finally:
            for sid in running:
                self._states.pop(sid, None)
                try:
                    self.start_server(sid, user=user, _from_watchdog=True, _skip_update=True)
                except Exception as e:
                    print(f"[WARN] restart after update failed for {sid}: {e}")
        return {"status": "updated", "restarted": running, "group": group, "version": res.get("version")}

    # ── Player info / broadcasts ─────────────────────────

    def player_count(self, server_id: str):
        """Last known online player count (None if unknown)."""
        rec = self._read_registry(server_id)
        if rec.get("players_at") and time.time() - rec["players_at"] < 180:
            return rec.get("players")
        return None

    def poll_players(self, server_id: str):
        """Ask the game how many players are online; stores it in the registry."""
        srv = self.get_server(server_id)
        plugin = self.plugin_for(srv)
        if not srv or plugin is None or not self.is_running(server_id):
            return None
        try:
            res = plugin.query_players(srv)
        except Exception as e:
            res = None
        names = None
        if isinstance(res, dict):
            count, max_players, names = res.get("count"), res.get("max"), res.get("names")
        elif res:
            count, max_players = res
        else:
            found = self._players_from_log(srv, plugin)
            if found is None:
                return None
            mp = srv.get("maxPlayers")
            if mp is None and hasattr(plugin, "max_players"):
                try:
                    mp = plugin.max_players(srv)
                except Exception:
                    mp = None
            count, max_players, names = len(found), mp, \
                [{"name": n, "seconds": max(0, time.time() - t)} for n, t in sorted(found.items())]
        if count == 0:
            names = []
        rec = self._read_registry(server_id)
        extra = {"players_list": None}   # unknown names: don't keep showing an old list
        if names is not None:
            from core.players import merge_since
            extra["players_list"] = merge_since(rec.get("players_list") or [], names)
        self._write_registry(server_id, players=count, max_players=max_players,
                             players_at=time.time(), **extra)
        return count

    def _players_from_log(self, srv, plugin):
        """Online players from join/leave log lines (games with no query protocol)."""
        try:
            patterns = plugin.player_log_patterns(srv)
        except Exception:
            patterns = None
        if not patterns:
            return None
        if not hasattr(self, "_log_players"):
            from core.players import LogPlayers
            self._log_players = LogPlayers()
        sid = srv["id"]
        rec = self._read_registry(sid)
        cap = os.path.join(srv.get("directory") or "", "logs", f"baobun-{sid}.log")
        game_log = next((c for c in self.get_log_candidates(sid)
                         if os.path.isfile(c) and not c.endswith(f"baobun-{sid}.log")), None)
        found = None
        # BaoBun's console capture (this run only) and the game's own log: a player
        # counts as online if either source says so.
        if sid in self.processes and os.path.isfile(cap):
            found = self._log_players.update(sid + ":console", cap, patterns, run_started=rec.get("started_at"))
        if game_log:
            other = self._log_players.update(sid + ":game", game_log, patterns, run_started=rec.get("started_at"))
            if other is not None:
                found = dict(other, **(found or {}))
        return found

    def start_player_polling(self, interval: float = 30.0):
        if getattr(self, "_player_thread", None):
            return

        def loop():
            while True:
                for srv in self.load_servers():
                    sid = srv.get("id")
                    if sid and self.is_running(sid):
                        try:
                            self.poll_players(sid)
                        except Exception:
                            pass
                time.sleep(interval)
        self._player_thread = threading.Thread(target=loop, daemon=True, name="players")
        self._player_thread.start()

    def broadcast(self, server_id: str, message: str) -> bool:
        """Show a chat/announcement message to everyone on the server."""
        srv = self.get_server(server_id) or {}
        custom = srv.get("broadcastCommand")
        try:
            if custom and "{message}" in custom:
                return self.send_console_command(server_id, custom.replace("{message}", message)).get("status") == "sent"
            plugin = self.plugin_for(srv)
            return bool(plugin and plugin.broadcast(srv, message))
        except Exception as e:
            print(f"[WARN] broadcast failed for {srv.get('name')}: {e}")
            return False

    def warn_players(self, server_ids, action: str = "restarting", minutes=None):
        """Count down in-game (e.g. 10, 5, 1 minutes) before a restart/update.
        Skips the wait entirely for servers known to be empty."""
        if minutes is None:
            minutes = self.get_setting("warn_minutes", [10, 5, 1])
        try:
            steps = sorted({int(m) for m in (minutes or []) if int(m) > 0}, reverse=True)
        except (TypeError, ValueError):
            steps = [10, 5, 1]
        targets = [sid for sid in server_ids if self.player_count(sid) != 0]
        if not steps or not targets:
            return
        for sid in targets:      # Discord's activity feed says it too
            self._emit("warning", sid, name=(self.get_server(sid) or {}).get("name", sid), action=action,
                       minutes=steps[0])
        for i, m in enumerate(steps):
            for sid in targets:
                self.broadcast(sid, f"Server {action} in {m} minute{'s' if m != 1 else ''}.")
            nxt = steps[i + 1] if i + 1 < len(steps) else 0
            deadline = time.time() + (m - nxt) * 60
            while time.time() < deadline:
                if all(self.player_count(sid) == 0 for sid in targets):
                    return  # everyone left; no need to keep waiting
                time.sleep(5)
        for sid in targets:
            self.broadcast(sid, f"Server {action} now.")

    def install_steamcmd_game(self, server_id: str, app_id: str) -> dict:
        """Install a SteamCMD game, then create a launcher for it."""
        with self._lock:
            servers = self.load_servers()
            srv_def = next((s for s in servers if s["id"] == server_id), None)
            if not srv_def:
                raise ValueError(f"Server {server_id} not found")

        directory = srv_def.get("directory", "")
        if not directory or not Path(directory).exists():
            raise FileNotFoundError(f"Server directory not found: {directory}")

        requires_account = bool(srv_def.get("requiresAccount", False))
        self._steamcmd_app_update(app_id, directory, requires_account)

        launcher_result = self.create_game_launcher(server_id, app_id)
        self._record_install(server_id, app_id)

        return {
            "status": "installed",
            "app_id": app_id,
            "directory": directory,
            "launcher": launcher_result
        }

    def install_options(self, server_id: str) -> dict:
        """What Install / Update can do for this server (Steam, Vintage Story, Minecraft)."""
        from core import game_installers as gi
        srv = self.get_server(server_id)
        if not srv:
            raise ValueError(f"Server {server_id} not found")
        out = gi.options_for(srv)
        out["running"] = self.is_running(server_id)
        out["installedVersion"] = gi.installed_version(self, srv)
        return out

    def install_game(self, server_id: str, options: dict | None = None, user: str = "system") -> dict:
        """Download/install the game for any supported server kind.

        Steam games go through SteamCMD; Vintage Story and Minecraft are
        downloaded from their official sites (see core/game_installers.py)."""
        from core import game_installers as gi
        options = options or {}
        srv = self.get_server(server_id)
        if not srv:
            raise ValueError(f"Server {server_id} not found")
        k = gi.kind(srv)
        if k is None:
            raise ValueError("BaoBun can't download this game - copy its server files into the folder.")
        if self.is_running(server_id):
            raise RuntimeError("Stop the server first.")
        directory = srv.get("directory") or ""
        if not directory:
            raise ValueError("The server has no folder")
        os.makedirs(directory, exist_ok=True)
        if k == "steam":
            return self.install_steamcmd_game(server_id, str(srv["appId"]))
        prog = gi.Progress(directory)
        try:
            if k == "vintage-story":
                res = gi.install_vintage_story(srv, version=options.get("version"),
                                               channel=options.get("channel") or "stable", progress=prog)
                fields = {}
                cur = srv.get("executable") or ""
                if not cur or not os.path.isfile(cur if os.path.isabs(cur) else os.path.join(directory, cur)):
                    fields["executable"] = res["executable"]
                fields["gameVersion"] = None   # read it from the installed files from now on
            elif k == "hytale":
                from core import hytale_install
                res = hytale_install.install(srv, str(self.config_path.parent), prog)
                fields = dict(res.get("fields") or {})
                if res.get("version"):
                    fields["hytaleVersion"] = res["version"]
            else:
                res = gi.install_minecraft(srv, flavor=options.get("flavor") or srv.get("mcFlavor") or "vanilla",
                                           version=options.get("version"),
                                           accept_eula=bool(options.get("accept_eula")), progress=prog)
                fields = dict(res.get("fields") or {})
        except Exception as e:
            prog.log("FAILED: %s" % e)
            raise
        for note in res.get("notes") or []:
            prog.log("NOTE: " + note)
        with self._lock:
            servers = self.load_servers()
            for s2 in servers:
                if s2.get("id") == server_id:
                    for key, val in fields.items():
                        if val is None:
                            s2.pop(key, None)
                        else:
                            s2[key] = val
            self.save_servers(servers)
        self._audit("server_install", server_id, user=user, kind=k, version=res.get("version"))
        self._write_registry(server_id, update_available=False, update_checked_at=time.time())
        return {"status": "installed", "kind": k, "version": res.get("version"), "notes": res.get("notes") or []}

    def hytale_sign_in(self, server_id: str, user: str = "system", wait: float = 25.0) -> dict:
        """Ask a running Hytale server for a device sign-in link: keeps the sign-in
        encrypted on disk, then starts /auth login device and reads the link and
        code from its log. {"url", "code", "sent"}."""
        from core import hytale_install
        srv = self.get_server(server_id)
        if not srv:
            raise ValueError(f"Server {server_id} not found")
        if not self.is_running(server_id):
            raise RuntimeError("Start the server first - it hands out the sign-in link.")
        path = self.get_log_path(server_id)
        try:
            start = os.path.getsize(path) if path else 0
        except OSError:
            start = 0
        self.send_console_command(server_id, "/auth persistence Encrypted", user=user)
        self.send_console_command(server_id, "/auth login device", user=user)
        self._audit("hytale_signin", server_id, user=user)
        end = time.time() + wait
        while time.time() < end:
            time.sleep(1.0)
            try:
                with open(path, "rb") as f:
                    f.seek(start)
                    text = f.read(65536).decode("utf-8", "replace")
            except (OSError, TypeError):
                continue
            found = hytale_install.parse_signin(text)
            if found:
                return dict(found, sent=True)
        return {"url": None, "code": None, "sent": True}

    def update_steamcmd_game(self, server_id: str, app_id: str) -> dict:
        """Validate/refresh an existing SteamCMD game (no launcher recreation)."""
        with self._lock:
            servers = self.load_servers()
            srv_def = next((s for s in servers if s["id"] == server_id), None)
            if not srv_def:
                raise ValueError(f"Server {server_id} not found")

        directory = srv_def.get("directory", "")
        if not directory or not Path(directory).exists():
            raise FileNotFoundError(f"Server directory not found: {directory}")

        requires_account = bool(srv_def.get("requiresAccount", False))
        self._steamcmd_app_update(app_id, directory, requires_account)
        return {"status": "updated", "app_id": app_id, "directory": directory}

    def download_workshop_mods(self, server_id, mod_ids, workshop_appid=None):
        """Download Steam Workshop items into a server's install directory."""
        servers = self.load_servers()
        srv = next((s for s in servers if s["id"] == server_id), None)
        if not srv:
            raise ValueError("Server not found")
        directory = srv.get("directory", "")
        if not directory or not Path(directory).exists():
            raise FileNotFoundError("Server directory not found: " + directory)
        appid = str(workshop_appid or srv.get("appId") or "")
        if not appid:
            raise ValueError("No app ID available for Workshop downloads")
        ids = [str(x).strip() for x in mod_ids if str(x).strip()]
        if not ids:
            return {"downloaded": 0, "results": []}
        requires_account = bool(srv.get("requiresAccount", False))
        results = []
        for mid in ids:
            log_path = os.path.join(directory, "workshop_" + mid + ".log")
            proc = self._steamcmd_run(["+force_install_dir", directory],
                                      ["+workshop_download_item", appid, mid],
                                      requires_account, log_path=log_path)
            results.append({"mod_id": mid, "ok": proc.returncode == 0})
        return {"downloaded": sum(1 for r in results if r["ok"]), "results": results}


    # ── Vintage Story ModDB (browse + update) ───────────────────────

    VS_MODDB_API = "https://mods.vintagestory.at/api"

    def _vs_api_get(self, path, params=None):
        """GET JSON from the Vintage Story ModDB API."""
        import urllib.parse
        url = self.VS_MODDB_API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "BaoBunCP/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def vs_search_mods(self, text="", orderby="downloads", orderdirection="desc", limit=50):
        """Browse/search Vintage Story mods. Returns a list of mod dicts."""
        data = self._vs_api_get("/mods", {
            "text": text, "orderby": orderby, "orderdirection": orderdirection,
        })
        mods = data.get("mods", []) if isinstance(data, dict) else []
        if not isinstance(mods, list):
            mods = []
        if limit:
            mods = mods[:limit]
        return mods

    def vs_get_mod(self, modid):
        """Full info + releases for a mod by numeric id or modid string."""
        data = self._vs_api_get("/mod/" + str(modid))
        return data.get("mod", data) if isinstance(data, dict) else data

    def _vs_mods_folder(self, srv):
        """The folder for the server's own (downloaded) VS mods: <data path>/Mods.

        Never the install's Mods folder - that one holds the base game
        (VSSurvivalMod, VSEssentials, VSCreativeMod)."""
        if not srv.get("directory"):
            raise ValueError("Server directory not set")
        return os.path.join(vs_data_path(srv), "Mods")

    def _read_modinfo(self, zip_path):
        """Return (modid, version, name) from a mod zip or folder, or None.
        modid is lower-case (VS treats mod ids case-insensitively)."""
        from core.mods import read_vs_modinfo, _ci
        info = read_vs_modinfo(zip_path)
        if info is None:
            return None
        return (str(_ci(info, "modid", default="") or "").lower(), str(_ci(info, "version", default="") or ""),
                _ci(info, "name", default="") or "")

    def vs_list_installed_mods(self, server_id):
        """Scan the server's Mods folder and return installed mods."""
        servers = self.load_servers()
        srv = next((s for s in servers if s["id"] == server_id), None)
        if not srv:
            raise ValueError("Server not found")
        folder = self._vs_mods_folder(srv)
        result = []
        if not os.path.isdir(folder):
            return result
        for fn in sorted(os.listdir(folder)):
            p = os.path.join(folder, fn)
            if fn.lower().endswith(".zip"):
                info = self._read_modinfo(p)
                if info:
                    result.append({"filename": fn, "path": p, "modid": info[0],
                                   "version": info[1], "name": info[2]})
            elif os.path.isdir(p) and not fn.startswith(("_", ".")):
                info = self._read_modinfo(p)
                if info:
                    result.append({"filename": fn, "path": p, "modid": info[0],
                                   "version": info[1], "name": info[2]})
        return result

    def vs_server_version(self, server_id) -> str | None:
        """Vintage Story game version of a server ("1.20.3"), or None if unknown.

        Order: "gameVersion" on the server entry, the VintagestoryAPI.dll file
        version (Windows), then the version line in the server log."""
        srv = self.get_server(server_id) or {}
        if srv.get("gameVersion"):
            return str(srv["gameVersion"]).lstrip("v")
        directory = srv.get("directory") or ""
        dll = os.path.join(directory, "VintagestoryAPI.dll")
        v = file_version(dll) if os.path.isfile(dll) else None
        if v:
            parts = v.split(".")
            return ".".join(parts[:3])
        for path in self.get_log_candidates(server_id):
            for line in tail_lines(path, 4000):
                m = re.search(r"(?i)game\s*version[:\s]+v?(\d+\.\d+\.\d+)", line)
                if m:
                    return m.group(1)
        return None

    @staticmethod
    def _vs_pick_release(releases, game_version):
        """Newest release built for this game version: exact match, then same
        major.minor (e.g. 1.20.x). Falls back to the newest release."""
        if not releases:
            return None, "none"
        if not game_version:
            return releases[0], "unknown_version"
        def tags(r):
            return {str(t).lstrip("v") for t in (r.get("tags") or [])}
        for r in releases:
            if game_version in tags(r):
                return r, "exact"
        mm = ".".join(game_version.split(".")[:2])
        for r in releases:
            if any(t == mm or t.startswith(mm + ".") for t in tags(r)):
                return r, "minor"
        return releases[0], "incompatible"

    def vs_download_mod(self, server_id, modid, url=None, filename=None):
        """Download a VS mod into the server's Mods folder.

        Picks the newest release made for the server's game version, and removes
        older copies of the same mod so the server doesn't load two versions.
        """
        servers = self.load_servers()
        srv = next((s for s in servers if s["id"] == server_id), None)
        if not srv:
            raise ValueError("Server not found")
        folder = self._vs_mods_folder(srv)
        os.makedirs(folder, exist_ok=True)

        note = ""
        modidstr = None
        if not url or not filename:
            mod = self.vs_get_mod(modid)
            releases = mod.get("releases", []) if isinstance(mod, dict) else []
            if not releases:
                raise ValueError("No releases found for mod " + str(modid))
            gv = self.vs_server_version(server_id)
            rel, match = self._vs_pick_release(releases, gv)
            if match == "incompatible":
                note = (f"No release lists game version {gv}; installed the newest one "
                        f"({rel.get('modversion')}), which may not work.")
            elif match == "unknown_version":
                note = "Server game version unknown; installed the newest release."
            url = rel.get("mainfile") or rel.get("fileUrl")
            filename = rel.get("filename") or (
                rel.get("modidstr", "") + "-" + rel.get("modversion", "") + ".zip")
            modidstr = rel.get("modidstr") or ((mod.get("modidstrs") or [None])[0] if isinstance(mod, dict) else None)
            if not url:
                raise ValueError("No download URL for mod " + str(modid))

        filename = os.path.basename(filename)
        dest = os.path.join(folder, filename)
        req = urllib.request.Request(url, headers={"User-Agent": "BaoBunCP/1.0"})
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = resp.read()
        tmp = dest + ".partial"
        with open(tmp, "wb") as f:
            f.write(data)
        info = self._read_modinfo(tmp)
        os.replace(tmp, dest)
        new_id = (info[0] if info else None) or modidstr or (modid if isinstance(modid, str) else None)
        new_id = str(new_id).lower() if new_id else None
        removed = []
        if new_id:
            old_dir = os.path.join(os.path.dirname(folder), "Mods-old-versions")
            for m in self.vs_list_installed_mods(server_id):
                if (m.get("modid") or "").lower() == new_id and os.path.normcase(m["path"]) != os.path.normcase(dest):
                    os.makedirs(old_dir, exist_ok=True)
                    try:
                        shutil.move(m["path"], os.path.join(old_dir, os.path.basename(m["path"])))
                        removed.append(m["filename"])
                    except OSError:
                        pass
        result = {"filename": filename, "path": dest, "size": len(data), "replaced": removed}
        if note:
            result["note"] = note
        return result

    def vs_check_updates(self, server_id):
        """Compare installed mods against the ModDB (for this server's game version)."""
        installed = self.vs_list_installed_mods(server_id)
        gv = self.vs_server_version(server_id)
        results = []
        for m in installed:
            entry = dict(m)
            entry["status"] = "unknown"
            entry["latest"] = ""
            entry["update_available"] = False
            entry["game_version"] = gv
            if not m["modid"]:
                results.append(entry)
                continue
            try:
                mod = self.vs_get_mod(m["modid"])
                releases = mod.get("releases", []) if isinstance(mod, dict) else []
                rel, match = self._vs_pick_release(releases, gv)
                latest = rel.get("modversion", "") if rel else ""
                entry["latest"] = latest
                entry["compatible"] = match in ("exact", "minor", "unknown_version")
                entry["update_available"] = bool(latest and latest != m["version"]
                                                 and entry["compatible"])
                entry["status"] = "ok"
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = str(e)
            results.append(entry)
        return results

    def _record_install(self, server_id: str, app_id: str):

        """Persist install metadata for a server."""
        meta_path = Path(str(self.config_path).replace(".json", "_installs.json"))
        installs = {}
        if meta_path.exists():
            try:
                installs = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                installs = {}

        installs[server_id] = {
            "app_id": app_id,
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        meta_path.write_text(json.dumps(installs, indent=2), encoding="utf-8")

    # ── Logs ──────────────────────────────────────────────

    def get_log_candidates(self, server_id: str) -> list[str]:
        """Every existing log file for a server (game logs and BaoBun's console capture)."""
        srv_def = self.get_server(server_id)
        if not srv_def:
            return []
        directory = srv_def.get("directory") or ""
        paths = []
        plugin = self.plugin_for(srv_def)
        if plugin is not None:
            try:
                paths.extend(plugin.log_candidates(srv_def))
            except Exception:
                pass
        paths += [os.path.join(directory, "logs", f"baobun-{server_id}.log"),
                  os.path.join(directory, "server.log"),
                  os.path.join(directory, "console-output.log")]
        out = []
        for p in paths:
            if p and os.path.isfile(p) and p not in out:
                out.append(p)
        return out

    def get_log_path(self, server_id: str) -> str | None:
        """Best log file for a server: the game's own log (via its plugin), else BaoBun's capture."""
        srv_def = self.get_server(server_id)
        if not srv_def:
            return None
        directory = srv_def.get("directory") or ""
        candidates = []
        plugin = self.plugin_for(srv_def)
        if plugin is not None:
            try:
                candidates.extend(plugin.log_candidates(srv_def))
            except Exception:
                pass
        if srv_def.get("type") == "vintage-story":
            candidates += [os.path.join(directory, "Data", "Logs", "stratum-server.log"),
                           os.path.join(directory, "Data", "Logs", "server.log")]
        captured = os.path.join(directory, "logs", f"baobun-{server_id}.log")
        existing = [c for c in candidates if c and os.path.isfile(c)]
        # Prefer whichever was written most recently: game logs when the game
        # writes them, otherwise the console output BaoBun captured.
        if os.path.isfile(captured):
            existing.append(captured)
        for name in ("server.log", "console-output.log"):
            cand = os.path.join(directory, name)
            if os.path.isfile(cand):
                existing.append(cand)
        if not existing:
            return None
        return max(existing, key=lambda p: os.path.getmtime(p))

    def get_console_log_path(self, server_id: str) -> str | None:
        """The server's console output as BaoBun captured it (includes replies to
        commands); falls back to the game's own log file."""
        srv = self.get_server(server_id) or {}
        cap = os.path.join(srv.get("directory") or "", "logs", f"baobun-{server_id}.log")
        if os.path.isfile(cap):
            return cap
        return self.get_log_path(server_id)

    def get_logs(self, server_id: str, last_n: int = 200) -> list[str]:
        """Read recent log lines (reads only the end of large files)."""
        log_file = self.get_log_path(server_id)
        if not log_file:
            return []
        return tail_lines(log_file, last_n)

    # ── Console Commands ─────────────────────────────────

    def console_route(self, server_id: str, srv: dict | None = None) -> str | None:
        """How a console command reaches this server: "stdin", "rcon" or None."""
        srv = srv or self.get_server(server_id) or {}
        info = self.processes.get(server_id)
        stdin_ok = info is not None and info.can_send
        try:
            from core import rcon_config
            prof = rcon_config.profile(srv)
            rc = rcon_config.info(srv) if prof else None
        except Exception:
            prof, rc = None, None
        if prof and not prof.get("stdin"):
            return "rcon" if rc else None   # this game ignores its console window
        if stdin_ok:
            return "stdin"
        return "rcon" if rc else None

    def send_console_command(self, server_id: str, command: str, user: str = "system") -> dict:
        """Send a command to the running server: its console (stdin), or RCON for
        games that ignore the console (ARK, Palworld, ...) or when the console isn't
        attached."""
        if server_id not in self.processes and self.agent_available():
            return self._forward("console.send", timeout=15, server_id=server_id,
                                 command=command, user=user)
        info = self.processes.get(server_id)
        if not self.is_running(server_id):
            raise RuntimeError("Server is not running")
        srv = self.get_server(server_id) or {}
        route = self.console_route(server_id, srv)
        if route == "rcon":
            return self._send_rcon(server_id, srv, command, user, info)
        if route is None:
            try:
                from core import rcon_config
                prof = rcon_config.profile(srv)
            except Exception:
                prof = None
            if prof and not prof.get("stdin"):
                raise RuntimeError("%s doesn't read commands from its console window - BaoBun uses RCON "
                                   "for it. %s" % (prof.get("name", "This game"), prof.get("setup") or ""))
            raise RuntimeError(
                "This server's console is not available (it was started outside this BaoBun session). "
                "Restart it from BaoBun to get the console back, or turn on RCON in its config.")
        if not info.send(command):
            raise RuntimeError("Could not write to the server console (it may be shutting down).")
        self._audit("console_command", server_id, user=user, command=command[:200])
        return {"status": "sent"}

    def _server_pid(self, server_id):
        info = self.processes.get(server_id)
        if info is not None and info.is_alive:
            return info.pid
        rec = self._read_registry(server_id)
        if rec.get("pid") and pid_alive(rec.get("pid"), rec.get("create_time")):
            return rec["pid"]
        return None

    def port_owner(self, port: int):
        """(owning BaoBun server id or None, pids listening on port) - (None, set()) if unknown."""
        try:
            import psutil
            owners = {c.pid for c in psutil.net_connections(kind="inet")
                      if c.laddr and c.laddr.port == int(port) and c.pid
                      and (c.type != socket.SOCK_STREAM or c.status == psutil.CONN_LISTEN)}
        except Exception:
            return None, set()
        if not owners:
            return None, set()
        for s in self.load_servers():
            pid = self._server_pid(s.get("id"))
            if not pid:
                continue
            try:
                import psutil
                tree = {pid} | {c.pid for c in psutil.Process(pid).children(recursive=True)}
            except Exception:
                tree = {pid}
            if owners & tree:
                return s["id"], owners
        return None, owners

    def _rcon_target_ok(self, server_id, port):
        """Make sure RCON on this port really reaches *this* server: two servers left
        on the same RCON port would otherwise get each other's commands (and stops)."""
        owner, pids = self.port_owner(port)
        if not pids or owner == server_id:
            return
        if owner:
            other = (self.get_server(owner) or {}).get("name", owner)
            raise RuntimeError("RCON port %s belongs to %s, not this server - BaoBun won't send the command "
                               "there. Give each server its own RCON port (Ports page)." % (port, other))
        # Another program (not a BaoBun server) answers on this port. That's normal when the
        # game hands RCON to a helper process or was started by a .bat that detaches, so
        # only note it - refusing here would turn clean stops into force-kills.

    def _send_rcon(self, server_id, srv, command, user, info=None) -> dict:
        from core import rcon_config
        try:
            target = rcon_config.info(srv)
            if target:
                self._rcon_target_ok(server_id, target["port"])
            reply = rcon_config.send(srv, command)
        except Exception as e:
            rcon_config.forget()
            raise RuntimeError("RCON command failed: %s" % e)
        # show the exchange in the live console (same file handle the game writes to)
        fh = getattr(info, "_log_file", None) if info is not None else None
        echoed = False
        if fh is not None:
            try:
                text = "[RCON] > %s\n" % command
                if reply:
                    text += "".join("[RCON] %s\n" % l for l in reply.splitlines()[:200])
                fh.write(text.encode("utf-8", "replace"))
                echoed = True
            except Exception:
                pass
        self._audit("console_command", server_id, user=user, command=command[:200], via="rcon")
        return {"status": "sent", "via": "rcon", "reply": reply, "echoed": echoed}

    def shutdown(self):
        """Stop every server this instance owns (used when the owner exits)."""
        for sid, info in list(self.processes.items()):
            try:
                if info.is_alive:
                    self.stop_server(sid)
            except Exception:
                pass

    def _cluster_ark_ini(self, cluster_id):
        """Locate the shared GameUserSettings.ini for an ARK cluster."""
        cid = (cluster_id or "").strip().replace(" ", "")
        for s in self.load_servers():
            if s.get("clusterId") == cid:
                d = s.get("directory", "")
                if d:
                    return os.path.join(
                        d, "ShooterGame", "Saved", "Config", "WindowsServer",
                        "GameUserSettings.ini")
        raise FileNotFoundError("No ARK cluster server found for cluster ID: " + cid)

    def get_ark_gameplay_settings(self, cluster_id):
        """Read curated gameplay settings from the cluster's GameUserSettings.ini."""
        ini = self._cluster_ark_ini(cluster_id)
        text = ""
        if Path(ini).exists():
            text = Path(ini).read_text(encoding="utf-8", errors="replace")
        values = {}
        in_section = False
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("["):
                in_section = (s.lower() == "[serversettings]")
                continue
            if in_section and "=" in s and not s.startswith((";", "//", "#")):
                k, _, v = s.partition("=")
                values[k.strip()] = v.strip()
        result = []
        for key, label, kind, default, help_text in ARK_GAMEPLAY_SETTINGS:
            result.append({
                "key": key, "label": label, "kind": kind,
                "value": values.get(key, default), "default": default,
                "help": help_text,
            })
        return result

    def set_ark_gameplay_settings(self, cluster_id, settings):
        """Write curated gameplay settings to the cluster's shared GameUserSettings.ini.

        Only keys present in ARK_GAMEPLAY_SETTINGS are changed; everything else in
        the file is preserved verbatim. Returns the number of keys written.
        """
        ini = self._cluster_ark_ini(cluster_id)
        p = Path(ini)
        if p.exists():
            ts = time.strftime("%Y%m%d_%H%M%S")
            shutil.copy2(ini, ini + ".bak." + ts)
            text = p.read_text(encoding="utf-8", errors="replace")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            text = ""

        valid = {k for k, *_ in ARK_GAMEPLAY_SETTINGS}
        updates = {str(k): str(v) for k, v in settings.items() if str(k) in valid}

        out = []
        in_section = False
        section_index = None
        written = set()
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("["):
                in_section = (s.lower() == "[serversettings]")
                out.append(line)
                if in_section:
                    section_index = len(out) - 1
                continue
            if in_section and "=" in s and not s.startswith((";", "//", "#")):
                k = s.partition("=")[0].strip()
                if k in updates:
                    out.append(k + "=" + updates[k])
                    written.add(k)
                else:
                    out.append(line)
                continue
            out.append(line)

        pending = [k for k in updates if k not in written]
        if pending:
            if section_index is not None:
                for j, k in enumerate(pending):
                    out.insert(section_index + 1 + j, k + "=" + updates[k])
            else:
                out.append("[ServerSettings]")
                for k in pending:
                    out.append(k + "=" + updates[k])

        p.write_text("\n".join(out) + "\n", encoding="utf-8")
        return len(updates)

    # ── ARK boost/rate profiles ─────────────────────────

    BOOST_PROFILE_KEYS = [
        "XPMultiplier", "TamingSpeedMultiplier", "HarvestAmountMultiplier",
        "MatingIntervalMultiplier", "EggHatchSpeedMultiplier",
        "BabyMatureSpeedMultiplier", "BabyCuddleIntervalMultiplier",
    ]

    DEFAULT_BOOST_PROFILES = [
        {"name": "Vanilla (1x)",
         "settings": {"XPMultiplier": "1.0", "TamingSpeedMultiplier": "1.0",
                      "HarvestAmountMultiplier": "1.0", "MatingIntervalMultiplier": "1.0",
                      "EggHatchSpeedMultiplier": "1.0", "BabyMatureSpeedMultiplier": "1.0",
                      "BabyCuddleIntervalMultiplier": "1.0"}},
        {"name": "Relaxed (3x)",
         "settings": {"XPMultiplier": "3.0", "TamingSpeedMultiplier": "3.0",
                      "HarvestAmountMultiplier": "3.0", "MatingIntervalMultiplier": "0.5",
                      "EggHatchSpeedMultiplier": "3.0", "BabyMatureSpeedMultiplier": "3.0",
                      "BabyCuddleIntervalMultiplier": "0.5"}},
        {"name": "Boosted (5x)",
         "settings": {"XPMultiplier": "5.0", "TamingSpeedMultiplier": "5.0",
                      "HarvestAmountMultiplier": "5.0", "MatingIntervalMultiplier": "0.3",
                      "EggHatchSpeedMultiplier": "5.0", "BabyMatureSpeedMultiplier": "5.0",
                      "BabyCuddleIntervalMultiplier": "0.3"}},
        {"name": "High Rate (10x)",
         "settings": {"XPMultiplier": "10.0", "TamingSpeedMultiplier": "10.0",
                      "HarvestAmountMultiplier": "10.0", "MatingIntervalMultiplier": "0.15",
                      "EggHatchSpeedMultiplier": "10.0", "BabyMatureSpeedMultiplier": "10.0",
                      "BabyCuddleIntervalMultiplier": "0.2"}},
    ]

    def _boost_profiles_path(self):
        return self.config_path.parent / "boost_profiles.json"

    def list_boost_profiles(self):
        sp = self._boost_profiles_path()
        if not sp.exists():
            sp.write_text(json.dumps(self.DEFAULT_BOOST_PROFILES, indent=2), encoding="utf-8")
            return list(self.DEFAULT_BOOST_PROFILES)
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return list(self.DEFAULT_BOOST_PROFILES)

    def save_boost_profile(self, name, settings):
        name = (name or "").strip()
        if not name:
            raise ValueError("Profile name is required")
        clean = {}
        for k in self.BOOST_PROFILE_KEYS:
            if k in settings:
                clean[k] = str(settings[k])
        profiles = [p for p in self.list_boost_profiles() if p.get("name") != name]
        profiles.append({"name": name, "settings": clean})
        self._boost_profiles_path().write_text(json.dumps(profiles, indent=2), encoding="utf-8")
        return profiles

    def delete_boost_profile(self, name):
        profiles = [p for p in self.list_boost_profiles() if p.get("name") != name]
        self._boost_profiles_path().write_text(json.dumps(profiles, indent=2), encoding="utf-8")
        return profiles

    def _backup_sources(self, srv):
        """(arcname-prefix, absolute folder or file) pairs to include in a world backup."""
        directory = srv.get("directory", "")
        explicit = list(srv.get("savePaths") or [])
        plugin = self.plugin_for(srv)
        if not explicit and plugin is not None:
            try:
                explicit = list(plugin.save_paths(srv) or [])
            except Exception:
                explicit = []
        # ARK: creatures/items/survivors waiting in a shared cluster folder outside the install
        m = re.search(r'(?i)(?:^|\s)-ClusterDirOverride=("[^"]*"|\S+)', srv.get("launchArgs") or "")
        if m:
            explicit.append(m.group(1).strip('"'))
        sources = []
        ext_index = 0
        for p in explicit:
            full = p if os.path.isabs(p) else os.path.join(directory, p)
            full = os.path.normpath(full)
            if not os.path.exists(full):
                continue
            try:
                rel = os.path.relpath(full, directory)
            except ValueError:  # different drive on Windows
                rel = ".."
            if rel.startswith(".."):
                sources.append(("_external/" + str(ext_index), full))
                ext_index += 1
            else:
                sources.append((rel.replace(os.sep, "/"), full))
        return sources

    def backup_server_world(self, server_id, dest_dir=None, keep=None, reason="manual"):
        """Back up a server's config + world (save) data to a timestamped zip.

        Uses the game plugin's save folders (which may live outside the install,
        e.g. %APPDATA%/VintagestoryData) plus BaoBun's config/save heuristics,
        asks a running server to flush its world first, and prunes old backups.
        """
        srv = self.get_server(server_id)
        if not srv:
            raise ValueError("Server not found")
        directory = srv.get("directory", "")
        if not directory or not os.path.isdir(directory):
            raise FileNotFoundError("Server directory not found: " + directory)
        dest = Path(dest_dir) if dest_dir else (self.config_path.parent / "backups" / server_id)
        dest.mkdir(parents=True, exist_ok=True)

        plugin = self.plugin_for(srv)
        if plugin is not None and self.is_running(server_id):
            try:
                plugin.pre_backup(srv)
            except Exception as e:
                print(f"[WARN] pre-backup save failed for {srv.get('name')}: {e}")

        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (srv.get("name") or "server"))
        ts = time.strftime("%Y%m%d_%H%M%S")
        zip_path = dest / (ts + "_" + safe + ".zip")
        n = 2
        while zip_path.exists():  # two backups in the same second
            zip_path = dest / (ts + "_" + safe + "_" + str(n) + ".zip")
            n += 1
        tmp_path = zip_path.with_suffix(".zip.partial")
        count = 0
        total = 0
        written = set()
        manifest = {"server_id": server_id, "name": srv.get("name"), "created": ts,
                    "reason": reason, "external": {}, "server": dict(srv)}
        try:        # so it can be installed at the same version on another PC
            from core import game_installers as _gi
            manifest["gameVersion"] = _gi.installed_version(self, srv)
        except Exception:
            pass
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            def add(full, arc):
                nonlocal count, total
                arc = arc.replace("\\", "/")
                if arc in written:
                    return
                low = full.lower()
                if low.endswith(("-wal", "-shm", "-journal")) and os.path.isfile(full.rsplit("-", 1)[0]):
                    return  # folded into the consistent copy of the main database file
                try:
                    snap = sqlite_snapshot(full)
                    z.write(snap or full, arcname=arc)
                    if snap:
                        os.unlink(snap)
                    written.add(arc)
                    count += 1
                    total += os.path.getsize(full)
                except OSError:
                    pass
            for rel in iter_backup_files(directory):
                add(os.path.join(directory, rel), rel)
            for prefix, full in self._backup_sources(srv):
                if prefix.startswith("_external/"):
                    manifest["external"][prefix] = full
                if os.path.isfile(full):
                    add(full, prefix if prefix.startswith("_external/") else prefix)
                    continue
                for root, _dirs, files in os.walk(full):
                    for fn in files:
                        fp = os.path.join(root, fn)
                        add(fp, prefix + "/" + os.path.relpath(fp, full))
            z.writestr("baobun_backup.json", json.dumps(manifest, indent=2))
        os.replace(tmp_path, zip_path)

        if keep is None:
            try:
                keep = int(self.get_setting("backup_keep", 10) or 10)
            except (TypeError, ValueError):
                keep = 10
        pruned = self._prune_backups(dest, keep)
        if self.db is not None:
            try:
                self.db.record_backup(server_id, str(zip_path), count, os.path.getsize(zip_path))
            except Exception:
                pass
        self._audit("backup_create", server_id, files=count, reason=reason)
        result = {"zip_path": str(zip_path), "files": count, "bytes": total, "pruned": pruned}
        mirror = self.mirror_backup(server_id, zip_path, keep)
        if mirror.get("copied"):
            result["mirror"] = mirror["copied"]
        elif mirror.get("error"):
            result["mirror_error"] = mirror["error"]
        if count == 0:
            result["warning"] = ("No world or config files were found to back up. Set the "
                                 "save folder for this server (\"savePaths\") so backups capture the world.")
        return result

    def mirror_backup(self, server_id, zip_path, keep=None) -> dict:
        """Copy a finished backup to the second backup folder (Settings > "Also copy
        backups to", e.g. another drive or a NAS) so a dead disk can't take the
        worlds with it. Old copies there are pruned the same way."""
        target = (self.get_setting("backup_mirror_dir", "") or "").strip()
        if not target:
            return {}
        srv = self.get_server(server_id) or {}
        folder = Path(target) / server_id
        try:
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / Path(zip_path).name
            tmp = dest.with_suffix(".zip.partial")
            shutil.copyfile(zip_path, tmp)
            os.replace(tmp, dest)
            name_file = folder / "server-name.txt"
            if not name_file.exists():
                name_file.write_text(srv.get("name") or server_id, encoding="utf-8")
            try:      # what the server is, so it can be set up again on another PC
                (folder / "server.json").write_text(json.dumps(srv, indent=2), encoding="utf-8")
            except OSError:
                pass
            self._prune_backups(folder, int(keep or self.get_setting("backup_keep", 10) or 10))
            if os.path.getsize(dest) != os.path.getsize(zip_path):
                raise OSError("the copy is incomplete")
            self._mirror_status(True, target)
            return {"copied": str(dest)}
        except Exception as e:
            print(f"[WARN] could not copy the backup to {target}: {e}")
            self._mirror_status(False, target, str(e))
            return {"error": str(e)}

    def _mirror_status(self, ok, target, error=None):
        try:
            p = self.config_path.parent / "runtime" / "mirror_status.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
            data.update({"target": target, "last": time.time(), "ok": bool(ok)})
            if ok:
                data["last_ok"] = time.time()
                data.pop("error", None)
            else:
                data["error"] = error
            atomic_write_json(p, data)
        except Exception:
            pass

    def mirror_status(self) -> dict:
        try:
            return json.loads((self.config_path.parent / "runtime" / "mirror_status.json").read_text(encoding="utf-8"))
        except Exception:
            return {}

    def test_mirror(self, target=None) -> dict:
        """Can BaoBun write to the second backup folder right now?"""
        target = (target or self.get_setting("backup_mirror_dir", "") or "").strip()
        if not target:
            return {"ok": False, "error": "No second backup folder set"}
        try:
            Path(target).mkdir(parents=True, exist_ok=True)
            probe = Path(target) / ".baobun-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            usage = shutil.disk_usage(target)
            return {"ok": True, "free_gb": round(usage.free / 1024 ** 3, 1)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _prune_backups(folder: Path, keep: int) -> int:
        """Keep the newest ``keep`` regular backups (safety pre-restore backups are kept too)."""
        if keep <= 0:
            return 0
        regular = [f for f in folder.glob("*.zip") if not f.name.startswith("pre_restore_")]
        regular.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        removed = 0
        for old in regular[keep:]:
            try:
                old.unlink()
                removed += 1
            except OSError:
                pass
        pre = sorted(folder.glob("pre_restore_*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in pre[5:]:
            try:
                old.unlink()
            except OSError:
                pass
        return removed

    def list_server_backups(self, server_id, include_mirror: bool = False):
        """List timestamped world-backup zips for a server, newest first.
        include_mirror: also the copies in the second backup folder (another
        drive or PC) that aren't on this PC any more, marked "location": "copy"."""
        dest = self.config_path.parent / "backups" / server_id
        out = []
        if dest.exists():
            for f in sorted(dest.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
                out.append({"path": str(f), "name": f.name, "size": f.stat().st_size, "location": "here"})
        if include_mirror:
            have = {b["name"] for b in out}
            extra = [b for b in self._mirror_backups(server_id) if b["name"] not in have]
            if extra:
                out = sorted(out + extra, key=lambda b: b.get("mtime") or os.path.getmtime(b["path"]),
                             reverse=True)
        return out

    def _mirror_backups(self, server_id, timeout: float = 8.0) -> list:
        """Backup copies in the second backup folder (looked up with a time limit,
        so an offline PC can't freeze the list)."""
        target = (self.get_setting("backup_mirror_dir", "") or "").strip()
        if not target:
            return []
        found = []

        def look():
            folder = Path(target) / server_id
            try:
                for f in folder.glob("*.zip"):
                    st = f.stat()
                    found.append({"path": str(f), "name": f.name, "size": st.st_size, "mtime": st.st_mtime,
                                  "location": "copy"})
            except OSError:
                pass
        t = threading.Thread(target=look, daemon=True)
        t.start()
        t.join(timeout)
        return list(found) if not t.is_alive() else []

    def restore_server_world(self, server_id, zip_path, external_map=None, safety_backup=True):
        """Restore a server's world data from a backup zip (with a safety backup first).
        external_map: {"_external/N": folder} - where save folders that lived outside
        the server go (used when the backup came from another PC)."""
        srv = self.get_server(server_id)
        if not srv:
            raise ValueError("Server not found")
        if self.is_running(server_id):
            raise RuntimeError("Stop " + (srv.get("name") or "the server")
                               + " before restoring a backup (it would overwrite the restored files).")
        directory = srv.get("directory", "")
        if not directory or not os.path.isdir(directory):
            raise FileNotFoundError("Server directory not found: " + directory)
        zp = Path(zip_path)
        if not zp.exists():
            raise FileNotFoundError("Backup file not found: " + str(zp))
        pre = None
        if safety_backup:
            try:
                pre = self.backup_server_world(server_id, keep=0, reason="pre_restore")["zip_path"]
                pre_named = Path(pre).with_name("pre_restore_" + Path(pre).name)
                os.replace(pre, pre_named)
                pre = str(pre_named)
            except Exception as e:
                print(f"[WARN] safety backup before restore failed: {e}")
                pre = None
        restored = 0
        with zipfile.ZipFile(zp, "r") as z:
            names = z.namelist()
            external = {}
            if "baobun_backup.json" in names:
                try:
                    external = json.loads(z.read("baobun_backup.json")).get("external", {}) or {}
                except Exception:
                    external = {}
            if external_map:
                external = dict(external, **external_map)
            for n in names:
                if n.endswith("/") or n == "baobun_backup.json":
                    continue
                arc = Path(n)
                if arc.is_absolute() or ".." in arc.parts:
                    continue
                target = None
                if n.startswith("_external/"):
                    parts = n.split("/", 2)
                    base = external.get(parts[0] + "/" + parts[1]) if len(parts) >= 2 else None
                    if not base:
                        continue
                    target = Path(base) / parts[2] if len(parts) == 3 else Path(base)
                else:
                    target = Path(directory) / arc
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                restored += 1
        self._audit("backup_restore", server_id, backup=str(zp), files=restored)
        return {"restored": restored, "pre_backup": pre}

    def export_server_profile(self, server_id, dest_path):
        """Export a server's definition (minus its runtime id) to a JSON profile."""
        servers = self.load_servers()
        srv = next((s for s in servers if s["id"] == server_id), None)
        if not srv:
            raise ValueError("Server not found")
        export = {k: v for k, v in srv.items() if k != "id"}
        export["_baoBunProfile"] = 1
        Path(dest_path).write_text(json.dumps(export, indent=2), encoding="utf-8")
        return dest_path

    def import_server_profile(self, src_path):
        """Import a server profile JSON (adds a new server entry)."""
        data = json.loads(Path(src_path).read_text(encoding="utf-8"))
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            raise ValueError("Not a valid server profile")
        data.pop("_baoBunProfile", None)
        name = data.get("name")
        if not name:
            raise ValueError("Profile has no name")
        servers = self.load_servers()
        if any(s.get("name") == name for s in servers):
            raise ValueError("A server named '" + name + "' already exists.")
        data["id"] = "srv_" + str(int(time.time() * 1000))
        servers.append(data)
        self.save_servers(servers)
        return data
