"""ルックタブ（docs/05 §4、R-2 / R-5）: 部位 / マテリアルを選んでパラメータを調整する。

値はスライダー操作中も連続でビューポートに反映する（setAttr のみ）。
グループは折りたたみ式のセクション（E-2）。見出しのチェックは機能タブの「部位の列」と同じ意味。
"""

from __future__ import annotations

import webbrowser

from PySide6 import QtCore, QtGui, QtWidgets

from pathlib import Path

from . import REPO_ROOT, features, look, params, preview, project, roles, session
from .look_layout import (  # noqa: F401  COMMON_ROWS / GROUP_LABELS などは ui_ab・他のモジュールも参照する
    COMMON_ROWS, GROUP_FEATURE, GROUP_LABELS, MANUAL_PAGE, PARAM_HELP, PREVIEW_BADGE, SUB_FEATURES, TEXTURE_ROWS,
    anchor_of, build_groups, feature_of_key, pref_get, pref_set, section_pref, split_group,
)

MIXED = "—"

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


def open_manual(anchor: str) -> None:
    """デザイナーマニュアルの該当節をブラウザで開く。"""
    uri = (REPO_ROOT / "docs" / "DesignerManual" / MANUAL_PAGE).as_uri() + (f"#{anchor}" if anchor else "")
    webbrowser.open(uri)


class Section(QtWidgets.QWidget):
    """折りたたみセクション: 見出し（▼/▶ + タイトル + 機能チェック + ○/△ + 「?」）と中身。"""

    def __init__(self, tab: "LookTab", group: str, title: str, feature_id: str | None) -> None:
        super().__init__()
        self.group, self.title = group, title
        self.expanded = bool(pref_get(section_pref(group), 1))
        self.forced_open = False  # 検索中は該当のあるセクションを開く（開閉の記憶は上書きしない）
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        head = QtWidgets.QFrame()
        head.setFrameShape(QtWidgets.QFrame.StyledPanel)
        h = QtWidgets.QHBoxLayout(head)
        h.setContentsMargins(4, 2, 4, 2)
        self.toggle_btn = QtWidgets.QPushButton()
        self.toggle_btn.setFlat(True)
        self.toggle_btn.setStyleSheet("font-weight: bold; text-align: left; padding: 3px 4px;")
        self.toggle_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.toggle_btn.clicked.connect(self._on_toggle)
        h.addWidget(self.toggle_btn, 1)
        if feature_id:
            tab.add_feature_header(h, feature_id, anchor_of(group), None)
            f = features.BY_ID[feature_id]
            if not f.material_scope and not f.required:
                self.toggle_btn.setToolTip(f"{f.label}はキャラクター単位の機能です（機能タブの「全体」の列で切り替え）")
        else:
            h.addWidget(tab.make_help_button(anchor_of(group)))
        outer.addWidget(head)
        self.body = QtWidgets.QWidget()
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(8, 0, 0, 0)
        outer.addWidget(self.body)
        self._sync()

    def _on_toggle(self, *_) -> None:
        self.set_expanded(not self.expanded)

    def set_expanded(self, on: bool, save: bool = True) -> None:
        self.expanded = on
        if save:
            pref_set(section_pref(self.group), 1 if on else 0)
        self._sync()

    def set_forced_open(self, on: bool) -> None:
        if on != self.forced_open:
            self.forced_open = on
            self._sync()

    def _sync(self) -> None:
        shown = self.expanded or self.forced_open
        self.body.setVisible(shown)
        self.toggle_btn.setText(f"{'▼' if shown else '▶'} {self.title}")


class LookTab(QtWidgets.QWidget):
    def __init__(self, s: session.Session) -> None:
        super().__init__()
        self.session = s
        self.target: str | None = None
        self.rows: dict[str, ParamRow] = {}
        self.feature_checks: dict[str, QtWidgets.QCheckBox] = {}
        self.sections: dict[str, Section] = {}
        self.group_keys: dict[str, list[str]] = {}  # グループ → 行のキー
        self.sub_blocks: dict[str, tuple[QtWidgets.QWidget, list[str]]] = {}  # サブ機能 ID → (小見出し + 行, 行のキー)

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

        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.toolbar = QtWidgets.QHBoxLayout()
        self.open_all_btn = QtWidgets.QPushButton("すべて開く")
        self.open_all_btn.clicked.connect(lambda *_: self.set_all_expanded(True))
        self.close_all_btn = QtWidgets.QPushButton("すべて閉じる")
        self.close_all_btn.clicked.connect(lambda *_: self.set_all_expanded(False))
        self.toolbar.addWidget(self.open_all_btn)
        self.toolbar.addWidget(self.close_all_btn)
        self.toolbar.addStretch(1)
        rv.addLayout(self.toolbar)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        self.panel = QtWidgets.QWidget()
        self.form = QtWidgets.QVBoxLayout(self.panel)
        self.title = QtWidgets.QLabel()
        self.title.setWordWrap(True)
        self.form.addWidget(self.title)
        self._build_rows()
        self.form.addStretch(1)
        scroll.setWidget(self.panel)
        rv.addWidget(scroll, 1)
        split.addWidget(right)
        split.setSizes([200, 560])
        v.addWidget(split, 1)

    # -------------------------------------------------------------- 構築
    def _build_rows(self) -> None:
        for group, rows in build_groups().items():
            sec = Section(self, group, GROUP_LABELS.get(group, group), GROUP_FEATURE.get(group))
            self.sections[group] = sec
            self.form.addWidget(sec)
            self.group_keys[group] = [r[0] for r in rows]
            main, subs = split_group(group, rows)
            self._add_rows(sec.body_layout, main)
            for fid, sub_rows in subs:
                block = QtWidgets.QWidget()
                bl = QtWidgets.QVBoxLayout(block)
                bl.setContentsMargins(0, 0, 0, 0)
                head = QtWidgets.QHBoxLayout()
                head.setContentsMargins(0, 2, 0, 0)
                self.add_feature_header(head, fid, anchor_of(group, fid), features.BY_ID[fid].label)
                bl.addLayout(head)
                rows_widget = QtWidgets.QWidget()
                rows_layout = QtWidgets.QVBoxLayout(rows_widget)
                rows_layout.setContentsMargins(16, 0, 0, 0)
                self._add_rows(rows_layout, sub_rows)
                bl.addWidget(rows_widget)
                sec.body_layout.addWidget(block)
                self.sub_blocks[fid] = (block, [r[0] for r in sub_rows])

    def _add_rows(self, layout: QtWidgets.QVBoxLayout, rows: list[tuple]) -> None:
        if not rows:
            return
        grid_widget = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnStretch(1, 1)
        for i, (key, label, kind, lo, hi) in enumerate(rows):
            row = ParamRow(self, key, label, kind, lo, hi)
            row.add_to(grid, i)
            self.rows[key] = row
        layout.addWidget(grid_widget)

    def make_help_button(self, anchor: str) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton()
        b.setText("?")
        b.setAutoRaise(True)
        b.setToolTip("デザイナーマニュアルのこの節を開く")
        b.clicked.connect(lambda *_, a=anchor: open_manual(a))
        return b

    def add_feature_header(self, layout, fid: str, anchor: str, title: str | None) -> None:
        """見出しの機能チェック + ○/△ バッジ + 「?」を layout に足す。title があればチェックの文言にする（サブ機能）。"""
        from .ui_features import PREVIEW_LABELS

        f = features.BY_ID[fid]
        cb = QtWidgets.QCheckBox(title or "")
        if f.required:
            cb.setEnabled(False)
            cb.setToolTip(f"{f.label}は必須の機能です（オフにできません）")
        elif not f.material_scope:
            cb.setVisible(False)  # キャラクター単位の機能は部位ごとには切り替えられない
        else:
            cb.setToolTip(f"{f.label}: この部位 / マテリアルで使う（機能タブの部位の列と同じ）")
            cb.clicked.connect(lambda *_, fid=fid: self.toggle_feature(fid))
        self.feature_checks[fid] = cb
        badge = QtWidgets.QLabel(PREVIEW_BADGE[f.maya_preview])
        badge.setToolTip(PREVIEW_LABELS[f.maya_preview])
        layout.addWidget(cb, 1 if title else 0)
        layout.addWidget(badge)
        layout.addWidget(self.make_help_button(anchor))

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

    def feature_on(self, fid: str | None) -> bool:
        """選んだ部位 / マテリアルでこの機能が有効か（混在は有効扱い。値の行を出す）。"""
        lk = self.session.look
        if lk is None or fid is None:
            return True
        mats = self.session.materials_of(self.target) if self.target else []
        if mats:  # 部位ごとの上書きを含む（docs/11 §2.1）
            return any(look.material_enabled(lk, m, fid) for m in mats)
        return look.enabled(lk, fid)

    def _update_checks(self) -> None:
        lk = self.session.look
        for fid, cb in self.feature_checks.items():
            f = features.BY_ID[fid]
            cb.blockSignals(True)
            if lk is None:
                cb.setCheckState(QtCore.Qt.Unchecked)
            elif f.required:
                cb.setCheckState(QtCore.Qt.Checked)
            elif self.target and f.material_scope:
                _ov, eff = self.session.material_feature_state(self.target, fid)
                partial = len(set(eff)) > 1
                cb.setTristate(partial)
                cb.setCheckState(QtCore.Qt.PartiallyChecked if partial else (QtCore.Qt.Checked if eff[0] else QtCore.Qt.Unchecked))
                cb.setEnabled(True)
            else:  # 何も選んでいない: 全体の状態を表示だけ
                cb.setTristate(False)
                cb.setCheckState(QtCore.Qt.Checked if look.enabled(lk, fid) else QtCore.Qt.Unchecked)
                cb.setEnabled(False)
            cb.blockSignals(False)

    def _apply_visibility(self) -> None:
        """行・小見出し・セクションの表示を 1 か所で決める。
        オフの機能は値の行だけ隠す。見出し（グループ・小見出し）は残す（どこでオンにするか迷わない）。"""
        for key, row in self.rows.items():
            row.set_visible(self.feature_on(feature_of_key(key)))

    def toggle_feature(self, fid: str) -> None:
        """見出しのチェック: 全部オンならオフへ、それ以外（オフ・混在）はオンへ（機能タブの部位の列と同じ）。"""
        if not self.target or self.session.look is None:
            return
        try:
            _ov, eff = self.session.material_feature_state(self.target, fid)
            on = not all(eff)
            if self.target in self.session.look["parts"]:
                self.session.set_part_feature(self.target, fid, on)
            else:
                self.session.set_material_feature(self.target, fid, on)
        except Exception as exc:
            _warn(self, exc)
        self.refresh_values()  # 表示中でないタブは session の通知で更新されないことがあるので自分でも更新する

    def set_all_expanded(self, on: bool) -> None:
        for sec in self.sections.values():
            sec.set_expanded(on)

    def refresh_values(self) -> None:
        self._update_checks()
        self._apply_visibility()
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
        if k in ("bool", "toggle01"):
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
        self.tab.set(self.key, (1.0 if on else 0.0) if self.kind == "toggle01" else bool(on))

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
        notes = [self.key, PARAM_HELP.get(self.key, ""), "部位内で値が異なる（操作すると揃う）" if mixed else "", "バリアントで上書き中" if overridden else ""]
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
        elif k in ("bool", "toggle01"):
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
