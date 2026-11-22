@echo off
REM Tofu Server Manager - build dist\TofuSM.exe from the current source.
REM Works while Tofu is running: the new exe is built elsewhere, the running
REM one is renamed out of the way (Windows allows renaming a running program),
REM and the new one is copied in. Restart Tofu afterwards to use it.
title Build TofuSM.exe
REM Finds Python on any PC: the "py" launcher that comes with python.org Python,
REM else "python" on PATH. Set PYTHON yourself first to use a specific one.
if not defined PYTHON if exist "%~dp0find_python.bat" call "%~dp0find_python.bat"
if not defined PYTHON (where py >nul 2>&1 && set "PYTHON=py -3")
if not defined PYTHON set "PYTHON=python"
cd /d "%~dp0"
set RESULT=1
if not exist logs mkdir logs
echo Installing build tools (first time only)...
%PYTHON% -m pip install -q -r requirements.txt pyinstaller
echo Building - this takes a minute or two...
REM a leftover exe from an earlier build must not pass for a fresh one
if exist build\out\TofuSM.exe del /q build\out\TofuSM.exe
if exist build\out\TofuSM.exe goto failed
%PYTHON% -m PyInstaller --noconfirm --clean --distpath build\out TofuSM.spec > logs\build.log 2>&1
if errorlevel 1 goto failed
if not exist build\out\TofuSM.exe goto failed
set RESULT=0

if not exist dist mkdir dist
REM old copies from earlier builds (ones still running just stay until next time)
del /q dist\TofuSM.old-*.exe >nul 2>&1
set RUNNING=0
if exist dist\TofuSM.exe (
  del /q dist\TofuSM.exe >nul 2>&1
  if exist dist\TofuSM.exe (
    set RUNNING=1
    move /y dist\TofuSM.exe "dist\TofuSM.old-%RANDOM%.exe" >nul
  )
)
if exist dist\TofuSM.exe goto locked
copy /y build\out\TofuSM.exe dist\TofuSM.exe >nul
if errorlevel 1 goto locked
echo.
echo BUILD OK: dist\TofuSM.exe is up to date.
if "%RUNNING%"=="1" (
  echo Tofu is still running the OLD version. To switch, restart Tofu AND its
  echo background agent ^(best at a quiet time - see README "Rebuilding the executable"^).
)
goto end

:locked
set RESULT=1
echo.
echo BUILD OK, but dist\TofuSM.exe couldn't be replaced (something has it locked).
echo The new exe is at build\out\TofuSM.exe - close Tofu and copy it over.
goto end

:failed
set RESULT=1
echo.
echo BUILD FAILED - the last lines of logs\build.log:
powershell -NoProfile -Command "Get-Content logs\build.log -Tail 25"

:end
if /i not "%~1"=="/nopause" pause
exit /b %RESULT%
