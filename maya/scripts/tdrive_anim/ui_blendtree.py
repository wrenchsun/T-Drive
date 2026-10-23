"""ブレンドタブの「Blend Tree パラメータ」節（docs/20 §4.5、チケット AN-11-2 / AN-11-3）。D-Drive の AnimEditor「確認する › ブレンド」の 3 つ目の手段。

- 状態図（animset の `stateGraph`）の状態のうち `blendTree` を持つものを選び、X / Y のパラメータ（状態図の float）を **2D パッド**（160 px）と
  スライダーで動かす。動かすたびに重みを計算し直し（`core/blend_tree.py`）、合成を作り直す（`composition.compose_blend_tree`）
- 作り直しは**マウスボタンを離したとき 1 回**（パッド: 離す / スライダー: sliderReleased / キー・ホイール・数値入力: その場で 1 回。AN-F-47）。
  ドラッグ中は作らず、重みの % とパッドの点だけ追従する。再生中でも効き、時刻と再生状態は保つ
- 雛形: 「2D の移動ツリーを作る」（`_F/_B/_L/_R` が 2 方向以上）、「1D の Locomotion を作る」（Idle と Run がある）
- パラメータの値は見ているだけの状態（animset には入れない）。ツリー自体の変更（雛形・パラメータ名の付け替え）は animset の変更なので Undo できる
"""

from __future__ import annotations

import copy
import math
import re

from maya import cmds
from PySide6 import QtCore, QtGui, QtWidgets

from tdrive import widgets as WG

from . import composition as K
from .core import blend_tree as BT
from .core import state_machine as M
from .ui_common import ComposeProgress

PAD_SIZE = 160
PAD_MARGIN = 10
HIT_RADIUS = 8.0  # 青い点の当たり判定（px。AN-F-49）
RANGES = (("0 〜 1", (0.0, 1.0)), ("-1 〜 1", (-1.0, 1.0)))


class PadWidget(QtWidgets.QWidget):
    """2D パッド（D-Drive の AnimEditor と同じ 160 px）。モーションの位置を点で、いまの値を丸で描く。ドラッグで動かす。"""

    valueChanged = QtCore.Signal(float, float)  # 動かした（ドラッグ中も毎回）
    released = QtCore.Signal()
    pointMoved = QtCore.Signal(int, float, float)  # 青い点（モーションの位置）をドラッグして離した: (モーションの番号, x, y)（AN-F-48）

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(PAD_SIZE, PAD_SIZE)
        self.lo, self.hi = 0.0, 1.0
        self.x = self.y = 0.0
        self.points: list[tuple[float, float, str, float]] = []  # (x, y, 名前, 重み)
        self._drag_point: int | None = None
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setToolTip("2D パッド: ドラッグでパラメータ X / Y を動かします（離したときに合成を作り直します）。"
                        "青い点はモーションの位置（点を押してドラッグで動かせます。Shift を押しながらなら点の上でも値を動かします）、大きさは今の重み。範囲は右で 0〜1 / -1〜1 を切り替え。既定: 原点（0, 0）")

    def range(self) -> tuple[float, float]:
        return self.lo, self.hi

    def set_range(self, lo: float, hi: float) -> None:
        self.lo, self.hi = lo, hi
        self.x, self.y = self._clamp(self.x), self._clamp(self.y)
        self.update()

    def _clamp(self, v: float) -> float:
        return max(self.lo, min(self.hi, v))

    def set_points(self, points: list[tuple[float, float, str, float]]) -> None:
        self.points = points
        self.update()

    def set_value(self, x: float, y: float, emit: bool = True) -> None:
        self.x, self.y = self._clamp(x), self._clamp(y)
        self.update()
        if emit:
            self.valueChanged.emit(self.x, self.y)

    def _to_px(self, x: float, y: float) -> QtCore.QPointF:
        span = self.hi - self.lo
        w = PAD_SIZE - 2 * PAD_MARGIN
        return QtCore.QPointF(PAD_MARGIN + (x - self.lo) / span * w, PAD_SIZE - PAD_MARGIN - (y - self.lo) / span * w)  # y は上が大きい

    def _from_px(self, pos: QtCore.QPointF) -> tuple[float, float]:
        span = self.hi - self.lo
        w = PAD_SIZE - 2 * PAD_MARGIN
        return (self.lo + (pos.x() - PAD_MARGIN) / w * span, self.lo + (PAD_SIZE - PAD_MARGIN - pos.y()) / w * span)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor("#2b2b2b"))
        p.setPen(QtGui.QColor("#555"))
        p.drawRect(PAD_MARGIN, PAD_MARGIN, PAD_SIZE - 2 * PAD_MARGIN, PAD_SIZE - 2 * PAD_MARGIN)
        zero = self._to_px(0.0, 0.0)
        p.drawLine(QtCore.QPointF(PAD_MARGIN, zero.y()), QtCore.QPointF(PAD_SIZE - PAD_MARGIN, zero.y()))
        p.drawLine(QtCore.QPointF(zero.x(), PAD_MARGIN), QtCore.QPointF(zero.x(), PAD_SIZE - PAD_MARGIN))
        for k, (x, y, _name, w) in enumerate(self.points):
            c = self._to_px(x, y)
            r = 3.0 + 6.0 * w
            p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 2) if k == self._drag_point else QtGui.QColor("#6fa8dc"))  # ドラッグ中の点は白い縁で強調
            p.setBrush(QtGui.QColor(111, 168, 220, 60 + int(150 * w)))
            p.drawEllipse(c, r, r)
        cur = self._to_px(self.x, self.y)
        p.setPen(QtGui.QPen(QtGui.QColor("#f0a040"), 2))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(cur, 5, 5)
        p.drawLine(QtCore.QPointF(cur.x() - 8, cur.y()), QtCore.QPointF(cur.x() + 8, cur.y()))
        p.drawLine(QtCore.QPointF(cur.x(), cur.y() - 8), QtCore.QPointF(cur.x(), cur.y() + 8))

    def _drag(self, event) -> None:
        x, y = self._from_px(event.position())
        self.set_value(x, y)

    def _point_at(self, pos: QtCore.QPointF) -> int | None:
        best = None
        for i, (x, y, _n, w) in enumerate(self.points):
            c = self._to_px(x, y)
            d = math.hypot(c.x() - pos.x(), c.y() - pos.y())
            if d <= HIT_RADIUS and (best is None or d < best[0]):
                best = (d, i)
        return best[1] if best else None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != QtCore.Qt.LeftButton:
            return
        # 青い点（半径 8 px 以内で一番近いもの）を押したら、その点のドラッグ（モーションの位置）。Shift を押していれば常に橙の十字（パラメータの値）のドラッグ（AN-F-49）
        self._drag_point = None if event.modifiers() & QtCore.Qt.ShiftModifier else self._point_at(event.position())
        self.update()
        if self._drag_point is None:
            self._drag(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not event.buttons() & QtCore.Qt.LeftButton:  # ボタンを押していない: 点の上ではカーソルを変える
            on = self._drag_point is None and not event.modifiers() & QtCore.Qt.ShiftModifier and self._point_at(event.position()) is not None
            self.setCursor(QtCore.Qt.SizeAllCursor if on else QtCore.Qt.CrossCursor)
            return
        if self._drag_point is None:
            self._drag(event)
            return
        x, y = self._from_px(event.position())
        _x, _y, name, w = self.points[self._drag_point]
        self.points[self._drag_point] = (round(self._clamp(x), 2), round(self._clamp(y), 2), name, w)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != QtCore.Qt.LeftButton:
            return
        i, self._drag_point = self._drag_point, None
        self.update()
        if i is None:
            self.released.emit()
        else:
            self.pointMoved.emit(i, self.points[i][0], self.points[i][1])


class BlendTreePanel(QtWidgets.QGroupBox):
    """Blend Tree パラメータの節。`BlendTab` が作って並べ、合成の名前・クリップ・スケルトン・メッセージ欄を渡す。"""

    def __init__(self, tab, comp_name: str) -> None:
        super().__init__("Blend Tree パラメータ（Unity の Blend Tree と同じ重みで、2D パッド / スライダーで確認）")
        self.tab = tab
        self.comp_name = comp_name  # ブレンドタブの合成の名前（遷移列 / 状態図の合成と同じ。1 つだけ持つ）
        self.values: dict[str, float] = {}
        self._building = False
        v = QtWidgets.QVBoxLayout(self)
        self.state_combo = WG.ComboBox()
        self.state_combo.setToolTip("状態図のうち、ブレンドツリーを持つ状態。雛形のボタンで作れます")
        self.state_combo.activated.connect(lambda *_: self._state_changed())
        form = QtWidgets.QFormLayout()
        form.addRow("ブレンドツリー", self.state_combo)
        self.x_combo = WG.ComboBox()
        self.x_combo.setToolTip("X に使うパラメータ（状態図の float）。1D ではこれだけ。変えるとツリーの parameters が変わります（Undo できます）")
        self.y_combo = WG.ComboBox()
        self.y_combo.setToolTip("Y に使うパラメータ（状態図の float）。2D のみ")
        self.x_combo.activated.connect(lambda *_: self._params_picked())
        self.y_combo.activated.connect(lambda *_: self._params_picked())
        form.addRow("X パラメータ", self.x_combo)
        form.addRow("Y パラメータ", self.y_combo)
        v.addLayout(form)
        mid = QtWidgets.QHBoxLayout()
        self.pad = PadWidget()
        self.pad.valueChanged.connect(lambda *_: self._pad_moved())
        self.pad.released.connect(lambda *_: self.rebuild())  # 離したとき 1 回（AN-F-47）
        mid.addWidget(self.pad)
        right = QtWidgets.QVBoxLayout()
        self.pad_range = WG.ComboBox()
        for label, rng in RANGES:
            self.pad_range.addItem(label, rng)
        self.pad_range.setToolTip("パッドとスライダーの範囲。0〜1（既定。モーションの位置がすべて 0 以上のとき）か -1〜1（左右・後ろがあるとき）")
        self.pad_range.activated.connect(lambda *_: self._range_changed())
        right.addWidget(self.pad_range)
        self.sliders: list[tuple[WG.Slider, QtWidgets.QLabel]] = []
        for i in range(2):
            row = QtWidgets.QHBoxLayout()
            sl = WG.Slider(QtCore.Qt.Horizontal)
            sl.setRange(-1000, 1000)
            lab = QtWidgets.QLabel("0.00")
            sl.setToolTip(f"{'X' if i == 0 else 'Y'} パラメータの値（範囲は上の切替。既定: 0）。ドラッグを離したときに合成を作り直します")
            sl.valueChanged.connect(lambda *_, k=i: self._slider_moved(k))
            sl.sliderReleased.connect(lambda *_: self.rebuild())
            row.addWidget(sl, 1)
            row.addWidget(lab)
            self.sliders.append((sl, lab))
            right.addLayout(row)
        right.addStretch(1)
        mid.addLayout(right, 1)
        v.addLayout(mid)
        self.tree_weights_label = QtWidgets.QLabel()  # モーションごとの重み
        self.tree_weights_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        v.addWidget(self.tree_weights_label)
        self.pad.pointMoved.connect(lambda i, x, y: self.move_motion(i, x, y))  # 青い点のドラッグ（AN-F-48）
        self.motion_table = QtWidgets.QTableWidget(0, 4)  # モーションの表（クリップ / しきい値 or 位置 / 削除。AN-F-48）
        self.motion_table.verticalHeader().setVisible(False)
        self.motion_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.motion_table.setMinimumHeight(110)
        self.motion_table.setToolTip("ブレンドツリーのモーションの一覧です。クリップを選び、しきい値（1D）か位置 X / Y（2D）を入れます。"
                                     "Enter かフォーカスを外すと保存されます（animset の変更なので Ctrl+Z で戻せます）。2D はパッドの青い点をドラッグしても動かせます")
        v.addWidget(self.motion_table)
        mb = QtWidgets.QHBoxLayout()
        self.add_motion_btn = QtWidgets.QPushButton("＋ モーションを足す")
        self.add_motion_btn.setToolTip("ツリーにモーションを 1 つ足します（クリップは未使用のものから、しきい値 / 位置は空いている所）。あとで表で直します")
        self.add_motion_btn.clicked.connect(lambda *_: self.add_motion())
        self.new_tree_btn = QtWidgets.QPushButton("ツリーを新規…")
        self.new_tree_btn.setToolTip("名前・種類（1D / 2DSimpleDirectional / 2DFreeformDirectional / 2DFreeformCartesian）・パラメータ名を決めて、新しいブレンドツリーを状態図に足します")
        self.new_tree_btn.clicked.connect(lambda *_: self.new_tree_dialog())
        self.del_tree_btn = QtWidgets.QPushButton("ツリーを削除")
        self.del_tree_btn.setToolTip("選んでいるブレンドツリーを状態図から消します（確認あり。Ctrl+Z で戻せます）")
        self.del_tree_btn.clicked.connect(lambda *_: self.delete_tree())
        for b in (self.add_motion_btn, self.new_tree_btn, self.del_tree_btn):
            mb.addWidget(b)
        mb.addStretch(1)
        v.addLayout(mb)
        btns = QtWidgets.QHBoxLayout()
        self.t2d_btn = QtWidgets.QPushButton("2D の移動ツリーを作る")
        self.t2d_btn.setToolTip("Walk（無ければ Run）の _F / _B / _L / _R が 2 方向以上そろっているとき、Idle を中央にした 2DSimpleDirectional（MoveX / MoveY）を状態図に足します")
        self.t2d_btn.clicked.connect(lambda *_: self.make_template_2d())
        self.t1d_btn = QtWidgets.QPushButton("1D の Locomotion を作る")
        self.t1d_btn.setToolTip("Idle(0) / Walk_F(0.5) / Run_F(1) の 1D ツリー（Speed）を状態図に足します。Idle と Run が必要（Walk は無くてもよい）")
        self.t1d_btn.clicked.connect(lambda *_: self.make_template_1d())
        self.build_btn = QtWidgets.QPushButton("合成を作り直す")
        self.build_btn.setToolTip("今のパラメータの値でブレンドツリーの合成を作ります（パッドを動かすと自動で作り直します）。UpperBody レイヤーはブレンドツリーには重ねません")
        self.build_btn.clicked.connect(lambda *_: self.rebuild())
        for b in (self.t2d_btn, self.t1d_btn, self.build_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        v.addLayout(btns)
        self.hint = QtWidgets.QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: gray;")
        v.addWidget(self.hint)
        self.refresh()

    # ------------------------------------------------------------ 状態図
    def _graph(self) -> dict | None:
        d = self.tab.session.data
        return d.get("stateGraph") if d and isinstance(d.get("stateGraph"), dict) else None

    def _tree_states(self) -> list[dict]:
        g = self._graph()
        return [s for s in (g or {}).get("states", []) if isinstance(s.get("blendTree"), dict)]

    def current_state(self) -> dict | None:
        name = self.state_combo.currentText()
        return next((s for s in self._tree_states() if s["name"] == name), None)

    def current_tree(self) -> dict | None:
        s = self.current_state()
        return s["blendTree"] if s else None

    def refresh(self) -> None:
        """animset / 状態図が変わった: 状態の一覧・パラメータのコンボ・雛形ボタンの有効 / 無効を直す。"""
        d = self.tab.session.data
        self._building = True
        try:
            keep = self.state_combo.currentText()
            names = [s["name"] for s in self._tree_states()]
            if [self.state_combo.itemText(i) for i in range(self.state_combo.count())] != names:
                self.state_combo.clear()
                self.state_combo.addItems(names)
            if keep in names:
                self.state_combo.setCurrentText(keep)
            g = self._graph() or {}
            floats = [p["name"] for p in g.get("parameters", []) if p.get("type") == "float"]
            tree = self.current_tree()
            for combo in (self.x_combo, self.y_combo):
                combo.clear()
                combo.addItems(floats)
            if tree:
                ps = BT.parameters(tree)
                for combo, i in ((self.x_combo, 0), (self.y_combo, 1)):
                    if i < len(ps):
                        combo.setCurrentText(ps[i])
            self.y_combo.setEnabled(bool(tree) and BT.is_2d(tree))
            self.pad.setEnabled(bool(tree) and BT.is_2d(tree))
            self.sliders[1][0].setEnabled(bool(tree) and BT.is_2d(tree))
            self.build_btn.setEnabled(bool(tree))
            self.add_motion_btn.setEnabled(bool(tree))
            self.del_tree_btn.setEnabled(bool(tree))
            self.new_tree_btn.setEnabled(d is not None)
            self._fill_motion_table(tree)
            clips = d["clips"] if d else []
            mid = d["modelId"] if d else ""
            self.t2d_btn.setEnabled(d is not None and BT.template_2d(clips, mid) is not None)
            self.t1d_btn.setEnabled(d is not None and BT.template_1d(clips, mid) is not None)
            if tree:
                lo, hi = BT.default_range(tree)
                if not getattr(self, "_range_touched", False):
                    self.pad_range.setCurrentIndex(0 if (lo, hi) == (0.0, 1.0) else 1)
                self._apply_range()
                self._load_values(tree)
            else:
                self.pad.set_points([])
                self.tree_weights_label.setText("")
            self.hint.setText("" if tree else ("animset を作ると使えます" if d is None else
                                               "ブレンドツリーがありません。下の雛形ボタンで作れます（Walk の _F/_B/_L/_R がそろっている / Idle と Run がある）"))
        finally:
            self._building = False
        self._show_weights()

    # ------------------------------------------------------------ モーションの編集（AN-F-48）
    def _fill_motion_table(self, tree: dict | None) -> None:
        t = self.motion_table
        t.setRowCount(0)
        if tree is None:
            t.setColumnCount(0)
            return
        two = BT.is_2d(tree)
        heads = ["クリップ", "X", "Y", ""] if two else ["クリップ", "しきい値", ""]
        t.setColumnCount(len(heads))
        t.setHorizontalHeaderLabels(heads)
        t.horizontalHeader().setStretchLastSection(False)
        t.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        names = list(self.tab._clips())
        for i, m in enumerate(tree["motions"]):
            t.insertRow(i)
            combo = WG.ComboBox()
            combo.addItems(names if m["clip"] in names else [m["clip"], *names])
            combo.setCurrentText(m["clip"])
            combo.setToolTip("このモーションのクリップ（クリップタブで作ったもの）")
            combo.activated.connect(lambda *_, k=i, c=combo: self.edit_motion(k, clip=c.currentText()))
            t.setCellWidget(i, 0, combo)
            vals = list(BT.motion_pos(m)) if two else [float(m["threshold"])]
            for j, val in enumerate(vals):
                sp = WG.DoubleSpinBox()
                sp.setRange(-100.0, 100.0)
                sp.setDecimals(3)
                sp.setSingleStep(0.1)
                sp.setValue(val)
                sp.setToolTip(("しきい値" if not two else "位置 " + "XY"[j]) + "（範囲 -100〜100）。Enter かフォーカスを外すと保存。他のモーションと同じ値にしないでください（重なると警告）")
                sp.editingFinished.connect(lambda *_, k=i: self._row_edited(k))
                t.setCellWidget(i, 1 + j, sp)
            btn = QtWidgets.QPushButton("削除")
            btn.setToolTip("このモーションをツリーから消します（最後の 1 つは消せません）")
            btn.clicked.connect(lambda *_, k=i: self.remove_motion(k))
            t.setCellWidget(i, len(heads) - 1, btn)
        t.setMaximumHeight(60 + 30 * len(tree["motions"]))

    def _row_edited(self, row: int) -> None:
        if self._building:
            return
        tree = self.current_tree()
        if tree is None or row >= len(tree["motions"]):
            return
        n = 2 if BT.is_2d(tree) else 1
        vals = [self.motion_table.cellWidget(row, 1 + j).value() for j in range(n)]
        cur = list(BT.motion_pos(tree["motions"][row])) if n == 2 else [float(tree["motions"][row]["threshold"])]
        if all(abs(a - b) < 1e-9 for a, b in zip(vals, cur)):  # フォーカスを外しただけ（値は同じ）では保存しない
            return
        if n == 2:
            self.edit_motion(row, position=[vals[0], vals[1]])
        else:
            self.edit_motion(row, threshold=vals[0])

    def _modify_tree(self, fn, what: str) -> bool:
        """現在のツリーを fn(tree) で書き換えて animset に保存する（1 操作 = 1 Undo）。重複があれば警告（保存はする）。"""
        tree = self.current_tree()
        d = self.tab.session.data
        if tree is None or d is None:
            return False
        name = self.state_combo.currentText()
        g = copy.deepcopy(self._graph())
        st = next(x for x in g["states"] if x["name"] == name)
        fn(st["blendTree"])
        self.tab._guard(self.tab.session.set_value, "stateGraph", g)
        self.tab.graph = d.get("stateGraph")
        self.refresh()
        dup = [e for e in BT.validate(self.current_tree() or {}) if "重なって" in e]
        self.tab._say(what + "".join(f"\n⚠ {e}（保存はしましたが、重みが決まりません。値を変えてください）" for e in dup))
        if K.info(self.comp_name).get("blendTree") and not dup:
            self.rebuild()  # 合成中なら、ツリーの変更を反映する
        return True

    def edit_motion(self, index: int, clip: str | None = None, position: list | None = None, threshold: float | None = None) -> bool:
        def fn(tree: dict) -> None:
            m = tree["motions"][index]
            if clip is not None:
                m["clip"] = clip
            if position is not None:
                m["position"] = [round(float(position[0]), 4), round(float(position[1]), 4)]
            if threshold is not None:
                m["threshold"] = round(float(threshold), 4)
        return self._modify_tree(fn, "モーションを変えました")

    def move_motion(self, index: int, x: float, y: float) -> bool:
        """パッドの青い点をドラッグして離した: モーションの位置を変える。"""
        return self.edit_motion(index, position=[x, y])

    def add_motion(self) -> bool:
        tree = self.current_tree()
        if tree is None:
            return False
        used = {m["clip"] for m in tree["motions"]}
        names = list(self.tab._clips())
        clip = next((n for n in names if n not in used), names[0] if names else "")
        if not clip:
            self.tab._say("⚠ クリップがありません")
            return False

        def fn(t: dict) -> None:
            if t["type"] == "1D":
                t["motions"].append({"clip": clip, "threshold": round(max(float(m["threshold"]) for m in t["motions"]) + 0.5, 4)})
                return
            taken = [BT.motion_pos(m) for m in t["motions"]]
            cands = ((0, 0), (0, 1), (0, -1), (-1, 0), (1, 0), (1, 1), (-1, 1), (1, -1), (-1, -1), (0.5, 0.5))
            free = next(((x, y) for x, y in cands if not any(abs(x - a) < 1e-6 and abs(y - b) < 1e-6 for a, b in taken)), (2.0, 2.0))
            t["motions"].append({"clip": clip, "position": [free[0], free[1]]})
        return self._modify_tree(fn, f"モーション {clip} を足しました（表でしきい値 / 位置を直してください）")

    def remove_motion(self, index: int) -> bool:
        tree = self.current_tree()
        if tree is None:
            return False
        if len(tree["motions"]) <= 1:
            self.tab._say("⚠ 最後のモーションは消せません（ツリーごと消すときは「ツリーを削除」）")
            return False
        return self._modify_tree(lambda t: t["motions"].pop(index), "モーションを消しました")

    def create_tree(self, name: str, typ: str, params: list[str]) -> bool:
        """新しいブレンドツリーを状態図に足す（最初のモーションはクリップの 1 つ目。あとで足す）。"""
        d = self.tab.session.data
        if d is None:
            return False
        ident = r"^[A-Za-z][A-Za-z0-9_]*$"
        if not re.match(ident, name or ""):
            self.tab._say("⚠ 名前は英字で始まる英数字と _ にしてください")
            return False
        if typ not in BT.TYPES:
            self.tab._say(f"⚠ 種類は {' / '.join(BT.TYPES)} のどれか")
            return False
        n = 1 if typ == "1D" else 2
        params = [p.strip() for p in params][:n]
        if len(params) != n or not all(re.match(ident, p) for p in params) or len(set(params)) != n:
            self.tab._say(f"⚠ パラメータ名は {n} つ（英字で始まる英数字と _、重複なし）")
            return False
        if not d["clips"]:
            self.tab._say("⚠ クリップがありません（先にクリップを作ってください）")
            return False
        first = d["clips"][0]["name"]
        motion = {"clip": first, "threshold": 0} if typ == "1D" else {"clip": first, "position": [0, 0]}
        before = len(self._tree_states())
        self._add_state(name, {"type": typ, "parameters": params, "motions": [motion]}, params)
        return len(self._tree_states()) == before + 1

    def new_tree_dialog(self) -> None:
        d = self.tab.session.data
        if d is None:
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("ブレンドツリーを新規")
        form = QtWidgets.QFormLayout(dlg)
        name = QtWidgets.QLineEdit("Move")
        name.setToolTip("状態の名前（英字で始まる英数字と _）。既定: Move")
        typ = WG.ComboBox()
        typ.addItems(BT.TYPES)
        typ.setCurrentText("2DSimpleDirectional")
        typ.setToolTip("Unity の Blend Tree の種類。1D はパラメータ 1 つ、2D は 2 つ")
        floats = [p["name"] for p in (self._graph() or {}).get("parameters", []) if p.get("type") == "float"]
        px, py = WG.ComboBox(), WG.ComboBox()
        for c, dflt in ((px, "MoveX"), (py, "MoveY")):
            c.setEditable(True)
            c.addItems(list(dict.fromkeys([*floats, dflt])))
            c.setCurrentText(dflt)
            c.setToolTip("パラメータ名（状態図の float か、新しい名前。新しい名前は状態図に float として足されます）")
        form.addRow("名前", name)
        form.addRow("種類", typ)
        form.addRow("X パラメータ", px)
        form.addRow("Y パラメータ（2D のみ）", py)
        typ.currentTextChanged.connect(lambda t, c=py: c.setEnabled(t != "1D"))
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        form.addRow(box)
        if dlg.exec():
            self.create_tree(name.text().strip(), typ.currentText(), [px.currentText(), py.currentText()])

    def delete_tree(self, confirm: bool = True) -> bool:
        name = self.state_combo.currentText()
        d = self.tab.session.data
        if self.current_tree() is None or d is None:
            return False
        if confirm and QtWidgets.QMessageBox.question(self, "ツリーを削除", f"ブレンドツリー {name} を状態図から消します。よろしいですか？（Ctrl+Z で戻せます）") != QtWidgets.QMessageBox.Yes:
            return False
        g = copy.deepcopy(self._graph())
        g["states"] = [x for x in g["states"] if x["name"] != name]
        g["transitions"] = [t for t in g.get("transitions", []) if name not in (t.get("from"), t.get("to"))]
        if K.info(self.comp_name).get("blendTree"):
            K.remove(self.comp_name)
        self.tab._guard(self.tab.session.set_value, "stateGraph", g)
        self.tab.graph = d.get("stateGraph")
        self.refresh()
        self.tab._say(f"ブレンドツリー {name} を消しました")
        return True

    def _load_values(self, tree: dict) -> None:
        g = self._graph() or {}
        defaults = {p["name"]: float(p.get("default", 0)) for p in g.get("parameters", [])}
        for p in BT.parameters(tree):
            self.values.setdefault(p, defaults.get(p, 0.0))
        ps = BT.parameters(tree)
        x = self.values.get(ps[0], 0.0)
        y = self.values.get(ps[1], 0.0) if len(ps) > 1 else 0.0
        self.pad.set_value(x, y, emit=False)
        self._sync_sliders()

    def _state_changed(self) -> None:
        self.refresh()

    def _params_picked(self) -> None:
        """X / Y のパラメータを付け替えた: ツリーの parameters を書き換える（animset の変更 = 1 Undo）。"""
        if self._building:
            return
        tree = self.current_tree()
        if tree is None:
            return
        n = 2 if BT.is_2d(tree) else 1
        ps = [self.x_combo.currentText(), self.y_combo.currentText()][:n]
        if not all(ps):
            return
        if n == 2 and ps[0] == ps[1]:
            self.tab._say("⚠ X と Y に同じパラメータは使えません")
            self.refresh()
            return
        g = copy.deepcopy(self._graph())
        for s in g["states"]:
            if s["name"] == self.state_combo.currentText():
                s["blendTree"]["parameters"] = ps
        self.tab._guard(self.tab.session.set_value, "stateGraph", g)
        self.tab.graph = self.tab.session.data.get("stateGraph")
        self.refresh()

    # ------------------------------------------------------------ 範囲・値
    def _range_changed(self) -> None:
        self._range_touched = True
        self._apply_range()
        self._show_weights()

    def _apply_range(self) -> None:
        lo, hi = self.pad_range.currentData()
        self.pad.set_range(lo, hi)
        self._sync_sliders()

    def _sync_sliders(self) -> None:
        lo, hi = self.pad.range()
        tree = self.current_tree()
        ps = BT.parameters(tree) if tree else []
        for i, (sl, lab) in enumerate(self.sliders):
            val = self.values.get(ps[i], 0.0) if i < len(ps) else 0.0
            val = max(lo, min(hi, val))
            if i < len(ps):
                self.values[ps[i]] = val
            sl.blockSignals(True)
            sl.setValue(int(round((val - lo) / (hi - lo) * 2000 - 1000)))
            sl.blockSignals(False)
            lab.setText(f"{val:.2f}")

    def _slider_moved(self, i: int) -> None:
        if self._building:
            return
        tree = self.current_tree()
        if tree is None:
            return
        ps = BT.parameters(tree)
        if i >= len(ps):
            return
        lo, hi = self.pad.range()
        val = lo + (self.sliders[i][0].value() + 1000) / 2000 * (hi - lo)
        self.values[ps[i]] = val
        self.sliders[i][1].setText(f"{val:.2f}")
        if len(ps) > 1:
            self.pad.set_value(self.values.get(ps[0], 0.0), self.values.get(ps[1], 0.0), emit=False)
        self._show_weights()
        if not self.sliders[i][0].isSliderDown():  # ドラッグ中は作らない（離したとき sliderReleased で 1 回）。キー・ホイールはその場で 1 回
            self.rebuild()

    def _pad_moved(self) -> None:
        tree = self.current_tree()
        if tree is None:
            return
        ps = BT.parameters(tree)
        self.values[ps[0]] = self.pad.x
        if len(ps) > 1:
            self.values[ps[1]] = self.pad.y
        self._sync_sliders()
        self._show_weights()  # ドラッグ中は重みの表示だけ。作り直しは離したとき（released）

    def set_values(self, **kw: float) -> None:
        """パラメータの値を直接入れる（テスト・スクリプト用）。パッドとスライダーも合わせて作り直しを予約する。"""
        self.values.update(kw)
        tree = self.current_tree()
        if tree:
            self._load_values(tree)
            self._show_weights()
            self.rebuild()

    def _current_values(self) -> list[float]:
        tree = self.current_tree()
        return [self.values.get(p, 0.0) for p in BT.parameters(tree)] if tree else []

    def _show_weights(self) -> None:
        tree = self.current_tree()
        if tree is None:
            return
        try:
            ws = BT.weights(tree, self._current_values())
        except BT.BlendTreeError as exc:
            self.tree_weights_label.setText(f"⚠ {exc}")
            return
        two = BT.is_2d(tree)
        lines = []
        for m, w in zip(tree["motions"], ws):
            where = f"({BT.motion_pos(m)[0]:g}, {BT.motion_pos(m)[1]:g})" if two else f"{m['threshold']:g}"
            lines.append(f"{m['clip'].split('_', 2)[-1] if m['clip'].startswith('A_') else m['clip']}  {where}  {w * 100:.0f}%")
        self.tree_weights_label.setText("\n".join(lines))
        if two:
            self.pad.set_points([(*BT.motion_pos(m), m["clip"], w) for m, w in zip(tree["motions"], ws)])

    def mix_text(self) -> str:
        """合成中の今の割合（バナー・HUD 用。例: `Walk_F 70% / Idle 30%`）。"""
        info = K.info(self.tab_comp()).get("blendTree") or {}
        tree = self.current_tree()
        ws = info.get("weights")
        if not tree or not ws or len(ws) != len(tree["motions"]):
            return ""
        return BT.weight_text({**tree, "motions": [{**m, "clip": m["clip"].split("_", 2)[-1] if m["clip"].startswith("A_") else m["clip"]} for m in tree["motions"]]}, ws)

    def tab_comp(self) -> str:
        return self.comp_name

    # ------------------------------------------------------------ 作り直し
    def rebuild(self) -> None:
        tree = self.current_tree()
        if tree is None:
            self.tab._say("⚠ ブレンドツリーがありません（雛形のボタンで作れます）")
            return
        t = cmds.currentTime(query=True)
        playing = cmds.play(query=True, state=True)
        cmds.play(state=False)
        had = bool(K.info(self.tab_comp()).get("blendTree"))
        self.tab._guard(self._compose, tree, use_cache=had)
        if had:  # 再生中の作り直しは時刻と再生状態を保つ。最初の 1 回は 0 から
            cmds.currentTime(t, edit=True)
            if playing:
                cmds.play(forward=True)

    def _compose(self, tree: dict, use_cache: bool) -> None:
        tab = self.tab
        errs = BT.validate(tree, tab.session.data["clips"])
        if errs:
            raise ValueError(errs[0])
        motions = K.tree_motions(tree, self._current_values(), tab._clips())
        tab._remember_speed()
        progress = ComposeProgress(tab)
        try:
            res = K.compose_blend_tree(self.tab_comp(), motions, tab._skeleton(), tab._fps(),
                                       {"state": self.state_combo.currentText(), "type": tree["type"], "values": self._current_values()},
                                       progress=progress, use_cache=use_cache, hide_ctls=tab.hide_ctl_cb.isChecked())
        except K.ComposeCancelled:
            tab._say("合成の作成を中止しました（合成は作っていません）")
            tab.refresh()
            return
        finally:
            progress.close()
        cmds.playbackOptions(loop="continuous" if tab.loop_cb.isChecked() else "once")
        tab._mix_placements = []
        t = res.timings
        took = f"（{t.get('total', 0):.1f} 秒）" if t else ""
        warns = "".join(f"\n⚠ {w}" for w in res.warnings)
        tab._say(f"ブレンドツリーの合成を作りました（{res.frames:.0f} フレーム、{res.joints} ジョイント）{took}: {self.mix_text()}。パッドを動かすと作り直します{warns}{tab._fps_note()}")
        tab.refresh()

    # ------------------------------------------------------------ 雛形
    def _add_state(self, name: str, tree: dict, params: list[str]) -> None:
        d = self.tab.session.data
        if d is None:
            return
        g = copy.deepcopy(self._graph()) or M.default_graph(d["clips"], d["modelId"])
        if any(s["name"] == name for s in g["states"]):
            self.tab._say(f"⚠ 状態 {name} は既にあります（消すか名前を変えてから作り直してください）")
            return
        have = {p["name"] for p in g["parameters"]}
        for p in params:
            if p not in have:
                g["parameters"].append({"name": p, "type": "float", "default": 0})
        g["states"].append({"name": name, "blendTree": tree})
        self.tab._guard(self.tab.session.set_value, "stateGraph", g)
        self.tab.graph = self.tab.session.data.get("stateGraph")
        self.refresh()
        self.state_combo.setCurrentText(name)
        self.refresh()
        self.tab._say(f"ブレンドツリー {name}（{tree['type']}、モーション {len(tree['motions'])} 本）を状態図に足しました。パッドを動かすと合成を作ります")

    def make_template_2d(self) -> None:
        d = self.tab.session.data
        tree = BT.template_2d(d["clips"], d["modelId"]) if d else None
        if tree is None:
            self.tab._say("⚠ Walk（または Run）の _F / _B / _L / _R が 2 方向以上そろっていません")
            return
        self._add_state("Move", tree, BT.parameters(tree))

    def make_template_1d(self) -> None:
        d = self.tab.session.data
        tree = BT.template_1d(d["clips"], d["modelId"]) if d else None
        if tree is None:
            self.tab._say("⚠ Idle と Run のクリップが必要です")
            return
        self._add_state("Locomotion", tree, BT.parameters(tree))

    def select_tree(self, name: str) -> None:
        self.state_combo.setCurrentText(name)
        self.refresh()

