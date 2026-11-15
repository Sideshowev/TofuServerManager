"""How well BaoBun knows each game - shown in the game picker so nobody expects
more than BaoBun can do.

"full":  BaoBun knows the game's real settings files and/or has its Essentials
         view, how it must be launched, and (where it has them) players and
         console commands - it has been set up for this game on purpose.
"basic": BaoBun installs it and runs it, finds settings files by searching the
         server folder, and watches it like any other server - but nothing in
         BaoBun was written for this game specifically, so the Settings tab is a
         best guess and some games may need launch options added by hand.

A game moves to "full" by adding it to core/quick_settings.py (Essentials),
core/config_forms.py KNOWN_FILES (settings files) or core/game_defaults.py
(launch options it can't start without), ideally after a run of
tools/game_smoke_test.py shows it installs and starts.
"""

from __future__ import annotations

from typing import Dict

SPECIAL = {"vintage-story", "minecraft", "hytale"}        # own installers and settings
ARK = {"2430930", "376030"}
LABEL = {"full": "Fully supported", "basic": "Basic support"}
HELP = {"full": "BaoBun knows this game's settings and how to run it.",
        "basic": "BaoBun installs and runs it, but its settings are found by searching the server folder - "
                 "some may need setting by hand."}

# Games the smoke test (tools/game_smoke_test.py, Nov 2026) could not get working, and why -
# shown in the game picker so nobody finds out the hard way. Remove a game when a later run passes.
_NO_PROGRAM = ("BaoBun's test installed it but couldn't find its server program - you may have to pick the "
               "program yourself in Edit Server.")
_LINUX_ONLY = "Steam gave BaoBun only the Linux version of this server, which can't run on Windows."
_CRASHED = "It stopped right after starting in BaoBun's test, and it isn't known why yet - it may not work."
KNOWN_ISSUES = {
    "ASKA": _CRASHED, "Military Conflict: Vietnam": _CRASHED, "Out of Reach": _CRASHED, "Sunkenland": _CRASHED,
    "Synergy": "It stopped right after starting in BaoBun's test - it seems to need Half-Life 2 files the "
               "server download doesn't include.",
    "BlazeRush": "It needs the Steam app running on this PC - it isn't a true stand-alone server.",
    "Brickadia": "It needs a hosting token from your Brickadia account before it will start.",
    "Don't Starve Together": "It needs a server token from your Klei account - BaoBun tells you where to get it.",
    "Risk of Rain 2": "Steam gave no game files in BaoBun's test - it may need a Steam account that owns the game.",
    "D.I.P.R.I.P.": _NO_PROGRAM,
    "Pavlov": "Steam has no Windows version of this server - it only runs on Linux.",
    "The Front": _LINUX_ONLY, "Astro Colony": _LINUX_ONLY,
    "Nuclear Dawn": "Steam gave no game files in BaoBun's test - it may need a Steam account that owns the game.",
}


def issue(name: str):
    """A known problem with this game, in plain words, or None."""
    return KNOWN_ISSUES.get(str(name or ""))


def level(preset: dict) -> str:
    from core import quick_settings, config_forms
    app = str(preset.get("appId") or "")
    app = quick_settings.ALIASES.get(app, app)
    t = (preset.get("type") or "").lower()
    name = str(preset.get("name") or "")
    if t == "vintage-story" or app in ARK:
        return "full"
    if not app and (name == "Minecraft (Java)" or name.startswith("Hytale") or t in SPECIAL):
        return "full"
    if app and (app in quick_settings.GAMES or app in config_forms.KNOWN_FILES):
        return "full"
    exe = str(preset.get("executable") or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if exe in ("srcds.exe", "srcds_win64.exe", "cs2.exe"):     # Source engine: handled as a family
        return "full"
    try:
        from core.game_defaults import required
        if app and required({"appId": app, "name": "x", "launchArgs": ""})[1]:
            return "full"
    except Exception:
        pass
    return "basic"


def levels(presets: Dict[str, dict]) -> Dict[str, str]:
    return {n: level(dict(p, name=n)) for n, p in presets.items() if n != "Custom / Other"}
