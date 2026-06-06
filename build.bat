@echo off
echo Building VoiceCommands.exe ...

pip install pyinstaller >nul 2>&1

pyinstaller ^
  --onefile ^
  --noconsole ^
  --name VoiceCommands ^
  --add-data "version.txt;." ^
  main.py

echo.
echo Done! Exe is at: dist\VoiceCommands.exe
echo.
echo NEXT STEPS:
echo   1. Go to https://github.com/xXBunchXx/voice-commands/releases/new
echo   2. Set the tag to match version.txt (e.g. v1.0.0)
echo   3. Upload dist\VoiceCommands.exe as a release asset
echo   4. Publish the release
pause
