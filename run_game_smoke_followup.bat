@echo off
REM Re-test the games that failed last time, then test every game not tested yet.
title Tofu game smoke test (retest + all)
if not defined PYTHON if exist "%~dp0find_python.bat" call "%~dp0find_python.bat"
if not defined PYTHON (where py >nul 2>&1 && set "PYTHON=py -3")
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PYTHON="%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" -3"
if not defined PYTHON for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PYTHON="%%D\python.exe""
if not defined PYTHON set "PYTHON=python"
cd /d "%~dp0"
if not exist logs mkdir logs
%PYTHON% -m pip install -q -r requirements.txt
%PYTHON% tools\game_smoke_test.py --failed > logs\game_smoke_retest.log 2>&1
%PYTHON% tools\game_smoke_test.py --all > logs\game_smoke_all.log 2>&1
echo Done - the report is logs\game_smoke_report.md
pause
