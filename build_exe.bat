@echo off
REM FPS RePlayer 配布用 exe をビルドする
setlocal
set QT_QPA_PLATFORM=
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python

REM 依存とPyInstallerを確認
"%PY%" -m pip install --upgrade pyinstaller >nul 2>&1

REM アイコンがあれば使う (assets\icon.ico)
set ICON=
if exist "%~dp0assets\icon.ico" set ICON=--icon "%~dp0assets\icon.ico" --add-data "%~dp0assets\icon.ico;assets"

REM ビルド (PyAVのffmpeg DLLを同梱)
"%PY%" -m PyInstaller --noconfirm --windowed --name "FPSRePlayer" --collect-all av %ICON% "%~dp0src\app.py"

echo.
echo === 完了 ===
echo dist\FPSRePlayer\ フォルダごと配布してください (中の FPSRePlayer.exe が本体)
endlocal
