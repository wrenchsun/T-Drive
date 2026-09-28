"""プレビュータブ（docs/05 §6、R-4）: 環境プロファイル・カメラプリセット・キャラクターライト・パリティ状態。"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from . import environment, preview, session

ROTATE_STEP_DEG = 3.0
ROTATE_INTERVAL_MS = 33


def _warn(parent: QtWidgets.QWidget, exc: Exception) -> None:
    QtWidgets.QMessageBox.warning(parent, "T-Drive Toon", str(exc))


class PreviewTab(QtWidgets.QWidget):
    def __init__(self, s: session.Session) -> None:
        super().__init__()
        self.session = s
        v = QtWidgets.QVBoxLayout(self)

        # ---- 環境
        box = QtWidgets.QGroupBox("環境（Unity の描画条件）")
        form = QtWidgets.QFormLayout(box)
        self.profile = QtWidgets.QComboBox()
        self.profile.activated.connect(self.on_profile)
        form.addRow("環境プロファイル", self.profile)
        self.profile_info = QtWidgets.QLabel()
        self.profile_info.setWordWrap(True)
        form.addRow("", self.profile_info)
        v.addWidget(box)

        # ---- カメラ
        box = QtWidgets.QGroupBox("カメラ（環境プロファイルの縦 FOV / Near / Far）")
        h = QtWidgets.QVBoxLayout(box)
        row = QtWidgets.QHBoxLayout()
        self.target = QtWidgets.QComboBox()
        self.target.addItem("顔", "head")
        self.target.addItem("全身", "all")
        row.addWidget(QtWidgets.QLabel("範囲:"))
        row.addWidget(self.target)
        for name, yaw in environment.CAMERA_PRESETS.items():
            b = QtWidgets.QPushButton(name)
            b.clicked.connect(lambda _c=False, y=yaw: self.on_camera(y))
            row.addWidget(b)
        row.addStretch(1)
        h.addLayout(row)
        v.addWidget(box)

        # ---- ライト
        box = QtWidgets.QGroupBox("キャラクターライト（Unity のオイラー角と同じ表現）")
        grid = QtWidgets.QGridLayout(box)
        row = QtWidgets.QHBoxLayout()
        for name, (pitch, yaw) in preview.LIGHT_PRESETS.items():
            b = QtWidgets.QPushButton(name)
            b.clicked.connect(lambda _c=False, p=pitch, y=yaw: self.set_light(p, y))
            row.addWidget(b)
        game = QtWidgets.QPushButton("ゲームと同じ")
        game.setToolTip("環境プロファイルのキャラクターライトに戻す")
        game.clicked.connect(self.on_game_light)
        row.addWidget(game)
        row.addStretch(1)
        grid.addLayout(row, 0, 0, 1, 3)
        self.yaw = self._slider(0, 359)
        self.pitch = self._slider(-89, 89)
        self.yaw_value = QtWidgets.QLabel()
        self.pitch_value = QtWidgets.QLabel()
        grid.addWidget(QtWidgets.QLabel("ヨー（水平）"), 1, 0)
        grid.addWidget(self.yaw, 1, 1)
        grid.addWidget(self.yaw_value, 1, 2)
        grid.addWidget(QtWidgets.QLabel("ピッチ（高さ）"), 2, 0)
        grid.addWidget(self.pitch, 2, 1)
        grid.addWidget(self.pitch_value, 2, 2)
        self.rotate = QtWidgets.QPushButton("ライト回転 ▶")
        self.rotate.setCheckable(True)
        self.rotate.setToolTip("ヨーを自動で回し、影の境界の動き方（パカパカしないか）を確認する")
        self.rotate.toggled.connect(self.on_rotate)
        grid.addWidget(self.rotate, 3, 0, 1, 3)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(ROTATE_INTERVAL_MS)
        self.timer.timeout.connect(self._tick)
        v.addWidget(box)

        # ---- パリティ
        box = QtWidgets.QGroupBox("Unity とのパリティ（Maya 側の設定）")
        h = QtWidgets.QVBoxLayout(box)
        self.parity = QtWidgets.QTableWidget(0, 4)
        self.parity.setHorizontalHeaderLabels(["項目", "期待", "現在", ""])
        self.parity.verticalHeader().setVisible(False)
        self.parity.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.parity.horizontalHeader().setStretchLastSection(True)
        h.addWidget(self.parity)
        fix = QtWidgets.QPushButton("色の設定を Unity に合わせる")
        fix.clicked.connect(self.on_fix_color)
        h.addWidget(fix)
        v.addWidget(box)

        row = QtWidgets.QHBoxLayout()
        save = QtWidgets.QPushButton("この条件を保存（preview.json）")
        save.setToolTip("環境・ライトを looks/<キャラクター>/preview.json に保存。Look を開くと復元される")
        save.clicked.connect(self.on_save)
        row.addStretch(1)
        row.addWidget(save)
        v.addLayout(row)
        v.addStretch(1)

    def _slider(self, lo: int, hi: int) -> QtWidgets.QSlider:
        s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        s.setRange(lo, hi)
        s.valueChanged.connect(self._on_slider)
        return s

    # -------------------------------------------------------------- 表示
    def refresh(self) -> None:
        state = preview.environment_state()
        self.profile.blockSignals(True)
        self.profile.clear()
        self.profile.addItems(environment.list_profiles())
        if state["profile"]:
            self.profile.setCurrentText(state["profile"])
        self.profile.blockSignals(False)
        if state["profile"]:
            prof = environment.load_profile(state["profile"])
            cam = prof["camera"]
            warn = environment.profile_warnings(prof)
            self.profile_info.setText(
                f"トーンマップ: {prof['tonemapping']} / 縦 FOV {cam['verticalFov']}° / Near {cam['near']} m / Far {cam['far']} m"
                + ("<br><span style='color:#f0a040'>" + " / ".join(warn) + "</span>" if warn else "")
                + f"<br><span style='color:#888'>{prof.get('source', '')}</span>"
            )
        self._show_light()
        rows = environment.parity_status()
        self.parity.setRowCount(len(rows))
        for i, (name, expected, actual, ok) in enumerate(rows):
            for j, text in enumerate((name, expected, actual, "OK" if ok else "要確認")):
                item = QtWidgets.QTableWidgetItem(str(text))
                if j == 3:
                    item.setForeground(QtCore.Qt.green if ok else QtCore.Qt.yellow)
                self.parity.setItem(i, j, item)
        self.parity.resizeColumnsToContents()

    def _show_light(self) -> None:
        pitch, yaw = preview.environment_state()["lightEuler"]
        for s, v in ((self.yaw, yaw), (self.pitch, pitch)):
            s.blockSignals(True)
            s.setValue(int(round(v)))
            s.blockSignals(False)
        self.yaw_value.setText(f"{yaw:.0f}°")
        self.pitch_value.setText(f"{pitch:.0f}°")

    # -------------------------------------------------------------- 操作
    def on_profile(self) -> None:
        try:
            warnings = preview.use_profile(self.profile.currentText())
        except Exception as exc:
            _warn(self, exc)
            return
        if warnings:
            _warn(self, RuntimeError("\n".join(warnings)))
        self.refresh()

    def on_camera(self, yaw: float) -> None:
        state = preview.environment_state()
        name = state["profile"] or environment.default_profile()
        if not name:
            _warn(self, RuntimeError("環境プロファイルがありません（looks/_env）"))
            return
        try:
            environment.frame_camera(environment.load_profile(name), yaw, target=self.target.currentData())
        except Exception as exc:
            _warn(self, exc)

    def set_light(self, pitch: float, yaw: float) -> None:
        preview.set_light_euler(pitch, yaw)
        self._show_light()

    def on_game_light(self) -> None:
        preview.reset_light_to_profile()
        self._show_light()

    def _on_slider(self) -> None:
        preview.set_light_euler(self.pitch.value(), self.yaw.value())
        self.yaw_value.setText(f"{self.yaw.value()}°")
        self.pitch_value.setText(f"{self.pitch.value()}°")

    def on_rotate(self, on: bool) -> None:
        self.rotate.setText("ライト回転 ■" if on else "ライト回転 ▶")
        self.timer.start() if on else self.timer.stop()

    def _tick(self) -> None:
        pitch, yaw = preview.environment_state()["lightEuler"]
        preview.set_light_euler(pitch, yaw + ROTATE_STEP_DEG)
        self._show_light()

    def on_fix_color(self) -> None:
        environment.apply_color_management()
        self.refresh()

    def on_save(self) -> None:
        try:
            path = self.session.save_preview_settings()
        except Exception as exc:
            _warn(self, exc)
            return
        QtWidgets.QMessageBox.information(self, "T-Drive Toon", f"保存しました: {preview.to_repo_path(str(path))}")
