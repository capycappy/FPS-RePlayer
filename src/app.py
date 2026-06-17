"""エントリーポイント。"""
import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from main_window import MainWindow
from version import APP_NAME, APP_VERSION


def resource_path(rel: str) -> str:
    """開発時/PyInstaller同梱時の両方でリソースの絶対パスを返す。"""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:  # 開発時: プロジェクトルート (src の親)
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    icon_path = resource_path(os.path.join("assets", "icon.ico"))
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    win = MainWindow()
    win.show()
    # コマンドライン引数で動画を指定して起動も可能 (ファイル関連付け用)
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        win.load(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
