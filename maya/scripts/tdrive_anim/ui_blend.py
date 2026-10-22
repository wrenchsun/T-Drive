"""ブレンドタブ（docs/20、チケット AN-4 / AN-5）。いまは枠だけ。"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from . import session


class BlendTab(QtWidgets.QWidget):
    def __init__(self, s: session.AnimSession) -> None:
        super().__init__()
        self.session = s
        v = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel("ブレンドタブは準備中です（AN-4 / AN-5）")
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setEnabled(False)
        v.addWidget(label)

    def refresh(self) -> None:
        pass

    def detach(self) -> None:
        pass
