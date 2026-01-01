@echo off
cd /d "%~dp0"

if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

:: Run with python (shows console for logs)
python -m app.main
