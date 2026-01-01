Set WshShell = CreateObject("WScript.Shell")
' Run pythonw from system path
' chr(34) is double quote "
' usage: pythonw -m app.main
WshShell.Run "pythonw -m app.main", 0, False
Set WshShell = Nothing
