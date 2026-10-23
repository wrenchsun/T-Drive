"""ブレンドタブ（docs/20 §4.4、チケット AN-4 / AN-5）: 遷移列の表・状態図のパラメータ・UpperBody レイヤー・合成の生成 / 解除・再生。

- 2 つの入口: ① 遷移列を手で並べる ② 「MS2026 の状態図」のパラメータ（Speed / ActionState）を動かす（再生中に効く）
- 合成の方式は `composition.py`（pairBlend の連鎖。Time Editor は使わない）。DirectX 11 には依存しない
- 操作感は Phase E に合わせる: 右クリックのコピー / 貼り付け・1 操作 = 1 Undo（animset の変更）・ツールチップに範囲と既定値・壊れた入力は警告だけ
"""

from __future__ import annotations

import contextlib
import copy
import json
import traceback

from maya import cmds
from PySide6 import QtCore, QtWidgets

from tdrive import lifecycle, widgets as WG

from . import composition as K
from . import scene_naming as SN
from . import session
from . import transport as T
from .ui_common import ComposeProgress, MessageLabel
from .ui_timeline import ParamTimeline
from .core import animset as A
from .core import sequence as Seq
from .core import state_machine as M

COMP_NAME = "blend"
ROWS_FORMAT = "TDriveBlendRows"
NEED_ANIMSET = "先に animset を作ってください（上の ModelId 欄 → 新規）"
COL_CLIP, COL_HOLD, COL_FADE, COL_SWITCH, COL_SPEED = range(5)
HEADERS = ["クリップ", "保持（秒）", "クロスフェード（秒）", "切替位置", "速度"]


HUD_NAME = "tdAnimMixHUD"
TIMELINE_KEY = "tdAnimParamTimeline"  # 記録したパラメータ（プレビュー用。animset には入れない。シーンの fileInfo に JSON で持つ。AN-F-29）


def _hud_show(text: str) -> None:
    """ビューポートの HUD にも今のブレンドの割合を出す（再生中も更新される。合成中だけ。AN-F-22）。UI の無い環境（batch）では何もしない。"""
    try:
        if cmds.headsUpDisplay(HUD_NAME, exists=True):
            cmds.headsUpDisplay(HUD_NAME, edit=True, label=text)
        else:
            cmds.headsUpDisplay(HUD_NAME, section=7, block=cmds.headsUpDisplay(nextFreeBlock=7), blockSize="medium", label=text, labelFontSize="large")
    except (RuntimeError, TypeError):
        pass


def _hud_hide() -> None:
    with contextlib.suppress(RuntimeError, TypeError):
        if cmds.headsUpDisplay(HUD_NAME, exists=True):
            cmds.headsUpDisplay(HUD_NAME, remove=True)


lifecycle.on_reload(_hud_hide)

_active_tab: "BlendTab | None" = None


@lifecycle.guarded()
def _cb_time_changed() -> None:
    tab = _active_tab
    if tab is not None:
        tab.update_mix()


lifecycle.keep_alive(_cb_time_changed)


def _reload_cleanup() -> None:
    tab = _active_tab
    if tab is not None:
        with contextlib.suppress(RuntimeError):  # 画面が先に消えていても止めない
            tab._sync_mix(False)


lifecycle.on_reload(_reload_cleanup)


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
        self._mix_placements: list[Seq.Placement] = []
        self._pending = False  # 記録を変えたのにまだ作り直していない（AN-F-29）
        self._syncing_table = False
        self._timeline_loaded = False
        self._mix_job: int | None = None
        self._saved_speed: tuple[float, float] | None = None
        self._rebuild_timer = QtCore.QTimer(self)  # Speed スライダーのドラッグ中は作り直さず、止まってからまとめて 1 回（SF-5）
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(150)
        self._rebuild_timer.timeout.connect(lambda *_: self._rebuild_keeping_time())
        T.register(self._stop_timers, self._timer_name)
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
        self.pending_label = QtWidgets.QLabel()  # 記録を変えたのにまだ作り直していないとき（AN-F-29）
        self.pending_label.setStyleSheet("background-color: #6a4210; color: #ffe2b0; padding: 4px; border-radius: 3px;")
        self.pending_label.setWordWrap(True)
        self.pending_label.setVisible(False)
        v.addWidget(self.pending_label)
        self.mix_label = QtWidgets.QLabel()  # 合成中の今のブレンドの割合（タイムスライダーを動かすと変わる。AN-F-5）
        self.mix_label.setStyleSheet("color: #f0a040;")
        v.addWidget(self.mix_label)

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
        hint = QtWidgets.QLabel("表のセルをダブルクリックすると編集できます（クリップはプルダウンで選びます）")
        hint.setStyleSheet("color: gray;")
        bv.addWidget(hint)
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

        # ---- 状態図
        gbox = QtWidgets.QGroupBox("状態図（Idle / Run / Jump）をパラメータで動かす")
        gv = QtWidgets.QVBoxLayout(gbox)
        form = QtWidgets.QFormLayout()
        self.speed = WG.Slider(QtCore.Qt.Horizontal)
        self.speed.setRange(0, 100)
        self.speed.setToolTip("Speed（0〜1）。0.1 を境に Idle ⇄ Run が切り替わります（この状態図では速さの違いは出ません。速さのブレンドは AN-11）。"
                              "ドラッグを離したときに記録されます。再生中に動かすと、その時刻から遷移します")
        self.speed_label = QtWidgets.QLabel("0.00")
        self.speed.valueChanged.connect(lambda *_: self._speed_moved())
        self.speed.sliderReleased.connect(lambda *_: self.on_param_changed())  # 記録はドラッグを離したときだけ（AN-F-27）
        sp = QtWidgets.QHBoxLayout()
        sp.addWidget(self.speed, 1)
        sp.addWidget(self.speed_label)
        self.speed_spin = WG.DoubleSpinBox()  # 数値で入れる（Enter / フォーカスを外したときに記録。AN-F-33）
        self.speed_spin.setRange(0.0, 1.0)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setToolTip("Speed を数値で入れます（0.00〜1.00）。Enter かフォーカスを外すと記録されます。0.1 を境に Idle ⇄ Run")
        self.speed_spin.editingFinished.connect(lambda *_: self._speed_typed())
        sp.addWidget(self.speed_spin)
        form.addRow("Speed", sp)
        self.action_state = WG.ComboBox()
        for name, val in M.ACTION_STATES.items():
            self.action_state.addItem(f"{name} ({val})", val)
        self.action_state.setToolTip("ActionState。Jump (2) / Fall (3) にすると、Idle・Run から Jump へ遷移します。Jump 以外に戻すと、Speed に応じて Idle か Run に戻ります（AC_PlayerPreview と同じ）")
        self.action_state.activated.connect(lambda *_: self.on_param_changed())
        form.addRow("ActionState", self.action_state)
        self.total_sec = WG.DoubleSpinBox()
        self.total_sec.setRange(1.0, 120.0)
        self.total_sec.setValue(8.0)
        self.total_sec.setSuffix(" 秒")
        self.total_sec.setToolTip("合成する長さ。範囲: 1〜120 秒。既定: 記録の最後の時刻 + 2 秒（手で変えるとそのまま）")
        self._total_manual = False  # 手で変えたら、記録の最後 + 2 秒への自動追従を止める
        self.total_sec.valueChanged.connect(lambda *_: self._on_total_changed())
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
        self.timeline_view = ParamTimeline()  # Maya のタイムスライダーと同期したタイムライン（AN-F-29）
        self.timeline_view.selectionChanged.connect(self._on_marker_selected)
        self.timeline_view.markerMoved.connect(self._on_marker_moved)
        self.timeline_view.deleteRequested.connect(self._on_marker_delete)
        self.timeline_view.addRequested.connect(self._on_marker_add)
        self.timeline_view.scrubRequested.connect(self._on_scrub)
        gv.addWidget(self.timeline_view)
        tl_row = QtWidgets.QHBoxLayout()
        self.add_marker_btn = QtWidgets.QPushButton("現在フレームに追加")
        self.add_marker_btn.setToolTip("今のタイムスライダーのフレームに、いまの Speed と ActionState の値を記録します")
        self.add_marker_btn.clicked.connect(lambda *_: self._add_marker_here())
        tl_row.addWidget(self.add_marker_btn)
        self.auto_rebuild = QtWidgets.QCheckBox("変更のたびに作り直す")
        self.auto_rebuild.setToolTip("オフ（既定）: 記録だけして、「作り直す」を押したときに 1 回だけ合成を作ります（まとめて打ってから反映できます）。オンにすると、記録を変えるたびに作り直します")
        tl_row.addWidget(self.auto_rebuild)
        self.rebuild_btn = QtWidgets.QPushButton("作り直す")
        self.rebuild_btn.setToolTip("記録したパラメータで、状態図の合成を作り直します（現在の時刻と再生状態は保ちます）")
        self.rebuild_btn.clicked.connect(lambda *_: self.rebuild_now())
        tl_row.addWidget(self.rebuild_btn)
        tl_row.addStretch(1)
        gv.addLayout(tl_row)
        self.timeline_table = QtWidgets.QTableWidget(0, 4)  # 時刻 / パラメータ / 値 / その結果の状態（読み取り専用。タイムラインの選択と同期）
        self.timeline_table.setHorizontalHeaderLabels(["時刻", "パラメータ", "値", "その結果の状態"])
        self.timeline_table.horizontalHeader().setStretchLastSection(True)
        self.timeline_table.verticalHeader().setVisible(False)
        self.timeline_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.timeline_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.timeline_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.timeline_table.setMinimumHeight(90)
        self.timeline_table.setMaximumHeight(170)
        self.timeline_table.setToolTip("記録の一覧です（最大 50 行。古いものは畳みます）。選ぶとタイムラインのマーカーも選ばれます")
        self.timeline_table.itemSelectionChanged.connect(self._on_table_selected)
        gv.addWidget(self.timeline_table)
        self.switch_label = QtWidgets.QLabel()  # 評価してできた遷移列（読み取り専用）
        self.switch_label.setWordWrap(True)
        self.switch_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        gv.addWidget(self.switch_label)
        self.graph_reset_btn = QtWidgets.QPushButton("状態図を既定に戻す")
        self.graph_reset_btn.setToolTip("animset の状態図を既定（Idle / Run / Jump）に作り直します（記録したパラメータは残ります）")
        self.graph_reset_btn.clicked.connect(lambda *_: self.reset_graph())
        gv.addWidget(self.graph_reset_btn, 0, QtCore.Qt.AlignLeft)
        v.addWidget(gbox)

        # ---- レイヤー
        lbox = QtWidgets.QGroupBox("レイヤー（上半身だけ別のクリップを重ねる）")
        lf = QtWidgets.QFormLayout(lbox)
        self.layer_on = QtWidgets.QCheckBox("UpperBody を使う")
        self.layer_on.setToolTip("animset のレイヤー UpperBody の maskRoot（既定 spine_C0_3_jnt）以下のジョイントにだけ、下のクリップを重ねます")
        lf.addRow(self.layer_on)
        self.layer_clip = WG.ComboBox()
        self.layer_clip.setToolTip("上半身に重ねるクリップ（ループします）")
        lf.addRow("クリップ", self.layer_clip)
        self.layer_weight = WG.Slider(QtCore.Qt.Horizontal)
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
        self.hide_ctl_cb = QtWidgets.QCheckBox("コントローラを隠す")  # 合成中はコントローラは元のキーのポーズのままで合わないので隠す（既定オン。AN-F-37）
        self.hide_ctl_cb.setChecked(True)
        self.hide_ctl_cb.setToolTip("合成中は、リグのコントローラ（rig_controllers_grp、無ければ *_ctl）を非表示にします。外すと元の表示に戻ります。次に合成を作るときから効きます。既定: ON")
        self.loop_cb = QtWidgets.QCheckBox("ループ")
        self.loop_cb.setChecked(True)
        self.loop_cb.setToolTip("ON: ループ再生／ OFF: 1 回だけ。既定: ON")
        self.play_speed = WG.ComboBox()
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
        for w in (self.play_btn, self.stop_btn, self.loop_cb, self.hide_ctl_cb, self.play_speed, self.remove_btn, self.shot_btn):
            ph.addWidget(w)
        v.addWidget(pbox)
        self.message = MessageLabel()
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

    # ------------------------------------------------------------ 今のブレンドの割合（AN-F-5）
    def _sync_mix(self, active: bool) -> None:
        """合成中だけ timeChanged の scriptJob を持ち、割合を表示する。"""
        global _active_tab
        if active and self._mix_job is None:
            _active_tab = self
            try:
                self._mix_job = cmds.scriptJob(event=["timeChanged", _cb_time_changed])
            except RuntimeError:
                self._mix_job = None
        elif not active and self._mix_job is not None:
            with contextlib.suppress(RuntimeError):
                cmds.scriptJob(kill=self._mix_job, force=True)
            self._mix_job = None
            if _active_tab is self:
                _active_tab = None
        self.update_mix()

    def update_mix(self) -> None:
        self.timeline_view.set_current_frame(cmds.currentTime(query=True))
        if not self._mine() or not self._mix_placements:
            self.mix_label.setText("")
            _hud_hide()
            return
        text = "今のブレンド: " + Seq.mix_text(self._mix_placements, cmds.currentTime(query=True))
        self.mix_label.setText(text)
        self.mix_label.repaint()  # 再生中・ドラッグ中は Qt の再描画が走りにくいので明示する（AN-F-22）
        _hud_show(text)

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
        for w in (self.rows_table, self.build_rows_btn, self.graph_reset_btn, self.build_graph_btn, self.play_btn, self.layer_on, self.layer_clip):
            w.setEnabled(has)
        act = self._mine()
        if act:
            i = K.info(act[0])
            self.banner.setText(f"⚠ 合成中: {K.PREFIX}{act[0]}（{i.get('frames', 0):.0f} フレーム、{i.get('joints', 0)} ジョイント）。"
                                "ジョイントは合成につながっています。シーンを保存しても合成は保存されません（保存の間だけ元のつながりに戻します）。"
                                "合成中はジョイントにキーを打たないでください（オートキーは切ってあります）。使い終わったら「合成を外す」"
                                + (f"\n合成中はコントローラは動きません（{i.get('hiddenControllers')} 個を非表示にしています。外すと戻ります）" if i.get("hiddenControllers") else ""))
        else:
            self.banner.setText("")
        self.banner.setVisible(bool(act))
        self._sync_mix(bool(act))
        self.remove_btn.setEnabled(bool(act))
        self.shot_btn.setEnabled(self.session.shot is not None and bool(self.session.shot["characters"]))
        if not has:
            self.rows = []
            self._fill_rows()
            self._reset_graph_section()  # animset を閉じたら、状態図・記録・タイムラインも初期表示に（AN-F-44）
            self._say(NEED_ANIMSET)
            return
        self._load_timeline()  # 開き直したシーンの記録を戻す（AN-F-29）
        if self.timeline and self.graph is None and isinstance(d_ := self.session.data.get("stateGraph"), dict):
            self.graph, self.mode = d_, "graph"
            self._fill_graph_info()
        if self.message.text() == NEED_ANIMSET:  # animset を作ったら案内を消す（AN-F-1）
            self._say("")
        d = self.session.data
        names = list(self._clips())
        if [self.layer_clip.itemText(i) for i in range(self.layer_clip.count())] != names:
            self.layer_clip.clear()
            self.layer_clip.addItems(names)
        if not self.rows or self.rows == self._default_rows:  # 状態図モードでも、遷移列の表は空にしない（リロード直後に空に見えた。AN-F-43）  # 未編集の既定の列は、クリップの増減に追従する
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
                combo = WG.ComboBox()
                combo.addItems(names)
                combo.setCurrentText(row["clip"])
                combo.activated.connect(lambda *_, rr=r, c=combo: self._set(rr, "clip", c.currentText()))
                combo.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)  # 埋め込みコンボが右クリックを受けてしまうので、行メニューへ転送する（AN-F-40）
                combo.customContextMenuRequested.connect(lambda pos, rr=r, c=combo: self._combo_menu(rr, c, pos))
                self.rows_table.setCellWidget(r, COL_CLIP, combo)
                for col, key, val in ((COL_HOLD, "holdSec", row.get("holdSec", 0)), (COL_FADE, "crossFadeSec", Seq.fade_sec(row, self._clips().get(row["clip"], {}))),
                                      (COL_SWITCH, "switchAt", row.get("switchAt", 1.0)), (COL_SPEED, "speed", row.get("speed", 1.0))):
                    self.rows_table.setItem(r, col, QtWidgets.QTableWidgetItem(f"{val:g}"))
            self.rows_table.resizeColumnsToContents()
        finally:
            self._building = False
        self.rows_table.updateGeometries()
        self.rows_table.viewport().repaint()  # 作り直した直後に表が古いまま見える（再描画されない）ことがあった（AN-F-20）

    def _fill_graph_info(self) -> None:
        if self.graph is None:
            self.graph_info.setText("スライダーを動かすか「状態図で合成を作る」を押すと、既定の状態図（Idle / Run / Jump）で動かします")
            self.timeline_table.setRowCount(0)
            self.timeline_view.set_data([], [], self.total_sec.value(), self._fps())
            self.switch_label.setText("")
            return
        probs = M.validate(self.graph, self.session.data["clips"] if self.session.data else [])
        states = " / ".join(f"{s['name']}={s.get('clip') or '（未割り当て）'}" for s in self.graph.get("states", []))
        self.graph_info.setText(f"状態: {states}" + ("\n" + "\n".join("⚠ " + p for p in probs[:4]) if probs else ""))
        self._fill_timeline_view()

    def _fill_timeline_view(self) -> None:
        """記録の表と、評価した遷移列（読み取り専用）。評価できない状態図のときは空にする。"""
        self._auto_total()
        try:
            r = M.evaluate(self.graph, self.timeline, self.total_sec.value(), self._fps())
        except Exception:  # noqa: BLE001  壊れた状態図は上の警告で知らせる
            self.timeline_table.setRowCount(0)
            self.timeline_view.set_data([], [], self.total_sec.value(), self._fps())
            self.switch_label.setText("")
            return
        rows = M.marker_rows(self.timeline, r, self._fps())
        ms = M.markers(self.timeline)
        self._syncing_table = True
        try:
            self.timeline_table.setRowCount(len(rows))
            offset = len(rows) - len(ms)  # 古い記録を畳んだときは先頭に 1 行ある
            for i, cells in enumerate(rows):
                for j, text in enumerate(cells):
                    self.timeline_table.setItem(i, j, QtWidgets.QTableWidgetItem(text))
                if i >= offset >= 0 and i - offset < len(ms):  # 時刻（秒）を覚えておく（タイムラインの選択との同期に使う）
                    self.timeline_table.item(i, 0).setData(QtCore.Qt.UserRole, ms[i - offset][0])
        finally:
            self._syncing_table = False
        self.timeline_view.set_data(M.markers(self.timeline), M.state_bands(r, self.total_sec.value()), self.total_sec.value(), self._fps())
        self.timeline_view.set_current_frame(cmds.currentTime(query=True))
        self.switch_label.setText("評価した遷移列: " + M.describe_switches(r))

    def _auto_total(self) -> None:
        """「長さ」の既定は記録の最後の時刻 + 2 秒（手で変えたあとは触らない）。"""
        if self._total_manual or not self.timeline:
            return
        want = min(120.0, max(1.0, max(t for t, _ in self.timeline) + 2.0))
        if abs(self.total_sec.value() - want) > 1e-9:
            self.total_sec.blockSignals(True)
            self.total_sec.setValue(want)
            self.total_sec.blockSignals(False)

    def _on_total_changed(self) -> None:
        self._total_manual = True
        if self.graph is not None:
            self._fill_timeline_view()

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

    def _combo_menu(self, row: int, combo: QtWidgets.QWidget, pos) -> None:
        """表の中のコンボの上の右クリック: その行を選んで、表と同じ行メニューを出す。"""
        self.rows_table.selectRow(row)
        self._menu(combo.mapTo(self.rows_table.viewport(), pos))

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

    def _compose(self, placements: list[Seq.Placement], use_cache: bool = False) -> None:
        self._remember_speed()
        progress = ComposeProgress(self)
        try:
            res = K.compose(COMP_NAME, placements, self._skeleton(), self._fps(), self._layers(Seq.total_frames(placements)), progress=progress, use_cache=use_cache, hide_ctls=self.hide_ctl_cb.isChecked())
        except K.ComposeCancelled:
            self._say("合成の作成を中止しました（合成は作っていません）")
            self.refresh()
            return
        finally:
            progress.close()
        cmds.playbackOptions(loop="continuous" if self.loop_cb.isChecked() else "once")
        warns = "".join(f"\n⚠ {w}" for w in [*res.warnings, *Seq.overlap_warnings(placements, self._fps())])
        self._mix_placements = list(placements)
        t = res.timings
        took = f"（{t.get('total', 0):.1f} 秒: 読み取り {t.get('sample', 0):.1f} / 切り離し {t.get('detach', 0):.1f} / ノード作成 {t.get('chain', 0):.1f}）" if t else ""
        self._say(f"合成を作りました（{res.frames:.0f} フレーム、{res.joints} ジョイント、{res.plugs} チャンネル）{took}。▶ 再生で確認できます{warns}{self._fps_note()}")
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

    def _reset_graph_section(self) -> None:
        """animset が無くなった（新規シーン・閉じる）: 前のシーンの状態図・パラメータの記録・タイムライン・「長さ」を捨てて初期表示にする。"""
        self._rebuild_timer.stop()
        self.graph = None
        self.mode = "rows"
        self.timeline = []
        self._default_rows = []
        self._total_manual = False
        self._timeline_loaded = False  # 次のシーンの fileInfo から戻せるように
        self._mix_placements = []
        self._set_pending(False)
        self.total_sec.blockSignals(True)
        self.total_sec.setValue(8.0)
        self.total_sec.blockSignals(False)
        self._fill_graph_info()

    def reset_timeline(self) -> None:
        self.timeline = []
        self._total_manual = False
        self._set_pending(False)
        self._save_timeline()
        self._fill_graph_info()

    def reset_graph(self) -> None:
        """animset の状態図を既定に作り直す（AN-F-28）。"""
        d = self.session.data
        if d is None:
            return
        self._guard(self.session.set_value, "stateGraph", M.default_graph(d["clips"], d["modelId"]))
        self.graph = d.get("stateGraph")
        self._say("状態図を既定（Idle / Run / Jump）に戻しました")
        self._fill_graph_info()

    def _params(self) -> dict[str, float]:
        return {"Speed": self.speed.value() / 100.0, "ActionState": float(self.action_state.currentData())}

    def _speed_typed(self) -> None:
        """数値欄に入れた: スライダーを合わせて記録する（AN-F-33）。"""
        v = int(round(self.speed_spin.value() * 100))
        self.speed.blockSignals(True)
        self.speed.setValue(v)
        self.speed.blockSignals(False)
        self.speed_label.setText(f"{v / 100.0:.2f}")
        self.on_param_changed()

    def _speed_moved(self) -> None:
        self.speed_label.setText(f"{self.speed.value() / 100.0:.2f}")
        if not self.speed_spin.hasFocus():  # スライダーを動かしたら数値欄も合わせる
            self.speed_spin.blockSignals(True)
            self.speed_spin.setValue(self.speed.value() / 100.0)
            self.speed_spin.blockSignals(False)
        if not self.speed.isSliderDown():  # ドラッグ中は記録しない（離したとき sliderReleased で 1 回。キー操作・ホイール・プログラムからの変更はここで記録）
            self.on_param_changed()

    def on_param_changed(self) -> None:
        """パラメータを動かした: 今の時刻の変更として記録する。状態図の合成が出ていれば、その時刻から遷移するように作り直す。"""
        if self.session.data is None:
            return
        if self.mode != "graph" or self.graph is None:
            self.use_graph()  # 「MS2026 の状態図から」ボタンは廃止。パラメータを動かしたら既定の状態図を自動で使う（AN-F-28）
        if self.graph is None:
            return
        fps = self._fps()
        t = max(0.0, cmds.currentTime(query=True) / fps) if self._mine() else 0.0
        t = round(round(t * fps) / fps, 3)  # 同じフレームの記録は上書き（AN-F-27）
        self.timeline = M.with_event(self.timeline, t, self._params())
        self._commit_timeline()

    # ------------------------------------------------------------ タイムラインの編集（AN-F-29）
    def _commit_timeline(self) -> None:
        """記録を変えた: 保存し、表示を直し、作り直す（トグルがオンなら）か「作り直していません」を出す。"""
        self._save_timeline()
        self._fill_graph_info()
        if self._mine() and K.info(COMP_NAME).get("graph"):
            if self.auto_rebuild.isChecked():
                self._rebuild_timer.start()  # ドラッグが止まってから 1 回だけ作り直す
            else:
                self._set_pending(True)
        elif self._mine():
            self._say("⚠ 今の合成は遷移列から作ったものなので、スライダーを動かしても反映されません。「状態図で合成を作る」を押してください")

    def _set_pending(self, on: bool) -> None:
        self._pending = on
        self.pending_label.setText("⚠ 記録を変えましたが、まだ合成を作り直していません。「作り直す」を押すと反映されます" if on else "")
        self.pending_label.setVisible(on)

    def rebuild_now(self) -> None:
        """「作り直す」: 記録したパラメータで 1 回だけ合成を作る。合成が無ければ状態図で新しく作る。"""
        self._rebuild_timer.stop()
        if self.graph is None:
            self.use_graph()
        if self._mine() and K.info(COMP_NAME).get("graph"):
            self._rebuild_keeping_time()
        else:
            self.build_from_graph()
        self._set_pending(False)

    def _save_timeline(self) -> None:
        """記録をシーンの fileInfo に JSON で持つ（animset には入れない）。"""
        with contextlib.suppress(RuntimeError, TypeError):
            cmds.fileInfo(TIMELINE_KEY, json.dumps([[t, v] for t, v in self.timeline], ensure_ascii=False))

    def _load_timeline(self) -> None:
        """シーンの fileInfo から記録を戻す（開き直し後。記録が空のときだけ）。"""
        if self.timeline or self._timeline_loaded:
            return
        self._timeline_loaded = True
        with contextlib.suppress(RuntimeError, ValueError, TypeError, KeyError):
            raw = cmds.fileInfo(TIMELINE_KEY, query=True)
            if raw:
                data = json.loads(raw[0].replace('\\"', '"'))  # fileInfo は " を \" にして持つ
                self.timeline = [(float(t), {str(k): float(x) for k, x in v.items()}) for t, v in data]

    def _on_marker_moved(self, param: str, t_old: float, t_new: float) -> None:
        self.timeline = M.move_marker(self.timeline, param, t_old, t_new)
        self._commit_timeline()
        self.timeline_view.select(param, t_new)

    def _on_marker_delete(self, param: str, t: float) -> None:
        self.timeline = M.delete_marker(self.timeline, param, t)
        self._commit_timeline()

    def _on_marker_add(self, frame: int, lane: str) -> None:
        """ダブルクリック: そのフレームに、その段のパラメータの今の値を記録する。"""
        if self.graph is None:
            self.use_graph()
        value = self._params()[lane]
        self.timeline = M.with_event(self.timeline, round(frame / self._fps(), 3), {lane: value})
        self._commit_timeline()

    def _add_marker_here(self) -> None:
        if self.graph is None:
            self.use_graph()
        if self.graph is None:
            return
        t = round(round(max(0.0, cmds.currentTime(query=True))) / self._fps(), 3)
        self.timeline = M.with_event(self.timeline, t, self._params())
        self._commit_timeline()

    def _on_scrub(self, frame: int) -> None:
        """タイムラインのクリック / ドラッグ: Maya の現在時刻をそのフレームへ（AN-F-32）。"""
        cmds.currentTime(frame, edit=True)
        self.timeline_view.set_current_frame(frame)

    def _on_marker_selected(self, param: str, t: float) -> None:
        """タイムラインで選んだ → 表の行も選ぶ。"""
        if self._syncing_table:
            return
        self._syncing_table = True
        try:
            self.timeline_table.clearSelection()
            for i in range(self.timeline_table.rowCount()):
                it = self.timeline_table.item(i, 1)
                tt = self.timeline_table.item(i, 0)
                if it is not None and tt is not None and it.text() == param and tt.data(QtCore.Qt.UserRole) is not None and abs(float(tt.data(QtCore.Qt.UserRole)) - t) < 1e-6:
                    self.timeline_table.selectRow(i)
                    break
        finally:
            self._syncing_table = False

    def _on_table_selected(self) -> None:
        """表で選んだ → タイムラインのマーカーも選ぶ。"""
        if self._syncing_table:
            return
        rows = sorted({i.row() for i in self.timeline_table.selectedIndexes()})
        if not rows:
            self.timeline_view.select("", 0.0)
            return
        t_item, p_item = self.timeline_table.item(rows[0], 0), self.timeline_table.item(rows[0], 1)
        if t_item is not None and p_item is not None and t_item.data(QtCore.Qt.UserRole) is not None and p_item.text() in ("Speed", "ActionState"):
            self.timeline_view.select(p_item.text(), float(t_item.data(QtCore.Qt.UserRole)))

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
        self._fill_timeline_view()
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
        self._set_pending(False)
        self._say("作り直し中…")
        QtWidgets.QApplication.processEvents()
        self._guard(lambda: self._compose(self._graph_placements(), use_cache=True))  # スライダーの作り直しは、アニメーションが同じならサンプリングを使い回す（AN-F-26）
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
        T.stop_all()  # どのタブの再生・タイマーも全部止める（AN-F-35）

    def _stop_timers(self) -> None:
        self._rebuild_timer.stop()

    def _timer_name(self) -> str:
        return "作り直し待ち" if self._rebuild_timer.isActive() else ""

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
        self._sync_mix(False)
        if self._mine():
            self.remove()  # 閉じるときは合成を外して、保存したシーンに合成が残らないようにする
        self._restore_speed()  # 合成を作らずに再生しただけでも、再生の速さを戻す
