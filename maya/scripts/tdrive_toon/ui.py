"""T-Drive Toon エディタ（docs/05_maya_tool_spec.md）。

UI はセッション層（session.current()）の薄いラッパー。操作はすべてセッション API を呼び、
表示はセッションの変更通知（listeners）で更新する。

ウィンドウ（ドッキング・エラー表示・Ctrl+Z の振り分け）は殻（tdrive.shell）が持つ。ここは Toon タブの中身
（ToonPanel）と、殻に載せるための ToonTool。`show()` / `restore()` / `close()` は互換のために残す
（古いドッキングのレイアウトに保存された uiScript「from tdrive_toon import ui; ui.restore()」も動く）。
"""

from __future__ import annotations

import re
from pathlib import Path

from maya import cmds
from PySide6 import QtCore, QtWidgets

from tdrive import shell

from . import environment, look, preview, project, roles, session
from .ui_ab import ABTab
from .ui_character import CharacterTab
from .ui_features import FeaturesTab
from .ui_look import LookTab
from .ui_preview import PreviewTab

CHARACTER_ID = re.compile(r"^[a-z0-9_]+$")
TOOL_ID = "toon"
# 旧エディタ（2 段タブになる前）の名前。他から参照されていた名前を残す
WINDOW_NAME = "TDriveToonEditor"
CONTROL_NAME = shell.CONTROL_NAME
RESTORE_SCRIPT = "from tdrive_toon import ui; ui.restore()"  # 古いレイアウトに保存されている uiScript


def show() -> "shell.ShellWindow":
    """殻（T-Drive ウィンドウ）を開いて Toon タブを前面にする。"""
    return shell.show(TOOL_ID)


def restore(control: str | None = None) -> None:
    """古い uiScript から呼ばれる。殻を作り直す（Toon タブ）。"""
    shell.restore(control)


def close() -> None:
    """ウィンドウを閉じる（ドッキング位置の記録も消す）。"""
    shell.close()


def _error(parent: QtWidgets.QWidget, exc: Exception) -> None:
    QtWidgets.QMessageBox.warning(parent, "T-Drive Toon", str(exc))


# ============================================================================ Toon パネル


class ToonPanel(QtWidgets.QWidget):
    """Toon タブの中身: ヘッダー + 部位/ルック/キャラクター/機能/A/B/プレビュー。"""

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
        self.parts_tab = PartsTab(self.session)
        self.tabs.addTab(self.parts_tab, "部位")
        self.look_tab = LookTab(self.session)
        self.tabs.addTab(self.look_tab, "ルック")
        self.character_tab = CharacterTab(self.session)
        self.tabs.addTab(self.character_tab, "キャラクター")
        self.features_tab = FeaturesTab(self.session)
        self.tabs.addTab(self.features_tab, "機能")
        self.ab_tab = ABTab(self.session)
        self.ab_tab.open_in_look.connect(self._open_in_look)
        self.tabs.addTab(self.ab_tab, "A/B")
        self.preview_tab = PreviewTab(self.session)
        self.tabs.addTab(self.preview_tab, "プレビュー")
        layout.addWidget(self.tabs, 1)
        self._stale: set[QtWidgets.QWidget] = set()  # 表示していないタブは次に開いたときに更新する（部位タブの更新は ~0.1 秒）
        self.tabs.currentChanged.connect(self._refresh_current_if_stale)

        self.session.listeners.append(self.refresh)
        self.refresh()

    def detach(self) -> None:
        try:
            self.preview_tab.timer.stop()
        except RuntimeError:
            pass  # 枠ごと破棄済み
        if self.refresh in self.session.listeners:
            self.session.listeners.remove(self.refresh)

    def _refresh_current_if_stale(self, _index: int = 0) -> None:
        tab = self.tabs.currentWidget()
        if tab in self._stale:
            self._stale.discard(tab)
            tab.refresh()

    def _open_in_look(self, target: str) -> None:
        self.tabs.setCurrentWidget(self.look_tab)
        self.look_tab.select_target(target)

    def refresh(self) -> None:
        self.header.refresh()
        self.preview_tab.refresh()  # 軽い。ライト安定化の設定値もここで追従させる
        current = self.tabs.currentWidget()
        for tab in (self.parts_tab, self.look_tab, self.character_tab, self.features_tab, self.ab_tab):
            if tab is current:
                tab.refresh()
                self._stale.discard(tab)
            else:
                self._stale.add(tab)
        problems = environment.parity_problems()
        self.warning.setText("Unity とのパリティ: " + " / ".join(problems) if problems else "")
        self.warning.setVisible(bool(problems))


class ToonTool:
    """殻（tdrive.shell）に載せる Toon ツール（tdrive.tool.Tool）。"""

    id = TOOL_ID
    label = "Toon"

    def __init__(self) -> None:
        self._panel: ToonPanel | None = None

    def build_widget(self) -> QtWidgets.QWidget:
        self._panel = ToonPanel()
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
        return "T-Drive: これ以上元に戻せません"

    def redo_message(self) -> str:
        return "T-Drive: やり直せる操作がありません"


def make_tool() -> ToonTool:
    return ToonTool()


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
        self.export_btn = QtWidgets.QPushButton("Unity 出力")
        self.export_btn.setToolTip("build/unity/<キャラクター>/ に materialdata.json と FBX を書き出す（FBX はバックグラウンド。開いているシーンは変わらない）")
        self.export_btn.clicked.connect(self.on_export)
        row.addWidget(self.export_btn)
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
        where = "開発用（ツール本体）" if project.is_tool_repo() else project.root().as_posix()
        self.label.setToolTip(f"プロジェクト: {project.root().as_posix()}（T-Drive › プロジェクトを選ぶ… で変更）")
        if lk is None:
            self.label.setText(f"プロジェクト: {where}\nLook: （未作成）— 新規 か 開く から始めてください")
        else:
            path = preview.to_repo_path(str(self.session.path)) if self.session.path else "（未保存）"
            dirty = "  ●未保存" if self.session.dirty else ""
            self.label.setText(f"プロジェクト: {where}\nLook: {path}   {lk['character']} v{lk['lookVersion']}{dirty}")
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
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Look を開く", str(project.looks_dir()), "Look (look.json *.json)")
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
        start = str(self.session.path or project.looks_dir() / lk["character"] / "look.json")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Look を別名保存", start, "Look (*.json)")
        if path:
            try:
                self.session.save(path)
            except Exception as exc:
                _error(self, exc)

    def on_export(self) -> None:
        lk = self.session.look
        if lk is None:
            return
        variants = look.variant_names(lk)
        variant = look.BASE
        if len(variants) > 1:
            variant, ok = QtWidgets.QInputDialog.getItem(self, "Unity 出力", "書き出すバリアント:", variants, 0, False)
            if not ok:
                return
        try:
            json_path, self._job = self.session.start_unity_export(variant)
        except Exception as exc:
            _error(self, exc)
            return
        self._json_path = json_path
        self.export_btn.setEnabled(False)
        self.export_btn.setText("出力中…（FBX）")
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll_export)
        self._timer.start()

    def _poll_export(self) -> None:
        if not self._job.poll():
            return
        self._timer.stop()
        self.export_btn.setEnabled(True)
        self.export_btn.setText("Unity 出力")
        try:
            res = self._job.result()
        except Exception as exc:
            _error(self, exc)
            return
        warnings = res.get("warnings", [])
        text = (
            f"書き出しました:\n{preview.to_repo_path(str(self._json_path))}\n{preview.to_repo_path(res['path'])}"
            + (f"\n\n注意 {len(warnings)} 件（詳細）" if warnings else "")
        )
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, "Unity 出力", text, parent=self)
        if warnings:
            box.setDetailedText("\n".join(warnings))
        box.exec()

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
        self.mask_box = MaskBox(self.session, self._selected_parts)
        v.addWidget(self.mask_box)
        v.addWidget(FaceNormalBox(self.session, self._selected_parts))
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

                paths = scene.get(mat, [])
                meshes = QtWidgets.QTableWidgetItem(", ".join(preview.short_name(p) for p in paths))
                meshes.setToolTip("\n".join(paths))  # 同名メッシュの区別用に完全パス
                meshes.setFlags(meshes.flags() & ~QtCore.Qt.ItemIsEditable)
                self.table.setItem(row, 3, meshes)
            self.table.resizeColumnsToContents()
            self.table.setColumnWidth(2, 170)
        finally:
            self._updating = False
        self.mask_box.refresh()

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


class MaskBox(QtWidgets.QGroupBox):
    """Toon マスク（頂点カラー）: 初期化・チャンネル単位ペイント・チャンネル単体表示（docs/05 §3）。"""

    def __init__(self, s: session.Session, selected_parts) -> None:
        super().__init__("Toon マスク（頂点カラー。白 = 何もしない、効かせたい所を黒く塗る）")
        from . import mask

        self.session, self.selected_parts, self.mask = s, selected_parts, mask
        v = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        init = QtWidgets.QPushButton("マスク初期化")
        init.setToolTip("対象メッシュに Color Set tdToonMask を作り白で埋める（既にあれば何もしない）")
        init.clicked.connect(self.on_init)
        row.addWidget(init)
        bake = QtWidgets.QPushButton("スムーズ法線を焼く")
        bake.setToolTip("アウトラインが角で割れないよう、平均化した法線を UV Set tdSmoothNormal に焼く（形状を変えたら焼き直す）")
        bake.clicked.connect(lambda: self._run(self.session.bake_smooth_normals, self._target()))
        row.addWidget(bake)
        row.addWidget(QtWidgets.QLabel("  塗る:"))
        self.paint_buttons = {}
        for ch in mask.CHANNELS:
            b = QtWidgets.QPushButton(ch)
            b.setToolTip(mask.CHANNEL_HELP[ch])
            b.setFixedWidth(34)
            b.clicked.connect(lambda _c=False, c=ch: self.on_paint(c))
            row.addWidget(b)
            self.paint_buttons[ch] = b
        self.commit_btn = QtWidgets.QPushButton("確定")
        self.cancel_btn = QtWidgets.QPushButton("キャンセル")
        self.commit_btn.clicked.connect(lambda: self._run(self.session.end_mask_paint, True))
        self.cancel_btn.clicked.connect(lambda: self._run(self.session.end_mask_paint, False))
        row.addWidget(self.commit_btn)
        row.addWidget(self.cancel_btn)
        row.addStretch(1)
        row.addWidget(QtWidgets.QLabel("表示:"))
        self.view = QtWidgets.QComboBox()
        self.view.addItem("通常", None)
        for ch in mask.CHANNELS:
            self.view.addItem(f"{ch} だけ（白黒）", ch)
        self.view.currentIndexChanged.connect(lambda _i: mask.show_channel(self.view.currentData()))
        row.addWidget(self.view)
        v.addLayout(row)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        v.addWidget(self.status)

    def _target(self) -> str | None:
        parts = self.selected_parts()
        return parts[0] if parts else None  # 表で部位を選んでいればその部位、無ければ Maya の選択

    def _run(self, fn, *args):
        try:
            fn(*args)
        except Exception as exc:
            _error(self, exc)

    def on_init(self) -> None:
        self._run(self.session.init_mask, self._target())

    def on_paint(self, channel: str) -> None:
        self._run(self.session.begin_mask_paint, channel, self._target())

    def refresh(self) -> None:
        editing = self.mask.editing()
        self.commit_btn.setEnabled(bool(editing))
        self.cancel_btn.setEnabled(bool(editing))
        for ch, b in self.paint_buttons.items():
            b.setStyleSheet("background-color: #3a6ea5;" if ch == editing else "")
        if editing:
            self.status.setText(f"<b>{editing}</b> を編集中: {self.mask.CHANNEL_HELP[editing]}。Paint Vertex Color Tool で黒く塗り、終わったら <b>確定</b>。")
        else:
            self.status.setText("表で部位を選ぶとその部位、選ばなければ Maya で選択中のメッシュが対象。R/G/B/A を押すとそのチャンネルを塗り始めます。")


class FaceNormalBox(QtWidgets.QGroupBox):
    """顔の法線（Toon Normal、docs/05 §3.1）: 楕円体プロキシの法線へ寄せて影を単純にする。"""

    def __init__(self, s: session.Session, selected_parts) -> None:
        super().__init__("顔の法線（楕円体に寄せて顔の影を単純にする）")
        self.session, self.selected_parts = s, selected_parts
        row = QtWidgets.QHBoxLayout(self)
        proxy = QtWidgets.QPushButton("プロキシ作成")
        proxy.setToolTip("対象の大きさに合わせた楕円体（tdFaceNormalProxy）を作る。移動・回転・スケールで顔の形に合わせる")
        proxy.clicked.connect(lambda: self._run(self.session.create_face_proxy, self._target()))
        row.addWidget(proxy)
        row.addWidget(QtWidgets.QLabel("強さ"))
        self.weight = QtWidgets.QDoubleSpinBox()
        self.weight.setRange(0.0, 1.0)
        self.weight.setSingleStep(0.1)
        self.weight.setValue(0.7)
        row.addWidget(self.weight)
        self.selected_only = QtWidgets.QCheckBox("選択した頂点だけ")
        row.addWidget(self.selected_only)
        apply = QtWidgets.QPushButton("転写")
        apply.clicked.connect(
            lambda: self._run(self.session.transfer_face_normals, self._target(), self.weight.value(), self.selected_only.isChecked())
        )
        row.addWidget(apply)
        reset = QtWidgets.QPushButton("リセット")
        reset.setToolTip("最初に転写する前の法線に戻す")
        reset.clicked.connect(lambda: self._run(self.session.reset_face_normals, self._target()))
        row.addWidget(reset)
        row.addStretch(1)

    def _target(self) -> str | None:
        parts = self.selected_parts()
        return parts[0] if parts else None

    def _run(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as exc:
            _error(self, exc)
