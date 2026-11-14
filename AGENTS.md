# Instructions for AI helpers working on BaoBun

BaoBun Control Panel is a Windows game-server manager (Python, customtkinter
desktop app + a background "Watchdog" service + web panel + Discord bot). Read
this whole file before changing anything.

## Rules (never break these)
1. **Universal.** BaoBun is shared with other people. Nothing may assume a
   particular PC, drive letter, user name, folder or hardware. Use settings,
   detection or the user's own folders - never hard-coded paths.
2. **Secrets.** Never print, log, copy or commit passwords, tokens or keys.
   Never edit or overwrite `discord_config.json`, `settings.json` or
   `servers.json` - those are the owner's live data.
3. **Running servers.** Never start, stop or change real game servers while
   working. Tests use fake servers in temporary folders.
4. **Tests must pass.** Run `run_tests.bat` (or `python -m pytest -q tests`)
   before saying anything is done. Add a test for every behaviour change.
5. **Plain words.** Messages the app shows say what happened and what to do,
   in everyday language (no jargon, no blame).

## Where things are
| Area | Files |
|---|---|
| Server control (start/stop, installs, backups, configs) | `server_manager.py` |
| One API for desktop/web/Discord | `core/api_layer.py` (permissions: `core/web_auth.py`) |
| Watchdog (background service) | `baobun_agent.py` |
| Desktop windows | `app.py`, `ui/*.py` |
| Web panel | `core/web_panel.py`, `ui/web/index.html` |
| Discord bot | `discord_bot.py`, `core/discord_*.py` |
| Game list | `core/game_catalog.py`, `presets.json` |
| Start options a game needs | `core/game_defaults.py` |
| Settings files per game | `core/config_forms.py` (`KNOWN_FILES`), `core/quick_settings.py` (Essentials) |
| Automatic settings for other games | `core/auto_settings.py` |
| Support level shown in the game picker | `core/game_support.py` |
| Players (allow list, bans, admins) | `core/player_access.py` |
| Tests | `tests/test_engine.py`, `tests/test_desktop_smoke.py` (Windows only) |

## Jobs that suit you
- **Run tests / builds** (`run_tests.bat`, `build_exe.bat`, `build_installer.bat`)
  and report only what failed, with the failing test names and the key lines.
- **Summarise `logs\game_smoke_report.md`** after `run_game_smoke_test.bat`:
  for each game that didn't end in `ok`, write game / result / the 5-10 log
  lines that explain it. Put it in `game_profiles/SMOKE_SUMMARY.md`.
- **Research a game** and fill a profile - see `game_profiles/README.md`.
- **Triage a bug report** (log, screenshot, description): what the user did,
  the exact error lines, which file/feature it points to. Don't fix big things.
- **Small edits that follow an existing pattern** (one catalog entry, one
  message text). Show the diff and run the tests.

## Leave these to the main developer (write a note instead)
- Changes across several files, anything security-related (secrets, web panel
  sign-in, linked PCs, Discord permissions), new features, design decisions.

## Handing work back
Write `HANDOFF.md` in the project folder:
```
## What I did
## What I found (with file:line or log lines)
## Tests: passed / failed (names)
## Open questions
```
Keep it short - the point is to save the main developer reading time.

## Extra research source: CreeperHost wiki

https://www.creeperhost.net/help/docs/ has a section per game (about 45 games). Use it to learn WHICH settings
and commands matter for a game (what a host puts on its settings page, how admins are added, how the world is
changed). Its pages mostly describe CreeperHost's own panel, so they rarely name the real file, key or start
option - confirm those from the game's official docs, WindowsGSM's notes or the files the game writes
(logs/game_smoke_report.md lists them per game). Never copy its text.
