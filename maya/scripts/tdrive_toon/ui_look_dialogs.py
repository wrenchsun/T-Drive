"""ルックタブから開くダイアログ（E-5 プリセットの保存、E-6 影色をベース色から作る、E-10 テクスチャの拡大表示）。

計算・検証は Qt 非依存の look_layout.py / presets.py に置き、ここは画面だけ。
"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from . import params, presets, project
from .look_layout import (
    GROUP_LABELS, SHADE2_KEY, SHADE_DEFAULTS, SHADE_KEY, SHADE_RANGES, average_rgb, build_groups, first_image_from_uri_list, is_image_path, shade_pair,
)


class PresetSaveDialog(QtWidgets.QDialog):
    """名前と、含めるグループ（既定は全部）を選ぶ。"""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("プリセットを保存")
        v = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText("ファイル名（例: 影と線だけ）")
        self.label = QtWidgets.QLineEdit()
        self.label.setPlaceholderText("一覧に出す名前（省略すると上と同じ）")
        form.addRow("名前:", self.name)
        form.addRow("表示名:", self.label)
        v.addLayout(form)
        v.addWidget(QtWidgets.QLabel("含めるグループ（チェックしたグループの値だけ保存・適用されます）:"))
        self.checks: dict[str, QtWidgets.QCheckBox] = {}
        grid = QtWidgets.QGridLayout()
        for i, group in enumerate(build_groups()):
            cb = QtWidgets.QCheckBox(GROUP_LABELS.get(group, group))
            cb.setChecked(True)
            self.checks[group] = cb
            grid.addWidget(cb, i // 2, i % 2)
        v.addLayout(grid)
        row = QtWidgets.QHBoxLayout()
        all_b = QtWidgets.QPushButton("すべて")
        none_b = QtWidgets.QPushButton("なし")
        all_b.clicked.connect(lambda *_: self._set_all(True))
        none_b.clicked.connect(lambda *_: self._set_all(False))
        row.addWidget(all_b)
        row.addWidget(none_b)
        row.addStretch(1)
        v.addLayout(row)
        self.message = QtWidgets.QLabel()
        self.message.setStyleSheet("color: #ff9090;")
        v.addWidget(self.message)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def _set_all(self, on: bool) -> None:
        for cb in self.checks.values():
            cb.setChecked(on)

    def groups(self) -> list[str]:
        return [g for g, cb in self.checks.items() if cb.isChecked()]

    def _on_accept(self) -> None:
        try:
            presets.safe_name(self.name.text())
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        if not self.groups():
            self.message.setText("含めるグループを 1 つ以上選んでください")
            return
        self.accept()


def ask_overwrite(parent: QtWidgets.QWidget, name: str) -> bool:
    """同名のプリセットを上書きするか。既定は「いいえ」。"""
    ans = QtWidgets.QMessageBox.question(
        parent, "T-Drive Toon", f"プリセット「{name}」は既にあります。上書きしますか？",
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No,
    )
    return ans == QtWidgets.QMessageBox.Yes


# ---------------------------------------------------------------- E-6 影色をベース色から作る
def map_average_color(path: str) -> list[float] | None:
    """テクスチャの平均色（sRGB の 0〜1）。32px 程度に縮めて平均する。読めなければ None。"""
    img = QtGui.QImage(project.from_project_path(path))
    if img.isNull():
        return None
    img = img.scaled(32, 32, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
    img = img.convertToFormat(QtGui.QImage.Format_RGBA8888)
    pixels = []
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            pixels.append((c.redF(), c.greenF(), c.blueF(), c.alphaF()))
    return average_rgb(pixels)


class ShadeFromBaseDialog(QtWidgets.QDialog):
    """色相のずらし / 彩度 / 明度のスライダーで、影色と 2 影の色をベース色から作る。操作中はライブ反映。
    開いたときに checkpoint、OK で確定（1 Undo）、キャンセルで元の値に戻す。"""

    LABELS = ("色相のずらし", "彩度（倍）", "明度（倍）")

    def __init__(self, tab, base_rgb: list[float], map_rgb: list[float] | None) -> None:
        super().__init__(tab)
        self.tab = tab
        self.base_rgb, self.map_rgb = list(base_rgb), map_rgb
        self.setWindowTitle("影色をベース色から作る")
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(QtWidgets.QLabel("元にする色:"))
        self.radio_base = QtWidgets.QRadioButton("ベース色（乗算）")
        self.radio_map = QtWidgets.QRadioButton("ベースマップの平均色")
        self.radio_base.setChecked(True)
        self.radio_map.setEnabled(map_rgb is not None)
        if map_rgb is None:
            self.radio_map.setToolTip("ベースマップが無い（または読めない）ときは選べません")
        for r in (self.radio_base, self.radio_map):
            r.toggled.connect(lambda *_: self._apply(live=True))
            v.addWidget(r)
        self.chip = QtWidgets.QLabel()
        self.chip.setFixedHeight(22)
        v.addWidget(self.chip)
        form = QtWidgets.QFormLayout()
        self.sliders: list[QtWidgets.QSlider] = []
        self.value_labels: list[QtWidgets.QLabel] = []
        for text, default, (lo, hi) in zip(self.LABELS, SHADE_DEFAULTS, SHADE_RANGES):
            sl = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            sl.setRange(0, 1000)
            sl.setValue(round((default - lo) / (hi - lo) * 1000))
            lab = QtWidgets.QLabel()
            lab.setMinimumWidth(44)
            sl.valueChanged.connect(lambda *_: self._apply(live=True))
            row = QtWidgets.QHBoxLayout()
            row.addWidget(sl, 1)
            row.addWidget(lab)
            form.addRow(text + ":", row)
            self.sliders.append(sl)
            self.value_labels.append(lab)
        v.addLayout(form)
        reset = QtWidgets.QPushButton("既定値に戻す")
        reset.clicked.connect(lambda *_: self._reset_sliders())
        v.addWidget(reset)
        v.addWidget(QtWidgets.QLabel("2 影の色は、影色にもう一度同じ量をかけた色です。"))
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowModality(QtCore.Qt.WindowModal)
        self.accepted.connect(lambda *_: self._apply(live=False))
        self.rejected.connect(lambda *_: self.tab.session.cancel_edit())
        self.tab.begin()  # Undo の区切りは開いたときに 1 回
        self._apply(live=True)

    def values(self) -> tuple[float, float, float]:
        return tuple(lo + (hi - lo) * sl.value() / 1000 for sl, (lo, hi) in zip(self.sliders, SHADE_RANGES))

    def source(self) -> list[float]:
        return self.map_rgb if self.radio_map.isChecked() and self.map_rgb is not None else self.base_rgb

    def _reset_sliders(self) -> None:
        for sl, d, (lo, hi) in zip(self.sliders, SHADE_DEFAULTS, SHADE_RANGES):
            sl.setValue(round((d - lo) / (hi - lo) * 1000))

    def _apply(self, live: bool) -> None:
        h, s_mul, v_mul = self.values()
        for lab, val in zip(self.value_labels, (h, s_mul, v_mul)):
            lab.setText(f"{val:+.2f}" if lab is self.value_labels[0] else f"{val:.2f}")
        shade, shade2 = shade_pair(self.source(), h, s_mul, v_mul)
        r, g, b = (int(min(max(x, 0.0), 1.0) * 255) for x in shade[:3])
        fg = "#000" if (r * 0.3 + g * 0.6 + b * 0.1) > 128 else "#fff"
        self.chip.setStyleSheet(f"background-color: rgb({r},{g},{b}); color: {fg}; border: 1px solid #888;")
        self.chip.setText(f"影色 ({shade[0]:.2f}, {shade[1]:.2f}, {shade[2]:.2f})")
        self.chip.setAlignment(QtCore.Qt.AlignCenter)
        self.tab.set(SHADE_KEY, shade, live=live)
        self.tab.set(SHADE2_KEY, shade2, live=live)


# ---------------------------------------------------------------- E-10 テクスチャ欄
THUMB_SIZE = 48
ZOOM_MAX = 512
_THUMBS: dict[str, tuple[float, QtGui.QPixmap]] = {}  # 絶対パス → (更新時刻, サムネイル)


def thumbnail(path: str) -> QtGui.QPixmap | None:
    """プロジェクト基準のパスのサムネイル。パスごとにキャッシュし、ファイルが更新されたら読み直す。読めなければ None。"""
    import os

    full = project.from_project_path(path)
    try:
        mtime = os.path.getmtime(full)
    except OSError:
        _THUMBS.pop(full, None)
        return None
    hit = _THUMBS.get(full)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    img = QtGui.QImage(full)
    if img.isNull():
        _THUMBS.pop(full, None)
        return None
    pix = QtGui.QPixmap.fromImage(img.scaled(THUMB_SIZE, THUMB_SIZE, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
    _THUMBS[full] = (mtime, pix)
    return pix


def dropped_texture_path(mime: QtCore.QMimeData) -> str | None:
    """ドロップされたものからテクスチャのパス（絶対）。Explorer のファイル（text/uri-list）か、Maya の file ノード名（文字）。"""
    if mime.hasUrls():
        path = first_image_from_uri_list("\n".join(u.toString() for u in mime.urls()))
        if path:
            return path
    if mime.hasText():
        text = mime.text().strip()
        path = first_image_from_uri_list(text)
        if path:
            return path
        node = text.splitlines()[0].strip() if text else ""
        if node:
            try:
                from maya import cmds

                if cmds.objExists(node) and cmds.nodeType(node) == "file":
                    tex = cmds.getAttr(f"{node}.fileTextureName")
                    if tex and is_image_path(tex):
                        return tex
            except Exception:  # noqa: BLE001  Maya が無い・ノード名でない
                pass
            if is_image_path(node) and Path(node).is_file():
                return node
    return None


class TextureZoomDialog(QtWidgets.QDialog):
    """テクスチャを大きく表示する（原寸。大きければ 512px までに縮める）。"""

    def __init__(self, parent: QtWidgets.QWidget, path: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(path)
        v = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel()
        img = QtGui.QImage(project.from_project_path(path))
        if img.isNull():
            label.setText("読み込めません")
        else:
            if max(img.width(), img.height()) > ZOOM_MAX:
                img = img.scaled(ZOOM_MAX, ZOOM_MAX, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            label.setPixmap(QtGui.QPixmap.fromImage(img))
        v.addWidget(label)
        v.addWidget(QtWidgets.QLabel(f"{path}（{img.width()}×{img.height()} 表示）" if not img.isNull() else path))


class TextureThumb(QtWidgets.QLabel):
    """48px のサムネイル。クリックで拡大。ファイルが無い / 読めないときは灰色の枡。ドロップを受ける。"""

    clicked = QtCore.Signal()
    dropped = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setAcceptDrops(True)
        self.path: str | None = None
        self.set_path(None)

    def set_path(self, path: str | None, mixed: bool = False) -> None:
        self.path = path
        pix = thumbnail(path) if path and not mixed else None
        if pix is not None:
            self.setPixmap(pix)
            self.setStyleSheet("border: 1px solid #666;")
            self.setCursor(QtCore.Qt.PointingHandCursor)
            self.setToolTip("クリックで大きく表示 / ここにファイルをドロップで差し替え")
        else:
            self.setPixmap(QtGui.QPixmap())
            self.setText("—" if mixed else "")
            self.setStyleSheet("background-color: #555; border: 1px solid #666; color: #ccc;")
            self.setCursor(QtCore.Qt.ArrowCursor)
            self.setToolTip("ファイルをドロップで差し替え" if not path else f"読めません: {path}")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton and self.pixmap() is not None and not self.pixmap().isNull():
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if dropped_texture_path(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        path = dropped_texture_path(event.mimeData())
        if path:
            event.acceptProposedAction()
            self.dropped.emit(path)
        else:
            event.ignore()


class TexturePathEdit(QtWidgets.QLineEdit):
    """パス欄。Explorer のファイル / Maya の file ノード名のドロップを受ける（普通の文字のドロップは今までどおり）。"""

    dropped = QtCore.Signal(str)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if dropped_texture_path(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if dropped_texture_path(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        path = dropped_texture_path(event.mimeData())
        if path:
            event.acceptProposedAction()
            self.dropped.emit(path)
        else:
            super().dropEvent(event)
