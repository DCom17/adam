' JARVIS app launcher (pinnable). This is the target of the "JARVIS" shortcut.
'
' It runs start-jarvis.ps1 -AppWindow with no console window of its own, so clicking
' the pinned JARVIS orb: starts the server if it isn't running (the server still opens
' its OWN visible window, per Jarvis's transparent design), waits for it, then opens a
' clean chrome-less Edge app window - already signed in. If the server is already up it
' just opens the window. Pinnable because the shortcut's target is wscript.exe.
Option Explicit
Dim fso, shell, here, root, ps1, cmd
Set fso   = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)   ' ...\scripts
root = fso.GetParentFolderName(here)                      ' project root
ps1  = fso.BuildPath(here, "start-jarvis.ps1")
cmd  = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & ps1 & Chr(34) & " -AppWindow"
shell.CurrentDirectory = root
shell.Run cmd, 0, False    ' 0 = hidden launcher (no flash); the server window is spawned visible by the ps1
