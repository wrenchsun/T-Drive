"""機能タブ（docs/11 §4、4-3 / 4-12 / 4-13）: 機能 × 全体・部位 の一覧表。

- 「全体」列: キャラクター全体のオン/オフ（既定）
- 部位の列: その部位で**実際に有効か**。押すとその部位だけ切り替わる。全体と同じにすると上書きは自動で消える
  （上書きしたマスは色付き。部位の中でマテリアルが違うときは中間の状態）。行末の ↺ で全体どおりに戻す
- キャラクター単位の機能（接地影・画面上の線など）は部位ごとには切り替えられない（「—」）

オフの機能は効果なしの値でプレビュー・出力され、Unity では機能の組み合わせごとのシェーダーから取り除かれる。
保存されている値は消えない（オンに戻すと復活する）。
（2026-09-29 に「部位を選んで 全体に従う / オン / オフ」から変更: どこで有効なのか分かりにくかったため）
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import features, look, roles, session

IMPL_LABELS = {features.SH: "シェーダー", features.ME: "メッシュ", features.CO: "コンポーネント", features.RF: "Renderer Feature"}
PREVIEW_LABELS = {"full": "○ Maya でも同じ見た目", "partial": "△ Maya では簡易表示（Unity で最終確認）", "none": "× Maya では確認できない"}
OVERRIDE_ON = "#2f5a2f"   # この部位だけオン
OVERRIDE_OFF = "#6a4a1f"  # この部位だけオフ


class FeaturesTab(QtWidgets.QWidget):
    def __init__(self, s: session.Session) -> None:
        super().__init__()
        self.session = s
        self._parts: list[str] = []
        v = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "使う機能にチェックを入れます。<b>全体</b> = キャラクター全体の設定、<b>部位の列</b> = その部位で実際に使うか"
            "（押すとその部位だけ切り替わり、色が付きます。行末の ↺ で全体どおりに戻す）。"
            "オフの機能は効果なしで表示・出力され、Unity のシェーダーから取り除かれて軽くなります。調整値は消えません。"
        )
        note.setWordWrap(True)
        v.addWidget(note)

        top = QtWidgets.QHBoxLayout()
        unused = QtWidgets.QPushButton("使っていない機能をオフにする")
        unused.setToolTip("値がすべて効果なし（既定値）の機能を全体でまとめてオフにする。Ctrl+Z で戻せる")
        unused.clicked.connect(self._disable_unused)
        top.addWidget(unused)
        top.addStretch(1)
        legend = QtWidgets.QLabel(
            f"<span style='background:{OVERRIDE_ON}'>&nbsp;この部位だけオン&nbsp;</span> "
            f"<span style='background:{OVERRIDE_OFF}'>&nbsp;この部位だけオフ&nbsp;</span>"
        )
        top.addWidget(legend)
        self.summary = QtWidgets.QLabel()
        top.addWidget(self.summary)
        v.addLayout(top)

        self.table = QtWidgets.QTableWidget(len(features.FEATURES), 2)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        v.addWidget(self.table, 1)

    # -------------------------------------------------------------- 表示
    def refresh(self) -> None:
        lk = self.session.look
        self.setEnabled(lk is not None)
        if lk is None:
            self.summary.setText("")
            self.table.setColumnCount(2)
            return
        parts = sorted(lk["parts"])
        if parts != self._parts:
            self._parts = parts
        self._build(lk)

    def _build(self, lk: dict) -> None:
        parts = self._parts
        t = self.table
        t.clear()
        cols = ["機能", "全体", *parts, "", "使用中"]
        t.setColumnCount(len(cols))
        t.setRowCount(len(features.FEATURES))
        t.setHorizontalHeaderLabels(cols)
        for j, part in enumerate(parts, start=2):
            role = lk["parts"][part]["role"]
            t.horizontalHeaderItem(j).setToolTip(f"{part}（{roles.ROLE_PRESETS.get(role, {}).get('label', role)}）: {', '.join(lk['parts'][part]['materials'])}")
        reset_col, used_col = len(cols) - 2, len(cols) - 1
        used = look.used_features(lk)
        n_on = 0
        for row, f in enumerate(features.FEATURES):
            whole = look.enabled(lk, f.id)
            n_on += whole
            name = QtWidgets.QTableWidgetItem(f.label + ("（必須）" if f.required else ""))
            name.setToolTip(_contents(f))
            t.setItem(row, 0, name)
            t.setCellWidget(row, 1, self._check(whole, enabled=not f.required,
                                                on_click=lambda *_, fid=f.id: self._toggle_whole(fid),
                                                tip="キャラクター全体（部位の列の既定）"))
            any_override = False
            for j, part in enumerate(parts, start=2):
                if not f.material_scope:
                    item = QtWidgets.QTableWidgetItem("—")
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
                    item.setForeground(QtGui.QBrush(QtGui.QColor("#777")))
                    item.setToolTip("必須の機能" if f.required else "キャラクター単位の機能（部位ごとには切り替えられない。全体の列で）")
                    t.setItem(row, j, item)
                    continue
                overrides, effective = self.session.material_feature_state(part, f.id)
                any_override |= any(o is not None for o in overrides)
                state = (QtCore.Qt.Checked if all(effective) else
                         QtCore.Qt.Unchecked if not any(effective) else QtCore.Qt.PartiallyChecked)
                # 色は「全体と実際に違う」マスだけ（全体と同じ上書きは見た目に影響しないので色を付けない。↺ で消せる）
                color = ""
                if any(e != whole for e in effective):
                    color = OVERRIDE_ON if any(effective) else OVERRIDE_OFF
                t.setCellWidget(row, j, self._check(
                    state, enabled=True, color=color, tip=self._tip(lk, part, f, overrides, effective, whole),
                    on_click=lambda *_, fid=f.id, p=part: self._toggle_part(fid, p)))
            if f.material_scope:
                btn = QtWidgets.QToolButton()
                btn.setText("↺")
                btn.setToolTip("この機能の部位ごとの設定を消して、全体どおりに戻す")
                btn.setEnabled(any_override)
                btn.clicked.connect(lambda *_, fid=f.id: self._clear(fid))
                t.setCellWidget(row, reset_col, btn)
            u = QtWidgets.QTableWidgetItem()
            u.setTextAlignment(QtCore.Qt.AlignCenter)
            if f.id in used and f.id not in look.enabled_features(lk):
                u.setText("値あり")
                u.setForeground(QtGui.QBrush(QtGui.QColor("#f0a040")))
                u.setToolTip("調整値が保存されているがオフなので効果なし。オンにすると復活する")
            elif f.id in used:
                u.setText("●")
                u.setToolTip("値が既定値と違う（= 使っている）")
            t.setItem(row, used_col, u)
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        n_ov = sum(1 for m in lk["materials"].values() if m.get(look.FEATURE_OVERRIDES))
        self.summary.setText(f"  全体でオン {n_on} / {len(features.FEATURES)}" + (f"・部位ごとの設定あり（マテリアル {n_ov}）" if n_ov else ""))

    def _check(self, state, enabled: bool, on_click, tip: str, color: str = "") -> QtWidgets.QWidget:
        cell = QtWidgets.QWidget()
        if color:
            cell.setAutoFillBackground(True)
            cell.setStyleSheet(f"background-color: {color};")
        h = QtWidgets.QHBoxLayout(cell)
        h.setContentsMargins(0, 0, 0, 0)
        h.setAlignment(QtCore.Qt.AlignCenter)
        c = QtWidgets.QCheckBox()
        if isinstance(state, bool):
            state = QtCore.Qt.Checked if state else QtCore.Qt.Unchecked
        c.setTristate(state == QtCore.Qt.PartiallyChecked)
        c.setCheckState(state)
        c.setEnabled(enabled)
        c.setToolTip(tip)
        cell.setToolTip(tip)
        # PySide6 は「必須引数 + 既定値付き引数」の lambda を引数なしで呼ぶ → on_click は *_ で受ける（CLAUDE.md）
        c.clicked.connect(on_click)
        h.addWidget(c)
        return cell

    @staticmethod
    def _tip(lk: dict, part: str, f: features.Feature, overrides, effective, whole: bool) -> str:
        mats = lk["parts"][part]["materials"]
        if len(set(effective)) > 1:
            lines = [f"{part}: マテリアルで違う"] + [
                f"  {m}: {'オン' if e else 'オフ'}{'（この部位だけ）' if o is not None else '（全体どおり）'}"
                for m, o, e in zip(mats, overrides, effective)]
        elif any(o is not None for o in overrides) and effective[0] != whole:
            lines = [f"{part}: {'オン' if effective[0] else 'オフ'}（この部位だけ。全体は{'オン' if whole else 'オフ'}）"]
        elif any(o is not None for o in overrides):
            lines = [f"{part}: {'オン' if effective[0] else 'オフ'}（部位ごとの設定あり。今は全体と同じ。全体を変えてもこの部位はこのまま / ↺ で消す）"]
        else:
            lines = [f"{part}: {'オン' if effective[0] else 'オフ'}（全体どおり）"]
        if f.id == "selfShadow" and any(effective):
            spec = [lk["materials"][m]["specific"] for m in mats]
            if all(sp.get("_ToonReceiveShadow", 1.0) <= 0.0 for sp in spec):
                lines.append("※ 受ける量が 0 なので、この部位に影は出ません（ルックタブ › 影で変更）")
            if all(sp.get("_ToonCastShadow", 1.0) < 0.5 for sp in spec):
                lines.append("※ 「影を落とす」がオフなので、この部位は他に影を落としません")
        lines.append("押すと切り替え（全体と同じにすると部位ごとの設定は消える）")
        return "\n".join(lines)

    # -------------------------------------------------------------- 操作
    def _run(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "T-Drive Toon", str(exc))
            self.refresh()

    def _toggle_whole(self, feature_id: str) -> None:
        self._run(self.session.set_feature, feature_id, not look.enabled(self.session.look, feature_id))

    def _toggle_part(self, feature_id: str, part: str) -> None:
        _overrides, effective = self.session.material_feature_state(part, feature_id)
        # 全部オンならオフへ、それ以外（オフ・混在）はオンへ
        self._run(self.session.set_part_feature, part, feature_id, not all(effective))

    def _clear(self, feature_id: str) -> None:
        self._run(self.session.clear_feature_overrides, feature_id)

    def _disable_unused(self) -> None:
        off = self.session.disable_unused_features()
        labels = [features.BY_ID[fid].label for fid in off]
        QtWidgets.QMessageBox.information(
            self, "T-Drive Toon", "オフにしました:\n" + "\n".join(labels) if labels else "使っていない機能はありません"
        )


def _contents(f: features.Feature) -> str:
    items = list(f.params) + [f"common.{c}" for c in f.common] + [f"characterSettings.{s}" for s in f.settings]
    return "\n".join([
        "属するもの: " + (", ".join(items) if items else "（頂点カラーなどメッシュのデータ）"),
        "Unity での実現: " + " + ".join(IMPL_LABELS[i] for i in f.impl),
        "Maya: " + PREVIEW_LABELS[f.maya_preview],
        "部位ごとに切り替え: " + ("できる" if f.material_scope else "できない（キャラクター単位）"),
    ])
