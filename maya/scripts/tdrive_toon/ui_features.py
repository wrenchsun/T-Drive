"""機能タブ（docs/11 §4、4-3）: 機能ごとのオン/オフ。

オフの機能は効果なしの値でプレビュー・出力され、Unity ではプロジェクト専用シェーダーから取り除かれる。
保存されている値は消えない（オンに戻すと復活する）。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import features, look, session

IMPL_LABELS = {features.SH: "シェーダー", features.ME: "メッシュ", features.CO: "コンポーネント", features.RF: "Renderer Feature"}
PREVIEW_LABELS = {"full": "○", "partial": "△", "none": "×"}
COLUMNS = ("オン", "機能", "技術", "Unity での実現", "Maya プレビュー", "使用中")


class FeaturesTab(QtWidgets.QWidget):
    def __init__(self, s: session.Session) -> None:
        super().__init__()
        self.session = s
        v = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "使う機能だけをオンにします。オフの機能は「効果なし」で表示・出力され、"
            "Unity ではプロジェクト専用シェーダーから取り除かれて軽くなります。"
            "オフにしても調整値は消えません（オンに戻すと復活）。"
        )
        note.setWordWrap(True)
        v.addWidget(note)

        top = QtWidgets.QHBoxLayout()
        unused = QtWidgets.QPushButton("使っていない機能をオフにする")
        unused.setToolTip("値がすべて効果なし（既定値）の機能をまとめてオフにする。Ctrl+Z で戻せる")
        unused.clicked.connect(self._disable_unused)
        top.addWidget(unused)
        top.addStretch(1)
        self.summary = QtWidgets.QLabel()
        top.addWidget(self.summary)
        v.addLayout(top)

        self.table = QtWidgets.QTableWidget(len(features.FEATURES), len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.checks: dict[str, QtWidgets.QCheckBox] = {}
        self.used_items: dict[str, QtWidgets.QTableWidgetItem] = {}
        for row, f in enumerate(features.FEATURES):
            cell = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(cell)
            h.setContentsMargins(6, 0, 0, 0)
            check = QtWidgets.QCheckBox()
            check.setEnabled(not f.required)
            check.clicked.connect(lambda on, fid=f.id: self._toggle(fid, on))
            h.addWidget(check)
            self.table.setCellWidget(row, 0, cell)
            self.checks[f.id] = check
            name = QtWidgets.QTableWidgetItem(f.label + ("（必須）" if f.required else ""))
            name.setToolTip(_contents(f))
            self.table.setItem(row, 1, name)
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(" ".join(f.techniques)))
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(" + ".join(IMPL_LABELS[i] for i in f.impl)))
            pv = QtWidgets.QTableWidgetItem(PREVIEW_LABELS[f.maya_preview])
            pv.setTextAlignment(QtCore.Qt.AlignCenter)
            pv.setToolTip({"full": "Maya でも同じ見た目で確認できる", "partial": "Maya では簡易表示（Unity で最終確認）",
                           "none": "Maya では確認できない（Unity で確認）"}[f.maya_preview])
            self.table.setItem(row, 4, pv)
            used = QtWidgets.QTableWidgetItem()
            used.setTextAlignment(QtCore.Qt.AlignCenter)
            self.table.setItem(row, 5, used)
            self.used_items[f.id] = used
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        v.addWidget(self.table, 1)

    # -------------------------------------------------------------- 表示
    def refresh(self) -> None:
        lk = self.session.look
        self.setEnabled(lk is not None)
        if lk is None:
            self.summary.setText("")
            return
        used = look.used_features(lk)
        n_on = 0
        for f in features.FEATURES:
            on = look.enabled(lk, f.id)
            n_on += on
            c = self.checks[f.id]
            c.blockSignals(True)
            c.setChecked(on)
            c.blockSignals(False)
            item = self.used_items[f.id]
            if f.id in used and not on:
                item.setText("値あり（オフ中）")
                item.setForeground(QtGui.QBrush(QtGui.QColor("#f0a040")))
                item.setToolTip("調整値が保存されているがオフなので効果なし。オンにすると復活する")
            elif f.id in used:
                item.setText("●")
                item.setForeground(QtGui.QBrush())
                item.setToolTip("値が既定値と違う（= 使っている）")
            else:
                item.setText("")
                item.setToolTip("値がすべて既定値（オフにしても見た目は変わらない）" if on else "")
        self.summary.setText(f"オン {n_on} / {len(features.FEATURES)}")

    # -------------------------------------------------------------- 操作
    def _toggle(self, feature_id: str, on: bool) -> None:
        try:
            self.session.set_feature(feature_id, on)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "T-Drive Toon", str(exc))
            self.refresh()

    def _disable_unused(self) -> None:
        off = self.session.disable_unused_features()
        labels = [features.BY_ID[fid].label for fid in off]
        QtWidgets.QMessageBox.information(
            self, "T-Drive Toon", "オフにしました:\n" + "\n".join(labels) if labels else "使っていない機能はありません"
        )


def _contents(f: features.Feature) -> str:
    items = list(f.params) + [f"characterSettings.{s}" for s in f.settings]
    return "属するもの: " + (", ".join(items) if items else "（頂点カラーなどメッシュのデータ）")
