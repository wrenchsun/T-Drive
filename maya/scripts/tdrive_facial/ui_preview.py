"""プレビューのグループ（グリッドタブの中。docs/14 §5.8）: カメラ連動の補正を、Maya のビューポートでそのまま確かめる。

## なぜグリッドタブの中か
カメラを動かして角度を変える・点をクリックしてカメラを合わせる、という操作はグリッドタブでする。補正が効いている様子を見るのも
同じ場所がいちばん手数が少ない（タブを行き来しない）。ヘッダー（常に見えている）に置くには操作が多すぎる（作る・消す・A/B・カメラ・
感情の重み・手動角度・キーに焼く）ので、グリッドタブの下に畳める箱として置いた。

画面は「描く + 入力を渡す」だけ。操作はすべて `session.preview_*`（`session.py`）を呼ぶ。

- [プレビューを作る / 作り直す] [消す]: rig（`tdFacialPreview_<asset>`）と式を作る・消す。編集中でも使える（セッションが一度抜けて戻る）
- [補正あり / なし]: A/B 比較（rig の enable）
- カメラ: モデルパネルのカメラ + 「今のビューのカメラ」。プレビューがあれば、選ぶとすぐつなぎ替える
- 強さ（alpha）・誇張（exaggeration）・パース補正（perspective）・感情の重み（レイヤーごと）: rig のアトリビュートを動かす。キーが打ってあるものは動かせない（「キーあり」と出す）
  - パース補正: 広角で寄ったときの奥行きの補正の強さ。パース補正のシェイプ（`Persp_K{n}`）を配線していないときは動かせない。
    今の軸の値（カメラの距離 cm / 縦の画角 度）は角度の表示の横に出る
  - 誇張: 重み 1 を超えるポーズをベイクしてできる `_Ex` シェイプ（誇張用）の効き具合。`_Ex` が無いときは動かせない
  - 重みをカメラの距離で決めているレイヤーは、感情のスライダーを動かせない（距離で決まる）。距離は角度の表示の横に出る
- 手動の角度: カメラを使わず Yaw / Pitch を数値で指定する（角度を決め打ちで確かめる）
- [キーに焼く…]: 時間範囲を評価して FC_* にキーを打つ（レンダリング・Unity 以外への持ち出し用）。式は外れる
- 「格子やベイクが変わりました。作り直してください」: 作ったあとでデータが変わったとき。自動の作り直しが保留されたとき（編集中）も出る
"""

from __future__ import annotations

import traceback
from functools import partial
from typing import Optional

from maya import cmds
from PySide6 import QtCore, QtWidgets

from tdrive import lifecycle

from .session import FacialSessionError
from .ui import DIM_STYLE, WARN_STYLE, YAW_PLUS_HELP, ask_yes_no

CURRENT_VIEW = "今のビューのカメラ"
SLIDER_STEPS = 100


class TimeRangeDialog(QtWidgets.QDialog):
    """「キーに焼く」の時間範囲（開始・終了・ステップ・終わったらプレビュー用ノードも消すか）。"""

    def __init__(self, parent: Optional[QtWidgets.QWidget], start: float, end: float) -> None:
        super().__init__(parent)
        self.setWindowTitle("プレビューをキーに焼く")
        f = QtWidgets.QFormLayout(self)
        self.start = self._spin(start)
        self.end = self._spin(end)
        self.step = self._spin(1.0, 0.01)
        self.start.setToolTip("焼き始めるフレーム（既定は再生範囲の開始）")
        self.end.setToolTip("焼き終わるフレーム（既定は再生範囲の終了）")
        self.step.setToolTip("何フレームごとに評価してキーを打つか（1 = 毎フレーム）")
        f.addRow("開始フレーム", self.start)
        f.addRow("終了フレーム", self.end)
        f.addRow("ステップ", self.step)
        self.remove_rig = QtWidgets.QCheckBox("焼いたあと、プレビュー用ノード（強さ・感情のキーも）を消す")
        f.addRow(self.remove_rig)
        note = QtWidgets.QLabel(
            "FC_* の重みにキーを打ち、プレビューの式を外します（キーだけで同じ補正になります）。\n"
            "Maya の元に戻す（Ctrl+Z）1 回で戻せます。"
        )
        note.setWordWrap(True)
        note.setStyleSheet(DIM_STYLE)
        f.addRow(note)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        f.addRow(buttons)
        self.error = QtWidgets.QLabel()
        self.error.setStyleSheet(WARN_STYLE)
        f.addRow(self.error)

    @staticmethod
    def _spin(value: float, lo_step: float = 1.0) -> QtWidgets.QDoubleSpinBox:
        w = QtWidgets.QDoubleSpinBox()
        w.setRange(-100000.0, 100000.0)
        w.setDecimals(2)
        w.setSingleStep(lo_step)
        w.setValue(value)
        return w

    def _on_accept(self) -> None:
        if self.end.value() < self.start.value():
            self.error.setText("終了フレームが開始フレームより前です")
            return
        if self.step.value() <= 0:
            self.error.setText("ステップは 0 より大きくしてください")
            return
        self.accept()

    def values(self) -> tuple[float, float, float, bool]:
        return self.start.value(), self.end.value(), self.step.value(), self.remove_rig.isChecked()


class PreviewGroup(QtWidgets.QGroupBox):
    """プレビューの操作一式。`refresh()` で全部描き直し、`tick()` で（カメラを追う）角度の表示だけを更新する。"""

    def __init__(self, session, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__("プレビュー（カメラを回すと補正が追従）", parent)
        self.session = session
        self._updating = False
        self._emotion_rows: dict[str, tuple[QtWidgets.QLabel, QtWidgets.QSlider, QtWidgets.QLabel]] = {}
        self._emotion_sig: Optional[tuple] = None  # None = まだ行を作っていない（感情レイヤーが無くても強さ・誇張の行は作る）
        self._detached = False
        self._drag = 0  # スライダーをドラッグしている間は、通知で描き直さない（つまみが跳ねる）

        v = QtWidgets.QVBoxLayout(self)
        self.state_label = QtWidgets.QLabel()
        self.state_label.setWordWrap(True)
        v.addWidget(self.state_label)

        row = QtWidgets.QHBoxLayout()
        self.btn_build = QtWidgets.QPushButton("プレビューを作る")
        self.btn_build.setToolTip("ベイク済みの FC_* を、カメラの角度に合わせて自動で動かす仕掛けを作ります（作り直しても強さ・感情のキーは残ります）")
        self.btn_build.clicked.connect(lambda *_: self.on_build())
        self.btn_delete = QtWidgets.QPushButton("消す")
        self.btn_delete.setToolTip("プレビュー用のノードを消します（FC_* の重みは 0 に戻ります）")
        self.btn_delete.clicked.connect(lambda *_: self.on_delete())
        self.btn_ab = QtWidgets.QPushButton("補正あり")
        self.btn_ab.setCheckable(True)
        self.btn_ab.setChecked(True)
        self.btn_ab.setToolTip("オン = 補正あり / オフ = 補正なし（A/B 比較）")
        self.btn_ab.toggled.connect(self.on_ab_toggled)
        for w in (self.btn_build, self.btn_delete, self.btn_ab):
            row.addWidget(w)
        row.addStretch(1)
        v.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("カメラ"))
        self.camera_combo = QtWidgets.QComboBox()
        self.camera_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.camera_combo.setMinimumContentsLength(10)
        self.camera_combo.setToolTip("補正を計算するカメラ。プレビューがあるときは、選ぶとすぐつなぎ替えます")
        self.camera_combo.activated.connect(self.on_camera_activated)
        row.addWidget(self.camera_combo, 1)
        v.addLayout(row)

        self.stale_note = QtWidgets.QLabel("格子やベイクが変わりました。作り直してください")
        self.stale_note.setWordWrap(True)
        self.stale_note.setStyleSheet(WARN_STYLE)
        v.addWidget(self.stale_note)
        self.edit_note = QtWidgets.QLabel("編集中は補正が止まっています（シーンが基準姿勢のため）。編集を終えると再開します")
        self.edit_note.setWordWrap(True)
        self.edit_note.setStyleSheet(DIM_STYLE)
        v.addWidget(self.edit_note)
        self.warn_note = QtWidgets.QLabel()
        self.warn_note.setWordWrap(True)
        self.warn_note.setStyleSheet(WARN_STYLE)
        v.addWidget(self.warn_note)

        # 強さ・感情
        self.sliders = QtWidgets.QGridLayout()
        self.sliders.setColumnStretch(1, 1)
        v.addLayout(self.sliders)
        self.alpha_label = QtWidgets.QLabel("強さ")
        self.alpha_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.alpha_slider.setRange(0, SLIDER_STEPS)
        self.alpha_slider.setToolTip("補正全体の強さ（0 = 補正なし、1 = そのまま）")
        self.alpha_value = QtWidgets.QLabel()
        self.alpha_slider.valueChanged.connect(partial(self.on_slider, "alpha"))
        self._watch_drag(self.alpha_slider)
        self.ex_label = QtWidgets.QLabel("誇張")
        self.ex_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.ex_slider.setRange(0, SLIDER_STEPS)
        self.ex_slider.setToolTip(
            "誇張の強さ（0 = 誇張なし、1 = ポーズに入れた誇張の通り）。重みが 1 を超えるポーズをベイクすると、"
            "その分が誇張用のシェイプ（_Ex）に入ります。誇張用のシェイプが無いときは動かせません"
        )
        self.ex_value = QtWidgets.QLabel()
        self.ex_slider.valueChanged.connect(partial(self.on_slider, "exaggeration"))
        self._watch_drag(self.ex_slider)
        self.persp_label = QtWidgets.QLabel("パース補正")
        self.persp_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.persp_slider.setRange(0, SLIDER_STEPS)
        self.persp_slider.setToolTip(
            "パース補正の強さ（0 = 補正なし、1 = 作った通り）。広角で寄ったときの奥行きを押さえる補正で、カメラの距離 / 画角に応じて動きます。"
            "ベイク済みのパース補正のキーがプレビューに入っていないときは動かせません"
        )
        self.persp_value = QtWidgets.QLabel()
        self.persp_slider.valueChanged.connect(partial(self.on_slider, "perspective"))
        self._watch_drag(self.persp_slider)

        # 手動の角度
        row = QtWidgets.QHBoxLayout()
        self.manual_check = QtWidgets.QCheckBox("角度を手動で決める")
        self.manual_check.setToolTip("オンにすると、カメラの代わりに下の Yaw / Pitch で補正を計算します（決め打ちで確かめたいとき）")
        self.manual_check.toggled.connect(self.on_manual_toggled)
        self.manual_yaw = self._angle_spin("Yaw", -180.0, 180.0)
        self.manual_pitch = self._angle_spin("Pitch", -90.0, 90.0)
        self.manual_yaw.setToolTip("カメラの代わりに使う左右の角度。" + YAW_PLUS_HELP)
        self.manual_pitch.setToolTip("カメラの代わりに使う上下の角度（+Pitch = 上から見る）")
        self.manual_yaw.valueChanged.connect(partial(self.on_manual_angle, "manualYaw"))
        self.manual_pitch.valueChanged.connect(partial(self.on_manual_angle, "manualPitch"))
        row.addWidget(self.manual_check)
        row.addWidget(self.manual_yaw)
        row.addWidget(self.manual_pitch)
        row.addStretch(1)
        v.addLayout(row)
        self.manual_hint = QtWidgets.QLabel("Yaw: " + YAW_PLUS_HELP)  # 手動の角度の向き（グリッドタブの「カメラの角度」と同じ言い方）
        self.manual_hint.setWordWrap(True)
        self.manual_hint.setStyleSheet(DIM_STYLE)
        v.addWidget(self.manual_hint)

        row = QtWidgets.QHBoxLayout()
        self.btn_keys = QtWidgets.QPushButton("キーに焼く…")
        self.btn_keys.setToolTip("時間範囲を評価して FC_* にキーを打ちます（プレビューの式は外れます）")
        self.btn_keys.clicked.connect(lambda *_: self.on_bake_keys())
        row.addWidget(self.btn_keys)
        self.angle_label = QtWidgets.QLabel()
        self.angle_label.setStyleSheet(DIM_STYLE)
        row.addWidget(self.angle_label, 1)
        v.addLayout(row)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(DIM_STYLE)
        v.addWidget(self.status)
        self.refresh()

    def _watch_drag(self, slider: QtWidgets.QSlider) -> None:
        slider.sliderPressed.connect(self._drag_start)
        slider.sliderReleased.connect(self._drag_end)

    def _drag_start(self) -> None:
        self._drag += 1

    def _drag_end(self) -> None:
        self._drag = max(0, self._drag - 1)

    def on_state(self) -> None:
        """セッションの軽い通知（編集状態の出入り・プレビューの作成など）。スライダーのドラッグ中は何もしない。"""
        if self._detached or self._drag:
            return
        if self.window().isVisible() and not self.isVisible():
            # 画面は出ているのに、このタブが隠れている: 読み直さない（スライダー 1 目盛りごとに全部読み直して重い。M-11）。見えたとき 1 回だけ
            self._stale = True
            return
        try:
            self.refresh()
        except RuntimeError:
            self._detached = True

    def showEvent(self, event) -> None:  # noqa: N802 (Qt)
        super().showEvent(event)
        if getattr(self, "_stale", False) and not self._detached:
            self._stale = False
            self.refresh()

    @staticmethod
    def _angle_spin(prefix: str, lo: float, hi: float) -> QtWidgets.QDoubleSpinBox:
        w = QtWidgets.QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(1)
        w.setSingleStep(5.0)
        w.setPrefix(f"{prefix} ")
        w.setSuffix("°")
        w.setKeyboardTracking(False)
        return w

    # ============================================================ ダイアログ（差し替えられる）
    def ask_time_range(self, start: float, end: float) -> Optional[tuple[float, float, float, bool]]:
        dlg = TimeRangeDialog(self, start, end)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        return dlg.values()

    def ask_clear_keys(self, text: str) -> bool:
        return ask_yes_no(self, text, "プレビュー")

    # ============================================================ 表示
    def set_status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(WARN_STYLE if error else DIM_STYLE)

    def has_doc(self) -> bool:
        return self.session.presenters is not None

    def refresh(self) -> None:
        """全部描き直す（データ・プレビューの状態が変わったとき）。ドラッグ中のスライダーは呼ばない。"""
        if self._detached:
            return
        self._updating = True
        try:
            self._refresh()
        finally:
            self._updating = False

    def _refresh_cameras(self, keep) -> None:
        self.camera_combo.clear()
        self.camera_combo.addItem(CURRENT_VIEW, None)
        for c in self.session.model_cameras():
            self.camera_combo.addItem(c, c)
        i = self.camera_combo.findData(keep)
        self.camera_combo.setCurrentIndex(max(i, 0))

    def _refresh(self) -> None:
        s = self.session
        if not self.has_doc():
            self.setEnabled(False)
            self.state_label.setText("データが開かれていません")
            return
        self.setEnabled(True)
        st = s.preview_status()
        doc = s.doc
        can_build = bool(doc.asset) and doc.target is not None and bool(doc.target.mesh)
        exists = st.state != "none"
        live = st.state in ("live", "stale")
        self.btn_build.setText("作り直す" if exists else "プレビューを作る")
        self.btn_build.setEnabled(can_build)
        self.btn_delete.setEnabled(exists)
        self.btn_ab.setEnabled(live)
        self.btn_ab.setChecked(st.enabled if exists else True)
        self.btn_ab.setText("補正あり" if self.btn_ab.isChecked() else "補正なし")
        self.btn_keys.setEnabled(st.state == "live")
        self.state_label.setText(
            {
                "none": "プレビューはまだありません。作ると、カメラを動かしたときに補正がシーンに現れます（ベイク済みの FC_* を使います）",
                "live": f"プレビュー動作中（カメラ: {st.camera or '—'}）",
                "stale": "プレビューは動いていますが、古い状態です",
                "keys": "キーに焼いた状態です（式は外れています）。もう一度「作り直す」を押すと式が戻ります",
            }[st.state]
        )
        self.stale_note.setVisible(st.state == "stale")
        self.edit_note.setVisible(exists and st.editing)
        warn = " / ".join(st.warnings)
        self.warn_note.setText(warn)
        self.warn_note.setVisible(bool(warn))

        cur = self.camera_combo.currentData()
        self._refresh_cameras(cur)
        if st.camera:
            i = self.camera_combo.findData(st.camera)
            if i >= 0 and exists:
                self.camera_combo.setCurrentIndex(i)

        # 強さ・感情のスライダー（行の構成は感情レイヤーが変わったときだけ作り直す）
        sig = tuple((attr, name in st.distance_layers) for name, attr, _v, _k in st.emotions)
        if sig != self._emotion_sig:
            self._rebuild_emotion_rows(st)
        self._set_slider(self.alpha_slider, self.alpha_value, st.alpha, st.alpha_keyed, live)
        self._set_slider(self.ex_slider, self.ex_value, st.exaggeration, st.exaggeration_keyed, live and st.has_extreme)
        if exists and not st.has_extreme:
            self.ex_value.setText(f"{st.exaggeration:.2f}（誇張用のシェイプなし）")
        self._set_slider(self.persp_slider, self.persp_value, st.perspective, st.perspective_keyed, live and st.has_perspective)
        if exists and not st.has_perspective:
            self.persp_value.setText(f"{st.perspective:.2f}（パース補正のシェイプなし）")
        for name, attr, value, keyed in st.emotions:
            label, slider, vl = self._emotion_rows[attr]
            self._set_slider(slider, vl, value, keyed, live)
            if name in st.distance_layers:  # 重みはカメラの距離で決まる: emotion_ は使われない
                slider.setEnabled(False)
                vl.setText("距離で決まる" + (f"（今 {st.out_distance:.0f} cm）" if st.out_distance is not None else ""))
        self.manual_check.setEnabled(live and not st.manual_keyed)
        self.manual_check.setChecked(st.use_manual)
        for w, val in ((self.manual_yaw, st.manual_yaw), (self.manual_pitch, st.manual_pitch)):
            w.setValue(val)
            w.setEnabled(live and st.use_manual and not st.manual_keyed)
        self._update_angle(st)

    def _rebuild_emotion_rows(self, st) -> None:
        while self.sliders.count():
            item = self.sliders.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._emotion_rows = {}
        self.sliders.addWidget(self.alpha_label, 0, 0)
        self.sliders.addWidget(self.alpha_slider, 0, 1)
        self.sliders.addWidget(self.alpha_value, 0, 2)
        self.sliders.addWidget(self.ex_label, 1, 0)
        self.sliders.addWidget(self.ex_slider, 1, 1)
        self.sliders.addWidget(self.ex_value, 1, 2)
        self.sliders.addWidget(self.persp_label, 2, 0)
        self.sliders.addWidget(self.persp_slider, 2, 1)
        self.sliders.addWidget(self.persp_value, 2, 2)
        for i, (name, attr, _v, _k) in enumerate(st.emotions, start=3):
            by_distance = name in st.distance_layers
            label = QtWidgets.QLabel(f"感情 {name}")
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(0, SLIDER_STEPS)
            slider.setToolTip(
                f"レイヤー「{name}」は重みをカメラの距離で決めます（レイヤータブの「重みの出どころ」）。このスライダーは使われません"
                if by_distance
                else f"レイヤー「{name}」の補正の重み（0〜1）。Unity では感情カーブから入ります"
            )
            vl = QtWidgets.QLabel()
            slider.valueChanged.connect(partial(self.on_slider, attr))
            self._watch_drag(slider)
            self.sliders.addWidget(label, i, 0)
            self.sliders.addWidget(slider, i, 1)
            self.sliders.addWidget(vl, i, 2)
            self._emotion_rows[attr] = (label, slider, vl)
        self._emotion_sig = tuple((attr, n in st.distance_layers) for n, attr, _v, _k in st.emotions)

    @staticmethod
    def _set_slider(slider: QtWidgets.QSlider, value_label: QtWidgets.QLabel, value: float, keyed: bool, usable: bool) -> None:
        slider.blockSignals(True)
        slider.setValue(int(round(value * SLIDER_STEPS)))
        slider.blockSignals(False)
        slider.setEnabled(usable and not keyed)
        value_label.setText(f"{value:.2f}" + ("（キーあり）" if keyed else ""))

    def _update_angle(self, st=None) -> None:
        if self.session.preview_rig_node() is None:
            self.angle_label.setText("")
            return
        rig = self.session.preview_rig_node()
        try:
            yaw = cmds.getAttr(f"{rig}.outYaw")
            pitch = cmds.getAttr(f"{rig}.outPitch")
        except (RuntimeError, ValueError):
            self.angle_label.setText("")
            return
        text = f"使っている角度: Yaw {yaw:.1f}° / Pitch {pitch:.1f}°"
        persp = self.session.perspective_readout()
        if persp:
            text += " / " + persp
        try:
            if cmds.attributeQuery("outDistance", node=rig, exists=True):  # 距離で重みを決めるレイヤーがあるとき
                text += f" / 距離 {cmds.getAttr(f'{rig}.outDistance'):.0f} cm"
        except (RuntimeError, ValueError):
            pass
        self.angle_label.setText(text)

    def tick(self) -> None:
        """グリッドタブのカメラ追従のタイマーから呼ばれる（約 10 回 / 秒）。角度の表示だけを更新する。"""
        if self._detached or not self.has_doc():
            return
        try:
            self._update_angle()
        except RuntimeError:
            pass

    # ============================================================ 操作
    def _call(self, label: str, fn):
        try:
            return fn()
        except FacialSessionError as exc:
            self.set_status(str(exc), error=True)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"{label}でエラーが出ました: {exc}", error=True)
            lifecycle.report_error(f"プレビュー: {label}", traceback.format_exc(), once=False)
        return None

    def _camera_choice(self) -> Optional[str]:
        """つなぐカメラ（長い名前）。「今のビューのカメラ」はその時点のビューのカメラを解決する。"""
        data = self.camera_combo.currentData()
        return self.session.camera_transform(data)

    def on_build(self) -> None:
        if not self.has_doc():
            return
        s = self.session
        rep = self._call("プレビューの作成", lambda: s.preview_build(self._camera_choice()))
        if rep is None:
            self.refresh()
            return
        if rep.warnings and self.ask_clear_keys(
            "FC_* に打たれたキーのために、配線できなかったシェイプがあります:\n" + "\n".join(rep.warnings[:6])
            + "\n\nそのキーを消して作り直しますか？（キーに焼いたものが消えます）"
        ):
            self._call("キーの削除", s.preview_clear_keys)
            rep = self._call("プレビューの作成", lambda: s.preview_build(self._camera_choice())) or rep
        self.refresh()
        msg = f"プレビューを作りました（ターゲット {rep.targets} 本"
        if rep.perspective_targets:
            msg += f"、パース補正 {rep.perspective_targets} 本"
        msg += "）"
        if rep.targets == 0:
            msg += "。配線するシェイプがありません（ベイクしてから作り直してください）"
        if rep.warnings:
            msg += f"。警告 {len(rep.warnings)} 件"
        self.set_status(msg, error=rep.targets == 0)

    def on_delete(self) -> None:
        if not self.has_doc():
            return
        done = self._call("プレビューの削除", self.session.preview_delete)
        self.refresh()
        self.set_status("プレビューを消しました" if done else "プレビューはありません")

    def on_ab_toggled(self, on: bool) -> None:
        if self._updating or not self.has_doc():
            return
        self._call("A/B の切り替え", lambda: self.session.preview_set_enabled(bool(on)))
        self.btn_ab.setText("補正あり" if on else "補正なし")
        self.set_status("補正あり" if on else "補正なし（元の形）")

    def on_camera_activated(self, _index: int = 0) -> None:
        if self._updating or not self.has_doc() or not self.session.preview_exists():
            return
        cam = self._call("カメラの切り替え", lambda: self.session.preview_set_camera(self._camera_choice()))
        if cam is not None:
            self.set_status(f"カメラを {cam.split('|')[-1]} にしました")
        self.refresh()

    def on_slider(self, attr: str, pos: int) -> None:
        """強さ・感情のスライダー。ドラッグ 1 回ごとに rig のアトリビュートだけを書く（タブは作り直さない）。"""
        if self._updating or not self.has_doc():
            return
        v = pos / SLIDER_STEPS
        got = self._call("値の設定", lambda: self.session.preview_set_attr(attr, v))
        label = (
            self.alpha_value
            if attr == "alpha"
            else self.ex_value
            if attr == "exaggeration"
            else self.persp_value
            if attr == "perspective"
            else self._emotion_rows[attr][2]
        )
        if got is not None:
            label.setText(f"{got:.2f}")

    def on_manual_toggled(self, on: bool) -> None:
        if self._updating or not self.has_doc():
            return
        self._call("手動角度", lambda: self.session.preview_set_attr("useManual", bool(on)))
        self.refresh()

    def on_manual_angle(self, attr: str, value: float) -> None:
        if self._updating or not self.has_doc():
            return
        self._call("手動角度", lambda: self.session.preview_set_attr(attr, float(value)))
        self._update_angle()

    def on_bake_keys(self) -> None:
        if not self.has_doc():
            return
        s = self.session
        if s.preview_state() != "live":
            self.set_status("キーに焼くには、最新のプレビューが要ります（作り直してください）", error=True)
            return
        start = cmds.playbackOptions(query=True, minTime=True)
        end = cmds.playbackOptions(query=True, maxTime=True)
        got = self.ask_time_range(float(start), float(end))
        if got is None:
            self.set_status("キーに焼くのを取りやめました")
            return
        a, b, step, remove_rig = got
        res = self._call("キーに焼く", lambda: s.preview_bake_to_keys(a, b, step, remove_rig))
        self.refresh()
        if res is not None:
            self.set_status(
                f"キーに焼きました: {a:g}〜{b:g} フレーム（{len(res['frames'])} フレーム）、シェイプ {len(res['keyed'])} 本。プレビューの式は外れました"
            )

    # ============================================================ 後片付け
    def detach(self) -> None:
        self._detached = True
