"""検証タブ（docs/14 §5.9）: データの問題を探して一覧にし、似た名前への一括改名・無い参照の削除で直す。

[検証] を押すとシーンの情報を取り直して調べる（データもシーンも変えない）。自動では調べ直さない（重いことがあるため）。
一覧はエラー → 警告 → 情報の順。点の場所がある行はダブルクリックで、グリッド / ポーズタブをその点へ移す。
"""

from __future__ import annotations

import traceback

from PySide6 import QtCore, QtGui, QtWidgets

from tdrive import lifecycle

from .core.presenters import CONFIRM_CANCEL, SELECT_NEEDS_CONFIRM, SELECT_SELECTED
from .core.validate import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARNING
from .ui import DIM_STYLE, WARN_STYLE, ask_save_discard_cancel, ask_yes_no

COL_WHERE, COL_MESSAGE, COL_CANDIDATE = range(3)
NO_RENAME = "（改名しない）"
SEVERITY_COLOR = {SEVERITY_ERROR: "#e05a5a", SEVERITY_WARNING: "#f0a040", SEVERITY_INFO: "#9aa6b8"}


class ValidateTab(QtWidgets.QWidget):
    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self._updating = False
        self.choices: dict[tuple[str, str], str] = {}  # (kind, 旧名) → 選んだ新名（"" = 改名しない）
        self._combos: list[tuple[tuple[str, str], QtWidgets.QComboBox]] = []
        self._issue_of_item: dict[int, object] = {}

        v = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.run_btn = QtWidgets.QPushButton("検証")
        self.run_btn.setToolTip("データとシーンを突き合わせて、問題を一覧にする（データは変わりません）")
        self.run_btn.clicked.connect(lambda *_: self.on_run())
        row.addWidget(self.run_btn)
        self.summary = QtWidgets.QLabel()
        row.addWidget(self.summary, 1)
        v.addLayout(row)
        self.stale_label = QtWidgets.QLabel("最後の検証のあとでデータが変わりました（[検証] で取り直せます）")
        self.stale_label.setStyleSheet(WARN_STYLE)
        self.stale_label.setWordWrap(True)
        v.addWidget(self.stale_label)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(("場所", "内容", "改名の候補"))
        self.tree.setRootIsDecorated(True)
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setMinimumHeight(220)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(COL_WHERE, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(COL_MESSAGE, QtWidgets.QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(COL_CANDIDATE, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.setToolTip("点の場所がある行はダブルクリックで、その点へ移ります")
        self.tree.itemDoubleClicked.connect(lambda item, _col=0: self.on_double_click(item))
        v.addWidget(self.tree, 1)
        self.detail = QtWidgets.QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.detail.setStyleSheet(DIM_STYLE)
        self.detail.setMinimumHeight(40)
        self.tree.currentItemChanged.connect(lambda cur, _prev=None: self._show_detail(cur))
        v.addWidget(self.detail)

        row = QtWidgets.QHBoxLayout()
        self.rename_btn = QtWidgets.QPushButton("候補で一括改名")
        self.rename_btn.setToolTip("「改名の候補」で選んだ名前に、データの中の名前をまとめて書き換える")
        self.rename_btn.clicked.connect(lambda *_: self.on_rename())
        self.remove_btn = QtWidgets.QPushButton("無い参照を削除")
        self.remove_btn.setToolTip("モデルに無いシェイプ・ボーンへの参照をデータから消す（大小文字だけの違いは改名で直すので残す）")
        self.remove_btn.clicked.connect(lambda *_: self.on_remove())
        self.rebake_btn = QtWidgets.QPushButton("全部ベイクし直す")
        self.rebake_btn.setToolTip("全レイヤー・全点を焼き直す（未ベイク・変更ありの解消）。編集状態は先に抜けます")
        self.rebake_btn.clicked.connect(lambda *_: self.on_rebake())
        row.addWidget(self.rename_btn)
        row.addWidget(self.remove_btn)
        row.addWidget(self.rebake_btn)
        row.addStretch(1)
        v.addLayout(row)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        self.refresh()

    # ============================================================ 表示
    def show_status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(WARN_STYLE if error else DIM_STYLE)

    def refresh(self) -> None:
        s = self.session
        if s.presenters is None:
            return
        view = s.validation.view()
        self._updating = True
        try:
            if view.ran:
                self.summary.setText(view.summary)
            else:
                self.summary.setText("まだ検証していません。[検証] を押してください")
            self.stale_label.setVisible(bool(view.ran and s.validation.stale))
            # 候補の選び: 今の提案だけ引き継ぐ（無くなったものは捨てる）
            old = self.choices
            self.choices = {(p.kind, p.old): old.get((p.kind, p.old), p.new) for p in view.proposals}
            self._fill_tree(view)
            self.rename_btn.setEnabled(any(self.choices.get(k) and self.choices[k] != k[1] for k in self.choices))
            has_missing = any(r.fix == "remove" for g in view.groups for r in g.issues)
            self.remove_btn.setEnabled(view.can_remove_missing and has_missing)
        finally:
            self._updating = False

    def _show_detail(self, item) -> None:
        issue = self._issue_of_item.get(id(item)) if item is not None else None
        self.detail.setText(issue.message if issue is not None else "")

    def _fill_tree(self, view) -> None:
        self.detail.setText("")
        self.tree.clear()
        self._combos.clear()
        self._issue_of_item.clear()
        for g in view.groups:
            top = QtWidgets.QTreeWidgetItem([f"{g.label}（{len(g.issues)}）", "", ""])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setForeground(0, self._color(g.severity))
            self.tree.addTopLevelItem(top)
            for r in g.issues:
                where = ""
                if r.layer is not None:
                    where = r.layer_name or f"L{r.layer}"
                    if r.row is not None and r.col is not None:
                        where += f" R{r.row} C{r.col}"
                it = QtWidgets.QTreeWidgetItem([where, r.message, ""])
                it.setToolTip(COL_MESSAGE, r.message)
                if r.can_select_point:
                    it.setToolTip(COL_WHERE, "ダブルクリックでこの点へ移ります")
                top.addChild(it)
                self._issue_of_item[id(it)] = r
                if r.fix == "rename" and (r.kind, r.name) in self.choices:
                    combo = QtWidgets.QComboBox()
                    cands = list(r.candidates)
                    cur = self.choices[(r.kind, r.name)]
                    for c in cands:
                        combo.addItem(c, c)
                    if cur and cur not in cands:
                        combo.addItem(cur, cur)
                    combo.addItem(NO_RENAME, "")
                    combo.setCurrentIndex(max(combo.findData(cur), 0) if cur else combo.count() - 1)
                    key = (r.kind, r.name)
                    combo.activated.connect(lambda *_, k=key, c=combo: self.on_candidate_chosen(k, c))
                    self.tree.setItemWidget(it, COL_CANDIDATE, combo)
                    self._combos.append((key, combo))
                elif r.fix == "remove":
                    it.setText(COL_CANDIDATE, "（無い参照 → 削除できます）")
            top.setExpanded(True)

    @staticmethod
    def _color(severity: str):
        return QtGui.QBrush(QtGui.QColor(SEVERITY_COLOR.get(severity, "#9aa6b8")))

    # ============================================================ ダイアログ（差し替えられる）
    def ask_confirm(self, text: str, title: str = "検証") -> bool:
        return ask_yes_no(self, text, title)

    def ask_move_choice(self, text: str) -> str:
        """未保存のポーズ編集があるまま別の点・レイヤーへ移るとき: 保存 / 破棄 / キャンセル。"""
        return ask_save_discard_cancel(self, text, "点の切り替え")

    # ============================================================ 操作
    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self.show_status(str(exc), error=True)
            lifecycle.report_error("検証タブの操作でエラー", traceback.format_exc())
            return None

    def on_run(self) -> None:
        issues = self._call(self.session.validate)
        if issues is None:
            return
        self.refresh()
        self.show_status("")

    def on_candidate_chosen(self, key: tuple[str, str], combo: QtWidgets.QComboBox) -> None:
        if self._updating:
            return
        self.choices[key] = combo.currentData() or ""
        for k, c in self._combos:  # 同じ名前の行は同じ選びにそろえる
            if k == key and c is not combo:
                c.blockSignals(True)
                c.setCurrentIndex(max(c.findData(self.choices[key]), 0))
                c.blockSignals(False)
        self.rename_btn.setEnabled(any(v and v != k[1] for k, v in self.choices.items()))

    def on_rename(self) -> None:
        res = self._call(self.session.apply_renames, dict(self.choices))
        if res is None:
            return
        if not res.ok:
            self.show_status(res.message, error=True)
            return
        self._call(self.session.validate)  # 直したあとの状態で取り直す
        self.refresh()
        self.show_status(f"{res.message}。検証し直しました（残り {self.session.validation.view().total} 件）")

    def on_remove(self) -> None:
        n_missing = sum(1 for g in self.session.validation.view().groups for r in g.issues if r.fix == "remove")
        if not self.ask_confirm(
            f"モデルに無いシェイプ・ボーンへの参照（{n_missing} 件）を、データから消します。\n（元に戻すには、ツールの Undo を使います）\nよろしいですか？",
            "無い参照を削除",
        ):
            return
        res = self._call(self.session.remove_missing)
        if res is None:
            return
        if not res.ok:
            self.show_status(res.message, error=True)
            return
        self._call(self.session.validate)
        self.refresh()
        self.show_status(f"{res.message}。検証し直しました（残り {self.session.validation.view().total} 件）")

    def on_rebake(self) -> None:
        if not self.ask_confirm("全レイヤー・全点をベイクし直します。時間がかかることがあります。よろしいですか？", "ベイク"):
            return
        rep = self._call(self.session.bake_all)
        if rep is None:
            return
        self._call(self.session.validate)
        self.refresh()
        self.show_status(rep.summary() + ("。注意: " + " / ".join(rep.warnings) if rep.warnings else ""))

    def on_double_click(self, item: QtWidgets.QTreeWidgetItem) -> None:
        issue = self._issue_of_item.get(id(item))
        if issue is None or not getattr(issue, "can_select_point", False):
            return
        self.go_to_issue(issue)

    def go_to_issue(self, issue) -> bool:
        """問題の点へ移る（レイヤーが違えばそのレイヤーを選んでから）。移れたら True。"""
        s = self.session
        try:
            if issue.layer != s.layers.view().active:
                r = s.set_active_layer(issue.layer)
                if r.status == SELECT_NEEDS_CONFIRM:
                    choice = self.ask_move_choice("編集中のポーズに未保存の変更があります。別のレイヤーへ移る前に保存しますか？")
                    r = s.set_active_layer(issue.layer, choice=choice)
                if r.status != SELECT_SELECTED:
                    self.show_status(r.message or "移動を取りやめました")
                    return False
            r = s.select_point(issue.row, issue.col)
            if r.status == SELECT_NEEDS_CONFIRM:
                choice = self.ask_move_choice("編集中のポーズに未保存の変更があります。別の点へ移る前に保存しますか？")
                if choice == CONFIRM_CANCEL:
                    r = s.select_point(issue.row, issue.col, choice=CONFIRM_CANCEL)
                    self.show_status("移動を取りやめました")
                    return False
                r = s.select_point(issue.row, issue.col, choice=choice)
        except Exception as exc:  # noqa: BLE001
            self.show_status(str(exc), error=True)
            lifecycle.report_error("検証タブからの点の移動でエラー", traceback.format_exc())
            return False
        ok = r.status in (SELECT_SELECTED, "same_point")
        self.show_status(f"R{issue.row} C{issue.col} へ移りました（編集状態になっています）" if ok else (r.message or "移動できませんでした"), error=not ok)
        return ok

    def detach(self) -> None:
        pass
