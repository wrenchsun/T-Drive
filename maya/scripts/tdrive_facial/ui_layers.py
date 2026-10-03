"""レイヤータブ（docs/14 §5.5）: 感情レイヤーの追加・改名・有効 / 無効・削除・コピー、重みの出どころ（直接 / カメラの距離）、表情での弱めの設定。

レイヤーの一覧で選んだ行が「編集するレイヤー」（アクティブレイヤー）。切り替えるときに未保存のポーズ編集があれば、
保存 / 破棄 / キャンセルを聞く（`ask_switch_choice`。テストが差し替える）。操作はすべてセッションのコマンドを呼ぶ。
"""

from __future__ import annotations

import traceback
from typing import Optional

from PySide6 import QtCore, QtWidgets

from tdrive import lifecycle

from .core.presenters import (
    EMOTION_PRESETS,
    SELECT_CANCELLED,
    SELECT_NEEDS_CONFIRM,
)
from .ui import DIM_STYLE, WARN_STYLE, ask_save_discard_cancel, ask_yes_no

COL_ENABLED, COL_NAME, COL_CURVE, COL_POINTS = range(4)
HEADERS = ("有効", "レイヤー名", "感情カーブ", "点")
PRESET_TIPS = {
    "Anger": "怒り",
    "Contempt": "軽蔑",
    "Disgust": "嫌悪",
    "Fear": "恐れ",
    "Joy": "喜び",
    "Sadness": "悲しみ",
    "Surprise": "驚き",
}


class LayersTab(QtWidgets.QWidget):
    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self._updating = False

        v = QtWidgets.QVBoxLayout(self)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        body = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(body)
        scroll.setWidget(body)
        v.addWidget(scroll, 1)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        # ---- 一覧
        self.count_label = QtWidgets.QLabel()
        col.addWidget(self.count_label)
        self.table = QtWidgets.QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.SelectedClicked)
        self.table.setMinimumHeight(170)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(True)
        hh.setSectionResizeMode(COL_NAME, QtWidgets.QHeaderView.Stretch)
        self.table.setColumnWidth(COL_ENABLED, 44)
        self.table.setColumnWidth(COL_CURVE, 110)
        self.table.setToolTip("選んだレイヤーが編集の対象になります（グリッド・ポーズタブで編集する）。感情カーブはダブルクリックで書き換えられます")
        self.table.itemChanged.connect(self.on_item_changed)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        col.addWidget(self.table, 1)

        # ---- 追加
        add_box = QtWidgets.QGroupBox("レイヤーを足す")
        g = QtWidgets.QGridLayout(add_box)
        self.preset_buttons: dict[str, QtWidgets.QPushButton] = {}
        for i, name in enumerate(EMOTION_PRESETS):
            b = QtWidgets.QPushButton(name)
            b.setToolTip(f"{PRESET_TIPS.get(name, name)}のレイヤーを足して、編集の対象にする（もうあるときは押せません）")
            b.clicked.connect(lambda *_, n=name: self.on_add(n))
            g.addWidget(b, i // 4, i % 4)
            self.preset_buttons[name] = b
        row = QtWidgets.QHBoxLayout()
        self.custom_name = QtWidgets.QLineEdit()
        self.custom_name.setPlaceholderText("自由な名前（英数字と _）")
        self.custom_name.returnPressed.connect(lambda *_: self.on_add_custom())
        self.custom_name.textChanged.connect(lambda *_: self._check_custom_name())
        row.addWidget(self.custom_name, 1)
        self.custom_add = QtWidgets.QPushButton("追加")
        self.custom_add.clicked.connect(lambda *_: self.on_add_custom())
        row.addWidget(self.custom_add)
        g.addLayout(row, 2, 0, 1, 4)
        self.custom_note = QtWidgets.QLabel()
        self.custom_note.setStyleSheet(WARN_STYLE)
        self.custom_note.setWordWrap(True)
        g.addWidget(self.custom_note, 3, 0, 1, 4)
        col.addWidget(add_box)

        # ---- 選んだレイヤーの操作
        op_box = QtWidgets.QGroupBox("選んだレイヤーの操作")
        ov = QtWidgets.QVBoxLayout(op_box)
        self.selected_label = QtWidgets.QLabel()
        ov.addWidget(self.selected_label)
        row = QtWidgets.QHBoxLayout()
        self.rename_btn = QtWidgets.QPushButton("名前を変える…")
        self.rename_btn.setToolTip("名前を変えると、焼いたシェイプ（FC_*）は消えて、新しい名前の分は未ベイクになります")
        self.rename_btn.clicked.connect(lambda *_: self.on_rename())
        self.delete_btn = QtWidgets.QPushButton("削除…")
        self.delete_btn.setToolTip("このレイヤーと、焼いたシェイプ（FC_*）を消します")
        self.delete_btn.clicked.connect(lambda *_: self.on_delete())
        row.addWidget(self.rename_btn)
        row.addWidget(self.delete_btn)
        row.addStretch(1)
        ov.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("ポーズをコピー: 元"))
        self.copy_source = QtWidgets.QComboBox()
        self.copy_source.setToolTip("ほかのレイヤーのポーズを、選んだレイヤーへコピーする（叩き台作り）。同じ位置の点は上書きされます")
        row.addWidget(self.copy_source, 1)
        self.copy_btn = QtWidgets.QPushButton("コピー")
        self.copy_btn.clicked.connect(lambda *_: self.on_copy())
        row.addWidget(self.copy_btn)
        ov.addLayout(row)
        col.addWidget(op_box)

        # ---- 重みの出どころ（選んだレイヤー）
        src_box = QtWidgets.QGroupBox("重みの出どころ（選んだレイヤー）")
        sf = QtWidgets.QFormLayout(src_box)
        sf.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.src_combo = QtWidgets.QComboBox()
        self.src_combo.addItem("直接（Timeline / 部品の値）", "direct")
        self.src_combo.addItem("カメラの距離", "distance")
        self.src_combo.setToolTip(
            "感情レイヤーの重みをどこから受け取るか。直接 = Timeline や部品の値（感情カーブ）から入れる。"
            "カメラの距離 = カメラが遠い・近いで自動で決める（遠景だけデフォルメ用のレイヤーを効かせる、など）"
        )
        self.src_combo.activated.connect(lambda *_: self.on_source_activated())
        sf.addRow("重みの出どころ", self.src_combo)
        self.src_start = self._dist_spin(0.0, 100000.0, 10.0, " cm", "この距離のとき、重みは「から」の値になる（これより近くても同じ）")
        self.src_end = self._dist_spin(0.0, 100000.0, 10.0, " cm", "この距離のとき、重みは「まで」の値になる（これより遠くても同じ）。間は直線で変わる")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("開始"))
        row.addWidget(self.src_start)
        row.addWidget(QtWidgets.QLabel("終了"))
        row.addWidget(self.src_end)
        row.addStretch(1)
        sf.addRow("距離", row)
        self.src_from = self._dist_spin(0.0, 1.0, 0.05, "", "開始の距離（とそれより近いとき）の重み")
        self.src_to = self._dist_spin(0.0, 1.0, 0.05, "", "終了の距離（とそれより遠いとき）の重み")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("から"))
        row.addWidget(self.src_from)
        row.addWidget(QtWidgets.QLabel("まで"))
        row.addWidget(self.src_to)
        row.addStretch(1)
        sf.addRow("重み", row)
        self.src_note = QtWidgets.QLabel()
        self.src_note.setWordWrap(True)
        self.src_note.setStyleSheet(DIM_STYLE)
        sf.addRow(self.src_note)
        col.addWidget(src_box)

        # ---- 表情での弱め
        dampen_box = QtWidgets.QGroupBox("表情が強いときに補正を弱める")
        f = QtWidgets.QFormLayout(dampen_box)
        self.dampen = QtWidgets.QDoubleSpinBox()
        self.dampen.setRange(0.0, 1.0)
        self.dampen.setSingleStep(0.05)
        self.dampen.setDecimals(2)
        self.dampen.setKeyboardTracking(False)
        self.dampen.setToolTip("0 = 弱めない / 1 = 表情が最大のとき補正を 0 にする")
        self.dampen.valueChanged.connect(lambda *_: self.on_dampen_changed())
        f.addRow("弱める強さ", self.dampen)
        self.intensity_note = QtWidgets.QLabel()
        self.intensity_note.setWordWrap(True)
        self.intensity_note.setStyleSheet(DIM_STYLE)
        f.addRow(self.intensity_note)
        self.intensity_list = QtWidgets.QListWidget()
        self.intensity_list.setMinimumHeight(110)
        self.intensity_list.setToolTip("表情の強さを測るシェイプ。チェックしたシェイプの最大値で、補正を弱めます（作業セットのシェイプから選ぶ）")
        self.intensity_list.itemChanged.connect(self.on_intensity_changed)
        f.addRow("強さを測るシェイプ", self.intensity_list)
        col.addWidget(dampen_box)
        col.addStretch(1)
        self.refresh()

    def _dist_spin(self, lo: float, hi: float, step: float, suffix: str, tip: str) -> QtWidgets.QDoubleSpinBox:
        w = QtWidgets.QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setSingleStep(step)
        w.setDecimals(2)
        w.setKeyboardTracking(False)
        if suffix:
            w.setSuffix(suffix)
        w.setToolTip(tip)
        w.valueChanged.connect(lambda *_: self.on_source_value_changed())
        return w

    # ============================================================ 表示
    def show_status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(WARN_STYLE if error else DIM_STYLE)

    def refresh(self) -> None:
        s = self.session
        if s.presenters is None:
            return
        view = s.layers.view()
        doc = s.doc
        self._updating = True
        try:
            self.count_label.setText(f"レイヤー {view.count} / {view.limit}（Neutral を含む）")
            self._fill_table(view)
            for name, exists in view.presets:
                b = self.preset_buttons[name]
                b.setEnabled(not exists and view.can_add)
            self.custom_add.setEnabled(view.can_add)
            self._check_custom_name()
            act = view.layers[view.active] if 0 <= view.active < len(view.layers) else None
            if act is not None:
                self.selected_label.setText(f"選んでいるレイヤー: <b>{act.name}</b>" + ("（Neutral は名前を変えたり消したりできません）" if act.is_neutral else ""))
                self.rename_btn.setEnabled(act.can_rename)
                self.delete_btn.setEnabled(act.can_delete)
                self.copy_source.clear()
                for j in view.copy_sources.get(act.index, []):
                    self.copy_source.addItem(view.layers[j].name, j)
                self.copy_btn.setEnabled(self.copy_source.count() > 0)
            self._refresh_source(act)
            self.dampen.setValue(doc.policy.expression_dampen)
            self._fill_intensity(doc)
        finally:
            self._updating = False

    def _refresh_source(self, act) -> None:
        """重みの出どころの欄（選んでいるレイヤーのぶん）。Neutral は常に全量なので使えない。"""
        usable = act is not None and not act.is_neutral
        spec = self.session.layer_weight_source(act.index) if usable else {"source": "curve"}
        by_distance = spec.get("source") == "distance"
        self.src_combo.setCurrentIndex(1 if by_distance else 0)
        d = self.session.layers.DISTANCE_DEFAULTS
        for w, key in ((self.src_start, "start"), (self.src_end, "end"), (self.src_from, "from"), (self.src_to, "to")):
            w.setValue(float(spec.get(key, d[key])))
            w.setEnabled(usable and by_distance)
        self.src_combo.setEnabled(usable)
        if not usable:
            self.src_note.setText("Neutral は常に全量で足されるので、重みの出どころはありません")
        elif by_distance:
            self.src_note.setText(
                "カメラと格子の中心の距離で重みが決まります（Maya のプレビューにも反映。プレビューの「感情」スライダーは使われません）。"
                "Unity でも同じ距離で決まります"
            )
        else:
            self.src_note.setText("Timeline や部品の値（感情カーブ）から重みを受け取ります（Maya のプレビューでは「感情」スライダー）")

    def _fill_table(self, view) -> None:
        self.table.setRowCount(0)
        self.table.setRowCount(len(view.layers))
        for r in view.layers:
            en = QtWidgets.QTableWidgetItem()
            en.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            en.setCheckState(QtCore.Qt.Checked if r.enabled else QtCore.Qt.Unchecked)
            en.setToolTip("外すと、このレイヤーは Unity での評価から外れます")
            self.table.setItem(r.index, COL_ENABLED, en)
            name = QtWidgets.QTableWidgetItem(r.name)
            name.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            if r.active:
                f = name.font()
                f.setBold(True)
                name.setFont(f)
            self.table.setItem(r.index, COL_NAME, name)
            curve = QtWidgets.QTableWidgetItem(r.emotion_curve)
            if r.can_set_emotion_curve:
                curve.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEditable)
                curve.setToolTip("感情の重みを受け取る名前（Unity のコンポーネント・Timeline・Animator から入る）。ダブルクリックで変更")
            else:
                curve.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                curve.setForeground(QtCore.Qt.gray)
            self.table.setItem(r.index, COL_CURVE, curve)
            pts = QtWidgets.QTableWidgetItem(f"{r.points}（キー {r.keys} / ベイク済み {r.baked}）")
            pts.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            self.table.setItem(r.index, COL_POINTS, pts)
        if 0 <= view.active < len(view.layers):
            self.table.selectRow(view.active)
        self.table.resizeColumnToContents(COL_POINTS)

    def _fill_intensity(self, doc) -> None:
        avail = list(doc.working_set.curves)
        names = avail + [n for n in doc.intensity_curves if n not in avail]
        self.intensity_list.clear()
        for n in names:
            it = QtWidgets.QListWidgetItem(n)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if n in doc.intensity_curves else QtCore.Qt.Unchecked)
            it.setData(QtCore.Qt.UserRole, n)
            if n not in avail:
                it.setForeground(QtCore.Qt.gray)
                it.setToolTip("作業セットにありません")
            self.intensity_list.addItem(it)
        self.intensity_list.setEnabled(bool(names))
        if not avail and not names:
            self.intensity_note.setText("セットアップタブの作業セットでシェイプを選ぶと、ここから選べます")
        elif not doc.intensity_curves:
            self.intensity_note.setText("チェックが無いときは、作業セットのシェイプ全部の最大値で補正を弱めます")
        else:
            self.intensity_note.setText("")

    # ============================================================ ダイアログ（差し替えられる）
    def ask_switch_choice(self, text: str) -> str:
        """未保存のポーズ編集があるままレイヤーを切り替えるとき: 保存 / 破棄 / キャンセル（CONFIRM_*）。"""
        return ask_save_discard_cancel(self, text, "レイヤーの切り替え")

    def ask_confirm_delete(self, text: str) -> bool:
        return ask_yes_no(self, text, "レイヤーの削除")

    def ask_new_name(self, current: str) -> Optional[str]:
        name, ok = QtWidgets.QInputDialog.getText(self, "レイヤー名を変える", "新しい名前（英数字と _）:", text=current)
        return name if ok else None

    # ============================================================ 操作
    def _run(self, fn, *args, **kwargs):
        """セッションのコマンドを呼ぶ。失敗は状態欄に出す。結果（成功）を返す。"""
        try:
            res = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self.show_status(str(exc), error=True)
            lifecycle.report_error("レイヤータブの操作でエラー", traceback.format_exc())
            self.refresh()
            return None
        if getattr(res, "ok", True) is False:
            self.show_status(res.message or "できませんでした", error=True)
            self.refresh()
            return None
        return res

    def _active_index(self) -> int:
        return self.session.layers.view().active

    def _switch(self, index: int):
        """アクティブレイヤーを index へ。未保存のポーズ編集があれば聞く。結果（SelectResult）を返す。"""
        s = self.session
        try:
            r = s.set_active_layer(index)
            if r.status == SELECT_NEEDS_CONFIRM:
                names = [l.name for l in s.doc.layers]
                choice = self.ask_switch_choice(
                    f"「{names[s.ctx.active_layer]}」に未保存のポーズ編集があります。\n「{names[index]}」へ切り替える前に保存しますか？"
                )
                r = s.set_active_layer(index, choice=choice)  # cancel も 1 回の呼び出しで足りる
        except Exception as exc:  # noqa: BLE001
            self.show_status(str(exc), error=True)
            lifecycle.report_error("レイヤーの切り替えでエラー", traceback.format_exc())
            self.refresh()
            return None
        if r.status == SELECT_CANCELLED:
            self.show_status("切り替えを取りやめました")
        elif r.message and r.status not in ("selected", "same_point"):
            self.show_status(r.message, error=True)
        self.refresh()
        return r

    def on_selection_changed(self) -> None:
        if self._updating:
            return
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        index = rows[0].row()
        if index == self._active_index():
            return
        self._switch(index)

    def on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._updating:
            return
        index, col = item.row(), item.column()
        if col == COL_ENABLED:
            self._run(self.session.set_layer_enabled, index, item.checkState() == QtCore.Qt.Checked)
        elif col == COL_CURVE:
            res = self._run(self.session.set_layer_emotion_curve, index, item.text())
            if res is not None and getattr(res, "warnings", None):
                self.show_status(" ".join(res.warnings))
        else:
            return
        self.refresh()

    def on_add(self, name: str) -> None:
        """プリセットの追加（足して編集の対象にする）。"""
        res = self._run(self.session.add_layer, name, select=True)
        if res is None:
            return
        sw = res.switch
        if sw is not None and sw.status == SELECT_NEEDS_CONFIRM:
            self._switch(res.index)  # 続きを聞く
            return
        self.show_status(res.message)
        self.refresh()

    def on_add_custom(self) -> None:
        name = self.custom_name.text().strip()
        chk = self.session.layers.check_name(name)
        if not chk.ok:
            self.show_status(chk.message, error=True)
            return
        self.on_add(name)
        self.custom_name.clear()

    def _check_custom_name(self) -> None:
        text = self.custom_name.text().strip()
        if not text:
            self.custom_note.setText("")
            return
        chk = self.session.layers.check_name(text)
        self.custom_note.setText("" if chk.ok else chk.message)

    def on_rename(self) -> None:
        idx = self._active_index()
        old = self.session.doc.layers[idx].name
        new = self.ask_new_name(old)
        if new is None:
            return
        res = self._run(self.session.rename_layer, idx, new)
        if res is not None:
            self.show_status(res.message + ("。" + " ".join(res.warnings) if res.warnings else ""))
            self.refresh()

    def on_delete(self) -> None:
        idx = self._active_index()
        name = self.session.doc.layers[idx].name
        if not self.ask_confirm_delete(
            f"レイヤー「{name}」を削除します。\nこのレイヤーのポーズと、焼いたシェイプ（FC_*）もシーンから消えます。よろしいですか？"
        ):
            return
        res = self._run(self.session.delete_layer, idx)
        if res is not None:
            self.show_status(res.message + ("。" + " ".join(res.warnings) if res.warnings else ""))
            self.refresh()

    def on_copy(self) -> None:
        src = self.copy_source.currentData()
        if src is None:
            return
        res = self._run(self.session.copy_layer, src, self._active_index())
        if res is not None:
            self.show_status(res.message)
            self.refresh()

    def on_source_activated(self) -> None:
        if self._updating:
            return
        idx = self._active_index()
        want = self.src_combo.currentData()
        cur = self.session.layer_weight_source(idx).get("source", "curve")
        if want == "direct" and cur in ("direct", "curve"):
            return
        res = self._run(self.session.set_layer_weight_source, idx, want)
        if res is not None:
            self.show_status("重みの出どころを変えました")
        self.refresh()

    def on_source_value_changed(self) -> None:
        if self._updating:
            return
        res = self._run(
            self.session.set_layer_weight_source,
            self._active_index(),
            "distance",
            start=self.src_start.value(),
            end=self.src_end.value(),
            w_from=self.src_from.value(),
            w_to=self.src_to.value(),
        )
        if res is not None:
            self.show_status("")

    def on_dampen_changed(self) -> None:
        if self._updating:
            return
        res = self._run(self.session.set_policy, expression_dampen=self.dampen.value())
        if res is not None:
            self.show_status("")

    def on_intensity_changed(self, _item: Optional[QtWidgets.QListWidgetItem] = None) -> None:
        if self._updating:
            return
        names = [
            self.intensity_list.item(i).data(QtCore.Qt.UserRole)
            for i in range(self.intensity_list.count())
            if self.intensity_list.item(i).checkState() == QtCore.Qt.Checked
        ]
        self._run(self.session.set_intensity_curves, names)

    def detach(self) -> None:
        pass
