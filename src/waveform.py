"""非推奨: 機能は timeline.py に統合されました。後方互換のための再エクスポート。"""
from timeline import (  # noqa: F401
    compute_envelope,
    compute_thumbnails,
    WaveformWorker,
    FilmstripWorker,
    WaveformBar,
    FilmstripBar,
)
