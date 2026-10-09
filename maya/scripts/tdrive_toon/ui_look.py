"""ルックタブ（docs/05 §4、R-2 / R-5）: 部位 / マテリアルを選んでパラメータを調整する。

値はスライダー操作中も連続でビューポートに反映する（setAttr のみ）。
グループは折りたたみ式のセクション（E-2）。見出しのチェックは機能タブの「部位の列」と同じ意味。
"""

from __future__ import annotations

import json
import os
import webbrowser

from PySide6 import QtCore, QtGui, QtWidgets

from pathlib import Path

from . import REPO_ROOT, features, look, look_layout, params, presets, preview, project, roles, session, ui_look_dialogs
from .look_layout import (  # noqa: F401  COMMON_ROWS / GROUP_LABELS などは ui_ab・他のモジュールも参照する
    COMMON_ROWS, MODE_DETAIL, MODE_PREF, MODE_SIMPLE, SIMPLE_KEYS, GROUP_FEATURE, GROUP_LABELS, MANUAL_PAGE, PARAM_HELP, PREVIEW_BADGE, SUB_FEATURES, TEXTURE_ROWS,
    anchor_of, build_groups, collect_values, feature_of_key, format_floats, format_hex, parse_color, pref_get, pref_set, row_passes, section_pref, split_group,
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


class _WheelOnFocusMixin:
    """ホイールはフォーカスがあるときだけ値を変える（パネルをスクロール中の誤操作を防ぐ。E-8）。"""

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()  # 親（スクロール領域）へ渡す


class ToonSlider(_WheelOnFocusMixin, QtWidgets.QSlider):
    """ダブルクリックで ↺（doubleClicked）、Shift を押しながらドラッグで 1/10 の細かさ、ホイールはフォーカス時だけ。"""

    doubleClicked = QtCore.Signal()
    FINE = 0.1

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setToolTip("ドラッグで変更 / Shift を押しながらで 1/10 の細かさ / ダブルクリックで既定値に戻す / ホイールはクリックしてフォーカスしてから")
        self._manual = False  # Shift を押した後は、ドラッグの終わりまでこちらで動かす（ハンドルが飛ばないように）
        self._last_x = 0.0
        self._virtual = 0.0

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._manual = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._manual = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.doubleClicked.emit()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self.isSliderDown():
            return super().mouseMoveEvent(event)
        x = event.position().x()
        shift = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
        if shift and not self._manual:
            self._manual, self._last_x, self._virtual = True, x, float(self.value())
        if self._manual:
            span = max(1, self.width() - self.style().pixelMetric(QtWidgets.QStyle.PM_SliderLength))
            per_px = (self.maximum() - self.minimum()) / span
            self._virtual += (x - self._last_x) * per_px * (self.FINE if shift else 1.0)
            self._virtual = min(max(self._virtual, self.minimum()), self.maximum())
            self._last_x = x
            self.setValue(int(round(self._virtual)))
            event.accept()
            return
        super().mouseMoveEvent(event)


class ToonSpin(_WheelOnFocusMixin, QtWidgets.QDoubleSpinBox):
    """Enter で確定（editingFinished）、Esc でフォーカスしたときの値に戻す、ホイールはフォーカス時だけ。"""

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._at_focus = 0.0

    def focusInEvent(self, event) -> None:  # noqa: N802
        self._at_focus = self.value()
        super().focusInEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == QtCore.Qt.Key_Escape:
            self.blockSignals(True)
            self.setValue(self._at_focus)
            self.blockSignals(False)
            self.lineEdit().deselect()
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)


class ToonIntSpin(_WheelOnFocusMixin, QtWidgets.QSpinBox):
    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(QtCore.Qt.StrongFocus)


class Section(QtWidgets.QWidget):
    """折りたたみセクション: 見出し（▼/▶ + タイトル + 機能チェック + ○/△ + 「?」）と中身。"""

    def __init__(self, tab: "LookTab", group: str, title: str, feature_id: str | None) -> None:
        super().__init__()
        self.group, self.title, self.tab = group, title, tab
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
        for w in (head, self.toggle_btn):  # 見出しの右クリック: グループ単位のコピー / 貼り付け / ↺ / base（E-4）
            w.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            w.customContextMenuRequested.connect(lambda pos, w=w: self.tab.group_menu(self.group, w, pos))
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
        self.preset_btn = QtWidgets.QToolButton()
        self.preset_btn.setText("プリセット ▼")
        self.preset_btn.setToolTip("名前を付けて保存した値のセット（プロジェクトの presets フォルダ）。選んでいる部位 / マテリアルへ適用します")
        self.preset_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.preset_menu = QtWidgets.QMenu(self.preset_btn)
        self.preset_menu.aboutToShow.connect(self._fill_preset_menu)  # 開くたびに一覧を作り直す
        self.preset_btn.setMenu(self.preset_menu)
        top.addWidget(self.preset_btn)
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
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
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
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("かんたん", MODE_SIMPLE)
        self.mode.addItem("詳細", MODE_DETAIL)
        self.mode.setToolTip("かんたん = よく使う項目だけ（影・線・固定色・リム・髪ハイライト・ベース色）。詳細 = 全部。検索中は両方から探します")
        saved = pref_get(MODE_PREF, MODE_DETAIL)
        self.mode.setCurrentIndex(max(0, self.mode.findData(saved)))
        self.mode.currentIndexChanged.connect(self._on_mode)
        self.toolbar.addWidget(self.mode)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("検索（名前・Unity 名・説明）")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip("表示名・Unity のプロパティ名・説明に部分一致する項目だけ表示します。Esc で消去")
        self.search.textChanged.connect(self._on_search)
        esc = QtGui.QShortcut(QtGui.QKeySequence("Escape"), self.search)
        esc.setContext(QtCore.Qt.WidgetShortcut)
        esc.activated.connect(self.search.clear)
        self.toolbar.addWidget(self.search, 1)
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
            if group == "Shadow":
                btn = QtWidgets.QPushButton("ベース色から作る…")
                btn.setToolTip("ベース色（かベースマップの平均色）から、色相・彩度・明度をずらして影色と 2 影の色を作る")
                btn.clicked.connect(lambda *_: self.open_shade_dialog())
                sec.body_layout.addWidget(btn)
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
        表示の可否 = 機能がオン AND モード（かんたん / 詳細。検索中は無視）AND 検索に一致。
        オフの機能は値の行だけ隠す。見出し（グループ・小見出し）は残す（どこでオンにするか迷わない）。
        検索中は、一致する行を持つグループだけ出して開く（開閉の記憶は上書きしない）。"""
        query = self.search.text()
        mode = self.mode.currentData()
        passes = {key: row_passes(key, row.label.text(), query, mode) for key, row in self.rows.items()}
        for key, row in self.rows.items():
            row.set_visible(passes[key] and self.feature_on(feature_of_key(key)))
        for block, keys in self.sub_blocks.values():
            block.setVisible(any(passes[k] for k in keys))
        for group, sec in self.sections.items():
            sec.setVisible(any(passes[k] for k in self.group_keys[group]))
            sec.set_forced_open(bool(query.strip()))

    def _on_mode(self, *_) -> None:
        pref_set(MODE_PREF, self.mode.currentData())
        self._apply_visibility()

    def _on_search(self, *_) -> None:
        self._apply_visibility()

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
            defaults = [self.session.default_value(m, key) for m in mats] if row.kind in (params.FLOAT, "int") else None
            row.show_value(values[0], mixed, overridden, defaults)

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

    # -------------------------------------------------------------- コピー / 貼り付け / 戻す（E-4）
    def notify(self, text: str) -> None:
        """画面の上に短く知らせる（Maya が無ければ print）。"""
        try:
            from maya import cmds

            cmds.inViewMessage(amg=f"T-Drive: {text}", pos="topCenter", fade=True)
        except Exception:  # noqa: BLE001
            print(f"T-Drive: {text}")

    def all_keys(self) -> list[str]:
        return [k for keys in self.group_keys.values() for k in keys]

    def copy_keys(self, keys: list[str], target: str | None = None) -> None:
        """keys の今の値をクリップボードへ JSON で（メモ帳に貼って中身が見える）。"""
        target = target or self.target
        if not target or self.session.look is None:
            return
        flat = collect_values(lambda k: self.session.values(target, k), keys)
        text = json.dumps(look.flat_to_payload(flat), ensure_ascii=False, indent=2)
        QtWidgets.QApplication.clipboard().setText(text)
        self.notify(f"{len(flat)} 件をコピーしました")

    def clipboard_flat(self) -> tuple[dict, list[str]] | None:
        """クリップボードの JSON を検証した (使える値, 捨てたキー)。JSON でなければ None。"""
        try:
            payload = json.loads(QtWidgets.QApplication.clipboard().text())
        except (ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        return look.filter_pasteable(payload)

    def paste_keys(self, keys: list[str] | None = None, target: str | None = None) -> None:
        """クリップボードの値を target（既定 = 選択中）へ。keys があればその範囲だけ（行・グループ）。1 回の貼り付け = 1 Undo。"""
        target = target or self.target
        if not target or self.session.look is None:
            return
        parsed = self.clipboard_flat()
        if parsed is None:
            self.notify("クリップボードに貼り付けられる内容がありません（T-Drive でコピーした JSON が必要）")
            return
        flat, dropped = parsed
        if keys is not None:
            flat = look.restrict_keys(flat, keys)
        self.apply_flat(flat, len(dropped), target, "貼り付け")

    def apply_flat(self, flat: dict, dropped: int, target: str | None = None, verb: str = "適用") -> None:
        """検証済みの値を target へ 1 回の編集で書く（貼り付け・プリセット共通）。"""
        target = target or self.target
        if not target or self.session.look is None:
            return
        if not flat:
            self.notify(f"{verb}できる項目がありません（{dropped} 件を捨てました）" if dropped else f"{verb}できる項目がありません")
            return
        try:
            self.session.set_values(target, flat)
        except Exception as exc:  # noqa: BLE001
            _warn(self, exc)
            return
        self.notify(f"{len(flat)} 件を{verb}し、{dropped} 件を捨てました")

    def reset_keys(self, keys: list[str]) -> None:
        if self.target and keys:
            self.session.reset_values(self.target, keys)

    def clear_override_keys(self, keys: list[str]) -> None:
        if self.target and keys:
            self.session.clear_overrides(self.target, keys)

    def fill_edit_menu(self, menu: QtWidgets.QMenu, keys: list[str], what: str, target: str | None = None) -> None:
        """コピー / 貼り付け / ↺ / base に戻す（base は編集先がバリアントのときだけ）を menu に足す。
        target を渡すとツリー用（その部位 / マテリアルへのコピー・貼り付けだけ）。"""
        enabled = (target or self.target) is not None and self.session.look is not None
        copy_a = menu.addAction(f"{what}をコピー")
        paste_a = menu.addAction(f"{what}に貼り付け")
        copy_a.setEnabled(enabled)
        paste_a.setEnabled(enabled)
        copy_a.triggered.connect(lambda *_: self.copy_keys(keys, target))
        paste_a.triggered.connect(lambda *_: self.paste_keys(keys, target))
        if target is None:  # ツリーのときは出さない（マテリアル全体を戻す操作は用意しない）
            menu.addSeparator()
            reset_a = menu.addAction(f"↺ {what}をプリセットに戻す")
            reset_a.setEnabled(enabled)
            reset_a.triggered.connect(lambda *_: self.reset_keys(keys))
            if self.session.edit_variant != look.BASE:
                base_a = menu.addAction(f"{what}を base に戻す（バリアントの上書きを外す）")
                base_a.setEnabled(enabled)
                base_a.triggered.connect(lambda *_: self.clear_override_keys(keys))

    def group_menu(self, group: str, widget: QtWidgets.QWidget, pos) -> None:
        menu = QtWidgets.QMenu(widget)
        self.fill_edit_menu(menu, self.group_keys[group], f"「{GROUP_LABELS.get(group, group)}」グループ")
        menu.exec(widget.mapToGlobal(pos))

    def _tree_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        target = item.data(0, QtCore.Qt.UserRole)
        menu = QtWidgets.QMenu(self.tree)
        self.fill_edit_menu(menu, self.all_keys(), f"「{target}」の全項目", target)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # -------------------------------------------------------------- 名前付きプリセット（E-5）
    def _fill_preset_menu(self) -> None:
        menu = self.preset_menu
        menu.clear()
        has_target = self.target is not None and self.session.look is not None
        try:
            entries = presets.list_presets()
        except OSError as exc:
            entries = []
            menu.addAction(f"（プリセットフォルダを読めません: {exc}）").setEnabled(False)
        for e in entries:
            if e.ok:
                act = menu.addAction(e.label if e.label == e.name else f"{e.label}（{e.name}）")
                act.setEnabled(has_target)
                act.setToolTip(str(e.path))
                act.triggered.connect(lambda *_, path=e.path: self.apply_preset(path))
            else:
                act = menu.addAction(f"{e.name}（読めません）")
                act.setEnabled(False)
                act.setToolTip(e.error)
        if not entries:
            menu.addAction("（プリセットはまだありません）").setEnabled(False)
        menu.addSeparator()
        save = menu.addAction("保存…")
        save.setEnabled(has_target)
        save.triggered.connect(lambda *_: self.save_preset())
        open_dir = menu.addAction("フォルダを開く")
        open_dir.triggered.connect(lambda *_: self.open_preset_folder())

    def apply_preset(self, path) -> None:
        """プリセットを選んでいる部位 / マテリアルへ（含まれるキーだけ上書き。貼り付けと同じ経路。1 Undo）。"""
        if not self.target or self.session.look is None:
            return
        try:
            preset = presets.load(path)
        except presets.PresetError as exc:
            QtWidgets.QMessageBox.warning(self, "T-Drive Toon", f"プリセットを読めません: {Path(path).name}\n{exc}")
            return
        for w in preset.warnings:
            print(f"T-Drive: プリセット {Path(path).name}: {w}")
        self.apply_flat(preset.flat, len(preset.dropped), self.target, "適用")

    def save_preset(self) -> None:
        if not self.target or self.session.look is None:
            return
        dlg = ui_look_dialogs.PresetSaveDialog(self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        name = presets.safe_name(dlg.name.text())
        if presets.path_of(name).exists() and not ui_look_dialogs.ask_overwrite(self, name):
            return
        target = self.target
        flat = collect_values(lambda k: self.session.values(target, k), look_layout.keys_of_groups(None, dlg.groups()))
        data = presets.build(dlg.label.text().strip() or name, dlg.groups(), flat)
        try:
            path = presets.save(name, data)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "T-Drive Toon", f"保存できません: {exc}")
            return
        self.notify(f"プリセットを保存しました: {path.name}")

    def open_preset_folder(self) -> None:
        folder = presets.presets_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))  # noqa: S606  Windows のエクスプローラーで開く
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "T-Drive Toon", f"フォルダを開けません: {exc}")

    # -------------------------------------------------------------- 影色をベース色から作る（E-6）
    def open_shade_dialog(self) -> None:
        if not self.target or self.session.look is None:
            return
        tints = self.session.values(self.target, "common.albedoTint")
        base = list(tints[0][:3]) if tints and isinstance(tints[0], list) else [1.0, 1.0, 1.0]
        maps = self.session.values(self.target, "common.albedo")
        map_rgb = None
        if maps and maps[0]:
            map_rgb = ui_look_dialogs.map_average_color(maps[0])
        dlg = ui_look_dialogs.ShadeFromBaseDialog(self, base, map_rgb)
        self._shade_dialog = dlg  # 開いている間の参照
        dlg.open()

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
        self._mixed = False
        self._shown: float | None = None  # 数値欄に表示している値（混在のときは None）
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
        # 右クリック: コピー / 貼り付け / ↺ / base に戻す（E-4）。数値欄は Qt 標準の編集メニューのまま
        targets = [self.label, self.editor]
        for name in ("slider", "path"):
            if hasattr(self, name):
                targets.append(getattr(self, name))
        for w in targets:
            w.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            w.customContextMenuRequested.connect(lambda pos, w=w: self._show_menu(w, pos))

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
            self.slider = ToonSlider(QtCore.Qt.Horizontal)
            self.slider.setRange(0, 1000)
            self.spin = ToonSpin()
            self.spin.setRange(self.lo, self.hi)
            self.spin.setDecimals(3)
            self.spin.setSingleStep((self.hi - self.lo) / 100)
            self.slider.sliderPressed.connect(self.tab.begin)
            self.slider.valueChanged.connect(self._on_slider)
            self.slider.sliderReleased.connect(lambda: self.tab.set(self.key, round(self.spin.value(), 4)))
            self.slider.doubleClicked.connect(lambda *_: self.tab.reset(self.key))
            self.spin.editingFinished.connect(self._on_spin)
            h.addWidget(self.slider, 1)
            h.addWidget(self.spin)
            return w
        if k == "int":
            self.ispin = ToonIntSpin()
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
        if self._shown is not None and abs(self.spin.value() - round(self._shown, self.spin.decimals())) < 1e-9:
            return  # Esc で戻した・変えずに Enter / フォーカス移動: 何も起きない（Undo を積まない）
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

    def _pick_color(self, *_) -> None:
        """ダイアログを開いたまま操作でき（ウィンドウモーダル）、ドラッグ中もビューポートに反映する。
        OK で確定、キャンセルで元の値（混在のときは元の各値）に戻す。混在のときは白から始める。"""
        start = [1.0, 1.0, 1.0, 1.0] if self._mixed else list(self._color)
        dlg = QtWidgets.QColorDialog(QtGui.QColor.fromRgbF(*[min(max(x, 0.0), 1.0) for x in start]), self.color_btn)
        dlg.setOption(QtWidgets.QColorDialog.ShowAlphaChannel, True)
        dlg.setWindowTitle("色（Unity のインスペクターと同じ値）")
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dlg.setWindowModality(QtCore.Qt.WindowModal)
        begun = [False]

        def rgba(c: QtGui.QColor) -> list[float]:
            return [round(x, 4) for x in (c.redF(), c.greenF(), c.blueF(), c.alphaF())]

        def on_changed(c: QtGui.QColor) -> None:
            if not begun[0]:
                begun[0] = True
                self.tab.begin()  # Undo の区切りは開いた後の最初の変更で 1 回
            self.tab.set(self.key, rgba(c), live=True)

        def on_accepted() -> None:
            if not begun[0]:
                self.tab.begin()
            self.tab.set(self.key, rgba(dlg.currentColor()))  # 確定（画面全体の再描画通知つき）

        def on_rejected() -> None:
            if begun[0]:
                self.tab.session.cancel_edit()  # 元の値へ（やり直しには残さない）

        dlg.currentColorChanged.connect(on_changed)
        dlg.accepted.connect(on_accepted)
        dlg.rejected.connect(on_rejected)
        dlg.open()

    def _show_menu(self, widget: QtWidgets.QWidget, pos) -> None:
        menu = QtWidgets.QMenu(widget)
        self._fill_kind_menu(menu)
        self.tab.fill_edit_menu(menu, [self.key], "この項目")
        menu.exec(widget.mapToGlobal(pos))

    def _fill_kind_menu(self, menu: QtWidgets.QMenu) -> None:
        """種類ごとの項目（色のコピー / 貼り付け、テクスチャの操作）。あれば最後に区切りを入れる。"""
        if self.kind == params.COLOR:
            self._fill_color_menu(menu)
            menu.addSeparator()

    def _fill_color_menu(self, menu: QtWidgets.QMenu) -> None:
        copy_hex = menu.addAction("16 進をコピー")
        copy_f = menu.addAction("0〜1 の数値をコピー")
        paste = menu.addAction("色を貼り付け（#RRGGBB / #RRGGBBAA / r, g, b[, a]）")
        copy_hex.setEnabled(not self._mixed)
        copy_f.setEnabled(not self._mixed)
        copy_hex.triggered.connect(lambda *_: QtWidgets.QApplication.clipboard().setText(format_hex(self._color)))
        copy_f.triggered.connect(lambda *_: QtWidgets.QApplication.clipboard().setText(format_floats(self._color)))
        paste.triggered.connect(lambda *_: self._paste_color())

    def _paste_color(self) -> None:
        text = QtWidgets.QApplication.clipboard().text()
        alpha = 1.0 if self._mixed else self._color[3]
        value = parse_color(text, alpha)
        if value is None:
            QtWidgets.QMessageBox.warning(self.color_btn, "T-Drive Toon", f"色として読めません: {text[:40]!r}\n#RRGGBB・#RRGGBBAA・r, g, b[, a]（0〜1）で貼り付けてください")
            return
        self.tab.begin()
        self.tab.set(self.key, value)

    def _browse(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.editor, "テクスチャ", str(preview.project.root()), "Image (*.png *.tga *.tif *.exr *.psd *.jpg)"
        )
        if path:
            self.tab.begin()
            self.tab.set(self.key, preview.to_repo_path(path))

    # ---------------------------------------------------------- 値の表示
    def _range_note(self, defaults: list | None) -> str:
        """ラベルのツールチップ: 「範囲 lo〜hi / 既定 x」（数値の項目だけ。既定 = ロールのプリセット値か契約の既定値）。"""
        if self.kind not in (params.FLOAT, "int") or not defaults:
            return ""
        default = "マテリアルで異なる" if any(d != defaults[0] for d in defaults) else f"{defaults[0]:g}"
        return f"範囲 {self.lo:g}〜{self.hi:g} / 既定 {default}"

    def show_value(self, value, mixed: bool, overridden: bool, defaults: list | None = None) -> None:
        notes = [self.key, self._range_note(defaults), PARAM_HELP.get(self.key, ""), "部位内で値が異なる（操作すると揃う）" if mixed else "", "バリアントで上書き中" if overridden else ""]
        self.label.setToolTip(" / ".join(n for n in notes if n))
        self.label.setStyleSheet("color: #f0c060; font-weight: bold;" if overridden else "")
        self.base_btn.setVisible(overridden and self.visible)
        k = self.kind
        if k == params.FLOAT:
            self.spin.blockSignals(True)
            self.slider.blockSignals(True)
            self._shown = None if mixed else float(value)
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
            self._mixed = mixed
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
