"""グリッドタブ（docs/14 §5.3、F1-2）: 格子の図・今のカメラの角度・点のクリック・自動生成 / キー解除 / クリア・ベイク。

画面は「描く + 入力を渡す」だけ。状態は `session.grid`（GridPresenter）が持ち、操作は **セッションのコマンドだけ**を呼ぶ
（docs/15 §4.1。Presenter の変更系を直接呼ぶと、Undo・古い FC_* の削除・未保存の印が抜ける）。

- 格子の図（`GridCanvas`）: 上 = 見下ろす（+Pitch）、下 = あおり、左 = −Yaw、右 = +Yaw、中央 = 正面。
  緑 = キー、水色 = 自動生成、灰 = 空、橙の太い枠 = 選択中、破線の黄の枠 = 未ベイク、実線の桃の枠 = ベイク後に変更あり、赤い点 = 今のカメラ
- カメラの追従: 約 10 回 / 秒の QTimer で `session.view_angles()` を読み、角度が変わったときだけ描き直す。
  **Maya のコールバックは使わない**（タブが見えているときだけ動く。`detach()` で止める）
- 点をクリック → `session.select_point`（その角度へカメラを動かし、ポーズをシーンへ当てる）。未保存の編集があれば確認する
- ボタンは押した操作だけをする。ベイクのボタンは**ベイクだけ**（Maya の Undo 1 回で戻る。docs/15 §4.3）
- 任意のサムネイル（F2-6）: 「サムネイルを表示」（既定オフ）で、ポーズのある点のセルにその角度から見た顔の小さな画像を敷く（ラベルの後ろ）。
  画像は「サムネイルを作り直す」で作る（`thumbnails.capture`。一時カメラ + playblast。ユーザーのカメラ・選択・編集中の値は動かさない）。
  置き場所は一時フォルダ。ポーズが変わった点の古い画像は出さない（鍵が違う）。ビューポートが無い環境では作れない旨を出すだけ
"""

from __future__ import annotations

import traceback
import weakref
from functools import partial
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from tdrive import lifecycle, project

from . import anim_import
from . import scene_grid
from . import thumbnails
from .core.presenters import (
    ACTION_BAKE_POINT,
    ACTION_CAMERA_TO_POINT,
    ACTION_CLEAR,
    ACTION_COPY,
    ACTION_PASTE,
    ACTION_PASTE_MIRRORED,
    ACTION_UNKEY,
    CONFIRM_CANCEL,
    CONFIRM_DISCARD,
    CONFIRM_SAVE,
    FRAME_CHANGED,
    FRAME_UNBAKED,
    SELECT_CANCELLED,
    SELECT_INVALID,
    SELECT_NEEDS_CONFIRM,
    STATE_EMPTY,
    STATE_GENERATED,
    STATE_KEY,
    CameraMarker,
    GridView,
)
from .session import FacialSessionError
from .ui import YAW_MINUS_HELP, YAW_PLUS_HELP  # noqa: F401  再公開（ui_setup が ui_grid から読む）
from .ui_persp import PerspectiveGroup
from .ui_preview import PreviewGroup

POLL_MS = 100  # カメラの角度を読む間隔（約 10 回 / 秒）
ANGLE_EPS = 0.01  # これ未満の角度の違いは「変わっていない」とみなす（度）

# Maya の暗い UI で読める色（Qt の既定の文字色は palette から取る）
FILL = {
    STATE_KEY: QtGui.QColor("#3f9d51"),
    STATE_GENERATED: QtGui.QColor("#2b8fa8"),
    STATE_EMPTY: QtGui.QColor("#46494f"),
}
SELECT_COLOR = QtGui.QColor("#ff9f1c")  # 選択中（橙）
FRAME_UNBAKED_COLOR = QtGui.QColor("#ffe14d")  # 未ベイク（黄の破線）
FRAME_CHANGED_COLOR = QtGui.QColor("#ff7ad9")  # ベイク後に変更あり（桃の実線）
MARKER_COLOR = QtGui.QColor("#ff3b30")  # 今のカメラ（赤）
OK_STYLE = "color: #9aa6b8;"
ERR_STYLE = "color: #ff8a80;"

# Yaw の向きの言い方は ui.py（プレビューのヒントも同じ文言を使うため。ここから再公開）

SCENE_GRID_HELP = (
    "ビューポートに、顔のまわりの球の上に格子の点を並べて出します（UE 版の FacialController と同じ）。点をクリックしてその点を選べます。\n"
    "色: 灰 = 空 / 緑 = キー / 水色 = 自動生成 / 黄 = 未ベイク / 桃 = ベイク後に変更あり / 橙の大きな点 = 選択中 / 赤 = 今のカメラの向き\n"
    "頭を動かすと格子もついてきます。シーンファイルには保存されません（保存・書き出しのあいだは自動で消え、終わると戻ります）。"
    "シェイプの書き出し・サムネイルにも写りません"
)

AXIS_HELP = (
    "横 = Yaw（左の端 = −Yaw、中央 = 正面、右の端 = +Yaw）。" + YAW_PLUS_HELP + "、" + YAW_MINUS_HELP + "\n"
    "縦 = Pitch（上 = +で見下ろす、下 = −であおる、水平 = 0°）"
)


def fmt_angle(v: float) -> str:
    """軸の見出し用の角度（+22.5° / 0° / -45°）。"""
    if abs(v) < 1e-6:
        return "0°"
    t = f"{v:+.1f}"
    return (t[:-2] if t.endswith(".0") else t) + "°"


# ---------------------------------------------------------------------------
# 格子の図
# ---------------------------------------------------------------------------


class GridCanvas(QtWidgets.QWidget):
    """格子の図（自前で描く）。点のクリック・右クリック・ツールチップだけを `GridTab` へ渡す。"""

    cellClicked = QtCore.Signal(int, int)  # (row, col)
    contextRequested = QtCore.Signal(int, int, QtCore.QPoint)  # (row, col, 画面上の位置)

    LEFT = 56  # Pitch の見出し
    TOP = 38  # Yaw の見出し
    RIGHT = 8
    GAP = 3
    LEGEND_H = 22

    def __init__(self, tab: "GridTab") -> None:
        super().__init__()
        self._tab = tab
        self._hover: Optional[tuple[int, int]] = None
        self.setMouseTracking(True)
        self.setMinimumSize(260, 170)
        policy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setToolTip(AXIS_HELP)

    def sizeHint(self) -> QtCore.QSize:
        v = self._tab.current_view()
        cols, rows = (v.cols, v.rows) if v is not None else (5, 3)
        return QtCore.QSize(self.LEFT + self.RIGHT + cols * 64, self.TOP + self.LEGEND_H * 2 + rows * 52)

    # --- 幾何 ---
    def _legend_rows(self) -> int:
        return 2 if self.width() < 520 else 1

    CELL_ASPECT = 0.75  # セルの高さ / 幅の上限（縦に間延びさせない）

    def _cell_size(self, view: GridView) -> tuple[float, float]:
        legend = self.LEGEND_H * self._legend_rows()
        cw = (self.width() - self.LEFT - self.RIGHT) / max(view.cols, 1)
        ch = min((self.height() - self.TOP - legend - 4) / max(view.rows, 1), cw * self.CELL_ASPECT)
        return max(cw, 8.0), max(ch, 8.0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        v = self._tab.current_view()
        rows, cols = (v.rows, v.cols) if v is not None else (3, 5)
        cw = (w - self.LEFT - self.RIGHT) / max(cols, 1)
        legend = self.LEGEND_H * (2 if w < 520 else 1)
        return int(self.TOP + rows * max(cw * self.CELL_ASPECT, 14.0) + legend + 4)

    def cell_rect(self, view: GridView, display_index: int, col: int) -> QtCore.QRectF:
        cw, ch = self._cell_size(view)
        return QtCore.QRectF(self.LEFT + col * cw, self.TOP + display_index * ch, cw, ch)

    def cell_at(self, pos: QtCore.QPoint) -> Optional[tuple[int, int]]:
        """画面上の位置 → (row, col)。格子の外は None。"""
        view = self._tab.current_view()
        if view is None:
            return None
        cw, ch = self._cell_size(view)
        x, y = pos.x() - self.LEFT, pos.y() - self.TOP
        if x < 0 or y < 0:
            return None
        col, di = int(x // cw), int(y // ch)
        if col >= view.cols or di >= view.rows:
            return None
        return view.display_rows[di], col

    def cell_center(self, row: int, col: int) -> QtCore.QPoint:
        """点 (row, col) の中心の位置（テスト・ツールチップ用）。"""
        view = self._tab.current_view()
        assert view is not None
        di = view.display_rows.index(row)
        return self.cell_rect(view, di, col).center().toPoint()

    # --- 入力 ---
    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == QtCore.Qt.LeftButton:
            hit = self.cell_at(e.position().toPoint())
            if hit is not None:
                self.cellClicked.emit(*hit)
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QtGui.QMouseEvent) -> None:
        hit = self.cell_at(e.position().toPoint())
        if hit != self._hover:
            self._hover = hit
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e: QtCore.QEvent) -> None:
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(e)

    def contextMenuEvent(self, e: QtGui.QContextMenuEvent) -> None:
        hit = self.cell_at(e.pos())
        if hit is not None:
            self.contextRequested.emit(hit[0], hit[1], e.globalPos())

    def event(self, e: QtCore.QEvent) -> bool:
        if e.type() == QtCore.QEvent.ToolTip:
            hit = self.cell_at(e.pos())
            text = self._tab.tooltip_for(*hit) if hit is not None else AXIS_HELP
            QtWidgets.QToolTip.showText(e.globalPos(), text, self)
            return True
        return super().event(e)

    # --- 描画 ---
    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        view = self._tab.current_view()
        text_color = self.palette().color(QtGui.QPalette.WindowText)
        if view is None:
            p.setPen(text_color)
            p.drawText(self.rect(), QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap, self._tab.empty_message())
            return
        small = QtGui.QFont(self.font())
        small.setPointSizeF(max(self.font().pointSizeF() - 1.0, 7.0))
        p.setFont(small)
        fm = QtGui.QFontMetrics(small)
        gap = self.GAP

        # 軸の見出し
        p.setPen(text_color)
        corner = QtCore.QRectF(0, 0, self.LEFT - 4, self.TOP - 2)
        p.drawText(corner, QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom, "Pitch ↑\nYaw →")
        pts = {(pv.row, pv.col): pv for pv in view.points}
        top_row = view.display_rows[0]
        for col in range(view.cols):
            pv = pts[(top_row, col)]
            r = self.cell_rect(view, 0, col)
            head = QtCore.QRectF(r.left(), 0, r.width(), self.TOP - 2)
            front = abs(pv.yaw) < 1e-6
            f = QtGui.QFont(small)
            f.setBold(front)
            p.setFont(f)
            p.drawText(head, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignBottom, fmt_angle(pv.yaw) + ("\n正面" if front else ""))
        for di, row in enumerate(view.display_rows):
            pv = pts[(row, 0)]
            r = self.cell_rect(view, di, 0)
            head = QtCore.QRectF(0, r.top(), self.LEFT - 6, r.height())
            level = abs(pv.pitch) < 1e-6
            f = QtGui.QFont(small)
            f.setBold(level)
            p.setFont(f)
            p.drawText(head, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, fmt_angle(pv.pitch) + ("\n水平" if level else ""))
        p.setFont(small)

        # セル
        for di, row in enumerate(view.display_rows):
            for col in range(view.cols):
                pv = pts[(row, col)]
                r = self.cell_rect(view, di, col).adjusted(gap / 2, gap / 2, -gap / 2, -gap / 2)
                fill = QtGui.QColor(FILL[pv.state])
                if self._hover == (row, col):
                    fill = fill.lighter(120)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(r, 4, 4)
                pix = self._tab.thumb_for(row, col)
                if pix is not None and not pix.isNull():  # 顔の画像をセルに敷く（ラベル・枠はその上）。状態の色は縁に残す
                    inner = r.adjusted(2, 2, -2, -2)
                    path = QtGui.QPainterPath()
                    path.addRoundedRect(inner, 3, 3)
                    p.save()
                    p.setClipPath(path)
                    scaled = pix.scaled(inner.size().toSize(), QtCore.Qt.KeepAspectRatioByExpanding, QtCore.Qt.SmoothTransformation)
                    p.drawPixmap(
                        QtCore.QPointF(inner.center().x() - scaled.width() / 2.0, inner.center().y() - scaled.height() / 2.0), scaled
                    )
                    p.fillRect(inner, QtGui.QColor(0, 0, 0, 60))  # ラベルが読めるよう少し暗くする
                    p.restore()
                if pv.frame in (FRAME_UNBAKED, FRAME_CHANGED):
                    pen = QtGui.QPen(FRAME_UNBAKED_COLOR if pv.frame == FRAME_UNBAKED else FRAME_CHANGED_COLOR, 2)
                    if pv.frame == FRAME_UNBAKED:
                        pen.setStyle(QtCore.Qt.DashLine)
                    p.setPen(pen)
                    p.setBrush(QtCore.Qt.NoBrush)
                    p.drawRoundedRect(r.adjusted(4, 4, -4, -4), 2, 2)
                if r.width() >= 40 and r.height() >= 26:
                    label = f"R{row} C{col}"
                    if pix is not None and not pix.isNull():  # 画像の上でも読めるよう影を付ける
                        p.setPen(QtGui.QColor(0, 0, 0, 200))
                        lab = r.adjusted(0, 0, 0, -7)  # 下の枠（未ベイクの破線など）に重ならない高さ
                        p.drawText(lab.translated(1, 1), QtCore.Qt.AlignBottom | QtCore.Qt.AlignHCenter, label)
                        p.setPen(QtGui.QColor(255, 255, 255, 235))
                        p.drawText(lab, QtCore.Qt.AlignBottom | QtCore.Qt.AlignHCenter, label)
                    else:
                        p.setPen(QtGui.QColor(255, 255, 255, 190))
                        p.drawText(r, QtCore.Qt.AlignCenter, label)
                if pv.selected:
                    p.setPen(QtGui.QPen(SELECT_COLOR, 3))
                    p.setBrush(QtCore.Qt.NoBrush)
                    p.drawRoundedRect(r.adjusted(-0.5, -0.5, 0.5, 0.5), 4, 4)

        # カメラの赤い点
        m = self._tab.current_marker()
        if m is not None:
            self._draw_marker(p, view, m)

        # 凡例
        self._draw_legend(p, fm, text_color)
        p.end()

    def marker_position(self, view: GridView, m: CameraMarker) -> QtCore.QPointF:
        """赤い点の画面上の位置（範囲外は端へ寄せた位置）。列・行の連続値の整数が点の中心。"""
        cw, ch = self._cell_size(view)
        x = self.LEFT + (m.col_pos_clamped + 0.5) * cw
        y = self.TOP + ((view.rows - 1 - m.row_pos_clamped) + 0.5) * ch
        return QtCore.QPointF(x, y)

    def _draw_marker(self, p: QtGui.QPainter, view: GridView, m: CameraMarker) -> None:
        c = self.marker_position(view, m)
        if m.clamped:  # 範囲の外: 端に寄せて、塗らない破線の輪にする
            pen = QtGui.QPen(MARKER_COLOR, 2, QtCore.Qt.DashLine)
            p.setPen(pen)
            fill = QtGui.QColor(MARKER_COLOR)
            fill.setAlpha(60)
            p.setBrush(fill)
            p.drawEllipse(c, 9, 9)
            p.setPen(QtGui.QColor("#ffffff"))
            p.drawText(QtCore.QRectF(c.x() - 40, c.y() + 10, 80, 14), QtCore.Qt.AlignCenter, "範囲外")
        else:
            p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 2))
            p.setBrush(MARKER_COLOR if not m.faded_out else QtGui.QColor("#b07070"))
            p.drawEllipse(c, 7, 7)

    def _draw_legend(self, p: QtGui.QPainter, fm: QtGui.QFontMetrics, text_color: QtGui.QColor) -> None:
        items = [
            ("fill", FILL[STATE_KEY], "キー"),
            ("fill", FILL[STATE_GENERATED], "自動生成"),
            ("fill", FILL[STATE_EMPTY], "空"),
            ("dash", FRAME_UNBAKED_COLOR, "未ベイク"),
            ("solid", FRAME_CHANGED_COLOR, "変更あり"),
            ("ring", SELECT_COLOR, "選択中"),
            ("dot", MARKER_COLOR, "カメラ"),
        ]
        view = self._tab.current_view()
        grid_bottom = self.TOP + (view.rows * self._cell_size(view)[1] if view is not None else 0)
        x, y = float(self.LEFT), float(grid_bottom + 4)
        right = self.width() - self.RIGHT
        for kind, color, label in items:
            w = 18 + fm.horizontalAdvance(label) + 12
            if x + w > right and x > self.LEFT:
                x, y = float(self.LEFT), y + self.LEGEND_H
            r = QtCore.QRectF(x, y + 4, 14, 12)
            if kind == "fill":
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(color)
                p.drawRoundedRect(r, 3, 3)
            elif kind in ("dash", "solid"):
                pen = QtGui.QPen(color, 2)
                if kind == "dash":
                    pen.setStyle(QtCore.Qt.DashLine)
                p.setPen(pen)
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(r, 2, 2)
            elif kind == "ring":
                p.setPen(QtGui.QPen(color, 3))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(r, 3, 3)
            else:
                p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1.5))
                p.setBrush(color)
                p.drawEllipse(r.center(), 5, 5)
            p.setPen(text_color)
            p.drawText(QtCore.QRectF(x + 18, y, w, self.LEGEND_H), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, label)
            x += w


# ---------------------------------------------------------------------------
# 他のデータからコピーのダイアログ
# ---------------------------------------------------------------------------


class CopyFromDialog(QtWidgets.QDialog):
    """「他のデータからコピー…」: コピー元のファイル（.fcpose.json）と、コピーの範囲（全レイヤー / 作業セットだけ / キーだけ）を聞く。"""

    def __init__(self, parent: QtWidgets.QWidget, start_dir: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("他のデータからコピー")
        self.setMinimumWidth(400)
        self.start_dir = start_dir
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(QtWidgets.QLabel("別の FacialController のデータ（.fcpose.json）から、ポーズをこのデータへコピーします。格子の大きさが違っても、角度で補間し直します"))
        row = QtWidgets.QHBoxLayout()
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setPlaceholderText("コピー元のデータ（.fcpose.json）")
        self.btn_choose = QtWidgets.QPushButton("選ぶ…")
        self.btn_choose.clicked.connect(lambda *_: self.on_choose())
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.btn_choose)
        v.addLayout(row)
        self.cb_all = QtWidgets.QCheckBox("全レイヤー")
        self.cb_all.setToolTip("名前が同じレイヤー同士でコピーします（このデータに無いレイヤーは作ります）。切ると、今のレイヤーだけ")
        self.cb_ws = QtWidgets.QCheckBox("作業セットだけ")
        self.cb_ws.setToolTip("このデータの作業セット（セットアップタブで選ぶ）のシェイプ・ボーンだけをコピーします。作業セットが空なら全部")
        self.cb_keys = QtWidgets.QCheckBox("キーだけ")
        self.cb_keys.setChecked(True)
        self.cb_keys.setToolTip("コピー元のキー（緑の点）だけを使います。切ると、コピー元の自動生成の点も使います")
        for cb in (self.cb_all, self.cb_ws, self.cb_keys):
            v.addWidget(cb)
        note = QtWidgets.QLabel("コピーした点は自動生成の点になります（このデータにあったキーも上書きします）。「元に戻す」で戻せます")
        note.setWordWrap(True)
        note.setStyleSheet(OK_STYLE)
        v.addWidget(note)
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("コピーする")
        self.buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("キャンセル")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        v.addWidget(self.buttons)
        self.path_edit.textChanged.connect(lambda *_: self._sync())
        self._sync()

    def choose_path(self, start_dir: str) -> str:
        """コピー元のファイルを聞く（キャンセルは ""）。"""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "コピー元のデータを選ぶ", start_dir, "FacialController のデータ (*.fcpose.json *.json)")
        return path

    def on_choose(self) -> None:
        path = self.choose_path(self.start_dir)
        if path:
            self.path_edit.setText(path)

    def _sync(self) -> None:
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(bool(self.path_edit.text().strip()))

    def set_path(self, path: str) -> None:
        self.path_edit.setText(path)

    def flags(self) -> list[str]:
        out = []
        if self.cb_all.isChecked():
            out.append("all_layers")
        if self.cb_ws.isChecked():
            out.append("working_set_only")
        if self.cb_keys.isChecked():
            out.append("keys_only")
        return out

    def options(self) -> dict:
        return {"path": self.path_edit.text().strip(), "flags": self.flags()}


# ---------------------------------------------------------------------------
# タブ
# ---------------------------------------------------------------------------

_live_tabs: "weakref.WeakSet[GridTab]" = weakref.WeakSet()


def _detach_all() -> None:
    """ツールのリロードの前: 動いているタイマーを全部止める（古いモジュールのコードが動き続けないように）。"""
    for tab in list(_live_tabs):
        try:
            tab.detach()
        except RuntimeError:  # Qt 側が先に破棄されている
            pass


lifecycle.on_reload(_detach_all)


class GridTab(QtWidgets.QWidget):
    """グリッドタブ。`refresh()` で全部描き直し、`detach()` でタイマーと通知の購読をやめる。"""

    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self._view: Optional[GridView] = None
        self._marker: Optional[CameraMarker] = None
        self._angles: Optional[tuple[float, float]] = None
        self._angle_error = ""
        self._generation = -1
        self._unsub: list[Callable[[], None]] = []
        self._tracking = False
        self._tab_hidden = False  # hideEvent で立つ（隠れているあいだはシーンの格子の追従だけ）
        self._detached = False
        self._thumb_map: dict[tuple[int, int], QtGui.QPixmap] = {}  # (row, col) → サムネイル（アクティブレイヤー。表示中だけ読む）
        self._pix_cache: dict[str, QtGui.QPixmap] = {}  # ファイルのパス（鍵つきの名前なので中身は変わらない）→ QPixmap
        self.thumb_render = None  # サムネイルの描き方の差し替え（None = playblast。テスト用）
        self.thumb_report: Optional[thumbnails.ThumbReport] = None

        # 縦に長いのでスクロールできるようにする（プレビューの箱が下に付く）
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        body = QtWidgets.QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self.scroll = scroll
        v = QtWidgets.QVBoxLayout(body)
        self.title = QtWidgets.QLabel()
        v.addWidget(self.title)

        row = QtWidgets.QHBoxLayout()
        self.move_camera = QtWidgets.QCheckBox("カメラも動かす")
        self.move_camera.setChecked(True)
        self.move_camera.setToolTip("点をクリックしたとき、その角度へカメラを動かします（顔の基準ボーンを見る位置）。切ると、ポーズだけをシーンへ当てます")
        row.addWidget(self.move_camera)
        self.show_thumbs = QtWidgets.QCheckBox("サムネイルを表示")
        self.show_thumbs.setToolTip(
            "ポーズのある点のセルに、その角度から見た顔の小さな画像を敷きます（任意）。画像は「サムネイルを作り直す」で作ります。ポーズを変えた点の古い画像は出ません"
        )
        self.show_thumbs.toggled.connect(lambda *_: self.on_thumbs_toggled())
        row.addWidget(self.show_thumbs)
        self.btn_thumbs = QtWidgets.QPushButton("サムネイルを作り直す")
        self.btn_thumbs.setToolTip(
            "今のレイヤーの、ポーズのある点それぞれの顔の画像を作り直します。一時のカメラで撮るので、今のカメラ・選択・編集中の値は動きません（数秒かかります）"
        )
        self.btn_thumbs.clicked.connect(lambda *_: self.on_rebuild_thumbs())
        row.addWidget(self.btn_thumbs)
        row.addStretch(1)
        self.camera_label = QtWidgets.QLabel()
        self.camera_label.setToolTip("今のビューのカメラが、顔から見て Yaw / Pitch どの角度にいるか。カメラを回すと赤い点が追従します。" + YAW_PLUS_HELP)
        v.addLayout(row)
        row = QtWidgets.QHBoxLayout()  # カメラの角度は 2 行目（スクロールバーがあっても右端で切れない）
        row.addStretch(1)
        row.addWidget(self.camera_label)
        v.addLayout(row)

        # ---- シーンに格子を出す（任意。ビューポートの球面の上に点を並べる。文書・シーンには保存しない）
        row = QtWidgets.QHBoxLayout()
        self.show_scene_grid = QtWidgets.QCheckBox("シーンに格子を表示")
        self.show_scene_grid.setToolTip(SCENE_GRID_HELP)
        self.show_scene_grid.toggled.connect(lambda *_: self.on_scene_grid_toggled())
        row.addWidget(self.show_scene_grid)
        row.addWidget(QtWidgets.QLabel("大きさ"))
        self.scene_grid_scale = QtWidgets.QDoubleSpinBox()
        self.scene_grid_scale.setRange(scene_grid.SCALE_MIN, scene_grid.SCALE_MAX)
        self.scene_grid_scale.setSingleStep(0.25)
        self.scene_grid_scale.setDecimals(2)
        self.scene_grid_scale.setSuffix(" 倍")
        self.scene_grid_scale.setKeyboardTracking(False)
        self.scene_grid_scale.setValue(scene_grid.DEFAULT_SCALE)
        self.scene_grid_scale.setToolTip("格子の球の半径を、顔の大きさの何倍にするか。顔が埋まって見づらいときは大きく、遠すぎるときは小さくします")
        self.scene_grid_scale.valueChanged.connect(lambda *_: self.on_scene_grid_scale())
        row.addWidget(self.scene_grid_scale)
        self.scene_grid_pick = QtWidgets.QCheckBox("クリックで点を選ぶ")
        self.scene_grid_pick.setChecked(True)
        self.scene_grid_pick.setToolTip(
            "ビューポートの格子の点をクリックすると、グリッドの図でそのセルをクリックしたのと同じにその点を選びます（「カメラも動かす」も同じに効きます）。"
            "切ると点はクリックで選べなくなります（見るだけ）"
        )
        self.scene_grid_pick.toggled.connect(lambda *_: self.on_scene_grid_pick())
        row.addWidget(self.scene_grid_pick)
        row.addStretch(1)
        v.addLayout(row)

        self.canvas = GridCanvas(self)
        self.canvas.cellClicked.connect(self.on_cell_clicked)
        self.canvas.contextRequested.connect(self.on_context)
        v.addWidget(self.canvas)

        self.summary_label = QtWidgets.QLabel()
        v.addWidget(self.summary_label)

        # ---- 点の操作
        box = QtWidgets.QGroupBox("点の操作")
        g = QtWidgets.QVBoxLayout(box)
        row = QtWidgets.QHBoxLayout()
        self.btn_generate = QtWidgets.QPushButton("自動生成")
        self.btn_generate.setToolTip("キーから残りの点を埋めます。キーは変えません（自動生成の点は作り直されます）")
        self.btn_generate.clicked.connect(self.on_generate)
        self.all_layers = QtWidgets.QCheckBox("全レイヤー")
        self.all_layers.setToolTip("オフ = 今のレイヤーだけ")
        self.btn_unkey = QtWidgets.QPushButton("キー解除")
        self.btn_unkey.setToolTip("選んでいる点のキーを外します（ポーズは残り、自動生成の点になります）")
        self.btn_unkey.clicked.connect(self.on_unkey)
        self.btn_clear = QtWidgets.QPushButton("クリア")
        self.btn_clear.setToolTip("選んでいる点を空にします（焼いた FC_* もシーンから消えます）")
        self.btn_clear.clicked.connect(self.on_clear)
        self.btn_clear_layer = QtWidgets.QPushButton("レイヤーをクリア")
        self.btn_clear_layer.setToolTip("今のレイヤーの点を全部消します")
        self.btn_clear_layer.clicked.connect(self.on_clear_layer)
        for w in (self.btn_generate, self.all_layers, self.btn_unkey, self.btn_clear):
            row.addWidget(w)
        row.addStretch(1)
        g.addLayout(row)
        row = QtWidgets.QHBoxLayout()  # 幅 400〜480 px で横にはみ出さないよう、2 行目へ
        row.addWidget(self.btn_clear_layer)
        self.btn_copy_from = QtWidgets.QPushButton("他のデータからコピー…")
        self.btn_copy_from.setToolTip("別の FacialController のデータ（.fcpose.json）から、ポーズをこのデータへコピーします（全レイヤー・作業セットだけ・キーだけを選べます）")
        self.btn_copy_from.clicked.connect(lambda *_: self.on_copy_from())
        row.addWidget(self.btn_copy_from)
        row.addStretch(1)
        g.addLayout(row)
        v.addWidget(box)

        # ---- ベイク
        box = QtWidgets.QGroupBox("ベイク（点のポーズを FC_* のシェイプにする）")
        g = QtWidgets.QVBoxLayout(box)
        row = QtWidgets.QHBoxLayout()
        self.btn_bake_all = QtWidgets.QPushButton("ベイク（全部）")
        self.btn_bake_all.setToolTip("全レイヤー・全部の点と、パース補正のキーを焼きます。Maya の元に戻す（Ctrl+Z）1 回で戻せます")
        self.btn_bake_all.clicked.connect(self.on_bake_all)
        self.btn_bake_point = QtWidgets.QPushButton("ベイク（この点）")
        self.btn_bake_point.setToolTip("選んでいる点（パース補正のキーを選んでいるときはそのキー）だけ焼きます")
        self.btn_bake_point.clicked.connect(self.on_bake_point)
        self.btn_bake_layer = QtWidgets.QPushButton("ベイク（このレイヤー）")
        self.btn_bake_layer.setToolTip("今のレイヤーの点だけ焼きます")
        self.btn_bake_layer.clicked.connect(self.on_bake_layer)
        self.btn_bake_stale = QtWidgets.QPushButton("ベイク（変更のある点）")
        self.btn_bake_stale.setToolTip(
            "未ベイク・ベイク後に変更・シェイプが消えた点（とパース補正のキー）だけ焼きます（速い）。Neutral の点を焼くときは、感情レイヤーの同じ位置の点も一緒に焼き直します"
        )
        self.btn_bake_stale.clicked.connect(self.on_bake_stale)
        for w in (self.btn_bake_stale, self.btn_bake_all):
            row.addWidget(w)
        row.addStretch(1)
        g.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        for w in (self.btn_bake_point, self.btn_bake_layer):
            row.addWidget(w)
        row.addStretch(1)
        g.addLayout(row)
        self.report = QtWidgets.QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(96)
        self.report.setPlaceholderText("ベイクの結果がここに出ます")
        g.addWidget(self.report)
        v.addWidget(box)

        self.persp = PerspectiveGroup(session, move_camera=lambda: self.move_camera.isChecked())
        v.addWidget(self.persp)

        self.preview = PreviewGroup(session)
        v.addWidget(self.preview)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(OK_STYLE)
        v.addWidget(self.status)
        v.addStretch(1)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self._tick)
        self._flush_timer = QtCore.QTimer(self)  # Presenter の通知をまとめて 1 回で描き直す
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(0)
        self._flush_timer.timeout.connect(self._flush_now)
        self._pending = False

        session.listeners.append(self._on_session_changed)
        session.state_listeners.append(self.preview.on_state)  # 編集状態・プレビューの変化（軽い通知）
        _live_tabs.add(self)
        self.refresh()

    # ------------------------------------------------------------------ 状態
    def has_doc(self) -> bool:
        return self.session.presenters is not None

    def empty_message(self) -> str:
        return "FacialController のデータが開かれていません\n（セットアップタブで新規作成するか、開いてください）"

    def current_view(self) -> Optional[GridView]:
        if self._view is None and self.has_doc():
            try:
                self._view = self.session.grid.view()
            except Exception:  # noqa: BLE001  壊れた文書でも画面は落とさない
                lifecycle.report_error("グリッドの表示を作れませんでした", traceback.format_exc())
                return None
        return self._view if self.has_doc() else None

    def current_marker(self) -> Optional[CameraMarker]:
        if self._angles is None or not self.has_doc():
            return None
        if self._marker is None:
            try:
                self._marker = self.session.grid.locate(*self._angles)
            except Exception:  # noqa: BLE001
                return None
        return self._marker

    def tooltip_for(self, row: int, col: int) -> str:
        v = self.current_view()
        if v is None:
            return ""
        pv = v.points[row * v.cols + col]
        extra = {FRAME_UNBAKED: "\n未ベイク", FRAME_CHANGED: "\nベイク後に変更あり"}.get(pv.frame, "")
        state = {STATE_KEY: "キー", STATE_GENERATED: "自動生成", STATE_EMPTY: "空"}[pv.state]
        return f"{pv.tooltip}\n{state}{extra}"

    def set_status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(ERR_STYLE if error else OK_STYLE)

    # ------------------------------------------------------------------ 通知
    def _on_session_changed(self) -> None:
        if self._detached:
            return
        try:
            self.refresh()
        except RuntimeError:  # Qt のオブジェクトが先に破棄された
            self.detach()

    def _on_ctx_event(self, _event: str) -> None:
        """Presenter（選択・ドキュメント・ベイクの状態など）の変化。途中の状態を描かないよう、まとめて後で描き直す。"""
        self._pending = True
        self._view = None
        self._marker = None
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_now(self) -> None:
        if self._detached:
            return
        self._pending = False
        self._sync_controls()
        self._reload_thumbs()
        self.canvas.update()
        self.session.scene_grid.refresh()  # 選択・点の状態・レイヤーの切り替えをシーンの格子へ

    def flush(self) -> None:
        """まとめ待ちの描き直しがあれば今すぐ行う（テスト・スモーク用）。"""
        if self._pending:
            self._flush_timer.stop()
            self._flush_now()

    def _attach(self) -> None:
        """Presenter は新規作成・開くたびに作り直される。作り直されたら購読を付け直す。"""
        if self._generation == self.session.generation and (self._unsub or not self.has_doc()):
            return
        for fn in self._unsub:
            fn()
        self._unsub = []
        self._generation = self.session.generation
        if self.has_doc():
            self._unsub.append(self.session.ctx.subscribe(self._on_ctx_event))

    # ------------------------------------------------------------------ 表示
    def refresh(self) -> None:
        """全部描き直す。データが無くても例外を出さない。"""
        if self._detached:
            return
        self._attach()
        self._view = None
        self._marker = None
        if not self.has_doc():
            self._angles = None
        self._sync_controls()
        self._reload_thumbs()
        self._poll_camera(force=True)
        self.session.scene_grid.on_pick = self.on_cell_clicked
        self.session.scene_grid.refresh()
        self.canvas.updateGeometry()
        self.canvas.update()
        self.persp.refresh()
        self.preview.refresh()

    def _sync_controls(self) -> None:
        doc = self.has_doc()
        self.canvas.setEnabled(doc)
        for w in (self.move_camera, self.all_layers, self.show_thumbs, self.btn_thumbs, self.show_scene_grid, self.scene_grid_scale, self.scene_grid_pick):
            w.setEnabled(doc)
        sg = self.session.scene_grid
        for w, val in ((self.show_scene_grid, sg.enabled), (self.scene_grid_pick, sg.pickable)):
            w.blockSignals(True)
            w.setChecked(val)
            w.blockSignals(False)
        self.scene_grid_scale.blockSignals(True)
        self.scene_grid_scale.setValue(sg.scale)
        self.scene_grid_scale.blockSignals(False)
        if not doc:
            self.title.setText("データが開かれていません")
            self.summary_label.setText("")
            for b in (
                self.btn_generate, self.btn_unkey, self.btn_clear, self.btn_clear_layer, self.btn_copy_from,
                self.btn_bake_all, self.btn_bake_point, self.btn_bake_layer, self.btn_bake_stale,
            ):
                b.setEnabled(False)
            return
        view = self.current_view()
        if view is None:
            return
        s = self.session
        ta = s.grid.toolbar_actions()
        pa = s.grid.actions()
        d = s.doc
        self.title.setText(f"レイヤー: <b>{view.layer}</b>（{view.cols} × {view.rows}）")
        self.summary_label.setText(view.summary.text)
        self.btn_generate.setEnabled(ta["generate"])
        self.btn_unkey.setEnabled(ta[ACTION_UNKEY])
        self.btn_clear.setEnabled(ta[ACTION_CLEAR])
        self.btn_clear_layer.setEnabled(ta["clear_layer"])
        self.btn_copy_from.setEnabled(True)
        can_bake = bool(d.asset) and d.target is not None and bool(d.target.mesh)
        self.btn_bake_all.setEnabled(can_bake)
        self.btn_bake_layer.setEnabled(can_bake)
        self.btn_bake_stale.setEnabled(can_bake)
        self.btn_bake_point.setEnabled(can_bake and (pa[ACTION_BAKE_POINT] or s.ctx.selected_key() is not None))

    # ------------------------------------------------------------------ サムネイル（任意の表示）
    def thumb_for(self, row: int, col: int) -> Optional[QtGui.QPixmap]:
        """セル (row, col) に敷くサムネイル。表示がオフ・画像が無い・古い（ポーズが変わった）なら None。"""
        if not self.show_thumbs.isChecked():
            return None
        return self._thumb_map.get((row, col))

    def load_thumbnails(self) -> dict[tuple[int, int], QtGui.QPixmap]:
        """アクティブレイヤーの、今のポーズに合うサムネイルを読む（鍵が違う古いファイルは読まない）。差し替えられる（テスト用）。"""
        out: dict[tuple[int, int], QtGui.QPixmap] = {}
        if not self.has_doc():
            return out
        s = self.session
        li = min(max(s.ctx.active_layer, 0), len(s.doc.layers) - 1)
        for (r, c), pt in s.doc.layers[li].points.items():
            if pt.pose.is_empty():
                continue
            path = thumbnails.current_thumb(s, li, r, c)
            if path is None:
                continue
            key = str(path)
            pix = self._pix_cache.get(key)
            if pix is None:
                pix = QtGui.QPixmap(key)
                if pix.isNull():
                    continue
                if len(self._pix_cache) > 400:
                    self._pix_cache.clear()
                self._pix_cache[key] = pix
            out[(r, c)] = pix
        return out

    def _reload_thumbs(self) -> None:
        if not self.show_thumbs.isChecked() or not self.has_doc():
            self._thumb_map = {}
            return
        try:
            self._thumb_map = self.load_thumbnails()
        except Exception:  # noqa: BLE001  サムネイルの不具合で格子の表示を止めない
            self._thumb_map = {}
            lifecycle.report_error("サムネイルを読めませんでした", traceback.format_exc())

    def on_thumbs_toggled(self) -> None:
        self._reload_thumbs()
        if self.show_thumbs.isChecked() and self.has_doc() and not self._thumb_map:
            self.set_status("サムネイルがまだありません（または古くなっています）。「サムネイルを作り直す」で作ります")
        self.canvas.update()

    def on_rebuild_thumbs(self) -> None:
        if not self.has_doc():
            return
        self.set_status("サムネイルを作っています…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            rep = thumbnails.capture(self.session, render=self.thumb_render)
        except Exception as exc:  # noqa: BLE001  capture は例外を出さない作りだが、念のため
            rep = thumbnails.ThumbReport(ok=False, message=f"サムネイルを作れませんでした: {exc}")
            lifecycle.report_error("グリッドタブ: サムネイルの作成", traceback.format_exc(), once=False)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.thumb_report = rep
        if rep.made and not self.show_thumbs.isChecked():
            self.show_thumbs.setChecked(True)  # 作ったら見えるようにする（toggled で読み込まれる）
        self._reload_thumbs()
        self.canvas.update()
        self.set_status(rep.message, error=not rep.ok)

    # ------------------------------------------------------------------ カメラの追従
    def start_tracking(self) -> None:
        """カメラの追従を始める（タブが見えているときだけ。データが開かれていなければ動かない）。"""
        self._tracking = True
        if self.has_doc() and not self._detached:
            self.timer.start()

    def stop_tracking(self) -> None:
        self._tracking = False
        if self._detached or not self.session.scene_grid.enabled:
            self.timer.stop()  # シーンに格子を出しているあいだは、タブが隠れても頭に追従させるため止めない（_tick が見える間だけカメラを読む）

    def showEvent(self, e: QtGui.QShowEvent) -> None:
        super().showEvent(e)
        self._tab_hidden = False
        self.start_tracking()

    def hideEvent(self, e: QtGui.QHideEvent) -> None:
        self._tab_hidden = True
        self.stop_tracking()
        super().hideEvent(e)

    def _tick(self) -> None:
        if self._detached or not self.has_doc():
            self.timer.stop()
            return
        if self._tab_hidden:  # タブは隠れている: シーンの格子の追従だけ（止めてよければ止める）
            if not self.session.scene_grid.enabled:
                self.timer.stop()
                return
            self.session.scene_grid.follow()
            return
        self._poll_camera()
        self.session.scene_grid.follow(self._angles)
        self.preview.tick()

    def _poll_camera(self, force: bool = False) -> None:
        """カメラの角度を読み、変わっていれば赤い点とラベルだけ更新する。"""
        if not self.has_doc():
            self._angles = None
            self.camera_label.setText("カメラ: —")
            return
        if self._tracking and not self.timer.isActive() and not self._detached:
            self.timer.start()  # 最初はデータが無くて止まっていた
        try:
            yaw, pitch = self.session.view_angles()
        except FacialSessionError as exc:
            self._angle_error = str(exc)
        except Exception as exc:  # noqa: BLE001  カメラが無い・ノードが消えた等。追従を止めずに表示だけ
            self._angle_error = str(exc)
        else:
            self._angle_error = ""
            old = self._angles
            if force or old is None or abs(old[0] - yaw) > ANGLE_EPS or abs(old[1] - pitch) > ANGLE_EPS:
                self._angles = (yaw, pitch)
                self._marker = None
                m = self.current_marker()
                out = "（範囲外）" if m is not None and m.clamped else ""
                self.camera_label.setText(f"カメラ: Yaw {yaw:.1f}° / Pitch {pitch:.1f}°{out}")
                self.canvas.update()
            return
        if self._angles is not None or force:
            self._angles = None
            self._marker = None
            self.canvas.update()
        self.camera_label.setText("カメラ: 角度を取得できません")
        self.camera_label.setToolTip(self._angle_error)

    # ------------------------------------------------------------------ 確認ダイアログ（テストで差し替えられる）
    def ask_unsaved_choice(self, message: str) -> str:
        """未保存のポーズ編集があるまま別の点へ移るとき。CONFIRM_SAVE / CONFIRM_DISCARD / CONFIRM_CANCEL を返す。"""
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("T-Drive FacialController")
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText("今の点に、まだ保存していないポーズの編集があります。")
        box.setInformativeText("保存してから移りますか？ 破棄すると編集した内容は戻せません。")
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

    def ask_confirm(self, title: str, text: str) -> bool:
        """はい / いいえの確認。"""
        r = QtWidgets.QMessageBox.question(
            self, title, text, QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No
        )
        return r == QtWidgets.QMessageBox.Yes

    # ------------------------------------------------------------------ 共通
    def _run(self, label: str, fn: Callable[[], object]):
        """セッションのコマンドを呼ぶ。失敗は画面の下の行に出し、例外は外へ出さない。"""
        try:
            return fn()
        except FacialSessionError as exc:
            self.set_status(str(exc), error=True)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"{label}でエラーが出ました: {exc}", error=True)
            lifecycle.report_error(f"グリッドタブ: {label}", traceback.format_exc(), once=False)
        return None

    def _show_result(self, res) -> None:
        if res is None:
            return
        self.set_status(res.message or ("完了しました" if res.ok else "できませんでした"), error=not res.ok)

    # ------------------------------------------------------------------ シーンの格子
    def on_scene_grid_toggled(self) -> None:
        on = self.show_scene_grid.isChecked()
        try:
            self.session.scene_grid.set_enabled(on)
        except Exception as exc:  # noqa: BLE001  出せない（データ・基準ボーンが無い等）
            self.show_scene_grid.blockSignals(True)
            self.show_scene_grid.setChecked(False)
            self.show_scene_grid.blockSignals(False)
            self.set_status(f"シーンに格子を出せませんでした: {exc}", error=True)
            return
        self.session.scene_grid.on_pick = self.on_cell_clicked
        if on and not self.timer.isActive() and not self._detached:
            self.timer.start()  # 頭に追従させる
        self.set_status("シーンに格子を出しました（保存はされません）" if on else "シーンの格子を消しました")

    def on_scene_grid_scale(self) -> None:
        self.session.scene_grid.set_scale(self.scene_grid_scale.value())

    def on_scene_grid_pick(self) -> None:
        self.session.scene_grid.set_pickable(self.scene_grid_pick.isChecked())

    # ------------------------------------------------------------------ 点のクリック
    def on_cell_clicked(self, row: int, col: int) -> None:
        if not self.has_doc():
            return
        s = self.session
        was_editing = s.editing
        move = self.move_camera.isChecked()
        r = self._run("点の選択", lambda: s.select_point(row, col, move_camera=move))
        if r is None:
            return
        if r.status == SELECT_NEEDS_CONFIRM:
            choice = self.ask_unsaved_choice(r.message)
            r = self._run("点の選択", lambda: s.select_point(row, col, choice=choice, move_camera=move))
            if r is None:
                return
            if r.status == SELECT_CANCELLED:
                self.set_status("切り替えを取りやめました（今の点の編集はそのままです）")
                return
        if r.status == SELECT_INVALID:
            self.set_status(r.message, error=True)
            return
        yaw, pitch = s.grid.angles_of(row, col)
        msg = f"点 R{row}, C{col}（Yaw {yaw:.1f}° / Pitch {pitch:.1f}°）を選びました"
        if r.saved:
            msg = "保存して移りました。" + msg
        if not was_editing:
            msg += "。編集を始めました（シーンを基準姿勢にしています。ポーズタブで編集できます）"
        self.set_status(msg)
        self._poll_camera()  # カメラを動かしたら赤い点をすぐ追従させる

    # ------------------------------------------------------------------ ボタン
    def _count_generated(self, all_layers: bool) -> int:
        d = self.session.doc
        layers = d.layers if all_layers else [self.session.ctx.layer]
        n = 0
        for layer in layers:
            n += sum(1 for (r, c), p in layer.points.items() if not p.is_key and 0 <= r < d.grid.rows and 0 <= c < d.grid.cols)
        return n

    def on_generate(self) -> None:
        if not self.has_doc():
            return
        all_layers = self.all_layers.isChecked()
        n = self._count_generated(all_layers)
        if n and not self.ask_confirm(
            "自動生成", f"自動生成の点 {n} 個を作り直します（キーは変わりません）。\nよろしいですか？"
        ):
            self.set_status("自動生成を取りやめました")
            return
        self._show_result(self._run("自動生成", lambda: self.session.generate(all_layers)))

    def on_unkey(self) -> None:
        if self.has_doc():
            self._show_result(self._run("キー解除", lambda: self.session.unkey()))

    def on_clear(self) -> None:
        if self.has_doc():
            self._show_result(self._run("クリア", lambda: self.session.clear_point()))

    def on_clear_layer(self) -> None:
        if not self.has_doc():
            return
        name = self.session.ctx.layer.name
        n = len(self.session.ctx.layer.points)
        if not self.ask_confirm(
            "レイヤーをクリア",
            f"レイヤー「{name}」の {n} 点を全部消します（焼いた FC_* もシーンから消えます）。\nキーも消えます。よろしいですか？",
        ):
            self.set_status("取りやめました")
            return
        self._show_result(self._run("レイヤーのクリア", lambda: self.session.clear_layer()))

    # ------------------------------------------------------------------ 他のデータからコピー
    def copy_start_dir(self) -> str:
        return str(project.root())

    def make_copy_dialog(self) -> CopyFromDialog:
        return CopyFromDialog(self, self.copy_start_dir())

    def run_dialog(self, dialog: QtWidgets.QDialog) -> bool:
        """ダイアログを出して、OK なら True（テストで差し替える）。"""
        return dialog.exec() == QtWidgets.QDialog.Accepted

    def on_copy_from(self) -> None:
        if not self.has_doc():
            return
        dlg = self.make_copy_dialog()
        if not self.run_dialog(dlg):
            return
        o = dlg.options()
        res = self._run("他のデータからコピー", lambda: anim_import.copy_from_file(self.session, o["path"], o["flags"]))
        if res is None:
            return
        self.set_status(res.message, error=not res.ok)
        self.refresh()

    # ------------------------------------------------------------------ ベイク（ベイクだけをする）
    def on_bake_all(self) -> None:
        self._bake("全部", lambda s: s.bake_all())

    def on_bake_stale(self, *_checked) -> None:
        self._bake("変更のある点", lambda s: s.bake_stale())

    def on_bake_point(self) -> None:
        sel = self.session.ctx.selection if self.has_doc() else None
        key = self.session.ctx.selected_key() if self.has_doc() else None
        if sel is None and key is not None:  # 編集の対象がパース補正のキー
            self._bake(f"パース補正のキー {key + 1}", lambda s: s.bake_perspective_key(key))
            return
        if sel is None:
            self.set_status("点（またはパース補正のキー）が選ばれていません", error=True)
            return
        self._bake("この点", lambda s: s.bake_point(sel[0], sel[1]))

    def on_bake_layer(self) -> None:
        if self.has_doc():
            idx = self.session.ctx.active_layer
            self._bake("このレイヤー", lambda s: s.bake_layer(idx))

    def _bake(self, what: str, call: Callable[[object], object]) -> None:
        """ベイクだけを呼ぶ（後で選び直したり、編集状態へ戻したりしない。Maya の Undo 1 回で戻る）。"""
        if not self.has_doc():
            return
        s = self.session
        note = ""
        if s.editing and s.pose.dirty:
            note = "編集中の（まだ保存していない）ポーズは焼かれません。\n"
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            report = self._run(f"ベイク（{what}）", lambda: call(s))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if report is None:
            self.report.setPlainText(note + "ベイクできませんでした。\n" + self.status.text())
            return
        self.report.setPlainText(note + self.report_text(report))
        self.set_status(f"ベイク（{what}）が終わりました。元に戻すときは Maya の Ctrl+Z を 1 回押します")

    @staticmethod
    def report_text(report) -> str:
        """BakeReport を、デザイナー向けの文章にする。"""
        lines = [report.summary()]
        for n in report.notes:
            lines.append(n)
        if report.extreme:
            lines.append(f"誇張用のシェイプ（重み 1 を超えたポーズの分 _Ex）: {len(report.extreme)} 個")
        if getattr(report, "perspective", None):
            lines.append(f"パース補正のシェイプ（FC_…_Persp_K）: {len(report.perspective)} 個")
        if report.empty:
            lines.append(f"差分が残らなかった点（空のシェイプ）: {len(report.empty)} 個")
        for ml in getattr(report, "mesh_lines", lambda: [])():  # メッシュが 2 つ以上のとき、メッシュごとの数
            lines.append("メッシュ別  " + ml)
        if report.missing_curves:
            lines.append("シーンに無くて飛ばしたシェイプ: " + "、".join(report.missing_curves))
        if report.missing_bones:
            lines.append("シーンに無くて飛ばしたボーン: " + "、".join(report.missing_bones))
        for w in report.warnings:
            lines.append("注意: " + w)
        return "\n".join(lines)

    # ------------------------------------------------------------------ 右クリック
    def build_point_menu(self, row: int, col: int) -> QtWidgets.QMenu:
        """点の右クリックメニュー（項目の有効 / 無効は GridPresenter.actions）。項目の objectName = 操作名。"""
        menu = QtWidgets.QMenu(self)
        acts = self.session.grid.actions(row, col)
        items = (
            (ACTION_UNKEY, "キー解除"),
            (ACTION_CLEAR, "クリア"),
            (ACTION_BAKE_POINT, "この点だけベイク"),
            (ACTION_COPY, "ポーズをコピー"),
            (ACTION_PASTE, "貼り付け"),
            (ACTION_PASTE_MIRRORED, "左右反転して貼り付け"),
            (ACTION_CAMERA_TO_POINT, "カメラをこの点へ"),
        )
        for name, label in items:
            a = menu.addAction(label)
            a.setObjectName(name)
            a.setEnabled(bool(acts.get(name)))
            a.triggered.connect(partial(self._run_point_action, name, row, col))
        return menu

    def on_context(self, row: int, col: int, global_pos: QtCore.QPoint) -> None:
        if not self.has_doc():
            return
        self.build_point_menu(row, col).exec(global_pos)

    def _run_point_action(self, name: str, row: int, col: int, *_checked) -> None:
        s = self.session
        if not self.has_doc():
            return
        if name == ACTION_UNKEY:
            self._show_result(self._run("キー解除", lambda: s.unkey(row, col)))
        elif name == ACTION_CLEAR:
            self._show_result(self._run("クリア", lambda: s.clear_point(row, col)))
        elif name == ACTION_BAKE_POINT:
            self._bake(f"R{row} C{col}", lambda ss: ss.bake_point(row, col))
        elif name == ACTION_COPY:
            self._show_result(self._run("コピー", lambda: s.copy_pose(row, col)))
        elif name == ACTION_PASTE:
            self._show_result(self._run("貼り付け", lambda: s.paste_pose(row, col)))
        elif name == ACTION_PASTE_MIRRORED:
            self._show_result(self._run("左右反転して貼り付け", lambda: s.paste_mirrored(row, col)))
        elif name == ACTION_CAMERA_TO_POINT:
            r = self._run("カメラの移動", lambda: s.camera_to_point(row, col))
            if r is not None:
                self.set_status(f"カメラを点 R{row}, C{col}（Yaw {r[0]:.1f}° / Pitch {r[1]:.1f}°）へ動かしました")
                self._poll_camera()

    # ------------------------------------------------------------------ 後片付け
    def detach(self) -> None:
        """タイマーを止め、通知の購読をやめる（パネルを閉じる・リロードの前）。二重に呼んでも安全。"""
        self._detached = True
        self.stop_tracking()
        self._flush_timer.stop()
        sg = self.session.scene_grid
        if sg.on_pick == self.on_cell_clicked:
            sg.on_pick = None
        sg.remove()  # パネルを閉じる・リロード: シーンの格子は消す（「出す」の設定は残り、開き直すと出る）
        for fn in self._unsub:
            fn()
        self._unsub = []
        if self._on_session_changed in self.session.listeners:
            self.session.listeners.remove(self._on_session_changed)
        self.persp.detach()
        if self.preview.on_state in self.session.state_listeners:
            self.session.state_listeners.remove(self.preview.on_state)
        self.preview.detach()
        _live_tabs.discard(self)
