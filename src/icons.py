"""ツールバー用の専用アイコン (Tactical セット)。

assets/icons/*.svg を読み込み、色を差し替えて QIcon にする。SVG 内の "#COLOR#" が
描画色に置き換わる。通常 / ホバー(Active) / 無効(Disabled) の3色を1つの QIcon に持たせる。
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

# 配色 (Tactical)
ICON_NORMAL = "#a9d8e2"
ICON_HOVER = "#ffffff"
ICON_DISABLED = "#3f4652"
ICON_ACCENT = "#00e5ff"     # 再生ボタン
ICON_ON_YELLOW = "#111111"  # 黄色いボタンの上


def _icons_dir() -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "icons")


_svg_cache: dict[str, str] = {}


def _svg_text(name: str) -> str:
    if name not in _svg_cache:
        path = os.path.join(_icons_dir(), f"{name}.svg")
        try:
            with open(path, "r", encoding="utf-8") as f:
                _svg_cache[name] = f.read()
        except OSError:
            _svg_cache[name] = ""
    return _svg_cache[name]


def pixmap(name: str, color: str, size: int, dpr: float = 1.0) -> QPixmap:
    """SVG を指定色・サイズで描いた QPixmap (高DPI対応)。"""
    svg = _svg_text(name).replace("#COLOR#", color)
    px = int(round(size * dpr))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    if svg:
        r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        r.render(p, QRectF(0, 0, px, px))
        p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, normal: str = ICON_NORMAL, hover: str = ICON_HOVER,
         disabled: str = ICON_DISABLED, size: int = 20) -> QIcon:
    ic = QIcon()
    for dpr in (1.0, 2.0):
        ic.addPixmap(pixmap(name, normal, size, dpr), QIcon.Normal)
        ic.addPixmap(pixmap(name, hover, size, dpr), QIcon.Active)
        ic.addPixmap(pixmap(name, disabled, size, dpr), QIcon.Disabled)
    return ic
