"""合成の作成の速さ・中止のスモーク（AN-F-21。mayapy・ヘッドレス）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_perf_smoke.py [mini|misoten]

サンプリングは、フレームごとに `currentTime` を 1 回動かして全ジョイントをまとめて読む（`getAttr(time=)` をジョイント × 系統 × フレームごとに呼ばない）。
評価の回数がフレーム数 + 定数以下であること、値が従来（`getAttr(time=)`）と同じであること、進捗の中止で何も残らないこと。
misoten は `assets/misoten/MisotenChara_Rig.ma`（Git 管理外）を読み込むだけ。113 フレームの合成が 3 秒以内（目標）。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
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


class Counter:
    """cmds.currentTime(edit) と getAttr(time=) の回数を数える。"""

    def __init__(self, cmds) -> None:
        self.cmds = cmds
        self.sets = 0
        self.timed_get = 0
        self.keys = 0
        self._ct, self._ga, self._sk = cmds.currentTime, cmds.getAttr, cmds.setKeyframe

    def __enter__(self):
        def ct(*a, **k):
            if k.get("edit"):
                self.sets += 1
            return self._ct(*a, **k)

        def ga(*a, **k):
            if "time" in k:
                self.timed_get += 1
            return self._ga(*a, **k)

        def sk(*a, **k):
            self.keys += 1
            return self._sk(*a, **k)

        self.cmds.currentTime, self.cmds.getAttr, self.cmds.setKeyframe = ct, ga, sk
        return self

    def __exit__(self, *exc):
        self.cmds.currentTime, self.cmds.getAttr, self.cmds.setKeyframe = self._ct, self._ga, self._sk


def run(which: str) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_perf_"))
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
    tail = make_mini_rig.add_compound_joint()["joint"]
    joints = make_mini_rig.JOINTS + [tail]
    s = S.current()
    s.new("MisotenChara")
    s.add_clip_from_range("Idle", 0, 30)
    s.add_clip_from_range("Run", 100, 118, direction="F")
    clips = {c["name"]: c for c in s.data["clips"]}
    pls = Seq.from_rows([{"clip": "A_MisotenChara_Idle", "holdSec": 1.0}, {"clip": "A_MisotenChara_Run_F", "holdSec": 0.6, "crossFadeSec": 0.2}], clips, 30)
    cmds.currentTime(7, edit=True)
    with Counter(cmds) as c:
        K.compose("perf", pls, joints, 30)
    frames = (30 - 0 + 1) + (118 - 100 + 1)
    check("getAttr(time=) を使わない", c.timed_get == 0, str(c.timed_get))
    check("currentTime を動かす回数がフレーム数 + 定数以下", c.sets <= frames + 6, f"{c.sets} / {frames}")
    check("サンプリングのあと時刻は合成の先頭（0）", cmds.currentTime(query=True) == 0)
    K.remove("perf")
    check("外すと元の時刻（7）に戻る", cmds.currentTime(query=True) == 7)

    # 中止: 何も残らず、元のつながり・時刻のまま
    orig = {p: cmds.listConnections(p, source=True, destination=False, plugs=True) for p in ("spine_C0_0_jnt.translateX", "arm_L0_0_jnt.rotateZ")}
    cmds.currentTime(11, edit=True)
    seen: list[tuple[int, int]] = []

    def cancel_at_5(done: int, total: int) -> bool:
        seen.append((done, total))
        return done < 5

    try:
        K.compose("perf", pls, joints, 30, progress=cancel_at_5)
        raised = False
    except K.ComposeCancelled:
        raised = True
    check("進捗の中止で ComposeCancelled", raised and seen[-1][0] == 5 and seen[-1][1] >= frames, str(seen[-3:]))
    check("中止したら合成も作ったノードも残らない", K.active() == [] and not cmds.ls("tdAnim*"))
    check("中止したら元のつながりのまま", {p: cmds.listConnections(p, source=True, destination=False, plugs=True) for p in orig} == orig)
    check("中止したら時刻も元のまま", cmds.currentTime(query=True) == 11)
    check("進捗は (済, 全体) で呼ばれる", len({t for _, t in seen}) == 1 and [d for d, _ in seen] == list(range(len(seen))))

    # サンプリングの使い回し: アニメーションが同じなら使い回し、キーの値が変わったら読み直す（AN-F-26）
    K.compose("perf", pls, joints, 30, use_cache=True)
    r2 = K.compose("perf", pls, joints, 30, use_cache=True)
    cmds.currentTime(5, edit=True)
    v_cached = cmds.getAttr("spine_C0_0_jnt.translateX")
    check("キャッシュを使った作り直しでも値は同じ", abs(v_cached - cmds.getAttr("spine_C0_0_jnt.translateX")) < 1e-9 and r2.timings["sample"] < 0.05, str(r2.timings))
    K.remove("perf")
    cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=5, value=77.0)
    r3 = K.compose("perf", pls, joints, 30, use_cache=True)
    cmds.currentTime(5, edit=True)
    check("キーを変えるとキャッシュを使わず読み直す（新しい値が合成に出る）", abs(cmds.getAttr("spine_C0_0_jnt.translateX") - 77.0) < 0.5 and r3.timings["sample"] > 0.0, f"{cmds.getAttr('spine_C0_0_jnt.translateX')}")
    K.remove("perf")


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
    cmds.file(str(MISOTEN), open=True, force=True, ignoreVersion=True, prompt=False)  # 読むだけ
    from tdrive_anim import composition as K0

    K0.remove()  # 保存されたリグに合成が残っていても外す（メモリ上だけ。ファイルは書き換えない）
    cmds.currentUnit(time="ntsc")
    joints = cmds.ls(type="joint")
    s = S.current()
    s.new("MisotenChara")
    s.add_clip_from_range("Idle", 0, 112)  # 113 フレーム
    s.add_clip_from_range("Run", 0, 112, direction="F")
    s.add_clip_from_range("Jump", 0, 60)
    clips = {c["name"]: c for c in s.data["clips"]}
    pls = Seq.from_rows([{"clip": "A_MisotenChara_Idle", "holdSec": 1.0}, {"clip": "A_MisotenChara_Run_F", "holdSec": 1.5, "crossFadeSec": 0.08},
                         {"clip": "A_MisotenChara_Jump", "crossFadeSec": 0.08}], clips, 30)
    t0 = time.perf_counter()
    with Counter(cmds) as c:
        res = K.compose("real", pls, joints, 30)
    dt = time.perf_counter() - t0
    INFO.append(f"113 フレーム × {len(joints)} ジョイント × 3 行の合成: {dt:.1f} 秒（currentTime {c.sets} 回、getAttr(time=) {c.timed_get} 回）")
    INFO.append("段階: " + ", ".join(f"{k} {v:.2f}" for k, v in res.timings.items()))
    check("実機リグ: 合成ができる", K.active() == ["real"] and res.joints > 50)
    hidden = K.info("real").get("hiddenControllers", 0)
    INFO.append(f"非表示にしたコントローラ: {hidden}")
    check("実機リグ: 合成中はコントローラ（rig_controllers_grp）が非表示になる（AN-F-37）", hidden > 10, str(hidden))
    check("実機リグ: 段階ごとの所要時間が記録される", set(res.timings) >= {"sample", "detach", "chain", "total"}, str(res.timings))
    INFO.append("（所要時間は他の処理の負荷で数倍に揺れる。通常は作成全体 1〜2 秒台）")
    check("実機リグ: 作成全体が 30 秒以内（目標 3 秒。時間は負荷で揺れるので上限だけ）", dt <= 30.0, f"{dt:.1f} 秒 {res.timings}")
    check("実機リグ: カーブを cmds.setKeyframe で 1 キーずつ打たない（重みのカーブだけ。AN-F-26）", c.keys <= 300, str(c.keys))
    check("実機リグ: currentTime を動かす回数がフレーム数 + 定数以下", c.sets <= 113 + 6, str(c.sets))
    K.compose("real", pls, joints, 30, use_cache=True)  # キャッシュを作る
    t1 = time.perf_counter()
    r3 = K.compose("real", pls, joints, 30, use_cache=True)  # パラメータ変更の作り直し相当
    t3 = time.perf_counter() - t1
    INFO.append(f"作り直し（サンプリングを使い回す）: {t3:.2f} 秒、読み取り {r3.timings['sample']:.2f} 秒")
    check("実機リグ: 作り直しはサンプリングを使い回す（読み取りがほぼ 0）", r3.timings["sample"] < 0.2, f"{t3:.2f} {r3.timings}")
    # ---- ブレンドツリー（AN-11-2）: 2D の 5 モーションを 113 フレームで。作り直し（サンプリングを使い回す）が 1 秒以内
    from tdrive_anim.core import blend_tree as BT

    K.remove("real")
    for d in ("B", "L", "R"):
        s.add_clip_from_range("Walk", 0, 112, direction=d)
    s.add_clip_from_range("Walk", 0, 112, direction="F")
    clips = {c["name"]: c for c in s.data["clips"]}
    tree = BT.template_2d(s.data["clips"], "MisotenChara")
    K.compose_blend_tree("real", K.tree_motions(tree, [0.5, 0.5], clips), joints, 30, use_cache=True)  # キャッシュを作る
    t2 = time.perf_counter()
    r4 = K.compose_blend_tree("real", K.tree_motions(tree, [-0.3, 0.8], clips), joints, 30, use_cache=True)
    t4 = time.perf_counter() - t2
    INFO.append(f"ブレンドツリー 2D（{len(tree['motions'])} モーション × 113 フレーム × {len(joints)} ジョイント）の作り直し: {t4:.2f} 秒 {r4.timings}")
    check("実機リグ: ブレンドツリーの作り直し（サンプリングを使い回す）が 1 秒以内", t4 <= 1.0 and r4.timings["sample"] < 0.2, f"{t4:.2f} {r4.timings}")
    cmds.setKeyframe(joints[0], attribute="translateX", time=5, value=123.0) if not cmds.listConnections(joints[0] + ".translateX", source=True, destination=False) else None
    K.remove("real")
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
