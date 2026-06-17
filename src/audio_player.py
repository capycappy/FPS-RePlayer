"""再生時の音声出力 (QAudioSink + PyAV)。

倍速/低速にも追従する varispeed 再生。音声を rate/speed にリサンプルし、
出力デバイスは元の rate で消費するので、再生速度=speed・音程も speed 倍
(レコードの早回し/遅回しと同じ) になる。波形はそのまま再生される。
"""
from __future__ import annotations

import av
from PySide6.QtCore import QObject, QTimer
from PySide6.QtMultimedia import QAudioSink, QAudioFormat, QMediaDevices


class AudioPlayer(QObject):
    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.container = av.open(path)
        self.astream = self.container.streams.audio[0]
        self.rate = int(self.astream.rate or 48000)

        fmt = QAudioFormat()
        fmt.setSampleRate(self.rate)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)
        self._fmt = fmt

        self.sink = None
        self.io = None
        self.resampler = None
        self._gen = None
        self._buf = bytearray()
        self._t0 = 0.0
        self._speed = 1.0
        self._playing = False
        self._volume = 1.0

        self.timer = QTimer(self)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self._feed)

    # ------------------------------------------------------------------
    def play(self, t_start: float, speed: float = 1.0):
        """位置 t_start(秒) から速度 speed で音声再生を開始/やり直し。"""
        self.stop()
        try:
            # rate/speed にリサンプル → 元 rate で消費すると speed 倍速・音程も変化
            out_rate = max(1, int(round(self.rate / max(0.01, speed))))
            self.resampler = av.AudioResampler(
                format="s16", layout="stereo", rate=out_rate)
            self.container.seek(int(t_start * av.time_base), backward=True)
            self._gen = self.container.decode(self.astream)
            self._t0 = t_start
            self._speed = speed
            self._buf = bytearray()
            # 開始位置まで読み飛ばし
            self._skip_to(t_start)

            dev = QMediaDevices.defaultAudioOutput()
            self.sink = QAudioSink(dev, self._fmt)
            self.sink.setVolume(self._volume)
            self.io = self.sink.start()
            self._playing = True
            self.timer.start()
        except Exception:
            self.stop()

    def set_volume(self, vol: float):
        self._volume = max(0.0, min(1.0, vol))
        if self.sink is not None:
            self.sink.setVolume(self._volume)

    def is_playing(self) -> bool:
        return self._playing and self.sink is not None

    def position_sec(self) -> float:
        """実際に再生済みの位置(元動画の秒)。映像同期のマスタークロック。"""
        if self.sink is None:
            return self._t0
        played = self.sink.processedUSecs() / 1_000_000.0  # 出力(実)時間
        return self._t0 + played * self._speed

    def stop(self):
        self._playing = False
        self.timer.stop()
        if self.sink is not None:
            try:
                self.sink.stop()
            except Exception:
                pass
        self.sink = None
        self.io = None
        self._gen = None
        self._buf = bytearray()

    def close(self):
        self.stop()
        try:
            self.container.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _skip_to(self, t_start: float):
        for frame in self._gen:
            if frame.time is None or frame.time + 0.05 >= t_start:
                self._buf += self._to_bytes(frame)
                break

    def _to_bytes(self, frame) -> bytes:
        frame.pts = None
        out = bytearray()
        for r in self.resampler.resample(frame):
            out += r.to_ndarray().tobytes()
        return bytes(out)

    def _feed(self):
        if not self._playing or self.io is None or self.sink is None:
            return
        free = self.sink.bytesFree()
        if free <= 0:
            return
        while len(self._buf) < free:
            frame = next(self._gen, None)
            if frame is None:
                break
            self._buf += self._to_bytes(frame)
        if not self._buf:
            return
        chunk = bytes(self._buf[:free])
        written = self.io.write(chunk)
        if written > 0:
            del self._buf[:written]
