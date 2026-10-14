"""テスト用のダミー外部ツール（tdrive.tool.Tool の最小実装）。"""

from __future__ import annotations


class DummyTool:
    id = "tdrive_dummy"
    label = "Dummy"

    def build_widget(self):
        from PySide6 import QtWidgets

        return QtWidgets.QLabel("dummy external tool")

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


def create_tool() -> DummyTool:
    return DummyTool()
