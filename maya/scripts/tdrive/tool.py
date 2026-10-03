"""殻（tdrive.shell）に載せるツールの約束（docs/15 §2.2）。Maya 非依存（型の定義だけ）。

殻は Tool を知っているだけで、Toon・Facial の中身を知らない。3 つ目のツールも同じ形で足せる。
`build_widget()` が返すウィジェットに `detach()` があれば、殻が閉じる・作り直すときに呼ぶ
（タイマー・変更通知の購読を止めるため）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from PySide6 import QtWidgets


class Tool(Protocol):
    id: str  # "toon" | "facial"
    label: str  # 1 段目のタブ名

    def build_widget(self) -> "QtWidgets.QWidget": ...

    def on_scene_opened(self) -> None:  # シーンに記録されたデータを開く
        ...

    def on_scene_saved(self) -> None:  # データも保存する
        ...

    def undo(self) -> bool: ...

    def redo(self) -> bool: ...

    def refresh(self) -> None: ...
