@echo off
echo Building VoiceCommands.exe ...

echo Closing any running VoiceCommands instances...
taskkill /F /IM VoiceCommands.exe >nul 2>&1
timeout /t 3 /nobreak >nul

:: ── Install / upgrade dependencies ────────────────────────────────────────────
echo.
echo Installing dependencies...
pip install --upgrade pyinstaller certifi pystray pillow vosk
if errorlevel 1 (
    echo.
    echo ERROR: pip install failed. Check the output above.
    pause
    exit /b 1
)

:: ── Choose version bump type ──────────────────────────────────────────────────
for /f "tokens=*" %%v in (version.txt) do set CUR_VER=%%v
for /f "tokens=*" %%v in ('powershell -NoProfile -Command "$p=(Get-Content version.txt).Trim()-split[char]46;$p[2]=[int]$p[2]+1;$p-join[char]46"') do set VER_PATCH=%%v
for /f "tokens=*" %%v in ('powershell -NoProfile -Command "$p=(Get-Content version.txt).Trim()-split[char]46;$p[1]=[int]$p[1]+1;$p[2]=0;$p-join[char]46"') do set VER_MINOR=%%v
for /f "tokens=*" %%v in ('powershell -NoProfile -Command "$p=(Get-Content version.txt).Trim()-split[char]46;$p[0]=[int]$p[0]+1;$p[1]=0;$p[2]=0;$p-join[char]46"') do set VER_MAJOR=%%v

echo.
echo Current version: %CUR_VER%
echo.
echo Select update type:
echo   1. Small  ^(patch: %CUR_VER% -^> %VER_PATCH%^)
echo   2. Medium ^(minor: %CUR_VER% -^> %VER_MINOR%^)
echo   3. Large  ^(major: %CUR_VER% -^> %VER_MAJOR%^)
echo.
set /p UPDATE_TYPE="Enter 1, 2, or 3: "

if "%UPDATE_TYPE%"=="1" (
    powershell -NoProfile -Command "$v=(Get-Content version.txt).Trim();$p=$v-split'\.';$p[2]=[int]$p[2]+1;$n=$p-join'.';Set-Content version.txt $n;Write-Host('Patch: '+$v+' -> '+$n)"
) else if "%UPDATE_TYPE%"=="2" (
    powershell -NoProfile -Command "$v=(Get-Content version.txt).Trim();$p=$v-split'\.';$p[1]=[int]$p[1]+1;$p[2]=0;$n=$p-join'.';Set-Content version.txt $n;Write-Host('Minor: '+$v+' -> '+$n)"
) else if "%UPDATE_TYPE%"=="3" (
    powershell -NoProfile -Command "$v=(Get-Content version.txt).Trim();$p=$v-split'\.';$p[0]=[int]$p[0]+1;$p[1]=0;$p[2]=0;$n=$p-join'.';Set-Content version.txt $n;Write-Host('Major: '+$v+' -> '+$n)"
) else (
    echo Invalid choice, defaulting to Small ^(patch^).
    powershell -NoProfile -Command "$v=(Get-Content version.txt).Trim();$p=$v-split'\.';$p[2]=[int]$p[2]+1;$n=$p-join'.';Set-Content version.txt $n;Write-Host('Patch: '+$v+' -> '+$n)"
)

:: ── Build ─────────────────────────────────────────────────────────────────────
echo.
echo Running PyInstaller...
pyinstaller ^
  --onefile ^
  --noconsole ^
  --name VoiceCommands ^
  --add-data "version.txt;." ^
  --collect-all vosk ^
  --collect-all pystray ^
  --hidden-import PIL ^
  --exclude-module _bootlocale ^
  --exclude-module _distutils_hack ^
  main.py

if errorlevel 1 (
    echo.
    echo Build failed! Check the output above for details.
    pause
    exit /b 1
)

echo.
echo Copying Vosk model into dist\ ...
xcopy /E /I /Y "vosk-model-small-en-us-0.15" "dist\vosk-model-small-en-us-0.15"

echo.
echo Zipping dist\ into VoiceCommands.zip ...
powershell -NoProfile -Command "$src = Resolve-Path 'dist'; Add-Type -Assembly System.IO.Compression.FileSystem; if (Test-Path 'VoiceCommands.zip') { Remove-Item 'VoiceCommands.zip' }; [System.IO.Compression.ZipFile]::CreateFromDirectory($src, (Resolve-Path '.').Path + '\VoiceCommands.zip')"

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
