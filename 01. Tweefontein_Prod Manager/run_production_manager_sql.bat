@echo off
title Tweefontein Production Manager SQL
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :error
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    if errorlevel 1 goto :error
) else (
    call ".venv\Scripts\activate.bat"
)

python production_manager_sql.py
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo The application could not start.
echo Review the error above, then press any key.
pause >nul
