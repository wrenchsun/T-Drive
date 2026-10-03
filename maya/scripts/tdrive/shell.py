"""T-Drive ウィンドウ（殻）: ドッキングできる 1 つのウィンドウに、登録されたツールを 1 段目のタブで並べる（docs/15 §2.1）。

- ツール（tdrive.tool.Tool）は `register_tool` で登録する。組み込みの Toon / FacialController は `_BUILTIN_TOOLS`
  から遅延 import する（どちらかの import に失敗しても、殻とほかのツールは開く。そのタブには理由を表示する）
- ツール内のエラーの表示バー（lifecycle.py）と、Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z の「今開いているツール」への振り分けは殻が持つ
- workspaceControl は `TDriveShellWorkspaceControl`。× で閉じても枠は残し（retain）、次に開くと同じ位置に戻る。
  Maya 再起動後は保存されたレイアウトから uiScript（RESTORE_SCRIPT）で中身が作り直される
"""

from __future__ import annotations

import importlib
import traceback

from maya import cmds
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin
from PySide6 import QtCore, QtGui, QtWidgets

from . import __version__, lifecycle
from .tool import Tool

SHELL_NAME = "TDriveShell"
CONTROL_NAME = f"{SHELL_NAME}WorkspaceControl"
# 2 段タブになる前のエディタの枠。古いレイアウトに残っていることがある
OLD_CONTROL_NAME = "TDriveToonEditorWorkspaceControl"
OLD_WINDOW_NAMES = ("TDriveToonEditor",)
# Maya がレイアウトを復元するとき（再起動後・ワークスペース切替時）に呼ぶスクリプト
RESTORE_SCRIPT = "from tdrive import shell; shell.restore()"

# 組み込みツール: (id, タブ名, モジュール)。モジュールは make_tool() を持つ
_BUILTIN_TOOLS = (
    ("toon", "Toon", "tdrive_toon.ui"),
    ("facial", "FacialController", "tdrive_facial.ui"),
)

_tools: list = []
_window: "ShellWindow | None" = None
_control: str | None = None  # 今の中身が入っている枠の名前（古いレイアウトから復元されたときは旧名のことがある）


# ============================================================================ ツールの登録
def register_tool(tool: Tool) -> None:
    """ツールを登録する。同じ id があれば置き換える（順番は保つ）。"""
    for i, old in enumerate(_tools):
        if old.id == tool.id:
            _tools[i] = tool
            return
    _tools.append(tool)


def tools() -> list:
    """登録済みのツール（組み込みを先頭に並べる。まだなら読み込む）。"""
    _ensure_builtin_tools()
    return list(_tools)


def get_tool(tool_id: str):
    return next((t for t in tools() if t.id == tool_id), None)


class _UnavailableTool:
    """読み込めなかった（まだ無い）ツールの代わりのタブ。理由を表示するだけ。"""

    def __init__(self, tool_id: str, label: str, text: str) -> None:
        self.id, self.label, self._text = tool_id, label, text

    def build_widget(self) -> QtWidgets.QWidget:
        w = QtWidgets.QLabel(self._text)
        w.setAlignment(QtCore.Qt.AlignCenter)
        w.setWordWrap(True)
        w.setEnabled(False)
        return w

    def on_scene_opened(self) -> None:
        pass

    def on_scene_saved(self) -> None:
        pass

    def undo(self) -> bool:
        return False

    def redo(self) -> bool:
        return False

    def refresh(self) -> None:
        pass


def _ensure_builtin_tools() -> None:
    """組み込みツールのうち未登録のものを読み込む。読み込めなければ理由を表示するタブにする。"""
    builtin_ids = [spec[0] for spec in _BUILTIN_TOOLS]
    registered = {t.id for t in _tools}
    for tool_id, label, module in _BUILTIN_TOOLS:
        if tool_id in registered:
            continue
        try:
            register_tool(importlib.import_module(module).make_tool())
        except ModuleNotFoundError as exc:
            if exc.name and (module == exc.name or module.startswith(exc.name + ".")):
                text = f"{label} は準備中です（docs/14）"  # ツール自体がまだ無い
            else:
                text = f"{label} を読み込めませんでした: {exc}"
                lifecycle.report_error(f"{label} を読み込めません", traceback.format_exc())
            register_tool(_UnavailableTool(tool_id, label, text))
        except Exception as exc:  # noqa: BLE001  1 つのツールの失敗で殻を開けなくしない
            lifecycle.report_error(f"{label} を読み込めません", traceback.format_exc())
            register_tool(_UnavailableTool(tool_id, label, f"{label} を読み込めませんでした: {exc}"))
    # 組み込みを先頭（定義順）に、外部から登録されたものはその後ろに
    _tools.sort(key=lambda t: builtin_ids.index(t.id) if t.id in builtin_ids else len(builtin_ids))


# ============================================================================ ウィンドウの中身
class ShellWidget(QtWidgets.QWidget):
    """1 段目のタブ（ツールごと）+ ツール内のエラーの表示バー + Undo / Redo の振り分け。Maya のドッキングには依存しない。"""

    def __init__(self, parent: QtWidgets.QWidget | None = None, *args, **kwargs) -> None:
        super().__init__(parent, *args, **kwargs)  # MayaQWidgetDockableMixin が parent= を渡してくる
        self.setObjectName(SHELL_NAME)
        self.setWindowTitle(f"T-Drive {__version__}")

        layout = QtWidgets.QVBoxLayout(self)
        # ツール内のエラー（Qt の操作・描画のコールバック）。スクリプトエディタを見なくても気付けるようにする（lifecycle.py）
        self.error_bar = QtWidgets.QWidget()
        eh = QtWidgets.QHBoxLayout(self.error_bar)
        eh.setContentsMargins(0, 0, 0, 0)
        self.error_label = QtWidgets.QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #ff6060;")
        eh.addWidget(self.error_label, 1)
        close = QtWidgets.QToolButton()
        close.setText("×")
        close.setToolTip("表示を消す（内容はスクリプトエディタに残っています）")
        close.clicked.connect(lambda *_: self.error_bar.setVisible(False))
        eh.addWidget(close)
        self.error_bar.setVisible(False)
        layout.addWidget(self.error_bar)
        self._error_count = 0
        lifecycle.install_error_hook()
        lifecycle.add_error_listener(self._on_tool_error)

        self.tool_tabs = QtWidgets.QTabWidget()
        self.tools: list = []
        self.tool_widgets: dict[str, QtWidgets.QWidget] = {}
        for tool in tools():
            try:
                widget = tool.build_widget()
            except Exception as exc:  # noqa: BLE001  1 つのツールの失敗でほかのタブを巻き込まない
                lifecycle.report_error(f"{tool.label} の画面を作れません", traceback.format_exc())
                widget = _UnavailableTool(tool.id, tool.label, f"{tool.label} を開けませんでした: {exc}").build_widget()
            self.tools.append(tool)
            self.tool_widgets[tool.id] = widget
            self.tool_tabs.addTab(widget, tool.label)
        layout.addWidget(self.tool_tabs, 1)

        # ツール内 Undo（Look の値など）。どのタブにフォーカスがあっても効く。Maya の Undo とは別
        for key, fn in (("Ctrl+Z", self._undo), ("Ctrl+Y", self._redo), ("Ctrl+Shift+Z", self._redo)):
            sc = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            sc.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    # -------------------------------------------------------------- ツール
    def current_tool(self):
        i = self.tool_tabs.currentIndex()
        return self.tools[i] if 0 <= i < len(self.tools) else None

    def current_tool_id(self) -> str | None:
        tool = self.current_tool()
        return tool.id if tool else None

    def select_tool(self, tool_id: str) -> None:
        widget = self.tool_widgets.get(tool_id)
        if widget is not None:
            self.tool_tabs.setCurrentWidget(widget)

    def refresh(self) -> None:
        for tool in self.tools:
            try:
                tool.refresh()
            except Exception:  # noqa: BLE001
                lifecycle.report_error(f"{tool.label} の更新で例外", traceback.format_exc())

    def detach(self) -> None:
        """タイマー・変更通知の購読を止める（閉じる・作り直す前に呼ぶ）。何度呼んでもよい。"""
        for widget in self.tool_widgets.values():
            fn = getattr(widget, "detach", None)
            if fn is not None:
                try:
                    fn()
                except RuntimeError:
                    pass  # 枠ごと破棄済み
        lifecycle.remove_error_listener(self._on_tool_error)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt)
        self.detach()
        super().closeEvent(event)

    # -------------------------------------------------------------- Undo / Redo（今開いているツールへ）
    def _undo(self) -> None:
        tool = self.current_tool()
        if tool is not None and not tool.undo():
            cmds.inViewMessage(amg=_message(tool, "undo_message", "T-Drive: これ以上元に戻せません"), pos="topCenter", fade=True)

    def _redo(self) -> None:
        tool = self.current_tool()
        if tool is not None and not tool.redo():
            cmds.inViewMessage(amg=_message(tool, "redo_message", "T-Drive: やり直せる操作がありません"), pos="topCenter", fade=True)

    # -------------------------------------------------------------- エラー表示
    def _on_tool_error(self, summary: str, _detail: str) -> None:
        try:
            self._error_count += 1
            more = f"（ほか {self._error_count - 1} 件）" if self._error_count > 1 else ""
            self.error_label.setText(f"エラー: {summary}{more} — 詳細はスクリプトエディタ。報告してください")
            self.error_bar.setVisible(True)
        except RuntimeError:
            lifecycle.remove_error_listener(self._on_tool_error)  # 画面が破棄済み


def _message(tool, attr: str, default: str) -> str:
    """ツールが Undo できなかったときの案内。ツールが決めたければ undo_message() / redo_message() を持たせる。"""
    fn = getattr(tool, attr, None)
    return fn() if callable(fn) else default


class ShellWindow(MayaQWidgetDockableMixin, ShellWidget):
    """ドッキングできる T-Drive ウィンドウ。"""


# ============================================================================ 開く・復元・閉じる
def active_control() -> str | None:
    """今の殻が入っている枠の名前（無ければ None）。"""
    for name in (_control, CONTROL_NAME, OLD_CONTROL_NAME):
        if name and cmds.workspaceControl(name, exists=True):
            return name
    return None


def window() -> "ShellWindow | None":
    return _window


def show(tool_id: str | None = None) -> "ShellWindow":
    """T-Drive ウィンドウを開く（tool_id のタブを前面に）。前回ドッキングした位置があればそこに開く。

    × で閉じても枠（workspaceControl）は残す（retain）ので、次に開くと同じ位置に戻る。
    """
    global _window, _control
    control = active_control()
    if control and _window is None:
        # 中身を失った枠（モジュール再読み込み後など）は作り直す。
        # ※ workspaceControl -q -uiScript は設定済みでも None を返すので判定に使えない
        cmds.deleteUI(control)
        control = None
    if control:
        cmds.workspaceControl(control, edit=True, visible=True)
        cmds.workspaceControl(control, edit=True, restore=True)
        _update_dock_label()
        _window.refresh()
    else:
        if _window is not None:
            _window.detach()
        _control = None
        _remove_old_control()
        _window = ShellWindow()
        lifecycle.on_reload(_cleanup)
        _window.show(dockable=True, floating=True, area="right", retain=True, uiScript=RESTORE_SCRIPT)
        _update_dock_label()
    if tool_id:
        _window.select_tool(tool_id)
    return _window


def restore(control: str | None = None) -> None:
    """workspaceControl の uiScript から呼ばれる。保存されたドッキング位置に中身を作り直す。

    古いレイアウトに保存された旧エディタの枠（TDriveToonEditorWorkspaceControl）から呼ばれたときも、その枠に殻を作る。
    """
    global _window, _control
    from maya import OpenMayaUI as omui
    from shiboken6 import getCppPointer, wrapInstance

    parent = control or omui.MQtUtil.getCurrentParent()
    if _window is not None:
        _window.detach()
    ptr = omui.MQtUtil.findControl(parent) if isinstance(parent, str) else parent
    # モジュール再読み込み後などで枠に古いウィンドウが残っていれば片付ける（二重表示を防ぐ）
    holder = wrapInstance(int(ptr), QtWidgets.QWidget)
    olds = holder.findChildren(QtWidgets.QWidget, SHELL_NAME)
    for name in OLD_WINDOW_NAMES:
        olds += holder.findChildren(QtWidgets.QWidget, name)
    for old in olds:
        old.setParent(None)
        old.deleteLater()
    _window = ShellWindow()
    _control = parent if isinstance(parent, str) else None
    # 自分が入っている枠の名前（名前が文字列で渡されなくても、入れ物の親をたどって分かるようにする）
    own = [parent] if isinstance(parent, str) else []
    w = holder
    while w is not None:
        own.append(w.objectName())
        w = w.parentWidget()
    lifecycle.on_reload(_cleanup)
    omui.MQtUtil.addWidgetToMayaLayout(int(getCppPointer(_window)[0]), int(ptr))
    _remove_old_control(own)  # 新しい枠から復元されたなら、旧エディタの枠は要らない（二重表示を防ぐ。自分が入る枠は消さない）
    _update_dock_label()


def select_tool(tool_id: str) -> None:
    if _window is not None:
        _window.select_tool(tool_id)


def _same_control(a: str | None, b: str | None) -> bool:
    """枠の名前が同じか（UI のパス `a|b|名前` の最後の部分・大文字小文字の違いは同じとみなす）。"""
    if not a or not b:
        return False
    return a.rsplit("|", 1)[-1].lower() == b.rsplit("|", 1)[-1].lower()


def _remove_old_control(own=()) -> None:
    """旧エディタの枠（2 段タブになる前）が残っていれば消す。ドッキング位置は引き継がない（新しい枠を作る）。

    **今復元している枠自身（`_control` と、`own` = 自分が入っている枠の名前）は消さない**（古いレイアウトから復元されたとき、
    復元中の枠を消してパネルが消える・落ちるのを防ぐ。docs/19 M-4）。
    """
    if _same_control(_control, OLD_CONTROL_NAME) or any(_same_control(n, OLD_CONTROL_NAME) for n in own):
        return
    if cmds.workspaceControl(OLD_CONTROL_NAME, exists=True):
        cmds.deleteUI(OLD_CONTROL_NAME)


def _update_dock_label() -> None:
    """ドッキングの見出し（workspaceControl の label）を今の版にする。

    見出しは枠を最初に作ったときに付き、リロード・再起動・更新では変わらない（v0.2.0 に上げても 0.1.0 のままだった）。
    """
    control = active_control()
    if control:
        cmds.workspaceControl(control, edit=True, label=f"T-Drive {__version__}")


def _cleanup() -> None:
    """ツールのリロードの前の後片付け（lifecycle.on_reload）。タイマー・変更通知の購読を止める。"""
    if _window is not None:
        _window.detach()


def close() -> None:
    """ウィンドウを閉じる（ドッキング位置の記録も消す）。"""
    global _window, _control
    # 後片付け（出力中のジョブの中止・購読の解除・基準姿勢から元へ戻す）を、Qt の枠を消すより先に行う（M-6）
    if _window is not None:
        _window.detach()
    for name in (_control, CONTROL_NAME, OLD_CONTROL_NAME):
        if name and cmds.workspaceControl(name, exists=True):
            cmds.deleteUI(name)
    _window = None
    _control = None
