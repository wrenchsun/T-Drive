"""ブレンドタブの「Blend Tree パラメータ」節（docs/20 §4.5、チケット AN-11-2 / AN-11-3）。D-Drive の AnimEditor「確認する › ブレンド」の 3 つ目の手段。

- 状態図（animset の `stateGraph`）の状態のうち `blendTree` を持つものを選び、X / Y のパラメータ（状態図の float）を **2D パッド**（160 px）と
  スライダーで動かす。動かすたびに重みを計算し直し（`core/blend_tree.py`）、合成を作り直す（`composition.compose_blend_tree`）
- 作り直しは SF-5 と同じ: ドラッグ中は作らず、止まって 0.15 秒後に 1 回。再生中でも効き、時刻と再生状態は保つ
- 雛形: 「2D の移動ツリーを作る」（`_F/_B/_L/_R` が 2 方向以上）、「1D の Locomotion を作る」（Idle と Run がある）
- パラメータの値は見ているだけの状態（animset には入れない）。ツリー自体の変更（雛形・パラメータ名の付け替え）は animset の変更なので Undo できる
"""

from __future__ import annotations

import copy

from maya import cmds
from PySide6 import QtCore, QtGui, QtWidgets

from tdrive import widgets as WG

from . import composition as K
from .core import blend_tree as BT
from .core import state_machine as M
from .ui_common import ComposeProgress

PAD_SIZE = 160
PAD_MARGIN = 10
RANGES = (("0 〜 1", (0.0, 1.0)), ("-1 〜 1", (-1.0, 1.0)))


class PadWidget(QtWidgets.QWidget):
    """2D パッド（D-Drive の AnimEditor と同じ 160 px）。モーションの位置を点で、いまの値を丸で描く。ドラッグで動かす。"""

    valueChanged = QtCore.Signal(float, float)  # 動かした（ドラッグ中も毎回）
    released = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(PAD_SIZE, PAD_SIZE)
        self.lo, self.hi = 0.0, 1.0
        self.x = self.y = 0.0
        self.points: list[tuple[float, float, str, float]] = []  # (x, y, 名前, 重み)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setToolTip("2D パッド: ドラッグでパラメータ X / Y を動かします（離して 0.15 秒後に合成を作り直します）。"
                        "青い点はモーションの位置、大きさは今の重み。範囲は右で 0〜1 / -1〜1 を切り替え。既定: 原点（0, 0）")

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
        for x, y, _name, w in self.points:
            c = self._to_px(x, y)
            r = 3.0 + 6.0 * w
            p.setPen(QtGui.QColor("#6fa8dc"))
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

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton:
            self._drag(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & QtCore.Qt.LeftButton:
            self._drag(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton:
            self.released.emit()


class BlendTreePanel(QtWidgets.QGroupBox):
    """Blend Tree パラメータの節。`BlendTab` が作って並べ、合成の名前・クリップ・スケルトン・メッセージ欄を渡す。"""

    def __init__(self, tab, comp_name: str) -> None:
        super().__init__("Blend Tree パラメータ（Unity の Blend Tree と同じ重みで、2D パッド / スライダーで確認）")
        self.tab = tab
        self.comp_name = comp_name  # ブレンドタブの合成の名前（遷移列 / 状態図の合成と同じ。1 つだけ持つ）
        self.values: dict[str, float] = {}
        self._building = False
        self.timer = QtCore.QTimer(self)  # パッドをドラッグしている間は作り直さず、止まってから 1 回（SF-5）
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(lambda *_: self.rebuild())
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
            sl.setToolTip(f"{'X' if i == 0 else 'Y'} パラメータの値（範囲は上の切替。既定: 0）。ドラッグを止めて 0.15 秒後に合成を作り直します")
            sl.valueChanged.connect(lambda *_, k=i: self._slider_moved(k))
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
        self._values_changed()

    def _pad_moved(self) -> None:
        tree = self.current_tree()
        if tree is None:
            return
        ps = BT.parameters(tree)
        self.values[ps[0]] = self.pad.x
        if len(ps) > 1:
            self.values[ps[1]] = self.pad.y
        self._sync_sliders()
        self._values_changed()

    def set_values(self, **kw: float) -> None:
        """パラメータの値を直接入れる（テスト・スクリプト用）。パッドとスライダーも合わせて作り直しを予約する。"""
        self.values.update(kw)
        tree = self.current_tree()
        if tree:
            self._load_values(tree)
            self._values_changed()

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
    def _values_changed(self) -> None:
        self._show_weights()
        self.timer.start()  # 止まって 0.15 秒後に 1 回

    def flush(self) -> None:
        """待っている作り直しがあれば今すぐ行う（テスト・合成を外す前に）。"""
        if self.timer.isActive():
            self.timer.stop()
            self.rebuild()

    def stop_timer(self) -> None:
        self.timer.stop()

    def timer_name(self) -> str:
        return "ブレンドツリーの作り直し待ち" if self.timer.isActive() else ""

    def rebuild(self) -> None:
        self.timer.stop()
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

