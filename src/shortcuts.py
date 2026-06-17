"""キーボード/マウスの割り当て設定。

各アクションにキー(QKeySequence)とマウスジェスチャを割り当て、QSettings に保存。
デフォルトは従来の操作。ユーザーは設定ダイアログで自由に再割り当てできる。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog, QGridLayout, QLabel, QComboBox, QKeySequenceEdit,
    QDialogButtonBox, QPushButton, QFrame,
)

from i18n import tr
from version import APP_NAME, APP_VERSION

# id, アイコン, 既定キー, 既定マウスジェスチャ (表示名は i18n の act_<id>)
DEFAULT_ACTIONS = [
    ("open",        "📂",  "Ctrl+O", ""),
    ("play_pause",  "▶",   "Space",  "LeftClick"),
    ("frame_prev",  "◁",   "Left",   "Shift+WheelDown"),
    ("frame_next",  "▷",   "Right",  "Shift+WheelUp"),
    ("speed_down",  "≪",   "Down",   "WheelDown"),
    ("speed_up",    "≫",   "Up",     "WheelUp"),
    ("speed_reset", "1x",  "",       "MiddleClick"),
    ("zoom_in",     "🔍+", "",       "Ctrl+WheelUp"),
    ("zoom_out",    "🔍-", "",       "Ctrl+WheelDown"),
    ("set_in",      "IN",  "I",      ""),
    ("set_out",     "OUT", "O",      ""),
    ("file_prev",   "⏪",  "Ctrl+Left",  ""),
    ("file_next",   "⏩",  "Ctrl+Right", ""),
]

MOUSE_GESTURES = [
    "", "LeftClick", "MiddleClick",
    "WheelUp", "WheelDown",
    "Shift+WheelUp", "Shift+WheelDown",
    "Ctrl+WheelUp", "Ctrl+WheelDown",
    "Alt+WheelUp", "Alt+WheelDown",
]


class InputConfig:
    def __init__(self):
        self.keys = {a[0]: a[2] for a in DEFAULT_ACTIONS}
        self.mouse = {a[0]: a[3] for a in DEFAULT_ACTIONS}

    def load(self, settings):
        for aid, _, dk, dm in DEFAULT_ACTIONS:
            self.keys[aid] = settings.value(f"bind/{aid}/key", dk, str)
            self.mouse[aid] = settings.value(f"bind/{aid}/mouse", dm, str)

    def save(self, settings):
        for aid in self.keys:
            settings.setValue(f"bind/{aid}/key", self.keys[aid])
            settings.setValue(f"bind/{aid}/mouse", self.mouse[aid])

    def reset(self):
        self.keys = {a[0]: a[2] for a in DEFAULT_ACTIONS}
        self.mouse = {a[0]: a[3] for a in DEFAULT_ACTIONS}

    def mouse_to_action(self) -> dict:
        """ジェスチャ名 -> アクションid (同一ジェスチャは後勝ち)。"""
        m = {}
        for aid, g in self.mouse.items():
            if g:
                m[g] = aid
        return m


LANG_OPTIONS = [("auto", "lang_auto"), ("en", "English"), ("ja", "日本語")]


class ShortcutDialog(QDialog):
    """言語選択 + アクションごとのキー/マウス割り当て設定ダイアログ。"""

    def __init__(self, parent, config: InputConfig, lang_pref: str = "auto"):
        super().__init__(parent)
        self.setWindowTitle(tr("settings_title"))
        self.config = config
        self.lang_pref = lang_pref
        self._rows = {}

        grid = QGridLayout(self)
        # 言語選択
        grid.addWidget(self._header(tr("language")), 0, 0)
        self.lang_combo = QComboBox()
        for code, label in LANG_OPTIONS:
            self.lang_combo.addItem(tr(label) if code == "auto" else label, code)
        idx = next((i for i, (c, _) in enumerate(LANG_OPTIONS) if c == lang_pref), 0)
        self.lang_combo.setCurrentIndex(idx)
        grid.addWidget(self.lang_combo, 0, 1, 1, 2)

        line0 = QFrame()
        line0.setFrameShape(QFrame.HLine)
        grid.addWidget(line0, 1, 0, 1, 3)

        grid.addWidget(self._header(tr("col_action")), 2, 0)
        grid.addWidget(self._header(tr("col_key")), 2, 1)
        grid.addWidget(self._header(tr("col_mouse")), 2, 2)

        for r, (aid, icon, _, _) in enumerate(DEFAULT_ACTIONS, start=3):
            grid.addWidget(QLabel(f"{icon}  {tr('act_' + aid)}"), r, 0)

            kse = QKeySequenceEdit(QKeySequence(self.config.keys[aid]))
            kse.setMaximumSequenceLength(1)
            grid.addWidget(kse, r, 1)

            combo = QComboBox()
            combo.addItems(MOUSE_GESTURES)
            cur = self.config.mouse.get(aid, "")
            combo.setCurrentIndex(MOUSE_GESTURES.index(cur)
                                  if cur in MOUSE_GESTURES else 0)
            grid.addWidget(combo, r, 2)

            self._rows[aid] = (kse, combo)

        nrow = len(DEFAULT_ACTIONS) + 3
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        reset_btn = QPushButton(tr("restore_defaults"))
        bb.addButton(reset_btn, QDialogButtonBox.ResetRole)
        reset_btn.clicked.connect(self._restore_defaults)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        grid.addWidget(bb, nrow, 0, 1, 3)

        ver = QLabel(f"{APP_NAME}  v{APP_VERSION}")
        ver.setStyleSheet("color:#888;")
        ver.setAlignment(Qt.AlignCenter)
        grid.addWidget(ver, nrow + 1, 0, 1, 3)

    def _header(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight:bold;")
        return lbl

    def _restore_defaults(self):
        for aid, _, dk, dm in DEFAULT_ACTIONS:
            kse, combo = self._rows[aid]
            kse.setKeySequence(QKeySequence(dk))
            combo.setCurrentIndex(MOUSE_GESTURES.index(dm) if dm in MOUSE_GESTURES else 0)

    def _accept(self):
        for aid, (kse, combo) in self._rows.items():
            self.config.keys[aid] = kse.keySequence().toString()
            self.config.mouse[aid] = combo.currentText()
        self.lang_pref = self.lang_combo.currentData()
        self.accept()
