"""複合プラグでつながるジョイント（mGear 型）の合成のスモーク（AN-F-6。mayapy・ヘッドレス）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_compound_smoke.py [mini|misoten]

`joint.rotate` ← `decomposeMatrix.outputRotate` のように、複合プラグごと別ノードから来るジョイントでも、
合成が作れる・各フレームの値が元の評価と一致する・外すと元の接続（複合プラグ）に戻る・保存の間だけ元へ戻せる、を確かめる。
misoten は `assets/misoten/MisotenChara_Rig.ma`（Git 管理外）を**読み込むだけ**で、書き換えない。
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

TOL = 0.3
RESULTS: list[tuple[str, bool, str]] = []
INFO: list[str] = []
MISOTEN = REPO / "assets" / "misoten" / "MisotenChara_Rig.ma"


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def src_of(cmds, plug: str):
    """そのプラグ自身の入力の元（子プラグの入力は含めない）。"""
    return cmds.connectionInfo(plug, sourceFromDestination=True) or None


def kid_src(cmds, plug: str):
    """子プラグ自身の入力（親の複合プラグ経由の入力は含めない）。"""
    got = cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
    return got[0] if got else None


def run(which: str) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_compound_"))
    try:
        _mini(tmp) if which == "mini" else _misoten(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _mini(tmp: Path) -> None:
    from maya import cmds

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import composition as K
    from tdrive_anim import session as S
    from tdrive_anim.core import sequence as Seq

    project.set_root(tmp)
    make_mini_rig.build()
    cmds.currentUnit(time="ntsc")
    make_mini_rig.key_motion(0, 30)
    for f, v in ((100, 0), (118, 40)):
        cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=f, value=v)
    info = make_mini_rig.add_compound_joint()
    tail = info["joint"]
    joints = make_mini_rig.JOINTS + [tail]
    check("前提: 複合プラグは入力あり・子プラグは入力なし", src_of(cmds, tail + ".rotate") and not cmds.listConnections(tail + ".rotateZ", source=True, destination=False))
    s = S.current()
    s.new("MisotenChara")
    for n, a, b in (("Idle", 0, 30), ("Run", 100, 118)):
        s.add_clip_from_range(n, a, b, direction="F" if n == "Run" else "")
    clips = {c["name"]: c for c in s.data["clips"]}
    fps = 30
    pls = Seq.from_rows([{"clip": "A_MisotenChara_Idle", "holdSec": 1.0}, {"clip": "A_MisotenChara_Run_F", "holdSec": 0.6, "crossFadeSec": 0.2}], clips, fps)
    ch = K.animated_channels(joints)
    check("複合プラグでつながるジョイントも合成の対象（6 チャンネル）", tail in ch and sorted(ch[tail]) == sorted(K.TRANSLATE + K.ROTATE), str(ch.get(tail)))
    times = [0, 10, 29, 30.5, 33, 36, 50]
    plugs = [tail + ".translateX", tail + ".rotateZ", "spine_C0_0_jnt.translateX"]
    exp = {t: {p: sum(w * cmds.getAttr(p, time=Seq.source_frame(next(x for x in pls if x.clip == c), t)) for c, w in Seq.pose_weights(pls, t)) for p in plugs} for t in times}
    orig = {"translate": src_of(cmds, tail + ".translate"), "rotate": src_of(cmds, tail + ".rotate")}
    res = K.compose("cmp", pls, joints, fps)
    check("合成ができる", K.active() == ["cmp"] and res.joints >= 4, str(res))
    bad = []
    for t in times:
        cmds.currentTime(t, edit=True)
        for p in plugs:
            if abs(cmds.getAttr(p) - exp[t][p]) > TOL:
                bad.append((t, p, round(cmds.getAttr(p), 3), round(exp[t][p], 3)))
    check("各フレームの値が元の評価のブレンドと一致", not bad, str(bad[:5]))
    check("合成中は複合プラグが切り離され、子プラグが合成につながる", src_of(cmds, tail + ".rotate") is None and (kid_src(cmds, tail + ".rotateZ") or "").startswith("tdAnimComp_"))
    # 保存の間だけ元へ
    n = K.suspend_for_save()
    now = {"translate": src_of(cmds, tail + ".translate"), "rotate": src_of(cmds, tail + ".rotate")}
    check("保存の間は複合プラグが元につながる（子プラグは切れる）", n > 0 and now == orig and kid_src(cmds, tail + ".rotateZ") is None, f"{now} {orig}")
    m = K.resume_after_save()
    check("保存のあと合成につなぎ直す", m == n and src_of(cmds, tail + ".rotate") is None and (kid_src(cmds, tail + ".rotateZ") or "").startswith("tdAnimComp_"), f"{n} {m}")
    cmds.currentTime(10, edit=True)
    check("つなぎ直したあとも値が合う", abs(cmds.getAttr(tail + ".rotateZ") - exp[10][tail + ".rotateZ"]) < TOL)
    K.remove("cmp")
    back = {"translate": src_of(cmds, tail + ".translate"), "rotate": src_of(cmds, tail + ".rotate")}
    check("外すと複合プラグの元の接続に戻る", back == orig and all(not kid_src(cmds, f"{tail}.{c}") for c in K.CHANNELS), f"{back} {orig}")
    check("外すと作ったノードが残らない", not cmds.ls("tdAnim*"))
    # 作り直しても取り違えない
    K.compose("cmp", pls, joints, fps)
    K.compose("cmp", pls, joints, fps)
    K.remove()
    check("作り直して外しても複合プラグに戻る", {"translate": src_of(cmds, tail + ".translate"), "rotate": src_of(cmds, tail + ".rotate")} == orig)


def _misoten(tmp: Path) -> None:
    from maya import cmds

    from tdrive import project
    from tdrive_anim import composition as K
    from tdrive_anim import session as S
    from tdrive_anim.core import sequence as Seq

    if not MISOTEN.is_file():
        check("MisotenChara_Rig.ma が無いのでスキップ", True)
        return
    project.set_root(tmp)
    cmds.file(new=True, force=True)
    cmds.file(str(MISOTEN), open=True, force=True, ignoreVersion=True, prompt=False)  # 読むだけ。保存しない
    joints = cmds.ls(type="joint")
    INFO.append(f"ジョイント {len(joints)} 本")
    ch = K.animated_channels(joints)
    check("実機リグで合成の対象のジョイントが見つかる", len(ch) > 50, str(len(ch)))
    before = {j + "." + f: src_of(cmds, j + "." + f) for j in ch for f in ("translate", "rotate", "scale")}
    s = S.current()
    s.new("MisotenChara")
    for n, a, b in (("Idle", 0, 30), ("Run", 100, 118)):
        s.add_clip_from_range(n, a, b, direction="F" if n == "Run" else "")
    clips = {c["name"]: c for c in s.data["clips"]}
    pls = Seq.from_rows([{"clip": "A_MisotenChara_Idle", "holdSec": 1.0}, {"clip": "A_MisotenChara_Run_F", "crossFadeSec": 0.2}], clips, 30)
    res = K.compose("real", pls, joints, 30)
    check("実機リグで合成が作られる", K.active() == ["real"] and res.joints > 50, str(res))
    K.remove("real")
    after = {p: src_of(cmds, p) for p in before}
    diff = [p for p in before if before[p] != after[p]]
    check("外すと元の接続（複合プラグ）に戻る", not diff, str(diff[:5]))
    cmds.file(new=True, force=True)  # 保存しないで閉じる


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
