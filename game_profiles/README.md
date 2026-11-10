# Game profiles (research inbox)

One JSON file per game, named after the Steam App ID (e.g. `892970.json`) or the
game (`minecraft-bedrock.json`). Fill `TEMPLATE.json`; every value must come
from a source you list in `sources` - write `null` when unsure instead of
guessing. These files are **not** read by BaoBun directly: the main developer
checks them and adds them to `core/game_defaults.py`, `core/config_forms.py`
and `core/game_catalog.py`.

Good sources: WindowsGSM plugins (github.com/WindowsGSM and `WindowsGSM.<Game>`
repos - read facts only, don't copy code), LinuxGSM `lgsm/config-default/config-lgsm/<game>server/_default.cfg`
(Linux paths: `LinuxServer` -> `WindowsServer`, `~/.config/...` -> `%APPDATA%`
or `%USERPROFILE%\AppData\LocalLow\...`), the game's official dedicated-server
guide or wiki, Steam community guides.

Fields:
- `executable` - path of the Windows server program inside the install folder.
- `required_args` - options without which it won't start or won't run headless
  (use `{port}`, `{name}`, `{password}`, `{dir}` as placeholders).
- `optional_args` - useful but not required.
- `ports` - game / query / rcon / other, with protocol.
- `settings_files` - path inside the install (or `%APPDATA%\...` style), format
  (`ini`, `json`, `properties` for key=value, `xml`), and whether the game
  creates it on first start.
- `essentials` - where server name, password, admin password, max players,
  PvP and public listing live (file + key, or launch option).
- `ready_log_line` - text in the console/log that means players can join.
- `saves` - where the world/save data lives.
- `needs_steam_account` - true if SteamCMD needs a login that owns the game.
- `gotchas` - anything surprising (first start takes 10 minutes, needs a
  password of 5+ characters, opens a window without `-console`...).
