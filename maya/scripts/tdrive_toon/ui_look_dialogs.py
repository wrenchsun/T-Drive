"""ルックタブから開くダイアログ（E-5 プリセットの保存、E-6 影色をベース色から作る、E-10 テクスチャの拡大表示）。

計算・検証は Qt 非依存の look_layout.py / presets.py に置き、ここは画面だけ。
"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from . import params, presets, project
from .look_layout import GROUP_LABELS, build_groups


class PresetSaveDialog(QtWidgets.QDialog):
    """名前と、含めるグループ（既定は全部）を選ぶ。"""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("プリセットを保存")
        v = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText("ファイル名（例: 影と線だけ）")
        self.label = QtWidgets.QLineEdit()
        self.label.setPlaceholderText("一覧に出す名前（省略すると上と同じ）")
        form.addRow("名前:", self.name)
        form.addRow("表示名:", self.label)
        v.addLayout(form)
        v.addWidget(QtWidgets.QLabel("含めるグループ（チェックしたグループの値だけ保存・適用されます）:"))
        self.checks: dict[str, QtWidgets.QCheckBox] = {}
        grid = QtWidgets.QGridLayout()
        for i, group in enumerate(build_groups()):
            cb = QtWidgets.QCheckBox(GROUP_LABELS.get(group, group))
            cb.setChecked(True)
            self.checks[group] = cb
            grid.addWidget(cb, i // 2, i % 2)
        v.addLayout(grid)
        row = QtWidgets.QHBoxLayout()
        all_b = QtWidgets.QPushButton("すべて")
        none_b = QtWidgets.QPushButton("なし")
        all_b.clicked.connect(lambda *_: self._set_all(True))
        none_b.clicked.connect(lambda *_: self._set_all(False))
        row.addWidget(all_b)
        row.addWidget(none_b)
        row.addStretch(1)
        v.addLayout(row)
        self.message = QtWidgets.QLabel()
        self.message.setStyleSheet("color: #ff9090;")
        v.addWidget(self.message)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def _set_all(self, on: bool) -> None:
        for cb in self.checks.values():
            cb.setChecked(on)

    def groups(self) -> list[str]:
        return [g for g, cb in self.checks.items() if cb.isChecked()]

    def _on_accept(self) -> None:
        try:
            presets.safe_name(self.name.text())
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        if not self.groups():
            self.message.setText("含めるグループを 1 つ以上選んでください")
            return
        self.accept()


def ask_overwrite(parent: QtWidgets.QWidget, name: str) -> bool:
    """同名のプリセットを上書きするか。既定は「いいえ」。"""
    ans = QtWidgets.QMessageBox.question(
        parent, "T-Drive Toon", f"プリセット「{name}」は既にあります。上書きしますか？",
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No,
    )
    return ans == QtWidgets.QMessageBox.Yes
