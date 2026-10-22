"""出力タブ（docs/20 §6.4、チケット AN-6 / AN-8）: Unity 用の分割出力（モデル / アニメーション）と、ショット（D-Drive のカットシーン用）の出力。

- 別プロセスの mayapy が FBX を書く（`export.SplitJob`）ので Maya は止まらない。進捗はウィンドウ内、ログに出力ファイルとサイズ・警告
- 命名の❌（禁止文字・`@`・24 fps・クリップ名の形 など）があるうちは出力ボタンを無効にして理由を表示する（docs/20 §2.5）
- dx11Shader・Render Override は使わない
"""

from __future__ import annotations

import os
import traceback

from maya import cmds
from PySide6 import QtCore, QtWidgets

from tdrive import lifecycle, project

from . import export as X
from . import scene_naming as SN
from . import session
from . import shot_scene as SS
from .core import animset as A
from .core import export_plan as E
from .core import shot as SH


class ExportTab(QtWidgets.QWidget):
    def __init__(self, s: session.AnimSession) -> None:
        super().__init__()
        self.session = s
        self.job: "X.SplitJob | X.ShotJob | None" = None
        self.report: SN.Report | None = None
        self.blocked = ""  # 出力を止めている理由（空 = 出せる）
        self._tasks_done = False
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QtWidgets.QWidget()
        scroll.setWidget(body)
        v = QtWidgets.QVBoxLayout(body)

        # ---- 出力先
        top = QtWidgets.QGroupBox("出力先")
        tv = QtWidgets.QVBoxLayout(top)
        row = QtWidgets.QHBoxLayout()
        self.preset = QtWidgets.QComboBox()
        for p in E.PRESETS:
            self.preset.addItem(E.PRESET_LABEL[p], p)
        self.preset.setToolTip("既定: プロジェクトの build/unity/<ModelId>。MS2026 / D-Drive の SourceAssets はプロジェクト設定にパスを書くと選べます（docs/20 §9 Q-4）")
        self.preset.activated.connect(lambda *_: self.refresh())
        row.addWidget(self.preset, 1)
        self.open_btn = QtWidgets.QPushButton("フォルダを開く")
        self.open_btn.clicked.connect(lambda *_: self.open_folder())
        row.addWidget(self.open_btn)
        for label, key, tip in (("MS2026 のパス…", "animMs2026Root", "MS2026 の Unity プロジェクトのフォルダ（Assets の 1 つ上）を選びます。プロジェクト設定に保存されます"),
                                ("D-Drive のパス…", "animDdriveRoot", "D-Drive の Unity プロジェクトのフォルダ（Assets の 1 つ上）を選びます。プロジェクト設定に保存されます")):
            b = QtWidgets.QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, k=key: self.choose_root(k))
            row.addWidget(b)
        tv.addLayout(row)
        self.dirs = QtWidgets.QLabel()
        self.dirs.setWordWrap(True)
        self.dirs.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        tv.addWidget(self.dirs)
        v.addWidget(top)

        # ---- 命名の確認
        self.block_label = QtWidgets.QLabel()
        self.block_label.setWordWrap(True)
        self.block_label.setStyleSheet("color: #ff6060;")
        v.addWidget(self.block_label)
        self.recheck_btn = QtWidgets.QPushButton("命名を確認")
        self.recheck_btn.setToolTip("命名タブと同じ検査をして、出力を止める問題（❌）がないか確かめます。出力ボタンを押したときにも確認します")
        self.recheck_btn.clicked.connect(lambda *_: self.check_names())
        v.addWidget(self.recheck_btn)

        # ---- モデル
        mbox = QtWidgets.QGroupBox("モデル（SK_<ModelId>.fbx）")
        mv = QtWidgets.QVBoxLayout(mbox)
        self.mesh_label = QtWidgets.QLabel()
        self.mesh_label.setWordWrap(True)
        mv.addWidget(self.mesh_label)
        mr = QtWidgets.QHBoxLayout()
        self.set_meshes_btn = QtWidgets.QPushButton("スキンされたメッシュを animset に設定")
        self.set_meshes_btn.setToolTip("スケルトンのルート以下にスキンされているメッシュを、出力するメッシュとして animset に入れます（保存は上の「保存」）")
        self.set_meshes_btn.clicked.connect(lambda *_: self.set_meshes())
        self.model_btn = QtWidgets.QPushButton("モデルを出力")
        self.model_btn.setToolTip("メッシュ + スキン + スケルトン（アニメーション無し、バインドポーズ）を書き出します。humanoid.json と animset.json も同じフォルダに書きます")
        self.model_btn.clicked.connect(lambda *_: self.start(model=True, clips=[]))
        mr.addWidget(self.set_meshes_btn)
        mr.addWidget(self.model_btn)
        mv.addLayout(mr)
        v.addWidget(mbox)

        # ---- アニメーション
        abox = QtWidgets.QGroupBox("アニメーション（A_<ModelId>_<動作>.fbx。クリップごとに 1 ファイル）")
        av = QtWidgets.QVBoxLayout(abox)
        self.only_changed = QtWidgets.QCheckBox("変更のあるクリップだけ")
        self.only_changed.setChecked(True)
        self.only_changed.setToolTip("前回の出力から、範囲内のカーブや属性が変わっていないクリップは飛ばします。既定: オン。出力先を変えると全部が対象になります")
        av.addWidget(self.only_changed)
        self.clip_list = QtWidgets.QListWidget()
        self.clip_list.setMinimumHeight(110)
        av.addWidget(self.clip_list)
        ar = QtWidgets.QHBoxLayout()
        self.all_btn = QtWidgets.QPushButton("すべてチェック")
        self.all_btn.clicked.connect(lambda *_, on=True: self._check_all(on))
        self.none_btn = QtWidgets.QPushButton("チェックを外す")
        self.none_btn.clicked.connect(lambda *_, on=False: self._check_all(on))
        self.anim_btn = QtWidgets.QPushButton("チェックしたクリップを出力")
        self.anim_btn.setToolTip("ジョイントだけをクリップの範囲でベイクして書き出します（コントローラ・コンストレイントは出ません）。rootMotion が none のクリップは Hips の XZ を固定します")
        self.anim_btn.clicked.connect(lambda *_: self.start(model=False, clips=self.checked_clips()))
        for b in (self.all_btn, self.none_btn, self.anim_btn):
            ar.addWidget(b)
        av.addLayout(ar)
        v.addWidget(abox)

        # ---- ショット
        sbox = QtWidgets.QGroupBox("ショット（<Shot>.fbx / <Shot>__<ModelId>.fbx）")
        sv = QtWidgets.QVBoxLayout(sbox)
        self.shot_label = QtWidgets.QLabel()
        self.shot_label.setWordWrap(True)
        self.shot_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        sv.addWidget(self.shot_label)
        self.shot_btn = QtWidgets.QPushButton("ショットを出力")
        self.shot_btn.setToolTip("ショットタブで開いているショットを書き出します: <Shot>.fbx（Ubercam + 小道具 + EVT_）と、キャラクターごとの <Shot>__<ModelId>.fbx（ルートジョイントの階層だけ・ショットの範囲でベイク）。\n"
                                 "同じ名前のファイルは上書きされます。顔の .fctrack と <Shot>.shot.json も同じフォルダに書きます")
        self.shot_btn.clicked.connect(lambda *_: self.start_shot())
        sv.addWidget(self.shot_btn)
        v.addWidget(sbox)

        # ---- 進捗とログ
        self.progress = QtWidgets.QProgressBar()
        self.progress.setVisible(False)
        v.addWidget(self.progress)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(130)
        self.log.setPlaceholderText("直近の出力ログ（ファイル名・サイズ・警告）")
        v.addWidget(self.log)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.poll)
        self.refresh()

    # ------------------------------------------------------------ 状態
    def _config(self) -> dict:
        return project.read_config()

    def dirs_for_preset(self) -> tuple | None:
        if self.session.data is None:
            return None
        try:
            return E.resolve_dirs(self.session.data, self.preset.currentData(), self._config(), project.root())
        except E.ExportPlanError:
            return None

    def preset_problem(self) -> str:
        if self.session.data is None:
            return "先に animset を作ってください（上の ModelId 欄 → 新規）"
        try:
            E.resolve_dirs(self.session.data, self.preset.currentData(), self._config(), project.root())
        except E.ExportPlanError as exc:
            return str(exc)
        return ""

    def refresh(self) -> None:
        d = self.session.data
        busy = self.job is not None
        self._tasks_done = False
        if d is None:
            for w in (self.model_btn, self.anim_btn, self.set_meshes_btn, self.all_btn, self.none_btn, self.open_btn, self.recheck_btn):
                w.setEnabled(False)
            self.dirs.setText("先に animset を作ってください（上の ModelId 欄 → 新規）")
            self.clip_list.clear()
            return
        avail = E.available_presets(d, self._config())
        for i in range(self.preset.count()):
            ok = not avail[self.preset.itemData(i)]
            item = self.preset.model().item(i)
            item.setEnabled(ok)
            item.setToolTip(avail[self.preset.itemData(i)])
        if avail[self.preset.currentData()]:
            self.preset.setCurrentIndex(0)
        dirs = self.dirs_for_preset()
        self.dirs.setText(f"モデル: {dirs[0].as_posix()}\nアニメ: {dirs[1].as_posix()}" if dirs else self.preset_problem())
        meshes = d["meshes"]
        self.mesh_label.setText(f"出力するメッシュ: {', '.join(meshes) if meshes else '（animset に未設定。スケルトンにスキンされたメッシュを自動で選びます）'}")
        checked = {self.clip_list.item(i).text() for i in range(self.clip_list.count()) if self.clip_list.item(i).checkState() == QtCore.Qt.Checked}
        self.clip_list.clear()
        for c in d["clips"]:
            it = QtWidgets.QListWidgetItem(c["name"])
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if (c["name"] in checked or not checked) else QtCore.Qt.Unchecked)
            it.setToolTip(f"{c['start']}〜{c['end']}（{c['end'] - c['start'] + 1} フレーム）")
            self.clip_list.addItem(it)
        self.open_btn.setEnabled(bool(dirs))
        self.shot_label.setText(self._shot_text())
        self.recheck_btn.setEnabled(not busy)
        self.set_meshes_btn.setEnabled(not busy)
        for w in (self.all_btn, self.none_btn):
            w.setEnabled(True)
        self._update_buttons()

    def _update_buttons(self) -> None:
        busy = self.job is not None
        reason = self.blocked or (self.preset_problem() if self.session.data else "")
        ok = bool(self.session.data) and not busy and not reason
        self.model_btn.setEnabled(ok)
        self.anim_btn.setEnabled(ok and self.clip_list.count() > 0)
        self.shot_btn.setEnabled(ok and self.session.shot is not None)
        self.block_label.setText(("⛔ 出力できません: " + reason) if reason and self.session.data else "")

    def _shot_text(self) -> str:
        sh = self.session.shot
        if sh is None:
            return "ショットが開かれていません（ショットタブで作る／開く）"
        try:
            d = E.resolve_shot_dir(sh, self.preset.currentData(), self._config(), project.root())
        except E.ExportPlanError as exc:
            return f"ショット {sh['shot']}: {exc}"
        names = [E.shot_file(sh["shot"])] + [E.shot_file(f) for _, f in SH.character_files(sh)]
        return f"ショット {sh['shot']}（{sh['range'][0]}〜{sh['range'][1]}、{sh['frameRate']} fps）\n出力先: {d.as_posix()}\nファイル: {', '.join(names)}"

    def shot_problem(self) -> str:
        """ショットを出せない理由（無ければ ""）。"""
        sh = self.session.shot
        if sh is None:
            return "ショットが開かれていません"
        errs = SH.errors(sh)
        if errs:
            return str(errs[0])
        want = {30: "ntsc", 60: "ntscf"}[sh["frameRate"]]
        have = cmds.currentUnit(query=True, time=True)
        if have != want:
            return f"シーンの時間単位（{have}）がショットのフレームレート（{sh['frameRate']} fps）と違います（命名タブで直す）"
        if not any(cmds.objExists(c["node"]) for c in sh["cameras"]):
            return "シーンにあるカメラの区間がありません（ショットタブでカメラを足してください）"
        for ch in sh["characters"]:
            try:
                aset = SS.animset_for(ch["modelId"], self.session.data)
                SS.character_joints(ch, aset)
            except (A.AnimSetError, ValueError) as exc:
                return str(exc)
        return ""

    def check_names(self) -> bool:
        """命名の検査をして、❌ があれば出力を止める。出せるなら True。"""
        d = self.session.data
        if d is None:
            return False
        try:
            self.report = SN.inspect_scene(d["modelId"], d)
        except Exception as exc:  # noqa: BLE001
            lifecycle.report_error("出力前の命名の検査で例外", traceback.format_exc())
            self.blocked = f"命名を検査できませんでした: {exc}"
            self._update_buttons()
            return False
        errs = self.report.errors
        if errs:
            lines = []
            for e in errs[:4]:
                lines.append(f"{e.name}: {e.reason}" if hasattr(e, "reason") and hasattr(e, "name") else f"{e.label}: 今 {e.current} → {e.wanted}")
            self.blocked = f"命名の❌ が {len(errs)} 件あります（命名タブで直してください）\n" + "\n".join(lines) + ("\n…" if len(errs) > 4 else "")
        else:
            self.blocked = ""
        self._update_buttons()
        return not self.blocked

    def checked_clips(self) -> list[str]:
        return [self.clip_list.item(i).text() for i in range(self.clip_list.count()) if self.clip_list.item(i).checkState() == QtCore.Qt.Checked]

    def _check_all(self, on: bool) -> None:
        for i in range(self.clip_list.count()):
            self.clip_list.item(i).setCheckState(QtCore.Qt.Checked if on else QtCore.Qt.Unchecked)

    def set_meshes(self) -> None:
        d = self.session.data
        if d is None:
            return
        found = X.skinned_meshes(A.root_name(d))
        if not found:
            self._append(f"⚠ {A.root_name(d)} 以下にスキンされたメッシュが見つかりません")
            return
        self.session.set_value("meshes", found)
        self._append(f"出力するメッシュを設定しました: {', '.join(found)}（上の「保存」で animset に保存）")

    def choose_root(self, key: str, path: str | None = None) -> None:
        """MS2026 / D-Drive の Unity プロジェクトのパスをプロジェクト設定に保存する（path を渡すとダイアログを出さない。テスト用）。"""
        if path is None:
            picked = cmds.fileDialog2(fileMode=3, caption="Unity プロジェクトのフォルダ（Assets の 1 つ上）")
            if not picked:
                return
            path = picked[0]
        project.write_config(**{key: path})
        self._append(f"設定しました: {key} = {path}")
        self.refresh()

    def open_folder(self) -> None:
        dirs = self.dirs_for_preset()
        if dirs:
            dirs[0].mkdir(parents=True, exist_ok=True)
            os.startfile(str(dirs[0]))  # noqa: S606  エクスプローラーで開く（Windows）

    # ------------------------------------------------------------ 出力
    def _append(self, text: str) -> None:
        self.log.appendPlainText(text)

    def start(self, model: bool, clips: list[str]) -> bool:
        """出力を始める。命名の❌・出力先・対象なしのときは始めずに理由をログに出す。始めたら True。"""
        d = self.session.data
        if d is None or self.job is not None:
            return False
        if not self.check_names():
            self._append("⛔ " + self.blocked.splitlines()[0])
            return False
        dirs = self.dirs_for_preset()
        if dirs is None:
            self._append("⛔ " + self.preset_problem())
            return False
        model_dir, anim_dir = dirs
        if clips and self.only_changed.isChecked():
            sp = self.session.path
            state = E.load_state(E.state_path(sp)) if sp else {"clips": {}}
            fps = {n: X.fingerprint(A.find_clip(d, n), d) for n in clips}
            skipped = [n for n in clips if n not in E.changed_clips(clips, fps, state, anim_dir)]
            clips = [n for n in clips if n not in skipped]
            if skipped:
                self._append(f"変更なしのため飛ばしました: {', '.join(skipped)}")
        if not model and not clips:
            self._append("出力するものがありません（クリップにチェックが無い、または変更のあるクリップがありません）")
            return False
        try:
            self.job = X.SplitJob(d, model_dir, anim_dir, clips, model, self.session.path)
        except Exception as exc:  # noqa: BLE001
            lifecycle.report_error("出力を始められません", traceback.format_exc())
            self._append(f"⛔ 出力を始められませんでした: {exc}")
            self.job = None
            return False
        self.progress.setRange(0, self.job.total)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._append(f"出力を始めました（{self.job.total} 件）。別プロセスで書き出すので Maya は操作できます")
        self.timer.start()
        self._update_buttons()
        return True

    def start_shot(self) -> bool:
        """ショットの出力を始める。命名の❌・ショットの問題・出力先のときは始めずに理由をログに出す。始めたら True。"""
        sh = self.session.shot
        if self.session.data is None or sh is None or self.job is not None:
            if sh is None:
                self._append("⛔ ショットが開かれていません")
            return False
        if not self.check_names():
            self._append("⛔ " + self.blocked.splitlines()[0])
            return False
        why = self.shot_problem()
        if why:
            self._append("⛔ " + why)
            return False
        try:
            out_dir = E.resolve_shot_dir(sh, self.preset.currentData(), self._config(), project.root())
            animsets = {ch["modelId"]: SS.animset_for(ch["modelId"], self.session.data) for ch in sh["characters"]}
            self.job = X.ShotJob(sh, out_dir, animsets)
        except Exception as exc:  # noqa: BLE001
            lifecycle.report_error("ショットの出力を始められません", traceback.format_exc())
            self._append(f"⛔ 出力を始められませんでした: {exc}")
            self.job = None
            return False
        self.progress.setRange(0, self.job.total)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._append(f"ショットの出力を始めました（{self.job.total} ファイル）。別プロセスで書き出すので Maya は操作できます")
        self.timer.start()
        self._update_buttons()
        return True

    def poll(self) -> None:
        job = self.job
        if job is None:
            self.timer.stop()
            return
        done, total = job.progress()
        self.progress.setValue(done)
        if not job.poll():
            return
        self.timer.stop()
        self.job = None
        self.progress.setVisible(False)
        try:
            for r in job.finish():
                if r["ok"]:
                    self._append(f"✔ {r['out']}（{r.get('size', 0) / 1024:.0f} KB, {r['seconds']} 秒）")
                    for w in r.get("warnings", []):
                        self._append(f"   ⚠ {w}")
                else:
                    self._append(f"✖ {r['name']}: {r.get('error', '失敗')}")
        except Exception as exc:  # noqa: BLE001
            lifecycle.report_error("出力の結果を読めません", traceback.format_exc())
            self._append(f"✖ 出力に失敗しました: {exc}")
        for line in getattr(job, "extra", []):
            self._append(line)
        self._tasks_done = True
        self._update_buttons()

    def wait(self, timeout: float = 300.0) -> None:
        """出力が終わるまで待つ（テスト・MCP 用）。"""
        import time

        t0 = time.time()
        while self.job is not None and time.time() - t0 < timeout:
            QtWidgets.QApplication.processEvents()
            self.poll()
            time.sleep(0.2)

    def detach(self) -> None:
        self.timer.stop()
        if self.job is not None:
            self.job.kill()  # 閉じるときは裏の mayapy を残さない
            self.job = None
