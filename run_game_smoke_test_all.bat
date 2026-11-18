@echo off
REM Test every game Tofu can download that has not been tested yet (many hours - run overnight).
call "%~dp0run_game_smoke_test.bat" --all
