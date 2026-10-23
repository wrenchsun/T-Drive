"""Animation タブの画面の共通部品。"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

WARN_STYLE = "background-color: #6a4210; color: #ffe2b0; padding: 4px; border-radius: 3px;"
INFO_STYLE = "padding: 4px;"


class MessageLabel(QtWidgets.QLabel):
    """各タブの結果・警告の行。⚠ / ⛔ で始まる文字はオレンジの背景で目立たせる（AN-F-17）。AnimPanel がタブの外・最上部へ移す。"""

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self.setWordWrap(True)
        self.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        if not text:
            self.setStyleSheet("")
        elif text.lstrip().startswith(("⚠", "⛔")):
            self.setStyleSheet(WARN_STYLE)
        else:
            self.setStyleSheet(INFO_STYLE)
