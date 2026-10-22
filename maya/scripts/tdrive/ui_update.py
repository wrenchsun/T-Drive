"""更新ウィンドウ（docs/13 §3、R-4）と起動時の確認。D-Drive の更新ウィンドウと同じ構成。

版 / 更新の確認（PATCH・MINOR・MAJOR）/ 変更点（CHANGELOG）/ この版に更新 / 前の版に戻す / 更新後の確認。
外部ツール（docs/13 §6、R-10）: 「URL を入力して追加」/ 一覧 / 更新 / 元に戻す / 無効化 / 削除（処理は external_ops）。
"""

from __future__ import annotations

import html
import runpy
import threading
import time

from maya import cmds
from PySide6 import QtCore, QtGui, QtWidgets

from tdrive import widgets as WG

from . import REPO_ROOT, external, external_load, external_ops, project, updater

WINDOW_TITLE = "T-Drive Toon の更新"
KIND_LABEL = {"major": "MAJOR（互換性が変わる）", "minor": "MINOR（機能の追加）", "patch": "PATCH（修正）", "same": "今の版", "older": "古い版"}
KIND_COLOR = {"major": "#ff6060", "minor": "#f0c060", "patch": "#80d080"}

EXT_COLUMNS = ["名前", "今の版", "固定タグ", "最新の版", "上げ幅", "有効"]
EXT_KIND_TEXT = {"major": "MAJOR", "minor": "MINOR", "patch": "PATCH", "same": "最新", "older": "古い版"}

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
        self.resize(680, 840)
        self.git = updater.Git(REPO_ROOT)
        self.versions: list[updater.Version] = []
        self.ext_base = None  # TDriveToon フォルダ（None で %LOCALAPPDATA%\TDriveToon。テストは一時フォルダを入れる）
        self.ext_entries: list[external.ToolEntry] = []
        self.ext_latest: dict[str, str | None] = {}  # id → 最新の版（「最新の版を確認」で埋める）
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
        self.target = WG.ComboBox()
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

        # ---- 外部ツール（docs/13 §6.3）
        box = QtWidgets.QGroupBox("外部ツール")
        h = QtWidgets.QVBoxLayout(box)
        row = QtWidgets.QHBoxLayout()
        self.ext_add_btn = QtWidgets.QPushButton("URL を入力して追加")
        self.ext_add_btn.setToolTip("外部ツールのリポジトリの URL（と、任意でタグ）を入れて導入する。導入するとその場で読み込まれる")
        self.ext_add_btn.clicked.connect(self.ext_add)
        self.ext_check_btn = QtWidgets.QPushButton("最新の版を確認")
        self.ext_check_btn.clicked.connect(self.ext_check)
        row.addWidget(self.ext_add_btn)
        row.addWidget(self.ext_check_btn)
        row.addStretch(1)
        h.addLayout(row)
        self.ext_table = QtWidgets.QTableWidget(0, len(EXT_COLUMNS))
        self.ext_table.setHorizontalHeaderLabels(EXT_COLUMNS)
        self.ext_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.ext_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.ext_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.ext_table.verticalHeader().setVisible(False)
        self.ext_table.horizontalHeader().setStretchLastSection(True)
        self.ext_table.setMinimumHeight(100)
        self.ext_table.itemSelectionChanged.connect(self.ext_selection_changed)
        h.addWidget(self.ext_table)
        self.ext_status = QtWidgets.QLabel("外部ツールはまだありません")
        self.ext_status.setWordWrap(True)
        h.addWidget(self.ext_status)
        row = QtWidgets.QHBoxLayout()
        self.ext_update_btn = QtWidgets.QPushButton("最新の版に更新")
        self.ext_update_btn.clicked.connect(self.ext_update)
        self.ext_rollback_btn = QtWidgets.QPushButton("元に戻す")
        self.ext_rollback_btn.clicked.connect(self.ext_rollback)
        self.ext_enable_btn = QtWidgets.QPushButton("無効にする")
        self.ext_enable_btn.clicked.connect(self.ext_toggle_enabled)
        self.ext_remove_btn = QtWidgets.QPushButton("削除")
        self.ext_remove_btn.clicked.connect(self.ext_remove)
        for b in (self.ext_update_btn, self.ext_rollback_btn, self.ext_enable_btn, self.ext_remove_btn):
            row.addWidget(b)
        row.addStretch(1)
        h.addLayout(row)
        v.addWidget(box)

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
        self.ext_refresh()

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

    # -------------------------------------------------------------- 外部ツール
    # ダイアログは _ext_ask_url / _ext_confirm / _ext_warn / _ext_info に集めてある（スモークテストは差し替える）
    def _ext_ask_url(self) -> tuple[str, str] | None:
        """URL とタグ（任意）を聞く → (url, tag)。キャンセル・URL が空なら None。"""
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("外部ツールを追加")
        form = QtWidgets.QFormLayout(dlg)
        url = QtWidgets.QLineEdit()
        url.setMinimumWidth(420)
        url.setPlaceholderText("https://github.com/…/….git")
        tag = QtWidgets.QLineEdit()
        tag.setPlaceholderText("空なら最新のリリースタグ（例: v1.2.0）")
        form.addRow("URL", url)
        form.addRow("タグ（任意）", tag)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QtWidgets.QDialog.Accepted or not url.text().strip():
            return None
        return url.text().strip(), tag.text().strip()

    def _ext_confirm(self, text: str) -> bool:
        yes_no = QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        return QtWidgets.QMessageBox.question(self, WINDOW_TITLE, text, yes_no, QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes

    def _ext_warn(self, text: str) -> None:
        QtWidgets.QMessageBox.warning(self, WINDOW_TITLE, text)

    def _ext_info(self, text: str) -> None:
        QtWidgets.QMessageBox.information(self, WINDOW_TITLE, text)

    def _ext_busy(self, fn):
        """時間のかかる処理（clone・ls-remote）を、カーソルを待機にして行う（check と同じ流儀）。"""
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            return fn()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def ext_selected(self) -> "external.ToolEntry | None":
        row = self.ext_table.currentRow()
        return self.ext_entries[row] if 0 <= row < len(self.ext_entries) else None

    def _ext_kind(self, version: str, latest: str | None) -> str | None:
        cur, new = updater.parse_version(version), updater.parse_version(latest or "")
        return updater.bump_kind(cur, new) if cur and new else None

    def ext_refresh(self, select_id: str | None = None) -> None:
        """一覧を tools.json から作り直す（ネットワークには出ない。最新の版は確認済みのものだけ）。"""
        if select_id is None:
            cur = self.ext_selected()
            select_id = cur.id if cur else None
        self.ext_entries, warnings = external.load_tools(external.tools_json_path(self.ext_base))
        self.ext_table.blockSignals(True)
        self.ext_table.setRowCount(len(self.ext_entries))
        for r, e in enumerate(self.ext_entries):
            st = external_ops.status(e, self.ext_base, check_remote=False)
            latest = self.ext_latest.get(e.id)
            kind = self._ext_kind(st["version"], latest)
            cells = [
                st["name"] + ("（開発用）" if st["dev"] else ""), st["version"] or "-", st["tag"] or "-",
                f"v{latest}" if latest else "-", EXT_KIND_TEXT.get(kind, ""), "有効" if e.enabled else "無効",
            ]
            for c, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if c == 4 and kind in KIND_COLOR:
                    item.setForeground(QtGui.QBrush(QtGui.QColor(KIND_COLOR[kind])))
                if st["problem"]:
                    item.setToolTip(st["problem"])
                self.ext_table.setItem(r, c, item)
        self.ext_table.blockSignals(False)
        ids = [e.id for e in self.ext_entries]
        if select_id in ids:
            self.ext_table.selectRow(ids.index(select_id))
        elif self.ext_entries:
            self.ext_table.selectRow(0)
        if warnings:
            self.ext_status.setText("<span style='color:#f0a040'>" + "<br>".join(html.escape(w) for w in warnings) + "</span>")
        elif not self.ext_entries:
            self.ext_status.setText("外部ツールはまだありません。「URL を入力して追加」で導入できます")
        self.ext_selection_changed()

    def ext_selection_changed(self) -> None:
        e = self.ext_selected()
        buttons = (self.ext_update_btn, self.ext_rollback_btn, self.ext_enable_btn, self.ext_remove_btn)
        if e is None:
            for b in buttons:
                b.setEnabled(False)
            return
        st = external_ops.status(e, self.ext_base, check_remote=False)
        dev_reason = "開発用（自分の clone・ブランチ上・変更あり）のため、更新ウィンドウでは更新しません。git で更新してください"
        self.ext_update_btn.setEnabled(not st["dev"] and not st["problem"])
        self.ext_update_btn.setToolTip(dev_reason if st["dev"] else "最新のリリースタグに更新する（変更点を見て確認してから）")
        self.ext_rollback_btn.setEnabled(not st["dev"] and bool(e.previous))
        self.ext_rollback_btn.setToolTip(dev_reason if st["dev"] else f"前の版: {e.previous}" if e.previous else "戻せる前の版の記録がありません")
        self.ext_enable_btn.setEnabled(True)
        self.ext_enable_btn.setText("無効にする" if e.enabled else "有効にする")
        self.ext_enable_btn.setToolTip("無効にすると次のリロード・起動から読み込まない（checkout は残す）" if e.enabled else "有効にして、その場で読み込む")
        self.ext_remove_btn.setEnabled(True)
        self.ext_remove_btn.setToolTip("一覧から外し、checkout のフォルダを消す" + ("（開発用の clone は消さない）" if external.is_dev_dir(e.dir) else ""))
        if st["problem"]:
            self.ext_status.setText(f"<span style='color:#ff6060'>{html.escape(st['problem'])}</span>")

    def ext_check(self) -> None:
        """有効な外部ツールの最新の版を確認する（git ls-remote）。"""
        for e, latest in self._ext_busy(lambda: external_ops.check_all(self.ext_base)):
            self.ext_latest[e.id] = latest
        self.ext_refresh()
        newer = []
        for e in self.ext_entries:
            st = external_ops.status(e, self.ext_base, check_remote=False)
            if self._ext_kind(st["version"], self.ext_latest.get(e.id)) in ("major", "minor", "patch"):
                newer.append(f"<b>{html.escape(st['name'])}</b> v{self.ext_latest[e.id]}")
        self.ext_status.setText("新しい版があります: " + ", ".join(newer) if newer else "外部ツールはすべて最新です（確認できなかったものは「-」）")
        self.ext_show_changes()

    def ext_show_changes(self) -> None:
        """選んだツールの変更点（最新の版まで）を、本体と同じ欄に出す。"""
        e = self.ext_selected()
        latest = self.ext_latest.get(e.id) if e else None
        if e is not None and latest:
            self._ext_show_target_changes(e, external_ops.status(e, self.ext_base, check_remote=False)["version"], latest)

    def _ext_show_target_changes(self, e: "external.ToolEntry", current: str, target: str) -> None:
        cur, new = updater.parse_version(current) or (0, 0, 0), updater.parse_version(target) or (0, 0, 0)
        kind = updater.bump_kind(cur, new)
        text = self._ext_busy(lambda: external_ops.changelog(e, current, target, self.ext_base))
        parts = []
        if kind == "major":
            parts.append(
                "<p style='color:#ff6060'><b>MAJOR の更新です。</b>今までのデータの扱いが変わるかもしれません。"
                "更新の前に変更点（互換性）を読み、担当者に確認してください。</p>"
            )
        if kind == "older":
            parts.append("<p style='color:#f0a040'>古い版に戻します。以下はこの版より後に入った変更（戻すと無くなるもの）です。</p>")
        parts.append(f"<pre style='white-space:pre-wrap'>{html.escape(text)}</pre>" if text else "<p>変更点の記録が見つかりません</p>")
        self.changes.setHtml("".join(parts))

    def ext_add(self) -> None:
        got = self._ext_ask_url()
        if got is None:
            return
        url, tag = got
        try:
            entry = self._ext_busy(lambda: external_ops.add_from_url(url, tag or None, self.ext_base))
        except updater.GitError as exc:
            self._ext_warn(f"追加できませんでした:\n{exc}")
            return
        loaded = self._ext_load([entry.id])
        self.ext_refresh(select_id=entry.id)
        self.ext_status.setText(f"<b>{html.escape(entry.id)}</b> {html.escape(entry.tag)} を追加しました" + ("（読み込み済み）" if loaded else ""))

    def _ext_load(self, ids: list[str]) -> bool:
        """このツールだけをその場で読み込む（ほかのツールは作り直さない）。失敗したものは「読み込めなかったタブ」になる。"""
        try:
            return bool(external_load.load_external_tools(external.tools_json_path(self.ext_base), only=ids))
        except Exception as exc:  # noqa: BLE001  読み込みの失敗で更新ウィンドウを止めない
            self._ext_warn(f"読み込めませんでした（ツールをリロードで再読み込みします）:\n{exc}")
            return False

    def ext_update(self) -> None:
        e = self.ext_selected()
        if e is None:
            return
        if self.ext_latest.get(e.id) is None:
            self.ext_latest[e.id] = self._ext_busy(lambda: external_ops.status(e, self.ext_base)["latest"])
        latest = self.ext_latest[e.id]
        st = external_ops.status(e, self.ext_base, check_remote=False)
        self.ext_refresh()
        if not latest:
            self.ext_status.setText("<span style='color:#ff6060'>最新の版を確認できませんでした</span>")
            return
        kind = self._ext_kind(st["version"], latest)
        self._ext_show_target_changes(e, st["version"], latest)
        if kind in ("same", "older"):
            self._ext_info(f"{st['name']} は最新の版（v{st['version']}）です。")
            return
        msg = f"{st['name']} を v{st['version']} → v{latest} に更新します。"
        if kind == "major":
            msg += "\n\nMAJOR の更新です。変更点（互換性）を読みましたか？"
        if not self._ext_confirm(msg + "\n\n続けますか？"):
            return
        self._ext_apply(e, lambda: external_ops.update(e, f"v{latest}", self.ext_base))

    def ext_rollback(self) -> None:
        e = self.ext_selected()
        if e is None or not e.previous:
            return
        if not self._ext_confirm(f"{e.id} を前の版（{e.previous}）に戻しますか？"):
            return
        self._ext_apply(e, lambda: external_ops.rollback(e, self.ext_base))

    def _ext_apply(self, e: "external.ToolEntry", fn) -> None:
        """更新 / 元に戻すの実行。反映は「ツールをリロード」（本体の更新後と同じ経路）を案内する。"""
        try:
            res = self._ext_busy(fn)
        except updater.GitError as exc:
            self._ext_warn(f"更新できませんでした:\n{exc}")
            return
        self.ext_refresh(select_id=e.id)
        note = "\n\nこの更新は Maya の再起動が必要です。" if res.get("restart") else ""
        self.ext_status.setText(f"{html.escape(e.id)}: {html.escape(str(res['from']))} → {html.escape(str(res['to']))}（ツールをリロードで反映）")
        self._ext_info(
            f"{e.id} を {res['to']} にしました。{note}\n\n反映するには、メニュー Tools › T-Drive › ツールをリロード を実行してください"
            "（未保存の Look・FacialController のデータは引き継がれます）。"
        )

    def ext_toggle_enabled(self) -> None:
        e = self.ext_selected()
        if e is None:
            return
        flag = not e.enabled
        try:
            external_ops.set_enabled(e, flag, self.ext_base)
        except OSError as exc:
            self._ext_warn(f"保存できませんでした:\n{exc}")
            return
        if flag:
            self._ext_load([e.id])
        self.ext_refresh(select_id=e.id)
        self.ext_status.setText(
            f"{html.escape(e.id)} を" + ("有効にしました（読み込み済み）" if flag else "無効にしました（次のリロード・起動から読み込みません）")
        )

    def ext_remove(self) -> None:
        e = self.ext_selected()
        if e is None:
            return
        body = "開発用の clone のフォルダは消しません（一覧から外すだけです）。" if external.is_dev_dir(e.dir) else "checkout のフォルダも消えます。"
        if not self._ext_confirm(f"{e.id} を削除します。\n\n{body}\n\n続けますか？"):
            return
        try:
            external_ops.remove(e, self.ext_base)
        except OSError as exc:
            self._ext_warn(f"削除できませんでした:\n{exc}")
            return
        self.ext_latest.pop(e.id, None)
        self.ext_refresh()
        self.ext_status.setText(f"{html.escape(e.id)} を削除しました（開いているタブは、ツールをリロードで消えます）")

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
        _message("T-Drive Toon を更新しました。<hl>Tools › T-Drive › 更新…</hl> で「プロジェクトの Look を確認する」を押してください")
    state = updater.read_state(git)
    if not updater.should_auto_check(state):
        return

    def work() -> None:
        body_latest = None
        try:
            versions = git.remote_versions()
            updater.write_state(git, lastCheck=time.time())
            cur = updater.parse_version(updater.installed_version()) or (0, 0, 0)
            if versions and versions[0] > cur:
                body_latest = updater.fmt(versions[0])
        except updater.GitError:
            pass  # ネットワーク・権限が無いときは黙って次回
        try:
            tools = external_ops.pending_updates()  # 外部ツールの更新（本体と合わせて 1 つのメッセージにする）
        except Exception:  # noqa: BLE001  確認の失敗は黙る
            tools = []
        text = external_ops.startup_message(body_latest, tools)
        if text:
            import maya.utils

            maya.utils.executeDeferred(_message, text)

    threading.Thread(target=work, name="TDriveToonUpdateCheck", daemon=True).start()


def _message(text: str) -> None:
    cmds.inViewMessage(amg=text, pos="topCenter", fade=True, fadeStayTime=8000)
