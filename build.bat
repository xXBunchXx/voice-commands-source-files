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
::    A  huge overhaul / milestone release        (option 1 — most significant)
::    B  fairly important / significant update    (option 2)
::    C  tested and confirmed stable              (option 3)
::    D  every new build, likely buggy            (option 4 — most frequent)
::
::  Incrementing any number resets all numbers to its left back to 0.
::  Option 0 resets to 1.0.0.0 and forces all users' save files to be wiped.

for /f "tokens=*" %%v in (version.txt) do set CUR_VER=%%v

for /f "tokens=*" %%v in ('python -c "p=open('version.txt').read().strip().split('.');p[0]=str(int(p[0])+1);print('.'.join(p))"') do set VER_A=%%v
for /f "tokens=*" %%v in ('python -c "p=open('version.txt').read().strip().split('.');p[1]=str(int(p[1])+1);p[0]='0';print('.'.join(p))"') do set VER_B=%%v
for /f "tokens=*" %%v in ('python -c "p=open('version.txt').read().strip().split('.');p[2]=str(int(p[2])+1);p[1]='0';p[0]='0';print('.'.join(p))"') do set VER_C=%%v
for /f "tokens=*" %%v in ('python -c "p=open('version.txt').read().strip().split('.');p[3]=str(int(p[3])+1);p[2]='0';p[1]='0';p[0]='0';print('.'.join(p))"') do set VER_D=%%v

echo.
echo Current version: %CUR_VER%
echo.
echo Select update type:
echo   1. Overhaul  ^(%CUR_VER% -^> %VER_A%^)  -- huge milestone release
echo   2. Important ^(%CUR_VER% -^> %VER_B%^)  -- fairly significant update
echo   3. Stable    ^(%CUR_VER% -^> %VER_C%^)  -- tested and confirmed stable
echo   4. Build     ^(%CUR_VER% -^> %VER_D%^)  -- new build, may be buggy
echo   0. RESET     ^(%CUR_VER% -^> 1.0.0.0^)  -- wipes ALL user save files on next launch
echo.
set /p UPDATE_TYPE="Enter 0, 1, 2, 3, or 4: "

if "%UPDATE_TYPE%"=="0" goto do_reset
if "%UPDATE_TYPE%"=="1" goto do_a
if "%UPDATE_TYPE%"=="2" goto do_b
if "%UPDATE_TYPE%"=="3" goto do_c
if "%UPDATE_TYPE%"=="4" goto do_d
echo Invalid choice, defaulting to Build.
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
set SKIP_RELEASE=1
set BUILD_TYPE=Reset
python -c "open('version.txt','w',encoding='utf-8').write('1.0.0.0')"
python -c "import re,pathlib;f=pathlib.Path('user_config.py');t=f.read_text(encoding='utf-8');t=re.sub('APP_VERSION\\s*=\\s*'+chr(34)+'[^'+chr(34)+']*'+chr(34),'APP_VERSION = '+chr(34)+'1.0.0.0'+chr(34),t,1);t=re.sub('RESET_BASELINE\\s*=\\s*\\([^)]*\\)','RESET_BASELINE = (1, 0, 0, 0)',t,1);f.write_text(t,encoding='utf-8')"
echo   Reset: %CUR_VER% -^> 1.0.0.0  (save wipe armed)
goto build

:do_a
set BUILD_TYPE=Overhaul
python -c "p=open('version.txt').read().strip().split('.');p[0]=str(int(p[0])+1);v='.'.join(p);open('version.txt','w',encoding='utf-8').write(v);print('Overhaul: %CUR_VER% ->',v)"
call :patch_py
goto build

:do_b
set BUILD_TYPE=Important
python -c "p=open('version.txt').read().strip().split('.');p[1]=str(int(p[1])+1);p[0]='0';v='.'.join(p);open('version.txt','w',encoding='utf-8').write(v);print('Important: %CUR_VER% ->',v)"
call :patch_py
goto build

:do_c
set BUILD_TYPE=Stable
python -c "p=open('version.txt').read().strip().split('.');p[2]=str(int(p[2])+1);p[1]='0';p[0]='0';v='.'.join(p);open('version.txt','w',encoding='utf-8').write(v);print('Stable: %CUR_VER% ->',v)"
call :patch_py
goto build

:do_d
set SKIP_RELEASE=1
set BUILD_TYPE=Build
python -c "p=open('version.txt').read().strip().split('.');p[3]=str(int(p[3])+1);p[2]='0';p[1]='0';p[0]='0';v='.'.join(p);open('version.txt','w',encoding='utf-8').write(v);print('Build: %CUR_VER% ->',v)"
call :patch_py
goto build

:: Subroutine: update APP_VERSION in user_config.py to match version.txt
:patch_py
python -c "import re,pathlib;v=pathlib.Path('version.txt').read_text(encoding='utf-8').strip();f=pathlib.Path('user_config.py');t=f.read_text(encoding='utf-8');t=re.sub('APP_VERSION\\s*=\\s*'+chr(34)+'[^'+chr(34)+']*'+chr(34),'APP_VERSION = '+chr(34)+v+chr(34),t,1);f.write_text(t,encoding='utf-8')"
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
:: Remove dist/ and zip from tracking if they were ever committed (gitignore won't untrack them)
git rm --cached -r dist/ >nul 2>&1
git rm --cached Echo.zip >nul 2>&1
:: Stage all source files
git add version.txt main.py user_config.py voice_controls.py manage_apps.py settings_window.py voice_templates.py .gitignore build.bat
git commit -m "%BUILD_TYPE% v%NEW_VER%"
git push
if errorlevel 1 (
    echo.
    echo WARNING: git push failed. Check the output above.
)

:: ── Publish zip as a GitHub Release (stable/important/overhaul only) ──────────
if "%SKIP_RELEASE%"=="1" (
    echo.
    echo  Skipping GitHub Release ^(Build versions are not published^).
    echo  The zip is ready at: %~dp0Echo.zip
    goto done
)
echo.
echo Creating GitHub Release v%NEW_VER% ...
where gh >nul 2>&1
if errorlevel 1 (
    echo.
    echo  GitHub CLI ^(gh^) is not installed.
    echo  It is needed to upload Echo.zip as a release asset.
    echo.
    echo  1. Download and install from: https://cli.github.com
    echo  2. Run once in a terminal:    gh auth login
    echo  3. Then run this bat again to upload the zip.
    echo.
    echo  The zip is ready at: %~dp0Echo.zip
    start https://cli.github.com
) else (
    gh release create "v%NEW_VER%" Echo.zip --repo xXBunchXx/Voice-commands --title "Echo v%NEW_VER%" --notes "Echo v%NEW_VER%"
    if errorlevel 1 (
        echo.
        echo  Release upload failed. If v%NEW_VER% already exists on GitHub,
        echo  delete it first: gh release delete "v%NEW_VER%" --yes --repo xXBunchXx/Voice-commands
    )
)

:done
echo.
echo ============================================================
echo  Built v%NEW_VER% successfully.
echo  Download link (once gh release is uploaded):
echo  https://github.com/xXBunchXx/Voice-commands/releases/download/v%NEW_VER%/Echo.zip
echo ============================================================
echo.
pause
