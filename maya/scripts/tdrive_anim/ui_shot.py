"""ショットタブ（docs/20 §5、チケット AN-7）: ショットの作成・カメラの区間・キャラクターのクリップ列・イベント・シーケンサー再生・動画。

- ショットは `shots/<Shot>.shot.json`（D-Drive の CutsceneData 1 つにあたる）。作った内容を Maya のシーンに反映するのが「ショットを反映」
  （再生範囲・Camera Sequencer・`EVT_` ロケータ・キャラクターの合成）
- 出力は出力タブ（AN-8）。dx11Shader・Render Override は使わない
- 操作感は Phase E に合わせる: 1 操作 = 1 Undo・ツールチップに範囲と既定値・壊れた入力は警告だけ・捨てる前の確認（右クリックメニュー・検索欄はこのタブには無い）
"""

from __future__ import annotations

import traceback

from maya import cmds
from PySide6 import QtCore, QtWidgets

from tdrive import lifecycle, project, widgets as WG

from . import composition as K
from . import scene_naming as SN
from . import session
from . import transport as T
from .ui_common import ComposeProgress, MessageLabel
from . import shot_scene as SS
from .core import animset as A
from .core import shot as SH


class ShotTab(QtWidgets.QWidget):
    def __init__(self, s: session.AnimSession) -> None:
        super().__init__()
        self.session = s
        self.blend_tab = None  # AnimPanel が渡す（ブレンドタブの遷移列を取り込む）
        self._building = False
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

        # ---- ショット
        box = QtWidgets.QGroupBox("ショット")
        f = QtWidgets.QFormLayout(box)
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText("例: Opening01")
        self.name.setToolTip("英数字の PascalCase（先頭は大文字。「__」は使えません）。ファイル名 <Shot>.fbx と <Shot>__<ModelId>.fbx になります")
        f.addRow("名前", self.name)
        self.fps = WG.ComboBox()
        self.fps.addItem("30 fps", 30)
        self.fps.addItem("60 fps", 60)
        self.fps.setToolTip("D-Drive は 30 か 60。1 ショットの中は同じにします。シーンの時間単位は変えません（違うときは警告するので、命名タブの「直す」で合わせます）。既定: 30")
        f.addRow("フレームレート", self.fps)
        self.start = WG.SpinBox()
        self.start.setRange(-100000, 1000000)
        self.end = WG.SpinBox()
        self.end.setRange(-100000, 1000000)
        self.end.setValue(240)
        self.start.setToolTip("ショットの開始フレーム。既定: 0")
        self.end.setToolTip("ショットの終了フレーム（含む）。既定: 240")
        rr = QtWidgets.QHBoxLayout()
        rr.addWidget(self.start)
        rr.addWidget(QtWidgets.QLabel("〜"))
        rr.addWidget(self.end)
        f.addRow("範囲", rr)
        row = QtWidgets.QHBoxLayout()
        self.create_btn = QtWidgets.QPushButton("新規ショット")
        self.create_btn.setToolTip("この名前・フレームレート・範囲で新しいショットを作ります（保存するまでファイルは書きません。シーンの時間単位は変えません）")
        self.create_btn.clicked.connect(lambda *_: self.create())
        self.open_btn = QtWidgets.QPushButton("開く…")
        self.open_btn.setToolTip("既存の shot.json を開きます（未保存の変更は捨てます）")
        self.open_btn.clicked.connect(lambda *_: self.open_dialog())
        self.save_btn = QtWidgets.QPushButton("保存")
        self.save_btn.setToolTip("shots/<Shot>.shot.json に保存します（シーンの保存でも自動で保存されます）")
        self.save_btn.clicked.connect(lambda *_: self.save())
        for b in (self.create_btn, self.open_btn, self.save_btn):
            row.addWidget(b)
        f.addRow(row)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        f.addRow(self.info)
        v.addWidget(box)

        # ---- カメラ
        cbox = QtWidgets.QGroupBox("カメラの区間（Camera Sequencer に同期）")
        cv = QtWidgets.QVBoxLayout(cbox)
        self.cam_table = QtWidgets.QTableWidget(0, 3)
        self.cam_table.setHorizontalHeaderLabels(["カメラ", "開始", "終了"])
        self.cam_table.horizontalHeader().setStretchLastSection(True)
        self.cam_table.verticalHeader().setVisible(False)
        self.cam_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.cam_table.setMaximumHeight(120)
        for r in (self.cam_table,):
            r.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        cv.addWidget(self.cam_table)
        cr = QtWidgets.QHBoxLayout()
        self.cam_combo = WG.ComboBox()
        self.cam_combo.setToolTip("シーンのカメラ（標準の persp などは除く）")
        self.cam_start = WG.SpinBox()
        self.cam_start.setRange(-100000, 1000000)
        self.cam_start.setToolTip("カメラを使い始めるフレーム。既定: ショットの開始")
        self.cam_end = WG.SpinBox()
        self.cam_end.setRange(-100000, 1000000)
        self.cam_end.setToolTip("カメラを使い終わるフレーム（含む）。既定: ショットの終了")
        self.cam_add = QtWidgets.QPushButton("区間を追加")
        self.cam_add.setToolTip("選んだカメラを、開始〜終了の区間で使います。ショットの範囲に収めてください")
        self.cam_add.clicked.connect(lambda *_: self.add_camera())
        self.cam_del = QtWidgets.QPushButton("削除")
        self.cam_del.setToolTip("表で選んだカメラの区間を外します（Ctrl+Z で戻せます）")
        self.cam_del.clicked.connect(lambda *_: self.remove_camera())
        for w in (self.cam_combo, self.cam_start, self.cam_end, self.cam_add, self.cam_del):
            cr.addWidget(w)
        cv.addLayout(cr)
        v.addWidget(cbox)

        # ---- キャラクター
        hbox = QtWidgets.QGroupBox("キャラクター（クリップ列。ブレンドタブの列を入れられます）")
        hv = QtWidgets.QVBoxLayout(hbox)
        self.char_table = QtWidgets.QTableWidget(0, 4)
        self.char_table.setHorizontalHeaderLabels(["ModelId", "ネームスペース", "クリップ列", "顔の .fctrack"])
        self.char_table.horizontalHeader().setStretchLastSection(True)
        self.char_table.verticalHeader().setVisible(False)
        self.char_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.char_table.setMaximumHeight(120)
        self.char_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        hv.addWidget(self.char_table)
        hr = QtWidgets.QHBoxLayout()
        self.ns_edit = QtWidgets.QLineEdit()
        self.ns_edit.setPlaceholderText("ネームスペース（無ければ空）")
        self.ns_edit.setToolTip("リグのネームスペース。カットシーンの出力では ModelId と同じにします（D-Drive の規則）。既定: ModelId")
        self.char_add = QtWidgets.QPushButton("今の animset のキャラクターを足す")
        self.char_add.clicked.connect(lambda *_: self.add_character())
        self.char_seq = QtWidgets.QPushButton("ブレンドタブの列を入れる")
        self.char_seq.setToolTip("ブレンドタブの遷移列を、選んだキャラクターのクリップ列にします")
        self.char_seq.clicked.connect(lambda *_: self.send_rows_from_blend())
        self.char_face = QtWidgets.QPushButton("顔の .fctrack を切り替え")
        self.char_face.setToolTip("FacialController のプレビューにキーがあるとき、出力時に .fctrack も書きます（AN-8）")
        self.char_face.clicked.connect(lambda *_: self.toggle_face())
        self.char_del = QtWidgets.QPushButton("削除")
        self.char_del.setToolTip("表で選んだキャラクターを外します（Ctrl+Z で戻せます）")
        self.char_del.clicked.connect(lambda *_: self.remove_character())
        for w in (self.ns_edit, self.char_add, self.char_seq, self.char_face, self.char_del):
            hr.addWidget(w)
        hv.addLayout(hr)
        v.addWidget(hbox)

        # ---- イベント
        ebox = QtWidgets.QGroupBox("イベント（EVT_<キー> のロケータ。D-Drive は未対応のため Signal マーカーにはなりません）")
        ev = QtWidgets.QVBoxLayout(ebox)
        self.ev_table = QtWidgets.QTableWidget(0, 2)
        self.ev_table.setHorizontalHeaderLabels(["フレーム", "キー"])
        self.ev_table.horizontalHeader().setStretchLastSection(True)
        self.ev_table.verticalHeader().setVisible(False)
        self.ev_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.ev_table.setMaximumHeight(100)
        self.ev_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        ev.addWidget(self.ev_table)
        er = QtWidgets.QHBoxLayout()
        self.ev_key = QtWidgets.QLineEdit()
        self.ev_key.setPlaceholderText("キー（例: Hit）")
        self.ev_add = QtWidgets.QPushButton("現在フレームに追加")
        self.ev_add.setToolTip("タイムスライダーの現在フレームにイベントを足します。同じキーを複数回置くと EVT_Hit1 のように番号が付き、D-Drive では別のキーになるので警告します")
        self.ev_add.clicked.connect(lambda *_: self.add_event())
        self.ev_del = QtWidgets.QPushButton("削除")
        self.ev_del.setToolTip("表で選んだイベントを外します（Ctrl+Z で戻せます）")
        self.ev_del.clicked.connect(lambda *_: self.remove_event())
        for w in (self.ev_key, self.ev_add, self.ev_del):
            er.addWidget(w)
        ev.addLayout(er)
        v.addWidget(ebox)

        # ---- 反映・再生
        pbox = QtWidgets.QGroupBox("シーンへ反映・再生")
        pv = QtWidgets.QHBoxLayout(pbox)
        self.build_btn = QtWidgets.QPushButton("ショットを反映")
        self.build_btn.setToolTip("再生範囲をショットの範囲にし、カメラの区間・イベントのロケータ・キャラクターのクリップ列の合成をシーンに作ります。元のカーブは壊しません")
        self.build_btn.clicked.connect(lambda *_: self.build())
        self.remove_btn = QtWidgets.QPushButton("合成を外す")
        self.remove_btn.setToolTip("ショットがシーンに作ったもの（合成・shot ノード・イベントのロケータ）を外し、再生範囲を戻します")
        self.remove_btn.clicked.connect(lambda *_: self.remove())
        self.play_btn = QtWidgets.QPushButton("▶ シーケンサー再生")
        self.play_btn.setToolTip("ショットの範囲を 1 回再生します（カメラの区間にあわせてビューポートのカメラが切り替わります）。ツールの再生なので Esc は効きません。■ で止めます")
        self.play_btn.clicked.connect(lambda *_: self.play())
        self.stop_btn = QtWidgets.QPushButton("■")
        self.stop_btn.setToolTip("再生を止めます")
        self.stop_btn.clicked.connect(lambda *_: T.stop_all())  # どのタブの再生・タイマーも全部止める（AN-F-35）
        self.movie_btn = QtWidgets.QPushButton("動画を撮る")
        self.movie_btn.setToolTip("captures/shots/<Shot>.mov に playblast します（ビューポートが必要）")
        self.movie_btn.clicked.connect(lambda *_: self.capture())
        self.hand_btn = QtWidgets.QPushButton("手付けキーのレイヤー（準備中）")
        self.hand_btn.setToolTip("準備中: 合成中はコントローラに打ったキーがジョイントに届かず、出力にも入らないため使えません（docs/21 SP-2）。\n手付けのキーは、合成を外した状態のクリップ側に打ってください")
        self.hand_btn.clicked.connect(lambda *_: self.make_hand_layer())
        for w in (self.build_btn, self.remove_btn, self.play_btn, self.stop_btn, self.movie_btn, self.hand_btn):
            pv.addWidget(w)
        v.addWidget(pbox)
        self.message = MessageLabel()
        v.addWidget(self.message)
        v.addStretch(1)
        self.refresh()

    # ------------------------------------------------------------ 共通
    def _say(self, text: str) -> None:
        self.message.setText(text)

    def _guard(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except K.ComposeCancelled:
            self._say("ショットの反映を中止しました（何も作っていません）")
        except (SH.ShotError, A.AnimSetError, ValueError) as exc:
            self._say(f"⚠ {exc}")
        except Exception as exc:  # noqa: BLE001
            lifecycle.report_error("ショットタブの操作で例外", traceback.format_exc())
            self._say(f"⚠ {exc}")
        return None

    def _shot(self) -> dict | None:
        return self.session.shot

    def _selected(self, table: QtWidgets.QTableWidget) -> int:
        rows = sorted({i.row() for i in table.selectedIndexes()})
        return rows[0] if rows else -1

    # ------------------------------------------------------------ 表示
    def refresh(self) -> None:
        s = self._shot()
        has = s is not None
        for w in (self.save_btn, self.cam_add, self.cam_del, self.char_add, self.char_seq, self.char_face, self.char_del, self.ev_add, self.ev_del,
                  self.build_btn, self.remove_btn, self.play_btn, self.stop_btn, self.movie_btn):
            w.setEnabled(has)
        self.hand_btn.setEnabled(False)  # 準備中（SP-2）
        self.char_add.setEnabled(has and self.session.data is not None)
        self.create_btn.setEnabled(True)
        comps = SS.compositions(s["shot"]) if has else []
        self.banner.setText(f"⚠ ショットの合成中: {', '.join(K.PREFIX + c for c in comps)}。ジョイントは合成につながっています。シーンを保存しても合成は保存されません（保存の間だけ元のつながりに戻します）。合成中はジョイントにキーを打たないでください。使い終わったら「合成を外す」" if comps else "")
        self.banner.setVisible(bool(comps))
        if not has:
            self.info.setText("ショットが開かれていません。名前を入れて「新規ショット」か、「開く…」で既存のものを選んでください")
            for t in (self.cam_table, self.char_table, self.ev_table):
                t.setRowCount(0)
            return
        where = project.to_project_path(self.session.shot_path) if self.session.shot_path else "（未保存）"
        issues = SH.validate(s)
        self.info.setText(f"{s['shot']}　{s['frameRate']} fps　{s['range'][0]}〜{s['range'][1]}\nshot.json: {where}{' *' if self.session.shot_dirty else ''}"
                          + ("".join(f"\n{i}" for i in issues[:4]) if issues else ""))
        cams = SS.scene_cameras()
        if [self.cam_combo.itemText(i) for i in range(self.cam_combo.count())] != cams:
            self.cam_combo.clear()
            self.cam_combo.addItems(cams)
        if not self.cam_start.hasFocus() and not self.cam_end.hasFocus():
            self.cam_start.setValue(s["range"][0])
            self.cam_end.setValue(s["range"][1])
        self._fill(self.cam_table, [(c["node"], str(c["start"]), str(c["end"])) for c in s["cameras"]])
        self._fill(self.char_table, [(c["modelId"], c.get("namespace", ""), f"{len(c['sequence'])} 行", "あり" if c.get("facialTrack") else "なし") for c in s["characters"]])
        self._fill(self.ev_table, [(str(e["frame"]), e["key"]) for e in s["events"]])

    @staticmethod
    def _fill(table: QtWidgets.QTableWidget, rows: list[tuple]) -> None:
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                table.setItem(r, c, QtWidgets.QTableWidgetItem(text))
        table.resizeColumnsToContents()

    # ------------------------------------------------------------ ショット
    def _confirm_discard(self, what: str) -> bool:
        """未保存のショットを捨ててよいか（既定「いいえ」。UX-4）。"""
        if not (self.session.shot is not None and self.session.shot_dirty):
            return True
        r = QtWidgets.QMessageBox.question(self, "T-Drive Animation", f"未保存のショットの変更があります。{what}と、その変更は失われます。続けますか？",
                                           QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        return r == QtWidgets.QMessageBox.Yes

    def _release_current(self) -> None:
        """今のショットがシーンに作った合成・shot ノード・ロケータを外す（別のショットに切り替える前に。残すと前のショットのジョイントの合成が残る）。"""
        s = self._shot()
        if s is not None and SS.is_reflected(s["shot"]):
            cmds.play(state=False)
            SS.remove(s["shot"])

    def create(self) -> None:
        def go():
            name = self.name.text().strip()
            fps = self.fps.currentData()
            if not self._confirm_discard("新規にする"):
                return
            existing = self.session.existing_shot(name) if name else None
            overwrite = False
            if existing is not None:  # 既存の shot.json を確認なしに上書きしない（UX-3 と同じ）
                r = QtWidgets.QMessageBox.question(self, "T-Drive Animation", f"{project.to_project_path(existing)} が既にあります。\n既存のものを開きますか？（「いいえ」で新規にして、保存時に上書きします）",
                                                   QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Yes)
                if r == QtWidgets.QMessageBox.Cancel:
                    return
                if r == QtWidgets.QMessageBox.Yes:
                    self._release_current()
                    self.session.open_shot(existing)
                    return
                overwrite = True
            self._release_current()
            self.session.new_shot(name, fps, self.start.value(), self.end.value())
            if overwrite:
                self.session.confirm_shot_overwrite()
            note = ""
            have = SN.scene_fps()
            if have != fps:  # SF-2: 時間単位は勝手に変えない（キーのフレーム番号が変わり、animset のクリップ範囲とずれる）
                note = (f"\n⚠ シーンの時間単位は {have or '30 / 60 以外の'} fps です。ショットは {fps} fps なので、命名タブの「直す」で時間単位を合わせてください"
                        "（Maya の既定ではキーの実時間が保たれ、フレーム番号が変わります）")
            self._say(f"ショット {name} を作りました。カメラ・キャラクター・イベントを足して「ショットを反映」{note}")

        self._guard(go)

    def open_dialog(self) -> None:
        if not self._confirm_discard("開く"):
            return
        picked = cmds.fileDialog2(fileMode=1, caption="shot.json を開く", fileFilter="shot (*.shot.json)", startingDirectory=project.shots_dir().as_posix())
        if picked:
            self._release_current()
            self._guard(self.session.open_shot, picked[0])

    def save(self) -> None:
        path = self._guard(self.session.save_shot)
        if path:
            self._say(f"保存しました: {project.to_project_path(path)}")

    # ------------------------------------------------------------ カメラ
    def add_camera(self) -> None:
        cam = self.cam_combo.currentText()
        if not cam:
            self._say("⚠ シーンにカメラがありません（persp 以外のカメラを作ってください）")
            return
        self._guard(self.session.shot_add_camera, cam, self.cam_start.value(), self.cam_end.value())

    def remove_camera(self) -> None:
        r = self._selected(self.cam_table)
        if r >= 0:
            self.session.shot_remove_camera(r)

    # ------------------------------------------------------------ キャラクター
    def add_character(self) -> None:
        d = self.session.data
        if d is None:
            return
        ns = self.ns_edit.text().strip()
        if not ns and self._ns_exists(d["modelId"]):
            ns = d["modelId"]  # 既定: ModelId と同じネームスペースがシーンにあればそれ
        rows = [dict(r) for r in self.blend_tab.rows] if self.blend_tab is not None else []
        self._guard(self.session.shot_add_character, d["modelId"], ns, rows)

    @staticmethod
    def _ns_exists(ns: str) -> bool:
        return bool(cmds.namespace(exists=ns))

    def send_rows(self, index: int, rows: list[dict]) -> None:
        self._guard(self.session.shot_set_sequence, index, rows)

    def send_rows_from_blend(self) -> None:
        r = self._selected(self.char_table)
        if r < 0 and self._shot() and len(self._shot()["characters"]) == 1:
            r = 0
        if r < 0:
            self._say("⚠ 表でキャラクターの行を選んでください")
            return
        if self.blend_tab is None or not self.blend_tab.rows:
            self._say("⚠ ブレンドタブの遷移列が空です")
            return
        self.send_rows(r, [dict(x) for x in self.blend_tab.rows])
        self._say(f"キャラクターのクリップ列を {len(self.blend_tab.rows)} 行にしました")

    def toggle_face(self) -> None:
        r = self._selected(self.char_table)
        s = self._shot()
        if s is None or not 0 <= r < len(s["characters"]):
            return
        chars = [dict(c) for c in s["characters"]]
        chars[r]["facialTrack"] = not chars[r].get("facialTrack")
        self._guard(self.session.shot_set, "characters", chars)

    def remove_character(self) -> None:
        r = self._selected(self.char_table)
        if r >= 0:
            self.session.shot_remove_character(r)

    # ------------------------------------------------------------ イベント
    def add_event(self) -> None:
        self._guard(self.session.shot_add_event, int(cmds.currentTime(query=True)), self.ev_key.text().strip())

    def remove_event(self) -> None:
        r = self._selected(self.ev_table)
        if r >= 0:
            self.session.shot_remove_event(r)

    # ------------------------------------------------------------ 反映・再生
    def build(self) -> None:
        s = self._shot()
        if s is None:
            return
        if SH.errors(s):
            self._say("⚠ " + str(SH.errors(s)[0]))
            return
        progress = ComposeProgress(self, "ショットの合成を作っています…")
        try:
            res = self._guard(SS.build, s, self.session.data, progress)
        finally:
            progress.close()
        if res is not None:
            warn = "".join(f"\n⚠ {w}" for w in res["warnings"])
            if SN.scene_fps_exact() != s["frameRate"]:
                warn += f"\n⚠ シーンの時間単位は {SN.scene_fps_exact() or '不明な'} fps、ショットは {s['frameRate']} fps です。命名タブの「直す」で合わせてください（カメラの区間・イベントはフレーム番号のままです）"
            self._say(f"ショットを反映しました（カメラ区間 {res['cameras']}、イベント {res['events']}、合成 {len(res['compositions'])}）{warn}")
        self.refresh()

    def remove(self) -> None:
        s = self._shot()
        if s is None:
            return
        SS.stop()
        n = SS.remove(s["shot"])
        self._say(f"外しました（合成 {n} 件）。再生範囲を戻しました")
        self.refresh()

    def play(self) -> None:
        s = self._shot()
        if s is not None:
            SS.play(s)

    def capture(self) -> None:
        s = self._shot()
        if s is not None:
            path, why = SS.capture_movie(s)
            self._say(f"動画を撮りました: {project.to_project_path(path)}" if path else f"⚠ {why}")

    def make_hand_layer(self) -> None:  # 準備中（SP-2）。ボタンは使えない状態にしてある
        s = self._shot()
        r = self._selected(self.char_table)
        if s is None or not s["characters"]:
            return
        ch = s["characters"][r if r >= 0 else 0]
        name = SS.hand_layer(s["shot"], ch["modelId"])
        self._say(f"手付けキー用のレイヤー {name} を用意しました。キーを打つコントローラを選んで、レイヤーに追加して使ってください")

    def detach(self) -> None:
        s = self._shot()
        if s is not None and SS.is_reflected(s["shot"]):
            SS.stop()
            SS.remove(s["shot"])  # 閉じるときは反映したもの（合成・shot ノード・ロケータ・再生範囲）を外す（SF-18）
