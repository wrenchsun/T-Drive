"""ショットの出力のスモーク（AN-8。mayapy・ヘッドレス。dx11 / Render Override は使わない）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_shot_export_smoke.py

別プロセスの mayapy で実際に FBX を書き、読み戻して確かめる:
  - <Shot>.fbx: カメラが Ubercam 1 台だけ（カメラ 2 台の区間を 1 本に焼いたもの）・EVT_ ロケータ（ddEvent のステップ）・小道具 PRP_・キャラクターは入っていない
  - <Shot>__<ModelId>.fbx: ジョイントだけ（メッシュ・スキンなし）・ネームスペースなし・ショットの範囲でベイク・クロスフェードを含む値が合成と一致
  - 同名上書き・<Shot>.shot.json の写し・出力先プリセット・ショットの問題があると出力が止まる・開いているシーンは壊れない
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
sys.path.insert(0, str(REPO / "tests" / "maya"))

from PySide6 import QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
TOL = 0.3
DEFAULT_CAMS = {"persp", "top", "front", "side"}


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_shotexp_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def read_fbx(cmds, mel, path: Path) -> dict:
    cmds.file(new=True, force=True)
    cmds.currentUnit(time="ntsc")
    cmds.loadPlugin("fbxmaya", quiet=True)
    cmds.file(path.as_posix(), i=True, type="FBX", ignoreVersion=True, options="fbx", mergeNamespacesOnClash=False)
    cams = [(cmds.listRelatives(c, parent=True) or [c])[0] for c in cmds.ls(type="camera")]
    curves = cmds.ls(type="animCurve")
    times = sorted({round(t, 3) for c in curves for t in (cmds.keyframe(c, query=True, timeChange=True) or [])})
    return {"cameras": [c for c in cams if c not in DEFAULT_CAMS], "meshes": cmds.ls(type="mesh", noIntermediate=True), "joints": cmds.ls(type="joint"),
            "skins": cmds.ls(type="skinCluster"), "locators": [(cmds.listRelatives(l, parent=True) or [l])[0] for l in cmds.ls(type="locator")], "curves": curves, "times": times}


def _run(tmp: Path) -> None:
    from maya import cmds, mel

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import session as S
    from tdrive_anim import shot_scene as SS
    from tdrive_anim import ui_export
    from tdrive_anim.core import animset as A
    from tdrive_anim.core import export_plan as E
    from tdrive_anim.core import sequence as Seq
    from tdrive_anim.core import shot as SH
    from anim_shot_smoke import key_char

    project.set_root(tmp)
    rig = make_mini_rig.build(namespace="MisotenChara")
    cmds.currentUnit(time="ntsc")
    key_char(cmds, "MisotenChara:")
    # カメラ 2 台（位置と画角が違う）+ 小道具
    cam1 = cmds.rename(cmds.camera(name="CAM_Main")[0], "CAM_Main")
    cam2 = cmds.rename(cmds.camera(name="CAM_Close")[0], "CAM_Close")
    cmds.xform(cam1, translation=(0, 150, 500))
    cmds.xform(cam2, translation=(30, 160, 100), rotation=(0, 20, 0))
    cmds.setAttr(cmds.listRelatives(cam1, shapes=True)[0] + ".focalLength", 35)
    cmds.setAttr(cmds.listRelatives(cam2, shapes=True)[0] + ".focalLength", 85)
    cmds.polyCube(name="PRP_Mic")
    s = S.current()
    s.new("MisotenChara", meshes=[rig["mesh"]], namespace="MisotenChara")
    for name, a, b in (("Idle", 0, 30), ("Run", 100, 118), ("Wave", 200, 230)):
        s.add_clip_from_range(name, a, b, direction="F" if name == "Run" else "")
    clips = {c["name"]: c for c in s.data["clips"]}
    s.save()
    s.new_shot("Opening01", 30, 10, 130)
    s.shot_add_camera("CAM_Main", 10, 70)
    s.shot_add_camera("CAM_Close", 71, 130)
    rows = [{"clip": "A_MisotenChara_Idle", "holdSec": 2.0}, {"clip": "A_MisotenChara_Run_F", "holdSec": 1.0, "crossFadeSec": 0.08}]
    s.shot_add_character("MisotenChara", "MisotenChara", rows)
    s.shot_add_event(60, "Hit")
    s.save_shot()
    shot = s.shot
    plugs = ["MisotenChara:spine_C0_0_jnt.translateX", "MisotenChara:arm_L0_0_jnt.rotateZ"]
    pls = Seq.from_rows(rows, clips, 30)
    for p in pls:
        p.start += 10
        p.end += 10
    frames = (10, 35, 69, 71, 72, 100)  # ベイクは整数フレーム（ループの折り返しをまたぐ小数フレームは補間になるので避ける）
    expected = {f: {p: sum(w * cmds.getAttr(p, time=Seq.source_frame(next(x for x in pls if x.clip == c), f)) for c, w in Seq.pose_weights(pls, f)) for p in plugs} for f in frames}
    orig_src = {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in plugs}
    scene_before = cmds.ls(dag=True, long=True)
    rig_scene = tmp / "rig.mb"
    cmds.file(rename=str(rig_scene))
    cmds.file(save=True, type="mayaBinary")

    # ---- UI から出力
    ui = ui_export.ExportTab(s)
    s.listeners.append(ui.refresh)
    ui.refresh()
    check("ショットの出力先 / ファイル名が出る", "build" in ui.shot_label.text().replace("\\", "/") and "Opening01.fbx" in ui.shot_label.text() and "Opening01__MisotenChara.fbx" in ui.shot_label.text(), ui.shot_label.text())
    ok_names = ui.check_names()
    check("ショットが出せる状態", ok_names and ui.shot_problem() == "" and ui.shot_btn.isEnabled(), ui.blocked.replace(chr(10), " / ") + ui.shot_problem())
    started = ui.start_shot()
    check("出力が始まる（別プロセス）", started and ui.job is not None)
    ui.wait(420)
    log = ui.log.toPlainText()
    out = tmp / "build" / "cutscene" / "Cutscene"
    cam_fbx, ch_fbx = out / "Opening01.fbx", out / "Opening01__MisotenChara.fbx"
    check("出力が終わり 2 本の FBX と shot.json ができる", ui.job is None and cam_fbx.is_file() and ch_fbx.is_file() and (out / "Opening01.shot.json").is_file(), log)
    check("ログに 2 件の✔", log.count("✔") >= 2, log)
    check("shot.json の写しが同じ内容", SH.load(out / "Opening01.shot.json") == shot)
    check("開いているシーンは壊れない（合成も残らない）", cmds.ls(dag=True, long=True) == scene_before and not cmds.ls("tdAnimComp_*") and not cmds.ls("tdShot_*")
          and {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in plugs} == orig_src)

    # ---- カメラ FBX
    info = read_fbx(cmds, mel, cam_fbx)
    check("カメラ FBX: カメラが 1 台だけ（Ubercam）", len(info["cameras"]) == 1, str(info["cameras"]))
    check("カメラ FBX: EVT_Hit のロケータと小道具 PRP_Mic がある", "EVT_Hit" in info["locators"] and cmds.objExists("PRP_Mic"), str(info["locators"]))
    check("カメラ FBX: キャラクター（ジョイント・スキン）は入っていない（メッシュは小道具の PRP_Mic だけ）", not info["joints"] and not info["skins"] and len(info["meshes"]) == 1, str({k: len(v) for k, v in info.items()}))
    check("カメラ FBX: ショットの範囲（10〜130）でベイクされている", info["times"] and info["times"][0] == 10 and info["times"][-1] == 130, str(info["times"][:2] + info["times"][-2:]))
    uber = info["cameras"][0] if info["cameras"] else ""
    if uber:
        tz = lambda f: cmds.getAttr(f"{uber}.translateZ", time=f)
        fl = lambda f: cmds.getAttr(f"{cmds.listRelatives(uber, shapes=True)[0]}.focalLength", time=f)
        check("Ubercam: 区間 1（10〜70）は CAM_Main の位置と画角", abs(tz(30) - 500) < 0.5 and abs(fl(30) - 35) < 0.1, f"{tz(30)} {fl(30)}")
        check("Ubercam: 区間 2（71〜130）は CAM_Close の位置と画角", abs(tz(100) - 100) < 0.5 and abs(fl(100) - 85) < 0.1, f"{tz(100)} {fl(100)}")
        check("Ubercam: 切り替わりは補間しない（70 と 71 の間は前の値のまま）", abs(tz(70.5) - 500) < 0.5, f"{tz(70.5)}")
    check("イベント: ddEvent が 60 フレームで 0 → 1", cmds.objExists("EVT_Hit") and [cmds.getAttr("EVT_Hit.ddEvent", time=t) for t in (59, 60, 61)] == [0, 1, 1]
          if cmds.attributeQuery("ddEvent", node="EVT_Hit", exists=True) else False, "ddEvent が FBX に出ていない" if not (cmds.objExists("EVT_Hit") and cmds.attributeQuery("ddEvent", node="EVT_Hit", exists=True)) else "")

    # ---- キャラクター FBX
    info = read_fbx(cmds, mel, ch_fbx)
    check("キャラ FBX: ジョイント 6 本・メッシュ/スキン/カメラなし", len(info["joints"]) == 6 and not info["meshes"] and not info["skins"] and not info["cameras"], str({k: len(v) for k, v in info.items()}))
    check("キャラ FBX: ネームスペースなしの名前", all(":" not in j for j in info["joints"]) and "spine_C0_0_jnt" in info["joints"], str(info["joints"]))
    check("キャラ FBX: ショットの範囲（10〜130）でベイク", info["times"] and info["times"][0] == 10 and info["times"][-1] == 130, str(info["times"][:2] + info["times"][-2:]))
    bad = []
    for f in frames:
        tx, rz = cmds.getAttr("spine_C0_0_jnt.translateX", time=f), cmds.getAttr("arm_L0_0_jnt.rotateZ", time=f)
        if abs(tx - expected[f][plugs[0]]) > TOL or abs(rz - expected[f][plugs[1]]) > TOL:
            bad.append((f, round(tx, 2), round(expected[f][plugs[0]], 2)))
    check("キャラ FBX: クリップ列（クロスフェード込み）が合成の値と一致", not bad, str(bad))

    # ---- 同名上書き・もう一度出力
    cmds.file(str(rig_scene), open=True, force=True)
    before = (ch_fbx.stat().st_mtime, cam_fbx.stat().st_mtime)
    import time as _t

    _t.sleep(1.1)
    ui.log.clear()
    ui.start_shot()
    ui.wait(420)
    check("同名のファイルは上書きされる", ch_fbx.stat().st_mtime > before[0] and cam_fbx.stat().st_mtime > before[1] and sorted(p.name for p in out.glob("*.fbx")) == ["Opening01.fbx", "Opening01__MisotenChara.fbx"], ui.log.toPlainText())

    # ---- 出力先プリセット
    ui.choose_root("animMs2026Root", str(tmp / "ms"))
    ui.preset.setCurrentIndex(1)
    ui.refresh()
    check("MS2026 のプリセット: …/SourceAssets/Cutscene/<Category>/", str((tmp / "ms" / "Assets" / "_Project" / "DDrive" / "SourceAssets" / "Cutscene" / "Cutscene").as_posix()) in ui.shot_label.text().replace("\\", "/"), ui.shot_label.text())
    ui.preset.setCurrentIndex(0)
    ui.refresh()

    # ---- 止まる条件
    s.shot_set("frameRate", 60)
    why = ui.shot_problem()
    check("フレームレートが違うと止まる", "60" in why and not ui.start_shot(), why)
    s.undo()
    s.shot_remove_camera(1)
    s.shot_remove_camera(0)
    check("カメラの区間が無いと止まる", "カメラ" in ui.shot_problem())
    s.undo()
    s.undo()
    s.shot_add_character("Ghost", "Ghost", [{"clip": "A_Ghost_Idle"}])
    check("animset の無いキャラクターがいると止まる", ui.shot_problem() != "", ui.shot_problem())
    s.undo()
    s.shot_set("shot", "bad name")
    check("ショット名が不正だと止まる", "ショット名" in ui.shot_problem(), ui.shot_problem())
    s.undo()
    ui.log.clear()
    cmds.currentUnit(time="film")
    check("24 fps（命名の❌）だと止まる", not ui.start_shot() and ui.job is None and "⛔" in ui.log.toPlainText(), ui.log.toPlainText())
    cmds.currentUnit(time="ntsc")

    # ---- .fctrack: FacialController のデータが無いと警告だけ（FBX は出る）
    s.shot_set("characters", [dict(c, facialTrack=True) for c in s.shot["characters"]])
    ui.log.clear()
    ui.start_shot()
    ui.wait(420)
    lg = ui.log.toPlainText()
    check("顔の .fctrack: データが無いときは警告だけで FBX は出る", ".fctrack" in lg and "⚠" in lg and "✔" in lg, lg)
    ui.detach()


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
