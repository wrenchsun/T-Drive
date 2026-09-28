"""A/B タブ（docs/05 §5、R-3）: 2 案を即時切替・同条件キャプチャで並べて比較・差分表示・採用。"""

from __future__ import annotations

import re

from PySide6 import QtCore, QtGui, QtWidgets

from . import environment, look, params, session
from .ui_look import COMMON_ROWS

VARIANT_NAME = re.compile(r"^[A-Za-z0-9_]+$")
TOGGLE_KEY = "Alt+T"


def _warn(parent: QtWidgets.QWidget, exc: Exception) -> None:
    QtWidgets.QMessageBox.warning(parent, "T-Drive Toon", str(exc))


class ABTab(QtWidgets.QWidget):
    open_in_look = QtCore.Signal(str)  # 差分の行クリック → ルックタブでそのマテリアルを開く

    def __init__(self, s: session.Session) -> None:
        super().__init__()
        self.session = s
        self.shots: list[dict] = []
        v = QtWidgets.QVBoxLayout(self)

        # ---- A / B の選択
        row = QtWidgets.QHBoxLayout()
        self.combo_a = QtWidgets.QComboBox()
        self.combo_b = QtWidgets.QComboBox()
        for label, combo, slot in (("A:", self.combo_a, 0), ("B:", self.combo_b, 1)):
            row.addWidget(QtWidgets.QLabel(label))
            combo.activated.connect(lambda _i, c=combo, sl=slot: self._run(self.session.set_ab, sl, c.currentData()))
            row.addWidget(combo, 1)
        add = QtWidgets.QPushButton("バリアント追加…")
        add.clicked.connect(self.on_add)
        row.addWidget(add)
        v.addLayout(row)

        # ---- 表示切替
        row = QtWidgets.QHBoxLayout()
        self.btn_a = QtWidgets.QPushButton("A を表示")
        self.btn_b = QtWidgets.QPushButton("B を表示")
        self.btn_toggle = QtWidgets.QPushButton(f"切替 ({TOGGLE_KEY})")
        self.btn_a.clicked.connect(lambda: self._run(self.session.show_ab, 0))
        self.btn_b.clicked.connect(lambda: self._run(self.session.show_ab, 1))
        self.btn_toggle.clicked.connect(lambda: self._run(self.session.toggle_ab))
        for b in (self.btn_a, self.btn_b, self.btn_toggle):
            row.addWidget(b)
        self.shown = QtWidgets.QLabel()
        row.addWidget(self.shown, 1)
        v.addLayout(row)
        # Maya のビューポートにフォーカスがあっても効くようアプリ全体のショートカットにする
        self.shortcut = QtGui.QShortcut(QtGui.QKeySequence(TOGGLE_KEY), self)
        self.shortcut.setContext(QtCore.Qt.ApplicationShortcut)
        self.shortcut.activated.connect(lambda: self._run(self.session.toggle_ab))

        # ---- キャプチャ
        row = QtWidgets.QHBoxLayout()
        cap = QtWidgets.QPushButton("並べてキャプチャ")
        cap.setToolTip("同じカメラ・ライト・解像度（1920×1080）で A と B を撮る")
        cap.clicked.connect(self.on_capture)
        row.addWidget(cap)
        self.all_angles = QtWidgets.QCheckBox("正面 / 3/4 / 横 / 後ろ を一括")
        row.addWidget(self.all_angles)
        self.target = QtWidgets.QComboBox()
        self.target.addItem("全身", "all")
        self.target.addItem("顔", "head")
        row.addWidget(self.target)
        row.addStretch(1)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["左右", "ワイプ"])
        self.mode.currentIndexChanged.connect(self._update_compare)
        row.addWidget(QtWidgets.QLabel("比較:"))
        row.addWidget(self.mode)
        v.addLayout(row)

        self.shot_select = QtWidgets.QComboBox()
        self.shot_select.currentIndexChanged.connect(self._update_compare)
        v.addWidget(self.shot_select)
        self.compare = CompareView()
        v.addWidget(self.compare, 3)
        self.wipe = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.wipe.setRange(0, 1000)
        self.wipe.setValue(500)
        self.wipe.valueChanged.connect(self._update_compare)
        v.addWidget(self.wipe)

        # ---- 差分と採用
        self.diff = QtWidgets.QTableWidget(0, 4)
        self.diff.setHorizontalHeaderLabels(["マテリアル", "項目", "A", "B"])
        self.diff.horizontalHeader().setStretchLastSection(True)
        self.diff.verticalHeader().setVisible(False)
        self.diff.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.diff.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.diff.cellDoubleClicked.connect(lambda r, _c: self.open_in_look.emit(self.diff.item(r, 0).text()))
        v.addWidget(self.diff, 2)
        row = QtWidgets.QHBoxLayout()
        self.btn_promote = QtWidgets.QPushButton("B を採用（base に確定）")
        self.btn_promote.clicked.connect(self.on_promote)
        self.btn_delete = QtWidgets.QPushButton("B を削除")
        self.btn_delete.clicked.connect(self.on_delete)
        row.addStretch(1)
        row.addWidget(self.btn_promote)
        row.addWidget(self.btn_delete)
        v.addLayout(row)

    # -------------------------------------------------------------- 表示
    def refresh(self) -> None:
        lk = self.session.look
        self.setEnabled(lk is not None)
        if lk is None:
            return
        names = look.variant_names(lk)
        for combo, slot in ((self.combo_a, 0), (self.combo_b, 1)):
            combo.blockSignals(True)
            combo.clear()
            for n in names:
                combo.addItem(n if n == look.BASE else f"{n}: {lk['variants'][n]['label']}", n)
            combo.setCurrentIndex(max(0, combo.findData(self.session.ab[slot])))
            combo.blockSignals(False)
        a, b = self.session.ab
        self.shown.setText(f"表示中: <b>{self.session.shown}</b>" + ("（A）" if self.session.shown == a else "（B）" if self.session.shown == b else ""))
        is_variant_b = b != look.BASE
        self.btn_promote.setEnabled(is_variant_b)
        self.btn_delete.setEnabled(is_variant_b)
        self.btn_promote.setText(f"B（{b}）を採用（base に確定）")
        self.btn_delete.setText(f"B（{b}）を削除")

        rows = self.session.diff_ab()
        self.diff.setRowCount(len(rows))
        for i, (mat, key, va, vb) in enumerate(rows):
            for j, text in enumerate((mat, _key_label(key), _fmt(va), _fmt(vb))):
                self.diff.setItem(i, j, QtWidgets.QTableWidgetItem(text))
        self.diff.resizeColumnsToContents()

    def _update_compare(self) -> None:
        i = self.shot_select.currentIndex()
        if not (0 <= i < len(self.shots)):
            self.compare.set_images(None, None, "左右", 0.5)
            return
        shot = self.shots[i]
        mode = self.mode.currentText()
        self.wipe.setVisible(mode == "ワイプ")
        self.compare.set_images(shot["A"], shot["B"], mode, self.wipe.value() / 1000)

    # -------------------------------------------------------------- 操作
    def _run(self, fn, *args):
        try:
            return fn(*args)
        except Exception as exc:
            _warn(self, exc)
            return None

    def on_add(self) -> None:
        dlg = AddVariantDialog(self, look.variant_names(self.session.require()))
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        name, label, copy_from = dlg.values()
        if self._run(self.session.add_variant, name, label, copy_from) is None and name in self.session.look["variants"]:
            self.session.set_ab(1, name)

    def on_capture(self) -> None:
        yaws = list(environment.CAMERA_PRESETS.values())[:4] if self.all_angles.isChecked() else None
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            shots = self._run(self.session.capture_ab, yaws, self.target.currentData())
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if not shots:
            return
        self.shots = shots
        names = {v: k for k, v in environment.CAMERA_PRESETS.items()}
        self.shot_select.blockSignals(True)
        self.shot_select.clear()
        for s in shots:
            self.shot_select.addItem("現在のカメラ" if s["yaw"] is None else names.get(s["yaw"], f"{s['yaw']}°"))
        self.shot_select.blockSignals(False)
        self._update_compare()

    def on_promote(self) -> None:
        b = self.session.ab[1]
        r = QtWidgets.QMessageBox.question(self, "採用", f"B（{b}）の内容を base に確定し、B を削除します。よろしいですか？\n（ルックタブの 元に戻す で取り消せます）")
        if r == QtWidgets.QMessageBox.Yes:
            self._run(self.session.promote, b)

    def on_delete(self) -> None:
        b = self.session.ab[1]
        r = QtWidgets.QMessageBox.question(self, "削除", f"バリアント B（{b}）を削除しますか？（元に戻す で取り消せます）")
        if r == QtWidgets.QMessageBox.Yes:
            self._run(self.session.delete_variant, b)


_COMMON_LABELS = {k: label for k, label, *_ in COMMON_ROWS}


def _key_label(key: str) -> str:
    p = params.PARAMS_BY_UNITY.get(key)
    label = p.label if p else _COMMON_LABELS.get(key, "")
    return f"{label}（{key}）" if label else key


def _fmt(v) -> str:
    if isinstance(v, list):
        return "(" + ", ".join(f"{x:.3g}" if isinstance(x, float) else str(x) for x in v) + ")"
    if isinstance(v, float):
        return f"{v:.3g}"
    return "（なし）" if v is None else str(v)


class CompareView(QtWidgets.QLabel):
    """A/B 画像の左右並べ / ワイプ表示。"""

    def __init__(self) -> None:
        super().__init__("「並べてキャプチャ」で A と B を同じ条件で撮って比べます")
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setMinimumHeight(220)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
        self._args = (None, None, "左右", 0.5)

    def set_images(self, a: str | None, b: str | None, mode: str, wipe: float) -> None:
        self._args = (a, b, mode, wipe)
        self._render()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt)
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        a, b, mode, wipe = self._args
        if not (a and b):
            return
        pa, pb = QtGui.QPixmap(a), QtGui.QPixmap(b)
        w, h = pa.width(), pa.height()
        if mode == "左右":
            canvas = QtGui.QPixmap(w * 2, h)
            p = QtGui.QPainter(canvas)
            p.drawPixmap(0, 0, pa)
            p.drawPixmap(w, 0, pb)
            self._labels(p, [(0, "A"), (w, "B")], h)
        else:
            canvas = QtGui.QPixmap(w, h)
            x = int(w * wipe)
            p = QtGui.QPainter(canvas)
            p.drawPixmap(0, 0, pa)
            p.drawPixmap(x, 0, pb, x, 0, w - x, h)
            p.setPen(QtGui.QPen(QtGui.QColor("#ffcc00"), 3))
            p.drawLine(x, 0, x, h)
            self._labels(p, [(0, "A"), (w - 120, "B")], h)
        p.end()
        self.setPixmap(canvas.scaled(self.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))

    @staticmethod
    def _labels(p: QtGui.QPainter, items, h: int) -> None:
        font = p.font()
        font.setPixelSize(max(24, h // 20))
        font.setBold(True)
        p.setFont(font)
        for x, text in items:
            p.fillRect(x + 10, 10, 90, font.pixelSize() + 16, QtGui.QColor(0, 0, 0, 160))
            p.setPen(QtGui.QColor("#ffffff"))
            p.drawText(x + 24, 18 + font.pixelSize(), text)


class AddVariantDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget, existing: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("バリアント追加")
        self.existing = existing
        form = QtWidgets.QFormLayout(self)
        suggestion = next(c for c in "BCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in existing)
        self.name = QtWidgets.QLineEdit(suggestion)
        form.addRow("名前（英数字・_）", self.name)
        self.label = QtWidgets.QLineEdit()
        self.label.setPlaceholderText("例: 顔の影を弱く")
        form.addRow("説明", self.label)
        self.copy_from = QtWidgets.QComboBox()
        self.copy_from.addItems(existing)
        form.addRow("複製元", self.copy_from)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        n = self.name.text().strip()
        if not VARIANT_NAME.match(n) or n in self.existing:
            _warn(self, ValueError("名前は英数字と _ のみ、既存・base と重ならないものにしてください"))
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return self.name.text().strip(), self.label.text().strip(), self.copy_from.currentText()
