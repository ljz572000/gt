@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" gt_studio.py
) else (
  python gt_studio.py
)

if errorlevel 1 pause
