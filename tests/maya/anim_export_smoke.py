"""Unity 用の分割出力のスモーク（AN-6。mayapy・ヘッドレス。dx11 / Render Override は使わない）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_export_smoke.py

別プロセスの mayapy で実際に FBX を書き、読み戻して確かめる:
  - SK_<ModelId>.fbx: メッシュ + スキン + ジョイント、アニメーションなし。humanoid.json / animset.json の写しが同じフォルダにできる
  - A_<ModelId>_<動作>.fbx（2 本）: ジョイントだけ（メッシュなし）、ベイク済み、先頭が 0 フレーム、テイク 1 つ（名前 = クリップ名）、`@` を使わない
  - rootMotion=none は Hips の XZ が固定、bake はそのまま動く
  - 変更のあるクリップだけ（記録）・命名の❌があると出力が止まる・開いているシーンは壊れない
"""

from __future__ import annotations

import json
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
INFO: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_export_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def read_fbx(cmds, mel, path: Path) -> dict:
    """FBX を新しいシーンに読み込んで中身を数える。"""
    cmds.file(new=True, force=True)
    cmds.currentUnit(time="ntsc")  # FBX の時間は 30 fps（既定の film のままだとキーの時刻が 24 fps に換算される）
    cmds.loadPlugin("fbxmaya", quiet=True)
    mel.eval(f'FBXRead -f "{path.as_posix()}"')  # FBXImport -f は直前に FBXRead したファイルを読むことがあるため、cmds.file で取り込む
    takes = [mel.eval(f"FBXGetTakeName {i}") for i in range(1, int(mel.eval("FBXGetTakeCount")) + 1)]
    cmds.file(path.as_posix(), i=True, type="FBX", ignoreVersion=True, options="fbx", mergeNamespacesOnClash=False)
    curves = cmds.ls(type="animCurve")
    times = sorted({round(t, 3) for c in curves for t in (cmds.keyframe(c, query=True, timeChange=True) or [])})
    return {"meshes": cmds.ls(type="mesh", noIntermediate=True), "joints": cmds.ls(type="joint"), "skins": cmds.ls(type="skinCluster"), "curves": curves,
            "times": times, "takes": takes, "constraints": cmds.ls(type="constraint")}


def _run(tmp: Path) -> None:
    from maya import cmds, mel

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import export as X
    from tdrive_anim import session as S
    from tdrive_anim import ui_export
    from tdrive_anim.core import animset as A
    from tdrive_anim.core import export_plan as E

    project.set_root(tmp)
    rig = make_mini_rig.build()
    cmds.currentUnit(time="ntsc")
    # Idle 0〜30: 腰が X に動く（rootMotion = none なら固定される）。Run 100〜118: 腰が動く（rootMotion = bake で残す）
    for f, v in ((0, 0), (30, 30), (100, 100), (118, 200)):
        cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=f, value=v)
        cmds.setKeyframe("spine_C0_0_jnt", attribute="translateZ", time=f, value=v / 2)
        cmds.setKeyframe("arm_L0_0_jnt", attribute="rotateZ", time=f, value=v / 3)
    s = S.current()
    s.new("MisotenChara", meshes=[rig["mesh"]])
    s.add_clip_from_range("Idle", 0, 30)
    s.add_clip_from_range("Run", 100, 118, direction="F")
    s.update_clip("A_MisotenChara_Run_F", rootMotion="bake")
    s.save()
    d = s.data
    names = [c["name"] for c in d["clips"]]
    scene_before = cmds.ls(dag=True, long=True)
    rig_scene = tmp / "rig.mb"
    cmds.file(rename=str(rig_scene))
    cmds.file(save=True, type="mayaBinary")

    # ---- UI を作って出力（命名の❌が無い状態）
    ui = ui_export.ExportTab(s)
    s.listeners.append(ui.refresh)
    ui.refresh()
    check("既定の出力先は build/unity/<ModelId>", ui.dirs_for_preset() == (tmp / "build" / "unity" / "MisotenChara", tmp / "build" / "unity" / "MisotenChara" / "anim"))
    check("MS2026 / D-Drive は未設定なので選べない（理由つき）", not ui.preset.model().item(1).isEnabled() and "設定" in ui.preset.model().item(1).toolTip())
    check("命名の❌が無ければ出せる", ui.check_names() and ui.model_btn.isEnabled() and ui.anim_btn.isEnabled(), ui.blocked)

    started = ui.start(model=True, clips=names)
    check("出力が始まる（別プロセス）", started and ui.job is not None and ui.progress.isVisible() is not None)
    ui.wait(420)
    log = ui.log.toPlainText()
    check("出力が終わる", ui.job is None, log)
    INFO.append(log)
    model_dir, anim_dir = tmp / "build" / "unity" / "MisotenChara", tmp / "build" / "unity" / "MisotenChara" / "anim"
    sk, idle, run_ = model_dir / "SK_MisotenChara.fbx", anim_dir / "A_MisotenChara_Idle.fbx", anim_dir / "A_MisotenChara_Run_F.fbx"
    check("SK_ と A_ が 2 本できる", sk.is_file() and idle.is_file() and run_.is_file() and sk.stat().st_size > 0, log)
    check("ファイル名に @ が無い", not any("@" in p.name for p in tmp.rglob("*.fbx")))
    check("付帯 JSON（humanoid.json / animset.json）が同じフォルダにできる", (model_dir / "humanoid.json").is_file() and (model_dir / "animset.json").is_file())
    check("animset.json の写しが同じ内容", A.load(model_dir / "animset.json") == d)
    check("ログに出力ファイルとサイズが出る", "SK_MisotenChara.fbx" in log and "KB" in log, log)
    check("開いているシーンは壊れない", cmds.ls(dag=True, long=True) == scene_before and cmds.objExists(rig["mesh"]) and bool(cmds.ls(type="skinCluster")))

    # ---- FBX の中身を読み戻す
    info = read_fbx(cmds, mel, sk)
    check("モデル: メッシュとスキンとジョイントが入っている", len(info["meshes"]) >= 1 and info["skins"] and len(info["joints"]) == 6, str({k: len(v) for k, v in info.items()}))
    check("モデル: アニメーションが入っていない", not info["curves"], str(info["curves"][:5]))
    info = read_fbx(cmds, mel, idle)
    check("アニメ: メッシュが入っていない・ジョイントだけ", not info["meshes"] and not info["skins"] and len(info["joints"]) == 6, str({k: len(v) for k, v in info.items()}))
    check("アニメ: ベイク済みで先頭が 0 フレーム・長さ 30", info["curves"] and info["times"][0] == 0 and info["times"][-1] == 30, str(info["times"][:3] + info["times"][-3:]))
    check("アニメ: コンストレイントが入っていない", not info["constraints"])
    check("アニメ: 1 ファイル 1 テイク", len(info["takes"]) == 1, str(info["takes"]))

    def hips_tx_range(clip_info) -> tuple:
        vals = [cmds.getAttr("spine_C0_0_jnt.translateX", time=t) for t in (0, 10, 20, 30)]
        return min(vals), max(vals)

    read_fbx(cmds, mel, idle)
    lo, hi = hips_tx_range(None)
    check("rootMotion=none: Hips の X が固定される", abs(hi - lo) < 1e-3 and abs(lo - 0.0) < 1e-3, f"{lo} {hi}")
    zs = [cmds.getAttr("spine_C0_0_jnt.translateZ", time=t) for t in (0, 15, 30)]
    check("rootMotion=none: Hips の Z も固定される", max(zs) - min(zs) < 1e-3, str(zs))
    arm = [cmds.getAttr("arm_L0_0_jnt.rotateZ", time=t) for t in (0, 15, 30)]
    check("他のジョイントのアニメーションは残る", arm[2] - arm[0] > 5.0, str(arm))
    read_fbx(cmds, mel, run_)
    run_x = [cmds.getAttr("spine_C0_0_jnt.translateX", time=t) for t in (0, 9, 18)]
    check("rootMotion=bake: Hips の移動がそのまま出る", run_x[2] - run_x[0] > 50.0, str(run_x))

    # ---- 変更のあるクリップだけ（読み戻しで開いているシーンが FBX に入れ替わったので、リグのシーンを開き直す）
    cmds.file(str(rig_scene), open=True, force=True)
    ui.log.clear()
    started = ui.start(model=False, clips=names)
    check("変更が無ければ何も出力しない", not started and "変更なし" in ui.log.toPlainText(), ui.log.toPlainText())
    cmds.setKeyframe("spine_C0_0_jnt", attribute="translateX", time=110, value=999)  # Run の範囲を変える
    ui.log.clear()
    started = ui.start(model=False, clips=names)
    ui.wait(300)
    out = ui.log.toPlainText()
    check("変更のあるクリップ（Run）だけ出力される", started and "A_MisotenChara_Run_F.fbx" in out and "A_MisotenChara_Idle.fbx" not in out.split("変更なしのため")[-1].split("\n", 1)[-1], out)
    state = E.load_state(E.state_path(s.path))
    check("記録（.export-state.json）に両方のクリップが残る", set(state["clips"]) == set(names), str(state))
    ui.only_changed.setChecked(False)
    ui.log.clear()
    ui.start(model=False, clips=names)
    ui.wait(300)
    check("「変更のあるものだけ」を切ると全部出る", ui.log.toPlainText().count("✔") == 2, ui.log.toPlainText())
    ui.only_changed.setChecked(True)

    # ---- 命名の❌があると出力が止まる
    cmds.currentUnit(time="film")
    ui.log.clear()
    ok = ui.check_names()
    check("24 fps だと❌で止まる（ボタン無効・理由つき）", not ok and not ui.model_btn.isEnabled() and not ui.anim_btn.isEnabled() and "24" in ui.block_label.text() or "film" in ui.block_label.text(), ui.block_label.text())
    check("止まっているときは start しない", ui.start(model=True, clips=names) is False and ui.job is None)
    cmds.currentUnit(time="ntsc")
    s.rename_clip("A_MisotenChara_Idle", "Idle@Walk")
    check("クリップ名の❌（@）でも止まる", not ui.check_names() and "@" in ui.block_label.text(), ui.block_label.text())
    s.undo()
    check("直すと出せる", ui.check_names())

    # ---- 出力先のプリセット
    ui.choose_root("animMs2026Root", str(tmp / "ms"))
    check("パスを設定すると MS2026 のプリセットが選べる", ui.preset.model().item(1).isEnabled())
    ui.preset.setCurrentIndex(1)
    ui.refresh()
    check("MS2026 の出力先が SourceAssets/Model|Anim/<Category>", ui.dirs_for_preset() == (tmp / "ms" / "Assets" / "_Project" / "DDrive" / "SourceAssets" / "Model" / "Player",
                                                                                   tmp / "ms" / "Assets" / "_Project" / "DDrive" / "SourceAssets" / "Anim" / "Player"))
    ui.preset.setCurrentIndex(0)

    # ---- メッシュの自動設定・存在しないスケルトンのルート
    s.set_value("meshes", [])
    ui.set_meshes()
    check("スキンされたメッシュを animset に設定", s.data["meshes"] == [rig["mesh"]], str(s.data["meshes"]))
    s.set_value("skeletonRoot", "nope_jnt")
    ui.log.clear()
    ui.check_names()
    check("スケルトンのルートが無いときは❌で止まる", not ui.model_btn.isEnabled() and "nope_jnt" in ui.block_label.text(), ui.block_label.text())
    s.undo()
    ui.detach()

    # ---- Toon の ExportJob が従来どおり使える（共通化後の互換）
    from tdrive_toon import export as toon_export
    from tdrive import export_job

    check("tdrive_toon.export.ExportJob は共通の BatchJob を継いでいる", issubclass(toon_export.ExportJob, export_job.BatchJob) and toon_export._mayapy() == export_job.mayapy())
    res = toon_export.export_fbx([rig["mesh"]], tmp / "toon" / "toon.fbx", timeout=300)
    check("Toon の export_fbx が従来どおり動く", (tmp / "toon" / "toon.fbx").is_file() and res["meshes"] >= 1, str(res))


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
