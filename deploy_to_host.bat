@echo off
REM Tofu - build the exe here and copy it to the server PC.
REM First run asks for the host's Tofu folder as a network path
REM (e.g. \\SERVER-PC\Tofu) and remembers it in deploy_target.txt.
REM Only TofuSM.exe is copied: the host keeps its own servers, settings and backups.
title Deploy Tofu to the server PC
cd /d "%~dp0"
set TARGET=
if exist deploy_target.txt set /p TARGET=<deploy_target.txt
if not "%TARGET%"=="" goto have_target
echo Where does Tofu live on the server PC?
echo Share that folder on the host (right-click it, Properties, Sharing), then type its
echo network path here, for example  \\SERVER-PC\Tofu
set /p TARGET=Path: 
if "%TARGET%"=="" goto end
>deploy_target.txt echo %TARGET%

:have_target
if not exist "%TARGET%\" (
  echo.
  echo Can't reach %TARGET%
  echo Is the host on and the folder shared? To change the path, delete deploy_target.txt.
  goto end
)
call build_exe.bat /nopause
if errorlevel 1 (
  echo.
  echo Not deployed: the build didn't finish.
  goto end
)
echo.
echo Copying to %TARGET% ...
del /q "%TARGET%\TofuSM.old-*.exe" >nul 2>&1
set RUNNING=0
if exist "%TARGET%\TofuSM.exe" (
  del /q "%TARGET%\TofuSM.exe" >nul 2>&1
  if exist "%TARGET%\TofuSM.exe" (
    set RUNNING=1
    move /y "%TARGET%\TofuSM.exe" "%TARGET%\TofuSM.old-%RANDOM%.exe" >nul
  )
)
if exist "%TARGET%\TofuSM.exe" (
  echo.
  echo Couldn't replace TofuSM.exe on the host - close Tofu there and run this again.
  goto end
)
copy /y dist\TofuSM.exe "%TARGET%\TofuSM.exe" >nul
if errorlevel 1 (
  echo.
  echo Copy failed - check you can write to %TARGET%.
  goto end
)
echo.
echo DEPLOYED to %TARGET%\TofuSM.exe
if "%RUNNING%"=="1" (
  echo Tofu on the host switches to it by itself when nobody is playing ^(or at the nightly
  echo restart^). To switch right away: "Update ready" in Tofu on the host, or "Switch now" in the web panel.
) else (
  echo Start it on the host with TofuSM.exe.
)

:end
pause
