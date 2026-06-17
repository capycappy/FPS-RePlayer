"""メインウィンドウ。再生/コマ送り/倍速/虫めがね/縦型書き出しを統合。"""
from __future__ import annotations

import os
import time
import queue

from PySide6.QtCore import Qt, QTimer, QThread, QSettings, QEvent, Signal
from PySide6.QtGui import (QImage, QKeySequence, QShortcut, QPainter, QPen,
                           QColor, QPainterPath, QAction)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QMessageBox, QDialog, QComboBox, QCheckBox,
    QDialogButtonBox, QFormLayout, QProgressDialog, QMenu,
)

from reader import VideoReader
from video_widget import VideoWidget
from exporter import ExportWorker
from audio_player import AudioPlayer
from player_engine import FramePrefetcher
from timeline import FilmstripBar, WaveformBar, FilmstripWorker, WaveformWorker
from shortcuts import InputConfig, ShortcutDialog
import i18n
from i18n import tr

from version import APP_NAME

SPEEDS = [0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 4.0, 8.0, 16.0]


def ndarray_to_qimage(arr) -> QImage:
    h, w, _ = arr.shape
    return QImage(arr.data, w, h, w * 3, QImage.Format_RGB888)


def fmt_time(sec: float) -> str:
    if sec < 0:
        sec = 0
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:05.2f}"


class WedgeVolumeSlider(QWidget):
    """音量スライダー。横向きの三角形(ウェッジ)で小→大を視覚化する。"""
    valueChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(62, 24)   # 他のボタンと同じ横幅
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

    def _wedge_path(self):
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.moveTo(0, h - 1)
        path.lineTo(w - 1, 1)
        path.lineTo(w - 1, h - 1)
        path.closeSubpath()
        return path

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        path = self._wedge_path()
        p.fillPath(path, QColor("#33363c"))           # 全体(暗)
        fill_w = self._value / 100.0 * self.width()
        p.save()
        p.setClipRect(0, 0, int(fill_w), self.height())
        p.fillPath(path, QColor("#3fb6c8"))            # 音量ぶん(明)
        p.restore()
        p.setPen(QPen(QColor("#5a5e66"), 1))
        p.drawPath(path)

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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 760)
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

        self.reader: VideoReader | None = None
        self.audio: AudioPlayer | None = None
        self.producer: FramePrefetcher | None = None
        self.wave_thread = None
        self.wave_worker = None
        self.film_thread = None
        self.film_worker = None
        self.cur_index = 0
        self.speed_idx = SPEEDS.index(1.0)
        self.playing = False
        self._play_t0 = 0.0
        self._play_frame0 = 0
        self._pending = None   # 表示時刻待ちの先読みフレーム
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
        self._update_labels()

    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.addWidget(self.video, 1)

        # タイムライン (シークバー兼用): サムネイル帯 + 音声波形
        # クリック=シーク / Ctrl+クリック=IN / Alt+クリック=OUT
        self.filmstrip = FilmstripBar()
        self.waveform = WaveformBar()
        for bar in (self.filmstrip, self.waveform):
            bar.seekRequested.connect(self._on_seek)
            bar.inRequested.connect(self.set_in_at)
            bar.outRequested.connect(self.set_out_at)
            root.addWidget(bar)

        # 1段目: 再生コントロール (アイコンのみ・英語ツールチップ)
        row1 = QHBoxLayout()
        self.btn_open = self._icon_button("📂", tr("tip_open"), self.open_file)
        self.btn_prev = self._icon_button("◁", tr("tip_prev"), self.prev_frame, repeat=True)
        self.btn_play = self._icon_button("▶", tr("tip_play"), self.toggle_play)
        self.btn_next = self._icon_button("▷", tr("tip_next"), self.next_frame, repeat=True)
        self.btn_slow = self._icon_button("▼", tr("tip_slower"), lambda: self.change_speed(-1))
        self.lbl_speed = QLabel("1.0x")
        self.lbl_speed.setMinimumWidth(48)
        self.lbl_speed.setAlignment(Qt.AlignCenter)
        self.btn_fast = self._icon_button("▲", tr("tip_faster"), lambda: self.change_speed(1))
        self.vol_slider = WedgeVolumeSlider()
        self.vol_slider.setToolTip(tr("tip_volume"))
        self.vol_slider.setValue(int(self.volume * 100))
        self.vol_slider.valueChanged.connect(self._on_volume)
        self.lbl_vol = QLabel(f"{int(self.volume * 100)}%")
        self.lbl_vol.setMinimumWidth(40)
        self.lbl_vol.setAlignment(Qt.AlignCenter)
        for w in (self.btn_open, self.btn_prev, self.btn_play, self.btn_next,
                  self.btn_slow, self.lbl_speed, self.btn_fast,
                  self.vol_slider, self.lbl_vol):
            row1.addWidget(w)
        row1.addStretch(1)
        self.lbl_frame = QLabel("- / -")
        row1.addWidget(self.lbl_frame)
        root.addLayout(row1)

        # 2段目: 拡大表示 & IN/OUT & 書き出し (アイコンのみ)
        row2 = QHBoxLayout()
        self.btn_settings = self._icon_button("⚙", tr("tip_settings"),
                                              self._open_shortcuts)
        self.lbl_zoom = QLabel("1.0x")
        self.lbl_zoom.setMinimumWidth(56)
        self.lbl_zoom.setAlignment(Qt.AlignCenter)
        self.btn_in = self._icon_button("IN", tr("tip_in"), self.set_in)
        self.btn_out = self._icon_button("OUT", tr("tip_out"), self.set_out)
        self.btn_clear_range = self._icon_button(tr("btn_clear"), tr("tip_clear"),
                                                 self.clear_range)
        self.lbl_range = QLabel("[ – ]")
        self.btn_export = QPushButton(tr("btn_export"))
        self.btn_export.clicked.connect(self.begin_export)
        self.btn_export_ok = QPushButton(tr("btn_export_ok"))
        self.btn_export_ok.clicked.connect(self.confirm_export)
        self.btn_export_ok.setVisible(False)
        self.btn_export_cancel = QPushButton(tr("btn_export_cancel"))
        self.btn_export_cancel.clicked.connect(self.cancel_export)
        self.btn_export_cancel.setVisible(False)
        for w in (self.btn_settings, self.lbl_zoom, self.btn_in,
                  self.btn_out, self.btn_clear_range, self.lbl_range):
            row2.addWidget(w)
        row2.addStretch(1)
        for w in (self.btn_export, self.btn_export_ok, self.btn_export_cancel):
            row2.addWidget(w)
        root.addLayout(row2)

        self.setCentralWidget(central)
        self._set_controls_enabled(False)

    def _icon_button(self, icon, tooltip, slot, repeat=False):
        b = QPushButton(icon)
        b.setToolTip(tooltip)
        b.setFixedWidth(62)
        b.clicked.connect(slot)
        if repeat:
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(300)
            b.setAutoRepeatInterval(55)
        return b

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

    def _open_shortcuts(self):
        dlg = ShortcutDialog(self, self.input_cfg, self.lang_pref)
        if dlg.exec() == QDialog.Accepted:
            self.input_cfg.save(self.settings)
            self._apply_bindings()
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
        self.btn_clear_range.setToolTip(tr("tip_clear"))
        self.btn_clear_range.setText(tr("btn_clear"))
        self.btn_export.setText(tr("btn_export"))
        self.btn_export_ok.setText(tr("btn_export_ok"))
        self.btn_export_cancel.setText(tr("btn_export_cancel"))
        name = os.path.basename(self.reader.path) if self.reader else ""
        self.setWindowTitle(f"{APP_NAME} — {name}" if name else APP_NAME)
        self.video.update()        # プレースホルダ再描画
        self.filmstrip.update()
        self.waveform.update()

    # ------------------------------------------------------------------
    def _set_controls_enabled(self, on: bool):
        # 音量(vol_slider)はファイル前から操作できるよう常に有効
        for w in (self.btn_prev, self.btn_play, self.btn_next, self.btn_slow,
                  self.btn_fast, self.btn_in, self.btn_out,
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
        self.video.clear_crop()
        maxframe = self.reader.total_frames - 1
        for bar in (self.filmstrip, self.waveform):
            bar.clear()
            bar.set_range(maxframe)
            bar.set_marks(None, None)
        self._set_controls_enabled(True)
        self.setWindowTitle(f"{APP_NAME} — {os.path.basename(path)}")
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
                f"   {fmt_time(t)} / {fmt_time(total_t)}   {self.reader.fps:.0f}fps")
        self.lbl_speed.setText(f"{SPEEDS[self.speed_idx]:g}x")
        self._update_range_label()

    def _update_range_label(self):
        a = "·" if self.in_frame is None else str(self.in_frame)
        b = "·" if self.out_frame is None else str(self.out_frame)
        self.lbl_range.setText(f"[ {a} – {b} ]")

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
        self.btn_play.setText("⏸")
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
        self.play_timer.stop()
        self.btn_play.setText("▶")
        self._pending = None
        if self.producer:
            self.producer.stop()
        if self.audio:
            self.audio.stop()

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
        self._show_frame(value)
        if self.playing:
            self._pending = None
            if self.producer:
                self.producer.start(value)   # 先読みを新しい位置から
            self._rebase_clock()
            self._sync_audio()   # 再生は止めず、新しい位置から音声を鳴らし直す

    # --- 拡大表示 -------------------------------------------------------
    def _on_zoom(self, z: float):
        self.lbl_zoom.setText(f"{z:.2f}x")

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
        self.in_frame = max(0, min(frame, self.reader.total_frames - 1))
        if self.out_frame is not None and self.out_frame <= self.in_frame:
            self.out_frame = None
        self._update_marks()

    def set_out_at(self, frame: int):
        if not self.reader:
            return
        self.out_frame = max(0, min(frame, self.reader.total_frames - 1))
        if self.in_frame is not None and self.in_frame >= self.out_frame:
            self.in_frame = None
        self._update_marks()

    def clear_range(self):
        self.in_frame = None
        self.out_frame = None
        self._update_marks()

    def _update_marks(self):
        self._update_range_label()
        self.filmstrip.set_marks(self.in_frame, self.out_frame)
        self.waveform.set_marks(self.in_frame, self.out_frame)

    # --- 縦型書き出しフロー ---------------------------------------------
    def begin_export(self):
        """縦型枠を画面に出して配置モードへ。"""
        if not self.reader:
            return
        self._pause()
        self.video.start_crop()
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

    def confirm_export(self):
        if not self.reader or not self.video.crop_rect:
            return
        a = self.in_frame if self.in_frame is not None else 0
        b = self.out_frame if self.out_frame is not None else self.reader.total_frames - 1
        x, y, w, h = self.video.crop_rect
        crop_text = f"{w} x {h}  ({x},{y})"
        range_text = (f"{a} – {b}  "
                      f"({fmt_time(self.reader.index_to_time(a))} – "
                      f"{fmt_time(self.reader.index_to_time(b))})")

        dlg = ExportDialog(self, crop_text, range_text)
        if dlg.exec() != QDialog.Accepted:
            return
        out_w, out_h = dlg.resolution()
        include_audio = dlg.include_audio() and self.reader.has_audio

        default_name = os.path.splitext(os.path.basename(self.reader.path))[0] + "_vertical.mp4"
        save_dir = self.settings.value("last_save_dir", "", str) \
            or self.settings.value("last_open_dir", "", str)
        start_path = os.path.join(save_dir, default_name) if save_dir else default_name
        dst, _ = QFileDialog.getSaveFileName(
            self, tr("save_title"), start_path, "MP4 (*.mp4)")
        if not dst:
            return
        self.settings.setValue("last_save_dir", os.path.dirname(dst))

        t_start = self.reader.index_to_time(a)
        t_end = self.reader.index_to_time(b + 1)
        self.video.end_crop()
        self._exit_export_mode()
        self._run_export(dst, self.video.crop_rect, t_start, t_end,
                         out_w, out_h, include_audio)

    def _run_export(self, dst, crop, t_start, t_end, out_w, out_h, audio):
        self.progress = QProgressDialog(tr("progress_label"), tr("cancel"), 0, 100, self)
        self.progress.setWindowTitle(tr("progress_title"))
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.setMinimumDuration(0)
        self.progress.setValue(0)

        self.thread = QThread()
        self.worker = ExportWorker(self.reader.path, dst, crop, t_start, t_end,
                                   out_w, out_h, audio)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(
            lambda f: self.progress.setValue(int(f * 100)))
        self.worker.finished.connect(self._on_export_done)
        self.progress.canceled.connect(self.worker.cancel)
        self.thread.start()

    def _on_export_done(self, ok: bool, msg: str):
        self.thread.quit()
        self.thread.wait()
        self.progress.reset()
        if ok:
            QMessageBox.information(self, tr("done_title"), f"{tr('done_msg')}\n{msg}")
        else:
            QMessageBox.warning(self, tr("fail_title"), msg)

    # --- ウィンドウ / ドラッグ&ドロップ ---------------------------------
    VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv",
                  ".ts", ".m4v", ".wmv", ".mpg", ".mpeg", ".m2ts")

    def changeEvent(self, event):
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._activated_ts = time.perf_counter()
        super().changeEvent(event)

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
    PRESETS = [("1080 x 1920 (FHD)", 1080, 1920),
               ("720 x 1280 (HD)", 720, 1280),
               ("1440 x 2560 (QHD)", 1440, 2560)]

    def __init__(self, parent=None, crop_text="", range_text=""):
        super().__init__(parent)
        self.setWindowTitle(tr("export_settings_title"))
        form = QFormLayout(self)
        if crop_text:
            form.addRow(tr("lbl_crop_range"), QLabel(crop_text))
        if range_text:
            form.addRow(tr("lbl_time_range"), QLabel(range_text))
        self.combo = QComboBox()
        for name, _, _ in self.PRESETS:
            self.combo.addItem(name)
        form.addRow(tr("lbl_resolution"), self.combo)
        self.chk_audio = QCheckBox(tr("chk_audio"))
        self.chk_audio.setChecked(True)
        form.addRow("", self.chk_audio)
        note = QLabel(tr("export_note"))
        note.setWordWrap(True)
        form.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def resolution(self):
        _, w, h = self.PRESETS[self.combo.currentIndex()]
        return w, h

    def include_audio(self):
        return self.chk_audio.isChecked()
