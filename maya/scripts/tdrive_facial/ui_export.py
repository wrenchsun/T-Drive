"""出力タブ（docs/14 §5.10、F1-8 の画面）: Unity 向け（FBX + .fcpose）・UE 版向け（.fcpose.json）・Timeline 用（.fctrack）。

画面は「描く + 入力を渡す」だけ。書き出しの本体は `export.py`（開いているシーンは変えない）。

- **Unity 向けに出力**: 別プロセスの mayapy が FBX を書く（`export.UnityExportJob`）ので、Maya の画面は止まらない。
  進行中は「書き出し中…」と経過秒数を出し、終わったら出力先・メッシュ・FC_* の数・除いた fcs_* の数・警告を要約する。
  出力の前に検証して、エラー・未ベイク・ベイク後の変更があれば「それでも出力する」かを聞く
- **UE 版向けに出力**: データ（.fcpose.json）をそのまま書く（保存先を聞く）
- **Timeline 用に出力**: プレビュー用ノードの強さ・感情・手動角度・誇張のキーを `<Shot>__<Model>.fctrack` へ。Unity 側では .fctrack を取り込んで Timeline に反映できる（実際のカットシーンでの確認は途中）
"""

from __future__ import annotations

import time
import traceback
import weakref
from pathlib import Path
from typing import Optional

from maya import cmds
from PySide6 import QtCore, QtGui, QtWidgets

from tdrive import lifecycle

from . import export
from .core import validate as V
from .session import FacialSessionError
from .ui import DIM_STYLE, WARN_STYLE

POLL_MS = 250
OK_STYLE = DIM_STYLE
ERR_STYLE = "color: #ff8a80;"

_live_tabs: "weakref.WeakSet[ExportTab]" = weakref.WeakSet()


def _detach_all() -> None:
    """ツールのリロードの前: 動いているタイマー・書き出しを止める。"""
    for tab in list(_live_tabs):
        try:
            tab.detach()
        except RuntimeError:
            pass


lifecycle.on_reload(_detach_all)


class ExportTab(QtWidgets.QWidget):
    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self._job: Optional[export.UnityExportJob] = None
        self._job_t0 = 0.0
        self._detached = False
        self._model_edited = False
        self._range_edited = False
        self.last_result: Optional[export.UnityExportResult] = None

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
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

        # ---- Unity
        box = QtWidgets.QGroupBox("Unity 向け（FBX + .fcpose）")
        v = QtWidgets.QVBoxLayout(box)
        hint = QtWidgets.QLabel(
            "顔メッシュと同じ骨格のメッシュ・ジョイントを FBX にし、ブレンドシェイプには焼いた FC_* を入れます（fcs_* は入りません）。"
            "開いているシーンは変わりません。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(DIM_STYLE)
        v.addWidget(hint)
        self.out_label = QtWidgets.QLabel()
        self.out_label.setWordWrap(True)
        self.out_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        v.addWidget(self.out_label)
        row = QtWidgets.QHBoxLayout()
        self.btn_unity = QtWidgets.QPushButton("Unity 向けに出力")
        self.btn_unity.setToolTip("検証してから、FBX と .fcpose を書き出します（別プロセスで書くので、Maya の画面は止まりません）")
        self.btn_unity.clicked.connect(lambda *_: self.on_export_unity())
        self.btn_cancel = QtWidgets.QPushButton("中止")
        self.btn_cancel.clicked.connect(lambda *_: self.on_cancel())
        self.btn_open = QtWidgets.QPushButton("フォルダを開く")
        self.btn_open.clicked.connect(lambda *_: self.on_open_folder())
        for w in (self.btn_unity, self.btn_cancel, self.btn_open):
            row.addWidget(w)
        row.addStretch(1)
        v.addLayout(row)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 0)  # 長さ不明（動いていることだけ見せる）
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(8)
        v.addWidget(self.progress)
        self.job_label = QtWidgets.QLabel()
        self.job_label.setStyleSheet(DIM_STYLE)
        v.addWidget(self.job_label)
        self.result = QtWidgets.QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setMaximumHeight(150)
        self.result.setPlaceholderText("出力の結果がここに出ます")
        v.addWidget(self.result)
        col.addWidget(box)

        # ---- UE
        box = QtWidgets.QGroupBox("UE 版向け（.fcpose.json）")
        v = QtWidgets.QVBoxLayout(box)
        hint = QtWidgets.QLabel("データ（.fcpose.json）をそのまま書き出します。座標系は meta に書いてあります。")
        hint.setWordWrap(True)
        hint.setStyleSheet(DIM_STYLE)
        v.addWidget(hint)
        self.btn_ue = QtWidgets.QPushButton("UE 版向けに出力…")
        self.btn_ue.setToolTip("保存先を選んで、データを .fcpose.json として書き出します")
        self.btn_ue.clicked.connect(lambda *_: self.on_export_ue())
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.btn_ue)
        row.addStretch(1)
        v.addLayout(row)
        col.addWidget(box)

        # ---- Timeline
        box = QtWidgets.QGroupBox("Timeline 用（.fctrack）")
        f = QtWidgets.QFormLayout(box)
        f.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        note = QtWidgets.QLabel(
            "グリッドタブのプレビューの強さ・感情・手動角度に打ったキーを、ショットごとのファイルにします。"
            "Unity 側では .fctrack を取り込んで Timeline に反映できます（<span style='color:#f0a040;'>実際のカットシーンでの確認は途中</span>です）。"
            "プレビューの「誇張」にキーを打ってあれば、その動きも入ります（キーが無ければ入りません）。"
        )
        note.setWordWrap(True)
        note.setTextFormat(QtCore.Qt.RichText)
        note.setStyleSheet(DIM_STYLE)
        f.addRow(note)
        self.shot = QtWidgets.QLineEdit()
        self.shot.setPlaceholderText("例: sc010_c020")
        self.shot.setToolTip("ショット名。ファイル名は <ショット名>__<モデル名>.fctrack になります")
        f.addRow("ショット名", self.shot)
        self.model = QtWidgets.QLineEdit()
        self.model.setToolTip("モデル名（既定はキャラクター ID）")
        self.model.textEdited.connect(lambda *_: self._mark_model_edited())
        f.addRow("モデル名", self.model)
        row = QtWidgets.QHBoxLayout()
        self.start = self._spin()
        self.end = self._spin()
        self.start.valueChanged.connect(lambda *_: self._mark_range_edited())
        self.end.valueChanged.connect(lambda *_: self._mark_range_edited())
        row.addWidget(self.start)
        row.addWidget(QtWidgets.QLabel("〜"))
        row.addWidget(self.end)
        row.addStretch(1)
        f.addRow("フレーム範囲", row)
        self.btn_track = QtWidgets.QPushButton("Timeline 用に出力（.fctrack）")
        self.btn_track.setToolTip("プレビュー用ノードのキーを書き出します（先にグリッドタブでプレビューを作り、強さ・感情にキーを打ってください）")
        self.btn_track.clicked.connect(lambda *_: self.on_export_track())
        f.addRow("", self.btn_track)
        self.track_label = QtWidgets.QLabel()
        self.track_label.setWordWrap(True)
        self.track_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.track_label.setStyleSheet(DIM_STYLE)
        f.addRow(self.track_label)
        col.addWidget(box)
        col.addStretch(1)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self._poll)
        session.listeners.append(self._on_session_changed)
        _live_tabs.add(self)
        self.refresh()

    @staticmethod
    def _spin() -> QtWidgets.QDoubleSpinBox:
        w = QtWidgets.QDoubleSpinBox()
        w.setRange(-100000.0, 100000.0)
        w.setDecimals(1)
        w.setKeyboardTracking(False)
        return w

    def _mark_model_edited(self) -> None:
        self._model_edited = True

    def _mark_range_edited(self) -> None:
        self._range_edited = True

    # ============================================================ 表示
    def set_status(self, text: str, error: bool = False, warn: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(ERR_STYLE if error else (WARN_STYLE if warn else OK_STYLE))

    def _set_job_label(self, text: str) -> None:
        self.job_label.setText(text)
        self.job_label.setVisible(bool(text))

    def has_doc(self) -> bool:
        return self.session.presenters is not None

    def _on_session_changed(self) -> None:
        if self._detached:
            return
        try:
            self._sync_controls()
        except RuntimeError:
            self.detach()

    def unity_out_dir(self) -> Path:
        return export.unity_dir(export.character_name(self.session.doc, self.session.path))

    def refresh(self) -> None:
        if self._detached:
            return
        self._sync_controls()

    def _sync_controls(self) -> None:
        doc = self.session.doc
        has = doc is not None
        busy = self._job is not None
        can = has and bool(doc.asset) and doc.target is not None and bool(doc.target.mesh)
        self.btn_unity.setEnabled(can and not busy)
        self.btn_cancel.setVisible(busy)
        self.progress.setVisible(busy)
        self.btn_ue.setEnabled(has and not busy)
        self.btn_track.setEnabled(has and not busy)
        self.btn_open.setEnabled(has)
        if not has:
            self.out_label.setText("データが開かれていません")
            return
        try:
            self.out_label.setText(f"出力先: {self.unity_out_dir().as_posix()}")
        except export.ExportError as exc:
            self.out_label.setText(str(exc))
        if not self._model_edited:
            self.model.setText(doc.asset or "")
        if not self._range_edited:
            self._set_range_from_playback()

    def _set_range_from_playback(self) -> None:
        for w, key in ((self.start, "minTime"), (self.end, "maxTime")):
            w.blockSignals(True)
            w.setValue(float(cmds.playbackOptions(query=True, **{key: True})))
            w.blockSignals(False)

    # ============================================================ ダイアログ（差し替えられる）
    def ask_proceed(self, text: str) -> bool:
        """検証に問題があるまま出力してよいか（「それでも出力する」）。"""
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, "出力", text, parent=self)
        go = box.addButton("それでも出力する", QtWidgets.QMessageBox.AcceptRole)
        stop = box.addButton("やめる", QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(stop)  # Enter では出力しない（M-9）
        box.exec()
        return box.clickedButton() is go

    def ask_ue_path(self, default: str) -> str:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "UE 版向けに出力", default, "FacialController (*.fcpose.json)")
        return path

    def open_folder(self, path: Path) -> None:
        """フォルダをエクスプローラーで開く（テストが差し替える）。"""
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    # ============================================================ 検証
    def precheck_text(self) -> str:
        """出力前の検証。エラー・未ベイク・ベイク後の変更があれば、その要約を返す（無ければ ""）。"""
        issues = self.session.validate()
        n_err = sum(1 for i in issues if i.severity == V.SEVERITY_ERROR)
        n_unbaked, n_changed, n_missing, k_unbaked, k_changed, k_missing = export.count_bake_issues(issues)
        lines: list[str] = []
        if n_err:
            lines.append(f"エラーが {n_err} 件あります（検証タブで確認できます）")
        if n_unbaked:
            lines.append(f"未ベイクの点が {n_unbaked} 点あります（その点の補正は FBX に入りません）")
        if n_changed:
            lines.append(f"ベイク後にポーズを変えた点が {n_changed} 点あります（FBX の形は古いままです）")
        if n_missing:
            lines.append(f"ベイクしたはずのシェイプが無い点が {n_missing} 点あります")
        if k_unbaked:
            lines.append(f"未ベイクのパース補正のキーが {k_unbaked} 個あります（そのキーの補正は FBX に入りません）")
        if k_changed:
            lines.append(f"ベイク後に変えたパース補正のキーが {k_changed} 個あります（FBX の形は古いままです）")
        if k_missing:
            lines.append(f"ベイクしたはずのシェイプが無いパース補正のキーが {k_missing} 個あります")
        return "\n".join(lines)

    # ============================================================ Unity 向け
    def on_export_unity(self) -> None:
        if not self.has_doc() or self._job is not None:
            return
        s = self.session
        try:
            problems = self.precheck_text()
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"検証でエラーが出ました: {exc}", error=True)
            lifecycle.report_error("出力タブ: 検証", traceback.format_exc(), once=False)
            return
        if problems and not self.ask_proceed(problems + "\n\nこのまま出力しますか？"):
            self.set_status("出力を取りやめました（検証タブで直してから出力できます）")
            return
        try:
            s.end_edit()  # 基準姿勢のまま書き出さない（ジョイントの姿勢は FBX にそのまま入る）
            self._job = export.UnityExportJob(s.doc, s.path)
        except (export.ExportError, FacialSessionError) as exc:
            self.set_status(str(exc), error=True)
            return
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"出力を始められませんでした: {exc}", error=True)
            lifecycle.report_error("出力タブ: Unity 向け", traceback.format_exc(), once=False)
            return
        self._job_t0 = time.perf_counter()
        self.result.setPlainText("")
        self.set_status("書き出し中です…（別プロセスで書いています。Maya はそのまま使えます）")
        self._set_job_label("書き出し中… 0 秒")
        self._sync_controls()
        self.timer.start()

    def _poll(self) -> None:
        job = self._job
        if job is None or self._detached:
            self.timer.stop()
            return
        self._set_job_label(f"書き出し中… {time.perf_counter() - self._job_t0:.0f} 秒")
        if not job.poll():
            return
        self.timer.stop()
        self._job = None
        try:
            res = job.result()
        except export.ExportError as exc:
            self._set_job_label("")
            self.set_status("書き出しに失敗しました", error=True)
            self.result.setPlainText(str(exc))
            self._sync_controls()
            return
        except Exception as exc:  # noqa: BLE001
            self._set_job_label("")
            self.set_status(f"書き出しでエラーが出ました: {exc}", error=True)
            lifecycle.report_error("出力タブ: Unity 向けの完了", traceback.format_exc(), once=False)
            self._sync_controls()
            return
        self.last_result = res
        self._set_job_label("")
        self.result.setPlainText(self.result_text(res))
        self.set_status(f"書き出しました（{res.seconds:.1f} 秒）" + (f"。警告 {len(res.warnings)} 件" if res.warnings else ""), warn=bool(res.warnings))
        self._sync_controls()

    @staticmethod
    def result_text(res: export.UnityExportResult) -> str:
        lines = [
            f"FBX: {res.fbx.as_posix()}",
            f".fcpose: {res.fcpose.as_posix()}",
            "メッシュ（{} 個）: {}".format(len(res.meshes), "、".join(res.meshes)),
            f"FBX に入った FC_*（顔メッシュ）: {res.fc_count} 本" + (f"（うち誇張用 _Ex {res.fc_ex_count} 本）" if res.fc_ex_count else ""),
            f"除いた fcs_*（彫刻用シェイプ）: {res.excluded_fcs} 本",
        ]
        for w in res.warnings:
            lines.append("警告: " + w)
        return "\n".join(lines)

    def on_cancel(self) -> None:
        job = self._job
        if job is None:
            return
        self.timer.stop()
        self._job = None
        job.cancel()
        self._set_job_label("")
        self.set_status("書き出しを中止しました")
        self._sync_controls()

    def on_open_folder(self) -> None:
        if not self.has_doc():
            return
        d = self.last_result.fbx.parent if self.last_result is not None else self.unity_out_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.open_folder(d)

    # ============================================================ UE 版向け
    def on_export_ue(self) -> None:
        if not self.has_doc():
            return
        doc = self.session.doc
        try:
            default = str(self.unity_out_dir().parent / "ue" / f"{export.character_name(doc, self.session.path)}{export.UE_SUFFIX}")
        except export.ExportError as exc:
            self.set_status(str(exc), error=True)
            return
        path = self.ask_ue_path(default)
        if not path:
            self.set_status("取りやめました")
            return
        if not path.endswith(".json"):
            path += export.UE_SUFFIX
        try:
            out = export.export_ue(doc, path)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"書き出せませんでした: {exc}", error=True)
            return
        self.set_status(f"UE 版向けに書き出しました: {Path(out).as_posix()}")

    # ============================================================ Timeline 用
    def on_export_track(self) -> None:
        if not self.has_doc():
            return
        shot, model = self.shot.text().strip(), self.model.text().strip()
        if not shot or not model:
            self.set_status("ショット名とモデル名を入れてください", error=True)
            return
        try:
            res = export.export_fctrack(self.session.doc, shot, model, self.start.value(), self.end.value())
        except export.ExportError as exc:
            self.set_status(str(exc), error=True)
            return
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"書き出せませんでした: {exc}", error=True)
            lifecycle.report_error("出力タブ: Timeline 用", traceback.format_exc(), once=False)
            return
        text = f"書き出しました: {Path(res['path']).as_posix()}（カーブ {len(res['curves'])} 本・キー {res['keys']} 個、{res['frameRate']:g} fps）"
        if res["warnings"]:
            text += "。注意: " + " / ".join(res["warnings"])
        self.set_status(text, warn=bool(res["warnings"]))
        self.track_label.setText(text.replace("。注意: ", "\n注意: ") + "\n（Unity 側では取り込んで Timeline に反映できます。実際のカットシーンでの確認は途中です）")

    # ============================================================ 後片付け
    def detach(self) -> None:
        """書き出し中なら止め（裏の mayapy を残さない）、タイマーと通知の購読をやめる。二重に呼んでも安全。"""
        self._detached = True
        self.timer.stop()
        job, self._job = self._job, None
        if job is not None:
            try:
                job.cancel()
            except Exception:  # noqa: BLE001
                lifecycle.report_error("書き出しを止められませんでした", traceback.format_exc(), once=False)
        if self._on_session_changed in self.session.listeners:
            self.session.listeners.remove(self._on_session_changed)
        _live_tabs.discard(self)
