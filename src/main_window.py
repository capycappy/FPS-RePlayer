"""メインウィンドウ。再生/コマ送り/倍速/虫めがね/縦型書き出しを統合。"""
from __future__ import annotations

import os
import subprocess
import time
import queue

from PySide6.QtCore import Qt, QTimer, QThread, QSettings, QEvent, Signal, QSize, QRectF
from PySide6.QtGui import (QImage, QKeySequence, QShortcut, QPainter, QPen, QCursor, QIntValidator,
                           QColor, QPainterPath, QAction, QDesktopServices)
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QLabel, QFileDialog, QMessageBox, QDialog, QComboBox, QCheckBox,
    QDialogButtonBox, QFormLayout, QProgressDialog, QMenu, QGridLayout,
    QScrollArea, QFrame,
)

from reader import VideoReader
from video_widget import VideoWidget
from exporter import ExportWorker, EXPORT_SPEEDS
from audio_player import AudioPlayer
from player_engine import FramePrefetcher
from timeline import FilmstripBar, WaveformBar, FilmstripWorker, WaveformWorker
from shortcuts import InputConfig, ShortcutDialog
from clip_store import ClipStore
import icons
import i18n
from i18n import tr

from version import APP_NAME, APP_VERSION
from updater import UpdateChecker, RELEASES_PAGE

SPEEDS = list(EXPORT_SPEEDS)   # 再生速度と書き出し速度は同じ段階


def ndarray_to_qimage(arr) -> QImage:
    h, w, _ = arr.shape
    return QImage(arr.data, w, h, w * 3, QImage.Format_RGB888)


def fmt_time(sec: float) -> str:
    if sec < 0:
        sec = 0
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:05.2f}"


class LevelVolumeSlider(QWidget):
    """音量スライダー。HUD のレベルメーターのような縦バーの列で 小→大 を表す。"""
    valueChanged = Signal(int)
    BARS = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.BARS * self.BAR_W + (self.BARS - 1) * self.GAP, 24)
        self.setCursor(Qt.PointingHandCursor)
        self._value = 80

    def value(self):
        return self._value

    def setValue(self, v):
        v = max(0, min(100, int(v)))
        if v != self._value:
            self._value = v
            self.valueChanged.emit(v)
        self.update()

    BAR_W = 5
    GAP = 3

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        h = self.height()
        lit = self._value / 100.0 * self.BARS
        h_min, h_max = 4.0, 14.0        # 一番高いバーでもボタンのアイコン (20px) より低く
        for i in range(self.BARS):
            x = i * (self.BAR_W + self.GAP)
            # 高さは等差 (端点は正確に h_min / h_max) → 上端が一直線に並ぶ
            bh = h_min + (h_max - h_min) * i / (self.BARS - 1)
            color = QColor("#00e5ff") if i + 1 <= lit + 0.5 else QColor("#1c2431")
            p.fillRect(QRectF(x, (h + h_max) / 2 - bh, self.BAR_W, bh), color)   # 下端揃え・全体は上下中央

    def _set_from_x(self, x):
        self.setValue(round(x / max(1, self.width()) * 100))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._set_from_x(event.position().x())
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._set_from_x(event.position().x())
            event.accept()

    def wheelEvent(self, event):
        self.setValue(self._value + (5 if event.angleDelta().y() > 0 else -5))
        event.accept()


class FrameField(QLineEdit):
    """クリップの IN/OUT フレーム番号。クリックして直接入力、または上下にドラッグして増減。"""
    valueChanged = Signal(int)
    PX_PER_FRAME = 2      # 何 px のドラッグで 1 フレーム動くか

    def __init__(self, value: int, lo: int, hi: int, parent=None):
        super().__init__(str(int(value)), parent)
        self._lo, self._hi = int(lo), int(hi)
        self._v0 = int(value)
        self._press = None
        self._dragging = False
        self._had_focus = False
        self.setValidator(QIntValidator(0, 10 ** 9, self))
        self.setAlignment(Qt.AlignCenter)
        self.setFixedWidth(44)
        self.setCursor(Qt.SizeVerCursor)
        self.setToolTip(tr("tip_frame_field"))
        self.editingFinished.connect(self._commit_text)

    def set_range(self, lo: int, hi: int):
        self._lo, self._hi = int(lo), int(hi)

    def value(self) -> int:
        try:
            return int(self.text())
        except ValueError:
            return self._v0

    def _set(self, v: int):
        v = max(self._lo, min(self._hi, int(v)))
        if v != self._v0:
            self._v0 = v
            self.setText(str(v))
            self.valueChanged.emit(v)

    def _commit_text(self):
        self._set(self.value())
        self.setText(str(self._v0))
        self.clearFocus()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 押した位置を覚えておき、上下に動いたらドラッグ増減、動かさなければ通常のクリック
            self._press = (event.position().y(), self._v0)
            self._dragging = False
            self._had_focus = self.hasFocus()
            if self._had_focus:
                super().mousePressEvent(event)   # フォーカス中はカーソル移動も同時に行う
            else:
                event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None and (event.buttons() & Qt.LeftButton):
            dy = self._press[0] - event.position().y()
            if self._dragging or abs(dy) > 3:
                if not self._dragging:
                    self._dragging = True
                    self.setCursor(Qt.SizeVerCursor)
                    self.deselect()
                self._set(self._press[1] + int(dy / self.PX_PER_FRAME))
                event.accept()
                return
            if self._had_focus:
                super().mouseMoveEvent(event)    # 横方向の動きは範囲選択
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._press is not None and event.button() == Qt.LeftButton:
            if self._dragging:                    # ドラッグで確定 → 編集状態は解除
                self.clearFocus()
                self.setCursor(Qt.SizeVerCursor)
            elif not self._had_focus:             # 未フォーカスのクリック → 入力モード (全選択)
                self.setFocus(Qt.MouseFocusReason)
                self.selectAll()
                self.setCursor(Qt.IBeamCursor)
            else:
                super().mouseReleaseEvent(event)  # フォーカス中のクリック → カーソル移動
            self._press = None
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def focusOutEvent(self, event):
        self.setCursor(Qt.SizeVerCursor)          # 編集を終えたらドラッグ用カーソルに戻す
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape):
            self._commit_text()
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        self._set(self._v0 + (1 if event.angleDelta().y() > 0 else -1))
        event.accept()


class ToolbarSep(QWidget):
    """ツールバーの区切り線 (スタイルシートに頼らず自前で描く)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(9, 24)

    def paintEvent(self, event):
        p = QPainter(self)
        x = self.width() // 2
        p.setPen(QPen(QColor(0, 229, 255, 60), 1))
        p.drawLine(x, 1, x, self.height() - 2)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  v{APP_VERSION}")
        self.resize(1100, 760)
        self._update_url = RELEASES_PAGE
        self.updater = None
        self.setAcceptDrops(True)
        self.settings = QSettings("Claude_Movieplayer", "FPSRePlayer")
        self._activated_ts = 0.0   # 直近にウィンドウがアクティブ化された時刻
        self.lang_pref = self.settings.value("language", "auto", str)
        i18n.set_lang(i18n.resolve(self.lang_pref))
        self.input_cfg = InputConfig()
        self.input_cfg.load(self.settings)
        self._shortcuts = []
        self._mouse_map = {}
        self.recent = self._load_recent()
        self.clip_store = ClipStore()   # 動画ごとの IN/OUT・クリップを永続化

        self.reader: VideoReader | None = None
        self.audio: AudioPlayer | None = None
        self.producer: FramePrefetcher | None = None
        self.wave_thread = None
        self.wave_worker = None
        self.film_thread = None
        self.film_worker = None
        self.cur_index = 0
        self.segments = []            # 確定済みクリップ [(in, out, speed), ...] 常に時系列順
        self.selected_clip = None     # 選択中クリップの index (IN/OUTで修正対象)
        self.preview_segs = None      # プレビュー再生中のクリップ一覧 (None=通常再生)
        self._preview_saved_speed = None  # プレビュー前の再生速度 (終了時に戻す)
        self.preview_idx = 0
        self._clear_backup = None     # 全クリアの取り消し用バックアップ
        self.speed_idx = SPEEDS.index(1.0)
        self.playing = False
        self._play_t0 = 0.0
        self._play_frame0 = 0
        self._pending = None   # 表示時刻待ちの先読みフレーム
        self._stop_at = None   # クリップ単体再生の停止位置 (OUT)
        self._playing_clip = None      # 行の ▶ で再生中のクリップ index
        self._clip_saved_speed = None  # その再生前の速度 (停止時に戻す)
        self.in_frame = None
        self.out_frame = None
        self.volume = float(self.settings.value("volume", 0.8, float))
        self._hold_arr = None  # QImage バッファの寿命確保

        self.video = VideoWidget()
        self.video.cropChanged.connect(self._on_crop)
        self.video.zoomChanged.connect(self._on_zoom)
        self.video.gesture.connect(self._on_gesture)
        self.video.contextMenuRequested.connect(self._show_context_menu)

        self.play_timer = QTimer(self)
        self.play_timer.setTimerType(Qt.PreciseTimer)
        self.play_timer.timeout.connect(self._advance)

        self._build_ui()
        self._apply_bindings()
        self._undo_sc = QShortcut(QKeySequence("Ctrl+Z"), self)     # 全クリアの取り消し
        self._undo_sc.activated.connect(self._undo_clear)
        self._update_labels()
        self._start_update_check()

    # ------------------------------------------------------------------
    STYLE = """
    QMainWindow, QWidget#central { background: #0b0c0f; }
    QLabel { color: #cfd3dc; }
    QToolTip { color: #d9f7ff; background: #0d1118; border: 1px solid #00e5ff66; }

    /* タイムライン下のツールバー (中央寄せ・固定) */
    QWidget#bar { background: #0b0c0f; border-top: 1px solid #1b1e25; }
    QWidget#pill { background: transparent; }
    QWidget#pill QPushButton { background: transparent; border: 1px solid transparent; border-radius: 6px;
                               min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; }
    QWidget#pill QPushButton:hover { background: #121722; border: 1px solid #00e5ff40; }
    QWidget#pill QPushButton:pressed { background: #0d1118; border: 1px solid #00e5ff99; }
    QWidget#pill QPushButton#play { border: 1px solid #00e5ff99; background: #0d1118;
                                    min-width: 46px; max-width: 46px; min-height: 40px; max-height: 40px; }
    QWidget#pill QPushButton#play:hover { border: 1px solid #00e5ff; background: #101826; }
    QWidget#pill QPushButton#play:pressed { background: #00e5ff; }
    QWidget#pill QPushButton#play:disabled { border: 1px solid #263038; background: #0d1118; }
    QWidget#pill QPushButton#export { border: none; background: #f5c400; color: #111111; min-width: 0;
                                      max-width: 1000px; padding: 0 12px 0 8px; font-size: 12px; font-weight: 700; }
    QWidget#pill QPushButton#export:hover { border: none; background: #ffd633; }
    QWidget#pill QPushButton#export[class="text"] { min-width: 96px; }
    QWidget#pill QPushButton#export:disabled { border: none; background: #3a3620; color: #7a7040; }
    QWidget#pill QPushButton#export_cancel:hover { border: 1px solid #ff4d4d; background: #2a1216; }
    QWidget#pill QPushButton#speed { border: none; min-width: 40px; max-width: 40px; padding: 0 6px;
                                     font-size: 12px; font-weight: 700; color: #00e5ff;
                                     font-family: Consolas, "Cascadia Mono", monospace; border-radius: 6px; }
    QWidget#pill QPushButton#speed:hover { background: #121722; }
    QWidget#pill QPushButton#speed::menu-indicator { image: none; width: 0; }
    QWidget#pill QLabel { color: #ffffff; font-size: 12px; font-weight: 600; }
    QWidget#pill QLabel[class="dim"] { color: #6fb9c8; font-weight: 500;
                                       font-family: Consolas, "Cascadia Mono", monospace; }
    QWidget#pill QFrame#sep { background: #00e5ff33; max-width: 1px; min-width: 1px;
                              min-height: 22px; max-height: 22px; }
    QMenu { background: #0d1118; color: #d9f7ff; border: 1px solid #00e5ff66; padding: 4px; }
    QMenu::item { padding: 4px 18px; border-radius: 3px; }
    QMenu::item:selected { background: #00e5ff; color: #07070c; }

    /* 映像左上の数値表示 / 右上の設定 */
    QLabel#hud { color: #9defff; background: rgba(7,7,12,160); border: 1px solid #00e5ff33; border-radius: 4px;
                 padding: 4px 8px; font-family: Consolas, "Cascadia Mono", monospace; font-size: 12px; }
    QPushButton#corner { background: rgba(7,7,12,160); border: 1px solid #00e5ff33; border-radius: 4px;
                         min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; }
    QPushButton#corner:hover { border: 1px solid #00e5ff; background: #121722; }
    QPushButton#update { color: #0c0c0e; background: #ffd200; font-weight: bold;
                         border: none; border-radius: 4px; padding: 0 10px; min-height: 28px; }

    /* 右のクリップ管理パネル */
    QWidget#side { background: #0e1117; border-left: 1px solid #1b2230; }
    QWidget#side QLabel[class="lab"] { color: #6fb9c8; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
    QWidget#side QLabel[class="range"] { color: #c9ceda; font-family: Consolas, "Cascadia Mono", monospace; font-size: 11px; }
    QWidget#side QLabel[class="hint"] { color: #f5c400; font-size: 11px; }
    QWidget#clipRow { background: #141924; border: 1px solid #141924; border-radius: 4px; }
    QWidget#clipRow:hover { border: 1px solid #00e5ff55; }
    QWidget#clipRow[selected="true"] { background: #2a2712; border: 1px solid #f5c400; }
    QWidget#clipRow QLabel { color: #c9ceda; font-family: Consolas, "Cascadia Mono", monospace; font-size: 11px; }
    QWidget#clipRow QLineEdit { color: #d9f7ff; background: #0d1118; border: 1px solid #22304a; border-radius: 3px;
                                padding: 1px 2px; font-family: Consolas, "Cascadia Mono", monospace; font-size: 11px;
                                selection-background-color: #00e5ff; selection-color: #07070c; }
    QWidget#clipRow QLineEdit:hover { border: 1px solid #00e5ff66; }
    QWidget#clipRow QLineEdit:focus { border: 1px solid #00e5ff; }
    QWidget#clipRow QPushButton#rowplay { background: #0d1118; border: 1px solid #00e5ff66; border-radius: 3px;
                                          min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; }
    QWidget#clipRow QPushButton#rowplay:hover { background: #00e5ff; border-color: #00e5ff; }
    QWidget#clipRow QPushButton#rowspeed { color: #f5c400; background: rgba(245,196,0,34); border: none;
                                           border-radius: 3px; padding: 2px 0; min-width: 40px; max-width: 40px;
                                           min-height: 18px; max-height: 18px; font-size: 10px; font-weight: 700;
                                           font-family: Consolas, "Cascadia Mono", monospace; }
    QWidget#clipRow QPushButton#rowspeed:hover { background: rgba(245,196,0,70); }
    QWidget#clipRow QPushButton#rowspeed::menu-indicator { image: none; width: 0; }
    QWidget#side QPushButton { color: #d9f7ff; background: #141924; border: 1px solid #22304a;
                               border-radius: 4px; min-height: 28px; padding: 0 10px; font-size: 12px; }
    QWidget#side QPushButton:hover { background: #1a2233; border-color: #00e5ff66; }
    QWidget#side QPushButton:disabled { color: #4a5563; }
    QWidget#side QPushButton#preview { background: #0d1118; color: #00e5ff; border: 1px solid #00e5ff99; font-weight: 700; }
    QWidget#side QPushButton#preview:hover { background: #101826; border-color: #00e5ff; }
    QWidget#side QPushButton#preview[active="true"] { background: #00e5ff; color: #07070c; }
    QPushButton#sidetab { background: #0e1117; border: none; border-left: 1px solid #1b2230; border-radius: 0;
                          color: #f5c400; font-size: 10px; font-weight: 700; padding: 0; }
    QPushButton#sidetab:hover { background: #141924; }
    QWidget#side QScrollArea { border: none; background: transparent; }
    QWidget#side QScrollArea > QWidget > QWidget { background: transparent; }
    """

    SIDE_W = 268

    def _build_ui(self):
        self.setStyleSheet(self.STYLE)
        central = QWidget()
        central.setObjectName("central")
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        left = QWidget()
        root = QVBoxLayout(left)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(0)
        root.addWidget(self.video, 1)

        # タイムライン (シークバー兼用): サムネイル帯 + 音声波形
        # クリック=シーク / Ctrl+クリック=IN / Alt+クリック=OUT
        # 2本のバーは隙間ゼロで密着させ、IN/OUT・再生線が1本につながって見えるように
        self.filmstrip = FilmstripBar()
        self.waveform = WaveformBar()
        tl_box = QVBoxLayout()
        tl_box.setSpacing(0)
        tl_box.setContentsMargins(0, 0, 0, 0)
        for bar in (self.filmstrip, self.waveform):
            bar.wheelEvent = lambda ev: (          # タイムライン上のホイールでも速度を上下
                self.change_speed(1 if ev.angleDelta().y() > 0 else -1), ev.accept())
            bar.seekRequested.connect(self._on_seek)
            bar.inRequested.connect(self.set_in_at)
            bar.outRequested.connect(self.set_out_at)
            bar.addClipRequested.connect(self.add_segment)
            bar.clearRangeRequested.connect(self._clear_float_range)
            bar.removeClipRequested.connect(self.remove_clip)
            bar.segEdgeMoved.connect(self._on_seg_edge_moved)
            bar.dragFinished.connect(self._on_seg_drag_finished)
            tl_box.addWidget(bar)
        root.addLayout(tl_box)

        self._build_overlays()
        self._build_toolbar()
        root.addWidget(self.bar)
        outer.addWidget(left, 1)

        self._build_sidebar()
        outer.addWidget(self.side)
        self._build_side_tab()
        outer.addWidget(self.side_tab)

        self.setCentralWidget(central)
        self._set_controls_enabled(False)
        QTimer.singleShot(0, self._lock_min_width)

    def _lock_min_width(self):
        """ツールバーの全ボタン (書き出しモードの分も含む) が収まる幅より狭くできないようにする。"""
        lay = self.pill.layout()
        total = lay.contentsMargins().left() + lay.contentsMargins().right()
        n = 0
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is None or w is self.btn_export:      # 通常時の書き出しボタンは ok/cancel より短い
                continue
            total += w.sizeHint().width()
            n += 1
        total += lay.spacing() * max(0, n - 1)
        self.setMinimumWidth(total + self.SIDE_W + 40)

    # --- タイムライン下のツールバー (中央寄せ) -----------------------------
    def _build_toolbar(self):
        self.bar = QWidget()
        self.bar.setObjectName("bar")
        self.bar.setAttribute(Qt.WA_StyledBackground, True)
        outer = QHBoxLayout(self.bar)
        outer.setContentsMargins(0, 6, 0, 4)
        self.pill = QWidget()
        self.pill.setObjectName("pill")
        outer.addStretch(1)
        outer.addWidget(self.pill)
        outer.addStretch(1)
        lay = QHBoxLayout(self.pill)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(4)

        self.btn_open = self._pill_button("open", tr("tip_open"), self.open_file)
        self.btn_prev = self._pill_button("step_back", tr("tip_prev"), self.prev_frame, repeat=True)
        self._icon_play = icons.icon("play", icons.ICON_ACCENT, icons.ICON_ACCENT, size=22)
        self._icon_pause = icons.icon("pause", icons.ICON_ACCENT, icons.ICON_ACCENT, size=22)
        self.btn_play = self._pill_button(None, tr("tip_play"), self.toggle_play)
        self.btn_play.setObjectName("play")
        self.btn_play.setIcon(self._icon_play)
        self.btn_play.setIconSize(QSize(22, 22))
        self.btn_next = self._pill_button("step_fwd", tr("tip_next"), self.next_frame, repeat=True)
        self.btn_slow = self._pill_button("slower", tr("tip_slower"), lambda: self.change_speed(-1))
        self.lbl_speed = QPushButton("1x")           # クリックで速度を直接選ぶ / ホイールで上下
        self.lbl_speed.setObjectName("speed")
        self.lbl_speed.setToolTip(tr("tip_speed_pick"))
        self.lbl_speed.setCursor(Qt.PointingHandCursor)
        # 幅はスタイルシート側で固定 (min-width = max-width) → "0.25x" でも "8x" でも変わらない
        self.lbl_speed.clicked.connect(self._pick_speed)
        self.lbl_speed.wheelEvent = lambda ev: (
            self.change_speed(1 if ev.angleDelta().y() > 0 else -1), ev.accept())
        self.btn_fast = self._pill_button("faster", tr("tip_faster"), lambda: self.change_speed(1))
        self.vol_slider = LevelVolumeSlider()
        self.vol_slider.setToolTip(tr("tip_volume"))
        self.vol_slider.setValue(int(self.volume * 100))
        self.vol_slider.valueChanged.connect(self._on_volume)
        self.lbl_vol = QLabel(f"{int(self.volume * 100)}%")
        self.lbl_vol.setProperty("class", "dim")
        self.lbl_vol.setMinimumWidth(34)
        self.lbl_vol.setAlignment(Qt.AlignCenter)
        self.btn_in = self._pill_button("in", tr("tip_in"), self.set_in)
        self.btn_out = self._pill_button("out", tr("tip_out"), self.set_out)
        self.btn_export = self._pill_button(None, tr("btn_export"), self.begin_export, text=True)
        self.btn_export.setText(tr("btn_export"))
        self.btn_export.setIcon(icons.icon("export", icons.ICON_ON_YELLOW, icons.ICON_ON_YELLOW,
                                           "#7a7040", size=18))
        self.btn_export.setIconSize(QSize(18, 18))
        self.btn_export.setObjectName("export")
        self.btn_export_ok = self._pill_button(None, tr("tip_export_ok"),
                                               self.confirm_export, text=True)
        self.btn_export_ok.setText(tr("btn_export_ok"))
        self.btn_export_ok.setIcon(icons.icon("export", icons.ICON_ON_YELLOW, icons.ICON_ON_YELLOW,
                                              "#7a7040", size=18))
        self.btn_export_ok.setIconSize(QSize(18, 18))
        self.btn_export_ok.setObjectName("export")
        self.btn_export_ok.setVisible(False)
        self.btn_export_cancel = self._pill_button(None, tr("tip_export_cancel"),
                                                   self.cancel_export)     # 他のアイコンボタンと同じ形
        self.btn_export_cancel.setIcon(icons.icon("clear", size=16))
        self.btn_export_cancel.setIconSize(QSize(16, 16))
        self.btn_export_cancel.setObjectName("export_cancel")
        self.btn_export_cancel.setVisible(False)
        self.export_orient = "v"            # "v" = 9:16 縦型 / "h" = 16:9 横型

        for w in (self.btn_open, self._sep(), self.btn_prev, self.btn_play, self.btn_next,
                  self._sep(), self.btn_slow, self.lbl_speed, self.btn_fast,
                  self._sep(), self.vol_slider, self.lbl_vol,
                  self._sep(), self.btn_in, self.btn_out,
                  self._sep(), self.btn_export, self.btn_export_ok, self.btn_export_cancel):
            lay.addWidget(w)

    # --- 映像の上のオーバーレイ (数値表示 / 設定) --------------------------
    def _build_overlays(self):
        v = self.video
        # 左上: フレーム/時刻/fps/拡大率  右上: 更新通知 + 設定
        self.lbl_frame = QLabel("- / -", v)
        self.lbl_frame.setObjectName("hud")
        self._zoom = 1.0
        self.btn_update = QPushButton("", v)   # アップデートありのときだけ表示
        self.btn_update.setObjectName("update")
        self.btn_update.setVisible(False)
        self.btn_update.setToolTip(tr("tip_update"))
        self.btn_update.clicked.connect(self._open_update)
        self.btn_settings = QPushButton(v)
        self.btn_settings.setIcon(icons.icon("gear"))
        self.btn_settings.setIconSize(QSize(18, 18))
        self.btn_settings.setObjectName("corner")
        self.btn_settings.setToolTip(tr("tip_settings"))
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.clicked.connect(self._open_shortcuts)
        for w in (self.lbl_frame, self.btn_update, self.btn_settings):
            w.raise_()
        v.installEventFilter(self)

    def _pick_speed(self):
        """再生速度をメニューから直接選ぶ。"""
        menu = QMenu(self)
        for i, s in enumerate(SPEEDS):
            act = menu.addAction(f"{s:g}x")
            act.setData(i)
        act = menu.exec(self.lbl_speed.mapToGlobal(self.lbl_speed.rect().topLeft()))
        if act is not None:
            self.speed_idx = act.data()
            self._update_labels()
            if self.playing:
                self._rebase_clock()
                self._restart_timer()
                self._sync_audio()

    def _sep(self):
        return ToolbarSep(self.pill)

    def _pill_button(self, icon_name, tooltip, slot, repeat=False, text=False):
        b = QPushButton()
        b.setToolTip(tooltip)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(slot)
        if icon_name:
            b.setIcon(icons.icon(icon_name))
            b.setIconSize(QSize(20, 20))
        if text:
            b.setProperty("class", "text")
        if repeat:
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(300)
            b.setAutoRepeatInterval(55)
        return b

    def eventFilter(self, obj, event):
        if obj is self.video and event.type() == QEvent.Resize:
            self._relayout_overlays()
        return super().eventFilter(obj, event)

    def _relayout_overlays(self):
        v = self.video
        self.lbl_frame.adjustSize()
        self.lbl_frame.move(12, 10)
        self.btn_settings.move(v.width() - self.btn_settings.width() - 12, 10)
        self.btn_update.adjustSize()
        self.btn_update.move(v.width() - self.btn_settings.width() - 12
                             - self.btn_update.width() - 6, 10)

    # --- 右のクリップ管理パネル -------------------------------------------
    def _build_sidebar(self):
        self.side = QWidget()
        self.side.setObjectName("side")
        self.side.setFixedWidth(self.SIDE_W)
        lay = QVBoxLayout(self.side)
        lay.setContentsMargins(12, 14, 12, 12)
        lay.setSpacing(10)

        self.lbl_clips_head = QLabel("CLIPS")
        self.lbl_clips_head.setProperty("class", "lab")
        lay.addWidget(self.lbl_clips_head)
        self.lbl_range = QLabel("")
        self.lbl_range.setProperty("class", "range")
        self.lbl_range.setWordWrap(True)
        self.lbl_range.setFixedHeight(34)          # 2行ぶんを常に確保 (行の位置が動かない)
        self.lbl_range.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(self.lbl_range)

        self.clip_scroll = QScrollArea()
        self.clip_scroll.setWidgetResizable(True)
        self.clip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.clip_list = QWidget()
        self.clip_list_lay = QVBoxLayout(self.clip_list)
        self.clip_list_lay.setContentsMargins(0, 0, 0, 0)
        self.clip_list_lay.setSpacing(4)
        self.clip_list_lay.addStretch(1)
        self.clip_scroll.setWidget(self.clip_list)
        # 何もない所をクリック → 選択解除 (新しいクリップを作れるように)
        self.clip_list.mousePressEvent = lambda ev: self._deselect_clip()
        self.side.mousePressEvent = lambda ev: self._deselect_clip()
        lay.addWidget(self.clip_scroll, 1)

        self.btn_preview = QPushButton(tr("btn_preview"))
        self.btn_preview.setIcon(icons.icon("preview", icons.ICON_ACCENT, icons.ICON_ACCENT, size=16))
        self.btn_preview.setIconSize(QSize(16, 16))
        self.btn_preview.setObjectName("preview")
        self.btn_preview.setToolTip(tr("tip_preview"))
        self.btn_preview.setCursor(Qt.PointingHandCursor)
        self.btn_preview.clicked.connect(self.toggle_preview)
        lay.addWidget(self.btn_preview)
        self.btn_clear_range = QPushButton(tr("btn_clear"))
        self.btn_clear_range.setIcon(icons.icon("clear", size=14))
        self.btn_clear_range.setIconSize(QSize(14, 14))
        self.btn_clear_range.setToolTip(tr("tip_clear"))
        self.btn_clear_range.setCursor(Qt.PointingHandCursor)
        self.btn_clear_range.clicked.connect(self.on_clear_clicked)
        lay.addWidget(self.btn_clear_range)
        self.lbl_out_len = QLabel("")
        self.lbl_out_len.setProperty("class", "range")
        lay.addWidget(self.lbl_out_len)
        self.side.setVisible(False)

    def _build_side_tab(self):
        """パネルの右端に常に見える細いタブ。クリックで開閉、閉じているときはクリップ数を表示。"""
        self.side_tab = QPushButton()
        self.side_tab.setObjectName("sidetab")
        self.side_tab.setCursor(Qt.PointingHandCursor)
        self.side_tab.setFixedWidth(18)
        self.side_tab.setToolTip(tr("tip_side_toggle"))
        self.side_tab.clicked.connect(self.toggle_side)
        self.side_open = bool(self.settings.value("side_open", True, bool))
        self._refresh_side_tab()

    def toggle_side(self):
        self.side_open = not self.side_open
        self.settings.setValue("side_open", self.side_open)
        self._refresh_clip_panel()

    def _refresh_side_tab(self):
        n = len(self.segments)
        self.side_tab.setIcon(icons.icon("panel_close" if self.side_open else "panel_open", size=14))
        self.side_tab.setIconSize(QSize(14, 14))
        self.side_tab.setText("" if self.side_open or not n else str(n))
        self.side_tab.setVisible(self.reader is not None)

    def _refresh_clip_panel(self):
        """クリップ一覧を作り直し、パネルは開閉状態に従って出す。"""
        self.side.setVisible(self.side_open and self.reader is not None)
        self._refresh_side_tab()
        self.lbl_clips_head.setText(f"CLIPS · {len(self.segments)}" if self.segments else "CLIPS")
        # 既存の行を捨てる (末尾の stretch は残す)
        while self.clip_list_lay.count() > 1:
            item = self.clip_list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, (a, b, sp) in enumerate(self.segments):
            self.clip_list_lay.insertWidget(i, self._make_clip_row(i, a, b, sp))
        self.btn_preview.setEnabled(bool(self.segments) or
                                    (self.in_frame is not None and self.out_frame is not None))
        self.btn_preview.setProperty("active", self.preview_segs is not None)
        self.btn_preview.style().unpolish(self.btn_preview)
        self.btn_preview.style().polish(self.btn_preview)
        self._refresh_out_len()

    def _make_clip_row(self, idx, a, b, sp):
        row = QWidget()
        row.setObjectName("clipRow")
        row.setAttribute(Qt.WA_StyledBackground, True)
        row.setProperty("selected", idx == self.selected_clip)
        row.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(row)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(6)
        play = QPushButton()
        now_playing = self.playing and self._playing_clip == idx
        play.setIcon(icons.icon("pause" if now_playing else "play",
                                icons.ICON_ACCENT, "#07070c", size=14))
        play.setToolTip(tr("tip_row_stop") if now_playing else tr("tip_row_play"))
        play.setIconSize(QSize(14, 14))
        play.setObjectName("rowplay")
        play.setCursor(Qt.PointingHandCursor)
        play.clicked.connect(lambda _=False, i=idx: self._play_clip(i))
        h.addWidget(play)
        h.addWidget(QLabel(f"#{idx + 1}"))
        maxframe = self.reader.total_frames - 1 if self.reader else 10 ** 9
        f_in = FrameField(a, 0, b - 1)
        f_out = FrameField(b, a + 1, maxframe)
        f_in.valueChanged.connect(lambda v, i=idx: self._on_field_edited(i, "in", v))
        f_out.valueChanged.connect(lambda v, i=idx: self._on_field_edited(i, "out", v))
        row._fields = (f_in, f_out)
        h.addWidget(f_in)
        dash = QLabel("–")
        dash.setAlignment(Qt.AlignCenter)
        h.addWidget(dash)
        h.addWidget(f_out)
        spd = QPushButton(f"{sp:g}x")
        spd.setObjectName("rowspeed")
        spd.setToolTip(tr("tip_row_speed"))
        spd.setCursor(Qt.PointingHandCursor)
        spd.clicked.connect(lambda _=False, i=idx, b=spd: self._pick_clip_speed(i, b))
        h.addWidget(spd)
        row.mousePressEvent = lambda ev, i=idx: self._select_clip(i)
        return row

    def _pick_clip_speed(self, idx, button):
        menu = QMenu(self)
        for s in EXPORT_SPEEDS:
            act = menu.addAction(f"{s:g}x")
            act.setData(s)
        act = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if act is not None:
            self._set_clip_speed(idx, act.data())

    def _deselect_clip(self):
        if self.selected_clip is not None:
            self.selected_clip = None
            self._update_marks()

    def _on_field_edited(self, idx, which, frame):
        """行の数値欄から IN/OUT を変更。並び順が変わる場合はドラッグ終了と同じ扱い。"""
        if not (0 <= idx < len(self.segments)):
            return
        a, b, sp = self.segments[idx]
        if which == "in":
            a = frame
        else:
            b = frame
        if a >= b:
            return
        self.segments[idx] = (a, b, sp)
        sel = self.segments[self.selected_clip] if self.selected_clip is not None else None
        order = sorted(self.segments)
        if order != self.segments:
            self.segments = order
            if sel is not None:
                self.selected_clip = self.segments.index(sel)
            QTimer.singleShot(0, self._update_marks)   # 欄自身を作り直すので次のイベントで
        else:
            item = self.clip_list_lay.itemAt(idx)
            row = item.widget() if item else None
            if row is not None and hasattr(row, "_fields"):     # 相手側の欄の上限/下限を更新
                maxframe = self.reader.total_frames - 1 if self.reader else 10 ** 9
                row._fields[0].set_range(0, b - 1)
                row._fields[1].set_range(a + 1, maxframe)
            self._update_range_label()
            for bar in (self.filmstrip, self.waveform):
                bar.set_segments(self.segments, self.selected_clip)
            self._refresh_out_len()
            if self.reader:
                self.clip_store.set(self.reader.path, self.segments,
                                    self.in_frame, self.out_frame)

    def _refresh_out_len(self):
        if self.reader and self.segments:
            total = sum((b - a + 1) / self.reader.fps / max(0.05, sp) for a, b, sp in self.segments)
            self.lbl_out_len.setText(f"{tr('lbl_out_len')}  {fmt_time(total)}")
        else:
            self.lbl_out_len.setText("")

    def _select_clip(self, idx):
        """行クリックで選択 (IN/OUT がそのクリップの修正になる)。もう一度で解除。"""
        self.selected_clip = None if self.selected_clip == idx else idx
        self._update_marks()

    def _play_clip(self, idx):
        """行の ▶: そのクリップを選択し、そのクリップの速度で先頭から再生。
        再生中にもう一度押すと停止。"""
        if not (0 <= idx < len(self.segments)):
            return
        if self.playing and self._playing_clip == idx:
            self._pause()
            return
        self._pause()
        self.selected_clip = idx
        self.in_frame = None
        self.out_frame = None
        self._clip_saved_speed = self.speed_idx
        self._apply_clip_speed(self.segments[idx])
        self._playing_clip = idx
        self._update_marks()
        self._stop_at = self.segments[idx][1]      # OUT で止める
        self._jump_play(self.segments[idx][0])
        self._playing_clip = idx                   # _jump_play 内の _pause で消えるので戻す
        self._refresh_clip_panel()

    def _set_clip_speed(self, idx, speed):
        if not (0 <= idx < len(self.segments)):
            return
        a, b, _ = self.segments[idx]
        self.segments[idx] = (a, b, float(speed))
        self._update_marks()

    # --- 入力割り当て ----------------------------------------------------
    def _apply_bindings(self):
        for sc in self._shortcuts:
            sc.setParent(None)
        self._shortcuts = []
        for aid, keystr in self.input_cfg.keys.items():
            if keystr:
                s = QShortcut(QKeySequence(keystr), self)
                s.activated.connect(lambda a=aid: self._do_action(a, False))
                self._shortcuts.append(s)
        self._mouse_map = self.input_cfg.mouse_to_action()

    def _on_gesture(self, name: str):
        action = self._mouse_map.get(name)
        if action:
            self._do_action(action, from_mouse=True)

    def _do_action(self, action: str, from_mouse: bool = False):
        if action == "open":
            self.open_file()
        elif action == "play_pause":
            self._on_video_click() if from_mouse else self.toggle_play()
        elif action == "frame_prev":
            self.prev_frame()
        elif action == "frame_next":
            self.next_frame()
        elif action == "speed_down":
            self.change_speed(-1)
        elif action == "speed_up":
            self.change_speed(1)
        elif action == "speed_reset":
            self.reset_speed()
        elif action == "zoom_in":
            self.video.zoom_step(0.25)
        elif action == "zoom_out":
            self.video.zoom_step(-0.25)
        elif action == "set_in":
            self.set_in()
        elif action == "set_out":
            self.set_out()
        elif action == "file_prev":
            self.prev_file()
        elif action == "file_next":
            self.next_file()

    # --- アップデート確認 ------------------------------------------------
    def _start_update_check(self):
        if not self.settings.value("check_updates", True, bool):
            return
        self.updater = UpdateChecker(APP_VERSION, APP_NAME, self)
        self.updater.available.connect(self._on_update_available)
        self.updater.check()

    def _on_update_available(self, version: str, url: str):
        self._update_url = url
        self.btn_update.setText(f"🔔 v{version}")
        self.btn_update.setVisible(True)
        self._relayout_overlays()

    def _open_update(self):
        QDesktopServices.openUrl(QUrl(self._update_url or RELEASES_PAGE))

    def _open_shortcuts(self):
        cur_updates = self.settings.value("check_updates", True, bool)
        dlg = ShortcutDialog(self, self.input_cfg, self.lang_pref, cur_updates)
        if dlg.exec() == QDialog.Accepted:
            self.input_cfg.save(self.settings)
            self._apply_bindings()
            self.settings.setValue("check_updates", dlg.chk_updates.isChecked())
            if dlg.lang_pref != self.lang_pref:
                self.lang_pref = dlg.lang_pref
                self.settings.setValue("language", self.lang_pref)
                i18n.set_lang(i18n.resolve(self.lang_pref))
                self.retranslate()

    def retranslate(self):
        self.btn_open.setToolTip(tr("tip_open"))
        self.btn_prev.setToolTip(tr("tip_prev"))
        self.btn_play.setToolTip(tr("tip_play"))
        self.btn_next.setToolTip(tr("tip_next"))
        self.btn_slow.setToolTip(tr("tip_slower"))
        self.btn_fast.setToolTip(tr("tip_faster"))
        self.vol_slider.setToolTip(tr("tip_volume"))
        self.btn_settings.setToolTip(tr("tip_settings"))
        self.btn_in.setToolTip(tr("tip_in"))
        self.btn_out.setToolTip(tr("tip_out"))
        self.btn_preview.setText(tr("btn_preview"))
        self.btn_preview.setToolTip(tr("tip_preview"))
        self._refresh_clear_button()
        self.btn_export.setText(tr("btn_export"))
        self.btn_export.setToolTip(tr("btn_export"))
        self._refresh_clip_panel()
        self._relayout_overlays()
        self.btn_export_ok.setText(tr("btn_export_ok"))
        self.btn_update.setToolTip(tr("tip_update"))
        self.side_tab.setToolTip(tr("tip_side_toggle"))
        base = f"{APP_NAME}  v{APP_VERSION}"
        name = os.path.basename(self.reader.path) if self.reader else ""
        self.setWindowTitle(f"{base} — {name}" if name else base)
        self.video.update()        # プレースホルダ再描画
        self.filmstrip.update()
        self.waveform.update()

    # ------------------------------------------------------------------
    def _set_controls_enabled(self, on: bool):
        # 音量(vol_slider)はファイル前から操作できるよう常に有効
        for w in (self.btn_prev, self.btn_play, self.btn_next, self.btn_slow,
                  self.btn_fast, self.btn_in, self.btn_out, self.btn_preview,
                  self.btn_clear_range, self.btn_export):
            w.setEnabled(on)

    # --- ファイル --------------------------------------------------------
    def open_file(self):
        # フォルダだけ開く (ファイル名欄は空。次ファイルは Ctrl+→ で移動できる)
        if self.reader and os.path.exists(self.reader.path):
            start = os.path.dirname(self.reader.path)
        elif self.recent and os.path.exists(self.recent[0]):
            start = os.path.dirname(self.recent[0])
        else:
            start = self.settings.value("last_open_dir", "", str)
        flt = (f"{tr('filter_video')} "
               "(*.mp4 *.mkv *.mov *.avi *.webm *.flv *.ts *.m4v *.wmv);;"
               f"{tr('filter_all')} (*.*)")
        path, _ = QFileDialog.getOpenFileName(self, tr("open_title"), start, flt)
        if not path:
            return
        self.settings.setValue("last_open_dir", os.path.dirname(path))
        self.load(path)

    # --- 最近のファイル / 右クリックメニュー ----------------------------
    def _load_recent(self):
        raw = self.settings.value("recent_files", [])
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw]
        return [str(x) for x in raw]

    def _add_recent(self, path):
        p = os.path.abspath(path)
        self.recent = [p] + [x for x in self.recent
                             if os.path.abspath(x) != p]
        self.recent = self.recent[:10]
        self.settings.setValue("recent_files", self.recent)
        self.settings.sync()   # 即ディスクへ書き込み (起動間で確実に保持)

    def _show_context_menu(self, global_pos):
        menu = QMenu(self)
        menu.addAction(tr("menu_open"), self.open_file)
        if self.reader:
            files, idx = self._sibling_files()
            a_prev = menu.addAction(tr("act_file_prev"), self.prev_file)
            a_prev.setEnabled(idx > 0)
            a_next = menu.addAction(tr("act_file_next"), self.next_file)
            a_next.setEnabled(0 <= idx < len(files) - 1)
        menu.addSeparator()
        header = menu.addAction(tr("menu_recent"))
        header.setEnabled(False)
        existing = [p for p in self.recent if os.path.exists(p)]
        if existing:
            for p in existing:
                act = QAction(os.path.basename(p), menu)
                act.setToolTip(p)
                act.triggered.connect(lambda checked=False, path=p: self.load(path))
                menu.addAction(act)
            menu.addSeparator()
            menu.addAction(tr("menu_clear_recent"), self._clear_recent)
        else:
            none_act = menu.addAction(tr("menu_no_recent"))
            none_act.setEnabled(False)
        menu.exec(global_pos)

    def _clear_recent(self):
        self.recent = []
        self.settings.setValue("recent_files", self.recent)
        self.settings.sync()

    # --- 同フォルダ内の前後ファイルへ移動 -------------------------------
    def _sibling_files(self):
        if not self.reader:
            return [], -1
        cur = os.path.abspath(self.reader.path)
        folder = os.path.dirname(cur)
        try:
            files = [os.path.join(folder, f) for f in os.listdir(folder)
                     if f.lower().endswith(self.VIDEO_EXTS)]
        except OSError:
            return [], -1
        files.sort(key=lambda p: os.path.basename(p).lower())
        idx = next((i for i, p in enumerate(files)
                    if os.path.abspath(p) == cur), -1)
        return files, idx

    def next_file(self):
        files, idx = self._sibling_files()
        if 0 <= idx < len(files) - 1:
            self.load(files[idx + 1])

    def prev_file(self):
        files, idx = self._sibling_files()
        if idx > 0:
            self.load(files[idx - 1])

    def load(self, path: str):
        self._pause()
        if self.reader:
            self.reader.close()
        if self.audio:
            self.audio.close()
            self.audio = None
        if self.producer:
            self.producer.close()
            self.producer = None
        try:
            self.reader = VideoReader(path)
            self.producer = FramePrefetcher(path)
        except Exception as e:
            QMessageBox.critical(self, tr("err_title"), f"{tr('err_open')}\n{e}")
            return
        if self.reader.has_audio:
            try:
                self.audio = AudioPlayer(path, self)
            except Exception:
                self.audio = None
        if self.audio:
            self.audio.set_volume(self.volume)
        self.cur_index = 0
        self.in_frame = None
        self.out_frame = None
        self.segments = []
        self.selected_clip = None
        self._clear_backup = None
        self._refresh_clear_button()
        self.video.clear_crop()
        maxframe = self.reader.total_frames - 1
        for bar in (self.filmstrip, self.waveform):
            bar.clear()
            bar.set_range(maxframe)
            bar.set_marks(None, None)
        self._restore_clips(path, maxframe)
        self._set_controls_enabled(True)
        self._refresh_clip_panel()          # 動画を開いたらパネルと開閉タブを出す
        self.setWindowTitle(f"{APP_NAME}  v{APP_VERSION} — {os.path.basename(path)}")
        self._add_recent(path)
        self._show_frame(0)
        self._update_labels()
        self._start_timeline_analysis(path)

    def _start_timeline_analysis(self, path: str):
        # サムネイル (映像) は常に、波形は音声があるときだけ生成
        self._stop_timeline_threads()
        self.filmstrip.set_loading(True)
        self.film_thread = QThread()
        self.film_worker = FilmstripWorker(path)
        self.film_worker.moveToThread(self.film_thread)
        self.film_thread.started.connect(self.film_worker.run)
        self.film_worker.done.connect(self._on_filmstrip_done)
        self.film_thread.start()

        if self.reader.has_audio:
            self.waveform.set_loading(True)
            self.wave_thread = QThread()
            self.wave_worker = WaveformWorker(path)
            self.wave_worker.moveToThread(self.wave_thread)
            self.wave_thread.started.connect(self.wave_worker.run)
            self.wave_worker.done.connect(self._on_waveform_done)
            self.wave_thread.start()

    def _stop_timeline_threads(self):
        for attr in ("film_thread", "wave_thread"):
            th = getattr(self, attr)
            if th:
                th.quit()
                th.wait()
                setattr(self, attr, None)

    def _on_filmstrip_done(self, path, thumbs):
        if self.film_thread:
            self.film_thread.quit()
            self.film_thread.wait()
            self.film_thread = None
        if self.reader and path == self.reader.path:
            self.filmstrip.set_thumbnails(thumbs)

    def _on_waveform_done(self, path, env):
        if self.wave_thread:
            self.wave_thread.quit()
            self.wave_thread.wait()
            self.wave_thread = None
        if self.reader and path == self.reader.path:
            self.waveform.set_envelope(env)

    # --- 表示 ------------------------------------------------------------
    def _show_frame(self, index: int, back: bool = False):
        """同期デコードして表示 (一時停止中のコマ送り/シーク用)。
        back=True (コマ戻し) のときは周辺フレームをまとめてキャッシュする。"""
        if not self.reader:
            return
        index = max(0, min(index, self.reader.total_frames - 1))
        arr = self.reader.get_frame(index, cache_window=48 if back else 0)
        self._present(index, arr)

    def _present(self, index: int, arr):
        """すでにデコード済みの配列を表示するだけ (再生時用・デコードしない)。"""
        self._hold_arr = arr  # GC 防止
        self.video.set_image(ndarray_to_qimage(arr))
        self.cur_index = index
        self.filmstrip.set_position(index)
        self.waveform.set_position(index)
        self._update_labels()

    def _update_labels(self):
        if self.reader:
            t = self.reader.index_to_time(self.cur_index)
            total_t = self.reader.index_to_time(self.reader.total_frames - 1)
            self.lbl_frame.setText(
                f"{self.cur_index} / {self.reader.total_frames - 1}"
                f"   {fmt_time(t)} / {fmt_time(total_t)}   {self.reader.fps:.0f}fps"
                + (f"   ⌕ {self._zoom:.2f}x" if self._zoom > 1.01 else ""))
            self.lbl_frame.adjustSize()
        self.lbl_speed.setText(f"{SPEEDS[self.speed_idx]:g}x")
        self._update_range_label()

    def _update_range_label(self):
        if self.selected_clip is not None:
            self.lbl_range.setProperty("class", "hint")
            self.lbl_range.setText(tr("lbl_editing").replace("{n}", str(self.selected_clip + 1)))
            self.lbl_range.style().unpolish(self.lbl_range); self.lbl_range.style().polish(self.lbl_range)
            return
        self.lbl_range.setProperty("class", "range")
        self.lbl_range.style().unpolish(self.lbl_range); self.lbl_range.style().polish(self.lbl_range)
        if self.in_frame is None and self.out_frame is None:
            self.lbl_range.setText("")
            return
        a = "·" if self.in_frame is None else str(self.in_frame)
        b = "·" if self.out_frame is None else str(self.out_frame)
        self.lbl_range.setText(f"IN {a}   OUT {b}")

    # --- 再生 ------------------------------------------------------------
    def _on_video_click(self):
        # 別ウィンドウから切り替えた直後のクリック(=アクティブ化のためのクリック)は無視
        if time.perf_counter() - self._activated_ts < 0.25:
            return
        if not self.reader:
            self.open_file()   # 起動直後(動画なし)はクリックでファイルを開く
            return
        self.toggle_play()

    def toggle_play(self):
        if not self.reader:
            return
        if self.playing:
            self._pause()
        else:
            self._play()

    def _play(self):
        if not self.reader or self.playing:
            return
        if self.cur_index >= self.reader.total_frames - 1:
            self._show_frame(0)
        self.playing = True
        self.btn_play.setIcon(self._icon_pause)
        self._pending = None
        if self.producer:
            self.producer.start(self.cur_index)   # 先読みデコード開始
        self._rebase_clock()
        self._restart_timer()
        self._sync_audio()

    def _rebase_clock(self):
        """映像同期用の壁時計を現在位置に合わせ直す。"""
        self._play_t0 = time.perf_counter()
        self._play_frame0 = self.cur_index

    def _pause(self):
        self.playing = False
        self._stop_at = None
        if self._playing_clip is not None:         # 行の ▶ 再生を終了 → 速度を戻す
            self._playing_clip = None
            if self._clip_saved_speed is not None:
                self.speed_idx = self._clip_saved_speed
                self._clip_saved_speed = None
                self._update_labels()
            QTimer.singleShot(0, self._refresh_clip_panel)
        self.play_timer.stop()
        self.btn_play.setIcon(self._icon_play)
        self._pending = None
        self._end_preview()           # 一時停止でプレビューも終了
        if self.producer:
            self.producer.stop()
        if self.audio:
            self.audio.stop()

    def _end_preview(self):
        """プレビューを終了し、クリップ速度で上書きしていた再生速度を戻す。"""
        if self.preview_segs is not None and self._preview_saved_speed is not None:
            self.speed_idx = self._preview_saved_speed
            self._update_labels()
        self._preview_saved_speed = None
        if self.preview_segs is not None:
            self.preview_segs = None
            self._refresh_clip_panel()

    def _apply_clip_speed(self, seg):
        """プレビュー中: クリップの書き出し速度に最も近い再生速度へ切り替える。"""
        target = seg[2] if len(seg) > 2 else 1.0
        self.speed_idx = min(range(len(SPEEDS)),
                             key=lambda i: abs(SPEEDS[i] - target))
        self._update_labels()

    def _sync_audio(self):
        """現在位置・現在速度で音声を鳴らし直す (倍速/低速にも追従)。"""
        if not self.audio:
            return
        if self.playing:
            self.audio.play(self.reader.index_to_time(self.cur_index),
                            SPEEDS[self.speed_idx])
        else:
            self.audio.stop()

    def _restart_timer(self):
        # 表示用の高頻度ポーリング。クロックに合わせて先読みキューから取り出す。
        self.play_timer.start(5)

    def _advance(self):
        if not self.reader or not self.producer:
            return
        # マスタークロック: 音声があれば音声の再生位置、無ければ壁時計
        if self.audio and self.audio.is_playing():
            target = round(self.audio.position_sec() * self.reader.fps)
        else:
            elapsed = time.perf_counter() - self._play_t0
            target = self._play_frame0 + round(
                elapsed * self.reader.fps * SPEEDS[self.speed_idx])

        # クリップ単体再生: OUT に達したら止める
        if self._stop_at is not None and target >= self._stop_at:
            stop = self._stop_at
            self._stop_at = None
            self._show_frame(stop)
            self._pause()
            return

        # プレビュー再生: 現在クリップの終端を超えたら次のクリップへジャンプ
        if self.preview_segs is not None:
            end = self.preview_segs[self.preview_idx][1]
            if target > end:
                self.preview_idx += 1
                if self.preview_idx >= len(self.preview_segs):
                    self._show_frame(end)
                    self._pause()          # 最後のクリップまで再生し終えた
                else:
                    nxt_seg = self.preview_segs[self.preview_idx]
                    self._apply_clip_speed(nxt_seg)   # 次クリップの速度で再生
                    self._preview_jump(nxt_seg[0])
                return

        last = self.reader.total_frames - 1
        if target >= last:
            self._show_frame(last)
            self._pause()
            return
        if target < 0:
            target = 0

        # クロック(target)に達したコマだけ表示。未来のコマは取り出さず次回まで保持。
        # idx<=target の中で最新を選び、idx>target は self._pending に戻す。
        q = self.producer.q
        chosen = None
        while True:
            nxt = self._pending
            self._pending = None
            if nxt is None:
                try:
                    nxt = q.get_nowait()
                except queue.Empty:
                    break
            if nxt[0] > target:
                self._pending = nxt        # まだ表示時刻ではない → 保持
                break
            chosen = nxt                   # idx<=target → 表示候補
            if nxt[0] == target:
                break
        if chosen is not None and chosen[0] != self.cur_index:
            self._present(chosen[0], chosen[1])

    def next_frame(self):
        self._pause()
        self._show_frame(self.cur_index + 1)

    def prev_frame(self):
        self._pause()
        self._show_frame(self.cur_index - 1, back=True)

    def change_speed(self, delta: int):
        self.speed_idx = max(0, min(len(SPEEDS) - 1, self.speed_idx + delta))
        self._update_labels()
        if self.playing:
            self._rebase_clock()
            self._restart_timer()
            self._sync_audio()

    def reset_speed(self):
        self.speed_idx = SPEEDS.index(1.0)
        self._update_labels()
        if self.playing:
            self._rebase_clock()
            self._restart_timer()
            self._sync_audio()

    def _on_seek(self, value: int):
        self._end_preview()                  # 手動シークでプレビューは解除
        self._stop_at = None
        self._show_frame(value)
        if self.playing:
            self._pending = None
            if self.producer:
                self.producer.start(value)   # 先読みを新しい位置から
            self._rebase_clock()
            self._sync_audio()   # 再生は止めず、新しい位置から音声を鳴らし直す

    # --- クリップのプレビュー再生 ---------------------------------------
    def toggle_preview(self):
        """クリップの範囲だけを番号順に連続再生 (出力プレビュー)。"""
        if self.preview_segs is not None:    # 実行中なら停止
            self._pause()
            return
        if not self.reader:
            return
        segs = self._export_segments()
        self._pause()
        self._preview_saved_speed = self.speed_idx   # 各クリップの速度で再生し、終了時に戻す
        self.preview_segs = segs
        self.preview_idx = 0
        self._apply_clip_speed(segs[0])
        self._show_frame(segs[0][0])
        self._play()
        self._refresh_clip_panel()

    def _preview_jump(self, frame: int):
        """プレビュー中の次クリップへのジャンプ (プレビュー状態は維持)。"""
        self._show_frame(frame)
        self._pending = None
        if self.producer:
            self.producer.start(frame)
        self._rebase_clock()
        self._sync_audio()

    # --- 拡大表示 -------------------------------------------------------
    def _on_zoom(self, z: float):
        self._zoom = z
        self._update_labels()

    def _on_crop(self, rect):
        pass

    def _on_volume(self, v: int):
        self.volume = v / 100.0
        self.lbl_vol.setText(f"{v}%")
        self.settings.setValue("volume", self.volume)
        if self.audio:
            self.audio.set_volume(self.volume)

    # --- IN / OUT (時間範囲) --------------------------------------------
    def set_in(self):
        self.set_in_at(self.cur_index)

    def set_out(self):
        self.set_out_at(self.cur_index)

    def set_in_at(self, frame: int):
        if not self.reader:
            return
        self._drop_clear_backup()
        frame = max(0, min(frame, self.reader.total_frames - 1))
        if self.selected_clip is not None:       # 選択中クリップの IN を修正
            self._edit_clip(in_=frame)
            return
        self.in_frame = frame
        if self.out_frame is not None and self.out_frame <= self.in_frame:
            self.out_frame = None
        self._commit_if_complete()
        self._update_marks()

    def set_out_at(self, frame: int):
        if not self.reader:
            return
        self._drop_clear_backup()
        frame = max(0, min(frame, self.reader.total_frames - 1))
        if self.selected_clip is not None:       # 選択中クリップの OUT を修正
            self._edit_clip(out=frame)
            return
        self.out_frame = frame
        if self.in_frame is not None and self.in_frame >= self.out_frame:
            self.in_frame = None
        self._commit_if_complete()
        self._update_marks()

    def _commit_if_complete(self):
        """IN と OUT が両方決まったら、その場でクリップとして確定する (＋は不要)。"""
        if self.in_frame is None or self.out_frame is None:
            return
        self.segments.append((self.in_frame, self.out_frame, 1.0))
        self.segments.sort()
        self.in_frame = None
        self.out_frame = None

    def add_segment(self):
        """現在の IN–OUT をクリップとして確定し、次の区間選択へ。
        クリップ選択中なら選択を解除するだけ (新規作成モードへ戻る)。"""
        if self.selected_clip is not None:
            self.selected_clip = None
            self._update_marks()
            return
        if self.in_frame is None or self.out_frame is None:
            return
        self.segments.append((self.in_frame, self.out_frame, 1.0))
        self.segments.sort()
        self.in_frame = None
        self.out_frame = None
        self._update_marks()

    def clear_range(self):
        self.in_frame = None
        self.out_frame = None
        self.segments = []
        self.selected_clip = None
        self._update_marks()

    def on_clear_clicked(self):
        """全クリア。直後は「取り消す」に変わり、新たにIN/OUTを打つまで復元可能。"""
        if self._clear_backup is not None:       # 取り消し (復元)
            (self.in_frame, self.out_frame,
             self.segments, self.selected_clip) = self._clear_backup
            self._clear_backup = None
            self._refresh_clear_button()
            self._update_marks()
            return
        if (self.in_frame is None and self.out_frame is None
                and not self.segments):
            return                               # 何もない時は無視
        self._clear_backup = (self.in_frame, self.out_frame,
                              list(self.segments), self.selected_clip)
        self.clear_range()
        self._refresh_clear_button()

    def _refresh_clear_button(self):
        if self._clear_backup is not None:
            self.btn_clear_range.setText(tr("btn_undo_clear"))
            self.btn_clear_range.setToolTip(tr("tip_undo_clear"))
            self.btn_clear_range.setIcon(icons.icon("undo", icons.ICON_ACCENT, "#ffffff", size=14))
        else:
            self.btn_clear_range.setText(tr("btn_clear"))
            self.btn_clear_range.setToolTip(tr("tip_clear"))
            self.btn_clear_range.setIcon(icons.icon("clear", size=14))
        self._refresh_clip_panel()

    def _undo_clear(self):
        if self._clear_backup is not None:
            self.on_clear_clicked()

    def _drop_clear_backup(self):
        """新たに IN/OUT を設定したら全クリアの取り消しは無効化。"""
        if self._clear_backup is not None:
            self._clear_backup = None
            self._refresh_clear_button()

    # --- タイムライン上の直接操作 ---------------------------------------
    def _clear_float_range(self):
        """右クリック: 浮動 IN-OUT 範囲のみクリア。"""
        self.in_frame = None
        self.out_frame = None
        self._update_marks()

    def remove_clip(self, idx: int):
        """右クリック: メニューを出し、「削除」を選んだときだけクリップを消す。"""
        if not (0 <= idx < len(self.segments)):
            return
        menu = QMenu(self)
        act = menu.addAction(tr("menu_delete_clip").replace("{n}", str(idx + 1)))
        if menu.exec(QCursor.pos()) is not act:
            return
        if not (0 <= idx < len(self.segments)):
            return
        del self.segments[idx]
        if self.selected_clip is not None:
            if self.selected_clip == idx:
                self.selected_clip = None
            elif self.selected_clip > idx:
                self.selected_clip -= 1
        self._update_marks()

    def _on_seg_edge_moved(self, idx: int, which: str, frame: int):
        """クリップ境界のドラッグ (選択不要)。ドラッグ中は並べ替えない。"""
        if not (0 <= idx < len(self.segments)):
            return
        a, b, sp = self.segments[idx]
        if which == "in":
            a = frame
        else:
            b = frame
        if a >= b:
            return
        self.segments[idx] = (a, b, sp)
        self._update_marks()

    def _on_seg_drag_finished(self):
        """ドラッグ終了時に時系列順へ整理 (選択は追跡)。"""
        sel = (self.segments[self.selected_clip]
               if self.selected_clip is not None else None)
        self.segments.sort()
        if sel is not None:
            self.selected_clip = self.segments.index(sel)
        self._update_marks()

    # --- クリップの修正 --------------------------------------------------
    def _edit_clip(self, in_=None, out=None):
        """選択中クリップの IN/OUT を現在フレームで置き換える。"""
        a, b, sp = self.segments[self.selected_clip]
        if in_ is not None:
            a = in_
        if out is not None:
            b = out
        if a == b:
            return
        tup = (min(a, b), max(a, b), sp)
        self.segments[self.selected_clip] = tup
        self.segments.sort()
        self.selected_clip = self.segments.index(tup)   # ソート後も選択を追跡
        self._update_marks()

    def _jump_play(self, frame: int):
        stop_at = self._stop_at
        if self.playing:
            self._on_seek(frame)
        else:
            self._show_frame(frame)
            self._play()
        self._stop_at = stop_at        # _on_seek/_pause で消えた停止位置を戻す

    def _restore_clips(self, path: str, maxframe: int):
        """保存済みの IN/OUT・クリップがあれば復元 (フレーム範囲にクランプ)。"""
        saved = self.clip_store.get(path)
        if not saved:
            return
        segs = []
        for item in saved.get("segments", []):
            a = max(0, min(int(item[0]), maxframe))
            b = max(0, min(int(item[1]), maxframe))
            sp = float(item[2]) if len(item) > 2 else 1.0   # 旧形式は速度なし
            if a < b:
                segs.append((a, b, sp))
        self.segments = sorted(segs)
        iv, ov = saved.get("in"), saved.get("out")
        self.in_frame = None if iv is None else max(0, min(int(iv), maxframe))
        self.out_frame = None if ov is None else max(0, min(int(ov), maxframe))
        if (self.in_frame is not None and self.out_frame is not None
                and self.in_frame >= self.out_frame):
            self.out_frame = None
        self._update_marks()

    def _update_marks(self):
        self._update_range_label()
        self._refresh_clip_panel()
        for bar in (self.filmstrip, self.waveform):
            bar.set_segments(self.segments, self.selected_clip)
            bar.set_marks(self.in_frame, self.out_frame)
        if self.reader:   # 変更のたびに自動保存 (1動画あたり~100バイト)
            self.clip_store.set(self.reader.path, self.segments,
                                self.in_frame, self.out_frame)

    # --- 縦型書き出しフロー ---------------------------------------------
    def begin_export(self):
        """縦型 / 横型 を選んでから、切り出し枠を画面に出して配置モードへ。"""
        if not self.reader:
            return
        menu = QMenu(self)
        act_v = menu.addAction(tr("menu_export_v"))
        act_h = menu.addAction(tr("menu_export_h"))
        pos = self.btn_export.mapToGlobal(self.btn_export.rect().topLeft())
        chosen = menu.exec(pos)
        if chosen is None:
            return
        self.export_orient = "h" if chosen is act_h else "v"
        self._pause()
        self.video.set_crop_aspect(16.0 / 9.0 if self.export_orient == "h" else 9.0 / 16.0)
        self.video.start_crop()
        self.btn_export_ok.setText(tr("btn_export_ok"))
        self.btn_export.setVisible(False)
        self.btn_export_ok.setVisible(True)
        self.btn_export_cancel.setVisible(True)

    def cancel_export(self):
        self.video.end_crop()
        self._exit_export_mode()

    def _exit_export_mode(self):
        self.btn_export.setVisible(True)
        self.btn_export_ok.setVisible(False)
        self.btn_export_cancel.setVisible(False)

    def _export_segments(self):
        """書き出し対象のクリップ一覧 [(in, out, speed), ...] (時系列順)。"""
        segs = list(self.segments)
        if self.in_frame is not None and self.out_frame is not None:
            segs.append((self.in_frame, self.out_frame, 1.0))   # 未確定のIN–OUTも含める
        if not segs:
            segs = [(0, self.reader.total_frames - 1, 1.0)]     # 未指定なら全体
        return sorted(segs)

    def _store_clip_speeds(self, segs):
        """書き出しダイアログで決めた速度を確定済みクリップに書き戻す (自動保存される)。"""
        speed_of = {(a, b): sp for a, b, sp in segs}
        changed = False
        for i, (a, b, sp) in enumerate(self.segments):
            new = speed_of.get((a, b), sp)
            if abs(new - sp) >= 1e-6:
                self.segments[i] = (a, b, new)
                changed = True
        if changed:
            self._update_marks()

    def confirm_export(self):
        if not self.reader or not self.video.crop_rect:
            return
        segs = self._export_segments()
        x, y, w, h = self.video.crop_rect
        crop_text = f"{w} x {h}  ({x},{y})"

        res_key = f"export_res_{self.export_orient}"
        dlg = ExportDialog(self, crop_text, segs, self.reader.fps,
                           horizontal=(self.export_orient == "h"),
                           preset_index=int(self.settings.value(res_key, 0, int)))
        if dlg.exec() != QDialog.Accepted:
            return
        self.settings.setValue(res_key, dlg.combo.currentIndex())   # 次回も同じ解像度
        out_w, out_h = dlg.resolution()
        include_audio = dlg.include_audio() and self.reader.has_audio
        transition = dlg.transition()
        segs = [(a, b, sp) for (a, b, _), sp in zip(segs, dlg.speeds())]
        self._store_clip_speeds(segs)

        suffix = "_wide.mp4" if self.export_orient == "h" else "_vertical.mp4"
        default_name = os.path.splitext(os.path.basename(self.reader.path))[0] + suffix
        save_dir = self.settings.value("last_save_dir", "", str) \
            or self.settings.value("last_open_dir", "", str)
        start_path = os.path.join(save_dir, default_name) if save_dir else default_name
        dst, _ = QFileDialog.getSaveFileName(
            self, tr("save_title"), start_path, "MP4 (*.mp4)")
        if not dst:
            return
        self.settings.setValue("last_save_dir", os.path.dirname(dst))

        time_segs = [(self.reader.index_to_time(a),
                      self.reader.index_to_time(b + 1), sp) for a, b, sp in segs]
        self.video.end_crop()
        self._exit_export_mode()
        self._run_export(dst, self.video.crop_rect, time_segs,
                         out_w, out_h, include_audio, transition)

    def _run_export(self, dst, crop, time_segs, out_w, out_h, audio, transition):
        self.progress = QProgressDialog(tr("progress_label"), tr("cancel"), 0, 100, self)
        self.progress.setWindowTitle(tr("progress_title"))
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.setMinimumDuration(0)
        self.progress.setValue(0)

        self.thread = QThread()
        self.worker = ExportWorker(self.reader.path, dst, crop, time_segs,
                                   out_w, out_h, audio, transition)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)

        def on_progress(f):
            self.progress.setValue(int(f * 100))
            # 実際に書き込んだサイズ ÷ 進捗率 で最終サイズを着地予測
            if f >= 0.05:
                try:
                    est = os.path.getsize(dst) / f / (1024 * 1024)
                    self.progress.setLabelText(
                        f"{tr('progress_label')}  ~{est:.0f} MB")
                except OSError:
                    pass
        self.worker.progress.connect(on_progress)
        self.worker.finished.connect(self._on_export_done)
        self.progress.canceled.connect(self.worker.cancel)
        self.thread.start()

    def _on_export_done(self, ok: bool, msg: str):
        self.thread.quit()
        self.thread.wait()
        self.progress.reset()
        if ok:
            # 出力先フォルダを開き、書き出したファイルを選択状態にする
            try:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(msg)])
            except Exception:
                QMessageBox.information(self, tr("done_title"),
                                        f"{tr('done_msg')}\n{msg}")
        else:
            QMessageBox.warning(self, tr("fail_title"), msg)

    # --- ウィンドウ / ドラッグ&ドロップ ---------------------------------
    VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv",
                  ".ts", ".m4v", ".wmv", ".mpg", ".mpeg", ".m2ts")

    def changeEvent(self, event):
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._activated_ts = time.perf_counter()
        if event.type() == QEvent.WindowStateChange:
            # 最大化/復元の直後はレイアウト確定後に再描画して崩れを防ぐ
            QTimer.singleShot(0, self._repaint_all)
        super().changeEvent(event)

    def _repaint_all(self):
        self.video.update()
        self.filmstrip.invalidate()
        self.waveform.invalidate()

    def _first_video_url(self, mime):
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            if url.isLocalFile():
                p = url.toLocalFile()
                if p.lower().endswith(self.VIDEO_EXTS):
                    return p
        return None

    def dragEnterEvent(self, event):
        if self._first_video_url(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = self._first_video_url(event.mimeData())
        if path:
            self.settings.setValue("last_open_dir", os.path.dirname(path))
            self.load(path)
            event.acceptProposedAction()

    def closeEvent(self, event):
        self._pause()
        self._stop_timeline_threads()
        if self.audio:
            self.audio.close()
        if self.producer:
            self.producer.close()
        if self.reader:
            self.reader.close()
        super().closeEvent(event)


class ExportDialog(QDialog):
    PRESETS_V = [("1080 x 1920 (FHD)", 1080, 1920),
                 ("720 x 1280 (HD)", 720, 1280),
                 ("1440 x 2560 (QHD)", 1440, 2560)]
    PRESETS_H = [("1920 x 1080 (FHD)", 1920, 1080),
                 ("1280 x 720 (HD)", 1280, 720),
                 ("2560 x 1440 (QHD)", 2560, 1440)]

    # CRF18 のゲーム映像でよくある映像ビットレートの目安 (Mbps)。内容次第で上下する
    EST_MBPS = {1080: 11.0, 720: 6.0, 1440: 20.0}

    def __init__(self, parent=None, crop_text="", segs=None, fps=30.0, horizontal=False,
                 preset_index=0):
        """segs: [(in_frame, out_frame, speed), ...]。各クリップの速度をここで決める。"""
        super().__init__(parent)
        self.PRESETS = self.PRESETS_H if horizontal else self.PRESETS_V
        self.setWindowTitle(tr("export_settings_title"))
        self._segs = list(segs or [])
        self._fps = max(1e-6, float(fps))
        form = QFormLayout(self)
        if crop_text:
            form.addRow(tr("lbl_crop_range"), QLabel(crop_text))

        # クリップ一覧 (速度は CLIPS パネルで決めたものを表示するだけ)
        if self._segs:
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            for i, (a, b, sp) in enumerate(self._segs):
                dur = (b - a + 1) / self._fps
                grid.addWidget(QLabel(f"#{i + 1}   {a}–{b}   ({fmt_time(dur)})"), i, 0)
                lbl_sp = QLabel(f"{sp:g}x")
                lbl_sp.setStyleSheet("color:#f5c400;font-weight:bold;")
                grid.addWidget(lbl_sp, i, 1)
            grid.setColumnStretch(0, 1)
            inner = QWidget()
            inner.setLayout(grid)
            if len(self._segs) > 6:          # 多いときはスクロール
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setFrameShape(QScrollArea.NoFrame)
                scroll.setWidget(inner)
                scroll.setFixedHeight(6 * 30)
                form.addRow(tr("lbl_clips"), scroll)
            else:
                form.addRow(tr("lbl_clips"), inner)

        self.lbl_out_len = QLabel("-")
        form.addRow(tr("lbl_out_len"), self.lbl_out_len)
        self.combo = QComboBox()
        for name, _, _ in self.PRESETS:
            self.combo.addItem(name)
        self.combo.setCurrentIndex(max(0, min(len(self.PRESETS) - 1, int(preset_index))))
        form.addRow(tr("lbl_resolution"), self.combo)
        self.lbl_est = QLabel("-")
        form.addRow(tr("lbl_est_size"), self.lbl_est)
        self.combo.currentIndexChanged.connect(self._update_est)
        self._update_est()
        self.chk_audio = QCheckBox(tr("chk_audio"))
        self.chk_audio.setChecked(True)
        form.addRow("", self.chk_audio)
        self.chk_transition = QCheckBox(tr("chk_transition"))
        self.chk_transition.setChecked(False)
        self.chk_transition.setEnabled(len(self._segs) > 1)   # クリップ2個以上のときのみ
        form.addRow("", self.chk_transition)
        note = QLabel(tr("export_note"))
        note.setWordWrap(True)
        form.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def speeds(self):
        """クリップごとの書き出し速度 (segs と同じ順。CLIPS パネルで設定済みの値)。"""
        return [sp for _, _, sp in self._segs]

    def _output_duration(self) -> float:
        """速度適用後の出力の長さ (秒)。"""
        total = 0.0
        for (a, b, _), sp in zip(self._segs, self.speeds()):
            total += (b - a + 1) / self._fps / max(0.05, sp)
        return total

    def _update_est(self):
        duration = self._output_duration()
        self.lbl_out_len.setText(fmt_time(duration) if duration > 0 else "-")
        if duration <= 0:
            self.lbl_est.setText("-")
            return
        _, w, h = self.PRESETS[self.combo.currentIndex()]
        mbps = self.EST_MBPS.get(min(w, h), 10.0) + 0.15   # 映像 + AAC音声 (短辺で目安)
        mid = mbps * duration / 8                  # MB
        self.lbl_est.setText(f"~{mid * 0.5:.0f} – {mid * 1.5:.0f} MB")

    def resolution(self):
        _, w, h = self.PRESETS[self.combo.currentIndex()]
        return w, h

    def include_audio(self):
        return self.chk_audio.isChecked()

    def transition(self):
        return self.chk_transition.isChecked() and self.chk_transition.isEnabled()
