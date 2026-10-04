"""グリッドタブの赤い点のドラッグ（ui_grid.GridCanvas / GridTab.drag_*）のスモーク（mayapy・画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_grid_drag_smoke.py

QTest で grid.canvas にマウスの press / move / release を送って確かめる。合成の小さな頭と一時フォルダだけを使う。
手順: QT_QPA_PLATFORM=offscreen → QApplication → maya.standalone.initialize。
"""

from __future__ import annotations

import math
import os
import re
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

from PySide6 import QtCore, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
LEFT = QtCore.Qt.LeftButton
NOMOD = QtCore.Qt.NoModifier
SHIFT = QtCore.Qt.ShiftModifier


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    from maya import cmds

    import facial_fixture
    from tdrive import project
    from tdrive_facial import session as S
    from tdrive_facial import ui_grid

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_gdrag_"))
    project.set_root(tmp)
    facial_fixture.build_mini_head()
    s = S.current()
    s.close()
    s.listeners.clear()
    grid = ui_grid.GridTab(s)

    def pump() -> None:
        app.processEvents()
        grid.flush()

    s.new("mini")
    s.add_to_working_set(curves=["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"], bones=["eye_L", "eye_R"])
    grid.resize(560, 900)
    grid.layout().activate()
    pump()
    grid.start_tracking()
    cv = grid.canvas
    s.camera_to_point(1, 2)  # 正面
    grid._poll_camera(force=True)
    pump()

    def pos_for(yaw: float, pitch: float) -> QtCore.QPoint:
        """角度に対応する画面上の位置（範囲外は外側へ延長）。赤い点の描き方と同じ式。"""
        v = grid.current_view()
        g = s.doc.grid
        cw, ch = cv._cell_size(v)
        col = (yaw / g.yaw_range + 1) * 0.5 * (g.cols - 1)
        row = (pitch / g.pitch_range + 1) * 0.5 * (g.rows - 1)
        return QtCore.QPointF(cv.LEFT + (col + 0.5) * cw, cv.TOP + ((g.rows - 1 - row) + 0.5) * ch).toPoint()

    def dot() -> QtCore.QPoint:
        return cv.marker_position(grid.current_view(), grid.current_marker()).toPoint()

    def cam_pos() -> tuple:
        return tuple(round(v, 6) for v in cmds.xform(S.FacialSession.camera_transform(), q=True, ws=True, t=True))

    def dist() -> float:
        return math.dist(cam_pos(), s.grid_basis()[0])

    def drag(start: QtCore.QPoint, points, mod=NOMOD) -> None:
        QTest.mousePress(cv, LEFT, mod, start)
        for p in points:
            QTest.mouseMove(cv, p)
            app.processEvents()
        QTest.mouseRelease(cv, LEFT, mod, points[-1] if points else start)
        pump()

    def state() -> tuple:
        return (s.ctx.selection, s.editing, s.dirty, s.has_unsaved_work, len(s._undo), len(s._redo), cmds.undoInfo(q=True, undoName=True))

    def angles_ok(yaw: float, pitch: float, tol: float = 0.5) -> bool:
        a, b = s.view_angles()
        return abs(a - yaw) <= tol and abs(b - pitch) <= tol

    def label_ok(yaw: float, pitch: float) -> bool:
        """読み出し「カメラ: Yaw x° / Pitch y°」の数字が 0.5° 以内（マウスの位置は整数 px なので少しずれる）。"""
        m = re.search(r"Yaw (-?[\d.]+)° / Pitch (-?[\d.]+)°", grid.camera_label.text())
        return bool(m) and abs(float(m.group(1)) - yaw) <= 0.5 and abs(float(m.group(2)) - pitch) <= 0.5

    d0 = dist()
    before = state()

    # ---- (a) 赤い点からのドラッグ: セルの間・範囲外（端へ収める）
    for yaw, pitch in ((37.5, -10.0), (-20.0, 30.0), (60.0, 0.0)):
        start = dot()
        drag(start, [start + QtCore.QPoint(8, 0), pos_for(yaw * 0.5, pitch * 0.5), pos_for(yaw, pitch)])
        check(f"点からのドラッグ: Yaw {yaw} / Pitch {pitch} へカメラが動く", angles_ok(yaw, pitch), f"{s.view_angles()}")
        check(f"点からのドラッグ({yaw}, {pitch}): 中心までの距離は同じ", abs(dist() - d0) <= 1e-3, f"{dist()} {d0}")
        p, d = pos_for(yaw, pitch), dot()
        check(
            f"点からのドラッグ({yaw}, {pitch}): 赤い点が動く・読み出しに正確な角度",
            math.hypot(d.x() - p.x(), d.y() - p.y()) < 2 and label_ok(yaw, pitch),
            grid.camera_label.text(),
        )
    drag(dot(), [pos_for(150, 80)])  # 格子の外へ出す
    check("範囲外へドラッグ: 端（Yaw 90 / Pitch 45）へ収める", angles_ok(90, 45), f"{s.view_angles()}")
    check("範囲外へドラッグ: 距離は同じ", abs(dist() - d0) <= 1e-3)
    drag(dot(), [pos_for(-200, -100)])
    check("範囲外へドラッグ（逆）: 端（Yaw -90 / Pitch -45）へ収める", angles_ok(-90, -45), f"{s.view_angles()}")
    check("ドラッグが終わると dragging でなくなる（追従のタイマーへ戻る）", not grid.dragging and cv._mode == "")

    # ---- (b) 選択・編集・文書・Undo は変わらない
    check("ドラッグ: 選択・編集状態・未保存・Undo 履歴・Maya の Undo 名が変わらない", state() == before, f"{state()} {before}")
    check("ドラッグ: 編集状態に入っていない", not s.editing and s.ctx.selection is None)

    # ---- (c) Shift + ドラッグ（何もない所から）/ Shift + クリック
    s.camera_to_point(1, 2)
    grid._poll_camera(force=True)
    drag(pos_for(-60, 25), [pos_for(-45, 20), pos_for(-30, 10)], mod=SHIFT)
    check("Shift + ドラッグ: 離した位置（Yaw -30 / Pitch 10）へ動く", angles_ok(-30, 10), f"{s.view_angles()}")
    QTest.mouseClick(cv, LEFT, SHIFT, pos_for(45, -30))
    pump()
    check("Shift + クリック: その角度へ飛ぶ", angles_ok(45, -30), f"{s.view_angles()}")
    check("Shift の操作: 選択・編集・Undo は変わらない・距離も同じ", state() == before and abs(dist() - d0) <= 1e-3, f"{state()}")

    # ---- 「カメラも動かす」が切れていても動く
    grid.move_camera.setChecked(False)
    drag(dot(), [pos_for(10, 5), pos_for(15, 5)])
    check("「カメラも動かす」オフでもドラッグは動く", angles_ok(15, 5), f"{s.view_angles()}")
    grid.move_camera.setChecked(True)

    # ---- (d) 赤い点を押して動かさずに離す = 普通のクリック（その点を選ぶ）
    s.camera_to_point(1, 3)
    grid._poll_camera(force=True)
    pump()
    start = dot()
    QTest.mousePress(cv, LEFT, NOMOD, start)
    QTest.mouseMove(cv, start + QtCore.QPoint(2, 1))  # しきい値より小さい
    QTest.mouseRelease(cv, LEFT, NOMOD, start + QtCore.QPoint(2, 1))
    pump()
    check("点を押して動かさず離す: そのセルの普通のクリック（選択・編集開始）", s.ctx.selection == (1, 3) and s.editing, f"{s.ctx.selection} {s.editing}")
    check("点を押して動かさず離す: カメラは点の角度", angles_ok(45, 0), f"{s.view_angles()}")

    # ---- 編集中のドラッグ: 選択もポーズも変わらず、未保存の確認も出ない
    asked: list[str] = []
    grid.ask_unsaved_choice = lambda msg: asked.append(msg) or "cancel"  # type: ignore[method-assign]
    s.pose.set_curve("bs.mouth_open", 0.4)
    pump()
    applied_before, undo_before = s._applied, len(s._undo)
    drag(dot(), [pos_for(20, 15), pos_for(-10, -20)])
    check("編集中のドラッグ: カメラが動く", angles_ok(-10, -20), f"{s.view_angles()}")
    check(
        "編集中のドラッグ: 選択・編集・適用中のポーズは変わらず、確認は出ない",
        s.ctx.selection == (1, 3) and s.editing and s._applied == applied_before and not asked,
        f"{s.ctx.selection} {s._applied} {asked}",
    )
    check("編集中のドラッグ: 編集中の未保存の値が残り、Undo は増えない", s.pose.dirty and len(s._undo) == undo_before)

    # ---- (e) ふつうのクリックは今までどおり選ぶ（未保存の編集があるので確認が出る）
    grid.ask_unsaved_choice = lambda msg: asked.append(msg) or "discard"  # type: ignore[method-assign]
    QTest.mouseClick(cv, LEFT, NOMOD, cv.cell_center(2, 0))
    pump()
    check("ふつうのクリック: その点を選ぶ（未保存の確認が出る）", s.ctx.selection == (2, 0) and len(asked) == 1, f"{s.ctx.selection} {asked}")

    # ---- (f) プレビューがあるとき: 補正がカメラに追従する
    s.end_edit()
    pump()
    rep = s.preview_build()
    check("前提: プレビューを作れる", s.preview_state() == "live", f"{s.preview_state()} {rep}")
    s.camera_to_point(1, 2)
    grid._poll_camera(force=True)
    pump()
    drag(dot(), [pos_for(30, 10), pos_for(-37.5, 12.5)])
    st = s.preview_status()
    check("プレビュー: ドラッグ後の rig の角度がドラッグした角度", abs(st.out_yaw + 37.5) < 0.5 and abs(st.out_pitch - 12.5) < 0.5, f"{st.out_yaw} {st.out_pitch}")
    check("プレビュー: ドラッグしても編集状態に入らない", not s.editing)

    # ---- 動かせないカメラ: 状態行に出して止まる
    cam = S.FacialSession.camera_transform()
    cmds.setAttr(cam + ".tx", lock=True)
    before_pos = cam_pos()
    drag(dot(), [pos_for(10, 0), pos_for(20, 0)])
    check("ロックされたカメラ: 動かさず、状態行に理由を出して止まる", cam_pos() == before_pos and "ロック" in grid.status.text() and not grid.dragging, grid.status.text())
    cmds.setAttr(cam + ".tx", lock=False)

    # ---- 後片付け
    grid.detach()
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
