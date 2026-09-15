@echo off
REM FPS RePlayer 配布用 exe をビルドする
REM 出力はプロジェクト外 (%USERPROFILE%\app\FPSRePlayer) に置く
setlocal
set QT_QPA_PLATFORM=
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python

set DISTPATH=%USERPROFILE%\app\FPSRePlayer
set WORKPATH=%LOCALAPPDATA%\FPSRePlayer\build
set SPECPATH=%LOCALAPPDATA%\FPSRePlayer

REM 依存とPyInstallerを確認
"%PY%" -m pip install --upgrade pyinstaller >nul 2>&1

REM アイコンがあれば使う (assets\icon.ico)
set ICON=
if exist "%~dp0assets\icon.ico" set ICON=--icon "%~dp0assets\icon.ico" --add-data "%~dp0assets\icon.ico;assets"

REM ビルド (PyAVのffmpeg DLLを同梱)
"%PY%" -m PyInstaller --noconfirm --windowed --name "FPSRePlayer" --collect-all av %ICON% ^
  --distpath "%DISTPATH%" --workpath "%WORKPATH%" --specpath "%SPECPATH%" "%~dp0src\app.py"

REM --collect-all が site-packages の __pycache__ を巻き込むと、pyc にビルド機のパス
REM (C:\Users\<name>\...) が残るので配布物から取り除く
for /f "delims=" %%d in ('dir /s /b /ad "%DISTPATH%\FPSRePlayer\__pycache__" 2^>nul') do rd /s /q "%%d"

REM 念のため配布物にビルド機のユーザーパスが残っていないか確認
findstr /s /m /c:"%USERPROFILE%" "%DISTPATH%\FPSRePlayer\*" >nul 2>&1 && (
  echo !!! 警告: 配布物にユーザーパスが含まれています。上の findstr で該当ファイルを確認してください
  findstr /s /m /c:"%USERPROFILE%" "%DISTPATH%\FPSRePlayer\*"
)

echo.
echo === 完了 ===
echo %DISTPATH%\FPSRePlayer\ フォルダごと配布してください (中の FPSRePlayer.exe が本体)
endlocal
