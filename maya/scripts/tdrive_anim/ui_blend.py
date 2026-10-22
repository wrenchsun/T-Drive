"""ブレンドタブ（docs/20 §4.4、チケット AN-4 / AN-5）: 遷移列の表・状態図のパラメータ・UpperBody レイヤー・合成の生成 / 解除・再生。

- 2 つの入口: ① 遷移列を手で並べる ② 「MS2026 の状態図」のパラメータ（Speed / ActionState）を動かす（再生中に効く）
- 合成の方式は `composition.py`（pairBlend の連鎖。Time Editor は使わない）。DirectX 11 には依存しない
- 操作感は Phase E に合わせる: 右クリックのコピー / 貼り付け・1 操作 = 1 Undo（animset の変更）・ツールチップに範囲と既定値・壊れた入力は警告だけ
"""

from __future__ import annotations

import copy
import json
import traceback

from maya import cmds
from PySide6 import QtCore, QtWidgets

from tdrive import lifecycle

from . import composition as K
from . import scene_naming as SN
from . import session
from .core import animset as A
from .core import sequence as Seq
from .core import state_machine as M

COMP_NAME = "blend"
ROWS_FORMAT = "TDriveBlendRows"
NEED_ANIMSET = "先に animset を作ってください（上の ModelId 欄 → 新規）"
COL_CLIP, COL_HOLD, COL_FADE, COL_SWITCH, COL_SPEED = range(5)
HEADERS = ["クリップ", "保持（秒）", "クロスフェード（秒）", "切替位置", "速度"]


class BlendTab(QtWidgets.QWidget):
    def __init__(self, s: session.AnimSession) -> None:
        super().__init__()
        self.session = s
        self.rows: list[dict] = []
        self._default_rows: list[dict] = []  # 既定の列として作った時点の控え。rows がこれと同じ（未編集）なら、クリップの増減で作り直す（AN-F-3）
        self.shot_tab = None  # AnimPanel が渡す（「この列をショットへ」）
        self.mode = "rows"  # "rows" | "graph"
        self.graph: dict | None = None
        self.timeline: list[tuple[float, dict]] = []
        self._building = False
        self._saved_speed: tuple[float, float] | None = None
        self._rebuild_timer = QtCore.QTimer(self)  # Speed スライダーのドラッグ中は作り直さず、止まってからまとめて 1 回（SF-5）
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(150)
        self._rebuild_timer.timeout.connect(lambda *_: self._rebuild_keeping_time())
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QtWidgets.QWidget()
        scroll.setWidget(body)
        v = QtWidgets.QVBoxLayout(body)

        self.banner = QtWidgets.QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("color: #f0a040;")
        v.addWidget(self.banner)

        # ---- 遷移列
        box = QtWidgets.QGroupBox("遷移列（上から順に再生。次の行へ固定秒・線形でクロスフェード）")
        bv = QtWidgets.QVBoxLayout(box)
        self.rows_table = QtWidgets.QTableWidget(0, len(HEADERS))
        self.rows_table.setHorizontalHeaderLabels(HEADERS)
        self.rows_table.horizontalHeader().setStretchLastSection(True)
        self.rows_table.verticalHeader().setVisible(False)
        self.rows_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.rows_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.rows_table.customContextMenuRequested.connect(self._menu)
        self.rows_table.itemChanged.connect(self._on_item_changed)
        self.rows_table.horizontalHeaderItem(COL_HOLD).setToolTip("この行を見せる秒数（ループするクリップ向け）。0 のときは「切替位置」で決まります。範囲: 0 以上。既定: 0")
        self.rows_table.horizontalHeaderItem(COL_FADE).setToolTip("この行へ切り替わるときのクロスフェード秒数（固定秒・線形）。1 行目は使いません。範囲: 0 以上。既定: 0.1（MS2026 の Controller は 0.08）")
        self.rows_table.horizontalHeaderItem(COL_SWITCH).setToolTip("この行へ切り替えるのは、前の行のクリップがここ（0〜1）に達したとき。1 以上は前のクリップが終わってから（D-Drive の SwitchAt と同じ持ち主）。前の行の「保持」が 0 より大きいときは保持が優先。1 行目では使いません。既定: 1")
        self.rows_table.horizontalHeaderItem(COL_SPEED).setToolTip("再生速度（1 = そのまま）。範囲: 0 より大きい値。既定: 1")
        self.rows_table.setMinimumHeight(140)
        bv.addWidget(self.rows_table)
        rb = QtWidgets.QHBoxLayout()
        for text, fn, tip in (("＋ 行を足す", self.add_row, "末尾に行を足します"), ("－ 行を消す", self.remove_row, "選んだ行を消します"),
                              ("↑", lambda: self.move_row(-1), "選んだ行を上へ"), ("↓", lambda: self.move_row(1), "選んだ行を下へ"),
                              ("既定の列に戻す", self.reset_rows, "Idle → Run → Jump の既定の列に作り直します（今の列は消えます）")):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, f=fn: f())
            rb.addWidget(b)
        rb.addStretch(1)
        bv.addLayout(rb)
        self.build_rows_btn = QtWidgets.QPushButton("この列で合成を作る")
        self.build_rows_btn.setToolTip("遷移列の通りに、スケルトンのアニメーションを合成します（元のカーブは壊しません。「合成を外す」で戻せます）")
        self.build_rows_btn.clicked.connect(lambda *_: self.build_from_rows())
        bv.addWidget(self.build_rows_btn)
        v.addWidget(box)

        # ---- 状態図（MS2026）
        gbox = QtWidgets.QGroupBox("MS2026 の状態図（Idle / Run / Jump）をパラメータで動かす")
        gv = QtWidgets.QVBoxLayout(gbox)
        self.graph_btn = QtWidgets.QPushButton("MS2026 の状態図から")
        self.graph_btn.setToolTip("AC_PlayerPreview.controller と同じ状態図（Idle / Run / Jump、遷移の条件と並び順も同じ、0.08 秒のクロスフェード、Exit Time なし）で動かします。animset に保存されます（animset に既にあるときはそれを使います）")
        self.graph_btn.clicked.connect(lambda *_: self.use_graph())
        gv.addWidget(self.graph_btn)
        form = QtWidgets.QFormLayout()
        self.speed = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.speed.setRange(0, 100)
        self.speed.setToolTip("Speed（0〜1）。0.1 より大きいと Idle → Run、小さいと Run → Idle。再生中に動かすと、その時刻から遷移します")
        self.speed_label = QtWidgets.QLabel("0.00")
        self.speed.valueChanged.connect(lambda *_: self._speed_moved())
        sp = QtWidgets.QHBoxLayout()
        sp.addWidget(self.speed, 1)
        sp.addWidget(self.speed_label)
        form.addRow("Speed", sp)
        self.action_state = QtWidgets.QComboBox()
        for name, val in M.ACTION_STATES.items():
            self.action_state.addItem(f"{name} ({val})", val)
        self.action_state.setToolTip("ActionState。Jump (2) / Fall (3) にすると、Idle・Run から Jump へ遷移します。Jump 以外に戻すと、Speed に応じて Idle か Run に戻ります（AC_PlayerPreview と同じ）")
        self.action_state.activated.connect(lambda *_: self.on_param_changed())
        form.addRow("ActionState", self.action_state)
        self.total_sec = QtWidgets.QDoubleSpinBox()
        self.total_sec.setRange(1.0, 120.0)
        self.total_sec.setValue(8.0)
        self.total_sec.setSuffix(" 秒")
        self.total_sec.setToolTip("合成する長さ。範囲: 1〜120 秒。既定: 8 秒")
        form.addRow("長さ", self.total_sec)
        gv.addLayout(form)
        self.reset_btn = QtWidgets.QPushButton("パラメータを最初から（記録を消す）")
        self.reset_btn.setToolTip("Speed / ActionState の記録（時刻ごとの変更）を消して、最初の状態からやり直します")
        self.reset_btn.clicked.connect(lambda *_: self.reset_timeline())
        gv.addWidget(self.reset_btn)
        self.build_graph_btn = QtWidgets.QPushButton("状態図で合成を作る")
        self.build_graph_btn.setToolTip("記録したパラメータの変更を状態図で評価し、できた遷移列で合成を作ります。作ったあとにスライダーを動かすと、その時刻から遷移し直します")
        self.build_graph_btn.clicked.connect(lambda *_: self.build_from_graph())
        gv.addWidget(self.build_graph_btn)
        self.graph_info = QtWidgets.QLabel()
        self.graph_info.setWordWrap(True)
        gv.addWidget(self.graph_info)
        v.addWidget(gbox)

        # ---- レイヤー
        lbox = QtWidgets.QGroupBox("レイヤー（上半身だけ別のクリップを重ねる）")
        lf = QtWidgets.QFormLayout(lbox)
        self.layer_on = QtWidgets.QCheckBox("UpperBody を使う")
        self.layer_on.setToolTip("animset のレイヤー UpperBody の maskRoot（既定 spine_C0_3_jnt）以下のジョイントにだけ、下のクリップを重ねます")
        lf.addRow(self.layer_on)
        self.layer_clip = QtWidgets.QComboBox()
        self.layer_clip.setToolTip("上半身に重ねるクリップ（ループします）")
        lf.addRow("クリップ", self.layer_clip)
        self.layer_weight = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.layer_weight.setRange(0, 100)
        self.layer_weight.setValue(100)
        self.layer_weight.setToolTip("重み（0〜1）。合成中でも動かせます。既定: 1")
        self.layer_weight.valueChanged.connect(lambda *_: self._layer_weight_moved())
        lf.addRow("重み", self.layer_weight)
        v.addWidget(lbox)

        # ---- 再生
        pbox = QtWidgets.QGroupBox("再生")
        ph = QtWidgets.QHBoxLayout(pbox)
        self.play_btn = QtWidgets.QPushButton("▶ 再生")
        self.play_btn.setToolTip("合成の範囲を再生します（再生の速さとループは右の設定。止めても元には戻りません。「合成を外す」で戻ります）")
        self.play_btn.clicked.connect(lambda *_: self.play())
        self.stop_btn = QtWidgets.QPushButton("■ 停止")
        self.stop_btn.setToolTip("再生を止めます")
        self.stop_btn.clicked.connect(lambda *_: self.stop())
        self.loop_cb = QtWidgets.QCheckBox("ループ")
        self.loop_cb.setChecked(True)
        self.loop_cb.setToolTip("ON: ループ再生／ OFF: 1 回だけ。既定: ON")
        self.play_speed = QtWidgets.QComboBox()
        for label, val in (("1×", 1.0), ("0.5×", 0.5), ("0.25×", 0.25)):
            self.play_speed.addItem(label, val)
        self.play_speed.setToolTip("再生の速さ。既定: 1×（実時間）")
        self.remove_btn = QtWidgets.QPushButton("合成を外す")
        self.remove_btn.setToolTip("合成を外し、元のカーブ・再生範囲・時刻に戻します")
        self.remove_btn.clicked.connect(lambda *_: self.remove())
        self.shot_btn = QtWidgets.QPushButton("この列をショットへ")
        self.shot_btn.setEnabled(False)
        self.shot_btn.setToolTip("ショットタブで開いているショットの、キャラクター（表で選んだ行）のクリップ列にします。ショットとキャラクターを先に用意してください")
        self.shot_btn.clicked.connect(lambda *_: self.send_to_shot())
        for w in (self.play_btn, self.stop_btn, self.loop_cb, self.play_speed, self.remove_btn, self.shot_btn):
            ph.addWidget(w)
        v.addWidget(pbox)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        v.addWidget(self.message)
        v.addStretch(1)
        self.refresh()

    # ------------------------------------------------------------ 共通
    def send_to_shot(self) -> None:
        """遷移列をショットのキャラクターへ渡す（ショットタブが付けてくれる shot_tab を使う）。"""
        if self.shot_tab is None:
            return
        if not self.rows:
            self._say("⚠ 遷移列が空です（行を足してください）")
            return
        self.shot_tab.send_rows_from_blend()
        self._say(self.shot_tab.message.text())

    def _mine(self) -> list[str]:
        """このタブが作った合成（ショットが作ったものは含めない）。"""
        return [n for n in K.active() if n == COMP_NAME]

    def _say(self, text: str) -> None:
        self.message.setText(text)

    def _clips(self) -> dict[str, dict]:
        return {c["name"]: c for c in (self.session.data or {}).get("clips", [])}

    def _fps(self) -> int:
        """秒 → フレームの換算に使うフレームレート。シーンの時間単位のもの（animset の frameRate と違えば、実際の再生の速さに合わせる。SP-5）。"""
        return SN.scene_fps_exact() or int((self.session.data or {}).get("frameRate", 30))

    def _fps_note(self) -> str:
        """シーンの時間単位が animset の frameRate と違うときの警告（空なら一致）。"""
        have, want = SN.scene_fps_exact(), int((self.session.data or {}).get("frameRate", 30))
        if have is None or have == want:
            return ""
        return f"\n⚠ シーンの時間単位は {have} fps、animset は {want} fps です。秒はシーンの {have} fps で換算しました。命名タブの「直す」で合わせてください（クリップの範囲はフレーム番号のままです）"

    def _guard(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (A.AnimSetError, Seq.SequenceError, M.StateGraphError, ValueError) as exc:
            self._say(f"⚠ {exc}")
        except Exception as exc:  # noqa: BLE001
            lifecycle.report_error("ブレンドタブの操作で例外", traceback.format_exc())
            self._say(f"⚠ {exc}")
        return None

    # ------------------------------------------------------------ 表示
    def refresh(self) -> None:
        has = self.session.data is not None
        for w in (self.rows_table, self.build_rows_btn, self.graph_btn, self.build_graph_btn, self.play_btn, self.layer_on, self.layer_clip):
            w.setEnabled(has)
        act = self._mine()
        if act:
            i = K.info(act[0])
            self.banner.setText(f"⚠ 合成中: {K.PREFIX}{act[0]}（{i.get('frames', 0):.0f} フレーム、{i.get('joints', 0)} ジョイント）。"
                                "ジョイントは合成につながっています。シーンを保存しても合成は保存されません（保存の間だけ元のつながりに戻します）。"
                                "合成中はジョイントにキーを打たないでください（オートキーは切ってあります）。使い終わったら「合成を外す」")
        else:
            self.banner.setText("")
        self.banner.setVisible(bool(act))
        self.remove_btn.setEnabled(bool(act))
        self.shot_btn.setEnabled(self.session.shot is not None and bool(self.session.shot["characters"]))
        if not has:
            self.rows = []
            self._fill_rows()
            self._say(NEED_ANIMSET)
            return
        if self.message.text() == NEED_ANIMSET:  # animset を作ったら案内を消す（AN-F-1）
            self._say("")
        d = self.session.data
        names = list(self._clips())
        if [self.layer_clip.itemText(i) for i in range(self.layer_clip.count())] != names:
            self.layer_clip.clear()
            self.layer_clip.addItems(names)
        if self.mode == "rows" and (not self.rows or self.rows == self._default_rows):  # 未編集の既定の列は、クリップの増減に追従する
            self.rows = Seq.default_rows(d["clips"], d["modelId"])
            self._default_rows = copy.deepcopy(self.rows)
        self.rows = [r for r in self.rows if r["clip"] in self._clips()]  # 消えたクリップの行は落とす
        self._fill_rows()
        self.graph = d.get("stateGraph") if isinstance(d.get("stateGraph"), dict) else self.graph
        self._fill_graph_info()

    def _fill_rows(self) -> None:
        self._building = True
        try:
            self.rows_table.setRowCount(len(self.rows))
            names = list(self._clips())
            for r, row in enumerate(self.rows):
                combo = QtWidgets.QComboBox()
                combo.addItems(names)
                combo.setCurrentText(row["clip"])
                combo.activated.connect(lambda *_, rr=r, c=combo: self._set(rr, "clip", c.currentText()))
                self.rows_table.setCellWidget(r, COL_CLIP, combo)
                for col, key, val in ((COL_HOLD, "holdSec", row.get("holdSec", 0)), (COL_FADE, "crossFadeSec", Seq.fade_sec(row, self._clips().get(row["clip"], {}))),
                                      (COL_SWITCH, "switchAt", row.get("switchAt", 1.0)), (COL_SPEED, "speed", row.get("speed", 1.0))):
                    self.rows_table.setItem(r, col, QtWidgets.QTableWidgetItem(f"{val:g}"))
            self.rows_table.resizeColumnsToContents()
        finally:
            self._building = False

    def _fill_graph_info(self) -> None:
        if self.graph is None:
            self.graph_info.setText("状態図はまだ使っていません（「MS2026 の状態図から」で作ります）")
            return
        probs = M.validate(self.graph, self.session.data["clips"] if self.session.data else [])
        states = " / ".join(f"{s['name']}={s.get('clip') or '（未割り当て）'}" for s in self.graph.get("states", []))
        events = ", ".join(f"{t:.2f}s:{v}" for t, v in self.timeline) or "なし"
        self.graph_info.setText(f"状態: {states}\n記録したパラメータ: {events}" + ("\n" + "\n".join("⚠ " + p for p in probs[:4]) if probs else ""))

    # ------------------------------------------------------------ 行の編集
    def _set(self, row: int, key: str, value) -> None:
        if 0 <= row < len(self.rows):
            self.rows[row][key] = value

    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._building or item.row() >= len(self.rows):
            return
        key = {COL_HOLD: "holdSec", COL_FADE: "crossFadeSec", COL_SWITCH: "switchAt", COL_SPEED: "speed"}.get(item.column())
        if key is None:
            return
        try:
            val = float(item.text())
            if val < 0 or (key == "speed" and val <= 0):
                raise ValueError
        except ValueError:
            self._say("⚠ 数値（0 以上。速度は 0 より大きい）で入力してください")
            self._fill_rows()
            return
        self.rows[item.row()][key] = val

    def reset_rows(self) -> None:
        """遷移列を既定（Idle → Run → Jump）に作り直す（AN-F-3）。"""
        if self.session.data is None:
            self._say(NEED_ANIMSET)
            return
        d = self.session.data
        self.mode = "rows"
        self.rows = Seq.default_rows(d["clips"], d["modelId"])
        self._default_rows = copy.deepcopy(self.rows)
        self._fill_rows()
        self._say("遷移列を既定の列に戻しました")

    def add_row(self) -> None:
        names = list(self._clips())
        if not names:
            self._say("⚠ クリップがありません（クリップタブで作ってください）")
            return
        self.rows.append({"clip": names[0], "holdSec": 0.0, "crossFadeSec": Seq.fade_sec({}, self._clips()[names[0]]), "switchAt": 1.0, "speed": 1.0})
        self._fill_rows()

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.rows_table.selectedIndexes()})

    def remove_row(self) -> None:
        for r in reversed(self._selected_rows()):
            if 0 <= r < len(self.rows):
                del self.rows[r]
        self._fill_rows()

    def move_row(self, delta: int) -> None:
        sel = self._selected_rows()
        if len(sel) != 1:
            return
        r, n = sel[0], sel[0] + delta
        if 0 <= n < len(self.rows):
            self.rows[r], self.rows[n] = self.rows[n], self.rows[r]
            self._fill_rows()
            self.rows_table.selectRow(n)

    def rows_to_text(self) -> str:
        return json.dumps({"format": ROWS_FORMAT, "rows": self.rows}, ensure_ascii=False)

    def paste_rows(self, text: str) -> None:
        """コピーした遷移列を末尾に貼り付ける。別の形式の文字列は警告だけ。クリップが無い行は飛ばす。"""
        try:
            raw = json.loads(text)
            if not isinstance(raw, dict) or raw.get("format") != ROWS_FORMAT or not isinstance(raw.get("rows"), list):
                raise ValueError
        except ValueError:
            self._say("⚠ 遷移列ではありません（貼り付けられません）")
            return
        names = self._clips()
        got = [r for r in raw["rows"] if isinstance(r, dict) and r.get("clip") in names]
        self.rows += got
        self._fill_rows()
        self._say(f"{len(got)} 行を貼り付けました" + (f"（クリップが無い {len(raw['rows']) - len(got)} 行は飛ばしました）" if len(got) != len(raw["rows"]) else ""))

    def _menu(self, pos) -> None:
        m = QtWidgets.QMenu(self)
        sel = self._selected_rows()
        if sel:
            m.addAction("選んだ行をコピー", lambda *_: QtWidgets.QApplication.clipboard().setText(
                json.dumps({"format": ROWS_FORMAT, "rows": [self.rows[r] for r in sel if r < len(self.rows)]}, ensure_ascii=False)))
        m.addAction("貼り付け", lambda *_: self.paste_rows(QtWidgets.QApplication.clipboard().text()))
        if sel:
            m.addAction("行を消す", lambda *_: self.remove_row())
        m.exec(self.rows_table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------ 合成
    def _layers(self, total_frames: float) -> list[K.Layer]:
        if not self.layer_on.isChecked() or not self.layer_clip.currentText():
            return []
        d = self.session.data
        mask = next((l.get("maskRoot") for l in d.get("layers", []) if l["name"] == "UpperBody"), None) or "spine_C0_3_jnt"
        if d.get("namespace") and ":" not in mask:
            mask = f"{d['namespace']}:{mask}"
        if not cmds.objExists(mask):
            raise ValueError(f"UpperBody の maskRoot {mask} がシーンにありません")
        joints = [mask] + (cmds.listRelatives(mask, allDescendents=True, type="joint") or [])
        pls = Seq.from_rows([{"clip": self.layer_clip.currentText()}], self._clips(), self._fps())
        pls[0].end = max(pls[0].end, total_frames)
        return [K.Layer(pls, joints, self.layer_weight.value() / 100.0, "UpperBody")]

    def _skeleton(self) -> list[str]:
        root = A.root_name(self.session.data)
        if not cmds.objExists(root):
            raise ValueError(f"スケルトンのルート {root} がシーンにありません")
        return [root] + (cmds.listRelatives(root, allDescendents=True, type="joint") or [])

    def _compose(self, placements: list[Seq.Placement]) -> None:
        self._remember_speed()
        res = K.compose(COMP_NAME, placements, self._skeleton(), self._fps(), self._layers(Seq.total_frames(placements)))
        cmds.playbackOptions(loop="continuous" if self.loop_cb.isChecked() else "once")
        warns = "".join(f"\n⚠ {w}" for w in Seq.overlap_warnings(placements, self._fps()))
        self._say(f"合成を作りました（{res.frames:.0f} フレーム、{res.joints} ジョイント、{res.plugs} チャンネル）。▶ 再生で確認できます{warns}{self._fps_note()}")
        self.refresh()

    def build_from_rows(self) -> None:
        self.mode = "rows"
        if not self.rows:
            self._say("⚠ 遷移列が空です（行を足してください）")
            return
        self._guard(lambda: self._compose(Seq.from_rows(self.rows, self._clips(), self._fps())))

    def use_graph(self) -> None:
        """MS2026 の状態図に切り替える（animset に無ければ既定を作って保存対象にする）。"""
        d = self.session.data
        if d is None:
            return
        self.mode = "graph"
        if not isinstance(d.get("stateGraph"), dict):
            self._guard(self.session.set_value, "stateGraph", M.default_graph(d["clips"], d["modelId"]))
        self.graph = d.get("stateGraph")
        self._fill_graph_info()

    def reset_timeline(self) -> None:
        self.timeline = []
        self._fill_graph_info()

    def _params(self) -> dict[str, float]:
        return {"Speed": self.speed.value() / 100.0, "ActionState": float(self.action_state.currentData())}

    def _speed_moved(self) -> None:
        self.speed_label.setText(f"{self.speed.value() / 100.0:.2f}")
        self.on_param_changed()

    def on_param_changed(self) -> None:
        """パラメータを動かした: 今の時刻の変更として記録する。状態図の合成が出ていれば、その時刻から遷移するように作り直す。"""
        if self.mode != "graph" or self.graph is None:
            return
        fps = self._fps()
        t = max(0.0, cmds.currentTime(query=True) / fps) if self._mine() else 0.0
        self.timeline = M.with_event(self.timeline, round(t, 3), self._params())
        self._fill_graph_info()
        if self._mine() and K.info(COMP_NAME).get("graph"):
            self._rebuild_timer.start()  # ドラッグが止まってから 1 回だけ作り直す

    def _graph_placements(self) -> list[Seq.Placement]:
        r = M.evaluate(self.graph, self.timeline, self.total_sec.value(), self._fps())
        return M.to_placements(r, self._clips(), self._fps())

    def build_from_graph(self) -> None:
        if self.graph is None:
            self.use_graph()
        if self.graph is None:
            return
        self.mode = "graph"
        if not self.timeline:
            self.timeline = [(0.0, self._params())]
        self._guard(lambda: self._compose(self._graph_placements()))
        if self._mine():
            self._mark_graph()

    def _mark_graph(self) -> None:
        node = K.PREFIX + COMP_NAME
        if cmds.objExists(node):
            i = K.info(COMP_NAME)
            i["graph"] = True
            K._store(node, "tdInfo", i)  # noqa: SLF001

    def flush_rebuild(self) -> None:
        """待っている作り直しがあれば今すぐ行う（テスト・合成を外す前に）。"""
        if self._rebuild_timer.isActive():
            self._rebuild_timer.stop()
            self._rebuild_keeping_time()

    def _rebuild_keeping_time(self) -> None:
        """再生中の再構築: 時刻と再生状態を保ったまま、状態図の合成を作り直す。"""
        if not (self._mine() and self.graph is not None):
            return
        t = cmds.currentTime(query=True)
        playing = cmds.play(query=True, state=True)
        cmds.play(state=False)
        self._guard(lambda: self._compose(self._graph_placements()))
        self._mark_graph()
        cmds.currentTime(t, edit=True)
        if playing:
            cmds.play(forward=True)

    def _layer_weight_moved(self) -> None:
        if self._mine():
            K.set_layer_weight(COMP_NAME, 0, self.layer_weight.value() / 100.0)

    def _remember_speed(self) -> None:
        if self._saved_speed is None:
            self._saved_speed = (cmds.playbackOptions(query=True, playbackSpeed=True), cmds.playbackOptions(query=True, maxPlaybackSpeed=True))

    def play(self) -> None:
        self._remember_speed()  # 合成を作らずに再生しても、再生の速さを戻せるように控える（SF-17）
        cmds.playbackOptions(loop="continuous" if self.loop_cb.isChecked() else "once", playbackSpeed=self.play_speed.currentData(), maxPlaybackSpeed=1)
        cmds.currentTime(cmds.playbackOptions(query=True, minTime=True), edit=True)
        cmds.play(forward=True)

    def stop(self) -> None:
        cmds.play(state=False)

    def _restore_speed(self) -> None:
        if self._saved_speed is not None:
            cmds.playbackOptions(playbackSpeed=self._saved_speed[0], maxPlaybackSpeed=self._saved_speed[1])
            self._saved_speed = None

    def remove(self) -> None:
        self._rebuild_timer.stop()
        self.stop()
        n = K.remove(COMP_NAME)
        self._restore_speed()
        warn = "".join(f"\n⚠ {w}" for w in K.last_warnings)
        self._say(("合成を外しました（元のカーブ・再生範囲に戻しました）" if n else "外す合成はありません") + warn)
        self.refresh()

    def detach(self) -> None:
        self._rebuild_timer.stop()
        if cmds.play(query=True, state=True):
            cmds.play(state=False)
        if self._mine():
            self.remove()  # 閉じるときは合成を外して、保存したシーンに合成が残らないようにする
        self._restore_speed()  # 合成を作らずに再生しただけでも、再生の速さを戻す
