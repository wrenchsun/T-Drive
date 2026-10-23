"""クリップタブ（docs/20 §3.2、チケット AN-3）: 一覧・検索・右クリック・現在の範囲から追加・ループ再生・イベント・サムネイル。

操作感は Phase E（docs/05 §4.2）に合わせる: 検索欄・右クリックのコピー / 貼り付け・1 操作 = 1 Undo・ラベルのツールチップに範囲と既定値・
壊れた入力は警告だけで止めない。dx11Shader・Render Override は使わない。Qt の clicked には `lambda *_, x=...:` を使う。
"""

from __future__ import annotations

import traceback

from maya import cmds
from PySide6 import QtCore, QtGui, QtWidgets

from tdrive import lifecycle, widgets as WG

from . import clips as MC
from . import session
from . import transport as T
from .ui_common import MessageLabel
from . import scene_naming as SN
from .core import animset as A
from .core import clips as C
from .core import naming_rules as N

NEED_ANIMSET = "先に animset を作ってください（上の ModelId 欄 → 新規）"
COL_NAME, COL_START, COL_END, COL_LOOP, COL_LAYER, COL_EVENTS = range(6)
HEADERS = ["名前", "開始", "終了", "ループ", "レイヤー", "イベント"]


class ClipsTab(QtWidgets.QWidget):
    def __init__(self, s: session.AnimSession) -> None:
        super().__init__()
        self.session = s
        self._building = False
        v = QtWidgets.QVBoxLayout(self)

        # ---- 追加
        add = QtWidgets.QGroupBox("現在の範囲から追加")
        ah = QtWidgets.QHBoxLayout(add)
        self.action = WG.ComboBox()
        self.action.setEditable(True)
        self.action.lineEdit().setPlaceholderText("選ぶか、入力もできます（例: Idle）")
        self.action.setToolTip("動作の名前（PascalCase）。語彙から選ぶか、自分で入力します。語彙は anim/_vocabulary.json で増やせます。既定: Idle")
        self.direction = WG.ComboBox()
        self.direction.addItems(["", "F", "B", "L", "R"])
        self.direction.setToolTip("方向（前 F / 後 B / 左 L / 右 R）。無ければ空")
        self.number = WG.SpinBox()
        self.number.setRange(0, 99)
        self.number.setSpecialValueText("連番なし")
        self.number.setToolTip("連番（01 など）。0 のときは付けません。範囲: 0〜99。既定: 連番なし")
        self.range_label = QtWidgets.QLabel()
        self.add_btn = QtWidgets.QPushButton("追加")
        self.add_btn.setToolTip("タイムスライダーの再生範囲をそのままクリップにします。名前は A_<ModelId>_<動作>… になります")
        self.add_btn.clicked.connect(lambda *_: self.add_from_range())
        for w in (self.action, self.direction, self.number, self.range_label, self.add_btn):
            ah.addWidget(w)
        v.addWidget(add)

        # ---- 一覧
        row = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("検索（名前・レイヤー・イベントのキー）")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self.refresh())
        row.addWidget(self.search, 1)
        self.play_btn = QtWidgets.QPushButton("▶ 再生")
        self.play_btn.setToolTip("選んだクリップの範囲でループ再生します（ループ OFF のクリップは 1 回）。止めると元の再生範囲に戻ります")
        self.play_btn.clicked.connect(lambda *_: self.play_selected())
        self.stop_btn = QtWidgets.QPushButton("■ 停止")
        self.stop_btn.setToolTip("再生を止めて、元の再生範囲・時刻・ループ設定に戻します")
        self.stop_btn.clicked.connect(lambda *_: self.stop())
        row.addWidget(self.play_btn)
        row.addWidget(self.stop_btn)
        v.addLayout(row)
        self.table = QtWidgets.QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._fill_detail)
        self.table.setMinimumHeight(180)
        v.addWidget(self.table, 1)
        hint = QtWidgets.QLabel("表のセルをダブルクリックすると編集できます（名前・開始・終了）")
        hint.setStyleSheet("color: gray;")
        v.addWidget(hint)
        self.message = MessageLabel()
        v.addWidget(self.message)
        self.nokey_label = QtWidgets.QLabel()  # 範囲にキーが無いクリップの警告（fps を変えて範囲がずれたときなど。AN-F-13）
        self.nokey_label.setWordWrap(True)
        self.nokey_label.setStyleSheet("color: #f0a040;")
        v.addWidget(self.nokey_label)

        # ---- 詳細（選んだクリップ）
        d = QtWidgets.QGroupBox("選んだクリップ")
        dv = QtWidgets.QFormLayout(d)
        self.root_motion = WG.ComboBox()
        self.root_motion.addItems(list(A.ROOT_MOTIONS))
        self.root_motion.setToolTip("none: Hips の移動を出力時にその場に固定（Unity の Original 相当）／ bake: そのまま出す。既定: none")
        self.root_motion.activated.connect(lambda *_: self._apply_detail("rootMotion", self.root_motion.currentText()))
        self.layer = WG.ComboBox()
        self.layer.setToolTip("Base: 全身／ UpperBody: 上半身だけ（レイヤーの確認はブレンドタブ）。既定: Base")
        self.layer.activated.connect(lambda *_: self._apply_detail("layer", self.layer.currentText()))
        self.cross = WG.DoubleSpinBox()
        self.cross.setRange(0.0, 5.0)
        self.cross.setSingleStep(0.02)
        self.cross.setDecimals(2)
        self.cross.setSuffix(" 秒")
        self.cross.setToolTip("このクリップへ切り替わるときの既定のクロスフェード（固定秒・線形）。範囲: 0〜5。既定: 0.1（D-Drive）／ MS2026 の Controller は 0.08（仮。docs/20 §9）")
        self.cross.editingFinished.connect(lambda *_: self._apply_detail("crossFade", round(self.cross.value(), 3)))
        self.loop_cb = QtWidgets.QCheckBox("ループする")
        self.loop_cb.setToolTip("ループ再生・状態図でのループ（Unity の Loop Time）。一覧の「ループ」列でも変えられます。既定: 動作の語彙から決まります（Idle・Run はオン）")
        self.loop_cb.clicked.connect(lambda *_: self._apply_detail("loop", self.loop_cb.isChecked()))
        self.state_group = WG.ComboBox()
        self.state_group.addItems(list(A.STATE_GROUPS))
        self.state_group.setToolTip("locomotion: 状態図（待機・走り…）に載せる／ oneshot: 単発の動作。既定: 動作の語彙から決まります")
        self.state_group.activated.connect(lambda *_: self._apply_detail("stateGroup", self.state_group.currentText()))
        dv.addRow("ループ", self.loop_cb)
        dv.addRow("ルートモーション", self.root_motion)
        dv.addRow("レイヤー", self.layer)
        dv.addRow("クロスフェード", self.cross)
        dv.addRow("状態グループ", self.state_group)
        self.events = QtWidgets.QTableWidget(0, 3)
        self.events.setHorizontalHeaderLabels(["フレーム", "キー", "引数"])
        self.events.horizontalHeader().setStretchLastSection(True)
        self.events.verticalHeader().setVisible(False)
        self.events.setMaximumHeight(110)
        self.events.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.events.customContextMenuRequested.connect(self._event_menu)
        dv.addRow("イベント", self.events)
        eb = QtWidgets.QHBoxLayout()
        self.ev_key = QtWidgets.QLineEdit()
        self.ev_key.setPlaceholderText("イベントのキー（例: Hit）")
        eb.addWidget(self.ev_key, 1)
        b1 = QtWidgets.QPushButton("現在フレームに追加")
        b1.setToolTip("タイムスライダーの現在フレームにイベントを足します（クリップの範囲内のみ）")
        b1.clicked.connect(lambda *_: self.add_event())
        b2 = QtWidgets.QPushButton("足音 左")
        b2.setToolTip("現在フレームに OnFootPlant（引数 0 = 左足。仮の決め）を足します")
        b2.clicked.connect(lambda *_, side="left": self.add_foot(side))
        b3 = QtWidgets.QPushButton("足音 右")
        b3.setToolTip("現在フレームに OnFootPlant（引数 1 = 右足）を足します")
        b3.clicked.connect(lambda *_, side="right": self.add_foot(side))
        for b in (b1, b2, b3):
            eb.addWidget(b)
        dv.addRow("", eb)
        self.thumb = QtWidgets.QLabel("サムネイル: 未作成")
        self.thumb.setMinimumHeight(24)
        dv.addRow("サムネイル", self.thumb)
        v.addWidget(d)
        self._clip_names: list[str] = []
        self._range_timer = QtCore.QTimer(self)  # タイムスライダーの再生範囲の表示を追従させる（前に出ているあいだだけ。UX-14）
        self._range_timer.setInterval(500)
        self._range_timer.timeout.connect(lambda *_: self._update_range_label())
        self._range_timer.start()
        self.refresh()

    # ------------------------------------------------------------ 選択
    def selected_names(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self._clip_names[r] for r in rows if r < len(self._clip_names)]

    def _one(self) -> dict | None:
        names = self.selected_names()
        return A.find_clip(self.session.data, names[0]) if names and self.session.data else None

    def _fill_nokey(self, d: dict) -> None:
        try:
            names = SN.clips_without_keys(d)
        except Exception:  # noqa: BLE001  警告の計算で画面を止めない
            names = []
        self.nokey_label.setText(
            ("⚠ 範囲にキーが無いクリップ: " + "、".join(names[:6]) + (f" ほか {len(names) - 6} 件" if len(names) > 6 else "")
             + "。フレームレートを変えたあとなどに範囲がずれていないか確かめてください（出力すると中身が止まった姿勢になります）") if names else "")

    def _say(self, text: str) -> None:
        self.message.setText(text)

    def _guard(self, fn, *args, **kwargs):
        """AnimSetError は警告として表示するだけで止めない。それ以外の例外は報告して続ける。"""
        try:
            return fn(*args, **kwargs)
        except A.AnimSetError as exc:
            self._say(f"⚠ {exc}")
        except Exception as exc:  # noqa: BLE001
            lifecycle.report_error("クリップタブの操作で例外", traceback.format_exc())
            self._say(f"⚠ {exc}")
        return None

    # ------------------------------------------------------------ 更新
    def _update_range_label(self) -> None:
        if self.session.data is None or not self.isVisible():
            return
        d = self.session.data
        lo, hi = MC.playback_range()
        keyed = MC.keyed_range(A.root_name(d))
        text = f"範囲 {lo}〜{hi}" + (f"（キー {keyed[0]}〜{keyed[1]}）" if keyed else "（ルート以下にキーなし）")
        if self.range_label.text() != text:
            self.range_label.setText(text)

    def refresh(self) -> None:
        has = self.session.data is not None
        for w in (self.add_btn, self.play_btn, self.table):
            w.setEnabled(has)
        if not has:
            self.nokey_label.setText("")
            self.table.setRowCount(0)
            self._clip_names = []
            self._say(NEED_ANIMSET)
            return
        if self.message.text() == NEED_ANIMSET:  # animset を作ったら案内を消す（AN-F-1）
            self._say("")
        d = self.session.data
        self._fill_vocab(d)
        self._fill_nokey(d)
        self._update_range_label()
        selected = self.selected_names()
        self._building = True
        try:
            shown = C.search(d, self.search.text())
            self._clip_names = [c["name"] for c in shown]
            self.table.setRowCount(len(shown))
            for r, c in enumerate(shown):
                items = [(COL_NAME, c["name"], True), (COL_START, str(c["start"]), True), (COL_END, str(c["end"]), True),
                         (COL_LOOP, "", False), (COL_LAYER, c["layer"], False), (COL_EVENTS, str(len(c["events"])), False)]
                for col, text, editable in items:
                    it = QtWidgets.QTableWidgetItem(text)
                    if not editable:
                        it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable)
                    self.table.setItem(r, col, it)
                loop = self.table.item(r, COL_LOOP)
                loop.setFlags((loop.flags() | QtCore.Qt.ItemIsUserCheckable) & ~QtCore.Qt.ItemIsEditable)
                loop.setCheckState(QtCore.Qt.Checked if c["loop"] else QtCore.Qt.Unchecked)
                self.table.item(r, COL_NAME).setToolTip(f"{C.frame_count(c)} フレーム（{C.duration_sec(c, d['frameRate']):.2f} 秒 @ {d['frameRate']} fps）")
            self.table.resizeColumnsToContents()
        finally:
            self._building = False
        for r, n in enumerate(self._clip_names):
            if n in selected:
                self.table.selectRow(r)
        self._fill_detail()

    def _fill_vocab(self, d: dict) -> None:
        if self.action.count() == 0:
            prof, _ = SN.profile()
            self.action.addItems(prof.vocabulary)
        names = [l["name"] for l in d.get("layers", [])]
        if [self.layer.itemText(i) for i in range(self.layer.count())] != names:
            self.layer.clear()
            self.layer.addItems(names)

    def _fill_detail(self) -> None:
        c = self._one()
        for w in (self.loop_cb, self.root_motion, self.layer, self.cross, self.state_group, self.events, self.ev_key):
            w.setEnabled(c is not None)
        self.events.setRowCount(0)
        if c is None:
            self.thumb.setText("サムネイル: 未作成")
            return
        self.loop_cb.setChecked(bool(c["loop"]))
        self.root_motion.setCurrentText(c["rootMotion"])
        self.layer.setCurrentText(c["layer"])
        self.cross.setValue(float(c["crossFade"]))
        self.state_group.setCurrentText(c["stateGroup"])
        self.events.setRowCount(len(c["events"]))
        for r, e in enumerate(c["events"]):
            for col, text in enumerate((str(e["frame"]), e["key"], "" if e.get("args") is None else str(e["args"]))):
                it = QtWidgets.QTableWidgetItem(text)
                it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable)
                self.events.setItem(r, col, it)
        p = MC.thumb_path(self.session.model_id, c)
        if p.is_file():
            pm = QtGui.QPixmap(str(p))
            self.thumb.setPixmap(pm.scaledToHeight(48))
        else:
            self.thumb.setPixmap(QtGui.QPixmap())
            self.thumb.setText("サムネイル: 未作成（右クリック → サムネイルを作る）")

    # ------------------------------------------------------------ 操作
    def add_from_range(self) -> None:
        lo, hi = MC.playback_range()
        num = self.number.value() or None
        self._guard(self.session.add_clip_from_range, self.action.currentText().strip(), lo, hi, self.direction.currentText(), num)

    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._building or item.row() >= len(self._clip_names):
            return
        name = self._clip_names[item.row()]
        col = item.column()
        try:
            if col == COL_NAME:
                new = item.text().strip()
                if new != name:
                    f = N.inspect(N.CLIP, new, self.session.model_id)
                    self.session.rename_clip(name, new)
                    if f is not None:
                        self._say(f"{'❌' if f.is_error else '⚠'} {f.reason}")
            elif col in (COL_START, COL_END):
                self.session.update_clip(name, **{"start" if col == COL_START else "end": int(item.text())})
            elif col == COL_LOOP:
                self.session.update_clip(name, loop=item.checkState() == QtCore.Qt.Checked)
        except A.AnimSetError as exc:  # AnimSetError は ValueError の仲間なので先に受ける
            self._say(f"⚠ {exc}")
            self.refresh()
        except ValueError:
            self._say("⚠ フレームは整数で入力してください")
            self.refresh()

    def _apply_detail(self, key: str, value) -> None:
        names = self.selected_names()
        if not names:
            return
        self._guard(self.session.update_clips, names, **{key: value})  # 複数選択なら全部に同じ値（全体で 1 回の Undo）

    def _confirm(self, text: str) -> bool:
        """壊す操作の前の確認（既定「いいえ」。UX-5）。"""
        r = QtWidgets.QMessageBox.question(self, "T-Drive Animation", text, QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        return r == QtWidgets.QMessageBox.Yes

    def delete_selected(self) -> None:
        names = self.selected_names()
        if names and self._confirm(f"{len(names)} 件のクリップ（{', '.join(names[:3])}{'…' if len(names) > 3 else ''}）を削除します（Ctrl+Z で戻せます）。続けますか？"):
            self._guard(self.session.remove_clips, names)

    def duplicate_selected(self) -> None:
        names = self.selected_names()
        if names:
            self._guard(self.session.duplicate_clips, names)

    def play_selected(self) -> None:
        c = self._one()
        if c is not None:
            MC.play(c)
            self._say(f"再生中: {c['name']}（{c['start']}〜{c['end']}）。止めると元の再生範囲に戻ります")

    def stop(self) -> None:
        T.stop_all()  # どのタブの再生・タイマーも全部止める（AN-F-35）
        self._say("停止しました（元の再生範囲に戻しました）")

    def to_slider(self) -> None:
        c = self._one()
        if c is not None:
            MC.set_slider_range(c["start"], c["end"])

    def add_event(self) -> None:
        c = self._one()
        if c is not None:
            self._guard(self.session.add_event, c["name"], int(cmds.currentTime(query=True)), self.ev_key.text())

    def add_foot(self, side: str) -> None:
        c = self._one()
        if c is not None:
            self._guard(self.session.add_foot_plant, c["name"], int(cmds.currentTime(query=True)), side)

    def make_thumbnail(self, force: bool = False) -> None:
        c = self._one()
        if c is None:
            return
        path, why = MC.make_thumbnail(self.session.model_id, c, force=force)
        self._say(why or "サムネイルを作りました")
        self._fill_detail()

    def _menu(self, pos) -> None:
        names = self.selected_names()
        m = QtWidgets.QMenu(self)
        if names:
            m.addAction("再生", lambda *_: self.play_selected())
            m.addAction("範囲をタイムスライダーに", lambda *_: self.to_slider())
            m.addSeparator()
            m.addAction("複製", lambda *_: self.duplicate_selected())
            m.addAction("削除", lambda *_: self.delete_selected())
            m.addSeparator()
            m.addAction("属性をコピー", lambda *_: QtWidgets.QApplication.clipboard().setText(C.attrs_to_text(self._one())))
            m.addAction("属性を貼り付け", lambda *_: self._guard(self.session.paste_attrs, names, QtWidgets.QApplication.clipboard().text()))
            m.addAction("サムネイルを作る", lambda *_: self.make_thumbnail())
            m.addAction("サムネイルを作り直す", lambda *_: self.make_thumbnail(force=True))
            m.addSeparator()
        m.addAction("現在の再生範囲から作る", lambda *_: self.add_from_range())
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _event_menu(self, pos) -> None:
        c = self._one()
        rows = sorted({i.row() for i in self.events.selectedIndexes()})
        if c is None or not rows:
            return
        m = QtWidgets.QMenu(self)
        m.addAction("イベントを削除", lambda *_: self._guard(self.session.remove_events, c["name"], rows))
        m.addAction("フレームへ移動", lambda *_: cmds.currentTime(c["events"][rows[0]]["frame"], edit=True))
        m.exec(self.events.viewport().mapToGlobal(pos))

    def resume(self) -> None:
        """再表示: 範囲表示のタイマーを戻す（AN-F-39）。"""
        self._range_timer.start()
        self.refresh()

    def detach(self) -> None:
        self._range_timer.stop()
        MC.stop()  # 閉じるときは再生範囲を戻す
