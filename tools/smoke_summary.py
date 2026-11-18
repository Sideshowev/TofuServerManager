"""Turn logs/game_smoke_report.json into game_profiles/SMOKE_SUMMARY.md.

The tables come straight from the report; the reviewed causes of the failures are in
REVIEWED below (kept by hand: add a line when a failure has been looked at).

  python tools/smoke_summary.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "logs" / "game_smoke_report.json"
OUT = ROOT / "game_profiles" / "SMOKE_SUMMARY.md"

# game -> (what's wrong, what would fix it). Reviewed by the main developer.
REVIEWED = {
    "Don't Starve Together": ("Starts now (it has to be started from its bin64 folder), but lets nobody in "
                              "without a Klei server token.",
                              "Tofu refuses to start it without the token and says where to get it. Next: a "
                              "place in Tofu to paste the token."),
    "Risk of Rain 2": ("Steam finishes without giving any game files (an empty depot).",
                       "Unknown whether a Steam account that owns the game gets the files. Until that's "
                       "tested it shouldn't be marked fully supported."),
    "Brickadia": ("Log: \"Authentication failed on dedicated server. Exiting now.\"",
                  "Needs a Brickadia account sign-in / token, like Don't Starve Together."),
    "BlazeRush": ("Log: \"Failed to initialize Steam. Probably Steam client is not running.\"",
                  "Needs the Steam client running on the PC - not a real dedicated server. Say so in its "
                  "description or drop it from the list."),
    "ASKA": ("Log: Failed to load \"SDL3.dll\" - it can't load one of its own libraries.",
             "Not looked into yet. Likely needs to be started from another folder, or its real program is "
             "a different file."),
    "Dystopia": ("Source mod: \"Unable to find gameinfo.txt\".",
                 "Needs -game <its folder>; Tofu's automatic -game only looks one folder deep. Not fixed."),
    "Synergy": ("Source mod: file system fails to start.",
                "Probably needs Half-Life 2 content that the server download doesn't include. Not confirmed."),
    "Military Conflict: Vietnam": ("Exits with code 1 and writes nothing at all.", "Unknown."),
    "Out of Reach": ("Exits with code 1 and writes nothing at all.", "Unknown."),
    "Sunkenland": ("Unity memory report at exit, no clear error.", "Unknown."),
    "FiveM (GTA V roleplay)": ("Not on Steam.", "Expected - the list already says to copy the files in."),
    "Path of Titans": ("Not on Steam.", "Expected - the list already says to copy the files in."),
    "Serious Sam HD: The Second Encounter": ("Needs a Steam account that owns the game.",
                                             "Expected - mark it \"needs a Steam account\" in the list."),
    "Takedown: Red Sabre": ("Needs a Steam account that owns the game.",
                            "Expected - mark it \"needs a Steam account\" in the list."),
    "Eden Star": ("Steam couldn't write the game files (code 8).",
                  "Earlier run said \"No subscription\": it needs a Steam account that owns the game."),
}
EXE_NOT_FOUND = ("Installed, but Tofu couldn't find the server program.",
                 "Look at what the install contains and set the program in the game list. Not done yet.")
SHORT = {"works": "works", "kept (not checkable)": "kept", "game ignores it": "IGNORED",
         "game rewrote it": "REWRITTEN", "save failed": "can't save", "setting not found afterwards": "lost"}


def cell(c, k):
    r = ((c.get("fields") or {}).get(k) or {}).get("result")
    return SHORT.get(r, r) if r else "-"


def main():
    res = json.loads(REPORT.read_text(encoding="utf-8"))
    ok = [r for r in res if r.get("result") == "ok"]
    by_port = [r for r in ok if r.get("ready_by") in ("port", "player query", "port/time")]
    timed = [r for r in ok if str(r.get("ready_by") or "").startswith("time only")]
    other_ports = [r for r in timed if r.get("listening_ports")]
    silent = [r for r in timed if not r.get("listening_ports")]
    bad = [r for r in res if r.get("result") != "ok"]
    L = ["# Game smoke test - what it found", "",
         "Made by `tools/smoke_summary.py` from `logs/game_smoke_report.json` on %s. The full report "
         "(with log ends) is `logs/game_smoke_report.md`." % time.strftime("%Y-%m-%d %H:%M"), "",
         "## In short", "",
         "- **%d games tested, %d installed, started and stayed up.**" % (len(res), len(ok)),
         "- %d of those opened a port Tofu expects (or answered a player query) - confirmed up." % len(by_port),
         "- %d were up on ports Tofu doesn't know about - Tofu's port list for them is wrong or missing."
         % len(other_ports),
         "- %d kept running but Tofu saw no open port after two minutes - **not confirmed**: still loading, "
         "or quietly not working." % len(silent),
         "- %d did not work (below)." % len(bad), ""]

    L += ["## Settings a new-server form could ask for", "",
          "For each game the test set the server name, join password and max players through Essentials, "
          "restarted the game and looked again.", "",
          "- **works** - the game kept it and players see it (checked with the Steam server query)",
          "- **kept** - the game kept it in its settings; this game doesn't answer the query, so it couldn't "
          "be checked from outside",
          "- **IGNORED** - saved, but the game showed something else: Tofu is writing to the wrong place",
          "- **REWRITTEN** - the game threw the value away when it started",
          "- **can't save** - Tofu couldn't write it (reasons below the table)",
          "- `-` - Tofu doesn't know where this game keeps it", "",
          "| Game | Essentials | Server name | Password | Max players |", "|---|---|---|---|---|"]
    ess = sorted((r for r in res if (r.get("essentials_check") or {}).get("fields")), key=lambda r: r["game"])
    for r in ess:
        c = r["essentials_check"]
        L.append("| %s | %s | %s | %s | %s |" % (r["game"], "found automatically" if c.get("auto") else
                                                "made for this game", cell(c, "name"), cell(c, "password"),
                                                cell(c, "players")))
    errs = []
    for r in ess:
        for k, f in (r["essentials_check"].get("fields") or {}).items():
            if f.get("error"):
                errs.append("- %s, %s: %s" % (r["game"], f.get("label") or k, f["error"]))
    if errs:
        L += ["", "Why some couldn't be saved:", ""] + errs
        L += ["", "(The ARK \"server name\" lines are the test's fault: its test name has a space in it. "
              "The ARK password / max players lines are real: a new ARK server has no map in its launch "
              "options yet. Arma Reforger and Conan Exiles are real too: the settings file Tofu writes to "
              "doesn't exist even after the first start.)"]
    none = sorted(r["game"] for r in ok if not (r.get("essentials_check") or {}).get("fields"))
    L += ["", "**No server name, password or max players known at all (%d games that run):** %s" % (
        len(none), ", ".join(none)), ""]

    L += ["## Up, but on ports Tofu doesn't expect", "",
          "| Game | Open ports | Tofu expected |", "|---|---|---|"]
    for r in sorted(other_ports, key=lambda r: r["game"]):
        L.append("| %s | %s | %s |" % (r["game"], ", ".join(r.get("listening_ports") or [])[:90],
                                       ", ".join(map(str, r.get("expected_ports") or [])) or "nothing"))
    L += ["", "## Running, but no open port seen (not confirmed)", "",
          ", ".join(sorted(r["game"] for r in silent)), "",
          "## Did not work", "", "| Game | Support | Result | What's wrong | What would fix it |",
          "|---|---|---|---|---|"]
    for r in sorted(bad, key=lambda r: (r.get("result") or "", r["game"])):
        why, fix = REVIEWED.get(r["game"]) or (
            EXE_NOT_FOUND if "Could not find game executable" in str(r.get("error")) else ("Not looked at yet.", ""))
        L.append("| %s | %s | %s | %s | %s |" % (r["game"], r.get("support", ""), r.get("result"), why, fix))
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("wrote", OUT, "-", len(res), "games,", len(ok), "ok,", len(by_port), "confirmed,",
          len(other_ports), "other ports,", len(silent), "silent,", len(bad), "failed")


if __name__ == "__main__":
    main()
