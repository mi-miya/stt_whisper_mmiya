@echo off
cd /d "%~dp0"
echo Installing libraries...
python -m pip install -r requirements.txt
echo.
echo Installation complete!
pause
