"""フレーム単位の前後シークができる動画リーダー (PyAV ベース)。

H.264/H.265 等のフレーム間圧縮があっても、キーフレームへシークして
そこから順方向にデコードすることで、任意のフレームを正確に取得する。

コマ戻し対策:
直近にデコードしたフレームを LRU キャッシュに保持する。コマ戻し時は
目的フレーム周辺をまとめてキャッシュするため、連続したコマ戻しは
毎回シーク&再デコードせずキャッシュから即返せる。
"""
from __future__ import annotations

import collections

import av
import numpy as np

# フレームキャッシュのメモリ上限 (フレーム枚数はこの予算と解像度から決める)
CACHE_BUDGET_BYTES = 512 * 1024 * 1024


class VideoReader:
    def __init__(self, path: str, cache: bool = True):
        self.path = path
        self.container = av.open(path)
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"

        self.time_base = float(self.stream.time_base)

        rate = self.stream.average_rate or self.stream.guessed_rate
        self.fps = float(rate) if rate else 30.0

        self.width = self.stream.codec_context.width
        self.height = self.stream.codec_context.height

        # 総フレーム数の推定
        if self.stream.frames and self.stream.frames > 0:
            self.total_frames = int(self.stream.frames)
        else:
            dur = None
            if self.stream.duration is not None:
                dur = self.stream.duration * self.time_base
            elif self.container.duration is not None:
                dur = self.container.duration / av.time_base
            self.total_frames = int(round(dur * self.fps)) if dur else 0
        if self.total_frames <= 0:
            self.total_frames = 1

        self.has_audio = len(self.container.streams.audio) > 0

        self._gen = None          # 現在のデコードジェネレータ
        self._gen_index = -2      # _gen が最後に出したフレーム番号
        self._cur_index = -1      # 直近に返したフレーム番号
        self._cur_array = None    # 直近フレーム (RGB ndarray)

        # LRU キャッシュ
        self._cache_on = cache
        frame_bytes = max(1, self.width * self.height * 3)
        self._cache_max = max(16, CACHE_BUDGET_BYTES // frame_bytes)
        self._cache: "collections.OrderedDict[int, np.ndarray]" = \
            collections.OrderedDict()

    # ------------------------------------------------------------------
    def _cache_put(self, idx: int, arr: np.ndarray):
        if not self._cache_on:
            return
        if idx in self._cache:
            self._cache.move_to_end(idx)
            return
        self._cache[idx] = arr
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)

    def _cache_get(self, idx: int):
        arr = self._cache.get(idx)
        if arr is not None:
            self._cache.move_to_end(idx)
        return arr

    # ------------------------------------------------------------------
    def _frame_index(self, frame) -> int:
        if frame.time is not None:
            return int(round(frame.time * self.fps))
        if frame.pts is not None:
            return int(round(frame.pts * self.time_base * self.fps))
        return self._gen_index + 1

    @staticmethod
    def _to_rgb(frame) -> np.ndarray:
        return np.ascontiguousarray(frame.to_ndarray(format="rgb24"))

    def _seek_to(self, index: int, cache_window: int):
        """キーフレームへシークし index まで順方向デコード。
        index-cache_window .. index-1 のフレームは変換してキャッシュする。"""
        target_sec = index / self.fps
        seek_pts = int(target_sec / self.time_base)
        start = self.stream.start_time or 0
        self.container.seek(seek_pts + start, stream=self.stream,
                            backward=True, any_frame=False)
        self._gen = self.container.decode(self.stream)
        cache_from = index - cache_window if cache_window > 0 else index
        last = None
        for frame in self._gen:
            fidx = self._frame_index(frame)
            self._gen_index = fidx
            if fidx >= index:
                return frame
            if cache_window > 0 and fidx >= cache_from:
                self._cache_put(fidx, self._to_rgb(frame))
            last = frame
        return last  # 末尾を超えた場合は最後のフレーム

    # ------------------------------------------------------------------
    def get_frame(self, index: int, cache_window: int = 0) -> np.ndarray:
        """フレーム番号 index の RGB ndarray (H,W,3 uint8) を返す。
        cache_window>0 のときは目的フレーム周辺もまとめてキャッシュ(コマ戻し用)。"""
        index = max(0, min(index, self.total_frames - 1))

        cached = self._cache_get(index)
        if cached is not None:
            self._cur_array = cached
            self._cur_index = index
            return cached

        frame = None
        # 順方向の連続再生は next() で高速に
        if self._gen is not None and index == self._gen_index + 1:
            try:
                frame = next(self._gen)
                self._gen_index = index
            except StopIteration:
                frame = None

        if frame is None:
            frame = self._seek_to(index, cache_window)

        if frame is None:
            arr = np.zeros((self.height, self.width, 3), np.uint8)
        else:
            arr = self._to_rgb(frame)

        self._cache_put(index, arr)
        self._cur_array = arr
        self._cur_index = index
        return arr

    def index_to_time(self, index: int) -> float:
        return index / self.fps

    def close(self):
        self._cache.clear()
        try:
            self.container.close()
        except Exception:
            pass
