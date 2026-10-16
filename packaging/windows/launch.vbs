Option Explicit
Dim shell, files, root, command, argument, result
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
command = """" & root & "\start.cmd" & """"
For Each argument In WScript.Arguments
  If InStr(argument, Chr(34)) > 0 Then WScript.Quit 2
  command = command & " """ & argument & """"
Next
shell.CurrentDirectory = root
result = shell.Run("cmd.exe /d /c """ & command & """", 0, True)
If result <> 0 Then MsgBox "StudyCenter Public konnte nicht starten. Im Programmordner start.cmd ausfuehren; bei einer entfernten Python-Laufzeit start.cmd --update. Persoenliche Daten bleiben erhalten.", 16, "StudyCenter Public"
WScript.Quit result
