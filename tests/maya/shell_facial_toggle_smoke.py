"""Toon ヘッダーの「顔の補正」チェック（tdrive.services 経由で FacialController の補正を切り替える）の GUI スモーク（mayapy・画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/shell_facial_toggle_smoke.py

手順: QT_QPA_PLATFORM=offscreen → QApplication → maya.standalone.initialize。ウィジェットは直接作る。ファイルはすべて一時フォルダ。
スクリーンショット: 環境変数 TDRIVE_UI_SHOT_DIR があればそこへ toon_header.png を書く（合成データ。利用者のパスは入れない）。
"""

from __future__ import annotations

import os
import runpy
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6 import QtGui, QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])  # maya.standalone より先に作る


def _load_ui_font() -> None:
    for name in ("meiryo.ttc", "YuGothM.ttc", "msgothic.ttc"):
        f = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name
        if f.exists():
            fid = QtGui.QFontDatabase.addApplicationFont(str(f))
            fams = QtGui.QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams:
                APP.setFont(QtGui.QFont(fams[0], 9))
                return


_load_ui_font()

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
INFO: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def pump() -> None:
    APP.processEvents()


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_shell_ft_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp: Path) -> None:
    from maya import cmds

    import facial_fixture
    from tdrive import project, services
    from tdrive_facial import session as S
    from tdrive_facial import ui as fui  # noqa: F401  読み込むとサービスに登録される
    from tdrive_facial.core import autofill, fcpose_io
    from tdrive_facial.ui_preview import PreviewGroup
    from tdrive_toon import session as toon_session
    from tdrive_toon import ui as toon_ui

    project.set_root(tmp)
    facial_fixture.build_mini_head()
    s = S.current()
    s.close()
    doc0 = facial_fixture.make_doc()
    autofill.generate_from_keys(doc0)
    p = tmp / "facial" / "mini" / "mini.fcpose.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    fcpose_io.save(doc0, p)

    header = toon_ui.HeaderBar(toon_session.current())
    cb = header.facial
    fh = fui.HeaderBar(s)  # FacialController 側のヘッダー（パネルと同じく、軽い通知で更新する）
    s.state_listeners.append(fh.refresh)
    fh.refresh()
    fcb = fh.facial
    NO_EDIT = "編集していません（シーンは元の顔のままです）"

    # ---- データ無し・プレビュー無し
    check("無し: チェックは無効・オフ", not cb.isEnabled() and not cb.isChecked())
    check("無し: ツールチップは「プレビューが無い」の案内", "プレビューが無いので" in cb.toolTip() and "プレビューを作る" in cb.toolTip(), cb.toolTip())
    check("無し: 提供元が登録されている", services.has_provider(services.FACIAL_CORRECTION))
    check("無し: FC ヘッダーのチェックも無効・オフ・文言", not fcb.isEnabled() and not fcb.isChecked() and fh.edit_state.text() == NO_EDIT, fh.edit_state.text())

    # ---- データを開いて、焼いて、プレビューを作る
    s.open(p)
    s.bake_all()
    check("データだけ（プレビュー無し）: まだ無効・オフ", not cb.isEnabled() and not cb.isChecked() and not fcb.isEnabled() and fh.edit_state.text() == NO_EDIT)
    cam = cmds.camera(name="ftcam")[0]
    head = tuple(cmds.xform("head", query=True, worldSpace=True, translation=True))
    cmds.setAttr(cam + ".translate", head[0] + 30, head[1] + 10, head[2] + 55)  # 斜めから見る
    cmds.setAttr(cam + ".rotate", -10, 30, 0)
    cam_long = cmds.ls(cam, long=True)[0]

    group = PreviewGroup(s)
    s.state_listeners.append(group.on_state)  # グリッドタブと同じつなぎ方
    s.preview_build(cam_long)
    pump()
    check("プレビュー作成: 有効・オン（通知だけで追従）", cb.isEnabled() and cb.isChecked(), f"{cb.isEnabled()} {cb.isChecked()}")
    check("プレビュー作成: FC ヘッダー: 有効・オン・「プレビュー中 …」", fcb.isEnabled() and fcb.isChecked() and fh.edit_state.text().startswith("プレビュー中 = カメラの角度に合わせて顔を補正しています"), fh.edit_state.text())
    check("プレビュー作成: ツールチップは「オフにすると…同じ」", "オフにすると" in cb.toolTip() and "補正あり / 補正なし" in cb.toolTip(), cb.toolTip())
    w_on = s.preview_weights()
    check("前提: 補正オンのとき FC_ の重みが 0 でないものがある", any(abs(v) > 1e-6 for v in w_on.values()), str(w_on))
    check("FC ボタン: 補正あり（オン）", group.btn_ab.isChecked() and group.btn_ab.text() == "補正あり")

    # ---- Toon のチェックを外す → rig の enable がオフ・重み 0・FC のボタンも「補正なし」
    cb.click()
    pump()
    rig = s.preview_rig_node()
    w_off = s.preview_weights()
    check("外す: rig の enable がオフ", cmds.getAttr(rig + ".enable") is False and not s.preview_is_enabled())
    check("外す: FC_ の重みがすべて 0", all(abs(v) < 1e-9 for v in w_off.values()), str(w_off))
    check("外す: チェックはオフ・有効のまま", not cb.isChecked() and cb.isEnabled())
    check("外す: FC の「補正あり / 補正なし」が補正なしになる", not group.btn_ab.isChecked() and group.btn_ab.text() == "補正なし")
    check("外す: FC ヘッダーは「顔の補正はオフです（元の顔）」・チェックオフ", fh.edit_state.text() == "顔の補正はオフです（元の顔）" and not fcb.isChecked() and fcb.isEnabled(), fh.edit_state.text())

    # ---- FC 側のボタンで戻す → Toon のチェックがオンに
    group.btn_ab.click()
    pump()
    check("FC ボタンでオン: Toon のチェックもオン", cb.isChecked() and s.preview_is_enabled())
    check("オン: 重みが戻る", any(abs(v) > 1e-6 for v in s.preview_weights().values()))
    group.btn_ab.click()
    pump()
    check("FC ボタンでオフ: Toon のチェックもオフ", not cb.isChecked())
    cb.click()
    pump()
    check("Toon でオン: FC のボタンもオン", cb.isChecked() and group.btn_ab.isChecked() and group.btn_ab.text() == "補正あり" and fcb.isChecked())
    fcb.click()  # FC ヘッダーのチェックでオフ → 他の 2 つも追従
    pump()
    check("FC ヘッダーでオフ: Toon・FC ボタンもオフ", not cb.isChecked() and not group.btn_ab.isChecked() and not s.preview_is_enabled() and fh.edit_state.text() == "顔の補正はオフです（元の顔）")
    fcb.click()
    pump()
    check("FC ヘッダーでオン: Toon・FC ボタンもオン", cb.isChecked() and group.btn_ab.isChecked() and s.preview_is_enabled() and fh.edit_state.text().startswith("プレビュー中"))
    shot_facial(fh)

    # ---- 外（Channel Box など）から enable を変えた → 通知は無いが、更新（タブを開く・全体の更新）で追従
    cmds.setAttr(rig + ".enable", False)
    header.refresh()
    check("外から enable を変えても、全体の更新でチェックが追従する", not cb.isChecked())
    cmds.setAttr(rig + ".enable", True)
    header.showEvent(QtGui.QShowEvent())
    check("タブを開いたとき（showEvent）にも追従する", cb.isChecked())

    # ---- 書き換えられない（キー / ロック）
    cmds.setKeyframe(rig + ".enable", time=1, value=1)
    cb.click()
    pump()
    check("キーあり: 状態メッセージが出る", not header.facial_status.isHidden() and "キー" in header.facial_status.text(), header.facial_status.text())
    check("キーあり: チェックは実際の状態（オン）のまま", cb.isChecked() and s.preview_is_enabled())
    cmds.cutKey(rig + ".enable", clear=True)
    cmds.setAttr(rig + ".enable", True)
    cmds.setAttr(rig + ".enable", lock=True)
    fcb.click()
    pump()
    check("ロック: FC ヘッダーにも理由が出る", not fh.facial_msg.isHidden() and "ロック" in fh.facial_msg.text() and fcb.isChecked(), fh.facial_msg.text())
    cb.click()
    pump()
    check("ロック: 状態メッセージが出る・状態は変わらない", "ロック" in header.facial_status.text() and cb.isChecked() and s.preview_is_enabled(), header.facial_status.text())
    cmds.setAttr(rig + ".enable", lock=False)
    cb.click()
    pump()
    check("外せるようになる: メッセージが消え、オフになる", header.facial_status.isHidden() and not cb.isChecked())
    cb.click()
    pump()
    check("戻す: オン", cb.isChecked() and s.preview_is_enabled())

    # ---- 編集中は無効
    s.begin_edit()
    pump()
    check("編集中: チェックは無効", not cb.isEnabled())
    check("編集中: FC ヘッダーのチェックも無効・「編集中」の文言", not fcb.isEnabled() and "編集中" in fh.edit_state.text() and "基準姿勢" in fh.edit_state.text(), fh.edit_state.text())
    check("編集中: ツールチップは「編集中は止まっている」", "編集" in cb.toolTip() and "止まっています" in cb.toolTip(), cb.toolTip())
    s.end_edit()
    pump()
    check("編集を終えると有効に戻る", cb.isEnabled() and "オフにすると" in cb.toolTip() and fcb.isEnabled() and fh.edit_state.text().startswith("プレビュー中"))

    # ---- プレビューを消す / 作り直す
    s.preview_delete()
    pump()
    check("プレビューを消す: 無効・オフ・「プレビューが無い」の案内", not cb.isEnabled() and not cb.isChecked() and "プレビューが無いので" in cb.toolTip() and not fcb.isEnabled() and fh.edit_state.text() == NO_EDIT)
    s.preview_build(cam_long)
    pump()
    check("作り直す: 有効・オン", cb.isEnabled() and cb.isChecked())
    s.preview_set_enabled(False)
    pump()
    s.preview_build(cam_long)  # 作り直しても enable は rig の値のまま（オフが保たれる）
    pump()
    check("作り直しても、オフが保たれチェックもオフ", not cb.isChecked() and cb.isEnabled() and not s.preview_is_enabled())

    # ---- 距離で決まるレイヤー: スライダー横の「距離で決まる（今 n cm）」がカメラに追従する（下の「使っている角度」の行と同じ値）
    s.set_layer_weight_source(1, "distance", start=40.0, end=120.0, w_from=0.0, w_to=1.0)  # プレビューは作り直される
    s.preview_set_enabled(False)
    group.refresh()
    check("距離: 距離レイヤーの行ができる", len(group._distance_labels) == 1, str(len(group._distance_labels)))
    rows = [group._distance_labels[0]]
    shown = []
    for dist in (60.0, 100.0, 35.0):
        cmds.setAttr(cam + ".translate", head[0], head[1], head[2] + dist)
        group.tick()  # カメラ追従のタイマーと同じ呼び出し（行は作り直さない）
        t_side = rows[0].text()
        t_line = group.angle_label.text()
        shown.append(t_side)
        check(f"距離 {dist:g} cm: スライダー横の表示が追従する", f"今 {dist:.0f} cm" in t_side, t_side)
        check(f"距離 {dist:g} cm: 下の角度の行と同じ距離（「距離」は 1 回だけ）", f"カメラの距離 {dist:.0f} cm" in t_line and t_line.count("距離") == 1, t_line)
        ro = s.preview_readout()
        va = s.view_angles(cam_long)
        check(f"距離 {dist:g} cm: Yaw / Pitch もカメラに追従（ユーザーが回したときと同じ transform 直書き）", f"Yaw {va[0]:.1f}° / Pitch {va[1]:.1f}°" in t_line and abs(ro["yaw"] - va[0]) < 0.06, f"{t_line} vs {va}")
    cmds.setAttr(cam + ".translate", head[0] + 50, head[1] + 10, head[2] + 50)  # 横へ回す（回し方も変える）
    cmds.setAttr(cam + ".rotate", -8, 45, 0)
    group.tick()
    va = s.view_angles(cam_long)
    check("横へ回す: Yaw が変わり、行に出る", abs(va[0]) > 20 and f"Yaw {va[0]:.1f}°" in group.angle_label.text(), f"{group.angle_label.text()} vs {va}")
    check("距離: 行のウィジェットは作り直されない（tick 後も同じラベル）", group._distance_labels[0] is rows[0] and len(set(shown)) == 3)

    # ---- ツールのリロード（本物のスクリプト）後も、両方が同じ状態で追従する（オフを持ち越す）
    runpy.run_path(str(REPO / "maya" / "mcp_scripts" / "reload_tdrive.py"))
    project.set_root(tmp)
    from tdrive import services as services2
    from tdrive_toon import ui as toon_ui2  # リロード後の新しいモジュール

    check("リロード: サービスの登録表は新しいモジュール", services2 is not services)
    header2 = toon_ui2.HeaderBar(toon_ui2.session.current())  # 先に Toon を作る（提供元はまだ）
    check("リロード直後（提供元より先に Toon）: 無効", not header2.facial.isEnabled())
    from tdrive_facial import session as S2
    from tdrive_facial import ui as fui2  # noqa: F401  読み込むと登録され、通知で追従する
    from tdrive_facial.ui_preview import PreviewGroup as PG2

    s2 = S2.current()
    fh2 = fui2.HeaderBar(s2)
    s2.state_listeners.append(fh2.refresh)
    fh2.refresh()
    group2 = PG2(s2)
    s2.state_listeners.append(group2.on_state)
    pump()
    check("リロード後: Toon のチェックが追従（オフ・有効）", header2.facial.isEnabled() and not header2.facial.isChecked() and s2.preview_exists(), f"{s2.presenters is not None} {services2.state(services2.FACIAL_CORRECTION)} {header2.facial.toolTip()}")
    check("リロード後: FC のボタンも補正なし", not group2.btn_ab.isChecked())
    header2.facial.click()
    pump()
    check("リロード後: Toon でオン → FC のボタンもオン・rig もオン", group2.btn_ab.isChecked() and s2.preview_is_enabled() and header2.facial.isChecked())
    group2.btn_ab.click()
    pump()
    check("リロード後: FC でオフ → Toon もオフ", not header2.facial.isChecked() and not s2.preview_is_enabled())
    check(
        "リロード: 古い画面の購読は新しい表に残らない",
        all(getattr(fn, "__self__", None) is not header for fn in services2._subscribers.get(services2.FACIAL_CORRECTION, [])),
    )
    header2.facial.click()
    pump()

    check("リロード後: FC ヘッダーも同じ状態（オン）", fh2.facial.isChecked() and fh2.edit_state.text().startswith("プレビュー中"), fh2.edit_state.text())

    # ---- 幅（スクリーンショットと、狭い幅でのはみ出し確認）
    shot_and_width(header2)

    # ---- tdrive_facial の読み込みが失敗しても、Toon のヘッダーは作れる（チェックは無効）
    services2._clear()
    for name in [m for m in sys.modules if m == "tdrive_facial" or m.startswith("tdrive_facial.")]:
        del sys.modules[name]
    sys.modules["tdrive_facial"] = None  # import が ImportError になる
    from tdrive import shell

    shell._tools.clear()
    ids = [t.id for t in shell.tools()]
    check("facial 読み込み失敗: 殻は toon も facial（理由の表示）も並べる", ids == ["toon", "facial"], str(ids))
    panel = shell.get_tool("toon").build_widget()
    check("facial 読み込み失敗: Toon のパネル（ヘッダー）は作れる", panel is not None and panel.header is not None)
    fc = panel.header.facial
    check("facial 読み込み失敗: チェックは無効・オフ・理由のツールチップ", not fc.isEnabled() and not fc.isChecked() and "読み込めていない" in fc.toolTip(), fc.toolTip())
    panel.detach()


def shot_facial(fh) -> None:
    """FacialController のヘッダーを 480 px 幅で保存し、狭い幅（400 px）でも収まることを確かめる。"""
    holder = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(holder)
    lay.addWidget(fh)
    holder.resize(480, 150)
    pump()
    check("FC ヘッダー: 400px まで縮められる", fh.minimumSizeHint().width() <= 400, str(fh.minimumSizeHint().width()))
    out = os.environ.get("TDRIVE_UI_SHOT_DIR")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        holder.grab().save(str(Path(out) / "facial_header.png"))
    lay.removeWidget(fh)
    fh.setParent(None)


def shot_and_width(header) -> None:
    """幅 750 px の見た目を保存し、チェックを足したことでヘッダーの最小幅がどれだけ増えたかを測る。"""
    holder = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(holder)
    lay.addWidget(header)
    header.label.setText("プロジェクト: デモ\nLook: looks/demo/look.json   demo v0.1.0")  # 合成の表示（利用者のパスを入れない）
    header.toon.setEnabled(True)
    header.original.setEnabled(True)
    header.toon.blockSignals(True)  # 見た目だけ（切り替えの処理は走らせない。エラーのダイアログで止まる）
    header.toon.setChecked(True)
    header.toon.blockSignals(False)
    holder.resize(750, 110)
    pump()
    with_cb = header.minimumSizeHint().width()
    header.facial.setVisible(False)
    without_cb = header.minimumSizeHint().width()
    header.facial.setVisible(True)
    INFO.append(f"ヘッダーの最小幅: チェックあり {with_cb}px / 無し {without_cb}px（増分 {with_cb - without_cb}px）")
    out = os.environ.get("TDRIVE_UI_SHOT_DIR")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        pump()
        holder.grab().save(str(Path(out) / "toon_header.png"))
        holder.resize(with_cb + 24, 120)
        pump()
        holder.grab().save(str(Path(out) / "toon_header_min.png"))
    check("ヘッダーは 750px に収まる", with_cb + 24 <= 750, f"{with_cb}")
    header.setParent(None)


if __name__ == "__main__":
    maya.standalone.initialize(name="python")
    try:
        run()
    except Exception:
        RESULTS.append(("例外", False, traceback.format_exc()))
    finally:
        failed = [r for r in RESULTS if not r[1]]
        for name, ok, detail in RESULTS:
            print(f"SMOKE {'PASS' if ok else 'FAIL'} {name}" + (f"\n    {detail}" if not ok and detail else ""))
        for line in INFO:
            print(f"SMOKE INFO {line}")
        print(f"SMOKE RESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
        sys.stdout.flush()
        maya.standalone.uninitialize()
        os._exit(1 if failed else 0)
