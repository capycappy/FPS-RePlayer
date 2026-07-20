"""軽量な多言語(英語/日本語)対応。

tr(key) で現在の言語の文字列を返す。初期言語は Windows のロケールから自動判定。
設定で "auto" / "en" / "ja" を選べる。
"""
from __future__ import annotations

from PySide6.QtCore import QLocale

_lang = "en"


def detect() -> str:
    """システムロケールから言語を判定 (日本語以外は英語)。"""
    try:
        if QLocale.system().language() == QLocale.Japanese:
            return "ja"
    except Exception:
        pass
    return "en"


def resolve(pref: str) -> str:
    return detect() if pref not in ("en", "ja") else pref


def set_lang(code: str):
    global _lang
    _lang = code if code in ("en", "ja") else "en"


def current() -> str:
    return _lang


def tr(key: str) -> str:
    entry = T.get(key)
    if not entry:
        return key
    return entry.get(_lang, entry.get("en", key))


T = {
    # tooltips (toolbar icons)
    "tip_open":     {"en": "Open file",            "ja": "ファイルを開く"},
    "tip_prev":     {"en": "Step back 1 frame",    "ja": "1コマ戻し"},
    "tip_play":     {"en": "Play / Pause",         "ja": "再生 / 一時停止"},
    "tip_next":     {"en": "Step forward 1 frame", "ja": "1コマ送り"},
    "tip_slower":   {"en": "Slower",               "ja": "遅く"},
    "tip_faster":   {"en": "Faster",               "ja": "速く"},
    "tip_volume":   {"en": "Volume",               "ja": "音量"},
    "tip_settings": {"en": "Settings",             "ja": "設定"},
    "tip_in":       {"en": "Set In point",         "ja": "IN点を設定"},
    "tip_out":      {"en": "Set Out point",        "ja": "OUT点を設定"},
    "tip_add_clip": {"en": "Add In–Out as clip / deselect clip",
                     "ja": "IN–OUT をクリップとして追加／クリップ選択を解除"},
    "tip_clip_prev": {"en": "Previous clip (jump to its In & play; In/Out then edit it)",
                      "ja": "前のクリップ（INへ移動して再生。IN/OUTでそのクリップを修正）"},
    "tip_clip_next": {"en": "Next clip (jump to its In & play; In/Out then edit it)",
                      "ja": "次のクリップ（INへ移動して再生。IN/OUTでそのクリップを修正）"},
    "tip_preview":   {"en": "Preview: play only the clips, in order (press again to stop)",
                      "ja": "プレビュー：クリップの範囲だけを番号順に連続再生（もう一度押すと停止）"},
    "tip_clear":    {"en": "Clear In/Out and clips", "ja": "IN/OUTとクリップをクリア"},
    # button texts
    "btn_clear":         {"en": "Clear",              "ja": "クリア"},
    "btn_export":        {"en": "Export vertical",    "ja": "縦型動画書き出し"},
    "btn_export_ok":     {"en": "Export this range",  "ja": "この範囲で書き出し"},
    "btn_export_cancel": {"en": "Cancel",             "ja": "キャンセル"},
    # video placeholder
    "placeholder": {"en": "Click   or   Drag & Drop   to open a video",
                    "ja": "クリック   または   ドラッグ&ドロップ   で動画を開く"},
    "timeline_wave":  {"en": "Analyzing audio...",     "ja": "波形を解析中..."},
    "timeline_thumb": {"en": "Generating thumbnails...", "ja": "サムネイル生成中..."},
    # file dialogs / messages
    "menu_open":         {"en": "Open file...",      "ja": "ファイルを開く..."},
    "menu_recent":       {"en": "Recent files",      "ja": "最近のファイル"},
    "menu_no_recent":    {"en": "(no recent files)", "ja": "(履歴なし)"},
    "menu_clear_recent": {"en": "Clear recent",      "ja": "履歴をクリア"},
    "open_title":   {"en": "Open video",        "ja": "動画を開く"},
    "filter_video": {"en": "Video files",       "ja": "動画ファイル"},
    "filter_all":   {"en": "All files",         "ja": "すべてのファイル"},
    "err_title":    {"en": "Error",             "ja": "エラー"},
    "err_open":     {"en": "Could not open:",   "ja": "読み込めませんでした:"},
    "save_title":   {"en": "Save vertical video", "ja": "縦型動画を保存"},
    "done_title":   {"en": "Done",              "ja": "完了"},
    "done_msg":     {"en": "Exported:",         "ja": "書き出しました:"},
    "fail_title":   {"en": "Export failed",     "ja": "書き出し失敗"},
    "cancel":       {"en": "Cancel",            "ja": "キャンセル"},
    "progress_label": {"en": "Exporting...",    "ja": "書き出し中..."},
    "progress_title": {"en": "Vertical export", "ja": "縦型書き出し"},
    # export settings dialog
    "export_settings_title": {"en": "Vertical export settings", "ja": "縦型書き出し設定"},
    "lbl_crop_range": {"en": "Crop area",         "ja": "切り出し範囲"},
    "lbl_time_range": {"en": "Time range",        "ja": "時間範囲"},
    "lbl_resolution": {"en": "Output resolution", "ja": "出力解像度"},
    "lbl_clips":      {"en": "Clips",             "ja": "クリップ"},
    "lbl_est_size":   {"en": "Estimated size",    "ja": "推定サイズ"},
    "chk_audio":      {"en": "Include audio",     "ja": "音声を含める"},
    "chk_transition": {"en": "Fade transition between clips",
                       "ja": "クリップ間にフェードのトランジション"},
    "export_note":    {"en": "Centers the selection and adds black bars to make it vertical.",
                       "ja": "選択範囲を中央に配置し、上下に黒帯を付けて縦型にします。"},
    # settings (shortcut) dialog
    "settings_title":   {"en": "Settings",          "ja": "設定"},
    "language":         {"en": "Language",          "ja": "言語"},
    "lang_auto":        {"en": "Auto (system)",     "ja": "自動 (システム)"},
    "col_action":       {"en": "Action",            "ja": "操作"},
    "col_key":          {"en": "Key",               "ja": "キー"},
    "col_mouse":        {"en": "Mouse",             "ja": "マウス"},
    "restore_defaults": {"en": "Restore defaults",  "ja": "デフォルトに戻す"},
    # action names
    "act_open":        {"en": "Open file",     "ja": "ファイルを開く"},
    "act_play_pause":  {"en": "Play / Pause",  "ja": "再生 / 一時停止"},
    "act_frame_prev":  {"en": "Step back",     "ja": "コマ戻し"},
    "act_frame_next":  {"en": "Step forward",  "ja": "コマ送り"},
    "act_speed_down":  {"en": "Slower",        "ja": "遅く"},
    "act_speed_up":    {"en": "Faster",        "ja": "速く"},
    "act_speed_reset": {"en": "Normal speed",  "ja": "等速"},
    "act_zoom_in":     {"en": "Zoom in",       "ja": "拡大"},
    "act_zoom_out":    {"en": "Zoom out",      "ja": "縮小"},
    "act_set_in":      {"en": "Set In point",  "ja": "IN点"},
    "act_set_out":     {"en": "Set Out point", "ja": "OUT点"},
    "act_file_prev":   {"en": "Previous file", "ja": "前のファイル"},
    "act_file_next":   {"en": "Next file",     "ja": "次のファイル"},
}
