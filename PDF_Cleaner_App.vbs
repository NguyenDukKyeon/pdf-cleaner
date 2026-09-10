' PDF_Cleaner_App.vbs
' Launcher chinh: desktop native, khong server, khong browser, khong console Python.

Dim fso, shell, appDir, pyExe, pyConsole, script, checkCmd, rc
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
pyExe = appDir & "\.venv\Scripts\pythonw.exe"
pyConsole = appDir & "\.venv\Scripts\python.exe"
script = appDir & "\desktop_app.py"
shell.CurrentDirectory = appDir

If Not fso.FileExists(pyExe) Or Not fso.FileExists(pyConsole) Then
    shell.Run Chr(34) & appDir & "\run_windows.bat" & Chr(34), 1, False
    WScript.Quit 0
End If

' Kiem tra dependency an, tranh truong hop pythonw fail im lang sau khi cap nhat app.
checkCmd = Chr(34) & pyConsole & Chr(34) & " -c " & Chr(34) & _
    "import webview, fitz, cv2, numpy, PIL, psutil, pdf2image, img2pdf" & Chr(34)
rc = shell.Run(checkCmd, 0, True)
If rc <> 0 Then
    shell.Run Chr(34) & appDir & "\run_windows.bat" & Chr(34), 1, False
    WScript.Quit 0
End If

shell.Run Chr(34) & pyExe & Chr(34) & " " & Chr(34) & script & Chr(34), 0, False
