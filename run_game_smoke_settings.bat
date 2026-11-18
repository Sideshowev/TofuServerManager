@echo off
REM Settings check: change each popular game's server name, restart, see if it sticks. Then retest Source games.
title Tofu settings check
if not defined PYTHON if exist "%~dp0find_python.bat" call "%~dp0find_python.bat"
if not defined PYTHON (where py >nul 2>&1 && set "PYTHON=py -3")
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PYTHON="%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" -3"
if not defined PYTHON for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PYTHON="%%D\python.exe""
if not defined PYTHON set "PYTHON=python"
cd /d "%~dp0"
if not exist logs mkdir logs
%PYTHON% -m pip install -q -r requirements.txt
echo Settings check on popular games, then the Source-engine games again. Close this window to stop.
%PYTHON% tools\game_smoke_test.py --redo --games "Valheim,Palworld,Enshrouded,7 Days to Die,Rust,Satisfactory,Conan Exiles,Sons of the Forest,Core Keeper,Abiotic Factor,Soulmask,SCUM,Space Engineers,The Lord of the Rings: Return to Moria,Squad,Mordhau,Arma Reforger,Unturned,Barotrauma,Stationeers,HumanitZ,VEIN,Insurgency: Sandstorm,Empyrion - Galactic Survival,Killing Floor 2,Garry's Mod,Team Fortress 2,Left 4 Dead 2,Counter-Strike 2,Minecraft (Java),Vintage Story,ARK: Survival Ascended,Counter-Strike: Source,Half-Life 2: Deathmatch" > logs\game_smoke_settings.log 2>&1
%PYTHON% tools\game_smoke_test.py --failed --failed-with srcds > logs\game_smoke_source.log 2>&1
echo Done - see logs\game_smoke_report.md
pause
