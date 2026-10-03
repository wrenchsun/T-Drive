"""更新ウィンドウ（docs/13 §3、R-4）と起動時の確認。D-Drive の更新ウィンドウと同じ構成。

版 / 更新の確認（PATCH・MINOR・MAJOR）/ 変更点（CHANGELOG）/ この版に更新 / 前の版に戻す / 更新後の確認。
"""

from __future__ import annotations

import html
import runpy
import threading
import time

from maya import cmds
from PySide6 import QtCore, QtWidgets

from . import REPO_ROOT, project, updater

WINDOW_TITLE = "T-Drive Toon の更新"
KIND_LABEL = {"major": "MAJOR（互換性が変わる）", "minor": "MINOR（機能の追加）", "patch": "PATCH（修正）", "same": "今の版", "older": "古い版"}
KIND_COLOR = {"major": "#ff6060", "minor": "#f0c060", "patch": "#80d080"}

_window: "UpdateWindow | None" = None


def show() -> "UpdateWindow":
    global _window
    try:
        if _window is not None and _window.isVisible():
            _window.raise_()
            return _window
    except RuntimeError:
        pass
    _window = UpdateWindow(_maya_main_window())
    _window.show()
    return _window


def _maya_main_window():
    from maya import OpenMayaUI as omui
    from shiboken6 import wrapInstance

    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None


class UpdateWindow(QtWidgets.QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(640, 620)
        self.git = updater.Git(REPO_ROOT)
        self.versions: list[updater.Version] = []
        v = QtWidgets.QVBoxLayout(self)

        # ---- 版
        box = QtWidgets.QGroupBox("今の版")
        form = QtWidgets.QFormLayout(box)
        self.version_label = QtWidgets.QLabel()
        self.kind_label = QtWidgets.QLabel()
        self.kind_label.setWordWrap(True)
        self.project_label = QtWidgets.QLabel()
        self.project_label.setWordWrap(True)
        form.addRow("版", self.version_label)
        form.addRow("導入", self.kind_label)
        form.addRow("プロジェクト", self.project_label)
        v.addWidget(box)

        # ---- 更新の確認
        box = QtWidgets.QGroupBox("更新")
        h = QtWidgets.QVBoxLayout(box)
        row = QtWidgets.QHBoxLayout()
        self.check_btn = QtWidgets.QPushButton("最新の版を確認")
        self.check_btn.clicked.connect(self.check)
        row.addWidget(self.check_btn)
        self.target = QtWidgets.QComboBox()
        self.target.setMinimumWidth(220)
        self.target.currentIndexChanged.connect(self.show_changes)
        row.addWidget(self.target, 1)
        h.addLayout(row)
        self.status = QtWidgets.QLabel("「最新の版を確認」を押してください")
        self.status.setWordWrap(True)
        h.addWidget(self.status)
        self.changes = QtWidgets.QTextBrowser()
        self.changes.setOpenExternalLinks(True)
        h.addWidget(self.changes, 1)
        row = QtWidgets.QHBoxLayout()
        self.update_btn = QtWidgets.QPushButton("この版に更新")
        self.update_btn.clicked.connect(self.update_to_selected)
        self.rollback_btn = QtWidgets.QPushButton("前の版に戻す")
        self.rollback_btn.clicked.connect(self.rollback)
        row.addWidget(self.update_btn)
        row.addWidget(self.rollback_btn)
        row.addStretch(1)
        h.addLayout(row)
        v.addWidget(box, 1)

        # ---- 更新後の確認・設定
        box = QtWidgets.QGroupBox("更新後の確認（プロジェクトの Look）")
        h = QtWidgets.QVBoxLayout(box)
        row = QtWidgets.QHBoxLayout()
        self.verify_btn = QtWidgets.QPushButton("プロジェクトの Look を確認する")
        self.verify_btn.setToolTip("全 Look を今の版で読み込んで検証する（古い Look は自動で新しい形式に補われる。保存はしない）")
        self.verify_btn.clicked.connect(self.verify_project)
        row.addWidget(self.verify_btn)
        self.applied_label = QtWidgets.QLabel()
        row.addWidget(self.applied_label, 1)
        h.addLayout(row)
        self.auto = QtWidgets.QCheckBox("Maya の起動時に更新を確認する（1 日 1 回）")
        self.auto.clicked.connect(lambda *_: self._set_auto(self.auto.isChecked()))
        h.addWidget(self.auto)
        v.addWidget(box)
        self.refresh()

    # -------------------------------------------------------------- 表示
    def refresh(self) -> None:
        cur = updater.installed_version()
        tag = self.git.current_tag() if self.git.is_repo() else None
        self.kind = updater.install_kind(self.git)
        self.version_label.setText(f"<b>{cur}</b>" + (f"（タグ {tag}）" if tag else ""))
        if self.kind == "release":
            self.kind_label.setText(f"リリース版（{REPO_ROOT.as_posix()}）")
        elif self.kind == "dev":
            branch = self.git.branch()
            self.kind_label.setText(
                f"<span style='color:#f0a040'>開発用（{'ブランチ ' + branch if branch else '変更あり'}）。更新は git で行ってください</span>"
            )
        else:
            self.kind_label.setText("<span style='color:#f0a040'>git で管理されていないため更新できません（入れ直してください）</span>")
        where = "開発用（ツール本体）" if project.is_tool_repo() else project.root().as_posix()
        self.project_label.setText(where)
        applied = project.read_config().get("lastAppliedToolVersion")
        self.applied_label.setText(
            f"確認済みの版: {applied}" if applied == cur else f"<span style='color:#f0a040'>未確認（確認済み: {applied or 'なし'}）</span>"
        )
        state = updater.read_state(self.git) if self.git.is_repo() else {}
        self.auto.setChecked(bool(state.get("autoCheck", True)))
        self.rollback_btn.setEnabled(self.kind == "release" and bool(state.get("previous")))
        self.rollback_btn.setToolTip(f"前の版: {state.get('previous')}" if state.get("previous") else "戻せる前の版の記録がありません")
        self.update_btn.setEnabled(self.kind == "release" and self.target.count() > 0)
        self.check_btn.setEnabled(self.kind != "none")

    def check(self) -> None:
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.git.fetch_tags()  # 変更点（CHANGELOG）をタグから読むため。今のチェックアウトは変えない
            self.versions = self.git.remote_versions()
            updater.write_state(self.git, lastCheck=time.time())
        except updater.GitError as exc:
            self.status.setText(f"<span style='color:#ff6060'>確認できませんでした: {html.escape(str(exc))}</span>")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        cur = updater.parse_version(updater.installed_version()) or (0, 0, 0)
        self.target.blockSignals(True)
        self.target.clear()
        for ver in self.versions:
            kind = updater.bump_kind(cur, ver)
            self.target.addItem(f"v{updater.fmt(ver)}  —  {KIND_LABEL[kind]}", ver)
        self.target.blockSignals(False)
        newest = self.versions[0] if self.versions else None
        if newest and newest > cur:
            self.status.setText(f"新しい版があります: <b>v{updater.fmt(newest)}</b>（今は {updater.fmt(cur)}）")
        else:
            self.status.setText("最新の版です")
        self.show_changes()
        self.refresh()

    def show_changes(self) -> None:
        ver = self.target.currentData()
        if ver is None:
            self.changes.clear()
            return
        cur = updater.parse_version(updater.installed_version()) or (0, 0, 0)
        kind = updater.bump_kind(cur, ver)
        try:
            text = self.git.show(f"v{updater.fmt(ver)}", "CHANGELOG.md")
        except updater.GitError:
            text = ""
        low, high = (cur, ver) if ver > cur else (ver, cur)
        sections = updater.changelog_between(text if ver > cur else _read_changelog(), low, high)
        parts = []
        if kind == "major":
            doc = updater.MAJOR_MIGRATION_DOC.format(major=ver[0])
            parts.append(
                f"<p style='color:#ff6060'><b>MAJOR の更新です。</b>今までの Look・データの扱いが変わります。"
                f"更新の前に移行ガイド（{doc}）を読み、担当者に確認してください。</p>"
            )
        if ver < cur:
            parts.append("<p style='color:#f0a040'>古い版に戻します。以下はこの版より後に入った変更（戻すと無くなるもの）です。</p>")
        for sv, body in sections:
            compat = updater.compat_notes(body)
            parts.append(f"<h3>{updater.fmt(sv)}</h3><pre style='white-space:pre-wrap'>{html.escape(body.split(chr(10), 1)[1] if chr(10) in body else '')}</pre>")
            if compat:
                parts.append(f"<p style='background:#403020'><b>互換性:</b> {html.escape(compat)}</p>")
        if not sections and kind != "same":
            parts.append("<p>変更点の記録が見つかりません</p>")
        self.changes.setHtml("".join(parts) or "<p>今の版です</p>")
        color = KIND_COLOR.get(kind)
        self.target.setStyleSheet(f"color: {color};" if color else "")
        self.update_btn.setEnabled(self.kind == "release" and kind != "same")

    # -------------------------------------------------------------- 操作
    def update_to_selected(self) -> None:
        ver = self.target.currentData()
        if ver is None:
            return
        cur = updater.parse_version(updater.installed_version()) or (0, 0, 0)
        kind = updater.bump_kind(cur, ver)
        msg = f"T-Drive Toon を v{updater.fmt(cur)} → v{updater.fmt(ver)} に{'戻し' if kind == 'older' else '更新し'}ます。"
        if kind == "major":
            msg += "\n\nMAJOR の更新です。移行ガイドを読みましたか？"
        msg += "\n\n編集中の Look・FacialController のデータの未保存の変更は、この更新では残ります（念のため先に保存してください）。続けますか？"
        yes_no = QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        if QtWidgets.QMessageBox.question(self, WINDOW_TITLE, msg, yes_no, QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        self._apply(lambda: updater.update_to(self.git, f"v{updater.fmt(ver)}"))

    def rollback(self) -> None:
        prev = updater.read_state(self.git).get("previous")
        yes_no = QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        if QtWidgets.QMessageBox.question(self, WINDOW_TITLE, f"前の版（{prev}）に戻しますか？", yes_no, QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        self._apply(lambda: updater.rollback(self.git))

    def _apply(self, fn) -> None:
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            res = fn()
            _update_mod_file()
        except updater.GitError as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, WINDOW_TITLE, f"更新できませんでした:\n{exc}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        new = updater.installed_version()
        if res["restart"]:
            QtWidgets.QMessageBox.information(
                self, WINDOW_TITLE, f"v{new} にしました。\n\nこの更新は Maya の再起動が必要です。Look と FacialController のデータを保存してから Maya を再起動してください（再起動すると未保存の変更は失われます）。"
            )
            self.refresh()
            return
        self.close()
        runpy.run_path(str(REPO_ROOT / "maya" / "mcp_scripts" / "reload_tdrive.py"))  # 未保存の Look・FacialController のデータは引き継がれる
        from tdrive import ui_update as fresh  # リロード後の新しいモジュール

        win = fresh.show()
        win.verify_project()
        QtWidgets.QMessageBox.information(win, WINDOW_TITLE, f"v{new} にしました。")

    def verify_project(self) -> None:
        errors, count = verify_project_looks()
        cur = updater.installed_version()
        if not errors:
            project.write_config(lastAppliedToolVersion=cur)
            self.status.setText(f"更新後の確認: Look {count} 件に問題はありませんでした")
        else:
            self.status.setText(
                "<span style='color:#ff6060'>更新後の確認で問題が見つかりました:</span><br>" + "<br>".join(html.escape(e) for e in errors[:10])
            )
        self.refresh()

    def _set_auto(self, on: bool) -> None:
        try:
            updater.write_state(self.git, autoCheck=bool(on))
        except updater.GitError:
            pass


def _read_changelog() -> str:
    try:
        return (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def _update_mod_file() -> None:
    path = updater.mod_path(cmds.internalVar(userAppDir=True))
    if path.exists():
        text = path.read_text(encoding="utf-8")
        path.write_text(updater.update_mod_version(text, updater.installed_version()), encoding="utf-8", newline="\n")


def verify_project_looks() -> tuple[list[str], int]:
    """プロジェクトの全 Look を今の版で読み込み・検証する（保存はしない）。"""
    from tdrive_toon import look  # Toon の Look の検証（殻は Toon を import しない = 遅延 import）

    errors, count = [], 0
    for path in sorted(project.looks_dir().glob("*/look.json")):
        count += 1
        try:
            lk = look.load(path)
            problems = look.validate(lk)
        except Exception as exc:  # noqa: BLE001  1 件の失敗で止めない
            problems = [str(exc)]
        errors += [f"{path.parent.name}: {p}" for p in problems]
    return errors, count


# ---------------------------------------------------------------- 起動時の確認
def startup_check() -> None:
    """Maya の起動時（userSetup）: 更新後の確認待ちの案内と、1 日 1 回の更新の確認（裏で）。"""
    git = updater.Git(REPO_ROOT)
    if updater.install_kind(git) != "release":
        return  # 開発用は案内しない
    applied = project.read_config().get("lastAppliedToolVersion")
    if not project.is_tool_repo() and applied != updater.installed_version():
        _message("T-Drive Toon を更新しました。<hl>T-Drive › 更新…</hl> で「プロジェクトの Look を確認する」を押してください")
    state = updater.read_state(git)
    if not updater.should_auto_check(state):
        return

    def work() -> None:
        try:
            versions = git.remote_versions()
            updater.write_state(git, lastCheck=time.time())
        except updater.GitError:
            return  # ネットワーク・権限が無いときは黙って次回
        cur = updater.parse_version(updater.installed_version()) or (0, 0, 0)
        if versions and versions[0] > cur:
            import maya.utils

            maya.utils.executeDeferred(
                _message, f"T-Drive Toon の新しい版 v{updater.fmt(versions[0])} があります（<hl>T-Drive › 更新…</hl>）"
            )

    threading.Thread(target=work, name="TDriveToonUpdateCheck", daemon=True).start()


def _message(text: str) -> None:
    cmds.inViewMessage(amg=text, pos="topCenter", fade=True, fadeStayTime=8000)
