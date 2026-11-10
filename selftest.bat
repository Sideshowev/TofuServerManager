@echo off
REM BaoBun Control Panel - self test. Runs the engine tests with fake servers in
REM temporary folders; your real servers and settings are not touched.
title BaoBun self test
REM Finds Python on any PC: the "py" launcher that comes with python.org Python,
REM else "python" on PATH. Set PYTHON yourself first to use a specific one.
if not defined PYTHON (where py >nul 2>&1 && set "PYTHON=py -3")
if not defined PYTHON set "PYTHON=python"
cd /d "%~dp0"
if not exist logs mkdir logs
%PYTHON% -m pip install -q -r requirements.txt
%PYTHON% -W ignore -m unittest discover -s tests -v > logs\selftest.log 2>&1
type logs\selftest.log
echo.
findstr /b /c:"OK" logs\selftest.log >nul && (echo ALL TESTS PASSED) || (echo SOME TESTS FAILED - see logs\selftest.log)
pause
