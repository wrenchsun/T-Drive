"""アニメーション作業台（1 段目タブ「Animation」。docs/20）。

UI はセッション層（session.current()）の薄いラッパー。操作はセッション API を呼び、表示はセッションの変更通知（listeners）で更新する。
ウィンドウ（ドッキング・エラー表示・Ctrl+Z の振り分け）は殻（tdrive.shell）が持つ。ここは Animation タブの中身（AnimPanel）と、
殻に載せるための AnimTool。dx11Shader・Render Override・.fx は使わない（DirectX 11 でない環境でも動く）。
"""

from __future__ import annotations

from pathlib import Path

from maya import cmds
from PySide6 import QtWidgets

from tdrive import project, shell

from . import session
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
        self.new_btn.setToolTip("この ModelId で animset を新しく作ります（保存するまでファイルは書きません）")
        self.new_btn.clicked.connect(lambda *_: self._new())
        self.open_btn = QtWidgets.QPushButton("開く…")
        self.open_btn.clicked.connect(lambda *_: self._open())
        self.save_btn = QtWidgets.QPushButton("保存")
        self.save_btn.setToolTip("animset.json を保存します（シーンを保存したときも自動で保存されます）")
        self.save_btn.clicked.connect(lambda *_: self._save())
        for b in (self.new_btn, self.open_btn, self.save_btn):
            row.addWidget(b)
        v.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        s = self.session
        if s.data is None:
            self.label.setText("animset が開かれていません。ModelId を入れて「新規」か、「開く…」で既存のものを選んでください")
        else:
            where = project.to_project_path(s.path) if s.path else "（未保存）"
            mark = " *" if s.dirty else ""
            self.label.setText(f"ModelId: {s.model_id}　{s.data['frameRate']} fps　クリップ {len(s.data['clips'])} 件\nanimset: {where}{mark}")
        self.save_btn.setEnabled(s.data is not None)
        if s.data is not None and not self.model_id.hasFocus():
            self.model_id.setText(s.model_id)

    def _new(self) -> None:
        try:
            self.session.new(self.model_id.text().strip() or "MisotenChara")
        except Exception as exc:  # noqa: BLE001
            _warn(self, exc)

    def _open(self) -> None:
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

        self.session.listeners.append(self.refresh)
        self.refresh()

    def _all_tabs(self):
        return (self.naming_tab, self.clips_tab, self.blend_tab, self.shot_tab, self.export_tab)

    def detach(self) -> None:
        for tab in self._all_tabs():
            tab.detach()
        if self.refresh in self.session.listeners:
            self.session.listeners.remove(self.refresh)

    def refresh(self) -> None:
        self.header.refresh()
        # 壊れたデータは警告だけで止めない（Phase E の流儀）
        issues = self.session.issues()
        self.warning.setText("\n".join(str(i) for i in issues[:5]) + (f"\n…ほか {len(issues) - 5} 件" if len(issues) > 5 else ""))
        self.warning.setVisible(bool(issues))
        current = self.tabs.currentWidget()
        if current is not None:
            current.refresh()


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
        return "Animation: これ以上元に戻せません"

    def redo_message(self) -> str:
        return "Animation: やり直せる操作がありません"


def make_tool() -> AnimTool:
    return AnimTool()
