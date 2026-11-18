@echo off
REM Tofu Server Manager - opens the app without a console window.
REM Use start_debug.bat to see log output in a console.
REM Finds Python on any PC: the "pyw" launcher from python.org Python, else pythonw on PATH.
if not defined PYTHONW (where pyw >nul 2>&1 && set "PYTHONW=pyw -3")
if not defined PYTHONW set "PYTHONW=pythonw"
start "" %PYTHONW% "%~dp0app.py"
