"""アニメーション作業台（1 段目タブ「Animation」。docs/20）。

UI はセッション層（session.current()）の薄いラッパー。操作はセッション API を呼び、表示はセッションの変更通知（listeners）で更新する。
ウィンドウ（ドッキング・エラー表示・Ctrl+Z の振り分け）は殻（tdrive.shell）が持つ。ここは Animation タブの中身（AnimPanel）と、
殻に載せるための AnimTool。dx11Shader・Render Override・.fx は使わない（DirectX 11 でない環境でも動く）。
"""

from __future__ import annotations

import traceback
from pathlib import Path

from maya import cmds
from PySide6 import QtCore, QtWidgets

from tdrive import lifecycle, project, shell

from . import session
from . import transport as T
from .core import animset as A
from .ui_blend import BlendTab
from .ui_clips import ClipsTab
from .ui_export import ExportTab
from .ui_naming import NamingTab
from .ui_shot import ShotTab

TOOL_ID = "anim"


def show() -> "shell.ShellWindow":
    """殻（T-Drive ウィンドウ）を開いて Animation タブを前面にする。"""
    return shell.show(TOOL_ID)


def _warn(parent: QtWidgets.QWidget, exc: Exception) -> None:
    QtWidgets.QMessageBox.warning(parent, "T-Drive Animation", str(exc))


class HeaderBar(QtWidgets.QWidget):
    """ModelId・animset の場所・新規 / 開く / 保存。"""

    def __init__(self, s: session.AnimSession) -> None:
        super().__init__()
        self.session = s
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.label = QtWidgets.QLabel()
        self.label.setWordWrap(True)
        v.addWidget(self.label)
        row = QtWidgets.QHBoxLayout()
        self.model_id = QtWidgets.QLineEdit()
        self.model_id.setPlaceholderText("ModelId（例: MisotenChara）")
        self.model_id.setToolTip("英数字の PascalCase。D-Drive の Model 識別子・カットシーンの __<ModelId>・ネームスペースに使います。\n既定: MisotenChara（仮。docs/20 §9）")
        row.addWidget(self.model_id, 1)
        self.new_btn = QtWidgets.QPushButton("新規")
        self.new_btn.setToolTip("この ModelId で animset を新しく作ります（保存するまでファイルは書きません。既にあるときは開くか上書きかを聞きます）")
        self.new_btn.clicked.connect(lambda *_: self._new())
        self.open_btn = QtWidgets.QPushButton("開く…")
        self.open_btn.setToolTip("既存の animset.json を開きます（未保存の変更とエディタ内の Undo は捨てます）")
        self.open_btn.clicked.connect(lambda *_: self._open())
        self.save_btn = QtWidgets.QPushButton("保存")
        self.save_btn.setToolTip("animset.json を保存します（シーンを保存したときも自動で保存されます）")
        self.save_btn.clicked.connect(lambda *_: self._save())
        for b in (self.new_btn, self.open_btn, self.save_btn):
            row.addWidget(b)
        v.addLayout(row)
        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel("リグのネームスペース"))
        self.namespace = QtWidgets.QLineEdit()
        self.namespace.setPlaceholderText("無ければ空（参照して読み込んだリグは MisotenChara など）")
        self.namespace.setToolTip("シーンのリグのネームスペース。スケルトンのルート・クリップの再生・出力はこのネームスペースの中を見ます。出力する FBX には付きません。既定: 空")
        self.namespace.editingFinished.connect(lambda *_: self._set_namespace())
        row2.addWidget(self.namespace, 1)
        v.addLayout(row2)
        self.refresh()

    def _set_namespace(self) -> None:
        s = self.session
        text = self.namespace.text().strip().rstrip(":")
        if s.data is not None and text != (s.data.get("namespace") or ""):
            s.set_value("namespace", text)

    def refresh(self) -> None:
        s = self.session
        if s.data is None:
            self.label.setText("animset が開かれていません。ModelId を入れて「新規」か、「開く…」で既存のものを選んでください")
        else:
            where = project.to_project_path(s.path) if s.path else "（未保存）"
            mark = " *" if s.dirty else ""
            self.label.setText(f"ModelId: {s.model_id}　{s.data['frameRate']} fps　クリップ {len(s.data['clips'])} 件\nanimset: {where}{mark}")
        self.save_btn.setEnabled(s.data is not None)
        self.namespace.setEnabled(s.data is not None)
        if s.data is not None and not self.model_id.hasFocus():
            self.model_id.setText(s.model_id)
        if s.data is not None and not self.namespace.hasFocus():
            self.namespace.setText(s.data.get("namespace", "") or "")

    def _confirm_discard(self, what: str) -> bool:
        """未保存の変更を捨ててよいか（既定「いいえ」。Toon の `_confirm_discard` と同じ作法。UX-4）。"""
        if not self.session.has_unsaved_work:
            return True
        r = QtWidgets.QMessageBox.question(self, "T-Drive Animation", f"未保存の変更があります。{what}と、その変更は失われます。続けますか？",
                                           QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        return r == QtWidgets.QMessageBox.Yes

    def _new(self) -> None:
        mid = self.model_id.text().strip() or "MisotenChara"
        if not self._confirm_discard("新規にする"):
            return
        try:
            existing = self.session.existing_default(mid)
            if existing is not None:  # UX-3: 既存の animset を確認なしに上書きしない
                box = QtWidgets.QMessageBox(self)
                box.setWindowTitle("T-Drive Animation")
                box.setText(f"{project.to_project_path(existing)} が既にあります。")
                box.setInformativeText("既存のものを開きますか？ 新規にして保存すると、既存のクリップの記録は上書きで失われます。")
                open_btn = box.addButton("既存を開く", QtWidgets.QMessageBox.AcceptRole)
                over_btn = box.addButton("新規にして上書きする", QtWidgets.QMessageBox.DestructiveRole)
                box.addButton("キャンセル", QtWidgets.QMessageBox.RejectRole)
                box.setDefaultButton(open_btn)
                box.exec()
                clicked = box.clickedButton()
                if clicked is open_btn:
                    self.session.open(existing)
                    return
                if clicked is not over_btn:
                    return
                self.session.new(mid)
                self.session.confirm_overwrite()
                return
            self.session.new(mid)
        except Exception as exc:  # noqa: BLE001
            _warn(self, exc)

    def _open(self) -> None:
        if not self._confirm_discard("開く"):
            return
        start = project.anim_dir()
        picked = cmds.fileDialog2(fileMode=1, caption="animset.json を開く", fileFilter="animset (*.json)", startingDirectory=start.as_posix())
        if picked:
            try:
                self.session.open(picked[0])
            except Exception as exc:  # noqa: BLE001  壊れた JSON は警告だけ（画面は止めない）
                _warn(self, exc)

    def _save(self) -> None:
        try:
            self.session.save()
        except Exception as exc:  # noqa: BLE001
            _warn(self, exc)


class AnimPanel(QtWidgets.QWidget):
    """Animation タブの中身: ヘッダー + 命名 / クリップ / ブレンド / ショット / 出力。"""

    def __init__(self) -> None:
        super().__init__()
        self.session = session.current()
        layout = QtWidgets.QVBoxLayout(self)
        self.header = HeaderBar(self.session)
        layout.addWidget(self.header)
        self.warning = QtWidgets.QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #f0a040;")
        layout.addWidget(self.warning)
        bar = QtWidgets.QHBoxLayout()  # 再生の状態の常時表示と「■ すべて停止」（AN-F-35）
        self.transport_label = QtWidgets.QLabel(T.status_text())
        bar.addWidget(self.transport_label, 1)
        self.stop_all_btn = QtWidgets.QPushButton("■ すべて停止")
        self.stop_all_btn.setToolTip("Maya の再生・クリップ / ショットの再生・作り直しの待ちを全部止めます")
        self.stop_all_btn.clicked.connect(lambda *_: self.stop_all())
        bar.addWidget(self.stop_all_btn)
        layout.addLayout(bar)
        self._transport_timer = QtCore.QTimer(self)
        self._transport_timer.setInterval(300)
        self._transport_timer.timeout.connect(lambda *_: self._update_transport())
        self._transport_timer.start()
        self.msg_host = QtWidgets.QWidget()  # 各タブの結果・警告の行。スクロール領域の外・最上部に固定する（AN-F-17）
        self.msg_layout = QtWidgets.QVBoxLayout(self.msg_host)
        self.msg_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.msg_host)

        self.tabs = QtWidgets.QTabWidget()
        self.naming_tab = NamingTab(self.session)
        self.clips_tab = ClipsTab(self.session)
        self.blend_tab = BlendTab(self.session)
        self.shot_tab = ShotTab(self.session)
        self.export_tab = ExportTab(self.session)
        for tab, label in ((self.naming_tab, "命名"), (self.clips_tab, "クリップ"), (self.blend_tab, "ブレンド"),
                           (self.shot_tab, "ショット"), (self.export_tab, "出力")):
            self.tabs.addTab(tab, label)
        layout.addWidget(self.tabs, 1)
        for tab in self._all_tabs():  # タブの message をタブの外へ。前に出ているタブのものだけ見せる
            self.msg_layout.addWidget(tab.message)
        self._show_current_message()
        self.tabs.currentChanged.connect(lambda *_: self._show_current_message())
        self.shot_tab.blend_tab = self.blend_tab  # 遷移列の受け渡し（ブレンドタブ ⇄ ショットタブ）
        self.blend_tab.shot_tab = self.shot_tab

        self.tabs.currentChanged.connect(lambda *_: self._refresh_current())  # UX-1: 前に出したタブを描き直す（前に出ていない間の変更を反映）
        self.session.listeners.append(self.refresh)
        self.refresh()

    def _show_current_message(self) -> None:
        current = self.tabs.currentWidget()
        for tab in self._all_tabs():
            tab.message.setVisible(tab is current)

    def _refresh_current(self) -> None:
        current = self.tabs.currentWidget()
        if current is None:
            return
        try:
            current.refresh()
        except Exception:  # noqa: BLE001  壊れたデータでタブ全体を止めない（UX-2）
            lifecycle.report_error(f"{self.tabs.tabText(self.tabs.currentIndex())}タブの更新で例外", traceback.format_exc())

    def _all_tabs(self):
        return (self.naming_tab, self.clips_tab, self.blend_tab, self.shot_tab, self.export_tab)

    def stop_all(self) -> None:
        T.stop_all()
        self._update_transport()

    def _update_transport(self) -> None:
        text = T.status_text()
        if self.transport_label.text() != text:
            self.transport_label.setText(text)
            self.transport_label.setStyleSheet("color: #f0a040;" if T.status() else "")

    def detach(self) -> None:
        self._transport_timer.stop()
        T.stop_all()  # ウィンドウを閉じるときも全部止める（AN-F-35）
        for tab in reversed(self._all_tabs()):  # 作った順の逆に後片付けする（ショット → ブレンド → クリップ…。再生範囲の控えが戻る順。SF-8）
            tab.detach()
        if self.refresh in self.session.listeners:
            self.session.listeners.remove(self.refresh)

    def refresh(self) -> None:
        self.header.refresh()
        # 壊れたデータは警告だけで止めない（Phase E の流儀）
        issues = self.session.issues()
        self.warning.setText("\n".join(str(i) for i in issues[:5]) + (f"\n…ほか {len(issues) - 5} 件" if len(issues) > 5 else ""))
        self.warning.setVisible(bool(issues))
        self._refresh_current()


class AnimTool:
    """殻（tdrive.shell）に載せる Animation ツール（tdrive.tool.Tool）。"""

    id = TOOL_ID
    label = "Animation"

    def __init__(self) -> None:
        self._panel: AnimPanel | None = None

    def build_widget(self) -> QtWidgets.QWidget:
        self._panel = AnimPanel()
        return self._panel

    def on_scene_opened(self) -> None:
        T.stop_all()  # 別のシーンを開いたら、再生・トランスポートを全部止める（AN-F-35）
        session.on_scene_opened()

    def on_scene_saved(self) -> None:
        session.on_scene_saved()

    def undo(self) -> bool:
        return session.current().undo()

    def redo(self) -> bool:
        return session.current().redo()

    def refresh(self) -> None:
        if self._panel is not None:
            try:
                self._panel.refresh()
            except RuntimeError:
                self._panel = None  # 枠ごと破棄済み

    def undo_message(self) -> str:
        return "Animation: " + (session.current().last_undo_message or "これ以上元に戻せません")

    def redo_message(self) -> str:
        return "Animation: " + (session.current().last_undo_message or "やり直せる操作がありません")


def make_tool() -> AnimTool:
    return AnimTool()
