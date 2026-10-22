"""ツール共通のウィジェット（Toon / FacialController / Animation）。

- 「ホイールはフォーカスしているときだけ」: マウスが重なっただけのホイールでスピンボックス・コンボ・スライダーの値が変わらない
  （パネルをスクロール中の誤操作を防ぐ。Phase E-8 の作法を Toon の `ui_look` から切り出した。AN-F-4）。
  フォーカスが無いときのホイールは親（スクロール領域）へ渡す。クリック・Tab でフォーカスが入る（StrongFocus）。
- スピンボックス・コンボ・スライダーは `QtWidgets.QSpinBox()` などを直接作らずに、ここの `SpinBox` / `DoubleSpinBox` / `ComboBox` / `Slider` を使う
  （`tests/test_ui_patterns.py` が直接作っていないか検査する）。
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class WheelOnFocusMixin:
    """ホイールはフォーカスがあるときだけ値を変える。"""

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()  # 親（スクロール領域）へ渡す


class SpinBox(WheelOnFocusMixin, QtWidgets.QSpinBox):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)


class DoubleSpinBox(WheelOnFocusMixin, QtWidgets.QDoubleSpinBox):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)


class ComboBox(WheelOnFocusMixin, QtWidgets.QComboBox):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)


class Slider(WheelOnFocusMixin, QtWidgets.QSlider):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
