@echo off
echo Building VoiceCommands.exe ...

echo Closing any running VoiceCommands instances...
taskkill /F /IM VoiceCommands.exe >nul 2>&1
timeout /t 1 /nobreak >nul

pip install pyinstaller certifi pystray pillow >nul 2>&1

pyinstaller ^
  --onefile ^
  --noconsole ^
  --name VoiceCommands ^
  --add-data "version.txt;." ^
  --collect-all vosk ^
  --collect-all pystray ^
  --hidden-import PIL ^
  main.py

if errorlevel 1 (
    echo Build failed!
    pause
    exit /b 1
)

echo.
echo Copying Vosk model into dist\ ...
xcopy /E /I /Y "vosk-model-small-en-us-0.15" "dist\vosk-model-small-en-us-0.15"

echo.
echo Zipping dist\ into VoiceCommands.zip ...
powershell -Command "Compress-Archive -Path 'dist\*' -DestinationPath 'VoiceCommands.zip' -Force"

echo.
echo Pushing to GitHub...
git add VoiceCommands.zip dist\VoiceCommands.exe version.txt
git commit -m "Release: update exe and zip"
git push

echo.
echo ============================================================
echo  Done! Share this download link with anyone who needs it:
echo  https://github.com/xXBunchXx/Voice-commands/raw/main/VoiceCommands.zip
echo ============================================================
echo.
pause
