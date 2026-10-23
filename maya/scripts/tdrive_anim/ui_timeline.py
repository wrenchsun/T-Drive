"""パラメータのタイムライン（docs/20 §4.4.1、チケット AN-F-29）: 状態図のパラメータの記録を、横長の時間軸にマーカーで見せて編集する。

- 横軸は合成の「長さ」。現在時刻の線（赤）はタイムスライダーに追従する。ActionState は上段の大きな色付きマーカー + 「Jump(2)」、Speed は下段の小さなマーカー + 値。
  評価してできた状態の区間を下の帯で見せる（Idle / Run / Jump の色）
- 操作: クリックで選択・ドラッグで時刻を移動（フレームにスナップ）・Delete キーか右クリックで削除・ダブルクリックでそのフレームに追加
- ここは描画と入力だけ（データは持たない）。変更は信号で `BlendTab` に伝え、`core/state_machine.py` の関数で時系列を直す
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

STATE_COLORS = {"Idle": "#4a6fa5", "Run": "#4f9d69", "Jump": "#d08a2e"}
ACTION_COLORS = {0: "#7a7a7a", 1: "#7a7a7a", 2: "#d08a2e", 3: "#c8504a"}
LEFT, RIGHT, TOP, LANE_H, BAND_H = 8, 8, 6, 26, 14


class ParamTimeline(QtWidgets.QWidget):
    selectionChanged = QtCore.Signal(str, float)  # (パラメータ名, 時刻 秒)。選択なしは ("", 0)
    markerMoved = QtCore.Signal(str, float, float)  # (パラメータ名, 元の時刻, 新しい時刻)
    deleteRequested = QtCore.Signal(str, float)
    addRequested = QtCore.Signal(int, str)  # (フレーム, "ActionState" | "Speed")
    scrubRequested = QtCore.Signal(int)  # マーカーの上以外をクリック / ドラッグ: Maya の現在時刻をそのフレームへ（AN-F-32）

    def __init__(self) -> None:
        super().__init__()
        self.markers: list[tuple[float, str, float]] = []  # (時刻 秒, パラメータ, 値)
        self.bands: list[tuple[float, float, str]] = []  # (開始, 終了, 状態)
        self.total_sec = 8.0
        self.fps = 30
        self.current_frame = 0.0
        self.selected: tuple[str, float] | None = None
        self._drag: tuple[str, float] | None = None  # ドラッグ中のマーカー（パラメータ, 元の時刻）
        self._drag_t = 0.0
        self._scrubbing = False
        self._last_scrub = 0.0
        self.setMinimumHeight(TOP + LANE_H * 2 + BAND_H + 22)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setToolTip("記録したパラメータ（上段: ActionState / 下段: Speed）。クリックで選択、ドラッグで移動（フレームにスナップ）、"
                        "Delete か右クリックで削除、ダブルクリックでそのフレームに追加。赤い線が現在のフレーム")

    # ---------------------------------------------------------- データ
    def set_data(self, markers: list[tuple[float, str, float]], bands: list[tuple[float, float, str]], total_sec: float, fps: int) -> None:
        self.markers, self.bands, self.total_sec, self.fps = list(markers), list(bands), max(0.1, float(total_sec)), int(fps)
        if self.selected is not None and not any(abs(t - self.selected[1]) < 1e-6 and p == self.selected[0] for t, p, _ in self.markers):
            self.selected = None
        self.update()

    def set_current_frame(self, frame: float) -> None:
        self.current_frame = float(frame)
        self.update()

    def select(self, param: str, t: float) -> None:
        self.selected = (param, t) if param else None
        self.update()

    # ---------------------------------------------------------- 座標
    def _plot_w(self) -> float:
        return max(10.0, self.width() - LEFT - RIGHT)

    def t_to_x(self, t: float) -> float:
        return LEFT + self._plot_w() * min(max(t / self.total_sec, 0.0), 1.0)

    def x_to_frame(self, x: float) -> int:
        t = (x - LEFT) / self._plot_w() * self.total_sec
        return int(round(min(max(t, 0.0), self.total_sec) * self.fps))  # フレームにスナップ

    def _lane_rect(self, param: str) -> QtCore.QRectF:
        row = 0 if param == "ActionState" else 1
        return QtCore.QRectF(LEFT, TOP + row * LANE_H, self._plot_w(), LANE_H - 2)

    def _marker_rect(self, t: float, param: str) -> QtCore.QRectF:
        lane = self._lane_rect(param)
        w = 14 if param == "ActionState" else 8
        h = lane.height() if param == "ActionState" else lane.height() * 0.6
        x = self.t_to_x(t)
        return QtCore.QRectF(x - w / 2, lane.center().y() - h / 2, w, h)

    def _tick_step_frames(self) -> int:
        """目盛りの間隔（フレーム）。幅に収まる最小のきりのよい値。"""
        total_f = max(1, int(round(self.total_sec * self.fps)))
        for step in (5, 10, 15, 30, 60, 90, 120, 150, 300, 600, 900):
            if total_f / step <= max(2, self._plot_w() / 48):
                return step
        return 900

    def marker_at(self, pos: QtCore.QPointF) -> tuple[str, float] | None:
        for t, p, _ in reversed(self.markers):
            if self._marker_rect(t, p).adjusted(-3, -3, 3, 3).contains(pos):
                return p, t
        return None

    # ---------------------------------------------------------- 描画
    def paintEvent(self, event) -> None:  # noqa: N802
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing)
        pal = self.palette()
        qp.fillRect(self.rect(), pal.base())
        small = QtGui.QFont(self.font())
        small.setPointSizeF(max(6.5, small.pointSizeF() - 1.5))
        qp.setFont(small)
        for param in ("ActionState", "Speed"):
            lane = self._lane_rect(param)
            qp.fillRect(lane, pal.alternateBase())
            qp.setPen(pal.mid().color())
            qp.drawText(lane.adjusted(3, 0, 0, 0), QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop, param)
        # 状態の帯
        band_y = TOP + LANE_H * 2 + 2
        for t0, t1, state in self.bands:
            r = QtCore.QRectF(self.t_to_x(t0), band_y, max(1.0, self.t_to_x(t1) - self.t_to_x(t0)), BAND_H)
            qp.fillRect(r, QtGui.QColor(STATE_COLORS.get(state, "#888888")))
            qp.setPen(QtGui.QColor("white"))
            qp.drawText(r, QtCore.Qt.AlignCenter, state)
        # 目盛り（フレーム番号。AN-F-34）
        qp.setPen(pal.mid().color())
        step_f = self._tick_step_frames()
        f = 0
        total_f = int(round(self.total_sec * self.fps))
        while f <= total_f:
            x = self.t_to_x(f / self.fps)
            qp.drawLine(QtCore.QPointF(x, band_y + BAND_H), QtCore.QPointF(x, band_y + BAND_H + 3))
            qp.drawText(QtCore.QPointF(x + 2, band_y + BAND_H + 13), f"{f}")
            f += step_f
        # マーカー
        for t, p, v in self.markers:
            drag = self._drag is not None and self._drag == (p, t)
            tt = self._drag_t if drag else t
            r = self._marker_rect(tt, p)
            color = QtGui.QColor(ACTION_COLORS.get(int(v), "#7a7a7a") if p == "ActionState" else "#5aa0d8")
            qp.setBrush(color)
            sel = self.selected == (p, t)
            qp.setPen(QtGui.QPen(QtGui.QColor("white") if sel else color.darker(150), 2 if sel else 1))
            qp.drawRoundedRect(r, 2, 2)
            fr = round(tt * self.fps)
            name = {0: "Idle", 1: "Walk", 2: "Jump", 3: "Fall"}.get(int(v), "ActionState")
            label = f"{name}({int(v)}) f={fr}" if p == "ActionState" else f"{v:g} f={fr}"
            qp.setPen(pal.text().color())
            qp.drawText(QtCore.QPointF(r.right() + 3, r.center().y() + 4), label)
        # 現在時刻の線
        x = self.t_to_x(self.current_frame / max(1, self.fps))
        qp.setPen(QtGui.QPen(QtGui.QColor("#e04040"), 2))
        qp.drawLine(QtCore.QPointF(x, 0), QtCore.QPointF(x, self.height()))
        qp.setPen(QtGui.QColor("#e04040"))
        qp.drawText(QtCore.QPointF(min(x + 3, self.width() - 40), self.height() - 3), f"f={int(round(self.current_frame))}")  # 今のフレーム

    # ---------------------------------------------------------- 入力
    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.setFocus()
        hit = self.marker_at(event.position())
        if event.button() == QtCore.Qt.LeftButton:
            self.selected = hit
            if hit is not None:
                self._drag, self._drag_t = hit, hit[1]
            else:
                self._scrubbing = True  # マーカーの上以外: 現在時刻をそのフレームへ動かす（AN-F-32）
                self.scrubRequested.emit(self.x_to_frame(event.position().x()))
            self.selectionChanged.emit(*(hit if hit else ("", 0.0)))
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            self._drag_t = self.x_to_frame(event.position().x()) / self.fps
            self.update()
        elif self._scrubbing:
            import time

            now = time.perf_counter()
            if now - self._last_scrub >= 0.03:  # ドラッグ中は 0.03 秒ごとにまとめる
                self._last_scrub = now
                self.scrubRequested.emit(self.x_to_frame(event.position().x()))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._scrubbing:
            self._scrubbing = False
            self.scrubRequested.emit(self.x_to_frame(event.position().x()))  # 離した位置を最後に必ず反映
        if self._drag is not None:
            p, t = self._drag
            new_t = self._drag_t
            self._drag = None
            if abs(new_t - t) > 1e-6:
                self.selected = (p, round(new_t, 3))
                self.markerMoved.emit(p, t, round(new_t, 3))
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.marker_at(event.position()) is None:
            lane = "ActionState" if event.position().y() < TOP + LANE_H else "Speed"
            self.addRequested.emit(self.x_to_frame(event.position().x()), lane)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace) and self.selected is not None:
            self.deleteRequested.emit(*self.selected)
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        hit = self.marker_at(QtCore.QPointF(event.pos()))
        if hit is None:
            return
        self.selected = hit
        self.selectionChanged.emit(*hit)
        menu = QtWidgets.QMenu(self)
        act = menu.addAction("このマーカーを削除")
        if menu.exec(event.globalPos()) is act:
            self.deleteRequested.emit(*hit)
