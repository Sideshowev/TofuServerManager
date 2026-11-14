@echo off
REM Picks a Python that already has BaoBun's packages. A PC can have several Pythons and
REM the newest one may be empty. Sets PYTHON; leaves it unset when none has them, and the
REM script that called this then falls back to its usual choice and installs the packages.
for %%P in ("py -3" "py -3.14" "py -3.13" "py -3.12" "py -3.11" "python") do (
  if not defined PYTHON (
    %%~P -c "import customtkinter, psutil" >nul 2>&1 && set "PYTHON=%%~P"
  )
)
