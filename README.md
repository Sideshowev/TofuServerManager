# BaoBun Control Panel

A native Windows desktop app for managing all your game servers from one place —
Vintage Story (standalone), SteamCMD games, and custom executables.

## Run it

- **Prebuilt executable:** double-click `dist\BaoBunCP.exe`
- **From source (dev):** run `start.bat`

The executable and its config files (`servers.json`, `discord_config.json`) live
together in `dist\`. Keep those `.json` files next to the exe so settings persist.

## How it runs your servers

BaoBun has one engine (`ServerManager`) and one owner of server processes: the
**Watchdog** - BaoBun's background service (`baobun_agent.py`, or `BaoBunCP.exe --agent`). The
**Watchdog** pill at the top of the window is green while it runs; click it to turn it on or off.

- Opening the app starts the Watchdog automatically if it isn't running. The window
  is a remote control: closing it never stops your servers.
- The Watchdog restarts crashed servers, runs schedules, tracks player activity for
  smart boot, runs the Discord bot, and listens on a localhost-only API
  (`runtime/agent.json` holds its port and a random token).
- Every running server has a small record in `runtime/` (PID, state, owner), so the
  app, the Watchdog and the Discord bot always agree on what is running and nothing is
  ever started twice. If the Watchdog is restarted it re-attaches to servers that are
  still running.
- If the Watchdog can't start (or you turn it off in Settings), the window runs servers
  itself, the old way.

**Stopping is graceful.** BaoBun asks the game to save and quit, waits, and only then
force-stops the whole process tree:

| Game | How it stops | Grace period |
|---|---|---|
| Vintage Story / Stratum | `/stop` on the console | 120 s |
| ARK: Survival Ascended | RCON `SaveWorld`, then `DoExit` | 120 s |
| Source games, Rust | `quit` on the console | 60 s |
| Anything else | set **Stop command** in Edit Server | 60 s (`stopTimeout` to change) |

Game-specific knowledge (stop commands, broadcasts, player counts, save folders,
log files, player-join patterns) lives in `plugins/<game>/main.py`. To support a
new game well, add a plugin there; see `core/plugin_bridge.py`.

**Player counts.** The Watchdog asks each running server how many players are online
every 30 seconds: Steam's server query (Source games, Rust, Valheim, Enshrouded,
7 Days to Die, ...) or RCON `ListPlayers` for ARK. They show in the server list, the
status line and on the Discord buttons. Vintage Story has no query protocol, so its
count is not shown (its player activity still comes from the join lines in its log).
If BaoBun can't find a game's query port, set `"queryPort": 12345` on the server.

**One data folder.** `dist\data_dir.txt` points the exe at the main BaoBun folder,
so `start.bat` and `dist\BaoBunCP.exe` use the same servers.json, settings and
backups. (Delete `data_dir.txt` to give the exe its own folder again.)

**Secrets are encrypted.** The Steam password (settings.json) and the Discord bot
token (discord_config.json) are encrypted with Windows DPAPI on first start: only
your Windows account on this PC can read them. To change the Discord token, paste
the new one into `"token"`; BaoBun encrypts it on the next start. SteamCMD gets the
password through a temporary script file (deleted immediately), not the command line.

**Updates.** Before downloading, BaoBun compares the installed build
(`steamapps/appmanifest_*.acf`) with Steam's latest, so "Update before start",
Update All and scheduled updates only download when there is something new.
Servers that share an install folder (all maps of an ARK cluster) are updated
together: all of them are warned, stopped, updated once, and restarted.
Set `"steamBranch": "beta-name"` on a server to follow a Steam beta branch.

**Building the exe.** Double-click `build_exe.bat` (close BaoBun first).

**Sharing BaoBun with other people.** Give them `dist\BaoBunCP.exe` - that's all
they need (no Python). It keeps its data (server list, settings, backups, logs) in
its own folder, or in `%LOCALAPPDATA%\BaoBun` if that folder can't be written to
(e.g. Program Files). Nothing in the exe is tied to your PCs: on first start they
get the welcome screen and an empty server list. If you share the source folder
instead, leave out your own `servers.json`, `settings.json`, `discord_config.json`,
`deploy_target.txt` and the `logs`, `backups`, `runtime` and `settings` folders.
Optional extras that open a network port - the web panel, the public status page
and the PC-down alerts page - are all off until someone turns them on.

**Tests.** Double-click `selftest.bat` (or run `python -m unittest discover -s tests -v`).
It starts fake servers in temporary folders to check start/stop/console, crash
restarts, the Watchdog link, backups, RCON and Steam queries. Your servers aren't touched.
`run_tests.bat` runs the same tests plus a **desktop smoke test** that really opens
BaoBun's windows (Nightly restart, PC-down alerts, Status page, Checkup, Ports, ARK World,
Install, Mods and the mod browser) for a moment and closes them - it catches window
mistakes the engine tests can't see. Results go to `logs\tests.log`. (If the project
is ever put on GitHub, a small workflow file can run all of this on a Windows machine
on every push.)

**Code layout.** `app.py` holds the main window; its bigger windows live in
`ui/app_server_form.py` (Add / Edit Server), `ui/app_settings.py` (Settings,
schedules, help), `ui/app_ark.py` (ARK cluster setup) and `ui/app_legacy_mods.py`,
with the shared theme and folders in `ui/app_common.py`. The engine is in `core/`.

## Using it

- **Home** shows a card per server: status, players, CPU, RAM, uptime, last
  backup, and one big Start/Stop button. The strip on top shows how many servers
  and players are online, total load, and the next scheduled task.
  **Manage ›** opens the full page for a server (Live Console, config, schedules, tools).
- **+ Add Server** is a 3-step wizard: pick a game (search 130+), name it and pick a
  folder (a free port is chosen for you), then Create & install with a live
  SteamCMD progress bar. "Advanced form" is still there for custom setups.
- **Live Console** (on a server's Manage page) follows the server's output as it is
  written, colours errors and warnings, has a filter and an "errors only" switch,
  and a command box with history (Up/Down).

### Power options (Edit Server)

- **CPU cores + priority** - pin a server to specific cores (e.g. `0-15`). **Auto** picks
  the least-used block: on a 64-thread Threadripper that is one 8-core CCD with its
  SMT threads, so busy servers don't share cache or fight each other.
- **Auto-stop when empty** - stop a server after N minutes with no players (games that
  report players: Steam-query games and ARK). Anyone can start it again from Discord.
- **Open Firewall** (Manage page, Tools) - adds Windows Firewall rules for the server
  program and its ports with one administrator prompt.

## What it does

- Start / Stop / Restart servers from a dark, BaoBun-branded GUI.
- **Auto-restart on crash** with crash-loop protection (backs off, up to 5 retries).
- **Config editor** for JSON / YAML / TOML / INI / key=value / raw text (with a form
  mode for INI and key=value files, and automatic `.bak` backups on save).
- **Console** tab to send commands to a running server's stdin.
- **RCON** tab to send admin commands over TCP (ARK, Source engine games,
  Squad, Conan Exiles, and any server using Source RCON).
- **Workshop mods** — download Steam Workshop items into a server folder via
  SteamCMD.
- **Vintage Story mods** — browse the VS ModDB, install mods and check for updates.
  BaoBun reads the server's game version and installs the newest release made for
  it (warning you if none matches); the old version of an updated mod is moved to
  `Mods-old-versions` (next to the Mods folder) so the server never loads two copies.
- **SteamCMD install** — give it a Steam App ID and it downloads SteamCMD, runs
  `login anonymous` + `app_update <id> validate`, and creates a `.bat` launcher.
- **Update-before-start** option for SteamCMD servers (validates files every launch).
- **Scheduled tasks** — per-server schedules for automatic backups, SteamCMD updates
  and restarts, every N hours or at daily times. Restarts and updates warn players
  in-game first (default 10, 5 and 1 minutes; the wait ends early if everyone leaves)
  and can wait while players are online. Schedules run in the Watchdog.
- **World backups & restore** — one-click backup of config + world/save data (the
  game is asked to save first; save folders outside the install, like
  `%APPDATA%\VintagestoryData`, are included), and restore with an automatic safety
  backup. Old backups are pruned (Settings → Backups to keep, default 10). Set
  `"savePaths": ["folder", ...]` on a server to back up exactly the folders you want.
- **Server profiles** — export/import a server's full configuration as JSON, so you
  can move or share setups between machines.
- **Update All** — validate/update every SteamCMD server at once.
- **Crash alerts** — if a server stops right after starting, a popup shows its
  recent log output so you can see what went wrong.
- **Activity tracking** — the header shows whether each server was active recently
  or has been idle (feeds smart boot).
- **Start with Windows** — a Settings toggle runs the Watchdog at login
  (set-and-forget hosting).
- **Smart boot** — remembers which servers were recently used and skips auto-starting
  ones that sat idle for 2+ days.
- **Discord bot with buttons** so your community can spin servers up without an admin.
- **First-run onboarding** — a welcome dialog on an empty install points you to
  adding your first server or the help guide.
- **Theme & accent picker** — dark or light mode plus six accent colors
  (cyan, blue, violet, emerald, amber, rose), set under Settings → Appearance.

## SteamCMD games

1. Click **+ Add Server**, choose type **SteamCMD**, fill in a name and the install
   directory, and enter the Steam App ID.
2. Select it and click **Install Game**. The app locates SteamCMD, installs the game,
   and writes a launcher `.bat`.
3. Click **Start**.

### Where SteamCMD comes from

The app looks for `steamcmd.exe` in this order, using the first one it finds:

1. A path you set in **Settings -> SteamCMD Path** (gear icon in the top bar).
2. A `SteamCMD\steamcmd.exe` folder sitting next to the app / `servers.json`
   (this is the "ship a copy in the folder" option — drop it here and it's detected).
3. The per-user cache `%LOCALAPPDATA%\BaoBun\SteamCMD`.
4. A system Steam install (`C:\Program Files (x86)\Steam`).

If none of those exist, it **auto-downloads** SteamCMD from Valve's CDN into
`%LOCALAPPDATA%\BaoBun\SteamCMD` and runs it. No manual install is required.

> If an install fails with a SteamCMD network error (e.g. code 7), it usually means
> that machine can't reach Steam's servers (firewall/proxy), not that SteamCMD is
> missing. In that case, copy a working `SteamCMD` folder next to the app, or set the
> exact `steamcmd.exe` path in Settings.

The server executable is auto-detected by scanning for a name that looks like a
server binary (e.g. `*Server*.exe`, `srcds.exe`, `headless`). If it guesses wrong, fix
it in **Edit Server** (Executable).

## Vintage Story and Minecraft (one-click download)

These two don't come from Steam, so BaoBun downloads them from their official sites.
Pick the game in **+ Add Server** and click **Create & install** (desktop), or tick
**Download it now** (web panel). For a server you already have, use **Install Game** /
**Install / update game** in Settings. Progress is written to `install.log` in the
server's folder.

- **Vintage Story** - the official Windows server zip from vintagestory.at (newest
  stable, or pick a version). Running it again on an official install *updates* it:
  worlds, mods and `serverconfig.json` live in the data folder, which the zip never
  touches, and BaoBun takes a world backup first. The card shows "game update
  available" when a newer stable is out. With *Update the game automatically when
  nobody is online* on, only patch updates (1.21.4 -> 1.21.5) install by themselves;
  a new 1.x version waits for you, because mods are tied to it. A custom build such
  as `StratumServer.exe` is never overwritten. Vintage Story 1.21+ needs the .NET 8
  runtime - the install tells you if it's missing.
- **Minecraft (Java)** - choose **Vanilla**, **Paper** (plugins), **Fabric**,
  **NeoForge** or **Forge** (mods) and a version. You have to tick the Minecraft EULA
  box once. BaoBun also finds the right Java (Minecraft 1.20.5+ needs Java 21,
  1.18-1.20.4 Java 17, older Java 8) and, if this PC doesn't have it, downloads
  Eclipse Temurin into `runtime\java` next to BaoBun. You can switch type or version
  later the same way; the world stays, but back it up first - worlds can't go back to
  an older version.

Minecraft servers run as `java -Xmx<memory> -jar server.jar nogui` (Forge/NeoForge:
the `@...win_args.txt` line from their `run.bat`). Edit Server has the memory (default
4096 MB), a fixed Java path and extra Java flags; the web panel can change the memory
only. BaoBun sends `stop` for a clean shutdown, `save-all` before backups, and reads
players with the normal Minecraft server-list ping (names come from the join/leave
lines when the server hides them). Mods for Fabric/Forge/NeoForge/Paper come from
Modrinth in the **Mods** tab - the loader and version are filled in by the install.

## Discord bot

1. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications)
   and copy its token.
2. Edit `dist\discord_config.json` (or set these in **Settings**):
   ```json
   {
     "token": "PASTE_YOUR_BOT_TOKEN_HERE",
     "channel_id": 123456789012345678,
     "log_channel_id": 123456789012345679,
     "log_max_age_hours": 24,
     "admin_ids": []
   }
   ```
   - `channel_id` — where the bot posts its control panel with buttons.
   - `log_channel_id` — (optional) where Start/Stop/Restart results are posted,
     keeping the control-panel channel clean. Blank = same channel as the panel.
   - `admin_ids` — Discord user IDs allowed to STOP servers (protects servers
     from griefing).
   - `admin_role_ids` — (optional) role IDs whose members can also stop servers.
   - `start_role_ids` — (optional) role IDs allowed to START servers. Leave empty
     to let anyone start. Hide a server from Discord with `"discordVisible": false`
     on it in servers.json.
   - Optional extras: `start_cooldown_seconds` (20 - one start per person per 20 s),
     `max_running` (0 = no limit; non-admins can't start more than this many servers at
     once), `announce_players` (true), `announce_activity` (true), `notify_ready` (true),
     `alert_channel_id` (crash alerts; default the log channel).
3. Invite the bot with the **bot** and **applications.commands** scopes, and give it
   View Channel, Send Messages, Embed Links and Read Message History in its channels
   (Manage Messages too, so it can tidy old messages quickly). The Watchdog log (`logs\agent.log`) says what's
   missing.
4. Restart the app (the bot runs inside the Watchdog, so it keeps working
   with the window closed).
5. In Discord, run **`/baobun-setup`** to pick the panel, activity, alert and
   game-night channels and the admin / "may start servers" roles - no file editing.
   Until admins are set, the Discord server's owner and anyone allowed to *Manage
   Server* count as admins. (These choices are kept in BaoBun's settings, so the token
   file is never touched.)

**The panel**: a status card (online/offline, who's playing, update available) and
one button per server, 4 per row. Green = start it, gray = running (shows players),
red = crashed (start it again), hourglass = starting/updating (can't be clicked twice).
Pressing a running server tells you who's on and how to join; admins also get
**Stop** / **Restart** buttons, with a warning when people are playing. The panel is
edited in place when BaoBun restarts, rebuilt if someone deletes it, and grows by
itself when you add servers.

**Slash commands** (server names autocomplete; answers only visible to whoever asked
unless noted):

- everyone: `/servers`, `/status` (posted for everyone, with the public status page
  link if it's on), `/start`, `/players`, `/join`, `/mods` (the mod list with links,
  plus a **Download mod pack** zip of the mods players need - Vintage Story,
  Minecraft and other games whose mods are files; up to Discord's 10 MB), `/notify`
  (a DM when a server is up, or whenever someone joins it), `/stats` (who played most,
  busiest servers and times - posted), `/event`, `/events`, `/event-cancel`, `/help`
- admins: `/stop`, `/restart`, `/say`, `/backup`, `/restore` (pick one of the last 5
  backups; the current world is saved first), `/logs` (last console lines, passwords
  hidden), `/update` (game files and mods; players are warned), `/checkup`,
  `/baobun-setup`

**Game nights**: `/event title:"ARK boss fight" when:"sat 8pm" server:ARK` posts the
event with **I'm in** / **Can't make it** buttons. Times can be `8pm`, `tomorrow
7:30pm`, `sat 8pm`, `11/14 21:00` or `in 2h` (read in the server PC's time zone, shown to
everyone in their own). 30 minutes before, everyone going is pinged; 10 minutes before
the server is started (unless you said not to); at the time it says "starting now"
with how to join. Whoever made it, or an admin, can `/event-cancel` it.

**Crash alerts** come with **Restart it** and **Show log** buttons (admins), and
restart / update warnings for servers with players on are posted in the activity
feed too. The bot's status shows how many servers are up and how many people play.

**"It's ready"**: whoever starts a server gets a ping in the log channel when people
can actually join (the game answers or its port is open), or a note if it failed.

**Activity feed** (`log_channel_id`): who started or stopped what (from Discord, the
web panel or the desktop app), servers going to sleep when nobody played, servers
coming back after boot, and players joining and leaving. Old activity messages are
tidied after `log_max_age_hours`; alerts are kept.

Add `"show_join_address": true` to also show each server's internet address in the
panel and in `/join` (only do this in a private Discord).

The bot reconnects on its own after network drops, and if it ever crashes it starts
again (waiting a bit longer each time). A wrong token stops it with a clear message
in the Watchdog log (`logs\agent.log`).

## Game settings, players and connecting

- **Settings tab → Essentials** (desktop; *Game settings* on the web): the things everyone changes - server
  name, password, max players, description, PvP, public listing - as a simple form.
  BaoBun writes them where each game keeps them (Vintage Story serverconfig.json,
  Minecraft server.properties, Valheim launch options, PalWorldSettings.ini,
  7 Days to Die serverconfig.xml, Project Zomboid .ini, Rust / ARK launch options,
  V Rising ServerHostSettings.json, Arma / DayZ .cfg, Arma Reforger config.json,
  Unturned Commands.dat, Don't Starve Together cluster.ini, Conan, Garry's Mod,
  Enshrouded). Passwords are never shown; leave a password box empty to keep it.
- **Who's online**: player names show on the cards and on top of the console.
  Steam games are asked directly (A2S), ARK uses RCON, and Vintage Story is read
  from its join/leave log lines - so Vintage Story now has player counts for idle
  auto-stop, graphs and Discord too.
- **How to Connect** (desktop tools button, web *Connect* tab): the address to give
  friends (internet, home network, Tailscale) with Copy buttons, the ports to
  forward, whether the server is actually listening, a Windows Firewall check, and
  **Forward automatically (UPnP)** for routers that allow it.
- **Game update badges**: the Watchdog asks Steam every few hours which servers have a
  newer build and the card says "Game update available" (click it on the web panel
  to update). Tick *Update the game automatically when nobody is online* in Edit
  Server to let BaoBun do it by itself - it never updates a running server while
  players are on (or when it can't tell).
- **Announcements**: Manage > Announcements (or the web Schedules tab) posts a
  message in game every N hours or at set times.
- **Find existing servers** (Home): point BaoBun at a folder and it finds servers
  that are already installed (by their program and Steam manifests) and adds them.
- **Second backup folder**: Settings > *Also copy every backup to* (another drive, a
  NAS, or a shared folder on another PC such as `\\BACKUP-PC\BaoBun-backups`). Old copies
  there are pruned the same way, every copy is size-checked, **Test** checks BaoBun
  can write there, and a failed copy shows in Checkup. Also in the web panel under
  **Server PC**. If BaoBun starts at boot, set that up *with network access*
  (Settings > Nightly restart) - Windows asks for your password once in its own window
  - otherwise copies to another PC fail after a restart.
- Console logs are rotated when a server starts (over 20 MB, 3 old copies kept).

## All settings (full settings editor)

In the desktop app it's the server's **Settings** tab, which has three views:
**Essentials** (the quick fields), **All settings** (this editor) and **Raw file**
(the file as plain text, `.bak` kept on save). In the web panel it's under **Game
settings** (admins), below the quick settings. It turns the game's own config file into a form, so nobody has to
edit JSON, INI, XML, YAML or `.properties` files by hand:

- every setting in the file shows up: on/off switches, number boxes (with limits),
  dropdowns and text fields, grouped into sections you can open and close
- a search box finds any setting by name or description
- the common settings of Vintage Story, Minecraft, Palworld, ARK, 7 Days to Die,
  Project Zomboid and Enshrouded get plain-English names, help text, sensible limits
  and a **reset** to the default
- **More settings this game understands** lists options the file doesn't contain yet
  (ARK, Palworld, Minecraft, 7DTD...) - change one and it's added in the right place
- saving changes only what you changed: comments, order and formatting stay, and a
  `.bak` copy is written first. Passwords are never shown; leave them blank to keep them
- files for other games appear too (anything in the Config files list), with
  automatic labels; settings that can't be edited safely as a field (lists of
  objects, repeated keys) are shown read-only with a pointer to the raw editor

Reading config files on the web is admin-only, because they can contain passwords.

## Mods

Every server's **Manage** page has a **Mods** tab (also in the web panel). It knows
where each game gets its mods and how that game's server turns them on:

| Game | Mods come from | What BaoBun does |
|---|---|---|
| Vintage Story | VS ModDB | downloads into the data `Mods` folder, picks the release for your game version, flags missing dependencies and duplicates |
| ARK: Survival Ascended | CurseForge | keeps the `-mods=` list (one shared list for a cluster); the server downloads the mods |
| ARK: Survival Evolved | Steam Workshop | writes `ActiveMods=` + `-automanagedmods`; the server downloads them |
| Arma Reforger | Reforger Workshop | writes `game.mods` in the server's config.json; the server downloads them |
| Project Zomboid | Steam Workshop | writes `WorkshopItems=` / `Mods=` in the server .ini; fills in Mod IDs after the first download |
| Space Engineers, Unturned, Don't Starve Together, Killing Floor 2 | Steam Workshop | writes the game's own mod config; the server downloads them |
| Garry's Mod | Workshop collection | sets `+host_workshop_collection` |
| Arma 3, DayZ | Steam Workshop | downloads with SteamCMD (needs the Steam login), links each mod in as `@Name`, copies its keys, writes `-mod=` / `-serverMod=` in load order |
| Conan Exiles | Steam Workshop | downloads, copies the `.pak` files, writes `modlist.txt` and `ServerModList` |
| Squad | Steam Workshop | downloads into `SquadGame/Plugins/Mods` |
| Valheim, V Rising | Thunderstore | installs BepInEx, then mods with their dependencies; checks for updates |
| Minecraft (Java) | Modrinth | finds mods/plugins for your loader (Fabric, Forge, NeoForge, Quilt, Paper...) and Minecraft version, with dependencies; updates |
| Rust, Hurtworld | uMod | installs Oxide/Carbon plugins (no restart needed) |
| 7 Days to Die, Palworld, Satisfactory | a mods folder | install from a download link or a file, switch on/off, remove |

Games that don't use server mods (Enshrouded, Sons of the Forest...) show none. For
any other game pick a **Mod source** in the tab's options: Steam Workshop download
(set the Workshop App ID - the game's own ID, which usually differs from the
dedicated server's) or a mods folder.

- Where the order matters (ARK, Arma, Conan...) use the arrows; the list is the load order.
- Paste several IDs or links at once to add them all. For Arma 3, **Add file...** takes a
  preset exported from the Arma 3 Launcher (.html) and adds every mod in it.
- CurseForge needs a free API key (console.curseforge.com) only to show ARK mod
  names - mods work without it. Set it in the Mods tab on the server PC.
- Before installs and updates BaoBun backs up the world (unless a backup is less
  than 30 minutes old). Removed mods are never deleted: they go to
  `backups\<server>\removed-mods`. Changes made while a server runs show
  "restart to apply".

### Automatic mod updates and "Keep version"

Every few hours the server PC checks each server's mods for updates (Vintage Story,
Modrinth, uMod, Thunderstore, Workshop). **Mod updates (all servers)** at the
bottom of the Mods tab (desktop and web) picks what happens:

- **Off**
- **Tell me on Discord** (default) - one message listing the updates, repeated only
  when the list changes
- **Update automatically** - applied during the nightly restart while the servers
  are stopped (or right away on stopped servers if the nightly restart is off). The
  world is backed up and the mod folder is copied first. If the server then crashes
  within 3 hours, BaoBun puts the old mods back, marks the updated ones *Keep
  version* and says so on Discord - unpin them once the mod author fixes it.

**Keep version** on a mod pins it: Update all and automatic updates skip it, but its
own **Update** button still works. Pins follow the mod's ID, so they survive file
renames. **Include this server** turns automatic updates off for one server.
ARK: Survival Ascended maps download their CurseForge mods themselves at every
start, so those can't be pinned.

### Browsing mods (desktop and web panel)

For games with a searchable mod site - **Vintage Story ModDB**, **Modrinth**
(Minecraft) and **uMod** (Rust) - the Mods tab has **Browse mods**: a grid of mod
cards with icons, authors, download counts and a short summary, a search box,
sorting (most popular, trending, recently updated, newest, most followed) and
category chips to narrow the list. Mods you already have show **✓ Installed**.

Click a card for the mod's page: screenshots (click to enlarge), the full
description, its tags, whether it works with your server's game version and which
version BaoBun would install, whether players need it too, and links to its
source and issue tracker. **Install on this server** works the same as typing the
mod ID (dependencies, world backup and restart hints included). Descriptions are
shown as plain text and pictures only - nothing from a mod page can run in the panel.

The desktop app has the same browser: **Browse mods** on the Mods tab opens a window
with result cards (icons) on the left and the mod's page on the right - pictures,
description, versions, links and **Install**. Click a screenshot to see it bigger.
Pictures are only downloaded from the mod sites' own image servers and cached in
`runtime\img_cache` (newest 400 kept). CurseForge (ARK) search works here too once a
CurseForge API key is set.

## History graphs

The Watchdog records CPU, RAM and players once a minute while a server runs (kept 14
days; without the Watchdog the window records them). Each Home card shows the last 6
hours as a small line (players if the game reports them, otherwise CPU), and the
**History** tab shows CPU, RAM and players for the last hour, 6 hours, 24 hours or
7 days - hover a chart to read exact values. Under the graphs it also says how
fast the server's memory grows since its last restart (e.g. *memory grows about
1.2 GB per hour*), which shows a leaking game straight away.

### Memory limit per server

Edit Server > Advanced (desktop) or Settings (web): **Restart it cleanly when it
stays above this much RAM** (MB). When a running server stays over its limit for 3
minutes, players get a 5 and 1 minute warning (skipped if nobody's on), the server
restarts cleanly and Discord gets a message with how fast its memory was growing.
At most once every 30 minutes per server, and never during the nightly restart. The
older *Discord alert when RAM use passes* setting still just alerts.

## Backups you know will restore

A backup nobody has opened isn't proven. Once a week the Watchdog opens the newest
backup of every server and checks it the way a restore would: every file reads back
with the right checksum, BaoBun's manifest is there, world databases (Vintage Story's
`.vcdbs`, `.db`, `.sqlite`) pass SQLite's own check, and it isn't suddenly less than
half the size of the last good one (a sign the world folder moved). The copy on the
second backup folder gets the checksum check too. A failed check shows in Checkup
("Newest backup failed its restore check", with the last backup known to restore) and
is posted to Discord. The backup lists (desktop **Restore**, web **Backups**, Discord
`/restore`) show the result and also list the copies kept on the other PC, so you can
restore one even after this PC's own copy is gone. Setting `backup_check_days` (7;
0 = off) sets how often.

## First start

On a fresh install BaoBun opens a short walkthrough: what it does and what this PC can
handle (cores, memory), where game servers get installed (it suggests the drive with
the most free space and checks BaoBun can write there), how many backups to keep and
an optional second folder for copies, the Watchdog and starting with Windows, and
optional extras (Discord bot, web panel, nightly restart). It ends with **Add a server**
or **Bring servers from another PC**. Skip it any time; run it again from Settings.
Installs that already have servers never see it.

## Discord bot without editing files

Settings > **Discord bot**: open the developer portal, make an application, copy the
bot token and paste it in. BaoBun keeps it encrypted in `settings.json` (it is never
shown again or sent to the web panel), connects the bot straight away and gives you an
**Invite the bot** button with the right permissions. Then type `/baobun-setup` in
Discord to choose channels and who may start servers. A token already in
`discord_config.json` keeps working and wins.

## Players: allow list, bans, admins

**Players** (desktop tools column, web panel **Players** tab, Discord `/access`) shows and
changes who may join, who is banned and who is an admin, the same way for every game:

| Game | Allow list | Bans | Admins | Notes |
|---|---|---|---|---|
| Minecraft | whitelist.json + on/off | banned-players.json | ops.json | console while running; files (with the player's id) while stopped |
| Vintage Story | /player … whitelist | /ban, /unban | /player … role admin | server must be running; lists read from Playerdata |
| Valheim | permittedlist.txt | bannedlist.txt | adminlist.txt | Steam IDs; picked up while running |
| ARK | PlayersExclusiveJoinList.txt + -exclusivejoin | BanPlayer over RCON | AllowedCheaterAccountIDs.txt | EOS IDs (ASA); allow list/admins apply after a restart |
| Rust, Project Zomboid, ... | - | console ban command | - | while running |

Tick **Do it on all my … servers** (Discord: `everywhere`) to ban or allow someone on
every server of that game at once. BaoBun records every change (who, when, by whom) and
shows the recent ones. Online players can be picked with one click.

## Installing BaoBun on another PC, and updates

**For people you share BaoBun with:** run `BaoBun-Setup-<version>.exe`. It installs
for the current user without admin rights (or for everyone, into Program Files, if
chosen), adds a Start menu entry and opens the first-start walkthrough. Updating or
uninstalling never touches servers, settings or backups.

**Making the installer:** bump `VERSION` in `core/version.py`, then run
`build_installer.bat`. The first time, it installs [Inno Setup 6](https://jrsoftware.org/isdl.php)
(free, only needed on the PC that makes the installer) with winget. It builds
the exe and writes `build\installer\BaoBun-Setup-<version>.exe` plus a `.sha256`
checksum file.

**Updates:** set `UPDATE_REPO` in `core/version.py` to your GitHub repository
(`owner/name`) before building, and publish each installer and its `.sha256` on a
GitHub release (tag `v1.2.0`). Installed copies check once a day; when there's a newer
release, a blue **BaoBun 1.2.0** button appears at the top (and the alert channel is
told once). It shows what's new and **Download and install** fetches the installer from
that release (GitHub only, size and checksum checked), runs it and BaoBun opens again.
The installer can replace BaoBun while it runs: the Watchdog moves to the new version
by itself when nobody is playing, so game servers keep running. The web panel shows
the notice too. With no `UPDATE_REPO` (or the `update_repo` setting) the check is off;
Settings has **Check for updates** and shows the version.

## One BaoBun for several PCs (Linked PCs)

Servers always run on the PC they're installed on, each PC with its own BaoBun. Link the
PCs and one BaoBun - its window, web panel and Discord bot - shows and controls the
servers on all of them:

1. On each PC that hosts servers: Settings > **Linked PCs** > turn on *Let another BaoBun
   control this PC*, press **Allow through Windows Firewall**, then **Copy code** (a
   `baobun://address:8792/...` pairing code - use the Tailscale one across networks).
2. On your main PC: Settings > **Linked PCs** > paste the code under *Add a PC*.

The other PC's servers then appear on the home screen ("Valheim · on GSERVER"), in the
web panel (every tab works, including console, settings, mods, backups and players) and
in Discord (control panel buttons, `/start`, `/stop`, `/servers`...). On the desktop,
**Manage ›** on such a card opens a window with the live console, start/stop/restart,
backup, game update and players.

Safety: the other PC only answers from the home network and Tailscale. Every request is
signed with the code's key (the key itself never crosses the network, requests can't be
replayed), and the link can do what a web-panel admin can do on that PC - never change
program paths or download files. Each PC records what the link did in its own activity
log. **Make a new code** cuts off every PC linked with the old one. Links don't chain
(a linked PC's own links aren't passed on).

## How well BaoBun knows each game

The game picker marks games with **basic support**: BaoBun installs and runs them and
finds their settings by searching the server folder, but nothing in BaoBun was made for
that game yet, so some settings may need setting by hand. The others are fully supported
(known settings files and/or the Essentials view, launch options they need). See
`core/game_support.py`.

**For every game, even basic ones:** before a server's first start BaoBun notes which
settings-like files already exist; files the game writes itself afterwards are listed first
in the Settings tab ("written by the game") and shipped data files are left out. The
Essentials view of a game without a hand-made page looks for the usual settings (server
name, description, password, admin password, max players, port, PvP, public listing) in those
files under their many spellings (`ServerName`, `hostname`, `MaxPlayers`, `max-players`...)
and offers them as a simple form (`core/auto_settings.py`). Start options and settings files
for several games were checked against WindowsGSM's notes (no code taken).

**Game smoke test** (`run_game_smoke_test.bat`, best overnight on a PC with lots of free
space): in a separate test copy of BaoBun it installs each popular game that needs no
Steam account, starts it, waits for it to be ready, notes what the Settings tab would
show, stops it and deletes it again. Your real servers are never touched. The report is
`logs\game_smoke_report.md`; games already tested are skipped on the next run (`--redo`
to test again, `--games "Valheim,Terraria"` for specific ones, `--keep` to keep installs).

## Restore on another PC (Bring Servers Here)

If the PC that runs your servers dies, or you move to a new one, any PC with BaoBun
can take the servers over from the backup copies (Settings > "Also copy backups to" -
another drive, a NAS or a shared folder on another PC). Each server's copy folder
holds its backups and a `server.json` describing it.

On the new PC press **Bring Servers Here** (desktop tools column; on the web panel it's
under **Add a server**), point it at the copy folder and tick the servers. For each
one BaoBun copies the newest backup to this PC, adds the server, installs the game
(Steam, Vintage Story, Minecraft, Hytale - the same Vintage Story / Minecraft version
when it can) and puts the world back. Paths move along: the server goes into a folder
under "Install into" (default: the default install folder), save folders that were in
the old Windows user's profile go to this user's profile, and other outside save
folders go inside the server folder; launch arguments are updated to match. ARK maps
that shared one install share one here too. Settings tied to the old PC's hardware
(CPU cores, Java location) are dropped.

It is manual on purpose: brought-over servers are set to start only when you start
them. Make sure a server isn't still running on the old PC first - one world running
on two PCs splits into two different worlds. The web panel only looks in the copy
folder set on this PC and never takes over Java options. Hytale servers need signing
in again on the new PC; games BaoBun can't download need their server files copied in.

## Save diagnostics (when something goes wrong)

**Save diagnostics** (Checkup window, or Settings) writes one zip into the
`diagnostics` folder and shows it in Explorer; admins can also download one from the
web panel (**Server PC** > *Something not working?*). It holds BaoBun's logs, the end of
each server's console log and install log, the server list and settings, what
Checkup says, the server statuses and basic facts about the PC (Windows version,
CPU, RAM). Passwords, tokens, keys, webhooks and internet IP addresses are replaced
with `****` / `x.x.x.x` (home-network and Tailscale addresses are kept), and the
Discord config is reduced to its channels and roles, so it's safe to share when
asking for help.

## Checkup (what needs attention)

**Checkup** on the home screen (and the *Needs attention* box in the web panel)
looks over every server and lists what will cause trouble, worst first, with a
button where BaoBun can fix it:

- game files or the server program missing (**Install**)
- Minecraft without the right Java or with the EULA not accepted; Vintage Story
  without its .NET runtime
- ARK / Palworld / Conan / Squad / V Rising / Arma / DayZ with RCON off (no
  restart warnings, no save before backups, force-stopped instead of a clean stop)
- no backups yet, or the last one over a week old with no backup schedule
  (**Back up now**)
- two servers on the same port, under 10 GB free disk, a crashed server
- no Windows Firewall rule for a server that has run (**Open firewall**, desktop)

The web panel re-checks every 5 minutes; **Check again** runs it right away.

## Crash help and Discord alerts

When a server stops by itself BaoBun reads the end of its console and its exit
code and says what went wrong in plain words - for example *Port 42420 is already
in use*, *.NET isn't installed*, *Visual C++ Redistributable is missing*, *the
server ran out of memory*, *a mod failed to load* or *the world save looks
damaged* - with what to do about it (and a download link where one helps). It shows
on the card, in the crash pop-up, on the web panel and in the alert.

**Settings > Set up Discord alerts** sends a Discord message when a server crashes,
keeps crashing (auto-restart gave up), a game update or backup fails, the PC's RAM
passes a limit (default 90%), a server drive gets low on space, or a server passes
its own RAM limit (Edit Server > Advanced). Alerts go through the BaoBun bot if it
runs (set `alert_channel_id` in `discord_config.json` for a separate channel), or
through a Discord webhook URL (stored encrypted). The same alert is sent at most
once per cooldown, so a crash loop doesn't flood the channel. **Send test alert**
checks the setup.

## Web panel (let other admins help from any PC)

The Watchdog can serve a browser control panel so trusted people in your
Discord guild can manage servers from their own PC or phone. Nothing is exposed
to the internet: it only answers this PC and devices on your **Tailscale**
network, and everyone signs in with **Discord**.

What helpers see: the same cards as the Home screen (start/stop, players, CPU,
RAM), a live console with a command box, backups (create/restore), schedules,
per-server settings, config file editor, game updates, and Add Server from the
game list. Every action shows up in Recent activity as `web:<their name>`.

Roles (decided by your guild, re-checked at every login, sessions last 12 hours):

| Role | Can do |
|---|---|
| Admin (`web_admin_ids` / `web_admin_role_ids`) | everything above, incl. add/remove servers |
| Operator (`web_operator_role_ids`) | view, start/stop/restart, console |

Never changeable from the web, only here on this PC: a server's program and
folder, Steam login, bot token, and BaoBun's own settings. New servers from the
web always go into your default install folder.

### Setup (one time, about 10 minutes)

1. **Tailscale** - install it on the PC that runs BaoBun (tailscale.com/download)
   and sign in. In the Tailscale admin console, open *Machines*, click `...` on
   this PC, **Share**, and send the invite to each helper (free). They install
   Tailscale and accept. Turn on MagicDNS (DNS page) for a friendly name.
2. **Discord app** - in the [Developer Portal](https://discord.com/developers/applications)
   open your bot's application (or make a new one) > **OAuth2**: copy the
   **Client ID**, reset and copy the **Client Secret**.
3. **IDs** - in Discord turn on *Settings > Advanced > Developer Mode*, then
   right-click your server > Copy Server ID, and right-click roles/users to copy theirs.
4. In BaoBun click **Web Panel**: tick *Turn on*, press **Detect** for the panel
   URL, fill in Client ID/Secret, server ID and the admin/operator IDs, then
   **Copy** the Redirect URL and add it under *OAuth2 > Redirects* in the
   Developer Portal. Press **Save & apply** (running servers are not touched).
5. Helpers open the panel URL (e.g. `http://my-pc.tail1234.ts.net:8765`) and
   press **Log in with Discord**.

The client secret is stored encrypted (Windows DPAPI). Traffic stays inside
Tailscale's encrypted tunnel. If someone loses their role, they lose access at
their next login; to cut everyone off at once, untick *Turn on* and apply.

## Game presets

Click **+ Add Server** and pick a game: over 200 presets that fill in the type,
port, Steam App ID and server program. The picker opens on **Popular** and has
**Survival & crafting**, **Shooters & PvP**, **Sandbox & building** and **Everything
else**; the search box looks through all of them.

Newer survival games include Core Keeper, Abiotic Factor, Icarus, Soulmask, ASKA,
Return to Moria, VEIN, The Front, Myth of Empires, Smalland, Night of the Dead,
Frozen Flame, Necesse, Craftopia, Stationeers, Empyrion, Last Oasis; shooters and
PvP include Squad 44 (Post Scriptum), Insurgency: Sandstorm, Rising Storm 2,
Mordhau, Pavlov, Holdfast, Ground Branch, Operation: Harsh Doorstop and Bannerlord,
next to Arma 3 / Reforger, DayZ, Squad, CS2 and the Source classics. Also on
SteamCMD: SCUM, HumanitZ, The Isle (Evrima), Sunkenland, Dead Matter, Nightingale,
Windrose, StarRupture, Voyagers of Nera, TerraTech Worlds, Plains of Pain, PixARK,
No One Survived, Citadel, Aloft, Portal Knights, Colony Survival, Rising World, Wurm
Unlimited, Brickadia, Foundry, Astro Colony, Cubic Odyssey, SCP: Secret Laboratory,
Nuclear Option, Carrier Command 2, Renown, Risk of Rain 2, Assetto Corsa
Competizione and rFactor 2.

Games with their own server downloads are in the list too, with a **Download page**
button and short steps: Path of Titans, Terraria (the free official server), tModLoader, TShock, FiveM and
RedM, alt:V, RAGE:MP, open.mp, Multi Theft Auto, BeamMP, Luanti (Minetest),
Veloren, OpenTTD, Xonotic, DDNet, Among Us (Impostor), Space Station 14, Factorio
and Minecraft Bedrock.

**Hytale** installs with one button: **Install / Update** downloads Java 25 (if the
PC has none) and Hytale's own downloader, and runs it. The first time, the install
window (desktop or web) shows a link and a code - open it, sign in with your Hytale
account and type the code; BaoBun never sees your password, and the downloader
remembers the sign-in for updates. Updating keeps the server's `config.json`,
permissions, whitelist, bans, worlds (`universe`), mods and sign-in. The server
itself must then be linked to your account once: **Sign the server in** (install
window, or Checkup) starts it if needed, sends `/auth persistence Encrypted` and
`/auth login device`, and shows the link and code. The console's Quick commands have
the same two commands. Hytale uses UDP port 5520.

When a preset's server program isn't where expected (games move it between
updates), BaoBun looks for it in the install and remembers where it found it.

Presets come from two places, merged at startup:

1. A built-in list in `core/game_catalog.py` for the most common games.
2. An editable `presets.json` next to the exe, so you can add a new game
   without touching code. Format:
   ```json
   {
     "name": "My Game",
     "type": "steamcmd",
     "appId": "123456",
     "port": 27015,
     "executable": null,
     "config_files": ["server.cfg"],
     "description": "My game server",
     "anonymous_login": true
   }
   ```
   Set `anonymous_login` to `false` for games that need a Steam account. Optional:
   `"category"` (survival / shooter / sandbox / other), `"popular": true`,
   `"args"` (default launch options) and `"manual": {"url": "https://...", "how": "..."}`
   for games whose files come from their own website.

When you choose an install folder, the app automatically creates a **subfolder
named after the server** inside it, so every game gets its own clean folder
instead of mixing files together.

## Steam account login

Most dedicated servers download with anonymous SteamCMD login. Games marked
"game purchase required" on [Valve's dedicated-servers list](https://developer.valvesoftware.com/wiki/Dedicated_Servers_List)
need a real Steam account that owns the game, so they use account login
automatically.

- In **Settings** (gear icon), enter a Steam username and password once.
- When you install/update a purchase-required game (DayZ, Arma 3, ARK, Project
  Zomboid, Terraria, Starbound, Assetto Corsa, and more), the app uses your
  account login automatically. Anonymous games keep using anonymous login.
- The generated `start.bat` validate step uses the same correct login, so
  update-before-start also works for these games.

Settings notes:

- Use a dedicated Steam account for servers, not your personal one.
- Steam Guard may prompt once on the first login.
- Credentials are stored in `settings.json` next to the exe (plaintext -- keep
  this file private). The generated `start.bat` files never contain the password;
  they log in with the username and SteamCMD's cached login.

## RCON (remote admin)

Some servers ignore anything typed into their console window: **ARK (ASA and
Evolved), Palworld, Conan Exiles, Squad, V Rising, and the BattlEye games Arma 3,
DayZ and Arma Reforger**. For those BaoBun sends
the console, quick commands, restart warnings, scheduled announcements, the save
before a backup and the clean stop (Palworld: `Save` + `DoExit`, ARK: `SaveWorld` +
`DoExit`, Arma 3/DayZ: `#shutdown`, broadcasts as `say -1`) over RCON automatically. Replies show up in the live console marked
`[RCON]`, and Palworld/ARK/Squad/Conan player lists come from RCON when the Steam
query doesn't answer.

BaoBun reads the port and password from the game's own settings - nothing extra to
type in:

| Game | Where RCON is turned on |
|---|---|
| ARK | `?RCONEnabled=True` + `-RCONPort=` + `ServerAdminPassword` in the launch options, or `GameUserSettings.ini` |
| Palworld | `PalWorldSettings.ini`: `RCONEnabled=True`, `RCONPort`, `AdminPassword` |
| Conan Exiles | `Game.ini` `[RconPlugin]`: `RconEnabled=1`, `RconPort`, `RconPassword` |
| Squad | `SquadGame/ServerConfig/Rcon.cfg`: `Port`, `Password` |
| V Rising | `ServerHostSettings.json`: `"Rcon": {"Enabled": true, "Port": ..., "Password": ...}` |
| Arma 3 / DayZ (BattlEye RCon) | `BattlEye\beserver_x64.cfg` (or `-bepath` / `<profiles>\BattlEye`): `RConPassword`, `RConPort` |
| Arma Reforger (BattlEye RCon) | `config.json`: `"rcon": {"address": "127.0.0.1", "port": 19999, "password": "..."}` |
| Minecraft | `server.properties`: `enable-rcon=true`, `rcon.port`, `rcon.password` |
| Project Zomboid | the server `.ini`: `RCONPort`, `RCONPassword` |
| Source games (GMod, CS2, TF2...) | `+rcon_password` or `rcon_password` in `server.cfg` |

If RCON is off for one of the console-ignoring games, the console says what to turn
on. Minecraft, Project Zomboid and Source games keep using their console and only
fall back to RCON when the console isn't attached (for example after the server was
started outside BaoBun).

There is no separate RCON tab: type commands in the **Live Console** and BaoBun
sends them the right way.

**Turn on RCON** (Checkup, or the Ports page) switches it on for you for every game
in the table except the Source games: it gives RCON its own free port and, if the
password is blank, a strong random one (20 letters and digits). The new password is
shown once with a Copy button and saved in the game's own settings, so you'll also
find it in Settings later. An existing password is never replaced. In ARK and
Palworld that password is also the in-game admin password.

RCON ports are only for BaoBun on the server PC: they're left out of the Windows
Firewall rules and router forwarding. Before every RCON command BaoBun checks who is
listening on the port, and refuses if it's another BaoBun server - so two servers
left on the same RCON port can never get (or stop) each other.

## Ports (many games share the same defaults)

Lots of games default to the same ports - 7777, 27015, 25575 for RCON - so two ARK
maps, or ARK next to Conan, would fight over them. The **Ports** page (top bar on
the desktop, the Ports button in the web panel) lists every server's ports - game,
query, RCON and extras like Palworld's REST API or Rust+ - with ranges for games
that use several in a row (Valheim 2456-2458, Arma 2302-2306). Clashes are red.

* **Fix automatically** moves servers onto free ports. Running servers keep theirs;
  otherwise the first server in the list keeps its ports and later ones move.
* **Change** sets a server's ports by hand.
* New servers get free ports when you add them.
* BaoBun writes the ports into the game's own settings (launch options, ARK's map
  URL, or the config file). If the game hasn't made its config file yet (Vintage
  Story, Enshrouded, Unturned...), the port is written as soon as the file exists -
  the first start uses the game's default, which Checkup mentions.
* A server won't start on top of a running BaoBun server's ports, and BaoBun warns
  when some other program already uses one.

BaoBun knows the ports of: ARK (both), Palworld, Valheim, Conan Exiles,
Satisfactory, Soulmask, Icarus, Abiotic Factor, Insurgency: Sandstorm, Mordhau,
Arma 3, DayZ, Arma Reforger, Rust, 7 Days to Die, Project Zomboid, Vintage Story,
Minecraft, Enshrouded, V Rising, Unturned, Squad and the Source games. For any other
game it uses the port from Edit Server, and you change it in the game's settings.

## Workshop mods

Select a server and click **Workshop Mods** in the tools row to download Steam
Workshop items directly into that server's `steamapps/workshop` folder via
SteamCMD.

- Enter one or more Workshop item IDs (comma-separated).
- Enter the **Workshop App ID** (leave blank to use the game's own app ID).
- Click **Download**. Downloads use the same Steam account login rules as installs
  (anonymous for free games, account login for purchase-required games).

> Downloading is separate from *activating* a mod — you still need to add the
> item to the game's own mod config (e.g. `-mods=`, a mod-list file, or the
> game's in-game mod menu).

## ARK: Survival Ascended cluster

Click the ARK Cluster button in the top bar to generate one server entry per
official map from a single ASA install (no separate install per map).

1. Install the ASA dedicated server once (Steam App ID 2430930) into a folder.
2. Open ARK Cluster, pick that folder (the one containing `ShooterGame`), set a
   shared Cluster ID, and tick the maps you want.
3. Click Create Maps.

Each map gets:
- The correct map identifier for the full released set: TheIsland_WP, TheCenter_WP,
  ScorchedEarth_WP, Aberration_WP, Extinction_WP, Ragnarok_WP, Astraeos_WP,
  Valguero_WP, Genesis_WP, LostColony_WP, plus community/premium maps
  Club ARK (BobsMissions_WP), Svartalfheim_WP, Forglar_WP, Amissa_WP, Bjarnheim_WP,
  Forglar2_WP (Forglar Part II), PROTOCOL_WP (Lost Protocol), The Island Reforged
  (TheIsland_WP), Dark's Event Map (Darken_Map_WP), Reverence (Reverence_WP),
  Prometeus (PROMETEUS_WP) and TaeniaStella (TaeniaStella_WP).
- Mod maps automatically get their CurseForge mod ID added to `-mods=`, so the
  server downloads them on first start. They show a "[mod]" tag and default to
  unchecked. "The Island Reforged" is a reforge of the official Island: it uses
  the same `TheIsland_WP` level but adds `-MapModID=1460513` to load the modded
  version.
- Unique ports (Game/Query/RCON, stepped by 2 to avoid the +1 query-port clash).
- `-clusterid` (shared, so players can transfer between maps) and a per-map
  `-AltSaveDirectoryName` (separate save folders).
- A manual-start flag, so maps spin up only when someone clicks them (via the
  grid or Discord button), matching the on-demand pattern.

`updateBeforeStart` is left off for cluster maps (validating 30 GB before every
map start would be slow). Update the shared install via SteamCMD once instead.

### ARK World: health, creatures, loot and rules

**ARK World** (top bar, the tools on an ARK server, or the **ARK world** tab in the
web panel) works on a whole cluster at once - or on a single ARK server.

- **Health** checks that every map can safely trade with the others:
  - every map has the cluster's mods (a creature or item from a mod vanishes when
    it's downloaded on a map without that mod) - **Sync all maps** fixes it, and
    BaoBun also puts the mod list back on a map right before it starts
  - every map has `-clusterid`
  - no two maps save into the same world folder
  - maps on different install folders share one cluster folder
    (`-ClusterDirOverride`, **Share one cluster folder**), run the same game version
    and have the same transfer rules (**Copy settings**)
  - uploads don't expire too quickly (**Keep uploads 30 days**)
  Problems also show up in Checkup.
- **Creatures**: pick any creature and set it to *Never spawns* or *Replace with*
  another one (`NPCReplacements` in Game.ini). Mod creatures can be added by class
  name. **Wipe wild creatures** clears the wild ones on running maps afterwards
  (`DestroyWildDinos`) - tamed creatures are never touched.
- **Loot**: change what supply drops, cave and deep-sea crates, boss rewards and
  boss/alpha drops give. Per crate: *Add to its normal loot* or *Replace its loot*,
  how many items it gives, and per item the amount, quality (Primitive ->
  Ascendant), blueprint chance and how likely it is (`ConfigOverrideSupplyCrateItems`).
  Hand-written entries BaoBun can't show as a list are kept untouched.
- **Rules**: rates, world, players, tames, transfers (upload/download blocks, how
  long uploads are kept, the tame level cap that must match on every map) and loot
  quality - blank means ARK's default.

ARK keeps these settings per install folder, so maps sharing one install share
them automatically; with several installs BaoBun writes them into every one. Every
save keeps the rest of Game.ini / GameUserSettings.ini as it was and leaves a
`.bak`. Changes are used when the maps restart.

**Updating a cluster**: Update on any map updates the whole cluster - every map gets
a warning and stops, each install folder is updated, BaoBun checks they all ended up
on the same game build (and says so if not), then the maps that were running start
again. Automatic updates and the nightly restart do the same: with "Update the game
automatically when nobody is online" on, waiting game updates are installed during
the nightly restart while everything is stopped. Health offers **Update all maps**
when installs are on different versions.

**Different creatures, loot or rules on one map**: in Health, **Own settings...**
(desktop) / **Give it its own folder...** (web) copies the shared install for that
map - same game, mods and world - into a new folder next to it (e.g.
`ASA-Ragnarok`). The map must be stopped, and the copy needs as much free space as
the install. Every map is pointed at one shared cluster folder first, so uploads
keep working both ways; the old install keeps its files (and that map's old world
copy as a backup). Then pick **"Ragnarok only"** in the list at the top (desktop) or
**This map only** (web) to change its Creatures, Loot and Rules alone. The transfer
rules (upload/download blocks, how long uploads are kept, the tame level cap...)
always go to every map, and Health only flags those if they drift apart (**Copy
settings** copies just them). Update both installs when ARK updates (Update on each
map), and use Open Firewall on the moved map once.

**Browsing ARK mods**: the web panel's Mods tab can search CurseForge (popular,
updated, newest...) with pictures and descriptions, and install a mod on every map
of the cluster in one click. It needs a free CurseForge API key
(console.curseforge.com), set in the Mods tab options on the server PC. Removing a mod
from a cluster warns that its creatures and items disappear from every map.

Changing a map's server name no longer moves it to a new save folder, and applying
cluster settings keeps each map's own ports and extra launch options.

### Cluster parity (password / mods)

Server password, admin password, and the mod list are shared launch-argument
values. Set them once in the ARK Cluster setup dialog, or use the **Cluster
Settings** button later to change them across every map in a cluster in one go.
(Passwords should not contain spaces.)

### Gameplay settings (cluster-wide)

Click **Cluster Settings → Gameplay Settings...** to tune the most important knobs
with friendly labels instead of raw INI keys:

- **Difficulty**, XP / harvest / taming / breeding **multipliers** (mating, hatch,
  mature, cuddle intervals), and day / night **length**.
- **Max players**, **PvE vs PvP**, **Hardcore**, **Show player on map**, and
  **Third person** toggles.

These all live in the shared `GameUserSettings.ini`, so one change applies to every
map in the cluster at once — and the editor preserves any other hand-written lines
verbatim (with a `.bak` backup on each save). Advanced keys not in this list remain
editable in the server's **Settings** tab (All settings / Raw file).

## Smart boot

When the Watchdog starts (e.g. at Windows login) it decides per server whether to start it.
Player activity comes from `servers_activity.json`, which the Watchdog fills by watching
each server's logs for player joins (patterns come from the game plugin; set
`"joinPattern"` on a server to add your own regex).

- **Manual start only** servers (checkbox in Edit Server; ARK cluster maps by default) → never auto-started.
- Servers with players in the last 2 days → started (change the number of days in
  Settings > Nightly restart).
- Servers idle for longer → skipped (start them from the app or Discord).
- Servers with no activity history yet → started only if they were running when the
  PC/agent last shut down.
- Override per-server via `servers_boot.json`:
  ```json
  { "force_start_list": ["server_id"], "never_auto_start": ["other_id"] }
  ```

## Nightly restart

Settings > **Nightly restart & start at boot**. Every night at the time you pick
(default 05:00) BaoBun:

1. posts in Discord which servers will come back and which will sleep, and warns
   players in-game 15, 5 and 1 minute before (it goes ahead even if people are on)
2. stops every server cleanly so it saves, then backs up the worlds
3. restarts Windows - clears memory leaks in the games and Windows itself
4. when the PC is back, starts only the servers that were running **and** had
   players in the last N days (or have no history yet). The rest sleep until
   someone presses **Start** on Discord or in the panel. Then it posts "Back up:
   ... Sleeping: ..." in Discord.

The web panel's **Server PC** button shows and changes the same settings (plus how
BaoBun updates itself and the PC-down alerts page) from any browser - admins only.

While this runs nobody can start a server (Discord says "restarting for
maintenance"). If Windows refuses to restart, BaoBun brings the servers back right
away instead. The window shows what would happen if it ran now.

**Start BaoBun when the PC turns on** (same window) is needed for this: the normal
"start when I sign in" only runs after someone logs in. It adds a Windows scheduled
task (one admin prompt) that starts the Watchdog at boot as your Windows user. **Simple**
stores no password but can't open network folders (`\\PC\folder`); **with network
access** has Windows ask for your password once (in its own window - BaoBun never sees
it) so backups to another PC and servers on network folders keep working. Checkup
warns when something needs the network version.

## Public status page (for friends, no login)

A read-only page anyone you share it with can open: which servers are up, how many
are playing (optionally who), the address to join with a Copy button, each server's
mod list, and a Discord invite button. It refreshes every minute and has no
buttons, passwords, folders or console - it runs on its own port (8790), separate
from the web panel. `/status.json` has the same data for other tools.

Turn it on in **Web Panel > Public status page...** (desktop) or **Server PC > Public
status page** (web panel). By default only this PC, the home network and Tailscale
can open it; tick **Answer the internet too** and forward the port on your router to
share it publicly, and use **Open Windows Firewall for it** once. Set **Address
players type** to your domain (otherwise it shows the PC's public IP). Servers with
*Show on the Discord panel* off are left off the page.

## PC-down alerts (Discord message when the server PC is down)

If the server PC freezes, loses power or Windows hangs, BaoBun on it can't tell
anyone. Settings > **PC-down alerts** fixes that with a second PC (for example your own PC):

- On the server PC, BaoBun answers a tiny "I'm alive" page on port 8791
  (`/healthz`, home network and Tailscale only). Click **Allow it through Windows
  Firewall** once there.
- On your own PC, type the server PC's name or address and click **Test**, then
  Save. BaoBun on the watching PC (its Watchdog) checks every minute and posts to
  the Discord alert webhook set on that PC when the server PC hasn't answered for 3
  minutes - saying whether the whole PC is gone or only BaoBun - and again when it's
  back. The nightly restart doesn't trigger it.

## Running BaoBun on a separate game-host PC

Recommended setup: develop and play on your own PC, and run the game servers on a
separate machine (more cores/RAM, never interrupted by your gaming).

1. **On the host:** make a folder, e.g. `D:\BaoBun`, and share it on your home
   network (right-click > Properties > Sharing) so your PC can write to it.
2. **On your PC:** double-click **`deploy_to_host.bat`**. The first time it asks for
   the share path (e.g. `\\SERVER-PC\BaoBun`) and remembers it in
   `deploy_target.txt`. It builds the exe and copies **only `BaoBunCP.exe`** over -
   the host keeps its own servers, settings, backups and Steam login. It works while
   BaoBun runs on the host (same rename trick as `build_exe.bat`).
3. **On the host:** start `BaoBunCP.exe`, add your servers there (one-click installs
   download onto the host), turn on the **Watchdog**, and turn on **Web Panel**.
4. **Manage it from your PC in a browser.** Either install Tailscale on both PCs, or
   tick **Also allow PCs on my home network** in Web Panel settings and use the host's
   home address (Detect fills it in, e.g. `http://192.168.1.50:8765`). Add that same
   address + `/auth/callback` as a Redirect in your Discord application. The first
   time the Watchdog starts, allow it through Windows Firewall on *private* networks.

The desktop app always manages the servers of the PC it runs on, so day to day you
use the web panel (or the Discord bot) from your own PC and the desktop app on the
host itself.

## Rebuilding the executable

Double-click **`build_exe.bat`** (no need to run it as administrator). It builds into
`build\out` and then swaps the new exe into `dist\BaoBunCP.exe`. If BaoBun is running
from that exe, the running copy is renamed to `BaoBunCP.old-NNNN.exe` (Windows allows
renaming a running program, not deleting it) and cleaned up by the next build.

**Switching to the new version** happens by itself: the Watchdog notices the
new exe and switches over as soon as nobody is playing and no console-controlled
server (Vintage Story, Minecraft, Project Zomboid, Source games) is running - or at
the nightly restart. Game servers keep running while it switches; RCON games (ARK,
Palworld...) don't even notice. To switch right away, click **Update ready** in the
top bar (the window reopens itself) or **Switch now** in the web panel. Servers
controlled through their console lose that console until they're next restarted,
so their next stop is a force-stop - BaoBun lists them before you confirm.

Set `"self_update": "manual"` in settings.json to only switch when you click.
Running BaoBun from source (python app.py) doesn't update itself.

Requires a Python 3.14 env with `requirements.txt` installed
(`customtkinter`, `discord.py`, `pyyaml`, `Pillow`, `psutil`, `toml`, plus
`pyinstaller` for builds). The game plugins are bundled into the exe; a `plugins`
folder next to the exe overrides them.
