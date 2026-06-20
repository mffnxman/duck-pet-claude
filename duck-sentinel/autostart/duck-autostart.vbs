' Silent launcher for the duck stack + pet. Runs at login with no console
' window (hence .vbs + WshShell.Run windowStyle=0).
'
' Install:
'   double-click install-autostart.bat
' Uninstall:
'   delete duck-autostart.vbs.lnk from shell:startup

Option Explicit
Dim sh, proc, home, bash, startCmd, petCmd
Set sh = CreateObject("WScript.Shell")
home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
bash = "C:\Program Files\Git\bin\bash.exe"

' Launch the sentinel stack via start.sh (git bash runs it silently).
startCmd = """" & bash & """ -lc ""~/tools/duck-sentinel/start.sh > /tmp/duck-boot.log 2>&1"""
sh.Run startCmd, 0, False

' Give the stack 6s to come up, then launch the pet duck + status panel.
WScript.Sleep 6000
petCmd = """" & bash & """ -lc ""cd ~/tools/claude-pet && pythonw pet.py > /tmp/pet.log 2>&1 &"""
sh.Run petCmd, 0, False

Dim statusCmd
statusCmd = """" & bash & """ -lc ""cd ~/tools/duck-sentinel && pythonw duck_status.py > /tmp/duck_status.log 2>&1 &"""
sh.Run statusCmd, 0, False
