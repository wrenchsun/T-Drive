"""FacialController タブの中身と、殻（tdrive.shell）に載せるツール（docs/14 §5、docs/15 §2.1・§2.2）。

UI はセッション層（`tdrive_facial.session.current()`）の薄いラッパー。操作はすべてセッションのコマンドを呼び、
表示はセッションの変更通知（`listeners`）で更新する（Presenter を直接書き換えない）。作りは Toon（`tdrive_toon/ui.py`）に合わせてある。

## タブの読み込みの約束
内側のタブは `tdrive_facial.ui_<名前>` に `class <名前>Tab(QtWidgets.QWidget)` を置けば自動で載る。

| タブ | モジュール | クラス |
| --- | --- | --- |
| セットアップ | ui_setup | SetupTab |
| グリッド | ui_grid | GridTab |
| ポーズ | ui_pose | PoseTab |
| シェイプ | ui_shapes | ShapesTab |
| レイヤー | ui_layers | LayersTab |
| 検証 | ui_validate | ValidateTab |
| 出力 | ui_export | ExportTab |

- `__init__(self, session)`、`refresh()`、あれば `detach()`（パネルを閉じるときに呼ぶ）
- 初めて開いたときに作る（遅延）。モジュールがまだ無ければ「準備中です」の表示、作れなければエラーの表示にして、パネルは必ず開く
- 表示していないタブは「古い」印だけ付け、開いたときに `refresh()` する
"""

from __future__ import annotations

import importlib
import re
import traceback
from pathlib import Path
from typing import Optional

from maya import cmds
from PySide6 import QtCore, QtWidgets

from tdrive import lifecycle, project

from . import scene as scene_mod
from . import session as session_mod
from .core import profile as profile_mod
from .core.presenters import CONFIRM_CANCEL, CONFIRM_DISCARD, CONFIRM_SAVE

TOOL_ID = "facial"
TITLE = "FacialController"
CHARACTER_ID = re.compile(r"^[a-z0-9_]+$")
NO_PROFILE = "（なし）"
WARN_STYLE = "color: #f0a040;"
DIM_STYLE = "color: #9aa6b8;"

# Yaw の向きの言い方（セットアップ・グリッド・ポーズ・プレビューのヒントはこれに合わせる。Unity の格子ウィンドウは「左 = −Yaw（カメラが右）」で同じ意味）
YAW_PLUS_HELP = "+Yaw = キャラクターの左側から見る（カメラがキャラクターの左）"
YAW_MINUS_HELP = "−Yaw = キャラクターの右側から見る（カメラがキャラクターの右）"

# (キー, タブ名, クラス名)。モジュール名は ui_<キー>
TAB_SPECS = (
    ("setup", "セットアップ", "SetupTab"),
    ("grid", "グリッド", "GridTab"),
    ("pose", "ポーズ", "PoseTab"),
    ("shapes", "シェイプ", "ShapesTab"),
    ("layers", "レイヤー", "LayersTab"),
    ("validate", "検証", "ValidateTab"),
    ("export", "出力", "ExportTab"),
)


# ============================================================================ 共通の小さな道具（各タブからも使う）


def warn(parent: Optional[QtWidgets.QWidget], text: str) -> None:
    """失敗・注意のメッセージ（メッセージ欄）。"""
    QtWidgets.QMessageBox.warning(parent, TITLE, text)


def ask_yes_no(parent: Optional[QtWidgets.QWidget], text: str, title: str = TITLE) -> bool:
    """はい / いいえの確認。Enter・既定のボタンは「いいえ」（破棄・上書き・削除を誤って確定しない。docs/19 M-9）。"""
    yes_no = QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
    return QtWidgets.QMessageBox.question(parent, title, text, yes_no, QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes


def ask_save_discard_cancel(parent: Optional[QtWidgets.QWidget], text: str, title: str = TITLE) -> str:
    """保存 / 破棄 / キャンセルを聞いて、presenters の CONFIRM_* を返す。"""
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Question, title, text, parent=parent)
    save = box.addButton("保存", QtWidgets.QMessageBox.AcceptRole)
    discard = box.addButton("破棄", QtWidgets.QMessageBox.DestructiveRole)
    box.addButton("キャンセル", QtWidgets.QMessageBox.RejectRole)
    box.exec()
    clicked = box.clickedButton()
    if clicked is save:
        return CONFIRM_SAVE
    if clicked is discard:
        return CONFIRM_DISCARD
    return CONFIRM_CANCEL


def placeholder(text: str) -> QtWidgets.QLabel:
    w = QtWidgets.QLabel(text)
    w.setAlignment(QtCore.Qt.AlignCenter)
    w.setWordWrap(True)
    w.setEnabled(False)
    return w


# ============================================================================ 新規ダイアログ


def mesh_choices() -> list[str]:
    """見えているメッシュのうち blendShape があるもの（長い名前）。"""
    out: list[str] = []
    for m in scene_mod.list_visible_meshes():
        try:
            if scene_mod.blend_shapes(m):
                out.append(m)
        except ValueError:
            continue
    return out


def selected_mesh_choice(choices: list[str]) -> Optional[str]:
    """選択中のメッシュ（transform / shape どちらでも）が choices にあればそれ。"""
    for n in cmds.ls(selection=True, long=True) or []:
        if cmds.nodeType(n) == "mesh":
            n = (cmds.listRelatives(n, parent=True, fullPath=True) or [n])[0]
        if n in choices:
            return n
    return None


def profile_names(character: str = "") -> list[str]:
    """選べる命名規則プロファイル: 同梱のもの + プロジェクトの `facial/<キャラクター>/profiles/` のもの。"""
    names = list(profile_mod.builtin_profiles().keys())
    if character:
        d = profile_mod.project_profiles_dir(project.root(), character)
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.is_file() and f.name.endswith(profile_mod.PROFILE_EXTENSION):
                    try:
                        n = profile_mod.load(f).name or f.name[: -len(profile_mod.PROFILE_EXTENSION)]
                    except Exception:  # noqa: BLE001  読めないプロファイルは一覧に出さない
                        continue
                    if n not in names:
                        names.append(n)
    return names


class NewDialog(QtWidgets.QDialog):
    """新規: キャラクター ID・対象メッシュ・命名規則プロファイル（省略可）。"""

    def __init__(self, parent: Optional[QtWidgets.QWidget], default_character: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("新規 FacialController データ")
        form = QtWidgets.QFormLayout(self)
        self.character = QtWidgets.QLineEdit(default_character)
        self.character.setToolTip("英小文字・数字・_ だけ（保存先のフォルダ名とシェイプ名に使います）")
        form.addRow("キャラクター ID", self.character)
        self.mesh = QtWidgets.QComboBox()
        choices = mesh_choices()
        for m in choices:
            self.mesh.addItem(scene_mod.short_name(m), m)
        picked = selected_mesh_choice(choices)
        if picked is not None:
            self.mesh.setCurrentIndex(choices.index(picked))
        self.mesh.setToolTip("顔のメッシュ。blendShape があるメッシュだけが並びます（選択中のものが初期値）")
        form.addRow("対象メッシュ", self.mesh)
        self.profile = QtWidgets.QComboBox()
        self.profile.setToolTip("シェイプ名の決まり（左右の名前・可動域）。あとでセットアップタブで変えられます")
        form.addRow("プロファイル", self.profile)
        self._fill_profiles()
        self.character.textChanged.connect(lambda *_: self._fill_profiles())
        self.error = QtWidgets.QLabel()
        self.error.setStyleSheet(WARN_STYLE)
        self.error.setWordWrap(True)
        form.addRow(self.error)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _fill_profiles(self) -> None:
        keep = self.profile.currentText()
        self.profile.blockSignals(True)
        self.profile.clear()
        self.profile.addItem(NO_PROFILE)
        for n in profile_names(self.character.text().strip()):
            self.profile.addItem(n)
        i = self.profile.findText(keep)
        self.profile.setCurrentIndex(max(i, 0))
        self.profile.blockSignals(False)

    def _on_accept(self) -> None:
        if not CHARACTER_ID.match(self.character.text().strip()):
            self.error.setText("キャラクター ID は英小文字・数字・_ のみです")
            return
        if self.mesh.count() == 0:
            self.error.setText("blendShape のあるメッシュが見つかりません。シーンに顔のメッシュを用意してください")
            return
        self.accept()

    def values(self) -> dict:
        profile = self.profile.currentText()
        return {
            "character": self.character.text().strip(),
            "mesh": self.mesh.currentData(),
            "profile": None if profile == NO_PROFILE else profile,
        }


# ============================================================================ タブの入れ物（遅延読み込み）


class _TabSlot(QtWidgets.QWidget):
    """1 つのタブの入れ物。中身は初めて開いたときに作る。作れなくても入れ物は残る（パネルは必ず開く）。"""

    def __init__(self, key: str, label: str, cls_name: str) -> None:
        super().__init__()
        self.key, self.label, self.cls_name = key, label, cls_name
        self.content: Optional[QtWidgets.QWidget] = None
        self.is_placeholder = False
        self.stale = True
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    def build(self, session) -> None:
        if self.content is not None:
            return
        module_name = f"{__package__}.ui_{self.key}"
        try:
            module = importlib.import_module(module_name)
            content = getattr(module, self.cls_name)(session)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                self._set(placeholder(f"{self.label} は準備中です"), True)
                return
            self._fail(exc)
            return
        except Exception as exc:  # noqa: BLE001  1 つのタブの不具合でパネルを開けなくしない
            self._fail(exc)
            return
        self._set(content, False)

    def _fail(self, exc: Exception) -> None:
        lifecycle.report_error(f"{self.label}タブを作れません", traceback.format_exc())
        self._set(placeholder(f"{self.label} タブを開けませんでした: {exc}"), True)

    def _set(self, widget: QtWidgets.QWidget, is_placeholder: bool) -> None:
        self.content = widget
        self.is_placeholder = is_placeholder
        self._layout.addWidget(widget)
        self.stale = True

    def refresh(self) -> None:
        """中身を更新する（作ってなければ作る）。更新で例外が出ても握りつぶさず、見える形で通知する。"""
        self.stale = False
        if self.content is None or self.is_placeholder:
            return
        try:
            self.content.refresh()
        except RuntimeError:
            pass  # 枠ごと破棄済み
        except Exception:  # noqa: BLE001
            lifecycle.report_error(f"{self.label}タブの更新でエラー", traceback.format_exc())

    def detach(self) -> None:
        fn = getattr(self.content, "detach", None)
        if fn is None:
            return
        try:
            fn()
        except RuntimeError:
            pass
        except Exception:  # noqa: BLE001
            lifecycle.report_error(f"{self.label}タブの後片付けでエラー", traceback.format_exc())


# ============================================================================ ヘッダー


class HeaderBar(QtWidgets.QWidget):
    """データの場所・●未保存・新規 / 開く / 保存 / 別名で保存・編集の入り切り。常に見えている。

    ダイアログは `ask_*` に分けてある（テストが差し替えて答えを返す）。
    """

    def __init__(self, s: session_mod.FacialSession) -> None:
        super().__init__()
        self.session = s
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.label = QtWidgets.QLabel()
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        v.addWidget(self.label)

        row = QtWidgets.QHBoxLayout()
        self.buttons: dict[str, QtWidgets.QPushButton] = {}
        for key, text, tip, fn in (
            ("new", "新規", "新しい FacialController データを作る", self.on_new),
            ("open", "開く", "保存したデータ（.fcpose.json）を開く", self.on_open),
            ("save", "保存", "今のデータを保存する", self.on_save),
            ("save_as", "別名で保存", "名前と場所を決めて保存する", self.on_save_as),
        ):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, f=fn: f())
            row.addWidget(b)
            self.buttons[key] = b
        row.addStretch(1)
        v.addLayout(row)

        edit_row = QtWidgets.QHBoxLayout()
        self.edit_btn = QtWidgets.QPushButton("編集")
        self.edit_btn.setCheckable(True)
        self.edit_btn.setToolTip(
            "オンにすると、シーンが基準姿勢（バインドポーズ・シェイプの重み 0）になり、グリッド / ポーズタブで点のポーズを作れます。"
            "オフにすると元の姿勢に戻ります"
        )
        self.edit_btn.clicked.connect(lambda checked=False: self.on_edit_clicked(checked))
        edit_row.addWidget(self.edit_btn)
        self.edit_state = QtWidgets.QLabel()
        self.edit_state.setWordWrap(True)
        edit_row.addWidget(self.edit_state, 1)
        v.addLayout(edit_row)

    # -------------------------------------------------------------- 表示
    def path_text(self) -> str:
        s = self.session
        if s.presenters is None:
            return "データ: （まだありません）— 新規 または 開く から始めてください"
        if s.path is None:
            where = "（保存先がまだありません）"
        else:
            try:
                where = project.to_project_path(s.path)
            except Exception:  # noqa: BLE001  プロジェクトの外のパス
                where = Path(s.path).as_posix()
            if not Path(s.path).exists():
                where += "（まだファイルがありません）"
        doc = s.doc
        dirty = "  ●未保存" if s.dirty else ("  ●点に保存していない編集中の値があります" if s.has_unsaved_work else "")
        return f"データ: {where}   {doc.asset or ''}{dirty}"

    def refresh(self) -> None:
        s = self.session
        has = s.presenters is not None
        self.label.setText(self.path_text())
        self.label.setToolTip(str(s.path) if s.path else "")
        self.buttons["save"].setEnabled(has)
        self.buttons["save_as"].setEnabled(has)
        self.edit_btn.setEnabled(has)
        editing = bool(has and s.editing)
        self.edit_btn.blockSignals(True)
        self.edit_btn.setChecked(editing)
        self.edit_btn.blockSignals(False)
        if editing:
            self.edit_state.setText("<b>編集中</b> = シーンが基準姿勢になっています（オフで元の姿勢に戻ります）")
            self.edit_state.setStyleSheet(WARN_STYLE)
        else:
            self.edit_state.setText("編集していません（シーンはそのままです）")
            self.edit_state.setStyleSheet(DIM_STYLE)

    # -------------------------------------------------------------- ダイアログ（差し替えられる）
    def ask_discard(self) -> bool:
        """未保存の変更を捨ててよいか。変更が無ければ聞かずに True。"""
        if not self.session.has_unsaved_work:
            return True
        return ask_yes_no(self, "未保存の変更（点に保存していない編集中の値を含む）があります。破棄しますか？")

    def ask_new(self) -> Optional[dict]:
        default = Path(cmds.file(query=True, sceneName=True) or "character").stem.lower()
        default = re.sub(r"[^a-z0-9_]", "_", default)
        dlg = NewDialog(self, default)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        return dlg.values()

    def ask_open_path(self) -> str:
        start = self.session.path.parent if self.session.path else project.root() / "facial"
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "FacialController のデータを開く", str(start), "FacialController (*.fcpose.json *.json)"
        )
        return path

    def ask_save_path(self) -> str:
        start = self.session.path or session_mod.default_path(self.session.doc.asset or "untitled")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "FacialController のデータを別名で保存", str(start), "FacialController (*.fcpose.json)"
        )
        return path

    def ask_overwrite(self, path: Path) -> bool:
        return ask_yes_no(self, f"{path.as_posix()} は既にあります。上書きしますか？\n（いいえ = 別の名前で保存します）")

    # -------------------------------------------------------------- 操作
    def on_new(self) -> None:
        if not self.ask_discard():
            return
        values = self.ask_new()
        if not values:
            return
        try:
            self.session.new(values["character"], mesh=values["mesh"], profile=values["profile"])
        except Exception as exc:  # noqa: BLE001  メッセージはそのまま見せる
            warn(self, str(exc))

    def on_open(self) -> None:
        if not self.ask_discard():
            return
        path = self.ask_open_path()
        if not path:
            return
        try:
            self.session.open(path)
        except Exception as exc:  # noqa: BLE001  検証エラーをそのまま見せる
            warn(self, str(exc))

    def on_save(self) -> None:
        if self.session.presenters is None:
            return
        try:
            self.session.save()
        except FileExistsError:
            path = self.session.path
            if path is not None and self.ask_overwrite(path):
                try:
                    self.session.save(overwrite=True)
                except Exception as exc:  # noqa: BLE001
                    warn(self, str(exc))
            else:
                self.on_save_as()
        except session_mod.FacialSessionError as exc:  # 変換して開いた元のファイルへは上書きできない: 別名保存へ（M-12）
            warn(self, str(exc))
            self.on_save_as()
        except Exception as exc:  # noqa: BLE001
            warn(self, str(exc))

    def on_save_as(self) -> None:
        if self.session.presenters is None:
            return
        path = self.ask_save_path()
        if not path:
            return
        if not path.endswith(".json"):
            path += session_mod.FILE_SUFFIX
        try:
            self.session.save_as(path)
        except Exception as exc:  # noqa: BLE001
            warn(self, str(exc))

    def on_edit_clicked(self, checked: bool) -> None:
        """編集の入り切り。入ると基準姿勢、切ると元の姿勢（session.begin_edit / end_edit）。"""
        s = self.session
        if s.presenters is None:
            self.refresh()
            return
        try:
            if checked:
                s.begin_edit()
            else:
                s.end_edit()
        except Exception as exc:  # noqa: BLE001
            warn(self, str(exc))
        self.refresh()


# ============================================================================ パネル


class FacialPanel(QtWidgets.QWidget):
    """FacialController タブの中身: ヘッダー + 警告 + セットアップ / グリッド / ポーズ / シェイプ / レイヤー / 検証 / 出力。"""

    def __init__(self) -> None:
        super().__init__()
        self.session = session_mod.current()
        self._detached = False
        self._generation = self.session.generation

        layout = QtWidgets.QVBoxLayout(self)
        self.header = HeaderBar(self.session)
        layout.addWidget(self.header)
        self.warning = QtWidgets.QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(WARN_STYLE)
        layout.addWidget(self.warning)
        self.hint = QtWidgets.QLabel("新規 または 開く でデータを作ってください")
        self.hint.setAlignment(QtCore.Qt.AlignCenter)
        self.hint.setStyleSheet(DIM_STYLE)
        layout.addWidget(self.hint)

        self.tabs = QtWidgets.QTabWidget()
        self.slots: dict[str, _TabSlot] = {}
        for key, label, cls_name in TAB_SPECS:
            slot = _TabSlot(key, label, cls_name)
            self.slots[key] = slot
            self.tabs.addTab(slot, label)
        layout.addWidget(self.tabs, 1)
        self.tabs.currentChanged.connect(lambda *_: self._refresh_current_if_stale())

        self.session.listeners.append(self._on_session_changed)
        self.session.state_listeners.append(self._on_state_changed)  # 軽い通知（編集状態・選択・ポーズの値）はヘッダーだけ更新する
        self.refresh()

    # -------------------------------------------------------------- タブ
    def tab(self, key: str) -> Optional[QtWidgets.QWidget]:
        """タブの中身（まだ作っていなければ作る）。準備中・エラーのときは表示用のラベル。"""
        slot = self.slots[key]
        slot.build(self.session)
        return slot.content

    def tab_is_placeholder(self, key: str) -> bool:
        self.slots[key].build(self.session)
        return self.slots[key].is_placeholder

    def select_tab(self, key: str) -> None:
        self.tabs.setCurrentWidget(self.slots[key])
        self._refresh_current_if_stale()

    def _current_slot(self) -> Optional[_TabSlot]:
        w = self.tabs.currentWidget()
        return w if isinstance(w, _TabSlot) else None

    def _refresh_current_if_stale(self) -> None:
        slot = self._current_slot()
        if slot is None:
            return
        first = slot.content is None
        if first:
            slot.build(self.session)
        if (first or slot.stale) and self.session.presenters is not None:
            slot.refresh()

    # -------------------------------------------------------------- 更新
    def _on_session_changed(self) -> None:
        try:
            self.refresh()
        except RuntimeError:
            # 枠ごと破棄済み: 通知の購読を外す
            if self._on_session_changed in self.session.listeners:
                self.session.listeners.remove(self._on_session_changed)

    def _on_state_changed(self) -> None:
        """セッションの軽い通知（`state_listeners`）: 編集状態の出入り・点の選択・ポーズの値の変化。ヘッダーと警告だけを更新する
        （スライダーのドラッグ中にタブを作り直さない）。"""
        try:
            self.header.refresh()
            self.warning.setText(self._warning_text())
            self.warning.setVisible(bool(self.warning.text()))
        except RuntimeError:
            # 枠ごと破棄済み: 通知の購読を外す
            if self._on_state_changed in self.session.state_listeners:
                self.session.state_listeners.remove(self._on_state_changed)

    def refresh(self) -> None:
        s = self.session
        has = s.presenters is not None
        if s.generation != self._generation:
            self._generation = s.generation  # データを作り直した（新規 / 開く / 閉じる）: 全タブの見た目を付け直す
            for slot in self.slots.values():
                slot.stale = True
                fn = getattr(slot.content, "rebind", None)
                if fn is not None and not slot.is_placeholder:
                    try:
                        fn()
                    except Exception:  # noqa: BLE001
                        lifecycle.report_error(f"{slot.label}タブの付け直しでエラー", traceback.format_exc())
        self.header.refresh()
        self.hint.setVisible(not has)
        for i, (key, _label, _cls) in enumerate(TAB_SPECS):
            self.tabs.setTabEnabled(i, has)
            self.slots[key].setEnabled(has)
        current = self._current_slot()
        for slot in self.slots.values():
            if slot is current and has:
                if slot.content is None:
                    slot.build(s)
                slot.refresh()
            else:
                slot.stale = True
        self.warning.setText(self._warning_text())
        self.warning.setVisible(bool(self.warning.text()))

    def _warning_text(self) -> str:
        s = self.session
        if s.presenters is None:
            return ""
        lines: list[str] = []
        if s.converted_from is not None:
            lines.append("別の座標系のファイルから変換して開きました。保存すると元のファイルではなく、新しい場所に Maya の系で保存されます")
        sc = s.scene
        if sc is not None and sc.curves is None:
            lines.append("対象のメッシュがシーンに見つかりません（セットアップタブで選んでください）")
        if s.editing and s.edit_warnings:
            lines.extend(s.edit_warnings)
        return "\n".join(lines)

    # -------------------------------------------------------------- 後片付け
    def detach(self) -> None:
        """通知の購読を止め、タブの後片付けをして、シーンを基準姿勢のまま残さない（何度呼んでもよい）。"""
        if self._detached:
            return
        self._detached = True
        if self._on_session_changed in self.session.listeners:
            self.session.listeners.remove(self._on_session_changed)
        if self._on_state_changed in self.session.state_listeners:
            self.session.state_listeners.remove(self._on_state_changed)
        for slot in self.slots.values():
            slot.detach()
        try:
            self.session.end_edit(quiet=True)
        except Exception:  # noqa: BLE001
            lifecycle.report_error("編集状態を抜けられませんでした", traceback.format_exc(), once=False)


# ============================================================================ ツール


class FacialTool:
    """殻（tdrive.shell）に載せる FacialController ツール（tdrive.tool.Tool）。"""

    id = TOOL_ID
    label = "FacialController"

    def __init__(self) -> None:
        self._panel: Optional[FacialPanel] = None

    def build_widget(self) -> QtWidgets.QWidget:
        self._panel = FacialPanel()
        return self._panel

    def on_scene_opened(self) -> None:
        session_mod.on_scene_opened()

    def on_scene_saved(self) -> None:
        session_mod.on_scene_saved()

    def undo(self) -> bool:
        return session_mod.current().undo()

    def redo(self) -> bool:
        return session_mod.current().redo()

    def refresh(self) -> None:
        s = session_mod.current()
        s.refresh()  # シーンの情報を取り直す（データがあれば変更通知で画面も更新される）
        if self._panel is not None:
            try:
                if s.presenters is None:
                    self._panel.refresh()
            except RuntimeError:
                self._panel = None  # 枠ごと破棄済み

    def undo_message(self) -> str:
        return "FacialController: これ以上元に戻せません"

    def redo_message(self) -> str:
        return "FacialController: やり直せる操作がありません"


def make_tool() -> FacialTool:
    return FacialTool()
