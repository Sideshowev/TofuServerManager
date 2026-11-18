@echo off
REM Tofu game smoke test: installs each popular game in a SEPARATE test copy of
REM Tofu, starts it, checks it gets ready and what its Settings tab shows, then
REM stops and deletes it. Your real servers are never touched. Takes hours and
REM downloads a lot - best overnight. Report: logs\game_smoke_report.md
REM Extra options are passed on, e.g.:  run_game_smoke_test.bat --games "Valheim,Terraria"
title Tofu game smoke test
if not defined PYTHON if exist "%~dp0find_python.bat" call "%~dp0find_python.bat"
if not defined PYTHON (where py >nul 2>&1 && set "PYTHON=py -3")
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PYTHON="%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" -3"
if not defined PYTHON for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PYTHON="%%D\python.exe""
if not defined PYTHON set "PYTHON=python"
cd /d "%~dp0"
if not exist logs mkdir logs
%PYTHON% -m pip install -q -r requirements.txt
%PYTHON% tools\game_smoke_test.py %* 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath logs\game_smoke_test.log"
echo.
echo Done - the report is logs\game_smoke_report.md
pause
