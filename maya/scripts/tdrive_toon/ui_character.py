"""キャラクタータブ（docs/02 §4.1、3-8 / 3-9）: キャラクター単位の設定と表情パラメータのプレビュー。

Maya でプレビューできない項目（Unity のコンポーネント・Renderer Feature が使うもの）も、ここで編集して Look に持つ。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import features, look, params, session

UNITY_ONLY = "<span style='color:#9aa6b8'>（Unity でのみ）</span>"


def _warn(parent: QtWidgets.QWidget, exc: Exception) -> None:
    QtWidgets.QMessageBox.warning(parent, "T-Drive Toon", str(exc))


class CharacterTab(QtWidgets.QWidget):
    def __init__(self, s: session.Session) -> None:
        super().__init__()
        self.session = s
        self._updating = False
        outer = QtWidgets.QVBoxLayout(self)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        body = QtWidgets.QWidget()
        self.form = QtWidgets.QVBoxLayout(body)
        scroll.setWidget(body)
        self.hidden_note = QtWidgets.QLabel()
        self.hidden_note.setWordWrap(True)
        self.hidden_note.setStyleSheet("color: #9aa6b8;")
        outer.addWidget(self.hidden_note)
        outer.addWidget(scroll)
        self.feature_boxes: dict[str, QtWidgets.QGroupBox] = {}

        # ---- ライト（影の安定化） T-17
        box, f = self._group("キャラクターライトの安定化（プレビュータブ › Unity の影の安定化を掛ける で確認）", feature="lightStabilize")
        self.smoothing = self._spin(f, "平滑化（秒）", "light.smoothing", 0.0, 2.0, 0.05)
        self.hysteresis = self._spin(f, "ヒステリシス（度）", "light.hysteresisDeg", 0.0, 20.0, 0.5)

        # ---- ステンシル T-24
        box, f = self._group(f"髪越し表示（ステンシル） {UNITY_ONLY}", feature="stencil")
        self.stencil = self._check(f, "使う（眉・目・アイラインを前髪の上に。Maya では「手前に出す」で代わりに確認）", "stencil.enabled")

        # ---- インナーライン T-23
        box, f = self._group("画面上の内側の線（インナーライン）", feature="innerLine")
        self.il_enabled = self._check(f, "使う", "innerLine.enabled")
        self.il_width = self._spin(f, "線幅（px@1080p）", "innerLine.width", 0.0, 10.0, 0.1)
        self.il_color = QtWidgets.QPushButton()
        self.il_color.clicked.connect(self._pick_line_color)
        f.addRow("線色", self.il_color)
        self.il_parts = QtWidgets.QListWidget()
        self.il_parts.setMaximumHeight(110)
        self.il_parts.itemChanged.connect(self._on_parts_changed)
        f.addRow("線を出す部位", self.il_parts)

        # ---- 接地影 T-29
        box, f = self._group("接地影（Maya では足元の板に簡易表示）", feature="contactShadow")
        self.cs_enabled = self._check(f, "使う", "contactShadow.enabled")
        self.cs_radius = self._spin(f, "半径（m）", "contactShadow.radius", 0.01, 2.0, 0.01)
        self.cs_strength = self._spin(f, "濃さ", "contactShadow.strength", 0.0, 1.0, 0.05)

        # ---- 奥行き圧縮 T-22（Maya でもプレビュー可）
        box, f = self._group("奥行き圧縮（顔を平面的に）", feature="depthCompression")
        self.depth = self._spin(f, "量（0〜1）", "depthCompression", 0.0, 1.0, 0.05)
        f.addRow("", QtWidgets.QLabel("効かせる部位は ルックタブ › 手前に出す › 奥行き圧縮の効かせ具合（例: 顔だけ 1）"))

        # ---- SDF 顔影マップ T-21（docs/05 §3.3）
        box, f = self._group("顔影マップ（SDF。ライトの角度で顔の影を作画どおりに進める）", feature="faceShadowSdf")
        gen = QtWidgets.QPushButton("マスクから生成…")
        gen.setToolTip("角度ごとの白黒マスク（face_shadow_000.png, _030 … _180）が入ったフォルダを選ぶ → 顔マテリアルに設定")
        gen.clicked.connect(self._generate_face_shadow)
        self.fs_size = QtWidgets.QComboBox()
        self.fs_size.addItems(["256", "512", "1024"])
        self.fs_size.setCurrentText("512")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(gen)
        row.addWidget(QtWidgets.QLabel("解像度"))
        row.addWidget(self.fs_size)
        row.addStretch(1)
        f.addRow("", row)
        f.addRow("", QtWidgets.QLabel("効かせ具合はルックタブ › 顔影マップ（SDF）。顔はキャラクターが +Z（正面）を向いている前提"))

        # ---- カメラ角度補正 T-20（docs/05 §3.2）
        box, f = self._group("カメラ角度補正（正面 / 3/4 / 横で顔の形を補正する BlendShape）", feature="viewCorrection")
        self.vc_mesh = QtWidgets.QLabel()
        f.addRow("対象メッシュ", self.vc_mesh)
        self.vc = {}
        for key, label in (("front", "正面"), ("threeQuarter", "3/4"), ("side", "横")):
            row = QtWidgets.QHBoxLayout()
            e = QtWidgets.QLineEdit()
            e.setPlaceholderText("（使わない）")
            e.editingFinished.connect(lambda k=key, w=e: self._set(f"viewCorrection.{k}", w.text().strip()))
            row.addWidget(e, 1)
            make = QtWidgets.QPushButton("作る")
            make.setToolTip("顔メッシュを選んで押す → 複製ができるので、それを彫る")
            make.clicked.connect(lambda _c=False, k=key: self._vc(self.session.create_view_correction, k))
            row.addWidget(make)
            reg = QtWidgets.QPushButton("登録")
            reg.setToolTip("彫り終わった補正シェイプを BlendShape に登録する")
            reg.clicked.connect(lambda _c=False, k=key: self._vc(self.session.register_view_correction, k))
            row.addWidget(reg)
            f.addRow(label, row)
            self.vc[key] = e
        row = QtWidgets.QHBoxLayout()
        link = QtWidgets.QPushButton("カメラに連動（プレビュー）")
        link.setToolTip("今のビューポートのカメラを回すと、角度に応じて補正が自動で混ざる")
        link.clicked.connect(lambda: self._vc(self.session.connect_view_correction))
        unlink = QtWidgets.QPushButton("連動を外す")
        unlink.clicked.connect(lambda: self._vc(self.session.disconnect_view_correction))
        row.addWidget(link)
        row.addWidget(unlink)
        row.addStretch(1)
        f.addRow("", row)

        # ---- 表情パラメータ T-25（3-9 プレビュー）
        box, v = self._group("表情パラメータ（0〜1 の入力で値を動かす対応表）", form=False, feature="expressions")
        row = QtWidgets.QHBoxLayout()
        self.expr = QtWidgets.QComboBox()
        self.expr.currentIndexChanged.connect(self._show_expression)
        row.addWidget(self.expr, 1)
        for text, fn in (("追加…", self._add_expression), ("削除", self._remove_expression), ("行を追加", self._add_row), ("行を削除", self._remove_row)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        v.addLayout(row)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["マテリアル", "パラメータ", "0 のとき", "1 のとき"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setMaximumHeight(160)
        self.table.itemChanged.connect(self._on_table_changed)
        v.addWidget(self.table)
        prow = QtWidgets.QHBoxLayout()
        prow.addWidget(QtWidgets.QLabel("プレビュー（保存されません）"))
        self.expr_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.expr_slider.setRange(0, 100)
        self.expr_slider.valueChanged.connect(self._preview_expression)
        prow.addWidget(self.expr_slider, 1)
        back = QtWidgets.QPushButton("Look の値に戻す")
        back.clicked.connect(lambda: (self.expr_slider.blockSignals(True), self.expr_slider.setValue(0),
                                      self.expr_slider.blockSignals(False), self.session.show(self.session.shown)))
        prow.addWidget(back)
        v.addLayout(prow)
        self.form.addStretch(1)

    # -------------------------------------------------------------- 部品
    def _group(self, title: str, form: bool = True, feature: str | None = None):
        box = QtWidgets.QGroupBox()
        if feature:
            self.feature_boxes[feature] = box
        lay = QtWidgets.QVBoxLayout(box)
        head = QtWidgets.QLabel(f"<b>{title}</b>")
        lay.addWidget(head)
        inner = QtWidgets.QFormLayout() if form else QtWidgets.QVBoxLayout()
        lay.addLayout(inner)
        self.form.addWidget(box)
        return box, inner

    def _spin(self, f, label, path, lo, hi, step):
        w = QtWidgets.QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setSingleStep(step)
        w.setDecimals(3)
        w.editingFinished.connect(lambda: self._set(path, round(w.value(), 4)))
        w.setProperty("path", path)
        f.addRow(label, w)
        return w

    def _check(self, f, label, path):
        w = QtWidgets.QCheckBox(label)
        w.clicked.connect(lambda on: self._set(path, bool(on)))
        w.setProperty("path", path)
        f.addRow("", w)
        return w

    def _generate_face_shadow(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "角度別マスクのフォルダ")
        if not folder:
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            path = self.session.generate_face_shadow(folder, int(self.fs_size.currentText()))
        except Exception as exc:
            _warn(self, exc)
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        QtWidgets.QMessageBox.information(self, "T-Drive Toon", f"生成して顔に設定しました:\n{path}")

    def _vc(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as exc:
            _warn(self, exc)

    def _set(self, path: str, value) -> None:
        if self._updating or self.session.look is None:
            return
        try:
            if self.session.setting(path) != value:
                self.session.set_setting(path, value)
        except Exception as exc:
            _warn(self, exc)

    # -------------------------------------------------------------- 表示
    def refresh(self) -> None:
        lk = self.session.look
        self.setEnabled(lk is not None)
        if lk is None:
            return
        off = []
        for fid, box in self.feature_boxes.items():
            on = look.enabled(lk, fid)
            box.setVisible(on)
            if not on:
                off.append(features.BY_ID[fid].label)
        self.hidden_note.setText(f"オフの機能は隠しています（機能タブ）: {'、'.join(off)}" if off else "")
        self.hidden_note.setVisible(bool(off))
        self._updating = True
        try:
            for w in self.findChildren(QtWidgets.QDoubleSpinBox):
                if w.property("path"):
                    w.setValue(float(self.session.setting(w.property("path"))))
            for w in self.findChildren(QtWidgets.QCheckBox):
                if w.property("path"):
                    w.setChecked(bool(self.session.setting(w.property("path"))))
            r, g, b, _a = self.session.setting("innerLine.color")
            self.il_color.setStyleSheet(f"background-color: rgb({int(r * 255)},{int(g * 255)},{int(b * 255)}); border: 1px solid #888;")
            chosen = set(self.session.setting("innerLine.parts"))
            self.il_parts.clear()
            for part in sorted(lk["parts"]):
                it = QtWidgets.QListWidgetItem(part)
                it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                it.setCheckState(QtCore.Qt.Checked if part in chosen else QtCore.Qt.Unchecked)
                self.il_parts.addItem(it)
            for key, e in self.vc.items():
                e.setText(self.session.setting(f"viewCorrection.{key}"))
            self.vc_mesh.setText(self.session.setting("viewCorrection.mesh") or "（未設定: 顔メッシュを選んで「作る」）")
            current = self.expr.currentText()
            self.expr.blockSignals(True)
            self.expr.clear()
            self.expr.addItems(sorted(self.session.setting("expressions")))
            if current:
                self.expr.setCurrentText(current)
            self.expr.blockSignals(False)
            self._show_expression()
        finally:
            self._updating = False

    def _show_expression(self) -> None:
        was = self._updating
        self._updating = True
        try:
            name = self.expr.currentText()
            rows = self.session.setting("expressions").get(name, []) if name else []
            self.table.setRowCount(0)
            self.table.setRowCount(len(rows))
            mats = sorted(self.session.look["materials"]) if self.session.look else []
            floats = [p.unity for p in params.SPECIFIC_PARAMS if p.kind == params.FLOAT]
            for i, e in enumerate(rows):
                for col, (items, value) in enumerate(((mats, e["material"]), (floats, e["property"]))):
                    combo = QtWidgets.QComboBox()
                    combo.addItems(items)
                    combo.setCurrentText(value)
                    combo.currentTextChanged.connect(self._write_table)
                    self.table.setCellWidget(i, col, combo)
                self.table.setItem(i, 2, QtWidgets.QTableWidgetItem(str(e["min"])))
                self.table.setItem(i, 3, QtWidgets.QTableWidgetItem(str(e["max"])))
            self.table.resizeColumnsToContents()
        finally:
            self._updating = was

    # -------------------------------------------------------------- 操作
    def _pick_line_color(self) -> None:
        c = self.session.setting("innerLine.color")
        picked = QtWidgets.QColorDialog.getColor(QtGui.QColor.fromRgbF(*c), self, "線色（Unity のインスペクターと同じ値）")
        if picked.isValid():
            self._set("innerLine.color", [round(picked.redF(), 4), round(picked.greenF(), 4), round(picked.blueF(), 4), 1.0])

    def _on_parts_changed(self, _item) -> None:
        parts = [self.il_parts.item(i).text() for i in range(self.il_parts.count()) if self.il_parts.item(i).checkState() == QtCore.Qt.Checked]
        self._set("innerLine.parts", parts)

    def _expressions(self) -> dict:
        import copy

        return copy.deepcopy(self.session.setting("expressions"))

    def _add_expression(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "表情パラメータ", "名前（英数字と _。例: blush）:")
        if not ok or not name.strip():
            return
        ex = self._expressions()
        ex.setdefault(name.strip(), [])
        self._set("expressions", ex)
        self.expr.setCurrentText(name.strip())

    def _remove_expression(self) -> None:
        name = self.expr.currentText()
        if name:
            ex = self._expressions()
            ex.pop(name, None)
            self._set("expressions", ex)

    def _add_row(self) -> None:
        name = self.expr.currentText()
        if not name or self.session.look is None:
            return
        ex = self._expressions()
        first = sorted(self.session.look["materials"])[0]
        ex[name].append({"material": first, "property": "_ToonTintStrength", "min": 0.0, "max": 1.0})
        self._set("expressions", ex)

    def _remove_row(self) -> None:
        name, row = self.expr.currentText(), self.table.currentRow()
        if name and row >= 0:
            ex = self._expressions()
            del ex[name][row]
            self._set("expressions", ex)

    def _on_table_changed(self, _item) -> None:
        self._write_table()

    def _write_table(self, *_args) -> None:
        if self._updating:
            return
        name = self.expr.currentText()
        rows = []
        try:
            for i in range(self.table.rowCount()):
                rows.append({
                    "material": self.table.cellWidget(i, 0).currentText(),
                    "property": self.table.cellWidget(i, 1).currentText(),
                    "min": float(self.table.item(i, 2).text()),
                    "max": float(self.table.item(i, 3).text()),
                })
        except (ValueError, AttributeError):
            return  # 入力途中
        ex = self._expressions()
        ex[name] = rows
        self._set("expressions", ex)

    def _preview_expression(self, value: int) -> None:
        name = self.expr.currentText()
        if name:
            self.session.preview_expression(name, value / 100.0)
