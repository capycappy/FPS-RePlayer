"""起動時のアップデート確認 (GitHub Releases API)。

公開リポジトリの最新Releaseのタグを取得し、現在のバージョンより新しければ通知する。
送信するのは GitHub 公開APIへの GET のみ (個人情報は一切送らない)。
設定でOFFにできる。
"""
from __future__ import annotations

import json

from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

RELEASES_API = "https://api.github.com/repos/capycappy/FPS-RePlayer/releases/latest"
RELEASES_PAGE = "https://github.com/capycappy/FPS-RePlayer/releases"


def parse_version(s: str):
    """'v1.2.0' 等をタプル (1,2,0) に。数値以外は無視。"""
    out = []
    for part in str(s).strip().lstrip("vV").split(".")[:4]:
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


class UpdateChecker(QObject):
    available = Signal(str, str)   # (最新バージョン, ダウンロードURL)

    def __init__(self, current_version: str, app_name: str, parent=None):
        super().__init__(parent)
        self.current = current_version
        self.app_name = app_name
        self._mgr = QNetworkAccessManager(self)

    def check(self):
        req = QNetworkRequest(QUrl(RELEASES_API))
        req.setHeader(QNetworkRequest.UserAgentHeader,
                      f"{self.app_name}/{self.current}")
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        reply = self._mgr.get(req)
        reply.finished.connect(lambda: self._done(reply))

    def _done(self, reply):
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                data = json.loads(bytes(reply.readAll()).decode("utf-8", "ignore"))
                tag = data.get("tag_name", "")
                if tag and parse_version(tag) > parse_version(self.current):
                    url = data.get("html_url") or RELEASES_PAGE
                    self.available.emit(str(tag).lstrip("vV"), url)
        except Exception:
            pass
        finally:
            reply.deleteLater()
