@echo off
REM Launch Chrome with --remote-debugging-port=9222 in a separate profile so
REM it doesn't fight with your main Chrome. Use this shortcut (or pin it)
REM when you want the duck to see your browser. Your normal Chrome stays
REM untouched.
REM
REM First run: asks for sign-ins since it's a fresh profile. After that,
REM just reuse this shortcut — the duck profile persists at
REM %USERPROFILE%\tools\duck-sentinel\chrome-profile
setlocal
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
    echo Chrome not found in Program Files.
    exit /b 1
)
set "DUCK_PROFILE=%USERPROFILE%\tools\duck-sentinel\chrome-profile"
if not exist "%DUCK_PROFILE%" mkdir "%DUCK_PROFILE%"
start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%DUCK_PROFILE%" %*
endlocal
