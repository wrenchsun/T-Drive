"""命名タブ（docs/20 §2.5、チケット AN-2）: 検査の一覧・改名計画の適用・シーン設定・掃除候補・Humanoid 対応表。

操作感は Phase E（docs/05 §4.2）に合わせる: 検索欄・右クリックのコピー / 貼り付け・1 操作 = 1 Undo・ラベルのツールチップに範囲と既定値・
壊れた JSON は警告だけで止めない。dx11Shader・Render Override は使わない。
"""

from __future__ import annotations

import contextlib
import traceback

from maya import cmds
from PySide6 import QtCore, QtGui, QtWidgets

from tdrive import lifecycle, project

from . import scene_naming as SN
from . import session
from .ui_common import MessageLabel
from .core import humanoid_map as H
from .core import naming_rules as N

KIND_LABEL = {N.MESH: "メッシュ", N.MATERIAL: "マテリアル", N.TEXTURE: "テクスチャ", N.CLIP: "クリップ", N.SHOT: "ショット",
              N.CAMERA: "カメラ", N.PROP: "小道具", N.EVENT: "イベント"}
COL_APPLY, COL_SEV, COL_KIND, COL_NAME, COL_SUG, COL_REASON = range(6)


SCENE_EVENTS = ("Undo", "Redo", "timeUnitChanged", "NameChanged")  # これらが起きたら、検査の表示を作り直す（AN-F-10）
_active_tab: "NamingTab | None" = None


@lifecycle.guarded()
def _cb_scene_event() -> None:
    tab = _active_tab
    if tab is not None:
        tab.request_reinspect()


lifecycle.keep_alive(_cb_scene_event)


def _reload_cleanup() -> None:
    tab = _active_tab
    if tab is not None:
        with contextlib.suppress(RuntimeError):
            tab.detach()


lifecycle.on_reload(_reload_cleanup)


class NamingTab(QtWidgets.QWidget):
    def __init__(self, s: session.AnimSession) -> None:
        super().__init__()
        self.session = s
        self.report = SN.Report()
        self.humanoid: dict | None = None
        self._inspected = False  # 一度でも「検査」した（それまでは自動で検査しない）
        self._stale = False  # 検査のあとシーンが変わった（前に出たときに検査し直す）
        self._inspecting = False
        self._jobs: list = []
        self._reinspect_timer = QtCore.QTimer(self)  # 連続する変化（改名の連打・Undo の連打）は 0.3 秒まとめて 1 回
        self._reinspect_timer.setSingleShot(True)
        self._reinspect_timer.setInterval(300)
        self._reinspect_timer.timeout.connect(lambda *_: self._reinspect())
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QtWidgets.QWidget()
        scroll.setWidget(body)
        v = QtWidgets.QVBoxLayout(body)

        # ---- 検査と改名計画
        box = QtWidgets.QGroupBox("検査と改名計画")
        bv = QtWidgets.QVBoxLayout(box)
        row = QtWidgets.QHBoxLayout()
        self.inspect_btn = QtWidgets.QPushButton("検査")
        self.inspect_btn.setToolTip("シーンのメッシュ・マテリアル・テクスチャと animset のクリップ名を MS2026 の規則で調べます。シーンは変更しません")
        self.inspect_btn.clicked.connect(lambda *_: self.inspect())
        row.addWidget(self.inspect_btn)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("検索（名前・提案・理由）")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self._apply_filter())
        row.addWidget(self.search, 1)
        self.only_errors = QtWidgets.QCheckBox("❌ だけ")
        self.only_errors.setToolTip("出力が止まる問題（❌）だけを表示します。既定: オフ")
        self.only_errors.toggled.connect(lambda *_: self._apply_filter())
        row.addWidget(self.only_errors)
        bv.addLayout(row)
        self.summary = QtWidgets.QLabel("「検査」を押すと一覧が出ます")
        self.summary.setWordWrap(True)
        bv.addWidget(self.summary)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["適用", "重大度", "種類", "今の名前", "提案（編集できます）", "理由"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setMinimumHeight(220)
        bv.addWidget(self.table)
        row2 = QtWidgets.QHBoxLayout()
        self.apply_btn = QtWidgets.QPushButton("チェックした提案を適用")
        self.apply_btn.setToolTip("メッシュ・マテリアルの改名は Maya の Ctrl+Z（ビューポートをクリックしてから）で 1 回で戻せます。クリップ名はツールの Ctrl+Z（このウィンドウで）で 1 回で戻せます。割り当ては変えません。\n"
                                  "T-Drive のプレビュー用シェーダーも同時に追従します。Look は、Toon で開いていればその Look（未保存の変更として残る）、開いていなければ looks/*/look.json を書き換えます"
                                  "（書き換えるファイルを先に見せて確認します。ファイルの書き換えはどちらの Ctrl+Z でも戻りません）。テクスチャは提案だけ（ファイル名も変わるため）")
        self.apply_btn.clicked.connect(lambda *_: self.apply())
        row2.addWidget(self.apply_btn)
        self.check_all_btn = QtWidgets.QPushButton("提案のある行をすべてチェック")
        self.check_all_btn.setToolTip("提案のある行の「適用」にすべてチェックを入れます（テクスチャ・提案のみの行は除く）")
        self.check_all_btn.clicked.connect(lambda *_: self._check_all(True))
        row2.addWidget(self.check_all_btn)
        row2.addStretch(1)
        bv.addLayout(row2)
        self.result = MessageLabel()
        self.message = self.result  # AnimPanel がタブの外・最上部へ移す（AN-F-17）
        bv.addWidget(self.result)
        v.addWidget(box)

        # ---- シーン設定
        self.settings_box = QtWidgets.QGroupBox("シーン設定（30 fps / cm / Y-up）")
        self.settings_layout = QtWidgets.QVBoxLayout(self.settings_box)
        v.addWidget(self.settings_box)

        # ---- 掃除候補
        cbox = QtWidgets.QGroupBox("掃除候補（未使用のものを一覧にするだけ。既定はすべて未チェック）")
        cv = QtWidgets.QVBoxLayout(cbox)
        self.cleanup_list = QtWidgets.QListWidget()
        self.cleanup_list.setMaximumHeight(120)
        cv.addWidget(self.cleanup_list)
        self.cleanup_btn = QtWidgets.QPushButton("チェックしたものを削除")
        self.cleanup_btn.setToolTip("チェックした未使用のマテリアル・テクスチャノード・空のネームスペースを削除します（確認が出ます。Maya の Ctrl+Z で戻せます）。割り当て済みのものは候補に出ません")
        self.cleanup_btn.clicked.connect(lambda *_: self.cleanup())
        cv.addWidget(self.cleanup_btn)
        v.addWidget(cbox)

        # ---- Humanoid
        hbox = QtWidgets.QGroupBox("Humanoid 対応表（humanoid.json）")
        hv = QtWidgets.QVBoxLayout(hbox)
        hrow = QtWidgets.QHBoxLayout()
        self.h_infer = QtWidgets.QPushButton("ジョイントから推定")
        self.h_infer.setToolTip("mGear のジョイント名から Unity の Humanoid ボーンを推定します（編集中の内容は上書き）")
        self.h_infer.clicked.connect(lambda *_: self.humanoid_infer())
        self.h_set = QtWidgets.QPushButton("選択中のジョイントを入れる")
        self.h_set.setToolTip("ビューポートで選んだジョイントを、表で選んだボーンに入れます")
        self.h_set.clicked.connect(lambda *_: self.humanoid_set_selected())
        self.h_save = QtWidgets.QPushButton("保存")
        self.h_save.setToolTip("anim/<ModelId>/humanoid.json に保存します（モデルの出力時にも書き出されます）")
        self.h_save.clicked.connect(lambda *_: self.humanoid_save())
        for b in (self.h_infer, self.h_set, self.h_save):
            hrow.addWidget(b)
        hrow.addStretch(1)
        hv.addLayout(hrow)
        self.h_search = QtWidgets.QLineEdit()
        self.h_search.setPlaceholderText("検索（ボーン名・ジョイント名）")
        self.h_search.setClearButtonEnabled(True)
        self.h_search.textChanged.connect(lambda *_: self._filter_humanoid())
        hv.addWidget(self.h_search)
        self.h_table = QtWidgets.QTableWidget(0, 2)
        self.h_table.setHorizontalHeaderLabels(["Humanoid ボーン", "ジョイント（編集できます）"])
        self.h_table.horizontalHeader().setStretchLastSection(True)
        self.h_table.verticalHeader().setVisible(False)
        self.h_table.setMinimumHeight(220)
        self.h_table.itemChanged.connect(self._on_humanoid_item)
        hv.addWidget(self.h_table)
        self.h_msg = QtWidgets.QLabel()
        self.h_msg.setWordWrap(True)
        hv.addWidget(self.h_msg)
        v.addWidget(hbox)
        v.addStretch(1)
        self._building = False
        self.refresh()

    # ------------------------------------------------------------ 検査
    def _start_jobs(self) -> None:
        """Maya の Undo / Redo / 時間単位 / 名前の変化を見る scriptJob（最初の検査のあとから。detach で外す）。"""
        global _active_tab
        if self._jobs:
            return
        _active_tab = self
        for ev in SCENE_EVENTS:
            with contextlib.suppress(RuntimeError):
                self._jobs.append(cmds.scriptJob(event=[ev, _cb_scene_event]))

    def request_reinspect(self) -> None:
        """シーンが変わった: 検査済みなら、0.3 秒まとめてから検査し直す（前に出ていなければ、前に出たときに）。"""
        if not self._inspected or self._inspecting or self.session.data is None:
            return
        self._stale = True
        self._reinspect_timer.start()

    def _reinspect(self, force: bool = False) -> None:
        if self._stale and (force or self.isVisible()):
            self._stale = False
            self.inspect()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._stale:
            self._reinspect()

    def inspect(self) -> None:
        if self.session.data is None:
            self.summary.setText("先に animset を作ってください（上の ModelId 欄 → 新規）")
            return
        self._inspecting = True
        try:
            self.report = SN.inspect_scene(self.session.model_id, self.session.data)
        except Exception as exc:  # noqa: BLE001
            lifecycle.report_error("命名の検査で例外", traceback.format_exc())
            self.summary.setText(f"検査できませんでした: {exc}")
            return
        finally:
            self._inspecting = False
        self._inspected = True
        self._stale = False
        self._start_jobs()
        self._fill_table()
        self._fill_settings()
        self._fill_cleanup()
        self._fill_humanoid(reset=self.humanoid is None)

    def _fill_table(self) -> None:
        self._building = True
        try:
            self.table.setRowCount(0)
            for f in self.report.findings:
                r = self.table.rowCount()
                self.table.insertRow(r)
                chk = QtWidgets.QTableWidgetItem()
                can = bool(f.suggestion) and not f.proposal_only
                chk.setFlags(QtCore.Qt.ItemIsUserCheckable | (QtCore.Qt.ItemIsEnabled if can else QtCore.Qt.NoItemFlags))
                chk.setCheckState(QtCore.Qt.Checked if can else QtCore.Qt.Unchecked)
                self.table.setItem(r, COL_APPLY, chk)
                sev = QtWidgets.QTableWidgetItem("❌" if f.is_error else "⚠")
                sev.setToolTip("出力が止まる問題" if f.is_error else "直すのがおすすめ（出力は止まりません）")
                self.table.setItem(r, COL_SEV, sev)
                self.table.setItem(r, COL_KIND, QtWidgets.QTableWidgetItem(KIND_LABEL.get(f.kind, f.kind)))
                self.table.setItem(r, COL_NAME, QtWidgets.QTableWidgetItem(f.name))
                sug = QtWidgets.QTableWidgetItem(f.suggestion + ("（提案のみ）" if f.proposal_only else ""))
                if f.proposal_only:
                    sug.setFlags(sug.flags() & ~QtCore.Qt.ItemIsEditable)
                self.table.setItem(r, COL_SUG, sug)
                self.table.setItem(r, COL_REASON, QtWidgets.QTableWidgetItem(f.reason))
                for c in (COL_SEV, COL_KIND, COL_NAME, COL_REASON):
                    it = self.table.item(r, c)
                    it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable)
            self.table.resizeColumnsToContents()
        finally:
            self._building = False
        errs = sum(f.is_error for f in self.report.findings) + sum(s.severity == N.ERROR for s in self.report.settings)
        warns = len(self.report.findings) + len(self.report.settings) - errs
        msg = f"❌ {errs} 件 / ⚠ {warns} 件" if (errs or warns) else "問題は見つかりませんでした"
        if self.report.warnings:
            msg += "\n" + "\n".join("⚠ " + w for w in self.report.warnings)
        self.summary.setText(msg)
        self._apply_filter()

    def _apply_filter(self) -> None:
        text = self.search.text().strip().lower()
        only_err = self.only_errors.isChecked()
        for r, f in enumerate(self.report.findings if self.table.rowCount() == len(self.report.findings) else []):
            hay = f"{f.name} {f.suggestion} {f.reason} {KIND_LABEL.get(f.kind, '')}".lower()
            self.table.setRowHidden(r, bool((text and text not in hay) or (only_err and not f.is_error)))

    def _check_all(self, on: bool) -> None:
        self._building = True
        for r, f in enumerate(self.report.findings):
            if f.suggestion and not f.proposal_only:
                self.table.item(r, COL_APPLY).setCheckState(QtCore.Qt.Checked if on else QtCore.Qt.Unchecked)
        self._building = False

    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        """提案の編集: 検査し直して理由・重大度を更新する（まだ規則に合わない名前は赤くする）。"""
        if self._building or item.column() != COL_SUG or item.row() >= len(self.report.findings):
            return
        f = self.report.findings[item.row()]
        f.suggestion = item.text().strip()
        bad = bool(f.suggestion) and N.inspect(f.kind, f.suggestion, self.session.model_id, node=f.node) is not None
        item.setForeground(QtGui.QBrush(QtGui.QColor("#ff6060" if bad else "#d0d0d0")))
        item.setToolTip("この名前もまだ規則に合いません" if bad else "")

    def _selected_findings(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def _menu(self, pos) -> None:
        rows = self._selected_findings()
        if not rows:
            return
        m = QtWidgets.QMenu(self)
        m.addAction("今の名前をコピー", lambda *_: QtWidgets.QApplication.clipboard().setText("\n".join(self.report.findings[r].name for r in rows)))
        m.addAction("提案をコピー", lambda *_: QtWidgets.QApplication.clipboard().setText("\n".join(self.report.findings[r].suggestion for r in rows)))
        m.addAction("提案に貼り付け", lambda *_: self._paste_suggestions(rows))
        m.addSeparator()
        m.addAction("シーンで選ぶ", lambda *_: self._select_in_scene(rows))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _paste_suggestions(self, rows: list[int]) -> None:
        lines = [l.strip() for l in QtWidgets.QApplication.clipboard().text().splitlines() if l.strip()]
        if not lines:
            return
        for i, r in enumerate(rows):
            f = self.report.findings[r]
            if f.proposal_only:
                continue
            self.table.item(r, COL_SUG).setText(lines[i] if i < len(lines) else lines[-1])

    def _select_in_scene(self, rows: list[int]) -> None:
        nodes = [self.report.findings[r].node for r in rows if cmds.objExists(self.report.findings[r].node)]
        if nodes:  # 空のリストを渡さない
            cmds.select(nodes, replace=True)

    # ------------------------------------------------------------ 適用
    def apply(self) -> None:
        picked = []
        for r, f in enumerate(self.report.findings):
            it = self.table.item(r, COL_APPLY)
            if it is not None and it.checkState() == QtCore.Qt.Checked and f.suggestion and not f.proposal_only:
                picked.append(f)
        if not picked:
            self.result.setText("適用する行がありません（提案のある行にチェックを入れてください）")
            return
        bad = [f for f in picked if N.inspect(f.kind, f.suggestion, self.session.model_id) is not None and N.inspect(f.kind, f.suggestion, self.session.model_id).is_error]
        if bad:
            self.result.setText("規則に合わない提案があります（赤い行）: " + ", ".join(f.suggestion for f in bad[:3]))
            return
        try:
            scene_items = [f for f in picked if f.kind in (N.MESH, N.MATERIAL)]
            write_files = True
            planned = SN.plan_look_follow({N.strip_namespace(f.name): f.suggestion for f in scene_items if f.kind == N.MATERIAL},
                                          {N.strip_namespace(f.name): f.suggestion for f in scene_items if f.kind == N.MESH})
            if planned["files"]:  # SF-11: look.json を直接書き換える。Ctrl+Z では戻らないので、先に見せて確かめる
                listing = "\n".join(f"・{project.to_project_path(p)}（{n} か所）" for p, n in planned["files"])
                r = QtWidgets.QMessageBox.question(
                    self, "T-Drive Animation", "次の Look ファイルの名前を書き換えます。ファイルの書き換えは Ctrl+Z で戻せません。\n" + listing
                    + "\n\n書き換えますか？（「いいえ」ならメッシュ・マテリアルの改名だけ行い、Look はそのままにします）",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
                write_files = r == QtWidgets.QMessageBox.Yes
            res = SN.apply_renames(scene_items)
            msgs = [f"改名 {len(res['renamed'])} 件"] + [f"スキップ: {n}（{why}）" for n, why in res["skipped"]]
            msgs += SN.follow_look(res["materials"], res["meshes"], write_files=write_files)
            clip_pairs = [(f.name, f.suggestion) for f in picked if f.kind == N.CLIP]
            if clip_pairs:  # クリップ名は全部で 1 つのツールの Undo
                done = self.session.rename_clips(clip_pairs)
                msgs += [f"クリップ {a} → {b}" for a, b in done]
                msgs += [f"スキップ: {a}（名前の重複か、クリップがありません）" for a, b in clip_pairs if (a, b) not in done]
            self.result.setText("\n".join(msgs))
        except Exception as exc:  # noqa: BLE001
            lifecycle.report_error("改名の適用で例外", traceback.format_exc())
            self.result.setText(f"適用できませんでした: {exc}")
        self.inspect()

    # ------------------------------------------------------------ シーン設定
    def _fill_settings(self) -> None:
        while self.settings_layout.count():
            w = self.settings_layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if not self.report.settings:
            self.settings_layout.addWidget(QtWidgets.QLabel("問題ありません（30 fps / cm / Y-up）"))
            return
        for s in self.report.settings:
            row = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(QtWidgets.QLabel(f"{'❌' if s.severity == N.ERROR else '⚠'} {s.label}: 今 {s.current} → {s.wanted}"), 1)
            if s.fixable:
                b = QtWidgets.QPushButton("直す")
                tip = "Maya の既定の変換に従います（キーは同じ実時間に保たれ、フレーム番号が変わります。docs/20 §9 Q-5）" if s.key == "time" else "1 回の Undo で戻せます"
                b.setToolTip(tip)
                b.clicked.connect(lambda *_, k=s.key: self.fix_setting(k))
                h.addWidget(b)
            self.settings_layout.addWidget(row)

    def fix_setting(self, key: str) -> None:
        rate = self.session.data["frameRate"] if self.session.data else 30
        keep_frames = False
        ratio = 1.0
        if key == "time":
            ratio = SN.time_change_ratio(rate)
            if abs(ratio - 1.0) > 1e-9:
                r = QtWidgets.QMessageBox.question(
                    self, "T-Drive Animation",
                    f"フレームレートを変えます（フレーム番号が約 {ratio:g} 倍になります）。\n\n"
                    "はい: キーの時刻（実時間）を保つ。フレーム番号が変わるので、クリップとショットの範囲・イベントも同じ比率で直します（既定）\n"
                    "いいえ: フレーム番号を保つ。キーの番号はそのままで、再生の速さが変わります（クリップの範囲は変えません）\n"
                    "キャンセル: 何もしない",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Yes)
                if r == QtWidgets.QMessageBox.Cancel:
                    return
                keep_frames = r == QtWidgets.QMessageBox.No
        SN.fix_settings([key], rate, keep_frames=keep_frames)
        note = ""
        if key == "time" and not keep_frames and abs(ratio - 1.0) > 1e-9 and self.session.data is not None:
            n = self.session.rescale_time(ratio)
            note = f"クリップの範囲とイベントを {ratio:g} 倍に直しました（{n} クリップ。ツールの Undo で戻せます）"
        self.inspect()
        if note:
            self.result.setText(note)

    # ------------------------------------------------------------ 掃除
    def _fill_cleanup(self) -> None:
        self.cleanup_list.clear()
        for it in self.report.cleanup:
            li = QtWidgets.QListWidgetItem(it.label)
            li.setFlags(li.flags() | QtCore.Qt.ItemIsUserCheckable)
            li.setCheckState(QtCore.Qt.Unchecked)
            li.setData(QtCore.Qt.UserRole, self.report.cleanup.index(it))
            self.cleanup_list.addItem(li)
        self.cleanup_btn.setEnabled(bool(self.report.cleanup))

    def cleanup(self) -> None:
        items = [self.report.cleanup[self.cleanup_list.item(i).data(QtCore.Qt.UserRole)] for i in range(self.cleanup_list.count())
                 if self.cleanup_list.item(i).checkState() == QtCore.Qt.Checked]
        if items:
            r = QtWidgets.QMessageBox.question(self, "T-Drive Animation", f"チェックした {len(items)} 件を削除します（Maya の Ctrl+Z で戻せます）。続けますか？",
                                               QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if r != QtWidgets.QMessageBox.Yes:
                return
            n = SN.apply_cleanup(items)
            self.result.setText(f"掃除: {n} 件を削除しました")
            self.inspect()

    # ------------------------------------------------------------ Humanoid
    def _root(self) -> str:
        return self.session.data["skeletonRoot"] if self.session.data else ""

    def _ns(self) -> str:
        return (self.session.data or {}).get("namespace", "") or ""

    def _fill_humanoid(self, reset: bool = False) -> None:
        if self.session.data is None:
            return
        if reset or self.humanoid is None:
            self.humanoid = SN.humanoid_for(self.session.model_id, self._root(), self._ns())
        self._building = True
        try:
            bones = self.humanoid.get("bones", {})
            self.h_table.setRowCount(0)
            for b in H.ALL_BONES:
                if b not in bones and b not in H.REQUIRED and b not in H.OPTIONAL and not any(b.endswith(p) for p in H.PHALANGES):
                    continue
                r = self.h_table.rowCount()
                self.h_table.insertRow(r)
                name = QtWidgets.QTableWidgetItem(b + (" *" if b in H.REQUIRED else ""))
                name.setFlags(name.flags() & ~QtCore.Qt.ItemIsEditable)
                name.setData(QtCore.Qt.UserRole, b)
                name.setToolTip("* = 必須（Unity の Humanoid に要る 15 ボーン）" if b in H.REQUIRED else "")
                self.h_table.setItem(r, 0, name)
                self.h_table.setItem(r, 1, QtWidgets.QTableWidgetItem(bones.get(b, "")))
            self.h_table.resizeColumnToContents(0)
        finally:
            self._building = False
        self._humanoid_check()
        self._filter_humanoid()

    def _humanoid_check(self) -> None:
        if self.humanoid is None:
            return
        probs = SN.humanoid_problems(self.humanoid, self._root(), self._ns())
        self.h_msg.setText("\n".join("⚠ " + p for p in probs[:6]) + (f"\n…ほか {len(probs) - 6} 件" if len(probs) > 6 else "")
                           if probs else f"✔ 必須 15 ボーンが埋まっています（extra {len(self.humanoid.get('extra', []))} 本）")

    def _filter_humanoid(self) -> None:
        text = self.h_search.text().strip().lower()
        for r in range(self.h_table.rowCount()):
            hay = f"{self.h_table.item(r, 0).data(QtCore.Qt.UserRole)} {self.h_table.item(r, 1).text()}".lower()
            self.h_table.setRowHidden(r, bool(text and text not in hay))

    def _on_humanoid_item(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._building or item.column() != 1 or self.humanoid is None:
            return
        bone = self.h_table.item(item.row(), 0).data(QtCore.Qt.UserRole)
        H.set_bone(self.humanoid, bone, item.text().strip())
        self._humanoid_check()

    def humanoid_infer(self) -> None:
        if self.session.data is None:
            return
        ns = self._ns()
        self.humanoid = H.new_map(self.session.model_id, SN.scene_joints(f"{ns}:{self._root()}" if ns else self._root()), self._root())
        self._fill_humanoid()

    def humanoid_set_selected(self) -> None:
        row = self.h_table.currentRow()
        sel = cmds.ls(selection=True, type="joint") or []
        if row < 0 or not sel:
            self.h_msg.setText("表でボーンの行を選び、ビューポートでジョイントを 1 つ選んでください")
            return
        self.h_table.item(row, 1).setText(N.strip_namespace(sel[0]))

    def humanoid_save(self) -> None:
        if self.humanoid is None or self.session.data is None:
            return
        try:
            path = H.save(SN.humanoid_path(self.session.model_id), self.humanoid)
            self.h_msg.setText(f"保存しました: {project.to_project_path(path)}")
        except OSError as exc:
            self.h_msg.setText(f"保存できませんでした: {exc}")

    # ------------------------------------------------------------ 更新
    def refresh(self) -> None:
        has = self.session.data is not None
        for w in (self.inspect_btn, self.apply_btn):
            w.setEnabled(has)
        if not has:
            self.summary.setText("先に animset を作ってください（上の ModelId 欄 → 新規）")
            self.humanoid = None

    def detach(self) -> None:
        global _active_tab
        self._reinspect_timer.stop()
        for j in self._jobs:
            with contextlib.suppress(RuntimeError, TypeError):  # batch では scriptJob が番号を返さない
                cmds.scriptJob(kill=j, force=True)
        self._jobs = []
        if _active_tab is self:
            _active_tab = None
