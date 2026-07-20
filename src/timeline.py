"""タイムライン UI (シークバー兼用)。

- FilmstripBar : 動画編集ソフト風のサムネイル帯
- WaveformBar  : 音声波形 (上半分)
共通基盤 TimelineBar が シーク / プレイヘッド / IN・OUT 表示 / マウス操作を担う。
クリック=シーク, Ctrl+クリック=IN, Alt+クリック=OUT。
重い解析 (サムネイル/波形) は別スレッドで行う。
"""
from __future__ import annotations

import av
import numpy as np
from PySide6.QtCore import Qt, QObject, Signal, QRect
from PySide6.QtGui import QPainter, QPen, QColor, QPixmap, QImage
from PySide6.QtWidgets import QWidget

from i18n import tr


# ======================================================================
#  解析 (別スレッド)
# ======================================================================
def _duration_sec(container, stream):
    if stream.duration is not None:
        return float(stream.duration * stream.time_base)
    if container.duration is not None:
        return container.duration / av.time_base
    return None


def compute_envelope(path: str, buckets: int = 2000):
    """音声のピーク振幅(0..1)配列。音声無しは None。"""
    container = av.open(path)
    try:
        if not container.streams.audio:
            return None
        astream = container.streams.audio[0]
        dur = _duration_sec(container, astream)
        if not dur or dur <= 0:
            dur = None
        resampler = av.AudioResampler(format="flt", layout="mono")
        env = np.zeros(buckets, dtype=np.float32)
        seq = [] if dur is None else None
        for frame in container.decode(astream):
            t = frame.time
            for r in resampler.resample(frame):
                a = r.to_ndarray()
                if a.size == 0:
                    continue
                peak = float(np.abs(a).max())
                if dur is not None and t is not None:
                    b = int(t / dur * buckets)
                    if 0 <= b < buckets and peak > env[b]:
                        env[b] = peak
                else:
                    seq.append(peak)
        if dur is None:
            if not seq:
                return None
            arr = np.asarray(seq, dtype=np.float32)
            env = arr[np.linspace(0, len(arr) - 1, buckets).astype(int)]
        m = float(env.max())
        if m > 0:
            env = env / m
        return env
    finally:
        container.close()


def compute_thumbnails(path: str, count: int = 40, height: int = 64):
    """動画全体から等間隔に count 枚のサムネイル(QImage, 高さ height)を生成。"""
    container = av.open(path)
    try:
        if not container.streams.video:
            return None
        vs = container.streams.video[0]
        vs.thread_type = "AUTO"
        dur = _duration_sec(container, vs)
        tb = float(vs.time_base) if vs.time_base else None
        start = vs.start_time or 0
        thumbs = []
        for i in range(count):
            frac = i / (count - 1) if count > 1 else 0.0
            try:
                if dur and tb:
                    container.seek(int((frac * dur) / tb) + start,
                                   stream=vs, backward=True)
                frame = next(container.decode(vs), None)
            except Exception:
                frame = None
            if frame is None:
                continue
            arr = np.ascontiguousarray(frame.to_ndarray(format="rgb24"))
            h, w, _ = arr.shape
            img = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
            thumbs.append(img.scaledToHeight(height, Qt.SmoothTransformation))
        return thumbs or None
    finally:
        container.close()


class _Worker(QObject):
    done = Signal(str, object)

    def __init__(self, path, fn, *args):
        super().__init__()
        self.path = path
        self._fn = fn
        self._args = args

    def run(self):
        try:
            result = self._fn(self.path, *self._args)
        except Exception:
            result = None
        self.done.emit(self.path, result)


class WaveformWorker(_Worker):
    def __init__(self, path, buckets=2000):
        super().__init__(path, compute_envelope, buckets)


class FilmstripWorker(_Worker):
    def __init__(self, path, count=40, height=64):
        super().__init__(path, compute_thumbnails, count, height)


# ======================================================================
#  表示
# ======================================================================
class TimelineBar(QWidget):
    seekRequested = Signal(int)
    inRequested = Signal(int)
    outRequested = Signal(int)

    LOADING_KEY = ""

    def __init__(self, height: int, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setCursor(Qt.IBeamCursor)   # タイムライン上は I ビーム (位置を合わせやすい)
        self.setStyleSheet("background:#0c0c0e;")
        self._frame = 0
        self._maxframe = 1
        self._in = None
        self._out = None
        self._segments = []      # 確定済みクリップ [(a, b), ...]
        self._selected = None    # 選択中クリップの index
        self._drag_marker = None  # ドラッグ中のマーカー ("in" | "out")
        self._pixmap = None
        self._loading = False

    # --- 外部 API -------------------------------------------------------
    def set_range(self, maxframe: int):
        self._maxframe = max(1, maxframe)
        self._pixmap = None
        self.update()

    def set_position(self, frame: int):
        self._frame = frame
        self.update()

    def set_marks(self, a, b):
        self._in = a
        self._out = b
        self.update()

    def set_segments(self, segments, selected=None):
        self._segments = list(segments)
        self._selected = selected
        self.update()

    def set_loading(self, on: bool):
        self._loading = on
        self.update()

    def clear(self):
        self._frame = 0
        self._segments = []
        self._pixmap = None
        self._loading = False
        self.update()

    def invalidate(self):
        """キャッシュ画像を破棄して再描画 (リサイズ/最大化時の崩れ対策)。"""
        self._pixmap = None
        self.update()

    def resizeEvent(self, event):
        self._pixmap = None     # 新しいサイズで作り直す (古い画像の引き伸ばし防止)
        super().resizeEvent(event)
        self.update()

    # --- 座標 -----------------------------------------------------------
    def _x_of_frame(self, frame: int) -> float:
        return (frame / self._maxframe) * self.width() if self._maxframe else 0.0

    def _frame_at(self, x: float) -> int:
        frac = min(1.0, max(0.0, x / max(1, self.width())))
        return round(frac * self._maxframe)

    # --- 描画 -----------------------------------------------------------
    def _new_pixmap(self) -> QPixmap:
        pm = QPixmap(self.width(), self.height())
        pm.fill(QColor("#0c0c0e"))
        return pm

    def _rebuild(self):
        self._pixmap = self._new_pixmap()   # サブクラスで内容を描く

    def _content_ready(self) -> bool:
        return False

    def paintEvent(self, event):
        if self._pixmap is None or self._pixmap.size() != self.size():
            self._rebuild()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pixmap)
        h = self.height()

        # 確定済みクリップ (黄色の帯 + 番号)。選択中は白枠で強調
        for i, (a, b) in enumerate(self._segments):
            xa = self._x_of_frame(a)
            xb = self._x_of_frame(b)
            sel = (i == self._selected)
            p.fillRect(int(xa), 0, max(2, int(xb - xa)), h,
                       QColor(255, 210, 0, 110 if sel else 70))
            p.setPen(QPen(QColor("#ffffff") if sel
                          else QColor(255, 210, 0, 200), 2 if sel else 1))
            p.drawRect(int(xa), 0, max(2, int(xb - xa)), h - 1)
            if (xb - xa) > 14 and h >= 30:
                p.setPen(QColor("#ffffff") if sel else QColor("#ffd200"))
                p.drawText(int(xa) + 4, 13, str(i + 1))

        # 編集中の IN/OUT 区間の塗り (両方セット時)
        if self._in is not None and self._out is not None and self._out > self._in:
            xa = self._x_of_frame(self._in)
            xb = self._x_of_frame(self._out)
            p.fillRect(int(xa), 0, int(xb - xa), h, QColor(255, 210, 0, 50))
        # IN/OUT の縦線は片方だけでも常に描く (端でも見えるようクランプ)
        p.setPen(QPen(QColor("#ffcc00"), 2))
        for v in (self._in, self._out):
            if v is not None:
                p.drawLine(self._clamp_x(self._x_of_frame(v)), 0,
                           self._clamp_x(self._x_of_frame(v)), h)

        if self._loading and not self._content_ready():
            p.setPen(QColor("#aaa"))
            p.drawText(self.rect(), Qt.AlignCenter, tr(self.LOADING_KEY))

        # プレイヘッド (端でも見えるようクランプ)
        p.setPen(QPen(QColor("#ff4d4d"), 2))
        px = self._clamp_x(self._x_of_frame(self._frame))
        p.drawLine(px, 0, px, h)

    def _clamp_x(self, x: float) -> int:
        return int(max(1, min(self.width() - 1, x)))

    # --- マウス ---------------------------------------------------------
    GRAB = 6   # IN/OUT 縦線の掴み判定 (px)

    def _marker_positions(self):
        """ドラッグ可能な IN/OUT マーカーの位置。クリップ選択中はその境界。"""
        if self._selected is not None and 0 <= self._selected < len(self._segments):
            a, b = self._segments[self._selected]
            return {"in": a, "out": b}
        m = {}
        if self._in is not None:
            m["in"] = self._in
        if self._out is not None:
            m["out"] = self._out
        return m

    def _hit_marker(self, x: float):
        best, bestd = None, self.GRAB + 1
        for name, f in self._marker_positions().items():
            d = abs(self._x_of_frame(f) - x)
            if d <= self.GRAB and d < bestd:
                best, bestd = name, d
        return best

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x = event.position().x()
        mods = event.modifiers()
        if not (mods & (Qt.ControlModifier | Qt.AltModifier)):
            marker = self._hit_marker(x)
            if marker:                       # IN/OUT 縦線を掴んだ → ドラッグ開始
                self._drag_marker = marker
                event.accept()
                return
        f = self._frame_at(x)
        if mods & Qt.ControlModifier:
            self.inRequested.emit(f)
        elif mods & Qt.AltModifier:
            self.outRequested.emit(f)
        else:
            self.seekRequested.emit(f)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_marker and (event.buttons() & Qt.LeftButton):
            f = self._frame_at(event.position().x())
            pos = self._marker_positions()
            if self._drag_marker == "in":    # OUT を追い越さないようクランプ
                other = pos.get("out")
                if other is not None:
                    f = min(f, other - 1)
                self.inRequested.emit(max(0, f))
            else:
                other = pos.get("in")
                if other is not None:
                    f = max(f, other + 1)
                self.outRequested.emit(min(f, self._maxframe))
            event.accept()
            return
        if not event.buttons():              # ホバー: 線の上では左右矢印カーソル
            self.setCursor(Qt.SizeHorCursor
                           if self._hit_marker(event.position().x())
                           else Qt.IBeamCursor)
        if (event.buttons() & Qt.LeftButton) and not (
                event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.seekRequested.emit(self._frame_at(event.position().x()))
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_marker = None
        super().mouseReleaseEvent(event)


class WaveformBar(TimelineBar):
    LOADING_KEY = "timeline_wave"

    def __init__(self, parent=None):
        super().__init__(32, parent)
        self._env = None

    def set_envelope(self, env):
        self._env = env
        self._loading = False
        self._pixmap = None
        self.update()

    def clear(self):
        self._env = None
        super().clear()

    def _content_ready(self):
        return self._env is not None

    def _rebuild(self):
        pm = self._new_pixmap()
        w, h = self.width(), self.height()
        p = QPainter(pm)
        base = h - 1
        p.setPen(QPen(QColor("#23262b"), 1))
        p.drawLine(0, base, w, base)
        if self._env is not None and len(self._env) > 0:
            n = len(self._env)
            p.setPen(QPen(QColor("#3fb6c8"), 1))
            for x in range(w):
                idx = min(n - 1, max(0, int(x / max(1, w) * (n - 1))))
                hh = float(self._env[idx]) * (h - 2)
                p.drawLine(x, base, x, int(base - hh))
        p.end()
        self._pixmap = pm


class FilmstripBar(TimelineBar):
    LOADING_KEY = "timeline_thumb"

    def __init__(self, parent=None):
        super().__init__(64, parent)
        self._thumbs = None

    def set_thumbnails(self, thumbs):
        self._thumbs = thumbs
        self._loading = False
        self._pixmap = None
        self.update()

    def clear(self):
        self._thumbs = None
        super().clear()

    def _content_ready(self):
        return bool(self._thumbs)

    def _rebuild(self):
        pm = self._new_pixmap()
        w, h = self.width(), self.height()
        if self._thumbs:
            p = QPainter(pm)
            n = len(self._thumbs)
            asp = self._thumbs[0].width() / max(1, self._thumbs[0].height())
            tw = max(1, int(h * asp))
            x = 0
            while x < w:
                frac = (x + tw / 2) / w
                idx = min(n - 1, max(0, round(frac * (n - 1))))
                p.drawImage(QRect(x, 0, tw, h), self._thumbs[idx])
                x += tw
            p.setPen(QPen(QColor("#000"), 1))
            x = 0
            while x < w:                      # 仕切り線でフィルム風に
                p.drawLine(x, 0, x, h)
                x += tw
            p.end()
        self._pixmap = pm
