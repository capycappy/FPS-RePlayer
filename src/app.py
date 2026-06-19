"""エントリーポイント。単一インスタンス（多重起動しない）。"""
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from main_window import MainWindow
from version import APP_NAME, APP_VERSION

SERVER_NAME = "FPSRePlayer_SingleInstance"


def resource_path(rel: str) -> str:
    """開発時/PyInstaller同梱時の両方でリソースの絶対パスを返す。"""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:  # 開発時: プロジェクトルート (src の親)
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _file_arg() -> str:
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        return os.path.abspath(sys.argv[1])
    return ""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    file_arg = _file_arg()

    # 既に起動中なら、そのインスタンスにファイルパスを渡して自分は終了
    probe = QLocalSocket()
    probe.connectToServer(SERVER_NAME)
    if probe.waitForConnected(300):
        probe.write((file_arg or "\n").encode("utf-8"))
        probe.flush()
        probe.waitForBytesWritten(500)
        probe.disconnectFromServer()
        return 0

    # 自分が最初のインスタンス: ローカルサーバを立てる
    QLocalServer.removeServer(SERVER_NAME)   # クラッシュ後の残骸を掃除
    server = QLocalServer()
    server.listen(SERVER_NAME)

    icon_path = resource_path(os.path.join("assets", "icon.ico"))
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    win = MainWindow()
    win.show()
    if file_arg:
        win.load(file_arg)

    def on_new_connection():
        conn = server.nextPendingConnection()
        if conn is None:
            return
        if conn.waitForReadyRead(500):
            path = bytes(conn.readAll()).decode("utf-8", "ignore").strip()
            if path and os.path.exists(path):
                win.load(path)
        # 既存ウィンドウを前面へ
        win.setWindowState((win.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        win.show()
        win.raise_()
        win.activateWindow()
        conn.disconnectFromServer()

    server.newConnection.connect(on_new_connection)

    rc = app.exec()
    server.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()
