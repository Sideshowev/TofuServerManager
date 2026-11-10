@echo off
REM BaoBun - run every test, including the desktop smoke test that opens the real
REM windows for a moment. Uses fake servers in temporary folders; your servers and
REM settings are not touched. Same as selftest.bat but shows the windows test too.
title BaoBun tests
REM Finds Python on any PC: the "py" launcher that comes with python.org Python,
REM else "python" on PATH. Set PYTHON yourself first to use a specific one.
if not defined PYTHON (where py >nul 2>&1 && set "PYTHON=py -3")
if not defined PYTHON set "PYTHON=python"
cd /d "%~dp0"
if not exist logs mkdir logs
%PYTHON% -m pip install -q -r requirements.txt pytest
%PYTHON% -m pytest -q tests > logs\tests.log 2>&1
type logs\tests.log
echo.
findstr /c:" failed" /c:" error" logs\tests.log >nul && (echo SOME TESTS FAILED - see logs\tests.log) || (echo ALL TESTS PASSED)
pause
