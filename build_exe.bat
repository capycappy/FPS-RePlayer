@echo off
REM Build the FPS RePlayer exe.
REM Output goes OUTSIDE the repo: %USERPROFILE%\app\FPSRePlayer\  (FPSRePlayer.exe directly inside)
setlocal
set QT_QPA_PLATFORM=
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python

REM PyInstaller creates <distpath>\<name>, so distpath is the parent folder
set DISTPATH=%USERPROFILE%\app
set APPDIR=%DISTPATH%\FPSRePlayer
set WORKPATH=%LOCALAPPDATA%\FPSRePlayer\build
set SPECPATH=%LOCALAPPDATA%\FPSRePlayer

REM Make sure PyInstaller is available
"%PY%" -m pip install --upgrade pyinstaller >nul 2>&1

REM Use the icon if present (assets\icon.ico)
set ICON=
if exist "%~dp0assets\icon.ico" set ICON=--icon "%~dp0assets\icon.ico" --add-data "%~dp0assets\icon.ico;assets"

REM Build (--collect-all av bundles the FFmpeg DLLs)
REM Note: --noconfirm replaces %APPDIR% entirely, so keep nothing else in it
"%PY%" -m PyInstaller --noconfirm --windowed --name "FPSRePlayer" --collect-all av %ICON% ^
  --distpath "%DISTPATH%" --workpath "%WORKPATH%" --specpath "%SPECPATH%" "%~dp0src\app.py"

REM --collect-all may copy site-packages __pycache__ dirs whose .pyc files embed
REM the build machine's path (C:\Users\<name>\...). Strip them from the bundle.
for /f "delims=" %%d in ('dir /s /b /ad "%APPDIR%\__pycache__" 2^>nul') do rd /s /q "%%d"

REM Safety check: warn if any file in the bundle still contains the build user's path
findstr /s /m /c:"%USERPROFILE%" "%APPDIR%\*" >nul 2>&1 && (
  echo !!! WARNING: the bundle contains the build machine user path in these files:
  findstr /s /m /c:"%USERPROFILE%" "%APPDIR%\*"
)

echo.
echo === Done ===
echo App: %APPDIR%\FPSRePlayer.exe
echo To publish a release, zip the %APPDIR% folder.
endlocal
