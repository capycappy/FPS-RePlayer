"""選択範囲を 9:16 縦型動画として書き出す (PyAV)。

crop -> scale(アスペクト維持) -> pad(黒帯) で out_w x out_h の縦型に収める。
音声は指定の時間範囲を切り出して AAC で同梱する。
重い処理なので QThread のワーカーとして実行する。
"""
from __future__ import annotations

from fractions import Fraction

import av
from PySide6.QtCore import QObject, Signal


def _even(v: int) -> int:
    v = int(v)
    return v - (v % 2)


class ExportWorker(QObject):
    progress = Signal(float)        # 0.0 - 1.0
    finished = Signal(bool, str)    # (成功, メッセージ)

    def __init__(self, src, dst, crop, t_start, t_end,
                 out_w=1080, out_h=1920, include_audio=True):
        super().__init__()
        self.src = src
        self.dst = dst
        self.crop = crop            # (x, y, w, h) ソース座標
        self.t_start = t_start
        self.t_end = t_end
        self.out_w = _even(out_w)
        self.out_h = _even(out_h)
        self.include_audio = include_audio
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

        out = av.open(self.dst, "w")

        rate = v_in.average_rate or v_in.guessed_rate or Fraction(30, 1)
        v_out = out.add_stream("libx264", rate=rate)
        v_out.width = self.out_w
        v_out.height = self.out_h
        v_out.pix_fmt = "yuv420p"
        v_out.options = {"crf": "18", "preset": "medium"}
        self._vcount = 0
        self._v_tb = Fraction(1, 1) / rate   # 出力フレームを 0 始まりで再採番

        # 映像フィルタグラフ
        graph = av.filter.Graph()
        buf = graph.add_buffer(template=v_in)
        crop_f = graph.add("crop", f"{cw}:{ch}:{cx}:{cy}")
        scale_f = graph.add(
            "scale", f"{self.out_w}:{self.out_h}:force_original_aspect_ratio=decrease")
        pad_f = graph.add(
            "pad", f"{self.out_w}:{self.out_h}:(ow-iw)/2:(oh-ih)/2:color=black")
        fmt_f = graph.add("format", "yuv420p")
        sink = graph.add("buffersink")
        buf.link_to(crop_f)
        crop_f.link_to(scale_f)
        scale_f.link_to(pad_f)
        pad_f.link_to(fmt_f)
        fmt_f.link_to(sink)
        graph.configure()

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

        # 範囲先頭へシーク
        inp.seek(int(self.t_start * av.time_base), backward=True)

        streams = [v_in] + ([a_in] if a_in is not None else [])
        duration = max(0.001, self.t_end - self.t_start)
        v_done = False
        a_done = a_in is None

        for frame in inp.decode(*streams):
            if self._cancel:
                break
            if isinstance(frame, av.VideoFrame):
                if frame.time is None:
                    continue
                if frame.time < self.t_start:
                    continue
                if frame.time > self.t_end:
                    v_done = True
                else:
                    graph.push(frame)
                    self._drain_video(graph, sink, v_out, out)
                    self.progress.emit(
                        min(0.99, (frame.time - self.t_start) / duration))
            else:  # AudioFrame
                if frame.time is None or a_out is None:
                    continue
                if frame.time < self.t_start:
                    continue
                if frame.time > self.t_end:
                    a_done = True
                else:
                    self._feed_audio(frame, resampler, fifo, a_out, out)
            if v_done and a_done:
                break

        if not self._cancel:
            # フラッシュ
            graph.push(None)
            self._drain_video(graph, sink, v_out, out, flush=True)
            for pkt in v_out.encode():
                out.mux(pkt)
            if a_out is not None:
                self._flush_audio(resampler, fifo, a_out, out)
                for pkt in a_out.encode():
                    out.mux(pkt)

        out.close()
        inp.close()

    # ------------------------------------------------------------------
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

    def _feed_audio(self, frame, resampler, fifo, a_out, out):
        frame.pts = None
        for r in resampler.resample(frame):
            fifo.write(r)
        while fifo.samples >= 1024:
            chunk = fifo.read(1024)
            chunk.pts = None
            for pkt in a_out.encode(chunk):
                out.mux(pkt)

    def _flush_audio(self, resampler, fifo, a_out, out):
        if fifo.samples > 0:
            chunk = fifo.read()
            chunk.pts = None
            for pkt in a_out.encode(chunk):
                out.mux(pkt)
