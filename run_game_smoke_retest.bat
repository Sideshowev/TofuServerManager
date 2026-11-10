@echo off
REM Test again only the games that failed in the last game smoke test.
call "%~dp0run_game_smoke_test.bat" --failed
