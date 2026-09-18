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
    "tip_update":   {"en": "A new version is available — click to open the download page",
                     "ja": "新しいバージョンがあります — クリックでダウンロードページを開く"},
    "chk_updates":  {"en": "Check for updates on startup",
                     "ja": "起動時にアップデートを確認する"},
    "tip_in":       {"en": "Set In point",         "ja": "IN点を設定"},
    "tip_out":      {"en": "Set Out point",        "ja": "OUT点を設定"},
    "tip_add_clip": {"en": "Deselect the clip (In/Out then create new clips). "
                           "Clips are created automatically once In and Out are both set",
                     "ja": "クリップの選択を解除（IN/OUT で新規作成へ戻る）。"
                           "IN と OUT が揃うとクリップは自動で確定されます"},
    "menu_delete_clip": {"en": "Delete clip #{n}", "ja": "クリップ #{n} を削除"},
    "btn_preview":   {"en": "Preview", "ja": "プレビュー"},
    "lbl_editing":   {"en": "Editing #{n}: In/Out move its ends. Click empty space to deselect.",
                      "ja": "#{n} を編集中：IN/OUT でその両端を修正。空いている所をクリックで解除"},
    "tip_speed_pick": {"en": "Click to choose playback speed", "ja": "クリックで再生速度を選択"},
    "tip_row_play":  {"en": "Play this clip from its In point (also selects it)",
                      "ja": "このクリップを先頭から再生（選択もされます）"},
    "tip_row_speed": {"en": "Export speed of this clip", "ja": "このクリップの書き出し速度"},
    "tip_clip_prev": {"en": "Previous clip (jump to its In & play; In/Out then edit it)",
                      "ja": "前のクリップ（INへ移動して再生。IN/OUTでそのクリップを修正）"},
    "tip_clip_next": {"en": "Next clip (jump to its In & play; In/Out then edit it)",
                      "ja": "次のクリップ（INへ移動して再生。IN/OUTでそのクリップを修正）"},
    "tip_preview":   {"en": "Preview: play only the clips, in order (press again to stop)",
                      "ja": "プレビュー：クリップの範囲だけを番号順に連続再生（もう一度押すと停止）"},
    "tip_clear":    {"en": "Clear all In/Out and clips",
                     "ja": "IN/OUTとクリップを全てクリア"},
    "tip_undo_clear": {"en": "Undo the last Clear all (Ctrl+Z)",
                       "ja": "直前の全クリアを取り消す（Ctrl+Z）"},
    # button texts
    "btn_clear":         {"en": "Clear all",          "ja": "全クリア"},
    "btn_undo_clear":    {"en": "Undo",               "ja": "取り消す"},
    "btn_export":        {"en": "Export clip",        "ja": "動画書き出し"},
    "btn_export_ok":     {"en": "Export",             "ja": "書き出し"},
    "tip_export_ok":     {"en": "Export the framed area of the clips (opens the settings dialog)",
                          "ja": "枠で囲んだ範囲をクリップごとに書き出す（設定画面が開きます）"},
    "tip_export_cancel": {"en": "Leave export mode without exporting",
                          "ja": "書き出しをやめて枠を消す"},
    "menu_export_v":     {"en": "Vertical 9:16 (Shorts / TikTok)", "ja": "縦型 9:16（ショート / TikTok）"},
    "menu_export_h":     {"en": "Horizontal 16:9 (YouTube / X)",   "ja": "横型 16:9（YouTube / X）"},
    "tip_row_stop":      {"en": "Stop", "ja": "停止"},
    "lbl_pending_hint":  {"en": "set OUT →", "ja": "OUT を打つと確定"},
    "lbl_pending_hint_in": {"en": "set IN →", "ja": "IN を打つと確定"},
    "lbl_pending_hint_start": {"en": "press I (or Ctrl-click the timeline) to set IN and start a clip",
                               "ja": "I キー（またはタイムラインを Ctrl+クリック）で IN を打つと切り抜き開始"},
    "tip_pending_out":   {"en": "Press O or Alt-click the timeline to set the Out point",
                          "ja": "O キー、またはタイムラインを Alt+クリックで OUT を設定"},
    "tip_side_toggle":   {"en": "Show / hide the clip panel", "ja": "クリップパネルを開く / 閉じる"},
    "tip_frame_field":   {"en": "Click to type a frame number, or drag up/down to nudge it (wheel works too)",
                          "ja": "クリックで数値を入力、上下にドラッグで増減（ホイールでも可）"},
    "crop_hint":         {"en": "Drag to move (snaps to center) / drag a corner to resize / Alt+drag keeps the center",
                          "ja": "ドラッグで移動（中央に吸着）／ 角をドラッグで拡大縮小／ Alt+ドラッグで中心固定"},
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
    "export_settings_title": {"en": "Export settings", "ja": "書き出し設定"},
    "lbl_crop_range": {"en": "Crop area",         "ja": "切り出し範囲"},
    "lbl_time_range": {"en": "Time range",        "ja": "時間範囲"},
    "lbl_resolution": {"en": "Output resolution", "ja": "出力解像度"},
    "lbl_clips":      {"en": "Clips / speed",     "ja": "クリップ / 速度"},
    "lbl_out_len":    {"en": "Output length",     "ja": "出力の長さ"},
    "note_speed":     {"en": "Speed is applied per clip. Audio keeps its pitch; "
                             "slow motion repeats frames.",
                       "ja": "速度はクリップごとに適用されます。音声は音程を保ったまま伸縮し、"
                             "スローはコマを複製します。"},
    "lbl_est_size":   {"en": "Estimated size",    "ja": "推定サイズ"},
    "chk_audio":      {"en": "Include audio",     "ja": "音声を含める"},
    "chk_transition": {"en": "Fade transition between clips",
                       "ja": "クリップ間にフェードのトランジション"},
    "export_note":    {"en": "The framed area is scaled to the output size (black bars if needed).",
                       "ja": "枠で囲んだ範囲を出力サイズに合わせます（必要なら黒帯）。"},
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
