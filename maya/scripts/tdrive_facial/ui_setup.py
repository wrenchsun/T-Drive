"""セットアップタブ（docs/14 §5.2）: 対象メッシュ・基準ボーン・前方向・格子・ミラー・自動生成・ベイク・プロファイル・作業セット。

画面の操作はすべてセッションのコマンド（`session.set_*` など）を呼ぶ。失敗したときは `result.message` を下の状態欄に出す。
値の表示は `refresh()` で Document から入れ直す（そのあいだ `_updating` で操作の通知を止める）。
"""

from __future__ import annotations

import traceback
from typing import Optional

from maya import cmds
from PySide6 import QtCore, QtWidgets

from tdrive import lifecycle, project

from . import scene as scene_mod
from .session import NoLookError
from .core import profile as profile_mod
from .core.model import FILL_MODES, MIRROR_AXES
from .ui import DIM_STYLE, NO_PROFILE, WARN_STYLE, ask_yes_no, mesh_choices, profile_names

MESH_MISSING = "（シーンに見つかりません）"
FORWARD_CHOICES = ("+X", "-X", "+Z", "-Z")  # Maya は Y-up なので上下は選べない
FILL_LABELS = {"IDW": "IDW（まわりのキーをなめらかに混ぜる）", "NearestKey": "最近傍（いちばん近いキーをそのまま使う）"}
CURVE_TIP = "シェイプ（blendShape のターゲット）。ビューポートでの強調表示は今後対応予定（TODO）"
BONE_TIP = "クリックすると Maya でこのジョイントを選びます"


def _selected_mesh_transforms() -> list[str]:
    """選択中のメッシュ（transform の長い名前。shape を選んでいても transform にする）。"""
    out: list[str] = []
    for n in cmds.ls(selection=True, long=True) or []:
        if cmds.nodeType(n) == "mesh":
            n = (cmds.listRelatives(n, parent=True, fullPath=True) or [n])[0]
        if cmds.listRelatives(n, shapes=True, type="mesh") and n not in out:
            out.append(n)
    return out


def _selected_joints() -> list[str]:
    """選択中のジョイント（短い名前）。"""
    out: list[str] = []
    for n in cmds.ls(selection=True, long=True, type="joint") or []:
        s = scene_mod.short_name(n)
        if s not in out:
            out.append(s)
    return out


def _spin(lo: float, hi: float, step: float, decimals: int = 2, suffix: str = "") -> QtWidgets.QDoubleSpinBox:
    w = QtWidgets.QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setDecimals(decimals)
    w.setKeyboardTracking(False)  # 入力の途中ではなく、確定したときに 1 回だけ通知する（Undo が細切れにならない）
    if suffix:
        w.setSuffix(suffix)
    return w


def _int_spin(lo: int, hi: int) -> QtWidgets.QSpinBox:
    w = QtWidgets.QSpinBox()
    w.setRange(lo, hi)
    w.setKeyboardTracking(False)
    return w


class SetupTab(QtWidgets.QWidget):
    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self._updating = False
        self._list_sig: dict[str, tuple] = {}

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        body = QtWidgets.QWidget()
        self.form = QtWidgets.QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self._build_target()
        self._build_bone()
        self._build_grid()
        self._build_mirror()
        self._build_autogen()
        self._build_profile()
        self._build_t20()
        self._build_working_set()
        self.form.addStretch(1)
        self.refresh()

    # ============================================================ 組み立て
    def _group(self, title: str) -> QtWidgets.QFormLayout:
        box = QtWidgets.QGroupBox(title)
        f = QtWidgets.QFormLayout(box)
        f.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.form.addWidget(box)
        return f

    def _build_target(self) -> None:
        f = self._group("対象のメッシュ")
        row = QtWidgets.QHBoxLayout()
        self.mesh = QtWidgets.QComboBox()
        self.mesh.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.mesh.setMinimumContentsLength(12)
        self.mesh.setToolTip("顔のメッシュ（blendShape があるもの）。ここを基準にシェイプとボーンを探します")
        self.mesh.activated.connect(self.on_mesh_activated)
        row.addWidget(self.mesh, 1)
        self.mesh_from_sel = QtWidgets.QPushButton("選択から")
        self.mesh_from_sel.setToolTip("Maya で選んでいるメッシュを顔のメッシュにする")
        self.mesh_from_sel.clicked.connect(lambda *_: self.on_mesh_from_selection())
        row.addWidget(self.mesh_from_sel)
        f.addRow("顔のメッシュ", row)

        self.extra_list = QtWidgets.QListWidget()
        self.extra_list.setMaximumHeight(70)
        self.extra_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.extra_list.setToolTip("まつ毛・眉など、顔と一緒に補正したい別メッシュ")
        f.addRow("追加のメッシュ", self.extra_list)
        row2 = QtWidgets.QHBoxLayout()
        self.extra_add = QtWidgets.QPushButton("選択から追加")
        self.extra_add.clicked.connect(lambda *_: self.on_extra_add())
        self.extra_remove = QtWidgets.QPushButton("外す")
        self.extra_remove.clicked.connect(lambda *_: self.on_extra_remove())
        row2.addWidget(self.extra_add)
        row2.addWidget(self.extra_remove)
        row2.addStretch(1)
        f.addRow("", row2)

    def _build_bone(self) -> None:
        f = self._group("基準ボーンと向き")
        row = QtWidgets.QHBoxLayout()
        self.base_bone = QtWidgets.QLineEdit()
        self.base_bone.setToolTip("頭のボーン。格子の中心と向きの基準になります")
        self.base_bone.editingFinished.connect(self.on_base_bone_edited)
        row.addWidget(self.base_bone, 1)
        self.bone_detect = QtWidgets.QPushButton("自動検出")
        self.bone_detect.setToolTip("顔のメッシュのスキンの骨から、頭のボーンを名前で探す")
        self.bone_detect.clicked.connect(lambda *_: self.on_detect_bone())
        row.addWidget(self.bone_detect)
        self.bone_from_sel = QtWidgets.QPushButton("選択から")
        self.bone_from_sel.setToolTip("Maya で選んでいるジョイントを基準ボーンにする")
        self.bone_from_sel.clicked.connect(lambda *_: self.on_bone_from_selection())
        row.addWidget(self.bone_from_sel)
        f.addRow("基準ボーン", row)

        self.forward = QtWidgets.QComboBox()
        for a in FORWARD_CHOICES:
            self.forward.addItem(a, a)
        self.forward.setToolTip("顔が向いている方向（基準ボーンのローカル軸）。Maya は Y が上なので X か Z を選びます")
        self.forward.activated.connect(self.on_forward_activated)
        f.addRow("顔の前方向", self.forward)

        row = QtWidgets.QHBoxLayout()
        self.center = [_spin(-1000.0, 1000.0, 0.1, 3) for _ in range(3)]
        for axis, w in zip("XYZ", self.center):
            w.setToolTip(f"格子の中心を基準ボーンから {axis} 方向へずらす量（cm）")
            w.valueChanged.connect(lambda *_: self.on_center_changed())
            row.addWidget(QtWidgets.QLabel(axis))
            row.addWidget(w, 1)
        f.addRow("中心のずらし(cm)", row)

    def _build_grid(self) -> None:
        f = self._group("格子")
        self.cols = _int_spin(1, 64)
        self.rows = _int_spin(1, 64)
        self.yaw = _spin(1.0, 360.0, 5.0, 1, "°")
        self.pitch = _spin(1.0, 180.0, 5.0, 1, "°")
        self.cols.setToolTip("横（Yaw）方向の点の数")
        self.rows.setToolTip("縦（Pitch）方向の点の数")
        self.yaw.setToolTip("左右の角度の範囲（全体の幅）")
        self.pitch.setToolTip("上下の角度の範囲（全体の幅）")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("列"))
        row.addWidget(self.cols)
        row.addWidget(QtWidgets.QLabel("行"))
        row.addWidget(self.rows)
        row.addStretch(1)
        f.addRow("点の数", row)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Yaw"))
        row.addWidget(self.yaw)
        row.addWidget(QtWidgets.QLabel("Pitch"))
        row.addWidget(self.pitch)
        row.addStretch(1)
        f.addRow("角度の範囲", row)
        self.grid_apply = QtWidgets.QPushButton("適用")
        self.grid_apply.setToolTip("格子の大きさを変える。作った点は角度で引き継ぎ、重ならなくなったキーは先にお知らせします")
        self.grid_apply.clicked.connect(lambda *_: self.on_grid_apply())
        f.addRow("", self.grid_apply)
        self.grid_note = QtWidgets.QLabel()
        self.grid_note.setWordWrap(True)
        self.grid_note.setStyleSheet(WARN_STYLE)
        self.grid_note.setVisible(False)
        f.addRow(self.grid_note)
        self.edge_fade = _spin(0.0, 90.0, 1.0, 1, "°")
        self.edge_fade.setToolTip("格子の端で補正をなめらかに弱める角度")
        self.edge_fade.valueChanged.connect(lambda *_: self.on_edge_fade_changed())
        f.addRow("端のフェード", self.edge_fade)

    def _build_mirror(self) -> None:
        f = self._group("ミラー（左右反転）")
        self.mirror_on = QtWidgets.QCheckBox("使う")
        self.mirror_on.setToolTip("オフにすると、ミラーでの貼り付けや反転ができなくなります")
        self.mirror_on.clicked.connect(lambda *_: self.on_mirror_changed())
        f.addRow("", self.mirror_on)
        row = QtWidgets.QHBoxLayout()
        self.suffix_l = QtWidgets.QLineEdit()
        self.suffix_r = QtWidgets.QLineEdit()
        for w in (self.suffix_l, self.suffix_r):
            w.setMaximumWidth(90)
            w.editingFinished.connect(self.on_mirror_changed)
        row.addWidget(QtWidgets.QLabel("左"))
        row.addWidget(self.suffix_l)
        row.addWidget(QtWidgets.QLabel("右"))
        row.addWidget(self.suffix_r)
        row.addStretch(1)
        f.addRow("名前の末尾", row)
        self.mirror_exclude = QtWidgets.QLineEdit()
        self.mirror_exclude.setToolTip("名前にこの文字を含むシェイプ・ボーンは反転しない（カンマで区切る）")
        self.mirror_exclude.editingFinished.connect(self.on_mirror_changed)
        f.addRow("反転しないもの", self.mirror_exclude)
        self.mirror_axis = QtWidgets.QComboBox()
        for a in MIRROR_AXES:
            self.mirror_axis.addItem(a, a)
        self.mirror_axis.setToolTip("ボーンを左右反転する軸。Maya で顔の左右が X のときは X")
        self.mirror_axis.activated.connect(lambda *_: self.on_mirror_changed())
        f.addRow("ボーンの反転軸", self.mirror_axis)

    def _build_autogen(self) -> None:
        f = self._group("自動生成とベイク")
        self.fill_mode = QtWidgets.QComboBox()
        for m in FILL_MODES:
            self.fill_mode.addItem(FILL_LABELS.get(m, m), m)
        self.fill_mode.setToolTip("キーを打っていない点のポーズを、キーからどう作るか")
        self.fill_mode.activated.connect(lambda *_: self.on_autogen_changed())
        f.addRow("作り方", self.fill_mode)
        self.idw_power = _spin(0.1, 10.0, 0.5, 2)
        self.idw_power.setToolTip("大きいほど近いキーの影響が強くなる（IDW のときだけ）")
        self.idw_power.valueChanged.connect(lambda *_: self.on_autogen_changed())
        f.addRow("べき乗（IDW）", self.idw_power)
        self.threshold = _spin(0.0, 10.0, 0.001, 4, " cm")
        self.threshold.setToolTip("ベイクのとき、これより小さい頂点の動きは捨てる")
        self.threshold.valueChanged.connect(lambda *_: self.on_bake_changed())
        f.addRow("ベイクのしきい値", self.threshold)

    def _build_profile(self) -> None:
        f = self._group("命名規則プロファイル")
        row = QtWidgets.QHBoxLayout()
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.setToolTip("シェイプ名の決まり（左右の名前・可動域・標準のシェイプ）")
        row.addWidget(self.profile_combo, 1)
        self.profile_overwrite = QtWidgets.QCheckBox("上書き")
        self.profile_overwrite.setToolTip("オン: このキャラクターで変えた可動域もプロファイルの値で置き換える")
        row.addWidget(self.profile_overwrite)
        self.profile_apply = QtWidgets.QPushButton("適用")
        self.profile_apply.clicked.connect(lambda *_: self.on_profile_apply())
        row.addWidget(self.profile_apply)
        f.addRow("プロファイル", row)
        self.profile_missing = QtWidgets.QLabel()
        self.profile_missing.setWordWrap(True)
        self.profile_missing.setStyleSheet(DIM_STYLE)
        f.addRow(self.profile_missing)

    def _build_t20(self) -> None:
        box = QtWidgets.QGroupBox("Toon のカメラ角度補正（T-20）")
        v = QtWidgets.QVBoxLayout(box)
        hint = QtWidgets.QLabel(
            "Toon のキャラクタータブで作った補正シェイプ（正面 / 3/4 / 横）を、Neutral レイヤーの Pitch 0・Yaw 0 / 45 / 90° の列のキーにします。"
            "T-20 のデータと Look は変えません。左側（Yaw −）は取り込んだあとに「自動生成」で埋めてください。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(DIM_STYLE)
        v.addWidget(hint)
        self.t20_btn = QtWidgets.QPushButton("カメラ角度補正（T-20）から取り込む…")
        self.t20_btn.setToolTip("開いている Toon の Look から読みます。開いていなければ look.json を選びます")
        self.t20_btn.clicked.connect(lambda *_: self.on_t20_import())
        v.addWidget(self.t20_btn)
        self.form.addWidget(box)

    def _build_working_set(self) -> None:
        box = QtWidgets.QGroupBox("作業セット（ポーズタブに出すシェイプとボーン）")
        v = QtWidgets.QVBoxLayout(box)
        hint = QtWidgets.QLabel("チェックを入れたものだけがポーズタブに並びます。")
        hint.setStyleSheet(DIM_STYLE)
        v.addWidget(hint)
        self.ws_note = QtWidgets.QLabel()
        self.ws_note.setStyleSheet(DIM_STYLE)
        self.ws_note.setWordWrap(True)
        v.addWidget(self.ws_note)
        cols = QtWidgets.QHBoxLayout()
        self.curve_list, self.curve_filter = self._ws_column(cols, "シェイプ", "curve")
        self.bone_list, self.bone_filter = self._ws_column(cols, "ボーン", "bone")
        v.addLayout(cols)
        self.add_selected_joints = QtWidgets.QPushButton("選択中のジョイントを追加")
        self.add_selected_joints.setToolTip("Maya で選んでいるジョイントを作業セットのボーンに加える")
        self.add_selected_joints.clicked.connect(lambda *_: self.on_add_selected_joints())
        v.addWidget(self.add_selected_joints)
        self.form.addWidget(box)

    def _ws_column(self, parent: QtWidgets.QHBoxLayout, title: str, kind: str):
        col = QtWidgets.QVBoxLayout()
        col.addWidget(QtWidgets.QLabel(title))
        flt = QtWidgets.QLineEdit()
        flt.setPlaceholderText("絞り込み")
        flt.setClearButtonEnabled(True)
        col.addWidget(flt)
        lst = QtWidgets.QListWidget()
        lst.setMinimumHeight(160)
        lst.itemChanged.connect(lambda item, k=kind: self.on_ws_item_changed(k, item))
        lst.currentItemChanged.connect(lambda cur, _prev=None, k=kind: self.on_ws_current_changed(k, cur))
        flt.textChanged.connect(lambda text, k=kind: self._apply_filter(k))
        col.addWidget(lst, 1)
        row = QtWidgets.QHBoxLayout()
        on = QtWidgets.QPushButton("表示中を全部オン")
        off = QtWidgets.QPushButton("全部オフ")
        on.clicked.connect(lambda *_, k=kind: self.on_ws_bulk(k, True))
        off.clicked.connect(lambda *_, k=kind: self.on_ws_bulk(k, False))
        row.addWidget(on)
        row.addWidget(off)
        col.addLayout(row)
        parent.addLayout(col, 1)
        return lst, flt

    # ============================================================ 表示
    def show_status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(WARN_STYLE if error else DIM_STYLE)

    def refresh(self) -> None:
        s = self.session
        doc = s.doc
        self._updating = True
        try:
            if doc is None:
                return
            self._refresh_target(doc)
            self.base_bone.setText(doc.grid.base_bone)
            self._set_combo_data(self.forward, doc.grid.forward_axis)
            for w, v in zip(self.center, doc.grid.center_offset):
                w.setValue(v)
            g = doc.grid
            self.cols.setValue(g.cols)
            self.rows.setValue(g.rows)
            self.yaw.setValue(g.yaw_range)
            self.pitch.setValue(g.pitch_range)
            self.edge_fade.setValue(g.edge_fade)
            self.mirror_on.setChecked(doc.mirror.enabled)
            self.suffix_l.setText(doc.mirror.suffix_l)
            self.suffix_r.setText(doc.mirror.suffix_r)
            self.mirror_exclude.setText(", ".join(doc.mirror.exclude))
            self._set_combo_data(self.mirror_axis, doc.mirror.bone_axis)
            self._set_combo_data(self.fill_mode, doc.autogen.mode)
            self.idw_power.setValue(doc.autogen.idw_power)
            self.idw_power.setEnabled(doc.autogen.mode == "IDW")
            self.threshold.setValue(doc.bake.delta_threshold if doc.bake is not None else 0.001)
            self._refresh_profile(doc)
            self._refresh_working_set(doc)
        finally:
            self._updating = False

    @staticmethod
    def _set_combo_data(combo: QtWidgets.QComboBox, value) -> None:
        i = combo.findData(value)
        if i < 0:
            combo.addItem(str(value), value)
            i = combo.count() - 1
        combo.setCurrentIndex(i)

    def _refresh_target(self, doc) -> None:
        stored = doc.target.mesh if doc.target else ""
        extras = list(doc.target.extra_meshes) if doc.target else []
        choices = mesh_choices()
        self.mesh.clear()
        current_long = ""
        if stored:
            try:
                current_long = scene_mod.resolve_mesh(stored)
            except ValueError:
                current_long = ""
        for m in choices:
            self.mesh.addItem(scene_mod.short_name(m), m)
            self.mesh.setItemData(self.mesh.count() - 1, m, QtCore.Qt.ToolTipRole)
        if stored:
            if current_long and current_long in choices:
                self.mesh.setCurrentIndex(choices.index(current_long))
            elif current_long:  # blendShape が無い / 非表示でも、今の設定は見せる
                self.mesh.addItem(scene_mod.short_name(current_long), current_long)
                self.mesh.setCurrentIndex(self.mesh.count() - 1)
            else:
                self.mesh.addItem(f"{stored} {MESH_MISSING}", None)
                self.mesh.setCurrentIndex(self.mesh.count() - 1)
        else:
            self.mesh.insertItem(0, "（未設定）", None)
            self.mesh.setCurrentIndex(0)
        self.extra_list.clear()
        for name in extras:
            it = QtWidgets.QListWidgetItem(name)
            try:
                it.setToolTip(scene_mod.resolve_mesh(name))
            except ValueError:
                it.setText(f"{name} {MESH_MISSING}")
                it.setForeground(QtCore.Qt.gray)
            it.setData(QtCore.Qt.UserRole, name)
            self.extra_list.addItem(it)

    def _refresh_profile(self, doc) -> None:
        names = profile_names(doc.asset or "")
        if doc.profile and doc.profile not in names:
            names.append(doc.profile)
        self.profile_combo.clear()
        self.profile_combo.addItem(NO_PROFILE, None)
        for n in names:
            self.profile_combo.addItem(n, n)
        i = self.profile_combo.findData(doc.profile) if doc.profile else 0
        self.profile_combo.setCurrentIndex(max(i, 0))
        prof = self.session.profile
        sc = self.session.scene
        if prof is None:
            self.profile_missing.setText("プロファイルを選ぶと、左右の名前と可動域が入ります")
            self.profile_missing.setToolTip("")
        elif sc is None or sc.curves is None:
            self.profile_missing.setText("標準シェイプの不足: 調べられません（対象のメッシュが見つかりません）")
        else:
            missing = profile_mod.missing_standard_curves(prof, sc.curves)
            if missing:
                self.profile_missing.setText(f"標準シェイプの不足: {len(missing)} 個（検証タブに一覧が出ます）")
                self.profile_missing.setToolTip("\n".join(missing[:60]))
            else:
                self.profile_missing.setText("標準シェイプはそろっています")
                self.profile_missing.setToolTip("")

    # ---- 作業セット
    def _refresh_working_set(self, doc) -> None:
        sc = self.session.scene
        ws = doc.working_set
        scene_curves = list(sc.curves) if sc is not None and sc.curves is not None else []
        scene_bones = list(sc.bones) if sc is not None and sc.bones is not None else []
        self.ws_note.setText("" if sc is not None and sc.curves is not None else "対象のメッシュが見つからないため、一覧は空です")
        for kind, lst, avail, chosen in (
            ("curve", self.curve_list, scene_curves, ws.curves),
            ("bone", self.bone_list, scene_bones, ws.bones),
        ):
            names = list(avail) + [n for n in chosen if n not in avail]  # モデルに無い名前も（外せるように）見せる
            sig = (tuple(names), tuple(avail))
            if self._list_sig.get(kind) != sig:
                self._list_sig[kind] = sig
                lst.clear()
                for n in names:
                    it = QtWidgets.QListWidgetItem(n)
                    it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                    it.setData(QtCore.Qt.UserRole, n)
                    it.setCheckState(QtCore.Qt.Unchecked)  # 明示しないとチェックボックスが描かれない
                    if n not in avail:
                        it.setForeground(QtCore.Qt.gray)
                        it.setToolTip("モデルにありません（検証タブで直せます）")
                    else:
                        it.setToolTip(CURVE_TIP if kind == "curve" else BONE_TIP)
                    lst.addItem(it)
            on = set(chosen)
            for i in range(lst.count()):
                it = lst.item(i)
                want = QtCore.Qt.Checked if it.data(QtCore.Qt.UserRole) in on else QtCore.Qt.Unchecked
                if it.checkState() != want:
                    it.setCheckState(want)
            self._apply_filter(kind)

    def _list_of(self, kind: str):
        return self.curve_list if kind == "curve" else self.bone_list

    def _filter_of(self, kind: str):
        return self.curve_filter if kind == "curve" else self.bone_filter

    def _apply_filter(self, kind: str) -> None:
        text = self._filter_of(kind).text().strip().lower()
        lst = self._list_of(kind)
        for i in range(lst.count()):
            it = lst.item(i)
            it.setHidden(bool(text) and text not in it.text().lower())

    # ============================================================ 操作
    def _run(self, fn, *args, **kwargs):
        """セッションのコマンドを呼ぶ。失敗（ok=False・例外）は状態欄に出して表示を Document に戻す。成功なら結果を返す。"""
        try:
            res = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001  メッセージはそのまま見せる
            self.show_status(str(exc), error=True)
            lifecycle.report_error("セットアップの操作でエラー", traceback.format_exc())
            self.refresh()
            return None
        if getattr(res, "ok", True) is False:
            self.show_status(res.message or "できませんでした", error=True)
            self.refresh()
            return None
        self.show_status(getattr(res, "message", "") or "")
        return res if res is not None else True

    # ---- 対象メッシュ
    def on_mesh_activated(self, index: int) -> None:
        if self._updating:
            return
        name = self.mesh.itemData(index)
        if name:
            self._run(self.session.set_target, mesh=name)
        else:
            self.refresh()

    def on_mesh_from_selection(self) -> None:
        sel = _selected_mesh_transforms()
        if not sel:
            self.show_status("メッシュを選んでから押してください", error=True)
            return
        self._run(self.session.set_target, mesh=sel[0])

    def on_extra_add(self) -> None:
        doc = self.session.doc
        if doc is None or doc.target is None:
            return
        have = []
        for n in [doc.target.mesh, *doc.target.extra_meshes]:
            try:
                have.append(scene_mod.resolve_mesh(n))
            except ValueError:
                pass
        new = [m for m in _selected_mesh_transforms() if m not in have]
        if not new:
            self.show_status("追加できるメッシュが選ばれていません（顔のメッシュ・追加済みのものは除きます）", error=True)
            return
        self._run(self.session.set_target, extra_meshes=[*doc.target.extra_meshes, *new])

    def on_extra_remove(self) -> None:
        doc = self.session.doc
        if doc is None or doc.target is None:
            return
        gone = {it.data(QtCore.Qt.UserRole) for it in self.extra_list.selectedItems()}
        if not gone:
            self.show_status("外すメッシュを一覧から選んでください", error=True)
            return
        self._run(self.session.set_target, extra_meshes=[n for n in doc.target.extra_meshes if n not in gone])

    # ---- 基準ボーン・向き
    def on_base_bone_edited(self) -> None:
        if self._updating:
            return
        doc = self.session.doc
        text = self.base_bone.text().strip()
        if doc is None or text == doc.grid.base_bone:
            return
        self._run(self.session.set_base_bone, text)

    def on_detect_bone(self) -> None:
        try:
            found = self.session.detect_base_bone()
        except Exception as exc:  # noqa: BLE001
            self.show_status(str(exc), error=True)
            return
        if not found:
            self.show_status("基準ボーンを見つけられませんでした。「選択から」か、名前を入力してください", error=True)
            return
        if self._run(self.session.set_base_bone, found):
            self.show_status(f"基準ボーンを「{found}」にしました")

    def on_bone_from_selection(self) -> None:
        joints = _selected_joints()
        if not joints:
            self.show_status("ジョイントを選んでから押してください", error=True)
            return
        self._run(self.session.set_base_bone, joints[0])

    def on_forward_activated(self, _index: int = 0) -> None:
        if self._updating:
            return
        self._run(self.session.set_forward_axis, self.forward.currentData())

    def on_center_changed(self) -> None:
        if self._updating:
            return
        self._run(self.session.set_center_offset, [w.value() for w in self.center])

    # ---- 格子
    def ask_drop_keys(self, text: str, count: int) -> bool:
        """新しい格子に重ならず捨てられるキーがあるとき、続けてよいか聞く（テストが差し替える）。"""
        return ask_yes_no(self, f"新しい格子に重ならないキーが {count} 個あり、捨てられます。\n{text}\n\n適用しますか？")

    def on_grid_apply(self) -> None:
        doc = self.session.doc
        if doc is None:
            return
        c, r, y, p = self.cols.value(), self.rows.value(), self.yaw.value(), self.pitch.value()
        g = doc.grid
        if (c, r) == (g.cols, g.rows) and abs(y - g.yaw_range) < 1e-9 and abs(p - g.pitch_range) < 1e-9:
            self.show_status("格子は変わりません")
            return
        try:
            trial = self.session.preview_resize(c, r, y, p)
        except Exception as exc:  # noqa: BLE001
            self.show_status(str(exc), error=True)
            return
        if not trial.ok:
            self.show_status(trial.message, error=True)
            return
        if trial.dropped_keys:
            lines = [f"{d.layer}  R{d.row} C{d.col}（Yaw {d.yaw:.0f}° / Pitch {d.pitch:.0f}°）" for d in trial.dropped_keys[:12]]
            more = len(trial.dropped_keys) - len(lines)
            text = "\n".join(lines) + (f"\n…ほか {more} 個" if more > 0 else "")
            self.grid_note.setText(f"捨てられるキー {len(trial.dropped_keys)} 個:\n{text}")
            self.grid_note.setVisible(True)
            if not self.ask_drop_keys(text, len(trial.dropped_keys)):
                self.show_status("格子の変更を取りやめました")
                return
        else:
            self.grid_note.setVisible(False)
        res = self._run(self.session.set_grid, c, r, y, p)
        if res is not None and res is not True:
            self.show_status(res.message + "（ベイク済みのシェイプで要らなくなったものは消しました）" if res.stale_morphs else res.message)

    def on_edge_fade_changed(self) -> None:
        if self._updating:
            return
        self._run(self.session.set_edge_fade, self.edge_fade.value())

    # ---- ミラー・自動生成・ベイク
    def on_mirror_changed(self) -> None:
        if self._updating:
            return
        exclude = [t.strip() for t in self.mirror_exclude.text().replace("、", ",").split(",") if t.strip()]
        self._run(
            self.session.set_mirror,
            enabled=self.mirror_on.isChecked(),
            suffix_l=self.suffix_l.text(),
            suffix_r=self.suffix_r.text(),
            exclude=exclude,
            bone_axis=self.mirror_axis.currentData(),
        )

    def on_autogen_changed(self) -> None:
        if self._updating:
            return
        self._run(self.session.set_autogen, mode=self.fill_mode.currentData(), idw_power=self.idw_power.value())

    def on_bake_changed(self) -> None:
        if self._updating:
            return
        self._run(self.session.set_bake_options, delta_threshold=self.threshold.value())

    # ---- プロファイル
    def on_profile_apply(self) -> None:
        name = self.profile_combo.currentData()
        if not name:
            self.show_status("プロファイルを選んでください", error=True)
            return
        self._run(self.session.apply_profile, name, self.profile_overwrite.isChecked())

    # ---- T-20 の取り込み
    def ask_look_path(self) -> str:
        """Toon の Look が開かれていないとき、look.json を聞く（テストが差し替える。キャンセルは ""）。"""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Toon の Look（look.json）を選ぶ", str(project.root() / "looks"), "Look (*.json)"
        )
        return path

    def ask_overwrite_keys(self, text: str) -> bool:
        return ask_yes_no(self, "既にキーがある点があります:\n" + text + "\n\nT-20 のポーズで上書きしますか？")

    def on_t20_import(self) -> None:
        if self.session.doc is None:
            return
        source = None  # None = 今開いている Toon の Look
        try:
            try:
                res = self.session.import_t20(None)
            except NoLookError:
                source = self.ask_look_path()
                if not source:
                    self.show_status("取り込みを取りやめました")
                    return
                res = self.session.import_t20(source)
            if res.existing and self.ask_overwrite_keys("、".join(res.existing)):
                res = self.session.import_t20(source, overwrite=True)
        except Exception as exc:  # noqa: BLE001  メッセージはそのまま見せる
            self.show_status(str(exc), error=True)
            return
        self.show_status(res.message, error=not res.ok)

    # ---- 作業セット
    def on_ws_item_changed(self, kind: str, item: QtWidgets.QListWidgetItem) -> None:
        if self._updating:
            return
        name = item.data(QtCore.Qt.UserRole)
        kw = {"curves": [name]} if kind == "curve" else {"bones": [name]}
        if item.checkState() == QtCore.Qt.Checked:
            self._run(self.session.add_to_working_set, **kw)
        else:
            self._run(self.session.remove_from_working_set, **kw)

    def on_ws_bulk(self, kind: str, on: bool) -> None:
        lst = self._list_of(kind)
        names = [lst.item(i).data(QtCore.Qt.UserRole) for i in range(lst.count()) if not lst.item(i).isHidden()]
        if not names:
            return
        kw = {"curves": names} if kind == "curve" else {"bones": names}
        self._run(self.session.add_to_working_set if on else self.session.remove_from_working_set, **kw)

    def on_ws_current_changed(self, kind: str, item: Optional[QtWidgets.QListWidgetItem]) -> None:
        """ボーンを選ぶと、そのジョイントを Maya で選ぶ（いちばん安全な強調）。シェイプはまだ強調しない（TODO）。"""
        if self._updating or item is None or kind != "bone":
            return
        name = item.data(QtCore.Qt.UserRole)
        try:
            joint = scene_mod.find_joint(name, scene_mod.mesh_joints(self.session.target_meshes()))
        except Exception:  # noqa: BLE001  対象メッシュが無いときなど
            joint = None
        if joint:
            try:
                cmds.select(joint, replace=True)
            except RuntimeError:
                pass

    def on_add_selected_joints(self) -> None:
        joints = _selected_joints()
        if not joints:
            self.show_status("ジョイントを選んでから押してください", error=True)
            return
        self._run(self.session.add_to_working_set, bones=joints)

    def detach(self) -> None:
        pass
