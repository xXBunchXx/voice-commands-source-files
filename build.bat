@echo off
echo Building VoiceCommands.exe ...

echo Closing any running VoiceCommands instances...
taskkill /F /IM VoiceCommands.exe >nul 2>&1
timeout /t 1 /nobreak >nul

pip install pyinstaller certifi pystray pillow >nul 2>&1

:: ── Auto-increment patch version ─────────────────────────────────────────────
echo Auto-incrementing version...
powershell -NoProfile -Command ^
  "$v = (Get-Content version.txt).Trim(); ^
   $parts = $v -split '\.'; ^
   $parts[2] = [int]$parts[2] + 1; ^
   $newv = $parts -join '.'; ^
   Set-Content version.txt $newv; ^
   Write-Host ('Version: ' + $v + ' -> ' + $newv)"

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

:: Read new version for commit message
for /f "tokens=*" %%v in (version.txt) do set NEW_VER=%%v

echo.
echo Pushing to GitHub...
git add VoiceCommands.zip dist\VoiceCommands.exe version.txt main.py
git commit -m "Release v%NEW_VER%"
git push

echo.
echo ============================================================
echo  Built and released v%NEW_VER%
echo  Share this download link with anyone who needs it:
echo  https://github.com/xXBunchXx/Voice-commands/raw/main/VoiceCommands.zip
echo ============================================================
echo.
pause
