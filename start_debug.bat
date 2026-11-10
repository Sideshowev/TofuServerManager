@echo off
REM BaoBun Control Panel - runs the app with a console showing its log output.
title BaoBun Control Panel (debug)
REM Finds Python on any PC: the "py" launcher that comes with python.org Python,
REM else "python" on PATH. Set PYTHON yourself first to use a specific one.
if not defined PYTHON (where py >nul 2>&1 && set "PYTHON=py -3")
if not defined PYTHON set "PYTHON=python"
%PYTHON% "%~dp0app.py"
pause
