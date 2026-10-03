"""リップシンクタブ（docs/14 §5.8c）: 音素 × 感情 → 口のシェイプの対応表を作って、シーンで試す。

画面は「描く + 入力を渡す」だけ。状態は `session.lipsync`（LipSyncPresenter）が持ち、操作は **セッションのコマンドだけ**を呼ぶ（Undo つき）。

- 「使う」・全体の強さ・声量の設定（最小 / 最大 / から / まで）・追従の速さ（Unity だけで使う）
- 音素の一覧: 足す / 名前を変える / 削除（その音素の行も消えるので確認する。既定は「いいえ」）/ 上へ / 下へ
- 「プロファイルから作る」: 文書のプロファイルの対応（音素 → シェイプ名）から基本の行を作る
- 表（行 = 音素、列 = 基本 + 感情のレイヤー）: マスを押すとそのマスが編集の対象になる（ポーズタブのスライダーで編集して保存。ボーンは保存されない）。
  右クリック / 「このマスを空にする」で行を消す
- 「試す」: 音素の強さ・声量・感情の重みを動かすと、計算結果をシーンへ当てて見られる。データも元の値も変えない。
  タブを離れる・編集の対象を選ぶ・「試すのをやめる」で終わる
"""

from __future__ import annotations

import traceback
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from tdrive import lifecycle

from .core.presenters import (
    CONFIRM_CANCEL,
    SELECT_CANCELLED,
    SELECT_INVALID,
    SELECT_NEEDS_CONFIRM,
)
from .session import FacialSessionError
from .ui import DIM_STYLE, WARN_STYLE, ask_save_discard_cancel, ask_yes_no

CELL_W = 84
SELECTED_BG = "#5285a6"
INVALID_FG = "#ff8a80"
EMPTY_FG = "#7d8796"
SLIDER_STEPS = 100


class _TrySlider(QtWidgets.QWidget):
    """0〜1 のスライダー 1 本（名前・つまみ・値）。動かすと `changed`。"""

    changed = QtCore.Signal()

    def __init__(self, name: str, tip: str = "", parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.label = QtWidgets.QLabel(name)
        self.label.setMinimumWidth(56)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, SLIDER_STEPS)
        self.value_label = QtWidgets.QLabel("0.00")
        self.value_label.setMinimumWidth(34)
        h.addWidget(self.label)
        h.addWidget(self.slider, 1)
        h.addWidget(self.value_label)
        if tip:
            self.setToolTip(tip)
        self.slider.valueChanged.connect(lambda *_: self._on_value())

    def _on_value(self) -> None:
        self.value_label.setText(f"{self.value():.2f}")
        self.changed.emit()

    def value(self) -> float:
        return self.slider.value() / SLIDER_STEPS

    def set_value(self, v: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(max(0.0, min(1.0, float(v))) * SLIDER_STEPS)))
        self.slider.blockSignals(False)
        self.value_label.setText(f"{self.value():.2f}")


def _spin(lo: float, hi: float, step: float, tip: str, decimals: int = 2) -> QtWidgets.QDoubleSpinBox:
    w = QtWidgets.QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setDecimals(decimals)
    w.setKeyboardTracking(False)
    w.setToolTip(tip)
    return w


class LipsyncTab(QtWidgets.QWidget):
    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self._updating = False
        self._busy = 0  # セッションのコマンドを実行している間は、通知で描き直さない
        self._detached = False
        self._try_sig: Optional[tuple] = None
        self.phoneme_sliders: dict[str, _TrySlider] = {}
        self.emotion_sliders: dict[str, _TrySlider] = {}

        outer = QtWidgets.QVBoxLayout(self)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        body = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        # ---- データが無いとき
        self.empty_box = QtWidgets.QGroupBox("リップシンク")
        ev = QtWidgets.QVBoxLayout(self.empty_box)
        note = QtWidgets.QLabel(
            "音声の解析で得た「音素の強さ」を、口のシェイプの動きに変える対応表を作ります。\n"
            "まだこのデータにはリップシンクの設定がありません。「リップシンクを使う」で始めるか、"
            "「プロファイルから作る」で、プロファイルにある音素と口のシェイプの対応から基本の行を作ります。"
        )
        note.setWordWrap(True)
        note.setStyleSheet(DIM_STYLE)
        ev.addWidget(note)
        row = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton("リップシンクを使う")
        self.btn_start.setToolTip("リップシンクの設定を作ります（音素はあとで足せます）")
        self.btn_start.clicked.connect(lambda *_: self.on_start())
        self.btn_profile_empty = QtWidgets.QPushButton("プロファイルから作る")
        self.btn_profile_empty.setToolTip("プロファイルにある音素と口のシェイプの対応から、基本の行を作ります")
        self.btn_profile_empty.clicked.connect(lambda *_: self.on_from_profile())
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_profile_empty)
        row.addStretch(1)
        ev.addLayout(row)
        col.addWidget(self.empty_box)

        # ---- 設定
        self.main = QtWidgets.QWidget()
        mv = QtWidgets.QVBoxLayout(self.main)
        mv.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self.main)

        set_box = QtWidgets.QGroupBox("設定")
        g = QtWidgets.QGridLayout(set_box)
        self.cb_enabled = QtWidgets.QCheckBox("使う")
        self.cb_enabled.setToolTip("オフのあいだは、行があってもリップシンクは何もしません（試すも、Unity も）")
        self.cb_enabled.toggled.connect(lambda *_: self.on_enabled())
        g.addWidget(self.cb_enabled, 0, 0)
        self.summary = QtWidgets.QLabel()
        self.summary.setStyleSheet(DIM_STYLE)
        g.addWidget(self.summary, 0, 1, 1, 3)
        g.addWidget(QtWidgets.QLabel("全体の強さ"), 1, 0)
        self.strength = _spin(0.0, 1.0, 0.05, "リップシンク全体の強さ（0 = 何もしない、1 = 作った通り）")
        self.strength.valueChanged.connect(lambda *_: self.on_strength())
        g.addWidget(self.strength, 1, 1)
        g.addWidget(QtWidgets.QLabel("声量"), 2, 0)
        vol = QtWidgets.QGridLayout()
        self.vol_min = _spin(0.0, 100.0, 0.05, "声量がこの値以下のとき、口の大きさは「から」の倍率になります")
        self.vol_max = _spin(0.0, 100.0, 0.05, "声量がこの値以上のとき、口の大きさは「まで」の倍率になります")
        self.vol_from = _spin(0.0, 2.0, 0.05, "声量が「最小」のときの、口の大きさの倍率（0〜2）")
        self.vol_to = _spin(0.0, 2.0, 0.05, "声量が「最大」のとき（声量を渡さないときも）の、口の大きさの倍率（0〜2）")
        for i, (text, w) in enumerate((("最小", self.vol_min), ("最大", self.vol_max), ("から", self.vol_from), ("まで", self.vol_to))):
            vol.addWidget(QtWidgets.QLabel(text), i // 2, (i % 2) * 2)
            vol.addWidget(w, i // 2, (i % 2) * 2 + 1)
            w.valueChanged.connect(lambda *_: self.on_volume())
        g.addLayout(vol, 2, 1, 1, 3)
        vnote = QtWidgets.QLabel("声量が「最小」のとき口の大きさは「から」倍、「最大」のとき「まで」倍になります（間はなめらかにつながります）")
        vnote.setWordWrap(True)
        vnote.setStyleSheet(DIM_STYLE)
        g.addWidget(vnote, 3, 0, 1, 4)
        g.addWidget(QtWidgets.QLabel("追従の速さ"), 4, 0)
        self.follow = _spin(0.0, 1000.0, 1.0, "口が目標の形へ追いつく速さ（1 秒あたり。0 = すぐに追いつく）。Unity だけで使います", decimals=1)
        self.follow.valueChanged.connect(lambda *_: self.on_follow())
        g.addWidget(self.follow, 4, 1)
        fnote = QtWidgets.QLabel("Unity だけで使います（Maya の「試す」には効きません。0 = すぐに追いつく）")
        fnote.setWordWrap(True)
        fnote.setStyleSheet(DIM_STYLE)
        g.addWidget(fnote, 4, 2, 1, 2)
        mv.addWidget(set_box)

        # ---- 音素の一覧
        ph_box = QtWidgets.QGroupBox("音素")
        pv = QtWidgets.QVBoxLayout(ph_box)
        self.phoneme_list = QtWidgets.QListWidget()
        self.phoneme_list.setMaximumHeight(96)
        self.phoneme_list.setToolTip("音素の名前の一覧（並びが表の行の並びです）。選んでから、名前を変える・削除・並べ替えができます")
        self.phoneme_list.itemSelectionChanged.connect(lambda *_: self._sync_phoneme_buttons())
        pv.addWidget(self.phoneme_list)
        row = QtWidgets.QHBoxLayout()
        self.phoneme_edit = QtWidgets.QLineEdit()
        self.phoneme_edit.setPlaceholderText("音素の名前（例: A）")
        self.phoneme_edit.setMaximumWidth(150)
        self.phoneme_edit.returnPressed.connect(self.on_add)
        self.btn_add = QtWidgets.QPushButton("足す")
        self.btn_add.setToolTip("名前を入れた音素を、一覧の最後に足します")
        self.btn_add.clicked.connect(lambda *_: self.on_add())
        self.btn_rename = QtWidgets.QPushButton("名前を変える…")
        self.btn_rename.setToolTip("選んでいる音素の名前を変えます（その音素の行も付いていきます）")
        self.btn_rename.clicked.connect(lambda *_: self.on_rename())
        self.btn_delete = QtWidgets.QPushButton("削除…")
        self.btn_delete.setToolTip("選んでいる音素を削除します。その音素の行も消えます")
        self.btn_delete.clicked.connect(lambda *_: self.on_delete())
        self.btn_up = QtWidgets.QPushButton("上へ")
        self.btn_up.clicked.connect(lambda *_: self.on_move(-1))
        self.btn_down = QtWidgets.QPushButton("下へ")
        self.btn_down.clicked.connect(lambda *_: self.on_move(1))
        row.addWidget(self.phoneme_edit)
        row.addWidget(self.btn_add)
        row.addStretch(1)
        pv.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        for w in (self.btn_rename, self.btn_delete, self.btn_up, self.btn_down):
            row.addWidget(w)
        row.addStretch(1)
        pv.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.btn_profile = QtWidgets.QPushButton("プロファイルから作る")
        self.btn_profile.setToolTip("プロファイルにある音素と口のシェイプの対応から、基本の行を作ります（すでに基本の行がある音素はそのまま残します）")
        self.btn_profile.clicked.connect(lambda *_: self.on_from_profile())
        row.addWidget(self.btn_profile)
        row.addStretch(1)
        pv.addLayout(row)
        self.profile_result = QtWidgets.QLabel()
        self.profile_result.setWordWrap(True)
        self.profile_result.setStyleSheet(DIM_STYLE)
        self.profile_result.setVisible(False)
        pv.addWidget(self.profile_result)
        mv.addWidget(ph_box)

        # ---- 表
        tbl_box = QtWidgets.QGroupBox("対応表（行 = 音素、列 = 基本と感情）")
        tv = QtWidgets.QVBoxLayout(tbl_box)
        tnote = QtWidgets.QLabel(
            "マスを押すと、そのマスが編集の対象になります（ポーズタブでシェイプの値を作って保存します。ボーンは保存されません）。"
            "感情の列が空のマスは、基本の行がそのまま使われます"
        )
        tnote.setWordWrap(True)
        tnote.setStyleSheet(DIM_STYLE)
        tv.addWidget(tnote)
        self.table = QtWidgets.QTableWidget(0, 0)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.table.horizontalHeader().setDefaultSectionSize(CELL_W)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.on_table_menu)
        self.table.cellClicked.connect(lambda r, c: self.on_cell_clicked(r, c))
        self.table.setToolTip("マスを押すと編集の対象になります。右クリックで「このマスを空にする」")
        tv.addWidget(self.table)
        row = QtWidgets.QHBoxLayout()
        self.btn_clear = QtWidgets.QPushButton("このマスを空にする")
        self.btn_clear.setToolTip("選んでいるマスの行を消します（基本の行を消すと、その音素は口が動かなくなります）")
        self.btn_clear.clicked.connect(lambda *_: self.on_clear_clicked())
        row.addWidget(self.btn_clear)
        row.addStretch(1)
        tv.addLayout(row)
        self.target_label = QtWidgets.QLabel()
        self.target_label.setWordWrap(True)
        tv.addWidget(self.target_label)
        mv.addWidget(tbl_box)

        # ---- 試す
        self.try_box = QtWidgets.QGroupBox("試す")
        xv = QtWidgets.QVBoxLayout(self.try_box)
        xnote = QtWidgets.QLabel(
            "音素の強さ・声量・感情の重みを動かして、口がどうなるかをシーンで確かめます。データは変わりません"
            "（編集の対象は外れます。タブを離れる・「試すのをやめる」で元に戻ります）"
        )
        xnote.setWordWrap(True)
        xnote.setStyleSheet(DIM_STYLE)
        xv.addWidget(xnote)
        row = QtWidgets.QHBoxLayout()
        self.btn_try = QtWidgets.QPushButton("試しはじめる")
        self.btn_try.setToolTip("シーンを基準姿勢にして、スライダーの値を口のシェイプへ当てはじめます")
        self.btn_try.clicked.connect(lambda *_: self.on_try_start())
        self.btn_try_stop = QtWidgets.QPushButton("試すのをやめる")
        self.btn_try_stop.setToolTip("試した口の形をやめて、シーンを元の状態へ戻します")
        self.btn_try_stop.clicked.connect(lambda *_: self.on_try_stop())
        row.addWidget(self.btn_try)
        row.addWidget(self.btn_try_stop)
        row.addStretch(1)
        xv.addLayout(row)
        self.try_body = QtWidgets.QWidget()
        self.try_layout = QtWidgets.QVBoxLayout(self.try_body)
        self.try_layout.setContentsMargins(0, 0, 0, 0)
        self.try_ph_box = QtWidgets.QVBoxLayout()
        self.try_layout.addLayout(self.try_ph_box)
        vrow = QtWidgets.QHBoxLayout()
        self.cb_volume = QtWidgets.QCheckBox("声量を渡す")
        self.cb_volume.setToolTip("オフのあいだは声量なしで計算します（口の大きさは「まで」の倍率）")
        self.cb_volume.toggled.connect(lambda *_: self.on_try_changed())
        self.volume_slider = _TrySlider("声量", "試す声量（0〜1）")
        self.volume_slider.set_value(1.0)
        self.volume_slider.changed.connect(self.on_try_changed)
        vrow.addWidget(self.cb_volume)
        vrow.addWidget(self.volume_slider, 1)
        self.try_layout.addLayout(vrow)
        self.try_emo_box = QtWidgets.QVBoxLayout()
        self.try_layout.addLayout(self.try_emo_box)
        xv.addWidget(self.try_body)
        self.try_result = QtWidgets.QLabel()
        self.try_result.setWordWrap(True)
        self.try_result.setStyleSheet(DIM_STYLE)
        xv.addWidget(self.try_result)
        mv.addWidget(self.try_box)

        col.addStretch(1)
        self._stale = False  # 見えていないあいだに通知があった（見えたときに描き直す）
        self.session.state_listeners.append(self._on_state)
        self.refresh()

    # ------------------------------------------------------------------ 状態
    def has_doc(self) -> bool:
        return self.session.presenters is not None

    def set_status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(WARN_STYLE if error else DIM_STYLE)

    def _on_state(self) -> None:
        """セッションの軽い通知（編集状態の出入り・対象の選択）。試す・対象の表示だけ更新する。"""
        if self._detached or self._busy or self._updating:
            return
        try:
            if self.isVisible():
                self.refresh()
            else:
                self._stale = True
        except RuntimeError:
            self.detach()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._stale and not self._detached:
            self._stale = False
            self.refresh()

    # ------------------------------------------------------------------ 表示
    def refresh(self) -> None:
        if self._detached:
            return
        self._updating = True
        try:
            self._refresh()
        finally:
            self._updating = False

    def _refresh(self) -> None:
        if not self.has_doc():
            self.empty_box.setVisible(True)
            self.main.setVisible(False)
            for b in (self.btn_start, self.btn_profile_empty):
                b.setEnabled(False)
            return
        s = self.session
        view = s.lipsync.view()
        self._view = view
        self.empty_box.setVisible(not view.present)
        self.main.setVisible(view.present)
        self.btn_start.setEnabled(True)
        self.btn_profile_empty.setEnabled(True)
        if not view.present:
            return
        self.cb_enabled.setChecked(view.enabled)
        self.summary.setText(view.summary)
        for w, v in (
            (self.strength, view.strength),
            (self.vol_min, view.volume_min),
            (self.vol_max, view.volume_max),
            (self.vol_from, view.volume_from),
            (self.vol_to, view.volume_to),
            (self.follow, view.follow),
        ):
            if abs(w.value() - v) > 1e-9 and not w.hasFocus():
                w.blockSignals(True)
                w.setValue(v)
                w.blockSignals(False)
        self._fill_phonemes(view)
        self._fill_table(view)
        self._sync_target(view)
        self._sync_try(view)

    def _fill_phonemes(self, view) -> None:
        keep = self.selected_phoneme()
        self.phoneme_list.blockSignals(True)
        self.phoneme_list.clear()
        for i, name in enumerate(view.phonemes):
            row = view.rows[i]
            it = QtWidgets.QListWidgetItem(name)
            tip = f"行 {row.entries} 個"
            if not row.valid:
                tip = "名前が空、または重複しています（この音素は使われません）\n" + tip
                it.setForeground(QtGui.QBrush(QtGui.QColor(INVALID_FG)))
            it.setToolTip(tip)
            self.phoneme_list.addItem(it)
            if name == keep:
                self.phoneme_list.setCurrentItem(it)
        self.phoneme_list.blockSignals(False)
        self.btn_add.setEnabled(view.can_add)
        self.phoneme_edit.setEnabled(view.can_add)
        self.btn_add.setToolTip("名前を入れた音素を、一覧の最後に足します" if view.can_add else f"音素は最大 {view.limit} 個です")
        self._sync_phoneme_buttons()

    def _sync_phoneme_buttons(self) -> None:
        if not hasattr(self, "_view"):
            return
        name = self.selected_phoneme()
        n = len(self._view.phonemes)
        i = self._view.phonemes.index(name) if name in self._view.phonemes else -1
        self.btn_rename.setEnabled(i >= 0)
        self.btn_delete.setEnabled(i >= 0)
        self.btn_up.setEnabled(i > 0)
        self.btn_down.setEnabled(0 <= i < n - 1)

    def selected_phoneme(self) -> str:
        it = self.phoneme_list.currentItem()
        return it.text() if it is not None and it.isSelected() else ""

    def _fill_table(self, view) -> None:
        cols = view.columns
        t = self.table
        t.clear()
        t.setColumnCount(len(cols))
        t.setRowCount(len(view.rows))
        t.setHorizontalHeaderLabels([c.label for c in cols])
        t.setVerticalHeaderLabels([r.phoneme for r in view.rows])
        for ri, row in enumerate(view.rows):
            for ci, cell in enumerate(row.cells):
                if cell.has_entry:
                    text = f"{cell.curves} シェイプ"
                elif cols[ci].is_base:
                    text = "—"
                else:
                    text = "（基本）"
                if not cell.valid:
                    text = "⚠ " + text
                it = QtWidgets.QTableWidgetItem(text)
                it.setTextAlignment(QtCore.Qt.AlignCenter)
                it.setToolTip(cell.tooltip if cell.valid else "同じマスの行が 2 つあるなど、問題があります（検証タブで確かめてください）\n" + cell.tooltip)
                if not cell.valid:
                    it.setForeground(QtGui.QBrush(QtGui.QColor(INVALID_FG)))
                elif not cell.has_entry:
                    it.setForeground(QtGui.QBrush(QtGui.QColor(EMPTY_FG)))
                if cell.selected:
                    it.setBackground(QtGui.QBrush(QtGui.QColor(SELECTED_BG)))
                    it.setForeground(QtGui.QBrush(QtGui.QColor("#ffffff")))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                t.setItem(ri, ci, it)
        h = 32 + 24 * len(view.rows) + (16 if len(cols) * CELL_W + 60 > 440 else 0)
        t.setFixedHeight(min(260, max(74, h)))

    def _sync_target(self, view) -> None:
        s = self.session
        sel = view.selected
        self.btn_clear.setEnabled(bool(sel) and any(c.has_entry for r in view.rows for c in r.cells if c.selected))
        if sel is not None:
            dirty = "  [未保存]" if s.pose.dirty else ""
            self.target_label.setText(f"<b>編集の対象:</b> {s.lip_label(*sel)}（ポーズタブで編集します）{dirty}")
            self.target_label.setStyleSheet("")
        else:
            self.target_label.setText("マスを押すと、そのマスのポーズをポーズタブで作れます" if view.count else "音素がありません。「足す」か「プロファイルから作る」で作ります")
            self.target_label.setStyleSheet(DIM_STYLE)

    # ------------------------------------------------------------------ 試す
    def _sync_try(self, view) -> None:
        s = self.session
        active = s.lip_try_active
        emo_cols = [c.name for ci, c in enumerate(view.columns) if not c.is_base and any(r.cells[ci].has_entry for r in view.rows)]
        sig = (tuple(view.phonemes), tuple(emo_cols))
        if sig != self._try_sig:
            self._try_sig = sig
            self._rebuild_try(view.phonemes, emo_cols)
        self.try_body.setEnabled(active)
        self.btn_try.setEnabled(not active and bool(view.phonemes))
        self.btn_try_stop.setEnabled(active)
        if not active:  # 試していないあいだは、つまみを 0 に戻しておく
            for w in (*self.phoneme_sliders.values(), *self.emotion_sliders.values()):
                w.set_value(0.0)
            self.volume_slider.set_value(1.0)
            self.cb_volume.blockSignals(True)
            self.cb_volume.setChecked(False)
            self.cb_volume.blockSignals(False)
            self.try_result.setText("")
        if not view.phonemes:
            self.try_result.setText("音素を足すと試せます")

    def _rebuild_try(self, phonemes, emo_cols) -> None:
        for lay in (self.try_ph_box, self.try_emo_box):
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
        self.phoneme_sliders = {}
        self.emotion_sliders = {}
        for ph in phonemes:
            w = _TrySlider(ph, f"音素「{ph}」の強さ（0〜1）")
            w.changed.connect(self.on_try_changed)
            self.try_ph_box.addWidget(w)
            self.phoneme_sliders[ph] = w
        if emo_cols:
            lab = QtWidgets.QLabel("感情の重み（行がある感情だけ）")
            lab.setStyleSheet(DIM_STYLE)
            self.try_emo_box.addWidget(lab)
        for name in emo_cols:
            w = _TrySlider(name, f"感情「{name}」の重み（0〜1。感情の列の行が、基本の行に重なります）")
            w.changed.connect(self.on_try_changed)
            self.try_emo_box.addWidget(w)
            self.emotion_sliders[name] = w

    def ask_unsaved_choice(self, message: str = "") -> str:
        return ask_save_discard_cancel(self, "今の対象に、まだ保存していないポーズの編集があります。保存してから試しはじめますか？（破棄すると編集した内容は戻せません）")

    def on_try_start(self) -> bool:
        """「試す」を始める（未保存の編集があれば確認する）。始められたら True。"""
        s = self.session
        if not self.has_doc():
            return False
        was_editing = s.editing
        r = self._run("試しはじめる", lambda: s.lip_try_start())
        if r is None:
            self.refresh()
            return False
        if r.status == SELECT_NEEDS_CONFIRM:
            choice = self.ask_unsaved_choice(r.message)
            r = self._run("試しはじめる", lambda: s.lip_try_start(choice=choice))
            if r is None or r.status == SELECT_CANCELLED:
                self.set_status("試しはじめるのを取りやめました（今の編集はそのままです）")
                self.refresh()
                return False
        msg = "試しています。スライダーを動かすと、口の形がシーンに当たります"
        if not was_editing:
            msg += "（編集を始めました。シーンを基準姿勢にしています）"
        self.set_status(msg)
        self.refresh()
        return True

    def on_try_stop(self) -> None:
        if not self.has_doc():
            return
        if self._run("試すのをやめる", lambda: self.session.lip_try_stop()):
            self.set_status("試すのをやめました（シーンは元の編集状態に戻りました）")
        self.refresh()

    def on_try_changed(self) -> None:
        if self._updating or not self.has_doc() or not self.session.lip_try_active:
            return
        s = self.session
        ph = {n: w.value() for n, w in self.phoneme_sliders.items()}
        vol = self.volume_slider.value() if self.cb_volume.isChecked() else None
        emo = {n: w.value() for n, w in self.emotion_sliders.items()}
        out = self._run("試す", lambda: s.lip_try_set(ph, vol, emo))
        if out is None:
            return
        shown = "  ".join(f"{k} {v:.2f}" for k, v in sorted(out.items()) if abs(v) > 1e-6)
        self.try_result.setText("当てた値: " + (shown or "（口は動いていません）"))

    # ------------------------------------------------------------------ ダイアログ（テストで差し替えられる）
    def ask_text(self, title: str, label: str, value: str = "") -> Optional[str]:
        """文字を聞く（キャンセルは None）。"""
        text, ok = QtWidgets.QInputDialog.getText(self, title, label, QtWidgets.QLineEdit.Normal, value)
        return text if ok else None

    def ask_confirm(self, text: str) -> bool:
        return ask_yes_no(self, text, "リップシンク")  # 既定は「いいえ」

    def show_cell_menu(self, global_pos, ph: str, emotion: str, has_entry: bool) -> None:
        menu = QtWidgets.QMenu(self)
        act = menu.addAction("このマスを空にする")
        act.setEnabled(has_entry)
        chosen = menu.exec(global_pos)
        if chosen is act:
            self.clear_cell(ph, emotion)

    # ------------------------------------------------------------------ 共通
    def _run(self, label: str, fn: Callable[[], object]):
        self._busy += 1
        try:
            return fn()
        except FacialSessionError as exc:
            self.set_status(str(exc), error=True)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"{label}でエラーが出ました: {exc}", error=True)
            lifecycle.report_error(f"リップシンク: {label}", traceback.format_exc(), once=False)
        finally:
            self._busy -= 1
        return None

    def _show(self, res, ok_text: str = "") -> None:
        if res is None:
            return
        self.set_status(res.message or (ok_text if res.ok else "できませんでした"), error=not res.ok)

    # ------------------------------------------------------------------ 設定
    def on_start(self) -> None:
        if not self.has_doc():
            return
        res = self._run("リップシンクを使う", lambda: self.session.set_lipsync_enabled(True))
        self.refresh()
        if res is not None and res.ok:
            self.set_status("リップシンクを使います。音素を足して、マスを選んで口の形を作ってください")

    def on_enabled(self) -> None:
        if self._updating or not self.has_doc():
            return
        on = self.cb_enabled.isChecked()
        res = self._run("使う / 使わない", lambda: self.session.set_lipsync_enabled(on))
        self.refresh()
        if res is not None and res.ok:
            self.set_status("リップシンクを使います" if on else "リップシンクを使わない設定にしました（行は残ります）")

    def on_strength(self) -> None:
        if self._updating or not self.has_doc():
            return
        v = float(self.strength.value())
        res = self._run("全体の強さ", lambda: self.session.set_lipsync_strength(v))
        if res is not None and not res.ok:
            self._show(res)
            self.refresh()
        elif res is not None:
            self.set_status(f"全体の強さを {v:.2f} にしました")

    def on_volume(self) -> None:
        if self._updating or not self.has_doc():
            return
        a, b, c, d = (float(w.value()) for w in (self.vol_min, self.vol_max, self.vol_from, self.vol_to))
        res = self._run("声量の設定", lambda: self.session.set_lipsync_volume(a, b, c, d))
        if res is not None and not res.ok:
            self._show(res)
            self.refresh()
        elif res is not None:
            self.set_status("声量の設定を変えました")

    def on_follow(self) -> None:
        if self._updating or not self.has_doc():
            return
        v = float(self.follow.value())
        res = self._run("追従の速さ", lambda: self.session.set_lipsync_follow(v))
        if res is not None and not res.ok:
            self._show(res)
            self.refresh()
        elif res is not None:
            self.set_status(f"追従の速さを {v:g} にしました（Unity だけで使います）")

    # ------------------------------------------------------------------ 音素
    def on_add(self) -> None:
        if not self.has_doc():
            return
        name = self.phoneme_edit.text()
        res = self._run("音素を足す", lambda: self.session.add_lip_phoneme(name))
        if res is not None and res.ok:
            self.phoneme_edit.clear()
        self.refresh()
        self._show(res)

    def on_rename(self) -> None:
        name = self.selected_phoneme()
        if not name or not self.has_doc():
            self.set_status("名前を変える音素を、一覧で選んでください", error=True)
            return
        new = self.ask_text("音素の名前を変える", f"音素「{name}」の新しい名前", name)
        if new is None:
            self.set_status("名前を変えるのを取りやめました")
            return
        res = self._run("音素の名前を変える", lambda: self.session.rename_lip_phoneme(name, new))
        self.refresh()
        self._show(res)

    def on_delete(self) -> None:
        name = self.selected_phoneme()
        if not name or not self.has_doc():
            self.set_status("削除する音素を、一覧で選んでください", error=True)
            return
        s = self.session
        view = s.lipsync.view()
        row = next((r for r in view.rows if r.phoneme == name), None)
        n = row.entries if row is not None else 0
        text = f"音素「{name}」を削除します。"
        text += f"\n\nこの音素の行（{n} 個）も削除されます。" if n else "\n\nこの音素には行がありません。"
        if view.selected is not None and view.selected[0] == name and s.pose.dirty:
            text += "\n\n編集中のポーズ（保存していない分）も失われます。"
        text += "\n\n削除しますか？"
        if not self.ask_confirm(text):
            self.set_status("削除を取りやめました")
            return
        res = self._run("音素の削除", lambda: s.remove_lip_phoneme(name))
        self.refresh()
        self._show(res, "削除しました")

    def on_move(self, delta: int) -> None:
        name = self.selected_phoneme()
        if not name or not self.has_doc():
            return
        view = self.session.lipsync.view()
        if name not in view.phonemes:
            return
        to = view.phonemes.index(name) + int(delta)
        res = self._run("音素の並べ替え", lambda: self.session.move_lip_phoneme(name, to))
        self.refresh()
        if res is not None and res.ok and res.code != "unchanged":
            self.set_status(f"音素「{name}」を動かしました")
            for i in range(self.phoneme_list.count()):
                if self.phoneme_list.item(i).text() == name:
                    self.phoneme_list.setCurrentRow(i)
                    break

    def profile_summary(self, res) -> str:
        """「プロファイルから作る」の結果の文章（作った / 残した / モデルに無い / 複数あった）。"""
        lines = [res.message] if res.message else []
        if res.kept:
            lines.append("すでに基本の行があるので残した音素: " + "、".join(res.kept))
        if res.missing:
            lines.append("モデルに無いシェイプ（入れていません）: " + "、".join(f"{p}→{sh}" for p, sh in res.missing))
        if res.ambiguous:
            lines.append("同じ名前のシェイプが複数あったので先頭を選びました: " + "、".join(f"{p}→{sh}" for p, _name, sh in res.ambiguous))
        return "\n".join(lines)

    def on_from_profile(self) -> None:
        if not self.has_doc():
            return
        s = self.session
        prof = s.profile
        self.profile_result.setVisible(False)
        if prof is None:
            self.set_status("このデータにはプロファイルが選ばれていません（セットアップタブで選べます）", error=True)
            return
        if not prof.lip_sync:
            self.set_status(f"プロファイル「{prof.name}」には、リップシンクの対応（音素 → シェイプ）がありません", error=True)
            return
        res = self._run("プロファイルから作る", lambda: s.create_lipsync_from_profile())
        self.refresh()
        if res is None:
            return
        self.profile_result.setText(self.profile_summary(res) or "作れませんでした")
        self.profile_result.setVisible(True)
        self._show(res)

    # ------------------------------------------------------------------ 表のマス
    def cell_at(self, row: int, col: int) -> Optional[tuple[str, str]]:
        view = self.session.lipsync.view()
        if not (0 <= row < len(view.rows) and 0 <= col < len(view.columns)):
            return None
        return view.rows[row].phoneme, view.columns[col].name

    def on_cell_clicked(self, row: int, col: int) -> None:
        if self._updating or not self.has_doc():
            return
        cell = self.cell_at(row, col)
        if cell is not None:
            self.select_cell(*cell)

    def select_cell(self, phoneme: str, emotion: str = "") -> bool:
        """マスを編集の対象にする（未保存の編集があれば確認する）。選べたら True。"""
        s = self.session
        was_editing = s.editing
        r = self._run("マスの選択", lambda: s.select_lip_cell(phoneme, emotion))
        if r is None:
            self.refresh()
            return False
        if r.status == SELECT_NEEDS_CONFIRM:
            choice = self.ask_switch_choice(r.message)
            r = self._run("マスの選択", lambda: s.select_lip_cell(phoneme, emotion, choice=choice))
            if r is None or r.status == SELECT_CANCELLED or choice == CONFIRM_CANCEL:
                self.set_status("切り替えを取りやめました（今の編集はそのままです）")
                self.refresh()
                return False
        if r.status == SELECT_INVALID:
            self.set_status(r.message, error=True)
            self.refresh()
            return False
        msg = f"{s.lip_label(phoneme, emotion)}を編集の対象にしました"
        if r.saved:
            msg = "保存して移りました。" + msg
        if not was_editing:
            msg += "。編集を始めました（シーンを基準姿勢にしています。ポーズタブで編集できます）"
        self.set_status(msg)
        self.refresh()
        return True

    def ask_switch_choice(self, message: str = "") -> str:
        return ask_save_discard_cancel(self, "今の対象に、まだ保存していないポーズの編集があります。保存してから移りますか？（破棄すると編集した内容は戻せません）")

    def on_table_menu(self, pos) -> None:
        it = self.table.itemAt(pos)
        if it is None or not self.has_doc():
            return
        cell = self.cell_at(it.row(), it.column())
        if cell is None:
            return
        view = self.session.lipsync.view()
        has = view.rows[it.row()].cells[it.column()].has_entry
        self.show_cell_menu(self.table.viewport().mapToGlobal(pos), cell[0], cell[1], has)

    def on_clear_clicked(self) -> None:
        sel = self.session.lipsync.view().selected if self.has_doc() else None
        if sel is None:
            self.set_status("空にするマスを、表で選んでください", error=True)
            return
        self.clear_cell(*sel)

    def clear_cell(self, phoneme: str, emotion: str = "") -> None:
        res = self._run("マスを空にする", lambda: self.session.clear_lip_cell(phoneme, emotion))
        self.refresh()
        if res is not None:
            if res.code == "unchanged":
                self.set_status("そのマスにはもともと行がありません")
            else:
                self.set_status(f"{self.session.lip_label(phoneme, emotion)}を空にしました")

    # ------------------------------------------------------------------ 後片付け
    def detach(self) -> None:
        """通知の購読をやめる（パネルを閉じる・リロードの前）。二重に呼んでも安全。"""
        self._detached = True
        if self._on_state in self.session.state_listeners:
            self.session.state_listeners.remove(self._on_state)
