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


class ComposeProgress:
    """合成の作成中の進捗バーと「中止」（AN-F-21）。`K.compose(progress=…)` に渡す。短い処理（0.3 秒未満）では出ない。"""

    def __init__(self, parent: QtWidgets.QWidget | None, text: str = "合成を作っています…") -> None:
        self.dialog = QtWidgets.QProgressDialog(text, "中止", 0, 100, parent)
        self.dialog.setWindowTitle("T-Drive Animation")
        self.dialog.setWindowModality(QtCore.Qt.WindowModal)
        self.dialog.setMinimumDuration(300)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)

    def __call__(self, done: int, total: int) -> bool:
        self.dialog.setMaximum(max(1, total))
        self.dialog.setValue(done)
        QtWidgets.QApplication.processEvents()
        return not self.dialog.wasCanceled()

    def close(self) -> None:
        self.dialog.close()
        self.dialog.deleteLater()
