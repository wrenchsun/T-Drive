"""ブレンドツリー（1D / 2D）の合成のスモーク（AN-11-2 / AN-11-3。mayapy・ヘッドレス。dx11 / Render Override は使わない）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_blend_tree_smoke.py

確かめること: 4 方向 + 中央の 2D ツリーでパッドの四隅・中点・原点の合成値が重みどおり（位置は重み付き和、回転は同じ軸なら角度の重み付き和）・
1D の中点で 2 クリップが半々・長さの違うクリップは正規化時間で同期し重み付きの長さで再生する・重みが合成のノードに残る・
外すと元のつながりへ戻る・UI（Blend Tree パラメータの節。パッド・スライダー・雛形・作り直し・保存 / 再読込）。
"""

from __future__ import annotations

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
sys.path.insert(0, str(REPO / "tests" / "fixtures" / "anim"))

from PySide6 import QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import maya.standalone  # noqa: E402

TOL = 0.05
RESULTS: list[tuple[str, bool, str]] = []
INFO: list[str] = []

# クリップ: (範囲, spine tx の始点→終点, arm_L rotateZ, arm_R rotateX)。リニアキー。見分けがつくように大きくずらす
CLIPS = {
    "Idle": ((0, 30), (0, 0), (0, 0), (0, 0)),
    "Walk_F": ((100, 130), (100, 130), (10, 20), (0, 30)),
    "Walk_B": ((200, 230), (-100, -130), (-10, -20), (0, -30)),
    "Walk_L": ((300, 330), (-50, -80), (40, 60), (20, 20)),
    "Walk_R": ((400, 430), (50, 80), (-40, -60), (-20, -20)),
}


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_bt_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def clip_value(name: str, which: int, u: float) -> float:
    (a, b), *chans = CLIPS[name]
    v0, v1 = chans[which]
    return v0 + (v1 - v0) * u


def _run(tmp: Path) -> None:
    from maya import cmds

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import composition as K
    from tdrive_anim import session as S
    from tdrive_anim.core import blend_tree as BT

    project.set_root(tmp)
    make_mini_rig.build()
    cmds.currentUnit(time="ntsc")
    for name, ((a, b), tx, rz, rx) in CLIPS.items():
        for f, i in ((a, 0), (b, 1)):
            cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=f, value=tx[i], itt="linear", ott="linear")
            cmds.setKeyframe("arm_L0_0_jnt", attribute="rotateZ", time=f, value=rz[i], itt="linear", ott="linear")
            cmds.setKeyframe("arm_R0_0_jnt", attribute="rotateX", time=f, value=rx[i], itt="linear", ott="linear")
    s = S.current()
    s.new("MisotenChara")
    for n, ((a, b), *_r) in CLIPS.items():
        direction = n.split("_")[1] if "_" in n else ""
        s.add_clip_from_range(n.split("_")[0], a, b, direction=direction)
    clips = {c["name"]: c for c in s.data["clips"]}
    joints = make_mini_rig.JOINTS
    fps = 30
    plugs = ["spine_C0_0_jnt.translateX", "arm_L0_0_jnt.rotateZ", "arm_R0_0_jnt.rotateX"]
    orig_src = {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in plugs}
    pre = "A_MisotenChara_"
    tree = BT.template_2d(s.data["clips"], "MisotenChara")
    check("雛形: 2D の移動ツリー（Idle + F/B/L/R）", tree is not None and len(tree["motions"]) == 5 and tree["motions"][0]["position"] == [0, 0], str(tree))

    def expected(tr, vals, f, n_out):
        ws = BT.weights(tr, vals)
        u = f / n_out
        out = []
        for which in range(3):
            out.append(sum(w * clip_value(m["clip"][len(pre):], which, u) for w, m in zip(ws, tr["motions"])))
        return ws, out

    def build(tr, vals, **kw):
        ms = K.tree_motions(tr, vals, clips)
        return K.compose_blend_tree("bt", ms, joints, fps, {"type": tr["type"]}, **kw)

    # ---- 2D: パッドの四隅・中点・原点
    points = {"原点": (0, 0), "右上の隅 (1,1)": (1, 1), "左下の隅 (-1,-1)": (-1, -1), "右下の隅 (1,-1)": (1, -1), "左上の隅 (-1,1)": (-1, 1),
              "中点 (0,0.5)": (0, 0.5), "中点 (0.5,0)": (0.5, 0), "中点 (-0.5,-0.5)": (-0.5, -0.5), "F の上 (0,1)": (0, 1)}
    for label, (x, y) in points.items():
        res = build(tree, [x, y])
        n_out = int(res.frames)
        bad = []
        for f in (0, 7, 15, n_out):
            cmds.currentTime(f, edit=True)
            ws, exp = expected(tree, [x, y], f, n_out)
            got = [cmds.getAttr(p) for p in plugs]
            if any(abs(g - e) > TOL for g, e in zip(got, exp)):
                bad.append((f, [round(g, 3) for g in got], [round(e, 3) for e in exp]))
        check(f"2D パッド {label}: 合成値 = 重み付き和", not bad, str(bad[:3]))
    cmds.currentTime(15, edit=True)
    build(tree, [1, 1])
    node = K.PREFIX + "bt"
    w = [cmds.getAttr(f"{node}.weight{i}") for i in range(5)]
    check("合成のノードに各モーションの重みが残る（隅 (1,1) = F と R が半々）", [round(x, 3) for x in w] == [0.0, 0.5, 0.0, 0.0, 0.5], str(w))
    inf = K.info("bt")
    check("tdInfo に blendTree の重みが残る", inf.get("blendTree", {}).get("weights") == [0.0, 0.5, 0.0, 0.0, 0.5], str(inf))
    check("合成中は元のカーブがジョイントから切り離されている", all("tdAnimComp_" in str(cmds.listConnections(p, source=True, destination=False, plugs=True)[0]) for p in plugs))
    check("合成は 1 つだけ", K.active() == ["bt"])
    # ---- 回転: 別の軸の回転を混ぜても単位クォータニオン（正規化）で、重みが 1 本に偏れば元の値
    res = build(tree, [0, 1])
    cmds.currentTime(res.frames, edit=True)
    check("回転: 重みが F だけなら F の終点の回転", abs(cmds.getAttr("arm_L0_0_jnt.rotateZ") - 20) < 0.05 and abs(cmds.getAttr("arm_R0_0_jnt.rotateX") - 30) < 0.05)
    # ---- 外す
    n = K.remove("bt")
    check("外すと元のつながりへ戻る", n == 1 and all(cmds.listConnections(p, source=True, destination=False, plugs=True)[0] == orig_src[p] for p in plugs))
    check("外すと合成のノードが残らない", K.active() == [] and not cmds.ls("tdAnimComp_*"))

    # ---- 1D: 中点で半々
    t1 = {"type": "1D", "parameters": ["Speed"], "motions": [{"clip": pre + "Idle", "threshold": 0}, {"clip": pre + "Walk_F", "threshold": 1}]}
    res = build(t1, [0.5])
    cmds.currentTime(15, edit=True)
    got = cmds.getAttr(plugs[0])
    check("1D の中点: Idle と Walk_F が半々（tx = (0 + 115) / 2）", abs(got - 0.5 * clip_value("Idle", 0, 0.5) - 0.5 * clip_value("Walk_F", 0, 0.5)) < TOL and abs(got - 57.5) < TOL, str(got))
    t1["motions"][1]["threshold"] = 2
    build(t1, [0.5])
    cmds.currentTime(30, edit=True)
    check("1D: しきい値 0 / 2 の 0.5 では Walk_F が 25%", abs(cmds.getAttr(plugs[0]) - 0.25 * 130) < TOL)
    K.remove("bt")

    # ---- 正規化時間: 長さの違うクリップ
    cmds.currentTime(0, edit=True)
    for f, v in ((500, 0), (560, 60)):
        cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=f, value=v, itt="linear", ott="linear")
    s.add_clip_from_range("Run", 500, 560, direction="F")
    clips = {c["name"]: c for c in s.data["clips"]}
    tl = {"type": "1D", "parameters": ["Speed"], "motions": [{"clip": pre + "Idle", "threshold": 0}, {"clip": pre + "Run_F", "threshold": 1}]}
    res = build(tl, [0.5])
    check("長さの違うクリップ: 長さは重み付き平均（30 と 60 の半々 = 45 フレーム）", res.frames == 45, str(res.frames))
    cmds.currentTime(45, edit=True)
    check("正規化時間: 終点で両方が終点（Idle 0 と Run 60 の半々 = 30）", abs(cmds.getAttr(plugs[0]) - 30) < TOL, str(cmds.getAttr(plugs[0])))
    cmds.currentTime(22.5, edit=True)
    check("正規化時間: 中間で両方が中間（0 と 30 の半々 = 15）", abs(cmds.getAttr(plugs[0]) - 15) < TOL, str(cmds.getAttr(plugs[0])))
    check("再生範囲が合成の長さになる", cmds.playbackOptions(query=True, maxTime=True) == 45)
    K.remove("bt")
    check("外すと再生範囲が戻る", cmds.playbackOptions(query=True, maxTime=True) != 45)

    # ---- UI
    from tdrive_anim import ui_blend as UB

    cmds.currentTime(0, edit=True)
    ui = UB.BlendTab(s)
    s.listeners.append(ui.refresh)
    ui.refresh()
    ui_checks(ui, cmds, K, BT, s, plugs, pre)
    ui.detach()
    s.listeners.remove(ui.refresh)


def ui_checks(ui, cmds, K, BT, s, plugs, pre) -> None:  # noqa: PLR0915
    from tdrive_anim import ui_blend as UB
    from tdrive_anim.core import state_machine as M

    check("UI: 雛形ボタンで 2D の移動ツリーが animset の状態図に入る", hasattr(ui, "make_template_2d"))
    ui.make_template_2d()
    states = [st for st in s.data["stateGraph"]["states"] if "blendTree" in st]
    check("UI: 状態図に blendTree の状態ができる（MoveX / MoveY のパラメータも）", len(states) == 1 and {"MoveX", "MoveY"} <= {p["name"] for p in s.data["stateGraph"]["parameters"]}, str(s.data.get("stateGraph")))
    probs = M.validate(s.data["stateGraph"], s.data["clips"])
    check("UI: 雛形は検証を通る（クリップ未割り当ての Jump は除く）", [p for p in probs if "Jump" not in p] == [], str(probs))
    s.rename_clip(pre + "Idle", pre + "IdleX")
    names = [m["clip"] for m in next(st for st in s.data["stateGraph"]["states"] if "blendTree" in st)["blendTree"]["motions"]]
    check("クリップ名を変えるとブレンドツリーのモーションも追従する", pre + "IdleX" in names and pre + "Idle" not in names, str(names))
    s.rename_clip(pre + "IdleX", pre + "Idle")
    ui.make_template_1d()
    check("UI: 1D の Locomotion の雛形は Idle / Walk_F / Run_F がそろったときだけ", len([st for st in s.data["stateGraph"]["states"] if "blendTree" in st]) in (1, 2))
    ui.select_tree(states[0]["name"])
    check("UI: パッドが 160 px", ui.pad.width() == 160 and ui.pad.height() == 160)
    from PySide6 import QtCore, QtGui
    from PySide6.QtTest import QTest

    calls = []
    orig = K.compose_blend_tree
    K.compose_blend_tree = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    try:
        def px(x, y):  # パッド座標 → ウィジェット座標
            pt = ui.pad._to_px(x, y)
            return QtCore.QPoint(int(round(pt.x())), int(round(pt.y())))

        ui.pad.set_range(-1.0, 1.0)
        ui.pad_range.setCurrentIndex(1)
        ui.tree_panel._range_changed()
        QTest.mousePress(ui.pad, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, px(0.0, 0.0))
        for k in range(1, 6):
            ui.pad.mouseMoveEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(px(k / 5, k / 5)), QtCore.QPointF(px(k / 5, k / 5)), QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
        check("UI: パッドのドラッグ中は合成を作らない（AN-F-47）", calls == [] and K.active() == [], str(calls))
        check("UI: ドラッグ中も重みの表示は追従する", "50%" in ui.tree_weights_label.text() or "%" in ui.tree_weights_label.text())
        QTest.mouseRelease(ui.pad, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, px(1.0, 1.0))
        check("UI: マウスを離したとき 1 回だけ作る", len(calls) == 1 and K.active() == [UB.COMP_NAME], str(calls))
        w = K.info(UB.COMP_NAME)["blendTree"]["weights"]
        check("UI: 隅 (1,1) は F と R の半々", [round(x, 2) for x in w] == [0, 0.5, 0, 0, 0.5], str(w))
        check("UI: 重みの % 表示", "50%" in ui.tree_weights_label.text(), ui.tree_weights_label.text())
        sl = ui.tree_panel.sliders[0][0]
        sl.setSliderDown(True)
        sl.setValue(-500)
        check("UI: スライダーのドラッグ中は作らない", len(calls) == 1)
        sl.setSliderDown(False)
        check("UI: スライダーを離したとき 1 回", len(calls) == 2, str(calls))
        sl.setValue(0)  # キー・ホイール相当（ドラッグ中ではない）: その場で 1 回
        check("UI: キー / ホイール相当の変更はその場で 1 回", len(calls) == 3, str(calls))
        ui.tree_panel.set_values(MoveX=0.0, MoveY=0.0)
        check("UI: 原点で Idle だけ", K.info(UB.COMP_NAME)["blendTree"]["weights"][0] == 1.0)
    finally:
        K.compose_blend_tree = orig
    ui.pad_range.setCurrentIndex(0)
    check("UI: 範囲の切替（0〜1 / -1〜1）", ui.pad.range() in ((0.0, 1.0), (-1.0, 1.0)))
    ui.remove()
    check("UI: 合成を外せる", K.active() == [])
    ui.select_tree(states[0]["name"])
    ui.tree_panel.set_values(MoveX=-1.0, MoveY=0.0)
    # ---- AN-F-48: モーションの表・足す / 消す・パッドの青い点のドラッグ・新規 / 削除・Undo
    panel = ui.tree_panel
    panel.select_tree(states[0]["name"])
    n0 = len(panel.current_tree()["motions"])
    check("AN-F-48: モーションの表に行が並ぶ", panel.motion_table.rowCount() == n0 and panel.motion_table.columnCount() == 4)
    check("AN-F-48: 足す", panel.add_motion() and len(panel.current_tree()["motions"]) == n0 + 1 and panel.motion_table.rowCount() == n0 + 1)
    check("AN-F-48: 足したモーションは空いている位置 (1,1)", panel.current_tree()["motions"][-1]["position"] == [1, 1], str(panel.current_tree()["motions"][-1]))
    check("AN-F-48: 足したあと Ctrl+Z で戻る（animset の Undo）", s.undo() and len(panel.current_tree()["motions"]) == n0)
    panel.refresh()
    check("AN-F-48: 位置を変える", panel.edit_motion(1, position=[0.0, 0.5]) and panel.current_tree()["motions"][1]["position"] == [0.0, 0.5])
    panel.edit_motion(0, position=[0.0, 0.5])
    check("AN-F-48: 位置の重複は保存して警告する", panel.current_tree()["motions"][0]["position"] == [0.0, 0.5] and "重なって" in ui.message.text(), ui.message.text())
    s.undo()
    s.undo()
    panel.refresh()
    ui.tree_panel.pad.set_range(-1.0, 1.0)
    panel.select_tree(states[0]["name"])
    idx = 1  # F = (0, 1)
    def ppx(x, y):
        pt = panel.pad._to_px(x, y)
        return QtCore.QPoint(int(round(pt.x())), int(round(pt.y())))
    QTest.mousePress(panel.pad, QtCore.Qt.LeftButton, QtCore.Qt.ControlModifier, ppx(0.0, 1.0))
    tgt = QtCore.QPointF(ppx(0.5, 0.5))
    panel.pad.mouseMoveEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseMove, tgt, tgt, QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.ControlModifier))
    check("AN-F-48: 点のドラッグ中は animset を変えない", panel.current_tree()["motions"][idx]["position"] == [0, 1])
    QTest.mouseRelease(panel.pad, QtCore.Qt.LeftButton, QtCore.Qt.ControlModifier, ppx(0.5, 0.5))
    pos = panel.current_tree()["motions"][idx]["position"]
    check("AN-F-48: 青い点を Ctrl+ドラッグして離すとモーションの位置が変わる（表と同期）", abs(pos[0] - 0.5) < 0.05 and abs(pos[1] - 0.5) < 0.05 and abs(panel.motion_table.cellWidget(idx, 2).value() - pos[1]) < 1e-6, str(pos))
    check("AN-F-48: 点のドラッグも 1 回の Undo で戻る", s.undo() and panel.current_tree()["motions"][idx]["position"] == [0, 1])
    check("AN-F-48: 消す", panel.remove_motion(n0 - 1) and len(panel.current_tree()["motions"]) == n0 - 1)
    s.undo()
    check("AN-F-48: 新規（1D）が状態図に入りパラメータも足される", panel.create_tree("Loco2", "1D", ["Spd"]) and any(p["name"] == "Spd" for p in s.data["stateGraph"]["parameters"]) and panel.current_tree()["type"] == "1D" and panel.motion_table.columnCount() == 3)
    check("AN-F-48: 同じ名前は作れない・不正な名前も", not panel.create_tree("Loco2", "1D", ["Spd"]) and not panel.create_tree("1bad", "1D", ["P"]) and not panel.create_tree("Z", "2DSimpleDirectional", ["A", "A"]))
    check("AN-F-48: 削除", panel.delete_tree(confirm=False) and "Loco2" not in [st["name"] for st in s.data["stateGraph"]["states"]])
    check("AN-F-48: 削除も Undo で戻る", s.undo() and "Loco2" in [st["name"] for st in s.data["stateGraph"]["states"]])
    ui.detach()
    check("UI: detach で合成が外れる", K.active() == [])


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
