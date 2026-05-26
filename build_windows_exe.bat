@echo off
setlocal

cd /d "%~dp0"

echo [1/3] Installing packaging tools...
py -m pip install --upgrade pip
py -m pip install pyinstaller openpyxl

echo [2/3] Building Windows executable...
py -m PyInstaller ^
  --onefile ^
  --windowed ^
  --name "SECFinancialScreening" ^
  --hidden-import openpyxl ^
  --hidden-import openpyxl.cell._writer ^
  sec_screening_dialog.py

echo [3/3] Done.
echo.
echo Executable:
echo %CD%\dist\SECFinancialScreening.exe
echo.
pause
