"""選択範囲を 9:16 縦型動画として書き出す (PyAV)。

複数の時間区間 (クリップ) を時系列に連結して1本の縦型動画にする。
各クリップは crop -> scale(アスペクト維持) -> pad(黒帯) で out_w x out_h に収める。
transition=True のときはクリップ境界にフェード (アウト/イン 各 FADE_SEC 秒) を挟む。
音声は AAC で同梱し、フェードは波形に直接ゲインを掛けて適用する。
重い処理なので QThread のワーカーとして実行する。
"""
from __future__ import annotations

from fractions import Fraction

import av
import numpy as np
from PySide6.QtCore import QObject, Signal

FADE_SEC = 0.3   # トランジション(フェード)の長さ


def _even(v: int) -> int:
    v = int(v)
    return v - (v % 2)


class ExportWorker(QObject):
    progress = Signal(float)        # 0.0 - 1.0
    finished = Signal(bool, str)    # (成功, メッセージ)

    def __init__(self, src, dst, crop, segments,
                 out_w=1080, out_h=1920, include_audio=True,
                 transition=False):
        super().__init__()
        self.src = src
        self.dst = dst
        self.crop = crop            # (x, y, w, h) ソース座標
        # [(t_start, t_end), ...] 秒。時系列順に連結される
        self.segments = [(float(a), float(b)) for a, b in segments]
        self.out_w = _even(out_w)
        self.out_h = _even(out_h)
        self.include_audio = include_audio
        self.transition = transition
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            self._run()
            if self._cancel:
                self.finished.emit(False, "キャンセルしました")
            else:
                self.progress.emit(1.0)
                self.finished.emit(True, self.dst)
        except Exception as e:  # noqa
            self.finished.emit(False, f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    def _run(self):
        inp = av.open(self.src)
        v_in = inp.streams.video[0]
        v_in.thread_type = "AUTO"
        a_in = inp.streams.audio[0] if (self.include_audio and inp.streams.audio) else None

        cx, cy, cw, ch = self.crop
        cx, cy = _even(cx), _even(cy)
        cw, ch = _even(cw), _even(ch)
        cw = max(2, min(cw, v_in.codec_context.width - cx))
        ch = max(2, min(ch, v_in.codec_context.height - cy))
        self._crop_args = (cw, ch, cx, cy)

        out = av.open(self.dst, "w")

        rate = v_in.average_rate or v_in.guessed_rate or Fraction(30, 1)
        v_out = out.add_stream("libx264", rate=rate)
        v_out.width = self.out_w
        v_out.height = self.out_h
        v_out.pix_fmt = "yuv420p"
        v_out.options = {"crf": "18", "preset": "medium"}
        self._vcount = 0
        self._v_tb = Fraction(1, 1) / rate   # 出力フレームを 0 始まりで再採番

        # 音声 (任意)
        a_out = None
        resampler = fifo = None
        if a_in is not None:
            try:
                a_out = out.add_stream("aac", rate=a_in.rate)
                resampler = av.AudioResampler(
                    format="fltp", layout="stereo", rate=a_in.rate)
                fifo = av.AudioFifo()
            except Exception:
                a_in = a_out = None

        total = max(0.001, sum(b - a for a, b in self.segments))
        done_before = 0.0
        nseg = len(self.segments)

        for i, (t0, t1) in enumerate(self.segments):
            if self._cancel:
                break
            seg_dur = max(0.001, t1 - t0)
            fade_in = self.transition and i > 0
            fade_out = self.transition and i < nseg - 1
            graph, sink = self._build_graph(v_in, seg_dur, fade_in, fade_out)

            # 音声フェード用の状態 (クリップ内のサンプル位置)
            self._a_pos = 0
            self._a_total = int(seg_dur * a_in.rate) if a_in is not None else 0
            self._a_fade_n = int(FADE_SEC * a_in.rate) if a_in is not None else 0
            self._a_fade_in = fade_in
            self._a_fade_out = fade_out

            inp.seek(int(t0 * av.time_base), backward=True)
            streams = [v_in] + ([a_in] if a_in is not None else [])
            v_done = False
            a_done = a_in is None

            for frame in inp.decode(*streams):
                if self._cancel:
                    break
                if isinstance(frame, av.VideoFrame):
                    if frame.time is None:
                        continue
                    if frame.time < t0:
                        continue
                    if frame.time > t1:
                        v_done = True
                    else:
                        graph.push(frame)
                        self._drain_video(graph, sink, v_out, out)
                        self.progress.emit(min(
                            0.99, (done_before + frame.time - t0) / total))
                else:  # AudioFrame
                    if frame.time is None or a_out is None:
                        continue
                    if frame.time < t0:
                        continue
                    if frame.time > t1:
                        a_done = True
                    else:
                        self._feed_audio(frame, resampler, fifo, a_out, out)
                if v_done and a_done:
                    break

            if self._cancel:
                break
            # このクリップの映像グラフをフラッシュ (音声fifoは連結のため継続)
            graph.push(None)
            self._drain_video(graph, sink, v_out, out, flush=True)
            done_before += seg_dur

        if not self._cancel:
            for pkt in v_out.encode():
                out.mux(pkt)
            if a_out is not None:
                self._flush_audio(resampler, fifo, a_out, out)
                for pkt in a_out.encode():
                    out.mux(pkt)

        out.close()
        inp.close()

    # ------------------------------------------------------------------
    def _build_graph(self, v_in, seg_dur, fade_in, fade_out):
        """クリップ1本ぶんの映像フィルタグラフを構築する。"""
        cw, ch, cx, cy = self._crop_args
        graph = av.filter.Graph()
        last = graph.add_buffer(template=v_in)

        def chain(name, args):
            nonlocal last
            f = graph.add(name, args)
            last.link_to(f)
            last = f

        chain("setpts", "PTS-STARTPTS")   # クリップ内時刻を0始まりに (fadeのst基準)
        chain("crop", f"{cw}:{ch}:{cx}:{cy}")
        chain("scale",
              f"{self.out_w}:{self.out_h}:force_original_aspect_ratio=decrease")
        chain("pad",
              f"{self.out_w}:{self.out_h}:(ow-iw)/2:(oh-ih)/2:color=black")
        if fade_in:
            chain("fade", f"t=in:st=0:d={FADE_SEC}")
        if fade_out:
            chain("fade", f"t=out:st={max(0.0, seg_dur - FADE_SEC)}:d={FADE_SEC}")
        chain("format", "yuv420p")
        sink = graph.add("buffersink")
        last.link_to(sink)
        graph.configure()
        return graph, sink

    def _drain_video(self, graph, sink, v_out, out, flush=False):
        while True:
            try:
                f = sink.pull()
            except av.error.BlockingIOError:
                break
            except (av.error.EOFError, EOFError):
                break
            f.pts = self._vcount
            f.time_base = self._v_tb
            self._vcount += 1
            for pkt in v_out.encode(f):
                out.mux(pkt)

    # ------------------------------------------------------------------
    def _feed_audio(self, frame, resampler, fifo, a_out, out):
        frame.pts = None
        for r in resampler.resample(frame):
            r = self._apply_audio_fade(r)
            fifo.write(r)
        while fifo.samples >= 1024:
            chunk = fifo.read(1024)
            chunk.pts = None
            for pkt in a_out.encode(chunk):
                out.mux(pkt)

    def _apply_audio_fade(self, r):
        """クリップ境界のフェードを波形ゲインとして適用 (fltp planar)。"""
        n = r.samples
        pos = self._a_pos
        self._a_pos = pos + n
        if not (self._a_fade_in or self._a_fade_out) or self._a_fade_n <= 0:
            return r
        fN = self._a_fade_n
        need_in = self._a_fade_in and pos < fN
        need_out = self._a_fade_out and (pos + n) > (self._a_total - fN)
        if not (need_in or need_out):
            return r
        arr = r.to_ndarray()                     # (ch, n) float32
        idx = np.arange(pos, pos + n, dtype=np.float64)
        gain = np.ones(n)
        if self._a_fade_in:
            gain = np.minimum(gain, np.clip(idx / fN, 0.0, 1.0))
        if self._a_fade_out:
            gain = np.minimum(gain, np.clip((self._a_total - idx) / fN, 0.0, 1.0))
        arr = (arr * gain.astype(np.float32)).astype(np.float32)
        nf = av.AudioFrame.from_ndarray(np.ascontiguousarray(arr),
                                        format="fltp", layout="stereo")
        nf.sample_rate = r.sample_rate
        return nf

    def _flush_audio(self, resampler, fifo, a_out, out):
        if fifo.samples > 0:
            chunk = fifo.read()
            chunk.pts = None
            for pkt in a_out.encode(chunk):
                out.mux(pkt)
