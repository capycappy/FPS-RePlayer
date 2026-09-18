"""動画ごとの IN/OUT・クリップ位置の永続化。

%APPDATA%/FPSRePlayer/clips.json に、動画の絶対パスをキーにして保存する。
クリップは [in, out, speed] (speed は書き出し速度、省略時 1.0)。
1エントリ約100バイトの軽量データ。最大 MAX_ENTRIES 件で古いものから間引く。
"""
from __future__ import annotations

import json
import os
import time


class ClipStore:
    MAX_ENTRIES = 500

    def __init__(self):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        self.dir = os.path.join(base, "FPSRePlayer")
        self.path = os.path.join(self.dir, "clips.json")
        self._data = {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            if not isinstance(self._data, dict):
                self._data = {}
        except Exception:
            self._data = {}

    # ------------------------------------------------------------------
    @staticmethod
    def _key(video_path: str) -> str:
        return os.path.normcase(os.path.abspath(video_path))

    def get(self, video_path: str):
        """{'segments': [[a,b,speed],...], 'in': int|None, 'out': int|None} or None"""
        return self._data.get(self._key(video_path))

    def set(self, video_path: str, segments, in_frame, out_frame):
        key = self._key(video_path)
        if not segments and in_frame is None and out_frame is None:
            if key in self._data:          # 空になったらエントリごと削除
                del self._data[key]
                self._write()
            return
        segs = []
        for seg in segments:
            speed = float(seg[2]) if len(seg) > 2 else 1.0
            segs.append([int(seg[0]), int(seg[1]), speed])
        self._data[key] = {
            "segments": segs,
            "in": None if in_frame is None else int(in_frame),
            "out": None if out_frame is None else int(out_frame),
            "ts": int(time.time()),
        }
        self._prune()
        self._write()

    # ------------------------------------------------------------------
    def _prune(self):
        if len(self._data) <= self.MAX_ENTRIES:
            return
        items = sorted(self._data.items(),
                       key=lambda kv: kv[1].get("ts", 0), reverse=True)
        self._data = dict(items[: self.MAX_ENTRIES])

    def _write(self):
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass   # 保存失敗は致命的ではない (次回の変更で再試行)
