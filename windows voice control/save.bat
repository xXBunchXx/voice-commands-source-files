@echo off
:: ── Quick save: commit all changes and push to both GitHub repos ──────────────
cd /d "%~dp0"

:: Collect a short message (optional — press Enter to use timestamp)
set /p MSG="Commit message (press Enter to use timestamp): "
if "%MSG%"=="" (
    for /f "tokens=2-4 delims=/ " %%a in ('date /t') do set D=%%c-%%a-%%b
    for /f "tokens=1-2 delims=: " %%a in ('time /t') do set T=%%a:%%b
    set MSG=backup %D% %T%
)

git add -A
git commit -m "%MSG%"

echo.
echo Pushing to origin (Voice-commands)...
git push origin main

echo.
echo Pushing to backup (voice-commands-source-files)...
git push backup main

if %errorlevel%==0 (
    echo.
    echo  Saved to both repos successfully.
) else (
    echo.
    echo  One or more pushes failed — check your internet connection.
)
pause
