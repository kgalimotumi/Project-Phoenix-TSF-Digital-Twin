@echo off
cd /d "%~dp0"
python "%~dp0subsoil_checklist_uploader.py"
if errorlevel 1 pause
