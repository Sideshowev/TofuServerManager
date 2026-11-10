"""Plain-language crash help.

``explain(exit_code, log_lines, srv)`` looks at how a server exited and at the
end of its console output, and returns a short list of findings:

    [{"key": "port_taken", "title": "Port 42420 is already in use",
      "fix": "Another program ...", "link": None}]

The most specific finding comes first. Nothing here touches the server; it
only reads what already happened.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
DOTNET_URL = "https://dotnet.microsoft.com/download/dotnet"

# Windows NTSTATUS exit codes worth explaining (unsigned 32-bit values).
NTSTATUS = {
    0xC0000135: ("missing_dll", "A Windows component the server needs is missing",
                 "Usually the Microsoft Visual C++ Redistributable (x64). Install it, then start the server again.",
                 VCREDIST_URL),
    0xC0000142: ("missing_dll", "A Windows component failed to load",
                 "Install or repair the Microsoft Visual C++ Redistributable (x64), then reboot if it keeps happening.",
                 VCREDIST_URL),
    0xC000007B: ("bad_image", "A program file is the wrong type or damaged",
                 "Often a 32-bit/64-bit mix-up or a broken Visual C++ install. Repair the Visual C++ "
                 "Redistributable and use Install / repair to re-check the game files.", VCREDIST_URL),
    0xC0000005: ("access_violation", "The server crashed (access violation)",
                 "This is a crash inside the game itself. Common causes: a broken or outdated mod, "
                 "damaged game files (use Install / repair) or a damaged save (restore a backup).", None),
    0xC0000409: ("game_crash", "The server crashed (stack buffer overrun)",
                 "A crash inside the game. Check recently added mods and re-check the game files.", None),
    0xC0000374: ("game_crash", "The server crashed (heap corruption)",
                 "A crash inside the game. Check recently added mods and re-check the game files.", None),
    0xC0000017: ("out_of_memory", "Windows ran out of memory for the server",
                 "Close other programs, give the PC a bigger page file, or run fewer servers at once.", None),
    0xC000012D: ("out_of_memory", "Windows ran out of memory for the server",
                 "Close other programs, give the PC a bigger page file, or run fewer servers at once.", None),
    0xE0434352: ("dotnet_crash", "The server hit an unhandled .NET error",
                 "Look at the last lines of the console for the error. If it mentions a mod, remove or update that mod.",
                 None),
    0xC000013A: ("closed", "The server window was closed (Ctrl+C or the console was closed)",
                 "Nothing is broken. Start it again from BaoBun instead of closing its window.", None),
}

# (key, regex, title, fix, link) - checked against the last lines of output.
PATTERNS = [
    ("needs_signin",
     r"(authentication failed on dedicated server|e_invalid_token|no auth token could be found)",
     "The game's server has to be signed in first",
     "This game only runs a server that is linked to an account with the game's maker. Follow the "
     "game's own server guide to get a server token (or to sign the server in), then start it again.",
     None),
    ("port_taken",
     r"(address already in use|only one usage of each socket address|failed to bind|"
     r"could not bind|bind\(\) failed|port \d+ (is )?(already )?in use|unable to (start|open) .*port|"
     r"WSAEADDRINUSE|socket.*already in use)",
     "The server's port is already in use",
     "Another program (or another copy of this server) is using the port. Stop the other server or "
     "change this server's port in Settings.", None),
    ("dotnet_missing",
     r"(you must install or update \.net|microsoft\.netcore\.app|microsoft\.aspnetcore\.app|"
     r"the framework '.*' was not found|hostfxr|a fatal error occurred.*\.net|"
     r"to run this application, you must install \.net)",
     "The right version of .NET isn't installed",
     "Install the .NET Desktop Runtime version named in the console output (x64), then start again.",
     DOTNET_URL),
    ("vcredist_missing",
     r"(vcruntime1\d\d(_1)?\.dll|msvcp1\d\d\.dll|msvcr\d+\.dll|ucrtbase\.dll|api-ms-win-crt)",
     "The Microsoft Visual C++ Redistributable is missing",
     "Install the latest Visual C++ Redistributable (x64) from Microsoft, then start again.", VCREDIST_URL),
    ("game_library",
     r"(failed to load|could not load|unable to load)( (library|module|dll))?\s*[\"']?[\w.\- \\/:]*\.dll",
     "One of the game's own files couldn't be loaded",
     "Use Install / repair to re-check the game files. If it keeps happening, this server may need to be "
     "started from a different folder or program - check the game's server guide.", None),
    ("out_of_memory",
     r"(outofmemoryexception|out of memory|std::bad_alloc|insufficient memory|"
     r"not enough memory|cannot allocate memory|malloc.*failed|java\.lang\.outofmemoryerror)",
     "The server ran out of memory",
     "Close other programs, lower view distance / world size, or give the server more RAM "
     "(for Java servers raise -Xmx in the launch arguments).", None),
    ("eula",
     r"(you need to agree to the eula|eula\.txt)",
     "The game's EULA hasn't been accepted",
     "Open eula.txt in the server folder (Config files tab), change eula=false to eula=true and save.", None),
    ("access_denied",
     r"(access (to the path .* )?is denied|unauthorizedaccessexception|permission denied|"
     r"cannot open file .* for writing|read-only file system)",
     "The server isn't allowed to write to its folder",
     "Move the server out of Program Files, make sure the folder isn't read-only, or check that "
     "antivirus isn't blocking it.", None),
    ("disk_full",
     r"(not enough space on the disk|no space left on device|disk full|ioexception: there is not enough space)",
     "The disk is full",
     "Free up space on the drive (old backups are in BaoBun's backups folder) and start again.", None),
    ("needs_steam_client",
     r"(steam client is not running)",
     "This game needs Steam running on the PC",
     "This game's server only works while the Steam app is open and signed in on this PC. Start Steam, then start the server again.",
     None),
    ("steam_init",
     r"(steamapi_init\(\) failed|steam_appid\.txt|steamgameserver_init failed|failed to initialize steam)",
     "Steam couldn't start inside the server",
     "Use Install / repair to re-download the server files. Some games need steam_appid.txt in the "
     "server folder.", None),
    ("corrupt_save",
     r"(database disk image is malformed|savegame.*corrupt|corrupt(ed)? (save|world|map)|"
     r"failed to load (the )?(save|world|savegame)|world file .* (damaged|corrupt))",
     "The world save looks damaged",
     "Restore the latest good backup from the Backups tab (the current world is kept as a safety backup).",
     None),
    ("source_game_folder",
     r"unable to find gameinfo\.txt",
     "The server wasn't told which game folder to use",
     "This is a Source engine game. In Edit Server, add -game followed by the name of the folder that has gameinfo.txt in it to the launch options (for example -game cstrike), then start it again.",
     None),
    ("mod_error",
     r"(failed (loading|to load) mod|exception (while|when) loading mod|mod .* (failed|threw|crashed)|"
     r"missing dependency|requires mod|incompatible mod|modloader.*error|\[mod\].*error)",
     "A mod failed to load",
     "Check the console for the mod's name, then update or remove it (Mods tab or the server's Mods folder).",
     None),
    ("already_running",
     r"(another instance|already running|lock file|world is locked|session\.lock)",
     "Another copy of the server seems to be running",
     "Stop the other copy (check Task Manager) or delete the stale lock file, then start again.", None),
    ("missing_install",
     r"(is not recognized as an internal or external command|the system cannot find the (file|path) specified|"
     r"no such file or directory|could not find .*\.exe)",
     "A file the server needs wasn't found",
     "Use Install / repair to download the server files, or fix the program path in Edit Server.", None),
]
_COMPILED = [(k, re.compile(rx, re.IGNORECASE), t, f, l) for k, rx, t, f, l in PATTERNS]

_PORT_RX = re.compile(r"port\D{0,12}(\d{2,5})|(?:\d{1,3}\.){3}\d{1,3}:(\d{2,5})\b|\]:(\d{2,5})\b",
                      re.IGNORECASE)


def _unsigned(code) -> Optional[int]:
    try:
        c = int(code)
    except (TypeError, ValueError):
        return None
    return c & 0xFFFFFFFF if c < 0 else c


def explain(exit_code, log_lines: Iterable[str], srv: Optional[dict] = None,
            health_problems: Optional[List[str]] = None, uptime: Optional[float] = None) -> List[Dict]:
    """Findings for a server that stopped by itself, most specific first."""
    srv = srv or {}
    lines = [l for l in (log_lines or []) if l and l.strip()][-200:]
    text = "\n".join(lines)
    found: List[Dict] = []
    seen = set()

    def add(key, title, fix, link=None, line=None):
        if key in seen:
            return
        seen.add(key)
        item = {"key": key, "title": title, "fix": fix, "link": link}
        if line:
            item["line"] = line.strip()[:240]
        found.append(item)

    for key, rx, title, fix, link in _COMPILED:
        hit = next((l for l in reversed(lines) if rx.search(l)), None)
        if hit is None:
            continue
        if key == "port_taken":
            m = _PORT_RX.search(hit)
            port = next((g for g in (m.groups() if m else ()) if g), None) or srv.get("port") or ""
            if port:
                title = "Port %s is already in use" % port
        add(key, title, fix, link, hit)

    code = _unsigned(exit_code)
    if code in NTSTATUS:
        key, title, fix, link = NTSTATUS[code]
        add(key, title, fix, link)

    for p in health_problems or []:
        low = p.lower()
        if "not found" in low or "does not exist" in low or "not reachable" in low:
            add("missing_install", p, "Use Install / repair, or fix the folder/program in Edit Server.")
        elif "port" in low:
            add("port_taken", p, "Stop the other program or change this server's port in Settings.")
        elif "free" in low and "gb" in low:
            add("disk_full", "Low disk space: " + p, "Free up space on the drive.")
        else:
            add("health", p, "")

    if not found:
        if uptime is not None and uptime < 15:
            add("quick_exit", "The server closed right after starting",
                "Read the last lines of the console below - the reason is usually printed there. "
                "Check the launch arguments and that the game files are complete (Install / repair).")
        elif code not in (None, 0):
            add("unknown", "The server stopped with exit code %s" % _fmt_code(exit_code),
                "Read the last lines of the console - the reason is usually printed there.")
    return found[:4]


def _fmt_code(code) -> str:
    c = _unsigned(code)
    if c is None:
        return str(code)
    return "0x%08X" % c if c > 0xFFFF else str(c)


def summary(findings: List[Dict]) -> str:
    """One line for cards, Discord and notifications."""
    return findings[0]["title"] if findings else ""
