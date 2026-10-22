"""アニメーション作業台の一周のスモーク（AN-9。mayapy・ヘッドレス = DirectX 11 なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_flow_smoke.py [mini|misoten]

命名 → クリップ → ブレンド → 出力 → ショット → 出力 を、画面（タブ）の操作と同じ関数で一周する。
リグは、`assets/misoten/MisotenChara_Rig.ma`（Git 管理外）があればそれ（MisotenChara 本体）、無ければ小さなリグ（tests/fixtures/anim）。
引数 mini / misoten で選べる。ここで確かめるのは「止まらずに一周でき、期待のファイルができる」こと。値の正しさは各機能のスモークで確認している。
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

from PySide6 import QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)  # 確認ダイアログは常に「はい」（ヘッドレスで止まらないように）

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
INFO: list[str] = []
MISOTEN = REPO / "assets" / "misoten" / "MisotenChara_Rig.ma"


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run(which: str) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_flow_"))
    try:
        _run(tmp, which)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp: Path, which: str) -> None:
    from maya import cmds

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import composition as K
    from tdrive_anim import scene_naming as SN
    from tdrive_anim import session as S
    from tdrive_anim import shot_scene as SS
    from tdrive_anim import ui_blend, ui_clips, ui_export, ui_naming, ui_shot
    from tdrive_anim.core import animset as A
    from tdrive_anim.core import humanoid_map as H

    project.set_root(tmp)
    t_all = time.perf_counter()
    if which == "misoten":
        cmds.file(new=True, force=True)
        cmds.file(str(MISOTEN), open=True, force=True, ignoreVersion=True, prompt=False)
    else:
        make_mini_rig.build()
        make_mini_rig.add_materials()
    root = "spine_C0_0_jnt"
    INFO.append(f"リグ: {which}（ジョイント {len(cmds.ls(type='joint'))} 本、時間単位 {cmds.currentUnit(query=True, time=True)}）")
    # 動きが無いと全クリップが同じなので、見分けのつくキーを少し打つ（腰の X と、動かせる最初のコントローラ）
    for f, v in ((0, 0), (30, 10), (100, 0), (118, 40), (200, 0), (230, -20)):
        cmds.setKeyframe(root, attribute="translateX", time=f, value=v)
    s = S.current()

    # ① animset
    s.new("MisotenChara")
    check("① animset を新規作成", s.data is not None and s.data["modelId"] == "MisotenChara")

    # ② 命名
    ui_n = ui_naming.NamingTab(s)
    t0 = time.perf_counter()
    ui_n.inspect()
    check("② 検査が動く（一覧が出る）", ui_n.table.rowCount() >= 1 and ui_n.report.has_errors, ui_n.summary.text())
    check("② 24 fps（film）が❌で出る", any(i.key == "time" for i in ui_n.report.settings), str(ui_n.report.settings))
    ui_n.fix_setting("time")
    ui_n.fix_setting("linear")
    ui_n.fix_setting("up")
    check("② 30 fps / cm / Y-up に直る", cmds.currentUnit(query=True, time=True) == "ntsc" and not [i for i in ui_n.report.settings if i.key in ("time", "linear", "up")])
    ui_n._check_all(True)
    ui_n.apply()
    meshes = SN.scene_meshes()
    check("② メッシュが SK_ の名前になる", meshes and all(m.split("|")[-1].startswith("SK_MisotenChara_") for m in meshes), str(meshes))
    check("② 命名の❌が無くなる", not ui_n.report.has_errors, str([getattr(e, "reason", getattr(e, "label", e)) for e in ui_n.report.errors]))
    ui_n.humanoid_infer()
    ui_n.humanoid_save()
    h = H.load(SN.humanoid_path("MisotenChara"))
    if which == "misoten":
        check("② Humanoid 必須 15 ボーンが埋まり、検証に問題なし", H.missing_required(h) == [] and SN.humanoid_problems(h, root) == [], str(H.missing_required(h)))
    else:
        check("② Humanoid の対応表ができる（ミニリグは必須に足りない）", (SN.humanoid_path("MisotenChara")).is_file() and h["bones"].get("Hips") == root)
    INFO.append(f"② 命名: {time.perf_counter() - t0:.1f} 秒")

    # ③ クリップ
    ui_c = ui_clips.ClipsTab(s)
    for action, a, b, d in (("Idle", 0, 30, ""), ("Run", 100, 118, "F"), ("Jump", 200, 230, "")):
        cmds.playbackOptions(minTime=a, maxTime=b)
        ui_c.action.setCurrentText(action)
        ui_c.direction.setCurrentText(d)
        ui_c.add_from_range()
    names = [c["name"] for c in s.data["clips"]]
    check("③ クリップが 3 つできる", names == ["A_MisotenChara_Idle", "A_MisotenChara_Run_F", "A_MisotenChara_Jump"], str(names))
    ui_c.table.selectRow(1)
    ui_c.play_selected()
    ui_c.stop()
    s.save()
    check("③ 再生して止めると元の範囲に戻り、animset が保存される", not __import__("tdrive_anim.clips", fromlist=["x"]).is_playing() and s.path.is_file())

    # ④ ブレンド
    ui_b = ui_blend.BlendTab(s)
    s.listeners.append(ui_b.refresh)
    ui_b.refresh()
    t0 = time.perf_counter()
    ui_b.build_from_rows()
    check("④ 遷移列（Idle → Run → Jump）から合成ができる", K.active() == ["blend"], ui_b.message.text())
    INFO.append(f"④ 合成: {time.perf_counter() - t0:.1f} 秒（{K.info('blend').get('joints')} ジョイント、{K.info('blend').get('plugs')} チャンネル）")
    ui_b.remove()
    check("④ 合成を外すと元に戻る", K.active() == [] and not cmds.ls("tdAnimComp_*"))
    ui_b.use_graph()
    ui_b.build_from_graph()
    check("④ MS2026 の状態図からも合成できる", K.active() == ["blend"], ui_b.message.text())
    ui_b.remove()

    # ⑤ 出力（モデル + クリップ）
    ui_e = ui_export.ExportTab(s)
    s.listeners.append(ui_e.refresh)
    ui_e.refresh()
    ui_e.set_meshes()
    from tdrive_anim import export as X

    expected_joints = len(X.skeleton_joints(root))  # ルート以下（mGear の補助ジョイントのうちルートの外にあるものは出ない）
    ok = ui_e.check_names()
    check("⑤ 出力できる状態（命名の❌なし）", ok, ui_e.blocked)
    t0 = time.perf_counter()
    ui_e.start(model=True, clips=names)
    ui_e.wait(900)
    model_dir = tmp / "build" / "unity" / "MisotenChara"
    files = sorted(p.name for p in model_dir.rglob("*") if p.is_file())
    check("⑤ SK_ と A_ の FBX・humanoid.json・animset.json ができる",
          {"SK_MisotenChara.fbx", "humanoid.json", "animset.json", "A_MisotenChara_Idle.fbx", "A_MisotenChara_Run_F.fbx", "A_MisotenChara_Jump.fbx"} <= set(files), f"{files}\n{ui_e.log.toPlainText()}")
    INFO.append(f"⑤ モデル + クリップ 3 本の出力: {time.perf_counter() - t0:.1f} 秒")

    # ⑥ ショット
    cam1 = cmds.rename(cmds.camera(name="CAM_Main")[0], "CAM_Main")
    cam2 = cmds.rename(cmds.camera(name="CAM_Close")[0], "CAM_Close")
    cmds.xform(cam1, translation=(0, 150, 500))
    cmds.xform(cam2, translation=(30, 160, 100))
    ui_s = ui_shot.ShotTab(s)
    s.listeners.append(ui_s.refresh)
    ui_s.blend_tab, ui_b.shot_tab = ui_b, ui_s
    ui_s.name.setText("Opening01")
    ui_s.start.setValue(0)
    ui_s.end.setValue(120)
    ui_s.create()
    check("⑥ ショットができる", s.shot and s.shot["shot"] == "Opening01", ui_s.message.text())
    ui_s.cam_combo.setCurrentText("CAM_Main")
    ui_s.cam_start.setValue(0)
    ui_s.cam_end.setValue(60)
    ui_s.add_camera()
    ui_s.cam_combo.setCurrentText("CAM_Close")
    ui_s.cam_start.setValue(61)
    ui_s.cam_end.setValue(120)
    ui_s.add_camera()
    ui_s.add_character()
    ui_b.rows = [{"clip": "A_MisotenChara_Idle", "holdSec": 1.0, "crossFadeSec": 0.1, "switchAt": 1.0, "speed": 1.0},
                 {"clip": "A_MisotenChara_Run_F", "holdSec": 1.0, "crossFadeSec": 0.08, "switchAt": 1.0, "speed": 1.0}]
    ui_s.char_table.selectRow(0)
    ui_b.send_to_shot()
    cmds.currentTime(45, edit=True)
    ui_s.ev_key.setText("Hit")
    ui_s.add_event()
    check("⑥ カメラ 2 台・キャラクター・クリップ列・イベントが入る", len(s.shot["cameras"]) == 2 and len(s.shot["characters"]) == 1
          and len(s.shot["characters"][0]["sequence"]) == 2 and len(s.shot["events"]) == 1, str(s.shot))
    t0 = time.perf_counter()
    ui_s.build()
    check("⑥ ショットを反映できる（Camera Sequencer・イベント・合成）", len(SS.sequencer_shots("Opening01")) == 2 and SS.event_frames("Opening01") == {"Hit": 45} and SS.compositions("Opening01"), ui_s.message.text())
    INFO.append(f"⑥ ショットの反映: {time.perf_counter() - t0:.1f} 秒")
    ui_s.remove()
    s.save_shot()

    # ⑦ 出力（ショット）
    t0 = time.perf_counter()
    ui_e.refresh()
    ui_e.log.clear()
    ui_e.start_shot()
    ui_e.wait(900)
    out = tmp / "build" / "cutscene" / "Cutscene"
    got = sorted(p.name for p in out.glob("*"))
    check("⑦ ショットの FBX 2 本と shot.json ができる", got == ["Opening01.fbx", "Opening01.shot.json", "Opening01__MisotenChara.fbx"], f"{got}\n{ui_e.log.toPlainText()}")
    INFO.append(f"⑦ ショットの出力: {time.perf_counter() - t0:.1f} 秒")

    # DirectX 11 に依存していない
    if which == "mini":  # MisotenChara 本体は Toon のプレビュー用ノード（dx11Shader）を含むので、開いた時点でプラグインが読まれる（Animation タブのせいではない）
        check("一周のあいだ dx11Shader を読み込んでいない", not (cmds.pluginInfo("dx11Shader", query=True, registered=True) and cmds.pluginInfo("dx11Shader", query=True, loaded=True)))
    for w in (ui_n, ui_c, ui_b, ui_e, ui_s):
        w.detach()

    # ---- 書き出した FBX を読み戻す（開いているシーンが置き換わるので最後に）
    from maya import mel

    def read_fbx(path: Path) -> dict:
        cmds.file(new=True, force=True)
        cmds.currentUnit(time="ntsc")
        cmds.loadPlugin("fbxmaya", quiet=True)
        mel.eval(f'FBXRead -f "{path.as_posix()}"')
        takes = int(mel.eval("FBXGetTakeCount"))
        cmds.file(path.as_posix(), i=True, type="FBX", ignoreVersion=True, options="fbx", mergeNamespacesOnClash=False)
        return {"meshes": len(cmds.ls(type="mesh", noIntermediate=True)), "skins": len(cmds.ls(type="skinCluster")), "joints": len(cmds.ls(type="joint")),
                "curves": len(cmds.ls(type="animCurve")), "takes": takes}

    sk = read_fbx(model_dir / "SK_MisotenChara.fbx")
    idle = read_fbx(model_dir / "anim" / "A_MisotenChara_Idle.fbx")
    ch = read_fbx(out / "Opening01__MisotenChara.fbx")
    INFO.append(f"SK: {sk} / A_Idle: {idle} / ショットのキャラ: {ch}")
    check("読み戻し: SK_ にメッシュ・スキン・ジョイントがあり、アニメは無い", sk["meshes"] >= 1 and sk["skins"] >= 1 and sk["joints"] == expected_joints and sk["curves"] == 0, str(sk))
    check("読み戻し: A_ はジョイントだけ・アニメあり・1 テイク", idle["meshes"] == 0 and idle["joints"] == expected_joints and idle["curves"] > 0 and idle["takes"] == 1, str(idle))
    check("読み戻し: ショットのキャラクターはジョイントだけ・アニメあり", ch["meshes"] == 0 and ch["joints"] == expected_joints and ch["curves"] > 0, str(ch))
    if which == "misoten":
        check("読み戻し（MisotenChara）: メッシュ 3 枚・スキン 3 つ", sk["meshes"] == 3 and sk["skins"] == 3, str(sk))
    INFO.append(f"一周の合計: {time.perf_counter() - t_all:.1f} 秒")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else ("misoten" if MISOTEN.exists() else "mini")
    maya.standalone.initialize(name="python")
    try:
        run(which)
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
