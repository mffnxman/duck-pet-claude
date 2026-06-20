@echo off
REM Install the duck auto-launch: copies a shortcut to the Windows Startup
REM folder so the full stack + pet duck come up at login, silently.
REM
REM Uninstall: del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\duck-autostart.lnk"

setlocal
set "VBS=%~dp0duck-autostart.vbs"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LNK=%STARTUP%\duck-autostart.lnk"

if not exist "%VBS%" (
    echo Can't find %VBS%
    exit /b 1
)

REM Use PowerShell to create the .lnk (no external tools)
powershell -NoProfile -Command ^
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%');" ^
    "$s.TargetPath = '%VBS%';" ^
    "$s.WorkingDirectory = '%~dp0';" ^
    "$s.Description = 'Duck Sentinel auto-launch';" ^
    "$s.Save()"

if exist "%LNK%" (
    echo Installed to: %LNK%
    echo The duck stack will auto-launch at your next login.
) else (
    echo Install failed.
    exit /b 1
)
endlocal
