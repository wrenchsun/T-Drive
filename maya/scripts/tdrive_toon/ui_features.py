"""機能タブ（docs/11 §4、4-3 / 4-12）: 機能ごとのオン/オフ。

オフの機能は効果なしの値でプレビュー・出力され、Unity ではプロジェクト専用シェーダーから取り除かれる。
保存されている値は消えない（オンに戻すと復活する）。

左の一覧で部位・マテリアルを選ぶと、マテリアルの値で効く機能を「全体に従う / オン / オフ」で上書きできる
（シェーダー単位。Unity では機能の組み合わせごとにシェーダーが作られる。docs/11 §2.1・§3）。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import features, look, roles, session

IMPL_LABELS = {features.SH: "シェーダー", features.ME: "メッシュ", features.CO: "コンポーネント", features.RF: "Renderer Feature"}
PREVIEW_LABELS = {"full": "○", "partial": "△", "none": "×"}
COLUMNS = ("全体", "選択中の部位", "機能", "Unity での実現", "Maya プレビュー", "使用中")
COL_ALL, COL_SEL, COL_NAME, COL_IMPL, COL_PREVIEW, COL_USED = range(6)
INHERIT, MIXED = "inherit", "mixed"


class FeaturesTab(QtWidgets.QWidget):
    def __init__(self, s: session.Session) -> None:
        super().__init__()
        self.session = s
        self.target: str | None = None  # None = キャラクター全体
        v = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "使う機能だけをオンにします。オフの機能は「効果なし」で表示・出力され、"
            "Unity ではプロジェクト専用シェーダーから取り除かれて軽くなります。"
            "オフにしても調整値は消えません（オンに戻すと復活）。"
            "左で部位・マテリアルを選ぶと、その部位だけオン / オフにできます（シェーダー単位）。"
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

        split = QtWidgets.QSplitter()
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["対象"])
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._on_target)
        split.addWidget(self.tree)

        self.table = QtWidgets.QTableWidget(len(features.FEATURES), len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.checks: dict[str, QtWidgets.QCheckBox] = {}
        self.combos: dict[str, QtWidgets.QComboBox] = {}
        self.used_items: dict[str, QtWidgets.QTableWidgetItem] = {}
        for row, f in enumerate(features.FEATURES):
            cell = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(cell)
            h.setContentsMargins(6, 0, 0, 0)
            check = QtWidgets.QCheckBox()
            check.setEnabled(not f.required)
            # PySide6 は「必須引数 + 既定値付き引数」の lambda を引数なしで呼ぶ → 状態はチェックから読む（CLAUDE.md）
            check.clicked.connect(lambda *_, fid=f.id, c=check: self._toggle(fid, c.isChecked()))
            h.addWidget(check)
            self.table.setCellWidget(row, COL_ALL, cell)
            self.checks[f.id] = check
            if f.material_scope:
                combo = QtWidgets.QComboBox()
                combo.activated.connect(lambda *_, fid=f.id, c=combo: self._set_material(fid, c.currentData()))
                self.table.setCellWidget(row, COL_SEL, combo)
                self.combos[f.id] = combo
            else:
                item = QtWidgets.QTableWidgetItem("キャラクター単位" if not f.required else "必須")
                item.setForeground(QtGui.QBrush(QtGui.QColor("#888")))
                item.setToolTip("キャラクター全体で切り替える機能（部位ごとには切り替えられない）")
                self.table.setItem(row, COL_SEL, item)
            name = QtWidgets.QTableWidgetItem(f.label + ("（必須）" if f.required else ""))
            name.setToolTip(_contents(f))
            self.table.setItem(row, COL_NAME, name)
            self.table.setItem(row, COL_IMPL, QtWidgets.QTableWidgetItem(" + ".join(IMPL_LABELS[i] for i in f.impl)))
            pv = QtWidgets.QTableWidgetItem(PREVIEW_LABELS[f.maya_preview])
            pv.setTextAlignment(QtCore.Qt.AlignCenter)
            pv.setToolTip({"full": "Maya でも同じ見た目で確認できる", "partial": "Maya では簡易表示（Unity で最終確認）",
                           "none": "Maya では確認できない（Unity で確認）"}[f.maya_preview])
            self.table.setItem(row, COL_PREVIEW, pv)
            used = QtWidgets.QTableWidgetItem()
            used.setTextAlignment(QtCore.Qt.AlignCenter)
            self.table.setItem(row, COL_USED, used)
            self.used_items[f.id] = used
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(COL_SEL, 190)
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QtWidgets.QHeaderView.Stretch)
        split.addWidget(self.table)
        split.setSizes([180, 620])
        v.addWidget(split, 1)

    # -------------------------------------------------------------- 表示
    def refresh(self) -> None:
        lk = self.session.look
        self.setEnabled(lk is not None)
        if lk is None:
            self.summary.setText("")
            self.tree.clear()
            return
        if self.target and self.target not in lk["parts"] and self.target not in lk["materials"]:
            self.target = None
        self._fill_tree(lk)
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
        self._refresh_combos()
        n_ov = sum(1 for m in lk["materials"].values() if m.get(look.FEATURE_OVERRIDES))
        self.summary.setText(f"オン {n_on} / {len(features.FEATURES)}" + (f"（部位ごとの上書き: マテリアル {n_ov} 件）" if n_ov else ""))

    def _fill_tree(self, lk: dict) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        whole = QtWidgets.QTreeWidgetItem(["キャラクター全体"])
        whole.setData(0, QtCore.Qt.UserRole, None)
        self.tree.addTopLevelItem(whole)
        if self.target is None:
            self.tree.setCurrentItem(whole)
        for part, info in sorted(lk["parts"].items()):
            label = roles.ROLE_PRESETS.get(info["role"], {}).get("label", info["role"])
            item = QtWidgets.QTreeWidgetItem([f"{part}（{label}）"])
            item.setData(0, QtCore.Qt.UserRole, part)
            self.tree.addTopLevelItem(item)
            if part == self.target:
                self.tree.setCurrentItem(item)
            for m in info["materials"]:
                child = QtWidgets.QTreeWidgetItem([m + ("  ✎" if lk["materials"][m].get(look.FEATURE_OVERRIDES) else "")])
                child.setData(0, QtCore.Qt.UserRole, m)
                child.setToolTip(0, "部位ごとの上書きあり" if lk["materials"][m].get(look.FEATURE_OVERRIDES) else "")
                item.addChild(child)
                if m == self.target:
                    self.tree.setCurrentItem(child)
            item.setExpanded(True)
        self.tree.blockSignals(False)

    def _refresh_combos(self) -> None:
        lk = self.session.look
        header = "選択中の部位" if self.target is None else f"{self.target}"
        self.table.horizontalHeaderItem(COL_SEL).setText(header)
        for fid, combo in self.combos.items():
            combo.blockSignals(True)
            combo.clear()
            if self.target is None:
                combo.addItem("—（左で部位を選ぶ）", None)
                combo.setEnabled(False)
            else:
                overrides, effective = self.session.material_feature_state(self.target, fid)
                whole_on = look.enabled(lk, fid)
                combo.addItem(f"全体に従う（{'オン' if whole_on else 'オフ'}）", INHERIT)
                combo.addItem("オン", True)
                combo.addItem("オフ", False)
                if len(set(overrides)) > 1:
                    combo.insertItem(0, "—（部位の中で混在）", MIXED)
                    combo.setCurrentIndex(0)
                else:
                    value = overrides[0] if overrides else None
                    combo.setCurrentIndex(combo.findData(INHERIT if value is None else value))
                combo.setEnabled(True)
                on_any = any(effective)
                combo.setStyleSheet("" if overrides and overrides[0] is None and len(set(overrides)) == 1
                                    else ("color: #80d080;" if on_any else "color: #f0a040;"))
            combo.blockSignals(False)

    # -------------------------------------------------------------- 操作
    def _on_target(self) -> None:
        item = self.tree.currentItem() or next(iter(self.tree.selectedItems()), None)
        self.target = item.data(0, QtCore.Qt.UserRole) if item else None
        self._refresh_combos()

    def _toggle(self, feature_id: str, on: bool) -> None:
        try:
            self.session.set_feature(feature_id, on)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "T-Drive Toon", str(exc))
            self.refresh()

    def _set_material(self, feature_id: str, value) -> None:
        if self.target is None or value == MIXED:
            return
        try:
            self.session.set_material_feature(self.target, feature_id, None if value == INHERIT else bool(value))
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
    items = list(f.params) + [f"common.{c}" for c in f.common] + [f"characterSettings.{s}" for s in f.settings]
    return "属するもの: " + (", ".join(items) if items else "（頂点カラーなどメッシュのデータ）")
