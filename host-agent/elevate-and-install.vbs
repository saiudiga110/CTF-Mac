Set shell = CreateObject("Shell.Application")
shell.ShellExecute "powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File """ & CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\install.ps1""", "", "runas", 1
