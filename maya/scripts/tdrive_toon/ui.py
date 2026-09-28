"""T-Drive Toon エディタ（docs/05_maya_tool_spec.md）。

UI はセッション層（session.current()）の薄いラッパー。操作はすべてセッション API を呼び、
表示はセッションの変更通知（listeners）で更新する。
"""

from __future__ import annotations

import re
from pathlib import Path

from maya import cmds
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin
from PySide6 import QtCore, QtWidgets

from . import __version__, environment, look, preview, roles, session
from .ui_look import LookTab

WINDOW_NAME = "TDriveToonEditor"
CHARACTER_ID = re.compile(r"^[a-z0-9_]+$")

_window: "EditorWindow | None" = None


def show() -> "EditorWindow":
    global _window
    close()
    _window = EditorWindow()
    _window.show(dockable=True, floating=True, area="right")
    return _window


def close() -> None:
    global _window
    ctrl = f"{WINDOW_NAME}WorkspaceControl"
    if cmds.workspaceControl(ctrl, exists=True):
        cmds.deleteUI(ctrl)
    if _window is not None:
        _window.detach()
        _window = None


def _error(parent: QtWidgets.QWidget, exc: Exception) -> None:
    QtWidgets.QMessageBox.warning(parent, "T-Drive Toon", str(exc))


# ============================================================================ ウィンドウ


class EditorWindow(MayaQWidgetDockableMixin, QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(WINDOW_NAME)
        self.setWindowTitle(f"T-Drive Toon {__version__}")
        self.session = session.current()

        layout = QtWidgets.QVBoxLayout(self)
        self.header = HeaderBar(self.session)
        layout.addWidget(self.header)
        self.warning = QtWidgets.QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #f0a040;")
        layout.addWidget(self.warning)

        self.tabs = QtWidgets.QTabWidget()
        self.parts_tab = PartsTab(self.session)
        self.tabs.addTab(self.parts_tab, "部位")
        self.look_tab = LookTab(self.session)
        self.tabs.addTab(self.look_tab, "ルック")
        self.tabs.addTab(_placeholder("A/B 比較は 1-7 で実装予定"), "A/B")
        self.tabs.addTab(_placeholder("プレビュー設定は 1-8 で実装予定"), "プレビュー")
        layout.addWidget(self.tabs, 1)

        self.session.listeners.append(self.refresh)
        self.refresh()

    def detach(self) -> None:
        if self.refresh in self.session.listeners:
            self.session.listeners.remove(self.refresh)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt)
        self.detach()
        super().closeEvent(event)

    def refresh(self) -> None:
        self.header.refresh()
        self.parts_tab.refresh()
        self.look_tab.refresh()
        problems = environment.parity_problems()
        self.warning.setText("Unity とのパリティ: " + " / ".join(problems) if problems else "")
        self.warning.setVisible(bool(problems))


def _placeholder(text: str) -> QtWidgets.QWidget:
    w = QtWidgets.QLabel(text)
    w.setAlignment(QtCore.Qt.AlignCenter)
    w.setEnabled(False)
    return w


# ============================================================================ ヘッダー


class HeaderBar(QtWidgets.QWidget):
    def __init__(self, s: session.Session) -> None:
        super().__init__()
        self.session = s
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        self.label = QtWidgets.QLabel()
        v.addWidget(self.label)

        row = QtWidgets.QHBoxLayout()
        for text, fn in (("新規", self.on_new), ("開く", self.on_open), ("保存", self.on_save), ("別名保存", self.on_save_as)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(QtWidgets.QLabel("表示:"))
        self.toon = QtWidgets.QRadioButton("Toon")
        self.original = QtWidgets.QRadioButton("元の見た目")
        self.toon.toggled.connect(self.on_toggle_preview)
        row.addWidget(self.toon)
        row.addWidget(self.original)
        v.addLayout(row)

    def refresh(self) -> None:
        lk = self.session.look
        if lk is None:
            self.label.setText("Look: （未作成）— 新規 か 開く から始めてください")
        else:
            path = preview.to_repo_path(str(self.session.path)) if self.session.path else "（未保存）"
            dirty = "  ●未保存" if self.session.dirty else ""
            self.label.setText(f"Look: {path}   {lk['character']} v{lk['lookVersion']}{dirty}")
        active = preview.is_active()
        for b, on in ((self.toon, active), (self.original, not active)):
            b.blockSignals(True)
            b.setChecked(on)
            b.setEnabled(lk is not None)
            b.blockSignals(False)

    def _confirm_discard(self) -> bool:
        if not self.session.dirty:
            return True
        r = QtWidgets.QMessageBox.question(self, "T-Drive Toon", "未保存の変更があります。破棄しますか？")
        return r == QtWidgets.QMessageBox.Yes

    def on_new(self) -> None:
        if not self._confirm_discard():
            return
        default = Path(cmds.file(query=True, sceneName=True) or "character").stem.lower()
        default = re.sub(r"[^a-z0-9_]", "_", default)
        name, ok = QtWidgets.QInputDialog.getText(self, "新規 Look", "キャラクター ID（英小文字・数字・_）:", text=default)
        if not ok:
            return
        if not CHARACTER_ID.match(name):
            _error(self, ValueError("キャラクター ID は英小文字・数字・_ のみです"))
            return
        self.session.new(name)

    def on_open(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Look を開く", str(session.LOOKS_DIR), "Look (look.json *.json)")
        if path:
            try:
                self.session.open(path)
            except Exception as exc:  # 検証エラーをそのまま見せる
                _error(self, exc)

    def on_save(self) -> None:
        try:
            self.session.save()
        except Exception as exc:
            _error(self, exc)

    def on_save_as(self) -> None:
        lk = self.session.look
        if lk is None:
            return
        start = str(self.session.path or session.LOOKS_DIR / lk["character"] / "look.json")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Look を別名保存", start, "Look (*.json)")
        if path:
            try:
                self.session.save(path)
            except Exception as exc:
                _error(self, exc)

    def on_toggle_preview(self, checked: bool) -> None:
        try:
            self.session.set_preview(self.toon.isChecked())
        except Exception as exc:
            _error(self, exc)


# ============================================================================ 部位タブ（R-1）

ROLE_LABELS = {r: f"{roles.ROLE_PRESETS[r]['label']} ({r})" for r in roles.ROLES}
UNREGISTERED = "（未登録）"


class PartsTab(QtWidgets.QWidget):
    COLS = ("マテリアル", "部位", "ロール", "メッシュ")

    def __init__(self, s: session.Session) -> None:
        super().__init__()
        self.session = s
        v = QtWidgets.QVBoxLayout(self)

        row = QtWidgets.QHBoxLayout()
        for text, tip, fn in (
            ("自動登録", "未登録のマテリアルを名前からロール推定して登録し、Toon 表示にする", self.on_auto),
            ("選択から登録…", "選択中のメッシュ / 面のマテリアルを部位に登録する", self.on_register_selection),
            ("部位を選択", "表で選んだ行の部位に属するメッシュを Maya で選択する", self.on_select_part),
            ("登録解除", "表で選んだ行の部位を登録解除する（マテリアルの調整値は残る）", self.on_unregister),
        ):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)

        self.table = QtWidgets.QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked)
        self.table.itemChanged.connect(self.on_item_changed)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setTextElideMode(QtCore.Qt.ElideRight)
        v.addWidget(self.table, 1)

        self.hint = QtWidgets.QLabel("部位名はダブルクリックで変更。別の部位名を入力するとマテリアルがその部位へ移動します。")
        self.hint.setEnabled(False)
        v.addWidget(self.hint)
        self._updating = False

    # -------------------------------------------------------------- 表示
    def refresh(self) -> None:
        self._updating = True
        try:
            lk = self.session.look
            scene = preview.scene_materials()
            names = sorted(set(scene) | set(lk["materials"] if lk else []))
            # 一度空にしてセル内ウィジェット（ロールのコンボ）を確実に破棄する。残ると前回の表示が重なる
            self.table.setRowCount(0)
            self.table.setRowCount(len(names))
            for row, mat in enumerate(names):
                part = look.part_of(lk, mat) if lk else None
                item = QtWidgets.QTableWidgetItem(mat)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                if mat not in scene:
                    item.setToolTip("シーンにありません（Look にだけ存在）")
                    item.setForeground(QtCore.Qt.gray)
                self.table.setItem(row, 0, item)

                part_item = QtWidgets.QTableWidgetItem(part or UNREGISTERED)
                part_item.setData(QtCore.Qt.UserRole, mat)
                if lk is None:
                    part_item.setFlags(part_item.flags() & ~QtCore.Qt.ItemIsEditable)
                self.table.setItem(row, 1, part_item)

                combo = QtWidgets.QComboBox()
                for r in roles.ROLES:
                    combo.addItem(ROLE_LABELS[r], r)
                combo.setEnabled(part is not None)
                if part:
                    combo.setCurrentIndex(roles.ROLES.index(lk["parts"][part]["role"]))
                combo.currentIndexChanged.connect(lambda _i, p=part, c=combo: self.on_role_changed(p, c.currentData()))
                self.table.setCellWidget(row, 2, combo)

                meshes = QtWidgets.QTableWidgetItem(", ".join(scene.get(mat, [])))
                meshes.setFlags(meshes.flags() & ~QtCore.Qt.ItemIsEditable)
                self.table.setItem(row, 3, meshes)
            self.table.resizeColumnsToContents()
            self.table.setColumnWidth(2, 170)
        finally:
            self._updating = False

    def _selected_parts(self) -> list[str]:
        lk = self.session.look
        parts = []
        for idx in self.table.selectionModel().selectedRows():
            mat = self.table.item(idx.row(), 0).text()
            p = look.part_of(lk, mat) if lk else None
            if p and p not in parts:
                parts.append(p)
        return parts

    # -------------------------------------------------------------- 操作
    def _run(self, fn, *args):
        try:
            return fn(*args)
        except Exception as exc:
            _error(self, exc)
            self.refresh()
            return None

    def on_auto(self) -> None:
        if self.session.look is None:
            _error(self, RuntimeError("先に 新規 か 開く で Look を用意してください"))
            return
        done = self._run(self.session.auto_register)
        if done is not None and not preview.is_active():
            self._run(self.session.show, self.session.shown)

    def on_register_selection(self) -> None:
        if self.session.look is None:
            _error(self, RuntimeError("先に 新規 か 開く で Look を用意してください"))
            return
        mats = preview.materials_on_selection()
        if not mats:
            _error(self, RuntimeError("メッシュか面を選択してください"))
            return
        dlg = RegisterDialog(self, mats, sorted(self.session.look["parts"]))
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            part, role = dlg.values()
            self._run(self.session.register, part, role, mats)

    def on_select_part(self) -> None:
        for p in self._selected_parts()[:1]:
            self._run(self.session.select_part, p)

    def on_unregister(self) -> None:
        for p in self._selected_parts():
            self._run(self.session.unregister, p)

    def on_role_changed(self, part: str | None, role: str) -> None:
        if self._updating or not part:
            return
        r = QtWidgets.QMessageBox.question(
            self, "ロール変更", f"部位 '{part}' のロールを {ROLE_LABELS[role]} にします。\nロールのプリセット値を再適用しますか？\n（いいえ = 調整値はそのまま）",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel,
        )
        if r == QtWidgets.QMessageBox.Cancel:
            self.refresh()
            return
        self._run(self.session.set_role, part, role, r == QtWidgets.QMessageBox.Yes)

    def on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._updating or item.column() != 1:
            return
        mat = item.data(QtCore.Qt.UserRole)
        new = item.text().strip()
        lk = self.session.look
        old = look.part_of(lk, mat)
        if not new or new == UNREGISTERED or new == old:
            self.refresh()
            return
        if old and len(lk["parts"][old]["materials"]) == 1 and new not in lk["parts"]:
            self._run(self.session.rename_part, old, new)  # 1 マテリアルだけの部位なら部位名の変更
        else:
            self._run(self.session.move_material, mat, new)


class RegisterDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget, materials: list[str], parts: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("選択から登録")
        form = QtWidgets.QFormLayout(self)
        form.addRow("マテリアル", QtWidgets.QLabel(", ".join(materials)))
        guess = roles.guess_role(materials[0])
        self.part = QtWidgets.QComboBox()
        self.part.setEditable(True)
        self.part.addItems(parts)
        self.part.setCurrentText(guess if guess != "other" else materials[0])
        form.addRow("部位名", self.part)
        self.role = QtWidgets.QComboBox()
        for r in roles.ROLES:
            self.role.addItem(ROLE_LABELS[r], r)
        self.role.setCurrentIndex(roles.ROLES.index(guess))
        form.addRow("ロール", self.role)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[str, str]:
        return self.part.currentText().strip(), self.role.currentData()
