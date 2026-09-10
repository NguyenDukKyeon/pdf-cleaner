@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Dang tao moi truong Python cho PDF_Cleaner...
  py -m venv .venv
  if errorlevel 1 goto :fail
)

echo Dang kiem tra/cai thu vien can thiet...
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 goto :fail

echo Hoan tat. Dang mo PDF_Cleaner...
start "" ".venv\Scripts\pythonw.exe" "desktop_app.py"
exit /b 0

:fail
echo.
echo Khong the cai dat PDF_Cleaner. Kiem tra Python va ket noi mang roi thu lai.
pause
exit /b 1
