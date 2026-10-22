"""Phase AN コードレビュー（docs/21）の合成まわりの回帰スモーク（mayapy・ヘッドレス）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_review_comp_smoke.py

SF-3 単位変換ノードを挟むつながり / SF-4・SF-5・SF-6 合成は Maya の Undo に積まない / SF-7 オートキー / SF-8 再生範囲の控えの共有 /
SF-17 合成なしの再生で速さを戻す / SF-19 合成中の保存 / SF-20 合成中に足した接続 / SP-5 時間単位 / SP-6 重なりの警告 / SP-10 回転順。
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

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_review_comp_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def key_clips(cmds) -> None:
    spec = {(0, 30): (0, 30, 0, 0), (100, 118): (100, 200, 60, 90), (200, 230): (-50, -80, -30, 30)}
    for (a, b), (x0, x1, z0, z1) in spec.items():
        for f, x, z in ((a, x0, z0), (b, x1, z1)):
            cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=f, value=x)
            cmds.setKeyframe("arm_L0_0_jnt", attribute="rotateZ", time=f, value=z)


def _run(tmp: Path) -> None:
    from maya import cmds

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import clips as MC
    from tdrive_anim import composition as K
    from tdrive_anim import playstate
    from tdrive_anim import session as S
    from tdrive_anim import shot_scene as SS
    from tdrive_anim import ui_blend
    from tdrive_anim.core import sequence as Seq

    project.set_root(tmp)
    make_mini_rig.build()
    cmds.currentUnit(time="ntsc")
    key_clips(cmds)
    s = S.current()
    s.new("MisotenChara")
    for n, a, b in (("Idle", 0, 30), ("Run", 100, 118), ("Jump", 200, 230)):
        s.add_clip_from_range(n, a, b, direction="F" if n == "Run" else "")
    clips = {c["name"]: c for c in s.data["clips"]}
    joints = make_mini_rig.JOINTS
    pls = Seq.from_rows([{"clip": "A_MisotenChara_Idle", "holdSec": 1.0}, {"clip": "A_MisotenChara_Run_F", "holdSec": 1.0}], clips, 30)

    # ---- SF-3: 単位変換ノードを挟むつながり（multiplyDivide → rotateY）は、外したあとも元の値で動く
    md = cmds.createNode("multiplyDivide")
    cmds.setAttr(md + ".input1X", 7.0)
    cmds.setAttr(md + ".input2X", 1.0)
    cmds.connectAttr(md + ".outputX", "arm_R0_0_jnt.rotateY")
    check("前提: multiplyDivide → rotateY は単位変換ノードを挟む", bool(cmds.ls(type="unitConversion")))
    before = cmds.getAttr("arm_R0_0_jnt.rotateY")
    K.compose("blend", pls, joints, 30)
    check("SF-3: 合成中も rotateY が値を持つ", abs(cmds.getAttr("arm_R0_0_jnt.rotateY", time=10) - before) < 1e-4)
    K.remove("blend")
    src = cmds.listConnections("arm_R0_0_jnt.rotateY", source=True, destination=False, plugs=True, skipConversionNodes=True)
    check("SF-3: 外したあと、multiplyDivide → rotateY がつながっている", src == [md + ".outputX"], str(src))
    check("SF-3: 外したあとの値が元と同じ", abs(cmds.getAttr("arm_R0_0_jnt.rotateY") - before) < 1e-4)

    # ---- SF-4 / SF-5 / SF-6: 合成の作成・解除・作り直しは Maya の Undo に積まない
    cmds.undoInfo(state=True)
    cmds.polyCube(name="undoMarker")
    marker = cmds.undoInfo(query=True, undoName=True)
    K.compose("blend", pls, joints, 30)
    K.compose("blend", pls, joints, 30)
    K.remove("blend")
    check("SF-4/5: 合成の作成・作り直し・解除のあとも Undo の履歴は直前の操作のまま", cmds.undoInfo(query=True, undoName=True) == marker, str(cmds.undoInfo(query=True, undoName=True)))
    check("SF-4/5: Undo の状態は有効のまま戻る", cmds.undoInfo(query=True, state=True))

    # ---- SF-7: 合成中はオートキーを切り、外すと戻す
    cmds.autoKeyframe(state=True)
    K.compose("blend", pls, joints, 30)
    check("SF-7: 合成中はオートキーが切れている", not cmds.autoKeyframe(query=True, state=True))
    K.remove("blend")
    check("SF-7: 外すとオートキーが元（ON）に戻る", bool(cmds.autoKeyframe(query=True, state=True)))
    cmds.autoKeyframe(state=False)

    # ---- SF-8: 再生範囲の控えは共有。クリップ再生 → 合成 → どの順で止めても元に戻る
    for order in ("stop-first", "remove-first"):
        cmds.playbackOptions(minTime=5, maxTime=50, animationStartTime=0, animationEndTime=100, loop="once")
        cmds.currentTime(12, edit=True)
        MC.play(clips["A_MisotenChara_Idle"])
        K.compose("blend", pls, joints, 30)
        if order == "stop-first":
            MC.stop()
            K.remove("blend")
        else:
            K.remove("blend")
            MC.stop()
        r = (cmds.playbackOptions(query=True, minTime=True), cmds.playbackOptions(query=True, maxTime=True), cmds.playbackOptions(query=True, loop=True), cmds.currentTime(query=True))
        check(f"SF-8: クリップ再生 → 合成 → {order} でも元の範囲・ループ・時刻に戻る", r == (5, 50, "once", 12), str(r))
    # 合成のあとにクリップを再生し、合成を外す → クリップを止める
    cmds.playbackOptions(minTime=5, maxTime=50, animationStartTime=0, animationEndTime=100, loop="once")
    K.compose("blend", pls, joints, 30)
    MC.play(clips["A_MisotenChara_Run_F"])
    K.remove("blend")
    MC.stop()
    check("SF-8: 合成 → クリップ再生 → 合成を外す → 停止でも元に戻る", (cmds.playbackOptions(query=True, minTime=True), cmds.playbackOptions(query=True, maxTime=True)) == (5, 50))
    check("SF-8: 控えは残らない", not playstate._owners)

    # ---- SF-19: 合成中の保存は、保存の間だけ元のつながりに戻す（保存したファイルは元のつながり。保存後は合成につなぎ直す）
    plug = "spine_C0_0_jnt.translateX"
    orig_src = cmds.listConnections(plug, source=True, destination=False, plugs=True)[0]
    K.compose("blend", pls, joints, 30)
    scene = tmp / "comp_scene.ma"
    cmds.file(rename=str(scene))
    cmds.file(save=True, type="mayaAscii")  # kBeforeSave / kAfterSave のコールバック
    now = cmds.listConnections(plug, source=True, destination=False, plugs=True)[0]
    check("SF-19: 保存したあとは合成につながり直している", now.startswith("tdAnimComp_"), now)
    cmds.file(str(scene), open=True, force=True)
    again = cmds.listConnections(plug, source=True, destination=False, plugs=True)[0]
    check("SF-19: 保存されたシーンのジョイントは元のつながり", again == orig_src, again)
    check("SF-19: 開き直しても合成のノードは残り、外せる", K.active() == ["blend"] and K.remove("blend") == 1 and not cmds.ls("tdAnimComp_*"))

    # ---- SF-20: 合成中に足した接続は、外すとき上書きしない
    K.compose("blend", pls, joints, 30)
    mine = cmds.createNode("animCurveTU")
    cmds.setKeyframe(mine, time=1, value=3)
    cmds.setKeyframe(mine, time=9, value=5)
    cmds.connectAttr(mine + ".output", "spine_C0_0_jnt.translateY", force=True)  # 合成の対象外のチャンネルへ
    cmds.disconnectAttr(mine + ".output", "spine_C0_0_jnt.translateY")
    out = cmds.listConnections("spine_C0_0_jnt.translateX", source=True, destination=False, plugs=True)[0]  # 合成が繋いだチャンネルに割り込む
    cmds.connectAttr(mine + ".output", "spine_C0_0_jnt.translateX", force=True)
    K.remove("blend")
    cur = cmds.listConnections("spine_C0_0_jnt.translateX", source=True, destination=False, plugs=True, skipConversionNodes=True)
    check("SF-20: 合成中に足した接続を上書きしない", cur == [mine + ".output"], str(cur))
    check("SF-20: 残したことを警告に書く", any("translateX" in w for w in K.last_warnings), str(K.last_warnings))
    check("SF-20: ほかのチャンネルは元に戻る", cmds.listConnections("arm_L0_0_jnt.rotateZ", source=True, destination=False, plugs=True)[0].startswith("arm_L0_0_jnt_rotateZ"))

    # ---- SP-10: 回転順の違うジョイントでも pairBlend の回転順をそろえる
    cmds.file(new=True, force=True)
    make_mini_rig.build()
    cmds.currentUnit(time="ntsc")
    key_clips(cmds)
    cmds.setAttr("arm_L0_0_jnt.rotateOrder", 2)  # zxy ではなく yzx
    s.close()
    s.new("MisotenChara")
    for n, a, b in (("Idle", 0, 30), ("Run", 100, 118)):
        s.add_clip_from_range(n, a, b, direction="F" if n == "Run" else "")
    clips = {c["name"]: c for c in s.data["clips"]}
    pls = Seq.from_rows([{"clip": "A_MisotenChara_Idle", "holdSec": 1.0}, {"clip": "A_MisotenChara_Run_F", "holdSec": 1.0}], clips, 30)
    K.compose("blend", pls, make_mini_rig.JOINTS, 30)
    pbs = [n for n in cmds.ls(type="pairBlend") if "arm_L0_0_jnt" in n]
    check("SP-10: pairBlend の回転順がジョイントにつながる", pbs and all(cmds.getAttr(n + ".rotateOrder") == 2 for n in pbs), str([(n, cmds.getAttr(n + ".rotateOrder")) for n in pbs]))
    K.remove("blend")

    # ---- SP-5 / SP-6 / SF-17 / SF-5（UI）
    ui = ui_blend.BlendTab(s)
    s.listeners.append(ui.refresh)
    cmds.currentUnit(time="film")
    check("SP-5: シーンが 24 fps なら秒の換算も 24 fps", ui._fps() == 24)
    check("SP-5: animset と違えば警告が出る", "24 fps" in ui._fps_note() and "30 fps" in ui._fps_note(), ui._fps_note())
    cmds.currentUnit(time="ntsc")
    check("SP-5: 一致していれば警告なし", ui._fps_note() == "")
    ui.rows = [{"clip": "A_MisotenChara_Idle", "holdSec": 0.1, "crossFadeSec": 0.1, "switchAt": 1.0, "speed": 1.0},
               {"clip": "A_MisotenChara_Run_F", "holdSec": 0.1, "crossFadeSec": 0.2, "switchAt": 1.0, "speed": 1.0},
               {"clip": "A_MisotenChara_Idle", "holdSec": 0.0, "crossFadeSec": 0.2, "switchAt": 1.0, "speed": 1.0}]
    ui.build_from_rows()
    check("SP-6: フェードが終わる前に次の行が始まるときは警告", "3 行目" in ui.message.text() and "割り込み" in ui.message.text(), ui.message.text())
    ui.remove()
    clips_attr = s.data["clips"][1]
    s.update_clip(clips_attr["name"], crossFade=0.08)
    ui.rows = [{"clip": "A_MisotenChara_Idle", "holdSec": 1.0}, {"clip": "A_MisotenChara_Run_F"}]
    pl = Seq.from_rows(ui.rows, ui._clips(), 30)
    check("SP-4: 行に値が無ければクリップの crossFade（0.08 秒 = 2.4 フレーム）", abs(pl[1].fade_in - 2.4) < 1e-6)
    # SF-17: 合成を作らずに再生 → 閉じる
    speed0 = (cmds.playbackOptions(query=True, playbackSpeed=True), cmds.playbackOptions(query=True, maxPlaybackSpeed=True))
    ui.play_speed.setCurrentIndex(2)
    ui.play()
    cmds.play(state=False)
    changed = cmds.playbackOptions(query=True, playbackSpeed=True) != speed0[0]
    ui.detach()
    check("SF-17: 合成なしで再生した速さは、閉じると元に戻る", changed and (cmds.playbackOptions(query=True, playbackSpeed=True), cmds.playbackOptions(query=True, maxPlaybackSpeed=True)) == speed0)
    # SF-5: スライダーのドラッグ中は作り直さない（compose の回数を数える）
    ui.use_graph()
    ui.speed.setValue(40)
    ui.build_from_graph()
    calls = []
    real = K.compose
    K.compose = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    try:
        for v in (10, 20, 30, 40, 50, 60):
            ui.speed.setValue(v)
        before_n = len(calls)
        ui.flush_rebuild()
    finally:
        K.compose = real
    check("SF-5: ドラッグ中は作り直さず、止まってから 1 回だけ", before_n == 0 and len(calls) == 1, f"{before_n} {len(calls)}")
    ui.detach()
    s.listeners.remove(ui.refresh)
    check("終わり: 合成は残らない", K.active() == [] and not cmds.ls("tdAnimComp_*"))


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
