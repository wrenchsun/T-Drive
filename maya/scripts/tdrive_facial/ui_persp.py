"""パース補正の箱（グリッドタブの中。docs/14 §5.8b）: 広角で寄ったときの奥行きを押さえる補正の設定と、キーの一覧・編集。

画面は「描く + 入力を渡す」だけ。状態は `session.perspective`（PerspectivePresenter）が持ち、操作は **セッションのコマンドだけ**を呼ぶ
（Undo・古い FC_* の削除・プレビューの作り直しが付く）。

- 「使う」「軸」「強さ」: パース補正を使うか / 軸（カメラの距離 cm か、縦の画角 度）/ 強さ（0〜1）
- キーの一覧: 値・ポーズの有無・ベイクの状態。行を選ぶと**そのキーが編集の対象**になる（格子の点の選択は外れ、ポーズタブがそのキーのポーズを編集する）。
  「カメラも動かす」がオンなら、カメラを顔の正面に置き、軸が距離ならその距離へ、画角ならその画角へ合わせる
- 「キーを足す」「値を変える…」「削除…」。削除すると後ろのキーの番号が詰まるので、確認してから行う（既定は「いいえ」）
- 箱は畳める（キーが無いときは最初は畳まれている。キーを選ぶと開く）
"""

from __future__ import annotations

import traceback
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from tdrive import lifecycle

from .core.presenters import (
    BAKE_BAKED,
    BAKE_CHANGED,
    BAKE_NONE,
    BAKE_UNBAKED,
    SELECT_CANCELLED,
    SELECT_INVALID,
    SELECT_NEEDS_CONFIRM,
)
from .session import FacialSessionError
from .ui import DIM_STYLE, WARN_STYLE, ask_save_discard_cancel, ask_yes_no

COL_VALUE, COL_POSE, COL_BAKE = range(3)
HEADERS = ("値", "ポーズ", "ベイク")
BAKE_SHORT = {
    BAKE_NONE: "（作らない）",
    BAKE_UNBAKED: "未ベイク",
    BAKE_CHANGED: "変更あり",
    BAKE_BAKED: "ベイク済み",
}
BAKE_COLOR = {BAKE_UNBAKED: "#ffe14d", BAKE_CHANGED: "#ff7ad9"}  # グリッドの枠と同じ色（黄 = 未ベイク / 桃 = ベイク後に変更あり）
AXIS_ITEMS = (("distance", "距離（cm）"), ("fov", "画角（度）"))


class PerspectiveGroup(QtWidgets.QWidget):
    """パース補正の箱。`refresh()` で全部描き直し、`detach()` で通知の購読をやめる。"""

    def __init__(self, session, move_camera: Optional[Callable[[], bool]] = None, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.session = session
        self._move_camera = move_camera or (lambda: False)
        self._updating = False
        self._busy = 0  # セッションのコマンドを実行している間は、通知で描き直さない（途中の状態を描かない・クリックの最中に一覧を作り直さない）
        self._detached = False
        self._generation = -1
        self._unsub: list[Callable[[], None]] = []
        self._user_toggled = False  # 開閉を手で変えたら、自動の開閉をやめる
        self._rows: list = []

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.toggle = QtWidgets.QToolButton()
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.toggle.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.toggle.setStyleSheet("QToolButton { text-align: left; font-weight: bold; padding: 4px; }")
        self.toggle.setToolTip("パース補正の箱を開閉します")
        self.toggle.toggled.connect(self.on_toggled)
        outer.addWidget(self.toggle)

        self.box = QtWidgets.QGroupBox()
        outer.addWidget(self.box)
        v = QtWidgets.QVBoxLayout(self.box)

        note = QtWidgets.QLabel(
            "広角で顔に寄ったとき（鼻が大きく・耳が小さく見える）だけ、奥行きを押さえる補正を足します。"
            "カメラの距離（または画角）ごとに「キー」を置き、そのキーのポーズを作ります。ポーズが空のキーは「補正なし」の範囲になります"
        )
        note.setWordWrap(True)
        note.setStyleSheet(DIM_STYLE)
        v.addWidget(note)

        row = QtWidgets.QHBoxLayout()
        self.cb_enabled = QtWidgets.QCheckBox("使う")
        self.cb_enabled.setToolTip("オフのあいだは、キーがあっても補正しません（プレビューにも Unity にも入りません）")
        self.cb_enabled.toggled.connect(lambda *_: self.on_enabled())
        row.addWidget(self.cb_enabled)
        row.addWidget(QtWidgets.QLabel("軸"))
        self.axis_combo = QtWidgets.QComboBox()
        for data, text in AXIS_ITEMS:
            self.axis_combo.addItem(text, data)
        self.axis_combo.setToolTip(
            "補正を切り替える物差し。距離 = カメラと顔の中心の距離（cm。パースの強さを実際に決めるので、ふつうはこちら）/ "
            "画角 = カメラの縦の画角（度。広角のレンズのときだけ補正したいとき）。軸を変えると、キーの値の数字はそのままなので見直してください"
        )
        self.axis_combo.activated.connect(self.on_axis_activated)
        row.addWidget(self.axis_combo)
        row.addStretch(1)
        v.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("強さ"))
        self.strength = QtWidgets.QDoubleSpinBox()
        self.strength.setRange(0.0, 1.0)
        self.strength.setSingleStep(0.05)
        self.strength.setDecimals(2)
        self.strength.setKeyboardTracking(False)
        self.strength.setToolTip("パース補正全体の強さ（0 = 補正なし、1 = 作った通り）。Unity の調整値・Timeline のクリップでも変えられます")
        self.strength.valueChanged.connect(self.on_strength)
        row.addWidget(self.strength)
        row.addStretch(1)
        self.summary = QtWidgets.QLabel()
        self.summary.setStyleSheet(DIM_STYLE)
        row.addWidget(self.summary)
        v.addLayout(row)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(len(HEADERS))
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.setToolTip("行を選ぶと、そのキーが編集の対象になります（ポーズタブでポーズを作ります）")
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnWidth(COL_VALUE, 90)
        self.tree.setColumnWidth(COL_POSE, 110)
        self.tree.itemClicked.connect(self.on_item_clicked)
        self.tree.itemActivated.connect(self.on_item_clicked)
        v.addWidget(self.tree)

        row = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("キーを足す")
        self.btn_add.setToolTip("新しいキーを足します（値は今のキーと重ならない提案から。あとで変えられます）。足したキーは編集の対象になります")
        self.btn_add.clicked.connect(lambda *_: self.on_add())
        self.btn_value = QtWidgets.QPushButton("値を変える…")
        self.btn_value.setToolTip("選んでいるキーの値（距離 cm / 画角 度）を変えます。ポーズと番号は変わらないので、焼き直しは要りません")
        self.btn_value.clicked.connect(lambda *_: self.on_set_value())
        self.btn_delete = QtWidgets.QPushButton("削除…")
        self.btn_delete.setToolTip("選んでいるキーを削除します。後ろのキーの番号が詰まるので、削除したあとは再ベイクが要ります")
        self.btn_delete.clicked.connect(lambda *_: self.on_delete())
        for w in (self.btn_add, self.btn_value, self.btn_delete):
            row.addWidget(w)
        row.addStretch(1)
        v.addLayout(row)

        self.target_label = QtWidgets.QLabel()
        self.target_label.setWordWrap(True)
        v.addWidget(self.target_label)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(DIM_STYLE)
        v.addWidget(self.status)
        self.refresh()

    # ------------------------------------------------------------------ 状態
    def has_doc(self) -> bool:
        return self.session.presenters is not None

    def set_status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(WARN_STYLE if error else DIM_STYLE)

    def _attach(self) -> None:
        """Presenter は新規作成・開くたびに作り直される。作り直されたら購読を付け直す。"""
        if self._generation == self.session.generation and (self._unsub or not self.has_doc()):
            return
        for fn in self._unsub:
            fn()
        self._unsub = []
        self._generation = self.session.generation
        if self.has_doc():
            self._unsub.append(self.session.perspective.subscribe(self._on_presenter_event))

    def _on_presenter_event(self, _event: str = "") -> None:
        """データ・ベイクの状態・選択の変化。選択だけの変化（グリッドの点を選んだなど）でも一覧の選択を合わせる。"""
        if self._detached or self._updating or self._busy:
            return
        try:
            self.refresh()
        except RuntimeError:  # Qt のオブジェクトが先に破棄された
            self.detach()

    # ------------------------------------------------------------------ 表示
    def refresh(self) -> None:
        if self._detached:
            return
        self._attach()
        self._updating = True
        try:
            self._refresh()
        finally:
            self._updating = False

    def _refresh(self) -> None:
        doc = self.has_doc()
        self.box.setEnabled(doc)
        if not doc:
            self.toggle.setText("▼ パース補正")
            self.tree.clear()
            self.target_label.setText("")
            return
        s = self.session
        view = s.perspective.view()
        self._rows = view.keys
        if not self._user_toggled:  # キーがあるあいだは開いておく（無ければ畳む）。選んでいるキーがあれば必ず開く
            want = view.count > 0 or view.selected is not None
            if self.toggle.isChecked() != want:
                self.toggle.blockSignals(True)
                self.toggle.setChecked(want)
                self.toggle.blockSignals(False)
        elif view.selected is not None and not self.toggle.isChecked():
            self.toggle.blockSignals(True)
            self.toggle.setChecked(True)
            self.toggle.blockSignals(False)
        self.box.setVisible(self.toggle.isChecked())
        arrow = "▼" if self.toggle.isChecked() else "▶"
        self.toggle.setText(f"{arrow} パース補正（{view.summary}）")

        self.cb_enabled.setChecked(view.enabled)
        i = self.axis_combo.findData(view.axis)
        self.axis_combo.setCurrentIndex(max(i, 0))
        self.strength.setValue(view.strength)
        self.summary.setText(f"キー {view.count} / {view.limit}")

        self.tree.clear()
        for r in view.keys:
            pose = "あり" if r.has_pose else "なし（補正なし）"
            it = QtWidgets.QTreeWidgetItem([f"{r.index + 1}: {r.value:g} {view.value_unit}", pose, BAKE_SHORT.get(r.bake, r.bake_text)])
            tip = r.tooltip + (f"\nポーズ: シェイプ {r.curves} 本・ボーン {r.bones} 本" if r.has_pose else "")
            tip += ("\n" + r.weight_hint if r.weight_hint else "") + "\n" + r.bake_text
            if not r.valid:
                tip = "値が軸に合っていない、または同じ値のキーが先にあります（このキーは使われません）\n" + tip
                it.setForeground(COL_VALUE, QtGui.QBrush(QtGui.QColor("#ff8a80")))
            for c in range(len(HEADERS)):
                it.setToolTip(c, tip)
            color = BAKE_COLOR.get(r.bake)
            if color:
                it.setForeground(COL_BAKE, QtGui.QBrush(QtGui.QColor(color)))
            it.setData(0, QtCore.Qt.UserRole, r.index)
            self.tree.addTopLevelItem(it)
            if r.index == view.selected:
                self.tree.setCurrentItem(it)
        self.tree.setFixedHeight(min(190, max(80, 30 + 21 * view.count)))  # 行数に合わせる（上限あり）
        if view.selected is None:
            self.tree.clearSelection()
            self.tree.setCurrentItem(None)
        sel = view.selected is not None
        self.btn_add.setEnabled(view.can_add)
        self.btn_value.setEnabled(sel)
        self.btn_delete.setEnabled(sel)
        if sel:
            self.target_label.setText(f"<b>編集の対象:</b> {s.key_label(view.selected)}（ポーズタブで編集します）")
            self.target_label.setStyleSheet("")
        else:
            self.target_label.setText("キーを選ぶと、そのキーのポーズをポーズタブで作れます" if view.count else "キーがありません。「キーを足す」で作ります")
            self.target_label.setStyleSheet(DIM_STYLE)
        self.btn_add.setToolTip(
            "新しいキーを足します（値は今のキーと重ならない提案から。あとで変えられます）。足したキーは編集の対象になります"
            if view.can_add
            else f"キーは最大 {view.limit} 個です"
        )

    def selected_index(self) -> Optional[int]:
        it = self.tree.currentItem()
        return int(it.data(0, QtCore.Qt.UserRole)) if it is not None and it.isSelected() else None

    # ------------------------------------------------------------------ ダイアログ（テストで差し替えられる）
    def ask_value(self, title: str, label: str, value: float, lo: float, hi: float, decimals: int = 1) -> Optional[float]:
        """数値を聞く（キャンセルは None）。"""
        v, ok = QtWidgets.QInputDialog.getDouble(self, title, label, value, lo, hi, decimals)
        return v if ok else None

    def ask_unsaved_choice(self, message: str) -> str:
        return ask_save_discard_cancel(self, "今の対象に、まだ保存していないポーズの編集があります。保存してから移りますか？（破棄すると編集した内容は戻せません）")

    def ask_confirm(self, text: str) -> bool:
        return ask_yes_no(self, text, "パース補正")

    # ------------------------------------------------------------------ 共通
    def _run(self, label: str, fn: Callable[[], object]):
        self._busy += 1
        try:
            return fn()
        except FacialSessionError as exc:
            self.set_status(str(exc), error=True)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"{label}でエラーが出ました: {exc}", error=True)
            lifecycle.report_error(f"パース補正: {label}", traceback.format_exc(), once=False)
        finally:
            self._busy -= 1
        return None

    def _show(self, res, ok_text: str = "") -> None:
        if res is None:
            return
        self.set_status(res.message or (ok_text if res.ok else "できませんでした"), error=not res.ok)

    def _value_range(self) -> tuple[float, float, str]:
        axis = self.session.perspective.axis
        return (0.1, 179.9, "度") if axis == "fov" else (0.1, 100000.0, "cm")

    # ------------------------------------------------------------------ 開閉・設定
    def on_toggled(self, on: bool) -> None:
        self._user_toggled = True
        self.box.setVisible(bool(on))
        if self.has_doc():
            self.refresh()

    def on_enabled(self) -> None:
        if self._updating or not self.has_doc():
            return
        on = self.cb_enabled.isChecked()
        self._show(self._run("使う / 使わない", lambda: self.session.set_perspective_enabled(on)))
        self.set_status("パース補正を使います" if on else "パース補正を使わない設定にしました（キーは残ります）")

    def on_axis_activated(self, _index: int = 0) -> None:
        if self._updating or not self.has_doc():
            return
        axis = self.axis_combo.currentData()
        res = self._run("軸の変更", lambda: self.session.set_perspective_axis(axis))
        if res is not None and res.code != "unchanged":
            self._show(res)
        self.refresh()

    def on_strength(self, value: float) -> None:
        if self._updating or not self.has_doc():
            return
        res = self._run("強さの変更", lambda: self.session.set_perspective_strength(float(value)))
        if res is not None and not res.ok:
            self._show(res)
            self.refresh()
        elif res is not None:
            self.set_status(f"強さを {value:.2f} にしました")

    # ------------------------------------------------------------------ キーの選択
    def on_item_clicked(self, item: QtWidgets.QTreeWidgetItem, _column: int = 0) -> None:
        if self._updating or not self.has_doc() or item is None:
            return
        self.select_key(int(item.data(0, QtCore.Qt.UserRole)))

    def select_key(self, index: int) -> bool:
        """キーを編集の対象にする（未保存の編集があれば確認する）。選べたら True。"""
        s = self.session
        move = bool(self._move_camera())
        was_editing = s.editing
        r = self._run("キーの選択", lambda: s.select_key(index, move_camera=move))
        if r is None:
            self.refresh()
            return False
        if r.status == SELECT_NEEDS_CONFIRM:
            choice = self.ask_unsaved_choice(r.message)
            r = self._run("キーの選択", lambda: s.select_key(index, choice=choice, move_camera=move))
            if r is None or r.status == SELECT_CANCELLED:
                self.set_status("切り替えを取りやめました（今の編集はそのままです）")
                self.refresh()
                return False
        if r.status == SELECT_INVALID:
            self.set_status(r.message, error=True)
            self.refresh()
            return False
        msg = f"{s.key_label(index)}を編集の対象にしました"
        if r.saved:
            msg = "保存して移りました。" + msg
        if not was_editing:
            msg += "。編集を始めました（シーンを基準姿勢にしています。ポーズタブで編集できます）"
        self.set_status(msg)
        self.refresh()
        return True

    # ------------------------------------------------------------------ キーの追加・変更・削除
    def on_add(self) -> None:
        if not self.has_doc():
            return
        s = self.session
        p = s.perspective
        if not p.can_add:
            self.set_status("キーは最大まで足してあります", error=True)
            return
        lo, hi, unit = self._value_range()
        label = f"キーの値（{unit}）"
        value = self.ask_value("キーを足す", label, p.suggest_value(), lo, hi)
        if value is None:
            self.set_status("キーを足すのを取りやめました")
            return
        chk = p.check_value(value)
        if not chk.ok:
            self.set_status(chk.message, error=True)
            return
        res = self._run("キーを足す", lambda: s.add_perspective_key(value))
        if res is None or not res.ok:
            self._show(res)
            return
        self.refresh()
        if res.index is not None and self.select_key(res.index):
            self.set_status(res.message + "。そのキーを編集の対象にしました（ポーズタブでポーズを作ってください）")

    def on_set_value(self) -> None:
        if not self.has_doc():
            return
        index = self.selected_index()
        if index is None:
            self.set_status("値を変えるキーを、一覧で選んでください", error=True)
            return
        s = self.session
        row = s.perspective.view().keys[index]
        lo, hi, unit = self._value_range()
        value = self.ask_value("値を変える", f"キー {index + 1} の値（{unit}）", row.value, lo, hi)
        if value is None:
            self.set_status("値を変えるのを取りやめました")
            return
        chk = s.perspective.check_value(value, ignore_index=index)
        if not chk.ok:
            self.set_status(chk.message, error=True)
            return
        res = self._run("値を変える", lambda: s.set_perspective_key_value(index, value))
        self.refresh()
        if res is not None:
            self.set_status(f"キー {index + 1} の値を {value:g} {unit} にしました" if res.ok and res.code != "unchanged" else (res.message or "値は変わっていません"), error=not res.ok)

    def on_delete(self) -> None:
        if not self.has_doc():
            return
        index = self.selected_index()
        if index is None:
            self.set_status("削除するキーを、一覧で選んでください", error=True)
            return
        s = self.session
        view = s.perspective.view()
        row = view.keys[index]
        text = f"パース補正のキー {index + 1}（{row.value:g} {view.value_unit}）を削除します。\n\n{view.remove_note}"
        if s.pose.dirty and s.ctx.selected_key() == index:
            text += "\n\nこのキーに保存していないポーズの編集があれば、失われます。"
        text += "\n\n削除しますか？"
        if not self.ask_confirm(text):
            self.set_status("削除を取りやめました")
            return
        res = self._run("キーの削除", lambda: s.remove_perspective_key(index))
        self.refresh()
        self._show(res, "削除しました")

    # ------------------------------------------------------------------ 後片付け
    def detach(self) -> None:
        """通知の購読をやめる（パネルを閉じる・リロードの前）。二重に呼んでも安全。"""
        self._detached = True
        for fn in self._unsub:
            fn()
        self._unsub = []
