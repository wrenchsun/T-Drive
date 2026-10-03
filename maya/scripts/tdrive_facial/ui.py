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


def make_tool() -> FacialTool:
    return FacialTool()
