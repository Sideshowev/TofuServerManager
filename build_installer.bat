@echo off
REM BaoBun - build the Windows installer: build\installer\BaoBun-Setup-<version>.exe
REM Needs Inno Setup 6 (free) - installed automatically with winget the first time.
REM The version comes from core\version.py - bump it there before a release.
REM Publish the installer and its .sha256 file on a GitHub release of the repository
REM named in core\version.py (UPDATE_REPO) and installed copies offer the update.
title Build the BaoBun installer
if not defined PYTHON if exist "%~dp0find_python.bat" call "%~dp0find_python.bat"
if not defined PYTHON (where py >nul 2>&1 && set "PYTHON=py -3")
if not defined PYTHON set "PYTHON=python"
cd /d "%~dp0"
if not exist logs mkdir logs
set RESULT=1
set STEPS=logs\installer_steps.log
>"%STEPS%" echo %DATE% %TIME% start

call :find_iscc
if not defined ISCC (
  echo Inno Setup 6 ^(free, needed only to MAKE the installer^) isn't installed - installing it now...
  where winget >nul 2>&1
  if errorlevel 1 (
    echo winget isn't available on this PC. Get Inno Setup 6 from https://jrsoftware.org/isdl.php and run this again.
    goto end
  )
  winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements > logs\innosetup_install.log 2>&1
  call :find_iscc
)
if not defined ISCC (
  echo Couldn't install Inno Setup automatically ^(see logs\innosetup_install.log^).
  echo Get it free from https://jrsoftware.org/isdl.php and run this again.
  goto end
)

>>"%STEPS%" echo Inno Setup: %ISCC%
for /f "usebackq delims=" %%V in (`%PYTHON% -c "from core.version import VERSION; print(VERSION)"`) do set VER=%%V
if not defined VER (
  echo Couldn't read the version from core\version.py.
  goto end
)

>>"%STEPS%" echo version %VER% - building the exe
REM in its own cmd, so nothing in the exe build can end this script
cmd /c build_exe.bat /nopause
if errorlevel 1 (
  >>"%STEPS%" echo exe build failed
  echo Not building the installer: the exe build failed.
  goto end
)

>>"%STEPS%" echo exe built - making the installer
echo Making the installer for BaoBun %VER%...
"%ISCC%" /Q "/DMyAppVersion=%VER%" installer\BaoBun.iss > logs\installer.log 2>&1
if errorlevel 1 (
  echo INSTALLER FAILED - see logs\installer.log
  powershell -NoProfile -Command "Get-Content logs\installer.log -Tail 20"
  goto end
)
set OUT=build\installer\BaoBun-Setup-%VER%.exe
powershell -NoProfile -Command "(Get-FileHash '%OUT%' -Algorithm SHA256).Hash.ToLower() + '  BaoBun-Setup-%VER%.exe' | Set-Content -Encoding ascii '%OUT%.sha256'"
set RESULT=0
>>"%STEPS%" echo installer OK
echo.
echo INSTALLER OK: %OUT%
echo (and %OUT%.sha256 - upload both to the GitHub release)

:end
if /i not "%~1"=="/nopause" pause
exit /b %RESULT%

:find_iscc
set ISCC=
for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe") do (
  if exist %%P set "ISCC=%%~P"
)
if not defined ISCC (
  where iscc >nul 2>&1 && set "ISCC=iscc"
)
exit /b 0
