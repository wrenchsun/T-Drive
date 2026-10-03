"""ポーズタブ（docs/14 §5.4、F1-3）: 選んでいる点のポーズ（シェイプの重み・ボーンのずれ）を編集して保存する。

画面は「描く + 入力を渡す」だけ。編集中の値は `session.pose`（PosePresenter）が持ち、保存するまで文書へは書かれない。
操作は **セッションのコマンド**を呼ぶ（docs/15 §4.1）。例外はスライダーなどの編集中の値（`set_curve` / `set_bone`）と絞り込みで、
これらはセッション経由で呼ぶと同時にシーンへ当て直される。

## ボーンの回転の表示（Euler 角）
文書のボーンのずれ `BoneOffset.r` は**クォータニオン [x, y, z, w]**（親ボーンの空間で足す回転）。数値の欄にはそのまま出すと読めないので、
**Euler 角（度）で出す**。決まり:

- 回転の順は **XYZ**（Maya の既定の rotateOrder と同じ）。固定軸で X → Y → Z の順に回す = クォータニオンは `q = qz · qy · qx`
  （ハミルトン積。`qx` を最初に当てる）。`euler_xyz_to_quat` / `quat_to_euler_xyz` がこの式
- 範囲は X・Z が ±180°、Y が ±90°（Y は XYZ の真ん中の軸。±90° ちょうどはジンバルロックで X と Z が決まらないので、そのとき Z = 0 にする）
- 1 つの欄を編集したときは、**今のクォータニオンから出した Euler 角のその軸だけを置き換えて**クォータニオンに戻す
  （ほかの軸の欄の丸め誤差を書き戻さない）。平行移動の欄を編集しても回転は触らない
- スケールは欄を持たない（文書の値をそのまま残す）
"""

from __future__ import annotations

import dataclasses
import math
import traceback
import weakref
from pathlib import Path
from typing import Callable, Optional

from maya import cmds
from PySide6 import QtCore, QtGui, QtWidgets

from tdrive import lifecycle, project

from . import scene as scene_mod
from .core.model import BoneOffset
from .core.presenters import BoneRow, CurveRow, PoseView
from .session import FacialSessionError

MAX_BONE_ROWS = 60  # 作業セットで絞っていないとき、ボーンの行はこれだけまで（残りは絞り込みで探す）
OK_STYLE = "color: #9aa6b8;"
ERR_STYLE = "color: #ff8a80;"
EDITED_STYLE = "color: #f0c060; font-weight: bold;"  # 0 でない / 恒等でない行（Toon の「上書き中」と同じ色）
MISSING_STYLE = "color: #ff8a80;"
DIRTY_COLOR = "#ff9f1c"
LABEL_WIDTH = 150
T_RANGE = 1000.0  # 平行移動の欄の範囲（cm）
SLIDER_STEPS = 1000


# ---------------------------------------------------------------------------
# Euler 角 ⇔ クォータニオン（Maya 非依存。上の「ボーンの回転の表示」を参照）
# ---------------------------------------------------------------------------


def euler_xyz_to_quat(rx: float, ry: float, rz: float) -> tuple[float, float, float, float]:
    """Euler 角 [度]（X → Y → Z の順に固定軸で回す）→ クォータニオン [x, y, z, w]。q = qz · qy · qx。"""
    hx, hy, hz = (math.radians(a) * 0.5 for a in (rx, ry, rz))
    cx, sx, cy, sy, cz, sz = math.cos(hx), math.sin(hx), math.cos(hy), math.sin(hy), math.cos(hz), math.sin(hz)
    return (
        sx * cy * cz - cx * sy * sz,
        cx * sy * cz + sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
        cx * cy * cz + sx * sy * sz,
    )


def quat_to_euler_xyz(q) -> tuple[float, float, float]:
    """クォータニオン [x, y, z, w] → Euler 角 [度]（`euler_xyz_to_quat` の逆。Y は ±90° まで）。"""
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    x, y, z, w = (c / n for c in q)
    sinp = 2.0 * (w * y - z * x)
    sinp = max(-1.0, min(1.0, sinp))
    ry = math.asin(sinp)
    if abs(sinp) > 0.999999:  # ジンバルロック: X と Z が決まらない。Z = 0 にして X へ寄せる
        rx = 2.0 * math.atan2(x, w) * (1.0 if sinp > 0 else -1.0)
        rz = 0.0
    else:
        rx = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
        rz = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return (math.degrees(rx), math.degrees(ry), math.degrees(rz))


def _clear_layout(layout: QtWidgets.QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()


# ---------------------------------------------------------------------------
# 行
# ---------------------------------------------------------------------------


class _CurveRowWidgets:
    """シェイプ 1 本分: 名前 / スライダー / 数値 / 0 に戻す。値の表示は `show` だけで更新する（行は作り直さない）。"""

    def __init__(self, tab: "PoseTab", row: CurveRow) -> None:
        self.tab = tab
        self.name = row.name
        self.lo, self.hi = float(row.lo), float(row.hi)
        self.label = QtWidgets.QLabel(row.name)
        self.label.setFixedWidth(LABEL_WIDTH)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, SLIDER_STEPS)
        self.slider.setMinimumWidth(70)
        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setRange(self.lo, self.hi)
        self.spin.setDecimals(3)
        self.spin.setSingleStep(max((self.hi - self.lo) / 100.0, 0.001))
        self.spin.setMinimumWidth(70)
        self.spin.setKeyboardTracking(False)  # 打ち終わり（Enter / フォーカスが外れる）で反映。矢印は 1 回ごとに反映
        self.reset_btn = QtWidgets.QToolButton()
        self.reset_btn.setText("0")
        self.reset_btn.setToolTip("このシェイプを 0 に戻す")
        self.slider.valueChanged.connect(self._on_slider)
        self.spin.valueChanged.connect(self._on_spin)
        self.reset_btn.clicked.connect(self._on_reset)

    def widgets(self) -> list[QtWidgets.QWidget]:
        return [self.label, self.slider, self.spin, self.reset_btn]

    def _to_slider(self, v: float) -> int:
        return int(round((v - self.lo) / (self.hi - self.lo) * SLIDER_STEPS)) if self.hi > self.lo else 0

    def _on_slider(self, pos: int) -> None:
        v = self.lo + (self.hi - self.lo) * pos / SLIDER_STEPS
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        self._commit(round(v, 4))

    def _on_spin(self, v: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(self._to_slider(v))
        self.slider.blockSignals(False)
        self._commit(round(v, 4))

    def _on_reset(self, *_checked) -> None:
        self._commit(0.0)
        self.show(0.0)

    def _commit(self, v: float) -> None:
        res = self.tab.set_curve(self.name, v)
        if res is not None and res.clamped and res.value is not None:
            self.show(res.value)
        self.set_style(abs(v) > 1e-4, False)

    def set_style(self, edited: bool, missing: bool) -> None:
        self.label.setStyleSheet(MISSING_STYLE if missing else (EDITED_STYLE if edited else ""))

    def show(self, value: float) -> None:
        for w, fn in ((self.spin, lambda: self.spin.setValue(value)), (self.slider, lambda: self.slider.setValue(self._to_slider(value)))):
            w.blockSignals(True)
            fn()
            w.blockSignals(False)

    def refresh(self, row: CurveRow) -> None:
        self.show(row.value)
        self.set_style(row.edited, row.missing)
        tips = [row.name]
        if row.missing:
            tips.append("このモデルに無いシェイプです")
        tips.append(f"範囲 {row.lo:g} 〜 {row.hi:g}" + ("" if row.explicit_limit else "（既定）"))
        if not row.in_working_set:
            tips.append("作業セットの外")
        self.label.setToolTip(" / ".join(tips))


class _BoneRowWidgets:
    """ボーン 1 本分: 名前（ラジオ。選ぶとボタンの対象になる）/ 移動 XYZ（cm）/ 回転 XYZ（度）/ 戻す。"""

    def __init__(self, tab: "PoseTab", row: BoneRow, group: QtWidgets.QButtonGroup) -> None:
        self.tab = tab
        self.name = row.name
        self._labels: list[QtWidgets.QLabel] = []
        self.radio = QtWidgets.QRadioButton(row.name)
        group.addButton(self.radio)
        self.t = [self._spin(-T_RANGE, T_RANGE, 3, 0.01, f"移動 {a}（cm）", a) for a in "XYZ"]
        self.r = [self._spin(-180.0, 180.0, 2, 1.0, f"回転 {a}（度）", a) for a in "XYZ"]
        self.r[1].setRange(-90.0, 90.0)
        self.r[1].setToolTip("回転 Y（度）。Maya の回転順 XYZ の真ん中の軸なので ±90° までです")
        self.reset_btn = QtWidgets.QToolButton()
        self.reset_btn.setText("戻す")
        self.reset_btn.setToolTip("このボーンを元の姿勢へ戻す")
        for i, sp in enumerate(self.t):
            sp.valueChanged.connect(lambda v, k=i: self.tab.edit_bone(self.name, "t", k, v))
        for i, sp in enumerate(self.r):
            sp.valueChanged.connect(lambda v, k=i: self.tab.edit_bone(self.name, "r", k, v))
        self.reset_btn.clicked.connect(lambda *_: self.tab.reset_bone(self.name))

    @staticmethod
    def _spin(lo: float, hi: float, decimals: int, step: float, tip: str, axis: str) -> QtWidgets.QDoubleSpinBox:
        s = QtWidgets.QDoubleSpinBox()
        s.setPrefix(f"{axis}  ")
        s.setRange(lo, hi)
        s.setDecimals(decimals)
        s.setSingleStep(step)
        s.setKeyboardTracking(False)
        s.setToolTip(tip)
        s.setMinimumWidth(84)
        return s

    def add_to(self, grid: QtWidgets.QGridLayout, row: int) -> None:
        """3 行で 1 本分（名前と戻す / 移動 XYZ / 回転 XYZ）。狭いパネルでも横にはみ出さないように縦に積む。"""
        grid.addWidget(self.radio, row, 0, 1, 3)
        grid.addWidget(self.reset_btn, row, 3)
        for line, (tag, spins) in enumerate((("移動 cm", self.t), ("回転 °", self.r)), start=1):
            lab = QtWidgets.QLabel(tag)
            lab.setStyleSheet(OK_STYLE)
            grid.addWidget(lab, row + line, 0)
            for i, sp in enumerate(spins):
                grid.addWidget(sp, row + line, 1 + i)
            self._labels.append(lab)

    def refresh(self, row: BoneRow) -> None:
        rx, ry, rz = quat_to_euler_xyz(row.r)
        for sp, val in zip(self.t + self.r, (*row.t, rx, ry, rz)):
            sp.blockSignals(True)
            sp.setValue(val)
            sp.blockSignals(False)
        self.radio.setStyleSheet(MISSING_STYLE if row.missing else (EDITED_STYLE if row.edited else ""))
        tips = [row.name]
        if row.missing:
            tips.append("このモデルに無いボーンです")
        if not row.in_working_set:
            tips.append("作業セットの外")
        self.radio.setToolTip(" / ".join(tips))


# ---------------------------------------------------------------------------
# タブ
# ---------------------------------------------------------------------------

_live_tabs: "weakref.WeakSet[PoseTab]" = weakref.WeakSet()


def _detach_all() -> None:
    for tab in list(_live_tabs):
        try:
            tab.detach()
        except RuntimeError:
            pass


lifecycle.on_reload(_detach_all)


class PoseTab(QtWidgets.QWidget):
    """ポーズタブ。`refresh()` で全部描き直し、`detach()` で通知の購読をやめる。"""

    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self._generation = -1
        self._unsub: list[Callable[[], None]] = []
        self._driving = False  # 自分の操作中の通知では、行を作り直さない（スライダーの途中で崩れないように）
        self._detached = False
        self._pending = False
        self._failed = False
        self._curve_sig: Optional[tuple] = None
        self._bone_sig: Optional[tuple] = None
        self._curve_rows: dict[str, _CurveRowWidgets] = {}
        self._bone_rows: dict[str, _BoneRowWidgets] = {}
        self._bone_group = QtWidgets.QButtonGroup(self)
        self._bone_group.setExclusive(True)

        outer = QtWidgets.QVBoxLayout(self)
        self.header = QtWidgets.QLabel()
        self.header.setWordWrap(True)
        self.header.setTextFormat(QtCore.Qt.RichText)
        outer.addWidget(self.header)
        self.note = QtWidgets.QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(OK_STYLE)
        outer.addWidget(self.note)

        # ---- ボタン（保存・読み直す・ゼロ・左右反転・書き出し / 読み込み）
        row = QtWidgets.QHBoxLayout()
        self.btn_save = QtWidgets.QPushButton("保存（この点をキーにする）")
        self.btn_save.setToolTip("編集中のポーズをこの点へ保存します。この点はキー（緑）になります。全部 0 のポーズを保存すると点が消えます")
        self.btn_save.clicked.connect(self.on_save)
        self.btn_reload = QtWidgets.QPushButton("読み直す")
        self.btn_reload.setToolTip("編集中の値を捨てて、保存してあるポーズを読み直します")
        self.btn_reload.clicked.connect(self.on_reload)
        self.btn_zero = QtWidgets.QPushButton("ゼロに戻す")
        self.btn_zero.setToolTip("シェイプもボーンも全部 0（元の姿勢）にします。保存するまでデータは変わりません")
        self.btn_zero.clicked.connect(self.on_zero)
        self.btn_mirror = QtWidgets.QPushButton("左右反転")
        self.btn_mirror.setToolTip("編集中のポーズの左右を入れ替えます（_L ⇔ _R、ボーンのずれも鏡映）")
        self.btn_mirror.clicked.connect(self.on_mirror)
        for w in (self.btn_save, self.btn_reload, self.btn_zero, self.btn_mirror):
            row.addWidget(w)
        row.addStretch(1)
        outer.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.btn_export = QtWidgets.QPushButton("この点を書き出し…")
        self.btn_export.setToolTip("編集中のポーズを単独のポーズファイル（.fcpose.json）に書き出します")
        self.btn_export.clicked.connect(self.on_export)
        self.btn_import = QtWidgets.QPushButton("読み込み…")
        self.btn_import.setToolTip("ポーズファイルを編集中の値へ読み込みます（保存するまでデータは変わりません）")
        self.btn_import.clicked.connect(self.on_import)
        row.addWidget(self.btn_export)
        row.addWidget(self.btn_import)
        row.addStretch(1)
        outer.addLayout(row)

        # ---- スクロールする部分
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        body = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.body = body

        # 取り込み
        box = QtWidgets.QGroupBox("Maya で動かした値を取り込む")
        g = QtWidgets.QHBoxLayout(box)
        self.btn_capture = QtWidgets.QPushButton("シーンから取り込む")
        self.btn_capture.setToolTip(
            "Maya のシェイプエディタ・チャンネルボックス・回転 / 移動ツールで動かした値を、編集中のポーズへ取り込みます。取り込んだあと「保存」で点に書きます"
        )
        self.btn_capture.clicked.connect(self.on_capture)
        self.cb_capture_ws = QtWidgets.QCheckBox("作業セットだけ")
        self.cb_capture_ws.setChecked(True)
        self.cb_capture_ws.setToolTip("取り込み・読み込みで、作業セット（セットアップタブで選ぶ）のシェイプ・ボーンだけを対象にします。作業セットが空なら全部")
        g.addWidget(self.btn_capture)
        g.addWidget(self.cb_capture_ws)
        g.addStretch(1)
        bl.addWidget(box)

        # シェイプ
        self.curve_box = QtWidgets.QGroupBox("シェイプ")
        cv = QtWidgets.QVBoxLayout(self.curve_box)
        r = QtWidgets.QHBoxLayout()
        self.curve_filter = QtWidgets.QLineEdit()
        self.curve_filter.setPlaceholderText("絞り込み（名前の一部。大文字小文字は区別しません）")
        self.curve_filter.setClearButtonEnabled(True)
        self.curve_filter.textChanged.connect(self.on_curve_filter)
        self.cb_working = QtWidgets.QCheckBox("作業セットだけ")
        self.cb_working.setToolTip("作業セットのシェイプ・ボーンだけを一覧に出します（作業セットが空のときは全部）")
        self.cb_working.toggled.connect(self.on_working_only)
        r.addWidget(self.curve_filter, 1)
        r.addWidget(self.cb_working)
        cv.addLayout(r)
        self.curve_note = QtWidgets.QLabel()
        self.curve_note.setStyleSheet(OK_STYLE)
        self.curve_note.setWordWrap(True)
        cv.addWidget(self.curve_note)
        self.curve_grid = QtWidgets.QGridLayout()
        self.curve_grid.setColumnStretch(1, 1)
        cv.addLayout(self.curve_grid)
        bl.addWidget(self.curve_box)

        # ボーン
        self.bone_box = QtWidgets.QGroupBox("ボーン（ずれ。親ボーンから見た移動 cm / 回転 度）")
        bv = QtWidgets.QVBoxLayout(self.bone_box)
        r = QtWidgets.QHBoxLayout()
        self.bone_filter = QtWidgets.QLineEdit()
        self.bone_filter.setPlaceholderText("絞り込み（ボーン名の一部）")
        self.bone_filter.setClearButtonEnabled(True)
        self.bone_filter.textChanged.connect(self.on_bone_filter)
        r.addWidget(self.bone_filter, 1)
        bv.addLayout(r)
        r = QtWidgets.QHBoxLayout()
        self.btn_select_bone = QtWidgets.QPushButton("選択中のジョイントを Maya で選ぶ")
        self.btn_select_bone.setToolTip(
            "左の丸で選んだボーンを Maya で選択します。Maya の移動 / 回転ツールで動かしたら、上の「シーンから取り込む」を押してください"
        )
        self.btn_select_bone.clicked.connect(self.on_select_bone)
        self.btn_reset_bone = QtWidgets.QPushButton("このボーンを戻す")
        self.btn_reset_bone.clicked.connect(self.on_reset_selected_bone)
        self.btn_reset_bones = QtWidgets.QPushButton("ボーンを全部戻す")
        self.btn_reset_bones.setToolTip("ボーンのずれを全部元へ戻します（シェイプは触りません）")
        self.btn_reset_bones.clicked.connect(self.on_reset_bones)
        for w in (self.btn_select_bone, self.btn_reset_bone, self.btn_reset_bones):
            r.addWidget(w)
        r.addStretch(1)
        bv.addLayout(r)
        self.bone_note = QtWidgets.QLabel()
        self.bone_note.setStyleSheet(OK_STYLE)
        self.bone_note.setWordWrap(True)
        bv.addWidget(self.bone_note)
        self.bone_grid = QtWidgets.QGridLayout()
        self.bone_grid.setColumnStretch(1, 1)
        self.bone_grid.setColumnStretch(2, 1)
        self.bone_grid.setColumnStretch(3, 1)
        bv.addLayout(self.bone_grid)
        bl.addWidget(self.bone_box)
        bl.addStretch(1)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(OK_STYLE)
        outer.addWidget(self.status)

        self._flush_timer = QtCore.QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(0)
        self._flush_timer.timeout.connect(self._flush_now)

        session.listeners.append(self._on_session_changed)
        _live_tabs.add(self)
        self.refresh()

    # ------------------------------------------------------------------ 状態・通知
    def has_doc(self) -> bool:
        return self.session.presenters is not None

    def set_status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(ERR_STYLE if error else OK_STYLE)

    def _on_session_changed(self) -> None:
        if self._detached:
            return
        try:
            self.refresh()
        except RuntimeError:
            self.detach()

    def _on_pose_event(self, _event: str = "") -> None:
        """Presenter の変化（点の選択・編集中の値・ドキュメント）。自分で値を動かしている最中は何もしない。"""
        if self._driving or self._detached:
            return
        self._pending = True
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_now(self) -> None:
        if self._detached:
            return
        self._pending = False
        self.refresh()

    def flush(self) -> None:
        """まとめ待ちの描き直しがあれば今すぐ行う（テスト・スモーク用）。"""
        if self._pending:
            self._flush_timer.stop()
            self._flush_now()

    def _attach(self) -> None:
        if self._generation == self.session.generation and (self._unsub or not self.has_doc()):
            return
        for fn in self._unsub:
            fn()
        self._unsub = []
        self._generation = self.session.generation
        if self.has_doc():
            self._unsub.append(self.session.ctx.subscribe(self._on_pose_event))
            self._unsub.append(self.session.pose.subscribe(self._on_pose_event))

    # ------------------------------------------------------------------ 表示
    def refresh(self) -> None:
        """全部描き直す。データが無くても例外を出さない。"""
        if self._detached:
            return
        self._attach()
        if not self.has_doc():
            self._show_empty()
            return
        view = self.session.pose.view()
        self._sync_header(view)
        self._sync_controls(view)
        self._sync_curves(view)
        self._sync_bones(view)

    def _show_empty(self) -> None:
        self.header.setText("FacialController のデータが開かれていません（セットアップタブで新規作成するか、開いてください）")
        self.note.setText("")
        self.body.setEnabled(False)
        for b in (self.btn_save, self.btn_reload, self.btn_zero, self.btn_mirror, self.btn_export, self.btn_import):
            b.setEnabled(False)
        self._curve_sig = self._bone_sig = None
        _clear_layout(self.curve_grid)
        _clear_layout(self.bone_grid)
        self._curve_rows.clear()
        self._bone_rows.clear()

    def _header_html(self, selection, layer: str, dirty: bool) -> str:
        if selection is None:
            return "グリッドで点を選んでください（グリッドタブで点をクリックすると、その点のポーズをここで編集できます）"
        r, c = selection
        yaw, pitch = self.session.grid.angles_of(r, c)
        mark = f" <span style='color:{DIRTY_COLOR}; font-weight:bold;'>[未保存]</span>" if dirty else ""
        return f"<b>編集中:</b> レイヤー「{layer}」 点 R{r}, C{c}（Yaw {yaw:.1f}° / Pitch {pitch:.1f}°）{mark}"

    def _sync_header(self, view: PoseView) -> None:
        self.header.setText(self._header_html(view.selection, view.layer, view.dirty))
        if view.selection is not None and not self.session.editing:
            self.note.setText("今はシーンにこのポーズが当たっていません。スライダーを動かすと、シーンを基準姿勢にして当て直します。")
        else:
            self.note.setText("")
        self.note.setVisible(bool(self.note.text()))

    def _sync_controls(self, view: PoseView) -> None:
        ed = view.can_edit
        self.body.setEnabled(ed)
        self.btn_save.setEnabled(view.can_save)
        self.btn_reload.setEnabled(ed)
        self.btn_zero.setEnabled(ed)
        self.btn_mirror.setEnabled(view.can_mirror)
        self.btn_export.setEnabled(ed)
        self.btn_import.setEnabled(ed)
        self.cb_working.blockSignals(True)
        self.cb_working.setChecked(view.working_set_only)
        self.cb_working.blockSignals(False)
        for edit, text in ((self.curve_filter, view.curve_filter), (self.bone_filter, view.bone_filter)):
            if edit.text() != text:
                edit.blockSignals(True)
                edit.setText(text)
                edit.blockSignals(False)

    def _sync_curves(self, view: PoseView) -> None:
        sig = tuple((r.name, r.lo, r.hi) for r in view.curves)
        if sig != self._curve_sig:  # 一覧が変わったときだけ行を作り直す
            _clear_layout(self.curve_grid)
            self._curve_rows = {}
            for i, row in enumerate(view.curves):
                w = _CurveRowWidgets(self, row)
                self._curve_rows[row.name] = w
                for col, widget in enumerate(w.widgets()):
                    self.curve_grid.addWidget(widget, i, col)
            self._curve_sig = sig
        for row in view.curves:
            self._curve_rows[row.name].refresh(row)
        note = f"編集した値: {view.edited_curves} 本"
        if view.hidden_curves:
            note += f"（作業セットの外に 0 でない値が {view.hidden_curves} 本あります。「作業セットだけ」を切ると見えます）"
        if not view.curves:
            note = "表示できるシェイプがありません（作業セットが空で、シーンのシェイプも読めていない）"
        self.curve_note.setText(note)

    def _sync_bones(self, view: PoseView) -> None:
        rows = list(view.bones)
        shown = rows
        extra = 0
        if not view.working_set_active and len(rows) > MAX_BONE_ROWS:  # 絞っていないときは多すぎるので、編集済みを先に・上限つき
            shown = sorted(rows, key=lambda r: not r.edited)[:MAX_BONE_ROWS]
            extra = len(rows) - len(shown)
        sig = tuple(r.name for r in shown)
        if sig != self._bone_sig:
            keep = self.selected_bone()
            _clear_layout(self.bone_grid)
            for b in list(self._bone_group.buttons()):
                self._bone_group.removeButton(b)
            self._bone_rows = {}
            for i, row in enumerate(shown):
                w = _BoneRowWidgets(self, row, self._bone_group)
                self._bone_rows[row.name] = w
                w.add_to(self.bone_grid, i * 3)
            if keep in self._bone_rows:
                self._bone_rows[keep].radio.setChecked(True)
            self._bone_sig = sig
        for row in shown:
            self._bone_rows[row.name].refresh(row)
        note = f"編集した値: {view.edited_bones} 本"
        if extra:
            note += f"（ボーンが多いので {len(shown)} 本だけ出しています。残り {extra} 本は絞り込みで探してください）"
        if view.hidden_bones:
            note += f"（作業セットの外に動いているボーンが {view.hidden_bones} 本あります）"
        if not rows:
            note = "表示できるボーンがありません"
        self.bone_note.setText(note)

    def selected_bone(self) -> str:
        for name, w in self._bone_rows.items():
            if w.radio.isChecked():
                return name
        return ""

    def _after_edit(self) -> None:
        """自分の操作のあと: 見出しの [未保存] だけを更新する（行は作り直さない）。"""
        if self.has_doc():
            ctx = self.session.ctx
            self.header.setText(self._header_html(ctx.selection, ctx.layer.name, self.session.pose.dirty))

    # ------------------------------------------------------------------ 共通
    def _run(self, label: str, fn: Callable[[], object]):
        """セッションのコマンドを呼ぶ。失敗は画面の下の行に出し（`self._failed` が True）、例外は外へ出さない。"""
        self._failed = False
        try:
            return fn()
        except FacialSessionError as exc:
            self.set_status(str(exc), error=True)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"{label}でエラーが出ました: {exc}", error=True)
            lifecycle.report_error(f"ポーズタブ: {label}", traceback.format_exc(), once=False)
        self._failed = True
        return None

    def _show_result(self, res) -> None:
        if res is not None:
            self.set_status(res.message or ("完了しました" if res.ok else "できませんでした"), error=not res.ok)

    def _ensure_applied(self) -> None:
        """編集状態を抜けていたら（ベイクのあとなど）、編集中の値をシーンへ当て直す（基準姿勢に入る）。"""
        if not self.session.editing:
            self.session.apply_buffer_to_scene()

    # ------------------------------------------------------------------ 編集中の値（スライダー・数値）
    def set_curve(self, name: str, value: float):
        """シェイプの値を入れて、シーンへ当てる（セッション経由）。行の見た目の更新は呼んだ行がする。"""
        self._driving = True
        try:
            res = self._run("シェイプの編集", lambda: self.session.set_curve(name, value))
            if res is not None and not res.ok:
                self.set_status(res.message, error=True)
                return None
            if res is not None:
                self._run("シーンへ当てる", self._ensure_applied)
            self._after_edit()
            return res
        finally:
            self._driving = False

    def edit_bone(self, name: str, kind: str, axis: int, value: float) -> None:
        """ボーンの欄を 1 つ編集した。kind = "t"（移動）/ "r"（回転）。回転の変換は上の「ボーンの回転の表示」の約束。"""
        self._driving = True
        try:
            off = self.session.pose.bone_offset(name)
            if kind == "t":
                t = list(off.t)
                t[axis] = float(value)
                new = dataclasses.replace(off, t=(t[0], t[1], t[2]))
            else:
                eul = list(quat_to_euler_xyz(off.r))
                eul[axis] = float(value)
                new = dataclasses.replace(off, r=euler_xyz_to_quat(*eul))
            res = self._run("ボーンの編集", lambda: self.session.set_bone(name, new))
            if res is not None and not res.ok:
                self.set_status(res.message, error=True)
                return
            if res is not None:
                self._run("シーンへ当てる", self._ensure_applied)
            w = self._bone_rows.get(name)
            if w is not None:
                edited = not (
                    all(abs(x) <= 1e-3 for x in new.t)
                    and all(abs(x) <= 1e-4 for x in new.r[:3])
                    and abs(abs(new.r[3]) - 1.0) <= 1e-4
                    and all(abs(x - 1.0) <= 1e-4 for x in new.s)
                )
                w.radio.setStyleSheet(EDITED_STYLE if edited else "")
            self._after_edit()
        finally:
            self._driving = False

    def reset_bone(self, name: str) -> None:
        self._driving = True
        try:
            res = self._run("ボーンを戻す", lambda: self.session.set_bone(name, BoneOffset()))
            if res is not None and res.ok:
                self._run("シーンへ当てる", self._ensure_applied)
        finally:
            self._driving = False
        self.refresh()

    # ------------------------------------------------------------------ 絞り込み
    def on_curve_filter(self, text: str) -> None:
        if self.has_doc():
            self.session.pose.set_filter(text)
            self.flush()

    def on_bone_filter(self, text: str) -> None:
        if self.has_doc():
            self.session.pose.set_bone_filter(text)
            self.flush()

    def on_working_only(self, on: bool) -> None:
        if self.has_doc():
            self.session.pose.set_working_set_only(bool(on))
            self.flush()

    # ------------------------------------------------------------------ ボタン
    def on_save(self) -> None:
        self._show_result(self._run("保存", lambda: self.session.save_point()))

    def on_reload(self) -> None:
        self._run("読み直し", lambda: self.session.reload_pose())
        if not self._failed:
            self.set_status("保存してあるポーズを読み直しました")
        self.refresh()

    def on_zero(self) -> None:
        res = self._run("ゼロに戻す", lambda: self.session.zero_pose())
        self._show_result(res)
        if res is not None and res.ok:
            self.set_status("全部 0 に戻しました（保存するまでデータは変わりません）")
        self.refresh()

    def on_mirror(self) -> None:
        res = self._run("左右反転", lambda: self.session.mirror_pose())
        self._show_result(res)
        self.refresh()

    def on_reset_bones(self) -> None:
        res = self._run("ボーンを戻す", lambda: self.session.reset_bones())
        if res is not None and res.ok:
            self.set_status("ボーンを全部戻しました（保存するまでデータは変わりません）")
        else:
            self._show_result(res)
        self.refresh()

    def on_reset_selected_bone(self) -> None:
        name = self.selected_bone()
        if not name:
            self.set_status("戻すボーンを、左の丸で選んでください", error=True)
            return
        self.reset_bone(name)
        self.set_status(f"ボーン「{name}」を戻しました（保存するまでデータは変わりません）")

    def on_select_bone(self) -> None:
        """選んだボーンのジョイントを Maya で選択する（Maya の移動 / 回転ツールで動かして取り込むため）。"""
        name = self.selected_bone()
        if not name:
            self.set_status("Maya で選ぶボーンを、左の丸で選んでください", error=True)
            return
        joint = self._run("ジョイントを探す", lambda: self._find_joint(name))
        if joint is None:
            if not self._failed:
                self.set_status(f"ジョイント「{name}」がシーンにありません", error=True)
            return
        cmds.select(joint, replace=True)
        self.set_status(f"ジョイント「{name}」を Maya で選びました。回転 / 移動ツールで動かしたら「シーンから取り込む」を押してください")

    def _find_joint(self, name: str) -> Optional[str]:
        meshes = self.session.target_meshes()
        return scene_mod.find_joint(name, scene_mod.mesh_joints(meshes))

    def on_capture(self) -> None:
        wso = self.cb_capture_ws.isChecked()
        rep = self._run("取り込み", lambda: self.session.capture_from_scene(working_set_only=wso))
        if rep is None:
            return
        if not rep.ok:
            self.set_status(rep.message, error=True)
            return
        text = f"取り込みました: シェイプ {rep.curves_set} 本・ボーン {rep.bones_set} 本" + ("" if rep.changed else "（変化なし）")
        if rep.ignored:
            text += f"。作業セットの外なので取り込まなかった名前 {len(rep.ignored)} 個"
        if rep.unknown:
            text += f"。モデルに無い名前 {len(rep.unknown)} 個: " + "、".join(rep.unknown[:5])
        if rep.clamped:
            text += f"。可動域で丸めたシェイプ {len(rep.clamped)} 本"
        self.set_status(text + "。保存すると点に書かれます")
        self.refresh()

    # --- 書き出し / 読み込み（ファイルの選択は差し替えられる）---
    def default_export_path(self) -> str:
        d = self.session.doc
        sel = self.session.ctx.selection
        stem = f"{self.session.ctx.layer.name}_R{sel[0]}_C{sel[1]}" if sel else "pose"
        return str(Path(project.root()) / "facial" / (d.asset or "untitled") / "poses" / f"{stem}.fcpose.json")

    def choose_export_path(self, default: str) -> str:
        """書き出し先を聞く（キャンセルは ""）。"""
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "ポーズを書き出す", default, "FacialController ポーズ (*.fcpose.json)")
        return path

    def choose_import_path(self, start_dir: str) -> str:
        """読み込むファイルを聞く（キャンセルは ""）。"""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "ポーズを読み込む", start_dir, "FacialController ポーズ (*.fcpose.json *.json)")
        return path

    def on_export(self) -> None:
        if not self.has_doc():
            return
        path = self.choose_export_path(self.default_export_path())
        if not path:
            return
        out = self._run("書き出し", lambda: self.session.export_pose_file(path))
        if out is None:
            if not self._failed:
                self.set_status("ポーズが空なので書き出しませんでした（全部 0 のポーズは書き出しません）", error=True)
            return
        self.set_status(f"書き出しました: {out}")

    def on_import(self) -> None:
        if not self.has_doc():
            return
        start = str(Path(self.default_export_path()).parent)
        path = self.choose_import_path(start)
        if not path:
            return
        wso = self.cb_capture_ws.isChecked()
        rep = self._run("読み込み", lambda: self.session.import_pose_file(path, working_set_only=wso))
        if rep is None:
            return
        text = rep.message
        if rep.ignored:
            text += f"。作業セットの外なので読み込まなかった名前 {len(rep.ignored)} 個"
        if rep.unknown:
            text += f"。モデルに無い名前 {len(rep.unknown)} 個"
        self.set_status(text + "。保存すると点に書かれます" if rep.ok else text, error=not rep.ok)
        self.refresh()

    # ------------------------------------------------------------------ 後片付け
    def detach(self) -> None:
        """通知の購読をやめる（パネルを閉じる・リロードの前）。二重に呼んでも安全。"""
        self._detached = True
        self._flush_timer.stop()
        for fn in self._unsub:
            fn()
        self._unsub = []
        if self._on_session_changed in self.session.listeners:
            self.session.listeners.remove(self._on_session_changed)
        _live_tabs.discard(self)
