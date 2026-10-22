"""ショットのスモーク（AN-7。mayapy・ヘッドレス。dx11 / Render Override は使わない）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_shot_smoke.py

確かめること: ショットを作る → カメラ 2 台の区間が Camera Sequencer の shot ノードになる・キャラクターのクリップ列がショットの範囲の先頭から並ぶ
（値を元のカーブと突き合わせ）・EVT_ ロケータの時刻・ネームスペース付きのキャラクターと 2 体同時・外すと全部戻る・保存して開き直すと戻る・
Undo・ブレンドタブの列を渡す・UI の警告。
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
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)  # 確認ダイアログは常に「はい」（ヘッドレスで止まらないように）

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
TOL = 0.3


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_shot_"))
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def key_char(cmds, pre: str, offset: float = 0.0) -> None:
    """Idle 0〜30 / Run 100〜118 / Wave 200〜230 のキー（値は見分けがつくようにずらす。offset でキャラクターごとに変える）。"""
    for (a, b), (v0, v1) in (((0, 30), (0, 30)), ((100, 118), (100, 200)), ((200, 230), (-50, -80))):
        for f, v in ((a, v0), (b, v1)):
            cmds.setKeyframe(f"{pre}spine_C0_0_jnt", attribute="translateX", time=f, value=v + offset)
            cmds.setKeyframe(f"{pre}arm_L0_0_jnt", attribute="rotateZ", time=f, value=(v / 3) + offset)


def _run(tmp: Path) -> None:
    from maya import cmds

    import anim_blend_smoke  # noqa: F401  (key_clips を使わない。import だけで通ることの確認)
    import make_mini_rig
    from tdrive import project
    from tdrive_anim import composition as K
    from tdrive_anim import session as S
    from tdrive_anim import shot_scene as SS
    from tdrive_anim import ui_shot
    from tdrive_anim.core import animset as A
    from tdrive_anim.core import sequence as Seq
    from tdrive_anim.core import shot as SH

    project.set_root(tmp)
    rig = make_mini_rig.build(namespace="MisotenChara")
    cmds.currentUnit(time="ntsc")
    key_char(cmds, "MisotenChara:")
    cam1 = cmds.rename(cmds.camera(name="CAM_Main")[0], "CAM_Main")
    cam2 = cmds.rename(cmds.camera(name="CAM_Close")[0], "CAM_Close")
    s = S.current()
    s.new("MisotenChara", meshes=[rig["mesh"]], namespace="MisotenChara")
    for name, a, b in (("Idle", 0, 30), ("Run", 100, 118), ("Wave", 200, 230)):
        s.add_clip_from_range(name, a, b, direction="F" if name == "Run" else "")
    clips = {c["name"]: c for c in s.data["clips"]}
    s.save()
    fps = 30
    cmds.playbackOptions(minTime=5, maxTime=50, animationStartTime=0, animationEndTime=100, loop="once")
    cmds.currentTime(12, edit=True)
    plugs = ["MisotenChara:spine_C0_0_jnt.translateX", "MisotenChara:arm_L0_0_jnt.rotateZ"]
    orig_src = {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in plugs}

    # ---- 作成・検証
    try:
        s.new_shot("opening", 30, 0, 240)
        check("ショット名の形が違うと拒否", False)
    except SH.ShotError:
        check("ショット名の形が違うと拒否", s.shot is None)
    s.new_shot("Opening01", 30, 10, 250)
    check("ショットができる", s.shot["shot"] == "Opening01" and s.shot_path == tmp / "shots" / "Opening01.shot.json" and s.shot_dirty)
    s.shot_add_camera("CAM_Main", 10, 130)
    s.shot_add_camera("CAM_Close", 131, 250)
    s.shot_add_character("MisotenChara", "MisotenChara", [
        {"clip": "A_MisotenChara_Idle", "holdSec": 2.0}, {"clip": "A_MisotenChara_Run_F", "holdSec": 2.0, "crossFadeSec": 0.08}, {"clip": "A_MisotenChara_Wave", "crossFadeSec": 0.1}])
    s.shot_add_event(70, "Hit")
    s.shot_add_event(200, "Finish")
    check("カメラ 2 台・キャラクター・イベントが入る", len(s.shot["cameras"]) == 2 and len(s.shot["characters"]) == 1 and len(s.shot["events"]) == 2)
    check("検証にエラーがない", SH.errors(s.shot) == [])

    # ---- 期待値（反映前に元のカーブから計算）
    rows = s.shot["characters"][0]["sequence"]
    pls = Seq.from_rows(rows, clips, fps)
    off = 10
    times = [10, 20, 39.9, 70.05, 71.2, 72.4, 100, 130]
    expected = {t: {p: sum(w * cmds.getAttr(p, time=Seq.source_frame(next(x for x in pls if x.clip == c), t - off)) for c, w in Seq.pose_weights(pls, t - off)) for p in plugs} for t in times}

    # ---- 反映
    res = SS.build(s.shot, s.data)
    check("反映: カメラ区間 2・イベント 2・合成 1", res["cameras"] == 2 and res["events"] == 2 and len(res["compositions"]) == 1 and not res["warnings"], str(res))
    shots = SS.sequencer_shots("Opening01")
    cam_of = lambda x: (cmds.listRelatives(x["camera"], parent=True) or [x["camera"]])[0]  # 接続先がシェイプでもトランスフォームでも、親のトランスフォーム
    check("Camera Sequencer にカメラ 2 台の区間ができる（区間と、shot ノードの currentCamera）",
          [(x["start"], x["end"], cam_of(x)) for x in shots] == [(10, 130, "CAM_Main"), (131, 250, "CAM_Close")], str(shots))
    check("カメラの切り替え（区間の前半は Main、後半は Close）", [SH.camera_at(s.shot, f) for f in (10, 130, 131, 250)] == ["CAM_Main", "CAM_Main", "CAM_Close", "CAM_Close"])
    check("再生範囲がショットの範囲", SS.group_name("Opening01") == "tdShot_Opening01" and cmds.objExists("tdShot_Opening01")
          and cmds.playbackOptions(query=True, minTime=True) == 10 and cmds.playbackOptions(query=True, maxTime=True) == 250)
    bad = []
    for t in times:
        cmds.currentTime(t, edit=True)
        for p in plugs:
            if abs(cmds.getAttr(p) - expected[t][p]) > TOL:
                bad.append((t, p, round(cmds.getAttr(p), 2), round(expected[t][p], 2)))
    check("クリップ列がショットの先頭（10）から並ぶ（元のカーブのブレンドと一致）", not bad, str(bad[:4]))
    check("イベントのロケータ EVT_Hit / EVT_Finish が td グループの下にある", SS.event_frames("Opening01") == {"Hit": 70, "Finish": 200}, str(SS.event_frames("Opening01")))
    check("ロケータの ddEvent は 0 → 1 のステップ", [cmds.getAttr("EVT_Hit.ddEvent", time=t) for t in (60, 69, 70, 100)] == [0, 0, 1, 1])
    check("td ノードは出力の対象外の印（td 接頭辞）", all(n.startswith("td") for n in cmds.ls("tdShot_*") + cmds.ls("tdAnimComp_*", type="network")))

    # ---- 外す
    n = SS.remove("Opening01")
    check("外すと合成・shot ノード・ロケータが消え、再生範囲が戻る",
          n == 1 and not SS.compositions("Opening01") and not cmds.ls("EVT_*") and not cmds.ls(type="shot")
          and cmds.playbackOptions(query=True, minTime=True) == 5 and cmds.playbackOptions(query=True, maxTime=True) == 50 and cmds.currentTime(query=True) == 12,
          f"{n} {cmds.ls(type='shot')}")
    check("元のつながりに戻る", {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in plugs} == orig_src)
    check("カメラは壊れない", cmds.objExists(cam1) and cmds.objExists(cam2))

    # ---- 保存して開き直す
    import copy

    in_memory = copy.deepcopy(s.shot)  # 保存前のメモリの内容と比べる（保存したファイルを読み直した値同士の比較では、書き込みの欠落に気づけない。TS-10）
    path = s.save_shot()
    scene_file = Path(tempfile.gettempdir()) / "tdrive_shot_smoke_scene.ma"
    cmds.file(rename=str(scene_file))
    cmds.file(save=True, type="mayaAscii")  # kBeforeSave で fileInfo にショットの場所が入る
    s.shot = None
    cmds.file(str(scene_file), open=True, force=True)  # 実際の保存 → 開く
    S.on_scene_opened()
    check("保存・開き直しでショットが戻る（保存前のメモリと同じ・実際にシーンを保存して開き直す）", s.shot == in_memory and not s.shot_dirty, str(s.shot_path))

    # ---- Undo
    s.shot_add_event(100, "Extra")
    check("イベントの追加を Undo で戻せる", len(s.shot["events"]) == 3 and s.undo() and len(s.shot["events"]) == 2 and s.redo() and len(s.shot["events"]) == 3)
    s.undo()
    try:
        s.shot_add_event(999, "Out")
        check("範囲外のイベントはエラー", False)
    except SH.ShotError:
        check("範囲外のイベントはエラー", len(s.shot["events"]) == 2)

    # ---- 2 体のキャラクター（ネームスペースが別）。片方の合成がもう片方を壊さない
    make_mini_rig.build(namespace="Hero", new_scene=False)
    key_char(cmds, "Hero:", offset=1000.0)
    hero_plugs = ["Hero:spine_C0_0_jnt.translateX"]
    hero_src = {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in hero_plugs}
    hero_set = A.new_animset("Hero")
    for name, a, b in (("Idle", 0, 30), ("Run", 100, 118)):
        A.add_clip(hero_set, A.default_clip(f"A_Hero_{name}", a, b, "Hero"))
    A.save(tmp / "anim" / "Hero" / "animset.json", hero_set)  # ショットは別の ModelId の animset をファイルから読む
    s.shot_add_character("Hero", "Hero", [{"clip": "A_Hero_Idle", "holdSec": 1.0}, {"clip": "A_Hero_Run", "crossFadeSec": 0.1}])
    expect_hero = cmds.getAttr(hero_plugs[0], time=15)
    res = SS.build(s.shot, s.data)
    check("2 体: 合成が 2 つ（名前が別）", sorted(res["compositions"]) == sorted(["shot_Opening01_MisotenChara", "shot_Opening01_Hero"]), str(res))
    cmds.currentTime(25, edit=True)  # Hero は Idle の 15 フレーム目
    check("2 体: Hero の値は Hero のクリップから（オフセット 1000 を含む）", abs(cmds.getAttr(hero_plugs[0]) - expect_hero) < TOL, f"{cmds.getAttr(hero_plugs[0])} / {expect_hero}")
    cmds.currentTime(20, edit=True)
    check("2 体: MisotenChara の合成も生きている", abs(cmds.getAttr(plugs[0]) - expected[20][plugs[0]]) < TOL, f"{cmds.getAttr(plugs[0])} / {expected[20][plugs[0]]}")
    SS.remove("Opening01")
    check("2 体: 外すと両方の元のつながりに戻る", {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in plugs} == orig_src
          and {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in hero_plugs} == hero_src)
    s.shot_remove_character(1)

    # ---- 警告: 無いカメラ・無いスケルトン・animset の無いキャラクター
    s.shot_add_camera("CAM_Missing", 10, 20)
    res = SS.build(s.shot, s.data)
    check("無いカメラは警告で飛ばす", any("CAM_Missing" in w for w in res["warnings"]) and len(SS.sequencer_shots("Opening01")) == 2, str(res["warnings"]))
    SS.remove("Opening01")
    s.shot_remove_camera(2)
    s.shot_add_character("Ghost", "Ghost", [{"clip": "A_Ghost_Idle"}])
    try:
        SS.build(s.shot, s.data)
        check("animset の無いキャラクターはエラー", False)
    except A.AnimSetError:
        check("animset の無いキャラクターはエラー", True)
    check("失敗してもシーンの元のつながりは壊れない", {p: cmds.listConnections(p, source=True, destination=False, plugs=True)[0] for p in plugs} == orig_src)
    SS.remove("Opening01")
    s.shot_remove_character(len(s.shot["characters"]) - 1)

    # ---- 手付けキーのレイヤー・動画（ビューポートが無い環境）
    layer = SS.hand_layer("Opening01", "MisotenChara")
    check("手付けキー用の animLayer ができる（2 回呼んでも 1 つ）", layer == "tdShotHand_Opening01_MisotenChara" and SS.hand_layer("Opening01", "MisotenChara") == layer and len(cmds.ls("tdShotHand_*", type="animLayer")) == 1)
    p, why = SS.capture_movie(s.shot)
    check("mayapy では動画を撮れない理由を返す（例外にしない）", p is None and "動画" in why, why)

    # ---- UI
    from tdrive_anim import ui_blend

    shot_ui = ui_shot.ShotTab(s)
    blend_ui = ui_blend.BlendTab(s)
    shot_ui.blend_tab, blend_ui.shot_tab = blend_ui, shot_ui
    s.listeners += [shot_ui.refresh, blend_ui.refresh]
    shot_ui.refresh()
    APP.processEvents()
    check("UI: ショットの内容が表に出る", shot_ui.cam_table.rowCount() == 2 and shot_ui.char_table.rowCount() == 1 and shot_ui.ev_table.rowCount() == 2)
    check("UI: ブレンドタブの「この列をショットへ」が使える", blend_ui.shot_btn.isEnabled())
    blend_ui.rows = [{"clip": "A_MisotenChara_Run_F", "holdSec": 1.0, "crossFadeSec": 0.1, "switchAt": 1.0, "speed": 1.0}, {"clip": "A_MisotenChara_Idle", "holdSec": 0.0, "crossFadeSec": 0.08, "switchAt": 1.0, "speed": 1.0}]
    shot_ui.char_table.selectRow(0)
    blend_ui.send_to_shot()
    check("UI: 遷移列がキャラクターのクリップ列になる（Undo できる）", [r["clip"] for r in s.shot["characters"][0]["sequence"]] == ["A_MisotenChara_Run_F", "A_MisotenChara_Idle"]
          and s.undo() and len(s.shot["characters"][0]["sequence"]) == 3 and s.redo())
    shot_ui.name.setText("Bad Name")
    shot_ui.create()
    check("UI: 不正な名前は警告だけ（今のショットは変わらない）", "⚠" in shot_ui.message.text() and s.shot["shot"] == "Opening01", shot_ui.message.text())
    cmds.currentTime(100, edit=True)
    shot_ui.ev_key.setText("Boom")
    shot_ui.add_event()
    check("UI: 現在フレームにイベントを追加", any(e["key"] == "Boom" and e["frame"] == 100 for e in s.shot["events"]))
    shot_ui.ev_key.setText("bad key")
    shot_ui.add_event()
    check("UI: 不正なキーは警告だけ", "⚠" in shot_ui.message.text())
    shot_ui.build()
    check("UI: ショットを反映 → バナーが出る", SS.compositions("Opening01") and "合成中" in shot_ui.banner.text(), shot_ui.banner.text())
    shot_ui.remove()
    check("UI: 合成を外す", not SS.compositions("Opening01") and not shot_ui.banner.isVisible())
    shot_ui.build()
    shot_ui.detach()
    check("UI: ウィンドウを閉じる（detach）と合成が外れる", not SS.compositions("Opening01"))
    shot_ui.name.setText("Closing02")
    shot_ui.start.setValue(0)
    shot_ui.end.setValue(60)
    shot_ui.fps.setCurrentIndex(1)
    unit_before = cmds.currentUnit(query=True, time=True)
    shot_ui.create()
    check("UI: 新規ショット（60 fps）はシーンの時間単位を変えず、警告する（SF-2）", s.shot["shot"] == "Closing02" and s.shot["frameRate"] == 60 and cmds.currentUnit(query=True, time=True) == unit_before and "命名タブ" in shot_ui.message.text(), shot_ui.message.text())
    for t in (shot_ui, blend_ui):
        t.detach()
    s.listeners = [f for f in s.listeners if f not in (shot_ui.refresh, blend_ui.refresh)]


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
