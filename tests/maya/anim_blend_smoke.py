"""ブレンド / 遷移の合成のスモーク（AN-4 / AN-5。mayapy・ヘッドレス。dx11 / Render Override / Time Editor は使わない）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_blend_smoke.py

確かめること: Idle → Run を 0.08 秒でクロスフェードした合成を作ると、各時刻のジョイントの値が「元のカーブの値 × 重み」の線形ブレンドになる・
合成を外すと元のつながり・値・再生範囲に戻る・状態図（Speed / ActionState）のパラメータで遷移する・UpperBody レイヤーが上半身にだけ乗る・
UI（遷移列の表・パラメータ・合成の生成 / 解除）。
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

TOL = 0.3  # 元のカーブはスプライン、合成は 1 フレームごとのサンプルの線形補間なので、わずかに（値の 0.1% ほど）ずれる
RESULTS: list[tuple[str, bool, str]] = []
INFO: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_blend_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def key_clips(cmds) -> None:
    """3 つのクリップ分のキー: Idle 0〜30、Run 100〜118、Jump 200〜230。値は見分けがつくように大きくずらす。"""
    spec = {
        (0, 30): {"tx": (0, 30), "rz": (0, 20), "bx": (0, 0)},
        (100, 118): {"tx": (100, 200), "rz": (60, 90), "bx": (10, 40)},
        (200, 230): {"tx": (-50, -80), "rz": (-30, 30), "bx": (-20, 20)},
    }
    for (a, b), v in spec.items():
        for f, i in ((a, 0), (b, 1)):
            cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=f, value=v["tx"][i])
            cmds.setKeyframe("arm_L0_0_jnt", attribute="rotateZ", time=f, value=v["rz"][i])
            cmds.setKeyframe("arm_R0_0_jnt", attribute="rotateX", time=f, value=v["bx"][i])
            cmds.setKeyframe("spine_C0_1_jnt", attribute="rotateX", time=f, value=v["bx"][i])


def _run(tmp: Path) -> None:
    from maya import cmds

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import composition as K
    from tdrive_anim import session as S
    from tdrive_anim.core import animset as A
    from tdrive_anim.core import sequence as Seq
    from tdrive_anim.core import state_machine as M

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
    fps = 30
    cmds.playbackOptions(minTime=5, maxTime=50, animationStartTime=0, animationEndTime=100, loop="once")
    cmds.currentTime(12, edit=True)

    plugs = ["spine_C0_0_jnt.translateX", "arm_L0_0_jnt.rotateZ", "arm_R0_0_jnt.rotateX", "spine_C0_1_jnt.rotateX"]
    orig_src = {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in plugs}

    def orig_value(clip: str, plug: str, src_frame: float) -> float:
        return cmds.getAttr(plug, time=src_frame)

    orig15 = cmds.getAttr("spine_C0_0_jnt.translateX", time=15)
    # ---- 元の値（合成前に計算しておく）
    rows = [{"clip": "A_MisotenChara_Idle", "holdSec": 1.0}, {"clip": "A_MisotenChara_Run_F", "holdSec": 1.5, "crossFadeSec": 0.08}]
    pls = Seq.from_rows(rows, clips, fps)
    times = [0, 10, 29, 30.05, 31.2, 32.4, 40, 60.5, 75]
    expected = {}
    for t in times:
        expected[t] = {p: sum(w * orig_value(c, p, Seq.source_frame(next(x for x in pls if x.clip == c), t)) for c, w in Seq.pose_weights(pls, t)) for p in plugs}

    res = K.compose("blend", pls, joints, fps)
    check("合成ができる", res.joints >= 3 and res.plugs >= 4 and K.active() == ["blend"], str(res))
    check("再生範囲が合成の長さになる", cmds.playbackOptions(query=True, maxTime=True) == Seq.total_frames(pls) and cmds.playbackOptions(query=True, minTime=True) == 0)
    bad = []
    for t in times:
        cmds.currentTime(t, edit=True)
        for p in plugs:
            got = cmds.getAttr(p)
            if abs(got - expected[t][p]) > TOL:
                bad.append((t, p, round(got, 3), round(expected[t][p], 3)))
    check("各時刻の値 = 元のカーブの線形ブレンド（Idle → Run 0.08 秒）", not bad, str(bad[:5]))
    cmds.currentTime(31.2, edit=True)
    mid = cmds.getAttr("spine_C0_0_jnt.translateX")
    check("合成中は元のカーブがジョイントから切り離されている", all(not str(cmds.listConnections(p, source=True, destination=False, plugs=True)[0]).startswith("spine_C0_0_jnt_translateX") for p in plugs[:1]))
    check("回転は pairBlend の四元数補間", any(cmds.getAttr(f"{n}.rotInterpolation") == 1 for n in cmds.ls(type="pairBlend")))
    # ---- 外す
    n = K.remove("blend")
    check("合成を外せる", n == 1 and K.active() == [] and not cmds.ls("tdAnimComp_*"), str(cmds.ls("tdAnimComp_*")))
    check("元のつながりに戻る", {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in plugs} == orig_src)
    check("元の再生範囲・ループ・時刻に戻る",
          cmds.playbackOptions(query=True, minTime=True) == 5 and cmds.playbackOptions(query=True, maxTime=True) == 50
          and cmds.playbackOptions(query=True, animationEndTime=True) == 100 and cmds.playbackOptions(query=True, loop=True) == "once"
          and cmds.currentTime(query=True) == 12)
    check("元の値に戻る", abs(cmds.getAttr("spine_C0_0_jnt.translateX", time=15) - orig15) < 1e-6)
    check("作ったノードが残らない", not cmds.ls(type="pairBlend") and not cmds.ls("tdAnim*"))
    check("外すのを繰り返しても落ちない", K.remove() == 0)

    # ---- 作り直しは 1 つだけ
    K.compose("blend", pls, joints, fps)
    n_pb = len(cmds.ls(type="pairBlend"))
    K.compose("blend", pls, joints, fps)
    check("作り直しても合成は 1 つ・pairBlend の数は変わらない（TS-5）", K.active() == ["blend"] and n_pb > 0 and len(cmds.ls(type="pairBlend")) == n_pb, f"{n_pb} -> {len(cmds.ls(type='pairBlend'))}")
    kept = K._load("tdAnimComp_blend", "tdOriginal", [])
    got = {dst: src for src, dst in kept}
    check("作り直しても元のつながりは 1 組（控えが合成の出力を元と取り違えない）", len(kept) == len(got) and all(got.get(p) == orig_src[p] for p in plugs), str(kept[:3]))
    K.remove()
    check("作り直しのあと外しても元に戻る", {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in plugs} == orig_src)

    # ---- 速度・切り替えなし(1 行)・即切り替え(0 秒)
    cut = Seq.from_rows([{"clip": "A_MisotenChara_Idle", "holdSec": 1.0}, {"clip": "A_MisotenChara_Jump", "crossFadeSec": 0}], clips, fps)
    e_before = cmds.getAttr("spine_C0_0_jnt.translateX", time=29.5)
    e_after = cmds.getAttr("spine_C0_0_jnt.translateX", time=200.5)
    K.compose("cut", cut, joints, fps)
    cmds.currentTime(29.5, edit=True)
    before = cmds.getAttr("spine_C0_0_jnt.translateX")
    cmds.currentTime(30.5, edit=True)
    after = cmds.getAttr("spine_C0_0_jnt.translateX")
    check("クロスフェード 0 秒は即切り替え", abs(before - e_before) < TOL and abs(after - e_after) < TOL, f"{before} {after} / {e_before} {e_after}")
    K.remove()
    single = Seq.from_rows([{"clip": "A_MisotenChara_Run_F", "speed": 2.0}], clips, fps)
    e_fast = cmds.getAttr("spine_C0_0_jnt.translateX", time=109)
    K.compose("single", single, joints, fps)
    cmds.currentTime(4.5, edit=True)
    check("速度 2 倍（1 行だけでも作れる）", abs(cmds.getAttr("spine_C0_0_jnt.translateX") - e_fast) < TOL, f"{cmds.getAttr('spine_C0_0_jnt.translateX')} / {e_fast}")
    K.remove()
    try:
        K.compose("x", [], joints, fps)
        check("空の列はエラー", False)
    except ValueError:
        check("空の列はエラー", not K.active())
    try:
        K.compose("x", pls, ["nope_jnt"], fps)
    except Exception:
        pass
    check("存在しないジョイントでも元のつながりは壊れない", {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in plugs} == orig_src)

    # ---- 保存して開き直しても外せる（状態は network ノードの文字列）
    K.compose("blend", pls, joints, fps)
    scene = tmp / "composed.ma"
    cmds.file(rename=str(scene))
    cmds.file(save=True, type="mayaAscii")
    cmds.file(new=True, force=True)
    cmds.file(str(scene), open=True, force=True)
    check("開き直しても合成が残っている（外せる）", K.active() == ["blend"])
    K.remove()
    check("開き直しのあとでも元に戻る", {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in plugs} == orig_src and not cmds.ls("tdAnim*"))

    # ---- AN-5: 状態図（Idle → Speed 0.5 で Run → ActionState=2 で Jump）
    g = M.default_graph(s.data["clips"], "MisotenChara")
    check("既定の状態図は Idle / Run / Jump に割り当たる", [st["clip"] for st in g["states"]] == ["A_MisotenChara_Idle", "A_MisotenChara_Run_F", "A_MisotenChara_Jump"])
    tl = [(1.0, {"Speed": 0.5}), (2.0, {"ActionState": 2})]
    r = M.evaluate(g, tl, 3.0, fps)
    sm = M.to_placements(r, clips, fps)
    check("状態図の遷移列", [p.clip for p in sm] == ["A_MisotenChara_Idle", "A_MisotenChara_Run_F", "A_MisotenChara_Jump"] and abs(sm[1].fade_in - 2.4) < 1e-6)
    sm_expected = {t: {p: sum(w * orig_value(c, p, Seq.source_frame(next(x for x in sm if x.clip == c), t)) for c, w in Seq.pose_weights(sm, t)) for p in plugs} for t in (10, 31.2, 45, 61.2, 80)}
    K.compose("sm", sm, joints, fps)
    bad = []
    for t, exp in sm_expected.items():
        cmds.currentTime(t, edit=True)
        for p in plugs:
            if abs(cmds.getAttr(p) - exp[p]) > TOL:
                bad.append((t, p))
    check("状態図の合成が元のカーブのブレンドと一致", not bad, str(bad))
    K.remove()

    # ---- UpperBody レイヤー: 上半身（spine_C0_2 以下）だけ別クリップ。下半身は動かない
    upper_joints = ["spine_C0_2_jnt", "spine_C0_3_jnt", "arm_L0_0_jnt", "arm_R0_0_jnt"]
    base = Seq.from_rows([{"clip": "A_MisotenChara_Idle"}], clips, fps)
    top = Seq.from_rows([{"clip": "A_MisotenChara_Run_F"}], clips, fps)
    layer = K.Layer(top, upper_joints, 1.0)
    e_up = cmds.getAttr("arm_L0_0_jnt.rotateZ", time=109)
    e_idle_arm = cmds.getAttr("arm_L0_0_jnt.rotateZ", time=9)
    e_idle_tx = cmds.getAttr("spine_C0_0_jnt.translateX", time=9)
    e_idle_sp1 = cmds.getAttr("spine_C0_1_jnt.rotateX", time=9)
    r2 = K.compose("layers", base, joints, fps, [layer])
    check("レイヤー付きの合成ができる", r2.layers == 1)
    cmds.currentTime(9, edit=True)
    check("上半身（arm_L0_0）にだけ Run が乗る", abs(cmds.getAttr("arm_L0_0_jnt.rotateZ") - e_up) < TOL, str(cmds.getAttr("arm_L0_0_jnt.rotateZ")))
    check("下半身（spine_C0_0 / spine_C0_1）は Idle のまま", abs(cmds.getAttr("spine_C0_0_jnt.translateX") - e_idle_tx) < TOL and abs(cmds.getAttr("spine_C0_1_jnt.rotateX") - e_idle_sp1) < TOL,
          f"{cmds.getAttr('spine_C0_0_jnt.translateX')} {cmds.getAttr('spine_C0_1_jnt.rotateX')}")
    K.set_layer_weight("layers", 0, 0.5)
    check("レイヤーの重みスライダー（0.5）", abs(cmds.getAttr("arm_L0_0_jnt.rotateZ") - (e_up * 0.5 + e_idle_arm * 0.5)) < TOL, str(cmds.getAttr("arm_L0_0_jnt.rotateZ")))
    K.set_layer_weight("layers", 0, 0.0)
    check("重み 0 は Idle だけ", abs(cmds.getAttr("arm_L0_0_jnt.rotateZ") - e_idle_arm) < TOL)
    K.remove()

    _ui(tmp, cmds, S, A, K, clips)


def _ui(tmp, cmds, S, A, K, clips) -> None:
    from tdrive_anim import ui_blend

    s = S.current()
    ui = ui_blend.BlendTab(s)
    s.listeners.append(ui.refresh)
    ui.refresh()
    APP.processEvents()
    check("UI: 既定の遷移列（Idle → Run → Jump）が入る", ui.rows_table.rowCount() == 3, str(ui.rows_table.rowCount()))
    ui.build_from_rows()
    check("UI: 合成を作る", K.active() == ["blend"] and "合成中" in ui.banner.text(), ui.banner.text())
    ui.remove()
    check("UI: 合成を外す", K.active() == [] and "合成中" not in ui.banner.text())
    ui.add_row()
    check("UI: 行を足せる", ui.rows_table.rowCount() == 4)
    ui.rows_table.selectRow(3)
    ui.remove_row()
    check("UI: 行を消せる", ui.rows_table.rowCount() == 3)
    ui.rows_table.selectRow(1)
    ui.move_row(-1)
    check("UI: 行を上へ動かせる", ui.rows[0]["clip"] == "A_MisotenChara_Run_F")
    ui.move_row(1)
    ui.rows_table.selectRow(0)
    QtWidgets.QApplication.clipboard().setText(ui.rows_to_text())
    ui.paste_rows(QtWidgets.QApplication.clipboard().text())
    check("UI: 行のコピー / 貼り付け", ui.rows_table.rowCount() >= 3)
    ui.paste_rows("これは違う文字列")
    check("UI: 別の文字列は警告だけ", "貼り付けられません" in ui.message.text(), ui.message.text())
    # パラメータ駆動
    ui.use_graph()
    check("UI: 状態図モードに切り替わる", ui.mode == "graph" and ui.graph is not None)
    ui.speed.setValue(50)  # 0.5
    ui.on_param_changed()
    check("UI: Speed を動かすと現在時刻から遷移が記録される", any("Speed" in v for _, v in ui.timeline), str(ui.timeline))
    ui.build_from_graph()
    check("UI: 状態図から合成を作る", K.active() == ["blend"])
    first = K.info("blend")["clips"]
    cmds.currentTime(60, edit=True)  # 2 秒の時点で Speed を 0 に戻す → その時刻から Run → Idle
    ui.speed.setValue(0)
    check("UI: スライダーを動かしても、すぐには作り直さない（150 ms まとめる。SF-5）", K.info("blend")["clips"] == first)
    ui.flush_rebuild()
    now = K.info("blend")["clips"]
    check("UI: 合成の最中にスライダーを動かすと、その時刻から遷移が足される", first == ["A_MisotenChara_Idle", "A_MisotenChara_Run_F"] and now == first + ["A_MisotenChara_Idle"], f"{first} -> {now}")
    check("UI: 作り直しても時刻が保たれる", cmds.currentTime(query=True) == 60)
    check("UI: 作り直しても合成は 1 つ", K.active() == ["blend"] and len(cmds.ls("tdAnimComp_*", type="network")) == 1)
    ui.remove()
    ui.layer_clip.setCurrentText("A_MisotenChara_Run_F")
    ui.build_from_rows()
    ui.detach()
    check("UI: detach で合成が外れる", K.active() == [])
    s.listeners.remove(ui.refresh)


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
