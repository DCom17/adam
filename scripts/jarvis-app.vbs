' Forwarding shim: the product was renamed Adam, and this launcher is now
' adam-app.vbs. Kept for one release so desktop/taskbar shortcuts created by
' older installs (they target wscript.exe -> this file) keep working after an
' update. add-app-shortcut.ps1 replaces those shortcuts when it is re-run.
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run "wscript.exe """ & fso.BuildPath(here, "adam-app.vbs") & """", 0, False
