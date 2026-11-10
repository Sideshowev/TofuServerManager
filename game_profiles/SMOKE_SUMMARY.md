# Game smoke test - what it found

Made by `tools/smoke_summary.py` from `logs/game_smoke_report.json` on 2026-11-10 06:17. The full report (with log ends) is `logs/game_smoke_report.md`.

## In short

- **133 games tested, 113 installed, started and stayed up.**
- 65 of those opened a port BaoBun expects (or answered a player query) - confirmed up.
- 18 were up on ports BaoBun doesn't know about - BaoBun's port list for them is wrong or missing.
- 30 kept running but BaoBun saw no open port after two minutes - **not confirmed**: still loading, or quietly not working.
- 20 did not work (below).

## Settings a new-server form could ask for

For each game the test set the server name, join password and max players through Essentials, restarted the game and looked again.

- **works** - the game kept it and players see it (checked with the Steam server query)
- **kept** - the game kept it in its settings; this game doesn't answer the query, so it couldn't be checked from outside
- **IGNORED** - saved, but the game showed something else: BaoBun is writing to the wrong place
- **REWRITTEN** - the game threw the value away when it started
- **can't save** - BaoBun couldn't write it (reasons below the table)
- `-` - BaoBun doesn't know where this game keeps it

| Game | Essentials | Server name | Password | Max players |
|---|---|---|---|---|
| 7 Days to Die | made for this game | kept | kept | kept |
| ARK: Survival Ascended | made for this game | can't save | can't save | kept |
| ARK: Survival Evolved | made for this game | can't save | can't save | can't save |
| ASTRONEER | found automatically | kept | kept | - |
| Age of Chivalry | found automatically | kept | - | - |
| Arma Reforger | made for this game | can't save | can't save | can't save |
| Conan Exiles | made for this game | can't save | can't save | - |
| Counter-Strike 1.6 | found automatically | kept | - | - |
| Counter-Strike: Condition Zero | found automatically | kept | - | - |
| Craftopia | found automatically | - | kept | - |
| Day of Defeat | found automatically | kept | - | - |
| Deathmatch Classic | found automatically | kept | - | - |
| Double Action | found automatically | kept | - | - |
| Empyrion - Galactic Survival | found automatically | - | - | kept |
| Enshrouded | made for this game | works | - | works |
| Eternal Silence | found automatically | - | kept | - |
| Fistful of Frags | found automatically | - | kept | - |
| Garry's Mod | made for this game | kept | kept | kept |
| Half-Life | found automatically | kept | - | - |
| Half-Life: Opposing Force | found automatically | kept | - | - |
| HumanitZ | found automatically | kept | kept | kept |
| Killing Floor 2 | found automatically | works | works | IGNORED |
| Minecraft (Java) | made for this game | - | - | kept |
| Mod and Play | found automatically | IGNORED | IGNORED | - |
| Mordhau | found automatically | kept | kept | - |
| Night of the Dead | found automatically | kept | kept | kept |
| Nuclear Option | found automatically | works | works | works |
| PVKII | found automatically | kept | - | - |
| Palworld | made for this game | kept | kept | kept |
| PixARK | found automatically | REWRITTEN | - | REWRITTEN |
| Project Zomboid | made for this game | works | works | works |
| Renown | found automatically | kept | kept | kept |
| Ricochet | found automatically | kept | - | - |
| Rising Storm 2: Vietnam | found automatically | IGNORED | works | works |
| Rising World | found automatically | works | works | - |
| Rust | made for this game | works | - | works |
| Squad | found automatically | kept | - | kept |
| Team Fortress Classic | found automatically | kept | - | - |
| The Lord of the Rings: Return to Moria | found automatically | REWRITTEN | - | REWRITTEN |
| Unturned | made for this game | works | works | works |
| V Rising | made for this game | kept | kept | kept |
| Valheim | made for this game | kept | kept | - |
| Vintage Story | made for this game | kept | - | kept |
| Voyagers of Nera | found automatically | - | - | kept |
| Windrose | found automatically | kept | kept | - |
| Zombie Panic Source | found automatically | kept | - | - |
| rFactor 2 | found automatically | - | kept | - |

Why some couldn't be saved:

- ARK: Survival Ascended, Server name: Server name can't contain spaces, ? or & (ARK reads it from the launch line)
- ARK: Survival Ascended, Password: The launch options don't start with a map (e.g. TheIsland_WP?listen), so BaoBun can't add ServerPassword. Add the map first in Settings.
- ARK: Survival Evolved, Server name: Server name can't contain spaces, ? or & (ARK reads it from the launch line)
- ARK: Survival Evolved, Password: The launch options don't start with a map (e.g. TheIsland_WP?listen), so BaoBun can't add ServerPassword. Add the map first in Settings.
- ARK: Survival Evolved, Max players: The launch options don't start with a map (e.g. TheIsland_WP?listen), so BaoBun can't add MaxPlayers. Add the map first in Settings.
- Arma Reforger, Server name: config.json doesn't exist yet. Start the server once so it creates its config, then try again.
- Arma Reforger, Password: config.json doesn't exist yet. Start the server once so it creates its config, then try again.
- Arma Reforger, Max players: config.json doesn't exist yet. Start the server once so it creates its config, then try again.
- Conan Exiles, Server name: Engine.ini doesn't exist yet. Start the server once so it creates its config, then try again.
- Conan Exiles, Password: Engine.ini doesn't exist yet. Start the server once so it creates its config, then try again.

(The ARK "server name" lines are the test's fault: its test name has a space in it. The ARK password / max players lines are real: a new ARK server has no map in its launch options yet. Arma Reforger and Conan Exiles are real too: the settings file BaoBun writes to doesn't exist even after the first start.)

**No server name, password or max players known at all (66 games that run):** Abiotic Factor, Alien Swarm: Reactive Drop, American Truck Simulator, Avorion, Barotrauma, Black Mesa: Deathmatch, Citadel: Forged with Fire, Colony Survival, Core Keeper, Counter-Strike 2, Counter-Strike: Global Offensive, Counter-Strike: Source, Day of Defeat: Source, Day of Infamy, Dead Matter, Eco, Empires, Euro Truck Simulator 2, Fortress Forever, Foundry, Frozen Flame, Ground Branch, HL Deathmatch: Source, Half-Life 2: Deathmatch, Holdfast: Nations At War, Hurtworld, Icarus, Insurgency (2014), Insurgency: Modern Infantry Combat, Insurgency: Sandstorm, JBMod, Just Cause 2 MP, Kingdoms Rise, Last Oasis, Left 4 Dead, Left 4 Dead 2, Life is Feudal: Your Own, Medieval Engineers, Mount & Blade II: Bannerlord, Myth of Empires, NEOTOKYO, NS2: Combat, Necesse, No One Survived, Operation: Harsh Doorstop, Plains of Pain, Reflex, SCP: Secret Laboratory, SCUM, Satisfactory, Smalland: Survive the Wilds, Sons of the Forest, Soulmask, Space Engineers, Squad 44 (Post Scriptum), StarRupture, Stationeers, Stormworks: Build and Rescue, Sven Co-op, Team Fortress 2, TerraTech Worlds, The Forest, The Isle (Evrima), Tower Unite, VEIN, Wurm Unlimited

## Up, but on ports BaoBun doesn't expect

| Game | Open ports | BaoBun expected |
|---|---|---|
| ASTRONEER | 7777/UDP | 8777 |
| Core Keeper | 62064/UDP, 64940/TCP | nothing |
| Dead Matter | 11111/UDP, 58537/TCP, 7777/UDP | 7001 |
| Ground Branch | 27015/UDP, 63416/UDP, 63916/TCP | 7777 |
| JBMod | 27005/UDP, 27015/TCP+UDP, 27020/UDP | nothing |
| Last Oasis | 27015/UDP, 49597/TCP | nothing |
| Myth of Empires | 27015/UDP, 27106/TCP, 55634/UDP, 7777/UDP | nothing |
| NS2: Combat | 27015/UDP, 27016/UDP, 61245/TCP | nothing |
| Night of the Dead | 49206/TCP, 7777/UDP | 27015 |
| No One Survived | 1985/TCP, 27015/UDP, 51090/UDP, 59946/TCP, 7777/UDP | 5761 |
| Nuclear Option | 53403/TCP, 54816/UDP, 7778/UDP | 27015 |
| Operation: Harsh Doorstop | 27005/UDP, 51910/TCP | 7777 |
| Reflex | 25787/TCP+UDP, 61379/TCP | nothing |
| Renown | 14001/UDP, 25575/TCP, 64612/TCP | 7777 |
| Smalland: Survive the Wilds | 64356/TCP | 7777 |
| Squad 44 (Post Scriptum) | 27015/UDP, 50968/UDP | 10027 |
| The Isle (Evrima) | 27015/UDP | 7777 |
| Windrose | 42650/TCP | 7777 |

## Running, but no open port seen (not confirmed)

American Truck Simulator, Arma Reforger, Colony Survival, Counter-Strike 1.6, Counter-Strike: Condition Zero, Day of Defeat, Deathmatch Classic, Eco, Empyrion - Galactic Survival, Euro Truck Simulator 2, Half-Life, Half-Life: Opposing Force, Hurtworld, Just Cause 2 MP, Kingdoms Rise, Life is Feudal: Your Own, Medieval Engineers, Mount & Blade II: Bannerlord, Ricochet, Rust, SCP: Secret Laboratory, Sons of the Forest, Space Engineers, Stationeers, Stormworks: Build and Rescue, Sven Co-op, Team Fortress Classic, The Lord of the Rings: Return to Moria, Wurm Unlimited, rFactor 2

## Did not work

| Game | Support | Result | What's wrong | What would fix it |
|---|---|---|---|---|
| ASKA | basic | crashed | Log: Failed to load "SDL3.dll" - it can't load one of its own libraries. | Not looked into yet. Likely needs to be started from another folder, or its real program is a different file. |
| BlazeRush | basic | crashed | Log: "Failed to initialize Steam. Probably Steam client is not running." | Needs the Steam client running on the PC - not a real dedicated server. Say so in its description or drop it from the list. |
| Brickadia | basic | crashed | Log: "Authentication failed on dedicated server. Exiting now." | Needs a Brickadia account sign-in / token, like Don't Starve Together. |
| Dystopia | basic | crashed | Source mod: "Unable to find gameinfo.txt". | Needs -game <its folder>; BaoBun's automatic -game only looks one folder deep. Not fixed. |
| Military Conflict: Vietnam | basic | crashed | Exits with code 1 and writes nothing at all. | Unknown. |
| Out of Reach | basic | crashed | Exits with code 1 and writes nothing at all. | Unknown. |
| Sunkenland | basic | crashed | Unity memory report at exit, no clear error. | Unknown. |
| Synergy | basic | crashed | Source mod: file system fails to start. | Probably needs Half-Life 2 content that the server download doesn't include. Not confirmed. |
| Don't Starve Together | full | didn't start | Starts now (it has to be started from its bin64 folder), but lets nobody in without a Klei server token. | BaoBun refuses to start it without the token and says where to get it. Next: a place in BaoBun to paste the token. |
| Astro Colony | basic | install failed | Installed, but BaoBun couldn't find the server program. | Look at what the install contains and set the program in the game list. Not done yet. |
| D.I.P.R.I.P. | basic | install failed | Installed, but BaoBun couldn't find the server program. | Look at what the install contains and set the program in the game list. Not done yet. |
| Eden Star | basic | install failed | Steam couldn't write the game files (code 8). | Earlier run said "No subscription": it needs a Steam account that owns the game. |
| FiveM (GTA V roleplay) | basic | install failed | Not on Steam. | Expected - the list already says to copy the files in. |
| Nuclear Dawn | basic | install failed | Installed, but BaoBun couldn't find the server program. | Look at what the install contains and set the program in the game list. Not done yet. |
| Path of Titans | basic | install failed | Not on Steam. | Expected - the list already says to copy the files in. |
| Pavlov | basic | install failed | Installed, but BaoBun couldn't find the server program. | Look at what the install contains and set the program in the game list. Not done yet. |
| Risk of Rain 2 | full | install failed | Steam finishes without giving any game files (an empty depot). | Unknown whether a Steam account that owns the game gets the files. Until that's tested it shouldn't be marked fully supported. |
| Serious Sam HD: The Second Encounter | basic | install failed | Needs a Steam account that owns the game. | Expected - mark it "needs a Steam account" in the list. |
| Takedown: Red Sabre | basic | install failed | Needs a Steam account that owns the game. | Expected - mark it "needs a Steam account" in the list. |
| The Front | basic | install failed | Installed, but BaoBun couldn't find the server program. | Look at what the install contains and set the program in the game list. Not done yet. |
