"""再生用の先読みデコーダ (プリフェッチ)。

別スレッドでフレームを順次デコードし、上限付きキューに溜める。
UI スレッドは音声/壁時計のクロックに合わせてキューから取り出して描画するだけ。
これによりデコード時間が表示のカクつきに直結しなくなる (GOM 等と同方式)。
"""
from __future__ import annotations

import queue
import threading

from reader import VideoReader


class FramePrefetcher:
    def __init__(self, path: str, maxsize: int = 12):
        # 再生専用に独立した VideoReader (UI のステップ用とは別コンテナ)
        # 前方ストリーミングのみなのでキャッシュは不要 (メモリ節約)
        self.reader = VideoReader(path, cache=False)
        self.fps = self.reader.fps
        self.total = self.reader.total_frames
        self.q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._start_index = 0

    # ------------------------------------------------------------------
    def start(self, index: int):
        """index から先読みデコードを開始 (実行中なら一旦止めて再開)。"""
        self.stop()
        self._stop.clear()
        self._start_index = max(0, min(index, self.total - 1))
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._drain()

    def close(self):
        self.stop()
        self.reader.close()

    # ------------------------------------------------------------------
    def _drain(self):
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    def _run(self):
        idx = self._start_index
        total = self.total
        while not self._stop.is_set() and idx < total:
            try:
                arr = self.reader.get_frame(idx)   # 連続取得は next() で高速
            except Exception:
                break
            item = (idx, arr)
            # キューが満杯なら表示が追いつくまで待つ (stop も監視)
            while not self._stop.is_set():
                try:
                    self.q.put(item, timeout=0.05)
                    break
                except queue.Full:
                    continue
            idx += 1
