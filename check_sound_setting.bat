@echo off
echo === Checking sound_enabled logs ===
findstr /C:"sound_enabled" logs\app.log
echo.
echo === Current config.json setting ===
findstr /C:"sound_enabled" config.json
pause
