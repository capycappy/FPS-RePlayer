"""動画表示ウィジェット。

- アスペクト比維持で中央にレターボックス描画
- カーソル追従の虫めがね (正方形レンズ)
- 縦型書き出し用の 9:16 クロップ枠 (マウスで移動・リサイズ)
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import (QImage, QPainter, QPen, QColor, QPainterPath, QBrush,
                           QPixmap, QCursor)
from PySide6.QtWidgets import QWidget

from i18n import tr


def make_thin_cross_cursor() -> QCursor:
    """1px の細い十字カーソル (中心に少し隙間を空けて狙点を見やすく)。"""
    size = 25
    c = size // 2
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setPen(QPen(QColor(255, 255, 255, 235), 1))
    p.drawLine(c, 0, c, c - 4)
    p.drawLine(c, c + 4, c, size)
    p.drawLine(0, c, c - 4, c)
    p.drawLine(c + 4, c, size, c)
    p.end()
    return QCursor(pm, c, c)

# クロップ枠のアスペクト比 (幅 / 高さ) = 9:16 縦型
CROP_ASPECT = 9.0 / 16.0
HANDLE = 7          # コーナーハンドルの半径(描画)
HIT = 16            # コーナー掴み判定の半径(widget px)
MIN_H = 48          # クロップ枠の最小高さ(ソース px)


class VideoWidget(QWidget):
    cropChanged = Signal(object)   # (x, y, w, h) ソース座標 or None
    zoomChanged = Signal(float)    # 虫めがね倍率
    gesture = Signal(str)          # マウスジェスチャ名 (LeftClick / WheelUp ...)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 270)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background:#101012;")
        self._thin_cursor = make_thin_cross_cursor()
        self.setCursor(self._thin_cursor)   # プレイヤー上は細い十字カーソル

        self._img: QImage | None = None
        self._img_w = 0
        self._img_h = 0

        # 虫めがね (常時カーソル追従。等倍では非表示、Ctrl+ホイールで拡大すると白枠表示)
        self.zoom = 1.0
        self.lens = 460
        self._cursor = QPointF(-1, -1)
        self._inside = False
        # 左クリックのドラッグ判定 (誤ドラッグでは再生切替しない)
        self._press_pos = None
        self._press_moved = False

        # クロップ枠
        self.crop_mode = False
        self.crop_rect = None          # (x, y, w, h) ソース座標
        self._crop_drag = None         # 'move' | 'TL' | 'TR' | 'BL' | 'BR'
        self._drag_src0 = None         # ドラッグ開始時のソース座標
        self._rect0 = None             # ドラッグ開始時の crop_rect

    # ------------------------------------------------------------------
    def set_image(self, img: QImage):
        self._img = img
        self._img_w = img.width()
        self._img_h = img.height()
        self.update()

    # --- クロップ枠 -----------------------------------------------------
    def start_crop(self):
        if not self._img_w:
            return
        if self.crop_rect is None:
            h = self._even(min(self._img_h * 0.9, self._img_w / CROP_ASPECT * 0.9))
            w = self._even(h * CROP_ASPECT)
            x = self._even((self._img_w - w) / 2)
            y = self._even((self._img_h - h) / 2)
            self.crop_rect = (x, y, w, h)
            self.cropChanged.emit(self.crop_rect)
        self.crop_mode = True
        self.update()

    def end_crop(self):
        self.crop_mode = False
        self.setCursor(self._thin_cursor)
        self.update()

    def clear_crop(self):
        self.crop_rect = None
        self.crop_mode = False
        self.cropChanged.emit(None)
        self.update()

    @staticmethod
    def _even(v):
        return int(v) - (int(v) % 2)

    # --- 座標変換 -------------------------------------------------------
    def _disp_geom(self):
        if not self._img_w or not self._img_h:
            return 1.0, 0.0, 0.0
        W, H = self.width(), self.height()
        scale = min(W / self._img_w, H / self._img_h)
        off_x = (W - self._img_w * scale) / 2
        off_y = (H - self._img_h * scale) / 2
        return scale, off_x, off_y

    def _widget_to_src(self, p: QPointF) -> QPointF:
        scale, off_x, off_y = self._disp_geom()
        return QPointF((p.x() - off_x) / scale, (p.y() - off_y) / scale)

    def _src_to_widget(self, x, y) -> QPointF:
        scale, off_x, off_y = self._disp_geom()
        return QPointF(x * scale + off_x, y * scale + off_y)

    def _clamp_src(self, p: QPointF) -> QPointF:
        return QPointF(max(0, min(p.x(), self._img_w)),
                       max(0, min(p.y(), self._img_h)))

    def _crop_corners_widget(self):
        x, y, w, h = self.crop_rect
        return {
            "TL": self._src_to_widget(x, y),
            "TR": self._src_to_widget(x + w, y),
            "BL": self._src_to_widget(x, y + h),
            "BR": self._src_to_widget(x + w, y + h),
        }

    # --- 描画 -----------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101012"))
        if self._img is None:
            painter.setPen(QColor("#888"))
            painter.drawText(self.rect(), Qt.AlignCenter, tr("placeholder"))
            return

        scale, off_x, off_y = self._disp_geom()
        target = QRectF(off_x, off_y, self._img_w * scale, self._img_h * scale)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(target, self._img)

        if self.crop_mode and self.crop_rect:
            self._draw_crop(painter)

        if self._inside and self.zoom > 1.01 and not self.crop_mode:
            self._draw_magnifier(painter)

    def _draw_crop(self, painter: QPainter):
        x, y, w, h = self.crop_rect
        tl = self._src_to_widget(x, y)
        br = self._src_to_widget(x + w, y + h)
        r = QRectF(tl, br)
        # 範囲外を暗く
        painter.setBrush(QBrush(QColor(0, 0, 0, 130)))
        painter.setPen(Qt.NoPen)
        outer = QPainterPath()
        outer.addRect(QRectF(self.rect()))
        inner = QPainterPath()
        inner.addRect(r)
        painter.drawPath(outer.subtracted(inner))
        # 枠
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#ffd200"), 2))
        painter.drawRect(r)
        # コーナーハンドル
        painter.setBrush(QColor("#ffd200"))
        painter.setPen(Qt.NoPen)
        for c in self._crop_corners_widget().values():
            painter.drawRect(QRectF(c.x() - HANDLE, c.y() - HANDLE,
                                    HANDLE * 2, HANDLE * 2))
        # サイズ表示と操作ヒント
        painter.setPen(QColor("#ffd200"))
        painter.drawText(r.adjusted(4, -20, 0, 0), Qt.AlignLeft | Qt.AlignTop,
                         f"{int(w)} x {int(h)} (9:16)")
        painter.drawText(self.rect().adjusted(0, 10, 0, 0),
                         Qt.AlignHCenter | Qt.AlignTop,
                         "枠をドラッグで移動 / 角をドラッグで拡大縮小 → 「この範囲で書き出し」")

    def _draw_magnifier(self, painter: QPainter):
        scale, off_x, off_y = self._disp_geom()
        src_c = self._widget_to_src(self._cursor)
        side = self.lens / (scale * self.zoom)   # 正方形のソース範囲
        src_rect = QRectF(src_c.x() - side / 2, src_c.y() - side / 2, side, side)

        lens = QRectF(self._cursor.x() - self.lens / 2,
                      self._cursor.y() - self.lens / 2,
                      self.lens, self.lens)

        painter.save()
        painter.setClipRect(lens)
        painter.fillRect(lens, QColor("#000"))
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(lens, self._img, src_rect)
        painter.restore()

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.drawRect(lens)
        # 中央の十字は描かない (OSの細い十字カーソルをそのまま使い、拡大中も見た目を統一)
        painter.setPen(QColor("#28c8ff"))
        painter.drawText(lens.adjusted(0, lens.height() + 2, 0, 24),
                         Qt.AlignHCenter | Qt.AlignTop, f"x{self.zoom:.1f}")

    # --- マウス ---------------------------------------------------------
    def mouseMoveEvent(self, event):
        self._cursor = QPointF(event.position())
        self._inside = True
        if self._crop_drag and self.crop_rect:
            self._update_crop_drag(self._widget_to_src(event.position()))
        elif self.crop_mode and self.crop_rect:
            self.setCursor(self._cursor_for(event.position()))
        elif self._press_pos is not None:
            d = event.position() - self._press_pos
            if abs(d.x()) + abs(d.y()) > 5:
                self._press_moved = True
        self.update()

    def leaveEvent(self, event):
        self._inside = False
        self.update()

    def zoom_step(self, delta: float):
        self.zoom = max(1.0, min(20.0, self.zoom + delta))
        self.zoomChanged.emit(self.zoom)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.gesture.emit("MiddleClick")
            event.accept()
            return
        if event.button() == Qt.RightButton:
            self.gesture.emit("RightClick")
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            if self.crop_mode and self.crop_rect:
                self._crop_drag = self._hit_test(event.position())
                self._drag_src0 = self._widget_to_src(event.position())
                self._rect0 = self.crop_rect
                event.accept()
                return
            # 通常時: クリック(=ドラッグでない)で LeftClick ジェスチャ
            self._press_pos = QPointF(event.position())
            self._press_moved = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._crop_drag:
            self._crop_drag = None
            self.cropChanged.emit(self.crop_rect)
            self.update()
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._press_pos is not None:
            moved = self._press_moved
            self._press_pos = None
            self._press_moved = False
            if not moved and not self.crop_mode:
                self.gesture.emit("LeftClick")
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if self.crop_mode:
            event.accept()
            return
        mods = event.modifiers()
        up = event.angleDelta().y() > 0
        prefix = ""
        if mods & Qt.ControlModifier:
            prefix = "Ctrl+"
        elif mods & Qt.ShiftModifier:
            prefix = "Shift+"
        elif mods & Qt.AltModifier:
            prefix = "Alt+"
        self.gesture.emit(prefix + ("WheelUp" if up else "WheelDown"))
        event.accept()

    # --- クロップ操作ロジック -------------------------------------------
    def _hit_test(self, pos: QPointF) -> str:
        corners = self._crop_corners_widget()
        for name, c in corners.items():
            if (abs(c.x() - pos.x()) <= HIT) and (abs(c.y() - pos.y()) <= HIT):
                return name
        x, y, w, h = self.crop_rect
        r = QRectF(self._src_to_widget(x, y), self._src_to_widget(x + w, y + h))
        if r.contains(pos):
            return "move"
        return "move"   # 枠外でも掴んだら移動扱い

    def _cursor_for(self, pos: QPointF):
        hit = self._hit_test(pos)
        if hit in ("TL", "BR"):
            return Qt.SizeFDiagCursor
        if hit in ("TR", "BL"):
            return Qt.SizeBDiagCursor
        return Qt.SizeAllCursor

    def _update_crop_drag(self, cur_src: QPointF):
        x0, y0, w0, h0 = self._rect0
        if self._crop_drag == "move":
            dx = cur_src.x() - self._drag_src0.x()
            dy = cur_src.y() - self._drag_src0.y()
            nx = max(0, min(self._img_w - w0, x0 + dx))
            ny = max(0, min(self._img_h - h0, y0 + dy))
            self.crop_rect = (self._even(nx), self._even(ny), w0, h0)
            return

        # コーナーリサイズ (9:16 維持、対角コーナーを固定)
        is_left = self._crop_drag in ("TL", "BL")
        is_top = self._crop_drag in ("TL", "TR")
        ox = x0 if not is_left else x0 + w0      # 固定する対角の X
        oy = y0 if not is_top else y0 + h0       # 固定する対角の Y

        dx = abs(cur_src.x() - ox)
        dy = abs(cur_src.y() - oy)
        # 高さ基準でアスペクト維持 (縦長なので h が主)
        new_h = max(dy, dx / CROP_ASPECT)
        # 画面内に収まる上限
        max_w = ox if is_left else (self._img_w - ox)
        max_h = oy if is_top else (self._img_h - oy)
        new_h = min(new_h, max_h, max_w / CROP_ASPECT)
        new_h = max(MIN_H, new_h)
        new_w = new_h * CROP_ASPECT

        nx = ox - new_w if is_left else ox
        ny = oy - new_h if is_top else oy
        self.crop_rect = (self._even(nx), self._even(ny),
                          self._even(new_w), self._even(new_h))
