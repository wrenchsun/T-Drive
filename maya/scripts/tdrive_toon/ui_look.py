"""ルックタブ（docs/05 §4、R-2 / R-5）: 部位 / マテリアルを選んでパラメータを調整する。

値はスライダー操作中も連続でビューポートに反映する（setAttr のみ）。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from pathlib import Path

from . import features, look, params, preview, project, roles, session

MIXED = "—"
# Common（D-Drive MaterialCommon）のうち編集できるもの。(キー, 表示名, 種類, 最小, 最大)
COMMON_ROWS = (
    ("common.albedoTint", "ベース色（乗算）", "color", 0, 0),
    ("common.blend", "ブレンド", "blend", 0, 0),
    ("common.cutoff", "カットオフ（Cutout 時）", "float", 0.0, 1.0),
    ("common.doubleSided", "両面表示", "bool", 0, 0),
    ("renderQueueOffset", "描画順オフセット（Unity のみ）", "int", -100, 100),
)
# テクスチャの差し替え（4-9、docs/05 §4.1）。Common（D-Drive MaterialCommon）のテクスチャと関連する値
TEXTURE_ROWS = (
    ("common.albedo", "ベースマップ（色）", params.TEXTURE, 0, 0),
    ("common.normal", "法線マップ", params.TEXTURE, 0, 0),
    ("common.normalScale", "法線マップの強さ", params.FLOAT, 0.0, 2.0),
    ("common.emission", "発光マップ", params.TEXTURE, 0, 0),
    ("common.emissionColor", "発光色", params.COLOR, 0, 0),
    ("common.emissionIntensity", "発光の強さ", params.FLOAT, 0.0, 10.0),
)
GROUP_LABELS = {
    "Textures": "テクスチャ（差し替え）",
    "Common": "共通", "Shadow": "影", "Mask": "マスク", "Tint": "固定色", "Outline": "線",
    "Light": "ライト", "Depth": "手前に出す（眉・目を髪の上に）", "Rim": "リム", "Hair": "髪ハイライト",
    "Color": "色補正", "MatCap": "MatCap（簡易反射）", "FaceShadow": "顔影マップ（SDF）",
}


# テクスチャ未設定のときの意味（パラメータで違う。docs/03 §7.2 の既定値）
TEXTURE_PLACEHOLDER = {
    "common.albedo": "なし（ベース色だけ）。↺ で元マテリアルのテクスチャに戻す",
    "common.normal": "なし（モデルの法線のまま）",
    "common.emission": "なし（発光色だけ）",
    "_ToonMaskMap": "なし（白 = 何もしない）",
    "_ToonHairHighlightMap": "なし（ハイライトなし）",
    "_ToonMatCapMap": "なし（反射なし）",
    "_ToonFaceShadowMap": "なし（キャラクタータブの「マスクから生成」で作る）",
}


def _warn(parent: QtWidgets.QWidget, exc: Exception) -> None:
    QtWidgets.QMessageBox.warning(parent, "T-Drive Toon", str(exc))


class LookTab(QtWidgets.QWidget):
    def __init__(self, s: session.Session) -> None:
        super().__init__()
        self.session = s
        self.target: str | None = None
        self.rows: dict[str, ParamRow] = {}

        v = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("編集先:"))
        self.variant = QtWidgets.QComboBox()
        self.variant.setToolTip("base = 基本の値。バリアントを選ぶとその案への上書きとして書き込む（A/B タブで比較）")
        self.variant.currentIndexChanged.connect(self.on_variant)
        top.addWidget(self.variant)
        top.addStretch(1)
        # ショートカット（Ctrl+Z / Ctrl+Y）はエディタ全体で効くよう EditorWindow 側で登録している
        for text, fn, key in (("元に戻す", self.session.undo, "Ctrl+Z"), ("やり直す", self.session.redo, "Ctrl+Y")):
            b = QtWidgets.QPushButton(f"{text} ({key})")
            b.clicked.connect(fn)
            top.addWidget(b)
        reload_tex = QtWidgets.QPushButton("テクスチャを読み込み直す")
        reload_tex.setToolTip("ペイントツールなどで描き直したテクスチャを、プレビューで読み込み直す")
        reload_tex.clicked.connect(self._reload_textures)
        top.addWidget(reload_tex)
        v.addLayout(top)

        split = QtWidgets.QSplitter()
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["部位 / マテリアル"])
        self.tree.itemSelectionChanged.connect(self.on_target)
        split.addWidget(self.tree)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        self.panel = QtWidgets.QWidget()
        self.form = QtWidgets.QVBoxLayout(self.panel)
        self.title = QtWidgets.QLabel()
        self.title.setWordWrap(True)
        self.form.addWidget(self.title)
        self.hidden_note = QtWidgets.QLabel()
        self.hidden_note.setWordWrap(True)
        self.hidden_note.setStyleSheet("color: #9aa6b8;")
        self.form.addWidget(self.hidden_note)
        self.boxes: list[tuple[QtWidgets.QGroupBox, list[str]]] = []
        self._build_rows()
        self.form.addStretch(1)
        scroll.setWidget(self.panel)
        split.addWidget(scroll)
        split.setSizes([200, 560])
        v.addWidget(split, 1)

    def _build_rows(self) -> None:
        groups: dict[str, list[tuple]] = {"Common": list(COMMON_ROWS), "Textures": list(TEXTURE_ROWS)}
        for p in params.SPECIFIC_PARAMS:
            groups.setdefault(p.group, []).append((p.unity, p.label, p.kind, p.min, p.max))
        for group, rows in groups.items():
            box = QtWidgets.QGroupBox(GROUP_LABELS.get(group, group))
            grid = QtWidgets.QGridLayout(box)
            grid.setColumnStretch(1, 1)
            for i, (key, label, kind, lo, hi) in enumerate(rows):
                row = ParamRow(self, key, label, kind, lo, hi)
                row.add_to(grid, i)
                self.rows[key] = row
            self.form.addWidget(box)
            self.boxes.append((box, [r[0] for r in rows]))

    # -------------------------------------------------------------- 表示
    def refresh(self) -> None:
        lk = self.session.look
        self.variant.blockSignals(True)
        self.variant.clear()
        if lk:
            for name in look.variant_names(lk):
                label = name if name == look.BASE else f"{name}: {lk['variants'][name]['label']}"
                self.variant.addItem(label, name)
            self.variant.setCurrentIndex(max(0, self.variant.findData(self.session.edit_variant)))
        self.variant.blockSignals(False)

        if lk is None or (self.target and self.target not in lk["parts"] and self.target not in lk["materials"]):
            self.target = None
        self.tree.blockSignals(True)
        self.tree.clear()
        if lk:
            for part, info in sorted(lk["parts"].items()):
                item = QtWidgets.QTreeWidgetItem([f"{part}（{roles.ROLE_PRESETS[info['role']]['label']}）"])
                item.setData(0, QtCore.Qt.UserRole, part)
                self.tree.addTopLevelItem(item)
                for m in info["materials"]:
                    child = QtWidgets.QTreeWidgetItem([m])
                    child.setData(0, QtCore.Qt.UserRole, m)
                    item.addChild(child)
                    child.setSelected(m == self.target)
                item.setExpanded(True)
                item.setSelected(part == self.target)
        self.tree.blockSignals(False)
        self.refresh_values()

    def _apply_features(self) -> None:
        """オフの機能のパラメータを隠す（値は残っている。機能タブでオンにすると出る）。"""
        lk = self.session.look
        mats = self.session.materials_of(self.target) if lk is not None and self.target else []
        off = set()
        for key, row in self.rows.items():
            fid = features.FEATURE_OF_COMMON.get(key.split(".", 1)[1]) if key.startswith("common.") else features.FEATURE_OF_PARAM.get(key)
            if lk is None or fid is None:
                visible = True
            elif mats:  # 選んだ部位 / マテリアルで有効か（部位ごとの上書きを含む。docs/11 §2.1）
                visible = any(look.material_enabled(lk, m, fid) for m in mats)
            else:
                visible = look.enabled(lk, fid)
            row.set_visible(visible)
            if not visible:
                off.add(features.BY_ID[fid].label)
        for box, keys in self.boxes:
            box.setVisible(any(self.rows[k].visible for k in keys))
        self.hidden_note.setText(f"オフの機能は隠しています（機能タブ）: {'、'.join(sorted(off))}" if off else "")
        self.hidden_note.setVisible(bool(off))

    def refresh_values(self) -> None:
        self._apply_features()
        enabled = self.target is not None and self.session.look is not None
        self.panel.setEnabled(enabled)
        if not enabled:
            self.title.setText("左で部位かマテリアルを選んでください（部位を選ぶと所属マテリアルへ一括で設定）")
            return
        mats = self.session.materials_of(self.target)
        kind = "部位" if self.target in self.session.look["parts"] else "マテリアル"
        self.title.setText(f"{kind}: <b>{self.target}</b>（{', '.join(mats)}） / 編集先: <b>{self.session.edit_variant}</b>")
        for key, row in self.rows.items():
            values = self.session.values(self.target, key)
            mixed = any(v != values[0] for v in values)
            overridden = any(self.session.is_overridden(m, key) for m in mats)
            row.show_value(values[0], mixed, overridden)

    # -------------------------------------------------------------- 操作
    def on_variant(self) -> None:
        name = self.variant.currentData()
        if name and name != self.session.edit_variant:
            self.session.set_edit_variant(name)

    def select_target(self, target: str) -> None:
        self.target = target
        self.refresh()

    def on_target(self) -> None:
        items = self.tree.selectedItems()
        self.target = items[0].data(0, QtCore.Qt.UserRole) if items else None
        self.refresh_values()

    def _reload_textures(self) -> None:
        n = preview.reload_textures()
        from maya import cmds

        cmds.inViewMessage(amg=f"T-Drive: テクスチャを読み込み直しました（{n} 枚）", pos="topCenter", fade=True)

    def begin(self) -> None:
        """1 回の編集操作の開始（Undo の区切り）。"""
        self.session.checkpoint()

    def set(self, key: str, value, live: bool = False) -> None:
        """live=True はスライダー操作中。画面全体の再描画通知を間引く。"""
        if self.target is None:
            return
        try:
            self.session.set_value(self.target, key, value, notify=not live)
        except Exception as exc:
            _warn(self, exc)

    def reset(self, key: str) -> None:
        if self.target:
            self.session.reset_value(self.target, key)

    def clear_override(self, key: str) -> None:
        if self.target:
            self.session.clear_override(self.target, key)


class ParamRow:
    """1 パラメータ分: ラベル / 編集ウィジェット / ↺（プリセットに戻す）/ base（上書き解除）。"""

    def __init__(self, tab: LookTab, key: str, label: str, kind: str, lo: float, hi: float) -> None:
        self.tab, self.key, self.kind, self.lo, self.hi = tab, key, kind, float(lo), float(hi)
        self._color = [1.0, 1.0, 1.0, 1.0]
        self.visible = True
        self.label = QtWidgets.QLabel(label)
        self.label.setFixedWidth(170)  # グループをまたいでスライダーの開始位置を揃える
        self.editor = self._make_editor()
        self.reset_btn = QtWidgets.QToolButton()
        self.reset_btn.setText("↺")
        self.reset_btn.setToolTip(
            "元マテリアルのテクスチャに戻す" if key == "common.albedo" else "ロールのプリセット値（無ければ既定値）に戻す"
        )
        self.reset_btn.clicked.connect(lambda: tab.reset(key))
        self.base_btn = QtWidgets.QToolButton()
        self.base_btn.setText("base")
        self.base_btn.setToolTip("このバリアントでの上書きを外して base の値に戻す")
        self.base_btn.clicked.connect(lambda: tab.clear_override(key))

    def set_visible(self, on: bool) -> None:
        self.visible = on
        for w in (self.label, self.editor, self.reset_btn):
            w.setVisible(on)
        if not on:
            self.base_btn.setVisible(False)

    def add_to(self, grid: QtWidgets.QGridLayout, row: int) -> None:
        grid.addWidget(self.label, row, 0)
        grid.addWidget(self.editor, row, 1)
        grid.addWidget(self.reset_btn, row, 2)
        grid.addWidget(self.base_btn, row, 3)

    # ---------------------------------------------------------- 編集ウィジェット
    def _make_editor(self) -> QtWidgets.QWidget:
        k = self.kind
        if k == params.FLOAT:
            w = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            self.slider.setRange(0, 1000)
            self.spin = QtWidgets.QDoubleSpinBox()
            self.spin.setRange(self.lo, self.hi)
            self.spin.setDecimals(3)
            self.spin.setSingleStep((self.hi - self.lo) / 100)
            self.slider.sliderPressed.connect(self.tab.begin)
            self.slider.valueChanged.connect(self._on_slider)
            self.slider.sliderReleased.connect(lambda: self.tab.set(self.key, round(self.spin.value(), 4)))
            self.spin.editingFinished.connect(self._on_spin)
            h.addWidget(self.slider, 1)
            h.addWidget(self.spin)
            return w
        if k == "int":
            self.ispin = QtWidgets.QSpinBox()
            self.ispin.setRange(int(self.lo), int(self.hi))
            self.ispin.editingFinished.connect(self._on_int)
            return self.ispin
        if k == "bool":
            self.check = QtWidgets.QCheckBox()
            self.check.clicked.connect(self._on_bool)
            return self.check
        if k == "blend":
            self.combo = QtWidgets.QComboBox()
            self.combo.addItems(params.BLEND_TYPES)
            self.combo.activated.connect(self._on_blend)
            return self.combo
        if k == params.COLOR:
            self.color_btn = QtWidgets.QPushButton()
            self.color_btn.setFixedHeight(22)
            self.color_btn.clicked.connect(self._pick_color)
            return self.color_btn
        if k == params.TEXTURE:
            w = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            self.path = QtWidgets.QLineEdit()
            self.path.setPlaceholderText(TEXTURE_PLACEHOLDER.get(self.key, "なし"))
            self.path.editingFinished.connect(self._on_path)
            browse = QtWidgets.QToolButton()
            browse.setText("…")
            browse.clicked.connect(self._browse)
            h.addWidget(self.path, 1)
            h.addWidget(browse)
            return w
        raise ValueError(k)

    def _to_slider(self, v: float) -> int:
        return int(round((v - self.lo) / (self.hi - self.lo) * 1000)) if self.hi > self.lo else 0

    def _on_slider(self, pos: int) -> None:
        v = self.lo + (self.hi - self.lo) * pos / 1000
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        if self.slider.isSliderDown():
            self.tab.set(self.key, round(v, 4), live=True)  # ドラッグ中（Undo の区切りは押した時に 1 回）
        else:
            # 溝のクリック・矢印キー・ホイールは 1 回の編集として反映する（表示だけ動いて値が変わらない不具合の修正）
            self.tab.begin()
            self.tab.set(self.key, round(v, 4))

    def _on_spin(self) -> None:
        if self.spin.specialValueText() and self.spin.value() == self.spin.minimum():
            return  # 混在表示のまま
        self.tab.begin()
        self.tab.set(self.key, round(self.spin.value(), 4))

    def _on_int(self) -> None:
        self.tab.begin()
        self.tab.set(self.key, self.ispin.value())

    def _on_bool(self, on: bool) -> None:
        self.check.setTristate(False)
        self.tab.begin()
        self.tab.set(self.key, bool(on))

    def _on_blend(self, _index: int) -> None:
        if self.combo.currentText() in params.BLEND_TYPES:
            self.tab.begin()
            self.tab.set(self.key, self.combo.currentText())

    def _on_path(self) -> None:
        text = self.path.text().strip()
        if text == MIXED:
            return
        self.tab.begin()
        self.tab.set(self.key, text or None)

    def _pick_color(self) -> None:
        c = QtGui.QColor.fromRgbF(*[min(max(x, 0.0), 1.0) for x in self._color])
        picked = QtWidgets.QColorDialog.getColor(
            c, self.color_btn, "色（Unity のインスペクターと同じ値）", QtWidgets.QColorDialog.ShowAlphaChannel
        )
        if picked.isValid():
            self.tab.begin()
            self.tab.set(self.key, [round(x, 4) for x in (picked.redF(), picked.greenF(), picked.blueF(), picked.alphaF())])

    def _browse(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.editor, "テクスチャ", str(preview.project.root()), "Image (*.png *.tga *.tif *.exr *.psd *.jpg)"
        )
        if path:
            self.tab.begin()
            self.tab.set(self.key, preview.to_repo_path(path))

    # ---------------------------------------------------------- 値の表示
    def show_value(self, value, mixed: bool, overridden: bool) -> None:
        notes = [self.key, "部位内で値が異なる（操作すると揃う）" if mixed else "", "バリアントで上書き中" if overridden else ""]
        self.label.setToolTip(" / ".join(n for n in notes if n))
        self.label.setStyleSheet("color: #f0c060; font-weight: bold;" if overridden else "")
        self.base_btn.setVisible(overridden and self.visible)
        k = self.kind
        if k == params.FLOAT:
            self.spin.blockSignals(True)
            self.slider.blockSignals(True)
            if mixed:
                self.spin.setSpecialValueText(MIXED)
                self.spin.setValue(self.spin.minimum())
            else:
                self.spin.setSpecialValueText("")
                self.spin.setValue(float(value))
                self.slider.setValue(self._to_slider(float(value)))
            self.spin.blockSignals(False)
            self.slider.blockSignals(False)
        elif k == "int":
            self.ispin.blockSignals(True)
            self.ispin.setValue(int(value))
            self.ispin.blockSignals(False)
        elif k == "bool":
            self.check.setTristate(mixed)
            state = QtCore.Qt.PartiallyChecked if mixed else (QtCore.Qt.Checked if value else QtCore.Qt.Unchecked)
            self.check.setCheckState(state)
        elif k == "blend":
            self.combo.blockSignals(True)
            if mixed:
                if self.combo.findText(MIXED) < 0:
                    self.combo.addItem(MIXED)
                self.combo.setCurrentText(MIXED)
            else:
                i = self.combo.findText(MIXED)
                if i >= 0:
                    self.combo.removeItem(i)
                self.combo.setCurrentText(value)
            self.combo.blockSignals(False)
        elif k == params.COLOR:
            self._color = list(value) if isinstance(value, list) else [1.0, 1.0, 1.0, 1.0]
            if mixed:
                self.color_btn.setText(MIXED)
                self.color_btn.setStyleSheet("")
            else:
                r, g, b = (int(min(max(x, 0.0), 1.0) * 255) for x in self._color[:3])
                self.color_btn.setText(f"({self._color[0]:.2f}, {self._color[1]:.2f}, {self._color[2]:.2f})")
                fg = "#000" if (r * 0.3 + g * 0.6 + b * 0.1) > 128 else "#fff"
                self.color_btn.setStyleSheet(f"background-color: rgb({r},{g},{b}); color: {fg}; border: 1px solid #888;")
        elif k == params.TEXTURE:
            self.path.setText(MIXED if mixed else (value or ""))
            missing = not mixed and bool(value) and not Path(project.from_project_path(value)).exists()
            self.path.setStyleSheet("border: 1px solid #ff6060; color: #ff9090;" if missing else "")
            self.path.setToolTip(
                f"ファイルが見つかりません: {project.from_project_path(value)}（プロジェクトフォルダ基準）" if missing else (value or "")
            )
