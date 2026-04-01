@echo off
title Claude Pet Duck
cd /d "%~dp0"

:: Check for Python
where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
    start /min pythonw pet.py
) else (
    where python >nul 2>&1
    if %ERRORLEVEL%==0 (
        start /min python pet.py
    ) else (
        echo Python not found! Install Python from https://python.org
        echo Make sure to check "Add Python to PATH" during install.
        pause
        exit /b 1
    )
)
echo Duck launched!
timeout /t 2 >nul
