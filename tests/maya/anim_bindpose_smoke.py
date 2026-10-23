"""モデル出力の骨がバインドポーズになるかのスモーク（AN-F-14。mayapy・ヘッドレス）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_bindpose_smoke.py [mini|misoten]

mGear のように、ジョイントが decomposeMatrix に（複合プラグごと）駆動され、駆動元のコントローラにキーがあるリグで、
任意のフレームで `_to_bind_pose` を呼んでも、骨のワールド行列 × スキンの bindPreMatrix が単位行列（= バインドポーズ）になる。
バインドポーズに戻せないリグ（ジョイント自身にキーがあって dagPose も無い）は警告になる。
misoten は `assets/misoten/MisotenChara_Rig.ma`（Git 管理外）を読み込むだけ（保存しない）。
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

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
INFO: list[str] = []
MISOTEN = REPO / "assets" / "misoten" / "MisotenChara_Rig.ma"


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run(which: str) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_bind_"))
    try:
        _mini(tmp) if which == "mini" else _misoten(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def wire_controlled(cmds, joint: str, ctl: str) -> None:
    """joint を、コントローラ ctl の translate / rotate から composeMatrix → decomposeMatrix で複合プラグごと駆動する（mGear 型）。
    ctl がデフォルト（0）のとき、joint は今のローカル位置（バインドポーズ）になる。"""
    base_t = cmds.getAttr(joint + ".translate")[0]
    base_r = cmds.getAttr(joint + ".rotate")[0]
    pma = cmds.createNode("plusMinusAverage", name=joint + "_offset")
    cmds.setAttr(pma + ".input3D[1]", *base_t)
    cmds.connectAttr(ctl + ".translate", pma + ".input3D[0]")
    comp = cmds.createNode("composeMatrix", name=joint + "_compose")
    dec = cmds.createNode("decomposeMatrix", name=joint + "_decompose")
    cmds.connectAttr(pma + ".output3D", comp + ".inputTranslate")
    cmds.connectAttr(ctl + ".rotate", comp + ".inputRotate")
    cmds.connectAttr(comp + ".outputMatrix", dec + ".inputMatrix")
    cmds.connectAttr(dec + ".outputTranslate", joint + ".translate", force=True)
    cmds.connectAttr(dec + ".outputRotate", joint + ".rotate", force=True)


def _mini(tmp: Path) -> None:
    from maya import cmds

    import make_mini_rig
    from tdrive_anim import export as X

    rig = make_mini_rig.build()
    joints = rig["joints"]
    cmds.currentUnit(time="ntsc")
    ctls = []
    for j in ("arm_L0_0_jnt", "arm_R0_0_jnt", "spine_C0_2_jnt"):
        c = cmds.spaceLocator(name=j.replace("_jnt", "_ctl"))[0]
        wire_controlled(cmds, j, c)
        cmds.setKeyframe(c, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(c, attribute="translateX", time=30, value=12)
        cmds.setKeyframe(c, attribute="rotateZ", time=0, value=0)
        cmds.setKeyframe(c, attribute="rotateZ", time=30, value=50)
        ctls.append(c)
    cmds.sets(ctls[:2], name="rig_controllers_grp")  # 3 つ目はキーで見つかる
    cmds.currentTime(0, edit=True)
    check("前提: バインドポーズなら骨が bindPreMatrix と一致", not X.bind_pose_mismatches(joints))
    cmds.currentTime(20, edit=True)
    posed = X.bind_pose_mismatches(joints)
    check("前提: 20 フレーム目ではコントローラが動いて骨がずれている", len(posed) >= 3, str(posed))
    warns = X._to_bind_pose(joints)
    check("コントローラを戻すと骨がバインドポーズに一致する", not X.bind_pose_mismatches(joints) and not warns, f"{X.bind_pose_mismatches(joints)} {warns}")
    check("animCurve が残らない", not X._anim_curves())

    # 別のフレームでも同じ結果（出力時のフレームに依らない）
    make_mini_rig.build()
    joints = make_mini_rig.JOINTS
    c = cmds.spaceLocator(name="arm_L0_0_ctl")[0]
    wire_controlled(cmds, "arm_L0_0_jnt", c)
    cmds.setKeyframe(c, attribute="rotateZ", time=0, value=0)
    cmds.setKeyframe(c, attribute="rotateZ", time=30, value=50)
    ok = []
    for f in (0, 7, 30):
        cmds.currentTime(f, edit=True)
        X._to_bind_pose(joints)
        ok.append(not X.bind_pose_mismatches(joints))
        make_mini_rig.build()
        c = cmds.spaceLocator(name="arm_L0_0_ctl")[0]
        wire_controlled(cmds, "arm_L0_0_jnt", c)
        cmds.setKeyframe(c, attribute="rotateZ", time=0, value=0)
        cmds.setKeyframe(c, attribute="rotateZ", time=30, value=50)
    check("どのフレームで出力しても骨がバインドポーズ", all(ok), str(ok))

    # 戻せないリグ: ジョイントが直接ポーズされていて dagPose が無い
    make_mini_rig.build()
    cmds.delete(cmds.ls(type="dagPose"))
    cmds.setAttr("arm_L0_0_jnt.rotateZ", 50)
    warns = X._to_bind_pose(make_mini_rig.JOINTS)
    check("ジョイントが直接ポーズされていて dagPose も無いと警告（出力は続ける）", len(warns) == 1 and "バインドポーズに戻せない" in warns[0] and "arm_L0_0_jnt" in warns[0], str(warns))

    # dagPose があれば、ジョイント自身のキーでも戻せる
    make_mini_rig.build()  # スキンを作ると bindPose の dagPose もできる
    cmds.setKeyframe("arm_L0_0_jnt", attribute="rotateZ", time=0, value=0)
    cmds.setKeyframe("arm_L0_0_jnt", attribute="rotateZ", time=30, value=50)
    cmds.currentTime(20, edit=True)
    warns = X._to_bind_pose(make_mini_rig.JOINTS)
    check("バインドポーズの dagPose があればジョイント自身のキーでも戻る", not warns and not X.bind_pose_mismatches(make_mini_rig.JOINTS), str(warns))


def _misoten(tmp: Path) -> None:
    from maya import cmds

    from tdrive_anim import export as X

    if not MISOTEN.is_file():
        check("MisotenChara_Rig.ma が無いのでスキップ", True)
        return
    cmds.file(new=True, force=True)
    cmds.file(str(MISOTEN), open=True, force=True, ignoreVersion=True, prompt=False)  # 読むだけ。保存しない
    from tdrive_anim import composition as K0

    K0.remove()  # 保存されたリグに合成が残っていても外す（メモリ上だけ。ファイルは書き換えない）
    joints = cmds.ls(type="joint")
    sets = cmds.ls("rig_controllers_grp", "*:rig_controllers_grp", type="objectSet")
    members = [m for st in sets for m in (cmds.sets(st, query=True) or [])]
    check("rig_controllers_grp がある", bool(members), str(sets))
    # コントローラを動かしてキーを打つ（メモリ上だけ）
    moved = 0
    for m in members[:40]:
        for a in ("tx", "ry"):
            plug = f"{m}.{a}"
            if cmds.objExists(plug) and not cmds.getAttr(plug, lock=True) and not cmds.listConnections(plug, source=True, destination=False):
                cmds.setKeyframe(plug, time=0, value=0)
                cmds.setKeyframe(plug, time=30, value=15)
                moved += 1
    cmds.currentTime(20, edit=True)
    before = X.bind_pose_mismatches(joints)
    INFO.append(f"ジョイント {len(joints)} 本、キーを打った属性 {moved}、動かしたあとのずれ {len(before)} 本")
    warns = X._to_bind_pose(joints)
    after = X.bind_pose_mismatches(joints)
    check("実機リグ: 出力時のポーズに依らず骨が bindPreMatrix と一致", not after and not warns, f"{after[:5]} {warns}")
    cmds.file(new=True, force=True)


if __name__ == "__main__":
    maya.standalone.initialize(name="python")
    try:
        run(sys.argv[1] if len(sys.argv) > 1 else "mini")
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
