"""シェイプタブ（docs/14 §5.6）: Maya ならではのシェイプ作成支援。

上の「対象のシェイプ」一覧で選んだシェイプに、下の道具（彫る / ポーズをシェイプにする / 左右に分ける・ミラー / 中間・誇張・組み合わせ /
別メッシュへ写す / 整理）を使う。どの道具も `session` のコマンドを呼ぶだけ（シーンを変える操作は Maya の Undo 1 回）。
確認のダイアログは `confirm` / `show_error` / `ask_continue_asymmetric` に分けてあり、テストでは差し替える。
"""

from __future__ import annotations

import traceback
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from tdrive import lifecycle

from . import scene, shapes
from .core import space
from .session import FacialSessionError
from .ui import DIM_STYLE, WARN_STYLE, ask_yes_no, warn

ERRORS = (shapes.ShapeError, FacialSessionError, ValueError, RuntimeError)
TAG_FILTERS = (("すべて", None), ("元から", shapes.TAG_ORIGINAL), ("FC_", shapes.TAG_FC), ("fcs_", shapes.TAG_SCULPT), ("組み合わせ", shapes.TAG_COMBO), ("作ったもの", shapes.TAG_MADE))


def _hint(text: str) -> QtWidgets.QLabel:
    w = QtWidgets.QLabel(text)
    w.setWordWrap(True)
    w.setStyleSheet(DIM_STYLE)
    return w


def _btn(text: str, tip: str, slot) -> QtWidgets.QPushButton:
    b = QtWidgets.QPushButton(text)
    b.setToolTip(tip)
    b.clicked.connect(lambda *_: slot())
    return b


def _spin(lo: float, hi: float, val: float, step: float, dec: int, suffix: str = "") -> QtWidgets.QDoubleSpinBox:
    s = QtWidgets.QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setSingleStep(step)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    return s


class ShapesTab(QtWidgets.QWidget):
    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self._updating = False
        self._infos: list[shapes.ShapeInfo] = []
        self._build()
        session.state_listeners.append(self.on_state)
        self.refresh()

    # ============================================================ 組み立て
    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.no_doc = _hint("新規 または 開く でデータを作ると使えます")
        outer.addWidget(self.no_doc)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        outer.addWidget(scroll, 1)
        body = QtWidgets.QWidget()
        scroll.setWidget(body)
        self.body = body
        v = QtWidgets.QVBoxLayout(body)

        # --- 対象のシェイプ
        g = QtWidgets.QGroupBox("対象のシェイプ")
        gv = QtWidgets.QVBoxLayout(g)
        gv.addWidget(_hint("顔メッシュの blendShape のシェイプ。道具はここで選んだものに使います（複数選択可）。タグ: 元から / FC_（ベイクの結果）/ fcs_（彫り用）/ 組み合わせ（2 本の積で動く補正）/ 作ったもの"))
        row = QtWidgets.QHBoxLayout()
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("名前で絞り込み")
        self.filter_edit.textChanged.connect(lambda *_: self._fill_list())
        self.tag_combo = QtWidgets.QComboBox()
        for label, _tag in TAG_FILTERS:
            self.tag_combo.addItem(label)
        self.tag_combo.currentIndexChanged.connect(lambda *_: self._fill_list())
        row.addWidget(self.filter_edit, 1)
        row.addWidget(self.tag_combo)
        gv.addLayout(row)
        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list.setMinimumHeight(130)
        self.list.itemSelectionChanged.connect(lambda *_: self._on_selection())
        gv.addWidget(self.list)
        v.addWidget(g)

        # --- 彫る
        g = QtWidgets.QGroupBox("彫る（この角度で彫る）")
        gv = QtWidgets.QVBoxLayout(g)
        gv.addWidget(_hint("グリッドで選んだ点のポーズを当てた状態で、その点専用のシェイプ fcs_… を Maya のスカルプト（移動）ツールで彫ります。終わると点のポーズに記録され、ベイクに含まれます（Unity へは出しません）。頭を動かしたポーズでは使えません"))
        self.sculpt_label = QtWidgets.QLabel()
        self.sculpt_label.setWordWrap(True)
        gv.addWidget(self.sculpt_label)
        row = QtWidgets.QHBoxLayout()
        self.sculpt_btn = _btn("この角度で彫る", "選択中の点の fcs_ シェイプを作って（あれば開いて）スカルプト対象にします", self.on_sculpt_begin)
        self.sculpt_end_btn = _btn("彫り終わる", "スカルプトを終えて、点のポーズに「fcs_… = 1」を記録して保存します", self.on_sculpt_end)
        self.sculpt_del_btn = _btn("彫りを消す", "選んだ fcs_ シェイプを消し、ポーズからの参照も外します（Maya の Undo で戻せます）", self.on_sculpt_delete)
        for b in (self.sculpt_btn, self.sculpt_end_btn, self.sculpt_del_btn):
            row.addWidget(b)
        row.addStretch(1)
        gv.addLayout(row)
        self.sculpt_list = QtWidgets.QListWidget()
        self.sculpt_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.sculpt_list.setMaximumHeight(90)
        self.sculpt_list.setToolTip("fcs_ シェイプと、それがどの点のものか。どの点からも使われていないもの（孤立）はオレンジ色で「孤立」と付きます")
        gv.addWidget(self.sculpt_list)
        v.addWidget(g)

        # --- ポーズをシェイプにする
        g = QtWidgets.QGroupBox("ポーズをシェイプにする")
        gv = QtWidgets.QVBoxLayout(g)
        gv.addWidget(_hint("今のポーズ（選択中の点の編集中の値。スライダー + ボーン）を、新しいシェイプ 1 本にします。よく使う組み合わせを 1 本のスライダーに。元のシェイプは上書きしません"))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("名前"))
        self.pose_name = QtWidgets.QLineEdit()
        row.addWidget(self.pose_name, 1)
        gv.addLayout(row)
        self.pose_ws = QtWidgets.QCheckBox("作業セットに入れる")
        self.pose_ws.setChecked(True)
        self.pose_scene = QtWidgets.QCheckBox("シーンの今の状態を取り込んでから作る（編集状態のとき）")
        gv.addWidget(self.pose_ws)
        gv.addWidget(self.pose_scene)
        self.pose_btn = _btn("ポーズをシェイプにする", "名前のシェイプを作ります", self.on_pose_to_shape)
        gv.addWidget(self.pose_btn)
        v.addWidget(g)

        # --- 左右に分ける・ミラー
        g = QtWidgets.QGroupBox("左右に分ける・ミラー")
        gv = QtWidgets.QVBoxLayout(g)
        self.split_hint = _hint("")
        gv.addWidget(self.split_hint)
        self._sync_split_hint(None)
        form = QtWidgets.QFormLayout()
        self.name_l = QtWidgets.QLineEdit()
        self.name_r = QtWidgets.QLineEdit()
        self.split_width = _spin(0.0, 100.0, 1.0, 0.5, 2, " cm")
        self.split_width.setToolTip("中央のぼかし幅。0 で段差")
        self.mirror_tol = _spin(0.001, 5.0, 0.05, 0.01, 3, " cm")
        self.mirror_tol.setToolTip("鏡の位置にあるとみなす許容誤差")
        form.addRow("左の名前", self.name_l)
        form.addRow("右の名前", self.name_r)
        form.addRow("中央のぼかし幅", self.split_width)
        form.addRow("ミラーの許容誤差", self.mirror_tol)
        gv.addLayout(form)
        row = QtWidgets.QHBoxLayout()
        self.split_btn = _btn("左右に分ける", "選んだ 1 本を左・右の名前の 2 本に分けます（元は残ります）", self.on_split)
        self.mirror_btn = _btn("ミラー（反対側を作る / 更新）", "選んだ _L（または _R）を鏡映して反対側を作ります", self.on_mirror)
        row.addWidget(self.split_btn)
        row.addWidget(self.mirror_btn)
        row.addStretch(1)
        gv.addLayout(row)
        v.addWidget(g)

        # --- 中間・誇張・組み合わせ
        g = QtWidgets.QGroupBox("中間・誇張・組み合わせ")
        gv = QtWidgets.QVBoxLayout(g)
        gv.addWidget(_hint("中間: 今のシーンの形を、選んだシェイプの重み◯のときの形として足します（元のシェイプは確認が必要）。誇張: 選んだシェイプを 1 にした形に足す分を <名前>_Ex にして、可動域を 0〜2 に開きます。ポーズでシェイプの重みを 1 より大きくすると、1 を超えた分がベイクのとき別の誇張用のシェイプ（FC_…_Ex）に分けて焼かれ、プレビューの「誇張」や Unity で強さを調整できます。組み合わせ: 2 本を選んで fcs_combo_… を作ります。2 本を同時に上げたときだけ効く補正です。Maya の中で計算され、ベイクした結果には含まれます（補正そのものは Unity へ出しません）"))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("重み"))
        self.inb_weight = _spin(0.01, 0.99, 0.5, 0.05, 2)
        row.addWidget(self.inb_weight)
        self.inb_btn = _btn("中間形を足す", "今のシーンの形を中間形として追加します", self.on_inbetween)
        self.ex_btn = _btn("誇張形 _Ex を作る", "<名前>_Ex を作り、可動域を 0〜2 にします。ポーズでこのシェイプを 1 より大きくした分は、ベイクのとき誇張用のシェイプ（FC_…_Ex）に分けて焼かれます", self.on_exaggerate)
        row.addWidget(self.inb_btn)
        row.addWidget(self.ex_btn)
        row.addStretch(1)
        gv.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.combo_btn = _btn("組み合わせ補正を作る（2 本選択）", "2 本を同時に上げたときだけ効く補正です。Maya の中で計算され、ベイクした結果には含まれます（補正そのものは Unity へ出しません）", self.on_combo)
        self.combo_sculpt_btn = _btn("組み合わせ補正を彫る", "選んだ組み合わせ補正を、駆動元を 1 にした状態で彫ります（終わりは「彫る」の箱の「彫り終わる」）", self.on_combo_sculpt)
        row.addWidget(self.combo_btn)
        row.addWidget(self.combo_sculpt_btn)
        row.addStretch(1)
        gv.addLayout(row)
        v.addWidget(g)

        # --- 別メッシュへ写す
        g = QtWidgets.QGroupBox("別メッシュへ写す")
        gv = QtWidgets.QVBoxLayout(g)
        gv.addWidget(_hint("選んだシェイプを、まつ毛・眉・別の頭など別メッシュへ写します。頂点が同じなら複写、違えば近接（proximityWrap）で転写します。写し先の元からあるシェイプは確認なしでは上書きしません"))
        row = QtWidgets.QHBoxLayout()
        self.dest_combo = QtWidgets.QComboBox()
        self.dest_combo.setMinimumWidth(160)
        row.addWidget(self.dest_combo, 1)
        self.transfer_btn = _btn("写す", "選んだシェイプを写し先へ写します", self.on_transfer)
        row.addWidget(self.transfer_btn)
        gv.addLayout(row)
        v.addWidget(g)

        # --- 整理
        g = QtWidgets.QGroupBox("整理")
        gv = QtWidgets.QVBoxLayout(g)
        gv.addWidget(_hint("微小な差分の掃除（元からのシェイプは確認が必要）、空・未使用・孤立の一覧、プロファイルの標準シェイプの不足。空 = 差分が無い / 未使用 = どのポーズ・作業セットからも使われていない / 孤立 = 対応する点が無い FC_ / fcs_。一覧は情報だけで、消せるのは孤立した fcs_ / FC_ だけです"))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("しきい値"))
        self.clean_thr = _spin(0.00001, 1.0, 0.001, 0.0005, 5, " cm")
        row.addWidget(self.clean_thr)
        self.clean_btn = _btn("微小な差分を掃除", "選んだシェイプの、しきい値未満の差分を消します", self.on_clean)
        row.addWidget(self.clean_btn)
        row.addStretch(1)
        gv.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.audit_btn = _btn("空・未使用・孤立を調べる", "空（差分が無い）・未使用（どのポーズ・作業セットからも使われていない）・孤立（対応する点が無い FC_ / fcs_）を一覧にします", self.on_audit)
        self.audit_del_btn = _btn("孤立した fcs_ / FC_ を消す", "孤立した fcs_（どの点のポーズからも使われていない）と、格子に対応する点が無い FC_ を消します", self.on_audit_delete)
        self.validate_btn = _btn("検証タブへ（改名の候補）", "プロファイルに沿った一括改名は検証タブで行います", self.on_goto_validate)
        row.addWidget(self.audit_btn)
        row.addWidget(self.audit_del_btn)
        row.addWidget(self.validate_btn)
        row.addStretch(1)
        gv.addLayout(row)
        self.audit_text = QtWidgets.QPlainTextEdit()
        self.audit_text.setReadOnly(True)
        self.audit_text.setMaximumHeight(110)
        gv.addWidget(self.audit_text)
        v.addWidget(g)
        v.addStretch(1)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        outer.addWidget(self.status)

    # ============================================================ ダイアログ（テストで差し替える）
    def confirm(self, text: str) -> bool:
        return ask_yes_no(self, text)

    def show_error(self, text: str) -> None:
        warn(self, text)

    def ask_continue_asymmetric(self, count: int) -> bool:
        return ask_yes_no(self, f"左右の対応が取れない頂点が {count} 個あります（ビューポートで選択しました）。\nそれでも続けますか（その頂点は 0 のままになります）")

    # ============================================================ 表示
    def set_status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(WARN_STYLE if error else DIM_STYLE)

    def _report(self, res: shapes.ShapeResult) -> None:
        lines = [res.summary(), *res.notes, *[f"注意: {w}" for w in res.warnings]]
        self.set_status("\n".join(lines))

    def on_state(self) -> None:
        try:
            self._update_buttons()
        except RuntimeError:
            if self.on_state in self.session.state_listeners:
                self.session.state_listeners.remove(self.on_state)

    def rebind(self) -> None:
        self.refresh()

    def selected_names(self) -> list[str]:
        return [i.data(QtCore.Qt.UserRole) for i in self.list.selectedItems()]

    def _sync_split_hint(self, doc) -> None:
        """左右に分ける・ミラーの案内。顔の左右の軸は文書の「ボーンの反転軸」（セットアップタブ。既定 X）で、+の側が L。"""
        ax = space.mirror_axis_text(doc.mirror.bone_axis if doc is not None else "X")
        self.split_hint.setText(
            f"左右一体のシェイプを _L / _R に分けます（顔の左右はメッシュの {ax} 軸。+{ax} が L。足すと元の形。軸はセットアップタブの「ボーンの反転軸」）。"
            "ミラーは _L から _R（または逆）を作る / 更新します。対応が取れない頂点があると選択して止まります"
        )

    def refresh(self) -> None:
        s = self.session
        has = s.presenters is not None
        self._sync_split_hint(s.doc)
        self.no_doc.setVisible(not has)
        self.body.setEnabled(has)
        if not has:
            self._infos = []
            self.list.clear()
            self.sculpt_list.clear()
            return
        try:
            self._infos = s.shape_list()
        except ERRORS:
            self._infos = []
        self._fill_list()
        self._fill_sculpt_list()
        self._fill_dest()
        self._update_buttons()

    def _fill_list(self) -> None:
        keep = set(self.selected_names())
        text = self.filter_edit.text().strip().lower()
        tag = TAG_FILTERS[max(self.tag_combo.currentIndex(), 0)][1]
        self._updating = True
        try:
            self.list.clear()
            for info in self._infos:
                if tag is not None and info.tag != tag:
                    continue
                if text and text not in info.name.lower():
                    continue
                extra = []
                if info.empty:
                    extra.append("空")
                if info.inbetweens:
                    extra.append("中間 " + ",".join(f"{(i - 5000) / 1000:g}" for i in info.inbetweens))
                if info.driven:
                    extra.append("駆動")
                item = QtWidgets.QListWidgetItem(f"{info.name}    [{info.tag_label}]  {info.vertex_count} 頂点" + ("  " + " ".join(extra) if extra else ""))
                item.setData(QtCore.Qt.UserRole, info.name)
                self.list.addItem(item)
                if info.name in keep:
                    item.setSelected(True)
        finally:
            self._updating = False

    def _fill_sculpt_list(self) -> None:
        self.sculpt_list.clear()
        try:
            orphans = set(self.session.shape_audit().orphan_sculpt)
        except ERRORS:
            orphans = set()
        doc = self.session.doc
        for info in self._infos:
            if info.tag not in (shapes.TAG_SCULPT, shapes.TAG_COMBO):
                continue
            if info.tag == shapes.TAG_COMBO:
                where = "組み合わせ補正 " + " × ".join(self._combo_drivers(info.name))
            elif info.point is not None:
                where = f"点 {info.point[0]} R{info.point[1]} C{info.point[2]}"
            else:
                where = "点に対応しない名前"
            it = QtWidgets.QListWidgetItem(f"{info.name}    {where}" + ("    孤立" if info.name in orphans else ""))
            it.setData(QtCore.Qt.UserRole, info.name)
            if info.name in orphans:
                it.setForeground(QtGui.QColor("#ffb74d"))  # 孤立（ツールチップの「オレンジ色」と同じ色）
            self.sculpt_list.addItem(it)

    def _combo_drivers(self, name: str) -> list[str]:
        sc = self.session.shape_ctx(create=False)
        return shapes.combo_drivers(sc, name) if sc is not None else []

    def _fill_dest(self) -> None:
        cur = self.dest_combo.currentText()
        self.dest_combo.clear()
        try:
            face = self.session.shape_ctx(create=False)
            face_mesh = face.mesh if face is not None else ""
        except ERRORS:
            face_mesh = ""
        for m in scene.list_visible_meshes():
            if m != face_mesh:
                self.dest_combo.addItem(scene.short_name(m), m)
        i = self.dest_combo.findText(cur)
        if i >= 0:
            self.dest_combo.setCurrentIndex(i)

    def _update_buttons(self) -> None:
        s = self.session
        has = s.presenters is not None
        st = s.sculpting if has else None
        sel = self.selected_names() if has else []
        point = s.sculpt_target_name() if has else None
        if st is not None:
            self.sculpt_label.setText(f"彫り中: {st.name}（Maya のスカルプト / 移動ツールで形を作り、終わったら「彫り終わる」）")
            self.sculpt_label.setStyleSheet(WARN_STYLE)
        else:
            self.sculpt_label.setText(f"選択中の点の彫り用シェイプ: {point}" if point else "グリッドタブで点を選ぶと、その点の fcs_ シェイプを彫れます")
            self.sculpt_label.setStyleSheet(DIM_STYLE)
        self.sculpt_btn.setEnabled(has and st is None and point is not None)
        self.sculpt_end_btn.setEnabled(st is not None)
        self.sculpt_del_btn.setEnabled(has and bool(self._selected_sculpt()))
        self.combo_btn.setEnabled(has and len(sel) == 2)
        self.combo_sculpt_btn.setEnabled(has and st is None and any(self._is_combo(n) for n in sel))
        one = len(sel) == 1
        self.split_btn.setEnabled(has and one)
        self.mirror_btn.setEnabled(has and one)
        self.inb_btn.setEnabled(has and one)
        self.ex_btn.setEnabled(has and one)
        self.transfer_btn.setEnabled(has and bool(sel) and self.dest_combo.count() > 0)
        self.clean_btn.setEnabled(has and bool(sel))
        self.pose_btn.setEnabled(has)

    def _is_combo(self, name: str) -> bool:
        return any(i.name == name and i.tag == shapes.TAG_COMBO for i in self._infos)

    def _selected_sculpt(self) -> list[str]:
        names = [i.data(QtCore.Qt.UserRole) for i in self.sculpt_list.selectedItems()]
        if not names:
            names = [n for n in self.selected_names() if any(i.name == n and i.tag in (shapes.TAG_SCULPT, shapes.TAG_COMBO) for i in self._infos)]
        return names

    def _on_selection(self) -> None:
        if self._updating:
            return
        sel = self.selected_names()
        if len(sel) == 1:
            sc = self.session.shape_ctx(create=False)
            base = shapes.base_name(sel[0])
            if sc is not None:
                for suf in (sc.suffix_l, sc.suffix_r):
                    if suf and base.endswith(suf):
                        base = base[: -len(suf)]
                self.name_l.setText(base + sc.suffix_l)
                self.name_r.setText(base + sc.suffix_r)
        if not self.pose_name.text():
            self.pose_name.setText(self._suggest_pose_name())
        self._update_buttons()

    def _suggest_pose_name(self) -> str:
        existing = {i.name for i in self._infos}
        k = 1
        while f"pose_shape_{k:02d}" in existing:
            k += 1
        return f"pose_shape_{k:02d}"

    # ============================================================ 実行
    def _run(self, fn) -> Optional[shapes.ShapeResult]:
        try:
            res = fn()
        except shapes.AsymmetryStop:
            raise
        except ERRORS as e:
            self.set_status(str(e), error=True)
            self.show_error(str(e))
            self.refresh()
            return None
        except Exception as e:  # noqa: BLE001  道具の不具合で Maya を巻き込まない
            lifecycle.report_error("シェイプ道具でエラー", traceback.format_exc())
            self.set_status(f"エラー: {e}", error=True)
            self.refresh()
            return None
        self._report(res)
        self.refresh()
        return res

    def _one(self) -> Optional[str]:
        sel = self.selected_names()
        return sel[0] if len(sel) == 1 else None

    def on_sculpt_begin(self) -> None:
        self._run(self.session.sculpt_begin)

    def on_sculpt_end(self) -> None:
        self._run(self.session.sculpt_end)

    def on_sculpt_delete(self) -> None:
        names = self._selected_sculpt()
        if not names or not self.confirm(f"彫り用シェイプ {len(names)} 個を消します（{', '.join(names[:3])}…）。ポーズからの参照も外します。よろしいですか"):
            return
        self._run(lambda: self.session.sculpt_delete(names))

    def on_pose_to_shape(self) -> None:
        name = self.pose_name.text().strip()
        self._run(lambda: self.session.shape_from_pose(name, self.pose_ws.isChecked(), self.pose_scene.isChecked()))
        self.pose_name.setText(self._suggest_pose_name())

    def on_split(self) -> None:
        src = self._one()
        if src is None:
            return
        l, r = self.name_l.text().strip(), self.name_r.text().strip()
        over = False
        sc = self.session.shape_ctx(create=False)
        if sc is not None:
            clash = [n for n in (l, r) if n in {i.name for i in self._infos if i.tag == shapes.TAG_ORIGINAL}]
            if clash:
                if not self.confirm(f"{', '.join(clash)} は元からあるシェイプです。上書きしますか"):
                    return
                over = True
        self._run(lambda: self.session.shape_split_lr(src, l, r, self.split_width.value(), 0.0, over))

    def on_mirror(self) -> None:
        src = self._one()
        if src is None:
            return
        tol = self.mirror_tol.value()
        sc = self.session.shape_ctx(create=False)
        dst = None
        over = False
        if sc is not None:
            from .core import profile as profile_mod

            dst = profile_mod.mirror_name(src, sc.suffix_l, sc.suffix_r)
            if dst != src and dst in {i.name for i in self._infos if i.tag == shapes.TAG_ORIGINAL}:
                if not self.confirm(f"{dst} は元からあるシェイプです。鏡映で上書きしますか"):
                    return
                over = True
        try:
            res = self._run(lambda: self.session.shape_mirror(src, None, tol, False, over))
        except shapes.AsymmetryStop as stop:
            self.set_status(str(stop), error=True)
            if self.ask_continue_asymmetric(len(stop.unmatched)):
                self._run(lambda: self.session.shape_mirror(src, None, tol, True, over))
            return
        return

    def on_inbetween(self) -> None:
        name = self._one()
        if name is None:
            return
        original = any(i.name == name and i.tag == shapes.TAG_ORIGINAL for i in self._infos)
        if original and not self.confirm(f"{name} は元からあるシェイプです。中間形を足すと元の動きが変わります。足しますか"):
            return
        self._run(lambda: self.session.shape_inbetween(name, self.inb_weight.value(), original))

    def on_exaggerate(self) -> None:
        name = self._one()
        if name is not None:
            self._run(lambda: self.session.shape_exaggerate(name))

    def on_combo(self) -> None:
        sel = self.selected_names()
        if len(sel) == 2:
            self._run(lambda: self.session.shape_combo(sel[0], sel[1]))

    def on_combo_sculpt(self) -> None:
        for n in self.selected_names():
            if self._is_combo(n):
                self._run(lambda: self.session.sculpt_begin_combo(n))
                return

    def on_transfer(self) -> None:
        names = self.selected_names()
        dest = self.dest_combo.currentData()
        if not names or not dest:
            return
        try:
            plan = self.session.shape_transfer_plan(names, dest)
        except ERRORS as e:
            self.set_status(str(e), error=True)
            self.show_error(str(e))
            return
        over: list[str] = []
        if plan.collisions:
            if plan.protected and not self.confirm(f"写し先に元からある {', '.join(plan.protected)} を上書きしますか（いいえなら飛ばします）"):
                skip = set(plan.protected)
            else:
                skip = set()
            over = [n for n in plan.collisions if n not in skip]
            if not over and not (set(names) - set(plan.collisions)):
                self.set_status("写せるシェイプがありません（すべて写し先と名前が衝突）")
                return
        self._run(lambda: self.session.shape_transfer(names, dest, over))

    def on_clean(self) -> None:
        names = self.selected_names()
        orig = [n for n in names if any(i.name == n and i.tag == shapes.TAG_ORIGINAL for i in self._infos)]
        allow = False
        if orig and self.confirm(f"元からあるシェイプ（{', '.join(orig[:3])}）も掃除しますか（いいえなら飛ばします）"):
            allow = True
        self._run(lambda: self.session.shape_clean(names, self.clean_thr.value(), allow))

    def on_audit(self) -> None:
        try:
            au = self.session.shape_audit()
            miss = self.session.shape_missing_standard()
        except ERRORS as e:
            self.set_status(str(e), error=True)
            return
        lines = [f"空のシェイプ（差分が無い）: {', '.join(au.empty) or 'なし'}",
                 f"未使用（どのポーズ・作業セットからも使われていない）: {', '.join(au.unreferenced) or 'なし'}",
                 f"孤立した fcs_（どの点のポーズからも使われていない）: {', '.join(au.orphan_sculpt) or 'なし'}",
                 f"孤立した FC_（対応する点が無い）: {', '.join(au.orphan_fc) or 'なし'}",
                 f"プロファイルの標準シェイプの不足: {len(miss)} 個" + (f"（{', '.join(miss[:12])}{'…' if len(miss) > 12 else ''}）" if miss else "")]
        self.audit_text.setPlainText("\n".join(lines))
        self._audit = au

    def on_audit_delete(self) -> None:
        try:
            au = self.session.shape_audit()
        except ERRORS as e:
            self.set_status(str(e), error=True)
            return
        names = au.orphan_sculpt + au.orphan_fc
        if not names:
            self.set_status("孤立した fcs_ / FC_ はありません")
            return
        if not self.confirm(f"孤立した {len(names)} 個（{', '.join(names[:4])}…）を消します。よろしいですか"):
            return
        self._run(lambda: self.session.shape_delete(names))
        self.on_audit()

    def on_goto_validate(self) -> None:
        p = self.parent()
        while p is not None and not hasattr(p, "select_tab"):
            p = p.parent()
        if p is not None:
            p.select_tab("validate")

    # ============================================================ 後片付け
    def detach(self) -> None:
        if self.on_state in self.session.state_listeners:
            self.session.state_listeners.remove(self.on_state)
