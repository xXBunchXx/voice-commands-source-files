@echo off
cd /d "%~dp0"

echo Building Echo.exe ...

echo Closing any running Echo instances...
taskkill /F /IM Echo.exe >nul 2>&1
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

:: ── Read current version and pre-calculate all bump options ───────────────────
::
::  Version scheme  A.B.C.D  (left to right):
::    A  every new build, likely buggy            (option 1)
::    B  tested and confirmed stable              (option 2)
::    C  fairly important / significant update    (option 3)
::    D  huge overhaul / milestone release        (option 4)
::
::  Incrementing any number resets all numbers to its left back to 0.
::  Option 0 resets to 1.0.0.0 and forces all users' save files to be wiped.

for /f "tokens=*" %%v in (version.txt) do set CUR_VER=%%v

for /f "tokens=*" %%v in ('powershell -NoProfile -Command "$p=(Get-Content version.txt -Encoding UTF8).Trim()-split[char]46;$p[0]=[int]$p[0]+1;$p-join[char]46"') do set VER_A=%%v
for /f "tokens=*" %%v in ('powershell -NoProfile -Command "$p=(Get-Content version.txt -Encoding UTF8).Trim()-split[char]46;$p[1]=[int]$p[1]+1;$p[0]=0;$p-join[char]46"') do set VER_B=%%v
for /f "tokens=*" %%v in ('powershell -NoProfile -Command "$p=(Get-Content version.txt -Encoding UTF8).Trim()-split[char]46;$p[2]=[int]$p[2]+1;$p[1]=0;$p[0]=0;$p-join[char]46"') do set VER_C=%%v
for /f "tokens=*" %%v in ('powershell -NoProfile -Command "$p=(Get-Content version.txt -Encoding UTF8).Trim()-split[char]46;$p[3]=[int]$p[3]+1;$p[2]=0;$p[1]=0;$p[0]=0;$p-join[char]46"') do set VER_D=%%v

echo.
echo Current version: %CUR_VER%
echo.
echo Select update type:
echo   1. Build     ^(A: %CUR_VER% -^> %VER_A%^)  -- new build, may be buggy
echo   2. Stable    ^(B: %CUR_VER% -^> %VER_B%^)  -- tested and confirmed stable
echo   3. Important ^(C: %CUR_VER% -^> %VER_C%^)  -- fairly significant update
echo   4. Overhaul  ^(D: %CUR_VER% -^> %VER_D%^)  -- huge milestone release
echo   0. RESET     ^(  : %CUR_VER% -^> 1.0.0.0^) -- wipes ALL user save files on next launch
echo.
set /p UPDATE_TYPE="Enter 0, 1, 2, 3, or 4: "

if "%UPDATE_TYPE%"=="0" goto do_reset
if "%UPDATE_TYPE%"=="1" goto do_a
if "%UPDATE_TYPE%"=="2" goto do_b
if "%UPDATE_TYPE%"=="3" goto do_c
if "%UPDATE_TYPE%"=="4" goto do_d
echo Invalid choice, defaulting to Build (A).
goto do_a

:do_reset
echo.
echo WARNING: This will reset the version to 1.0.0.0 and force ALL users'
echo save files to be wiped when they next launch the app.
echo.
set /p CONFIRM="Type YES to confirm reset: "
if /i not "%CONFIRM%"=="YES" (
    echo Reset cancelled.
    pause
    exit /b 0
)
powershell -NoProfile -Command "$e=New-Object System.Text.UTF8Encoding $false;[System.IO.File]::WriteAllText('version.txt','1.0.0.0',$e)"
powershell -NoProfile -Command "$e=New-Object System.Text.UTF8Encoding $false;$f='user_config.py';$c=[System.IO.File]::ReadAllText($f,[System.Text.Encoding]::UTF8);$c=$c -replace 'APP_VERSION\s*=\s*""[^""]*""','APP_VERSION = ""1.0.0.0""';$c=$c -replace 'RESET_BASELINE\s*=\s*\([^)]*\)','RESET_BASELINE = (1, 0, 0, 0)';[System.IO.File]::WriteAllText($f,$c,$e)"
echo   Reset: %CUR_VER% -^> 1.0.0.0  (save wipe armed)
goto build

:do_a
powershell -NoProfile -Command "$e=New-Object System.Text.UTF8Encoding $false;$v=(Get-Content version.txt -Encoding UTF8).Trim();$p=$v-split'\.';$p[0]=[int]$p[0]+1;$n=$p-join'.';[System.IO.File]::WriteAllText('version.txt',$n,$e);Write-Host('Build: '+$v+' -> '+$n)"
call :update_version_in_py
goto build

:do_b
powershell -NoProfile -Command "$e=New-Object System.Text.UTF8Encoding $false;$v=(Get-Content version.txt -Encoding UTF8).Trim();$p=$v-split'\.';$p[1]=[int]$p[1]+1;$p[0]=0;$n=$p-join'.';[System.IO.File]::WriteAllText('version.txt',$n,$e);Write-Host('Stable: '+$v+' -> '+$n)"
call :update_version_in_py
goto build

:do_c
powershell -NoProfile -Command "$e=New-Object System.Text.UTF8Encoding $false;$v=(Get-Content version.txt -Encoding UTF8).Trim();$p=$v-split'\.';$p[2]=[int]$p[2]+1;$p[1]=0;$p[0]=0;$n=$p-join'.';[System.IO.File]::WriteAllText('version.txt',$n,$e);Write-Host('Important: '+$v+' -> '+$n)"
call :update_version_in_py
goto build

:do_d
powershell -NoProfile -Command "$e=New-Object System.Text.UTF8Encoding $false;$v=(Get-Content version.txt -Encoding UTF8).Trim();$p=$v-split'\.';$p[3]=[int]$p[3]+1;$p[2]=0;$p[1]=0;$p[0]=0;$n=$p-join'.';[System.IO.File]::WriteAllText('version.txt',$n,$e);Write-Host('Overhaul: '+$v+' -> '+$n)"
call :update_version_in_py
goto build

:: Subroutine: patch APP_VERSION in user_config.py to match version.txt (UTF-8 no-BOM)
:update_version_in_py
powershell -NoProfile -Command "$e=New-Object System.Text.UTF8Encoding $false;$n=(Get-Content version.txt -Encoding UTF8).Trim();$f='user_config.py';$c=[System.IO.File]::ReadAllText($f,[System.Text.Encoding]::UTF8);$c=$c -replace 'APP_VERSION\s*=\s*""[^""]*""',('APP_VERSION = ""'+$n+'""');[System.IO.File]::WriteAllText($f,$c,$e)"
goto :eof

:: ── Build ─────────────────────────────────────────────────────────────────────
:build
echo.
echo Running PyInstaller...
pyinstaller --onefile --noconsole --name Echo --add-data "version.txt;." --collect-all vosk --collect-all pystray --hidden-import PIL --exclude-module _bootlocale --exclude-module _distutils_hack main.py

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
echo Zipping dist\ into Echo.zip ...
powershell -NoProfile -Command "$src=Resolve-Path 'dist';Add-Type -Assembly System.IO.Compression.FileSystem;if(Test-Path 'Echo.zip'){Remove-Item 'Echo.zip'};[System.IO.Compression.ZipFile]::CreateFromDirectory($src,(Resolve-Path '.').Path+'\Echo.zip')"

for /f "tokens=*" %%v in (version.txt) do set NEW_VER=%%v

:: ── Commit source files only (zip/dist excluded by .gitignore) ────────────────
echo.
echo Committing source to GitHub...
git add version.txt main.py user_config.py .gitignore
git commit -m "Release v%NEW_VER%"
git push
if errorlevel 1 (
    echo.
    echo WARNING: git push failed. Check the output above.
)

:: ── Publish zip as a GitHub Release (supports files up to 2 GB) ───────────────
echo.
echo Creating GitHub Release v%NEW_VER% ...
gh release create "v%NEW_VER%" Echo.zip --title "Echo v%NEW_VER%" --notes "Echo v%NEW_VER%"
if errorlevel 1 (
    echo.
    echo WARNING: gh release failed.
    echo Install GitHub CLI from https://cli.github.com then run:
    echo   gh auth login
    echo   gh release create "v%NEW_VER%" Echo.zip --title "Echo v%NEW_VER%"
)

echo.
echo ============================================================
echo  Built and released v%NEW_VER%
echo  Share this download link with anyone who needs it:
echo  https://github.com/xXBunchXx/Voice-commands/releases/download/v%NEW_VER%/Echo.zip
echo ============================================================
echo.
pause
