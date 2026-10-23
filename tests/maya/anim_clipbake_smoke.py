"""クリップ出力（A_*.fbx）のベイクが複合プラグ駆動のリグ（mGear）でも正しいかのスモーク（AN-F-24。mayapy・ヘッドレス）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_clipbake_smoke.py [mini|live]

`joint.rotate` ← `decomposeMatrix.outputRotate`（複合プラグ）でつながるジョイントを含むリグで、クリップ（100〜118）を `export_clip_in_place` で出力し、
FBX を読み戻すと: キーが 0〜長さに収まる・値が元の評価と一致する・コンストレイント系のノードが入っていない。
live は、実機のキー入りのシーン（環境変数 TDRIVE_LIVE_SCENE のパス。読むだけ）で、animset のクリップ範囲がそのままキーと合う。
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
TOL = 0.05


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def read_fbx(cmds, mel, path: Path) -> None:
    cmds.file(new=True, force=True)
    cmds.currentUnit(time="ntsc")
    cmds.loadPlugin("fbxmaya", quiet=True)
    mel.eval(f'FBXRead -f "{path.as_posix()}"')
    cmds.file(path.as_posix(), i=True, type="FBX", ignoreVersion=True, options="fbx", mergeNamespacesOnClash=False)


def run(which: str) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_clipbake_"))
    try:
        _mini(tmp) if which == "mini" else _live(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _mini(tmp: Path) -> None:
    from maya import cmds, mel

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import export as X
    from tdrive_anim import session as S

    project.set_root(tmp)
    rig = make_mini_rig.build()
    cmds.currentUnit(time="ntsc")
    for f, v in ((100, 0), (118, 40)):
        cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=f, value=v)
        cmds.setKeyframe("arm_L0_0_jnt", attribute="rotateZ", time=f, value=v * 2)
    tail = make_mini_rig.add_compound_joint()["joint"]  # translate / rotate が複合プラグごと decomposeMatrix から。composeMatrix にキー 100〜118
    s = S.current()
    s.new("MisotenChara", meshes=[rig["mesh"]])
    s.add_clip_from_range("Run", 100, 118, direction="F")
    s.update_clip("A_MisotenChara_Run_F", rootMotion="bake")
    clip = s.data["clips"][0]
    probes = {"tx": (tail + ".translateX", 0.0), "rz": (tail + ".rotateZ", 0.0), "root": ("spine_C0_0_jnt.translateX", 0.0), "arm": ("arm_L0_0_jnt.rotateZ", 0.0)}
    expected = {k: [cmds.getAttr(p, time=100 + i) for i in range(19)] for k, (p, _) in probes.items()}
    check("前提: 複合プラグ駆動のジョイントが動く（100〜118 で値が変わる）", max(expected["rz"]) - min(expected["rz"]) > 5 and max(expected["tx"]) - min(expected["tx"]) > 1)
    out = tmp / "A_MisotenChara_Run_F.fbx"
    scene = tmp / "rig.mb"
    cmds.file(rename=str(scene))
    cmds.file(save=True, type="mayaBinary")
    X.export_clip_in_place(s.data, clip, out)
    check("FBX が書かれる", out.is_file() and out.stat().st_size > 0)
    # 書き出し後の（破壊された）シーンの状態: 複合プラグの接続が残っていない・キーの先頭が 0
    comp_left = [f"{tail}.{f}" for f in ("translate", "rotate", "scale") if cmds.connectionInfo(f"{tail}.{f}", sourceFromDestination=True)]
    check("ベイク後、複合プラグの入力が残っていない", not comp_left, str(comp_left))
    times = cmds.keyframe([tail, "spine_C0_0_jnt", "arm_L0_0_jnt"], query=True, timeChange=True) or []
    check("ベイク後、キーが 0〜18 に収まる（先頭 0）", times and min(times) == 0 and max(times) == 18, f"{min(times or [0])}〜{max(times or [0])}")
    read_fbx(cmds, mel, out)
    all_t = sorted({round(t, 3) for c in cmds.ls(type="animCurve") for t in (cmds.keyframe(c, query=True, timeChange=True) or [])})
    check("FBX を読み戻すとキーは 0〜18 フレーム", all_t and all_t[0] == 0.0 and all_t[-1] == 18.0, str(all_t[:3]) + "…" + str(all_t[-2:]))
    check("コンストレイント系のノードが入っていない", not cmds.ls(type="constraint") and not cmds.ls("*matrix*", "*Matrix*", type=["composeMatrix", "decomposeMatrix"]))
    bad = []
    for k, (plug, _) in probes.items():
        for i in (0, 4, 9, 14, 18):
            got = cmds.getAttr(plug, time=i)
            if abs(got - expected[k][i]) > TOL:
                bad.append((k, i, round(got, 3), round(expected[k][i], 3)))
    check("読み戻した各フレームの値が元の評価と一致（複合プラグ駆動のジョイントも）", not bad, str(bad[:5]))


def _live(tmp: Path) -> None:
    from maya import cmds, mel

    from tdrive import project
    from tdrive_anim import export as X
    from tdrive_anim.core import animset as A

    scene = os.environ.get("TDRIVE_LIVE_SCENE")
    aset = REPO / "build" / "unity" / "MisotenChara" / "animset.json"
    if not scene or not Path(scene).is_file() or not aset.is_file():
        check("TDRIVE_LIVE_SCENE（実機のキー入りシーン）が無いのでスキップ", True)
        return
    project.set_root(tmp)
    cmds.file(scene, open=True, force=True, ignoreVersion=True, prompt=False)  # 読むだけ
    d = A.load(aset)
    clip = A.find_clip(d, "A_MisotenChara_Run_F")
    s0, e0 = clip["start"], clip["end"]
    joints = cmds.ls(type="joint")
    exp = {(j, ch): [cmds.getAttr(f"{j}.{ch}", time=s0 + i) for i in (0, (e0 - s0) // 2, e0 - s0)] for j in ("arm_L0_0_jnt", "spine_C0_0_jnt") for ch in ("rz", "ty")}
    out = tmp / "A_MisotenChara_Run_F.fbx"
    X.export_clip_in_place(d, clip, out)
    read_fbx(cmds, mel, out)
    all_t = sorted({round(t, 3) for c in cmds.ls(type="animCurve") for t in (cmds.keyframe(c, query=True, timeChange=True) or [])})
    INFO.append(f"ジョイント {len(joints)} 本、キーの範囲 {all_t[:1]}〜{all_t[-1:]}")
    check("実機シーン: FBX のキーが 0〜長さ", all_t and all_t[0] == 0.0 and all_t[-1] == float(e0 - s0), str(all_t[:2]) + str(all_t[-2:]))
    check("実機シーン: コンストレイント系が入っていない", not cmds.ls(type="constraint"))
    bad = [(k, i) for k, vals in exp.items() for i, t in zip(range(3), (0, (e0 - s0) // 2, e0 - s0)) if abs(cmds.getAttr(f"{k[0]}.{k[1]}", time=t) - vals[i]) > TOL]
    check("実機シーン: 読み戻した値が元の評価と一致", not bad, str(bad))


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
