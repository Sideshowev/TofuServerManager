@echo off
REM BaoBun Control Panel - build dist\BaoBunCP.exe from the current source.
REM Works while BaoBun is running: the new exe is built elsewhere, the running
REM one is renamed out of the way (Windows allows renaming a running program),
REM and the new one is copied in. Restart BaoBun afterwards to use it.
title Build BaoBunCP.exe
REM Finds Python on any PC: the "py" launcher that comes with python.org Python,
REM else "python" on PATH. Set PYTHON yourself first to use a specific one.
if not defined PYTHON (where py >nul 2>&1 && set "PYTHON=py -3")
if not defined PYTHON set "PYTHON=python"
cd /d "%~dp0"
set RESULT=1
if not exist logs mkdir logs
echo Installing build tools (first time only)...
%PYTHON% -m pip install -q -r requirements.txt pyinstaller
echo Building - this takes a minute or two...
%PYTHON% -m PyInstaller --noconfirm --clean --distpath build\out BaoBunCP.spec > logs\build.log 2>&1
if errorlevel 1 goto failed
if not exist build\out\BaoBunCP.exe goto failed
set RESULT=0

if not exist dist mkdir dist
REM old copies from earlier builds (ones still running just stay until next time)
del /q dist\BaoBunCP.old-*.exe >nul 2>&1
set RUNNING=0
if exist dist\BaoBunCP.exe (
  del /q dist\BaoBunCP.exe >nul 2>&1
  if exist dist\BaoBunCP.exe (
    set RUNNING=1
    move /y dist\BaoBunCP.exe "dist\BaoBunCP.old-%RANDOM%.exe" >nul
  )
)
if exist dist\BaoBunCP.exe goto locked
copy /y build\out\BaoBunCP.exe dist\BaoBunCP.exe >nul
if errorlevel 1 goto locked
echo.
echo BUILD OK: dist\BaoBunCP.exe is up to date.
if "%RUNNING%"=="1" (
  echo BaoBun is still running the OLD version. To switch, restart BaoBun AND its
  echo background agent ^(best at a quiet time - see README "Rebuilding the executable"^).
)
goto end

:locked
set RESULT=1
echo.
echo BUILD OK, but dist\BaoBunCP.exe couldn't be replaced (something has it locked).
echo The new exe is at build\out\BaoBunCP.exe - close BaoBun and copy it over.
goto end

:failed
set RESULT=1
echo.
echo BUILD FAILED - the last lines of logs\build.log:
powershell -NoProfile -Command "Get-Content logs\build.log -Tail 25"

:end
if /i not "%~1"=="/nopause" pause
exit /b %RESULT%
