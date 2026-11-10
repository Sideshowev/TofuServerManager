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
    try:
        from core.game_defaults import required
        if app and required({"appId": app, "name": "x", "launchArgs": ""})[1]:
            return "full"
    except Exception:
        pass
    return "basic"


def levels(presets: Dict[str, dict]) -> Dict[str, str]:
    return {n: level(dict(p, name=n)) for n, p in presets.items() if n != "Custom / Other"}
