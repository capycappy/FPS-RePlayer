@echo off
REM 検証プレイヤー 起動用
setlocal
REM 画面なし(offscreen)モードが万一設定されていてもクリアして通常表示にする
set QT_QPA_PLATFORM=
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
start "" "%PY%" "%~dp0src\app.py" %*
endlocal
