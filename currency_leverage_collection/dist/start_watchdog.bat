@echo off
REM Watchdog: restart main when it exits (5s delay)
REM Prefer packed EXE; otherwise use venv python; otherwise system python

chcp 65001 >nul
cd /d "%~dp0"

set "CMD="
if exist "dist\main.exe" (
    set "CMD=\"%cd%\dist\main.exe\""
) else if exist "venv\Scripts\python.exe" (
    set "CMD=\"%cd%\venv\Scripts\python.exe\" \"%cd%\main.py\""
) else (
    set "CMD=python \"%cd%\main.py\""
)

echo [watchdog] Starting loop: %CMD%
:loop
call %CMD%
echo [watchdog] Process exited with code %ERRORLEVEL%, restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
