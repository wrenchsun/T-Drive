"""FacialController タブ（殻 tdrive.shell に載せるツール）。F0-1 では準備中の表示だけ。実装は F1 以降（docs/tasks.md）。"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

TOOL_ID = "facial"


class FacialTool:
    """殻（tdrive.shell）に載せる FacialController ツール（tdrive.tool.Tool）。"""

    id = TOOL_ID
    label = "FacialController"

    def build_widget(self) -> QtWidgets.QWidget:
        w = QtWidgets.QLabel("FacialController は準備中です（docs/14）")
        w.setAlignment(QtCore.Qt.AlignCenter)
        w.setEnabled(False)
        return w

    # 以下はセッション層（tdrive_facial.session）へ委ねる。import は呼ぶときに行う（Qt だけの環境でもこのモジュールを読めるように）
    def on_scene_opened(self) -> None:
        from . import session

        session.on_scene_opened()

    def on_scene_saved(self) -> None:
        from . import session

        session.on_scene_saved()

    def undo(self) -> bool:
        from . import session

        return session.current().undo()

    def redo(self) -> bool:
        from . import session

        return session.current().redo()

    def refresh(self) -> None:
        from . import session

        session.current().refresh()


def make_tool() -> FacialTool:
    return FacialTool()
