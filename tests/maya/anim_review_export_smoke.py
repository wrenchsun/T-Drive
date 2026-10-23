"""Phase AN コードレビュー（docs/21）の出力まわりの回帰スモーク（mayapy・ヘッドレス）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_review_export_smoke.py

SF-1 合成中は出力しない / SF-12 指紋 / SF-13 一時名で書いて置き換える / SF-15・UX-7 作業フォルダの後始末と中止 / SF-21 .fctrack は開始時の複製 /
DD-1 ログの申し送り / DD-2 EVT_ は既定で入れない / DD-4 参照のリグ / DD-6・UX-8 ショットを出せない理由 / DD-10 再生範囲 / DD-11 FBX の版 /
DD-12 入れ子のネームスペース / DD-13 Ubercam の名前 / CR-6 出力先の安全 / SP-9 Hips。
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
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
ANSWER = {"value": QtWidgets.QMessageBox.Yes}
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: ANSWER["value"])

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_review_export_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def read_fbx(cmds, mel, path: Path) -> dict:
    cmds.file(new=True, force=True)
    cmds.currentUnit(time="ntsc")
    cmds.loadPlugin("fbxmaya", quiet=True)
    cmds.file(path.as_posix(), i=True, type="FBX", ignoreVersion=True, options="fbx", mergeNamespacesOnClash=False)
    curves = cmds.ls(type="animCurve")
    return {"joints": cmds.ls(type="joint"), "curves": curves, "locators": cmds.ls(type="locator"), "transforms": cmds.ls(transforms=True)}


def fbx_version(path: Path) -> int:
    raw = path.read_bytes()[:32]
    assert raw.startswith(b"Kaydara FBX Binary")
    return struct.unpack("<I", raw[23:27])[0]


def _run(tmp: Path) -> None:
    import json
    import types

    from maya import cmds, mel

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import composition as K
    from tdrive_anim import export as X
    from tdrive_anim import session as S
    from tdrive_anim import shot_scene as SS
    from tdrive_anim import ui_export
    from tdrive_anim.core import animset as A
    from tdrive_anim.core import export_plan as E
    from tdrive_anim.core import sequence as Seq
    from tdrive_anim.core import shot as SH

    project.set_root(tmp)
    rig = make_mini_rig.build()
    cmds.currentUnit(time="ntsc")
    for f, v in ((0, 0), (30, 30), (100, 100), (118, 200)):
        cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=f, value=v)
        cmds.setKeyframe("arm_L0_0_jnt", attribute="rotateZ", time=f, value=v / 3)
    s = S.current()
    s.new("MisotenChara", meshes=[rig["mesh"]])
    s.add_clip_from_range("Idle", 0, 30)
    s.add_clip_from_range("Run", 100, 118, direction="F")
    s.save()
    d = s.data
    idle = A.find_clip(d, "A_MisotenChara_Idle")
    ui = ui_export.ExportTab(s)
    s.listeners.append(ui.refresh)
    ui.refresh()

    # ---- SF-12: 指紋は評価した値。接線・範囲外のキー・コンストレイントの変更も拾う
    fp0 = X.fingerprint(idle, d)
    t_before = cmds.currentTime(query=True)
    check("SF-12: 同じ状態なら指紋は同じ", X.fingerprint(idle, d) == fp0)
    check("SF-12: 指紋の計算で現在の時刻が変わらない", cmds.currentTime(query=True) == t_before)
    cmds.keyTangent("spine_C0_0_jnt", attribute="translateX", time=(0, 0), outTangentType="step")
    fp1 = X.fingerprint(idle, d)
    check("SF-12: 接線だけ直したら指紋が変わる", fp1 != fp0)
    cmds.undo()
    cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=50, value=-500)  # 範囲（0〜30）の外のキー。範囲内の補間が変わる
    check("SF-12: 範囲外のキーで範囲内の補間が変わったら指紋が変わる", X.fingerprint(idle, d) not in (fp0, fp1))
    cmds.cutKey("spine_C0_0_jnt", attribute="translateX", time=(50, 50))
    cmds.setAttr("arm_R0_0_jnt.rotateX", 10)
    cmds.aimConstraint("arm_L0_0_jnt", "arm_R0_0_jnt", aimVector=(0, 1, 0), upVector=(0, 0, 1), worldUpType="scene")
    check("SF-12: コンストレイントを足したら指紋が変わる", X.fingerprint(idle, d) != fp0)
    cmds.delete(cmds.ls(type="aimConstraint"))
    cmds.setAttr("arm_R0_0_jnt.rotateX", 0)
    cmds.autoKeyframe(state=False)

    # ---- SF-1: 合成中は出力しない
    pls = Seq.from_rows([{"clip": "A_MisotenChara_Idle", "holdSec": 1.0}, {"clip": "A_MisotenChara_Run_F", "holdSec": 1.0}], {c["name"]: c for c in d["clips"]}, 30)
    K.compose("blend", pls, make_mini_rig.JOINTS, 30)
    ui.refresh()
    check("SF-1: 合成中は出力ボタンが灰色で理由が出る", not ui.model_btn.isEnabled() and not ui.anim_btn.isEnabled() and "合成" in ui.block_label.text(), ui.block_label.text())
    check("SF-1: 合成中は start が始まらない", ui.start(model=True, clips=["A_MisotenChara_Idle"]) is False and ui.job is None and "合成" in ui.log.toPlainText())
    try:
        X.SplitJob(d, tmp / "m", tmp / "a", ["A_MisotenChara_Idle"], True, s.path)
        check("SF-1: SplitJob を直接作っても合成中は拒否", False)
    except RuntimeError as exc:
        check("SF-1: SplitJob を直接作っても合成中は拒否", "合成" in str(exc))
    X._prepare_scene()
    check("SF-1: 裏のプロセスでも、シーンを開いた直後に合成を外す", K.active() == [] and not cmds.ls("tdAnimComp_*"))
    ui.refresh()
    check("SF-1: 合成が無くなれば出せる", ui.check_names() and ui.model_btn.isEnabled(), ui.blocked)

    # ---- SF-13: 一時名で書いて置き換える
    target = tmp / "out" / "x.fbx"
    target.parent.mkdir()
    target.write_bytes(b"OLD-GOOD-FILE")
    try:
        X._write_atomically(target, lambda tmp_path: None)  # 何も書かない
        check("SF-13: 書かれなかったら例外", False)
    except RuntimeError:
        check("SF-13: 書かれなかったら例外で、以前のファイルはそのまま", target.read_bytes() == b"OLD-GOOD-FILE")

    def boom(tmp_path):
        tmp_path.write_bytes(b"HALF")
        raise OSError("disk full")

    try:
        X._write_atomically(target, boom)
    except OSError:
        pass
    check("SF-13: 書いている途中の失敗でも以前のファイルはそのまま・一時ファイルも残らない", target.read_bytes() == b"OLD-GOOD-FILE" and [p.name for p in target.parent.iterdir()] == ["x.fbx"])
    X._write_atomically(target, lambda tmp_path: tmp_path.write_bytes(b"NEW"))
    check("SF-13: 書けたら置き換わる", target.read_bytes() == b"NEW" and [p.name for p in target.parent.iterdir()] == ["x.fbx"])

    # ---- TS-7: dx11Shader を読めない環境でも、アニメの出力は続く（裏のプロセスの loadPlugin だけが dx11Shader に触れる）
    import time as _time

    os.environ["TDRIVE_TEST_FAIL_PLUGINS"] = "dx11Shader"
    try:
        job = X.SplitJob(d, tmp / "dx_m", tmp / "dx_a", ["A_MisotenChara_Idle"], False, None)
        t0 = _time.time()
        while not job.poll() and _time.time() - t0 < 300:
            _time.sleep(0.2)
        res = job.result()
        job.dispose()
    finally:
        del os.environ["TDRIVE_TEST_FAIL_PLUGINS"]
    check("TS-7: dx11Shader の読み込みを失敗させても出力は成功し、記録（notes）が残る",
          res["results"][0]["ok"] and any("dx11Shader" in n for n in res.get("notes", [])) and (tmp / "dx_a" / "A_MisotenChara_Idle.fbx").is_file(), str(res)[:400])

    # ---- 実際の出力（モデル + クリップ）: SF-11/DD-1/DD-11/SF-15
    started = ui.start(model=True, clips=["A_MisotenChara_Idle"])
    work = ui.job.work if ui.job else None
    check("出力が始まる", started and work is not None)
    ui.wait(420)
    log = ui.log.toPlainText()
    model_dir = tmp / "build" / "unity" / "MisotenChara"
    sk, a_idle = model_dir / "SK_MisotenChara.fbx", model_dir / "anim" / "A_MisotenChara_Idle.fbx"
    check("出力ができる", sk.is_file() and a_idle.is_file(), log)
    check("DD-11: FBX は FBX 2020（7700）のバイナリ", fbx_version(a_idle) == 7700 and fbx_version(sk) == 7700, f"{fbx_version(a_idle)} {fbx_version(sk)}")
    check("DD-1: ログに ModelData の識別子の申し送りが出る", "SKMisotenChara" in log, log)
    check("SF-15: 成功したら作業フォルダ（シーンのコピー）を消す", work is not None and not work.exists(), str(work))
    check("SF-13: 一時ファイルが出力先に残らない", not list(tmp.rglob("*.writing.fbx")))

    # ---- UX-7 / SF-15: 中止
    ui._check_all(True)
    ui.only_changed.setChecked(False)
    ui.start(model=True, clips=["A_MisotenChara_Idle", "A_MisotenChara_Run_F"])
    job = ui.job
    check("UX-7: 出力中は「中止」ボタンが出る", job is not None and not ui.cancel_btn.isHidden())
    work = job.work
    ui.cancel()
    check("UX-7: 中止すると裏のプロセスが止まり、ジョブが空になる", ui.job is None and job.proc.poll() is not None and ui.cancel_btn.isHidden())
    check("SF-15: 中止したら作業フォルダも消える", not work.exists())
    check("UX-7: ログに中止が出る", "中止" in ui.log.toPlainText())
    check("SF-13: 中止しても以前の出力は壊れない", sk.stat().st_size > 0 and fbx_version(sk) == 7700)

    # ---- ショット: DD-6 / UX-8 / DD-2 / SF-21
    cam = cmds.camera(name="CAM_Main")[0]
    cmds.move(0, 100, 300, cam)
    s.shot_set  # noqa: B018
    s.new_shot("Opening01", 60, 0, 60)
    s.shot_add_camera(cam, 0, 60)
    s.shot_add_character("MisotenChara", "", [{"clip": "A_MisotenChara_Idle", "holdSec": 1.0}])
    s.shot_add_event(10, "Hit")
    ui.refresh()
    check("DD-6/UX-8: シーン 30 fps のままでは、ショットの出力ボタンは灰色で理由が見える（押す前に）",
          not ui.shot_btn.isEnabled() and "時間単位" in ui.shot_block_label.text(), ui.shot_block_label.text())
    cmds.currentUnit(time="ntscf")
    ui.refresh()
    check("DD-6: animset が 30 fps でショットが 60 fps なら理由に出る", "30 fps" in ui.shot_problem() and "60 fps" in ui.shot_problem(), ui.shot_problem())
    s.set_value("frameRate", 60)
    ui.refresh()
    check("UX-8: 条件が揃えば灰色でなくなる", ui.shot_problem() == "" and ui.shot_btn.isEnabled(), ui.shot_problem())
    s.set_value("frameRate", 30)
    cmds.currentUnit(time="ntsc")

    fake = types.SimpleNamespace(doc={"keys": [1, 2, 3]})
    import tdrive_facial.session as fsession

    real_current = fsession.current
    fsession.current = lambda: fake
    shot_for_job = SH.loads(SH.dumps(s.shot))
    shot_for_job["characters"][0]["facialTrack"] = True
    shot_for_job["frameRate"] = 30
    try:
        j = X.ShotJob(shot_for_job, tmp / "shotout", {"MisotenChara": d})
        fake.doc["keys"].append(4)  # 出力中に顔のキーを触る
        check("SF-21: .fctrack は開始時の複製で書く（あとの編集が混ざらない）", j._facial_doc == {"keys": [1, 2, 3]} and j._facial_doc is not fake.doc)
        j.kill()
    finally:
        fsession.current = real_current

    # ---- ここから先は一時シーンを壊す関数を直接呼ぶ（mayapy 専用）。シーンを保存して開き直しながら確かめる
    scene = tmp / "scene.ma"
    cmds.file(rename=str(scene))
    cmds.file(save=True, type="mayaAscii")

    # DD-13: シーンに Ubercam という名前のノードが既にあっても、Ubercam になる
    cmds.file(str(scene), open=True, force=True)
    cmds.group(empty=True, name="Ubercam")
    shot_dict = SH.loads(SH.dumps(s.shot))
    shot_dict["frameRate"] = 30
    uber = X.build_ubercam(shot_dict)
    check("DD-13: 同名のノードがあっても Ubercam の名前を保つ（D-Drive のトラック名）", uber == "Ubercam" and cmds.objExists("tdOld_Ubercam"), uber)

    # DD-2 / DD-10: EVT_ は既定で入れない。入れる設定なら入る。再生範囲がショットの範囲になる
    for include in (False, True):
        cmds.file(str(scene), open=True, force=True)
        sh = SH.loads(SH.dumps(s.shot))
        sh["frameRate"] = 30
        sh["export"]["includeEvents"] = include
        cmds.playbackOptions(minTime=-50, maxTime=500)
        out = tmp / f"shot_{include}.fbx"
        res = X.export_shot_camera_in_place(sh, out)
        info = read_fbx(cmds, mel, out)
        has_evt = any(n.startswith("EVT_") for n in info["transforms"])
        check(f"DD-2: includeEvents={include} のとき <Shot>.fbx の EVT_ は {'ある' if include else 'ない（既定）'}", has_evt == include and (res["events"] > 0) == include, str(info["transforms"]))
    cmds.file(str(scene), open=True, force=True)
    sh = SH.loads(SH.dumps(s.shot))
    sh["frameRate"] = 30
    cmds.playbackOptions(minTime=-50, maxTime=500)
    X._shot_fbx_options(0, 60, cameras=True)
    check("DD-10: ショットの出力の前に再生範囲をショットの範囲にする", (cmds.playbackOptions(query=True, minTime=True), cmds.playbackOptions(query=True, maxTime=True)) == (0, 60))

    # SP-9: rootMotion=none は humanoid.json の Hips を固定する（スケルトンのルートがその上にあるリグ）
    cmds.file(new=True, force=True)
    cmds.currentUnit(time="ntsc")
    cmds.select(clear=True)
    top = cmds.joint(name="root_jnt", position=(0, 0, 0))
    hips = cmds.joint(name="spine_C0_0_jnt", position=(0, 90, 0))
    cmds.joint(name="spine_C0_1_jnt", position=(0, 110, 0))
    for f, v in ((0, 0), (30, 30)):
        cmds.setKeyframe(top, attribute="translateX", time=f, value=v)
        cmds.setKeyframe(hips, attribute="translateX", time=f, value=v * 2)
    aset = A.new_animset("MisotenChara", "root_jnt")
    clip = A.default_clip("A_MisotenChara_Idle", 0, 30, "MisotenChara")
    out = tmp / "hips.fbx"
    X.export_clip_in_place(aset, clip, out, hips="spine_C0_0_jnt")
    read_fbx(cmds, mel, out)
    hips_x = [cmds.getAttr("spine_C0_0_jnt.translateX", time=t) for t in (0, 15, 30)]
    root_x = [cmds.getAttr("root_jnt.translateX", time=t) for t in (0, 15, 30)]
    check("SP-9: Hips の XZ が固定される（humanoid.json の Hips）", max(hips_x) - min(hips_x) < 1e-3, str(hips_x))
    check("SP-9: スケルトンのルート（Hips ではない）は固定されず動いたまま", max(root_x) - min(root_x) > 20, str(root_x))

    # TS-9: ネームスペース付きのリグのクリップ FBX にネームスペースが付かない
    cmds.file(new=True, force=True)
    cmds.currentUnit(time="ntsc")
    make_mini_rig.build(namespace="MisotenChara", new_scene=False)
    for f, v in ((0, 0), (30, 30)):
        cmds.setKeyframe("MisotenChara:spine_C0_0_jnt", attribute="translateX", time=f, value=v)
    aset_ns = A.new_animset("MisotenChara", "spine_C0_0_jnt")
    aset_ns["namespace"] = "MisotenChara"
    out_ns = tmp / "ns_clip.fbx"
    X.export_clip_in_place(aset_ns, A.default_clip("A_MisotenChara_Idle", 0, 30, "MisotenChara"), out_ns, hips="spine_C0_0_jnt")
    info = read_fbx(cmds, mel, out_ns)
    check("TS-9: ネームスペース付きのリグのクリップ FBX は、ジョイント名にネームスペースが付かない（SK_ と同じ名前）",
          len(info["joints"]) == 6 and all(":" not in j for j in info["joints"]), str(info["joints"]))

    # DD-12: 入れ子のネームスペースでも、外したあとの名前を間違えない
    cmds.file(new=True, force=True)
    cmds.currentUnit(time="ntsc")
    make_mini_rig.build(namespace="A:B", new_scene=False)
    cmds.setKeyframe("A:B:spine_C0_0_jnt", attribute="translateX", time=0, value=0)
    cmds.setKeyframe("A:B:spine_C0_0_jnt", attribute="translateX", time=30, value=30)
    aset = A.new_animset("MisotenChara", "spine_C0_0_jnt")
    aset["namespace"] = "A:B"
    shot_n = SH.new_shot("Nested01", 30, 0, 30)
    SH.add_character(shot_n, "MisotenChara", "A:B", [{"clip": "A_MisotenChara_Idle"}])
    aset["clips"] = [A.default_clip("A_MisotenChara_Idle", 0, 30, "MisotenChara")]
    out = tmp / "nested.fbx"
    try:
        X.export_shot_character_in_place(shot_n, 0, aset, {"MisotenChara": aset}, out)
        info = read_fbx(cmds, mel, out)
        check("DD-12: 入れ子のネームスペース（A:B）でも書ける・ジョイント名にネームスペースが付かない", out.is_file() and info["joints"] and all(":" not in j for j in info["joints"]), str(info["joints"][:3]))
    except Exception:
        check("DD-12: 入れ子のネームスペース（A:B）でも書ける", False, traceback.format_exc())

    # DD-4: 参照で読み込んだリグ
    cmds.file(new=True, force=True)
    make_mini_rig.build()
    rig_file = tmp / "rig_ref.ma"
    cmds.file(rename=str(rig_file))
    cmds.file(save=True, type="mayaAscii")
    cmds.file(new=True, force=True)
    cmds.currentUnit(time="ntsc")
    cmds.file(str(rig_file), reference=True, namespace="MisotenChara")
    for f, v in ((0, 0), (30, 30)):
        cmds.setKeyframe("MisotenChara:spine_C0_0_jnt", attribute="translateX", time=f, value=v)
    s2 = S.current()
    s2.new("MisotenChara", meshes=[], namespace="MisotenChara")
    s2.add_clip_from_range("Idle", 0, 30)
    ui2 = ui_export.ExportTab(s2)
    s2.listeners.append(ui2.refresh)
    ui2.refresh()
    ui2.preset.setCurrentIndex(0)
    project.set_root(tmp / "refproj")
    ui2.refresh()
    ok = ui2.start(model=False, clips=["A_MisotenChara_Idle"])
    ui2.wait(420)
    ref_out = tmp / "refproj" / "build" / "unity" / "MisotenChara" / "anim" / "A_MisotenChara_Idle.fbx"
    check("DD-4: 参照で読み込んだリグでもクリップを出力できる（参照を取り込んでからネームスペースを外す）", ok and ref_out.is_file(), ui2.log.toPlainText())
    if ref_out.is_file():
        info = read_fbx(cmds, mel, ref_out)
        check("DD-4: 出力したジョイント名にネームスペースが付かない", info["joints"] and all(":" not in j for j in info["joints"]), str(info["joints"][:3]))


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
