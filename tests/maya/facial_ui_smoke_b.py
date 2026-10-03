"""FacialController のグリッドタブ・ポーズタブ（tdrive_facial.ui_grid / ui_pose）の画面ありスモーク（mayapy で実行）。

  set QT_QPA_PLATFORM=offscreen
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_ui_smoke_b.py

画面なし（offscreen）でウィジェットを直接作り、マウスのクリック・スロット・QAction.trigger() で操作する（session を直接つつかない）。
合成の小さな頭（tests/maya/facial_fixture.py）と一時フォルダだけを使う。スクリーンショットは環境変数 TDRIVE_UI_SHOT_DIR があればそこへ書く。
QApplication は maya.standalone.initialize() より先に作る。workspaceControl / show() は使わない。
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("MAYA_DISABLE_CER", "1")
REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def dark_style() -> None:
    """Maya の暗い UI に近い見た目にする（スクリーンショットで暗い背景での読みやすさを見るため）。"""
    for font in ("meiryo.ttc", "YuGothR.ttc", "msgothic.ttc"):  # offscreen はフォントを持たないので、日本語が出るよう Windows のフォントを足す
        fid = QtGui.QFontDatabase.addApplicationFont(f"C:/Windows/Fonts/{font}")
        fams = QtGui.QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if fams:
            app.setFont(QtGui.QFont(fams[0], 9))
            break
    app.setStyle("Fusion")
    p = QtGui.QPalette()
    for role, color in (
        (QtGui.QPalette.Window, "#444444"), (QtGui.QPalette.WindowText, "#d8d8d8"), (QtGui.QPalette.Base, "#2b2b2b"),
        (QtGui.QPalette.AlternateBase, "#3a3a3a"), (QtGui.QPalette.Text, "#d8d8d8"), (QtGui.QPalette.Button, "#5d5d5d"),
        (QtGui.QPalette.ButtonText, "#e0e0e0"), (QtGui.QPalette.Highlight, "#5285a6"), (QtGui.QPalette.HighlightedText, "#ffffff"),
        (QtGui.QPalette.ToolTipBase, "#333333"), (QtGui.QPalette.ToolTipText, "#ffffff"),
    ):
        p.setColor(role, QtGui.QColor(color))
    app.setPalette(p)


def run() -> None:
    from maya import cmds
    from maya.api import OpenMaya as om

    import facial_fixture
    from tdrive import project
    from tdrive_facial import scene
    from tdrive_facial import session as S
    from tdrive_facial import ui_grid, ui_pose
    from tdrive_facial.core import fcpose_io, naming
    from tdrive_facial.core.presenters import CONFIRM_CANCEL, CONFIRM_DISCARD, CONFIRM_SAVE

    dark_style()
    shot_dir = os.environ.get("TDRIVE_UI_SHOT_DIR")
    if shot_dir:
        Path(shot_dir).mkdir(parents=True, exist_ok=True)

    def shot(widget: QtWidgets.QWidget, name: str, w: int = 560, h: int = 900) -> None:
        if not shot_dir:
            return
        widget.resize(w, h)
        widget.layout().activate()
        pump()
        widget.grab().save(str(Path(shot_dir) / name))

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_fui_"))
    project.set_root(tmp)
    ids = facial_fixture.build_mini_head()
    face, bs = ids["face"], ids["bs"]
    s = S.current()

    # ------------------------------------------------------------ データ無しでも落ちない
    grid = ui_grid.GridTab(s)
    pose = ui_pose.PoseTab(s)

    def pump() -> None:
        app.processEvents()
        grid.flush()
        pose.flush()

    try:
        grid.refresh()
        pose.refresh()
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
        RESULTS.append(("データ無し refresh の例外", False, traceback.format_exc()))
    check("データ無し: refresh() が例外を出さない・使えない状態", ok and not grid.btn_bake_all.isEnabled() and not pose.btn_save.isEnabled())
    grid.start_tracking()
    check("データ無し: カメラの追従タイマーは動かない", not grid.timer.isActive())
    check("ウィジェットが session.listeners に登録される", grid._on_session_changed in s.listeners and pose._on_session_changed in s.listeners)

    # ------------------------------------------------------------ 準備
    s.new("mini")
    s.add_to_working_set(curves=["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"], bones=["eye_L", "eye_R"])
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")
    grid.resize(560, 900)
    pose.resize(560, 900)
    grid.layout().activate()
    pose.layout().activate()
    pump()
    grid.start_tracking()
    check("データあり: カメラの追従タイマーが動く", grid.timer.isActive())
    cv = grid.canvas
    view = grid.current_view()
    check("格子の図: 5 × 3 のビュー", view is not None and (view.cols, view.rows) == (5, 3))
    # 向き: 最大 Pitch の行が上、−Yaw が左
    top, bot = cv.cell_center(2, 0), cv.cell_center(0, 0)
    left, right = cv.cell_center(1, 0), cv.cell_center(1, 4)
    check("格子の向き: 高い Pitch の行が上（y が小さい）・−Yaw の列が左", top.y() < bot.y() and left.x() < right.x(), f"{top} {bot} {left} {right}")
    mid = cv.cell_center(1, 2)
    check("格子の向き: 正面（C2）・水平（R1）が中央", abs(mid.x() - (left.x() + right.x()) / 2) < 2 and abs(mid.y() - (top.y() + bot.y()) / 2) < 2)
    check("見出し: 角度の書式", ui_grid.fmt_angle(0) == "0°" and ui_grid.fmt_angle(22.5) == "+22.5°" and ui_grid.fmt_angle(-45) == "-45°")
    check("ツールチップ: 点の角度", "Yaw 45.0° / Pitch 0.0°" in grid.tooltip_for(1, 3), grid.tooltip_for(1, 3))
    check("ポーズタブ: 点を選ぶ前は案内の文", "グリッドで点を選んでください" in pose.header.text() and not pose.body.isEnabled())

    def click_cell(row: int, col: int, button=QtCore.Qt.LeftButton) -> None:
        cv._hover = None
        QTest.mouseClick(cv, button, QtCore.Qt.NoModifier, cv.cell_center(row, col))
        pump()

    def cell_color(row: int, col: int) -> QtGui.QColor:
        """セルの左上の内側（選択の枠・文字を避けた所）の色。"""
        cv._hover = None
        img = cv.grab().toImage()
        v = grid.current_view()
        r = cv.cell_rect(v, v.display_rows.index(row), col)
        return img.pixelColor(int(r.left() + 10), int(r.top() + 10))

    def near_color(c: QtGui.QColor, ref: QtGui.QColor, tol: int = 12) -> bool:
        return abs(c.red() - ref.red()) <= tol and abs(c.green() - ref.green()) <= tol and abs(c.blue() - ref.blue()) <= tol

    # ------------------------------------------------------------ クリック → 選択・編集開始・カメラ
    check("前提: まだ編集状態でない", not s.editing)
    click_cell(1, 3)
    check("セルのクリック: 選択され、編集を始める", s.ctx.selection == (1, 3) and s.editing, f"{s.ctx.selection} {s.editing}")
    yaw, pitch = s.view_angles()
    check("セルのクリック: カメラがその点の角度へ動く（Yaw 45° / Pitch 0°）", abs(yaw - 45) < 0.5 and abs(pitch) < 0.5, f"{yaw:.2f} {pitch:.2f}")
    check("セルのクリック: 状態の行で編集開始を伝える", "編集を始めました" in grid.status.text(), grid.status.text())
    check("セルのクリック: カメラの表示と赤い点が点の位置", "Yaw 45.0°" in grid.camera_label.text(), grid.camera_label.text())
    m = grid.current_marker()
    mp = cv.marker_position(grid.current_view(), m)
    cc = cv.cell_center(1, 3)
    check("赤い点: クリックした点の中心にある", m is not None and not m.clamped and math.hypot(mp.x() - cc.x(), mp.y() - cc.y()) < 4, f"{mp} {cc}")
    check("ポーズタブ: 見出しが選んだ点になる・編集できる", "R1, C3" in pose.header.text() and pose.body.isEnabled(), pose.header.text())
    check("ポーズタブ: 作業セットのシェイプ 4 本・ボーン 2 本の行", sorted(pose._curve_rows) == ["bs.brow_up", "bs.mouth_open", "bs.smile_L", "bs.smile_R"] and sorted(pose._bone_rows) == ["eye_L", "eye_R"], f"{sorted(pose._curve_rows)} {sorted(pose._bone_rows)}")

    # ------------------------------------------------------------ カメラの追従（cmds で動かす）
    cmds.setAttr("persp.translate", 60.0, 10.0, 30.0)
    ui_grid_before = grid.camera_label.text()
    grid._tick()
    yaw2, pitch2 = s.view_angles()
    m2 = grid.current_marker()
    exp = s.grid.locate(yaw2, pitch2)
    mp2 = cv.marker_position(grid.current_view(), m2)
    check("カメラ追従: cmds で動かすと赤い点が動く", grid.camera_label.text() != ui_grid_before and f"{yaw2:.1f}" in grid.camera_label.text(), grid.camera_label.text())
    check("カメラ追従: 位置が GridPresenter.locate の連続位置", abs(m2.col_pos - exp.col_pos) < 1e-9 and abs(mp2.x() - cv.marker_position(grid.current_view(), exp).x()) < 1e-6)
    painted: list[int] = []
    orig_update = cv.update
    cv.update = lambda *a: (painted.append(1), orig_update(*a))[1]  # type: ignore[method-assign]
    grid._tick()
    check("カメラ追従: 角度が変わらなければ描き直さない", not painted)
    cv.update = orig_update  # type: ignore[method-assign]
    cmds.setAttr("persp.translate", 1.0, 120.0, 15.0)  # 真上近く（Pitch の範囲外）
    grid._tick()
    m3 = grid.current_marker()
    check("カメラ追従: 範囲外は端へ寄せる・範囲外の表示", m3 is not None and m3.clamped and abs(m3.row_pos_clamped - 2) < 1e-9 and "範囲外" in grid.camera_label.text(), grid.camera_label.text())
    shot(grid, "grid_out_of_range.png")
    s.camera_to_point(1, 3)
    grid._tick()

    # ------------------------------------------------------------ シェイプのスライダー・保存
    row_mouth = pose._curve_rows["bs.mouth_open"]
    row_mouth.slider.setValue(600)
    pump()
    check("スライダー: ブレンドシェイプの重みがシーンで変わる", abs(cmds.getAttr(f"{bs}.mouth_open") - 0.6) < 1e-3, f"{cmds.getAttr(bs + '.mouth_open')}")
    check("スライダー: 数値欄が追従・未保存の印", abs(row_mouth.spin.value() - 0.6) < 1e-3 and "未保存" in pose.header.text())
    row_mouth.spin.setValue(0.8)
    check("数値欄: 打ち込むとシーンへ反映", abs(cmds.getAttr(f"{bs}.mouth_open") - 0.8) < 1e-3 and row_mouth.slider.value() == 800)
    row_mouth.reset_btn.click()
    check("0 に戻すボタン", abs(cmds.getAttr(f"{bs}.mouth_open")) < 1e-9 and row_mouth.spin.value() == 0.0)
    row_mouth.slider.setValue(600)
    pose._curve_rows["bs.smile_L"].slider.setValue(500)
    check("保存前: 点はまだキーでない", (1, 3) not in s.doc.layers[0].points)
    pose.btn_save.click()
    pump()
    pt = s.doc.layers[0].points.get((1, 3))
    check("保存: 点がキーになる", pt is not None and pt.is_key and abs(pt.pose.curves["bs.mouth_open"] - 0.6) < 1e-3 and "bs.smile_L" in pt.pose.curves)
    check("保存: 絵の状態が key（緑）・[未保存] が消える", grid.current_view().points[1 * 5 + 3].state == "key" and "未保存" not in pose.header.text())
    check("保存: 描かれる色が緑", near_color(cell_color(1, 3), ui_grid.FILL["key"]), f"{cell_color(1, 3).name()}")
    check("未保存の印: 空のセルは灰色", near_color(cell_color(0, 0), ui_grid.FILL["empty"]), f"{cell_color(0, 0).name()}")

    # ------------------------------------------------------------ 未保存で別の点へ（保存 / 破棄 / キャンセル）
    asked: list[str] = []

    def answer(choice: str):
        def fn(message: str = "") -> str:
            asked.append(choice)
            return choice

        return fn

    pose._curve_rows["bs.brow_up"].slider.setValue(700)
    check("前提: 編集中の値が未保存", s.pose.dirty)
    grid.ask_unsaved_choice = answer(CONFIRM_CANCEL)  # type: ignore[method-assign]
    click_cell(0, 0)
    check("確認: キャンセルで選択は変わらず編集も残る", s.ctx.selection == (1, 3) and s.pose.dirty and asked == [CONFIRM_CANCEL] and abs(cmds.getAttr(f"{bs}.brow_up") - 0.7) < 1e-3)
    grid.ask_unsaved_choice = answer(CONFIRM_DISCARD)  # type: ignore[method-assign]
    click_cell(0, 0)
    check("確認: 破棄で移る（保存済みは変わらない・シーンは基準へ）",
          s.ctx.selection == (0, 0) and "bs.brow_up" not in s.doc.layers[0].points[(1, 3)].pose.curves and abs(cmds.getAttr(f"{bs}.brow_up")) < 1e-9)
    pose._curve_rows["bs.brow_up"].slider.setValue(400)
    grid.ask_unsaved_choice = answer(CONFIRM_SAVE)  # type: ignore[method-assign]
    click_cell(2, 4)
    p00 = s.doc.layers[0].points.get((0, 0))
    check("確認: 保存して移る（元の点がキーになる）", s.ctx.selection == (2, 4) and p00 is not None and p00.is_key and abs(p00.pose.curves.get("bs.brow_up", 0) - 0.4) < 1e-3)
    check("確認: 保存して移ったと状態の行に出る", "保存して移りました" in grid.status.text(), grid.status.text())

    # ------------------------------------------------------------ 取り込む（Maya で動かした値）
    cmds.setAttr("eye_L.rotateY", 20)
    cmds.setAttr(f"{bs}.smile_R", 0.7)
    pose.btn_capture.click()
    pump()
    check("取り込む: ウェイトがシェイプ行に出る", abs(s.pose.curves.get("bs.smile_R", 0) - 0.7) < 1e-6 and abs(pose._curve_rows["bs.smile_R"].spin.value() - 0.7) < 1e-3, f"{s.pose.curves}")
    check("取り込む: 回したジョイントがボーン行に出る", "eye_L" in s.pose.bones and any(abs(sp.value()) > 1.0 for sp in pose._bone_rows["eye_L"].r), f"{[sp.value() for sp in pose._bone_rows['eye_L'].r]}")
    check("取り込む: 未保存の印・状態の行", "未保存" in pose.header.text() and "取り込みました" in pose.status.text(), pose.status.text())

    # ------------------------------------------------------------ ボーンの欄（Euler 角）
    br = pose._bone_rows["eye_R"]
    before_m = cmds.xform("eye_R", query=True, matrix=True)
    br.r[0].setValue(25.0)
    pump()
    after_m = cmds.xform("eye_R", query=True, matrix=True)
    q = s.pose.bones["eye_R"].r
    check("ボーンの欄: 回転 X を打つとジョイントが動く", any(abs(a - b) > 1e-4 for a, b in zip(before_m, after_m)), f"{before_m} {after_m}")
    check("ボーンの欄: 文書のクォータニオンは Euler(25, 0, 0) と一致", all(abs(a - b) < 1e-6 for a, b in zip(q, ui_pose.euler_xyz_to_quat(25, 0, 0))), f"{q}")
    br.t[1].setValue(0.3)
    check("ボーンの欄: 移動を打っても回転は変わらない", all(abs(a - b) < 1e-9 for a, b in zip(s.pose.bones["eye_R"].r, q)) and abs(s.pose.bones["eye_R"].t[1] - 0.3) < 1e-6)
    br.r[1].setValue(10.0)
    e = ui_pose.quat_to_euler_xyz(s.pose.bones["eye_R"].r)
    check("ボーンの欄: 別の軸を打っても、ほかの軸の値は保たれる", abs(e[0] - 25) < 1e-6 and abs(e[1] - 10) < 1e-6 and abs(e[2]) < 1e-6, f"{e}")
    # Euler の約束を Maya の XYZ 回転順と照らす
    conv_ok = True
    for eul in ((10, 20, 30), (-45, 60, 120), (170, -80, -170), (0, 0, 0), (90, 0, 0)):
        qq = ui_pose.euler_xyz_to_quat(*eul)
        mq = om.MEulerRotation(*(math.radians(a) for a in eul), om.MEulerRotation.kXYZ).asQuaternion()
        mm = (mq.x, mq.y, mq.z, mq.w)
        same = all(abs(a - b) < 1e-6 for a, b in zip(qq, mm)) or all(abs(a + b) < 1e-6 for a, b in zip(qq, mm))
        back = ui_pose.quat_to_euler_xyz(qq)
        conv_ok = conv_ok and same and all(abs(a - b) < 1e-4 for a, b in zip(back, eul))
    check("Euler の約束: Maya の XYZ 回転順と一致・往復できる", conv_ok)
    lock = ui_pose.quat_to_euler_xyz(ui_pose.euler_xyz_to_quat(30, 90, 0))
    check("Euler の約束: Y = 90° のジンバルロックでも落ちない", abs(lock[1] - 90) < 1e-3 and lock[2] == 0.0, f"{lock}")
    shot(pose, "pose_tab.png", 480, 900)

    # ------------------------------------------------------------ ボーンを Maya で選ぶ・戻す
    br.radio.setChecked(True)
    cmds.select(clear=True)
    pose.btn_select_bone.click()
    check("ボーンを Maya で選ぶ: ジョイントが選択される", cmds.ls(selection=True) == ["eye_R"], f"{cmds.ls(selection=True)}")
    pose.btn_reset_bone.click()
    pump()
    check("このボーンを戻す: 欄が 0 に戻りシーンのジョイントも基準へ", "eye_R" not in s.pose.bones or all(abs(v) < 1e-9 for v in s.pose.bones["eye_R"].t) and all(abs(sp.value()) < 1e-6 for sp in pose._bone_rows["eye_R"].r + pose._bone_rows["eye_R"].t))

    # ------------------------------------------------------------ 左右反転
    s.pose.set_curve("bs.smile_L", 0.0)
    pose.btn_mirror.click()
    pump()
    check("左右反転: smile_R → smile_L・eye_L → eye_R", abs(s.pose.curves.get("bs.smile_L", 0) - 0.7) < 1e-3 and "bs.smile_R" not in s.pose.trimmed().curves and "eye_R" in s.pose.trimmed().bones and "eye_L" not in s.pose.trimmed().bones, f"{s.pose.curves} {list(s.pose.bones)}")
    check("左右反転: スライダーとシーンも反映", abs(pose._curve_rows["bs.smile_L"].spin.value() - 0.7) < 1e-3 and abs(cmds.getAttr(f"{bs}.smile_L") - 0.7) < 1e-3)

    # ------------------------------------------------------------ 書き出し / 読み込み / ゼロ / 読み直す
    out_file = tmp / "pose_out.fcpose.json"
    pose.choose_export_path = lambda default: str(out_file)  # type: ignore[method-assign]
    pose.btn_export.click()
    check("書き出し: ファイルができる", out_file.exists() and "書き出しました" in pose.status.text(), pose.status.text())
    exported = dict(s.pose.curves)
    pose.btn_zero.click()
    pump()
    check("ゼロに戻す: 全部 0・シーンも基準", not any(abs(v) > 1e-6 for v in s.pose.curves.values()) and not s.pose.bones and abs(cmds.getAttr(f"{bs}.smile_L")) < 1e-9)
    pose.choose_import_path = lambda start: str(out_file)  # type: ignore[method-assign]
    pose.btn_import.click()
    pump()
    check("読み込み: 書き出したポーズが戻る", abs(s.pose.curves.get("bs.smile_L", 0) - exported["bs.smile_L"]) < 1e-3 and abs(cmds.getAttr(f"{bs}.smile_L") - 0.7) < 1e-3, f"{s.pose.curves}")
    pose.btn_reload.click()
    pump()
    check("読み直す: 保存してあるポーズへ戻る（この点は空）", not s.pose.dirty and not any(abs(v) > 1e-6 for v in s.pose.curves.values()))
    pose.choose_export_path = lambda default: ""  # type: ignore[method-assign]
    pose.btn_export.click()  # 空のポーズ + キャンセル: 何も起きない
    check("書き出し: キャンセルは何もしない", True)

    # ------------------------------------------------------------ 自動生成
    keys_before = sum(1 for p in s.doc.layers[0].points.values() if p.is_key)
    n_before = len(s.doc.layers[0].points)
    confirms: list[str] = []
    grid.ask_confirm = lambda title, text: (confirms.append(title), True)[1]  # type: ignore[method-assign]
    grid.btn_generate.click()
    pump()
    gen = [p for p in s.doc.layers[0].points.values() if not p.is_key]
    check("自動生成: キーから点が埋まる（初回は確認なし）", keys_before >= 2 and len(s.doc.layers[0].points) > n_before and gen and not confirms, f"{keys_before} {n_before} {len(s.doc.layers[0].points)} {confirms}")
    check("自動生成: 水色で描かれる", near_color(cell_color(gen[0].row, gen[0].col), ui_grid.FILL["generated"]), f"{cell_color(gen[0].row, gen[0].col).name()}")
    snapshot = fcpose_io.to_dict(s.doc)
    grid.ask_confirm = lambda title, text: (confirms.append(title), False)[1]  # type: ignore[method-assign]
    grid.btn_generate.click()
    check("自動生成: 作り直しは確認が出る・いいえで何も変えない", confirms == ["自動生成"] and fcpose_io.to_dict(s.doc) == snapshot)
    grid.ask_confirm = lambda title, text: True  # type: ignore[method-assign]
    check("集計の行", "キー" in grid.summary_label.text() and f"生成 {len(gen)}" in grid.summary_label.text(), grid.summary_label.text())
    # 選択を残したままの絵
    click_cell(1, 2)
    cmds.setAttr("persp.translate", 25.0, 12.0, 45.0)
    grid._tick()
    shot(grid, "grid_keys_generated.png")

    # ------------------------------------------------------------ 右クリックメニュー（QAction.trigger）
    menu = grid.build_point_menu(1, 3)
    acts = {a.objectName(): a for a in menu.actions()}
    check("右クリック: 7 項目・有効 / 無効は presenter から", sorted(acts) == sorted(["unkey", "clear", "bake_point", "copy", "paste", "paste_mirrored", "camera_to_point"]) and acts["copy"].isEnabled() and not acts["paste"].isEnabled(), f"{[(n, a.isEnabled()) for n, a in acts.items()]}")
    acts["copy"].trigger()
    menu = grid.build_point_menu(2, 0)
    acts = {a.objectName(): a for a in menu.actions()}
    check("右クリック: コピー後は貼り付けが有効", acts["paste"].isEnabled() and acts["paste_mirrored"].isEnabled())
    acts["paste"].trigger()
    pump()
    p20 = s.doc.layers[0].points.get((2, 0))
    check("右クリック: 貼り付け（キーになる）", p20 is not None and p20.is_key and abs(p20.pose.curves.get("bs.mouth_open", 0) - 0.6) < 1e-3)
    acts = {a.objectName(): a for a in grid.build_point_menu(0, 4).actions()}
    acts["paste_mirrored"].trigger()
    p04 = s.doc.layers[0].points.get((0, 4))
    check("右クリック: 左右反転して貼り付け（smile_L → smile_R）", p04 is not None and p04.is_key and "bs.smile_R" in p04.pose.curves and "bs.smile_L" not in p04.pose.curves, f"{p04 and p04.pose.curves}")
    acts = {a.objectName(): a for a in grid.build_point_menu(2, 0).actions()}
    acts["unkey"].trigger()
    check("右クリック: キー解除", not s.doc.layers[0].points[(2, 0)].is_key)
    acts = {a.objectName(): a for a in grid.build_point_menu(2, 0).actions()}
    check("右クリック: キーでない点はキー解除が無効", not acts["unkey"].isEnabled())
    acts["clear"].trigger()
    check("右クリック: クリア", (2, 0) not in s.doc.layers[0].points)
    acts = {a.objectName(): a for a in grid.build_point_menu(0, 4).actions()}
    cmds.setAttr("persp.translate", 1.0, 80.0, 60.0)
    acts["camera_to_point"].trigger()
    y4, p4 = s.view_angles()
    check("右クリック: カメラをこの点へ（Yaw 90° / Pitch -45°…は R0 C4 = Yaw 90 / Pitch -45）", abs(y4 - 90) < 0.5 and abs(p4 + 45) < 0.5, f"{y4:.2f} {p4:.2f}")

    # ------------------------------------------------------------ ベイク（ボタンはベイクだけ）
    cmds.undoInfo(state=True)
    v = grid.current_view()
    unbaked = [pv for pv in v.points if pv.frame != "none"]
    check("ベイク前: 未ベイクの点に枠がある（絵）", len(unbaked) >= 3, f"{len(unbaked)}")
    click_cell(1, 3)
    nonempty = [(l.name, rc) for l in s.doc.layers for rc, p in l.points.items() if not p.pose.is_empty()]
    names = {naming.morph_name("mini", ln, *rc) for ln, rc in nonempty}
    grid.btn_bake_all.click()
    pump()
    fc = {t.alias for t in scene.fc_targets(face)}
    check("ベイク（全部）: FC_* ができる", fc == names and bool(names), f"{len(fc)} {len(names)}")
    check("ベイク（全部）: 結果の要約が箱に出る", grid.report.toPlainText().startswith("ベイク: 作成") and f"作成 {len(names)}" in grid.report.toPlainText(), grid.report.toPlainText())
    check("ベイク（全部）: 未ベイクの枠が消える・集計", all(pv.frame == "none" for pv in grid.current_view().points) and "未ベイク 0" in grid.summary_label.text(), grid.summary_label.text())
    check("ベイク（全部）: 編集状態を抜けてシーンは元の姿勢", not s.editing)
    check("ベイク: Maya の Undo の区切りは tdFacialBake だけ", cmds.undoInfo(query=True, undoName=True) == "tdFacialBake", f"{cmds.undoInfo(query=True, undoName=True)}")
    shot(grid, "grid_after_bake.png")
    cmds.undo()
    check("ベイク: Maya の Undo 1 回で元に戻る（ボタンはベイクだけをした）", not scene.fc_targets(face))
    cmds.redo()
    s.refresh_scene()
    pump()
    check("ベイク: redo で戻る", {t.alias for t in scene.fc_targets(face)} == names)
    # 変更あり
    click_cell(1, 3)
    pose._curve_rows["bs.mouth_open"].slider.setValue(300)
    pose.btn_save.click()
    pump()
    check("保存後: ベイク済みの点が「変更あり」の枠になる", grid.current_view().points[1 * 5 + 3].frame == "changed", grid.current_view().points[1 * 5 + 3].frame)
    pose._curve_rows["bs.mouth_open"].slider.setValue(350)  # 編集中（未保存）で
    grid.btn_bake_point.click()
    pump()
    check("ベイク（この点）: 1 点だけ置き換える・未保存の注意", "置き換え 1" in grid.report.toPlainText() and "保存していない" in grid.report.toPlainText(), grid.report.toPlainText())
    grid.btn_bake_layer.click()
    check("ベイク（このレイヤー）: 要約が出る", grid.report.toPlainText().startswith("ベイク:"), grid.report.toPlainText())
    click_cell(1, 3)
    pose._curve_rows["bs.mouth_open"].slider.setValue(250)
    pose.btn_save.click()
    check("保存後: 焼いたあとに変えた点は「変更あり」の枠（2 回目）", grid.current_view().points[1 * 5 + 3].frame == "changed")
    shot(grid, "grid_changed_frame.png")

    # ------------------------------------------------------------ クリア・レイヤーをクリア
    click_cell(0, 0)
    asked_confirm: list[str] = []
    grid.ask_confirm = lambda title, text: (asked_confirm.append(title), False)[1]  # type: ignore[method-assign]
    n_pts = len(s.doc.layers[0].points)
    grid.btn_clear_layer.click()
    check("レイヤーをクリア: 確認が出て、いいえなら何も変えない", asked_confirm == ["レイヤーをクリア"] and len(s.doc.layers[0].points) == n_pts)
    grid.ask_confirm = lambda title, text: True  # type: ignore[method-assign]
    grid.btn_clear_layer.click()
    pump()
    check("レイヤーをクリア: 点が全部消え、焼いた FC_* も消える", not s.doc.layers[0].points and not scene.fc_targets(face) and not grid.btn_clear_layer.isEnabled())

    # ------------------------------------------------------------ 後片付け・タイマー
    n_listeners = len(s.listeners)
    grid.start_tracking()
    check("タイマー: 動いている", grid.timer.isActive())
    grid.hideEvent(QtGui.QHideEvent())
    check("タイマー: 隠れたら止まる", not grid.timer.isActive())
    grid.showEvent(QtGui.QShowEvent())
    check("タイマー: 見えたら動く", grid.timer.isActive())
    grid.detach()
    pose.detach()
    check("detach: タイマーが止まり、listeners・Presenter の購読が外れる",
          not grid.timer.isActive() and len(s.listeners) == n_listeners - 2 and not s.ctx._listeners.__contains__(grid._on_ctx_event) and grid._unsub == [])
    grid.detach()  # 二重に呼んでも安全
    ok = True
    try:
        s.undo()
        s.pose.set_curve("bs.smile_L", 0.1)
    except Exception:  # noqa: BLE001
        ok = False
    check("detach 後: セッションの通知で外したウィジェットは呼ばれない（例外なし）", ok)

    g2 = ui_grid.GridTab(s)
    p2 = ui_pose.PoseTab(s)
    s.close()
    ok = True
    try:
        g2.refresh()
        p2.refresh()
    except Exception:  # noqa: BLE001
        ok = False
        RESULTS.append(("close 後 refresh の例外", False, traceback.format_exc()))
    check("データを閉じたあと: refresh() が例外を出さずタイマーも止まる", ok and not g2.timer.isActive() and not g2.btn_bake_all.isEnabled() and not p2.btn_save.isEnabled())
    g2.detach()
    p2.detach()
    shutil.rmtree(tmp, ignore_errors=True)


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
        print(f"SMOKE RESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
        sys.stdout.flush()
        maya.standalone.uninitialize()
        os._exit(1 if failed else 0)
